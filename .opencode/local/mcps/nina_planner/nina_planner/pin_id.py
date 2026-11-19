"""nina-pin-id — stamp a plan's derived id into its plan_id field.

A blank ``plan_id`` means "derive me from my content": ``_base_plan_id()``
hashes the plan's own JSON (minus ``plan_id``) into ``plan-<hex12>``. That
keeps a fresh plan self-describing but makes the id *volatile* — edit the
plan and the hash changes, while every frame already shot still carries the
OLD id as the ``_{plan_id}_`` token N.I.N.A. wrote into its file path. Those
frames then attribute to no live plan: ``get_plan_progress`` reads zero
acquired, ``nina-archive`` builds a different folder name, and ``nina-purge``
stops seeing the plan claiming its frames.

Pinning freezes the id *before* the edit: the current derived id is written
into ``plan_id``, so the plan can be edited freely while its frames keep
attributing to it. The file is loaded through ``plan_utils.load_plan`` (a
file that would not load is reported, never half-written) and rewritten
atomically — a temp file in the plan's own folder, then ``os.replace`` —
so an interrupted pin can never truncate a plan.

An already-pinned plan is a no-op (``already pinned: <id>``): an explicit
id is never overwritten, so a hand-written ``plan_id`` survives re-runs too.

Exit codes: 0 — every named plan was pinned (or already was, or would be
in a dry run), 1 — a plan could not be loaded or written, 2 — bad
arguments (argparse's own usage error, as nina-archive).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
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


# ------------------------------------------------------------- pinning


async def pin_plan_file(plan_file: str | Path, *, dry_run: bool = False) -> str:
    """Pin one plan file; return the message to print for it.

    An already-pinned plan is a no-op — its explicit id is never
    overwritten. Otherwise the id the plan derives RIGHT NOW is stamped into
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
        plan_utils.write_text_atomic(path, pinned.model_dump_json(indent=2))
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
    """Pin every plan named on the command line; return the process exit code.

    Per-plan failures (missing file, unloadable or unwritable plan) are
    printed to stderr and counted, but never abort the remaining plans.
    """
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
    """CLI entry point: parse ``argv`` (default ``sys.argv[1:]``) and run the pins.

    Returns:
        0 when every plan was pinned (or already was, or a dry run), 1 when any
        plan failed. Argparse exits 2 on bad usage itself.
    """
    args = _build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
