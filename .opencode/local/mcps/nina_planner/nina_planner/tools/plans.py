import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from .. import client, config, metadata, plan_utils
from ..app import mcp
from ..imaging import read_imaging_csv, read_weather_csv, widen_imaging_metadata
from ..models.plan import ObservationPlan
from ..mosaic import load_pointings
from ..progress import filter_metadata_rows, plan_progress
from . import equipment as equipment_tools


@mcp.tool()
async def get_imaging_metadata(
    file_path: str,
    pointing_index: int = 1,
    image_type: str = "light",
) -> dict[str, Any]:
    """Returns per-exposure imaging and weather metrics attributed to one plan pointing. Choose `image_type` from light, dark, bias, or flat. Use `get_plan_progress` for acquired and remaining counts. Read-only."""
    root = await metadata.resolve_imaging_root()
    plan = await plan_utils.load_plan(file_path)
    plan_utils.check_pointing_index(plan, pointing_index)
    rows = read_imaging_csv(root=root, image_type=image_type)
    if image_type.upper() != "LIGHT":
        # Calibration is anchored to the plan's light session, so the light
        # rows are needed to derive session_start even when the caller asked
        # for a calibration type.
        rows = read_imaging_csv(root=root, image_type="light") + rows
    rows = filter_metadata_rows(
        plan,
        rows,
        image_type,
        pointing_index=pointing_index,
        max_age_days=plan.calibration_window_days,
        now=datetime.now(UTC).replace(microsecond=0),
    )
    weather = read_weather_csv(root=root)
    res = widen_imaging_metadata(rows, weather_rows=weather)
    res["plan_id"] = plan.effective_plan_id(pointing_index)
    res["pointing_index"] = pointing_index
    res["target"] = plan.target
    client.log_payload("Metadata:", json.dumps(res, indent=2))
    return res


# plan tools


@mcp.tool()
async def write_plan_file(plan: ObservationPlan) -> str:
    """Validates and writes an observation-plan JSON file for use with `load_sequence`. Does not load or start a sequence."""
    profile = await equipment_tools.get_site_profile()
    await plan_utils.validate_filters(plan, profile)
    plan_utils.validate_position_angle(plan, profile)
    if not plan.plan_id:
        plan = plan.model_copy(update={"plan_id": plan._base_plan_id()})
    timestamp = datetime.now(tz=UTC).astimezone().strftime("%Y%m%dT%H%M%S")
    filename = f"{plan_utils.sanitize_filename(plan.target.lower())}_{plan_utils.sanitize_filename(plan.intent.lower())}_{timestamp}.json"
    filename = filename.replace(" ", "-")
    path = config.PROJECT_DIR / filename
    async with await anyio.open_file(path, "w", encoding="utf-8") as f:
        await f.write(plan.model_dump_json(indent=2))
    return f"Plan successfully written to {path}"


@mcp.tool()
async def write_mosaic_plan(
    plan_path: str,
    mosaic_csv: str,
    output_path: str | None = None,
) -> str:
    """Creates a multi-pointing mosaic plan by replacing a base plan's pointings with rows from a Telescopius CSV. Requires a rotator for nonzero position angles. Writes the plan file but does not load or start a sequence."""
    base = await plan_utils.load_plan(plan_path)
    csv_path = Path(mosaic_csv)
    if not csv_path.is_absolute():
        csv_path = config.PROJECT_DIR / csv_path
    pointings = load_pointings(csv_path)
    plan = base.model_copy(update={"pointings": pointings, "plan_id": ""})
    profile = await equipment_tools.get_site_profile()
    await plan_utils.validate_filters(plan, profile)
    plan_utils.validate_position_angle(plan, profile)
    if not plan.plan_id:
        plan = plan.model_copy(update={"plan_id": plan._base_plan_id()})

    if output_path:
        path = Path(output_path)
        if not path.is_absolute():
            path = config.PROJECT_DIR / path
    else:
        timestamp = datetime.now(tz=UTC).astimezone().strftime("%Y%m%dT%H%M%S")
        filename = (
            f"{plan_utils.sanitize_filename(plan.target.lower())}_"
            f"{plan_utils.sanitize_filename(plan.intent.lower())}_mosaic_{timestamp}.json"
        )
        filename = filename.replace(" ", "-")
        path = config.PROJECT_DIR / filename

    async with await anyio.open_file(path, "w", encoding="utf-8") as f:
        await f.write(plan.model_dump_json(indent=2))
    return (
        f"Mosaic plan successfully written to {path}: {len(plan.pointings)} pointing(s)"
    )


@mcp.tool()
async def get_plan_progress(
    file_path: str,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
) -> dict[str, Any]:
    """Returns acquired and remaining frame counts for each exposure group and plan pointing. Optional quality thresholds exclude failing light frames; calibration counts follow the plan's calibration window. Use to decide what remains to acquire."""
    plan = await plan_utils.load_plan(file_path)
    rows = await metadata.read_metadata()
    now = datetime.now(UTC).replace(microsecond=0)

    def progress_for(index: int) -> dict[str, list[dict[str, Any]]]:
        return plan_progress(
            plan,
            rows,
            pointing_index=index,
            max_hfr=max_hfr,
            min_detected_stars=min_detected_stars,
            max_guiding_rms_arcsec=max_guiding_rms_arcsec,
            now=now,
        )

    entries: list[dict[str, Any]] = []
    complete = True
    for index in range(1, len(plan.pointings) + 1):
        pointings_progress = progress_for(index)
        pane_done = all(
            item["remaining_count"] == 0
            for items in pointings_progress.values()
            for item in items
        )
        complete = complete and pane_done
        pointing = plan.pointings[index - 1]
        entries.append(
            {
                "pointing_index": index,
                "label": pointing.label,
                "plan_id": plan.effective_plan_id(index),
                "complete": pane_done,
                "frame_types": pointings_progress,
            }
        )
    res = {
        "plan_id": plan._base_plan_id(),
        "target": plan.target,
        "pointing_count": len(plan.pointings),
        "complete": complete,
        "pointings": entries,
    }
    client.log_payload("Progress:", json.dumps(res, indent=2))
    return res


# sequence tools
