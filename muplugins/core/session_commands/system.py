from .base import SessionCommand
from ..session_parsers.repl import REPLParser

class Py(SessionCommand):
    """
    Please note, this command is INSANELY dangerous as it exposes direct access to the running Python interpreter!
    This should ONLY EVER be used by developers!
    """
    key = "core/python"
    name = "python"
    aliases = {"python": 2}
    help_category = "Debug"
    min_level = 10

    async def func(self):
        await self.session.add_parser(REPLParser(self.session))