"""The active profile's custom horizon: fetch, parse, and interpolate it.

N.I.N.A. stores a custom horizon as azimuth/altitude pairs (a ``.hrz`` file
referenced from the profile's ``AstrometrySettings.HorizonFilePath``) and
exposes the parsed values on ``GET /profile/horizon``. The same skyline
gates the sequencer's altitude conditions: ``Loop while Altitude Above
Horizon`` / ``Wait until Above Horizon`` compare a target's altitude against
the horizon at the target's azimuth plus an offset, so planning tools must
apply the same floor or they disagree with what N.I.N.A. actually does --
e.g. ranking a target `visible_now` that the sequence would park on.

With no horizon file configured the endpoint answers with empty arrays and
the effective horizon is flat 0 degrees: ``is_custom`` is false and
``altitude_at`` returns 0, so every caller degrades to exactly the behavior
it had before this module existed.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator

from . import client

# N.I.N.A.'s horizon file is altitude in degrees; clamp so a malformed file
# can never produce an impossible floor (or a negative pit under the site).
_MAX_HORIZON_ALT = 90.0


class Horizon(BaseModel):
    """A profile's custom horizon as sorted ``(azimuth_deg, altitude_deg)`` pairs."""

    file_path: str = ""
    points: list[tuple[float, float]] = Field(default_factory=list)

    @field_validator("points", mode="before")
    @classmethod
    def _sort_and_wrap(cls, value: Any) -> Any:
        """Wrap azimuths into [0, 360), drop unusable pairs, sort, dedupe.

        N.I.N.A. files conventionally close the circle with a ``360`` entry
        mirroring ``0``; wrapping makes those a single point (the later
        entry wins), which is what the interpolation seam expects. Pairs
        that cannot be read as two finite numbers are dropped rather than
        allowed to poison the whole skyline.
        """
        if not isinstance(value, (list, tuple)):
            return []
        merged: dict[float, float] = {}
        for pair in value:
            try:
                az, alt = pair  # type: ignore[misc]
                az_f, alt_f = float(az), float(alt)
            except (TypeError, ValueError):
                continue
            if math.isfinite(az_f) and math.isfinite(alt_f):
                merged[az_f % 360.0] = alt_f
        return sorted(merged.items())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_custom(self) -> bool:
        """True when a usable custom horizon exists.

        N.I.N.A. requires at least two azimuth/altitude pairs for a horizon
        file to take effect, so one point (or none) means the flat 0 degree
        default.
        """
        return len(self.points) >= 2

    def altitude_at(self, az_deg: float) -> float:
        """Skyline altitude at ``az_deg``, linearly interpolated.

        N.I.N.A.'s rule: azimuths without an explicit point interpolate
        from the existing datapoints, wrapping across the 360 -> 0 seam.
        Returns 0.0 without a usable custom horizon, and clamps to
        [-90, 90].
        """
        pts = self.points
        if len(pts) < 2:
            return 0.0
        az = az_deg % 360.0
        for i in range(len(pts) - 1):
            a0, h0 = pts[i]
            a1, h1 = pts[i + 1]
            if a0 <= az <= a1:
                t = (az - a0) / (a1 - a0)
                return _clamp(h0 + (h1 - h0) * t)
        # Seam: past the last point (or before the first) the arc runs from
        # the last knot to the first knot + 360.
        a0, h0 = pts[-1]
        a1, h1 = pts[0][0] + 360.0, pts[0][1]
        az_u = az if az >= a0 else az + 360.0
        return _clamp(h0 + (h1 - h0) * (az_u - a0) / (a1 - a0))


def _clamp(alt: float) -> float:
    return max(-_MAX_HORIZON_ALT, min(_MAX_HORIZON_ALT, alt))


def parse_horizon(payload: Any, file_path: str = "") -> Horizon:
    """Build a Horizon from a ``/profile/horizon`` response body.

    Anything that is not the expected ``{Azimuths, Altitudes}`` shape (an
    older Advanced API answering something else, a mocked profile payload)
    parses to a flat horizon rather than raising -- the horizon is advisory
    detail on top of a working profile, never a reason to fail one.
    """
    if not isinstance(payload, dict):
        return Horizon(file_path=file_path)
    azs = payload.get("Azimuths")
    alts = payload.get("Altitudes")
    if not isinstance(azs, list) or not isinstance(alts, list):
        return Horizon(file_path=file_path)
    # zip truncates to the shorter array (mismatched lengths are the
    # caller's bug, not a reason to fail the profile); the points validator
    # does the numeric filtering, sorting, and 360 -> 0 dedupe.
    return Horizon(file_path=file_path, points=list(zip(azs, alts, strict=False)))


async def fetch_horizon(file_path: str = "") -> Horizon:
    """Read the active profile's horizon from ``GET /profile/horizon``.

    Best effort: if the endpoint is missing (an older Advanced API build)
    or N.I.N.A. is mid-restart, the failure is logged and a flat horizon
    is returned, because a working ``get_site_profile`` matters more than
    the skyline.
    """
    try:
        payload = await client.api_get("/profile/horizon")
    except Exception as exc:
        client.log_payload("fetch_horizon:", f"flat horizon ({exc})")
        return Horizon(file_path=file_path)
    return parse_horizon(payload, file_path=file_path)
