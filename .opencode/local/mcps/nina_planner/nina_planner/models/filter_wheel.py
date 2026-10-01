from __future__ import annotations

from pydantic import BaseModel, model_validator


class FilterWheelDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    is_moving: bool | None = None
    position: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            selected = data.get("selected_filter")
            if isinstance(selected, dict):
                data["position"] = selected.get("id")
        return data
