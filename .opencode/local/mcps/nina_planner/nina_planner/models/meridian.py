"""Meridian-flip timing model: hours until flip and side of pier."""

from __future__ import annotations

from pydantic import BaseModel


class MeridianInfo(BaseModel):
    """Meridian-flip state; ``hours_to_flip`` in hours, ``None`` when none pending."""

    hours_to_flip: float | None = None
    side_of_pier: str | None = None
