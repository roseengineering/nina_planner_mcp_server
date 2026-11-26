"""Tests for Siril script paths and best-effort descriptions."""

from __future__ import annotations

from pathlib import Path

from siril_mcp import server


def test_configured_script_paths_precede_system_and_repository_without_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    """Config entries are read first and duplicate directories are collapsed."""
    support = tmp_path / "support"
    config_dir = support / "siril"
    config_dir.mkdir(parents=True)
    configured = tmp_path / "configured"
    system = tmp_path / "system"
    repository = tmp_path / "repository"
    (config_dir / "config.ini").write_text(
        f"script_path={configured};{system}\n", encoding="utf-8"
    )
    monkeypatch.setattr(server, "SIRIL_SUPPORT_DIR", support)
    monkeypatch.setattr(server, "SYSTEM_SCRIPTS_DIRS", (system, configured))
    monkeypatch.setattr(server, "REPOSITORY_SCRIPTS_DIR", repository)

    assert server._config_script_dirs() == [configured, system]
    assert server._script_directories() == [
        (configured, "configured"),
        (system, "configured"),
        (repository, "repository"),
    ]


def test_scripts_in_finds_nested_files_in_stable_order(tmp_path: Path) -> None:
    """Script discovery is recursive, filters extensions, and sorts results."""
    nested = tmp_path / "processing"
    nested.mkdir()
    (nested / "z.py").write_text("", encoding="utf-8")
    (nested / "b.ssf").write_text("", encoding="utf-8")
    (nested / "a.ssf").write_text("", encoding="utf-8")
    (nested / "ignore.txt").write_text("", encoding="utf-8")

    assert [path.name for path in server._scripts_in(tmp_path)] == [
        "a.ssf",
        "b.ssf",
        "z.py",
    ]
    assert server._scripts_in(tmp_path / "missing") == []


def test_script_descriptions_prefer_python_docstrings_and_filter_header_noise(
    tmp_path: Path,
) -> None:
    """Python docstrings win; SSF headers discard metadata and section banners."""
    python_script = tmp_path / "process.py"
    python_script.write_text(
        '"""A concise processing tool.\n\nLonger details are omitted."""\n'
        "raise RuntimeError('must not execute')\n",
        encoding="utf-8",
    )
    assert server._script_description(python_script) == "A concise processing tool."

    ssf_script = tmp_path / "process.ssf"
    ssf_script.write_text(
        "# Script for Siril 1.4\n"
        "# Author: Example\n"
        "# ====================\n"
        "# PREPROCESSING SCRIPT\n"
        "# Calibrate and register the light frames.\n"
        "requires 1.3.4\n",
        encoding="utf-8",
    )
    assert server._script_description(ssf_script) == (
        "Calibrate and register the light frames."
    )


def test_script_description_falls_back_from_invalid_python_docstring(
    tmp_path: Path,
) -> None:
    """Malformed Python falls back to its leading comments without execution."""
    script = tmp_path / "fallback.py"
    script.write_text(
        "# Render a stretched preview.\n# Additional context.\ndef broken(:\n",
        encoding="utf-8",
    )

    assert server._script_description(script) == (
        "Render a stretched preview. Additional context."
    )
    assert server._one_paragraph("  First line\nsecond line\n\nignored") == (
        "First line second line"
    )
    assert server._one_paragraph(None) is None
    assert server._useful_comment("-----") is False
