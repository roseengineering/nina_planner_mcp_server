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
    """Returns per-frame-type acquisition progress for every pointing of an observation-plan JSON file: for each exposure group, the total_count from the plan, the acquired_count attributed to that plan/pointing from the imaging metadata (ImageMetaData.csv), and the remaining_count. Lights are attributed via the underscore-delimited plan id embedded in the recorded file path (`_{base_plan_id}_` for a single-pointing plan, `_{base_plan_id}-{pointing_index}_` per pane for a mosaic; no target-name fallback); flats/darks/bias are matched by image type (plus filter for flats, plus exposure for darks) and are anchored to the plan's light session: they count only when shot within `calibration_window_days` of the session start (the earliest attributed light), on either side of it. Calibration groups therefore also carry `window_open` (bool) and `window_end` (the window's upper bound, rendered in the host's local time with its UTC offset): while the current time is inside the window, remaining_count is total minus acquired; once it falls outside, no frame shot now could ever be credited, so remaining_count is reported as 0 rather than an unsatisfiable deficit (acquired_count stays truthful either way, and lights carry neither field) — raise `calibration_window_days` or write a new plan to reopen the window. Pass max_hfr, min_detected_stars, and/or max_guiding_rms_arcsec to exclude light frames that fail quality thresholds (frames missing the relevant field are excluded when a threshold is set). Use this before load_sequence to decide what still needs acquiring. Returns `complete` (all panes done) plus a `pointings` list of per-pane progress and per-pane `complete`, so a worker can acquire panes sequentially (finish pane N, then run pane N+1). Light items also carry `surplus_count` (frames attributed beyond the plan's total_count), and the top level carries `overshot` (bool) and `warning` (string or null). These exist because acquired_count is cumulative across runs while total_count is a per-plan constant, so a mode=full re-shoot that delivered only some of its frames still reports `complete: true, remaining: 0` — the older frames keep satisfying the plan. When `overshot` is true, treat `complete` as "these frames exist", not as "the last run succeeded", and verify the latest run with audit_plans plus the per-date frame folders."""
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
    client.log_payload("Progress:", json.dumps(res, indent=2))
    return res


# capture audit


_AUDIT_TYPES = ("light", "flat", "dark", "bias")
# Landing TYPE folders are uppercase (LIGHT/DARK/BIAS/FLAT); compare in that
# casing at the filesystem boundary — mixing cases silently counts zero.
_AUDIT_TYPES_UPPER = tuple(t.upper() for t in _AUDIT_TYPES)
_FITS_SUFFIXES = (".fits", ".fits.fz")
# Statuses that mean "not everything is saved/counted right" — the
# `all_saved` verdict fails on any of them (see audit_plans' docstring).
_AUDIT_BROKEN = ("MISSING_FILES", "EXPIRED", "SUSPECT_JUNK")
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
    """Count frame files under ``token_dir`` by their TYPE parent directory.

    Layout (verified): ``<token_dir>/<YYYY-MM-DD>/<TYPE>/<file>.fits.fz`` —
    the immediate parent directory name is the image type. Counting by parent
    name at any depth tolerates N.I.N.A. writing extra date folders or
    skipping them, and the ``.fits.fz``/``.fits`` suffix filter keeps the
    ImageMetaData/AcquisitionDetails sidecars out.

    N.I.N.A. marks a rejected frame by prefixing its filename with ``BAD_``
    (verified against the live landing dir: ``2026-10-22_04-46-37_L_...fits.fz``
    -> ``BAD_2026-10-22_04-46-37_L_...fits.fz``). Those count under
    ``bad_<type>`` instead of ``<type>`` so a deliberate reject never inflates
    a type's on-disk total. The CSV row still points at the pre-rename name,
    so the frame is dropped from attribution anyway; this keeps it out of
    ``on_disk`` too, which is what stops it skewing the disk-vs-CSV picture.
    """
    counts: dict[str, int] = {}
    for directory, filenames in _walk_visible(token_dir):
        # The immediate parent of the frames IS the directory — same test
        # as before, but now hidden dirs (a share's .snapshot/.Trashes
        # copies) are never walked, so copies can't inflate the count.
        image_type = directory.name.upper()
        if image_type not in _AUDIT_TYPES_UPPER:
            continue
        # Compare in uppercase, store in lowercase: every consumer
        # (rows, orphans, disk buckets) speaks the lowercase type names.
        key = image_type.lower()
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
    on_disk: int,
    attributed: int,
    remaining: int,
    window_open: bool | None,
    junk: int,
) -> str:
    """First-match-wins audit verdict (see audit_plans' docstring, §3 table).

    Order matters: the window-closed deficit (EXPIRED) must win over the
    ``remaining == 0`` contract that would otherwise read it as done.
    """
    is_calibration = image_type != "light"
    if is_calibration and window_open is False and attributed < expected:
        return "EXPIRED"
    if remaining > 0:
        return "MISSING_FILES"
    if junk > 0:
        return "SUSPECT_JUNK"
    if is_calibration and attributed >= expected and on_disk < expected:
        # Satisfied by the plan-wide calibration pool, but this plan's own
        # landing dir holds fewer frames than the plan wants.
        return "POOL_CREDITED"
    if not is_calibration and attributed > expected:
        return "OVERSHOT"  # full-mode re-acquisition extras; harmless
    return "OK"


def _audit_row(
    *,
    plan: ObservationPlan,
    image_type: str,
    expected: int,
    on_disk: int,
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
        on_disk=on_disk,
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
        "on_disk": on_disk,
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
        # Excluded from on_disk above; reported so an operator can see how
        # many were rejected, which explains a remaining deficit without
        # implying a gap.
        row["bad"] = bad
    if image_type != "light":
        row["window_open"] = window_open
        row["window_end"] = window_end
    return row


def _action_for(image_type: str) -> str:
    return {
        "light": "load_sequence light for this plan",
        "flat": "load_sequence flat (dawn or dusk) for this plan",
        "dark": "load_sequence dark for this plan",
        "bias": "load_sequence bias for this plan",
    }[image_type]


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
    recorded in a frame's file path drives the audit, and the plan files in
    ``project_dir`` (or exactly the ``plans`` paths/names given) supply the
    definitions. Plan-file discovery is recursive but pruned — directories
    starting with "." or named ``node_modules``/``__pycache__`` are never
    searched (see ``_plan_files``), and files order shallowest-first so a
    top-level plan wins any id tie against an archived copy. A plan file
    the CSV never references — a never-shot plan,
    or unrelated JSON such as config files — is ignored entirely and never
    becomes an error (use list_plans to enumerate plan files themselves).
    Conversely, frames whose recorded plan id no loadable plan file claims
    are reported under ``errors`` (a plan file was deleted or is corrupt).
    For each joined plan and each requested frame type it reconciles
    THREE truth sources and reports them side by side per row:

    - ``expected`` — the plan's total_count (per pointing for lights,
      plan-level for calibration, summed over the type's exposure groups).
    - ``on_disk`` — frame files physically inside THIS plan's own landing
      directories (``<root>/<label> _<token>_/<date>/<TYPE>/*.fits.fz``,
      token = ``_{base_plan_id}_`` for single-pointing plans and
      calibration, ``_{base_plan_id}-{n}_`` per mosaic pane; counted by the
      TYPE-named parent directory at any depth, sidecar CSV/JSON excluded).
      Hidden directories are never walked anywhere in this tool — neither
      the CSV reads nor the disk scans — so a share's ``.snapshot``/
      ``.Trashes`` copies can neither double-count rows nor inflate counts.
      Frames N.I.N.A. rejected via its ``BAD_`` filename prefix
      (``2026-10-22_04-46-37_L_...fits.fz`` ->
      ``BAD_2026-10-22_04-46-37_L_...fits.fz``) are excluded
      here and reported separately as the light row's ``bad`` count.
    - ``attributed`` / ``remaining`` — exactly what get_plan_progress
      reports: lights match the underscore token in the recorded path AND the
      exposure duration (±0.05s, so degenerate 0s frames never count);
      calibration is POOLED plan-wide (flats by type+filter, darks by
      type+exposure, bias by type) inside ± calibration_window_days of the
      earliest attributed light. While a calibration window is closed,
      remaining is reported as 0 even when a true deficit exists, so this
      audit surfaces that case explicitly as EXPIRED.

    Because calibration is pooled, ``attributed`` can legitimately exceed a
    plan's own ``on_disk`` (one bias set credits every plan) — that is
    POOL_CREDITED, informational, not a gap. The reverse, ``on_disk >
    attributed`` with ``remaining > 0``, means frames exist that the CSV does
    not count: either the Session Metadata plugin crashed before appending
    their rows, or they were rejected via a ``BAD_`` filename prefix. Both are
    rare and both are safe to re-shoot, so this audit reports them as the plain
    MISSING_FILES deficit they are and does not attempt to distinguish them.

    ``status`` per row (first match wins): EXPIRED (window closed with an
    uncredited deficit — needs a new plan/window, shooting cannot fix it) →
    MISSING_FILES (remaining > 0 — shoot more; ``missing`` equals
    ``remaining``) → SUSPECT_JUNK (light/dark rows whose duration is under
    half the plan's exposure or 0.01s — e.g. 0.00003s Smart Exposure junk —
    riding alongside a set whose attribution is otherwise complete; bias and
    flats exempt; when junk instead leaves a deficit the status is
    MISSING_FILES with the junk count as the cause) → POOL_CREDITED (calibration satisfied from the
    pool while this plan's own dir is short) → OVERSHOT (light attributed
    above expected from full-mode re-runs; harmless) → OK.

    Top level: ``all_saved`` (true iff no row is MISSING_FILES,
    EXPIRED or SUSPECT_JUNK and no error was recorded), ``totals``,
    ``left_to_do`` (ONLY rows a sequence run can fix,
    with missing = remaining),
    ``blocked`` (EXPIRED — needs plan/window changes), ``rows`` (every
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
    Light rows (and light ``left_to_do`` entries) additionally carry the
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
    an error. Read-only; makes no N.I.N.A. calls beyond the site profile.
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
        #   * plan file loads + CSV never mentions it      -> dropped
        #     silently (never-shot plan, or unrelated JSON such as config
        #     files — list_plans still reports unparseable files).
        root = await metadata.resolve_imaging_root()
        rows = await metadata.read_metadata()
        path_rows = [r for r in rows if (r.get("file_path") or "").strip()]
        all_loaded, load_errors = await _load_project_plans(plan_dir)

        # One pass: which plan (if any) claims each recorded row. Matching
        # reuses progress._light_target_matches, so every token form the
        # attribution logic accepts (underscore/bracket/paren, per-pointing,
        # single-pointing legacy) counts as a reference here too.
        claimed_by: list[int | None] = []
        for row in path_rows:
            hit: int | None = None
            for idx, (_path, plan) in enumerate(all_loaded):
                if any(
                    _light_target_matches(plan, row, pointing_index=i)
                    for i in range(1, len(plan.pointings) + 1)
                ):
                    hit = idx
                    break
            claimed_by.append(hit)
        audited = [
            pair
            for idx, pair in enumerate(all_loaded)
            if idx in set(i for i in claimed_by if i is not None)
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

    if not audited:
        res: dict[str, Any] = {
            "all_saved": not errors,
            "totals": {
                "expected": 0,
                "on_disk": 0,
                "attributed": 0,
                "plans": 0,
                "plan_errors": len(errors),
            },
            "left_to_do": [],
            "blocked": [],
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
                f"plan file in {plan_dir} (never-shot plans are not audited)"
            )
        client.log_payload("Audit:", json.dumps(res, indent=2))
        return res

    # Every branch above resolves root before it can load a plan, so an
    # audited result always has one; this both documents that invariant
    # and narrows the type for mypy before the disk scan.
    assert root is not None

    # One filesystem pass: bucket each landing token directory by its
    # trailing token, longest-first so pane tokens (base-N) win over base.
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

    disk: dict[tuple[str, int | None], dict[str, int]] = {}
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
        counts = _count_fits(entry)
        bucket = disk.setdefault(matched_key, {})
        for image_type, count in counts.items():
            bucket[image_type] = bucket.get(image_type, 0) + count

    def disk_total(image_type: str, *, plan: ObservationPlan) -> int:
        base = plan._base_plan_id()
        return sum(
            counts.get(image_type, 0)
            for (key_base, _pointing), counts in disk.items()
            if key_base == base
        )

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
            light_bucket = disk.get((base, pointing), {})
            light_disk = light_bucket.get("light", 0)
            light_bad = light_bucket.get("bad_light", 0)
            junk = _junk_count(plan, rows, "light", pointing_index=i)
            row = _audit_row(
                plan=plan,
                image_type="light",
                expected=totals["expected"],
                on_disk=light_disk,
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
        # session window) and count disk frames across every dir of the plan.
        if len(plan.pointings) >= 1:
            progress = plan_progress(plan, rows, pointing_index=1, now=now)
            for image_type in ("flat", "dark", "bias"):
                if image_type not in wanted:
                    continue
                totals = _plan_type_totals(progress, image_type)
                type_disk = disk_total(image_type, plan=plan)
                junk = _junk_count(plan, rows, image_type, pointing_index=1)
                row = _audit_row(
                    plan=plan,
                    image_type=image_type,
                    expected=totals["expected"],
                    on_disk=type_disk,
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
        if status == "MISSING_FILES":
            todo = {
                "plan_id": row["plan_id"],
                "target": row["target"],
                "intent": row["intent"],
                "pointing_index": row["pointing_index"],
                "type": row["type"],
                "missing": row["remaining"],
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
    res = {
        "all_saved": not broken and not errors,
        "totals": {
            "expected": sum(r["expected"] for r in rows_out),
            "on_disk": sum(r["on_disk"] for r in rows_out),
            "attributed": sum(r["attributed"] for r in rows_out),
            "plans": len(audited),
            "plan_errors": len(errors),
        },
        "left_to_do": left_to_do,
        "blocked": blocked,
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
