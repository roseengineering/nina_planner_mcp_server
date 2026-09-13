from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from ..nina_utils import ascom_float


class GuiderMetric(BaseModel):
    arcseconds: float | None = None
    pixel: float | None = None


class GuiderDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    state: str | None = None
    pixel_scale: float | None = None
    ra_rms_error: GuiderMetric = Field(default_factory=GuiderMetric)
    dec_rms_error: GuiderMetric = Field(default_factory=GuiderMetric)
    total_rms_error: GuiderMetric = Field(default_factory=GuiderMetric)
    peak_ra_excursion: GuiderMetric = Field(default_factory=GuiderMetric)
    peak_dec_excursion: GuiderMetric = Field(default_factory=GuiderMetric)

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            data["state"] = data.get("app_state", data.get("state"))

            pixel_scale = ascom_float(data.get("pixel_scale"))
            if pixel_scale is not None:
                data["pixel_scale"] = pixel_scale

            def _build_metric(prefix: str) -> GuiderMetric:
                arcsec = ascom_float(data.get(f"{prefix}_arcseconds"))
                pix = ascom_float(data.get(f"{prefix}_pixel"))
                return GuiderMetric(arcseconds=arcsec, pixel=pix)

            data["ra_rms_error"] = _build_metric("ra_rms_error")
            data["dec_rms_error"] = _build_metric("dec_rms_error")
            data["total_rms_error"] = _build_metric("total_rms_error")
            data["peak_ra_excursion"] = _build_metric("peak_ra_excursion")
            data["peak_dec_excursion"] = _build_metric("peak_dec_excursion")

        return data
