from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from ..nina_utils import ascom_datetime, ascom_float, ascom_int


class SensorInfo(BaseModel):
    bayer_offset: tuple[int, int] | None = None
    sensor_type: str | None = None
    resolution_px: tuple[int, int] | None = None
    pixel_size_microns: float | None = None
    bit_depth: int | None = None


class GainInfo(BaseModel):
    current: int | None = None
    default: int | None = None
    range: tuple[int, int] | None = None
    controllable: bool | None = None
    electrons_per_adu: float | None = None


class ExposureInfo(BaseModel):
    is_exposing: bool | None = None
    min_seconds: float | None = None
    max_seconds: float | None = None
    last_exposure_end: datetime | None = None


class OffsetInfo(BaseModel):
    current: int | None = None
    default: int | None = None
    range: tuple[int, int] | None = None
    controllable: bool | None = None


class BinningInfo(BaseModel):
    current: tuple[int, int] | None = None
    supported_modes: list[tuple[int, int]] = Field(default_factory=list)


class ThermalInfo(BaseModel):
    temperature_c: float | None = None
    has_cooler: bool = False
    cooler_on: bool = False
    target_temperature_c: float | None = None
    cooler_power_pct: float | None = None
    dew_heater: bool | None = None


class ReadoutInfo(BaseModel):
    current: str | None = None
    supported_modes: list[str] = Field(default_factory=list)


class CameraDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    state: str = "Idle"
    sensor: SensorInfo = Field(default_factory=SensorInfo)
    exposure: ExposureInfo = Field(default_factory=ExposureInfo)
    gain: GainInfo = Field(default_factory=GainInfo)
    offset: OffsetInfo = Field(default_factory=OffsetInfo)
    binning: BinningInfo = Field(default_factory=BinningInfo)
    thermal: ThermalInfo = Field(default_factory=ThermalInfo)
    readout: ReadoutInfo = Field(default_factory=ReadoutInfo)

    @model_validator(mode="before")
    @classmethod
    def _derive_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if not data.get("connected", False):
                return data

            data["state"] = data.get("camera_state") or "CameraIdle"

            bin_x = data.get("bin_x", 1)
            bin_y = data.get("bin_y", 1)
            if not isinstance(bin_x, int):
                try:
                    bin_x = int(bin_x)
                except (ValueError, TypeError):
                    bin_x = 1
            if not isinstance(bin_y, int):
                try:
                    bin_y = int(bin_y)
                except (ValueError, TypeError):
                    bin_y = 1

            raw_binning_modes = data.get("binning_modes", [])
            supported_modes: list[tuple[int, int]] = []
            if isinstance(raw_binning_modes, list):
                for mode in raw_binning_modes:
                    if isinstance(mode, (list, tuple)) and len(mode) == 2:
                        supported_modes.append((int(mode[0]), int(mode[1])))
                    elif isinstance(mode, str) and "x" in mode:
                        parts = mode.split("x")
                        if len(parts) == 2:
                            try:
                                supported_modes.append((int(parts[0]), int(parts[1])))
                            except (ValueError, TypeError):
                                pass

            data["sensor"] = SensorInfo(
                bayer_offset=(
                    data.get("bayer_offset_x", 0) or 0,
                    data.get("bayer_offset_y", 0) or 0,
                ),
                sensor_type=data.get("sensor_type", "Unknown"),
                resolution_px=(
                    data.get("x_size", 0) or 0,
                    data.get("y_size", 0) or 0,
                ),
                pixel_size_microns=ascom_float(data.get("pixel_size")),
                bit_depth=data.get("bit_depth", 8) or 8,
            )

            data["exposure"] = ExposureInfo(
                is_exposing=bool(data.get("is_exposing", False)),
                min_seconds=ascom_float(data.get("exposure_min")) or 0.0,
                max_seconds=ascom_float(data.get("exposure_max")) or 0.0,
                last_exposure_end=ascom_datetime(data.get("exposure_end_time")),
            )
            gain_current = ascom_int(data.get("gain"))
            gain_default = ascom_int(data.get("default_gain"))
            data["gain"] = GainInfo(
                current=gain_current,
                default=gain_default,
                range=(
                    ascom_int(data.get("gain_min")) or 0,
                    ascom_int(data.get("gain_max")) or 0,
                ),
                controllable=bool(data.get("can_set_gain", False)),
                electrons_per_adu=ascom_float(data.get("electrons_per_adu")),
            )

            offset_current = ascom_int(data.get("offset"))
            offset_default = ascom_int(data.get("default_offset"))
            data["offset"] = OffsetInfo(
                current=offset_current,
                default=offset_default,
                range=(
                    ascom_int(data.get("offset_min")) or 0,
                    ascom_int(data.get("offset_max")) or 0,
                ),
                controllable=bool(data.get("can_set_offset", False)),
            )

            data["binning"] = BinningInfo(
                current=(bin_x, bin_y),
                supported_modes=supported_modes,
            )

            data["thermal"] = ThermalInfo(
                temperature_c=ascom_float(data.get("temperature")),
                has_cooler=bool(data.get("can_set_temperature", False)),
                cooler_on=bool(data.get("cooler_on", False)),
                target_temperature_c=ascom_float(data.get("target_temp")),
                cooler_power_pct=ascom_float(data.get("cooler_power")),
                dew_heater=bool(
                    data.get("dew_heater_on") or data.get("has_dew_heater", False)
                ),
            )

            raw_readout_modes = data.get("readout_modes", [])
            if not isinstance(raw_readout_modes, list):
                raw_readout_modes = []
            readout_current = data.get("readout_mode")
            if not isinstance(readout_current, str):
                readout_current = str(readout_current) if readout_current else None
            data["readout"] = ReadoutInfo(
                current=readout_current,
                supported_modes=[str(m) for m in raw_readout_modes],
            )

        return data
