import math
import re
from datetime import datetime, timezone
from typing import Any

import anyio
import httpx
import numpy as np
from astropy import units as u
from astropy.coordinates import (
    AltAz,
    EarthLocation,
    GeocentricTrueEcliptic,
    SkyCoord,
    get_body,
    get_sun,
)
from astropy.time import Time

from .. import dso_catalog
from ..app import mcp
from . import equipment as equipment_tools

# --- name resolution --------------------------------------------------------
#
# Two layers, fastest/most-likely-live first:
#
# 1. SIMBAD's web interface on its live mirrors -- CfA Harvard, then IDIA
#    (South Africa). Both answer in well under a second.
# 2. Astropy's `SkyCoord.from_name` (SESAME), whose mirrors are CDS Strasbourg
#    + Harvard's Sesame -- kept as a backstop because it also consults NED/VizieR.
#
# Layer 2 leads only when layer 1 is down. CDS Strasbourg has been dark since
# its 2026-10-28 cybersecurity incident (its whole 130.79.128.x subnet silently
# drops SYNs), and Harvard's Sesame hangs on any name not already in its cache
# because its backend is that same Strasbourg -- so going to the mirrors
# directly is what keeps `resolve_target` working through the outage.

_SIMBAD_MIRRORS: tuple[str, ...] = (
    "https://simbad.cfa.harvard.edu",
    "https://simbad.idia.ac.za",
)
_SIMBAD_TIMEOUT_S = 10.0

# `Coordinates(ICRS,ep=J2000,eq=2000): 00 43 13.2  +61 46 26 (Opt ) E ...`
# RA is sexagesimal hours, Dec sexagesimal degrees with an explicit sign.
# SIMBAD's ASCII page carries arcsec precision, which is far finer than the
# arcmin-level initial slew this feeds; N.I.N.A.'s pointing/plate-solve does
# the rest.
_COORD_RE = re.compile(
    r"^Coordinates\(ICRS,ep=J2000[^)]*\):\s*"
    r"(\d{1,2}(?:\s+\d{1,2})+(?:\.\d+)?)"
    r"\s+"
    r"([+-]\d{1,2}(?:\s+\d{1,2})+(?:\.\d+)?)",
    re.MULTILINE,
)


def _fetch_simbad(base: str, name: str) -> str:
    """Fetch one mirror's ASCII `sim-id` page for ``name``."""
    resp = httpx.get(
        f"{base}/simbad/sim-id",
        params={"Ident": name, "output.format": "ASCII"},
        timeout=_SIMBAD_TIMEOUT_S,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


def _resolve_via_simbad_web(name: str) -> SkyCoord:
    """Resolve ``name`` against SIMBAD's live mirrors, Harvard then IDIA.

    Raises ``ValueError`` naming every mirror's reason when none can produce
    an ICRS coordinate for ``name`` (mirror unreachable, or SIMBAD does not
    know the identifier -- that page is HTTP 200 with a `!!` error and no
    coordinates line, so a plain status check would miss it).
    """
    errors: list[str] = []
    for base in _SIMBAD_MIRRORS:
        try:
            match = _COORD_RE.search(_fetch_simbad(base, name))
        except Exception as e:
            errors.append(f"{base}: {e}")
            continue
        if match is None:
            errors.append(f"{base}: no ICRS coordinates for {name!r}")
            continue
        ra, dec = match.group(1), match.group(2)
        return SkyCoord(ra, dec, unit=("hourangle", "deg"), frame="icrs")
    raise ValueError("; ".join(errors))


def _resolve_via_sesame(name: str) -> SkyCoord:
    """Astropy's SESAME resolver (CDS Strasbourg, then Harvard's Sesame)."""
    return SkyCoord.from_name(name)


def _resolve_name(name: str) -> SkyCoord:
    """Resolve a catalog name to coordinates: SIMBAD mirrors, then SESAME."""
    try:
        return _resolve_via_simbad_web(name)
    except Exception as web_error:
        try:
            return _resolve_via_sesame(name)
        except Exception as sesame_error:
            raise ValueError(
                f"SIMBAD mirrors failed ({web_error}); SESAME failed ({sesame_error})"
            ) from sesame_error


@mcp.tool()
async def resolve_target(name: str) -> dict[str, Any]:
    """Resolves a deep-sky object name (e.g. 'M31', 'NGC 6960') to J2000 RA/Dec via a SIMBAD lookup. NOT needed for any object in the built-in catalog (`best_targets` rows and `dso_catalog.csv`) -- those coordinates were already verified against SIMBAD and are returned ready to use, so prefer them and stay offline. Call this only for a name outside the catalog (e.g. a named target added to PLAN.md). Returns `ra_hours` and `dec_deg`. The lookup goes to SIMBAD's live mirrors (CfA Harvard, then IDIA) with astropy's SESAME resolver as a backstop, so it keeps working while CDS Strasbourg is offline; it still needs internet access. If every source fails, skip that target and pick another rather than guessing coordinates. This tool is read-only and takes no action."""
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
    """Altitude/azimuth of each target at the active site, sorted highest first. For candidates `best_targets` does not cover -- a PLAN.md named target outside the catalog. Each target: `{"name", "ra_hours"?, "dec_deg"?}`; passing `best_targets` row coords skips the SIMBAD lookup (name-only needs internet). `hours_ahead`: 0 = now. Geometric altitude; N.I.N.A. enforces the floor. Read-only."""
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


# --- moon (see moon_times) --------------------------------------------------
#
# astropy ships no lunar-phase helper (and `get_moon` was removed in 7.0 in
# favour of `get_body("moon", ...)`), so phase, illumination and rise/set are
# all derived here from `get_body` plus the same threshold-crossing sampler
# the sun events use. Phase is geocentric -- it is identical everywhere on
# Earth, which is what published tables report -- while altitude, azimuth,
# distance and rise/set are topocentric, i.e. what this site actually sees.
# Fully offline: astropy's builtin ephemeris, no network.

# Moon rise/set altitude. Meeus's standard altitude for the Moon is
# h0 = 0.7275*pi - 0.5667 deg (pi ~0.95 deg horizontal parallax) = +0.125
# deg, but that is the GEOCENTRIC figure: it folds in the parallax that drops
# the Moon's apparent position by up to ~0.95 deg. `get_body(..., location)`
# is already topocentric, so what is left is the usual apparent-upper-limb
# condition -- -(refraction + semidiameter) = -(0.5667 + 0.259) ~ -0.83 deg,
# the same number the sun events use (the Sun's and Moon's semidiameters are
# both ~0.26 deg). Measured against Ottawa's published table for 2026-11-07:
# -0.833 gives rise 10:11:15 / set 20:30:42 UTC against a published 10:11 /
# 20:30, while Meeus's +0.125 applied to topocentric altitudes lands 6
# minutes late on rise.
_MOON_HORIZON_ALT = -0.833

# One lunar day runs ~24h50m, so 48 h forward always contains a moonrise and
# a moonset unless the Moon genuinely never crosses the horizon (polar
# summer) -- then the entry is null, exactly as `sun_times` reports a polar
# twilight rather than inventing one.
_MOON_FORWARD_H = 48.0
_MOON_STEP_MIN = 5

# Phase events need a whole synodic month (29.53 d) of lookahead for the
# "next" of any principal phase, and the same again behind `when` for
# `age_days`, so +/-32 d at hourly cadence covers both with margin. Hourly
# linear interpolation tracks the ~0.5 deg/h drift of the Moon-Sun longitude
# difference to well under a minute -- verified against published phase
# times, which agree to the minute.
_PHASE_BACK_D = 32.0
_PHASE_FORWARD_D = 32.0
_PHASE_STEP_H = 1.0

# Principal phase instants, as angles of the Moon-minus-Sun ecliptic
# longitude difference: conjunction, eastern quadrature, opposition,
# western quadrature.
_PHASE_EVENT_ANGLES: tuple[tuple[str, float], ...] = (
    ("new_moon", 0.0),
    ("first_quarter", 90.0),
    ("full_moon", 180.0),
    ("last_quarter", 270.0),
)

# Illumination bands for `phase.name`. Boundaries sit where they are
# visually meaningful: the crescent/gibbous split is at half-lit, "quarter"
# is a +/-3% window around it, and new/full are the near-0 / near-1 plateaus
# (under 3% lit is within ~1.6 days of conjunction, over 97% its mirror at
# opposition). Illumination, not the name, is what decides a target: k is the
# Moon's contribution to sky background.
_PHASE_NEW_MAX = 0.03
_PHASE_FULL_MIN = 0.97
_PHASE_QUARTER_BAND = 0.03


def _moon_altitudes(times: Time, location: EarthLocation) -> list[float]:
    """Topocentric geometric moon altitude in degrees for each time.

    No refraction -- astropy's `AltAz` applies none at its default pressure
    of 0, which is why the crossing threshold above absorbs it. Accepts a
    scalar or vector `Time`; always returns a list.
    """
    moon = get_body("moon", times, location)
    altaz = moon.transform_to(AltAz(obstime=times, location=location))
    alts = np.atleast_1d(altaz.alt.deg)
    return [float(a) for a in alts]


def _elongation_series(times: Time) -> np.ndarray:
    """Moon-minus-Sun ecliptic longitude of date at each time, unwrapped.

    The Moon laps the Sun every 29.53 d (~12.2 deg/day) and never moves
    retrograde in ecliptic longitude, so consecutive hourly samples differ
    by a fraction of a degree and the unwrapped series is strictly
    increasing -- which is what lets every phase instant be found by binary
    search plus interpolation instead of a root find.
    """
    moon_lon = (
        get_body("moon", times)
        .transform_to(GeocentricTrueEcliptic(obstime=times))
        .lon.deg
    )
    sun_lon = get_sun(times).transform_to(GeocentricTrueEcliptic(obstime=times)).lon.deg
    delta = np.asarray((moon_lon - sun_lon) % 360.0, dtype=float)
    return np.unwrap(np.deg2rad(delta)) * 180.0 / math.pi


def _next_angle(base: float, target: float) -> float:
    """Smallest angle strictly greater than `base` congruent to `target`
    modulo 360."""
    angle = target + math.ceil((base - target) / 360.0) * 360.0
    return angle + 360.0 if angle <= base else angle


def _prev_angle(base: float, target: float) -> float:
    """Largest angle at or before `base` congruent to `target` modulo 360."""
    return target + math.floor((base - target) / 360.0) * 360.0


def _crossing_time(times: Time, values: np.ndarray, want: float) -> Time | None:
    """Time at which the strictly increasing `values` first reaches `want`.

    Linear interpolation between the bracketing samples. None when `want`
    falls outside the sampled window.
    """
    idx = int(np.searchsorted(values, want))
    if idx == 0 or idx >= len(values):
        return None
    lo, hi = float(values[idx - 1]), float(values[idx])
    if hi <= lo:
        return None
    frac = (want - lo) / (hi - lo)
    return times[idx - 1] + frac * (times[idx] - times[idx - 1])


def _phase_from_elongation(elongation_deg: float) -> dict[str, Any]:
    """Phase name, lit fraction, waxing flag and wrapped elongation.

    Pure function of the Moon-minus-Sun ecliptic longitude difference (any
    real value; taken modulo 360). Illumination is k = (1 - cos(elong))/2 --
    dropping the Moon's ecliptic latitude (<=5.1 deg) and the observer's
    offset from the geocenter moves k by under 0.5%, well inside the 1%
    published tables quote.
    """
    wrapped = float(elongation_deg) % 360.0
    illumination = (1.0 - math.cos(math.radians(wrapped))) / 2.0
    waxing = 0.0 < wrapped < 180.0
    if illumination < _PHASE_NEW_MAX:
        name = "new"
    elif illumination > _PHASE_FULL_MIN:
        name = "full"
    elif abs(illumination - 0.5) <= _PHASE_QUARTER_BAND:
        name = "first_quarter" if waxing else "last_quarter"
    elif illumination < 0.5:
        name = "waxing_crescent" if waxing else "waning_crescent"
    else:
        name = "waxing_gibbous" if waxing else "waning_gibbous"
    # Round first, then re-wrap: at new moon the elongation sits a hair under
    # 360, and rounding it would otherwise report `360.0` -- outside the
    # documented [0, 360) range -- for a Moon that is still a hair waning.
    return {
        "name": name,
        "illumination_fraction": round(illumination, 4),
        "waxing": waxing,
        "elongation_deg": round(wrapped, 3) % 360.0,
    }


def _moon_events(when: Time, location: EarthLocation) -> dict[str, Any]:
    """Phase, position, next moonrise/moonset, and the next four principal
    phase instants for `location` at `when`.

    Pure helper -- unit-testable without N.I.N.A. Events are the first
    occurrence strictly after `when`; each is null only when no crossing
    happens inside its sampling window (polar day/night for rise/set), never
    an error. Rise/set are topocentric for the site; phase instants and
    `age_days` are geocentric and therefore identical everywhere on Earth.
    """
    # Phase instants and age come off one unwrapped elongation series spanning
    # +/-32 days; `when` sits exactly at its midpoint index.
    samples = int((_PHASE_BACK_D + _PHASE_FORWARD_D) * 24 / _PHASE_STEP_H) + 1
    mid = samples // 2
    ptimes = when + (np.arange(samples) - mid) * _PHASE_STEP_H * u.hour
    elong = _elongation_series(ptimes)
    base = float(elong[mid])

    events: dict[str, Time | None] = {}
    for name, target in _PHASE_EVENT_ANGLES:
        events[name] = _crossing_time(ptimes, elong, _next_angle(base, target))
    previous_new = _crossing_time(ptimes, elong, _prev_angle(base, 0.0))

    # Horizon: topocentric altitude, first crossing of each kind after now.
    n_horizon = int(_MOON_FORWARD_H * 60 / _MOON_STEP_MIN) + 1
    htimes = when + np.arange(n_horizon) * _MOON_STEP_MIN * u.min
    alts = _moon_altitudes(htimes, location)
    rise: Time | None = None
    set_: Time | None = None
    for i in range(n_horizon - 1):
        if rise is None and alts[i] < _MOON_HORIZON_ALT <= alts[i + 1]:
            rise = _interp_time(htimes, alts, i, _MOON_HORIZON_ALT)
        if set_ is None and alts[i] >= _MOON_HORIZON_ALT > alts[i + 1]:
            set_ = _interp_time(htimes, alts, i, _MOON_HORIZON_ALT)
        if rise is not None and set_ is not None:
            break

    moon = get_body("moon", when, location)
    altaz = moon.transform_to(AltAz(obstime=when, location=location))
    age_days = (when - previous_new) / u.day if previous_new is not None else None

    phase = _phase_from_elongation(base)
    if age_days is not None:
        phase["age_days"] = round(float(age_days), 2)

    return {
        "time_utc": when.utc.isot,
        "site": {
            "latitude_deg": round(float(location.lat.deg), 4),
            "longitude_deg": round(float(location.lon.deg), 4),
            "elevation_m": round(float(location.height.to_value(u.m)), 1),
        },
        "moon_up_now": bool(float(altaz.alt.deg) > _MOON_HORIZON_ALT),
        "altitude_deg": round(float(altaz.alt.deg), 2),
        "azimuth_deg": round(float(altaz.az.deg), 2),
        # Topocentric (from the observer), so it can differ from an almanac's
        # geocentric figure by up to Earth's radius.
        "distance_km": round(float(moon.distance.to_value(u.km))),
        "phase": phase,
        "events": {
            name: (value.utc.isot if value is not None else None)
            for name, value in {
                "moonrise": rise,
                "moonset": set_,
                **events,
            }.items()
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
            # Pre-verified against SIMBAD: safe to point from these directly,
            # no resolve_target round-trip needed (works offline).
            "ra_hours": obj.ra_hours,
            "dec_deg": obj.dec_deg,
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
    """Ranks the Messier/bright-NGC catalog objects worth observing from the active site right now, each classified beginner/intermediate/advanced — "what should I look at tonight" with no star atlas. Tuned for EAA (short-subframe / live-stack).

    Difficulty comes from SURFACE BRIGHTNESS, not integrated magnitude: objects are modeled as ellipses, mu = mag + 2.5*log10(area in arcmin^2), because magnitude alone ranks diffuse M33 (mag 5.7) as easier than concentrated M57 (mag 8.8) — backwards. Compact objects (major axis under 5 arcmin: planetaries, compact globulars) bucket by integrated mag instead, since their light is concentrated anyway. Levels: "beginner" (shows well in short stacks, suburban skies), "intermediate", "advanced" (needs dark skies / long integration).

    Args: `min_altitude` (default 30, the usual PLAN.md floor) gates both now and tonight's peak; `top` caps each list (default 10); `level` filters to one difficulty; `kinds` filters by type — "galaxy", "globular", "open", "pn", "bn", "snr", "cloud".

    Returns `visible_now` (dark now and above the floor at this moment, sorted by a 0-100 score blending current altitude and easiness), `later_tonight` (peak altitude reached WITHIN astronomical darkness — sun below −18° — over the next 12 hours, carrying `peak_utc`/`peak_alt_deg` for scheduling; daytime and twilight peaks are never reported), and `is_dark_now` (when false, `visible_now` is empty and every row lands in `later_tonight`). Rows carry `surface_brightness` (mag/arcmin²), `large_for_field` (wider than the profile's field of view — mosaic/framing care), and `ra_hours`/`dec_deg` verified against SIMBAD: safe to point from directly, no `resolve_target` call and no network — use `resolve_target` only for a name outside this catalog. Read-only; no N.I.N.A. calls beyond reading the site profile. Site twilight boundaries come from `sun_times`."""
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


@mcp.tool()
async def moon_times() -> dict[str, Any]:
    """Returns the Moon's phase, illumination, position, next moonrise/moonset, and the next four principal phase instants (new, first quarter, full, last quarter) for the active site -- the lunar counterpart to `sun_times`, for judging how much moonlight a night will carry.

    `phase` gives `name` (one of new, waxing_crescent, first_quarter, waxing_gibbous, full, waning_gibbous, last_quarter, waning_crescent), `illumination_fraction` (0..1 -- THIS is the number to plan against: it is the Moon's contribution to sky background, so faint galaxies and nebulae want it low and lunar-rich targets want it high), `waxing` (true new -> full), `elongation_deg` (0..360; everything else derives from it), and `age_days` (days since the last new moon). Illumination uses the standard k = (1 - cos(elongation))/2; the name is banded off it (under 3% = new, over 97% = full, within 3% of half-lit = quarter, otherwise crescent below half / gibbous above), so near a boundary the fraction, not the name, is authoritative.

    `events` are the FIRST occurrence after now, UTC ISO, exactly as `sun_times` reports them: `moonrise` and `moonset` are topocentric horizon crossings for the site (upper limb apparent on the horizon -- geometric center altitude -0.833 deg, refraction and semidiameter folded in, matching how `sun_times` treats sunrise/sunset), and `new_moon`/`first_quarter`/`full_moon`/`last_quarter` are geocentric instants identical everywhere on Earth. `moonrise` or `moonset` is `null` only when the Moon does not cross the horizon within the next 48 h (polar day/night), never a failure.

    `moon_up_now`, `altitude_deg`, `azimuth_deg` and `distance_km` describe the Moon at this instant (topocentric, so `distance_km` can differ from an almanac's geocentric figure by up to Earth's radius). `moon_up_now` is defined against the same -0.833 deg line `moonrise`/`moonset` cross, so it is consistent with those two events.

    Combine with `sun_times`: `events.moonrise`/`moonset` against `darkness.start_utc`/`end_utc` says whether the Moon will be up during astronomical darkness, and `phase.illumination_fraction` says how bright it will be when it is. Read-only; no N.I.N.A. calls beyond reading the site profile, and no network."""
    profile = await equipment_tools.get_site_profile()
    site = profile.site
    location = EarthLocation(
        lat=site.latitude_deg * u.deg,
        lon=site.longitude_deg * u.deg,
        height=site.elevation_m * u.m,
    )
    when = Time(datetime.now(timezone.utc))
    return await anyio.to_thread.run_sync(_moon_events, when, location)
