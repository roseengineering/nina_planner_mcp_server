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
    """Returns per-exposure imaging metadata for one pointing of a plan, parsed from the ImageMetaData.csv and WeatherData.csv files in the N.I.N.A. imaging directory.

    Frame attribution:
    - Light frames are attributed via the plan id in the recorded file path: `{base_plan_id}` for a single-pointing plan, `{base_plan_id}-{pointing_index}` per pane for a mosaic. There is no target-name fallback.
    - Dark/bias/flat frames are matched plan-wide by image type, filter, and exposure, and count only when shot within the plan's `calibration_window_days` of the session start (the earliest attributed light), on either side of it.
    - `pointing_index` (default 1, range-checked 1..pointing count for every image type) labels the response (`plan_id`, `pointing_index`). For lights it selects the pointing; for calibration types it only selects which pointing's light session anchors the window.

    `image_type` is `light` (default), `dark`, `bias`, or `flat` (case-insensitive).

    This reports the frames that are attributable, not what the plan still needs; use get_plan_progress for that.

    Returns a wide table with one row per exposure: identity fields (file_path, exposure_number, exposure_start, duration, filter_name) plus metrics as columns namespaced by group (quality.hfr, guiding.rms, background.adu_mean, pointing.airmass, weather.temperature, ...). Weather samples are joined by exposure number and UTC exposure start. Timestamps are in the host's local time with UTC offset (e.g. 2026-09-21T21:16:25.123-05:00). Columns constant across the returned frames are listed once under `summary.constants`; columns with no real values (NaN/-1, empty, 'n/a', or the 0 sentinel NINA writes for unmeasured metrics) are listed under `summary.unpopulated`. This tool is read-only."""
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
    """Validates and writes an observation plan to a JSON file for later use by load_sequence. The plan defines one or more target pointings (RA/Dec/PA, optional label), acquisition intent, light and calibration frames, batching, cooling, autofocus, guiding, and observing constraints. This tool only creates the plan file; it does not load or start a sequence."""
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
    """Expands a base observation plan into a multi-pointing mosaic plan from a Telescopius-formatted mosaic CSV. The base plan supplies every setting except the pointings (target, intent, exposure groups, calibration, cooler, autofocus, guiding, constraints); its pointings are replaced wholesale by one pointing per CSV row. The CSV must follow the Telescopius export layout — nine columns in the fixed order `Pane`, `RA`, `DEC`, `Position Angle (East)`, `Pane width (arcmins)`, `Pane height (arcmins)`, `Overlap`, `Row`, `Column` — and RA must be hours in the range [0, 24) (sexagesimal like `0hr 56' 01"` or decimal hours); a non-matching header or an out-of-range RA rejects the whole file rather than wrapping it. `row`/`column` are stored on each pointing as metadata (N.I.N.A. does not consume them). The base plan's `plan_id` is cleared so the mosaic gets its own content-derived id, and each pane is attributed independently via `[{plan_id}-{pointing_index}]` when run with `load_sequence(request=[{"plan": <plan_path>, "pointing_index": N}])`. With no rotator, every pane's `position_angle_deg` must be 0; any nonzero PA fails loudly. Writes `<target>_<intent>_mosaic_<timestamp>.json` in the project directory unless `output_path` is given. This tool only creates the plan file; it does not load or start a sequence."""
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
    """Returns per-frame-type acquisition progress for every pointing of an observation-plan JSON file: for each exposure group, the total_count from the plan, the acquired_count attributed to that plan/pointing from ImageMetaData.csv, and the remaining_count. Lights are attributed via the plan id embedded in the recorded file path (`{base_plan_id}` for a single-pointing plan, `{base_plan_id}-{pointing_index}` per pane for a mosaic; no target-name fallback); flats/darks/bias are matched by image type, filter, and exposure and are anchored to the plan's light session: they count only when shot within `calibration_window_days` of the session start (the earliest attributed light), on either side of it. Calibration groups therefore also carry `window_open` (bool) and `window_end` (the window's upper bound, rendered in the host's local time with its UTC offset): while the current time is inside the window, remaining_count is total minus acquired; once it falls outside, no frame shot now could ever be credited, so remaining_count is reported as 0 rather than an unsatisfiable deficit (acquired_count stays truthful either way, and lights carry neither field) — raise `calibration_window_days` or write a new plan to reopen the window. Pass max_hfr, min_detected_stars, and/or max_guiding_rms_arcsec to exclude light frames that fail quality thresholds (frames missing the relevant field are excluded when a threshold is set). Use this before load_sequence to decide what still needs acquiring. Returns complete (all panes done) plus a pointings list of per-pane progress and per-pane complete, so a worker can acquire panes sequentially (finish pane N, then run pane N+1)."""
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
