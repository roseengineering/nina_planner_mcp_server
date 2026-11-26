"""Tests for guarded Siril autocrop execution."""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

from siril_mcp import server


def _write_float_fits(path: Path, width: int, height: int) -> None:
    """Write a small valid float FITS image for crop geometry checks."""
    cards = [
        f"{'SIMPLE':<8}= {'T':>20}".ljust(80),
        f"{'BITPIX':<8}= {-32:>20}".ljust(80),
        f"{'NAXIS':<8}= {2:>20}".ljust(80),
        f"{'NAXIS1':<8}= {width:>20}".ljust(80),
        f"{'NAXIS2':<8}= {height:>20}".ljust(80),
        "END".ljust(80),
    ]
    header = "".join(cards).encode("ascii")
    header += b" " * ((-len(header)) % 2880)
    data = struct.pack(f">{width * height}f", *([1.0] * width * height))
    data += b"\0" * ((-len(data)) % 2880)
    path.write_bytes(header + data)


def _autocrop_fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Return an original stack and a fake autocrop script."""
    original = tmp_path / "result.fit"
    _write_float_fits(original, 8, 8)
    script = tmp_path / "Autocrop.py"
    script.write_text("# test script\n", encoding="utf-8")
    monkeypatch.setattr(server, "_resolve_script", lambda _name: str(script))
    return original, script


def test_autocrop_output_replaces_stack_and_removes_temporary_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    """A valid crop is moved over the original after successful Siril execution."""
    original, script = _autocrop_fixture(tmp_path, monkeypatch)
    original_before = original.read_bytes()

    def run(command, **_kwargs):
        wrapper = Path(command[-1])
        work_line = wrapper.read_text(encoding="utf-8").splitlines()[1]
        temp_dir = Path(work_line.removeprefix('cd "').removesuffix('"'))
        _write_float_fits(temp_dir / "result_autocrop.fit", 4, 8)
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )

    monkeypatch.setattr(server.subprocess, "run", run)

    result = server._autocrop_output(tmp_path, original, "siril-cli", 10)

    assert result == {
        "status": "ok",
        "output": str(original),
        "script": str(script),
    }
    assert original.read_bytes() != original_before
    assert server._fits_shape(server._fits_header(original)[0]) == (4, 8)
    assert not list(tmp_path.glob(".siril_autocrop_*"))


def test_autocrop_output_refuses_crop_that_discards_most_of_the_field(
    tmp_path: Path, monkeypatch
) -> None:
    """A runaway crop is skipped and leaves the original stack untouched."""
    original, _script = _autocrop_fixture(tmp_path, monkeypatch)
    original_before = original.read_bytes()

    def run(command, **_kwargs):
        work_line = Path(command[-1]).read_text(encoding="utf-8").splitlines()[1]
        temp_dir = Path(work_line.removeprefix('cd "').removesuffix('"'))
        _write_float_fits(temp_dir / "result_autocrop.fit", 1, 8)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)

    result = server._autocrop_output(tmp_path, original, "siril-cli", 10)

    assert result["status"] == "skipped"
    assert result["keep_fraction"] == 0.125
    assert original.read_bytes() == original_before


def test_autocrop_output_handles_missing_script_and_cli_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing scripts and failed Siril processes return actionable errors."""
    original = tmp_path / "result.fit"
    _write_float_fits(original, 8, 8)
    monkeypatch.setattr(
        server, "_resolve_script", lambda _name: str(tmp_path / "missing.py")
    )
    assert server._autocrop_output(tmp_path, original, "siril-cli", 10)["status"] == (
        "error"
    )

    script = tmp_path / "Autocrop.py"
    script.write_text("# test script\n", encoding="utf-8")
    monkeypatch.setattr(server, "_resolve_script", lambda _name: str(script))
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=2, stdout="failure", stderr=""
        ),
    )
    result = server._autocrop_output(tmp_path, original, "siril-cli", 10)
    assert result["status"] == "error"
    assert result["message"] == "autocrop exited with status 2"
    assert original.is_file()
