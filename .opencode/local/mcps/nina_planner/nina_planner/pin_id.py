"""nina-pin-id — stamp a plan's derived id into its plan_id field.

A blank ``plan_id`` is valid and meaningful: it means "derive me from my
content" — ``ObservationPlan._base_plan_id()`` hashes the plan's own JSON
(minus ``plan_id``) into ``plan-<hex12>``. That keeps a fresh plan
self-describing, but it makes the id *volatile*: edit the plan — one more
light group, a raised ``total_count`` — and the hash changes with it,
while every frame already shot still carries the OLD id as the
``_{plan_id}_`` token N.I.N.A. wrote into its file path. The already-shot
frames then attribute to no live plan: ``get_plan_progress`` reports zero
acquired, ``nina-archive`` builds a different folder name, and
``nina-purge`` no longer sees the plan claiming its frames.

Pinning freezes the id *before* the edit: the current derived id is
written into the ``plan_id`` field, and from then on ``_base_plan_id()``
returns that literal value instead of re-hashing, so the plan can be
edited freely while its frames keep attributing to it. Nothing else
changes — the plan is loaded through the ordinary ``plan_utils.load_plan``
(so a file that would not load is reported, never half-written) and
rewritten with the same serializer ``write_plan_file`` uses
(``model_dump_json(indent=2)``). The write is atomic: a temp file in the
plan's own folder, then ``os.replace`` — an interrupted pin can never
truncate or half-write a plan file.

An already-pinned plan is a no-op (``already pinned: <id>``): pinning
never rewrites an explicit id, so a hand-written ``plan_id`` survives
re-running the tool too.

Pin BEFORE editing. Once a blank-id plan is edited its old hash cannot be
recomputed — it can only be recovered from an archive folder name
(``<stem>__<plan-id>``) or from frame paths (they contain
``_<plan_id>_``).

Exit codes: 0 — every named plan was pinned (or already was, or would be
in a dry run), 1 — a plan could not be loaded or written, 2 — bad
arguments (argparse's own usage error, as nina-archive).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from . import config, plan_utils


class PinError(Exception):
    """A plan could not be pinned; the message is user-facing."""


# ---------------------------------------------------------------- helpers


def _resolve(entry: str) -> Path:
    """Absolute plan path — the same rule ``plan_utils.load_plan`` applies."""
    path = Path(entry).expanduser()
    if not path.is_absolute():
        path = config.PROJECT_DIR / path
    return path


def _write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` via a temp sibling + ``os.replace``.

    The temp file lives in the plan's OWN folder so the rename is atomic
    (same filesystem) and no stray file appears anywhere a plan discovery
    walk could see it for longer than this call. Its permission bits are
    reset to the original file's, so a pin never tightens permissions on
    a plan others read; on failure the temp file is removed and the
    original is left untouched.
    """
    original_mode = path.stat().st_mode & 0o7777
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, original_mode)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------- pinning


async def pin_plan_file(plan_file: str | Path, *, dry_run: bool = False) -> str:
    """Pin one plan file; return the message to print for it.

    A plan that already carries a ``plan_id`` is a no-op — its explicit id
    is never overwritten. Otherwise the id the plan derives RIGHT NOW
    (``_base_plan_id()`` on the loaded, unedited content) is stamped into
    ``plan_id`` and the file rewritten with the canonical serializer;
    ``dry_run`` reports the same message without touching the file.
    Raises ``PinError`` (user-facing message) when the plan cannot be
    loaded or written.
    """
    path = _resolve(str(plan_file))
    try:
        plan = await plan_utils.load_plan(str(path))
    except Exception as exc:  # noqa: BLE001 - any load failure is one report
        raise PinError(f"{path.name}: {str(exc).splitlines()[0]}") from exc
    if plan.plan_id:
        return f"already pinned: {plan.plan_id}"
    plan_id = plan._base_plan_id()
    if dry_run:
        return f"would pin {path}: {plan_id}"
    pinned = plan.model_copy(update={"plan_id": plan_id})
    try:
        _write_atomic(path, pinned.model_dump_json(indent=2))
    except OSError as exc:
        raise PinError(f"{path.name}: could not write ({exc})") from exc
    return f"pinned {path}: {plan_id}"


# ------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nina-pin-id",
        description=(
            "Stamp a plan file's current derived id into its plan_id field, "
            "changing nothing else. Pin a plan BEFORE editing it (more "
            "lights, a raised total_count) so its id — and the frames "
            "already shot under it — survive the edit."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  nina-pin-id plans/m15_globular-cluster_20261003T112008.json\n"
            "  nina-pin-id --dry-run plan1.json plan2.json"
            "\n"
            "Pin BEFORE editing: once a blank-id plan is edited its old hash\n"
            "cannot be recomputed — it survives only in an archive folder\n"
            "name or in frame paths (they contain _<plan_id>_)."
        ),
    )
    parser.add_argument(
        "plans",
        nargs="+",
        metavar="PLAN.json",
        help="plan files to pin",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be pinned; write nothing",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    errors = 0
    for entry in args.plans:
        path = _resolve(entry)
        if not path.is_file():
            print(f"✗ plan file not found: {path}", file=sys.stderr)
            errors += 1
            continue
        try:
            message = await pin_plan_file(path, dry_run=args.dry_run)
        except PinError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            errors += 1
            continue
        if message.startswith("already pinned"):
            print(f"⏭  {message}")
        elif args.dry_run:
            print(f"🔍 {message}")
        else:
            print(f"📌 {message}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
