
from .base import PortalCommand
from httpx import HTTPStatusError
from pydantic import ValidationError
from ..routers.auth import TokenResponse, UserLogin, UserRegistration

class _AuthCommand(PortalCommand):
    parser_types = {"auth"}
    help_category = "Authentication"

class Register(_AuthCommand):
    key = "core/auth/register"
    name = "register"
    help_category = "Authentication"
    parser_types = {"auth"}
    match_defs = {"register": 1}

    async def func(self):
        lsargs = self.parsed.get("lsargs", "")
        rsargs = self.parsed.get("rsargs", "")

        if not (lsargs and rsargs):
            await self.send_line("Usage: register <email>=<password>")
            return
        
        try:
            u = UserRegistration(email=lsargs, password=rsargs)
        except ValidationError as e:
            await self.send_line(f"Invalid registration credentials: {e}")
            return

        try:
            data = {"email": u.email, "password": u.password.get_secret_value()}
            json_data = await self.api_call("POST", "/v1/auth/register", json=data)
        except HTTPStatusError as e:
            # there's a detail field in the response that should have more info about why it failed.
            try:
                error_detail = e.response.json().get("detail", "")
                await self.send_line(f"Registration failed: {error_detail}")
            except Exception:
                await self.send_line(f"Registration failed: {e}")
            return
        token = TokenResponse(**json_data)
        await self.connection.handle_login(token)

class Login(_AuthCommand):
    key = "core/auth/login"
    help_name = "login"
    match_defs = {"login": 1}

    async def func(self):
        lsargs = self.parsed.get("lsargs", "")
        rsargs = self.parsed.get("rsargs", "")

        if not (lsargs and rsargs):
            await self.send_line("Usage: login <email>=<password>")
            return
        
        try:
            u = UserLogin(username=lsargs, password=rsargs)
        except ValidationError as e:
            await self.send_line(f"Invalid login credentials: {e}")
            return
        # this uses the /auth/register endpoint... which should give us a TokenResponse.

        data = {
            "username": u.username,
            "password": u.password.get_secret_value(),
            "grant_type": "password",
        }
        try:
            json_data = await self.api_call("POST", "/v1/auth/login", data=data)
        except HTTPStatusError as e:
            await self.send_line(f"Login failed: {e}")
            return
        token = TokenResponse(**json_data)
        await self.connection.handle_login(token)