import asyncio

from muforge.application import Service

from ..events.system import SystemPing


class SystemPinger(Service):
    async def run(self):
        try:
            while True:
                for k, v in self.plugin.active_sessions.items():
                    await v.send_event(SystemPing())
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            return
