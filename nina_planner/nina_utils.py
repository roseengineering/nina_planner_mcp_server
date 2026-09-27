from __future__ import annotations

import math
import re
from datetime import datetime
from typing import TypeVar

T = TypeVar("T")


def to_snake(name: str) -> str:
    """Convert a camelCase / PascalCase name to snake_case.

    Two-pass: split before an acronym boundary (``RMSArcSec`` -> ``RMS_Arc``)
    and before each embedded uppercase letter (``ArcSec`` -> ``Arc_Sec``).
    """
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


_MISSING_STRINGS = {"nan", "n/a", "na", ""}


def _missing_string(value: str) -> bool:
    return value.strip().lower() in _MISSING_STRINGS


def ascom_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if _missing_string(stripped):
            return None
        return float(stripped)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, bool):
        return float(value)
    return float(str(value))


def ascom_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if _missing_string(stripped):
            return None
        return int(float(stripped))
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return int(value)
    return int(float(str(value)))


def ascom_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() == "nan":
            return None
        try:
            parsed = datetime.fromisoformat(stripped)
        except (ValueError, TypeError):
            return None
        # .NET DateTime.MinValue sentinel: ASCOM drivers emit
        # "0001-01-01T00:00:00" for never-set timestamps. All four ISO forms
        # — naive ("0001-01-01T00:00:00"), with ticks ("...0000000"), with
        # "Z" suffix, or with explicit offset — collapse to datetime.min
        # after `.replace(tzinfo=None)`. A real datetime at year 1 with
        # non-zero components (e.g. "...0001-01-01T00:00:00.123") is
        # preserved (microseconds != 0).
        if parsed.replace(tzinfo=None) == datetime.min:
            return None
        return parsed
    return None
