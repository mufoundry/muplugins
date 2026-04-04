import typing
import uuid
from collections import defaultdict
from pathlib import Path

import asyncpg
import orjson
from lark import Lark
from loguru import logger
from muforge.plugin import BasePlugin

from muplugins.core.session_commands.base import SessionCommand

from .database import INIT_SQL, Database
from .jwt import JWTManager
from .sessions import Session


def decode_json(data: bytes):
    decoded = orjson.loads(data)
    return decoded


async def init_connection(conn: asyncpg.Connection):
    for scheme in ("json", "jsonb"):
        await conn.set_type_codec(
            scheme,  # The PostgreSQL type to target.
            encoder=lambda v: orjson.dumps(v).decode("utf-8"),
            decoder=decode_json,
            schema="pg_catalog",
            format="text",
        )


async def perform_migrations(conn: asyncpg.Connection, app):
    # INIT_SQL creates the plugin_migrations table.
    await conn.execute(INIT_SQL)

    all_migrations = list()
    migrations = dict()

    for p in app.plugin_load_order:
        if not hasattr(p, "game_migrations"):
            continue
        mi = p.game_migrations()
        migrations[p.slug()] = mi
        for k, v in mi:
            all_migrations.append((p.slug(), k, v))

    migration_order: list[tuple[str, str, typing.Any]] = list()

    remaining_migrations = all_migrations.copy()

    resolved: set[tuple[str, str]] = set()

    while remaining_migrations:
        idx_remove = list()
        for i, m in enumerate(remaining_migrations):
            # each element in dep is a pair of (plugin_slug, migration_name)
            dep = getattr(m[2], "depends", list())
            has_deps = True
            for p_slug, m_name in dep:
                if (p_slug, m_name) not in resolved:
                    has_deps = False
                    break
            if has_deps:
                # We passed all checks.
                migration_order.append(m)
                resolved.add((m[0], m[1]))
                idx_remove.append(i)
        for i in reversed(idx_remove):
            remaining_migrations.pop(i)

    # We now have the list of sorted migrations to perform in order.
    # Some of them may have already been performed.

    performed = 0
    for plugin_slug, migration_name, migration in migration_order:
        exists = await conn.fetchrow(
            """
            SELECT applied_at FROM plugin_migrations
            WHERE plugin_slug = $1 AND migration_name = $2
        """,
            plugin_slug,
            migration_name,
        )
        if exists:
            continue
        up = getattr(migration, "upgrade", None)

        logger.info(f"Performing migration {migration_name} of plugin {plugin_slug}")

        # up can either be a string, none, or an async callable that should take the connection object.
        success = False
        if isinstance(up, str):
            await conn.execute(up)
            performed += 1
            success = True
        elif callable(up):
            await up(conn)
            performed += 1
            success = True
        else:
            logger.warning(
                f"Migration {migration_name} of plugin {plugin_slug} has no upgrade path. Skipping."
            )
            continue

        if success:
            await conn.execute(
                """
                INSERT INTO plugin_migrations (plugin_slug, migration_name, applied_at)
                VALUES ($1, $2, NOW())
            """,
                plugin_slug,
                migration_name,
            )

    logger.info(f"Performed {performed} migrations.")


class Core(BasePlugin):
    def __init__(self, app, settings=None):
        super().__init__(app, settings)
        self.crypt_context = None
        self.db = None
        self.active_sessions: dict[uuid.UUID, Session] = dict()
        
        # portal commands are only executed on the portal by telnet users.
        self.registered_portal_commands: dict[str, type] = dict()
        self.portal_commands_priority: dict[int, list[type]] = defaultdict(list)

        # Executed by the server, in the context of a session controlling a character.
        # they are not necessarily executed by in-game objects.
        self.registered_session_commands: dict[str, type] = dict()
        self.session_commands_priority: dict[int, list[type]] = defaultdict(list)

        self.lockparser = None
        self.lockfuncs: dict[str, typing.Awaitable] = dict()
        self.jwt_manager = None

        self.events: dict[str, type] = dict()
        self.events_reversed: dict[type, str] = dict()

    def name(self) -> str:
        return "MuForge Core"

    def slug(self) -> str:
        return "core"

    def version(self) -> str:
        return "0.0.1"

    def game_migrations(self) -> list[tuple[str, typing.Any]]:
        """
        Returns a list of tuples of (migration_name, migration_module)
        A migration module contains the following properties:

        upgrade, downgrade: either strings (SQL statements) or callables (async functions) that perform the migration.

        depends: a list of tuples of (plugin_slug, migration_name) that this migration depends on.
        The migrations will be run in the order of the dependencies.
        """
        from .migrations import version001

        return [("version001", version001)]

    def game_routers_v1(self) -> dict[str, typing.Any]:
        from .routers.auth import router as auth_router
        from .routers.pcs import router as pcs_router
        from .routers.users import router as users_router

        return {
            "auth": auth_router,
            "users": users_router,
            "pcs": pcs_router,
        }

    def game_static(self) -> str | None:
        return "static"

    def game_lockfuncs(self) -> dict[str, typing.Any]:
        return dict()

    def game_services(self) -> dict[str, type]:
        from .game_services.pinger import SystemPinger

        return {"system_pinger": SystemPinger}

    def portal_parsers(self) -> dict[str, type]:
        from .portal_parsers import AuthParser
        from .portal_parsers import PCParser
        from .portal_parsers import UserParser

        return {"auth": AuthParser, "user": UserParser, "pc": PCParser}

    def game_classes(self) -> dict[str, type]:
        from .sessions import Session

        return {"session": Session}

    def portal_classes(self) -> dict[str, type]:
        from .connection import CoreConnection

        return {"connection": CoreConnection}

    def core_events(self) -> dict[str, type]:
        from .events.messages import RichColumns, RichTextEvent, TextEvent
        from .events.system import SystemPing

        all_events = [SystemPing, TextEvent, RichTextEvent, RichColumns]

        return {ev.event_type(): ev for ev in all_events}

    async def setup_events(self):
        for p in self.app.plugin_load_order:
            if not hasattr(p, "core_events"):
                continue
            self.events.update(p.core_events())
        for k, v in self.events.items():
            self.events_reversed[v] = k

    async def setup_final(self, app_name: str):
        await self.setup_events()
        self.jwt_manager = JWTManager(self)

        match app_name:
            case "game":
                self.app.fastapi_instance.state.core = self
                
                await self.setup_crypt()
                await self.setup_database()
                await self.setup_lockfuncs()
                await self.setup_session_commands()
            case "portal":
                await self.setup_portal_commands()

    async def setup_crypt(self):
        from passlib.context import CryptContext

        self.crypt_context = CryptContext(**self.settings.get("crypt", {}))

    async def setup_lark(self):
        grammar = Path.cwd() / "grammar.lark"
        with open(grammar, "r") as f:
            data = f.read()
            self.lockparser = Lark(data)

    async def setup_database(self):
        postgre_settings = self.settings["postgresql"]
        pool = await asyncpg.create_pool(init=init_connection, **postgre_settings)
        self.db = Database(pool)

        async with self.db.transaction() as conn:
            await perform_migrations(conn, self.app)

    async def setup_lockfuncs(self):
        for p in self.app.plugin_load_order:
            self.lockfuncs.update(p.game_lockfuncs())

    def portal_commands(self) -> list["PortalCommand"]:
        out = list()
        from .portal_commands.universal import Help, MSSP, Quit
        out.extend([Help, MSSP, Quit])

        from .portal_commands.auth import Login, Register
        out.extend([Login, Register])

        from .portal_commands.user import Create, Play
        out.extend([Create, Play])

        return out

    def session_commands(self) -> list["SessionCommand"]:
        out = list()
        
        from .session_commands.system import Py
        out.append(Py)

        return out

    async def setup_portal_commands(self):
        for p in self.app.plugin_load_order:
            # first gather all commands.
            if not hasattr(p, "portal_commands"):
                continue
            for command in p.portal_commands():
                self.registered_portal_commands[command.key] = command
        
        # sort by priority
        for command in self.registered_portal_commands.values():
            self.portal_commands_priority[command.priority].append(command)
        for v in self.portal_commands_priority.values():
            v.sort(key=lambda c: c.key)

    async def setup_session_commands(self):
        for p in self.app.plugin_load_order:
            if not hasattr(p, "session_commands"):
                continue
            for command in p.session_commands():
                self.registered_session_commands[command.key] = command
        
        # sort by priority
        for command in self.registered_session_commands.values():
            self.session_commands_priority[command.priority].append(command)
        for v in self.session_commands_priority.values():
            v.sort(key=lambda c: c.key)


__all__ = ["Core"]
