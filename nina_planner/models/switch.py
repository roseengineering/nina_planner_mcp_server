from __future__ import annotations

from pydantic import BaseModel, computed_field


class ReadonlySwitchInfo(BaseModel):
    id: int | None = None
    name: str | None = None
    description: str | None = None
    value: float | None = None


class WritableSwitchInfo(BaseModel):
    id: int | None = None
    name: str | None = None
    description: str | None = None
    value: float | None = None
    target_value: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    step_size: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_on(self) -> bool | None:
        if self.value is None:
            return None
        return self.value == 1


class Switch(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    display_name: str | None = None
    driver_info: str | None = None
    driver_version: str | None = None
    device_id: str | None = None
    supported_actions: list[str] | None = None
    writable_switches: list[WritableSwitchInfo]
    readonly_switches: list[ReadonlySwitchInfo]
