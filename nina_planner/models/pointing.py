from __future__ import annotations

from pydantic import BaseModel


class PointingInfo(BaseModel):
    ra_hours: float | None = None
    dec_deg: float | None = None
    epoch: str | None = None
    azimuth_deg: float | None = None
    altitude_deg: float | None = None
