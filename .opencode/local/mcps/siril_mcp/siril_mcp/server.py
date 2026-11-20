"""MCP entry point: importing the tool modules registers their tools on ``mcp``."""

import csv
import glob
import json
import os
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

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


# Directories Siril searches for .ssf scripts (mirrors script_path in
# config.1.4.ini). Headless siril-cli does NOT apply that search path, so a
# bare script name must be resolved to an absolute path here.
SCRIPT_DIRS = (
    Path("/Applications/Siril.app/Contents/Resources/share/siril/scripts"),
    Path.home() / "Library/Application Support/org.siril.Siril/scripts",
    Path.home() / ".siril/scripts",
    Path.home() / "siril/scripts",
)


def _resolve_script(script: str) -> str:
    """Return an absolute path for ``script`` so headless siril-cli can find it.

    Absolute paths are used as-is when they exist; otherwise the bundled and
    user script directories are searched. Falls back to the original string so
    Siril can still report a helpful error.
    """
    candidate = Path(script).expanduser()
    if candidate.is_absolute():
        return str(candidate) if candidate.is_file() else script
    if candidate.is_file():
        return str(candidate.resolve())
    for base in SCRIPT_DIRS:
        target = base / script
        if target.is_file():
            return str(target)
    return script


def _find_outputs(work_dir: Path) -> list[str]:
    """Check the root working directory and Siril's default 'process/' subfolder."""
    found = []
    for base in (work_dir, work_dir / "process"):
        if not base.exists():
            continue
        for name in EXPECTED_OUTPUTS:
            candidate = base / name
            if candidate.is_file() and candidate.stat().st_size > 0:
                found.append(str(candidate.relative_to(work_dir)))
    # OSC_Preprocessing.ssf also saves the final stack as result_<LIVETIME>s.fit
    # in the working directory root.
    for match in glob.glob(str(work_dir / "result_*.fit")):
        if os.path.isfile(match) and os.path.getsize(match) > 0:
            rel = str(Path(match).relative_to(work_dir))
            if rel not in found:
                found.append(rel)
    return found


def _scan_targets(root: Path) -> dict:
    """Summarise light quality across every target under an imaging root.

    Lets an operator see at a glance which targets are worth sending to Siril
    before spending time on runs that die at registration.
    """
    below: list[str] = []
    star_counts: list[float] = []
    with_lights = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        quality = _read_light_quality(child)
        if not quality:
            continue
        with_lights += 1
        stars = quality.get("median_detected_stars")
        if stars is None:
            continue
        star_counts.append(stars)
        if stars < STAR_WARNING_THRESHOLD:
            below.append(child.name)

    summary: dict = {
        "targets_with_lights": with_lights,
        "below_star_threshold": len(below),
        "star_threshold": STAR_WARNING_THRESHOLD,
    }
    if star_counts:
        summary["median_detected_stars"] = round(statistics.median(star_counts), 1)
        summary["min_detected_stars"] = int(min(star_counts))
        summary["max_detected_stars"] = int(max(star_counts))
    if below:
        summary["targets_below_threshold"] = below
    return summary


@mcp.tool()
def check_directory_status(directory: str | None = None) -> dict:
    """Check whether a directory has already been processed by Siril.

    Reports whether it succeeded and whether the resulting stacked image is
    present and visible on disk. With no argument, inspects ``ARCHIVE_ROOT``;
    pass a target directory to inspect that target, or the archive root to get
    a cross-target quality summary.
    """
    root = directory or os.environ.get("ARCHIVE_ROOT")
    if root is None:
        # Report a missing setting as a setting, not as a missing directory:
        # a fabricated default would surface as "Directory not found" and send
        # whoever reads it looking for the wrong thing.
        return {
            "exists": False,
            "processed": False,
            "succeeded": False,
            "visible_outputs": [],
            "error": "ARCHIVE_ROOT is not set; pass directory= explicitly.",
        }
    work_dir = Path(root).expanduser().resolve()
    if not work_dir.is_dir():
        return {
            "exists": False,
            "processed": False,
            "succeeded": False,
            "visible_outputs": [],
            "error": f"Directory not found: {work_dir}",
        }

    # Check standard Siril preprocessing folders (biases, darks, flats, lights)
    expected_subdirs = ["biases", "darks", "flats", "lights"]
    present_subdirs = [d for d in expected_subdirs if (work_dir / d).is_dir()]
    has_process_dir = (work_dir / "process").is_dir()
    outputs = _find_outputs(work_dir)
    quality = _read_light_quality(work_dir)

    result = {
        "directory": str(work_dir),
        "processed": has_process_dir or len(outputs) > 0,
        "succeeded": len(outputs) > 0,
        "visible_outputs": outputs,
        "input_folders_found": present_subdirs,
        "light_quality": quality,
        "warnings": _quality_warnings(quality),
    }
    # An imaging root holds target subfolders rather than lights/ directly, so
    # report a cross-target summary instead of a single (absent) quality block.
    if quality is None and not present_subdirs:
        result["targets"] = _scan_targets(work_dir)
    return result


@mcp.tool(structured_output=False)
def run_osc_preprocessing(
    directory: str,
    script: str = "OSC_Preprocessing.ssf",
    force: bool = False,
    timeout: int = 3600,
    min_stars: int = 0,
) -> list[str]:
    """Run a Siril script headlessly via siril-cli and preview the result.

    Defaults to OSC_Preprocessing.ssf on ``directory``. Skips execution if
    already processed unless ``force=True``. Set ``min_stars`` to pre-flight
    skip targets whose ImageMetaData.csv shows too few stars to register.

    Returns the structured result as JSON text, followed by the preview PNG's
    path whenever there is an output to review. The image itself stays on disk
    for the agent to read.
    """
    work_dir = Path(directory).expanduser().resolve()
    if not work_dir.is_dir():
        return _finish(
            {"status": "error", "message": f"Directory does not exist: {work_dir}"},
            work_dir,
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
                "visible_outputs": outputs,
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
    cmd = [siril_cli, "-d", str(work_dir), "-s", _resolve_script(script)]
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

    output = (proc.stdout or "") + (proc.stderr or "")
    failures = [m for m in FAILURE_MARKERS if m in output]
    outputs = _find_outputs(work_dir)

    # siril-cli exits non-zero when a script aborts. The markers are kept as a
    # secondary signal in case a run reports failure but still exits 0.
    if proc.returncode != 0 or (failures and not outputs):
        message = (
            failures[0]
            if failures
            else f"siril-cli exited with status {proc.returncode}"
        )
        result = {
            "status": "failed",
            "message": message,
            "returncode": proc.returncode,
            "log_tail": _tail(output),
            "visible_outputs": outputs,
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
            "light_quality": quality,
            "warnings": warnings,
        },
        work_dir,
        outputs,
        siril_cli,
        timeout,
    )


def _tail(text: str, lines: int = 30) -> str:
    """Return the last ``lines`` non-empty lines of Siril output for diagnostics."""
    kept = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _generate_preview(
    work_dir: Path,
    outputs: list[str],
    siril_cli: str,
    timeout: int,
) -> tuple[str | None, str | None]:
    """Autostretch the first output to a PNG for the agent to review.

    Returns ``(path, error)``. Only the path travels back in the tool
    response; the PNG itself stays on disk for the agent to read or manipulate.
    """
    if not outputs:
        return None, "no output image available to preview"
    source = work_dir / outputs[0]
    if not source.is_file():
        return None, f"output not found: {outputs[0]}"

    preview_base = work_dir / PREVIEW_NAME
    preview_path = work_dir / f"{PREVIEW_NAME}.png"
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
        path, error = _generate_preview(work_dir, outputs, siril_cli, timeout)
        if path:
            result["preview"] = path
            extras.append(path)
        if error:
            result["preview_error"] = error
    return [json.dumps(result, indent=1), *extras]


# NINA records per-frame telemetry in lights/ImageMetaData.csv. Across the
# targets processed so far, registration succeeded at 288 median stars and
# failed at 3, 9 and 28, so this is a conservative floor for a warning.
STAR_WARNING_THRESHOLD = 50

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
            f"Only {stars:.0f} stars detected per frame (median); Siril's "
            f"registration typically needs >={STAR_WARNING_THRESHOLD} and may "
            "abort with 'not enough stars in reference image'."
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
_DIAGNOSES = (
    (
        "not enough stars in reference image",
        "Registration could not align the lights: the reference frame has too "
        "few detectable stars for Global Star Alignment.",
        "Check lights/ImageMetaData.csv median_detected_stars; re-shoot the "
        "target higher in the sky or with longer exposures.",
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
