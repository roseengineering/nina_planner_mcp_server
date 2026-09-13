from __future__ import annotations

from pydantic import BaseModel, field_validator


class ReadonlySwitchInfo(BaseModel):
    name: str | None = None
    description: str | None = None
    value: float | None = None


class WritableSwitchInfo(BaseModel):
    name: str | None = None
    description: str | None = None
    value: float | None = None
    is_on: bool | None = None
    range: tuple[float, float] | None = None
    step_size: float | None = None

    @field_validator("is_on", mode="before")
    @classmethod
    def _compute_is_on(cls, v: bool, info) -> bool:
        if isinstance(v, bool):
            return v
        return False


class Switch(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    writable_switches: list[WritableSwitchInfo]
    readonly_switches: list[ReadonlySwitchInfo]
