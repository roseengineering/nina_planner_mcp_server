from __future__ import annotations

from pydantic import BaseModel


class MeridianInfo(BaseModel):
    hours_to_flip: float | None = None
    side_of_pier: str | None = None
