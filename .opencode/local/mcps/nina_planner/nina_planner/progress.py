from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio

from . import config
from .models.plan import ObservationPlan
from .nina_utils import to_local_display

__all__ = [
    "filter_metadata_rows",
    "plan_progress",
    "plan_with_remaining",
    "append_progress_entry",
]


async def append_progress_entry(text: str) -> None:
    """Append an ISO-8601 timestamped entry to ``PROGRESS.md``.

    Matches the convention described in ``AGENTS.md`` — each significant
    action gets one entry. The block is appended as a markdown ``##`` section
    (timestamp + short title line, followed by the entry body) so both humans
    and later agent sessions can grep it by header. Existing content is
    preserved; the file always ends in a newline.

    The path to ``PROGRESS.md`` is taken from :data:`nina_planner.config.PROJECT_DIR`.
    """
    now = datetime.now().astimezone()
    timestamp = now.isoformat(timespec="seconds")
    block = f"\n## {timestamp} — auto\n\n{text.strip()}\n"
    path = config.PROJECT_DIR / "PROGRESS.md"
    async with await anyio.open_file(path, "a", encoding="utf-8") as f:
        await f.write(block)


def _path_components(path: str) -> list[str]:
    """Split a metadata path independent of its Windows/POSIX separators."""
    return [part for part in path.replace("\\", "/").split("/") if part]


def _exposure_matches(row: dict[str, Any], exposure: float | None) -> bool:
    if exposure is None:
        return True
    raw = row.get("duration")
    if raw is None or raw == "":
        return False
    try:
        return abs(float(raw) - exposure) < 0.05
    except (TypeError, ValueError):
        return False


def _light_target_matches(
    plan: ObservationPlan,
    row: dict[str, Any],
    *,
    pointing_index: int = 1,
) -> bool:
    """True when the row's recorded path carries this pointing's plan-id token.

    The token is unique to a plan pointing, so any single path segment may
    carry it: N.I.N.A. puts `$$TARGETNAME$$` wherever the profile's
    `FilePattern` places it — in the filename, in the directory under LIGHT,
    or in an ancestor directory above the date folder.
    """
    path = (row.get("file_path") or "").strip()
    if not path:
        return False

    components = _path_components(path)
    # The underscore token is primary: it is what N.I.N.A. writes, since it
    # rewrites "[" and "]" to "_" when substituting $$TARGETNAME$$ into the
    # path. The bracketed and parenthesised forms count as legacy (frames
    # shot under the earlier conventions, or paths recorded before the
    # rewrite), in both the plan-level and the historically-stamped
    # per-pointing shape.
    base = plan.effective_plan_id(pointing_index)
    expected_tokens = [f"_{base}_", f"[{base}]", f"({base})"]
    if len(plan.pointings) == 1 and pointing_index == 1:
        # Legacy: single-pointing plans historically stamped the per-pointing
        # token ("{base}-1"); frames acquired under it still count.
        per_pointing = plan.per_pointing_plan_id(1)
        expected_tokens += [
            f"_{per_pointing}_",
            f"[{per_pointing}]",
            f"({per_pointing})",
        ]

    # Substring match on purpose — any single path segment may carry the
    # token. The token set is safe across pointings only because every form
    # is delimited: the closing "_" keeps "_plan-x_" out of "_plan-x-1_",
    # and "_plan-x-1_" out of "_plan-x-10_" (same for the "[" "]" and
    # "(" ")" forms). Do not drop the delimiters or match a bare
    # un-delimited id here — mosaics with >=10 pointings would silently
    # absorb each other's frames (see the test_light_target_matches_*
    # regression tests). The guarantee leans on ids containing no "_"
    # (derived ids are "plan-<hex>"), since an id that carries the
    # delimiter could contain its own token.
    return any(
        token in component for token in expected_tokens for component in components
    )


def _quality_accepted(
    row: dict[str, Any],
    max_hfr: float | None,
    min_detected_stars: int | None,
    max_guiding_rms_arcsec: float | None = None,
) -> bool:
    # Sentinel contract, mirroring imaging._clean_metric: NINA serializes
    # unmeasured ASCOM values as 0 in ImageMetaData.csv, so under an *active*
    # filter a 0 means "not measured" and cannot pass it (a frame whose HFR
    # or guiding RMS was never taken must not count toward acquisition, or a
    # plan would be marked complete on unmeasured frames). With no filter
    # active the question does not arise and the frame counts as before.
    if max_hfr is not None:
        raw_hfr = row.get("hfr")
        if raw_hfr is None or raw_hfr == "":
            return False
        try:
            hfr = float(raw_hfr)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(hfr) or hfr == 0 or hfr > max_hfr:
            return False
    if min_detected_stars is not None:
        raw_stars = row.get("detected_stars")
        if raw_stars is None or raw_stars == "":
            return False
        try:
            stars = int(raw_stars)
        except (TypeError, ValueError):
            return False
        if stars == 0 or stars < min_detected_stars:
            return False
    if max_guiding_rms_arcsec is not None:
        raw_rms = row.get("guiding_rms_arc_sec")
        if raw_rms is None or raw_rms == "":
            return False
        try:
            rms = float(raw_rms)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(rms) or rms == 0 or rms > max_guiding_rms_arcsec:
            return False
    return True


def _exposure_instant(row: dict[str, Any]) -> datetime | None:
    """The row's exposure start as a naive UTC instant, or ``None``.

    Reads the CSV's ``ExposureStartUTC`` column — the only timestamp this
    module trusts. The naive local ``ExposureStart`` column is deliberately
    ignored: mixing the two in one window comparison would shift frames by
    the UTC offset, so a row without a usable UTC stamp simply does not
    count. Any offset that is present is converted to UTC, then dropped, so
    every instant this module compares is naive UTC.
    """
    raw = row.get("exposure_start_utc")
    if raw is None or raw == "":
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(UTC).replace(tzinfo=None)
    return stamp


def _session_start(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    pointing_index: int,
) -> datetime | None:
    """Earliest light exposure attributed to the pointing, or ``None``.

    This is the anchor for the calibration window: a calibration frame counts
    only when shot within ``calibration_window_days`` of this instant, on
    either side. Anchoring to the plan's own (fixed) light session rather
    than a moving "now" means calibration acquired for the session stays
    valid, instead of expiring and being re-acquired on every later run.
    The instant is naive UTC, matching :func:`_within_age`.
    """
    stamps: list[datetime] = []
    for row in rows:
        if (row.get("image_type") or row.get("frame_type") or "").upper() != "LIGHT":
            continue
        if not _light_target_matches(plan, row, pointing_index=pointing_index):
            continue
        stamp = _exposure_instant(row)
        if stamp is not None:
            stamps.append(stamp)
    return min(stamps) if stamps else None


def _within_age(
    row: dict[str, Any],
    max_age_days: int | None,
    session_start: datetime | None,
) -> bool:
    """True when a calibration frame was shot inside the plan's window.

    The frame counts when it was shot within ``max_age_days`` of the plan's
    session anchor (the earliest attributed light), on either side:

    ``session_start - max_age_days <= shot_at <= session_start + max_age_days``

    Both bounds are inclusive. There is deliberately no asymmetry here: a dark
    shot a week before the first light counts, one shot a week after it
    counts, and one shot a month after it does not.

    Both instants are naive UTC (see :func:`_exposure_instant`), so the
    comparison is independent of both N.I.N.A.'s timezone and the host's.
    """
    if max_age_days is None:
        return True
    if session_start is None:
        return True
    shot_at = _exposure_instant(row)
    if shot_at is None:
        return False
    if session_start.tzinfo is not None:
        session_start = session_start.astimezone(UTC).replace(tzinfo=None)
    window = timedelta(days=max_age_days)
    return session_start - window <= shot_at <= session_start + window


def _row_matches(
    row: dict[str, Any],
    image_type: str,
    *,
    filter_name: str | None = None,
    exposure: float | None = None,
    plan: ObservationPlan | None = None,
    pointing_index: int = 1,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
    max_age_days: int | None = None,
    session_start: datetime | None = None,
) -> bool:
    if (row.get("image_type") or row.get("frame_type") or "").upper() != image_type:
        return False
    if filter_name is not None and row.get("filter_name") != filter_name:
        return False
    if not _exposure_matches(row, exposure):
        return False
    if image_type == "LIGHT":
        if plan is not None and not _light_target_matches(
            plan, row, pointing_index=pointing_index
        ):
            return False
        return _quality_accepted(
            row, max_hfr, min_detected_stars, max_guiding_rms_arcsec
        )
    return _within_age(row, max_age_days, session_start)


def frame_group_constraints(
    image_type: str, group: Any
) -> tuple[str | None, float | None]:
    """The ``(filter_name, exposure)`` one frame group asks ``_row_matches`` for.

    The single place the per-type matching rule is stated:

    - ``LIGHT`` — **both**: a light must match its group's filter *and*
      exposure, alongside the attribution token.
    - ``FLAT`` — ``filter_name`` only. N.I.N.A.'s SkyFlat picks the exposure
      itself to hit a target signal level, so any flat through the right
      filter counts, whatever its duration.
    - ``DARK`` — ``exposure_time_seconds`` only: dark current scales with
      exposure time, so duration is part of what makes a dark usable.
    - ``BIAS`` — neither: every bias frame is equivalent.

    The frame models already agree with this table by *absence* —
    ``FlatExposurePlan`` carries no ``exposure_time_seconds``,
    ``DarkExposurePlan`` no ``filter_name``, ``BiasExposurePlan`` neither — so
    reading field presence alone would usually reproduce these values. The
    branches are kept anyway because that absence is a property of the
    pydantic models, not of the ``dict`` groups can also surface as
    (``_junk_count``), where a stray key would otherwise widen a match that
    two call sites had each promised to narrow.

    Both callers used to spell this rule out independently —
    ``filter_metadata_rows`` as its own ``specs`` table, ``plan_progress`` as
    ``data.get(...)`` plus an explicit ``exposure=None if image_type ==
    "FLAT"`` — each carrying a comment claiming to mirror the other. Nothing
    enforced the claim: a mutation removing the dark exposure gate from one
    copy left the audit verdict untouched, because the audit reads the other.
    """

    # Frame groups are pydantic models on both call paths, but `_junk_count`
    # shows they can surface as plain dicts too — attribute access on a dict
    # would raise, so resolve the field both ways.
    def field(name: str) -> Any:
        return group.get(name) if isinstance(group, dict) else getattr(group, name)

    if image_type == "FLAT":
        return (field("filter_name"), None)
    if image_type == "DARK":
        return (None, field("exposure_time_seconds"))
    if image_type == "BIAS":
        return (None, None)
    return (field("filter_name"), field("exposure_time_seconds"))


def _count_matching(
    rows: list[dict[str, Any]],
    image_type: str,
    *,
    filter_name: str | None = None,
    exposure: float | None = None,
    plan: ObservationPlan | None = None,
    pointing_index: int = 1,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
    max_age_days: int | None = None,
    session_start: datetime | None = None,
) -> int:
    count = 0
    for row in rows:
        if _row_matches(
            row,
            image_type,
            filter_name=filter_name,
            exposure=exposure,
            plan=plan,
            pointing_index=pointing_index,
            max_hfr=max_hfr,
            min_detected_stars=min_detected_stars,
            max_guiding_rms_arcsec=max_guiding_rms_arcsec,
            max_age_days=max_age_days,
            session_start=session_start,
        ):
            count += 1
    return count


def filter_metadata_rows(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    image_type: str,
    *,
    pointing_index: int = 1,
    max_age_days: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Rows attributed to `plan` for `image_type`.

    Lights match by the plan_id embedded in their file path (with the
    pointing_index suffix appended at load time); dark/bias frames match the
    plan's exposure specs, and flat frames match by filter alone.
    Calibration is anchored
    to the plan's light session: a frame counts when it was shot within
    ``max_age_days`` of ``session_start`` on either side, where
    ``session_start`` is the earliest attributed light exposure (falling back
    to ``now`` when the plan has no lights yet). This selects the frames that
    are *attributable*; whether the plan still *demands* more of them is a
    separate question answered by :func:`plan_progress` (a window that has
    closed around ``now`` reports zero remaining).
    """
    image_type_upper = image_type.upper()
    if image_type_upper == "LIGHT":
        # Deliberately NO filter/exposure constraints, unlike plan_progress
        # (which passes frame_group_constraints' LIGHT result, so each light
        # *group* counts only its own filter+exposure). This function answers
        # a different question — every frame attributable to this pointing,
        # across all its groups — so narrowing here would drop frames the
        # caller came to see. frame_group_constraints is therefore not
        # consulted for lights; that asymmetry is intentional, not drift.
        return [
            r
            for r in rows
            if _row_matches(r, "LIGHT", plan=plan, pointing_index=pointing_index)
        ]
    groups: list[Any]
    if image_type_upper == "FLAT":
        groups = plan.flat_frames
    elif image_type_upper == "DARK":
        groups = plan.dark_frames
    elif image_type_upper == "BIAS":
        groups = plan.bias_frames
    else:
        return []
    # Which constraints apply is NOT decided here: see frame_group_constraints.
    specs = [frame_group_constraints(image_type_upper, g) for g in groups]
    session_start = (
        _session_start(plan, rows, pointing_index) or now or datetime.now(UTC)
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        for filter_name, exposure in specs:
            if _row_matches(
                row,
                image_type_upper,
                filter_name=filter_name,
                exposure=exposure,
                max_age_days=max_age_days,
                session_start=session_start,
            ):
                out.append(row)
                break
    return out


def plan_progress(
    plan: ObservationPlan,
    rows: list[dict[str, Any]],
    *,
    pointing_index: int = 1,
    max_hfr: float | None = None,
    min_detected_stars: int | None = None,
    max_guiding_rms_arcsec: float | None = None,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Acquired/remaining counts per exposure group for one pointing.

    Lights count by attribution (plan id in the file path) plus the optional
    quality thresholds. Calibration (flat/dark/bias) is anchored to the
    earliest attributed light, and is reported in two parts:

    * ``acquired_count`` — frames shot inside ``calibration_window_days`` of
      that anchor, on either side of it. Always truthful, whether or not the
      window is still open.
    * ``remaining_count`` — what the plan still demands. While ``now`` sits
      inside the window this is ``total - acquired``; once ``now`` falls
      outside it, no frame shot now could ever be credited, so remaining is
      reported as ``0`` rather than an unsatisfiable deficit (which would
      otherwise re-trigger acquisition on every later run).

    Calibration group items therefore also carry ``window_open`` (bool) and
    ``window_end`` (the window's upper bound, ``anchor + window``, rendered in
    the host's local time with its UTC offset — the arithmetic behind it runs
    on naive UTC), so a zero remaining can be told apart from a finished plan.
    Light items carry neither, but do carry ``surplus_count`` — frames
    attributed beyond the plan's ``total_count``. It cannot exceed zero for
    calibration, so it is lights-only.

    ``surplus_count`` exists because ``acquired_count`` is cumulative across
    every run of the plan while ``total_count`` is a per-plan constant. A
    ``mode=full`` re-shoot asks N.I.N.A. for ``total_count`` *more* frames,
    but older frames keep satisfying the plan, so ``remaining_count`` stays 0
    and ``complete`` stays true whether the re-shoot delivered all of them or
    a handful. Surplus is the one signal here that a re-shoot happened at all;
    it is a flag to investigate, not a verdict — a target legitimately shot on
    several nights accumulates the same surplus. ``audit_plans`` and the
    per-date frame folders are what separate those two cases.
    """
    # `now` defaults to the host clock in UTC; an aware caller value is
    # converted to UTC, and a naive one is taken as already-UTC — the same
    # convention `_exposure_instant` applies to the CSV stamps, so the two
    # sides of the window comparison always share one clock.
    reference_now = now or datetime.now(UTC)
    if reference_now.tzinfo is not None:
        reference_now = reference_now.astimezone(UTC).replace(tzinfo=None)
    session_start = _session_start(plan, rows, pointing_index) or reference_now
    window = timedelta(days=plan.calibration_window_days)
    window_open = session_start - window <= reference_now <= session_start + window
    window_end = session_start + window

    def group(image_type: str, groups: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for g in groups:
            data = g.model_dump()
            # Which constraints apply is decided in ONE place — see
            # frame_group_constraints. Flats carry no exposure and darks no
            # filter, so this is exactly (filter, None) / (None, exposure)
            # / (None, None) as before, without restating it here.
            filter_name, exposure = frame_group_constraints(image_type, g)
            total = data["total_count"]
            acquired = _count_matching(
                rows,
                image_type,
                filter_name=filter_name,
                exposure=exposure,
                plan=plan if image_type == "LIGHT" else None,
                pointing_index=pointing_index,
                max_hfr=max_hfr,
                min_detected_stars=min_detected_stars,
                max_guiding_rms_arcsec=max_guiding_rms_arcsec,
                max_age_days=plan.calibration_window_days,
                session_start=session_start,
            )
            item: dict[str, Any] = {
                **data,
                "acquired_count": acquired,
                "remaining_count": max(total - acquired, 0),
            }
            if image_type == "LIGHT":
                # Frames beyond what the plan asked for. Deliberately
                # lights-only: calibration is pool-credited by type+filter
                # across plans, so a flat group routinely reads 103/20 and a
                # surplus there would be permanent noise on every plan.
                #
                # This is the field that catches a truncated mode=full
                # re-shoot, which `remaining_count` cannot: the re-shoot asks
                # for `total_count` *more* frames, but acquired is cumulative
                # across every run, so older frames keep satisfying the plan
                # and remaining stays 0 no matter what the re-shoot delivered.
                # M42 read acquired=137 / total=120 / remaining=0 at 17/120
                # frames for the night; surplus=17 is the only number in this
                # output that exposes it.
                item["surplus_count"] = max(acquired - total, 0)
            if image_type != "LIGHT":
                item["window_open"] = window_open
                # window_end is a naive-UTC instant; attach UTC so it renders
                # in the host's local zone with the offset kept.
                item["window_end"] = to_local_display(window_end.replace(tzinfo=UTC))
                if not window_open:
                    item["remaining_count"] = 0
            out.append(item)
        return out

    return {
        "light": group("LIGHT", plan.light_frames),
        "flat": group("FLAT", plan.flat_frames),
        "dark": group("DARK", plan.dark_frames),
        "bias": group("BIAS", plan.bias_frames),
    }


def plan_with_remaining(
    plan: ObservationPlan, progress: dict[str, list[dict[str, Any]]]
) -> ObservationPlan:
    def rebuild(groups: list[Any], key: str) -> list[Any]:
        out: list[Any] = []
        for g, item in zip(groups, progress[key], strict=True):
            remaining = item["remaining_count"]
            if remaining <= 0:
                continue
            out.append(type(g)(**{**g.model_dump(), "total_count": remaining}))
        return out

    return plan.model_copy(
        update={
            "light_frames": rebuild(plan.light_frames, "light"),
            "flat_frames": rebuild(plan.flat_frames, "flat"),
            "dark_frames": rebuild(plan.dark_frames, "dark"),
            "bias_frames": rebuild(plan.bias_frames, "bias"),
        }
    )
