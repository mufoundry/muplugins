import re
import typing

from muplugins.core.db.pcs import ActiveAs, PCModel
from muplugins.core.db.users import UserModel

if typing.TYPE_CHECKING:
    from ..sessions import Session

CMD_MATCH = re.compile(
    r"(?s)^(?P<cmd>\S+?)(?:/(?P<switches>\S+)?)?(?P<fullargs> +(?P<args>(?P<lsargs>.+?)(?:=(?P<rsargs>.*))?)?)?$"
)


class SessionCommand:
    """
    Help not implemented for this command. Contact staff!
    """

    # The unique key for this command. This is used for identifying it,
    # but also for overriding it with plugins.
    key = "core/notset"
    name = "!NOTSET!"
    # If help_category is None, the command will not be listed in the help system.
    help_category = "Uncategorized"
    priority = 0
    aliases = dict()
    min_level = 0
    # Set this to true if you want the command to exist but never reach the parser.
    # this could be helpful for creating help files or meta-topics.
    unusable = False

    class Error(ValueError):
        pass

    @classmethod
    def check_match(cls, session: Session, command: str) -> typing.Optional[dict[str, str]]:
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
        
        if command == cls.name:
            return match_dict
        for k, v in cls.aliases.items():
            if command == k:
                return match_dict
            if len(command) >= v and command.startswith(k):
                return match_dict
        return None

    @classmethod
    def check_access(cls, session: Session) -> bool:
        """
        Check if the user should have access to the command.
        If they don't, they don't see it at all.

        Args:
            enactor: The user to check access for.

        Returns:
            bool: True if the user has access, False otherwise.
        """
        return session.user.admin_level >= cls.min_level

    def __init__(
        self,
        session: Session,
        raw: str,
        parsed: dict[str, str],
    ):
        self.session = session
        self.raw = raw
        self.parsed = parsed

    @property
    def enactor(self) -> ActiveAs:
        return self.session.acting

    @property
    def user(self) -> UserModel:
        return self.enactor.user

    @property
    def pc(self) -> PCModel:
        return self.enactor.pc

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
        await self.session.send_text(text)

    async def send_line(self, text: str):
        await self.session.send_line(text)

    async def send_data(self, package: str, data):
        await self.session.send_data(package, data)
