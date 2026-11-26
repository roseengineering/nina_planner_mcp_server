"""Tests for preview, denoise, cleanup, and environment status tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

from siril_mcp import server


def _target(tmp_path: Path, monkeypatch, *, stack: bool = True) -> Path:
    root = tmp_path / "archive"
    target = root / "target"
    (target / "lights").mkdir(parents=True)
    if stack:
        (target / "result_60s.fit").write_bytes(b"stack")
    monkeypatch.setenv("ARCHIVE_ROOT", str(root))
    return target


def test_render_preview_generates_named_png_with_mocked_siril(
    tmp_path: Path, monkeypatch
) -> None:
    """Preview rendering uses the requested stack and preserves its name."""
    target = _target(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_find_siril_cli", lambda: "siril-cli")

    def run(command, **_kwargs):
        wrapper = Path(command[-1])
        assert 'autostretch\nsavepng "' in wrapper.read_text(encoding="utf-8")
        (target / "result_60s.png").write_bytes(b"png")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)

    result = server.render_preview("target", "result_60s.fit")

    assert result == {
        "status": "ok",
        "preview": str(target / "result_60s.png"),
        "source": str(target / "result_60s.fit"),
        "state": "succeeded",
    }


def test_render_preview_reports_missing_source_and_missing_png(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing input and missing generated PNG are surfaced as errors."""
    target = _target(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_find_siril_cli", lambda: "siril-cli")
    assert (
        "FITS file not found"
        in server.render_preview("target", "missing.fit")["message"]
    )

    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="no output", stderr=""
        ),
    )
    result = server.render_preview("target", "result_60s.fit")
    assert result["status"] == "error"
    assert "produced no PNG" in result["message"]
    assert target.is_dir()


def test_denoise_stack_writes_sibling_and_forces_cpu_when_requested(
    tmp_path: Path, monkeypatch
) -> None:
    """Denoising writes a sibling stack and passes the CPU option."""
    target = _target(tmp_path, monkeypatch)
    graxpert = tmp_path / "GraXpert-AI.py"
    graxpert.write_text("# fake script\n", encoding="utf-8")
    monkeypatch.setattr(server, "_find_siril_cli", lambda: "siril-cli")
    monkeypatch.setattr(
        server,
        "_graxpert_denoise_models",
        lambda: [tmp_path / "models" / "v1" / "model.onnx"],
    )
    monkeypatch.setattr(server, "_resolve_script", lambda _name: str(graxpert))
    wrapper_text: list[str] = []

    def run(command, **_kwargs):
        wrapper_text.append(Path(command[-1]).read_text(encoding="utf-8"))
        (target / "result_denoised_60s.fit").write_bytes(b"denoised")
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="done", stderr=""
        )

    monkeypatch.setattr(server.subprocess, "run", run)

    result = server.denoise_stack("target", strength=0.3, gpu=False)

    assert result["status"] == "succeeded"
    assert result["output"] == str(target / "result_denoised_60s.fit")
    assert result["strength"] == 0.3
    assert result["gpu"] is False
    assert result["model"] == "v1"
    assert "-denoise -strength 0.3 -nogpu" in wrapper_text[0]


def test_denoise_stack_skips_existing_output_and_handles_missing_model(
    tmp_path: Path, monkeypatch
) -> None:
    """Existing output is skipped and absent GraXpert models are reported."""
    target = _target(tmp_path, monkeypatch)
    (target / "result_denoised_60s.fit").write_bytes(b"previous")
    monkeypatch.setattr(server, "_find_siril_cli", lambda: "siril-cli")
    monkeypatch.setattr(server, "_graxpert_denoise_models", lambda: [])

    skipped = server.denoise_stack("target")
    assert skipped["status"] == "skipped"

    (target / "result_denoised_60s.fit").unlink()
    missing = server.denoise_stack("target")
    assert missing["status"] == "error"
    assert "model not downloaded" in missing["message"]


def test_clean_working_directory_preserves_inputs_and_requires_force_for_partial(
    tmp_path: Path, monkeypatch
) -> None:
    """Cleanup guards partial runs and preserves input data."""
    target = _target(tmp_path, monkeypatch, stack=False)
    process = target / "process"
    process.mkdir()
    (process / "frame.fit").write_bytes(b"temporary")
    (target / "masters").mkdir()
    (target / "masters" / "master.fit").write_bytes(b"master")
    (target / "preview.png").write_bytes(b"preview")

    refused = server.clean_working_directory("target")
    assert refused["status"] == "error"
    assert "Pass force=True" in refused["message"]
    assert process.exists()

    cleaned = server.clean_working_directory("target", include_masters=True, force=True)
    assert cleaned["status"] == "ok"
    assert set(cleaned["removed"]) == {"process", "masters"}
    assert not process.exists()
    assert not (target / "masters").exists()
    assert (target / "lights").is_dir()
    assert (target / "preview.png").is_file()


def test_clean_working_directory_refuses_non_target(
    tmp_path: Path, monkeypatch
) -> None:
    """Cleanup refuses folders that are not archived target directories."""
    root = tmp_path / "archive"
    root.mkdir()
    ordinary = root / "ordinary"
    ordinary.mkdir()
    monkeypatch.setenv("ARCHIVE_ROOT", str(root))

    result = server.clean_working_directory("ordinary", force=True)

    assert result["status"] == "error"
    assert "not a working directory" in result["message"]


def test_get_siril_status_reports_cli_version_and_archive(
    tmp_path: Path, monkeypatch
) -> None:
    """The status tool parses the version and reports resolved environment."""
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setenv("ARCHIVE_ROOT", str(root))
    monkeypatch.setenv("SIRIL_CLI", "custom-siril")
    monkeypatch.setattr(server, "_find_siril_cli", lambda: "custom-siril")
    monkeypatch.setattr(
        server,
        "_script_directories",
        lambda: [(tmp_path / "scripts", "configured")],
    )
    monkeypatch.setattr(
        server,
        "SIRIL_SUPPORT_DIR",
        tmp_path / "support",
    )
    (tmp_path / "support" / "siril" / "venv").mkdir(parents=True)
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Siril is started as macOS application\nSiril 1.4.0",
            stderr="",
        ),
    )

    result = server.get_siril_status()

    assert result["siril_cli"] == {
        "found": True,
        "path": "custom-siril",
        "version": "Siril 1.4.0",
    }
    assert result["archive_root"] == {"path": str(root), "error": None}
    assert result["python_venv"]["present"] is True
    assert result["environment"]["SIRIL_CLI"] == "custom-siril"
