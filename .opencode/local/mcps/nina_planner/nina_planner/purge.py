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
Files no CSV row ever recorded stay invisible by design (the Session
Metadata plugin is trusted): they ride along into quarantine, are counted
in the index record, and are never classified.

Deletion is two deliberate steps. ``--apply`` MOVES each prunable
directory into ``<imaging-root>/.prune-quarantine/<dir>`` — a dot
directory every attribution walk prunes (``imaging._prunable_dir``), so
quarantined frames and their ImageMetaData.csv behave exactly as if
gone, while one ``mv`` restores them — and only ``--purge`` unlinks.
``<imaging-root>/prune-index.jsonl`` appends one JSON record per action.
A run refuses to touch the root while a sequence is RUNNING (N.I.N.A.
may be appending CSV rows); an unreachable N.I.N.A. is treated as idle —
the same posture ``nina-archive`` takes — and ``--imaging-root`` bypasses
the profile lookup entirely.

This tool never deletes or modifies the archive folders under ``--dest``:
they are the preserved copy. Confirm your own cloud upload and local
download are intact before running ``--purge``.

Plan files are never touched — but a quarantined plan's file, left in
the project directory, now has zero attributed rows and reads as
NEVER_SHOT to ``audit_plans``/``get_plan_progress``; the console names
each such file (the archive folder already holds the verbatim
``plan.json``). A plan-shaped JSON that fails to load ABORTS the run:
its claims are unknown, and unknown claims make deletions unsafe —
unrelated project JSON is still ignored silently.

Exit codes: 0 — something was (or, in a dry run, would be) quarantined
or purged; 1 — nothing to do or an error; 2 — bad arguments.
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

_QUARANTINE_DIR = ".prune-quarantine"
_INDEX_NAME = "prune-index.jsonl"
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


def _purge_quarantine(root: Path) -> int:
    """Actually delete the quarantine. Returns how many items removed."""
    quarantine = root / _QUARANTINE_DIR
    if not quarantine.is_dir():
        print(f"nothing to purge — no quarantine at {quarantine}")
        return 0
    removed = files = total = 0
    for child in sorted(quarantine.iterdir()):
        try:
            if child.is_dir():
                count, size = _tree_size(child)
                shutil.rmtree(child)
            else:
                count, size = 1, child.stat().st_size
                child.unlink()
        except OSError as exc:
            print(f"  ✗ {child.name}: {exc}", file=sys.stderr)
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
    return removed


# ------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nina-purge",
        description=(
            "Retire archived frames from the N.I.N.A. imaging root "
            "(quarantine first, delete only on request). Dry-run by "
            "default; --apply quarantines, --purge really deletes."
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
        default="archive",
        help="archive directory holding manifests (default: ./archive)",
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
        help="delete the quarantine for real (independent of --apply)",
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
    # on unknown claims is unsafe. Release = archived AND (complete now
    # OR archived under --all): the manifest answers "archived", the
    # shared verdict answers "still finished", and the waiver covers a
    # deliberately shipped incomplete plan.
    claims: dict[str, set[str]] = {}
    plan_names: dict[str, str] = {}
    released: set[str] = set()
    notes: dict[str, str] = {}
    errors: list[tuple[Path, str]] = []
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
        manifest = by_plan.get(plan_id)
        if manifest is None:
            # Not archived: nothing else can release it, so skip the
            # (expensive) completeness recomputation.
            notes[plan_id] = "not archived"
        else:
            waived = manifest.get("complete") is False  # only an explicit --all
            complete_now, counts = _progress_facts(plan, rows, now)
            if waived or complete_now:
                released.add(plan_id)
            else:
                deficit = ", ".join(
                    f"{t.upper()} {n['acquired']}/{n['expected']}"
                    for t, n in counts.items()
                    if n["acquired"] < n["expected"]
                )
                notes[plan_id] = f"archived, incomplete now ({deficit})"
        for frames in _select_frames(plan, rows, now).values():
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
                    "📌 move the quarantined plan(s)' files out of the "
                    "project directory (their rows went with the frames; "
                    "they now read as NEVER_SHOT):"
                )
                for name in hinted:
                    print(f"     {name}")
    elif args.apply and not errors_occurred:
        print("\n🔍 nothing to quarantine")

    if args.purge and not errors_occurred:
        actionable += _purge_quarantine(root)

    # A pure verdict-table run (no action flags) is actionable when it
    # FOUND work: exit 0 means "something would be quarantined" per the
    # documented contract. Explicit legs count only their own results, so
    # e.g. `--purge` on an empty quarantine honestly exits 1.
    if not (args.apply or args.purge):
        actionable += len(prunable)

    if errors_occurred:
        return 1
    if actionable == 0:
        pure_report = not (args.apply or args.purge)
        print("\n🔍 nothing to do" + (" (dry run)" if pure_report else ""))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
