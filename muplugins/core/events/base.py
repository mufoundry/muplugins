import datetime

import pydantic

from ..connection import CoreConnection


class EventBase(pydantic.BaseModel):
    happened_at: datetime.datetime = pydantic.Field(
        default_factory=datetime.datetime.now
    )
    """
    Base class for all events.
    """

    async def handle_event(self, conn: "CoreConnection"):
        pass

    async def handle_event_parser(self, parser: "BaseParser"):
        pass

    @classmethod
    def event_type(cls) -> str:
        raise NotImplementedError("Subclasses must implement event_type()")