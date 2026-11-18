"""Dome device model: shutter/azimuth state and the derived dome status."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from ..nina_utils import ascom_float


class DomeDevice(BaseModel):
    """A connected dome as ``/equipment/info`` reports it.

    ``azimuth_deg`` is degrees (0–360) from north; the three driver flags
    (slewing/following/synchronized) are collapsed into ``dome_status`` by
    :func:`compute_dome_status` because N.I.N.A.'s payload exposes them only
    as separate booleans.
    """

    connected: bool
    name: str
    description: str | None = None
    is_homed: bool | None = None
    is_parked: bool | None = None
    azimuth_deg: float | None = None
    shutter_status: str | None = None
    dome_status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if not data.get("connected", False):
                return data

            data["is_parked"] = data.get("at_park", False)
            data["is_homed"] = data.get("at_home", False)

            azimuth = ascom_float(data.get("azimuth"))
            if azimuth is not None:
                data["azimuth_deg"] = azimuth

            slewing = data.get("slewing", False)
            is_following = data.get("is_following", False)
            is_synchronized = data.get("is_synchronized", False)
            data["dome_status"] = compute_dome_status(
                slewing, is_following, is_synchronized
            )

        return data


def compute_dome_status(
    slewing: bool, is_following: bool, is_synchronized: bool
) -> str:
    """Collapse the dome's driver flags into one status string.

    Priority is slew first (movement overrides everything), then not
    following (``IDLE``), then not synchronized with the scope
    (``SYNCING``), else ``READY``.
    """
    if slewing:
        return "SLEWING"
    if not is_following:
        return "IDLE"
    if not is_synchronized:
        return "SYNCING"
    return "READY"
