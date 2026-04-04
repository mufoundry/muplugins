from .base import PortalCommand
from collections import defaultdict

from muforge.utils.misc import partial_match

from ..events.messages import RichColumns

class UniversalCommand(PortalCommand):

    @classmethod
    def check_parser(cls, parser) -> bool:
        return True

class Quit(UniversalCommand):
    key = "core/quit"
    help_name = "quit"
    help_category = "System"
    match_defs = {"quit": 4}

    async def func(self):
        cmd = self.parsed.get("cmd", "")
        if not cmd == "QUIT":
            await self.send_line("Did you mean to QUIT?")
            return
        
        await self.check_quit()

        await self.send_goodbye()

        self.connection.shutdown_cause = "quit"
        self.connection.shutdown_event.set()
    
    async def check_quit(self):
        pass

    async def send_goodbye(self):
        await self.send_line("Goodbye!")

class Help(UniversalCommand):
    key = "core/help"
    help_name = "help"
    help_category = "General"
    match_defs = {"help": 1, "?": 1}

    async def func(self):
        if not (args := self.parsed.get("args", "")):
            await self.display_full_help()
            return
        await self.display_file(args)

    async def handle_unknown(self, file_name: str = ""):
        await self.send_line(f"Command not found: {file_name}")

    async def display_file(self, file_name: str):
        commands = self.parser.available_commands().values()
        if not (command := partial_match(file_name, commands, key=lambda c: c.name)):
            await self.handle_unknown(file_name)
            return
        await command.display_help(self.connection)

    async def display_full_help(self):
        categories = defaultdict(list)
        commands = self.parser.available_commands().values()
        for command in commands:
            categories[command.help_category].append(command)

        category_keys = sorted(categories.keys())
        column_message = RichColumns()

        for key in category_keys:
            commands = categories[key]
            commands.sort(key=lambda cmd: cmd.name)
            cmds = [cmd.name for cmd in commands]
            column_message.data.append((key, cmds))
        await self.send_event(column_message)


class MSSP(UniversalCommand):
    key = "core/mssp"
    help_name = "info"
    help_category = "System"
    match_defs = {"mssp": 2}

    async def func(self):
        try:
            data = await self.api_call("GET", "/v1/telnet/mssp")
        except Exception as e:
            await self.send_line(f"Error retrieving MSSP data: {str(e)}")
            return
        
        rendered = "\r\n".join([f"{k}: {v}" for k, v in data])
        await self.send_line(rendered)