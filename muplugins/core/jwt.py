from datetime import datetime, timedelta, timezone

import jwt


class JWTManager:
    def __init__(self, plugin):
        self.plugin = plugin
        self.jwt_settings = plugin.settings["jwt"]

    def _create_token(self, sub: str, expires: datetime, refresh: bool = False):
        data = {
            "sub": sub,
            "exp": expires,
            "iat": datetime.now(tz=timezone.utc),
        }
        if refresh:
            data["refresh"] = True

        return jwt.encode(
            data, self.jwt_settings["secret"], algorithm=self.jwt_settings["algorithm"]
        )

    def create_token(self, sub: str):
        return self._create_token(
            sub,
            datetime.now(tz=timezone.utc)
            + timedelta(minutes=self.jwt_settings["token_expire_minutes"]),
        )

    def create_refresh(self, sub: str):
        return self._create_token(
            sub,
            datetime.now(tz=timezone.utc)
            + timedelta(minutes=self.jwt_settings["refresh_expire_minutes"]),
            True,
        )

    def decode_token(self, token: str):
        return jwt.decode(
            token,
            self.jwt_settings["secret"],
            algorithms=[self.jwt_settings["algorithm"]],
        )
