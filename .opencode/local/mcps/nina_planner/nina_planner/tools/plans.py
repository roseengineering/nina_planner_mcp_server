import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from .. import client, config, metadata, plan_utils
from ..app import mcp
from ..imaging import (
    _prunable_dir,
    _walk_visible,
    read_imaging_csv,
    read_weather_csv,
    widen_imaging_metadata,
)
from ..models.frame import FrameStats
from ..models.plan import ObservationPlan
from ..mosaic import load_pointings
from ..progress import _light_target_matches, filter_metadata_rows, plan_progress
from . import equipment as equipment_tools


@mcp.tool()
async def get_imaging_metadata(
    file_path: str,
    pointing_index: int = 1,
    image_type: str = "light",
) -> dict[str, Any]:
    """Returns per-exposure imaging metadata for one pointing of a plan, parsed from the ImageMetaData.csv and WeatherData.csv files in the N.I.N.A. imaging directory.

    Frame attribution:
    - Light frames are attributed via the underscore-delimited plan-id token in the recorded file path: `_{base_plan_id}_` for a single-pointing plan, `_{base_plan_id}-{pointing_index}_` per pane for a mosaic (N.I.N.A. rewrites `[` and `]` to `_` when it substitutes `$$TARGETNAME$$` into the path, so the token is written with underscores). There is no target-name fallback.
    - Dark/bias frames are matched plan-wide by image type and exposure; flat frames by image type and filter (exposure is not checked for flats). All calibration frames count only when shot within the plan's `calibration_window_days` of the session start (the earliest attributed light), on either side of it.
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


@mcp.tool()
async def get_frame_stats(image_type: str | None = None) -> dict[str, Any]:
    """Returns N.I.N.A.'s live per-frame statistics for the current (most recently captured) frame, read from the image-history API.

    This is the on-demand mirror of the quality watchdog's own data source, so on a QUALITY-WATCHDOG wake you can read exactly the numbers it saw instead of waiting for ImageMetaData.csv to be written. Returns the full record: HFR, HFR standard deviation (`hfr_st_dev`), detected star count (`stars`), background ADU statistics (`mean`, `median`, `st_dev`, `min`, `max`), guiding RMS text (`rms_text`), camera temperature, gain, offset, filter, exposure time, target name, and filename. `date` is the frame's capture timestamp.

    Scope: despite its name, image-history serves only the current frame (a single record; there is no paging and no count parameter). For a multi-frame trend use get_imaging_metadata, which reads the on-disk ImageMetaData.csv for the whole session — this tool is the live view, that one is the historical view. A genuine `0` (e.g. `stars: 0`) is a real reading, not a missing value.

    `image_type` (optional, e.g. "light", "dark") is passed through as the API's imageType filter to assert the current frame's type; when the current frame is not of that type the tool reports `count: 0` rather than erroring. This tool is read-only and takes no action.

    Returns a dict: `count` (1 when a frame is present, else 0), `image_type` (the frame's type, or the requested filter when empty), `frame` (the full statistics record as snake_case fields, or null), and `note` (present only when count is 0)."""
    path = "/image-history"
    if image_type:
        path += f"?imageType={image_type.upper()}"
    try:
        payload = await client.api_get(path)
    except RuntimeError as e:
        # The API reports an unmatchable/absent image as a failed envelope:
        # HTTP 200 with Success=false and Error="Index out of range", which
        # unwrap_response surfaces as a RuntimeError carrying that text. That
        # is an expected empty state (no frame yet, or none of this type), not
        # a fault — so it degrades to count 0 instead of raising.
        if "Index out of range" in str(e):
            note = (
                f"no {image_type.upper()} frame recorded yet"
                if image_type
                else "no frame recorded yet"
            )
            return {
                "count": 0,
                "image_type": image_type.upper() if image_type else None,
                "frame": None,
                "note": note,
            }
        raise

    # image-history returns a list whose single element is the current frame;
    # be tolerant of a bare object too, in case a future version changes shape.
    record: Any
    if isinstance(payload, list):
        record = payload[0] if payload else None
    elif isinstance(payload, dict):
        record = payload
    else:
        record = None

    if not isinstance(record, dict) or not record:
        return {
            "count": 0,
            "image_type": image_type.upper() if image_type else None,
            "frame": None,
            "note": "image-history returned no frame record",
        }

    frame = FrameStats.model_validate(record)
    res = {
        "count": 1,
        "image_type": frame.image_type or (image_type.upper() if image_type else None),
        "frame": frame.model_dump(mode="json"),
        "note": None,
    }
    client.log_payload("FrameStats:", json.dumps(res, indent=2))
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
    """Expands a base observation plan into a multi-pointing mosaic plan from a Telescopius-formatted mosaic CSV. The base plan supplies every setting except the pointings (target, intent, exposure groups, calibration, cooler, autofocus, guiding, constraints); its pointings are replaced wholesale by one pointing per CSV row. The CSV must follow the Telescopius export layout — nine columns in the fixed order `Pane`, `RA`, `DEC`, `Position Angle (East)`, `Pane width (arcmins)`, `Pane height (arcmins)`, `Overlap`, `Row`, `Column` — and RA must be hours in the range [0, 24) (sexagesimal like `0hr 56' 01"` or decimal hours); a non-matching header or an out-of-range RA rejects the whole file rather than wrapping it. `row`/`column` are stored on each pointing as metadata (N.I.N.A. does not consume them). The base plan's `plan_id` is cleared so the mosaic gets its own content-derived id, and each pane is attributed independently via `_{plan_id}-{pointing_index}_` when run with `load_sequence(request=[{"plan": <plan_path>, "pointing_index": N}])`. With no rotator, every pane's `position_angle_deg` must be 0; any nonzero PA fails loudly. Writes `<target>_<intent>_mosaic_<timestamp>.json` in the project directory unless `output_path` is given. This tool only creates the plan file; it does not load or start a sequence."""
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
    """Returns per-frame-type acquisition progress for every pointing of an observation-plan JSON file: for each exposure group, the total_count from the plan, the acquired_count attributed to that plan/pointing from the imaging metadata (ImageMetaData.csv), and the remaining_count. Lights are attributed via the underscore-delimited plan id embedded in the recorded file path (`_{base_plan_id}_` for a single-pointing plan, `_{base_plan_id}-{pointing_index}_` per pane for a mosaic; no target-name fallback); flats/darks/bias are matched by image type (plus filter for flats, plus exposure for darks) and are anchored to the plan's light session: they count only when shot within `calibration_window_days` of the session start (the earliest attributed light), on either side of it. Calibration groups therefore also carry `window_open` (bool) and `window_end` (the window's upper bound, rendered in the host's local time with its UTC offset): while the current time is inside the window, remaining_count is total minus acquired; once it falls outside, no frame shot now could ever be credited, so remaining_count is reported as 0 rather than an unsatisfiable deficit (acquired_count stays truthful either way, and lights carry neither field) — raise `calibration_window_days` or write a new plan to reopen the window. Pass max_hfr, min_detected_stars, and/or max_guiding_rms_arcsec to exclude light frames that fail quality thresholds (frames missing the relevant field are excluded when a threshold is set). Use this before load_sequence to decide what still needs acquiring. Every exposure group also carries `deficit_count` (the raw total minus acquired, floored at zero, never adjusted for the window). Returns `complete` (all panes done — decided from acquired counts and window state, NOT from `remaining_count == 0`, because a closed calibration window reports remaining 0 even when frames are missing) plus a `pointings` list of per-pane progress, per-pane `complete`, and per-pane `status` (`complete` / `incomplete` / `expired_with_deficit`), so a worker can acquire panes sequentially (finish pane N, then run pane N+1). The top level also carries `expired_with_deficit` (bool): true when any pane's calibration window closed with frames still missing — `complete` is then false, but the fix is a raised `calibration_window_days` or a new plan, never more shooting, and `warning` says so. Light items also carry `surplus_count` (frames attributed beyond the plan's total_count), and the top level carries `overshot` (bool) and `warning` (string or null). These exist because acquired_count is cumulative across runs while total_count is a per-plan constant, so a mode=full re-shoot that delivered only some of its frames still reports `complete: true, remaining: 0` — the older frames keep satisfying the plan. When `overshot` is true, treat `complete` as "these frames exist", not as "the last run succeeded", and verify the latest run with audit_plans plus the per-date frame folders."""
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
    expired_any = False
    for index in range(1, len(plan.pointings) + 1):
        pointings_progress = progress_for(index)
        # Completion is decided from ACQUIRED COUNTS and WINDOW STATE, never
        # from `remaining_count == 0` alone: plan_progress zeroes a
        # calibration group's remaining once its window closes, so a window
        # that expired at 8/10 darks reads remaining 0 while the deficit is
        # still very real. A group is satisfied when its frames exist
        # (acquired >= total); a closed window with frames missing is NOT
        # satisfied — it is expired, and shooting cannot fix it (only a
        # raised calibration_window_days or a new plan can).
        pane_status = "complete"
        for items in pointings_progress.values():
            for item in items:
                acquired = int(item.get("acquired_count", 0))
                total = int(item.get("total_count", 0))
                if acquired >= total:
                    continue
                if item.get("window_open") is False:
                    pane_status = "expired_with_deficit"
                elif pane_status != "expired_with_deficit":
                    pane_status = "incomplete"
        pane_done = pane_status == "complete"
        expired_any = expired_any or pane_status == "expired_with_deficit"
        complete = complete and pane_done
        pointing = plan.pointings[index - 1]
        entries.append(
            {
                "pointing_index": index,
                "label": pointing.label,
                "plan_id": plan.effective_plan_id(index),
                "complete": pane_done,
                # Named separately from `incomplete`: an expired deficit is
                # not something a sequence run can fix, so a caller must be
                # able to tell "shoot more frames" apart from "write a new
                # plan / raise calibration_window_days".
                "status": pane_status,
                "frame_types": pointings_progress,
            }
        )
    res = {
        "plan_id": plan._base_plan_id(),
        "target": plan.target,
        "pointing_count": len(plan.pointings),
        "complete": complete,
        "expired_with_deficit": expired_any,
        "overshot": False,
        "warning": None,
        "pointings": entries,
    }
    # A plan can be `complete` and still have lost frames: a mode=full
    # re-shoot asks for total_count *more* frames, but acquired_count is
    # cumulative, so the older frames keep the plan complete no matter what
    # the re-shoot delivered. Surplus is the only trace of that here, and it
    # is worth shouting about because the default reading — complete:true,
    # remaining:0 — is exactly what an operator sees after a truncated
    # re-shoot (M42: 17 of 120 frames landed, plan read complete).
    surplus = sum(
        item.get("surplus_count", 0)
        for entry in entries
        for item in entry["frame_types"]["light"]
    )
    if surplus:
        res["overshot"] = True
        res["warning"] = (
            f"{surplus} light frame(s) attributed beyond the plan total. That "
            "means this target has been acquired more than once — either a "
            "mode=full re-shoot or separate nights under the same plan id. "
            "`complete: true` only means the plan's frames exist; it does NOT "
            "mean a re-shoot delivered. Confirm what the latest run actually "
            "landed with audit_plans and the per-date frame folders on the "
            "imaging drive."
        )
    if expired_any:
        # Reported apart from `complete` because the fix differs: no sequence
        # run can close an expired calibration deficit (the window would have
        # to be open for a frame shot now to be credited), so an operator who
        # only reads "not complete" would re-shoot lights that are already
        # fine. Name the two remedies instead.
        expired_types = sorted(
            {
                image_type
                for entry in entries
                for image_type in ("flat", "dark", "bias")
                if any(
                    g.get("window_open") is False and int(g.get("deficit_count", 0)) > 0
                    for g in entry["frame_types"][image_type]
                )
            }
        )
        warning = (
            "Calibration window(s) expired with deficits: "
            f"{', '.join(expired_types) or 'calibration'} still short of the "
            "plan's total when the window closed. `complete: false` here does "
            "NOT mean shoot more frames — no frame shot now could be credited. "
            "Raise `calibration_window_days` or write a new plan to reopen "
            "the window; confirm the per-type counts with audit_plans "
            "(EXPIRED rows)."
        )
        if res["warning"]:
            res["warning"] = res["warning"] + " ALSO: " + warning
        else:
            res["warning"] = warning
    client.log_payload("Progress:", json.dumps(res, indent=2))
    return res


# capture audit


_AUDIT_TYPES = ("light", "flat", "dark", "bias")
_FITS_SUFFIXES = (".fits", ".fits.fz")
# Statuses that mean "not everything is saved/counted right" — the
# `all_saved` verdict fails on any of them (see audit_plans' docstring).
# NEVER_SHOT belongs here: a pointing no light row references is as
# un-acquired as a partial MISSING_FILES deficit, just wholly so.
_AUDIT_BROKEN = ("MISSING_FILES", "EXPIRED", "SUSPECT_JUNK", "NEVER_SHOT")
# A delimited plan-id-looking token inside a recorded path. CONSERVATIVE on
# purpose — letter-initial, ≥5 chars, contains "-" — so filename fragments
# like "60.00s", "2026-10-22" or "L" never read as an id. Used only to name
# ids that no plan claims (audit's unmatched list); plan↔row matching itself
# goes through progress._light_target_matches, which needs no such filter.
_PLAN_ID_CANDIDATE_RE = re.compile(r"[_\[(]([A-Za-z][A-Za-z0-9.\-]{2,})[_\])]")


def _probe_plan_id(path: Path) -> str | None:
    """Best-effort ``plan_id`` from a plan file that failed to validate.

    Used only to decide whether a *load failure* has frames in
    ImageMetaData.csv: JSON that parses but fails ObservationPlan validation
    still carries its id, so its frames can be attributed to the failing
    file instead of being reported as an unidentifiable gap. Returns None
    for unparseable JSON or a file without an id (never-shot, not a plan).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plan_id = data.get("plan_id") if isinstance(data, dict) else None
    return plan_id if isinstance(plan_id, str) and plan_id else None


def _plan_ids_in_path(path: str) -> list[str]:
    """Conservative plan-id candidates in a recorded path, in path order
    (see regex) — the directory's token comes first, filename junk never
    survives the letter/dash/length filter."""
    out: list[str] = []
    for match in _PLAN_ID_CANDIDATE_RE.finditer(path):
        token = match.group(1)
        if "-" in token and len(token) >= 5 and token not in out:
            out.append(token)
    return out


def _path_has_id(path: str, plan_id: str) -> bool:
    """True when any path segment carries ``plan_id`` in a delimited form.

    Covers the bare and pointing-suffixed shapes (``_{id}_`` / ``[{id}]`` /
    ``({id})`` and ``_{id}-N_``); the trailing "-" keeps ``plan-x`` out of
    ``plan-xy`` exactly as progress._light_target_matches requires.
    """
    for component in path.replace("\\", "/").split("/"):
        if not component:
            continue
        for form in (
            f"_{plan_id}_",
            f"[{plan_id}]",
            f"({plan_id})",
            f"_{plan_id}-",
            f"[{plan_id}-",
            f"({plan_id}-",
        ):
            if form in component:
                return True
    return False


def _plan_files(plan_dir: Path) -> list[Path]:
    """All ``*.json`` files under ``plan_dir``, recursively, shallowest first.

    Plans may live in subfolders (``plans/``, ``archive/``, per-month dirs),
    so discovery recurses — but only through visible, ordinary directories
    (the ``_walk_visible`` prune: hidden dirs like ``.opencode``/``.venv``
    and ``node_modules``/``__pycache__`` are never entered; on this project
    that prunes 476 of 500 JSON files, leaving exactly the observation
    plans). Directory symlinks are NOT followed (os.walk default), so a
    symlink cycle can never hang an unattended audit.

    Results are sorted (depth, name): if two files carry the same plan id —
    an archived copy next to the live plan — the top-level canonical file
    wins the claiming pass deterministically instead of whatever plain
    ``sorted()`` would pick alphabetically (where ``archive/`` sorts before
    ``m47.json``).
    """
    found: list[Path] = []
    for directory, filenames in _walk_visible(plan_dir):
        found.extend(directory / name for name in filenames if name.endswith(".json"))
    found.sort(key=lambda p: (len(p.relative_to(plan_dir).parts), p.name))
    return found


async def _load_project_plans(
    plan_dir: Path, selected: list[str] | None = None
) -> tuple[list[tuple[Path, ObservationPlan]], list[dict[str, str]]]:
    """Load plan files from ``plan_dir`` (or the ``selected`` paths).

    Returns ``(path, plan)`` pairs so callers like ``list_plans`` can report
    the file behind each plan. A broken/unparseable plan must not abort a
    whole-project sweep, so every failure is collected into ``errors`` as
    ``{file, reason}`` and the rest keep going — the audit reports partial
    truth rather than none. Default discovery walks ``plan_dir`` recursively
    through unpruned directories only (see ``_plan_files``).
    """
    if selected:
        files = []
        for entry in selected:
            path = Path(entry)
            if not path.is_absolute():
                path = plan_dir / path
            files.append(path)
    else:
        files = _plan_files(plan_dir)
    plans: list[tuple[Path, ObservationPlan]] = []
    errors: list[dict[str, str]] = []
    for file in files:
        try:
            plans.append((file, await plan_utils.load_plan(str(file))))
        except Exception as exc:  # noqa: BLE001 - one bad file, whole sweep lives
            errors.append({"file": file.name, "reason": str(exc)})
    return plans, errors


def _plan_type_totals(
    progress: dict[str, list[dict[str, Any]]], image_type: str
) -> dict[str, Any]:
    """Sum one frame type's plan-progress groups into expected/acquired/remaining.

    ``plan_progress`` returns a *list* of exposure groups per type (one per
    filter/exposure combination); lights vary per pointing, calibration is
    plan-level. ``window_open``/``window_end`` are identical across a type's
    groups, so the first group carries them.
    """
    groups = progress.get(image_type) or []
    return {
        "expected": sum(int(g.get("total_count", 0)) for g in groups),
        "attributed": sum(int(g.get("acquired_count", 0)) for g in groups),
        "remaining": sum(int(g.get("remaining_count", 0)) for g in groups),
        "window_open": groups[0].get("window_open") if groups else None,
        "window_end": groups[0].get("window_end") if groups else None,
    }


def _count_fits(token_dir: Path) -> dict[str, int]:
    """Count frame files under ``token_dir``, bucketed by parent directory name.

    Layout (verified): ``<token_dir>/<YYYY-MM-DD>/<TYPE>/<file>.fits.fz`` —
    the immediate parent directory name is the image type in the normal case.
    Counting by parent name at any depth tolerates N.I.N.A. writing extra
    date folders or skipping them, and the ``.fits.fz``/``.fits`` suffix
    filter keeps the ImageMetaData/AcquisitionDetails sidecars out.

    The parent name is **not** gated on the LIGHT/DARK/BIAS/FLAT list. A
    frame whose folder is called anything else is still a frame, and
    silently dropping it is what made ``orphans[].frames`` read ``0`` for a
    directory that plainly held fits files (an "unclassified, no fits
    images" reading of a real disk). Every fits file under ``token_dir``
    lands in *some* bucket, keyed by its parent directory lowercased, so the
    sum is always the truth about what is on disk. Hidden dirs
    (a share's ``.snapshot``/``.Trashes`` copies) are never walked, so
    copies can't inflate the count.

    N.I.N.A. marks a rejected frame by prefixing its filename with ``BAD_``
    (verified against the live landing dir: ``2026-10-22_04-46-37_L_...fits.fz``
    -> ``BAD_2026-10-22_04-46-37_L_...fits.fz``). Those count under
    ``bad_<parent>`` instead of ``<parent>``, so the light row's ``bad``
    (which reads ``bad_light``) still reports rejects without a rejected
    frame ever landing in the plain ``light`` bucket. The CSV row points at
    the pre-rename name, so the frame is dropped from attribution anyway —
    ``bad`` is what names the cause of that ``remaining`` deficit.
    """
    counts: dict[str, int] = {}
    for directory, filenames in _walk_visible(token_dir):
        # The immediate parent of the frames IS the directory. No TYPE
        # allow-list here: an unexpected folder name must be counted and
        # reported under its own name, never skipped.
        key = directory.name.lower()
        for name in filenames:
            lower = name.lower()
            if not lower.endswith(_FITS_SUFFIXES):
                continue
            bucket = f"bad_{key}" if lower.startswith("bad_") else key
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _own_token_rows(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    image_type: str,
    *,
    pointing_index: int,
) -> list[dict[str, Any]]:
    """Rows of ``image_type`` whose recorded path carries this plan's token.

    Deliberately *not* ``filter_metadata_rows``: that applies the pooled
    calibration matchers (type+filter / type+exposure / window), which
    exclude exactly the rows junk detection cares about — a 0.00003s dark
    never matches a 60s plan group, so scanning only matched rows would make
    junk invisible. Token containment (the same delimited ``_plan-..._``
    shape attribution uses) plus the row's own image_type is the right
    scope for "this plan's frame files".
    """
    type_upper = image_type.upper()
    if image_type == "light":
        ids = {
            plan.effective_plan_id(pointing_index),
            plan.per_pointing_plan_id(pointing_index),
        }
    else:
        ids = {plan._base_plan_id()}
        ids.update(plan.effective_plan_id(i) for i in range(1, len(plan.pointings) + 1))
    tokens = [f"_{pid}_" for pid in ids]
    matched: list[dict[str, Any]] = []
    for row in rows:
        if (row.get("image_type") or "").upper() != type_upper:
            continue
        path = (row.get("file_path") or "").strip()
        if not path:
            continue
        components = [part for part in path.replace("\\", "/").split("/") if part]
        if any(tok in comp for tok in tokens for comp in components):
            matched.append(row)
    return matched


def _junk_count(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    image_type: str,
    *,
    pointing_index: int,
) -> int:
    """Count degenerate frames among this plan's own token rows.

    Campaign precedent: a default Smart Exposure produced DARK frames at
    0.00003s inside the plan's landing dir. Junk = duration below
    ``max(0.01, half of the plan's smallest exposure for this type)``. Bias
    is exempt (0s is *correct* for bias) and flat is exempt (SkyFlat
    legitimately adapts from 0.01s). Frames failing the duration parse are
    not counted — the CSV contract can change under us and a parse quirk
    must never invent junk.

    Scope trade-off: a junk *dark* is excluded from ``attributed`` by the
    ±0.05s exposure matcher, so such a plan surfaces as MISSING_FILES (those
    frames genuinely should be re-shot) with ``junk`` naming the cause.
    SUSPECT_JUNK fires when attribution is satisfied while junk rides along —
    lights match on token alone, so junk lights *do* count.
    """
    if image_type not in ("light", "dark"):
        return 0

    # The plan model is pydantic: nested frame groups may surface as models,
    # so read exposure values through a dict-or-attribute accessor instead of
    # assuming plain dicts.
    def _exposure_of(group: Any) -> Any:
        if isinstance(group, dict):
            return group.get("exposure_time_seconds")
        return getattr(group, "exposure_time_seconds", None)

    frame_groups = getattr(plan, f"{image_type}_frames", None) or []
    exposures = [
        float(value) for value in map(_exposure_of, frame_groups) if value is not None
    ]
    floor = max(0.01, 0.5 * min(exposures)) if exposures else 0.01
    junk = 0
    for row in _own_token_rows(plan, rows, image_type, pointing_index=pointing_index):
        try:
            # str() first: an absent duration arrives as None, and
            # float(None) is a TypeError the ValueError handler would miss.
            duration = float(str(row.get("duration")))
        except ValueError:
            continue
        if duration < floor:
            junk += 1
    return junk


def _status_for(
    *,
    image_type: str,
    expected: int,
    own_rows: int,
    attributed: int,
    remaining: int,
    window_open: bool | None,
    junk: int,
) -> str:
    """First-match-wins audit verdict (see audit_plans' docstring, §3 table).

    Order matters: the window-closed deficit (EXPIRED) must win over the
    ``remaining == 0`` contract that would otherwise read it as done, and
    NEVER_SHOT (zero light attributions) precedes MISSING_FILES so an
    unattempted pointing is named as such rather than as an ordinary
    deficit.

    ``own_rows`` is the count of *this* type's ImageMetaData.csv rows whose
    recorded path carries this plan's token — the CSV-side replacement for
    the old on-disk count (see ``_own_token_rows``). It is consulted only
    for calibration, where it separates "this plan shot its own frames" from
    "the plan-wide pool satisfied this plan".
    """
    is_calibration = image_type != "light"
    if is_calibration and window_open is False and attributed < expected:
        return "EXPIRED"
    if not is_calibration and expected > 0 and attributed == 0:
        # Never attempted, which is a different fact from a partial
        # deficit: MISSING_FILES means frames were shot and some did not
        # count (rejects, truncation, junk), while here NOTHING was ever
        # attributed to this pointing — no run ever delivered a frame, so
        # the whole ask is untried work, exactly like a whole never-shot
        # plan. Identified from the light attribution count itself, never
        # from remaining_count (which is just expected - attributed and
        # cannot tell the two cases apart).
        return "NEVER_SHOT"
    if remaining > 0:
        return "MISSING_FILES"
    if junk > 0:
        return "SUSPECT_JUNK"
    if is_calibration and attributed >= expected and own_rows < expected:
        # Satisfied by the plan-wide calibration pool: enough frames exist
        # plan-wide, but this plan's own recorded frames fall short of what
        # it asks for — they were shot under a different plan's token.
        return "POOL_CREDITED"
    if not is_calibration and attributed > expected:
        return "OVERSHOT"  # full-mode re-acquisition extras; harmless
    return "OK"


def _audit_row(
    *,
    plan: ObservationPlan,
    image_type: str,
    expected: int,
    own_rows: int,
    attributed: int,
    remaining: int,
    window_open: bool | None,
    window_end: str | None,
    junk: int,
    bad: int,
    pointing_index: int | None,
) -> dict[str, Any]:
    status = _status_for(
        image_type=image_type,
        expected=expected,
        own_rows=own_rows,
        attributed=attributed,
        remaining=remaining,
        window_open=window_open,
        junk=junk,
    )
    # Lights carry the pointing-specific id (base for single-pointing plans,
    # base-N for a mosaic pane); calibration is plan-level and always uses
    # the bare base id — its landing dir shares the base token.
    effective = (
        plan.effective_plan_id(pointing_index or 1)
        if image_type == "light"
        else plan._base_plan_id()
    )
    row: dict[str, Any] = {
        "plan_id": plan._base_plan_id(),
        "effective_plan_id": effective,
        "target": plan.target,
        "intent": plan.intent,
        "pointing_index": pointing_index,
        "type": image_type,
        "expected": expected,
        "attributed": attributed,
        "remaining": remaining,
        "junk": junk,
        "status": status,
    }
    if image_type == "light":
        # The pointing's label — mosaic pane name ("r2_c3", "Pane 2") or a
        # single-pointing subtitle ("M47 center") — is the human handle for
        # what pointing_index/effective_plan_id express only numerically,
        # and it is the middle segment of the landing dir name
        # ("<target> - <label> _<token>_"). Light-only, like `bad`:  calib
        # rows are plan-level and have no pointing to label. Normalized to
        # "" when unset, the same contract as `intent`.
        row["label"] = plan.pointings[(pointing_index or 1) - 1].label or ""
        # Frames deliberately rejected via N.I.N.A.'s BAD_ filename prefix.
        # The CSV row still names the pre-rename file, so a rejected frame
        # never reaches `attributed`; `bad` reports how many were rejected,
        # which explains a remaining deficit without implying a lost frame.
        row["bad"] = bad
    if image_type != "light":
        row["window_open"] = window_open
        row["window_end"] = window_end
        # Rows of this type recorded under THIS plan's own token — the
        # evidence behind a POOL_CREDITED verdict, and the CSV-side stand-in
        # for the removed on-disk count. Calibration-only, like the window
        # fields: for lights, `attributed` already requires the token.
        row["own_rows"] = own_rows
    return row


def _action_for(image_type: str) -> str:
    return {
        "light": "load_sequence light for this plan",
        "flat": "load_sequence flat (dawn or dusk) for this plan",
        "dark": "load_sequence dark for this plan",
        "bias": "load_sequence bias for this plan",
    }[image_type]


def _written_iso(path: Path) -> str | None:
    """File mtime as ISO-8601 local with offset — same shape as list_plans."""
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime)
            .astimezone()
            .isoformat(timespec="seconds")
        )
    except OSError:
        return None


def _light_expected_per_pointing(plan: ObservationPlan) -> int:
    """Light frames ONE pointing asks for: the sum of its groups' total_count.

    A mosaic's light groups are plan-level but each pointing acquires them
    independently, so the plan's whole light ask is this times the pointing
    count (what a `load_sequence light` per pointing resolves to).
    """

    def _count_of(group: Any) -> int:
        value = (
            group.get("total_count")
            if isinstance(group, dict)
            else getattr(group, "total_count", 0)
        )
        return int(value or 0)

    return sum(_count_of(group) for group in (plan.light_frames or []))


def _never_shot_report(
    pairs: list[tuple[Path, ObservationPlan]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Report the loadable plans no recorded frame references.

    Discovery is a LEFT JOIN driven by ImageMetaData.csv, so a plan that was
    written but never shot has nothing to join on: it gets no rows, and by
    construction it used to vanish from the audit entirely. That is a real
    blind spot for the work-list question this tool answers — on Oct 24 the
    audit showed two 1-frame deficits while six whole unshot plans (240
    frames) were only found by manually diffing list_plans against the
    audited id set. The frames are as missing as any MISSING_FILES deficit;
    they are just missing all of them.

    So these plans are reported here (plan-level, and matched on the base
    plan id: a twin file of an already-shot plan is NOT never-shot, and two
    unshot copies of one plan report once, from the shallowest file) and as
    ``left_to_do`` entries with ``reason="NEVER_SHOT"`` and ``missing`` = the
    full light ask — nothing else in the audit can cover that work. They
    still get no ``rows``: expected-vs-attributed is meaningless
    for frames that were never attempted, and a row would read as "0 of 40
    captured, 40 missing", drowning the real per-plan deficits.

    Returns ``(never_shot, left_to_do)``; the todo entries carry one
    pointing each (mosaic panes load one at a time, exactly like the light
    rows they mirror).
    """
    entries: list[dict[str, Any]] = []
    todos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, plan in pairs:
        base = plan._base_plan_id()
        if base in seen:
            continue
        seen.add(base)
        per_pointing = _light_expected_per_pointing(plan)
        multi = len(plan.pointings) > 1
        entries.append(
            {
                "plan_id": base,
                "file": path.name,
                "target": plan.target,
                "intent": plan.intent,
                "pointing_count": len(plan.pointings),
                "light_expected": per_pointing * len(plan.pointings),
                "written": _written_iso(path),
            }
        )
        for i in range(1, len(plan.pointings) + 1):
            todos.append(
                {
                    "plan_id": base,
                    "target": plan.target,
                    "intent": plan.intent,
                    "pointing_index": i if multi else None,
                    "type": "light",
                    "missing": per_pointing,
                    "reason": "NEVER_SHOT",
                    "action": _action_for("light"),
                    # Same normalization as the light rows: the pointing's
                    # label is the human handle, "" when unset.
                    "label": plan.pointings[i - 1].label or "",
                }
            )
    return entries, todos


@mcp.tool()
async def audit_plans(
    plans: list[str] | None = None,
    project_dir: str | None = None,
    types: list[str] | None = None,
    include_orphans: bool = True,
) -> dict[str, Any]:
    """Audits every observation plan at once: are all captures saved to disk, and what is left to do?

    One call replaces per-plan get_plan_progress sweeps. Discovery is a LEFT
    JOIN of ImageMetaData.csv onto the project's plan files: every plan id
    recorded in a **light** frame's file path drives the audit, and the plan
    files in ``project_dir`` (or exactly the ``plans`` paths/names given)
    supply the definitions. Calibration rows deliberately do NOT join:
    calibration is pool-credited and lands under the shared base token, so
    letting a dark/flat/bias row reference a plan would hide plans that shot
    calibration but never a single light — never-shot means no light was
    ever attributed, judged from light attributions themselves, not from
    remaining counts. Plan-file discovery is recursive but pruned — directories
    starting with "." or named ``node_modules``/``__pycache__`` are never
    searched (see ``_plan_files``), and files order shallowest-first so a
    top-level plan wins any id tie against an archived copy. Unrelated JSON
    such as config files is ignored entirely and never becomes an error
    (use list_plans to enumerate plan files themselves). A plan whose id no
    recorded frame references — a never-shot plan — is NOT audited (no
    ``rows``) but is reported: see ``never_shot`` below. Conversely,
    frames whose recorded plan id no loadable plan file claims
    are reported under ``errors`` (a plan file was deleted or is corrupt).
    For each joined plan and each requested frame type it reconciles
    TWO truth sources and reports them side by side per row:

    - ``expected`` — the plan's total_count (per pointing for lights,
      plan-level for calibration, summed over the type's exposure groups).
    - ``attributed`` / ``remaining`` — exactly what get_plan_progress
      reports: lights match the underscore token in the recorded path AND the
      exposure duration (±0.05s, so degenerate 0s frames never count);
      calibration is POOLED plan-wide (flats by type+filter, darks by
      type+exposure, bias by type) inside ± calibration_window_days of the
      earliest attributed light. While a calibration window is closed,
      remaining is reported as 0 even when a true deficit exists, so this
      audit surfaces that case explicitly as EXPIRED.

    Both come from ImageMetaData.csv. The filesystem is consulted for exactly
    one thing the CSV cannot know — frames N.I.N.A. rejected via its ``BAD_``
    filename prefix (``2026-10-22_04-46-37_L_...fits.fz`` ->
    ``BAD_2026-10-22_04-46-37_L_...fits.fz``): the CSV row still names the
    pre-rename file, so a reject never reaches ``attributed``, and the light
    row's ``bad`` count reports how many were rejected. Calibration rows carry
    ``own_rows`` — how many of this type's recorded frames sit under THIS
    plan's own token rather than the shared pool. Hidden directories are
    never walked anywhere in this tool — neither the CSV reads nor the disk
    scans — so a share's ``.snapshot``/``.Trashes`` copies can neither
    double-count rows nor inflate counts.

    Because calibration is pooled, ``attributed`` can legitimately exceed a
    plan's own ``own_rows`` (one bias set credits every plan) — that is
    POOL_CREDITED, informational, not a gap. There is deliberately no
    disk-vs-CSV cross-check any more: a frame the Session Metadata plugin
    never recorded simply does not appear in either source, so it surfaces
    as an ordinary ``remaining`` deficit. Re-shooting is safe in that case,
    which is all this audit ever advised.

    ``status`` per row (first match wins): EXPIRED (window closed with an
    uncredited deficit — needs a new plan/window, shooting cannot fix it) →
    NEVER_SHOT (a light row with zero attributed frames — no run ever
    delivered a frame for this pointing, so the whole ask is untried work;
    like a whole never-shot plan it is listed in ``left_to_do`` with
    ``reason: "NEVER_SHOT"``) → MISSING_FILES (remaining > 0 — shoot more;
    ``missing`` equals ``remaining``) → SUSPECT_JUNK (light/dark rows whose duration is under
    half the plan's exposure or 0.01s — e.g. 0.00003s Smart Exposure junk —
    riding alongside a set whose attribution is otherwise complete; bias and
    flats exempt; when junk instead leaves a deficit the status is
    MISSING_FILES with the junk count as the cause) → POOL_CREDITED (calibration satisfied from the
    pool while this plan's own recorded frames fall short) → OVERSHOT (light
    attributed above expected from full-mode re-runs; harmless) → OK.

    Top level: ``all_saved`` (true iff no row is MISSING_FILES,
    EXPIRED, NEVER_SHOT or SUSPECT_JUNK, no error was recorded, and
    ``never_shot`` is empty), ``totals`` (including ``never_shot``, the
    number of unshot plan files),
    ``left_to_do`` (ONLY what a sequence run can fix: MISSING_FILES and
    row-level NEVER_SHOT rows with missing = remaining, plus one NEVER_SHOT
    entry per unshot pointing of a wholly unshot plan with missing = that
    pointing's whole light ask),
    ``blocked`` (EXPIRED — needs plan/window changes),
    ``never_shot`` (loadable plan files no recorded frame references —
    matched on plan id, so a rewritten/archived twin of an already-shot
    plan is not never-shot: its frames exist under the shared id. Written
    but never imaged, and the audit's dual of ``orphans``, which is frames
    with no plan file. Plan-level entries carrying ``plan_id``,
    ``file``, ``target``, ``intent``, ``pointing_count``, ``light_expected``
    and ``written``; deduped by plan id, shallowest-first. Without these an
    operator reading only ``left_to_do`` sees a 2-frame deficit where 240
    frames of never-shot targets also wait — the join's blind spot, since a
    plan with zero frames has no CSV row to join on), ``rows`` (every
    plan × type, plus per-pointing light rows for mosaics; single-pointing
    plans carry pointing_index null), ``orphans`` (landing token directories
    matching no current plan — from deleted plans), ``errors`` (light
    frames whose recorded plan id no loadable plan file claims, plus plan
    files that fail to load *while the CSV references their id*; a load
    failure nothing references is not an error — never fatal to the sweep),
    and ``root`` (the imaging directory actually scanned).

    Every row, ``left_to_do``, and ``blocked`` entry carries the plan's
    ``target`` and ``intent`` (the plan's 2–3 word observation goal), so a
    consumer can tell two plans for the same target apart — or distinguish
    an acquisition from, say, a calibration session — without loading the
    plan files itself. Empty string when a plan leaves ``intent`` unset.
    (``never_shot`` entries carry both fields too.)
    Light rows (and light ``left_to_do`` entries, MISSING_FILES and
    NEVER_SHOT alike) additionally carry the
    pointing's ``label`` — a mosaic pane name like ``r2_c3``/``Pane 2``, or
    a single-pointing subtitle like ``M47 center`` — the human handle for
    what ``pointing_index``/``effective_plan_id`` express numerically, and
    the middle segment of the landing directory name. Calibration rows
    carry no ``label`` (plan-level, like their null pointing_index); it is
    the empty string when a pointing's label is unset.

    A missing/unmounted imaging root RAISES (check the site profile's image
    save path and, on non-Windows hosts, NINA_DRIVE_MOUNT) rather than
    reporting empty counts — "no frames" and "no mount" must never look
    alike. ``types`` (case-insensitive) subsets the output rows; ``plans``
    entries resolve against ``project_dir`` (default: the server's project
    directory) and bypass CSV discovery — exactly those files are audited
    even if the CSV never mentions them, and any load failure among them is
    an error. Naming a file IS the request to audit it, so explicit mode
    never populates ``never_shot``: an unshot plan named there audits as an
    ordinary row instead — status NEVER_SHOT, listed in ``left_to_do``.
    Read-only; makes no N.I.N.A. calls beyond the site profile.
    """
    plan_dir = Path(project_dir) if project_dir else config.PROJECT_DIR
    if not plan_dir.is_absolute():
        plan_dir = config.PROJECT_DIR / plan_dir

    wanted = tuple(t.lower() for t in (types or _AUDIT_TYPES))
    unknown = [t for t in wanted if t not in _AUDIT_TYPES]
    if unknown:
        raise ValueError(
            f"unknown types {unknown}: valid types are {', '.join(_AUDIT_TYPES)}"
        )

    now = datetime.now(UTC).replace(microsecond=0)
    explicit = bool(plans)
    # all_loaded: every loadable plan file (drives disk/orphan accounting —
    # a plan file's landing dirs are not orphans just because the plan was
    # never shot). audited: the plans rows are built for. errors: load
    # failures that matter + light frames no plan claims (see below).
    all_loaded: list[tuple[Path, ObservationPlan]] = []
    audited: list[tuple[Path, ObservationPlan]] = []
    # Loadable plan files no recorded frame references — the join's blind
    # spot, reported under `never_shot` + `left_to_do` (see
    # _never_shot_report). Empty in explicit mode, where naming a file IS
    # the request to audit it.
    unreferenced: list[tuple[Path, ObservationPlan]] = []
    errors: list[dict[str, Any]] = []
    root: Path | None = None
    rows: list[dict[str, Any]] = []

    if explicit:
        # Caller named exact files: audit exactly those (even never-shot —
        # that is the point of naming one) and every load failure is an
        # error — no discovery pass gets to soften an explicit request.
        all_loaded, errors = await _load_project_plans(plan_dir, plans)
        audited = all_loaded
        if audited:
            # read_metadata validates the imaging root (_require_root)
            # before any counting: a dead mount must raise here, not
            # surface as zero frames.
            root = await metadata.resolve_imaging_root()
            rows = await metadata.read_metadata()
    elif not _plan_files(plan_dir):
        # Nothing to join against: report before touching the imaging root,
        # so an empty project dir stays NINA-free (root: null below).
        pass
    else:
        # LEFT JOIN — ImageMetaData.csv's recorded plan ids drive, plan
        # files are the joined side:
        #   * CSV references a plan id + plan file loads   -> audited;
        #   * CSV references a plan id + no loadable file  -> error (gap);
        #   * plan file loads + CSV never mentions it      -> not audited
        #     (never-shot plan, reported under `never_shot`/`left_to_do`;
        #     or unrelated JSON such as config files — list_plans still
        #     reports unparseable files).
        root = await metadata.resolve_imaging_root()
        rows = await metadata.read_metadata()
        path_rows = [r for r in rows if (r.get("file_path") or "").strip()]
        all_loaded, load_errors = await _load_project_plans(plan_dir)

        # One pass: which plan (if any) claims each recorded row. Matching
        # reuses progress._light_target_matches, so every token form the
        # attribution logic accepts (underscore/bracket/paren, per-pointing,
        # single-pointing legacy) counts as a reference here too.
        #
        # LIGHT rows only decide whether a plan was referenced. Calibration
        # is pool-credited and lands under the shared `_{base}_` token, so a
        # plan whose id appears only on flat/dark/bias rows has never had a
        # single light acquired — it IS never-shot, and letting calibration
        # rows join it into `audited` hid exactly the blind spot this report
        # exists to expose (written but never imaged, discovered only by
        # manually diffing list_plans against the audited id set).
        claimed_by: list[int | None] = []
        for row in path_rows:
            hit: int | None = None
            if (
                row.get("image_type") or row.get("frame_type") or ""
            ).upper() == "LIGHT":
                for idx, (_path, plan) in enumerate(all_loaded):
                    if any(
                        _light_target_matches(plan, row, pointing_index=i)
                        for i in range(1, len(plan.pointings) + 1)
                    ):
                        hit = idx
                        break
            claimed_by.append(hit)
        referenced = {i for i in claimed_by if i is not None}
        audited = [pair for idx, pair in enumerate(all_loaded) if idx in referenced]
        # The same pass's other side: plans no LIGHT row claims — a real
        # plan with zero lights (see _never_shot_report). ID-level, not
        # file-level: two files may share one plan id (a rewritten plan, an
        # archived copy) and only the shallowest claims the rows, but their
        # frames DO exist — reporting the twin as never-shot would
        # double-count one target's work (live: NGC 1931's Oct-24 rewrite
        # beside its Oct-23 original).
        claimed_ids = {all_loaded[i][1]._base_plan_id() for i in referenced}
        unreferenced = [
            pair for pair in all_loaded if pair[1]._base_plan_id() not in claimed_ids
        ]

        # A load failure is an error only when recorded frames reference
        # its plan id AND no loadable file provides that id — an
        # unreadable, never-shot file is nobody's problem yet (junk JSON
        # rarely carries an id at all), and a corrupt duplicate whose id a
        # valid twin still serves needs no verdict either.
        failed_ids: set[str] = set()
        loadable_ids = {plan._base_plan_id() for _f, plan in all_loaded}
        for err in load_errors:
            plan_id = _probe_plan_id(plan_dir / err["file"])
            if (
                plan_id
                and plan_id not in loadable_ids
                and any(
                    _path_has_id((r.get("file_path") or ""), plan_id) for r in path_rows
                )
            ):
                failed_ids.add(plan_id)
                errors.append(
                    {
                        "file": err["file"],
                        "plan_id": plan_id,
                        "reason": err["reason"],
                    }
                )

        # The left side of the join: light frames whose plan id no
        # loadable file claims. Light only — calibration pools across
        # plans regardless of whose dir it was shot in, so unowned
        # calibration rows are normal, not a gap.
        gaps: dict[str | None, int] = {}
        for i, row in enumerate(path_rows):
            if claimed_by[i] is not None:
                continue
            if (row.get("image_type") or "").upper() != "LIGHT":
                continue
            path = row.get("file_path") or ""
            if any(_path_has_id(path, fid) for fid in failed_ids):
                continue  # already reported as that file's load failure
            candidates = _plan_ids_in_path(path)
            key = candidates[0] if candidates else None
            gaps[key] = gaps.get(key, 0) + 1
        for key, count in gaps.items():
            if key:
                reason = (
                    f"{count} light frames in ImageMetaData.csv reference "
                    f"plan-id {key!r}, but no loadable plan file in "
                    f"{plan_dir} claims them (deleted or corrupt plan file?)"
                )
            else:
                reason = (
                    f"{count} light frames in ImageMetaData.csv carry no "
                    f"plan id and match no plan file in {plan_dir}"
                )
            errors.append({"file": None, "plan_id": key, "reason": reason})

    never_shot, never_shot_todos = _never_shot_report(unreferenced)

    if not audited:
        res: dict[str, Any] = {
            "all_saved": not errors and not never_shot,
            "totals": {
                "expected": 0,
                "attributed": 0,
                "plans": 0,
                "plan_errors": len(errors),
                "never_shot": len(never_shot),
            },
            "left_to_do": never_shot_todos,
            "blocked": [],
            "never_shot": never_shot,
            "rows": [],
            "orphans": [],
            "errors": errors,
            "root": str(root) if root is not None else None,
        }
        if errors:
            res["warning"] = f"no plan could be loaded from {plan_dir}"
        elif explicit or not all_loaded:
            res["warning"] = f"no plan files in {plan_dir}"
        else:
            res["warning"] = (
                "no captured frames in ImageMetaData.csv reference any "
                f"plan file in {plan_dir} — every plan file is unshot; "
                "they are listed under never_shot / left_to_do"
            )
        client.log_payload("Audit:", json.dumps(res, indent=2))
        return res

    # Every branch above resolves root before it can load a plan, so an
    # audited result always has one; this both documents that invariant
    # and narrows the type for mypy before the disk scan.
    assert root is not None

    # One filesystem pass: bucket each landing token directory by its
    # trailing token, longest-first so pane tokens (base-N) win over base.
    # What remains of this pass is the `bad` reject count (a filesystem
    # fact: N.I.N.A. renames the file, the CSV keeps the old name) and
    # orphan detection — everything else about a row now comes from the
    # plan and ImageMetaData.csv, not from how the folders are named.
    known: dict[str, tuple[str, int | None]] = {}
    for _path, plan in all_loaded:
        base = plan._base_plan_id()
        multi = len(plan.pointings) > 1
        for i in range(1, len(plan.pointings) + 1):
            known[f"_{plan.effective_plan_id(i)}_"] = (
                base,
                i if multi else None,
            )
        # Calibration is plan-level: it may share the base token dir (and,
        # for mosaics, the pane dirs' TYPE folders are still counted per
        # pane above; the base token below covers plan-level calibration).
        known.setdefault(f"_{base}_", (base, None))
    tokens_longest_first = sorted(known, key=len, reverse=True)

    bad_files: dict[tuple[str, int | None], int] = {}
    unclaimed: list[Path] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or _prunable_dir(entry.name):
            continue
        matched_key = None
        for token in tokens_longest_first:
            if entry.name.endswith(token):
                matched_key = known[token]
                break
        if matched_key is None:
            # Pruned walk: a share's .snapshot/.Trashes copy is never an
            # orphan candidate — it is a copy, not frames nobody claims.
            if any(
                name.lower().endswith(_FITS_SUFFIXES)
                for _dir, names in _walk_visible(entry)
                for name in names
            ):
                unclaimed.append(entry)
            continue
        # Only the `bad_light` bucket is read below: `bad` is a light-row
        # field, and it is the one fact about this plan's own files that
        # ImageMetaData.csv cannot carry — the row still points at the
        # pre-rename name. Calibration rejects are not reported anywhere
        # (no field asks for them), so they are deliberately not summed in.
        count = _count_fits(entry).get("bad_light", 0)
        if count:
            bad_files[matched_key] = bad_files.get(matched_key, 0) + count

    rows_out: list[dict[str, Any]] = []
    left_to_do: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for _path, plan in audited:
        base = plan._base_plan_id()
        multi = len(plan.pointings) > 1
        for i in range(1, len(plan.pointings) + 1):
            progress = plan_progress(plan, rows, pointing_index=i, now=now)
            pointing = i if multi else None

            totals = _plan_type_totals(progress, "light")
            light_bad = bad_files.get((base, pointing), 0)
            junk = _junk_count(plan, rows, "light", pointing_index=i)
            row = _audit_row(
                plan=plan,
                image_type="light",
                expected=totals["expected"],
                # Unused for lights: `_status_for` consults own_rows only for
                # calibration, and `_audit_row` emits it only there.
                own_rows=0,
                attributed=totals["attributed"],
                remaining=totals["remaining"],
                window_open=totals["window_open"],
                window_end=totals["window_end"],
                junk=junk,
                bad=light_bad,
                pointing_index=pointing,
            )
            rows_out.append(row)

        # Calibration is plan-level: derive it once (pointing 1 anchors the
        # session window). `own_rows` counts this plan's own recorded frames
        # of that type, which is what separates a pool credit from a plan
        # that shot its own calibration.
        if len(plan.pointings) >= 1:
            progress = plan_progress(plan, rows, pointing_index=1, now=now)
            for image_type in ("flat", "dark", "bias"):
                if image_type not in wanted:
                    continue
                totals = _plan_type_totals(progress, image_type)
                own_rows = len(
                    _own_token_rows(plan, rows, image_type, pointing_index=1)
                )
                junk = _junk_count(plan, rows, image_type, pointing_index=1)
                row = _audit_row(
                    plan=plan,
                    image_type=image_type,
                    expected=totals["expected"],
                    own_rows=own_rows,
                    attributed=totals["attributed"],
                    remaining=totals["remaining"],
                    window_open=totals["window_open"],
                    window_end=totals["window_end"],
                    junk=junk,
                    bad=0,
                    pointing_index=None,
                )
                rows_out.append(row)

    if "light" not in wanted:
        rows_out = [r for r in rows_out if r["type"] != "light"]

    for row in rows_out:
        status = row["status"]
        if status in ("MISSING_FILES", "NEVER_SHOT"):
            todo = {
                "plan_id": row["plan_id"],
                "target": row["target"],
                "intent": row["intent"],
                "pointing_index": row["pointing_index"],
                "type": row["type"],
                "missing": row["remaining"],
                # Distinguished so a consumer can tell an untried pointing
                # (nothing ever attributed) from a partial one (frames shot,
                # some uncounted) — the fix is the same sequence run either
                # way, but the operator's expectation differs.
                "reason": status,
                "action": _action_for(row["type"]),
            }
            # Light rows only (same rule as rows): calibration entries are
            # plan-level and have no pointing to label.
            if "label" in row:
                todo["label"] = row["label"]
            left_to_do.append(todo)
        elif status == "EXPIRED":
            blocked.append(
                {
                    "plan_id": row["plan_id"],
                    "target": row["target"],
                    "intent": row["intent"],
                    "type": row["type"],
                    "expected": row["expected"],
                    "attributed": row["attributed"],
                    "window_end": row["window_end"],
                    "reason": status,
                    "action": (
                        "raise calibration_window_days or write a new plan "
                        "to reopen the window"
                    ),
                }
            )

    orphans = []
    if include_orphans:
        for entry in unclaimed:
            counts = _count_fits(entry)
            orphans.append(
                {
                    "dir": entry.name,
                    "frames": sum(counts.values()),
                    "types": counts,
                    "note": "no plan file with this plan_id",
                }
            )

    broken = [r for r in rows_out if r["status"] in _AUDIT_BROKEN]
    # Per-plan deficits first (the shape callers have always seen), then the
    # plans nothing could ever have counted: a NEVER_SHOT entry's `missing`
    # is the whole light ask, and it is work a sequence run fixes exactly
    # like a MISSING_FILES row.
    left_to_do.extend(never_shot_todos)
    res = {
        # never_shot breaks the verdict on purpose: "are all captures saved"
        # is answered by the frames that exist, and these plans have none.
        # A plan file you never intend to shoot pins this false — delete it
        # or move it out of the project dir (unrelated JSON is already
        # ignored: it never loads as a plan).
        "all_saved": not broken and not errors and not never_shot,
        "totals": {
            "expected": sum(r["expected"] for r in rows_out),
            "attributed": sum(r["attributed"] for r in rows_out),
            "plans": len(audited),
            "plan_errors": len(errors),
            "never_shot": len(never_shot),
        },
        "left_to_do": left_to_do,
        "blocked": blocked,
        "never_shot": never_shot,
        "rows": rows_out,
        "orphans": orphans,
        "errors": errors,
        "root": str(root),
    }
    client.log_payload("Audit:", json.dumps(res, indent=2))
    return res


@mcp.tool()
async def list_plans(project_dir: str | None = None) -> dict[str, Any]:
    """Returns every observation plan file under the project directory with its identity, so an agent can enumerate plans without globbing.

    Discovery is recursive but pruned: any directory starting with "." or
    named ``node_modules``/``__pycache__`` is skipped (the project dir also
    holds the agent's own installation and its hundreds of package JSONs),
    and files are ordered shallowest-first so a top-level plan precedes an
    archived copy. Each entry carries ``file``, absolute ``path``,
    ``plan_id``, ``target``, ``intent``, ``pointing_count``, and ``written``
    (file mtime, ISO-8601 local with offset). Unparseable JSON files are
    reported per file under ``errors`` instead of failing the call.
    Read-only."""
    plan_dir = Path(project_dir) if project_dir else config.PROJECT_DIR
    if not plan_dir.is_absolute():
        plan_dir = config.PROJECT_DIR / plan_dir
    plans, errors = await _load_project_plans(plan_dir)
    entries = []
    for path, plan in plans:
        try:
            written: str | None = (
                datetime.fromtimestamp(path.stat().st_mtime)
                .astimezone()
                .isoformat(timespec="seconds")
            )
        except OSError:
            written = None
        entries.append(
            {
                "file": path.name,
                "path": str(path),
                "plan_id": plan._base_plan_id(),
                "target": plan.target,
                "intent": plan.intent,
                "pointing_count": len(plan.pointings),
                "written": written,
            }
        )
    res = {"plans": entries, "errors": errors}
    client.log_payload("Plans:", json.dumps(res, indent=2))
    return res


# sequence tools
