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
        if _missing_string(value):
            return None
        return float(value)
    if isinstance(value, float):
        if math.isnan(value) or value == -1.0:
            return None
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if value == -1:
            return None
        return float(value)
    if isinstance(value, bool):
        return float(value)
    return float(str(value))


def ascom_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        if _missing_string(value):
            return None
        return int(float(value))
    if isinstance(value, float):
        if math.isnan(value) or value == -1.0:
            return None
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        if value == -1:
            return None
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
            return datetime.fromisoformat(stripped)
        except (ValueError, TypeError):
            return None
    return None
