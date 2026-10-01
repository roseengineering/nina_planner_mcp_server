from __future__ import annotations

from pydantic import BaseModel, model_validator

from ..nina_utils import ascom_float, ascom_int


class FocuserDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    is_moving: bool | None = None
    is_settling: bool | None = None
    position: int | None = None
    temperature_c: float | None = None
    step_size_microns: int | None = None
    temp_comp: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if not data.get("connected", False):
                return data

            # ninaAPI sends `Temperature` / `StepSize`; the unit-bearing model
            # fields have no same-named counterpart, so map them explicitly.
            data["temperature_c"] = ascom_float(data.get("temperature"))
            data["step_size_microns"] = ascom_int(data.get("step_size"))

        return data
