import math
from datetime import datetime, timezone
from typing import Any

import anyio
import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_sun
from astropy.time import Time

from .. import dso_catalog
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


# --- difficulty ranking (see best_targets) -----------------------------------

# Surface-brightness / magnitude thresholds. Extended objects are bucketed by
# mean surface brightness in mag/arcmin^2; compact objects (major axis < 5
# arcmin: planetaries, compact globulars) by integrated mag. Tuned for EAA
# (short-subframe / live-stack observing) -- what a camera stack reaches,
# roughly 1.3 mag beyond the unaided eye.
_LEVEL_THRESHOLDS: dict[str, tuple[float, float]] = {
    "extended": (14.8, 15.8),
    "compact": (11.0, 12.0),
}

# Metric range mapped onto the 0..1 "easiness" factor in scoring.
_SCORE_RANGE: dict[str, tuple[float, float]] = {
    "extended": (10.5, 17.5),
    "compact": (6.5, 13.5),
}

_COMPACT_ARCMIN = 5.0


def _surface_brightness(mag: float, major_arcmin: float, minor_arcmin: float) -> float:
    """Mean surface brightness in mag/arcmin^2 (lower = brighter surface).

    Models the object as an ellipse: area = (pi/4) * major * minor, then
    mu = mag + 2.5 * log10(area). Objects with no minor axis are treated
    as round. This is the metric that separates "bright but spread out"
    (M33) from "faint but concentrated" (M57) -- integrated magnitude
    alone ranks them backwards.
    """
    minor = minor_arcmin if minor_arcmin > 0 else major_arcmin
    area = math.pi / 4.0 * major_arcmin * minor
    if area <= 0:
        return mag
    return mag + 2.5 * math.log10(area)


def _is_compact(major_arcmin: float) -> bool:
    return major_arcmin < _COMPACT_ARCMIN


def _difficulty(metric: float, compact: bool) -> str:
    """Bucket a metric (surface brightness or integrated mag) into a level."""
    easy, hard = _LEVEL_THRESHOLDS["compact" if compact else "extended"]
    if metric < easy:
        return "beginner"
    if metric < hard:
        return "intermediate"
    return "advanced"


def _easiness(metric: float, compact: bool) -> float:
    """0..1 factor, 1 = easiest, from the metric's expected range."""
    floor_, ceiling = _SCORE_RANGE["compact" if compact else "extended"]
    return max(0.0, min(1.0, (ceiling - metric) / (ceiling - floor_)))


# --- sun / twilight (see sun_times and best_targets) -------------------------

# Sun altitude at which each event happens. Sunset/sunrise use the standard
# apparent-altitude value (-0.833 deg = refraction + solar semidiameter);
# the twilight names are the classic definitions.
_DARKNESS_SUN_ALT = -18.0  # astronomical darkness -- the night-loop's "dark"
_SUN_CROSSINGS: tuple[tuple[float, str, str], ...] = (
    (-0.833, "sunset", "sunrise"),
    (-6.0, "civil_dusk", "civil_dawn"),
    (-12.0, "nautical_dusk", "nautical_dawn"),
    (-18.0, "astronomical_dusk", "astronomical_dawn"),
)

# _sun_events backfills 24h before `when` so a window that is already open
# (e.g. checking just before dawn) can still find the dusk that started it,
# and so "morning" can pair the last dawn with the dusk before it.
_SUN_BACKFILL_H = 24.0
_SUN_FORWARD_H = 48.0
_SUN_STEP_MIN = 5


def _sun_altitudes(times: Time, location: EarthLocation) -> list[float]:
    """Geometric sun altitude in degrees for each time (no refraction).

    Accepts a scalar or vector `Time`; always returns a list.
    """
    sun = get_sun(times)
    altaz = sun.transform_to(AltAz(obstime=times, location=location))
    alts = np.atleast_1d(altaz.alt.deg)
    return [float(a) for a in alts]


def _interp_time(times: Time, alts: list[float], i: int, threshold: float) -> Time:
    """Crossing time between samples i and i+1, linearly interpolated."""
    frac = (threshold - alts[i]) / (alts[i + 1] - alts[i])
    return times[i] + frac * (times[i + 1] - times[i])


def _sun_events(when: Time, location: EarthLocation) -> dict[str, Any]:
    """Next sunrise/sunset and twilight times for the site, the darkness
    window associated with `night_phase`, and a coarse `night_phase`
    ("dark" / "morning" / "evening") for the night-loop's end-of-night test.

    Samples sun altitude every 5 minutes from 24h before `when` to 48h after
    and interpolates each threshold crossing. Events are the first occurrence
    after `when`; `None` means no crossing in the window (polar day/night).
    When not dark, `darkness` pairs with the phase: the just-completed
    window in "morning", the upcoming one in "evening" -- never a mismatched
    dusk/dawn pair. Pure helper -- unit-testable without N.I.N.A.
    """
    n_samples = int((_SUN_BACKFILL_H + _SUN_FORWARD_H) * 60 / _SUN_STEP_MIN) + 1
    times = when + (np.arange(n_samples) * _SUN_STEP_MIN - _SUN_BACKFILL_H * 60) * u.min
    alts = _sun_altitudes(times, location)

    # Collect every crossing of every threshold, labelled dusk/dawn.
    crosses: dict[str, list[tuple[Time, str]]] = {
        down_name: [] for _, down_name, _ in _SUN_CROSSINGS
    }
    for i in range(len(alts) - 1):
        for threshold, down_name, _up_name in _SUN_CROSSINGS:
            if alts[i] >= threshold > alts[i + 1]:
                crosses[down_name].append(
                    (_interp_time(times, alts, i, threshold), "dusk")
                )
            elif alts[i] < threshold <= alts[i + 1]:
                crosses[down_name].append(
                    (_interp_time(times, alts, i, threshold), "dawn")
                )

    # Map every threshold's down/up events to the requested names, first
    # occurrence strictly after `when`.
    events: dict[str, Time | None] = {}
    for _threshold, down_name, up_name in _SUN_CROSSINGS:
        pairs = crosses[down_name]
        events[down_name] = next(
            (t for t, kind in pairs if kind == "dusk" and t > when), None
        )
        events[up_name] = next(
            (t for t, kind in pairs if kind == "dawn" and t > when), None
        )

    # Darkness window. If dark now, the window containing now. Otherwise pair
    # the window with the phase: morning reports the night that just ended
    # (what a night report documents), evening the one about to open (what a
    # schedule needs).
    now_alt = _sun_altitudes(when, location)[0]
    is_dark_now = now_alt < _DARKNESS_SUN_ALT
    astro = crosses["astronomical_dusk"]
    past_dawns = [t for t, kind in astro if kind == "dawn" and t <= when]
    future_dusks = [t for t, kind in astro if kind == "dusk" and t > when]
    if is_dark_now:
        past_dusks = [t for t, kind in astro if kind == "dusk" and t <= when]
        future_dawns = [t for t, kind in astro if kind == "dawn" and t > when]
        start = past_dusks[-1] if past_dusks else None
        end = future_dawns[0] if future_dawns else None
        night_phase = "dark"
    else:
        # Not dark: did the night just end, or is one about to begin? Compare
        # how long ago astronomical dawn was against how long until the next
        # dusk -- nearer the end means morning (night over), nearer the start
        # means evening (darkness still coming).
        last_dawn = past_dawns[-1] if past_dawns else None
        next_dusk = future_dusks[0] if future_dusks else None
        if last_dawn is None and next_dusk is None:
            night_phase = "evening"  # no crossing in window; don't end a night
        elif next_dusk is None:
            night_phase = "morning"
        elif last_dawn is None:
            night_phase = "evening"
        elif (when - last_dawn) < (next_dusk - when):
            night_phase = "morning"
        else:
            night_phase = "evening"
        if night_phase == "morning":
            # The completed night: the dusk before the last dawn opened it.
            past_dusks = [t for t, kind in astro if kind == "dusk" and t <= when]
            start = past_dusks[-1] if past_dusks else None
            end = last_dawn
        else:
            # The upcoming night: the next dusk opens it, the dawn after
            # that closes it -- never the previous morning's dawn.
            start = next_dusk
            end = next(
                (
                    t
                    for t, kind in astro
                    if kind == "dawn" and next_dusk and t > next_dusk
                ),
                None,
            )

    return {
        "time_utc": when.utc.isot,
        "site": {
            "latitude_deg": round(float(location.lat.deg), 4),
            "longitude_deg": round(float(location.lon.deg), 4),
            "elevation_m": round(float(location.height.to_value(u.m)), 1),
        },
        "is_dark_now": is_dark_now,
        "night_phase": night_phase,
        "events": {
            name: (t.utc.isot if t is not None else None) for name, t in events.items()
        },
        "darkness": {
            "start_utc": start.utc.isot if start is not None else None,
            "end_utc": end.utc.isot if end is not None else None,
        },
    }


def _rank_catalog(
    catalog: "list[dso_catalog.Dso]",
    when: Time,
    lat_deg: float,
    lon_deg: float,
    elevation_m: float,
    min_altitude: float,
    fov_deg: tuple[float, float] | None,
    level: str | None,
    kinds: list[str] | None,
    top: int,
) -> dict[str, Any]:
    """Rank catalog objects by altitude over the next 12 hours and difficulty.

    Pure helper around astropy so it is unit-testable without N.I.N.A.
    Returns visible-now rows (sorted by score) plus later-tonight rows
    (objects that will climb above min_altitude later this night).
    """
    location = EarthLocation(
        lat=lat_deg * u.deg, lon=lon_deg * u.deg, height=elevation_m * u.m
    )
    coords = SkyCoord(
        [d.ra_hours for d in catalog] * u.hourangle,
        [d.dec_deg for d in catalog] * u.deg,
    )
    # Sample now + every 30 minutes for 12 hours to find each object's
    # peak tonight without a full ephemeris -- but only within astronomical
    # darkness (sun below -18 deg): a daytime or twilight peak is useless
    # for observing and must not be reported as "tonight's best".
    samples = 25
    times = when + np.arange(samples) * 30 * u.min
    dark = [s < _DARKNESS_SUN_ALT for s in _sun_altitudes(times, location)]
    if not any(dark):
        return {
            "visible_now": [],
            "later_tonight": [],
            "sampled_catalog": len(catalog),
        }
    dark_idx = [i for i, is_dark in enumerate(dark) if is_dark]
    is_dark_now = dark[0]
    alts: list[list[float]] = []
    azs: list[list[float]] = []
    for i in range(samples):
        frame = AltAz(obstime=times[i], location=location)
        altaz = coords.transform_to(frame)
        alts.append([float(a) for a in altaz.alt.deg])
        azs.append([float(a) for a in altaz.az.deg])

    visible: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []
    for idx, obj in enumerate(catalog):
        if kinds is not None and obj.kind not in kinds:
            continue
        compact = _is_compact(obj.major_arcmin)
        metric = (
            obj.mag
            if compact
            else _surface_brightness(obj.mag, obj.major_arcmin, obj.minor_arcmin)
        )
        obj_level = _difficulty(metric, compact)
        if level is not None and obj_level != level:
            continue
        alt_now = alts[0][idx]
        # Peak within astronomical darkness only (dark_idx indexes the
        # 30-minute samples that fall below the sun-altitude gate).
        night_alts = [alts[i][idx] for i in dark_idx]
        peak_alt = max(night_alts)
        peak_at = times[dark_idx[night_alts.index(peak_alt)]]
        if peak_alt < min_altitude:
            continue  # never clears the floor while dark tonight
        easiness = _easiness(metric, compact)
        if fov_deg is not None and not compact:
            large = obj.major_arcmin / 60.0 > max(fov_deg)
        else:
            large = False
        row = {
            "name": obj.name,
            "common": obj.common,
            "kind": obj.kind,
            "level": obj_level,
            "mag": obj.mag,
            "surface_brightness": round(
                _surface_brightness(obj.mag, obj.major_arcmin, obj.minor_arcmin), 2
            ),
            "alt_deg": round(alt_now, 1),
            "az_deg": round(azs[0][idx], 1),
            "peak_alt_deg": round(peak_alt, 1),
            "peak_utc": peak_at.utc.isot,
            "large_for_field": large,
        }
        if is_dark_now and alt_now >= min_altitude:
            row["score"] = round(
                100.0 * (0.5 * max(0.0, min(alt_now, 90.0)) / 90.0 + 0.5 * easiness),
                1,
            )
            visible.append(row)
        else:
            row["score"] = round(
                100.0 * (0.5 * max(0.0, min(peak_alt, 90.0)) / 90.0 + 0.5 * easiness),
                1,
            )
            later.append(row)

    visible.sort(key=lambda r: -r["score"])
    later.sort(key=lambda r: -r["score"])
    return {
        "is_dark_now": is_dark_now,
        "visible_now": visible[:top],
        "later_tonight": later[:top],
        "sampled_catalog": len(catalog),
    }


@mcp.tool()
async def best_targets(
    min_altitude: float = 30.0,
    top: int = 10,
    level: str | None = None,
    kinds: list[str] | None = None,
) -> dict[str, Any]:
    """Ranks the best deep-sky objects to observe from the active site right now, from a built-in catalog of Messier and bright NGC objects, each classified beginner/intermediate/advanced -- so "what should I look at tonight" needs no star atlas. Tuned for EAA (short-subframe / live-stack observing), matching what a N.I.N.A. rig actually does.

    Difficulty is ranked by SURFACE BRIGHTNESS, not integrated magnitude: objects are modeled as ellipses with mu = mag + 2.5*log10(area in arcmin^2), because magnitude alone ranks M33 (mag 5.7, huge and diffuse) as easier than M57 (mag 8.8, tiny and concentrated) when the reality is the opposite. Compact objects (major axis under 5 arcmin: planetaries, compact globulars) are bucketed by integrated magnitude instead, since their light is concentrated anyway. Thresholds (EAA depth): extended beginner under 14.8 mu, intermediate under 15.8; compact beginner under mag 11.0, intermediate under 12.0. Levels: "beginner" (shows well in short stacks, suburban skies), "intermediate", "advanced" (needs dark skies / long integration).

    `min_altitude` (default 30, matching the usual PLAN.md floor) gates both now and tonight's peak. `top` caps each returned list (default 10). `level` filters to one difficulty ("beginner" | "intermediate" | "advanced"). `kinds` filters by type: "galaxy", "globular", "open" (cluster), "pn" (planetary nebula), "bn" (bright nebula), "snr" (supernova remnant), "cloud" (star cloud).

    Returns `visible_now` (dark now, above the altitude floor at this moment, sorted by a score blending current altitude and easiness, 0-100) and `later_tonight` (the next observing opportunity: peak altitude reached WITHIN astronomical darkness -- sun below -18 deg -- over the next 12 hours, with each row's `peak_utc` and `peak_alt_deg` for scheduling; daytime and twilight peaks are never reported, so a midday "peak" in an older version of this output cannot happen). `is_dark_now` reports whether the site is currently in astronomical darkness; when false, `visible_now` is empty and every row lands in `later_tonight`. Rows carry `surface_brightness` in mag/arcmin^2 and `large_for_field` (object wider than the profile's field of view -- mosaic or framing care needed). Twilight boundaries for the site come from `sun_times`. No N.I.N.A. calls beyond reading the site profile; read-only, takes no action. Catalog coordinates are for ranking, not pointing -- use resolve_target for verified plan coordinates."""
    if level is not None and level not in ("beginner", "intermediate", "advanced"):
        raise ValueError(
            f"level must be beginner, intermediate, or advanced, got {level!r}"
        )
    profile = await equipment_tools.get_site_profile()
    site = profile.site
    optics = profile.optics
    fov = optics.field_of_view_deg if optics else None
    when = Time(datetime.now(timezone.utc))
    return await anyio.to_thread.run_sync(
        lambda: {
            "time_utc": when.utc.isot,
            "min_altitude": min_altitude,
            **_rank_catalog(
                list(dso_catalog.CATALOG),
                when,
                site.latitude_deg,
                site.longitude_deg,
                site.elevation_m,
                min_altitude,
                fov,
                level,
                kinds,
                top,
            ),
        }
    )


@mcp.tool()
async def sun_times() -> dict[str, Any]:
    """Returns the site's next sunrise, sunset, and all four twilight boundaries (civil, nautical, astronomical dusk and dawn), plus the astronomical-darkness window currently in effect or next to open -- the source of truth for "is the night over", "when does darkness start", and dusk/dawn scheduling.

    Events use standard definitions against apparent sun altitude: sunrise/sunset at -0.833 deg (refraction + solar semidiameter), civil twilight -6 deg, nautical -12 deg, astronomical -18 deg. Each time is the FIRST occurrence after now (UTC ISO). `darkness` pairs with `night_phase`: while "dark" it is the window containing now (`start_utc` in the past); while "morning" the night that just ended (what a night report documents); while "evening" the window about to open. Either side is `null` only in polar-day/polar-night conditions where the crossing does not occur within the window. `is_dark_now` is sun below -18 deg at this instant -- the same gate `best_targets` uses, so the two tools never disagree.

    `night_phase` classifies the moment for end-of-night decisions: "dark" (inside astronomical darkness), "morning" (darkness just ended / nearest boundary is astronomical dawn -- the night is over), "evening" (darkness begins before the last dawn recedes -- the night is still ahead). Use `night_phase == "morning"` rather than "no targets above the floor" as the signal to end a night: a mid-night gap between targets is not the end of the night. Read-only; takes no action, makes no N.I.N.A. calls beyond reading the site profile."""
    profile = await equipment_tools.get_site_profile()
    site = profile.site
    location = EarthLocation(
        lat=site.latitude_deg * u.deg,
        lon=site.longitude_deg * u.deg,
        height=site.elevation_m * u.m,
    )
    when = Time(datetime.now(timezone.utc))
    return await anyio.to_thread.run_sync(_sun_events, when, location)
