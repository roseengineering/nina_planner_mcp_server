from __future__ import annotations

from pydantic import BaseModel


class FocuserDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    is_moving: bool | None = None
    is_settling: bool | None = None
    position: int | None = None
    temperature_c: float | None = None
    step_size_microns: int | None = None
    temp_comp: bool | None = None
