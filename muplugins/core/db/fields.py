from typing import Annotated, Optional

from pydantic import AfterValidator, constr, field_serializer
from rich.text import Text

from . import validators

name_line = constr(strip_whitespace=True, min_length=1, max_length=255)
optional_name_line = Optional[name_line]
rich_text = Annotated[str, AfterValidator(validators.rich_text)]
optional_rich_text = Annotated[
    Optional[str], AfterValidator(validators.optional_rich_text)
]

locks = Annotated[dict[str, str], AfterValidator(validators.locks)]
optional_locks = Annotated[
    Optional[dict[str, str]], AfterValidator(validators.optional_locks)
]

username = Annotated[str, AfterValidator(validators.NameSanitizer("username"))]
pc_name = Annotated[
    str, AfterValidator(validators.NameSanitizer("Player Character name"))
]

class RichText(str):
    def __init__(self, value: str | Text):
        super().__init__(value)
        self._text = Text.from_markup(value) if not isinstance(value, Text) else value
    
    @property
    def text(self) -> Text:
        return self._text
    
    @field_serializer('value')
    def serialize(self) -> str:
        return self._text.markup