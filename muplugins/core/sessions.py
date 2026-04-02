import asyncio
from datetime import datetime, timezone
import uuid

import orjson
from dataclasses import dataclass

from .db.pcs import ActiveAs
from .events.base import EventBase
from .events.messages import RichTextEvent, TextEvent

import typing

if typing.TYPE_CHECKING:
    from .session_commands.base import SessionCommand


class SessionParser:
    def __init__(self, session: "Session"):
        self.session = session
    
    async def execute_command(self, raw: str):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    async def cleanup(self):
        pass

    async def send_text(self, text: str):
        await self.session.send_text(text)
    
    async def send_line(self, text: str):
        await self.session.send_line(text)

class Session:

    def __init__(self, core, acting: ActiveAs):
        self.core = core
        self.acting = acting
        # User and PC are filled in after the session is created.
        self.created_at = datetime.now(timezone.utc)
        self.last_active_at = datetime.now(timezone.utc)
        self.subscriptions: dict[uuid.UUID, Subscription] = {}
        self.active = False
        self.task_group = None
        self.shutdown_event = asyncio.Event()
        self.parser_stack: list[SessionParser] = []

    @property
    def app(self):
        return self.core.app

    @property
    def user(self):
        return self.acting.user

    @property
    def pc(self):
        return self.acting.pc
    
    @property
    def db(self):
        return self.core.db
    
    def repl_globals(self, data: dict):
        data["session"] = self
        data["core"] = self.core
        data["app"] = self.app
        data["pc"] = self.pc
        data["user"] = self.user

    async def send_event(self, event: EventBase) -> None:
        for sub in self.subscriptions.values():
            await sub.queue.put(event)

    def send_event_nowait(self, event: EventBase) -> None:
        for q in self.subscriptions:
            q.put_nowait(event)
    
    async def send_text(self, text: str):
        await self.send_event(
            TextEvent(text=text)
        )
    
    async def send_line(self, text: str):
        if not text.endswith("\n"):
            text += "\n"
        await self.send_text(text)

    async def subscribe(self, request: Request) -> tuple[uuid.UUID, asyncio.Queue]:
        """Create a new queue for this character and add it to the subscription list."""
        async with self.db.connection() as conn:
             row = await conn.fetchrow("""
                INSERT INTO pc_subscriptions (pc_id, user_id, ip_address, user_agent)
                VALUES ($1, $2, $3, $4) RETURNING id
             """, self.pc.id, self.user.id, request.client.host, request.headers.get("User-Agent"))

        sub = Subscription(request, asyncio.Queue())
        self.subscriptions[row["id"]] = sub
        return row["id"], sub.queue

    async def unsubscribe(self, id: uuid.UUID):
        """Remove the given queue from this session's subscription list."""
        self.subscriptions.pop(id, None)
        async with self.db.connection() as conn:
            await conn.execute("DELETE FROM pc_subscriptions WHERE id = $1", id)

    async def run(self):
        async with asyncio.TaskGroup() as tg:
            self.task_group = tg
            await tg.create_task(self.listen_events())

            await self.shutdown_event.wait()

    async def start(self):
        """
        Start the session. Should do login things.

        """
        self.active = True
        async with self.db.connection() as conn:
            await conn.execute("""
                INSERT INTO pc_sessions (pc_id) VALUES ($1)
            """, self.pc.id)
        self.app.task_group.create_task(self.run())
        await self.on_start()

    async def stop_local(self):
        for sub in self.subscriptions.values():
            await sub.queue.put(None)

    async def stop(self, graceful: bool = True):
        if not self.active:
            return

    async def available_commands(self):
        priorities = sorted(list(self.core.session_commands_priority.keys()))
        for priority in priorities:
            for command in self.core.session_commands_priority[priority]:
                if await command.check_access(self):
                    yield command
    
    async def execute_session_command(self, command: str) -> bool:
        for cmd in await self.available_commands():
            if match := await command.check_match(command):
                instance = cmd(self, command, match)
                return await instance.execute()

    async def command_passthrough(self, command: str):
        await self.send_line(f"Unknown command: {command}")

    async def execute_command(self, command: str):
        # case 1: route to top parser. These are for menus.
        if self.parser_stack:
            await self.parser_stack[-1].execute_command(command)
            return
        
        # case 2: it might be a session command!
        if res := await self.execute_session_command(command):
            return res
        
        # case 3: unknown command
        # this is great for overriding!
        await self.command_passthrough(command)

    async def add_parser(self, parser: SessionParser):
        self.parser_stack.append(parser)
        await parser.start()
    
    async def pop_parser(self):
        if self.parser_stack:
            parser = self.parser_stack.pop()
            await parser.stop()