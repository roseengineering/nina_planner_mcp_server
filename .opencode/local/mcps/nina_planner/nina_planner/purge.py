"""nina-purge — retire archived frames from the N.I.N.A. imaging root.

Where ``nina-archive`` copies completed plans out to a portable folder,
``nina-purge`` completes the lifecycle: it removes from the imaging root
the frames an archive already holds. Nothing here deletes on the first
pass — quarantine first, live with it, then ``--purge`` for real.

The rule, per recorded frame F (deletion only ever considers frames the
Session Metadata plugin recorded):

    prunable(F)  <=>  preserved(F)              # some committed manifest.json
                                                # under --dest lists F's source
                     AND every plan P whose selection contains F is released

``released(P)`` needs both halves of "finished AND archived": P must have
an archive folder (found by plan id across ``<dest>/*``/manifest.json),
AND at purge time P must still read complete under the shared verdict
(``_progress_facts``) or its manifest must carry the ``--all`` waiver
(``complete: false`` — the operator's receipt for shipping an incomplete
plan). A plan that is complete but NOT yet archived protects its frames:
archive before you purge. The completeness half is recomputed from live
rows and has one sharp edge — a plan whose own directory was already
quarantined reads incomplete (its rows went with its frames) and keeps
holding any directories of its own that remain; the console's plan-file
hint is the resolution: move the file out (the archive folder holds the
verbatim ``plan.json``) and its held frames become prunable on the next
run. Frames no loaded plan's selection contains are orphans — prunable
when preserved, reported and kept otherwise.

Claims reuse ``archive._select_frames`` verbatim — the same attribution
``nina-archive`` ships — so a light is claimed only by the plan whose
``_{plan_id}_`` token it carries, while pooled calibration (flats by
type+filter, darks by type+exposure, bias by type, inside every claiming
plan's calibration window) is claimed by EVERY plan that would credit it:
a finished plan's darks stay put exactly as long as any unarchived plan
still draws on them.

Directories are the unit of quarantine and the verdict is an AND over
every recorded frame in them: one held frame keeps the whole landing
directory. N.I.N.A. ``BAD_`` reject twins (found by probing a dropped
row's pre-rename name, exactly as archive's disk report does) are
prunable garbage: they ride with their directory and never block it.
Own-token frames no plan ships into a type folder — degenerate captures
(0.00003s Smart Exposure junk) and real but uncredited ones (outside
every window, an exposure no group asked for) — are archived by
``nina-archive`` under ``JUNK/`` and listed in the manifest like any
other file, so they read ``preserved`` and ride along too; they used to
sit in ``disk.not_selected`` and hold the directory for ever as "not
preserved — run nina-archive first". Files no CSV row ever recorded stay
invisible by design (the Session Metadata plugin is trusted): they ride
along into quarantine, are counted in the index record, and are never
classified.

Deletion is two deliberate steps. ``--apply`` MOVES each prunable
directory into ``<imaging-root>/.quarantine/<dir>`` — a dot
directory every attribution walk prunes (``imaging._prunable_dir``), so
quarantined frames and their ImageMetaData.csv behave exactly as if
gone, while one ``mv`` restores them — and only ``--purge`` unlinks.
``<imaging-root>/purge-index.jsonl`` appends one JSON record per action —
including one per plan file under ``--purge``, carrying
``plan_file_deleted``. A run refuses to touch the root while a sequence
is RUNNING (N.I.N.A. may be appending CSV rows); an unreachable N.I.N.A.
is treated as idle — the same posture ``nina-archive`` takes — and
``--imaging-root`` bypasses the profile lookup entirely.

This tool never deletes or modifies the archive folders under ``--dest``:
they are the preserved copy. Confirm your own cloud upload and local
download are intact before running ``--purge``.

Plan files are removed as the LAST step of ``--purge``, once the
plan's frames are gone: the archive already holds the verbatim
``plan.json`` (the copy to restore from), while a leftover plan whose
rows went with its frames reads as NEVER_SHOT to
``audit_plans``/``get_plan_progress`` and could be re-shot. Three
gates run BEFORE any frame is deleted, so a refusal leaves everything
intact: the archive folder ``<dest>/<stem>__<plan-id>/`` must exist
with its ``plan.json``; that copy must be BYTE-IDENTICAL to the plan
file (a difference means the plan was edited after archiving — the
whole plan is refused with "archive is stale, re-run nina-archive
--force first": nothing is deleted for it, the run continues with the
other plans, exit 1); and the plan file must never live inside
``--dest`` (never delete the archived copy). Per plan the order is
fixed — gates, frames, then the file — so a failed frame deletion (or
frames still held back by another plan's claims) KEEPS the plan file
for a re-run; a file already gone is simply skipped, and a
``--purge`` run with nothing left is an idempotent success. A
plan-shaped JSON that fails to load ABORTS the run: its claims are
unknown, and unknown claims make deletions unsafe — unrelated project
JSON is still ignored silently.

Exit codes: 0 — something was (or, in a dry run, would be) quarantined
or purged, and any ``--purge`` run without an error (purging is
idempotent); 1 — nothing to do or an error, including a stale-archive
refusal; 2 — bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config, metadata, plan_utils
from .archive import (
    _MANIFEST_NAME,
    _folder_name,
    _human_bytes,
    _inside,
    _looks_like_plan_file,
    _progress_facts,
    _select_frames,
)
from .client import _sequence_is_running
from .imaging import _prunable_dir, read_imaging_csv
from .models.plan import ObservationPlan
from .tools.plans import _plan_files

_QUARANTINE_DIR = ".quarantine"
_INDEX_NAME = "purge-index.jsonl"
_FRAME_TYPES = ("light", "dark", "bias", "flat")
# Plugin-written metadata that legitimately lives beside frames: it rides
# into quarantine with the frames (which is what hides the rows from the
# next attribution pass) and is never counted as "unrecorded".
_SIDECAR_NAMES = frozenset(
    {"ImageMetaData.csv", "WeatherData.csv", "AcquisitionDetails.csv"}
)


class PurgeError(Exception):
    """An action could not proceed; the message is user-facing."""


# ---------------------------------------------------------------- helpers


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _norm(path: Any) -> str:
    """Canonical string form of a recorded path, matching the archive's."""
    return str(Path(str(path)))


def _append_index(base: Path, record: dict[str, Any]) -> None:
    with (base / _INDEX_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _tree_size(path: Path) -> tuple[int, int]:
    """``(files, bytes)`` under ``path`` — accounting only, post-move."""
    files = 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            files += 1
            total += child.stat().st_size
    return files, total


def _unrecorded_count(path: Path, recorded: int) -> int:
    """Files under ``path`` that are neither recorded frames, rejects,
    nor plugin sidecars — the honest anomaly count for an index record.

    Sidecars are excluded because EVERY frame directory carries its
    ``ImageMetaData.csv`` (one per type), so counting them would flag
    every healthy quarantine as anomalous.
    """
    count = 0
    for child in path.rglob("*"):
        if child.is_file() and child.name not in _SIDECAR_NAMES:
            count += 1
    return max(count - recorded, 0)


def _unique_join(reasons: list[str], limit: int = 3) -> str:
    seen = list(dict.fromkeys(reasons))
    if len(seen) > limit:
        return "; ".join(seen[:limit]) + f"; +{len(seen) - limit} more"
    return "; ".join(seen)


def _plan_file_gate(
    plan_file: Path, plan: ObservationPlan, dest: Path
) -> tuple[str, bool, bool, str]:
    """Archive gates for one plan file: ``(folder, archive_ok, stale, reason)``.

    Checked BEFORE any frame is deleted, so a refusal leaves everything
    intact:

    - Gate 1 — the archive folder ``dest / _folder_name(plan_file, plan)``
      must exist and hold its ``plan.json``: the durable copy is what makes
      removing the project's copy safe at all.
    - Gate 2 — that ``plan.json`` must be BYTE-IDENTICAL to the plan file.
      A difference means the plan was edited after archiving; the whole plan
      is refused with ``archive is stale, re-run nina-archive --force
      first`` (``stale=True``) — ``nina-archive --force`` rebuilds the
      folder from the current file and the refusal clears.
    - Gate 3 — the plan file must not live inside ``--dest`` (never delete
      the archived copy); discovery already excludes those, kept here as a
      belt-and-braces check.

    ``archive_ok`` is gate 1 alone; a stale comparison still means the
    folder exists (gate 2 only runs when gate 1 passed).
    """
    folder_path = dest / _folder_name(plan_file, plan)
    folder = str(folder_path)
    if _inside(plan_file, dest):
        return folder, False, False, "plan file is inside --dest (never deleted)"
    archived = folder_path / "plan.json"
    if not archived.is_file():
        reason = (
            f"no archive folder at {folder_path}"
            if not folder_path.is_dir()
            else f"no plan.json in archive folder {folder_path}"
        )
        return folder, False, False, reason
    try:
        identical = plan_file.read_bytes() == archived.read_bytes()
    except OSError as exc:
        return folder, False, False, f"cannot compare against the archive ({exc})"
    if not identical:
        return (
            folder,
            True,
            True,
            "archive is stale, re-run nina-archive --force first",
        )
    return folder, True, False, ""


async def _resolve_root(args: argparse.Namespace) -> Path:
    if args.imaging_root:
        root = Path(args.imaging_root).expanduser()
        if not root.is_dir():
            raise PurgeError(
                f"--imaging-root does not exist or is not a directory: {root}"
            )
        return root
    try:
        return Path(await metadata.resolve_imaging_root())
    except Exception as exc:  # noqa: BLE001 - surface any NINA/profile failure
        raise PurgeError(
            "cannot resolve the imaging root from the N.I.N.A. site profile "
            f"({exc}). Is NINA running? Pass --imaging-root to purge "
            "without NINA."
        ) from exc


async def _idle_state() -> tuple[bool, str]:
    """``(may_proceed, message)`` for a mutation of the imaging root.

    A running sequence may be appending ImageMetaData.csv rows while this
    tool reads them, so mutations refuse. An unreachable N.I.N.A. is
    treated as idle — the same posture ``nina-archive`` takes — and the
    claim map already protects every unarchived plan's frames, so this
    gate only closes the read-while-writing race.
    """
    try:
        running = await _sequence_is_running()
    except Exception as exc:  # noqa: BLE001 - unreachable NINA is fine
        return True, f"N.I.N.A. unreachable ({exc}) — proceeding as if idle"
    if running:
        return False, (
            "a N.I.N.A. sequence is RUNNING — stop it before purging "
            "(it may be appending CSV rows)"
        )
    return True, ""


# ----------------------------------------------------------- collection


def _read_manifests(
    dest: Path,
) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    """Archive knowledge from ``<dest>/*``/``manifest.json``.

    Returns ``(by plan id, preserved source paths, warnings)``. Only
    committed folders count — hidden staging siblings (``.<name>.partial``
    from an interrupted archive) are skipped, so a half-built archive can
    never release a plan. Duplicate plan ids merge with the ``--all``
    waiver (``complete: false``) winning, and ``preserved`` is the union
    of every manifest's ``files[].source`` — the strings the archive
    recorded, normalized the same way this tool normalizes row paths.
    """
    by_plan: dict[str, dict[str, Any]] = {}
    preserved: set[str] = set()
    warnings: list[str] = []
    if not dest.is_dir():
        return (
            by_plan,
            preserved,
            [f"no archive at {dest} — nothing reads as preserved"],
        )
    for folder in sorted(dest.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        manifest_path = folder / _MANIFEST_NAME
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.append(f"unreadable manifest {manifest_path}: {exc}")
            continue
        plan_id = str(data.get("plan_id") or "")
        if not plan_id:
            warnings.append(f"manifest {manifest_path} has no plan_id — ignored")
            continue
        existing = by_plan.get(plan_id)
        if existing is None or (
            existing.get("complete") is not False and data.get("complete") is False
        ):
            by_plan[plan_id] = data  # an explicit --all waiver wins the merge
        for entry in data.get("files") or []:
            source = entry.get("source")
            if source:
                preserved.add(_norm(source))
    return by_plan, preserved, warnings


# --------------------------------------------------------- classification


def _classify(
    root: Path,
    rows: list[dict[str, Any]],
    rejects: list[Path],
    claims: dict[str, set[str]],
    released: set[str],
    preserved: set[str],
    plan_names: dict[str, str],
    notes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per top-level directory verdicts, plus outside-root reports.

    The verdict is an AND over every recorded frame in the directory: one
    held frame keeps the whole directory; ``BAD_`` reject twins never
    hold (they are garbage and ride along); a directory with no recorded
    frames is held — unrecorded files are never classified, so nothing
    about them can be proven preserved.
    """
    buckets: dict[str, dict[str, list[Any]]] = {}
    outside: list[dict[str, Any]] = []
    root_resolved = root.resolve()

    def bucket_for(path: Path) -> str | None:
        try:
            return path.resolve().relative_to(root_resolved).parts[0]
        except (ValueError, IndexError):
            return None

    for row in rows:
        file_path = str(row.get("file_path") or "")
        if not file_path:
            continue
        name = bucket_for(Path(file_path))
        if name is None:
            outside.append({"path": file_path, "type": row.get("image_type")})
            continue
        buckets.setdefault(name, {"frames": [], "rejects": []})["frames"].append(row)
    for twin in rejects:
        name = bucket_for(twin)
        if name is None:
            outside.append({"path": str(twin), "type": "BAD_"})
            continue
        buckets.setdefault(name, {"frames": [], "rejects": []})["rejects"].append(twin)

    # Enumerate the disk too, so empty or wholly-unrecorded directories
    # are reported rather than silently absent from the table.
    on_disk = {
        p.name for p in root.iterdir() if p.is_dir() and not _prunable_dir(p.name)
    }

    out: list[dict[str, Any]] = []
    for name in sorted(on_disk | set(buckets)):
        entries = buckets.get(name, {"frames": [], "rejects": []})
        held_reasons: list[str] = []
        prunable_frames = 0
        total_bytes = 0
        plan_ids: set[str] = set()
        for row in entries["frames"]:
            path = _norm(row["file_path"])
            try:
                total_bytes += Path(path).stat().st_size
            except OSError:
                pass  # vanished after the CSV read; counts as neither
            claimants = claims.get(path, set())
            plan_ids |= claimants
            if path not in preserved:
                held_reasons.append("not preserved — run nina-archive first")
                continue
            unreleased = sorted(claimants - released)
            if unreleased:
                named = ", ".join(
                    f"{plan_names.get(pid, pid)} [{pid}: "
                    f"{notes.get(pid, 'not released')}]"
                    for pid in unreleased
                )
                held_reasons.append(f"claimed by {named}")
            else:
                prunable_frames += 1
        reject_count = len(entries["rejects"])
        for twin in entries["rejects"]:
            try:
                total_bytes += twin.stat().st_size
            except OSError:
                pass
        if held_reasons:
            verdict, reason = "hold", _unique_join(held_reasons)
        elif prunable_frames or reject_count:
            verdict = "prune"
            reason = (
                "preserved; all claims released"
                if prunable_frames
                else "BAD_ rejects only"
            )
        else:
            verdict = "hold"
            reason = "no recorded frames (unrecorded files are never classified)"
        out.append(
            {
                "dir": name,
                "verdict": verdict,
                "reason": reason,
                "frames": len(entries["frames"]),
                "rejects": reject_count,
                "bytes": total_bytes,
                "plan_ids": sorted(plan_ids),
            }
        )
    return out, outside


# --------------------------------------------------------------- actions


def _quarantine(
    root: Path,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Move each prunable directory into the hidden quarantine.

    Returns the entries that actually moved (a directory can vanish or
    fail between classification and the move) — the caller's plan-file
    hint is scoped to these.
    """
    quarantine = root / _QUARANTINE_DIR
    moved: list[dict[str, Any]] = []
    for entry in entries:
        name = entry["dir"]
        src = root / name
        if not src.is_dir():
            print(
                f"  ⚠ {name}: vanished before the move — skipped",
                file=sys.stderr,
            )
            continue
        quarantine.mkdir(parents=True, exist_ok=True)
        target = quarantine / name
        suffix = 0
        while target.exists():
            suffix += 1
            target = quarantine / f"{name}~{suffix}"
        try:
            shutil.move(str(src), str(target))
        except OSError as exc:
            print(f"  ✗ {name}: {exc}", file=sys.stderr)
            continue
        _, total = _tree_size(target)
        unrecorded = _unrecorded_count(target, entry["frames"] + entry["rejects"])
        record = {
            "at": _now().isoformat(timespec="seconds"),
            "action": "quarantine",
            "dir": name,
            "quarantine": str(target.relative_to(root)),
            "frames": entry["frames"],
            "rejects": entry["rejects"],
            "unrecorded": unrecorded,
            "bytes": total,
            "claimants": entry["plan_ids"],
        }
        _append_index(root, record)
        print(
            f"  📦 {name} → {record['quarantine']}/ "
            f"({entry['frames']} frames, {entry['rejects']} rejects, "
            f"{_human_bytes(total)})"
        )
        if unrecorded:
            print(
                f"  ⚠ {name}: {unrecorded} unrecorded file(s) rode along — "
                "inspect before --purge"
            )
        moved.append(entry)
    return moved


def _purge_quarantine(
    root: Path, purgeable: set[str], skips: dict[str, str]
) -> tuple[int, int, int]:
    """Actually delete the quarantine. Returns ``(removed, skipped, failed)``.

    Only children the plan-file gates released (``purgeable``) are unlinked:
    a kept child is reported with its reason (``skips``), so a gate refusal
    after ``--apply`` leaves the quarantine — the frames — intact. Loose
    files (never created by this tool, never claimed) always ride along.
    """
    quarantine = root / _QUARANTINE_DIR
    if not quarantine.is_dir():
        print(f"nothing to purge — no quarantine at {quarantine}")
        return 0, 0, 0
    removed = files = total = skipped = failed = 0
    for child in sorted(quarantine.iterdir()):
        if child.is_dir() and child.name not in purgeable:
            skipped += 1
            print(
                f"  ⏭ {child.name}: kept in quarantine — "
                f"{skips.get(child.name, 'not released')}"
            )
            continue
        try:
            if child.is_dir():
                count, size = _tree_size(child)
                shutil.rmtree(child)
            else:
                count, size = 1, child.stat().st_size
                child.unlink()
        except OSError as exc:
            print(f"  ✗ {child.name}: {exc}", file=sys.stderr)
            failed += 1
            continue
        removed += 1
        files += count
        total += size
    if removed:
        _append_index(
            root,
            {
                "at": _now().isoformat(timespec="seconds"),
                "action": "purge",
                "removed": removed,
                "files": files,
                "bytes": total,
            },
        )
        print(
            f"  🗑 purged {removed} quarantined item(s), "
            f"{files} files, {_human_bytes(total)} freed"
        )
    try:
        quarantine.rmdir()  # tidy the (now empty) container
    except OSError:
        pass
    return removed, skipped, failed


def _quarantine_gates(
    root: Path,
    plan_records: list[dict[str, Any]],
    by_plan: dict[str, dict[str, Any]],
    plan_names: dict[str, str],
) -> tuple[Path | None, dict[str, set[str]], set[str], dict[str, str]]:
    """Gate the quarantine against the plan-file gates before unlinking.

    Returns ``(quarantine dir, child → claimants, purgeable children, skip
    reasons)``. Claimants come from the ``quarantine`` records this tool
    itself appended to ``<root>/purge-index.jsonl`` (each carries the moved
    path and the claiming plan ids). A quarantined directory is purgable
    only when EVERY plan whose frames it carries still passes its gates —
    a stale archive (gate 2) or a vanished archive folder (gate 1) after
    ``--apply`` must leave those frames intact. A claimant whose plan file
    is already gone is vouched for by a surviving manifest (the old
    move-the-file-out workflow); with neither, the directory's claims are
    unverifiable and it is kept. A directory with no record at all (moved
    in by hand) is never purged automatically.
    """
    quarantine = root / _QUARANTINE_DIR
    if not quarantine.is_dir():
        return None, {}, set(), {}
    claimants: dict[str, set[str]] = {}
    index = root / _INDEX_NAME
    if index.is_file():
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("action") == "quarantine" and record.get("quarantine"):
                claimants[Path(str(record["quarantine"])).name] = set(
                    record.get("claimants") or []
                )
    stale_ids = {r["plan_id"] for r in plan_records if r["stale"]}
    gate1_ids = {r["plan_id"] for r in plan_records if r["archive_ok"]}
    gate1_reason = {
        r["plan_id"]: r["gate_reason"] for r in plan_records if not r["archive_ok"]
    }
    file_ids = {r["plan_id"] for r in plan_records}
    purgeable: set[str] = set()
    skips: dict[str, str] = {}
    for child in sorted(quarantine.iterdir()):
        if not child.is_dir():
            purgeable.add(child.name)  # loose files: never claimed
            continue
        ids = claimants.get(child.name)
        if ids is None:
            skips[child.name] = "no quarantine record — inspect manually"
            continue
        bad = ""
        for plan_id in sorted(ids):
            if plan_id in stale_ids:
                bad = (
                    f"{plan_names.get(plan_id, plan_id)} [{plan_id}: archive is stale]"
                )
                break
            if plan_id in file_ids:
                if plan_id not in gate1_ids:
                    bad = (
                        f"{plan_names.get(plan_id, plan_id)} "
                        f"[{plan_id}: {gate1_reason.get(plan_id, 'no archive folder')}]"
                    )
                    break
            elif plan_id not in by_plan:
                bad = (
                    f"{plan_names.get(plan_id, plan_id)} "
                    f"[{plan_id}: no plan file and no manifest]"
                )
                break
        if bad:
            skips[child.name] = bad
        else:
            purgeable.add(child.name)
    return quarantine, claimants, purgeable, skips


def _sweep_plan_files(
    records: list[dict[str, Any]],
    *,
    root: Path,
    dirs: list[dict[str, Any]],
    held_claimants: set[str],
    purge: bool,
) -> tuple[int, int, int]:
    """Print each plan file's final status — the LAST step per plan.

    A file is removable only when its gates pass AND no frame of its plan
    remains: still on disk in a directory this run would not prune (report)
    or still present after the purge (``purge``), or inside a quarantine
    directory the gates kept (``held_claimants``). Under ``purge`` the file
    is actually unlinked — only after its frames are gone — and every
    evaluated file appends a ``plan_file_deleted`` record to the root's
    purge index; a report run writes nothing. Returns
    ``(removed, kept, failed)``.
    """
    verdict_by_dir = {entry["dir"]: entry["verdict"] for entry in dirs}
    root_resolved = root.resolve()
    removed = kept = failed = 0
    for record in records:
        plan_file = record["file"]
        if not plan_file.is_file():
            continue  # already gone: nothing to do for this plan
        reason = ""
        if record["stale"] or not record["archive_ok"]:
            reason = record["gate_reason"]
        elif record["plan_id"] in held_claimants:
            reason = "frames still in quarantine"
        else:
            for path in record["selected"]:
                frame = Path(path)
                if not frame.is_file():
                    continue  # gone — the state this step waits for
                if purge:
                    reason = "frames still present"
                    break
                try:
                    top = frame.resolve().relative_to(root_resolved).parts[0]
                except (ValueError, IndexError):
                    reason = "frames still present"  # outside the root
                    break
                if verdict_by_dir.get(top) != "prune":
                    reason = "frames still present"
                    break
        if reason:
            kept += 1
            print(f"  ⏭ kept plan file {plan_file} ({reason})")
            if purge:
                _append_index(
                    root,
                    {
                        "at": _now().isoformat(timespec="seconds"),
                        "action": "plan-file",
                        "plan_id": record["plan_id"],
                        "plan_file": str(plan_file),
                        "folder": record["folder"],
                        "plan_file_deleted": False,
                        "reason": reason,
                    },
                )
            continue
        if not purge:
            print(f"  🗄 would remove plan file {plan_file}")
            continue
        try:
            plan_file.unlink()
        except OSError as exc:
            print(f"  ✗ cannot remove plan file {plan_file}: {exc}", file=sys.stderr)
            failed += 1
            _append_index(
                root,
                {
                    "at": _now().isoformat(timespec="seconds"),
                    "action": "plan-file",
                    "plan_id": record["plan_id"],
                    "plan_file": str(plan_file),
                    "folder": record["folder"],
                    "plan_file_deleted": False,
                    "reason": str(exc),
                },
            )
            continue
        removed += 1
        print(f"  🗄 removed plan file {plan_file}")
        _append_index(
            root,
            {
                "at": _now().isoformat(timespec="seconds"),
                "action": "plan-file",
                "plan_id": record["plan_id"],
                "plan_file": str(plan_file),
                "folder": record["folder"],
                "plan_file_deleted": True,
            },
        )
    return removed, kept, failed


# ------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nina-purge",
        description=(
            "Retire archived frames from the N.I.N.A. imaging root "
            "(quarantine first, delete only on request) — including each "
            "purged plan's original plan JSON. Dry-run by default; "
            "--apply quarantines, --purge really deletes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  nina-purge                                    # verdict table\n"
            "  nina-purge --apply                            # quarantine\n"
            "  nina-purge --purge                            # real delete\n"
            "  nina-purge --imaging-root /mnt/c/Users/me/Documents/N.I.N.A"
        ),
    )
    parser.add_argument(
        "--dest",
        default=".archive",
        help="archive directory holding manifests (default: ./.archive)",
    )
    parser.add_argument(
        "--imaging-root",
        default=None,
        help=(
            "N.I.N.A. image save path to purge frames from; overrides the "
            "site-profile lookup, so purging works while NINA is down"
        ),
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="project directory holding the plan files (default: cwd)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="quarantine prunable directories (default: report only)",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help=(
            "delete the quarantine for real and remove each purged plan's "
            "original plan JSON (independent of --apply; idempotent)"
        ),
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    project_dir = (
        Path(args.project_dir).expanduser() if args.project_dir else config.PROJECT_DIR
    )
    if not project_dir.is_absolute():
        project_dir = config.PROJECT_DIR / project_dir
    dest = Path(args.dest).expanduser()
    if not dest.is_absolute():
        dest = Path.cwd() / dest

    try:
        root = await _resolve_root(args)
    except PurgeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    if _inside(dest, root):
        # Archived ImageMetaData.csv copies inside the root would be read
        # by the next attribution pass and double-count every frame.
        print(
            f"✗ --dest ({dest}) is inside the imaging root ({root}); its "
            "archived CSV copies would double-count every frame on the next "
            "attribution pass. Pick a destination outside the image save "
            "path.",
            file=sys.stderr,
        )
        return 1

    try:
        rows: list[dict[str, Any]] = []
        dropped_rows: list[dict[str, Any]] = []
        for image_type in _FRAME_TYPES:
            kept, dropped = read_imaging_csv(
                root=root, image_type=image_type, report_dropped=True
            )
            rows.extend(kept)
            dropped_rows.extend(dropped)
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    now = _now()
    by_plan, preserved, manifest_warnings = _read_manifests(dest)
    for warning in manifest_warnings:
        print(f"  ⚠ {warning}", file=sys.stderr)

    # Claims: invert the archive's own selection. A plan-shaped file that
    # fails to load aborts — its claims are unknown, and a deletion made
    # on unknown claims is unsafe. Every file also runs its archive gates
    # (see _plan_file_gate) BEFORE anything is deleted; a stale archive
    # refuses the whole plan id.
    claims: dict[str, set[str]] = {}
    plan_names: dict[str, str] = {}
    released: set[str] = set()
    notes: dict[str, str] = {}
    errors: list[tuple[Path, str]] = []
    plan_records: list[dict[str, Any]] = []
    refusals: list[tuple[Path, str]] = []
    ignored = 0
    for plan_file in [p for p in _plan_files(project_dir) if not _inside(p, dest)]:
        try:
            plan: ObservationPlan = await plan_utils.load_plan(str(plan_file))
        except Exception as exc:  # noqa: BLE001 - report, then refuse below
            if _looks_like_plan_file(plan_file):
                errors.append((plan_file, str(exc).splitlines()[0]))
            else:
                ignored += 1
            continue
        plan_id = plan._base_plan_id()
        plan_names.setdefault(plan_id, plan_file.name)
        folder, archive_ok, stale, gate_reason = _plan_file_gate(plan_file, plan, dest)
        if stale:
            refusals.append((plan_file, gate_reason))
        selected = _select_frames(plan, rows, now)
        plan_records.append(
            {
                "file": plan_file,
                "plan": plan,
                "plan_id": plan_id,
                "folder": folder,
                "archive_ok": archive_ok,
                "stale": stale,
                "gate_reason": gate_reason,
                "selected": [
                    str(row["file_path"])
                    for frames in selected.values()
                    for row in frames
                    if row.get("file_path")
                ],
            }
        )
        for frames in selected.values():
            for row in frames:
                file_path = row.get("file_path")
                if file_path:
                    claims.setdefault(_norm(file_path), set()).add(plan_id)
    if errors:
        for plan_file, reason in errors:
            print(
                f"✗ {plan_file.name}: {reason} — purge refuses to guess "
                "this plan's claims",
                file=sys.stderr,
            )
        print(
            "✗ fix or move out the file(s) above; nothing was purged", file=sys.stderr
        )
        return 1

    if refusals:
        # Gate 2: the plan file and its archive disagree — refuse the whole
        # plan (frames held, file kept), keep sweeping the other plans.
        for plan_file, reason in refusals:
            print(
                f"✗ {plan_file.name}: {reason} — refusing to release or "
                "remove this plan's frames or file",
                file=sys.stderr,
            )

    # Release: archived AND gates pass AND (complete now OR archived under
    # --all). The manifest answers "archived", the shared verdict answers
    # "still finished", the waiver covers a deliberately shipped incomplete
    # plan, and the gates keep a stale or vanished archive from releasing
    # anything. Computed per plan id after the loop: any file carrying a
    # stale copy refuses the whole id, while an id archived through at least
    # one gate-passing file stays eligible.
    for plan_id in sorted({r["plan_id"] for r in plan_records}):
        records = [r for r in plan_records if r["plan_id"] == plan_id]
        if any(r["stale"] for r in records):
            notes[plan_id] = "archive is stale, re-run nina-archive --force first"
            continue
        manifest = by_plan.get(plan_id)
        if manifest is None:
            # Not archived: nothing else can release it, so skip the
            # (expensive) completeness recomputation.
            notes[plan_id] = "not archived"
            continue
        passing = [r for r in records if r["archive_ok"]]
        if not passing:
            notes[plan_id] = records[0]["gate_reason"]
            continue
        waived = manifest.get("complete") is False  # only an explicit --all
        complete_now, counts = _progress_facts(passing[0]["plan"], rows, now)
        if waived or complete_now:
            released.add(plan_id)
        else:
            deficit = ", ".join(
                f"{t.upper()} {n['acquired']}/{n['expected']}"
                for t, n in counts.items()
                if n["acquired"] < n["expected"]
            )
            notes[plan_id] = f"archived, incomplete now ({deficit})"

    # BAD_ twins: the CSV names the pre-rename file, so read_imaging_csv
    # dropped the row; the twin on disk is N.I.N.A.'s reject — garbage
    # that was never archived and can never be claimed.
    rejects: list[Path] = []
    gone = 0
    for row in dropped_rows:
        path = str(row.get("file_path") or "")
        if not path:
            continue
        twin = Path(path).with_name(f"BAD_{Path(path).name}")
        if twin.is_file():
            rejects.append(twin)
        else:
            gone += 1

    dirs, outside = _classify(
        root, rows, rejects, claims, released, preserved, plan_names, notes
    )
    prunable = [e for e in dirs if e["verdict"] == "prune"]
    held = [e for e in dirs if e["verdict"] == "hold"]

    print(
        f"🔍 {len(dirs)} director(y|ies) under {root}: "
        f"{len(prunable)} prunable, {len(held)} held"
        + (
            f", {len(outside)} frame(s) outside the root (never pruned)"
            if outside
            else ""
        )
    )
    for entry in dirs:
        icon = "✂️ " if entry["verdict"] == "prune" else "🔒"
        print(
            f"  {icon} {entry['dir']:<40} {entry['verdict'].upper():<6} "
            f"{entry['frames']} frames, {entry['rejects']} rejects, "
            f"{_human_bytes(entry['bytes'])}  ({entry['reason']})"
        )
    if rejects:
        print(
            f"  💽 {len(rejects)} BAD_ reject twin(s) ride with their "
            "directories (never archived, never block)"
        )
    if gone:
        print(f"  💽 {gone} recorded frame(s) already gone — nothing to prune")
    for item in outside[:5]:
        print(f"  ⚠ {item['path']}: outside the imaging root — not prunable")
    if ignored:
        print(f"  ({ignored} unrelated JSON file(s) ignored)")

    errors_occurred = False
    actionable = 0

    # Root mutations: refuse while a sequence may be writing.
    root_wants_mutation = (args.apply and prunable) or args.purge
    if root_wants_mutation:
        allowed, message = await _idle_state()
        if not allowed:
            print(f"✗ {message}", file=sys.stderr)
            errors_occurred = True
        elif message:
            print(f"  ⚠ {message}")

    if args.apply and prunable and not errors_occurred:
        moved = _quarantine(root, prunable)
        actionable += len(moved)
        if moved:
            print(
                f"\n✅ {len(moved)} director(y|ies) quarantined — nothing was "
                "deleted. Live with it, then: nina-purge --purge"
            )
            hinted = sorted(
                {
                    plan_names[pid]
                    for entry in moved
                    for pid in entry["plan_ids"]
                    if pid in plan_names
                }
            )
            if hinted:
                print(
                    "📌 the quarantined plan(s)' files read as NEVER_SHOT "
                    "until nina-purge --purge removes them automatically "
                    "(their verbatim plan.json is in the archive):"
                )
                for name in hinted:
                    print(f"     {name}")
    elif args.apply and not errors_occurred:
        print("\n🔍 nothing to quarantine")

    if args.purge and not errors_occurred:
        # Gates first (they were checked before any deletion above): only
        # directories whose claiming plans still pass them are unlinked.
        quarantine_dir, claimants, purgeable, skip_reasons = _quarantine_gates(
            root, plan_records, by_plan, plan_names
        )
        purged, kept_dirs, failed = _purge_quarantine(root, purgeable, skip_reasons)
        actionable += purged
        if kept_dirs or failed:
            print(
                f"  ⚠ {kept_dirs + failed} quarantined item(s) kept — fix "
                "the reason(s) above, then re-run nina-purge --purge",
                file=sys.stderr,
            )
            errors_occurred = True
        # Plan files: the FINAL step per plan — only once its frames are
        # actually gone. Anything still on disk (or still quarantined in a
        # kept directory) keeps its file for the re-run.
        held_ids: set[str] = set()
        if quarantine_dir is not None and quarantine_dir.is_dir():
            for child in quarantine_dir.iterdir():
                held_ids |= claimants.get(child.name, set())
        swept, _kept, sweep_failed = _sweep_plan_files(
            plan_records, root=root, dirs=dirs, held_claimants=held_ids, purge=True
        )
        actionable += swept
        if sweep_failed:
            errors_occurred = True

    # A pure verdict-table run (no action flags) is actionable when it
    # FOUND work: exit 0 means "something would be quarantined" per the
    # documented contract. Explicit legs count only their own results, so
    # e.g. `--purge` on an empty quarantine honestly exits 1.
    if not (args.apply or args.purge):
        actionable += len(prunable)
        # Plan-file preview: what the --apply → --purge chain would remove.
        # Not counted as actionable — only --purge ever removes a file.
        _qdir, q_claimants, q_purgeable, q_skips = _quarantine_gates(
            root, plan_records, by_plan, plan_names
        )
        for dirname, why in sorted(q_skips.items()):
            print(f"  ⏭ {dirname}: would keep in quarantine — {why}")
        held_ids = {
            plan_id
            for dirname, ids in q_claimants.items()
            if dirname not in q_purgeable
            for plan_id in ids
        }
        _sweep_plan_files(
            plan_records, root=root, dirs=dirs, held_claimants=held_ids, purge=False
        )

    if errors_occurred or refusals:
        return 1
    if actionable == 0:
        pure_report = not (args.apply or args.purge)
        print("\n🔍 nothing to do" + (" (dry run)" if pure_report else ""))
        # --purge is idempotent: an already-purged state is success, not
        # "nothing to do". Report/--apply/dest legs keep the chainable
        # contract: 1 means nothing was found to act on.
        return 0 if args.purge else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
