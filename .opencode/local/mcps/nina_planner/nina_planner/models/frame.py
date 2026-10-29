from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, model_validator

from ..nina_utils import ascom_datetime, ascom_float, ascom_int

# The exact camelCase field set N.I.N.A.'s live ``GET /v2/api/image-history``
# returns for its single (current-frame) record, mapped to snake_case model
# fields. Explicit rather than derived via ``to_snake`` because a few names
# (``RmsText`` -> ``rm_text``, ``HFRStDev`` -> ``hfr_st_dev``) do not survive
# the generic conversion, and because pinning the map documents the endpoint's
# full contract in one place.
_FIELD_MAP = {
    "ExposureTime": "exposure_time",
    "ImageType": "image_type",
    "Filter": "filter",
    "RmsText": "rms_text",
    "Temperature": "temperature",
    "CameraName": "camera_name",
    "TargetName": "target_name",
    "Gain": "gain",
    "Offset": "offset",
    "Date": "date",
    "TelescopeName": "telescope_name",
    "FocalLength": "focal_length",
    "StDev": "st_dev",
    "Mean": "mean",
    "Median": "median",
    "Min": "min",
    "Max": "max",
    "Stars": "stars",
    "HFR": "hfr",
    "HFRStDev": "hfr_st_dev",
    "IsBayered": "is_bayered",
    "Filename": "filename",
}

_FLOAT_FIELDS = {
    "exposure_time",
    "temperature",
    "focal_length",
    "hfr",
    "hfr_st_dev",
    "mean",
    "median",
    "st_dev",
}
_INT_FIELDS = {"gain", "offset", "stars", "min", "max"}


class FrameStats(BaseModel):
    """Per-frame statistics for N.I.N.A.'s *current* (most recent) frame.

    Sourced from ``GET /v2/api/image-history``, which despite the name returns
    only the most recent frame's record — a single element, with no paging. The
    full field set is preserved so the frame-quality discrimators the
    frame-quality-diagnostics skill relies on (HFR vs HFRStDev vs star count vs
    background ADU) are all present, and so a calibration frame is never
    mistaken for a science frame.

    Unlike the ``ImageMetaData.csv`` path (:func:`imaging._clean_metric`), a
    genuine ``0`` is preserved as ``0`` — ``Stars: 0`` is a real star-count
    reading (the star-count failure condition), not a missing value. Unmeasured
    values arrive as NaN and are normalized to ``None`` here by the
    ``ascom_*`` helpers, so ``0`` always means a true reading.
    """

    filename: str | None = None
    image_type: str | None = None
    date: datetime | None = None
    target_name: str | None = None
    filter: str | None = None
    exposure_time: float | None = None
    temperature: float | None = None
    gain: int | None = None
    offset: int | None = None
    camera_name: str | None = None
    telescope_name: str | None = None
    focal_length: float | None = None
    is_bayered: bool | None = None
    # Quality metrics.
    hfr: float | None = None
    hfr_st_dev: float | None = None
    stars: int | None = None
    # Background ADU statistics.
    mean: float | None = None
    median: float | None = None
    st_dev: float | None = None
    min: int | None = None
    max: int | None = None
    # Guiding RMS as NINA renders it, e.g. 'Tot: 0.42 (0.31")'.
    rms_text: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        out: dict[str, object] = {}
        for key, value in data.items():
            raw = str(key)
            name = _FIELD_MAP.get(raw, raw)
            if name in _FLOAT_FIELDS:
                out[name] = ascom_float(value)
            elif name in _INT_FIELDS:
                out[name] = ascom_int(value)
            elif name == "date":
                out[name] = ascom_datetime(value)
            else:
                out[name] = value
        return out
