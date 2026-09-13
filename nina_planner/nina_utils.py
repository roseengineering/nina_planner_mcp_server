from __future__ import annotations

import math
from datetime import datetime
from typing import TypeVar

T = TypeVar("T")


def ascom_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value.lower() == "nan":
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
        if value.lower() == "nan":
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
