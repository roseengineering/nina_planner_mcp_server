"""Parse a Telescopius mosaic CSV into plan pointings.

A mosaic CSV lists one pane per row with its center coordinates, position
angle, and grid position. N.I.N.A. only needs RA/Dec/PA per pointing, so this
module converts each row into a :class:`Pointing`; the pane row/column are
carried along as optional metadata (not sent to NINA) for labeling, ordering,
and post-processing mosaic assembly.

Parsing is pinned to the Telescopius export layout: the nine columns in their
fixed order, with RA in hours in ``[0, 24)``. Columns are read positionally,
the header row must match the expected names, and an out-of-range RA rejects
the whole file — a wrong file fails loudly instead of pointing at the wrong
sky.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from pydantic import ValidationError

from .models.pointing import Pointing

__all__ = ["parse_sexagesimal", "parse_header", "load_pointings", "load_pointings_text"]

logger = logging.getLogger(__name__)


# 1-3 numeric components with optional sexagesimal unit suffixes and an optional
# trailing hemisphere letter. Colon or whitespace separates components. Bare
# decimals (e.g. "45.855") are accepted as degrees/hours.
_SEXAGESIMAL = re.compile(
    r"^\s*(?P<sign>[-+])?\s*"
    r"(?P<a>\d+(?:\.\d+)?)\s*(?:hr|h|d|°|º)?\s*[: ]?\s*"
    r"(?P<b>\d+(?:\.\d+)?)?\s*(?:min|'|′)?\s*[: ]?\s*"
    r'(?P<c>\d+(?:\.\d+)?)?\s*(?:sec|"|″)?\s*'
    r"(?P<hemi>[NSEW])?\s*$",
    re.IGNORECASE,
)


def parse_sexagesimal(value: str | float | int) -> float:
    """Convert a sexagesimal or decimal angle string to a float.

    Accepts forms like ``0hr 56' 01"``, ``45º 51' 18"``, ``13 56 45``,
    ``00:42:44.30``, ``+41:16:08.5``, and bare decimals. A leading ``-`` or a
    trailing ``S``/``W`` hemisphere makes the result negative.
    """
    if isinstance(value, (int, float)):
        return float(value)
    m = _SEXAGESIMAL.match(value)
    if not m:
        raise ValueError(f"cannot parse sexagesimal value: {value!r}")
    a = float(m.group("a"))
    b = float(m.group("b") or 0.0)
    c = float(m.group("c") or 0.0)
    total = a + b / 60.0 + c / 3600.0
    negative = m.group("sign") == "-" or (m.group("hemi") or "").upper() in ("S", "W")
    return -total if negative else total


def parse_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


# The fixed Telescopius export layout, in column order. Parsing is positional
# against this spec; the header row is only sanity-checked, so a non-
# Telescopius file is rejected loudly instead of being silently misread.
_TELESCOPIUS_COLUMNS = (
    "pane",
    "ra",
    "dec",
    "position_angle_east",
    "pane_width_arcmins",
    "pane_height_arcmins",
    "overlap",
    "row",
    "column",
)


def _cell(row: list[str], index: int) -> str:
    return row[index].strip()


def _parse_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return None


def load_pointings_text(text: str, source: str = "<csv>") -> list[Pointing]:
    """Parse a Telescopius mosaic CSV into pointings, skipping bad rows.

    Columns are read positionally per the Telescopius export layout
    (``_TELESCOPIUS_COLUMNS``); the header row must match those names or the
    file is rejected. RA is hours by spec and must be in ``[0, 24)`` — an
    out-of-range RA (e.g. a degrees value) rejects the whole file rather
    than being wrapped onto the wrong sky. ``ValueError`` is also raised if
    the text is empty or yields no valid pointings. Individual
    otherwise-malformed rows are skipped with a logged warning.
    """
    rows = [r for r in csv.reader(text.splitlines()) if any(f.strip() for f in r)]
    if not rows:
        raise ValueError(f"{source} is empty")
    header = [parse_header(h) for h in rows[0]]
    if header[: len(_TELESCOPIUS_COLUMNS)] != list(_TELESCOPIUS_COLUMNS):
        raise ValueError(
            f"{source}: header must be the Telescopius layout "
            f"{', '.join(_TELESCOPIUS_COLUMNS)}, got {header}"
        )

    i_pane, i_ra, i_dec, i_pa = 0, 1, 2, 3
    i_row, i_col = 7, 8

    pointings: list[Pointing] = []
    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) <= i_col:
            logger.warning(
                f"skipping line {line_no}: expected "
                f"{len(_TELESCOPIUS_COLUMNS)} columns, got {len(row)}"
            )
            continue
        try:
            ra = parse_sexagesimal(_cell(row, i_ra))
            dec = parse_sexagesimal(_cell(row, i_dec))
        except (ValueError, TypeError) as exc:
            logger.warning(f"skipping line {line_no}: {exc}")
            continue

        # RA is hours by spec; nothing is ever wrapped. An out-of-range value
        # means the file is wrong (e.g. degrees), so reject the whole file
        # rather than silently pointing at the wrong sky.
        if not 0.0 <= ra < 24.0:
            raise ValueError(
                f"{source}: line {line_no}: RA {ra} out of range [0, 24) hours"
            )

        data: dict[str, object] = {"ra_hours": round(ra, 6), "dec_deg": round(dec, 6)}

        pa_raw = _cell(row, i_pa)
        if pa_raw:
            try:
                data["position_angle_deg"] = round(
                    float(pa_raw.rstrip("º°")) % 360.0, 3
                )
            except ValueError as exc:
                logger.warning(
                    f"skipping line {line_no}: bad position angle {pa_raw!r}: {exc}"
                )
                continue

        row_idx = _parse_int(_cell(row, i_row))
        col_idx = _parse_int(_cell(row, i_col))
        if row_idx is not None:
            data["row"] = row_idx
        if col_idx is not None:
            data["column"] = col_idx

        label = _cell(row, i_pane)
        if not label and row_idx is not None and col_idx is not None:
            label = f"r{row_idx}_c{col_idx}"
        if label:
            data["label"] = label

        try:
            pointings.append(Pointing(**data))  # type: ignore[arg-type]
        except ValidationError as exc:
            logger.warning(f"skipping line {line_no}: {exc.errors()[0]['msg']}")
            continue

    if not pointings:
        raise ValueError(f"{source}: no valid pointings found")
    return pointings


def load_pointings(path: str | Path) -> list[Pointing]:
    """Read a mosaic CSV file and return its validated pointings."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return load_pointings_text(text, source=str(path))
