from datetime import datetime, timezone
from typing import Any

import anyio
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time

from ..app import mcp
from . import equipment as equipment_tools


def _resolve_name(name: str) -> SkyCoord:
    return SkyCoord.from_name(name)


@mcp.tool()
async def resolve_target(name: str) -> dict[str, Any]:
    """Resolves a deep-sky object name (e.g. 'M31', 'NGC 6960') to J2000 RA/Dec via a SIMBAD lookup, so plans use verified coordinates instead of remembered ones. Returns `ra_hours` and `dec_deg`. Requires internet access; if the lookup fails, supply coordinates explicitly. This tool is read-only and takes no action."""
    try:
        coord = await anyio.to_thread.run_sync(_resolve_name, name)
    except Exception as e:
        raise ValueError(f"could not resolve target {name!r}: {e}") from e
    return {
        "name": name,
        "ra_hours": round(float(coord.ra.hour), 6),
        "dec_deg": round(float(coord.dec.deg), 6),
    }


def _altitudes(
    targets: list[dict[str, Any]],
    lat_deg: float,
    lon_deg: float,
    elevation_m: float,
    when: Time,
) -> list[dict[str, Any]]:
    location = EarthLocation(
        lat=lat_deg * u.deg, lon=lon_deg * u.deg, height=elevation_m * u.m
    )
    frame = AltAz(obstime=when, location=location)
    results: list[dict[str, Any]] = []
    for target in targets:
        name = str(target.get("name", ""))
        if target.get("ra_hours") is not None and target.get("dec_deg") is not None:
            coord = SkyCoord(
                float(target["ra_hours"]) * u.hourangle,
                float(target["dec_deg"]) * u.deg,
            )
        else:
            try:
                coord = _resolve_name(name)
            except Exception as e:
                raise ValueError(
                    f"target {name!r} has no ra_hours/dec_deg and could not be "
                    f"resolved: {e}"
                ) from e
        altaz = coord.transform_to(frame)
        results.append(
            {
                "name": name,
                "alt_deg": round(float(altaz.alt.deg), 1),
                "az_deg": round(float(altaz.az.deg), 1),
            }
        )
    return sorted(results, key=lambda r: -r["alt_deg"])


@mcp.tool()
async def target_altitudes(
    targets: list[dict[str, Any]], hours_ahead: float = 0.0
) -> dict[str, Any]:
    """Computes the altitude and azimuth of each target at the active site's location, sorted highest altitude first, for ranking night-loop candidates. Each target is `{"name": ...}` plus optional J2000 `ra_hours` and `dec_deg`; without coordinates the name is resolved via SIMBAD. `hours_ahead` evaluates a future time (default 0 = now, using this host's clock). Altitude is geometric (no refraction) and only orders candidates — N.I.N.A.'s altitude guards still enforce the floor at runtime. This tool is read-only and takes no action."""
    profile = await equipment_tools.get_site_profile()
    site = profile.site
    when = Time(datetime.now(timezone.utc)) + hours_ahead * u.hour
    rows = await anyio.to_thread.run_sync(
        _altitudes,
        targets,
        site.latitude_deg,
        site.longitude_deg,
        site.elevation_m,
        when,
    )
    return {"time_utc": when.utc.isot, "targets": rows}
