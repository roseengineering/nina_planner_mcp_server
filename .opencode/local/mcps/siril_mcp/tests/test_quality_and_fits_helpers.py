"""Tests for light-quality summaries and minimal FITS inspection."""

from __future__ import annotations

import struct
from pathlib import Path

from siril_mcp import server


def _fits_card(keyword: str, value: str) -> str:
    return f"{keyword:<8}= {value:>20}".ljust(80)


def _write_float_fits(path: Path, pixels: list[float], width: int, height: int) -> None:
    cards = [
        _fits_card("SIMPLE", "T"),
        _fits_card("BITPIX", "-32"),
        _fits_card("NAXIS", "2"),
        _fits_card("NAXIS1", str(width)),
        _fits_card("NAXIS2", str(height)),
        "END".ljust(80),
    ]
    header = "".join(cards).encode("ascii")
    header += b" " * ((-len(header)) % 2880)
    data = struct.pack(f">{len(pixels)}f", *pixels)
    data += b"\0" * ((-len(data)) % 2880)
    path.write_bytes(header + data)


def test_read_light_quality_aggregates_numeric_fields_and_ignores_empty_values(
    tmp_path: Path,
) -> None:
    """Telemetry medians and registration likelihood use available values."""
    lights = tmp_path / "lights"
    lights.mkdir()
    (lights / "ImageMetaData.csv").write_text(
        "detected_stars,hfr,adu_mean,adu_st_dev,airmass\n"
        "40,2.0,100,8,1.2\n"
        "20,4.0,300,12,2.0\n"
        "NaN,,150,,\n",
        encoding="utf-8",
    )

    quality = server._read_light_quality(tmp_path)

    assert quality == {
        "frames": 3,
        "median_detected_stars": 30.0,
        "min_detected_stars": 20,
        "registration_likely": True,
        "median_hfr": 3.0,
        "median_adu_mean": 150.0,
        "median_adu_st_dev": 10.0,
        "median_airmass": 1.6,
    }


def test_read_light_quality_returns_none_for_missing_or_empty_csv(
    tmp_path: Path,
) -> None:
    """Absent and header-only telemetry do not produce a false summary."""
    assert server._read_light_quality(tmp_path) is None
    lights = tmp_path / "lights"
    lights.mkdir()
    (lights / "ImageMetaData.csv").write_text("detected_stars,hfr\n", encoding="utf-8")
    assert server._read_light_quality(tmp_path) is None


def test_quality_verdict_warnings_and_diagnosis_use_archive_baseline() -> None:
    """Quality helpers distinguish faint, ready, and obstructed targets."""
    assert (
        server._sky_baseline(
            [
                {"quality": {"median_adu_mean": 1000.0, "median_detected_stars": 40}},
                {"quality": {"median_adu_mean": 9000.0, "median_detected_stars": 10}},
            ]
        )
        == 1000.0
    )
    assert server._quality_verdict({}, None) == "unknown"
    assert (
        server._quality_verdict(
            {"median_adu_mean": 3000.0, "median_detected_stars": 40}, 3000.0
        )
        == "obstructed_high_background"
    )
    assert server._quality_verdict({"median_detected_stars": 29}, None) == (
        "faint_or_low_altitude"
    )
    assert server._quality_verdict({"median_detected_stars": 30}, None) == "ready"
    assert server._quality_warnings(None) == [
        "No lights/ImageMetaData.csv; light quality unknown."
    ]

    warnings = server._quality_warnings(
        {
            "median_detected_stars": 12,
            "median_hfr": 4.1,
            "median_airmass": 2.1,
        }
    )
    assert len(warnings) == 3
    assert "Only 12 stars" in warnings[0]
    assert "HFR 4.10" in warnings[1]
    assert "airmass 2.10" in warnings[2]

    diagnosis = server._diagnose(
        "Not enough star pairs in reference image",
        {"median_detected_stars": 42, "frames": 3},
    )
    assert diagnosis is not None
    assert "could not pair enough" in diagnosis["explanation"]
    assert diagnosis["evidence"] == (
        "ImageMetaData.csv reports 42 median detected stars across 3 frames."
    )
    assert server._diagnose("unrecognized error", None) is None


def test_fits_header_shape_empty_fraction_and_crop_ratio(tmp_path: Path) -> None:
    """Small valid FITS fixtures exercise header and pixel-level helpers."""
    source = tmp_path / "source.fit"
    pixels = [1.0] * 64
    for index in (0, 8, 16, 24):
        pixels[index] = 0.0
    _write_float_fits(source, pixels, width=8, height=8)

    parsed = server._fits_header(source)
    assert parsed is not None
    header, data_offset = parsed
    assert server._fits_shape(header) == (8, 8)
    assert data_offset == 2880
    assert server._empty_fraction(source) == 0.5

    cropped = tmp_path / "cropped.fit"
    _write_float_fits(cropped, [1.0] * 32, width=4, height=8)
    assert server._crop_keep_fraction(source, cropped) == 0.5
    assert server._fits_shape({"NAXIS": "2", "NAXIS1": "bad"}) is None


def test_fits_helpers_decline_malformed_or_truncated_files(tmp_path: Path) -> None:
    """Unreadable FITS data returns unknown rather than a crop decision."""
    malformed = tmp_path / "malformed.fit"
    malformed.write_bytes(b"not a FITS file")
    assert server._fits_header(malformed) is None
    assert server._empty_fraction(malformed) is None

    truncated = tmp_path / "truncated.fit"
    _write_float_fits(truncated, [1.0] * 16, width=4, height=4)
    truncated.write_bytes(truncated.read_bytes()[:2880])
    assert server._empty_fraction(truncated) is None
