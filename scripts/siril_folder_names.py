#!/usr/bin/env python3
"""Rename nina-archive type folders to the names Siril's scripts expect.

Siril's built-in preprocessing scripts (``OSC_Preprocessing.ssf`` and
friends) are case-sensitive and read exactly four directories out of their
working directory: ``lights``, ``darks``, ``flats``, ``biases``.  An
archived plan folder is meant to *be* a Siril working directory, so those
are the names it has to use.  ``nina-archive`` used to write ``LIGHT/``,
``FLAT/``, ``DARK/``, ``BIAS/`` and ``JUNK/``; the code now writes the
lowercase plural names (``nina_planner.archive._FOLDER_NAMES``), and this
script fixes archives that were built before that.

Two things move together, and moving only one of them leaves the archive
lying:

1. the directory on disk, and
2. ``manifest.json``'s ``files[].path`` — the receipt that says where each
   archived frame lives.  ``files[].type`` is left alone: it is N.I.N.A.'s
   ``ImageType``, uppercase, and nothing reads it to find a file.

Nothing else in a manifest names an archive-relative path — ``source``,
``disk.*`` and ``missing[]`` all point at the imaging root.

Modes (both are dry runs without ``--apply``):

    # local archive: rename directories + rewrite receipts
    python3 scripts/siril_folder_names.py --dest ~/landing/soyuz --apply

    # cloud archive: rename object prefixes server-side (no re-upload),
    # then rewrite the same receipts in each manifest.json
    python3 scripts/siril_folder_names.py \
        --gcs gs://astronomyimaging/landing/soyuz --apply

Exit status: 0 when everything is already correct or was fixed, 1 when
anything could not be fixed (both names present, a receipt that resolves
nowhere) — in which case that folder is left untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# Keep in step with nina_planner.archive._FOLDER_NAMES (duplicated here so
# this script runs on a bare python3, without the project venv).
FOLDERS: dict[str, str] = {
    "LIGHT": "lights",
    "FLAT": "flats",
    "DARK": "darks",
    "BIAS": "biases",
    "JUNK": "junk",
}
MANIFEST_NAME = "manifest.json"


# ------------------------------------------------------------------ local


def fix_local(dest: Path, apply: bool) -> int:
    """Rename type dirs and rewrite manifest paths under ``dest``."""
    if not dest.is_dir():
        print(f"✗ not a directory: {dest}", file=sys.stderr)
        return 1

    plan_dirs = sorted(
        p for p in dest.iterdir() if p.is_dir() and (p / MANIFEST_NAME).is_file()
    )
    if not plan_dirs:
        print(f"no archive folders (no {MANIFEST_NAME}) under {dest}")
        return 0

    problems = 0
    renamed = 0
    rewritten = 0
    receipts = 0
    clean = 0

    for plan_dir in plan_dirs:
        folder_problems = 0

        # 1. directories on disk.  Compare the exact on-disk names rather
        # than probing with Path: APFS is case-insensitive, so
        # `plan_dir / "JUNK"` resolves to an already-renamed `junk/` and a
        # finished folder would read as a clash.
        on_disk = set(os.listdir(plan_dir))
        for old, new in FOLDERS.items():
            if old in on_disk and new in on_disk:
                print(f"✗ {plan_dir.name}: both {old}/ and {new}/ exist")
                folder_problems += 1
            elif old in on_disk:
                print(f"  {plan_dir.name}: {old}/ → {new}/")
                if apply:
                    (plan_dir / old).rename(plan_dir / new)
                renamed += 1

        # 2. the receipts in manifest.json
        manifest = plan_dir / MANIFEST_NAME
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"✗ {plan_dir.name}: unreadable {MANIFEST_NAME}: {exc}")
            problems += 1
            continue

        changed = 0
        for entry in data.get("files", []):
            head, sep, tail = str(entry.get("path", "")).partition("/")
            if sep and head in FOLDERS:
                entry["path"] = f"{FOLDERS[head]}/{tail}"
                changed += 1

        if changed:
            # Every receipt must resolve before any of them is written: a
            # manifest that names a file which is not there is worse than
            # one that names the old folder.
            for entry in data["files"]:
                if not (plan_dir / str(entry.get("path", ""))).is_file():
                    print(f"✗ {plan_dir.name}: no such file {entry.get('path')}")
                    folder_problems += 1
            if folder_problems == 0:
                print(f"  {plan_dir.name}: manifest {changed} path(s) → lowercase")
                if apply:
                    manifest.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                rewritten += 1
                receipts += changed
        elif folder_problems == 0:
            clean += 1

        problems += folder_problems

    print(
        f"\n{len(plan_dirs)} archive folder(s): {renamed} director(ies) renamed, "
        f"{receipts} receipt path(s) in {rewritten} manifest(s), "
        f"{clean} already correct, {problems} problem(s)"
    )
    if not apply and (renamed or rewritten):
        print("dry run — pass --apply to make those changes")
    return 1 if problems else 0


# -------------------------------------------------------------------- gcs


def _gcs_list(url: str) -> list[str]:
    """Every object URL under ``url`` (a prefix), or ``[]`` on failure."""
    result = subprocess.run(
        ["gcloud", "storage", "ls", "--recursive", f"{url.rstrip('/')}/"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return []
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("gs://")
    ]


def _gcs_mv(src: str, dst: str) -> bool:
    result = subprocess.run(
        ["gcloud", "storage", "mv", src, dst],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"✗ mv {src} failed:\n{result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def _gcs_fix_receipts(url: str, plan_dirs: set[str], apply: bool) -> tuple[int, int]:
    """Rewrite each plan folder's manifest ``files[].path`` to the new names.

    The rename moves the OBJECTS; this moves the receipt that says where each
    archived frame lives, so a manifest never names a folder that is not
    there.  ``files[].type`` is left alone — it is N.I.N.A.'s ImageType, and
    nothing reads it to find a file.
    """
    base = url.rstrip("/")
    changed = 0
    problems = 0
    for plan in sorted(plan_dirs):
        obj = f"{base}/{plan}/{MANIFEST_NAME}"
        result = subprocess.run(
            ["gcloud", "storage", "cat", obj],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"✗ {plan}: cannot read {MANIFEST_NAME}", file=sys.stderr)
            problems += 1
            continue
        try:
            data = json.loads(result.stdout)
        except ValueError as exc:
            print(f"✗ {plan}: unreadable {MANIFEST_NAME}: {exc}", file=sys.stderr)
            problems += 1
            continue
        count = 0
        for entry in data.get("files", []):
            head, sep, tail = str(entry.get("path", "")).partition("/")
            if sep and head in FOLDERS:
                entry["path"] = f"{FOLDERS[head]}/{tail}"
                count += 1
        if not count:
            continue
        print(f"  {plan}: manifest {count} path(s) → lowercase")
        changed += 1
        if not apply:
            continue
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            tmp = handle.name
        try:
            result = subprocess.run(
                ["gcloud", "storage", "cp", tmp, obj],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            os.unlink(tmp)
        if result.returncode != 0:
            print(f"✗ {plan}: upload failed\n{result.stderr.strip()}", file=sys.stderr)
            problems += 1
    return changed, problems


def fix_gcs(url: str, apply: bool) -> int:
    """Rename ``<url>/<plan>/<TYPE>/`` prefixes, then their manifest paths."""
    if not url.startswith("gs://"):
        print(f"✗ not a gs:// url: {url}", file=sys.stderr)
        return 1

    objects = _gcs_list(url)
    if not objects:
        print(f"no objects under {url}")
        return 1

    prefix = f"{url.rstrip('/')}/"
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    plan_dirs: set[str] = set()
    for obj in objects:
        parts = obj[len(prefix) :].split("/")
        if len(parts) >= 2 and not parts[0].startswith("."):
            plan_dirs.add(parts[0])
        # <plan folder>/<TYPE>/<file> — an object at the top level
        # (archive-index.jsonl) or deeper has no type segment to rename.
        if len(parts) >= 3 and parts[1] in FOLDERS:
            groups[(parts[0], parts[1])].append(obj)

    problems = 0
    moved = 0
    if not groups:
        print(f"no uppercase type folders under {url} — objects already lowercase")
    else:
        existing = set(objects)
        for (plan, old), members in sorted(groups.items()):
            new = FOLDERS[old]
            src = f"{prefix}{plan}/{old}"
            dst = f"{prefix}{plan}/{new}"
            clash = [o for o in existing if o.startswith(f"{dst}/")]
            if clash:
                print(f"✗ {plan}: {old}/ and {new}/ both exist ({len(clash)} objects)")
                problems += 1
                continue
            print(f"  {plan}: {old}/ → {new}/  ({len(members)} objects)")
            if apply:
                if not _gcs_mv(src, dst):
                    problems += 1
                    continue
                existing.difference_update(members)
                existing.update(o.replace(f"{src}/", f"{dst}/", 1) for o in members)
            moved += 1

        print(f"\n{len(groups)} prefix(es) to rename, {problems} problem(s)")
        if not apply:
            print("dry run — pass --apply to run gcloud storage mv")
        else:
            # Re-list: a rename that did not land must not read as success.
            remaining = [
                obj
                for obj in _gcs_list(url)
                if len(obj[len(prefix) :].split("/")) >= 3
                and obj[len(prefix) :].split("/")[1] in FOLDERS
            ]
            if remaining:
                print(
                    f"✗ {len(remaining)} object(s) still under an uppercase folder",
                    file=sys.stderr,
                )
                return 1
            print(f"renamed {moved} prefix(es); objects are lowercase")

    # 2. the receipts, which name the folders the objects now live in
    print(f"\n{MANIFEST_NAME} files[].path:")
    changed, receipt_problems = _gcs_fix_receipts(url, plan_dirs, apply)
    problems += receipt_problems
    if changed and apply:
        print(f"rewrote {changed} manifest(s)")
    elif changed:
        print(f"{changed} manifest(s) to rewrite — pass --apply")
    else:
        print("already lowercase")
    return 1 if problems else 0


# -------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="siril_folder_names",
        description=(
            "rename nina-archive type folders to Siril's lowercase plural "
            "names (lights, darks, flats, biases, junk)"
        ),
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("~/landing/soyuz").expanduser(),
        help="local archive directory (default: ~/landing/soyuz)",
    )
    parser.add_argument(
        "--gcs",
        metavar="URL",
        help="cloud archive prefix, e.g. gs://bucket/landing/soyuz",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="make the changes (default: report only)",
    )
    args = parser.parse_args(argv)

    if shutil.which("gcloud") is None and args.gcs:
        print("✗ gcloud not found on PATH", file=sys.stderr)
        return 1

    if args.gcs:
        return fix_gcs(args.gcs, args.apply)
    return fix_local(args.dest, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
