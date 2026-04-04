from .base import PortalCommand
from collections import defaultdict
from httpx import HTTPStatusError
from ..db.pcs import PCModel, ActiveAs
from ..db.users import UserModel

from muforge.utils.misc import partial_match

class _UserCommand(PortalCommand):
    parser_types = {"user"}
    help_category = "User"


class Create(_UserCommand):
    key = "core/user/create"
    name = "create"
    help_category = "User"
    match_defs = {"create": 1}

    async def func(self):
        if not (args := self.parsed.get("args", "")):
            await self.send_line("You must supply a name for your character.")
            return
        js_data = {"name": args}
        try:
            character_data = await self.api_call("POST", "/v1/pcs/", json=js_data)
        except HTTPStatusError as e:
            await self.send_line(f"Error creating character: {e.response.text}")
            return
        except Exception as e:
            await self.send_line(f"An unknown error occurred: {str(e)}")
            return
        character = PCModel(**character_data)
        await self.parser.handle_look()
        await self.send_line(f"Character {character.name} created.")


class Play(_UserCommand):
    key = "core/user/play"
    name = "play"
    help_category = "User"
    match_defs = {"play": 1}

    async def func(self):
        if not (args := self.parsed.get("args", "")):
            await self.send_line("You must supply a name for your character.")
            return
        user_id = self.connection.payload.get("sub")
        user_data = await self.api_call("GET", f"/v1/users/{user_id}")
        user = UserModel(**user_data)
        character_data = await self.api_call("GET", f"/v1/users/{user_id}/pcs")
        characters = [PCModel(**c) for c in character_data]

        if not (character := partial_match(args, characters, key=lambda c: c.name)):
            await self.send_line("Character not found.")
            return

        active = ActiveAs(user=user, pc=character)

        parser_class = self.parser.app.parsers["pc"]
        parser = parser_class(active)
        await self.connection.push_parser(parser)
    

class Logout(_UserCommand):
    key = "core/user/logout"
    name = "logout"
    help_category = "User"
    match_defs = {"logout": 3, "exit": 1}

    async def func(self):
        self.connection.jwt = None
        self.connection.payload = None
        self.connection.refresh_token = None
        await self.connection.pop_parser()