"""Regression tests for isolated Siril processing helpers."""

from __future__ import annotations

from pathlib import Path

from siril_mcp import server


def test_resolve_script_searches_nested_directories_in_order(
    tmp_path: Path, monkeypatch
) -> None:
    """A matching nested script resolves to its absolute path."""
    configured = tmp_path / "configured"
    repository = tmp_path / "repository"
    nested = configured / "processing"
    nested.mkdir(parents=True)
    repository.mkdir()
    configured_script = nested / "pipeline.ssf"
    repository_script = repository / "pipeline.ssf"
    configured_script.write_text("configured", encoding="utf-8")
    repository_script.write_text("repository", encoding="utf-8")
    monkeypatch.setattr(
        server,
        "_script_directories",
        lambda: [(configured, "configured"), (repository, "repository")],
    )

    assert server._resolve_script("pipeline.ssf") == str(configured_script)
    assert server._resolve_script("missing.ssf") == "missing.ssf"


def test_find_outputs_prefers_root_results_and_ignores_empty_files(
    tmp_path: Path,
) -> None:
    """Deliverables are ordered first and zero-byte files are excluded."""
    (tmp_path / "result_300s.fit").write_bytes(b"result")
    (tmp_path / "result_120s.fit").write_bytes(b"result")
    (tmp_path / "other.fits").write_bytes(b"other stack")
    (tmp_path / "empty.fit").write_bytes(b"")
    process = tmp_path / "process"
    process.mkdir()
    (process / "result.fit").write_bytes(b"intermediate")

    assert server._find_outputs(tmp_path) == [
        "result_120s.fit",
        "result_300s.fit",
        "other.fits",
        "process/result.fit",
    ]


def test_select_autocrop_output_chooses_rewritten_file_then_falls_back(
    tmp_path: Path,
) -> None:
    """A force-run rewrite takes priority over stale outputs."""
    first = tmp_path / "result_old.fit"
    second = tmp_path / "result_new.fit"
    first.write_bytes(b"old")
    second.write_bytes(b"new")
    outputs = [first.name, second.name]
    before = server._output_signatures(tmp_path, outputs)

    second.write_bytes(b"newly-written stack")
    assert server._select_autocrop_output(tmp_path, outputs, before) == second.name
    assert (
        server._select_autocrop_output(
            tmp_path, outputs, server._output_signatures(tmp_path, outputs)
        )
        == first.name
    )
    assert server._select_autocrop_output(tmp_path, [], before) is None


def test_first_failure_line_prefers_exception_over_traceback_header() -> None:
    """Traceback diagnostics return the exception rather than its preamble."""
    output = (
        "ordinary log line\n"
        "Traceback (most recent call last):\n"
        '  File "pipeline.py", line 12, in run\n'
        "ValueError: invalid image dimensions\n"
    )

    assert server._first_failure_line(output) == "ValueError: invalid image dimensions"
    assert server._first_failure_line("Script completed successfully") is None


def test_pyscript_wrapper_quotes_script_and_argument_paths() -> None:
    """Generated wrappers safely quote script paths and spaced arguments."""
    wrapper = server._write_pyscript_wrapper(
        "/scripts/Pre process.py",
        ["--output", "/archive/target with spaces"],
    )
    try:
        assert wrapper.read_text(encoding="utf-8") == (
            "requires 1.3.4\n"
            'pyscript "/scripts/Pre process.py" --output '
            '"/archive/target with spaces"\n'
        )
    finally:
        wrapper.unlink(missing_ok=True)
