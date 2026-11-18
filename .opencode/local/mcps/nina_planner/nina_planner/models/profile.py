from __future__ import annotations

import math
from typing import Self

from pydantic import BaseModel, computed_field, model_validator

from ..horizon import Horizon
from ..imaging import windows_to_local


class FilterInfo(BaseModel):
    name: str
    position: int
    focus_offset: int | None = None


class SiteLocationInfo(BaseModel):
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    elevation_m: float | None = None


class EquipmentConfig(BaseModel):
    has_mount: bool
    has_camera: bool
    has_focuser: bool
    has_guider: bool
    has_filter_wheel: bool
    has_flat_device: bool
    has_rotator: bool
    has_dome: bool
    has_weather: bool
    has_safety_monitor: bool
    has_switch: bool


class OpticalTrainInfo(BaseModel):
    telescope_name: str | None = None
    focal_length_mm: float | None = None
    focal_ratio: float | None = None
    pixel_size_microns: float | None = None
    default_gain: int | None = None
    camera_xsize_px: int | None = None
    camera_ysize_px: int | None = None
    field_of_view_deg: tuple[float, float] | None = None

    def fov_deg(self) -> tuple[float, float] | None:
        """The field of view ``(width, height)`` in degrees, unrounded.

        The one place the FOV trig lives: the profile's ``field_of_view_deg``
        and ``mosaic.pane_fov_arcmins`` both read it. ``None`` when the
        optics needed for the computation are missing or non-positive.
        """
        focal_length = self.focal_length_mm
        pixel_size = self.pixel_size_microns
        xsize = self.camera_xsize_px
        ysize = self.camera_ysize_px
        if (
            focal_length is None
            or pixel_size is None
            or xsize is None
            or ysize is None
            or focal_length <= 0
            or pixel_size <= 0
            or xsize <= 0
            or ysize <= 0
        ):
            return None
        sensor_width_mm = xsize * pixel_size / 1000.0
        sensor_height_mm = ysize * pixel_size / 1000.0
        fov_x = 2.0 * math.atan2(sensor_width_mm / 2.0, focal_length)
        fov_y = 2.0 * math.atan2(sensor_height_mm / 2.0, focal_length)
        return (math.degrees(fov_x), math.degrees(fov_y))

    @model_validator(mode="after")
    def _compute_fov(self) -> Self:
        fov = self.fov_deg()
        if fov is not None:
            self.field_of_view_deg = (round(fov[0], 4), round(fov[1], 4))
        return self


class ObservatoryProfile(BaseModel):
    profile_name: str
    profile_id: str
    description: str | None = None
    site: SiteLocationInfo
    optics: OpticalTrainInfo
    filters: list[FilterInfo]
    plate_solver: str | None = None
    blind_plate_solver: str | None = None
    guider_name: str | None = None
    image_save_path: str
    file_pattern: str | None = None
    equipment: EquipmentConfig
    # The profile's custom horizon (empty points = flat 0 deg). Read from
    # GET /profile/horizon by get_site_profile; planning tools use it so
    # their altitude floor matches what N.I.N.A.'s loop conditions enforce.
    horizon: Horizon = Horizon()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def local_image_save_path(self) -> str:
        """`image_save_path` resolved to a path usable on this host.

        On Windows this is the profile path unchanged; on Linux and WSL the
        drive letter is mapped through `NINA_DRIVE_MOUNT` (default
        `/mnt/<drive>`) so the imaging directory can be read locally.
        """
        return str(windows_to_local(self.image_save_path))

    def filter_position(self, name: str) -> int:
        filt = next((f for f in self.filters if f.name == name), None)
        if filt is None:
            raise ValueError(
                f"Filter {name!r} not found in profile {self.profile_name!r}. "
                f"Available: {[f.name for f in self.filters]}"
            )
        return filt.position
