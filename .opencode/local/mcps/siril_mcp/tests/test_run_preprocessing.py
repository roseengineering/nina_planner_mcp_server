"""Tests for headless preprocessing orchestration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from siril_mcp import server


def _archive_target(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    root = tmp_path / "archive"
    target = root / "target"
    (target / "lights").mkdir(parents=True)
    monkeypatch.setenv("ARCHIVE_ROOT", str(root))
    script = tmp_path / "custom.ssf"
    script.write_text("requires 1.3.4\n", encoding="utf-8")
    monkeypatch.setattr(server, "_find_siril_cli", lambda: "siril-cli")
    monkeypatch.setattr(server, "_resolve_script", lambda _script: str(script))
    monkeypatch.setattr(
        server,
        "_render_preview",
        lambda _work_dir, _source, _cli, _timeout, _name=server.PREVIEW_NAME: (
            "preview.png",
            None,
        ),
    )
    return target, script


def test_run_preprocessing_success_reports_output_and_preview(
    tmp_path: Path, monkeypatch
) -> None:
    """A successful run discovers its stack and attaches the preview."""
    target, _script = _archive_target(tmp_path, monkeypatch)

    def run(_command, **_kwargs):
        (target / "result_60s.fit").write_bytes(b"stack")
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="stack complete", stderr=""
        )

    monkeypatch.setattr(server.subprocess, "run", run)

    result = server.run_osc_preprocessing("target", autocrop=False)
    payload = json.loads(result[0])

    assert payload["status"] == "succeeded"
    assert payload["visible_outputs"] == ["result_60s.fit"]
    assert payload["autocrop"] == {"status": "skipped", "reason": "autocrop=False"}
    assert payload["preview"] == "preview.png"
    assert result[1] == "preview.png"


def test_run_preprocessing_detects_script_failure_even_with_zero_exit(
    tmp_path: Path, monkeypatch
) -> None:
    """Python exceptions in Siril output fail even when its exit code is zero."""
    _archive_target(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Traceback (most recent call last):\nRuntimeError: bad input",
            stderr="",
        ),
    )

    payload = json.loads(server.run_osc_preprocessing("target")[0])

    assert payload["status"] == "failed"
    assert payload["message"] == "RuntimeError: bad input"
    assert payload["visible_outputs"] == []


def test_run_preprocessing_timeout_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A subprocess timeout is returned as a failed preprocessing result."""
    _archive_target(tmp_path, monkeypatch)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("siril-cli", 7)

    monkeypatch.setattr(server.subprocess, "run", timeout)

    payload = json.loads(server.run_osc_preprocessing("target", timeout=7)[0])

    assert payload["status"] == "failed"
    assert payload["message"] == "siril-cli timed out after 7s"


def test_run_preprocessing_rejects_ssf_script_arguments(
    tmp_path: Path, monkeypatch
) -> None:
    """Arguments intended for Python scripts are not silently dropped."""
    _archive_target(tmp_path, monkeypatch)
    called = False

    def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess should not run for invalid arguments")

    monkeypatch.setattr(server.subprocess, "run", should_not_run)

    payload = json.loads(
        server.run_osc_preprocessing("target", script_args="-headless")[0]
    )

    assert payload["status"] == "error"
    assert "script_args applies to .py scripts" in payload["message"]
    assert called is False
