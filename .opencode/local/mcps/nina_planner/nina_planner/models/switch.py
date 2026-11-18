"""Switch device model: writable/readonly switches and derived on/off state."""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field


class ReadonlySwitchInfo(BaseModel):
    """A read-only switch: its current ``value`` is reported, not settable."""

    id: int | None = None
    name: str | None = None
    description: str | None = None
    value: float | None = None


class WritableSwitchInfo(BaseModel):
    """A settable switch plus the value range and step the driver exposes."""

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
        """Strict binary on/off derived from ``value``: only ``1`` is on.

        Deliberately range-agnostic — a 0–10 dimmer at position 10 is *off*,
        and a ±1 tri-state at +1 reads on. ``None`` when the driver reported
        no value (unknown), never a guess.
        """
        if self.value is None:
            return None
        return self.value == 1


class Switch(BaseModel):
    """A switch controller and the writable/readonly switches it exposes."""

    connected: bool
    name: str
    description: str | None = None
    display_name: str | None = None
    driver_info: str | None = None
    driver_version: str | None = None
    device_id: str | None = None
    supported_actions: list[str] | None = None
    writable_switches: list[WritableSwitchInfo] = Field(default_factory=list)
    readonly_switches: list[ReadonlySwitchInfo] = Field(default_factory=list)
