"""Regression checks for Siril stack output normalization."""

from __future__ import annotations

import statistics
import struct
import subprocess
from pathlib import Path

import pytest

from siril_mcp.server import _find_siril_cli, _with_register_options


def test_drop_output_norm_amends_stack_without_register(tmp_path: Path) -> None:
    """Drop only the final output remapping without requiring a register line."""
    script = tmp_path / "pipeline.ssf"
    script.write_text(
        "requires 1.3.4\n"
        "stack light rej 3 3 -norm=addscale -output_norm -32b -out=result\n",
        encoding="utf-8",
    )

    amended = _with_register_options(str(script), drop_output_norm=True)

    assert amended is not None
    try:
        text = amended.read_text(encoding="utf-8")
        assert "-output_norm" not in text
        assert "-norm=addscale" in text
        assert "-32b" in text
    finally:
        amended.unlink(missing_ok=True)


def test_dropping_output_norm_does_not_hide_missing_register(tmp_path: Path) -> None:
    """Still refuse min_pairs when an unrelated stack edit was possible."""
    script = tmp_path / "pipeline.ssf"
    script.write_text(
        "stack light rej 3 3 -norm=addscale -output_norm -32b -out=result\n",
        encoding="utf-8",
    )

    amended = _with_register_options(
        str(script),
        min_pairs=4,
        drop_output_norm=True,
    )

    assert amended is None


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


def _float_fits_median(path: Path) -> float:
    with path.open("rb") as handle:
        cards: list[bytes] = []
        while True:
            block = handle.read(2880)
            if not block:
                raise AssertionError("FITS header has no END card")
            for start in range(0, len(block), 80):
                card = block[start : start + 80]
                cards.append(card)
                if card[:8].strip() == b"END":
                    data_offset = ((len(cards) * 80 + 2879) // 2880) * 2880
                    break
            else:
                continue
            break

        values: dict[str, int] = {}
        for card in cards:
            if card[:8].strip() == b"END":
                break
            key = card[:8].decode("ascii").strip()
            if key == "BITPIX" or key == "NAXIS" or key.startswith("NAXIS"):
                values[key] = int(card[10:30].decode("ascii").strip())
        if values["BITPIX"] != -32:
            raise AssertionError(
                f"Expected float FITS output, got BITPIX={values['BITPIX']}"
            )
        count = 1
        for axis in range(1, values["NAXIS"] + 1):
            count *= values[f"NAXIS{axis}"]
        handle.seek(data_offset)
        raw = handle.read(count * 4)
    pixels = (item[0] for item in struct.iter_unpack(">f", raw))
    return statistics.median(pixels)


@pytest.mark.skipif(_find_siril_cli() is None, reason="siril-cli is not installed")
def test_synthetic_stack_preserves_known_background_median(tmp_path: Path) -> None:
    """A 0.001 input background stays within 1e-5 after the MCP stack policy."""
    width = height = 128
    count = width * height
    input_medians: list[float] = []
    for frame in range(3):
        pixels = [
            0.001 + (((index * 8191 + frame * 37) % count) - (count - 1) / 2) * 1e-8
            for index in range(count)
        ]
        path = tmp_path / f"synthetic_{frame + 1:05}.fit"
        _write_float_fits(path, pixels, width, height)
        input_medians.append(
            statistics.median(
                struct.unpack(f">{count}f", struct.pack(f">{count}f", *pixels))
            )
        )

    assert all(abs(median - 0.001) <= 1e-7 for median in input_medians)
    siril_cli = _find_siril_cli()
    assert siril_cli is not None

    script = tmp_path / "synthetic.ssf"
    script.write_text(
        "requires 1.3.4\n"
        "convert synthetic -out=converted\n"
        "stack synthetic_ rej 3 3 -norm=addscale -output_norm -32b -out=stacked\n",
        encoding="utf-8",
    )
    amended = _with_register_options(str(script), drop_output_norm=True)
    assert amended is not None
    try:
        assert "-output_norm" not in amended.read_text(encoding="utf-8")
        proc = subprocess.run(
            [siril_cli, "-d", str(tmp_path), "-s", str(amended)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        result_median = _float_fits_median(tmp_path / "stacked.fit")
        assert abs(result_median - 0.001) <= 1e-5
    finally:
        amended.unlink(missing_ok=True)
