import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pydantic
import typing
from pydantic import EmailStr, SecretStr
from asyncpg import Connection
from asyncpg.exceptions import UniqueViolationError
from fastapi import HTTPException, status

from .fields import username
from .users import UserModel

class UserRegistration(pydantic.BaseModel):
    email: EmailStr
    password: SecretStr
    username: typing.Optional[username] = None


class UserLogin(pydantic.BaseModel):
    login: username | EmailStr
    password: pydantic.SecretStr


class TokenResponse(pydantic.BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

    @classmethod
    def from_str(cls, manager, sub: str) -> "TokenResponse":
        token = manager.create_token(sub)
        refresh = manager.create_refresh(sub)
        return cls(access_token=token, refresh_token=refresh, token_type="bearer")

    @classmethod
    def from_uuid(cls, manager, id: uuid.UUID) -> "TokenResponse":
        sub = str(id)
        return cls.from_str(manager, sub)


class RefreshTokenModel(pydantic.BaseModel):
    refresh_token: str


# meant to be run in a Transaction.
async def register_user(
    conn: Connection, crypt_context, registration: UserRegistration
) -> UserModel:
    admin_level = 0

    try:
        hashed = crypt_context.hash(registration.password.get_secret_value())
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Error hashing password."
        )

    # if there are no users, make this user an admin.
    if not (await conn.fetchrow("SELECT id FROM users")):
        admin_level = 10

    try:
        # Insert the new user.
        user_row = await conn.fetchrow(
            """
            INSERT INTO users (email, admin_level, password_hash)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            registration.email,
            admin_level,
            hashed,
        )
        if registration.username:
            user_row = await conn.fetchrow(
                """
                UPDATE users SET username=$1 WHERE id=$2 RETURNING *
                """,
                registration.username,
                user_row["id"],
            )
    except UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already exists.",
        )
    user = UserModel(**user_row)

    return user


# Meant to be run in a Transaction.
async def authenticate_user(
    conn: Connection,
    crypt_context,
    email: EmailStr | username,
    password: SecretStr,
    ip: str,
    user_agent: str | None,
) -> UserModel:
    # Retrieve the latest password row for this user.
    retrieved_user = await conn.fetchrow(
        """
        SELECT *
        FROM users
        WHERE email = $1::citext OR username = $2::citext LIMIT 1
        """,
        email,
        email,
    )

    if not retrieved_user:
        raise HTTPException(status_code=400, detail="Invalid credentials.")

    user_id = retrieved_user["id"]

    pass_hash = retrieved_user["password_hash"]

    if not (pass_hash and crypt_context.verify(password, pass_hash)):
        await conn.execute(
            """
            INSERT INTO loginrecords (user_id, ip_address, success, user_agent)
            VALUES ($1, $2, $3, $4)
            """,
            user_id,
            ip,
            False,
            user_agent,
        )
        raise HTTPException(status_code=400, detail="Invalid credentials.")

    if crypt_context.needs_update(pass_hash):
        try:
            hashed = crypt_context.hash(password)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Error hashing password.",
            )
        password_row = await conn.fetchrow(
            """
            UPDATE users SET password_hash=$1 WHERE id=$2 RETURNING id
            """,
            user_id,
            hashed,
        )

    # Record successful login.
    await conn.execute(
        """
        INSERT INTO loginrecords (user_id, ip_address, success, user_agent)
        VALUES ($1, $2, $3, $4)
        """,
        retrieved_user["id"],
        ip,
        True,
        user_agent,
    )

    return UserModel(**retrieved_user)
