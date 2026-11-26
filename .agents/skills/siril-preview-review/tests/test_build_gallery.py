"""Tests for raw and explicitly denoised Siril preview galleries."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_gallery  # noqa: E402


class BuildGalleryTests(unittest.TestCase):
    """Exercise gallery modes against temporary archive trees."""

    def setUp(self) -> None:
        """Create an isolated archive and gallery output directory."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "archive"
        self.target = self.root / "observatory" / "target"
        self.target.mkdir(parents=True)
        self.out = Path(self.temp_dir.name) / "gallery"

    def _png(self, path: Path, color: tuple[int, int, int]) -> None:
        Image.new("RGB", (32, 20), color).save(path)

    def _set_mtime(self, path: Path, timestamp: int) -> None:
        os.utime(path, ns=(timestamp, timestamp))

    def _run(self, *extra: str) -> tuple[str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "build_gallery.py",
            "--archive-root",
            str(self.root),
            "--out",
            str(self.out),
            *extra,
        ]
        with (
            patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(build_gallery.main(), 0)
        return stdout.getvalue(), stderr.getvalue()

    def test_raw_mode_keeps_preview_png_behavior(self) -> None:
        """Raw mode links the original preview without changing archive files."""
        raw = self.target / "preview.png"
        self._png(raw, (10, 20, 30))
        raw_bytes = raw.read_bytes()
        stack = self.target / "result_60s.fit"
        stack.write_bytes(b"original stack")

        self._run()

        page = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn(raw.as_uri(), page)
        self.assertIn("thumbs/target.jpg", page)
        self.assertEqual(raw.read_bytes(), raw_bytes)
        self.assertEqual(stack.read_bytes(), b"original stack")

    def test_denoised_mode_includes_each_matching_sidecar_only(self) -> None:
        """Denoised mode includes every rendered source/sidecar pair."""
        self._png(self.target / "preview.png", (10, 20, 30))
        sources = ("result_60s.fit", "result_drizzle_60s.fit")
        denoised_stacks = (
            "result_denoised_60s.fit",
            "result_denoised_drizzle_60s.fit",
        )
        sidecars = ("result_denoised_60s.png", "result_denoised_drizzle_60s.png")
        for source in sources:
            (self.target / source).write_bytes(b"stack")
            self._set_mtime(self.target / source, 1_000_000_000)
        for denoised_stack in denoised_stacks:
            path = self.target / denoised_stack
            path.write_bytes(b"denoised stack")
            self._set_mtime(path, 2_000_000_000)
        for sidecar, color in zip(sidecars, ((30, 40, 50), (60, 70, 80)), strict=True):
            path = self.target / sidecar
            self._png(path, color)
            self._set_mtime(path, 3_000_000_000)

        stdout, stderr = self._run("--denoised", "--json")

        entries = json.loads(stdout)
        self.assertEqual(stderr, "")
        self.assertEqual({entry["stack"] for entry in entries}, set(sources))
        self.assertEqual(
            {entry["full"] for entry in entries},
            {(self.target / sidecar).as_uri() for sidecar in sidecars},
        )
        self.assertTrue(all(entry["thumb"].startswith("thumbs/") for entry in entries))
        self.assertNotIn((self.target / "preview.png").as_uri(), stdout)
        self.assertEqual(len(list((self.out / "thumbs").glob("*.jpg"))), 2)

        _html_stdout, html_stderr = self._run("--denoised")
        page = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html_stderr, "")
        for sidecar in sidecars:
            self.assertIn(f'href="{(self.target / sidecar).as_uri()}"', page)
        self.assertNotIn((self.target / "preview.png").as_uri(), page)

    def test_missing_denoised_sidecar_is_reported_without_raw_fallback(self) -> None:
        """A missing denoised image is reported and never replaced with raw."""
        raw = self.target / "preview.png"
        self._png(raw, (10, 20, 30))
        (self.target / "result_60s.fit").write_bytes(b"stack")
        denoised = self.target / "result_denoised_60s.fit"
        denoised.write_bytes(b"denoised stack")
        self._set_mtime(self.target / "result_60s.fit", 1_000_000_000)
        self._set_mtime(denoised, 2_000_000_000)

        stdout, stderr = self._run("--denoised", "--json")

        self.assertEqual(json.loads(stdout), [])
        self.assertIn("missing denoised preview", stderr)
        self.assertIn("result_denoised_60s.png", stderr)
        self.assertNotIn(raw.as_uri(), stdout)

    def test_stale_denoised_fits_is_reported_without_raw_fallback(self) -> None:
        """An old denoised FITS is not paired with either PNG."""
        raw = self.target / "preview.png"
        self._png(raw, (10, 20, 30))
        source = self.target / "result_60s.fit"
        denoised = self.target / "result_denoised_60s.fit"
        source.write_bytes(b"stack")
        denoised.write_bytes(b"stale denoised stack")
        self._png(self.target / "result_denoised_60s.png", (20, 30, 40))
        self._set_mtime(source, 2_000_000_000)
        self._set_mtime(denoised, 1_000_000_000)
        self._set_mtime(self.target / "result_denoised_60s.png", 3_000_000_000)

        stdout, stderr = self._run("--denoised", "--json")

        self.assertEqual(json.loads(stdout), [])
        self.assertIn("stale denoised FITS", stderr)
        self.assertNotIn(raw.as_uri(), stdout)

    def test_stale_denoised_png_is_reported_without_raw_fallback(self) -> None:
        """A PNG older than its denoised FITS is not included."""
        raw = self.target / "preview.png"
        self._png(raw, (10, 20, 30))
        source = self.target / "result_60s.fit"
        denoised = self.target / "result_denoised_60s.fit"
        source.write_bytes(b"stack")
        denoised.write_bytes(b"denoised stack")
        preview = self.target / "result_denoised_60s.png"
        self._png(preview, (20, 30, 40))
        self._set_mtime(source, 1_000_000_000)
        self._set_mtime(denoised, 3_000_000_000)
        self._set_mtime(preview, 2_000_000_000)

        stdout, stderr = self._run("--denoised", "--json")

        self.assertEqual(json.loads(stdout), [])
        self.assertIn("stale denoised preview", stderr)
        self.assertNotIn(raw.as_uri(), stdout)

    def test_missing_denoised_fits_is_reported_without_raw_fallback(self) -> None:
        """Missing denoised FITS is distinct from a missing rendered PNG."""
        raw = self.target / "preview.png"
        self._png(raw, (10, 20, 30))
        (self.target / "result_60s.fit").write_bytes(b"stack")

        stdout, stderr = self._run("--denoised", "--json")

        self.assertEqual(json.loads(stdout), [])
        self.assertIn("missing denoised FITS", stderr)
        self.assertNotIn(raw.as_uri(), stdout)

    def test_duplicate_source_stems_report_ambiguous_pair(self) -> None:
        """Sources that map to one sidecar are reported as ambiguous."""
        (self.target / "result_60s.fit").write_bytes(b"stack one")
        (self.target / "result_60s.fits").write_bytes(b"stack two")
        self._png(self.target / "result_denoised_60s.png", (10, 20, 30))

        stdout, stderr = self._run("--denoised", "--json")

        self.assertEqual(json.loads(stdout), [])
        self.assertIn("ambiguous denoised preview", stderr)
        self.assertIn("result_60s.fit", stderr)
        self.assertIn("result_60s.fits", stderr)


if __name__ == "__main__":
    unittest.main()
