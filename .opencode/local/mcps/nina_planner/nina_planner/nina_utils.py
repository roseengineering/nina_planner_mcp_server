"""Coercion helpers for ASCOM/N.I.N.A. field values and name formatting.

ASCOM drivers send "no value" as a surprising variety of shapes — ``None``,
``"NaN"``, ``"N/A"``, ``.NET DateTime.MinValue`` — so the ``ascom_*`` helpers
normalize them to ``None`` instead of raising, keeping one odd driver value
from failing a whole equipment-status call.
"""

from __future__ import annotations

import math
import re
from datetime import datetime


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
    """Coerce an ASCOM value to ``float``, mapping every "no value" shape to ``None``.

    Accepts ``None``, numeric strings (``"NaN"``/``"N/A"``/blank → ``None``),
    ``NaN``, ints and bools; anything else is passed through ``float()`` and may
    raise ``ValueError``/``TypeError`` for genuinely non-numeric input.
    """
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
    """Coerce an ASCOM value to ``int``, or ``None`` when it means "no value".

    Real ints pass through unchanged (preserving large values exactly); other
    shapes go through ``ascom_float``'s sentinel handling first, so ``"NaN"``
    and friends become ``None`` rather than an exception. Fractional input is
    truncated toward zero.
    """
    # Exact pass-through for real ints (keeps big values precise); every
    # other shape goes through ascom_float's sentinel handling first.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    parsed = ascom_float(value)
    return None if parsed is None else int(parsed)


def ascom_datetime(value: object) -> datetime | None:
    """Parse an ASCOM timestamp to ``datetime``, or ``None`` for "never set".

    Accepts ISO-8601 strings in any of the forms drivers emit (naive, with
    ticks, ``Z``-suffixed, or explicitly offset) and returns them with their
    original timezone info; ``None``, blank, ``"NaN"`` and unparseable text
    yield ``None``. The .NET ``DateTime.MinValue`` sentinel
    (``0001-01-01T00:00:00``) also yields ``None`` — it means "no timestamp",
    not the year 1.
    """
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


def to_local_display(dt: datetime) -> str:
    """Render ``dt`` in the host's local zone, carrying its UTC offset.

    All arithmetic in this package happens on UTC instants (see
    ``_exposure_instant`` in ``progress.py``); this is the single place a
    timestamp is turned back into something a person reads. The offset is
    kept in the output so an unambiguous instant survives — a bare local
    wall clock would silently mean a different moment depending on the
    machine that renders it.

    A naive input is taken as already-local (no conversion), matching the
    "naive means local" reading used everywhere else a wall clock is
    displayed.
    """
    if dt.tzinfo is None:
        return dt.isoformat(timespec="seconds")
    return dt.astimezone().isoformat(timespec="seconds")
