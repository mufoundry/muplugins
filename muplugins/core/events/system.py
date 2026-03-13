from ..connection import CoreConnection
from .base import EventBase


class SystemPing(EventBase):
    async def handle_event(self, conn: "CoreConnection"):
        pass
