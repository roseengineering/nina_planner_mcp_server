from __future__ import annotations

import math
from typing import Self

from pydantic import BaseModel, computed_field, model_validator

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

    @model_validator(mode="after")
    def _compute_fov(self) -> Self:
        if (
            self.pixel_size_microns is not None
            and self.focal_length_mm is not None
            and self.camera_xsize_px is not None
            and self.camera_ysize_px is not None
            and self.camera_xsize_px > 0
            and self.camera_ysize_px > 0
            and self.focal_length_mm > 0
            and self.pixel_size_microns > 0
        ):
            sensor_width_mm = self.camera_xsize_px * self.pixel_size_microns / 1000.0
            sensor_height_mm = self.camera_ysize_px * self.pixel_size_microns / 1000.0
            fov_x = (
                2
                * math.atan2(sensor_width_mm / 2, self.focal_length_mm)
                * 180
                / math.pi
            )
            fov_y = (
                2
                * math.atan2(sensor_height_mm / 2, self.focal_length_mm)
                * 180
                / math.pi
            )
            self.field_of_view_deg = (round(fov_x, 4), round(fov_y, 4))
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
    image_save_path: str
    file_pattern: str | None = None
    equipment: EquipmentConfig

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
