import re
import typing

from muplugins.core.db.pcs import ActiveAs, PCModel
from muplugins.core.db.users import UserModel

if typing.TYPE_CHECKING:
    from ..portal_parsers import CoreParser

CMD_MATCH = re.compile(
    r"(?s)^(?P<cmd>\S+?)(?:/(?P<switches>\S+)?)?(?P<fullargs> +(?P<args>(?P<lsargs>.+?)(?:=(?P<rsargs>.*))?)?)?$"
)


class PortalCommand:
    """
    Help not implemented for this command. Contact staff!
    """

    # The unique key for this command. This is used for identifying it,
    # but also for overriding it with plugins.
    key = "core/notset"
    help_name = None
    # If help_category or help_name is None, the command will not be listed in the help system.
    help_category = None

    # A short description of the command for the short help listing.
    short_syntax = "???"
    short_help = "No help available for this command."
    # priority for short help listing. Higher goes first.
    short_priority = 0
    
    # Normal priority for command matching. Higher goes first.
    priority = 0
    # Match_defs are a dictionary of full_command->minchars
    # For instance north->n
    # The second value is the minimum amount of characters that must match.
    # So in that example, n would match north, but nu would not. 
    match_defs: dict[str, int] = dict()
    min_level = 0
    # Set this to true if you want the command to exist but never reach the parser.
    # this could be helpful for creating help files or meta-topics.
    unusable = False
    parser_types: set[str] = set()

    class Error(ValueError):
        pass

    @classmethod
    def check_parser(cls, parser: CoreParser) -> bool:
        """
        Check if the command should be registered for this parser.
        This is useful for commands that only apply to certain parsers.
        """
        return parser.parser_type in cls.parser_types

    @classmethod
    def check_match(cls, parser: CoreParser, command: str) -> typing.Optional[dict[str, str]]:
        """
        Check if the command matches the user's input.

        Command will already be trimmed and lowercase. Equal to the <cmd> in the regex.

        We are a match if it is a direct match with an alias, or if it is a complete match
        with the command name, or if it is a partial match with the command name starting
        with min_length and not contradicting the name.

        IE: "north" should respond to "nort" but not "norb"
        """
        match_data = CMD_MATCH.match(command)
        if not match_data:
            return None
        match_dict = {k: v for k, v in match_data.groupdict().items() if v}
        
        cmd = match_dict.get("cmd", "").lower()
        for k, v in cls.match_defs.items():
            if cmd == k:
                return match_dict
            if len(cmd) >= v and k.startswith(cmd):
                return match_dict
        return None

    @classmethod
    def check_access(cls, parser: CoreParser) -> bool:
        """
        Check if the user should have access to the command.
        If they don't, they don't see it at all.

        Args:
            enactor: The user to check access for.

        Returns:
            bool: True if the user has access, False otherwise.
        """
        return parser.connection.admin_level >= cls.min_level

    def __init__(
        self,
        parser: CoreParser,
        raw: str,
        parsed: dict[str, str],
    ):
        self.parser = parser
        self.raw = raw
        self.parsed = parsed

    @property
    def connection(self):
        return self.parser.connection
    
    @property
    def admin_level(self) -> int:
        return self.connection.admin_level

    def can_execute(self) -> bool:
        """
        Check if the command can be executed.
        """
        return True

    async def execute(self) -> dict:
        """
        Execute the command.

        Returns:
            dict: The result of the command execution.

        Raises:
            HTTPException: If the command cannot be executed.
        """
        if not self.can_execute():
            return {"ok": False, "error": "Cannot execute command"}
        try:
            result = await self.func()
            return result or {"ok": True}
        except self.Error as err:
            await self.send_line(f"{err}")
            return {"ok": False, "error": str(err)}

    async def func(self) -> dict | None:
        """
        Execute the command.
        """
        pass

    async def send_text(self, text: str):
        await self.connection.send_text(text)

    async def send_line(self, text: str):
        await self.connection.send_line(text)

    async def send_data(self, package: str, data):
        await self.connection.send_data(package, data)

    async def api_call(self, *args, **kwargs):
        return await self.connection.api_call(*args, **kwargs)