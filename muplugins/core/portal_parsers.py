from muforge.portal.connections.parser import BaseParser
from .db.pcs import PCModel, ActiveAs
from .db.users import UserModel
import uuid
import asyncio
import orjson
import ssl

from httpx import HTTPStatusError
from websockets.asyncio.client import connect

from loguru import logger
from rich.errors import MarkupError
from rich.markup import escape


class CoreParser(BaseParser):
    parser_type = "core"

    @property
    def core(self):
        return self.connection.core
    
    def available_commands(self):
        priorities = sorted(self.connection.core.portal_commands_priority.keys())
        for priority in priorities:
            for c in self.connection.core.portal_commands_priority[priority]:
                if c.check_parser(self) and c.check_access(self):
                    yield c

    async def display_short_help(self):
        commands = sorted(self.available_commands(), key=lambda c: c.short_priority, reverse=True)
        help_table = self.make_table("Command", "Description", title="Available Commands")
        for command in commands:
            if not command.short_syntax:
                continue
            help_table.add_row(command.short_syntax or command.name, command.short_help)
        await self.send_rich(help_table)

    async def handle_command(self, command: str):
        found = None
        for cmd_class in self.available_commands():
            if not (match_data := cmd_class.check_match(self, command)):
                continue
            found = cmd_class(self, command, match_data)
            break

        if not found:
            await self.handle_no_match(command)
            return
        
        try:
            await found.execute()
        except MarkupError as e:
            await self.send_rich(f"[bold red]Error parsing markup:[/] {escape(str(e))}")
        except ValueError as error:
            await self.send_line(f"{error}")
        except HTTPStatusError as e:
            if e.response.status_code == 401:
                await self.send_line("You have been disconnected.")
                await self.connection.pop_parser()
                return
            logger.exception("HTTP error in handle_command: %s")
            await self.send_line("An error occurred. Please contact staff.")
        except Exception as error:
            if self.connection.admin_level >= 1:
                await self.send_line(f"An error occurred: {error}")
            else:
                await self.send_line("An unknown error occurred. Contact staff.")
            logger.exception(error)
    
    async def handle_no_match(self, command: str):
        await self.send_line("Huh? (Type 'help' for help)")

class AuthParser(CoreParser):
    """
    Implements the login menu. User registration and authentication, etc.
    """
    parser_type = "auth"

    async def display_welcome_logo(self):
        pass

    async def display_welcome_text(self):
        await self.send_line(
            f"Welcome to {self.app.complete_settings['MUFORGE'].get('name', 'MuForge')}!"
        )

    async def show_welcome(self):
        await self.display_welcome_logo()
        await self.display_welcome_text()
        await self.display_short_help()

    async def on_start(self):
        await self.show_welcome()

class UserParser(CoreParser):
    """
    Implements the character selection and user management features.
    """
    parser_type = "user"

    async def on_start(self):
        await self.handle_look()


    async def handle_look(self):
        user_id = self.connection.payload.get("sub")
        character_data = await self.api_call("GET", f"/v1/users/{user_id}/pcs")

        characters = [PCModel(**c) for c in character_data]

        character_table = self.make_table("Name", "Last Active", title="Characters")
        for character in characters:
            character_table.add_row(character.name, str(character.last_active_at))
        await self.send_rich(character_table)
        await self.display_short_help()

class PCParser(CoreParser):
    parser_type = "pc"
    
    def __init__(self, active: ActiveAs):
        super().__init__()
        self.active = active
        self.shutdown_event = asyncio.Event()
        self.client = None
        self.stream_task = None
        self.sid = None
        self.ws = None

    @property
    def character(self) -> PCModel:
        return self.active.pc

    @property
    def user(self) -> UserModel:
        return self.active.user

    @property
    def core(self):
        return self.connection.core

    async def on_start(self):
        await self.send_line(f"You have entered the game as {self.character.name}.")
        self.stream_task = self.connection.task_group.create_task(self.stream_updates())

    async def on_end(self):
        self.shutdown_event.set()

    async def handle_event(self, event_name: str, event_data: dict):
        if event_class := self.core.events.get(event_name, None):
            event = event_class(**event_data)
            await event.handle_event(self.connection)
            await event.handle_event_parser(self)
        else:
            logger.error(f"Unknown event: {event_name}")

    async def stream_updates(self):
        headers = self.connection.get_headers()
        base_url = str(self.connection.client.base_url).replace("http", "ws")
        url = f"{base_url}/v1/pcs/{self.character.id}/session?token={self.connection.jwt}"

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        async with connect(uri=url, additional_headers=headers, ssl=ssl_context) as ws:
            self.ws = ws

            while msg := await ws.recv():
                data = orjson.loads(msg)
                event_name = data.get("event", None)
                event_data = data.get("data", None)
                if event_name and event_data:
                    await self.handle_event(event_name, event_data)

    async def old_stream_updates(self):
        disconnects: int = 0
        while True:
            try:
                if disconnects > 0:
                    await asyncio.sleep(2 ^ disconnects)
                async for event_name, event_data in self.connection.api_stream(
                    "GET", f"/v1/pcs/{self.character.id}/events"
                ):
                    disconnects = 0
                    await self.handle_event(event_name, event_data)
                self.stream_task.cancel()
                await self.connection.pop_parser()
            except asyncio.CancelledError:
                return
            except HTTPStatusError as e:
                if e.response.status_code == 401:
                    await self.send_line("You have been disconnected.")
                    return
                logger.exception("HTTP error in stream_updates: %s")
                await self.send_line("An error occurred. Please contact staff.")
                disconnects += 1
                return
            except Exception as e:
                logger.exception("Unknown error occurred in stream_updates.")
                await self.send_line("An error occurred. Please contact staff.")
                disconnects += 1
                return

    async def handle_no_match(self, command: str):
        await self.ws.send(orjson.dumps({"command": command}), text=False)

    async def old_handle_no_match(self, command: str):
        """
        Relay the command to the game engine.
        """
        try:
            res = await self.api_call("POST", f"/v1/pcs/{self.character.id}/command", json={"command": command})
        except HTTPStatusError as e:
            if e.response.status_code == 401:
                await self.send_line("You have been disconnected.")
                await self.connection.pop_parser()
                return
            logger.exception("HTTP error in handle_no_match: %s")
            await self.send_line("An error occurred. Please contact staff.")