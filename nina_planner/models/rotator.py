from __future__ import annotations

from pydantic import BaseModel, model_validator

from ..nina_utils import ascom_float


class RotatorDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    is_moving: bool | None = None
    is_synced: bool | None = None
    position_deg: float | None = None
    mechanical_position_deg: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            position = ascom_float(data.get("position"))
            if position is not None:
                data["position_deg"] = position
            mechanical = ascom_float(data.get("mechanical_position"))
            if mechanical is not None:
                data["mechanical_position_deg"] = mechanical

            if "is_synced" not in data:
                sync_state = data.get("sync_state", "Unsynced")
                data["is_synced"] = sync_state in ("Synced", "Synchronized", True)

        return data
