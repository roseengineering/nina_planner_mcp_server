import base64
import json
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from typing import Any, cast

import anyio
from mcp.server.fastmcp import Image

from .. import client, config
from ..app import mcp
from ..models.observatory import ObservatoryEquipment
from ..models.profile import (
    EquipmentConfig,
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    ProfileSummary,
    SiteLocationInfo,
)
from ..nina_utils import ascom_float, ascom_int, to_local_display, to_snake


def _convert_keys(d: Any) -> Any:
    if isinstance(d, dict):
        return {to_snake(k): _convert_keys(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_convert_keys(v) for v in d]
    return d


def _convert_to_met(
    data: list[dict[str, Any]],
    since: int,
    name: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    timestamp = "timestamp"
    now = now or datetime.now().astimezone()
    # A naive `now` cannot take part in `ts - now` arithmetic against
    # offset-aware event timestamps (e.g. Z-suffix events), so tag it
    # host-local.
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    tzinfo = now.tzinfo
    res = []
    dropped = 0
    for d in data:
        # A record without a usable timestamp cannot be sorted or placed in
        # the `since` window; drop it rather than crash the whole listing.
        value = d.get(name)
        if not isinstance(value, str) or not value:
            dropped += 1
            continue
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            dropped += 1
            continue
        d = {k: v for k, v in d.items() if k != name}
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=tzinfo)
        d = _convert_keys(d)
        d[timestamp] = ts
        res.append(d)
    if dropped:
        client.log_payload(
            "_convert_to_met:",
            f"dropped {dropped} record(s) without a usable {name!r} timestamp",
        )
    data = sorted(res, key=lambda d: d[timestamp])
    res = []
    for d in data:
        ts = d[timestamp]
        # `now` is the host clock, so `since` is measured against local time;
        # the stamp is only re-rendered for display, never re-based.
        elapsed = (ts - now).total_seconds()
        if since + elapsed > 0:
            d[timestamp] = to_local_display(ts)
            res.append(d)
    return res


@mcp.tool()
async def _get_site_equipment_status(
    profile: ObservatoryProfile,
) -> ObservatoryEquipment:
    from ..models.camera import CameraDevice
    from ..models.dome import DomeDevice
    from ..models.filter_wheel import FilterWheelDevice
    from ..models.focuser import FocuserDevice
    from ..models.guider import GuiderDevice
    from ..models.mount import MountDevice
    from ..models.rotator import RotatorDevice
    from ..models.safety_monitor import SafetyMonitorDevice
    from ..models.switch import Switch
    from ..models.weather import Weather

    raw = await client.api_get("/equipment/info")

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
            await client.api_get(path + "/connect")
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
    """Returns current equipment status, measurements, capabilities, weather, and enclosure safety state. Automatically connects disconnected devices; retry in a few seconds if a device is still connecting."""
    profile = await get_site_profile()
    equipment = await _get_site_equipment_status(profile)
    client.log_payload("Equipment:", equipment.model_dump_json(indent=2))
    return cast(ObservatoryEquipment, equipment)


@mcp.tool()
async def get_site_profile() -> ObservatoryProfile:
    """Returns the active NINA profile's site, optics, camera, filters, plate solvers, and image paths. Use `local_image_save_path` for local file access; use `get_site_equipment_status()` for live device state. Read-only."""
    raw = await client.api_get("/profile/show?active=true")

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

    filters: list[FilterInfo] = []
    for f in filter_wheel.get("FilterWheelFilters", []):
        if not isinstance(f, dict) or not f.get("Name"):
            continue
        name = f["Name"]
        try:
            position = ascom_int(f.get("Position"))
        except (TypeError, ValueError):
            position = None
        if position is None:
            # Position selects the physical wheel slot and has no safe
            # default: drop the filter from the profile (a plan referencing
            # it then fails plan_utils.validate_filters loudly, by name) rather than
            # ever drive the wheel to a bogus slot mid-sequence.
            client.log_payload(
                "get_site_profile: skipping filter",
                f"{name!r}: Position missing or not a number: {f.get('Position')!r}",
            )
            continue
        try:
            focus_offset = ascom_int(f.get("FocusOffset"))
        except (TypeError, ValueError):
            # Metadata, not an instruction — FilterInfo's own default of
            # None ("no offset") is the safe reading; keep the filter.
            focus_offset = None
        filters.append(
            FilterInfo(name=name, position=position, focus_offset=focus_offset)
        )

    def _exists(
        section: dict, field: str, skip: AbstractSet[str] = frozenset({"No_Device"})
    ) -> bool:
        value = section.get(field)
        return value is not None and value not in skip and value != ""

    equipment = EquipmentConfig(
        has_mount=_exists(telescope, "MountName") or _exists(telescope, "Name"),
        has_guider=_exists(guider, "GuiderName", {"No_Guider", "No_Device"}),
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
        guider_name=guider.get("GuiderName"),
        image_save_path=image_file.get("FilePath", ""),
        file_pattern=image_file.get("FilePattern"),
        equipment=equipment,
    )
    client.log_payload("Profile:", profile.model_dump_json(indent=2))
    return profile


@mcp.tool()
async def list_site_profiles() -> list[ProfileSummary]:
    """Lists NINA profile summaries and marks the active profile. Use a profile's ID with `switch_site_profile()`; inspect active-profile details with `get_site_profile()`. Read-only."""
    metas = await client.api_get("/profile/show?active=false")
    active_id = (await client.api_get("/profile/show?active=true")).get("Id", "")
    profiles = [
        ProfileSummary(
            profile_id=meta.get("Id", ""),
            profile_name=meta.get("Name", ""),
            description=meta.get("Description") or None,
            last_used=meta.get("LastUsed") or None,
            active=meta.get("Id") == active_id,
        )
        for meta in metas
    ]
    client.log_payload(
        "Profiles:", json.dumps([p.model_dump(mode="json") for p in profiles])
    )
    return profiles


@mcp.tool(structured_output=False)
async def screenshot_dashboard() -> list[Image | str]:
    """Captures the NINA Imaging tab and returns the screenshot path and image. Switches tabs and writes a new temporary PNG; does not change equipment or sequence state."""
    await client.api_get("/application/switch-tab?tab=imaging")
    if config.SCREENSHOT_SETTLE_SECONDS:
        await anyio.sleep(config.SCREENSHOT_SETTLE_SECONDS)
    text = await client.api_get("/application/screenshot")
    if not isinstance(text, str) or not text.strip():
        detail = f"got {type(text).__name__}: {text!r:.200}"
        raise RuntimeError(
            f"NINA /application/screenshot returned no base64 screenshot ({detail})"
        )
    data = base64.b64decode(text)
    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).astimezone().strftime("%Y%m%dT%H%M%S%f")
    path = config.SCREENSHOT_DIR / f"dashboard_{timestamp}.png"
    async with await anyio.open_file(path, "wb") as f:
        await f.write(data)
    return [str(path), Image(data=data, format="png")]


@mcp.tool()
async def switch_site_profile(profile_id: str) -> str:
    """Switches to a profile ID returned by `list_site_profiles()`. This reconnects equipment and changes observatory settings; do not switch profiles while a sequence is running. Confirm with `get_site_profile()`."""
    return cast(str, await client.api_get(f"/profile/switch?profileid={profile_id}"))


@mcp.tool()
async def get_events(since: int = 300) -> Any:
    """Returns recent NINA events for sequence, equipment, safety, imaging, and error history. Use equipment and sequence status tools for current state. Read-only."""
    res = await client.api_get("/event-history")
    res = _convert_to_met(res, since=since, name="Time")
    client.log_payload("Events:", json.dumps(res, indent=2))
    return res


@mcp.tool()
async def get_logs(since: int = 300) -> Any:
    """Returns recent NINA application logs for diagnosing sequence and equipment problems. Read-only."""
    res = await client.api_get("/application/logs?lineCount=200")
    res = _convert_to_met(res, since=since, name="Timestamp")
    for d in res:
        if "line" in d:
            del d["line"]
        if "member" in d:
            del d["member"]
        if "source" in d:
            del d["source"]
    client.log_payload("Logs:", json.dumps(res, indent=2))
    return res
