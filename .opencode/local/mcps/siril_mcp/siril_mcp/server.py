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
import sys
import tempfile
from array import array
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

# Gate for the autocrop step. Autocrop.py was written for ``framing=max``
# stacks, whose registration leaves empty borders: it thresholds an
# autostretched copy at its 95th percentile, so on an ordinary stack with a
# dark sky background it instead crops to the brightest object's largest
# inscribed rectangle -- measured on M42, 1080x1920 -> 279x301 (4% of the area
# kept) with 0.00% zero-valued pixels, i.e. no border at all. The decision is
# therefore taken from the data rather than from the script's name.
#
# A stack is only worth cropping when this share of its pixels carries no
# signal whatsoever: that is the signature of a rotation/framing=max border.
# Normal sky background, however dark, sits well above it.
EMPTY_BORDER_MIN_FRACTION = 0.01

# Backstop for a crop that the pre-check allowed but that still throws most of
# the image away. Refused rather than applied, so an unexpected stack cannot
# silently lose its field the way M42 did.
MIN_CROP_KEEP_FRACTION = 0.25

# Content-aware crop script shipped in Siril's Get Scripts repository cache.
AUTOCROP_SCRIPT = "Autocrop.py"

# siril-cli locations to try, in order, when SIRIL_CLI is not set.
SIRIL_CLI_CANDIDATES = (
    "/Applications/Siril.app/Contents/MacOS/siril-cli",
    "/usr/local/bin/siril-cli",
    "/opt/homebrew/bin/siril-cli",
    # Windows default install location; harmless on other platforms since
    # is_file() simply returns False for it there.
    r"C:\Program Files\Siril\bin\siril-cli.exe",
)

# Substrings that mark a failed batch run, used as a secondary signal alongside
# siril-cli's exit status. GPS_Preprocess.py catches its own exceptions and
# prints "**** ERROR *** ..." instead of letting them reach the log as a
# Traceback, so its marker is listed here too.
FAILURE_MARKERS = (
    "Script execution failed",
    "Exiting batch processing",
    "Error in line",
    "Unknown command",
    "not compatible with this version",
    'The "requires" command is missing',
    "**** ERROR ***",
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
# code change. The bundled directory differs per OS (Siril's preferences
# documents each default), so it is selected at import time.
if os.name == "nt":
    # Windows: scripts ship in <install>\scripts, config in %APPDATA% (GLib's
    # user-config dir). Both install roots and both AppData roots are probed
    # because portable/32-bit installs and LOCALAPPDATA-only setups exist.
    _support_candidates = [
        Path(root) / "org.siril.Siril"
        for root in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"))
        if root
    ] or [Path.home() / "AppData" / "Roaming" / "org.siril.Siril"]
    SIRIL_SUPPORT_DIR = next(
        (path for path in _support_candidates if path.is_dir()),
        _support_candidates[0],
    )
    _install_roots = [
        Path(root) / "Siril"
        for root in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        )
        if root
    ] or [Path(r"C:\Program Files\Siril")]
    SYSTEM_SCRIPTS_DIRS = tuple(root / "scripts" for root in _install_roots)
elif sys.platform == "darwin":
    SYSTEM_SCRIPTS_DIRS = (
        Path("/Applications/Siril.app/Contents/Resources/share/siril/scripts"),
    )
    SIRIL_SUPPORT_DIR = Path.home() / "Library/Application Support/org.siril.Siril"
else:
    # GNU/Linux: both documented defaults, since packagers use either.
    SYSTEM_SCRIPTS_DIRS = (
        Path("/usr/share/siril/scripts"),
        Path("/usr/local/share/siril/scripts"),
    )
    SIRIL_SUPPORT_DIR = (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "org.siril.Siril"
    )
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
    # Always include the bundled directory (or directories) so the shipped
    # scripts stay reachable even when the config is missing or edited empty.
    for directory in SYSTEM_SCRIPTS_DIRS:
        add(directory, "configured")
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


def _write_pyscript_wrapper(
    pyscript: str, script_args: str | list[str] | None = None
) -> Path:
    """Wrap a ``.py`` in the two-line .ssf that makes Siril actually run it.

    siril-cli's ``-s`` executes Siril *command* scripts: a .py handed to it
    directly is parsed as commands, never runs, and still exits 0 having done
    nothing. Issuing ``pyscript`` from a wrapper is the only way to get Python
    to run inside Siril. The path is quoted because Siril's script directories
    live under "Application Support".

    ``script_args`` are appended after the quoted path: pyscript passes every
    following token to the script as its argument vector (Siril's documented
    ``pyscript [-async] scriptname.py [script_argv]``), which is how a script
    that would otherwise open a GUI -- ``GPS_Preprocess.py`` launches its Qt
    dialog when ``len(sys.argv) == 1`` -- is told to run headless instead.
    A list is joined with spaces; an element containing spaces is quoted.
    """
    if isinstance(script_args, list):
        rendered = " ".join(
            f'"{arg}"' if " " in arg else arg for arg in script_args if arg
        )
    else:
        rendered = (script_args or "").strip()
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".ssf", prefix="siril_pyscript_", delete=False
    )
    with handle:
        line = f'requires 1.3.4\npyscript "{pyscript}"'
        if rendered:
            line += f" {rendered}"
        handle.write(line + "\n")
    return Path(handle.name)


def _with_register_options(
    script_path: str,
    min_pairs: int = 0,
    framing: str | None = None,
    drop_output_norm: bool = False,
) -> Path | None:
    """Copy ``script_path`` with requested registration and stack options.

    siril-cli exposes neither knob as a flag, so honouring them means amending
    the script. ``-minpairs=`` is appended to the existing ``register`` command.
    Siril's ``-framing=`` lives on ``seqapplyreg``, not on ``register`` --
    ``register`` rejects it with "Unknown parameter" -- so framing needs a line
    of its own inserted directly after the register step, re-applying the
    just-computed transforms with the requested framing. ``drop_output_norm``
    removes Siril's final ``-output_norm`` remapping from stack commands.

    Returns ``None`` when nothing could be amended, which the caller reports
    rather than silently dropping the request.
    """
    if min_pairs <= 0 and not framing and not drop_output_norm:
        return None
    try:
        lines = (
            Path(script_path).read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return None
    amended: list[str] = []
    changed = False
    register_seen = False
    for line in lines:
        stripped = line.strip()
        if drop_output_norm and stripped.startswith("stack "):
            parts = stripped.split()
            if "-output_norm" in parts:
                stripped = " ".join(part for part in parts if part != "-output_norm")
                changed = True
                amended.append(stripped)
                continue
        if stripped.startswith("register "):
            register_seen = True
            if min_pairs > 0:
                # Replace any existing -minpairs= so an explicit argument wins
                # rather than colliding with the script's own value.
                stripped = re.sub(r"-minpairs=\d+", f"-minpairs={min_pairs}", stripped)
                if f"-minpairs={min_pairs}" not in stripped:
                    stripped = f"{stripped} -minpairs={min_pairs}"
                changed = True
            amended.append(stripped)
            if framing:
                parts = stripped.split()
                if len(parts) < 2:
                    return None
                amended.append(f"seqapplyreg {parts[1]} -framing={framing}")
                changed = True
            continue
        amended.append(line)
    if not changed or ((min_pairs > 0 or framing) and not register_seen):
        return None
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".ssf", prefix="siril_osc_", delete=False
    )
    with handle:
        handle.write("\n".join(amended) + "\n")
    return Path(handle.name)


def _pointing_count(work_dir: Path) -> int | None:
    """Read the archived pointing count, preferring the safe multi-pane case.

    The archive manifest records the count at archive time and ``plan.json``
    carries the pointings themselves. If both exist but disagree, the larger
    value wins so a stale single-pointing record cannot trigger ``framing=min``
    across a mosaic.
    """
    counts: list[int] = []
    for name in ("manifest.json", "plan.json"):
        try:
            payload = json.loads((work_dir / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if name == "manifest.json":
            count = payload.get("pointing_count")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                counts.append(count)
        else:
            pointings = payload.get("pointings")
            if isinstance(pointings, list) and pointings:
                counts.append(len(pointings))
    return max(counts) if counts else None


def _effective_framing(
    script: str,
    work_dir: Path,
    requested: str | None,
) -> tuple[str | None, bool]:
    """Resolve framing, defaulting the bundled single-pointing script to min."""
    if requested is not None:
        return requested, False
    if script != "OSC_Preprocessing.ssf" or _pointing_count(work_dir) != 1:
        return None, False
    return "min", True


def _output_signatures(
    work_dir: Path, outputs: list[str]
) -> dict[str, tuple[int, int]]:
    """Capture size and mtime for each output before a script runs.

    The snapshot lets the autocrop step identify the file the run actually
    changed when a force re-run leaves older stacks beside it.
    """
    signatures: dict[str, tuple[int, int]] = {}
    for relative in outputs:
        try:
            stat = (work_dir / relative).stat()
        except OSError:
            continue
        signatures[relative] = (stat.st_mtime_ns, stat.st_size)
    return signatures


def _select_autocrop_output(
    work_dir: Path,
    outputs: list[str],
    before: dict[str, tuple[int, int]],
) -> str | None:
    """Choose the output the current run created or rewrote.

    ``_find_outputs`` sorts root ``result_*.fit`` files first, which is right
    for previewing but can select an older root stack after a force run. A
    changed file wins; when nothing changed (a script that only re-used an
    existing stack), fall back to the normal first output.
    """
    changed = [
        relative
        for relative in outputs
        if _output_signatures(work_dir, [relative]).get(relative)
        != before.get(relative)
    ]
    if changed:
        return changed[0]
    return outputs[0] if outputs else None


def _write_autocrop_wrapper(source: Path, autocrop_script: str, temp_dir: Path) -> Path:
    """Write the .ssf that loads ``source`` and runs ``Autocrop.py``.

    The crop script saves beside Siril's current working directory, so the
    wrapper explicitly changes into a private directory first. That keeps the
    temporary ``*_autocrop.fit`` out of the archive's output discovery and
    lets the caller atomically replace the original stack afterwards.
    """
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".ssf", prefix="siril_autocrop_", delete=False
    )
    with handle:
        handle.write(
            "requires 1.3.4\n"
            f'cd "{temp_dir}"\n'
            f'load "{source}"\n'
            f'pyscript "{autocrop_script}"\n'
        )
    return Path(handle.name)


def _fits_header(path: Path) -> tuple[dict[str, str], int] | None:
    """Read a FITS header's simple cards, returning ``(header, data offset)``.

    Only the ``KEYWORD = value`` form is parsed, which covers every field the
    autocrop gate needs (``BITPIX``, ``NAXIS*``); values are taken before the
    first ``/`` so trailing comments never leak into them. Returns ``None``
    for anything that does not look like a FITS file, which callers must
    treat as "cannot tell" rather than as "no border".
    """
    header: dict[str, str] = {}
    offset = 0
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(2880)
                if len(block) < 2880:
                    return None
                offset += 2880
                for start in range(0, 2880, 80):
                    card = block[start : start + 80].decode("ascii", errors="replace")
                    key = card[:8].strip()
                    if key == "END":
                        return header, offset
                    if key and "=" in card:
                        header[key] = card[10:].split("/", 1)[0].strip()
                if offset > 1_000_000:
                    return None
    except OSError:
        return None


def _fits_shape(header: dict[str, str]) -> tuple[int, ...] | None:
    """Axis lengths from a parsed header, or ``None`` when incomplete."""
    try:
        naxis = int(header["NAXIS"])
        return tuple(int(header[f"NAXIS{axis}"]) for axis in range(1, naxis + 1))
    except (KeyError, ValueError):
        return None


def _empty_fraction(path: Path, sample_stride: int = 8) -> float | None:
    """Share of pixels carrying no signal -- the signature of an empty border.

    Samples every ``sample_stride``-th value of an uncompressed FITS so a
    6 M-pixel stack costs well under a second in the standard library: the
    server deliberately keeps numpy and astropy out of its dependencies.
    The threshold is half the sampled median, i.e. below any plausible sky
    background, so rotation wedges and framing=max borders are counted while
    ordinary dark background is not.

    Returns ``None`` when the file is not a plain uncompressed FITS -- a
    caller must then crop as before rather than assume there is no border.
    """
    parsed = _fits_header(path)
    if parsed is None:
        return None
    header, data_offset = parsed
    shape = _fits_shape(header)
    if not shape:
        return None

    # BITPIX selects both the dtype and how the bytes are laid out on disk.
    types = {-32: ("f", 4), 32: ("i", 4), -64: ("d", 8), 16: ("h", 2), 8: ("B", 1)}
    try:
        kind, width = types[int(header["BITPIX"])]
        total = 1
        for length in shape:
            total *= length
    except (KeyError, ValueError):
        return None
    if total <= 0:
        return None

    try:
        with path.open("rb") as handle:
            handle.seek(data_offset)
            raw = handle.read(width * total)
    except OSError:
        return None
    if len(raw) < width * total:
        return None

    values = array(kind)
    values.frombytes(raw)
    if sys.byteorder == "little":
        # FITS stores numbers big-endian.
        values.byteswap()

    sample = values[::sample_stride] or values
    if not sample:
        return None
    median = sorted(sample)[len(sample) // 2]
    if median <= 0:
        # An all-black frame: nothing to crop either way.
        return 0.0
    threshold = median / 2
    return sum(1 for value in sample if value < threshold) / len(sample)


def _crop_keep_fraction(before: Path, after: Path) -> float | None:
    """Fraction of the field a crop kept, from the two files' headers."""
    first = _fits_header(before)
    second = _fits_header(after)
    if first is None or second is None:
        return None
    shape_before = _fits_shape(first[0])
    shape_after = _fits_shape(second[0])
    if (
        not shape_before
        or not shape_after
        or len(shape_before) < 2
        or len(shape_after) < 2
    ):
        return None
    # FITS axis order, not numpy's: NAXIS1 is the width and NAXIS2 the height,
    # with any channel axis last. (astropy hands back the reverse.)
    area_before = shape_before[0] * shape_before[1]
    area_after = shape_after[0] * shape_after[1]
    if area_before <= 0:
        return None
    return area_after / area_before


def _autocrop_output(
    work_dir: Path,
    output: Path,
    siril_cli: str,
    timeout: int,
) -> dict:
    """Crop ``output`` with Siril's ``Autocrop.py`` and replace it in place.

    ``pyscript`` blocks by default when called from an .ssf, so the wrapper
    finishes before this function looks for the crop. The cropped file is
    moved over the original only after it exists and the command succeeded,
    leaving the uncropped stack untouched on any failure.
    """
    autocrop_script = _resolve_script(AUTOCROP_SCRIPT)
    if not Path(autocrop_script).is_file():
        return {
            "status": "error",
            "message": (
                f"{AUTOCROP_SCRIPT} was not found in Siril's script "
                "directories; set autocrop=False to skip cropping."
            ),
        }

    try:
        temp_dir = Path(tempfile.mkdtemp(prefix=".siril_autocrop_", dir=str(work_dir)))
    except OSError as exc:
        return {
            "status": "error",
            "message": f"could not create an autocrop workspace: {exc}",
        }

    wrapper: Path | None = None
    try:
        try:
            wrapper = _write_autocrop_wrapper(output, autocrop_script, temp_dir)
        except OSError as exc:
            return {
                "status": "error",
                "message": f"could not write the autocrop script: {exc}",
            }

        try:
            proc = subprocess.run(
                [siril_cli, "-d", str(work_dir), "-s", str(wrapper)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(work_dir),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"autocrop timed out after {timeout}s",
            }
        except OSError as exc:
            return {
                "status": "error",
                "message": f"failed to launch siril-cli for autocrop: {exc}",
            }

        combined = (proc.stdout or "") + (proc.stderr or "")
        candidates = sorted(
            (
                path
                for path in temp_dir.iterdir()
                if path.is_file()
                and path.suffix.lower() in {".fit", ".fits", ".fts"}
                and "_autocrop" in path.stem
                and path.stat().st_size > 0
            ),
            key=lambda path: path.name,
        )
        if not candidates:
            candidates = sorted(
                (
                    path
                    for path in temp_dir.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in {".fit", ".fits", ".fts"}
                    and path.stat().st_size > 0
                ),
                key=lambda path: path.name,
            )

        if proc.returncode != 0 or not candidates:
            failures = [marker for marker in FAILURE_MARKERS if marker in combined]
            if failures:
                message = failures[0]
            elif proc.returncode != 0:
                message = f"autocrop exited with status {proc.returncode}"
            else:
                message = "Autocrop.py produced no output FITS"
            return {
                "status": "error",
                "message": message,
                "returncode": proc.returncode,
                "log_tail": _tail(combined, 10),
            }

        # Refuse a crop that would discard most of the field: Autocrop.py's
        # threshold keys on the brightest content, so on a stack with no empty
        # border it returns a rectangle around the subject rather than a
        # border. Returning "skipped" (not "error") leaves the original stack
        # in place and lets the run finish normally.
        keep = _crop_keep_fraction(output, candidates[-1])
        if keep is not None and keep < MIN_CROP_KEEP_FRACTION:
            return {
                "status": "skipped",
                "reason": (
                    f"crop would keep only {keep:.1%} of the field "
                    f"(minimum {MIN_CROP_KEEP_FRACTION:.0%}); "
                    "there is no empty border to remove"
                ),
                "keep_fraction": round(keep, 4),
                "output": str(output),
            }

        try:
            os.replace(candidates[-1], output)
        except OSError as exc:
            return {
                "status": "error",
                "message": f"could not replace {output} with the cropped stack: {exc}",
                "log_tail": _tail(combined, 10),
            }

        return {
            "status": "ok",
            "output": str(output),
            "script": autocrop_script,
        }
    finally:
        if wrapper is not None:
            wrapper.unlink(missing_ok=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


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

    Any ``.fit(s)`` at the root counts, not only ``result_*``: other scripts
    name their deliverable differently (``GPS_Preprocess.py`` writes
    ``<object>_b...-f...-<LIVETIME>s.fit``), and a root-level stack is never an
    intermediate -- masters live in ``masters/`` and calibrated/registered
    frames in ``process/``.
    """
    found: list[str] = []

    def add(match: str) -> None:
        if os.path.isfile(match) and os.path.getsize(match) > 0:
            rel = str(Path(match).relative_to(work_dir))
            if rel not in found:
                found.append(rel)

    # The deliverable first: root result_*.fit (result_400s.fit, ...).
    for match in sorted(glob.glob(str(work_dir / "result_*.fit"))):
        add(match)
    # Then every other stack a script left at the root (GPS_Preprocess.py, ...).
    for pattern in ("*.fit", "*.fits"):
        for match in sorted(glob.glob(str(work_dir / pattern))):
            add(match)
    for base in (work_dir, work_dir / "process"):
        if not base.exists():
            continue
        for name in EXPECTED_OUTPUTS:
            add(str(base / name))
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
        # GPS_Preprocess.py with -bg registers the background-extracted
        # sequence instead, so the plain r_pp_light_* name never appears on
        # that path -- without this a successful run would read as 0 registered.
        "registered_r_pp_lights": count("r_pp_light_*.fit")
        + count("r_bkg_pp_light_*.fit"),
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
    script_args: str | list[str] | None = None,
    autocrop: bool = True,
    framing: Literal["current", "min", "max", "cog"] | None = None,
    preserve_output_norm: bool = False,
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

    ``framing`` explicitly re-applies registration with one of Siril's framing
    options: ``min`` keeps only the area every frame has in common, ``max``
    expands to the bounding box of all frames, ``current`` keeps the reference
    framing, and ``cog`` centres on the centre of gravity. When omitted for
    the bundled ``OSC_Preprocessing.ssf``, it defaults to ``min`` only when
    ``manifest.json`` or ``plan.json`` confirms a single-pointing target.
    Mosaics (more than one pointing), unknown pointing counts, and custom
    scripts keep their script framing. Pass ``framing="current"`` to opt out
    on a single-pointing target. Applies to .ssf scripts only.

    The bundled ``OSC_Preprocessing.ssf`` includes Siril's ``-output_norm``
    option, which affinely remaps nonzero stack pixels using that stack's
    minimum and maximum. That changes absolute background levels between runs
    even when their unnormalized backgrounds match, so this tool removes that
    flag from a temporary copy by default. Set ``preserve_output_norm=True``
    to keep the bundled script's original normalized-output behavior. Custom
    scripts are left unchanged.

    ``script_args`` are passed to a .py script as its argument vector (a
    string like "-ps -z 1 -f 20" or a list of tokens). This is how a script
    that would otherwise open its GUI runs headless: ``GPS_Preprocess.py``
    launches a Qt dialog when given no arguments, but with e.g.
    ``script="GPS_Preprocess.py", script_args="-ps -bg -z 1 -f 20"`` it runs
    its platesolve-mosaic pipeline (seqplatesolve -> seqapplyreg -framing=max
    -> stack -maximize -feather) from the command line. Applies to .py
    scripts only; an .ssf takes no arguments.

    After a successful run, the final output is passed through Siril's
    content-aware ``Autocrop.py`` and replaced with the cropped stack, so
    ``result_*.fit`` no longer contains stacked border artifacts. The
    uncropped intermediate under ``process/`` is left alone. Set
    ``autocrop=False`` only when that script is unavailable or the original
    dimensions must be preserved.

    Cropping is skipped, with the stack left intact, when the data shows
    there is no empty border to remove (under 1% of pixels carrying no
    signal) or when the crop would keep less than a quarter of the field --
    ``Autocrop.py``'s threshold keys on the brightest content, so on a normal
    stack it otherwise trims to the subject rather than to a border. Both are
    reported in ``autocrop.reason``.

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
    effective_framing, automatic_framing = _effective_framing(script, work_dir, framing)
    is_default_osc_script = (
        script == "OSC_Preprocessing.ssf"
        and Path(resolved).name == "OSC_Preprocessing.ssf"
    )
    # Temporary scripts this run needs, all removed in the finally below.
    temporaries: list[Path] = []
    if (min_pairs > 0 or effective_framing) and Path(resolved).suffix == ".py":
        knobs = ", ".join(
            name
            for name, requested in (
                ("min_pairs", min_pairs > 0),
                ("framing", bool(effective_framing)),
            )
            if requested
        )
        return _finish(
            {
                "status": "error",
                "message": f"{knobs} applies to .ssf scripts; a .py cannot be amended.",
            },
            work_dir,
            outputs,
            siril_cli,
            timeout,
        )
    if script_args and Path(resolved).suffix != ".py":
        # siril-cli's -s takes no script arguments, so an .ssf cannot receive
        # them -- reported rather than dropped, so the caller does not believe
        # a configured pipeline ran.
        return _finish(
            {
                "status": "error",
                "message": (
                    "script_args applies to .py scripts; siril-cli passes no "
                    "arguments to an .ssf."
                ),
            },
            work_dir,
            outputs,
            siril_cli,
            timeout,
        )
    if Path(resolved).suffix == ".py":
        # -s parses a .py as Siril commands and runs nothing at all, still
        # reporting success; the wrapper is what actually invokes it.
        temporaries.append(_write_pyscript_wrapper(resolved, script_args))
    else:
        drop_output_norm = is_default_osc_script and not preserve_output_norm
        amended = None
        if min_pairs > 0 or effective_framing or drop_output_norm:
            amended = _with_register_options(
                resolved,
                min_pairs,
                effective_framing,
                drop_output_norm=drop_output_norm,
            )
        if amended is None and (min_pairs > 0 or effective_framing):
            # Reported rather than ignored: silently dropping the argument
            # would leave the caller believing a looser floor, or a different
            # framing, was applied.
            requested = ", ".join(
                part
                for part in (
                    f"min_pairs={min_pairs}" if min_pairs > 0 else "",
                    f"framing={effective_framing}" if effective_framing else "",
                )
                if part
            )
            return _finish(
                {
                    "status": "error",
                    "message": (
                        f"{requested} was requested but {resolved} "
                        "has no register command to amend."
                    ),
                },
                work_dir,
                outputs,
                siril_cli,
                timeout,
            )
        if amended is not None:
            temporaries.append(amended)
    target = str(temporaries[0]) if temporaries else resolved
    before_outputs = _output_signatures(work_dir, outputs)
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

    # Crop only the file this run changed, then replace it in place. Doing it
    # before _finish means preview.png shows the same cropped image the caller
    # will find in result_*.fit. The selected path is also the preview source,
    # so a GPS-named stack is not hidden behind an older root result.
    preview_output = (
        _select_autocrop_output(work_dir, outputs, before_outputs) if outputs else None
    )
    if not outputs:
        autocrop_result: dict = {
            "status": "skipped",
            "reason": "no output to crop",
        }
    elif not autocrop:
        autocrop_result = {"status": "skipped", "reason": "autocrop=False"}
    elif preview_output is None:
        autocrop_result = {
            "status": "skipped",
            "reason": "no output to crop",
        }
    else:
        # Gate the crop on the data: a stack with no empty border has nothing
        # for Autocrop.py to remove, and running it anyway trims to the
        # brightest subject instead (see EMPTY_BORDER_MIN_FRACTION).
        empty = _empty_fraction(work_dir / preview_output)
        if empty is not None and empty < EMPTY_BORDER_MIN_FRACTION:
            autocrop_result = {
                "status": "skipped",
                "reason": (
                    f"no empty border to remove ({empty:.2%} of pixels carry "
                    f"no signal; {EMPTY_BORDER_MIN_FRACTION:.0%} required)"
                ),
                "empty_fraction": round(empty, 5),
            }
        else:
            autocrop_result = _autocrop_output(
                work_dir,
                work_dir / preview_output,
                siril_cli,
                timeout,
            )
            # "skipped" is the backstop declining a runaway crop -- the stack
            # stays as it is and the run still succeeds. Only a real error
            # fails the run.
            if autocrop_result.get("status") == "error":
                return _finish(
                    {
                        "status": "failed",
                        "stage": "autocrop",
                        "message": autocrop_result.get("message", "autocrop failed"),
                        "returncode": autocrop_result.get("returncode"),
                        "log_tail": autocrop_result.get("log_tail", _tail(output)),
                        "autocrop": autocrop_result,
                        "visible_outputs": outputs,
                        "pipeline_stages": _pipeline_stages(work_dir),
                        "light_quality": quality,
                        "warnings": warnings,
                    },
                    work_dir,
                    outputs,
                    siril_cli,
                    timeout,
                    preview_output,
                )
        if autocrop_result.get("status") == "ok":
            outputs = _find_outputs(work_dir)

    return _finish(
        {
            "status": "succeeded" if outputs else "completed_without_expected_output",
            "directory": str(work_dir),
            "script": script,
            "returncode": proc.returncode,
            "log_tail": _tail(output),
            "visible_outputs": outputs,
            "autocrop": autocrop_result,
            "framing": effective_framing,
            "framing_auto_selected": automatic_framing,
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
        preview_output,
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


# Siril's own GraXpert interface, shipped in the Get Scripts repository cache.
# It needs an image already loaded -- bare, it fails with "No image or sequence
# loaded" -- and it writes nothing: apply_blend() pushes the denoised pixels
# back into Siril's current image, so the wrapper has to save them itself.
GRAXPERT_SCRIPT = "GraXpert-AI.py"


# Output name for a denoised sibling stack: result_70s.fit ->
# result_denoised_70s.fit, result.fit -> result_denoised.fit. Anything else
# keeps its own name with a suffix, so an oddly named deliverable still lands
# somewhere obvious beside its source.
def _denoised_name(source: Path) -> str:
    stem = source.stem
    if stem == "result":
        new_stem = "result_denoised"
    elif stem.startswith("result_"):
        new_stem = "result_denoised_" + stem[len("result_") :]
    else:
        new_stem = f"{stem}_denoised"
    return new_stem + (source.suffix or ".fit")


def _is_denoised_stack(path: Path) -> bool:
    """Return whether ``path`` has the sibling name produced by denoising."""
    stem = path.stem.lower()
    return (
        stem == "result_denoised"
        or stem.startswith("result_denoised_")
        or stem.endswith("_denoised")
    )


def _file_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    """Return a stat signature for a nonempty file, or ``None`` if unavailable."""
    try:
        info = path.stat()
    except OSError:
        return None
    if not path.is_file() or info.st_size <= 0:
        return None
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _graxpert_denoise_models() -> list[Path]:
    """Locate every ``model.onnx`` under GraXpert's model directories.

    The directory layout is appdirs' ``user_data_dir(appname="GraXpert")``,
    which differs per platform; the script reads the same location, so this
    only exists to fail with a clear message before Siril is launched rather
    than with the script's own "No models found" buried in a log tail.
    """
    home = Path.home()
    candidates = [
        home / "Library" / "Application Support" / "GraXpert" / "denoise-ai-models",
        home / ".local" / "share" / "GraXpert" / "denoise-ai-models",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "GraXpert" / "denoise-ai-models")

    models: list[Path] = []
    for directory in candidates:
        if directory.is_dir():
            models.extend(sorted(directory.glob("*/model.onnx")))
    return models


@mcp.tool()
def denoise_stack(
    directory: str,
    fits_file: str | None = None,
    strength: float = 0.5,
    gpu: bool = True,
    force: bool = False,
    timeout: int = 3600,
) -> dict:
    """Denoise a stacked FITS with GraXpert AI, writing a sibling file.

    Runs Siril's ``GraXpert-AI.py -denoise`` through a generated wrapper that
    does ``load`` -> ``pyscript`` -> ``save``, because the script needs an
    image already loaded and never writes one itself. The original stack is
    never touched: ``result_70s.fit`` becomes ``result_denoised_70s.fit``.

    ``fits_file`` resolves against the working directory and defaults to the
    first non-denoised output, i.e. the final root stack. Denoised sibling
    outputs are excluded from default source selection; pass ``fits_file``
    explicitly to select a particular raw stack. ``strength`` blends denoised
    with original (0.5 matches both the script default and GraXpert's stored
    preference). ``gpu=False`` forces CPU inference with ``-nogpu`` -- needed
    on a host whose GPU cannot compile the model, where the GPU attempt fails
    with an ONNX Runtime ``model.mil`` error before falling back.

    Denoising is linear-in, linear-out; pair the result with
    ``render_preview(fits_file=...)`` to see it stretched. Requires the model
    to be downloaded first -- checked here so a missing model reads as one
    message rather than a Siril log tail.
    """
    root, root_error = _archive_root(None)
    candidate = Path(directory).expanduser()
    if root is None and not candidate.is_absolute():
        return {"status": "error", "message": root_error}
    work_dir = _resolve_working_directory(directory, root)
    if work_dir is None:
        return {"status": "error", "message": f"Directory does not exist: {directory}"}
    if not _is_working_directory(work_dir):
        return {"status": "error", "message": f"Not a working directory: {directory}"}

    siril_cli = _find_siril_cli()
    if not siril_cli:
        return {
            "status": "error",
            "message": "siril-cli not found. Set SIRIL_CLI to its full path.",
        }

    quality = _read_light_quality(work_dir)
    warnings = _quality_warnings(quality)

    outputs = [
        name for name in _find_outputs(work_dir) if not _is_denoised_stack(Path(name))
    ]
    if fits_file is None:
        if not outputs:
            return {
                "status": "error",
                "message": (
                    "No stack to denoise; pass fits_file= explicitly, and run "
                    "run_osc_preprocessing first if the target is unprocessed."
                ),
            }
        source = work_dir / outputs[0]
    else:
        asked = Path(fits_file).expanduser()
        source = asked if asked.is_absolute() else work_dir / asked
        if not source.is_file():
            return {"status": "error", "message": f"FITS file not found: {fits_file}"}

    output = work_dir / _denoised_name(source)
    prior_output_signature = _file_signature(output)
    output_is_current = False
    if output.is_file() and prior_output_signature is not None and not force:
        try:
            output_is_current = output.stat().st_mtime_ns >= source.stat().st_mtime_ns
        except OSError:
            # If either timestamp cannot be read, do not trust a possibly stale
            # denoised sibling; regenerate it instead.
            output_is_current = False
    if output_is_current:
        return {
            "status": "skipped",
            "message": "Already denoised and current. Pass force=True to re-run.",
            "directory": str(work_dir),
            "input": str(source),
            "output": str(output),
            "light_quality": quality,
            "warnings": warnings,
        }

    models = _graxpert_denoise_models()
    if not models:
        return {
            "status": "error",
            "message": (
                "GraXpert denoise model not downloaded. Download it once from "
                "GraXpert or from Siril's GraXpert-AI.py Model Manager, which "
                "populate the denoise-ai-models directory this script reads."
            ),
        }

    graxpert_script = _resolve_script(GRAXPERT_SCRIPT)
    if not Path(graxpert_script).is_file():
        return {
            "status": "error",
            "message": (f"{GRAXPERT_SCRIPT} not found in Siril's script directories."),
        }

    # save appends its own extension, so the wrapper names the stem and the
    # file is moved onto the intended name afterwards -- that keeps the exact
    # ``result_denoised_<LIVETIME>s.fit`` the caller expects regardless of
    # which suffix Siril chose.
    save_base = output.with_suffix("")
    denoise_args = f"-denoise -strength {strength}" + ("" if gpu else " -nogpu")
    script = (
        "requires 1.4.0\n"
        f'load "{source}"\n'
        f'pyscript "{graxpert_script}" {denoise_args}\n'
        f'save "{save_base}"\n'
    )

    wrapper: Path | None = None
    try:
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".ssf", prefix="siril_denoise_", delete=False
            ) as handle:
                handle.write(script)
                wrapper = Path(handle.name)
        except OSError as exc:
            return {
                "status": "error",
                "message": f"could not write the denoise script: {exc}",
            }

        try:
            proc = subprocess.run(
                [siril_cli, "-d", str(work_dir), "-s", str(wrapper)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(work_dir),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"denoise timed out after {timeout}s",
                "input": str(source),
            }
        except OSError as exc:
            return {
                "status": "error",
                "message": f"failed to launch siril-cli for denoise: {exc}",
            }
    finally:
        if wrapper is not None:
            wrapper.unlink(missing_ok=True)

    combined = (proc.stdout or "") + (proc.stderr or "")

    # Siril may have written <stem>.fit, <stem>.fits or <stem> itself.
    written = [
        path
        for path in (
            output,
            save_base.with_suffix(".fit"),
            save_base.with_suffix(".fits"),
            save_base.with_suffix(".fts"),
            save_base,
        )
        if path.is_file() and path.stat().st_size > 0
    ]
    if written and written[0] != output:
        try:
            os.replace(written[0], output)
        except OSError as exc:
            return {
                "status": "error",
                "message": f"could not move {written[0]} to {output}: {exc}",
                "log_tail": _tail(combined, 10),
            }

    failures = [marker for marker in FAILURE_MARKERS if marker in combined]
    python_failed = bool(_FAILURE_LINE.search(combined))
    if proc.returncode != 0 or not output.is_file() or (failures or python_failed):
        if failures:
            message = failures[0]
        elif python_failed:
            message = _first_failure_line(combined) or "the script reported an error"
        elif proc.returncode != 0:
            message = f"siril-cli exited with status {proc.returncode}"
        else:
            message = "GraXpert produced no output FITS"
        result = {
            "status": "failed",
            "message": message,
            "returncode": proc.returncode,
            "log_tail": _tail(combined),
            "input": str(source),
            "light_quality": quality,
            "warnings": warnings,
        }
        diagnosis = _diagnose(combined, quality)
        if diagnosis:
            result["diagnosis"] = diagnosis
        if "model.mil" in combined or "Failed to create MLModel" in combined:
            result["suggested_fix"] = (
                "GPU inference could not compile the model on this host; "
                "retry with gpu=False to force the CPU execution provider."
            )
        return result

    output_signature = _file_signature(output)
    if output_signature is None:
        return {
            "status": "failed",
            "message": "GraXpert produced no nonempty output FITS",
            "returncode": proc.returncode,
            "log_tail": _tail(combined),
            "input": str(source),
        }
    if output_signature == prior_output_signature:
        return {
            "status": "failed",
            "message": "GraXpert did not refresh the denoised output FITS",
            "returncode": proc.returncode,
            "log_tail": _tail(combined),
            "input": str(source),
        }
    try:
        output_is_current = output.stat().st_mtime_ns >= source.stat().st_mtime_ns
    except OSError:
        output_is_current = False
    if not output_is_current:
        return {
            "status": "failed",
            "message": "GraXpert output FITS is older than its source stack",
            "returncode": proc.returncode,
            "log_tail": _tail(combined),
            "input": str(source),
        }

    return {
        "status": "succeeded",
        "directory": str(work_dir),
        "script": graxpert_script,
        "input": str(source),
        "output": str(output),
        "strength": strength,
        "gpu": gpu,
        "model": str(models[-1].parent.name),
        "returncode": proc.returncode,
        "log_tail": _tail(combined),
        "light_quality": quality,
        "warnings": warnings,
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
    preview_output: str | None = None,
) -> list[str]:
    """Render the preview when there is an image, then shape the tool reply.

    The reply is the structured result as JSON text followed by the preview
    PNG's path. The image itself is never shipped back — the agent reads the
    file when it wants to look. ``preview_output`` names the output the run
    actually changed when it is not the first entry in ``outputs`` (a GPS
    stack beside an older ``result_*.fit``).
    """
    extras: list[str] = []
    if siril_cli and outputs:
        source_relative = preview_output if preview_output in outputs else outputs[0]
        path, error = _render_preview(
            work_dir, work_dir / source_relative, siril_cli, timeout
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
