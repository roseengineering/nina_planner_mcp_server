from __future__ import annotations

from pydantic import BaseModel, Field


class PointingInfo(BaseModel):
    ra_hours: float | None = None
    dec_deg: float | None = None
    epoch: str | None = None
    azimuth_deg: float | None = None
    altitude_deg: float | None = None


class Pointing(BaseModel):
    label: str | None = None
    ra_hours: float = Field(
        ge=0.0,
        lt=24.0,
        description="J2000 right ascension in hours [0.0, 24.0).",
    )
    dec_deg: float = Field(
        ge=-90.0,
        le=90.0,
        description="J2000 declination in degrees [-90.0, +90.0].",
    )
    position_angle_deg: float | None = Field(
        default=None,
        ge=0.0,
        lt=360.0,
        description="Rotator position angle in degrees [0.0, 360.0), measured east of north. "
        "Omitted (None) forwards 0 to NINA, leaving the rotator at its current/synced position.",
    )
