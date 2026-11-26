"""Tests for automatic framing selection from archived pointing metadata."""

from __future__ import annotations

import json
from pathlib import Path

from siril_mcp.server import (
    _effective_framing,
    _pointing_count,
    _with_register_options,
)


def test_known_single_pointing_defaults_to_min(tmp_path: Path) -> None:
    """The bundled OSC pipeline uses common-overlap framing for one pointing."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"pointing_count": 1}),
        encoding="utf-8",
    )

    assert _pointing_count(tmp_path) == 1
    assert _effective_framing("OSC_Preprocessing.ssf", tmp_path, None) == (
        "min",
        True,
    )


def test_explicit_framing_overrides_single_pointing_default(tmp_path: Path) -> None:
    """An explicit current framing opts out of the automatic min policy."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"pointing_count": 1}),
        encoding="utf-8",
    )

    assert _effective_framing("OSC_Preprocessing.ssf", tmp_path, "current") == (
        "current",
        False,
    )


def test_mosaic_from_manifest_keeps_script_framing(tmp_path: Path) -> None:
    """A multi-pointing manifest never receives automatic min framing."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"pointing_count": 3}),
        encoding="utf-8",
    )

    assert _pointing_count(tmp_path) == 3
    assert _effective_framing("OSC_Preprocessing.ssf", tmp_path, None) == (
        None,
        False,
    )


def test_plan_alone_identifies_a_single_pointing(tmp_path: Path) -> None:
    """A plan file is enough to enable the single-pointing default."""
    (tmp_path / "plan.json").write_text(
        json.dumps({"pointings": [{"label": "center"}]}),
        encoding="utf-8",
    )

    assert _pointing_count(tmp_path) == 1
    assert _effective_framing("OSC_Preprocessing.ssf", tmp_path, None) == (
        "min",
        True,
    )


def test_plan_pointings_guard_against_stale_single_pointing_manifest(
    tmp_path: Path,
) -> None:
    """The larger count wins when manifest and plan metadata disagree."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"pointing_count": 1}),
        encoding="utf-8",
    )
    (tmp_path / "plan.json").write_text(
        json.dumps({"pointings": [{"label": "pane 1"}, {"label": "pane 2"}]}),
        encoding="utf-8",
    )

    assert _pointing_count(tmp_path) == 2
    assert _effective_framing("OSC_Preprocessing.ssf", tmp_path, None) == (
        None,
        False,
    )


def test_missing_pointing_metadata_and_custom_scripts_keep_script_framing(
    tmp_path: Path,
) -> None:
    """Unknown targets and custom scripts do not get an implicit framing."""
    assert _pointing_count(tmp_path) is None
    assert _effective_framing("OSC_Preprocessing.ssf", tmp_path, None) == (
        None,
        False,
    )

    (tmp_path / "manifest.json").write_text(
        json.dumps({"pointing_count": 1}),
        encoding="utf-8",
    )
    assert _effective_framing("custom_pipeline.ssf", tmp_path, None) == (None, False)


def test_auto_min_amends_register_alongside_drop_output_norm(
    tmp_path: Path,
) -> None:
    """The selected min option is inserted after register, alongside other edits."""
    script = tmp_path / "OSC_Preprocessing.ssf"
    script.write_text(
        "register pp_light\n"
        "stack r_pp_light rej 3 3 -norm=addscale -output_norm -32b -out=result\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"pointing_count": 1}),
        encoding="utf-8",
    )
    framing, automatic = _effective_framing(
        "OSC_Preprocessing.ssf",
        tmp_path,
        None,
    )

    amended = _with_register_options(
        str(script),
        framing=framing,
        drop_output_norm=True,
    )

    assert automatic is True
    assert framing == "min"
    assert amended is not None
    try:
        lines = amended.read_text(encoding="utf-8").splitlines()
        assert lines == [
            "register pp_light",
            "seqapplyreg pp_light -framing=min",
            "stack r_pp_light rej 3 3 -norm=addscale -32b -out=result",
        ]
    finally:
        amended.unlink(missing_ok=True)
