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

            # ninaAPI nests guiding error under `RMSError`, e.g.
            # {"RA": {"Pixel": .., "Arcseconds": ..}, "PeakRA": {...}, ...},
            # which `_convert_keys` snake-cases to `rms_error.ra.arcseconds` etc.
            nested = data.get("rms_error")
            if not isinstance(nested, dict):
                nested = {}

            def _build_metric(nested_key: str) -> GuiderMetric:
                entry = nested.get(nested_key)
                if not isinstance(entry, dict):
                    entry = {}

                return GuiderMetric(
                    arcseconds=ascom_float(entry.get("arcseconds")),
                    pixel=ascom_float(entry.get("pixel")),
                )

            data["ra_rms_error"] = _build_metric("ra")
            data["dec_rms_error"] = _build_metric("dec")
            data["total_rms_error"] = _build_metric("total")
            data["peak_ra_excursion"] = _build_metric("peak_ra")
            data["peak_dec_excursion"] = _build_metric("peak_dec")

        return data
