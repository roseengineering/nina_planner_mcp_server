"""Pointing models: one profile/plan target's J2000 coordinates and rotation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .strict import StrictModel


class PointingInfo(BaseModel):
    """A reported pointing: J2000 RA/dec plus horizon angles, all nullable."""

    ra_hours: float | None = Field(
        default=None,
        ge=0.0,
        lt=24.0,
        description="J2000 right ascension in hours [0.0, 24.0).",
    )
    dec_deg: float | None = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="J2000 declination in degrees [-90.0, +90.0].",
    )
    epoch: str | None = None
    azimuth_deg: float | None = None
    altitude_deg: float | None = None


class Pointing(StrictModel):
    """One mosaic pane/pointing of a plan: target coordinates plus layout.

    ``ra_hours``/``dec_deg`` are J2000 (validated ranges in the field
    descriptions); ``row``/``column`` label the mosaic grid and are never
    sent to N.I.N.A.
    """

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
        description=(
            "Rotator position angle in degrees [0.0, 360.0), measured east of north. "
            "Omitted (None) forwards 0 to NINA, leaving the rotator at its "
            "current/synced position. 0 means the same thing — no rotation is "
            "requested (see requests_rotation), so it is valid with or without "
            "a rotator."
        ),
    )
    row: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Mosaic pane row. Not sent to NINA; kept for labeling, ordering, "
            "and post-processing mosaic assembly."
        ),
    )
    column: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Mosaic pane column. Not sent to NINA; kept for labeling, ordering, "
            "and post-processing mosaic assembly."
        ),
    )

    def requests_rotation(self) -> bool:
        """True iff this pointing asks N.I.N.A. to move the rotator.

        THE rule for "does this pointing rotate?", stated once so validation
        and emission can never disagree: ``None`` → no rotation (the field
        docs' "leave the rotator synced"), ``0`` → also no rotation (a value
        ``plan_mosaic`` stamps on every pane as a north-aligned grid, not a
        request), any nonzero value → rotate. Emission must consult this
        rule rather than ``is not None``: keying off that would send every
        mosaic pane on a rotator-less rig to ``CenterAndRotate``, a
        platesolving item that needs a rotator.
        """
        return self.position_angle_deg is not None and self.position_angle_deg != 0
