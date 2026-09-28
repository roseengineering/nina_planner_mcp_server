"""Parse a Telescopius-style mosaic CSV into plan pointings.

A mosaic CSV lists one pane per row with its center coordinates, position
angle, and grid position. N.I.N.A. only needs RA/Dec/PA per pointing, so this
module converts each row into a :class:`Pointing`; the pane row/column are
carried along as optional metadata (not sent to NINA) for labeling, ordering,
and post-processing mosaic assembly.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from .models.pointing import Pointing

__all__ = ["parse_sexagesimal", "parse_header", "load_pointings", "load_pointings_text"]


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


def _column_index(header: list[str], *names: str) -> int | None:
    for name in names:
        if name in header:
            return header.index(name)
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
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
    """Parse CSV text into pointings, skipping malformed rows.

    Raises ``ValueError`` if the text is empty, lacks RA/DEC columns, or yields
    no valid pointings. Individual bad rows are skipped with a warning to
    stderr.
    """
    rows = [r for r in csv.reader(text.splitlines()) if any(f.strip() for f in r)]
    if not rows:
        raise ValueError(f"{source} is empty")
    header = [parse_header(h) for h in rows[0]]

    i_ra = _column_index(header, "ra", "ra_hours", "right_ascension", "center_ra")
    i_dec = _column_index(header, "dec", "dec_deg", "declination", "center_dec")
    i_pa = _column_index(
        header, "position_angle_east", "position_angle_deg", "pa", "position_angle"
    )
    i_label = _column_index(header, "pane", "label", "name")
    i_row = _column_index(header, "row")
    i_col = _column_index(header, "column", "col")
    if i_ra is None or i_dec is None:
        raise ValueError(
            f"{source}: header must contain RA and DEC columns, got {header}"
        )

    pointings: list[Pointing] = []
    for line_no, row in enumerate(rows[1:], start=2):
        try:
            ra = parse_sexagesimal(_cell(row, i_ra))
            dec = parse_sexagesimal(_cell(row, i_dec))
        except (ValueError, TypeError) as exc:
            print(f"warning: skipping line {line_no}: {exc}", file=sys.stderr)
            continue

        ra %= 24.0

        data: dict[str, object] = {"ra_hours": round(ra, 6), "dec_deg": round(dec, 6)}

        pa_raw = _cell(row, i_pa)
        if pa_raw:
            try:
                data["position_angle_deg"] = round(
                    float(pa_raw.rstrip("º°")) % 360.0, 3
                )
            except ValueError as exc:
                print(
                    f"warning: skipping line {line_no}: bad position angle "
                    f"{pa_raw!r}: {exc}",
                    file=sys.stderr,
                )
                continue

        row_idx = _parse_int(_cell(row, i_row))
        col_idx = _parse_int(_cell(row, i_col))
        if row_idx is not None:
            data["row"] = row_idx
        if col_idx is not None:
            data["column"] = col_idx

        label = _cell(row, i_label)
        if not label and row_idx is not None and col_idx is not None:
            label = f"r{row_idx}_c{col_idx}"
        if label:
            data["label"] = label

        try:
            pointings.append(Pointing(**data))  # type: ignore[arg-type]
        except ValidationError as exc:
            print(
                f"warning: skipping line {line_no}: {exc.errors()[0]['msg']}",
                file=sys.stderr,
            )
            continue

    if not pointings:
        raise ValueError(f"{source}: no valid pointings found")
    return pointings


def load_pointings(path: str | Path) -> list[Pointing]:
    """Read a mosaic CSV file and return its validated pointings."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return load_pointings_text(text, source=str(path))
