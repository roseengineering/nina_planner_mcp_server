from __future__ import annotations

from pydantic import BaseModel, model_validator

from ..nina_utils import ascom_float


class Weather(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    temperature_c: float | None = None
    dew_point_c: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    wind_speed_mps: float | None = None
    wind_gust_mps: float | None = None
    wind_direction_deg: float | None = None
    sky_temperature_c: float | None = None
    sky_quality_mpsas: float | None = None
    rain_rate_mm_per_hr: float | None = None
    cloud_cover_pct: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            data["temperature_c"] = ascom_float(data.get("temperature"))
            data["dew_point_c"] = ascom_float(data.get("dew_point"))
            data["humidity_pct"] = ascom_float(data.get("humidity"))
            data["pressure_hpa"] = ascom_float(data.get("pressure"))
            data["wind_speed_mps"] = ascom_float(data.get("wind_speed"))
            data["wind_gust_mps"] = ascom_float(data.get("wind_gust"))
            data["wind_direction_deg"] = ascom_float(data.get("wind_direction"))
            data["sky_temperature_c"] = ascom_float(data.get("sky_temperature"))
            data["sky_quality_mpsas"] = ascom_float(data.get("sky_quality"))
            data["rain_rate_mm_per_hr"] = ascom_float(data.get("rain_rate"))
            data["cloud_cover_pct"] = ascom_float(data.get("cloud_cover"))
        return data
