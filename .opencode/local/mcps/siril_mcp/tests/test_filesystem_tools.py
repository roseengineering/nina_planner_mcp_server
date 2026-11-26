"""Tests for Siril archive and script-listing tools."""

from __future__ import annotations

from pathlib import Path

from siril_mcp import server


def _working_directory(path: Path, input_set: str = "lights") -> Path:
    """Create a minimal archived target directory."""
    (path / input_set).mkdir(parents=True)
    return path


def test_list_working_directories_summarizes_states_and_prunes_hidden_dirs(
    tmp_path: Path,
) -> None:
    """Only target folders are listed, not frame folders or hidden trees."""
    root = tmp_path / "archive"
    root.mkdir()
    ready = _working_directory(root / "m31")
    succeeded = _working_directory(root / "observatory-a" / "m42", "biases")
    (succeeded / "result.fit").write_bytes(b"stack")
    partial = _working_directory(root / "observatory-b" / "m13", "darks")
    (partial / "process").mkdir()

    # A nested working-directory-like folder inside an archived target must not
    # be discovered while walking that target's frame sets.
    _working_directory(ready / "lights" / "nested-not-a-target")
    _working_directory(root / ".hidden" / "ignored")
    (root / "ordinary-folder").mkdir()

    result = server.list_working_directories(str(root))

    assert result["summary"]["total_directories"] == 3
    assert result["summary"]["by_observatory"] == {
        "root": 1,
        "observatory-a": 1,
        "observatory-b": 1,
    }
    assert result["summary"]["by_processing_state"] == {
        "unprocessed": 1,
        "succeeded": 1,
        "failed_partial": 1,
    }
    assert {entry["name"] for entry in result["directories"]} == {
        "m31",
        "m42",
        "m13",
    }
    assert (
        next(row for row in result["directories"] if row["name"] == "m31")[
            "observatory"
        ]
        is None
    )


def test_list_working_directories_reports_missing_or_invalid_root(
    tmp_path: Path, monkeypatch
) -> None:
    """An unset or nonexistent archive root returns a useful error."""
    monkeypatch.delenv("ARCHIVE_ROOT", raising=False)
    assert "ARCHIVE_ROOT is not set" in server.list_working_directories()["error"]

    missing = tmp_path / "missing"
    assert server.list_working_directories(str(missing)) == {
        "error": f"Directory not found: {missing}"
    }


def test_check_directory_status_resolves_bare_name_and_reports_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    """A listed target name can be passed back for detailed status."""
    root = tmp_path / "archive"
    target = _working_directory(root / "observatory-a" / "m42")
    process = target / "process"
    process.mkdir()
    (process / "light_0001.fit").write_bytes(b"converted")
    (process / "pp_light_0001.fit").write_bytes(b"calibrated")
    (process / "r_bkg_pp_light_0001.fit").write_bytes(b"registered")
    (target / "masters").mkdir()
    (target / "masters" / "master.fit").write_bytes(b"master")
    monkeypatch.setenv("ARCHIVE_ROOT", str(root))

    result = server.check_directory_status("m42")

    assert result["directory"] == str(target)
    assert result["observatory"] == "observatory-a"
    assert result["state"] == "failed_partial"
    assert result["processed"] is True
    assert result["succeeded"] is False
    assert result["pipeline_stages"] == {
        "converted_lights": 1,
        "calibrated_pp_lights": 1,
        "registered_r_pp_lights": 1,
        "masters_present": True,
        "intermediates_cleaned": False,
    }


def test_check_directory_status_handles_missing_root_and_non_target(
    tmp_path: Path, monkeypatch
) -> None:
    """Relative names need an archive root and folders need archived inputs."""
    monkeypatch.delenv("ARCHIVE_ROOT", raising=False)
    assert "ARCHIVE_ROOT is not set" in server.check_directory_status("m42")["error"]

    root = tmp_path / "archive"
    root.mkdir()
    ordinary = root / "ordinary"
    ordinary.mkdir()
    monkeypatch.setenv("ARCHIVE_ROOT", str(root))
    assert (
        "Working directory not found"
        in server.check_directory_status("unknown")["error"]
    )
    assert (
        "is not a working directory"
        in server.check_directory_status("ordinary")["error"]
    )


def test_list_scripts_filters_entries_and_keeps_filtered_directory_counts(
    tmp_path: Path, monkeypatch
) -> None:
    """Kind, source, and query filters agree with per-directory counts."""
    configured = tmp_path / "configured"
    repository = tmp_path / "repository"
    (configured / "processing").mkdir(parents=True)
    repository.mkdir()
    (configured / "processing" / "OSC_Preprocessing.ssf").write_text(
        "requires 1.3.4\n", encoding="utf-8"
    )
    (configured / "preview.py").write_text(
        '"""Render a preprocessing preview."""\n', encoding="utf-8"
    )
    (repository / "other.ssf").write_text("requires 1.3.4\n", encoding="utf-8")
    monkeypatch.setattr(
        server,
        "_script_directories",
        lambda: [(configured, "configured"), (repository, "repository")],
    )
    monkeypatch.setattr(
        server,
        "_script_description",
        lambda path: "Preprocessing helper" if path.name == "preview.py" else None,
    )

    result = server.list_scripts(kind="py", query="PREPROCESSING")

    assert result["count"] == 1
    assert [entry["name"] for entry in result["scripts"]] == ["preview.py"]
    assert result["scripts"][0]["source"] == "configured"
    assert [directory["count"] for directory in result["directories"]] == [1, 0]
    assert all(directory["exists"] for directory in result["directories"])
