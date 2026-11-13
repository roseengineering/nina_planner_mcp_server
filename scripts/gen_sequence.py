#!/usr/bin/env python3
"""Generate a N.I.N.A. sequence from an observation plan; print it to stdout.

Usage:
    scripts/gen_sequence.py PLAN.json [--action light|dark|bias|flat] [options]

Builds exactly the sequence ``load_sequence`` would send for one entry of its
request list — the same builders, validations and guardrails — but writes the
JSON to stdout instead of POSTing it, so a sequence can be inspected, diffed
or archived without touching N.I.N.A.

    # the default: the plan's lights, the whole plan, JSON on stdout
    scripts/gen_sequence.py plans/m15_globular-cluster_20261003T112008.json > seq.json

    # hand it to N.I.N.A. and let the sequencer say whether it accepts it
    scripts/gen_sequence.py plans/m15_globular-cluster_20261003T112008.json --load

``--load`` POSTs ``/sequence/load`` — N.I.N.A. validates the sequence there
and refuses it with its own error — and then stops: nothing is ever started,
which stays a separate, deliberate act. stdout carries only the JSON in every
mode (notes, warnings and errors go to stderr), so ``--load`` changes where
the sequence goes, never what is printed.

The profile (filter-wheel positions, ``FilePattern``) and the equipment
(mount park/home capabilities, camera) are read live from N.I.N.A., exactly
as ``load_sequence`` reads them; reading the equipment status auto-connects
any device that is present but disconnected. The endpoint comes from
``NINA_ENDPOINT`` (default ``127.0.0.1:1888``).

Exit codes: 0 — the sequence was built (and accepted, with ``--load``);
1 — the plan, the rig, or N.I.N.A. refused; 2 — bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

try:
    from nina_planner import client, config, metadata, plan_utils
    from nina_planner.models.observatory import ObservatoryEquipment
    from nina_planner.models.profile import ObservatoryProfile
    from nina_planner.progress import (
        group_verdict,
        plan_progress,
        plan_with_remaining,
    )
    from nina_planner.sequence import (
        build_sequence_many,
        target_payload_darks,
        target_payload_flats,
        target_payload_lights,
        validate_dark_variant,
    )
    from nina_planner.tools.equipment import (
        _get_site_equipment_status,
        get_site_profile,
    )
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "✗ nina_planner is not importable from this interpreter — run the "
        "script through the project environment:\n"
        "    uv run --project .opencode/local/mcps/nina_planner "
        "python scripts/gen_sequence.py <plan.json>"
    ) from exc


class ScriptError(Exception):
    """A refusal whose message is meant for the operator, not a traceback."""


# ------------------------------------------------------------- live inputs


async def _fetch_profile() -> ObservatoryProfile:
    """The active profile, or say why N.I.N.A. could not be reached."""
    try:
        return await get_site_profile()
    except Exception as exc:
        raise ScriptError(
            f"cannot read the N.I.N.A. profile from {config.NINA_API_URL} "
            f"— is N.I.N.A. running? ({exc})"
        ) from exc


async def _fetch_equipment(profile: ObservatoryProfile) -> ObservatoryEquipment:
    """The connected equipment (auto-connecting what is present but down)."""
    try:
        return await _get_site_equipment_status(profile)
    except Exception as exc:
        raise ScriptError(f"cannot read N.I.N.A.'s equipment status: {exc}") from exc


# --------------------------------------------------------------- building


async def _generate(args: argparse.Namespace) -> tuple[dict[str, Any] | None, str]:
    """Build the sequence for ``args``, or decide there is nothing to build.

    Returns ``(sequence, note)``; ``sequence is None`` means nothing to
    load — the note says why (complete, or a calibration window expired
    under a deficit), and the caller reports it on stderr with exit 0,
    exactly as ``load_sequence`` reports a complete plan.
    """
    action: str = args.action
    # Every action reads the plan's same-named exposure group.
    group = action
    variant: str | None = args.variant
    pointing_index = args.pointing_index or 1
    where = f" pointing {pointing_index}" if action == "light" else ""
    # Reported per step so the caller sees which recipe (variant) ran —
    # the same label load_sequence shows.
    label = f"`{action}` ({variant})" if variant is not None else f"`{action}`"

    profile = await _fetch_profile()
    try:
        plan = await plan_utils.load_plan(args.plan)
    except Exception as exc:
        raise ScriptError(f"{args.plan}: {exc}") from exc

    # Same validation order as load_sequence: cheap plan/profile checks
    # first, so a bad plan never auto-connects a device on the way to failing.
    await plan_utils.validate_filters(plan, profile)
    plan_utils.validate_position_angle(plan, profile)
    if action == "light":
        plan_utils.check_pointing_index(plan, pointing_index)
    equipment = await _fetch_equipment(profile)
    if action in ("dark", "bias"):
        # The default (`wheel`) passes through this check exactly as an
        # explicit variant does, so an omitted variant never silently picks
        # another way to cut the light.
        validate_dark_variant(variant or "wheel", equipment, profile)

    if args.mode == "remaining":
        try:
            rows = await metadata.read_metadata()
        except (RuntimeError, FileNotFoundError) as exc:
            raise ScriptError(
                f"cannot compute remaining frames: {exc}. "
                "Pass --mode full to generate the whole plan regardless."
            ) from exc
        # One anchor for the whole build, as in load_sequence.
        now = datetime.now(UTC).replace(microsecond=0)
        progress = plan_progress(
            plan,
            rows,
            pointing_index=pointing_index,
            **plan_utils.quality_kwargs(plan),
            now=now,
        )
        items = progress[group]
        if all(item["remaining_count"] == 0 for item in items):
            total = sum(item["total_count"] for item in items)
            # remaining_count == 0 is not proof of completion: read the
            # shared rule (group_verdict) so an expired calibration window
            # with frames still missing says so instead of "complete".
            verdicts = [group_verdict(item) for item in items]
            if "expired_with_deficit" in verdicts:
                expired = sum(
                    int(item.get("deficit_count", 0))
                    for item, verdict in zip(items, verdicts, strict=True)
                    if verdict == "expired_with_deficit"
                )
                return None, (
                    f"{label}{where} — calibration window EXPIRED with "
                    f"{expired} frame(s) short of {total} total. Nothing to "
                    "load: no frame shot now could be credited. Raise the "
                    "plan's calibration_window_days or write a new plan to "
                    "reopen the window."
                )
            return None, (
                f"{label}{where} is complete — {total} of {total} frames "
                "already acquired; nothing to load."
            )
        note = f"{sum(item['remaining_count'] for item in items)} frame(s) to acquire"
        plan = plan_with_remaining(plan, progress)
    else:
        frame_groups: dict[str, list[Any]] = {
            "light": plan.light_frames,
            "dark": plan.dark_frames,
            "bias": plan.bias_frames,
            "flat": plan.flat_frames,
        }
        total = sum(f.total_count for f in frame_groups[group])
        note = f"{total} frame(s), mode=full"

    # Attribution guard: without $$TARGETNAME$$ in the FilePattern the
    # frames N.I.N.A. writes carry no plan id and attribute to nothing.
    if (
        action == "light"
        and not args.allow_unattributed
        and "$$TARGETNAME$$" not in (profile.file_pattern or "")
    ):
        raise ScriptError(
            "N.I.N.A. FilePattern must contain $$TARGETNAME$$ for "
            "light-frame plan attribution (pass --allow-unattributed to "
            "generate the sequence anyway)."
        )

    if action == "light":
        payload = target_payload_lights(
            plan, equipment, profile, pointing_index=pointing_index
        )
    elif action == "bias":
        payload = target_payload_darks(
            plan,
            equipment,
            profile,
            bias=True,
            variant=variant or "wheel",
        )
    elif action == "dark":
        payload = target_payload_darks(
            plan, equipment, profile, variant=variant or "wheel"
        )
    else:  # flat — validated to carry a dawn/dusk variant
        payload = target_payload_flats(plan, equipment, profile, dusk=variant == "dusk")

    # The same park guard load_sequence applies before anything is sent: the
    # End area always announces a park, and a mount that cannot park would
    # make that announcement around an instruction list that does nothing.
    mount = equipment.mount
    if mount is None or not mount.can_park:
        raise ScriptError(
            "Mount cannot park (can_park=False) — the End area would "
            "announce PARK around an instruction list that does nothing. "
            "Nothing was generated."
        )

    sequence = build_sequence_many(equipment, [payload])
    return sequence, f"{label}{where} — {note}"


# -------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen_sequence.py",
        description=(
            "Generate a N.I.N.A. sequence from an observation plan and write "
            "the sequence JSON to stdout. Nothing is loaded into N.I.N.A. "
            "unless --load is given, and nothing is ever started."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  scripts/gen_sequence.py plans/m15_...json > seq.json\n"
            "  scripts/gen_sequence.py plan.json --action dark --variant cover\n"
            "  scripts/gen_sequence.py plan.json --action flat --variant dawn\n"
            "  scripts/gen_sequence.py plan.json --load\n"
            "\n"
            "The default action is `light` and the default mode is `full`,\n"
            "so a bare plan path generates that plan's whole lights sequence."
        ),
    )
    parser.add_argument(
        "plan",
        metavar="PLAN.json",
        help="observation-plan JSON file (relative paths resolve against cwd)",
    )
    parser.add_argument(
        "--action",
        choices=("light", "dark", "bias", "flat"),
        default="light",
        help="what to build from the plan (default: light)",
    )
    parser.add_argument(
        "--variant",
        default=None,
        help=(
            "HOW the action runs, scoped to it: wheel|cover|shutter for "
            "dark/bias (default wheel), dawn|dusk for flat (required); "
            "not valid for light"
        ),
    )
    parser.add_argument(
        "--pointing-index",
        type=int,
        default=None,
        metavar="N",
        help="1-based pointing to build (default: 1); lights only",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "remaining"),
        default="full",
        help=(
            "full: the whole plan as written (default); remaining: only "
            "frames not yet attributed in the imaging metadata"
        ),
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help=(
            "POST the built sequence to N.I.N.A. (/sequence/load) so the "
            "sequencer validates and accepts it — never starts it; the JSON "
            "still goes to stdout"
        ),
    )
    parser.add_argument(
        "--allow-unattributed",
        action="store_true",
        help=(
            "generate lights even when the profile FilePattern lacks "
            "$$TARGETNAME$$ (those frames would not attribute to this plan)"
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="single-line JSON instead of indent=2",
    )
    return parser


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject a bad action/variant/pointing pairing as a usage error (2)."""
    if args.action == "light":
        if args.variant is not None:
            parser.error(f"--action light takes no --variant (got {args.variant!r})")
    elif args.action == "flat":
        if args.variant not in ("dawn", "dusk"):
            parser.error("--action flat requires --variant dawn or --variant dusk")
    elif args.variant not in (None, "wheel", "cover", "shutter"):
        parser.error(
            f"--action {args.action} --variant must be wheel, cover or "
            f"shutter (got {args.variant!r})"
        )
    if args.pointing_index is not None:
        if args.action != "light":
            parser.error(
                f"--pointing-index applies to lights only (got --action {args.action})"
            )
        if args.pointing_index < 1:
            parser.error("--pointing-index must be >= 1")


async def _run(args: argparse.Namespace) -> int:
    sequence, note = await _generate(args)
    print(note, file=sys.stderr)
    if sequence is None:
        # Nothing to build: an empty stdout and exit 0, so `> seq.json`
        # leaves an empty file rather than a stale one.
        return 0
    sys.stdout.write(
        json.dumps(
            sequence,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
        + "\n"
    )
    if args.load:
        try:
            await client.load_sequence_payload(sequence)
        except Exception as exc:
            raise ScriptError(f"N.I.N.A. refused the sequence: {exc}") from exc
        print(
            "✅ sequence accepted by N.I.N.A. — loaded, not started; call "
            "start_sequence() (or GET /v2/api/sequence/start) to begin.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate(parser, args)
    try:
        return asyncio.run(_run(args))
    except (ScriptError, ValueError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("✗ interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
