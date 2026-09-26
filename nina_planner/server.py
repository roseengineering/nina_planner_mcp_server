import json
import os
import re
import subprocess
import sys
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
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
    launch_nina_command,
    nina_exe_path,
    restore_clock_powershell,
    shift_clock_powershell,
    time_simulator_enabled,
    validate_iso_local,
)

NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")
NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"
NINA_PLANNER_LOG = os.environ.get("NINA_PLANNER_LOG")
PROJECT_DIR = Path(__file__).resolve().parent.parent

FrameType = Literal["light", "dark", "bias", "dawn_flat", "dusk_flat"]

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


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
            f"Filter(s) not found in profile '{profile.profile_name}': {sorted(unknown)}. "
            f"Available filters: {sorted(profile_filter_names)}"
        )


def _validate_position_angle(plan: ObservationPlan, profile: ObservatoryProfile) -> None:
    set_idxs = [
        i for i, p in enumerate(plan.pointings, start=1) if p.position_angle_deg is not None
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


def _convert_to_met(
    data: list[dict[str, Any]], since: int, now: datetime, name: str
) -> list[dict[str, Any]]:
    timestamp = "timestamp"
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
        tzinfo = ts.tzinfo
        d = _convert_keys(d)
        d[timestamp] = ts
        res.append(d)
    data = sorted(res, key=lambda d: d[timestamp])
    res = []
    for d in data:
        ts = d[timestamp]
        elapsed = (ts - now).total_seconds()
        if since + elapsed > 0:
            d[timestamp] = ts.astimezone(UTC).replace(microsecond=0).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
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
    instructions="""Provides tools for controlling NINA (Nighttime Imaging 'N' Astronomy) software run observatories. Lets you inspect equipment, get observatory setup, write observation plans, and run NINA sequences generated from the plans. Focuses on orchestrating observation plans at a high level through NINA sequences rather than managing the individual commands that make up those sequences. Safety semantics: the safety monitor's is_safe field reflects whether the observatory enclosure (roof/dome) is open and it is safe to unpark and expose. is_safe=true means the enclosure is open and the scope may be unparked and acquisition may proceed; is_safe=false means the enclosure is closed, so the scope must remain stowed and acquisition is gated until it becomes safe. This is distinct from weather conditions, which are reported separately by the weather device. Stow behavior is capability-driven: sequences emit Park Scope only when the mount reports can_park=true, otherwise they use Find home when the mount reports can_find_home=true. Crash recovery: ensure_nina_running() probes tasklist for NINA.exe and launches NINA from NINA_EXE_PATH (or the standard install path) when the GUI is not running. Always available; idempotent; fire-and-forget. Call it first when NINA-facing tools fail. Time simulation: simulate_observation_time(when) briefly shifts the Windows host clock so NINA (running on Windows) captures the simulated local time during its own startup, then restores the real OS clock. After the call, NINA continues to run on simulated local time while the OS clock reports real time — NINA's API timestamps will diverge from real time. Use get_events or compare NINA's /time to the real OS clock to detect this; do not interpret divergence as a hardware fault. The tool is opt-in via NINA_TIME_SIMULATOR_ENABLED=1 in the MCP environment. Progress tracking: maintain an observatory progress file (progress.md in the project directory). On session start, read it to restore context before acting. After significant actions — status checks, plan writes, sequence loads/starts/stops, errors, and interventions — append a short timestamped entry (ISO-8601 timestamp) recording what was done, the observed equipment and safety state, and any decisions. Both the active session and automated worker sessions append to this file so history is shared across sessions.""",
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
        focal_length_mm=float(telescope.get("FocalLength", 0)),
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
    timestamp = await _api_get("/time")
    now = datetime.fromisoformat(timestamp)
    res = await _api_get("/event-history")
    res = _convert_to_met(res, since=since, now=now, name="Time")
    _log_payload("Events:", json.dumps(res, indent=2))
    return res


@mcp.tool()
async def get_logs(since: int = 300) -> Any:
    """Returns recent NINA application log entries, including informational messages, warnings, and errors with source and timestamp details from the last `since` seconds. Use it to diagnose sequence failures, equipment communication problems, and unexpected behavior. This tool is read-only and takes no action."""
    timestamp = await _api_get("/time")
    now = datetime.fromisoformat(timestamp)
    res = await _api_get("/application/logs?lineCount=200")
    res = _convert_to_met(res, since=since, now=now, name="Timestamp")
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


@mcp.tool()
async def ensure_nina_running() -> str:
    """If ``NINA.exe`` is not running on the Windows host, launch it from
    ``NINA_EXE_PATH`` (or the standard install path if unset). Otherwise,
    this is a no-op.

    NINA occasionally crashes; without this tool, every other NINA-facing
    tool (``get_site_equipment_status``, ``load_sequence_from_plan``, etc.)
    fails because the REST API on ``localhost:1888`` is unreachable. Call
    this first when NINA seems unresponsive.

    The launch is fire-and-forget — the MCP call returns as soon as
    ``cmd.exe /c start "" "<nina_exe>"`` has been spawned. NINA's REST API
    typically takes 5–15 s to come up; callers should poll
    ``get_site_equipment_status()`` to confirm readiness before issuing
    further commands.

    Always available (no env-var gate) — the operation is idempotent
    (no-op when NINA is up) and does not touch the host clock.

    Returns a short status string identifying which branch was taken.
    """
    log_extra: list[str] = []
    try:
        probe = await _run_blocking(
            ["tasklist.exe", "/FI", "IMAGENAME eq NINA.exe"]
        )
        was_running = "NINA.exe" in (probe.stdout or "")
    except Exception as e:
        log_extra.append(f"nina probe failed: {e!r}")
        was_running = False

    if was_running:
        summary = (
            "ensure_nina_running: NINA.exe already running — no action."
        )
        log_extra.append("NINA already running, no launch")
    else:
        nina_exe = nina_exe_path()
        launch = launch_nina_command(nina_exe)
        _log_payload("ensure_nina_running: launch", " ".join(launch))
        await _spawn_detached(launch)
        log_extra.append(f"launched NINA from {nina_exe}")
        summary = (
            "ensure_nina_running: NINA.exe was not running; "
            f"launched from {nina_exe}. Poll get_site_equipment_status() "
            "to confirm readiness."
        )

    _log_payload("ensure_nina_running:", summary)
    try:
        await append_progress_entry(
            "ensure_nina_running: " + "; ".join(log_extra)
        )
    except Exception as e:
        _log_payload("ensure_nina_running: progress entry failed", repr(e))
    return summary



@mcp.tool()
async def simulate_observation_time(when: str = "", reset: bool = False) -> str:
    """Manages NINA's clock relative to the host OS clock.

    Two modes, selected by ``reset``:

    - ``reset=False`` (default): **simulate**. Briefly shifts the Windows host
      clock to ``when``, relaunches NINA so it captures the simulated time
      during its own init, then restores the OS clock to real time. NINA
      continues running on simulated local time afterwards — verify with
      :func:`get_nina_time` (``simulated=True`` while in effect).

    - ``reset=True``: **reset back to real time**. Unconditionally kills
      NINA.exe, runs ``w32tm /resync`` to ensure the OS clock is on real
      network time, relaunches NINA so it captures the now-real clock during
      its own init, and polls :func:`get_nina_time` until NINA's clock
      converges with the host (within ``DRIFT_TOLERANCE_SECONDS``). Use this
      after a simulate run to return the observatory to real time.

    In both modes the OS clock is on real time at the end; in simulate mode
    NINA is on simulated local time, in reset mode NINA is on real local
    time.

    The simulate path is logged as separate ``simulate_observation_time:``
    sub-payloads (``probe``, ``shift``, ``launch``, ``restore``) in
    ``NINA_PLANNER_LOG``. The only visible UI effects are two UAC prompts
    on the Windows host per simulate invocation (one for ``Set-Date``, one
    for ``w32tm /resync``); the reset path produces one UAC prompt (for
    ``w32tm /resync``).

    Args:
        when: Required when ``reset=False``. Naive ISO 8601 local datetime
            (e.g. ``"2026-10-15T23:15:00"`` or ``"2026-10-15 23:15:00"``).
            Must not carry a timezone suffix (``Z`` or ``+HH:MM``) — those
            would silently land wrong because ``Set-Date`` operates on
            local time. Must not contain a single quote. Must be empty when
            ``reset=True``.
        reset: When ``True``, run the reset-to-real-time path instead of
            simulate. ``when`` must be empty in this mode.

    Raises:
        RuntimeError: if the ``NINA_TIME_SIMULATOR_ENABLED`` env var is not
            set in the MCP environment. Set it to ``1`` in ``opencode.json``'s
            MCP ``environment`` block to enable.
        ValueError: if ``when`` fails ISO 8601 local datetime validation, or
            if ``when`` is empty when ``reset=False`` / non-empty when
            ``reset=True``.
    """
    if not time_simulator_enabled():
        raise RuntimeError(
            "Time-simulation tools are disabled. Set "
            "NINA_TIME_SIMULATOR_ENABLED=1 in the MCP environment block of "
            "opencode.json to enable them."
        )

    log_extra: list[str] = []
    nina_exe = nina_exe_path()

    if reset:
        if when:
            raise ValueError(
                "`when` must be empty when `reset=True` "
                "(reset uses no simulated time)."
            )
        log_extra.append("reset mode: returning NINA to real local time")

        try:
            probe = await _run_blocking(
                ["tasklist.exe", "/FI", "IMAGENAME eq NINA.exe"]
            )
            was_running = "NINA.exe" in (probe.stdout or "")
        except Exception as e:
            log_extra.append(f"nina probe failed: {e!r}")
            was_running = False

        try:
            await _run_blocking(kill_nina_command(), timeout=15.0)
            await anyio.sleep(2.0)
            if was_running:
                log_extra.append("killed running NINA")
            else:
                log_extra.append("NINA was not running (taskkill had no target)")
        except Exception as e:
            log_extra.append(f"taskkill failed: {e!r}")

        restore_ps = restore_clock_powershell()
        _log_payload("simulate_observation_time: restore", restore_ps)
        await _spawn_detached(["sudo", "powershell.exe", "-Command", restore_ps])
        log_extra.append("ran w32tm /resync to ensure OS clock is real")

        launch = launch_nina_command(nina_exe)
        _log_payload("simulate_observation_time: launch", " ".join(launch))
        await _spawn_detached(launch)
        log_extra.append(f"relaunched NINA from {nina_exe} on real OS clock")

        converged, delta = await _wait_for_real_time()
        if converged:
            log_extra.append(
                f"NINA clock converged with host within "
                f"{DRIFT_TOLERANCE_SECONDS:g}s (delta={delta:+.2f}s)"
            )
        else:
            log_extra.append(
                f"NINA clock still diverges from host by {delta:+.2f}s "
                f"after convergence wait — investigate"
            )

        summary = "simulate_observation_time(reset=True): " + "; ".join(log_extra)
        _log_payload("simulate_observation_time:", summary)
        try:
            await append_progress_entry(
                "simulate_observation_time(reset=True): killed NINA, ran "
                "w32tm /resync, relaunched NINA on real OS clock; "
                f"converged={converged}, delta={delta:+.2f}s."
            )
        except Exception as e:
            _log_payload(
                "simulate_observation_time: progress entry failed", repr(e)
            )
        return summary

    if not when or not when.strip():
        raise ValueError(
            "`when` must be a non-empty ISO 8601 local datetime when "
            "`reset=False` (e.g. '2026-10-15T23:15:00')."
        )
    validate_iso_local(when)

    was_running = False
    try:
        probe = await _run_blocking(
            ["tasklist.exe", "/FI", "IMAGENAME eq NINA.exe"]
        )
        was_running = "NINA.exe" in (probe.stdout or "")
    except Exception as e:
        log_extra.append(f"nina probe failed: {e!r}")

    if was_running:
        try:
            await _run_blocking(kill_nina_command(), timeout=15.0)
            await anyio.sleep(2.0)
            log_extra.append("killed running NINA")
        except Exception as e:
            log_extra.append(f"taskkill failed: {e!r}")
    else:
        log_extra.append("NINA was not running (proceeding without kill)")

    shift_ps = shift_clock_powershell(when)
    _log_payload("simulate_observation_time: shift", shift_ps)
    await _spawn_detached(["sudo", "powershell.exe", "-Command", shift_ps])
    await anyio.sleep(3.0)
    log_extra.append(f"shifted OS clock to {when}")

    launch = launch_nina_command(nina_exe)
    _log_payload("simulate_observation_time: launch", " ".join(launch))
    await _spawn_detached(launch)
    log_extra.append(f"relaunched NINA from {nina_exe}")

    await anyio.sleep(10.0)

    restore_ps = restore_clock_powershell()
    _log_payload("simulate_observation_time: restore", restore_ps)
    await _spawn_detached(["sudo", "powershell.exe", "-Command", restore_ps])
    log_extra.append("restored real-time OS clock via w32tm /resync")

    summary = "simulate_observation_time: " + "; ".join(log_extra)
    _log_payload("simulate_observation_time:", summary)
    try:
        await append_progress_entry(
            f"simulate_observation_time: shifted OS clock to {when!r} "
            f"briefly to relaunch NINA on simulated local time; OS clock "
            f"restored to real time. NINA continues running on simulated "
            f"local time."
        )
    except Exception as e:
        _log_payload("simulate_observation_time: progress entry failed", repr(e))
    return summary


async def _wait_for_real_time(
    tolerance_seconds: float = DRIFT_TOLERANCE_SECONDS,
    max_wait_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
) -> tuple[bool, float]:
    """Poll :func:`get_nina_time` until NINA's clock is within tolerance.

    Returns ``(converged, delta_seconds)`` where ``delta_seconds`` is the
    final observed delta (NINA − host, signed). Polling tolerates transient
    API failures and times out after ``max_wait_seconds``, returning the
    last observed delta (or ``NaN`` if no sample ever succeeded).
    """
    deadline = anyio.current_time() + max_wait_seconds
    last_delta = float("nan")
    while anyio.current_time() < deadline:
        try:
            result = await get_nina_time()
            last_delta = float(result["delta_seconds"])
            if abs(last_delta) <= tolerance_seconds:
                return True, last_delta
        except Exception:
            pass
        await anyio.sleep(poll_interval_seconds)
    try:
        result = await get_nina_time()
        last_delta = float(result["delta_seconds"])
    except Exception:
        pass
    return abs(last_delta) <= tolerance_seconds, last_delta


@mcp.tool()
async def get_nina_time() -> dict[str, Any]:
    """Returns NINA's reported time alongside the host OS clock, the signed
    delta between them (NINA − host), and a ``simulated`` flag (``True`` when
    |delta| exceeds the configured drift tolerance).

    Use this to detect whether a previous ``simulate_observation_time`` call
    is still in effect (``simulated=True``) or to verify that
    ``simulate_observation_time(reset=True)`` returned NINA to real time
    (``simulated=False`` and ``delta_seconds`` ≈ 0).

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



if __name__ == "__main__":
    mcp.run()
