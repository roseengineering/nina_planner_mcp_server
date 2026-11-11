#!/usr/bin/env python3
"""Survey the real horizon around the observatory and write a N.I.N.A. ``.hrz`` file.

N.I.N.A.'s custom horizon (Options -> General -> Custom Horizon) is plain text:
one ``azimuth altitude`` pair per line, degrees, gaps interpolated, starting at
``0`` and ending at ``360``.  This tool produces that file from photographs
instead of guesswork:

    devices    preflight: cameras / mount / wheel / safety / sequence / sun
    pan        slew a 0-360 grid, photograph every pointing, record the raw
               frame statistics N.I.N.A. extracted from each saved FITS
    build      turn the survey manifest into <name>.hrz (+ a PNG profile)

Only the N.I.N.A. **Advanced API** (v2) is spoken here, over plain HTTP:

    GET /v2/api/equipment/...        info / connect / slew / capture / filter
    GET /v2/api/image-history        RAW statistics of the last saved frame
    GET /v2/api/sequence/json        is a sequence running?
    GET /v2/api/profile/horizon      the horizon currently installed

Why frames must be saved: ``/equipment/camera/capture/statistics`` and the
streamed preview JPEG are N.I.N.A.'s *display-stretched* image (auto-stretched
to ~20% mean whatever the scene did), so cross-frame brightness comparison
through them is meaningless.  A frame saved with ``save=true`` lands as a real
FITS on the N.I.N.A. host, and its ``image-history`` record carries the TRUE
raw statistics (Mean / Median / StDev / Stars) - that is the metric ``build``
uses.  The local JPEG previews are for eyeballing only.

Endpoint defaults come from ``NINA_ENDPOINT`` (default ``127.0.0.1:1888``),
exactly like the nina-planner MCP server.

Examples
--------
    python3 scripts/horizon_pan.py devices               # what does N.I.N.A. see
    python3 scripts/horizon_pan.py pan --dry-run         # print the pointings
    python3 scripts/horizon_pan.py pan --filter L --ignore-safety
    python3 scripts/horizon_pan.py build --out observatory.hrz

Safety follows the observatory rules: ``is_safe=false`` (enclosure closed)
blocks sky-dependent acquisition unless ``--ignore-safety`` is passed, a
running sequence aborts the run, and no pointing may come within
``--sun-sep`` degrees of the Sun while it is above ``--sun-floor``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

DEFAULT_ENDPOINT = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")
DEFAULT_TARGET_NAME = "horizon_survey"

D2R = math.pi / 180.0
R2D = 180.0 / math.pi


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


class NinaError(RuntimeError):
    """Transport failure or a non-success N.I.N.A. API reply."""


class Nina:
    def __init__(self, endpoint: str, timeout: float = 30.0) -> None:
        self.base = "http://" + endpoint.rstrip("/") + "/v2/api"
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None, timeout: float | None = None):
        """GET ``path``; return the unwrapped ``Response``, or ``bytes`` for images."""
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "horizon-pan/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            raise NinaError(f"GET {path} -> HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NinaError(f"GET {path} failed: {exc}") from exc

        if "image/" in ctype:
            return body
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise NinaError(
                f"GET {path} returned non-JSON ({ctype}): {body[:200]!r}"
            ) from exc
        if not data.get("Success", False):
            raise NinaError(
                f"GET {path} refused: {data.get('Error') or data.get('Response')!r} "
                f"(StatusCode {data.get('StatusCode')})"
            )
        return data.get("Response")

    def info(self) -> dict:
        return self.get("/equipment/info") or {}


# ---------------------------------------------------------------------------
# Astronomy helpers (stdlib only)
# ---------------------------------------------------------------------------


def precess_to_j2000(ra_deg: float, dec_deg: float) -> tuple[float, float]:
    """IAU-1976 precession, of-date -> J2000 (sub-arcsecond over decades)."""
    now = datetime.now(UTC)
    years = (now.year + now.timetuple().tm_yday / 365.25) - 2000.0
    t = years / 100.0
    zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t**3) / 3600.0
    z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t**3) / 3600.0
    theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t**3) / 3600.0
    ra = ra_deg * D2R
    dec = dec_deg * D2R
    a = math.cos(dec) * math.sin(ra + zeta * D2R)
    b = math.cos(theta * D2R) * math.cos(dec) * math.cos(ra + zeta * D2R) - math.sin(
        theta * D2R
    ) * math.sin(dec)
    c = math.sin(theta * D2R) * math.cos(dec) * math.cos(ra + zeta * D2R) + math.cos(
        theta * D2R
    ) * math.sin(dec)
    ra_new = math.atan2(a, b) + z * D2R
    dec_new = math.asin(max(-1.0, min(1.0, c)))
    return (ra_new * R2D) % 360.0, dec_new * R2D


def altaz_to_radece(
    alt_deg: float,
    az_deg: float,
    lat_deg: float,
    lst_hours: float,
    epoch: str = "J2000",
) -> tuple[float, float]:
    """Alt/Az (geometric, az from North through East) -> (RA degrees, Dec degrees).

    ``epoch='J2000'`` precesses the of-date answer to J2000 (what ASCOM's
    ``SlewToCoordinates`` asks for); ``'JNOW'`` returns it of-date.  Which one
    the Advanced API wants is settled by ``pan``'s calibration slew, and the
    closed pointing loop corrects it either way.
    """
    lat, alt, az = lat_deg * D2R, alt_deg * D2R, az_deg * D2R
    sin_dec = math.sin(lat) * math.sin(alt) + math.cos(lat) * math.cos(alt) * math.cos(
        az
    )
    dec = math.asin(max(-1.0, min(1.0, sin_dec)))
    ha = math.atan2(
        -math.sin(az) * math.cos(alt),
        math.sin(alt) * math.cos(lat) - math.cos(alt) * math.sin(lat) * math.cos(az),
    )
    ra_deg = ((lst_hours - ha * R2D / 15.0) % 24.0) * 15.0
    dec_deg = dec * R2D
    if epoch == "J2000":
        ra_deg, dec_deg = precess_to_j2000(ra_deg, dec_deg)
    return ra_deg, dec_deg


def radece_to_altaz(
    ra_deg: float, dec_deg: float, lat_deg: float, lst_hours: float
) -> tuple[float, float]:
    """Inverse of :func:`altaz_to_radece` for of-date coordinates."""
    lat, dec = lat_deg * D2R, dec_deg * D2R
    ha = ((lst_hours * 15.0 - ra_deg) % 360.0) * D2R
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(
        ha
    )
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    az = math.atan2(
        -math.sin(ha) * math.cos(dec),
        math.sin(dec) * math.cos(lat) - math.cos(dec) * math.sin(lat) * math.cos(ha),
    )
    return alt * R2D, (az * R2D) % 360.0


def sun_altaz(
    lat_deg: float, lon_deg: float, when: datetime | None = None
) -> tuple[float, float]:
    """Low-precision solar position (NOAA-style); ~0.3 deg is plenty for a guard."""
    when = when or datetime.now(UTC)
    jd = when.timestamp() / 86400.0 + 2440587.5
    n = jd - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)) * R2D
    dec = math.asin(math.sin(eps) * math.sin(lam)) * R2D
    gmst = (18.697374558 + 24.06570982441908 * n) % 24.0
    lst = (gmst + lon_deg / 15.0) % 24.0
    return radece_to_altaz(ra, dec, lat_deg, lst)


def angular_sep(a1: float, z1: float, a2: float, z2: float) -> float:
    """Great-circle separation (deg) between two alt/az points."""
    a1, z1, a2, z2 = (x * D2R for x in (a1, z1, a2, z2))
    return (
        math.acos(
            max(
                -1.0,
                min(
                    1.0,
                    math.sin(a1) * math.sin(a2)
                    + math.cos(a1) * math.cos(a2) * math.cos(z1 - z2),
                ),
            )
        )
        * R2D
    )


# ---------------------------------------------------------------------------
# Shared preflight pieces
# ---------------------------------------------------------------------------


def sequence_is_running(nina: Nina) -> bool:
    """True when any node of ``/sequence/json`` reports RUNNING (the MCP's rule)."""

    def walk(node) -> bool:
        if isinstance(node, dict):
            if node.get("Status") == "RUNNING":
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    try:
        return walk(nina.get("/sequence/json"))
    except NinaError as exc:
        if "Sequencer not initialized" in str(exc):  # no sequence loaded at all
            return False
        raise


def pick_device(devices: list[dict], needle: str | None, kind: str) -> dict | None:
    if not needle:
        return None
    low = needle.lower()
    for dev in devices:
        hay = " ".join(
            str(dev.get(k) or "") for k in ("Id", "Name", "DisplayName", "Description")
        ).lower()
        if low in hay:
            return dev
    names = ", ".join(sorted({str(d.get("Name")) for d in devices})) or "<none>"
    raise NinaError(f"no {kind} matching {needle!r}; available: {names}")


def camera_fov(
    cam: dict, fov_w: float | None, fov_h: float | None
) -> tuple[float, float]:
    """(width_deg, height_deg) for the connected camera."""
    if fov_w and fov_h:
        return fov_w, fov_h
    x, y = int(cam.get("XSize") or 0), int(cam.get("YSize") or 0)
    name = " ".join(
        str(cam.get(k) or "") for k in ("Name", "DisplayName", "Description")
    ).lower()
    if "wide" in name:
        return 20.2, 11.4  # S30 wide lens, 23.2 deg diagonal
    if x and y:
        # S30 tele: 2.46 deg diagonal over 1080x1920 -> 1.20 x 2.13
        diag = 2.46
        w = diag * x / math.hypot(x, y)
        h = diag * y / math.hypot(x, y)
        return w, h
    return 1.2, 2.1


def grid_defaults(fov_w: float) -> tuple[int, str, float]:
    """(az_step_deg, pass-A rows, refine_step_deg) tuned to the frame width."""
    if fov_w >= 10:
        return 15, "5,15,25,35,45", 4.0
    if fov_w >= 5:
        return 8, "5,15,25,35,45", 2.5
    # S30 tele: 1.2 x 2.1 deg frame - rows every 6 deg, not 10, so the
    # bracket+refine pass can actually bracket a skyline instead of hopping
    # over most of the altitude range between samples.
    return 15, "3,9,15,21,27,33,39,45", 2.0


def frange(start: float, stop: float, step: float) -> list[float]:
    out, v = [], start
    while v < stop - 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def parse_rows(spec: str) -> list[float]:
    try:
        rows = [float(x) for x in spec.split(",") if x.strip() != ""]
    except ValueError as exc:
        raise SystemExit(
            f"--rows must be a comma list of altitudes, got {spec!r}"
        ) from exc
    if not rows:
        raise SystemExit("--rows produced no altitudes")
    return rows


def metric_key(metric: str) -> str:
    """Metric name -> the lowercase field name used inside manifest frames.

    The image-history record spells these ``Mean``/``Median``/``Stars``; the
    manifest stores them lowercase, so every metric lookup after capture goes
    through here.
    """
    return {"mean": "mean", "median": "median", "stars": "stars"}.get(metric, "mean")


def load_manifest(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"frames": []}


def save_manifest(path: str, manifest: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    os.replace(tmp, path)


def frame_key(az: float, alt: float) -> str:
    return f"{az:.1f}:{alt:.1f}"


# ---------------------------------------------------------------------------
# devices
# ---------------------------------------------------------------------------


def cmd_devices(args) -> int:
    nina = Nina(args.endpoint)
    info = nina.info()

    print(f"  endpoint   {nina.base}")
    mount = info.get("Mount") or {}
    cam = info.get("Camera") or {}
    wheel = info.get("FilterWheel") or {}
    safety = info.get("SafetyMonitor") or {}

    print(
        f"  mount      {mount.get('DisplayName') or mount.get('Name') or '?'}"
        f"  connected={mount.get('Connected')}"
    )
    if mount:
        print(
            f"             alt={mount.get('Altitude')} az={mount.get('Azimuth')}"
            f"  AtHome={mount.get('AtHome')} AtPark={mount.get('AtPark')}"
            f"  Slewing={mount.get('Slewing')} tracking={mount.get('TrackingMode')}"
        )
        print(
            f"             site lat={mount.get('SiteLatitude')}"
            f" lon={mount.get('SiteLongitude')}  LST={mount.get('SiderealTimeString')}"
            f"  epoch={mount.get('EquatorialSystem')}"
        )
    print(
        f"  camera     {cam.get('DisplayName') or cam.get('Name') or '?'}"
        f"  connected={cam.get('Connected')}"
        f"  sensor={cam.get('XSize')}x{cam.get('YSize')}"
        f"  gain={cam.get('Gain')}  exposing={cam.get('IsExposing')}"
    )
    if wheel:
        sel = (wheel.get("SelectedFilter") or {}).get("Name")
        avail = ", ".join(
            f.get("Name", "?") for f in (wheel.get("AvailableFilters") or [])
        )
        print(
            f"  filterwheel {wheel.get('DisplayName')}  selected={sel}"
            f"  available=[{avail}]"
        )
    print(f"  safety     IsSafe={safety.get('IsSafe')} ({safety.get('DisplayName')})")

    try:
        print(f"  sequence   running={sequence_is_running(nina)}")
    except NinaError as exc:
        print(f"  sequence   unknown ({exc})")

    try:
        cams = nina.get("/equipment/camera/list-devices") or []
        print("  cameras    available to connect (* = connected now):")
        for dev in cams:
            mark = "*" if dev.get("Connected") else " "
            print(
                f"    {mark} {dev.get('Name')}  [{dev.get('Category')}]"
                f"  {dev.get('Description') or ''}  id={dev.get('Id')}"
            )
    except NinaError as exc:
        print(f"  cameras    list failed ({exc})")

    try:
        hz = nina.get("/profile/horizon") or {}
        azs = hz.get("Azimuths") or []
        print(
            f"  horizon    {len(azs)} points installed"
            + ("" if azs else "  (none - flat 0 deg horizon)")
        )
    except NinaError as exc:
        print(f"  horizon    read failed ({exc})")

    lat = mount.get("SiteLatitude") or 0.0
    lon = mount.get("SiteLongitude") or 0.0
    s_alt, s_az = sun_altaz(lat, lon)
    guard = (
        ("DAYTIME: pointings within %.0f deg of the Sun will be SKIPPED" % args.sun_sep)
        if s_alt > args.sun_floor
        else "sun below floor - no guard needed"
    )
    print(f"  sun        alt={s_alt:.1f} az={s_az:.1f}  -> {guard}")

    print("\n  next:  python3 scripts/horizon_pan.py pan --dry-run")
    return 0


# ---------------------------------------------------------------------------
# pan
# ---------------------------------------------------------------------------


def build_grid(az_step: float, rows: list[float]) -> list[tuple[float, float]]:
    """Pass-A grid, serpentine so the mount never backtracks farther than one row."""
    points: list[tuple[float, float]] = []
    azs = frange(0.0, 360.0, az_step)
    for i, az in enumerate(azs):
        seq = rows if i % 2 == 0 else list(reversed(rows))
        for alt in seq:
            points.append((round(az, 3), alt))
    return points


def mount_info(nina: Nina) -> dict:
    """Lean per-device readback (Alt/Az, LST) - cheaper than the bundled /equipment/info."""
    return nina.get("/equipment/mount/info") or {}


def unpark_mount(nina: Nina) -> bool:
    """Unpark if the mount reports AtPark - the Alpaca API refuses to slew a
    parked mount (409 ``Mount parked``), so every slew path needs this first.

    Returns True when the mount is clear of park (or reports no park state),
    False if it is still parked after polling - the caller must not slew.
    """
    mm = mount_info(nina)
    if not mm.get("AtPark"):
        return True
    print("  mount is parked - unparking ...")
    try:
        nina.get("/equipment/mount/unpark", timeout=60.0)
    except NinaError as exc:
        print(f"  ! unpark failed ({exc})")
        return False
    for _ in range(40):
        mm = mount_info(nina)
        if not mm.get("AtPark"):
            print(f"  ✓ unparked: alt={mm.get('Altitude')} az={mm.get('Azimuth')}")
            return True
        time.sleep(0.75)
    print("  ! still reports AtPark after unpark - NOT slewing")
    return False


def slew_to(
    nina: Nina, alt: float, az: float, lat: float, lst: float, epoch: str, settle: float
) -> tuple[float, float, float, float, float]:
    """Slew to alt/az, verify against the mount's own Alt/Az, correct once.

    Returns ``(actual_alt, actual_az, cmd_ra_deg, cmd_dec_deg, error_deg)``.
    The *actual* values are what get recorded, so epoch slop only shifts where
    we think we aimed - never what the .hrz claims.
    """
    if not unpark_mount(nina):
        raise NinaError("mount is parked - slew refused (unpark failed)")
    ra, dec = altaz_to_radece(alt, az, lat, lst, epoch)
    nina.get(
        "/equipment/mount/slew",
        {
            "ra": round(ra, 6),
            "dec": round(dec, 6),
            "waitForResult": "true",
            "center": "false",
        },
        timeout=180.0,
    )
    time.sleep(settle)
    m = mount_info(nina)
    a_alt, a_az = float(m.get("Altitude") or 0.0), float(m.get("Azimuth") or 0.0)
    err = angular_sep(alt, az, a_alt, a_az)
    if err > 0.30:  # epoch guess off - correct once
        ra, dec = altaz_to_radece(
            alt - (a_alt - alt), az - (a_az - az), lat, lst, epoch
        )
        nina.get(
            "/equipment/mount/slew",
            {
                "ra": round(ra, 6),
                "dec": round(dec, 6),
                "waitForResult": "true",
                "center": "false",
            },
            timeout=180.0,
        )
        time.sleep(settle)
        m = mount_info(nina)
        a_alt, a_az = float(m.get("Altitude") or 0.0), float(m.get("Azimuth") or 0.0)
        err = angular_sep(alt, az, a_alt, a_az)
        if err > 1.0:
            print(f"      ! pointing {err:.2f} deg off target - recorded as-is")
    return a_alt, a_az, ra, dec, err


def capture_frame(nina: Nina, args, target_name: str, preview_path: str | None) -> dict:
    """One exposure: save the raw FITS (for image-history stats) + grab a preview."""
    params = {
        "duration": args.duration,
        "gain": args.gain,
        "save": "true",
        "waitForResult": "true",
        "imageType": "SNAPSHOT",
        "targetName": target_name,
    }
    if preview_path:
        params.update({"stream": "true", "quality": "85"})
    last_err: Exception | None = None
    for attempt in (0, 1):
        try:
            body = nina.get("/equipment/camera/capture", params, timeout=300.0)
            if preview_path and isinstance(body, (bytes, bytearray)):
                with open(preview_path, "wb") as fh:
                    fh.write(body)
            elif preview_path and attempt == 0:
                # server answered JSON instead of an image - retry without preview
                params.pop("stream", None)
                params.pop("quality", None)
                last_err = RuntimeError("capture did not stream an image")
                continue
            break
        except NinaError as exc:
            last_err = exc
            if attempt == 0 and "stream" in params:
                params.pop("stream", None)
                params.pop("quality", None)
                continue
            raise
    else:
        if last_err:
            raise last_err

    # RAW statistics of the frame just saved (single-record buffer -> poll freshness)
    prev_date = capture_frame._last_date
    record: dict = {}
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        recs = nina.get("/image-history") or []
        if isinstance(recs, dict):
            recs = [recs]
        if recs:
            newest = recs[-1]
            date = str(newest.get("Date") or "")
            if date and date != prev_date:
                record = newest
                capture_frame._last_date = date
                break
        time.sleep(0.7)
    if not record:
        print("      ! no fresh image-history record (stats missing for this frame)")
    return record


capture_frame._last_date = ""


def park_mount(nina: Nina) -> None:
    """Park the mount - on the Seestar this closes the lens - polling to confirm.

    ``Find home`` is never used: home only points the scope straight down and
    leaves the lens open, so it is not a closing maneuver. A mount reporting
    ``CanPark=false`` is left alone and reported - there is no fallback.
    """
    mm = mount_info(nina)
    if not mm.get("CanPark"):
        print("  ! mount reports CanPark=false - left where it is (no fallback)")
        return
    print("  parking the mount ...")
    try:
        nina.get("/equipment/mount/park", timeout=60.0)
        mm = mount_info(nina)
        for _ in range(60):
            if mm.get("AtPark") and not mm.get("Slewing"):
                break
            time.sleep(3)
            mm = mount_info(nina)
        print(
            f"  ✓ parked: alt={mm.get('Altitude')} az={mm.get('Azimuth')}"
            f" AtPark={mm.get('AtPark')} Slewing={mm.get('Slewing')}"
        )
    except NinaError as exc:
        print(f"  ! parking failed ({exc}) - PARK THE MOUNT MANUALLY")


def cmd_pan(args) -> int:
    nina = Nina(args.endpoint)

    # ---- preflight (a dry run reports conditions, it never gates) -------
    info = nina.info()
    mount, cam = info.get("Mount") or {}, info.get("Camera") or {}
    safety = info.get("SafetyMonitor") or {}

    try:
        running = sequence_is_running(nina)
    except NinaError as exc:
        running = None
        print(f"  ! could not read sequence state ({exc})")
    if running and not args.dry_run:
        print(
            "  ✗ a N.I.N.A. sequence is RUNNING - stop it first"
            " (nina-planner stop_sequence, or N.I.N.A.'s sequencer)."
        )
        return 2
    if safety.get("IsSafe") is False and not args.ignore_safety and not args.dry_run:
        print(
            "  ✗ IsSafe=false (enclosure closed) - sky-dependent acquisition is"
            " gated.\n     Re-run with --ignore-safety only if the sky is"
            " actually open."
        )
        return 3

    if not mount.get("Connected") and not args.dry_run:
        ensure_connected(nina, None, "mount")
    if (not cam.get("Connected") or args.camera) and not args.dry_run:
        cams = nina.get("/equipment/camera/list-devices") or []
        ensure_connected(nina, pick_device(cams, args.camera, "camera"), "camera")
        info = nina.info()
        cam = info.get("Camera") or {}
        mount = info.get("Mount") or {}

    fov_w, fov_h = camera_fov(cam, args.fov_w, args.fov_h)
    az_step = args.az_step or grid_defaults(fov_w)[0]
    rows_spec = args.rows or grid_defaults(fov_w)[1]
    refine_step = args.refine_step if args.refine_step else grid_defaults(fov_w)[2]
    rows = parse_rows(rows_spec)
    grid = build_grid(az_step, rows)

    lat = float(mount.get("SiteLatitude") or 0.0)
    lon = float(mount.get("SiteLongitude") or 0.0)

    sun_alt, sun_az = sun_altaz(lat, lon)
    sun_guard = sun_alt > args.sun_floor

    print(
        f"  camera     {cam.get('DisplayName') or cam.get('Name')}"
        f"  sensor={cam.get('XSize')}x{cam.get('YSize')}"
        f"  frame ~{fov_w:.1f} x {fov_h:.1f} deg"
    )
    print(
        f"  grid       {len(frange(0.0, 360.0, az_step))} azimuths x"
        f" {len(rows)} rows (step {az_step:g} deg, rows {rows_spec})"
        + ("" if args.no_refine else f" + refine every {refine_step:g} deg")
    )
    print(
        f"  exposure   {args.duration}s gain {args.gain}"
        f"  filter {'(leave as-is)' if args.filter.lower() in ('', 'none') else args.filter}"
    )
    print(
        f"  sun        alt={sun_alt:.1f} az={sun_az:.1f}"
        + (f"  -> guard ON (skip within {args.sun_sep:g} deg)" if sun_guard else "")
    )
    print(f"  output     {args.out}")

    if args.dry_run:
        print("\n  dry run - pointings (az, alt):")
        for az, alt in grid:
            note = ""
            if sun_guard and angular_sep(alt, az, sun_alt, sun_az) < args.sun_sep:
                note = "   [SKIP: sun]"
            print(f"    az {az:6.1f}  alt {alt:5.1f}{note}")
        print(
            f"\n  {len(grid)} pointings; refine pass would add samples between"
            " the bracketing rows."
        )
        return 0

    # ---- filter ---------------------------------------------------------
    wheel = info.get("FilterWheel") or {}
    if args.filter and args.filter.lower() not in ("", "none") and wheel:
        want = args.filter.lower()
        avail = wheel.get("AvailableFilters") or []
        match = next((f for f in avail if str(f.get("Name", "")).lower() == want), None)
        if match is None:
            print(
                f"  ✗ filter {args.filter!r} not on this wheel; available: "
                + ", ".join(str(f.get("Name")) for f in avail)
            )
            return 4
        if str((wheel.get("SelectedFilter") or {}).get("Name", "")).lower() != want:
            print(f"  selecting filter {match.get('Name')} ...")
            nina.get(
                "/equipment/filterwheel/change-filter",
                {"filterId": match["Id"]},
                timeout=60.0,
            )
            time.sleep(2.0)
            wheel = nina.info().get("FilterWheel") or {}
            sel = (wheel.get("SelectedFilter") or {}).get("Name")
            if str(sel).lower() != want:
                print(
                    f"  ✗ wheel still reports {sel!r} - aborting so frames are"
                    " not shot through the wrong slot."
                )
                return 4

    # ---- resume state ---------------------------------------------------
    os.makedirs(os.path.join(args.out, "previews"), exist_ok=True)
    manifest_path = os.path.join(args.out, "manifest.json")
    manifest = load_manifest(manifest_path)
    manifest.setdefault("frames", [])
    done = {f"{f['az_target']:.1f}:{f['alt_target']:.1f}" for f in manifest["frames"]}
    manifest.setdefault("meta", {}).update(
        {
            "endpoint": nina.base,
            "camera": cam.get("DisplayName") or cam.get("Name"),
            "sensor": [cam.get("XSize"), cam.get("YSize")],
            "fov_deg": [round(fov_w, 3), round(fov_h, 3)],
            "az_step": az_step,
            "rows": rows_spec,
            "refine_step": None if args.no_refine else refine_step,
            "duration_s": args.duration,
            "gain": args.gain,
            "filter": args.filter,
            "sun": {"alt": round(sun_alt, 2), "az": round(sun_az, 2)},
        }
    )
    if not capture_frame._last_date:
        # Seed the freshness watermark from whatever image-history holds NOW, so
        # a record left over from an earlier run can never be mistaken for the
        # frame we are about to take.
        try:
            recs = nina.get("/image-history") or []
            if isinstance(recs, dict):
                recs = [recs]
            if recs:
                capture_frame._last_date = str(recs[-1].get("Date") or "")
        except NinaError:
            pass
    if not capture_frame._last_date and manifest["frames"]:
        capture_frame._last_date = manifest["frames"][-1].get("date", "")

    # ---- epoch: reuse the manifest's convention, else calibrate once -----
    epoch = args.epoch
    recorded = {f.get("epoch") for f in manifest["frames"] if f.get("epoch")}
    if recorded:
        epoch = str(sorted(recorded)[-1])
        print(f"  epoch     {epoch} (reused from the existing manifest)")
    elif not args.no_calibrate:
        safe = next(
            (
                (az, alt)
                for az, alt in grid
                if not (
                    sun_guard and angular_sep(alt, az, sun_alt, sun_az) < args.sun_sep
                )
            ),
            grid[0],
        )
        epoch = calibrate_epoch(nina, safe, lat, args)

    # ---- pass A ----------------------------------------------------------
    skipped: list[tuple[float, float]] = []
    t0 = time.monotonic()
    try:
        for i, (az, alt) in enumerate(grid, 1):
            key = frame_key(az, alt)
            if key in done:
                continue
            if sun_guard and angular_sep(alt, az, sun_alt, sun_az) < args.sun_sep:
                skipped.append((az, alt))
                print(f"  [{i}/{len(grid)}] az {az:5.1f} alt {alt:4.1f}  skipped (sun)")
                continue
            lst = float(mount_info(nina).get("SiderealTime") or 0.0)
            a_alt, a_az, ra, dec, err = slew_to(
                nina, alt, az, lat, lst, epoch, args.settle
            )
            preview = os.path.join(
                args.out, "previews", f"az{az:06.1f}_alt{alt:05.1f}.jpg"
            )
            rec = capture_frame(nina, args, args.target_name, preview)
            entry = {
                "pass": "A",
                "az_target": az,
                "alt_target": alt,
                "az_actual": round(a_az, 3),
                "alt_actual": round(a_alt, 3),
                "pointing_err_deg": round(err, 3),
                "cmd_ra_deg": round(ra, 4),
                "cmd_dec_deg": round(dec, 4),
                "epoch": epoch,
                "duration_s": args.duration,
                "gain": args.gain,
                "filter": args.filter,
                "date": str(rec.get("Date") or ""),
                "mean": rec.get("Mean"),
                "median": rec.get("Median"),
                "stdev": rec.get("StDev"),
                "stars": rec.get("Stars"),
                "file": rec.get("Filename"),
                "preview": preview if os.path.exists(preview) else None,
            }
            manifest["frames"].append(entry)
            done.add(key)
            save_manifest(manifest_path, manifest)
            print(
                f"  [{i}/{len(grid)}] az {az:5.1f} alt {alt:4.1f} -> "
                f"actual {a_alt:5.1f}/{a_az:6.1f}  mean="
                f"{entry['mean'] if entry['mean'] is None else round(float(entry['mean']))}"
                f"  ({time.monotonic() - t0:.0f}s)"
            )
    except KeyboardInterrupt:
        print(
            "\n  stopped by user - manifest saved; re-run the same command"
            " to resume where it left off."
        )
        if args.park_at_end:
            park_mount(nina)
        return 130

    # ---- pass B: refine the crossing inside each bracket -----------------
    if not args.no_refine:
        vals = [
            float(f[metric_key(args.metric)])
            for f in manifest["frames"]
            if f.get(metric_key(args.metric)) is not None
        ]
        threshold = otsu_threshold(vals) if len(vals) >= 4 else None
        refine_pass(
            nina,
            args,
            manifest,
            manifest_path,
            refine_step,
            lat,
            epoch,
            sun_alt,
            sun_az,
            sun_guard,
            skipped,
            threshold,
        )

    # ---- park on the way out (night runs are unattended) ----------------
    if args.park_at_end:
        park_mount(nina)

    frames = manifest["frames"]
    print(f"\n  ✓ {len(frames)} frames surveyed into {manifest_path}")
    if skipped:
        print(
            f"  ! {len(skipped)} pointings skipped for sun proximity - they"
            " appear as gaps in the .hrz (N.I.N.A. interpolates across them)"
        )
    print(
        "  next:  python3 scripts/horizon_pan.py build --out "
        + os.path.join(args.out, "observatory.hrz")
    )
    return 0


def calibrate_epoch(nina: Nina, first: tuple[float, float], lat: float, args) -> str:
    """Slew once to settle J2000-vs-JNOW, then keep the convention that fit.

    ``first`` is a grid tuple ``(az, alt)`` - the same shape build_grid emits.
    """
    az, alt = first
    print(f"  calibrating coordinate epoch at az {az:g} alt {alt:g} ...")
    lst = float(mount_info(nina).get("SiderealTime") or 0.0)
    for epoch in (args.epoch, "JNOW" if args.epoch == "J2000" else "J2000"):
        ra, dec = altaz_to_radece(alt, az, lat, lst, epoch)
        nina.get(
            "/equipment/mount/slew",
            {
                "ra": round(ra, 6),
                "dec": round(dec, 6),
                "waitForResult": "true",
                "center": "false",
            },
            timeout=180.0,
        )
        time.sleep(args.settle)
        m = mount_info(nina)
        err = angular_sep(
            alt, az, float(m.get("Altitude") or 0.0), float(m.get("Azimuth") or 0.0)
        )
        print(f"    epoch {epoch}: error {err:.3f} deg")
        if err <= 0.30:
            print(f"  ✓ using epoch {epoch}")
            return epoch
    print(
        "  ! neither epoch matched closely; the per-point correction loop"
        " will absorb the difference"
    )
    return args.epoch


def refine_pass(
    nina: Nina,
    args,
    manifest: dict,
    manifest_path: str,
    refine_step: float,
    lat: float,
    epoch: str,
    sun_alt: float,
    sun_az: float,
    sun_guard: bool,
    skipped: list[tuple[float, float]],
    threshold: float | None,
) -> None:
    """Photograph the interior of each pass-A bracket that straddles the skyline."""
    by_az: dict[float, list[dict]] = {}
    for f in manifest["frames"]:
        by_az.setdefault(round(float(f["az_target"]), 1), []).append(f)

    todo: list[tuple[float, float]] = []
    for az in sorted(by_az):
        samples = sorted(by_az[az], key=lambda f: float(f["alt_target"]))
        for lo, hi in zip(samples, samples[1:]):
            v_lo = lo.get(metric_key(args.metric))
            v_hi = hi.get(metric_key(args.metric))
            if v_lo is None or v_hi is None:
                continue
            if threshold is not None:
                if not (float(v_lo) < threshold <= float(v_hi)):
                    continue  # this bracket does not contain the skyline
            elif float(v_lo) >= float(v_hi):
                continue  # no rise -> no crossing
            a0, a1 = float(lo["alt_target"]), float(hi["alt_target"])
            alt = a0 + refine_step
            while alt < a1 - 0.05:
                todo.append((round(az, 1), round(alt, 3)))
                alt += refine_step
    if not todo:
        print("  refine: no brackets need sampling")
        return

    print(f"  refine: {len(todo)} extra pointings")
    done = {f"{f['az_target']:.1f}:{f['alt_target']:.1f}" for f in manifest["frames"]}
    try:
        for i, (az, alt) in enumerate(todo, 1):
            if frame_key(az, alt) in done:
                continue
            if sun_guard and angular_sep(alt, az, sun_alt, sun_az) < args.sun_sep:
                skipped.append((az, alt))
                continue
            lst = float(mount_info(nina).get("SiderealTime") or 0.0)
            a_alt, a_az, ra, dec, err = slew_to(
                nina, alt, az, lat, lst, epoch, args.settle
            )
            preview = os.path.join(
                args.out, "previews", f"az{az:06.1f}_alt{alt:05.1f}.jpg"
            )
            rec = capture_frame(nina, args, args.target_name, preview)
            manifest["frames"].append(
                {
                    "pass": "B",
                    "az_target": az,
                    "alt_target": alt,
                    "az_actual": round(a_az, 3),
                    "alt_actual": round(a_alt, 3),
                    "pointing_err_deg": round(err, 3),
                    "cmd_ra_deg": round(ra, 4),
                    "cmd_dec_deg": round(dec, 4),
                    "epoch": epoch,
                    "duration_s": args.duration,
                    "gain": args.gain,
                    "filter": args.filter,
                    "date": str(rec.get("Date") or ""),
                    "mean": rec.get("Mean"),
                    "median": rec.get("Median"),
                    "stdev": rec.get("StDev"),
                    "stars": rec.get("Stars"),
                    "file": rec.get("Filename"),
                    "preview": preview if os.path.exists(preview) else None,
                }
            )
            save_manifest(manifest_path, manifest)
            if i % 5 == 0 or i == len(todo):
                print(f"    refine {i}/{len(todo)}")
    except KeyboardInterrupt:
        print("\n  refine stopped by user - manifest saved; re-run to resume.")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def otsu_threshold(values: list[float]) -> float:
    """Otsu on a coarse histogram - splits 'obstructed' from 'sky' frame metrics."""
    lo, hi = min(values), max(values)
    if hi - lo <= 0:
        return lo
    bins = 64
    hist = [0] * bins
    for v in values:
        hist[min(bins - 1, int((v - lo) / (hi - lo) * bins))] += 1
    total = len(values)
    sum_all = sum(i * hist[i] for i in range(bins))
    sum_b, w_b, best, thresh = 0.0, 0, -1.0, lo
    for i in range(bins):
        w_b += hist[i]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += i * hist[i]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best:
            best, thresh = var, lo + (i + 0.5) * (hi - lo) / bins
    return thresh


def skyline_for_az(
    frames: list[dict], threshold: float, metric: str
) -> tuple[float, str]:
    """Interpolated altitude where this azimuth's frames cross the threshold."""
    key = metric_key(metric)
    pts = sorted(
        (
            (float(f["alt_actual"]), float(f[key]))
            for f in frames
            if f.get(key) is not None
        ),
        key=lambda p: p[0],
    )
    if not pts:
        raise ValueError("no frames with a usable metric")
    above = [p for p in pts if p[1] >= threshold]
    below = [p for p in pts if p[1] < threshold]
    if not above:
        return pts[-1][0], "saturated"  # obstruction higher than we looked
    if not below:
        return pts[0][0], "open"  # sky down to the lowest frame taken
    for (a0, v0), (a1, v1) in zip(pts, pts[1:]):
        if v0 < threshold <= v1:
            if v1 == v0:
                return a0, "ok"
            return a0 + (threshold - v0) * (a1 - a0) / (v1 - v0), "ok"
    return above[0][0], "ok"


def median3(circ: list[float]) -> list[float]:
    out = []
    n = len(circ)
    for i in range(n):
        win = sorted([circ[(i - 1) % n], circ[i], circ[(i + 1) % n]])
        out.append(win[1])
    return out


def cmd_build(args) -> int:
    manifest = load_manifest(args.manifest)
    frames = manifest.get("frames") or []
    if not frames:
        print(f"  ✗ no frames in {args.manifest} - run `pan` first")
        return 2

    metric = args.metric
    values = [
        float(f[metric_key(metric)])
        for f in frames
        if f.get(metric_key(metric)) is not None
    ]
    if len(values) < 4:
        print(
            f"  ✗ only {len(values)} usable {metric} values - not enough to"
            " find a skyline"
        )
        return 2
    threshold = args.threshold if args.threshold is not None else otsu_threshold(values)
    spread = max(values) - min(values)
    print(
        f"  frames     {len(frames)} ({sum(1 for f in frames if f.get('pass') == 'A')}"
        f" grid + {sum(1 for f in frames if f.get('pass') == 'B')} refine)"
    )
    print(
        f"  metric     {metric}: min {min(values):.0f} max {max(values):.0f}"
        f" spread {spread:.0f} -> threshold {threshold:.0f}"
    )
    if spread < abs(threshold) * 0.25 and metric != "stars":
        print(
            "  ! the two plateaus barely separate - the sky may have been"
            " cloudy or the exposure badly chosen; check previews/ before"
            " trusting this file"
        )

    by_az: dict[float, list[dict]] = {}
    for f in frames:
        # Group by the TARGET azimuth, never the readback: az_actual wobbles
        # +-0.3 deg per slew, which would shatter one column into single-frame
        # groups and make crossing detection meaningless.
        by_az.setdefault(round(float(f["az_target"]), 1), []).append(f)

    azs = sorted(by_az)
    points: list[tuple[float, float, str]] = []
    for az in azs:
        alt, note = skyline_for_az(by_az[az], threshold, metric)
        points.append((az, max(0.0, min(90.0, alt)), note))

    if not args.no_smooth and len(points) >= 3:
        vals = median3([p[1] for p in points])
        points = [(points[i][0], vals[i], points[i][2]) for i in range(len(points))]

    # wrap: guarantee the file starts at 0 and ends at 360 with equal values
    wrapped: list[tuple[float, float]] = [(a, v) for a, v, _ in points]
    if wrapped[0][0] > 0.0:
        first = wrapped[0]
        wrapped.insert(0, (0.0, first[1]))
    if wrapped[-1][0] < 360.0:
        wrapped.append((360.0, wrapped[0][1]))
    else:
        wrapped[-1] = (360.0, wrapped[0][1])

    notes = [n for _, _, n in points]
    gaps = [
        (wrapped[i][0], wrapped[i + 1][0])
        for i in range(len(wrapped) - 1)
        if wrapped[i + 1][0] - wrapped[i][0] > 45.0
    ]

    meta = manifest.get("meta") or {}
    expo = meta.get("duration_s")
    header = [
        "# Custom horizon generated by scripts/horizon_pan.py",
        f"# {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"# camera: {meta.get('camera') or '?'}"
        f"  exposure: {'?' if expo is None else expo}s"
        f" gain {meta.get('gain', '?')}"
        f" filter {meta.get('filter', '?')}",
        f"# metric: {metric}, threshold {threshold:.0f}, {len(wrapped)} points",
        "# columns: azimuth_deg  altitude_deg",
    ]
    body = "\n".join(f"{a:g} {v:.1f}" for a, v in wrapped) + "\n"
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n" + body)

    print(f"  ✓ wrote {args.out}  ({len(wrapped)} points, az 0..360)")
    print("\n   az    alt   note")
    for az, alt, note in points:
        print(f"  {az:5.1f}  {alt:5.1f}  {note}")
    if notes.count("saturated"):
        print(
            f"  ! {notes.count('saturated')} azimuths where the obstruction"
            " exceeded the highest frame taken - raise --rows/--alt ceiling"
            " and re-shoot those azimuths for a truthful file"
        )
    if gaps:
        print(
            "  ! wide gaps (skipped for Sun): "
            + ", ".join(f"{a:g}-{b:g}" for a, b in gaps)
        )

    try:
        write_profile_png(wrapped, args.png)
        print(f"  ✓ profile picture {args.png}")
    except ImportError:
        print("  (Pillow not available - skipped the PNG profile)")

    print(
        "\n  install: N.I.N.A. -> Options -> General -> Custom Horizon ->"
        f" browse to {os.path.abspath(args.out)}"
    )
    return 0


def write_profile_png(points: list[tuple[float, float]], path: str) -> None:
    from PIL import Image, ImageDraw

    w, h = 1200, 420
    img = Image.new("RGB", (w, h), (16, 18, 24))
    d = ImageDraw.Draw(img)
    for alt in range(0, 91, 15):
        y = h - 40 - int((h - 70) * alt / 90.0)
        d.line([(60, y), (w - 20, y)], fill=(48, 52, 62))
        d.text((12, y - 6), f"{alt}", fill=(150, 155, 165))
    for az in range(0, 361, 45):
        x = 60 + int((w - 80) * az / 360.0)
        d.line([(x, 30), (x, h - 40)], fill=(38, 42, 50))
        d.text((x - 10, h - 32), f"{az}", fill=(150, 155, 165))
    xy = [
        (60 + (w - 80) * az / 360.0, h - 40 - (h - 70) * alt / 90.0)
        for az, alt in points
    ]
    d.line(xy, fill=(120, 220, 160), width=3)
    d.text((60, 8), "observed horizon  (az -> blocked altitude)", fill=(220, 225, 235))
    img.save(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="horizon_pan.py",
        description="Photograph the horizon in 0-360 and build a N.I.N.A. .hrz file.",
    )
    ap.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"N.I.N.A. Advanced API host:port (default {DEFAULT_ENDPOINT})",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_dev = sub.add_parser("devices", help="preflight report")
    p_dev.add_argument("--sun-sep", type=float, default=25.0)
    p_dev.add_argument("--sun-floor", type=float, default=-6.0)
    p_dev.set_defaults(func=cmd_devices)

    p_pan = sub.add_parser("pan", help="slew the grid and photograph it")
    p_pan.add_argument(
        "--out",
        default="horizon_survey",
        help="output dir for manifest.json + previews/ (default horizon_survey)",
    )
    p_pan.add_argument(
        "--target-name",
        default=DEFAULT_TARGET_NAME,
        help="N.I.N.A. targetName - names the saved-FITS folder"
        f" under the imaging root (default {DEFAULT_TARGET_NAME})",
    )
    p_pan.add_argument(
        "--camera",
        default=None,
        help="connect this camera first (substring of name/id, e.g. 'wide')",
    )
    p_pan.add_argument(
        "--filter",
        default="L",
        help="filter to select before shooting ('none' to leave as-is)",
    )
    p_pan.add_argument("--duration", default="2", help="exposure seconds (default 2)")
    p_pan.add_argument("--gain", default="200", help="gain (default 200)")
    p_pan.add_argument(
        "--az-step",
        type=float,
        default=None,
        help="azimuth step deg (default from frame width: 15 for wide)",
    )
    p_pan.add_argument(
        "--rows",
        default=None,
        help="pass-A altitude rows, comma list (default 5,15,25,35,45)",
    )
    p_pan.add_argument(
        "--refine-step",
        type=float,
        default=None,
        help="altitude step of the refine pass (default 4 for wide)",
    )
    p_pan.add_argument(
        "--no-refine",
        action="store_true",
        help="skip the refine pass (faster, coarser)",
    )
    p_pan.add_argument(
        "--metric",
        default="mean",
        choices=("mean", "median", "stars"),
        help="frame statistic used to find the skyline",
    )
    p_pan.add_argument(
        "--settle",
        type=float,
        default=1.5,
        help="seconds to wait after each slew (default 1.5)",
    )
    p_pan.add_argument(
        "--epoch",
        default="JNOW",
        choices=("J2000", "JNOW"),
        help="coordinate epoch to try first (default JNOW - measured on this"
        " rig: 0.07 deg vs 0.46 deg for J2000; calibration confirms)",
    )
    p_pan.add_argument(
        "--no-calibrate",
        action="store_true",
        help="skip the coordinate-epoch calibration slew",
    )
    p_pan.add_argument(
        "--park-at-end",
        action="store_true",
        help="park the mount (closes the lens) when the survey finishes"
        " or is interrupted",
    )
    p_pan.add_argument(
        "--sun-sep",
        type=float,
        default=25.0,
        help="keep this many deg away from the Sun (default 25)",
    )
    p_pan.add_argument(
        "--sun-floor",
        type=float,
        default=-6.0,
        help="Sun below this altitude disables the guard (default -6)",
    )
    p_pan.add_argument(
        "--ignore-safety",
        action="store_true",
        help="shoot even though IsSafe=false (enclosure closed)",
    )
    p_pan.add_argument(
        "--fov-w",
        type=float,
        default=None,
        help="override frame width deg (default: from camera)",
    )
    p_pan.add_argument(
        "--fov-h",
        type=float,
        default=None,
        help="override frame height deg (default: from camera)",
    )
    p_pan.add_argument(
        "--dry-run", action="store_true", help="print the pointings and exit"
    )
    p_pan.set_defaults(func=cmd_pan)

    p_bld = sub.add_parser("build", help="manifest -> .hrz")
    p_bld.add_argument(
        "--manifest", default=os.path.join("horizon_survey", "manifest.json")
    )
    p_bld.add_argument("--out", default="observatory.hrz")
    p_bld.add_argument(
        "--png", default=None, help="profile PNG path (default alongside --out)"
    )
    p_bld.add_argument("--metric", default="mean", choices=("mean", "median", "stars"))
    p_bld.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the sky/obstruction threshold (default Otsu)",
    )
    p_bld.add_argument(
        "--no-smooth",
        action="store_true",
        help="disable the 3-point median filter on the profile",
    )
    p_bld.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    if args.cmd == "build" and not args.png:
        base, _ = os.path.splitext(args.out)
        args.png = base + ".png"
    try:
        return args.func(args)
    except BrokenPipeError:  # e.g. `pan --dry-run | head`
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except NinaError as exc:
        print(f"\n  ✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
