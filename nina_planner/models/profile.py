from __future__ import annotations

import math

from pydantic import BaseModel, model_validator


class FilterInfo(BaseModel):
    name: str
    position: int | None = None
    focus_offset: int | None = None


class SiteLocationInfo(BaseModel):
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    elevation_m: float | None = None


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
    def _compute_fov(self):
        if (
            self.pixel_size_microns is not None
            and self.focal_length_mm is not None
            and self.camera_xsize_px is not None
            and self.camera_ysize_px is not None
            and self.camera_xsize_px > 0
            and self.camera_ysize_px > 0
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
