from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EywaSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )
