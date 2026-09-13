from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from ..nina_utils import ascom_float
from .meridian import MeridianInfo
from .pointing import PointingInfo


class MountDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    alignment_mode: str | None = None
    is_parked: bool | None = None
    is_homed: bool | None = None
    mount_status: str | None = None
    meridian: MeridianInfo = Field(default_factory=MeridianInfo)
    pointing: PointingInfo = Field(default_factory=PointingInfo)

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if not data.get("connected", False):
                return data

            data["is_parked"] = data.get("at_park", False)
            data["is_homed"] = data.get("at_home", False)

            slewing = data.get("slewing", False)
            tracking = data.get("tracking_enabled", False)
            if slewing:
                data["mount_status"] = "SLEWING"
            elif tracking:
                data["mount_status"] = "TRACKING"
            else:
                data["mount_status"] = "IDLE"

            coords = data.get("coordinates", {}) or {}
            ra = ascom_float(coords.get("ra")) or ascom_float(coords.get("ra_degs"))
            dec = ascom_float(coords.get("dec"))
            if ra is None and dec is None:
                ra = ascom_float(data.get("right_ascension"))
                dec = ascom_float(data.get("declination"))
            if ra is not None:
                ra = ra / 15.0

            data["pointing"] = PointingInfo(
                ra_hours=ra,
                dec_deg=dec,
                epoch=coords.get("epoch"),
                azimuth_deg=ascom_float(data.get("azimuth")),
                altitude_deg=ascom_float(data.get("altitude")),
            )

            data["meridian"] = MeridianInfo(
                hours_to_flip=ascom_float(data.get("time_to_meridian_flip")),
                side_of_pier=data.get("side_of_pier"),
            )
            if data["meridian"].hours_to_flip == 24.0:
                data["meridian"].hours_to_flip = None

        return data
