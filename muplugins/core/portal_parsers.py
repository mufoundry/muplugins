from muforge.portal.connections.parser import BaseParser
from .db.pcs import PCModel, ActiveAs
from .db.users import UserModel
import uuid

from httpx import HTTPStatusError
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

    async def display_welcome_commands(self):
        help_table = self.make_table("Command", "Description")
        help_table.add_row("register <email>=<password>", "Register a new account.")
        help_table.add_row("login <email>=<password>", "Login to an existing account.")
        help_table.add_row("info", "Display game information. (Same as MSSP)")
        help_table.add_row("help", "Display more information about available commands.")
        help_table.add_row("quit", "Disconnect from the game.")
        await self.send_rich(help_table)

    async def show_welcome(self):
        await self.display_welcome_logo()
        await self.display_welcome_text()
        await self.display_welcome_commands()

    async def on_start(self):
        await self.show_welcome()

class UserParser(CoreParser):
    """
    Implements the character selection and user management features.
    """
    parser_type = "user"

    async def on_start(self):
        await self.handle_look()
    
    def generate_help(self) -> str:
        help_table = self.make_table("Command", "Description", title="User Commands")
        help_table.add_row("help", "Displays this help message.")
        help_table.add_row("create <name>", "Creates a new character.")
        help_table.add_row("play <name>", "Selects a character to play.")
        help_table.add_row("delete <name>", "Deletes a character.")
        help_table.add_row("logout", "Logs out of the game.")
        help_table.add_row("look", "Lists all characters.")
        return help_table


    async def handle_look(self):
        user_id = self.connection.payload.get("sub")
        character_data = await self.api_call("GET", f"/v1/users/{user_id}/pcs")

        characters = [PCModel(**c) for c in character_data]

        character_table = self.make_table("Name", "Last Active", title="Characters")
        for character in characters:
            character_table.add_row(character.name, str(character.last_active_at))
        await self.send_rich(character_table)

class PCParser(CoreParser):
    parser_type = "pc"
    
    def __init__(self, active: ActiveAs):
        super().__init__()
        self.active = active
        self.shutdown_event = asyncio.Event()
        self.client = None
        self.stream_task = None
        self.sid = None

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
        if event_class := self.app.events.get(event_name, None):
            event = event_class(**event_data)
            await event.handle_event(self)
        else:
            logger.error(f"Unknown event: {event_name}")

    async def stream_updates(self):
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

    def available_commands(self) -> dict[int, list["BaseCommand"]]:
        out = dict()
        for priority, commands in self.app.commands_priority.items():
            for c in commands:
                if c.check_access(self.active):
                    out[c.name] = c
        return out

    def iter_commands(self):
        priorities = sorted(self.app.commands_priority.keys())
        for priority in priorities:
            for command in self.app.commands_priority[priority]:
                if command.check_access(self.active):
                    yield command

    def match_command(self, cmd: str) -> typing.Optional["BaseCommand"]:
        for command in self.iter_commands():
            if command.unusable:
                continue
            if command.check_match(self.active, cmd):
                return command

    async def refresh_active(self):
        json_data = await self.api_call(
            "GET", f"/pcs/{self.character.id}/active"
        )
        self.active = ActiveAs(**json_data)

    async def handle_no_match(self, match_dict: dict | None):
        await self.send_line("Huh? (Type 'help' for help)")

    async def handle_command(self, event: str):
        try:
            await self.refresh_active()
        except Exception as e:
            logger.error(e)
            await self.send_line("An error occurred. Please contact staff.")
            return

        if not (match_data := CMD_MATCH.match(event)):
            await self.handle_no_match(None)
            return

        # regex match_data.groupdict() returns a dictionary of all the named groups
        # and their values. Missing groups are None. That's silly. We'll filter it out.
        match_dict = {k: v for k, v in match_data.groupdict().items() if v is not None}
        cmd_key = match_dict.get("cmd")
        if not (cmd := self.match_command(cmd_key.lower())):
            await self.handle_no_match(match_dict)
            return

        try:
            command = cmd(self, cmd_key, match_dict)
            await command.execute()
        except MarkupError as e:
            await self.send_rich(f"[bold red]Error parsing markup:[/] {escape(str(e))}")
        except ValueError as error:
            await self.send_line(f"{error}")
        except Exception as error:
            if self.user.admin_level >= 1:
                await self.send_line(f"An error occurred: {error}")
            else:
                await self.send_line("An unknown error occurred. Contact staff.")
            logger.exception(error)

    async def handle_command_remote(self, event: str):
        try:
            result = await self.api_call(
                "POST",
                f"/v1/pcs/{self.character.id}/command",
                json={"command": event},
            )
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
            await self.send_line(f"An error occurred: {error}")
            logger.exception(error)
