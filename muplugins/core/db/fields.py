from typing import Optional, Annotated, Any
from pydantic import BaseModel, Field, GetPydanticSchema
from pydantic_core import core_schema
from pydantic import AfterValidator, constr
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

def _rich_text_schema(_source_type: Any, _handler: Any) -> core_schema.CoreSchema:
    def validate(value: Any) -> Text:
        if isinstance(value, Text):
            return value
        if isinstance(value, str):
            return Text.from_markup(value)
        raise TypeError("Expected a rich.text.Text or a markup string")

    return core_schema.no_info_plain_validator_function(
        validate,
        serialization=core_schema.plain_serializer_function_ser_schema(
            lambda value: value.markup,
            return_schema=core_schema.str_schema(),
        ),
    )


RichText = Annotated[
    Text,
    GetPydanticSchema(_rich_text_schema),
]

class TestRichTextModel(BaseModel):
    text: RichText = Field(default_factory=Text)