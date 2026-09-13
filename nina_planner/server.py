import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

from .models.observatory import ObservatoryEquipment
from .models.plan import ObservationPlan
from .models.profile import (
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    SiteLocationInfo,
)
from .nina_utils import ascom_float, ascom_int
from .sequence import build_sequence_darks, build_sequence_flats, build_sequence_lights

mcp = FastMCP("nina-planner")

NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "localhost:1888")
NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"


def _to_snake(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def _convert_keys(d: Any) -> Any:
    if isinstance(d, dict):
        return {_to_snake(k): _convert_keys(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_convert_keys(v) for v in d]
    return d


async def _api_get(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=NINA_API_URL, timeout=10.0) as client:
        resp = await client.get(path)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success", False):
            raise RuntimeError(data.get("Error", "API request failed"))
        return data.get("Response", {})


async def _api_post(path: str, body: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=NINA_API_URL, timeout=10.0) as client:
        resp = await client.post(path, json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success", False):
            raise RuntimeError(data.get("Error", "API request failed"))
        return data.get("Response", {})


async def _validate_filters(plan: ObservationPlan, profile: ObservatoryProfile):
    profile_filter_names = {f.name for f in profile.filters}
    plan_filter_names: set[str] = set()
    for light in plan.lights:
        if light.filter_name:
            plan_filter_names.add(light.filter_name)
    for flat in plan.flats:
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


# mcp tools


@mcp.tool()
async def get_site_equipment() -> ObservatoryEquipment:
    """List active equipment (telescope mount, camera, focuser, guider, safety monitor)."""
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

    for key, (_, path) in DEVICE_MAP.items():
        data = raw.get(key, {})
        if data and not data.get("Connected"):
            name = data.get("Name") or data.get("DisplayName")
            if name:
                try:
                    await _api_get(path + "/connect")
                except (httpx.HTTPError, RuntimeError):
                    logger.warning("Failed to connect %s", name)

    raw = await _api_get("/equipment/info")

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
        return model_cls(**converted)

    equipment = ObservatoryEquipment(
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
    logger.info("Equipment: %s", equipment.model_dump_json(indent=2))
    return equipment


@mcp.tool()
async def get_site_profile() -> ObservatoryProfile:
    """Get static observatory profile: latitude, longitude, elevation, and metadata of site."""
    raw = await _api_get("/profile/show?active=true")

    astrometry = raw.get("AstrometrySettings", {})
    telescope = raw.get("TelescopeSettings", {})
    camera = raw.get("CameraSettings", {})
    filter_wheel = raw.get("FilterWheelSettings", {})
    image_file = raw.get("ImageFileSettings", {})
    plate_solve = raw.get("PlateSolveSettings", {})

    framing = raw.get("FramingAssistantSettings", {})

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
    )
    logger.info("Profile: %s", profile.model_dump_json(indent=2))
    return profile


# plan calls


@mcp.tool()
async def write_plan_file(plan: ObservationPlan) -> str:
    """Write observation plan to a JSON file."""
    profile = await get_site_profile()
    await _validate_filters(plan, profile)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    filename = f"{plan.target.lower()}_{plan.intent.lower()}_{timestamp}.json"
    filename = filename.replace(" ", "-")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(plan.model_dump_json(indent=2))
    return f"Plan successfully written to {filename}"


FrameType = Literal["lights", "darks", "bias", "dawn_flats", "dusk_flats"]


@mcp.tool()
async def sequence_load(
    file_path: str,
    frame_type: FrameType = "lights",
) -> str:
    """Load an observation plan from a JSON file.
    Args:
        file_path: Path to a JSON observation plan file.
        frame_type: Type of sequence to construct. Allowed values:
            'lights', 'darks', 'bias', 'dawn_flats', 'dusk_flats'. Defaults to 'lights'.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.loads(f.read())
    plan = ObservationPlan.model_validate(data)
    profile = await get_site_profile()
    await _validate_filters(plan, profile)
    if frame_type == "lights":
        seq = build_sequence_lights(plan)
    elif frame_type == "darks":
        seq = build_sequence_darks(plan)
    elif frame_type == "bias":
        seq = build_sequence_darks(plan, bias=True)
    elif frame_type == "dawn_flats":
        seq = build_sequence_flats(plan, profile)
    elif frame_type == "dusk_flats":
        seq = build_sequence_flats(plan, profile, dusk=True)
    else:
        raise ValueError(f"Unsupported frame type: {frame_type}")
    await _api_get("/sequence/stop")
    await _api_post("/sequence/load", seq)
    return f"`{frame_type}` sequence loaded."


@mcp.tool()
async def sequence_start(reset: bool = False) -> str:
    """Start or resume the acquisition sequence.
    Args:
        reset: If True, resets exposure counts before starting. Defaults to False."""
    if reset:
        await _api_get("/sequence/reset")
    await _api_get("/sequence/start?skipValidation=true")
    return "Sequence started."


@mcp.tool()
async def sequence_stop() -> str:
    """stop sequence"""
    await _api_get("/sequence/stop")
    return "Sequence stopped."


@mcp.tool()
async def sequence_skip() -> str:
    """skip to end sequence to wait for observatory to close before tearing down"""
    await _api_get("/sequence/skip?type=toEnd")
    return "Sequence skipped to end."


## other functions


@mcp.tool()
async def park_telescope() -> str:
    """park the telescope"""
    await _api_get("/equipment/mount/park")
    return "Telescope parked."


@mcp.tool()
async def home_telescope() -> str:
    """home the telescope"""
    await _api_get("/equipment/mount/home")
    return "Telescope homed."


@mcp.tool()
async def warm_camera() -> str:
    """warm the camera"""
    await _api_get("/equipment/camera/warm?minutes=0")
    return "Camera warmed."


## returns json


@mcp.tool()
async def get_event_history() -> Any:
    """get latest observatory event history"""
    return await _api_get("/event-history")


@mcp.tool()
async def get_application_logs() -> Any:
    """get latest info, error, and warning logs"""
    return await _api_get("/application/logs?lineCount=100")


@mcp.tool()
async def sequence_state() -> Any:
    """get current state of sequence"""
    return await _api_get("/sequence/json")


if __name__ == "__main__":
    mcp.run()
