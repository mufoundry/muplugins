import datetime
import uuid

import pydantic
from rich.columns import Columns
from rich.console import Group

from ..connection import CoreConnection
from ..db.fields import RichText
from .base import EventBase

class TextEvent(EventBase):
    text: str

    async def handle_event(self, conn: "CoreConnection"):
        await conn.send_text(self.text)
    
    @classmethod
    def event_type(cls) -> str:
        return "text"

class RichTextEvent(EventBase):
    text: RichText

    async def handle_event(self, conn: "CoreConnection"):
        await conn.send_rich(self.text.text)
    
    @classmethod
    def event_type(cls) -> str:
        return "rich.text"


class RichColumns(EventBase):
    padding_min: int = 0
    padding_max: int = 5
    data: list[tuple[RichText, list[RichText]]] = pydantic.Field(default_factory=list)

    async def handle_event(self, conn: "CoreConnection"):
        cols = list()
        for title, items in self.data:
            col = Columns(
                [item.text for item in items],
                title=title.text,
                padding=(self.padding_min, self.padding_max),
                expand=True,
            )
            cols.append(col)
        await conn.send_rich(Group(*cols))

    @classmethod
    def event_type(cls) -> str:
        return "rich.columns"

# For the love of god let's not use this.
class GMCPEvent(EventBase):
    package: str
    data: dict

    async def handle_event(self, conn: "CoreConnection"):
        await conn.send_gmcp(self.package, self.data)

    @classmethod
    def event_type(cls) -> str:
        return "gmcp"