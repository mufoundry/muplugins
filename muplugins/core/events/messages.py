import datetime
import uuid

import pydantic
from rich.columns import Columns
from rich.console import Group

from ..connection import CoreConnection
from ..db.fields import rich_text
from .base import EventBase


class RichText(EventBase):
    text: rich_text

    async def handle_event(self, conn: "CoreConnection"):
        await conn.send_rich(self.text)


class RichColumns(EventBase):
    padding_min: int = 0
    padding_max: int = 5
    data: list[tuple[str, list[str]]] = pydantic.Field(default_factory=list)

    async def handle_event(self, conn: "CoreConnection"):
        cols = list()
        for title, items in self.data:
            col = Columns(
                items,
                title=title,
                padding=(self.padding_min, self.padding_max),
                expand=True,
            )
            cols.append(col)
        await conn.send_rich(Group(*cols))
