"""Built-in deep-sky catalog for target ranking (see ``tools.sky.best_targets``).

Static J2000 positions, integrated visual magnitudes, and angular sizes for
every Messier object (minus the double star M40 and asterism M73) plus bright
NGC/Caldwell showpieces, chosen to cover every season from northern latitudes
and include the southern showpieces for southern sites.

Magnitudes are integrated (total) visual magnitudes; sizes are major/minor
axes in arcminutes. These are catalog values good enough for ranking and
scheduling -- not for plate-solving; use ``resolve_target`` (SIMBAD) when a
plan needs verified precision coordinates.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True)
class Dso:
    """One catalog deep-sky object.

    ``kind`` is a short type tag: "galaxy", "globular", "open" (open cluster),
    "pn" (planetary nebula), "bn" (bright/diffuse nebula), "snr" (supernova
    remnant), or "cloud" (star cloud).
    """

    name: str
    common: str
    kind: str
    ra_hours: float
    dec_deg: float
    mag: float
    major_arcmin: float
    minor_arcmin: float


def _load_catalog() -> tuple[Dso, ...]:
    with (
        files(__package__)
        .joinpath("dso_catalog.csv")
        .open(newline="", encoding="utf-8") as source
    ):
        return tuple(
            Dso(
                name=row["name"],
                common=row["common"],
                kind=row["kind"],
                ra_hours=float(row["ra_hours"]),
                dec_deg=float(row["dec_deg"]),
                mag=float(row["mag"]),
                major_arcmin=float(row["major_arcmin"]),
                minor_arcmin=float(row["minor_arcmin"]),
            )
            for row in csv.DictReader(source)
        )


CATALOG = _load_catalog()
