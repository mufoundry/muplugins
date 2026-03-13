import asyncio
from datetime import datetime, timezone

import orjson

from .db.pcs import ActiveAs
from .events.base import EventBase


class Session:
    __slots__ = (
        "core",
        "actingactive",
        "created_at",
        "last_active_at",
        "subscriptions",
        "active",
        "task_group",
        "shutdown_event",
    )

    def __init__(self, core, acting: ActiveAs):
        self.core = core
        self.acting = acting
        # User and PC are filled in after the session is created.
        self.created_at = datetime.now(timezone.utc)
        self.last_active_at = datetime.now(timezone.utc)
        self.subscriptions: list[asyncio.Queue] = []
        self.active = False
        self.task_group = None
        self.shutdown_event = asyncio.Event()

    @property
    def app(self):
        return self.core.app

    @property
    def user(self):
        return self.acting.user

    @property
    def pc(self):
        return self.acting.pc

    async def send_event(self, event: EventBase) -> None:
        for q in self.subscriptions:
            await q.put(event)

    def send_event_nowait(self, event: EventBase) -> None:
        for q in self.subscriptions:
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        """Create a new queue for this character and add it to the subscription list."""
        q = asyncio.Queue()
        self.subscriptions.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Remove the given queue from this session's subscription list."""
        try:
            self.subscriptions.remove(q)
        except ValueError:
            pass

    async def handle_postgre_notification(self, conn, pid, channel, payload):
        try:
            data = orjson.loads(payload)
        except Exception:
            return

        if data.get("table") != "pc_events":
            return

        if str(data.get("pc_id")) != str(self.pc.id):
            return

        event_type = data.get("event_type")
        if not event_type:
            return

        ev_class = self.core.events.get(event_type)
        if not ev_class:
            return

        ev_data = data.get("data") or {}
        if "happened_at" not in ev_data and data.get("created_at"):
            ev_data = dict(ev_data)
            ev_data["happened_at"] = data["created_at"]

        try:
            event = ev_class(**ev_data)
        except Exception:
            return

        await self.send_event(event)

    async def listen_events(self):
        async with self.core.db.connection() as conn:
            await conn.add_listener("table_changes", self.handle_postgre_notification)
            # Do nothing in order to keep the listener running.
            while True:
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    break

    async def run(self):
        async with asyncio.TaskGroup() as tg:
            self.task_group = tg
            await tg.create_task(self.listen_events())

            await self.shutdown_event.wait()

    async def start(self):
        """
        Start the session. Should do login things.

        """
        self.active = True
        self.core.app.task_group.create_task(self.run())

    async def stop_local(self):
        for q in self.subscriptions:
            await q.put(None)

    async def stop(self, graceful: bool = True):
        if not self.active:
            return
