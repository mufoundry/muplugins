import asyncio
from datetime import datetime, timezone
import uuid

import orjson
from dataclasses import dataclass

from .db.pcs import ActiveAs
from .events.base import EventBase
from fastapi import Request

@dataclass(slots=True)
class Subscription:
    request: Request
    queue: asyncio.Queue


class Session:
    __slots__ = (
        "core",
        "acting",
        "created_at",
        "last_active_at",
        "subscriptions",
        "active",
        "task_group",
        "shutdown_event",
    )

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

    async def send_event(self, event: EventBase) -> None:
        for sub in self.subscriptions.values():
            await sub.queue.put(event)

    def send_event_nowait(self, event: EventBase) -> None:
        for sub in self.subscriptions.values():
            sub.queue.put_nowait(event)

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

    async def handle_postgre_notification(self, conn, pid, channel, payload):
        try:
            data = orjson.loads(payload)
        except Exception:
            return

        if data.get("table") != "pc_events":
            return

        if str(data.get("pc_id")) != str(self.pc.id):
            return

        event_type = data.get("event_type")
        if not event_type:
            return

        ev_class = self.core.events.get(event_type)
        if not ev_class:
            return

        ev_data = data.get("data") or {}
        if "happened_at" not in ev_data and data.get("created_at"):
            ev_data = dict(ev_data)
            ev_data["happened_at"] = data["created_at"]

        try:
            event = ev_class(**ev_data)
        except Exception:
            return

        await self.send_event(event)

    async def listen_events(self):
        async with self.core.db.connection() as conn:
            await conn.add_listener("pc_events", self.handle_postgre_notification)
            # Do nothing in order to keep the listener running.
            while True:
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    break

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

    async def execute_command(self, command: str):
        pass