"""MCP entry point: importing the tool modules registers their tools on ``mcp``."""

import ast
import csv
import glob
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="siril-mcp", instructions="")

# Standard outputs produced by Siril's OSC_Preprocessing.ssf
EXPECTED_OUTPUTS = (
    "result.fit",
    "result.fits",
    "r_pp_light_stacked.fit",
    "r_pp_light_stacked.fits",
)

# savepng writes <name>.png for a target of <name>, into the working directory.
PREVIEW_NAME = "preview"

# siril-cli locations to try, in order, when SIRIL_CLI is not set.
SIRIL_CLI_CANDIDATES = (
    "/Applications/Siril.app/Contents/MacOS/siril-cli",
    "/usr/local/bin/siril-cli",
    "/opt/homebrew/bin/siril-cli",
)

# Substrings that mark a failed batch run, used as a secondary signal alongside
# siril-cli's exit status.
FAILURE_MARKERS = (
    "Script execution failed",
    "Exiting batch processing",
    "Error in line",
    "Unknown command",
    "not compatible with this version",
    'The "requires" command is missing',
)

# Lines Siril prints when a script fails. siril-cli exits 0 and still logs
# "Script execution finished successfully" for a Python exception, a syntax
# error and a rejected pyscript alike, so the output -- never the exit status --
# is the only signal that anything went wrong.
_FAILURE_LINE = re.compile(
    r"(?m)^\s*(?:log:\s*)?(?:Error:|Traceback \(most recent call last\):"
    r"|[A-Z][A-Za-z0-9_]*(?:Error|Exception):)"
)


def _find_siril_cli() -> str | None:
    """Locate the siril-cli executable, preferring an explicit override."""
    override = os.environ.get("SIRIL_CLI")
    if override and Path(override).is_file():
        return override
    found = shutil.which("siril-cli")
    if found:
        return found
    for candidate in SIRIL_CLI_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


# Siril's own config lists where it looks for scripts, but headless siril-cli
# does NOT apply that search path, so a bare script name has to be resolved to
# an absolute path here. Read from config rather than hard-coding the list, so
# a directory the operator adds in Siril's preferences is picked up without a
# code change.
SYSTEM_SCRIPTS_DIR = Path(
    "/Applications/Siril.app/Contents/Resources/share/siril/scripts"
)
SIRIL_SUPPORT_DIR = Path.home() / "Library/Application Support/org.siril.Siril"
# Where Siril caches the scripts cloned by "Get Scripts". These sit outside
# script_path, so Siril does not enable them by default -- but headless
# siril-cli runs them fine when given an absolute path.
REPOSITORY_SCRIPTS_DIR = SIRIL_SUPPORT_DIR / "siril-scripts"


def _config_script_dirs() -> list[Path]:
    """Read the ``script_path`` list out of Siril's own config file.

    Returns an empty list when the config is absent or unreadable so callers
    can fall back to the built-in default instead of failing.
    """
    config_dir = SIRIL_SUPPORT_DIR / "siril"
    for config_file in sorted(config_dir.glob("config*.ini")):
        try:
            text = config_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "script_path":
                found = [
                    Path(part.strip()).expanduser()
                    for part in value.split(";")
                    if part.strip()
                ]
                if found:
                    return found
    return []


def _script_directories() -> list[tuple[Path, str]]:
    """Return ``(directory, source)`` pairs scripts can be loaded from.

    ``configured`` entries come from Siril's ``script_path`` and are searched
    first, so a locally-installed script wins over a repository copy of the
    same name. ``repository`` is the Get Scripts cache, which Siril leaves
    disabled but headless siril-cli will still run by absolute path.
    """
    ordered: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def add(directory: Path, source: str) -> None:
        key = directory.expanduser()
        if key in seen:
            return
        seen.add(key)
        ordered.append((key, source))

    for directory in _config_script_dirs():
        add(directory, "configured")
    # Always include the bundled directory so the shipped scripts stay reachable
    # even when the config is missing or edited to empty it.
    add(SYSTEM_SCRIPTS_DIR, "configured")
    add(REPOSITORY_SCRIPTS_DIR, "repository")
    return ordered


def _scripts_in(directory: Path) -> list[Path]:
    """Every ``.ssf``/``.py`` under ``directory``, recursively, stable order.

    Shared by ``list_scripts`` and ``_resolve_script`` so the two can never
    disagree about what exists: a name the agent reads in one is always
    resolvable in the other. Recursion matters because the repository cache
    groups scripts into subfolders (``processing/``, ``RC-Astro/``, ...).
    """
    if not directory.is_dir():
        return []
    return sorted(
        path for pattern in ("*.ssf", "*.py") for path in directory.rglob(pattern)
    )


def _resolve_script(script: str) -> str:
    """Return an absolute path for ``script`` so headless siril-cli can find it.

    Absolute paths are used as-is when they exist; otherwise every directory
    from :func:`_script_directories` is searched in order. Falls back to the
    original string so Siril can still report a helpful error.
    """
    candidate = Path(script).expanduser()
    if candidate.is_absolute():
        return str(candidate) if candidate.is_file() else script
    if candidate.is_file():
        return str(candidate.resolve())
    wanted = Path(script).name
    for directory, _source in _script_directories():
        for path in _scripts_in(directory):
            if path.name == wanted:
                return str(path)
    return script


def _write_pyscript_wrapper(pyscript: str) -> Path:
    """Wrap a ``.py`` in the two-line .ssf that makes Siril actually run it.

    siril-cli's ``-s`` executes Siril *command* scripts: a .py handed to it
    directly is parsed as commands, never runs, and still exits 0 having done
    nothing. Issuing ``pyscript`` from a wrapper is the only way to get Python
    to run inside Siril. The path is quoted because Siril's script directories
    live under "Application Support".
    """
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".ssf", prefix="siril_pyscript_", delete=False
    )
    with handle:
        handle.write(f'requires 1.3.4\npyscript "{pyscript}"\n')
    return Path(handle.name)


def _with_min_pairs(script_path: str, min_pairs: int) -> Path | None:
    """Copy ``script_path`` with ``-minpairs=`` added to its register command.

    siril-cli exposes no flag for it -- the knob lives inside the .ssf -- so
    honouring the argument means amending the script. Returns ``None`` when
    there is no register command to amend, which the caller reports rather
    than silently dropping the request.
    """
    try:
        lines = (
            Path(script_path).read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return None
    amended: list[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("register "):
            # Replace any existing -minpairs= so an explicit argument wins
            # rather than colliding with the script's own value.
            stripped = re.sub(r"-minpairs=\d+", f"-minpairs={min_pairs}", stripped)
            if f"-minpairs={min_pairs}" not in stripped:
                stripped = f"{stripped} -minpairs={min_pairs}"
            amended.append(stripped)
            changed = True
        else:
            amended.append(line)
    if not changed:
        return None
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".ssf", prefix="siril_minpairs_", delete=False
    )
    with handle:
        handle.write("\n".join(amended) + "\n")
    return Path(handle.name)


def _first_failure_line(output: str) -> str | None:
    """The most informative line Siril printed about a failure, if any.

    A traceback opens with "Traceback (most recent call last):" and closes with
    the exception itself, so the exception line is preferred when present --
    it names what actually went wrong.
    """
    matches = [
        line.strip()[:200] for line in output.splitlines() if _FAILURE_LINE.search(line)
    ]
    if not matches:
        return None
    for line in matches:
        if re.search(r"[A-Z][A-Za-z0-9_]*(?:Error|Exception):", line):
            return line
    return matches[0]


def _find_outputs(work_dir: Path) -> list[str]:
    """Check the root working directory and Siril's default 'process/' subfolder.

    Root outputs come first. ``OSC_Preprocessing.ssf`` writes the final, flipped
    stack as ``result_<LIVETIME>s.fit`` at the root, while ``process/result.fit``
    is the intermediate it was made from -- and preview generation renders the
    first entry, so ordering here decides what an agent actually sees.
    """
    found: list[str] = []
    # The deliverable first: root result_*.fit (result_400s.fit, ...).
    for match in sorted(glob.glob(str(work_dir / "result_*.fit"))):
        if os.path.isfile(match) and os.path.getsize(match) > 0:
            found.append(str(Path(match).relative_to(work_dir)))
    for base in (work_dir, work_dir / "process"):
        if not base.exists():
            continue
        for name in EXPECTED_OUTPUTS:
            candidate = base / name
            if candidate.is_file() and candidate.stat().st_size > 0:
                rel = str(candidate.relative_to(work_dir))
                if rel not in found:
                    found.append(rel)
    return found


# The frame sets NINA archives for a single target. Any one of them present
# makes the folder a working directory.
WORKING_SUBDIRS = ("lights", "biases", "darks", "flats")


def _is_working_directory(path: Path) -> bool:
    """True when ``path`` holds the frame sets archived for one target."""
    return any((path / name).is_dir() for name in WORKING_SUBDIRS)


def _iter_working_directories(root: Path) -> Iterator[tuple[Path, str | None]]:
    """Yield ``(path, observatory)`` for every working directory under root.

    Walks top-down and prunes each working directory the moment it is found, so
    the frame folders inside it -- biases/, lights/, process/ ... -- are never
    descended: those hold tens of thousands of FITS files, and visiting them
    would turn a listing into a full-disk scan. Hidden folders (.ruff_cache,
    .quarantine, ...) are pruned at every level.

    ``observatory`` is the first path component below root -- ``soyuz`` for
    ``~/landing/soyuz/<target>`` -- and ``None`` when targets sit directly in
    the archive root.
    """
    for current, dirs, _files in os.walk(root, topdown=True):
        here = Path(current)
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        if not _is_working_directory(here):
            continue
        relative = here.relative_to(root)
        observatory = relative.parts[0] if len(relative.parts) > 1 else None
        yield here, observatory
        dirs[:] = []  # never descend into the frame folders


def _processing_state(path: Path) -> str:
    """How far a working directory got: succeeded, failed_partial or unprocessed.

    ``failed_partial`` means Siril ran and left work behind without producing a
    stack -- the signature of a run that died at registration.
    """
    if _find_outputs(path):
        return "succeeded"
    if (path / "process").is_dir() or (path / "masters").is_dir():
        return "failed_partial"
    return "unprocessed"


def _pipeline_stages(work_dir: Path) -> dict:
    """Count the files each preprocessing stage produced.

    Tells a caller *where* a run died even after Siril's log is gone: full
    calibration alongside zero registered frames is a target that could not be
    aligned.
    """
    process = work_dir / "process"

    def count(pattern: str) -> int:
        return len(glob.glob(str(process / pattern))) if process.is_dir() else 0

    masters = work_dir / "masters"
    return {
        "converted_lights": count("light_*.fit"),
        "calibrated_pp_lights": count("pp_light_*.fit"),
        "registered_r_pp_lights": count("r_pp_light_*.fit"),
        "masters_present": masters.is_dir() and any(masters.glob("*.fit")),
        # All-zero counts mean two different things: never ran, or ran and was
        # cleaned by clean_working_directory. A result on disk separates them,
        # so say which rather than making the caller infer it.
        "intermediates_cleaned": not process.is_dir() and bool(_find_outputs(work_dir)),
    }


def _inspect_archive(archive_root: Path) -> list[dict]:
    """Walk the archive once and describe every working directory it holds.

    Shared by ``list_working_directories`` and ``check_directory_status`` so
    the two can never disagree about what exists or what state it is in.
    """
    rows: list[dict] = []
    for path, observatory in _iter_working_directories(archive_root):
        preview = path / f"{PREVIEW_NAME}.png"
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "observatory": observatory,
                "state": _processing_state(path),
                "quality": _read_light_quality(path) or {},
                "preview": str(preview) if preview.is_file() else None,
            }
        )
    return rows


def _sky_baseline(rows: list[dict]) -> float | None:
    """Median sky background across targets with enough stars to be trusted.

    This is the reference the obstruction test is measured against: a frame
    several times brighter than it is an obstruction, not a faint target.
    """
    healthy: list[float] = []
    for row in rows:
        quality = row["quality"]
        adu = quality.get("median_adu_mean")
        stars = quality.get("median_detected_stars")
        if adu is not None and stars is not None and stars >= STAR_WARNING_THRESHOLD:
            healthy.append(adu)
    return statistics.median(healthy) if healthy else None


def _quality_verdict(quality: dict, cutoff: float | None) -> str:
    """Classify one target's light quality against the archive baseline."""
    if not quality:
        return "unknown"
    adu = quality.get("median_adu_mean")
    if cutoff is not None and adu is not None and adu >= cutoff:
        # Brighter than any plausible sky for this site: something is in the
        # way (a lit roof), and no amount of re-processing will recover it.
        return "obstructed_high_background"
    stars = quality.get("median_detected_stars")
    if stars is None or stars < STAR_WARNING_THRESHOLD:
        return "faint_or_low_altitude"
    return "ready"


def _archive_root(value: str | None) -> tuple[Path | None, str | None]:
    """Resolve ``value`` or ``ARCHIVE_ROOT`` to a directory, or explain why not."""
    raw = value or os.environ.get("ARCHIVE_ROOT")
    if raw is None:
        return None, "ARCHIVE_ROOT is not set; pass directory= explicitly."
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        return None, f"Directory not found: {root}"
    return root, None


def _resolve_working_directory(directory: str, root: Path | None) -> Path | None:
    """Resolve a working directory from a path or a bare target folder name.

    Accepts ``~/landing/soyuz/m103_...``, ``soyuz/m103_...`` (relative to the
    archive root) and ``m103_...`` on its own, so a name read out of
    ``list_working_directories`` can be passed straight back.
    """
    candidate = Path(directory).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.is_dir() else None
    if root is None:
        return None
    direct = root / candidate
    if direct.is_dir():
        return direct
    wanted = candidate.name
    for path, _observatory in _iter_working_directories(root):
        if path.name == wanted:
            return path
    return None


@mcp.tool()
def list_working_directories(root: str | None = None) -> dict:
    """List every working directory under ARCHIVE_ROOT with its state and quality.

    A working directory is a folder holding one target's archived frame sets.
    The archive root may contain observatory sub-folders (``~/landing/soyuz``),
    which are reported per directory so a second rig needs no code change.

    Each row carries ``state`` (``succeeded`` / ``failed_partial`` /
    ``unprocessed``) and ``quality_verdict`` (``ready`` /
    ``obstructed_high_background`` / ``faint_or_low_altitude`` / ``unknown``),
    plus the raw ImageMetaData telemetry behind them.

    ``summary.by_*`` roll those up, ``summary.baselines`` reports the archive
    measurements the verdicts were derived from, and ``summary.runnable`` names
    the ready-but-unprocessed targets -- the ones worth sending to Siril next.
    """
    archive_root, error = _archive_root(root)
    if archive_root is None:
        return {"error": error}

    rows = _inspect_archive(archive_root)
    baseline = _sky_baseline(rows)
    cutoff = round(baseline * HIGH_BACKGROUND_FACTOR, 1) if baseline else None

    directories: list[dict] = []
    for row in rows:
        entry = dict(row)
        quality = entry.pop("quality")
        entry.update(quality)
        entry["quality_verdict"] = _quality_verdict(quality, cutoff)
        directories.append(entry)

    star_counts = [
        entry["median_detected_stars"]
        for entry in directories
        if entry.get("median_detected_stars") is not None
    ]
    return {
        "summary": {
            "archive_root": str(archive_root),
            "total_directories": len(directories),
            "by_observatory": dict(
                Counter(entry["observatory"] or "root" for entry in directories)
            ),
            "by_processing_state": dict(
                Counter(entry["state"] for entry in directories)
            ),
            "by_quality_verdict": dict(
                Counter(entry["quality_verdict"] for entry in directories)
            ),
            "runnable": [
                entry["name"]
                for entry in directories
                if entry["quality_verdict"] == "ready" and entry["state"] != "succeeded"
            ],
            "baselines": {
                "star_warning_threshold": STAR_WARNING_THRESHOLD,
                "healthy_sky_adu": round(baseline, 1) if baseline else None,
                "high_background_factor": HIGH_BACKGROUND_FACTOR,
                "high_background_adu_cutoff": cutoff,
                "median_detected_stars": (
                    round(statistics.median(star_counts), 1) if star_counts else None
                ),
            },
        },
        "directories": directories,
    }


@mcp.tool()
def check_directory_status(directory: str) -> dict:
    """Inspect one working directory: state, pipeline stage and light quality.

    ``directory`` may be a full path (``~/landing/soyuz/m103_...``), a path
    relative to ARCHIVE_ROOT (``soyuz/m103_...``) or just the target folder
    name (``m103_...``), so a name read out of ``list_working_directories`` can
    be passed straight back.

    ``state`` says how far it got; ``pipeline_stages`` says which stage produced
    files, which is what distinguishes a target that died at registration from
    one that was never run. ``quality_verdict`` is measured against the
    archive-wide sky baseline rather than a fixed number.
    """
    # Resolve the archive root even for absolute paths: the observatory label
    # and the sky baseline both come from it, so a full path must not lose them.
    root, root_error = _archive_root(None)
    if root is None and not Path(directory).expanduser().is_absolute():
        return {"error": root_error}
    work_dir = _resolve_working_directory(directory, root)
    if work_dir is None:
        searched = f" under {root}" if root is not None else ""
        return {"error": f"Working directory not found{searched}: {directory}"}
    if not _is_working_directory(work_dir):
        return {
            "error": (
                f"{work_dir} is not a working directory; use "
                "list_working_directories() for an archive-wide view."
            )
        }

    quality = _read_light_quality(work_dir)
    outputs = _find_outputs(work_dir)
    baseline = _sky_baseline(_inspect_archive(root)) if root is not None else None
    cutoff = round(baseline * HIGH_BACKGROUND_FACTOR, 1) if baseline else None
    preview = work_dir / f"{PREVIEW_NAME}.png"
    relative = work_dir.relative_to(root) if root else None
    observatory = (
        relative.parts[0] if relative is not None and len(relative.parts) > 1 else None
    )

    return {
        "directory": str(work_dir),
        "observatory": observatory,
        "state": _processing_state(work_dir),
        "quality_verdict": _quality_verdict(quality or {}, cutoff),
        "processed": (work_dir / "process").is_dir() or bool(outputs),
        "succeeded": bool(outputs),
        "visible_outputs": outputs,
        "preview": str(preview) if preview.is_file() else None,
        "input_folders_found": [
            name for name in WORKING_SUBDIRS if (work_dir / name).is_dir()
        ],
        "pipeline_stages": _pipeline_stages(work_dir),
        "light_quality": quality,
        "warnings": _quality_warnings(quality),
        "baselines": {
            "star_warning_threshold": STAR_WARNING_THRESHOLD,
            "healthy_sky_adu": round(baseline, 1) if baseline else None,
            "high_background_adu_cutoff": cutoff,
        },
    }


# Leading noise in a script header comment that says nothing about what the
# script does: Siril version, author, licence, date, script-version lines and
# bare changelog entries.
_HEADER_META = re.compile(
    r"(?i)^(script for siril\b|spdx-|\(c\)|copyright|licen[cs]e\b|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"\s*\d{0,2},?\s*\d{4}\b|"  # "December 2024", "Apr. 17, 2025"
    r".*\bv\d+\.\d+(?:\.\d+)?|"  # "Preprocessing v1.4"
    r"version\s*\d+\.\d+|"  # "Version 1.2.0"
    r"version history\b|"  # section label, not a description
    r"\d+\.\d+(?:\.\d+)?\b)"  # changelog line "1.0.1 bugfix: ..."
    # Unanchored: a credit line can appear anywhere in a header.
    r"|\bauthor\b|\breached at\b|\bcontact:"
)

_DESCRIPTION_LIMIT = 300


def _one_paragraph(text: str | None) -> str | None:
    """Collapse ``text`` to a single bounded line, or ``None`` when empty."""
    if not text:
        return None
    first = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    flat = re.sub(r"\s+", " ", first).strip()
    if not flat:
        return None
    if len(flat) > _DESCRIPTION_LIMIT:
        flat = flat[:_DESCRIPTION_LIMIT].rstrip() + "…"
    return flat


def _useful_comment(text: str) -> bool:
    """False for banner separators, section titles and author/date/version noise.

    Shared by every header reader so a ``.ssf`` and a ``.py`` trim the same
    boilerplate and read as if written the same way.
    """
    if not text or set(text) <= set("#=-* "):
        return False  # banner separator
    if _HEADER_META.match(text):
        return False  # Siril version, author, licence, date, script version
    if text.upper() == text and any(c.isalpha() for c in text):
        return False  # section banner, e.g. "PREPROCESSING SCRIPT"
    # ASCII-art rules carry almost no letters; prose never looks like this.
    if len(text) >= 12 and sum(c.isalpha() for c in text) / len(text) < 0.35:
        return False
    return True


def _python_docstring(path: Path) -> str | None:
    """Module docstring of a ``.py`` script -- parsed, never executed."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        docstring = ast.get_docstring(ast.parse(source))
    except (SyntaxError, ValueError, OSError):
        return None
    return _one_paragraph(docstring)


# A run of separator characters marks a banner line: "####…",
# "============", "────────", "#### PREPROCESSING SCRIPT ####".
_BANNER_RUN = re.compile(r"[#=─━═\-–—_*~+]{4,}")


def _comment_header(path: Path) -> str | None:
    """Leading ``#`` block of ``path``, minus boilerplate.

    Stops at the first real code line, so a comment sitting in the middle of a
    file is never mistaken for a description. Used for every ``.ssf`` -- which
    have no docstring equivalent at all -- and as the fallback for a ``.py``
    whose module docstring is missing.

    A banner line separates a header's meta (date, version, author) from its
    description, and authors put the description after the last *rule*, so that
    is where reading starts. Files with no rule keep the whole block and rely
    on the boilerplate filter alone.
    """
    raw: list[str] = []
    banner: list[bool] = []
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for number, line in enumerate(handle):
            if number > 40:
                break
            stripped = line.strip()
            if stripped.startswith("#"):
                text = stripped.lstrip("#").strip()
                if text.startswith("!") or text.startswith("coding"):
                    continue  # shebang or encoding declaration
                raw.append(text)
                # Judge the banner on the line as written: an all-# rule
                # reduces to an empty string once the hashes are stripped.
                banner.append(bool(_BANNER_RUN.search(stripped)))
                continue
            if not stripped:
                continue  # a blank line does not end the header block
            break  # first code line ends it

    if not raw:
        return None

    # Cut only at banners carrying no letters -- rules and ASCII-art blocks.
    # A titled banner such as "#### PREPROCESSING SCRIPT ####" is a section
    # heading rather than a separator: several authors write the summary
    # directly above it, and cutting there would skip past the one line that
    # says what the script does. Starting after the last pure rule keeps those
    # summaries while still dropping the date/version/author block above them.
    pure = [
        is_banner and not any(char.isalnum() for char in text)
        for is_banner, text in zip(banner, raw, strict=False)
    ]
    last_rule = max((index for index, p in enumerate(pure) if p), default=-1)
    after_rule = [text for text in raw[last_rule + 1 :] if _useful_comment(text)]
    if after_rule:
        return _one_paragraph(" ".join(after_rule))

    # A rule can open a sub-section rather than close the meta -- "# ===="
    # introducing "Version History:" -- which leaves nothing useful after it.
    # Fall back to filtering the whole block so the description is not lost.
    whole = [text for text in raw if _useful_comment(text)]
    return _one_paragraph(" ".join(whole)) if whole else None


def _script_description(path: Path) -> str | None:
    """Best-effort description: a Python docstring, else the header comment."""
    if path.suffix == ".py":
        docstring = _python_docstring(path)
        if docstring:
            return docstring
    return _comment_header(path)


@mcp.tool()
def list_scripts(
    kind: Literal["ssf", "py"] | None = None,
    source: Literal["configured", "repository"] | None = None,
    query: str | None = None,
) -> dict:
    """List the Siril scripts available to run.

    Every ``.ssf`` and ``.py`` file reachable from Siril's configured script
    directories plus the Get Scripts repository cache. Each entry carries the
    absolute ``path`` to pass straight to
    ``run_osc_preprocessing(script=...)``, which headless siril-cli needs
    because it does not honour Siril's own script search path.

    ``source`` is ``configured`` for directories Siril loads from, and
    ``repository`` for the cloned cache that Siril leaves disabled but will
    still run when given an absolute path.

    Filters exist because the unfiltered listing is 116 entries (~49 KB):
    ``kind`` selects ``ssf`` or ``py``, ``source`` selects where it came from,
    and ``query`` matches a substring against name and description together
    (so ``query="preprocessing"`` finds both the .ssf and its description).
    Directory counts reflect the filter, so they stay consistent with
    ``count``.
    """
    directories: list[dict] = []
    scripts: list[dict] = []
    for directory, script_source in _script_directories():
        entries: list[dict] = []
        for path in _scripts_in(directory):
            entry = {
                "name": path.name,
                "path": str(path),
                "kind": path.suffix.lstrip("."),
                "source": script_source,
                "description": _script_description(path),
            }
            if kind is not None and entry["kind"] != kind:
                continue
            if source is not None and entry["source"] != source:
                continue
            if query:
                haystack = f"{entry['name']} {entry.get('description') or ''}".lower()
                if query.lower() not in haystack:
                    continue
            entries.append(entry)
        directories.append(
            {
                "path": str(directory),
                "source": script_source,
                "exists": directory.is_dir(),
                "count": len(entries),
            }
        )
        scripts.extend(entries)
    return {"count": len(scripts), "directories": directories, "scripts": scripts}


@mcp.tool(structured_output=False)
def run_osc_preprocessing(
    directory: str,
    script: str = "OSC_Preprocessing.ssf",
    force: bool = False,
    timeout: int = 3600,
    min_stars: int = 0,
    min_pairs: int = 0,
) -> list[str]:
    """Run a Siril script headlessly via siril-cli and preview the result.

    Defaults to OSC_Preprocessing.ssf on ``directory``. Skips execution if
    already processed unless ``force=True``. Set ``min_stars`` to pre-flight
    skip targets whose ImageMetaData.csv shows too few stars to register.

    ``min_pairs`` lowers how many star pairs each frame needs against the
    registration reference; Siril drops frames below it. It rescues sparse
    fields that die at "Not enough star pairs" -- m81 needed ``min_pairs=4``
    -- but fewer registered frames means a shallower stack, so read
    ``pipeline_stages`` in the result before trusting a success. Applies to
    .ssf scripts only.

    Returns the structured result as JSON text, followed by the preview PNG's
    path whenever there is an output to review. The image itself stays on disk
    for the agent to read.
    """
    # Resolve through the archive exactly as check_directory_status does, so a
    # bare target name read out of summary.runnable passes straight back
    # instead of being looked up relative to the process's own cwd.
    candidate = Path(directory).expanduser()
    root, root_error = _archive_root(None)
    if root is None and not candidate.is_absolute():
        return _finish(
            {"status": "error", "message": root_error}, candidate, [], None, timeout
        )
    work_dir = _resolve_working_directory(directory, root)
    if work_dir is None:
        return _finish(
            {"status": "error", "message": f"Directory does not exist: {directory}"},
            candidate,
            [],
            None,
            timeout,
        )

    quality = _read_light_quality(work_dir)
    warnings = _quality_warnings(quality)
    outputs = _find_outputs(work_dir)
    siril_cli = _find_siril_cli()

    # Guard against redundant runs
    if outputs and not force:
        return _finish(
            {
                "status": "skipped",
                "message": "Directory already processed. Pass force=True to re-run.",
                "directory": str(work_dir),
                "visible_outputs": outputs,
                "pipeline_stages": _pipeline_stages(work_dir),
                "light_quality": quality,
                "warnings": warnings,
            },
            work_dir,
            outputs,
            siril_cli,
            timeout,
        )

    # Pre-flight: skip work that is very likely to die at registration.
    if min_stars and not force and quality:
        stars = quality.get("median_detected_stars")
        if stars is not None and stars < min_stars:
            return _finish(
                {
                    "status": "skipped_low_quality",
                    "message": (
                        f"Median {stars:.0f} detected stars is below min_stars="
                        f"{min_stars}; registration would likely fail. "
                        "Pass force=True to run anyway."
                    ),
                    "directory": str(work_dir),
                    "pipeline_stages": _pipeline_stages(work_dir),
                    "light_quality": quality,
                    "warnings": warnings,
                },
                work_dir,
                outputs,
                siril_cli,
                timeout,
            )

    if not siril_cli:
        return _finish(
            {
                "status": "error",
                "message": "siril-cli not found. Set SIRIL_CLI to its full path.",
            },
            work_dir,
            [],
            None,
            timeout,
        )

    # -d sets Siril's working directory. The script must be passed as an
    # absolute path: headless siril-cli does not honour script_path, so a bare
    # name fails with "File [...] does not exist".
    resolved = _resolve_script(script)
    # Temporary scripts this run needs, all removed in the finally below.
    temporaries: list[Path] = []
    if min_pairs > 0 and Path(resolved).suffix == ".py":
        return _finish(
            {
                "status": "error",
                "message": "min_pairs applies to .ssf scripts; a .py cannot be amended.",
            },
            work_dir,
            outputs,
            siril_cli,
            timeout,
        )
    if Path(resolved).suffix == ".py":
        # -s parses a .py as Siril commands and runs nothing at all, still
        # reporting success; the wrapper is what actually invokes it.
        temporaries.append(_write_pyscript_wrapper(resolved))
    elif min_pairs > 0:
        amended = _with_min_pairs(resolved, min_pairs)
        if amended is None:
            # Reported rather than ignored: silently dropping the argument
            # would leave the caller believing a looser floor was applied.
            return _finish(
                {
                    "status": "error",
                    "message": (
                        f"min_pairs={min_pairs} was requested but {resolved} "
                        "has no register command to amend."
                    ),
                },
                work_dir,
                outputs,
                siril_cli,
                timeout,
            )
        temporaries.append(amended)
    target = str(temporaries[0]) if temporaries else resolved
    cmd = [siril_cli, "-d", str(work_dir), "-s", target]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
        )
    except subprocess.TimeoutExpired:
        return _finish(
            {
                "status": "failed",
                "message": f"siril-cli timed out after {timeout}s",
                "visible_outputs": outputs,
                "light_quality": quality,
                "warnings": warnings,
            },
            work_dir,
            outputs,
            siril_cli,
            timeout,
        )
    except OSError as exc:
        return _finish(
            {"status": "error", "message": f"Failed to launch siril-cli: {exc}"},
            work_dir,
            [],
            None,
            timeout,
        )
    finally:
        for path in temporaries:
            path.unlink(missing_ok=True)

    output = (proc.stdout or "") + (proc.stderr or "")
    failures = [m for m in FAILURE_MARKERS if m in output]
    python_failed = bool(_FAILURE_LINE.search(output))
    outputs = _find_outputs(work_dir)

    # siril-cli exits non-zero when a script aborts, but a failing pyscript
    # still exits 0 -- so the reported failure lines carry that case.
    if proc.returncode != 0 or ((failures or python_failed) and not outputs):
        if failures:
            message = failures[0]
        elif python_failed:
            message = _first_failure_line(output) or "the script reported an error"
        else:
            message = f"siril-cli exited with status {proc.returncode}"
        result = {
            "status": "failed",
            "message": message,
            "returncode": proc.returncode,
            "log_tail": _tail(output),
            "visible_outputs": outputs,
            "pipeline_stages": _pipeline_stages(work_dir),
            "light_quality": quality,
            "warnings": warnings,
        }
        diagnosis = _diagnose(output, quality)
        if diagnosis:
            result["diagnosis"] = diagnosis
        return _finish(result, work_dir, outputs, siril_cli, timeout)

    return _finish(
        {
            "status": "succeeded" if outputs else "completed_without_expected_output",
            "directory": str(work_dir),
            "script": script,
            "returncode": proc.returncode,
            "log_tail": _tail(output),
            "visible_outputs": outputs,
            # The docstring tells the caller to read this before trusting a
            # success: m81 reports succeeded on 6 of 100 registered frames.
            "pipeline_stages": _pipeline_stages(work_dir),
            "light_quality": quality,
            "warnings": warnings,
        },
        work_dir,
        outputs,
        siril_cli,
        timeout,
    )


@mcp.tool()
def render_preview(directory: str, fits_file: str | None = None) -> dict:
    """Render an autostretched PNG of one FITS file in a working directory.

    ``run_osc_preprocessing`` renders ``preview.png`` as a side-effect of a
    run; this does it on demand for any file, which is what makes a
    standard-vs-drizzle comparison possible -- render both, then read them
    side by side.

    ``fits_file`` resolves against the working directory (``result_70s.fit``,
    ``process/result.fit``) or may be absolute, and defaults to the first
    entry of the output list, i.e. the final root stack. The PNG is named
    after its source (``result_70s.png``) so rendering a second file never
    overwrites the first.

    Renders in ~1s and does not touch the stack itself.
    """
    root, root_error = _archive_root(None)
    candidate = Path(directory).expanduser()
    if root is None and not candidate.is_absolute():
        return {"status": "error", "message": root_error}
    work_dir = _resolve_working_directory(directory, root)
    if work_dir is None or not _is_working_directory(work_dir):
        return {"status": "error", "message": f"Not a working directory: {directory}"}

    siril_cli = _find_siril_cli()
    if not siril_cli:
        return {
            "status": "error",
            "message": "siril-cli not found. Set SIRIL_CLI to its full path.",
        }

    if fits_file is None:
        outputs = _find_outputs(work_dir)
        if not outputs:
            return {
                "status": "error",
                "message": "No output FITS to preview; pass fits_file= explicitly.",
            }
        source = work_dir / outputs[0]
    else:
        asked = Path(fits_file).expanduser()
        source = asked if asked.is_absolute() else work_dir / fits_file
        if not source.is_file():
            return {"status": "error", "message": f"FITS file not found: {fits_file}"}

    path, error = _render_preview(work_dir, source, siril_cli, 120, source.stem)
    if path is None:
        return {"status": "error", "message": error}
    return {
        "status": "ok",
        "preview": path,
        "source": str(source),
        "state": _processing_state(work_dir),
    }


@mcp.tool()
def clean_working_directory(
    directory: str,
    include_masters: bool = False,
    force: bool = False,
) -> dict:
    """Delete a target's intermediate Siril work to reclaim disk.

    ``process/`` holds roughly 1.5-2 GB per target: converted symlinks,
    calibrated ``pp_light_*.fit`` and registered ``r_pp_light_*.fit`` frames.
    None of it is needed once the stack and preview exist at the root, and
    across the archive it is the largest consumer of space.

    Safety: only ``process/`` (and ``masters/`` when ``include_masters``) is
    ever removed, and only from a path verified to be a working directory.
    ``lights/``, ``darks/``, ``flats/``, ``biases/``, ``manifest.json``,
    ``plan.json``, ``preview.png`` and the root ``result_*.fit`` are never
    touched, and the archive root itself is refused outright.

    Refuses a target that has not succeeded unless ``force=True``: ``process/``
    is the evidence behind ``pipeline_stages``, so deleting it would destroy
    the ability to diagnose a run that died at registration.
    """
    root, root_error = _archive_root(None)
    candidate = Path(directory).expanduser()
    if root is None and not candidate.is_absolute():
        return {"status": "error", "message": root_error}
    work_dir = _resolve_working_directory(directory, root)
    if work_dir is None:
        return {
            "status": "error",
            "message": f"Working directory not found: {directory}",
        }
    if not _is_working_directory(work_dir):
        return {
            "status": "error",
            "message": (
                f"Refusing: {work_dir} is not a working directory, so there is "
                "nothing safe to delete."
            ),
        }

    state = _processing_state(work_dir)
    if state != "succeeded" and not force:
        return {
            "status": "error",
            "state": state,
            "message": (
                f"{work_dir.name} is {state}; process/ holds the evidence for "
                "diagnosing it. Pass force=True to delete anyway."
            ),
        }

    targets = [work_dir / "process"]
    if include_masters:
        targets.append(work_dir / "masters")

    freed = 0
    removed: list[str] = []
    for target in targets:
        # Only ever a direct child of the working directory: this is what
        # makes an accidental deletion of the archive itself impossible.
        if not target.is_dir() or target.parent != work_dir:
            continue
        for path in target.rglob("*"):
            try:
                freed += path.lstat().st_size
            except OSError:
                continue
        shutil.rmtree(target)
        removed.append(target.name)

    return {
        "status": "ok",
        "removed": removed,
        "freed_bytes": freed,
        "freed_mb": round(freed / 1_048_576, 1),
        "state": _processing_state(work_dir),
        "visible_outputs": _find_outputs(work_dir),
    }


@mcp.tool()
def get_siril_status() -> dict:
    """Report whether this environment can run Siril, without processing anything.

    A read-only health check: which ``siril-cli`` will be used and at what
    version, whether ``ARCHIVE_ROOT`` resolves, whether Siril's internal
    Python venv exists (that is what makes ``pyscript`` work), and which
    script directories are reachable. Worth calling before a long run, and
    when a failure looks environmental rather than about the data.
    """
    cli = _find_siril_cli()
    version = None
    if cli:
        try:
            proc = subprocess.run(
                [cli, "--version"], capture_output=True, text=True, timeout=60
            )
            lines = [
                line.strip()
                for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines()
                if line.strip()
            ]
            # siril-cli prints "Siril is started as macOS application" before
            # the version line, so match "siril <digits>" rather than "siril".
            version = next(
                (line for line in lines if re.match(r"(?i)^siril\s+[\d.]", line)),
                None,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            version = f"unavailable: {exc}"

    root, root_error = _archive_root(None)
    venv = SIRIL_SUPPORT_DIR / "siril" / "venv"
    return {
        "siril_cli": {"found": cli is not None, "path": cli, "version": version},
        "archive_root": {"path": str(root) if root else None, "error": root_error},
        "python_venv": {
            "path": str(venv),
            "present": venv.is_dir(),
            "note": "siril-cli builds this on first use; pyscript requires it.",
        },
        "script_directories": [
            {"path": str(path), "source": source, "exists": path.is_dir()}
            for path, source in _script_directories()
        ],
        "environment": {
            name: os.environ.get(name) for name in ("ARCHIVE_ROOT", "SIRIL_CLI")
        },
    }


def _tail(text: str, lines: int = 30) -> str:
    """Return the last ``lines`` non-empty lines of Siril output for diagnostics."""
    kept = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _render_preview(
    work_dir: Path,
    source: Path,
    siril_cli: str,
    timeout: int,
    name: str = PREVIEW_NAME,
) -> tuple[str | None, str | None]:
    """Autostretch ``source`` into ``<work_dir>/<name>.png``.

    Returns ``(path, error)``. Only the path travels back in the tool
    response; the PNG itself stays on disk for the agent to read or
    manipulate. ``name`` lets a second render sit beside the first instead of
    overwriting it, which is what makes a standard-vs-drizzle comparison
    possible.
    """
    if not source.is_file():
        return None, f"output not found: {source}"

    preview_base = work_dir / name
    preview_path = work_dir / f"{name}.png"
    script = f'requires 1.3.4\nload "{source}"\nautostretch\nsavepng "{preview_base}"\n'

    script_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".ssf", prefix="siril_preview_", delete=False
        ) as handle:
            handle.write(script)
            script_path = Path(handle.name)
        proc = subprocess.run(
            [siril_cli, "-d", str(work_dir), "-s", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
        )
    except subprocess.TimeoutExpired:
        return None, f"preview timed out after {timeout}s"
    except OSError as exc:
        return None, f"preview failed to launch siril-cli: {exc}"
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)

    if not preview_path.is_file():
        detail = _tail((proc.stdout or "") + (proc.stderr or ""), 5)
        return None, f"siril-cli produced no PNG (exit {proc.returncode}): {detail}"
    return str(preview_path), None


def _finish(
    result: dict,
    work_dir: Path,
    outputs: list[str],
    siril_cli: str | None,
    timeout: int,
) -> list[str]:
    """Render the preview when there is an image, then shape the tool reply.

    The reply is the structured result as JSON text followed by the preview
    PNG's path. The image itself is never shipped back — the agent reads the
    file when it wants to look.
    """
    extras: list[str] = []
    if siril_cli and outputs:
        path, error = _render_preview(
            work_dir, work_dir / outputs[0], siril_cli, timeout
        )
        if path:
            result["preview"] = path
            extras.append(path)
        if error:
            result["preview_error"] = error
    return [json.dumps(result, indent=1), *extras]


# NINA records per-frame telemetry in lights/ImageMetaData.csv. Set from an
# empirical sweep of the 12 targets this threshold had been skipping: forcing
# them through standard -> drizzle -> min_pairs recovered 4, every one of them
# at 32-43 median stars, while all 8 at 15-33 failed all three. 30 sits below
# every success and above most failures. The one miss (ngc-1333, 33 stars) is
# accepted noise: a wrong "ready" costs a visible failed run, while a wrong
# "faint" silently withholds a stack that would have worked.
STAR_WARNING_THRESHOLD = 30

# A frame several times brighter than the normal sky background is an
# obstruction (a lit roof) rather than a faint target: 14 targets sat at
# 3464-11069 ADU against a healthy median of 1087, all from one bad evening.
# Measured against the baseline instead of hard-coded so it tracks the site.
HIGH_BACKGROUND_FACTOR = 3.0

# Fields worth summarising from ImageMetaData.csv, with the aggregation used.
_METADATA_FIELDS = (
    ("detected_stars", "median_detected_stars"),
    ("hfr", "median_hfr"),
    ("adu_mean", "median_adu_mean"),
    ("adu_st_dev", "median_adu_st_dev"),
    ("airmass", "median_airmass"),
)


def _read_light_quality(work_dir: Path) -> dict | None:
    """Summarise NINA's per-frame telemetry for the lights in ``work_dir``.

    Returns ``None`` when lights/ImageMetaData.csv is absent. Star count is
    what predicts whether Siril's registration step will find enough stars.
    """
    csv_path = work_dir / "lights" / "ImageMetaData.csv"
    if not csv_path.is_file():
        return None
    try:
        with csv_path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return None
    if not rows:
        return None

    quality: dict = {"frames": len(rows)}
    stars: list[float] = []
    for column, key in _METADATA_FIELDS:
        values = [
            float(row[column])
            for row in rows
            if row.get(column) not in (None, "", "NaN", "nan")
        ]
        if not values:
            continue
        quality[key] = round(statistics.median(values), 3)
        if column == "detected_stars":
            stars = values
    if stars:
        quality["min_detected_stars"] = int(min(stars))
        quality["registration_likely"] = (
            statistics.median(stars) >= STAR_WARNING_THRESHOLD
        )
    return quality


def _quality_warnings(quality: dict | None) -> list[str]:
    """Turn a light-quality summary into human-readable warnings."""
    if not quality:
        return ["No lights/ImageMetaData.csv; light quality unknown."]
    warnings: list[str] = []
    stars = quality.get("median_detected_stars")
    if stars is not None and stars < STAR_WARNING_THRESHOLD:
        warnings.append(
            f"Only {stars:.0f} stars detected per frame (median); every target "
            f"tested below {STAR_WARNING_THRESHOLD} failed registration. Siril "
            "aborts either with 'not enough stars in reference image' or, more "
            "commonly, 'Not enough star pairs' when it cannot match frames."
        )
    hfr = quality.get("median_hfr")
    if hfr is not None and hfr > 3.0:
        warnings.append(f"Median HFR {hfr:.2f} is high; stars may be bloated.")
    airmass = quality.get("median_airmass")
    if airmass is not None and airmass > 1.8:
        warnings.append(
            f"Median airmass {airmass:.2f} (low altitude); expect faint stars."
        )
    return warnings


# Siril failure signatures mapped to an explanation plus a suggested fix.
# Registration saw stars but could not pair enough of them between frames. Siril
# logs this in two phrasings -- "Not enough star pairs" per image, then "No
# image was registered to the reference" at the end -- so both are listed.
# This is the failure mode ImageMetaData.csv cannot predict: Siril counts
# Gaussian-profile stars on the calibrated frame, which runs 4-6x below NINA's
# detected_stars taken from the raw one, so 103 can still mean ~19 in Siril.
_PAIRING_EXPLANATION = (
    "Registration found stars but could not pair enough of them between frames "
    "for Global Star Alignment. Siril counts Gaussian-profile stars on the "
    "calibrated frame, which runs 4-6x below NINA's detected_stars from the "
    "raw frame, so a healthy-looking star count can still leave Siril starved."
)
_PAIRING_FIX = (
    "Retry with OSC_Preprocessing_BayerDrizzle.ssf, which skips -debayer and "
    "keeps star centroids precise enough to pair; it recovered m1 and "
    "ngc-1499. If it still fails the field is genuinely sparse -- re-shoot "
    "higher in the sky or accept the target."
)

_DIAGNOSES = (
    (
        "not enough stars in reference image",
        "Registration could not align the lights: the reference frame has too "
        "few detectable stars for Global Star Alignment.",
        "Check lights/ImageMetaData.csv median_detected_stars; re-shoot the "
        "target higher in the sky or with longer exposures.",
    ),
    (
        "no image was registered to the reference",
        _PAIRING_EXPLANATION,
        _PAIRING_FIX,
    ),
    (
        "not enough star pairs",
        _PAIRING_EXPLANATION,
        _PAIRING_FIX,
    ),
    (
        "does not exist (from CWD",
        "Siril could not locate the script file.",
        "Pass an absolute path, or a name in one of Siril's script directories.",
    ),
    (
        "not compatible with this version",
        "The script requires a different Siril version than the installed one.",
        "Update Siril, or use a script matching the installed version.",
    ),
    (
        "traceback (most recent call last)",
        "The script itself raised an exception and stopped; Siril ran it "
        "successfully, so the fault is in the script rather than the pipeline.",
        "Read the exception at the end of log_tail -- the traceback names the "
        "line that failed.",
    ),
    (
        "not ready yet",
        "Siril's Python virtual environment failed to initialise.",
        "Open the Siril GUI once to let it build the venv, then retry.",
    ),
)


def _diagnose(output: str, quality: dict | None) -> dict | None:
    """Match Siril's log against known failures and explain what to do."""
    lowered = output.lower()
    for needle, explanation, fix in _DIAGNOSES:
        if needle.lower() in lowered:
            diagnosis = {"explanation": explanation, "suggested_fix": fix}
            if quality:
                stars = quality.get("median_detected_stars")
                if stars is not None and "stars" in explanation.lower():
                    diagnosis["evidence"] = (
                        f"ImageMetaData.csv reports {stars:.0f} median "
                        f"detected stars across {quality.get('frames')} frames."
                    )
            return diagnosis
    return None


if __name__ == "__main__":
    mcp.run()
