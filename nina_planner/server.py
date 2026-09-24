import json
import os
import re
import sys
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import anyio
import httpx
from mcp.server.fastmcp import FastMCP

from .imaging import read_imaging_csv, windows_to_local
from .models.observatory import ObservatoryEquipment
from .models.plan import ObservationPlan
from .models.profile import (
    EquipmentConfig,
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    SiteLocationInfo,
)
from .nina_utils import ascom_float, ascom_int
from .progress import plan_progress, plan_with_remaining
from .sequence import (
    build_sequence_darks,
    build_sequence_flats,
    build_sequence_lights,
    build_sequence_standby,
    build_sequence_teardown,
)

NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "localhost:1888")
NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"
PROJECT_DIR = Path(__file__).resolve().parent.parent

FrameType = Literal["light", "dark", "bias", "dawn_flat", "dusk_flat"]

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def _sanitize_filename(s: str) -> str:
    return _INVALID_FILENAME_CHARS.sub("-", s)


def _to_snake(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def _convert_keys(d: Any) -> Any:
    if isinstance(d, dict):
        return {_to_snake(k): _convert_keys(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_convert_keys(v) for v in d]
    return d


async def _api_get(path: str) -> Any:
    async with httpx.AsyncClient(base_url=NINA_API_URL, timeout=10.0) as client:
        resp = await client.get(path)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success", False):
            raise RuntimeError(data.get("Error", "API request failed"))
        print("_api_get:", json.dumps(data, indent=2), file=sys.stderr)
        return data.get("Response", {})


async def _api_post(path: str, body: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=NINA_API_URL, timeout=10.0) as client:
        resp = await client.post(path, json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success", False):
            raise RuntimeError(data.get("Error", "API request failed"))
        return cast(dict[str, Any], data.get("Response", {}))


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
            d[timestamp] = ts.replace(microsecond=0).isoformat()
            res.append(d)
    return res


async def _resolve_imaging_root() -> Path:
    profile = await get_site_profile()
    return windows_to_local(profile.image_save_path)


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
    instructions="""Provides tools for controlling NINA (Nighttime Imaging 'N' Astronomy) software run observatories. Lets you inspect equipment, get observatory setup, write observation plans, and run NINA sequences generated from the plans. Focuses on orchestrating observation plans at a high level through NINA sequences rather than managing the individual commands that make up those sequences. Safety semantics: the safety monitor's is_safe field reflects whether the observatory enclosure (roof/dome) is open and it is safe to unpark and expose. is_safe=true means the enclosure is open and the scope may be unparked and acquisition may proceed; is_safe=false means the enclosure is closed, so the scope must remain stowed and acquisition is gated until it becomes safe. This is distinct from weather conditions, which are reported separately by the weather device. Stow behavior is capability-driven: sequences emit Park Scope only when the mount reports can_park=true, otherwise they use Find home when the mount reports can_find_home=true. Progress tracking: maintain an observatory progress file (progress.md in the project directory). On session start, read it to restore context before acting. After significant actions — status checks, plan writes, sequence loads/starts/stops, errors, and interventions — append a short timestamped entry (ISO-8601 timestamp) recording what was done, the observed equipment and safety state, and any decisions. Both the active session and automated worker sessions append to this file so history is shared across sessions.""",
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
    )


@mcp.tool()
async def get_site_equipment_status() -> ObservatoryEquipment:
    """Returns the current connection, operating state, measurements, and capabilities of active observatory equipment, including weather and safety-monitor status (whether the enclosure is open and it is safe to unpark), and mount capabilities (`can_park`, `can_find_home`) that determine how the scope can be stowed. Use it for live operational and safety checks. This tool is read-only and takes no action."""
    profile = await get_site_profile()
    equipment = await _get_site_equipment_status(profile)
    print("Equipment:", equipment.model_dump_json(indent=2), file=sys.stderr)
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
    print("Profile:", profile.model_dump_json(indent=2), file=sys.stderr)
    return profile


@mcp.tool()
async def get_events(since: int = 300) -> Any:
    """Returns recent timestamped NINA observatory events and their event-specific details from the last `since` seconds. Use it to reconstruct sequence, equipment, safety, imaging, and error activity. This tool is read-only; use get_site_equipment_status and sequence_get_state for current status."""
    timestamp = await _api_get("/time")
    now = datetime.fromisoformat(timestamp)
    res = await _api_get("/event-history")
    res = _convert_to_met(res, since=since, now=now, name="Time")
    print("Events:", json.dumps(res, indent=2), file=sys.stderr)
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
    print("Logs:", json.dumps(res, indent=2), file=sys.stderr)
    return res


@mcp.tool()
async def get_imaging_metadata(image_type: str = "light") -> list[dict[str, Any]]:
    """Returns imaging metadata parsed from the ImageMetaData.csv files in the frame folders (LIGHT, DARK, BIAS, FLAT, etc.) of the mounted N.I.N.A imaging directory. Each row is tagged with Date, FrameType, and Source. When an AcquisitionDetails.csv is present in the same session directory, its fields (e.g. TargetName, FocalLength) are injected into each image row unless the image row already has a value for that field. Defaults to lights frames for star-quality checks; pass another image type (light, dark, bias, flat — case-insensitive)."""
    data = read_imaging_csv(root=await _resolve_imaging_root(), image_type=image_type)
    print("Metadata:", json.dumps(data, indent=2), file=sys.stderr)
    return data


# plan tools


@mcp.tool()
async def observation_plan_write_file(plan: ObservationPlan) -> str:
    """Validates and writes an observation plan to a JSON file for later use by sequence_load_plan. The plan defines target coordinates, acquisition intent, light and calibration frames, batching, cooling, autofocus, guiding, and observing constraints. This tool only creates the plan file; it does not load or start a sequence."""
    profile = await get_site_profile()
    await _validate_filters(plan, profile)
    if not plan.plan_id:
        plan = plan.model_copy(update={"plan_id": plan.effective_plan_id()})
    timestamp = datetime.now(tz=UTC).astimezone().strftime("%Y%m%dT%H%M%S")
    filename = f"{_sanitize_filename(plan.target.lower())}_{_sanitize_filename(plan.intent.lower())}_{timestamp}.json"
    filename = filename.replace(" ", "-")
    path = PROJECT_DIR / filename
    async with await anyio.open_file(path, "w", encoding="utf-8") as f:
        await f.write(plan.model_dump_json(indent=2))
    return f"Plan successfully written to {path}"


@mcp.tool()
async def observation_plan_get_progress(
    file_path: str,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
) -> dict[str, Any]:
    """Returns per-frame-type acquisition progress for an observation-plan JSON file: for each exposure group, the total_count from the plan, the acquired_count attributed to this plan from the imaging metadata (ImageMetaData.csv + AcquisitionDetails.csv), and the remaining_count. Lights are attributed via the plan_id embedded in the sequence target name (falling back to the target name for legacy frames); flats/darks/bias are matched by image type, filter, and exposure. Pass max_hfr and/or min_detected_stars to exclude light frames that fail quality thresholds (frames missing the quality fields are excluded when a threshold is set). Use this before sequence_load_plan to decide what still needs acquiring."""
    plan = await _load_plan(file_path)
    rows = await _read_metadata()
    res = {
        "plan_id": plan.effective_plan_id(),
        "target": plan.target,
        "frame_types": plan_progress(
            plan,
            rows,
            max_hfr=max_hfr,
            min_detected_stars=min_detected_stars,
            max_guiding_rms_arcsec=max_guiding_rms_arcsec,
        ),
    }
    print("Progress:", json.dumps(res, indent=2), file=sys.stderr)
    return res


# sequence tools


@mcp.tool()
async def sequence_load_plan(
    file_path: str,
    frame_type: FrameType = "light",
    mode: str = "remaining",
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
) -> str:
    """Loads an acquisition sequence with safety guardrails from an observation-plan JSON file. Select the frame type: light, dark, bias, dawn_flat, or dusk_flat. In the default `remaining` mode the sequence only acquires frames still needed (total minus frames already attributed to this plan in the imaging metadata); if nothing remains it reports the plan as complete and loads nothing. max_hfr and/or min_detected_stars exclude light frames failing quality thresholds from the acquired count. Pass `mode="full"` to acquire the entire plan again. This tool only loads the sequence; call sequence_start afterward."""
    plan = await _load_plan(file_path)
    profile = await get_site_profile()
    await _validate_filters(plan, profile)
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
            max_hfr=max_hfr,
            min_detected_stars=min_detected_stars,
            max_guiding_rms_arcsec=max_guiding_rms_arcsec,
        )
        items = progress[group_key]
        if all(item["remaining_count"] == 0 for item in items):
            total = sum(item["total_count"] for item in items)
            return (
                f"`{frame_type}` plan is complete — {total} of {total} frames "
                "already acquired; nothing to load."
            )
        plan = plan_with_remaining(plan, progress)
    elif mode != "full":
        raise ValueError(f"Unsupported mode: {mode} (use 'remaining' or 'full')")

    if frame_type == "light":
        seq = build_sequence_lights(plan, equipment)
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
async def sequence_execute_teardown() -> str:
    """Loads and starts the non-acquisition teardown sequence, safely stowing the telescope (park or home, per mount capability) while NINA's sequence-level safety guardrails remain active. Use for end-of-observation close-down — when you are done observing and want to shut down the scope — or before leaving the observatory unattended. Allow it to complete without interruption. This is separate from sequence_stop, which halts the current sequence but does not stow the scope."""
    equipment = await get_site_equipment_status()
    seq = build_sequence_teardown(equipment)
    await _api_post("/sequence/load", seq)
    await _api_get("/sequence/start?skipValidation=true")
    return "Teardown sequence started."


@mcp.tool()
async def sequence_enter_safety_standby() -> str:
    """Loads a non-acquisition standby sequence that keeps NINA sequence-level safety and stow guardrails active while the observatory is idle. After loading, call sequence_start. Stop it before loading an acquisition or teardown sequence. Use whenever equipment is deployed and no other sequence is running."""
    equipment = await get_site_equipment_status()
    seq = build_sequence_standby(equipment)
    await _api_post("/sequence/load", seq)
    await _api_get("/sequence/start?skipValidation=true")
    return "Safety standby sequence started."


@mcp.tool()
async def sequence_start() -> str:
    """Starts or resumes the currently loaded sequence, activating its acquisition or safety workflow. Call sequence_get_state afterward to verify that it is running."""
    await _api_get("/sequence/start?skipValidation=true")
    return "Sequence started."


@mcp.tool()
async def sequence_stop() -> str:
    """Stops the currently running NINA sequence immediately, leaving the telescope where it currently is — it does not stow the scope. Use for an urgent halt, to interrupt a stuck/looping sequence, or when the running sequence isn't what you wanted. If you then want to park/home the telescope, run sequence_execute_teardown separately. Note that sequence_load_plan and the teardown/standby entry tools already stop any running sequence before loading, so an explicit stop is only needed when you want to halt without loading anything new. Avoid stopping an in-progress teardown during the stow maneuver unless safety requires it, since interrupting mid-slew can leave the scope in an unsafe position. Call sequence_get_state afterward to confirm the sequence has stopped."""
    await _api_get("/sequence/stop")
    return "Sequence stopped."


@mcp.tool()
async def sequence_get_state() -> Any:
    """Returns the loaded sequence structure and the current status of its containers, instructions, conditions, and triggers. Use it to determine whether a sequence is loaded, running, completed, failed, or waiting. This tool is read-only and takes no action."""
    return await _api_get("/sequence/json")


if __name__ == "__main__":
    mcp.run()
