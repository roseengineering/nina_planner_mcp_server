import json
import os
import re
import subprocess
import sys
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import anyio
import httpx
from mcp.server.fastmcp import FastMCP

from .imaging import (
    read_imaging_csv,
    read_weather_csv,
    widen_imaging_metadata,
    windows_to_local,
)
from .models.observatory import ObservatoryEquipment
from .models.plan import ObservationPlan
from .models.profile import (
    EquipmentConfig,
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    SiteLocationInfo,
)
from .nina_utils import ascom_float, ascom_int, to_snake
from .progress import (
    append_progress_entry,
    filter_metadata_rows,
    plan_progress,
    plan_with_remaining,
)
from .sequence import (
    build_sequence_darks,
    build_sequence_flats,
    build_sequence_lights,
    build_sequence_standby,
    build_sequence_teardown,
)
from .system_time import (
    DRIFT_TOLERANCE_SECONDS,
    kill_nina_command,
    nina_exe_path,
    read_windows_clock_powershell,
    restore_set_date_powershell,
    schtasks_create_command,
    schtasks_delete_command,
    schtasks_run_command,
    shift_clock_powershell,
    validate_iso_local,
    write_nina_launcher,
)

NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")
NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"
NINA_PLANNER_LOG = os.environ.get("NINA_PLANNER_LOG")
PROJECT_DIR = Path(__file__).resolve().parent.parent

FrameType = Literal["light", "dark", "bias", "dawn_flat", "dusk_flat"]

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')

_NINA_PROCESS_RE = re.compile(r"NINA\.exe\s+(\d+)")


def _sanitize_filename(s: str) -> str:
    return _INVALID_FILENAME_CHARS.sub("-", s)


def _convert_keys(d: Any) -> Any:
    if isinstance(d, dict):
        return {to_snake(k): _convert_keys(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_convert_keys(v) for v in d]
    return d


def _log_payload(label: str, payload: str) -> None:
    if NINA_PLANNER_LOG:
        path = Path(NINA_PLANNER_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{timestamp} {label} {payload}\n")
    else:
        print(f"{label} {payload}", file=sys.stderr)


async def _api_get(path: str) -> Any:
    async with httpx.AsyncClient(base_url=NINA_API_URL, timeout=10.0) as client:
        resp = await client.get(path)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success", False):
            raise RuntimeError(data.get("Error", "API request failed"))
        _log_payload("_api_get:", json.dumps(data, indent=2))
        return data.get("Response", {})


async def _api_post(path: str, body: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=NINA_API_URL, timeout=10.0) as client:
        resp = await client.post(path, json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success", False):
            raise RuntimeError(data.get("Error", "API request failed"))
        return cast(dict[str, Any], data.get("Response", {}))


async def _spawn_detached(argv: list[str]) -> subprocess.Popen[bytes]:
    """Spawn a subprocess in a thread; return immediately without waiting.

    Used by the simulation tools to launch elevated PowerShell (with a UAC
    prompt on screen) and to launch NINA.exe — both fire-and-forget.
    """

    def _popen() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    return await anyio.to_thread.run_sync(_popen)


async def _run_blocking(argv: list[str], *, timeout: float = 30.0) -> Any:
    """Run a subprocess to completion in a thread; return CompletedProcess."""

    def _run() -> Any:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    return await anyio.to_thread.run_sync(_run)


async def _validate_filters(plan: ObservationPlan, profile: ObservatoryProfile) -> None:
    profile_filter_names = {f.name for f in profile.filters}
    plan_filter_names: set[str] = set()
    for light in plan.light:
        if light.filter_name:
            plan_filter_names.add(light.filter_name)
    for flat in plan.flat:
        if flat.filter_name:
            plan_filter_names.add(flat.filter_name)
    if plan.autofocus.reference_filter_name:
        plan_filter_names.add(plan.autofocus.reference_filter_name)
    unknown = plan_filter_names - profile_filter_names
    if unknown:
        raise ValueError(
            f"Filter(s) not found in profile '{profile.profile_name}': "
            f"{sorted(unknown)}. "
            f"Available filters: {sorted(profile_filter_names)}"
        )


def _validate_position_angle(
    plan: ObservationPlan, profile: ObservatoryProfile
) -> None:
    set_idxs = [
        i
        for i, p in enumerate(plan.pointings, start=1)
        if p.position_angle_deg is not None
    ]
    unset_idxs = [
        i for i, p in enumerate(plan.pointings, start=1) if p.position_angle_deg is None
    ]
    if set_idxs and unset_idxs:
        raise ValueError(
            f"Plan mixes position_angle_deg: pointings {set_idxs} set it but "
            f"pointings {unset_idxs} do not. Either set position_angle_deg on "
            "every pointing or omit it from all."
        )
    if not profile.equipment.has_rotator and set_idxs:
        labeled = [
            f"pointing {i} ({p.label})" if p.label else f"pointing {i}"
            for i, p in enumerate(plan.pointings, start=1)
            if p.position_angle_deg is not None
        ]
        raise ValueError(
            f"Plan sets position_angle_deg on {', '.join(labeled)} but profile "
            f"'{profile.profile_name}' has no rotator. Either add a rotator to the "
            "profile or omit position_angle_deg from the pointings."
        )


async def _convert_to_met(
    data: list[dict[str, Any]], since: int, name: str
) -> list[dict[str, Any]]:
    timestamp = "timestamp"
    now = datetime.fromisoformat(await _api_get("/time"))
    # If /time came back naive, fall back to the host's local timezone so
    # the subsequent `ts - now` arithmetic can't raise on mixed
    # offset-aware / offset-naive pairs (e.g., naive /time paired with
    # Z-suffix event timestamps).
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    tzinfo = now.tzinfo
    res = []
    for d in data:
        value = d[name]
        d = {
            k: v.lower() if isinstance(v, str) else v for k, v in d.items() if k != name
        }
        ts = datetime.fromisoformat(value)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=tzinfo)
        d = _convert_keys(d)
        d[timestamp] = ts
        res.append(d)
    data = sorted(res, key=lambda d: d[timestamp])
    res = []
    for d in data:
        ts = d[timestamp]
        elapsed = (ts - now).total_seconds()
        if since + elapsed > 0:
            d[timestamp] = (
                ts.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            res.append(d)
    return res


async def _resolve_imaging_root() -> Path:
    profile = await get_site_profile()
    return windows_to_local(profile.image_save_path)


def _check_pointing_index(plan: ObservationPlan, pointing_index: int) -> None:
    if pointing_index < 1 or pointing_index > len(plan.pointings):
        raise ValueError(
            f"pointing_index={pointing_index} out of range (plan has "
            f"{len(plan.pointings)} pointings; valid 1..{len(plan.pointings)})"
        )


async def _load_plan(file_path: str) -> ObservationPlan:
    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    async with await anyio.open_file(path, "r", encoding="utf-8") as f:
        data = json.loads(await f.read())
    return ObservationPlan.model_validate(data)


async def _read_metadata(image_type: str | None = None) -> list[dict[str, Any]]:
    root = await _resolve_imaging_root()
    if image_type is not None:
        return read_imaging_csv(root=root, image_type=image_type)
    rows: list[dict[str, Any]] = []
    for t in ("light", "dark", "bias", "flat"):
        rows += read_imaging_csv(root=root, image_type=t)
    return rows


### mcp tools


mcp = FastMCP(
    name="nina-planner",
    instructions="""Provides tools for controlling NINA (Nighttime Imaging 'N' Astronomy) software run observatories. Lets you inspect equipment, get observatory setup, write observation plans, and run NINA sequences generated from the plans. Focuses on orchestrating observation plans at a high level through NINA sequences rather than managing the individual commands that make up those sequences. Safety semantics: the safety monitor's is_safe field reflects whether the observatory enclosure (roof/dome) is open and it is safe to unpark and expose. is_safe=true means the enclosure is open and the scope may be unparked and acquisition may proceed; is_safe=false means the enclosure is closed, so the scope must remain stowed and acquisition is gated until it becomes safe. This is distinct from weather conditions, which are reported separately by the weather device. Stow behavior is capability-driven: sequences emit Park Scope only when the mount reports can_park=true, otherwise they use Find home when the mount reports can_find_home=true. Lifecycle: start_nina(time=None) launches NINA into the active interactive desktop session (pass time='YYYY-MM-DDTHH:MM:SS' to start on simulated time); stop_nina() terminates NINA immediately. If NINA is already running, start_nina will error and require calling stop_nina first. get_nina_status() reports process and REST-API liveness plus the clock in one call (cheaper than get_site_equipment_status and never raises on a down NINA). Progress tracking: maintain an observatory progress file (progress.md in the project directory). On session start, read it to restore context before acting. After significant actions — status checks, plan writes, sequence loads/starts/stops, errors, and interventions — append a short timestamped entry (ISO-8601 timestamp) recording what was done, the observed equipment and safety state, and any decisions. Both the active session and automated worker sessions append to this file so history is shared across sessions.""",
)


@mcp.tool()
async def _get_site_equipment_status(
    profile: ObservatoryProfile,
) -> ObservatoryEquipment:
    from .models.camera import CameraDevice
    from .models.dome import DomeDevice
    from .models.filter_wheel import FilterWheelDevice
    from .models.focuser import FocuserDevice
    from .models.guider import GuiderDevice
    from .models.mount import MountDevice
    from .models.rotator import RotatorDevice
    from .models.safety_monitor import SafetyMonitorDevice
    from .models.switch import Switch
    from .models.weather import Weather

    raw = await _api_get("/equipment/info")

    DEVICE_MAP = {
        "Mount": (MountDevice, "/equipment/mount"),
        "Camera": (CameraDevice, "/equipment/camera"),
        "Focuser": (FocuserDevice, "/equipment/focuser"),
        "Guider": (GuiderDevice, "/equipment/guider"),
        "SafetyMonitor": (SafetyMonitorDevice, "/equipment/safetymonitor"),
        "WeatherData": (Weather, "/equipment/weather"),
        "Dome": (DomeDevice, "/equipment/dome"),
        "FilterWheel": (FilterWheelDevice, "/equipment/filterwheel"),
        "Rotator": (RotatorDevice, "/equipment/rotator"),
        "Switch": (Switch, "/equipment/switch"),
    }

    EXISTS = {
        "Mount": profile.equipment.has_mount,
        "Camera": profile.equipment.has_camera,
        "Focuser": profile.equipment.has_focuser,
        "FilterWheel": profile.equipment.has_filter_wheel,
        "Guider": profile.equipment.has_guider,
        "Rotator": profile.equipment.has_rotator,
        "Dome": profile.equipment.has_dome,
        "WeatherData": profile.equipment.has_weather,
        "SafetyMonitor": profile.equipment.has_safety_monitor,
        "Switch": profile.equipment.has_switch,
    }

    devices_connecting: list[str] = []

    for key, (_, path) in DEVICE_MAP.items():
        if not EXISTS[key]:
            continue
        data = raw.get(key, {})
        if data and not data.get("Connected"):
            await _api_get(path + "/connect")
            devices_connecting.append(key)

    if devices_connecting:
        raise RuntimeError(
            f"Equipment connecting: {', '.join(devices_connecting)}. "
            "Please try again in a few seconds."
        )

    def _build(data: dict, model_cls: type) -> Any:
        if not data:
            return None
        connected = data.get("Connected", data.get("connected", False))
        name = data.get("Name") or data.get("name") or data.get("DisplayName")
        if not connected and not name:
            return None
        converted = _convert_keys(data)
        converted.setdefault("name", "")
        converted.setdefault("description", "")
        return model_cls(**converted)  # type: ignore[return-value]

    return ObservatoryEquipment(
        mount=_build(raw.get("Mount", {}), MountDevice),
        camera=_build(raw.get("Camera", {}), CameraDevice),
        focuser=_build(raw.get("Focuser", {}), FocuserDevice),
        guider=_build(raw.get("Guider", {}), GuiderDevice),
        safety_monitor=_build(raw.get("SafetyMonitor", {}), SafetyMonitorDevice),
        weather=_build(raw.get("WeatherData", {}), Weather),
        dome=_build(raw.get("Dome", {}), DomeDevice),
        filter_wheel=_build(raw.get("FilterWheel", {}), FilterWheelDevice),
        rotator=_build(raw.get("Rotator", {}), RotatorDevice),
        switch=_build(raw.get("Switch", {}), Switch),
    )


@mcp.tool()
async def get_site_equipment_status() -> ObservatoryEquipment:
    """Returns the current connection, operating state, measurements, and capabilities of active observatory equipment, including weather and safety-monitor status (whether the enclosure is open and it is safe to unpark), and mount capabilities (`can_park`, `can_find_home`) that determine how the scope can be stowed. Use it for live operational and safety checks. This tool is read-only and takes no action."""
    profile = await get_site_profile()
    equipment = await _get_site_equipment_status(profile)
    _log_payload("Equipment:", equipment.model_dump_json(indent=2))
    return cast(ObservatoryEquipment, equipment)


@mcp.tool()
async def get_site_profile() -> ObservatoryProfile:
    """Returns the active NINA observatory profile and static configuration, including site location, optics, camera geometry, filters, plate solvers, and image-save path. Use get_site_equipment for live equipment state. This tool is read-only and takes no action."""
    raw = await _api_get("/profile/show?active=true")

    astrometry = raw.get("AstrometrySettings", {})
    telescope = raw.get("TelescopeSettings", {})
    camera = raw.get("CameraSettings", {})
    filter_wheel = raw.get("FilterWheelSettings", {})
    image_file = raw.get("ImageFileSettings", {})
    plate_solve = raw.get("PlateSolveSettings", {})
    framing = raw.get("FramingAssistantSettings", {})
    guider = raw.get("GuiderSettings", {})
    rotator = raw.get("RotatorSettings", {})
    dome = raw.get("DomeSettings", {})
    weather = raw.get("WeatherDataSettings", {})
    safety = raw.get("SafetyMonitorSettings", {})
    focuser = raw.get("FocuserSettings", {})
    switch = raw.get("SwitchSettings", {})

    pixel_size = ascom_float(camera.get("PixelSize")) or 0
    gain = ascom_int(camera.get("Gain")) or 0
    camera_xsize = framing.get("CameraWidth", 0)
    camera_ysize = framing.get("CameraHeight", 0)

    site = SiteLocationInfo(
        latitude_deg=float(astrometry.get("Latitude", 0)),
        longitude_deg=float(astrometry.get("Longitude", 0)),
        elevation_m=float(astrometry.get("Elevation", 0)),
    )

    optics = OpticalTrainInfo(
        telescope_name=telescope.get("Name", ""),
        focal_length_mm=telescope.get("FocalLength"),
        focal_ratio=float(telescope.get("FocalRatio", 0)),
        pixel_size_microns=pixel_size,
        default_gain=gain,
        camera_xsize_px=camera_xsize,
        camera_ysize_px=camera_ysize,
    )

    filters = [
        FilterInfo(
            name=f["Name"],
            position=int(f["Position"]),
            focus_offset=int(f["FocusOffset"]),
        )
        for f in filter_wheel.get("FilterWheelFilters", [])
        if f.get("Name")
    ]

    def _exists(
        section: dict, field: str, skip: AbstractSet[str] = frozenset({"No_Device"})
    ) -> bool:
        value = section.get(field)
        return value is not None and value not in skip and value != ""

    equipment = EquipmentConfig(
        has_mount=_exists(telescope, "MountName") or _exists(telescope, "Name"),
        has_guider=_exists(guider, "GuiderName", {"Direct_Guider", "No_Guider"}),
        has_camera=_exists(camera, "Id"),
        has_focuser=_exists(focuser, "Id"),
        has_filter_wheel=_exists(filter_wheel, "Id"),
        has_rotator=_exists(rotator, "Id"),
        has_dome=_exists(dome, "Id"),
        has_weather=_exists(weather, "Id"),
        has_safety_monitor=_exists(safety, "Id"),
        has_switch=_exists(switch, "Id"),
    )

    profile = ObservatoryProfile(
        profile_name=raw.get("Name", ""),
        profile_id=raw.get("Id", ""),
        description=raw.get("Description") or None,
        site=site,
        optics=optics,
        filters=filters,
        plate_solver=plate_solve.get("PlateSolverType"),
        blind_plate_solver=plate_solve.get("BlindSolverType"),
        image_save_path=image_file.get("FilePath", ""),
        file_pattern=image_file.get("FilePattern"),
        equipment=equipment,
    )
    _log_payload("Profile:", profile.model_dump_json(indent=2))
    return profile


@mcp.tool()
async def get_events(since: int = 300) -> Any:
    """Returns recent timestamped NINA observatory events and their event-specific details from the last `since` seconds. Use it to reconstruct sequence, equipment, safety, imaging, and error activity. This tool is read-only; use get_site_equipment_status and get_sequence_state for current status."""
    res = await _api_get("/event-history")
    res = await _convert_to_met(res, since=since, name="Time")
    _log_payload("Events:", json.dumps(res, indent=2))
    return res


@mcp.tool()
async def get_logs(since: int = 300) -> Any:
    """Returns recent NINA application log entries, including informational messages, warnings, and errors with source and timestamp details from the last `since` seconds. Use it to diagnose sequence failures, equipment communication problems, and unexpected behavior. This tool is read-only and takes no action."""
    res = await _api_get("/application/logs?lineCount=200")
    res = await _convert_to_met(res, since=since, name="Timestamp")
    for d in res:
        if "line" in d:
            del d["line"]
        if "member" in d:
            del d["member"]
        if "source" in d:
            del d["source"]
    _log_payload("Logs:", json.dumps(res, indent=2))
    return res


@mcp.tool()
async def get_imaging_metadata(
    file_path: str,
    pointing_index: int = 1,
    image_type: str = "light",
) -> dict[str, Any]:
    """Returns imaging metadata for a plan and one of its pointings, parsed from the ImageMetaData.csv files in the frame folders (LIGHT, DARK, BIAS, FLAT, etc.) of the mounted N.I.N.A imaging directory. The plan file scopes the result: light frames are attributed to the plan via the `{base_plan_id}-{pointing_index}` plan id embedded in the recorded file path (no target-name fallback); dark/bias/flat frames are matched by image type/filter/exposure and must fall within the plan's `calibration_max_age_days` window of today (UTC). Returns a wide table with one row per exposure, each carrying the frame identity fields (file_path, date, exposure_number, exposure_start, duration, filter_name) plus its metrics as columns, namespaced by group (quality.hfr, guiding.rms, background.adu_mean, pointing.airmass, ...). Timestamps are normalized to seconds-precision UTC ISO-8601 with a `Z` suffix (e.g. 2026-09-22T02:16:25Z). Per-exposure weather samples from `WeatherData.csv` are joined onto the same row (weather.temperature, humidity, cloud_cover, ...) via the UTC exposure start. Dead columns are dropped dynamically: columns constant across the returned frames are listed once under summary.constants, and columns with no real values (ASCOM NaN/-1, empty, 'n/a', and the 0 sentinel NINA writes for unmeasured quality/guiding/CCD metrics) are listed under summary.unpopulated. Defaults to lights frames for star-quality checks; pass another image type (light, dark, bias, flat — case-insensitive)."""
    root = await _resolve_imaging_root()
    plan = await _load_plan(file_path)
    _check_pointing_index(plan, pointing_index)
    rows = read_imaging_csv(root=root, image_type=image_type)
    rows = filter_metadata_rows(
        plan,
        rows,
        image_type,
        pointing_index=pointing_index,
        max_age_days=plan.calibration_max_age_days,
        reference_date=datetime.now(UTC).date(),
    )
    weather = read_weather_csv(root=root)
    res = widen_imaging_metadata(rows, weather_rows=weather)
    res["plan_id"] = plan.effective_plan_id(pointing_index)
    res["pointing_index"] = pointing_index
    res["target"] = plan.target
    _log_payload("Metadata:", json.dumps(res, indent=2))
    return res


# plan tools


@mcp.tool()
async def write_plan_file(plan: ObservationPlan) -> str:
    """Validates and writes an observation plan to a JSON file for later use by load_sequence_from_plan. The plan defines one or more target pointings (RA/Dec/PA, optional label), acquisition intent, light and calibration frames, batching, cooling, autofocus, guiding, and observing constraints. This tool only creates the plan file; it does not load or start a sequence."""
    profile = await get_site_profile()
    await _validate_filters(plan, profile)
    _validate_position_angle(plan, profile)
    if not plan.plan_id:
        plan = plan.model_copy(update={"plan_id": plan._base_plan_id()})
    timestamp = datetime.now(tz=UTC).astimezone().strftime("%Y%m%dT%H%M%S")
    filename = f"{_sanitize_filename(plan.target.lower())}_{_sanitize_filename(plan.intent.lower())}_{timestamp}.json"
    filename = filename.replace(" ", "-")
    path = PROJECT_DIR / filename
    async with await anyio.open_file(path, "w", encoding="utf-8") as f:
        await f.write(plan.model_dump_json(indent=2))
    return f"Plan successfully written to {path}"


@mcp.tool()
async def get_plan_progress(
    file_path: str,
    pointing_index: int = 1,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
) -> dict[str, Any]:
    """Returns per-frame-type acquisition progress for an observation-plan JSON file and one of its pointings: for each exposure group, the total_count from the plan, the acquired_count attributed to this plan/pointing from the imaging metadata (ImageMetaData.csv + AcquisitionDetails.csv), and the remaining_count. Lights are attributed via the `{base_plan_id}-{pointing_index}` plan id embedded in the recorded file path (no target-name fallback); flats/darks/bias are matched by image type, filter, and exposure and must fall within the plan's calibration_max_age_days window of today (UTC). Pass max_hfr and/or min_detected_stars to exclude light frames that fail quality thresholds (frames missing the quality fields are excluded when a threshold is set). Use this before load_sequence_from_plan to decide what still needs acquiring."""
    plan = await _load_plan(file_path)
    _check_pointing_index(plan, pointing_index)
    rows = await _read_metadata()
    res = {
        "plan_id": plan.effective_plan_id(pointing_index),
        "pointing_index": pointing_index,
        "target": plan.target,
        "frame_types": plan_progress(
            plan,
            rows,
            pointing_index=pointing_index,
            max_hfr=max_hfr,
            min_detected_stars=min_detected_stars,
            max_guiding_rms_arcsec=max_guiding_rms_arcsec,
        ),
    }
    _log_payload("Progress:", json.dumps(res, indent=2))
    return res


# sequence tools


@mcp.tool()
async def load_sequence_from_plan(
    file_path: str,
    frame_type: FrameType = "light",
    pointing_index: int = 1,
    mode: str = "remaining",
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
) -> str:
    """Loads an acquisition sequence with safety guardrails from an observation-plan JSON file. Select the frame type: light, dark, bias, dawn_flat, or dusk_flat. `pointing_index` (1-based, default 1) selects which pointing of the plan to load — the lights target name embeds `{base_plan_id}-{pointing_index}` so each pointing's frames are attributed independently. In the default `remaining` mode the sequence only acquires frames still needed (total minus frames already attributed to this plan/pointing in the imaging metadata); if nothing remains it reports the plan as complete and loads nothing. max_hfr and/or min_detected_stars exclude light frames failing quality thresholds from the acquired count. Pass `mode="full"` to acquire the entire plan again. This tool only loads the sequence; call start_sequence afterward."""
    plan = await _load_plan(file_path)
    profile = await get_site_profile()
    await _validate_filters(plan, profile)
    _validate_position_angle(plan, profile)
    _check_pointing_index(plan, pointing_index)
    equipment = await _get_site_equipment_status(profile)

    group_key = {
        "light": "light",
        "dark": "dark",
        "bias": "bias",
        "dawn_flat": "flat",
        "dusk_flat": "flat",
    }[frame_type]

    if mode == "remaining":
        try:
            rows = await _read_metadata(group_key)
        except RuntimeError as e:
            raise ValueError(
                f"Cannot compute remaining frames: {e}. Pass mode='full' to "
                "load the whole plan regardless."
            ) from e
        progress = plan_progress(
            plan,
            rows,
            pointing_index=pointing_index,
            max_hfr=max_hfr,
            min_detected_stars=min_detected_stars,
            max_guiding_rms_arcsec=max_guiding_rms_arcsec,
        )
        items = progress[group_key]
        if all(item["remaining_count"] == 0 for item in items):
            total = sum(item["total_count"] for item in items)
            return (
                f"`{frame_type}` pointing {pointing_index} is complete — "
                f"{total} of {total} frames already acquired; nothing to load."
            )
        plan = plan_with_remaining(plan, progress)
    elif mode != "full":
        raise ValueError(f"Unsupported mode: {mode} (use 'remaining' or 'full')")

    if frame_type == "light":
        seq = build_sequence_lights(plan, equipment, pointing_index=pointing_index)
    elif frame_type == "dark":
        seq = build_sequence_darks(plan, equipment)
    elif frame_type == "bias":
        seq = build_sequence_darks(plan, equipment, bias=True)
    elif frame_type == "dawn_flat":
        seq = build_sequence_flats(plan, equipment, profile=profile)
    elif frame_type == "dusk_flat":
        seq = build_sequence_flats(plan, equipment, profile=profile, dusk=True)
    else:
        raise ValueError(f"Unsupported frame type: {frame_type}")
    await _api_post("/sequence/load", seq)
    return f"`{frame_type}` sequence loaded."


@mcp.tool()
async def stow_telescope() -> str:
    """Loads and starts the non-acquisition teardown sequence, safely stowing the telescope (park or home, per mount capability) while NINA's sequence-level safety guardrails remain active. Use for end-of-observation close-down — when you are done observing and want to shut down the scope — or before leaving the observatory unattended. Allow it to complete without interruption. This is separate from stop_sequence, which halts the current sequence but does not stow the scope."""
    equipment = await get_site_equipment_status()
    seq = build_sequence_teardown(equipment)
    await _api_post("/sequence/load", seq)
    await _api_get("/sequence/start?skipValidation=true")
    return "Teardown sequence started."


@mcp.tool()
async def enter_safety_standby() -> str:
    """Loads a non-acquisition standby sequence that keeps NINA sequence-level safety and stow guardrails active while the observatory is idle. After loading, call start_sequence. Stop it before loading an acquisition or teardown sequence. Use whenever equipment is deployed and no other sequence is running."""
    equipment = await get_site_equipment_status()
    seq = build_sequence_standby(equipment)
    await _api_post("/sequence/load", seq)
    await _api_get("/sequence/start?skipValidation=true")
    return "Safety standby sequence started."


@mcp.tool()
async def start_sequence() -> str:
    """Starts or resumes the currently loaded sequence, activating its acquisition or safety workflow. Call get_sequence_state afterward to verify that it is running."""
    await _api_get("/sequence/start?skipValidation=true")
    return "Sequence started."


@mcp.tool()
async def stop_sequence() -> str:
    """Stops the currently running NINA sequence immediately, leaving the telescope where it currently is — it does not stow the scope. Use for an urgent halt, to interrupt a stuck/looping sequence, or when the running sequence isn't what you wanted. If you then want to park/home the telescope, run stow_telescope separately. Note that load_sequence_from_plan and the teardown/standby entry tools already stop any running sequence before loading, so an explicit stop is only needed when you want to halt without loading anything new. Avoid stopping an in-progress teardown during the stow maneuver unless safety requires it, since interrupting mid-slew can leave the scope in an unsafe position. Call get_sequence_state afterward to confirm the sequence has stopped."""
    await _api_get("/sequence/stop")
    return "Sequence stopped."


@mcp.tool()
async def get_sequence_state() -> Any:
    """Returns the loaded sequence structure and the current status of its containers, instructions, conditions, and triggers. Use it to determine whether a sequence is loaded, running, completed, failed, or waiting. This tool is read-only and takes no action."""
    return await _api_get("/sequence/json")


async def _launch_nina_interactive(nina_exe: str) -> None:
    """Launch NINA in the active interactive desktop session via Task Scheduler."""
    _, win_path = write_nina_launcher(nina_exe)
    create_cmd = schtasks_create_command(win_path)
    run_cmd = schtasks_run_command()
    del_cmd = schtasks_delete_command()
    _log_payload("_launch_nina_interactive: create", " ".join(create_cmd))
    create_result = await _run_blocking(create_cmd, timeout=15.0)
    _raise_for_command_failure(
        create_result, create_cmd, "Task Scheduler task creation"
    )
    _log_payload("_launch_nina_interactive: run", " ".join(run_cmd))
    try:
        run_result = await _run_blocking(run_cmd, timeout=15.0)
        _raise_for_command_failure(run_result, run_cmd, "Task Scheduler task execution")
    finally:
        # Remove the temporary task even when starting it fails.
        try:
            await _run_blocking(del_cmd, timeout=10.0)
        except Exception as e:
            _log_payload("_launch_nina_interactive: delete task warning", repr(e))


def _raise_for_command_failure(result: Any, argv: list[str], action: str) -> None:
    """Raise with command output when a completed subprocess exits nonzero."""
    returncode = result.returncode
    if returncode == 0:
        return

    output = "\n".join(
        part.strip()
        for part in (result.stdout or "", result.stderr or "")
        if part.strip()
    )
    detail = f": {output}" if output else ""
    raise RuntimeError(
        f"{action} command {' '.join(argv)!r} failed with exit code "
        f"{returncode}{detail}"
    )


async def _read_windows_clock() -> datetime:
    """Read the current Windows host clock via Get-Date. Returns aware local datetime."""
    proc = await _run_blocking(
        ["powershell.exe", "-NoProfile", "-Command", read_windows_clock_powershell()],
        timeout=10.0,
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("Get-Date returned no output")
    return datetime.fromisoformat(raw)


async def _poll_until_async(check, timeout: float, interval: float) -> bool:
    """Poll ``check`` (async callable returning bool) until it returns True or timeout."""
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if await check():
            return True
        await anyio.sleep(interval)
    return False


def _now_local() -> datetime:
    """Current local time as a timezone-aware datetime."""
    return datetime.now().astimezone()


@mcp.tool()
async def stop_nina() -> dict[str, Any]:
    """Terminate the NINA.exe application immediately.

    Checks whether NINA.exe is currently running. If running, force-terminates
    it via taskkill.exe and logs the action to progress.md. If NINA is not running,
    returns cleanly as a no-op.
    """
    log_extra: list[str] = []
    try:
        probe = await _run_blocking(["tasklist.exe", "/FI", "IMAGENAME eq NINA.exe"])
        is_running = "NINA.exe" in (probe.stdout or "")
    except Exception as e:
        log_extra.append(f"nina probe failed: {e!r}")
        is_running = False

    if not is_running:
        summary = "stop_nina: NINA is not running — no action taken."
        _log_payload("stop_nina:", summary)
        return {"stopped": False, "summary": summary}

    try:
        await _run_blocking(kill_nina_command(), timeout=15.0)
        await anyio.sleep(1.0)
        summary = "stop_nina: NINA.exe terminated successfully."
        log_extra.append("killed running NINA")
    except Exception as e:
        summary = f"stop_nina failed: taskkill error: {e!r}"
        log_extra.append(summary)
        _log_payload("stop_nina error:", summary)
        raise RuntimeError(summary) from e

    _log_payload("stop_nina:", summary)
    try:
        await append_progress_entry("stop_nina: " + "; ".join(log_extra))
    except Exception as e:
        _log_payload("stop_nina: progress entry failed", repr(e))

    return {"stopped": True, "summary": summary}


@mcp.tool()
async def start_nina(time: str | None = None) -> dict[str, Any]:
    """Launches NINA into the interactive desktop session.

    If NINA is already running, raises an error instructing the caller to stop it first.

    Args:
        time: Optional naive ISO 8601 local datetime (e.g. '2026-10-15T23:15:00').
            If omitted or None, NINA launches on current real time.
            If provided, briefly shifts the Windows OS clock to that datetime,
            launches NINA so it captures the simulated time during initialization,
            then restores the OS clock back to real time.
    """
    if time is not None and not time.strip():
        raise ValueError(
            "`time` cannot be empty. Pass None for current time, or an ISO 8601 string."
        )
    if time is not None:
        validate_iso_local(time)

    try:
        probe = await _run_blocking(["tasklist.exe", "/FI", "IMAGENAME eq NINA.exe"])
        if probe.returncode != 0:
            raise RuntimeError(
                f"tasklist exited with status {probe.returncode}: "
                f"{(probe.stderr or '').strip()}"
            )
        is_running = "NINA.exe" in (probe.stdout or "")
    except Exception as e:
        _log_payload("start_nina probe warning:", repr(e))
        raise RuntimeError(
            "start_nina failed: could not determine whether NINA.exe is running; "
            "refusing to launch NINA or change the host clock."
        ) from e

    if is_running:
        raise RuntimeError("NINA is already running. Call stop_nina() first.")

    nina_exe = nina_exe_path()
    log_extra: list[str] = []

    if time is None:
        log_extra.append("launching NINA on real time")
        await _launch_nina_interactive(nina_exe)
        log_extra.append(f"launched NINA from {nina_exe}")
        summary = (
            "start_nina: launched NINA on real time into interactive desktop session. "
            "Poll get_site_equipment_status() to confirm readiness."
        )
        _log_payload("start_nina:", summary)
        try:
            await append_progress_entry("start_nina: " + "; ".join(log_extra))
        except Exception as e:
            _log_payload("start_nina: progress entry failed", repr(e))
        return {"summary": summary, "simulated": False}

    # Simulated time path
    log_extra.append(f"simulating observation time {time}")
    parsed_target = validate_iso_local(time)
    local_tz = _now_local().tzinfo
    target_aware = parsed_target.replace(tzinfo=local_tz)

    # 1. Capture real0 from Windows host clock.
    try:
        real0 = await _read_windows_clock()
    except Exception as e:
        raise RuntimeError(
            f"start_nina failed: could not read host clock before shift: {e!r}"
        ) from e
    shift_at = datetime.now(UTC)

    # 2. Shift Windows host clock to simulated target (direct, no UAC).
    shift_cmd = shift_clock_powershell(time)
    _log_payload("start_nina: shift", shift_cmd)
    try:
        await _run_blocking(
            ["powershell.exe", "-NoProfile", "-Command", shift_cmd], timeout=10.0
        )
    except Exception as e:
        raise RuntimeError(
            f"start_nina failed: shift to {time!r} failed "
            "(host may lack 'Change the system time' privilege): "
            f"{e!r}"
        ) from e

    # 3. Verify shift by polling the Windows host clock.
    async def _shift_check() -> bool:
        try:
            host_now = await _read_windows_clock()
            return abs((host_now - target_aware).total_seconds()) <= 2.0
        except Exception:
            return False

    shift_ok = await _poll_until_async(_shift_check, timeout=10.0, interval=0.5)
    if not shift_ok:
        # Best-effort restore to real0 before raising.
        try:
            restore_cmd = restore_set_date_powershell(
                real0.astimezone(local_tz).replace(tzinfo=None).isoformat()
            )
            await _run_blocking(
                ["powershell.exe", "-NoProfile", "-Command", restore_cmd], timeout=10.0
            )
        except Exception:
            pass
        raise RuntimeError(
            f"start_nina failed: host clock did not converge to {time!r} within 10s of shift. "
            "Host clock restored."
        )
    log_extra.append(f"shifted OS clock to {time}")

    captured = False
    launch_error: Exception | None = None
    restored = False
    restore_error: str | None = None

    try:
        # 4. Launch NINA in interactive session.
        await _launch_nina_interactive(nina_exe)
        log_extra.append(f"launched NINA from {nina_exe}")

        # 5. Capture-check: poll NINA /time until it reports the simulated date.
        async def _capture_check() -> bool:
            try:
                info = await get_nina_time()
                nina_naive = datetime.fromisoformat(info["nina_time"])
                nina_aware = (
                    nina_naive.replace(tzinfo=local_tz)
                    if nina_naive.tzinfo is None
                    else nina_naive.astimezone(local_tz)
                )
                # Tolerate up to 30s of drift: NINA captures the host clock at
                # init and then advances on its own, so readings drift forward
                # over time.
                return abs((nina_aware - target_aware).total_seconds()) <= 30.0
            except Exception:
                return False

        captured = await _poll_until_async(_capture_check, timeout=45.0, interval=1.0)
    except Exception as e:
        # Preserve the launch/capture failure until after the host clock has
        # been restored and checked below.
        launch_error = e
    finally:
        # 6. Always restore the host clock after a successful shift, including
        # when launcher setup or NINA startup raises.
        restore_at = datetime.now(UTC)
        elapsed = (restore_at - shift_at).total_seconds()
        restore_target_dt = real0 + timedelta(seconds=elapsed)
        restore_target_naive = restore_target_dt.astimezone(local_tz).replace(
            tzinfo=None
        )
        restore_cmd = restore_set_date_powershell(restore_target_naive.isoformat())
        _log_payload("start_nina: restore", restore_cmd)
        try:
            await _run_blocking(
                ["powershell.exe", "-NoProfile", "-Command", restore_cmd],
                timeout=10.0,
            )
        except Exception as e:
            restore_error = repr(e)

        # 7. Verify restore even if issuing Set-Date raised.
        async def _restore_check() -> bool:
            try:
                host_now = await _read_windows_clock()
                return abs((host_now - restore_target_dt).total_seconds()) <= 2.0
            except Exception:
                return False

        restored = await _poll_until_async(_restore_check, timeout=10.0, interval=0.5)
        log_extra.append(
            f"restored host clock to {restore_target_dt.isoformat()} "
            f"(verified={restored})"
        )

    if not restored:
        launch_detail = (
            f"; launch/capture error={launch_error!r}" if launch_error else ""
        )
        raise RuntimeError(
            f"start_nina failed: host clock did not restore to "
            f"{restore_target_dt.isoformat()} (restore_error={restore_error!r})"
            f"{launch_detail}. Manual intervention may be required."
        ) from launch_error
    if launch_error is not None:
        raise RuntimeError(
            f"start_nina failed during launch/capture: {launch_error!r}. "
            "Host clock was restored."
        ) from launch_error
    if not captured:
        raise RuntimeError(
            f"start_nina failed: NINA never captured simulated date {time!r} within 45s. "
            "Host clock has been restored. Verify NINA launched with get_site_equipment_status()."
        )

    # 8. Return correct simulated/delta now that host is real.
    nina_time_info = await get_nina_time()

    summary = "start_nina: " + "; ".join(log_extra)
    _log_payload("start_nina:", summary)
    try:
        await append_progress_entry("start_nina: " + "; ".join(log_extra))
    except Exception as e:
        _log_payload("start_nina: progress entry failed", repr(e))

    return {
        "summary": summary,
        "nina_time": nina_time_info["nina_time"],
        "host_time": nina_time_info["host_time"],
        "delta_seconds": nina_time_info["delta_seconds"],
        "simulated": nina_time_info["simulated"],
    }


async def get_nina_time() -> dict[str, Any]:
    """Returns NINA's reported time alongside the host OS clock, the signed
    delta between them (NINA − host), and a ``simulated`` flag (``True`` when
    |delta| exceeds the configured drift tolerance).

    Use this to detect whether NINA is running on simulated time (``simulated=True``)
    or real time (``simulated=False`` and ``delta_seconds`` ≈ 0).

    Timestamps are returned as naive ISO 8601 local datetimes (no timezone
    suffix); ``delta_seconds`` is computed in UTC after attaching the host's
    local timezone to whichever side is naive.
    """
    timestamp = await _api_get("/time")
    nina_dt = datetime.fromisoformat(timestamp)
    host_dt = datetime.now().astimezone()
    if nina_dt.tzinfo is None:
        nina_utc = nina_dt.replace(tzinfo=host_dt.tzinfo).astimezone(UTC)
    else:
        nina_utc = nina_dt.astimezone(UTC)
    host_utc = host_dt.astimezone(UTC)
    delta_seconds = (nina_utc - host_utc).total_seconds()
    return {
        "nina_time": nina_dt.replace(microsecond=0).isoformat(),
        "host_time": host_dt.replace(microsecond=0).isoformat(),
        "delta_seconds": delta_seconds,
        "simulated": abs(delta_seconds) > DRIFT_TOLERANCE_SECONDS,
    }


@mcp.tool()
async def get_nina_status() -> dict[str, Any]:
    """Lightweight NINA lifecycle and clock probe.

    Reports whether ``NINA.exe`` is running (via ``tasklist.exe``), whether the
    REST API is responsive, the NINA process PID (when running), and the
    simulated-time clock state. Cheaper than ``get_site_equipment_status``
    (no device probes) and never raises on a down NINA — partial failures
    degrade to ``None`` fields. Use this before ``start_nina()`` to confirm
    a previous NINA instance is actually down, or as a quick "is NINA
    alive?" check.

    Returns a dict with:
    - ``process_running`` (bool): ``NINA.exe`` visible to ``tasklist.exe``
    - ``api_responsive`` (bool): ``/time`` endpoint returned successfully
    - ``pid`` (int | None): NINA.exe PID when running
    - ``nina_time``, ``host_time``, ``delta_seconds``, ``simulated``: same
      fields as ``get_nina_time()``, populated only when ``api_responsive``
      is ``True``; otherwise all ``None``
    - ``summary`` (str): human-readable one-line status
    """
    process_running = False
    pid: int | None = None
    try:
        proc = await _run_blocking(
            ["tasklist.exe", "/FI", "IMAGENAME eq NINA.exe"],
            timeout=5.0,
        )
        stdout = proc.stdout or ""
        if "NINA.exe" in stdout:
            process_running = True
            match = _NINA_PROCESS_RE.search(stdout)
            if match:
                pid = int(match.group(1))
    except Exception as e:
        _log_payload("get_nina_status: proc probe failed", repr(e))

    api_responsive = False
    nina_time: str | None = None
    host_time: str | None = None
    delta_seconds: float | None = None
    simulated: bool | None = None
    try:
        clock = await get_nina_time()
        api_responsive = True
        nina_time = clock["nina_time"]
        host_time = clock["host_time"]
        delta_seconds = clock["delta_seconds"]
        simulated = clock["simulated"]
    except Exception as e:
        _log_payload("get_nina_status: api probe failed", repr(e))

    parts: list[str] = []
    if process_running:
        parts.append(f"NINA.exe running (pid {pid})" if pid else "NINA.exe running")
    else:
        parts.append("NINA.exe not running")
    parts.append("REST API responsive" if api_responsive else "REST API unresponsive")
    if simulated is True:
        parts.append("on simulated time")
    summary = "; ".join(parts)

    return {
        "process_running": process_running,
        "api_responsive": api_responsive,
        "pid": pid,
        "nina_time": nina_time,
        "host_time": host_time,
        "delta_seconds": delta_seconds,
        "simulated": simulated,
        "summary": summary,
    }


if __name__ == "__main__":
    mcp.run()
