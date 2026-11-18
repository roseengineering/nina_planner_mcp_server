"""Tests for nina_planner.imaging: CSV reads, drive mapping, weather rows."""

import csv
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

from nina_planner.imaging import (
    _resolve_image_path,
    read_imaging_csv,
    windows_to_local,
)


def _write_csv(path: Path, header: list[str], rows: list[list[str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


class ReadImagingCsvTest(unittest.TestCase):
    """read_imaging_csv over a temp imaging root: rows, drops, path bases."""

    def setUp(self):
        """Create a temporary imaging root with one dated LIGHT folder."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.light_dir = self.root / "2026-09-21" / "LIGHT"
        self.light_dir.mkdir(parents=True)

    def tearDown(self):
        """Remove the temporary imaging root."""
        self._tmp.cleanup()

    def _write_images(self, rows: list[list[str]], header: list[str] | None = None):
        if header is None:
            header = ["ImageType", "Duration", "FilterName"]
        _write_csv(self.light_dir / "ImageMetaData.csv", header, rows)

    def test_missing_root_raises_instead_of_returning_empty(self):
        """A nonexistent root raises instead of masquerading as no frames."""
        # A nonexistent root (unmounted drive, wrong NINA_DRIVE_MOUNT, stale
        # profile path) must raise, not masquerade as "no frames yet" — which
        # would make callers re-acquire the whole plan.
        missing = self.root / "does-not-exist"
        with self.assertRaises(FileNotFoundError) as ctx:
            read_imaging_csv(root=missing, image_type="light")
        self.assertIn("imaging root does not exist", str(ctx.exception))

    def test_returns_rows_with_frametype(self):
        """Rows carry frame_type, duration and filter_name, not NINA's date."""
        self._write_images([["LIGHT", "60.0", "LP"]])

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertNotIn("date", rows[0])
        self.assertEqual(rows[0]["frame_type"], "LIGHT")
        self.assertEqual(rows[0]["duration"], "60.0")
        self.assertEqual(rows[0]["filter_name"], "LP")

    def test_hidden_snapshot_copy_not_read(self):
        """A share's .snapshot copy of the CSV never doubles the rows."""
        # A share's .snapshot/.Trashes copy of the CSV must never be read
        # alongside the live one — its rows would double-count the session.
        self._write_images([["LIGHT", "60.0", "LP"]])
        _write_csv(
            self.root / ".snapshot" / "2026-09-21" / "LIGHT" / "ImageMetaData.csv",
            ["ImageType", "Duration", "FilterName"],
            [["LIGHT", "60.0", "LP"]],
        )
        _write_csv(
            self.root / "node_modules" / "2026-09-21" / "LIGHT" / "ImageMetaData.csv",
            ["ImageType", "Duration", "FilterName"],
            [["LIGHT", "60.0", "LP"]],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)  # only the visible CSV was read

    def test_injects_into_all_image_rows(self):
        """All rows of the CSV come back, one entry per image."""
        self._write_images(
            [
                ["LIGHT", "60.0", "L"],
                ["LIGHT", "60.0", "L"],
            ]
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 2)

    def test_missing_file_row_dropped(self):
        """A row whose FITS file is gone is dropped; live rows survive."""
        mount = self.root / "mnt"
        img_dir = mount / "NINA" / "2026-09-21" / "LIGHT"
        img_dir.mkdir(parents=True)
        present = "2026-09-21_00-00-00_Clear__60.00s_0000.fits"
        (img_dir / present).write_text("fits")
        _write_csv(
            img_dir / "ImageMetaData.csv",
            ["ImageType", "Duration", "FilterName", "FilePath"],
            [
                ["LIGHT", "60.0", "LP", str(img_dir / present)],
                ["LIGHT", "60.0", "LP", str(img_dir / "missing.fits")],
            ],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["duration"], "60.0")
        self.assertTrue(rows[0]["file_path"].startswith(str(mount)))

    def test_report_dropped_returns_dropped_rows_for_classification(self):
        """report_dropped returns gone rows with a path for the BAD_ probe."""
        # Default contract unchanged (plain list); report_dropped adds the
        # dropped rows with a best-effort path so a caller can classify WHY
        # the file is gone — e.g. probe for N.I.N.A.'s BAD_ reject rename,
        # which the CSV record itself can no longer reveal (it names the
        # pre-rename file).
        img_dir = self.root / "NINA" / "2026-09-21" / "LIGHT"
        img_dir.mkdir(parents=True)
        present = img_dir / "2026-09-21_00-00-00_L__60.00s_0000.fits"
        present.write_text("fits")
        missing = img_dir / "2026-09-21_00-01-00_L__60.00s_0001.fits"
        (img_dir / f"BAD_{missing.name}").write_text("fits")  # rejected twin
        _write_csv(
            img_dir / "ImageMetaData.csv",
            ["ImageType", "Duration", "FilterName", "FilePath"],
            [
                ["LIGHT", "60.0", "LP", str(present)],
                ["LIGHT", "60.0", "LP", str(missing)],
            ],
        )

        rows, dropped = read_imaging_csv(
            root=self.root, image_type="light", report_dropped=True
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(dropped), 1)
        # The returned path is the raw record's path, usable as-is for the
        # BAD_ probe even though the file itself is gone.
        dropped_path = Path(dropped[0]["file_path"])
        self.assertEqual(dropped_path, missing)
        self.assertTrue(dropped_path.with_name(f"BAD_{dropped_path.name}").is_file())
        self.assertEqual(dropped[0]["image_type"], "LIGHT")
        self.assertEqual(dropped[0]["duration"], "60.0")

        # Default call still returns the plain list callers expect.
        default = read_imaging_csv(root=self.root, image_type="light")
        self.assertIsInstance(default, list)
        self.assertEqual(len(default), 1)

    def test_relative_path_resolved_against_csv_sibling(self):
        """A relative FilePath resolves against the CSV's own folder, not CWD."""
        # Regression: a relative FilePath like "foo.fits" previously checked
        # against process CWD, so it was silently dropped unless the MCP
        # server happened to be launched from the imaging root.
        fits_name = "2026-09-21_00-00-00_Clear__60.00s_0000.fits"
        (self.light_dir / fits_name).write_text("fits")
        self._write_images(
            [["LIGHT", "60.0", "LP", fits_name]],
            header=["ImageType", "Duration", "FilterName", "FilePath"],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file_path"], str(self.light_dir / fits_name))

    def test_relative_path_resolved_against_date_folder(self):
        """Paths like "LIGHT/foo.fits" resolve against the date folder."""
        # NINA sometimes writes paths like "LIGHT/foo.fits" relative to the
        # 2026-09-21/ folder (one tier above the metadata file's parent).
        fits_name = "2026-09-21_00-00-00_Clear__60.00s_0000.fits"
        (self.light_dir / fits_name).write_text("fits")
        self._write_images(
            [["LIGHT", "60.0", "LP", f"LIGHT/{fits_name}"]],
            header=["ImageType", "Duration", "FilterName", "FilePath"],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file_path"], str(self.light_dir / fits_name))

    def test_relative_path_resolved_against_imaging_root(self):
        """Paths like "2026-09-21/LIGHT/foo.fits" resolve against the root."""
        # Or NINA may write paths from the imaging root, e.g.
        # "2026-09-21/LIGHT/foo.fits".
        fits_name = "2026-09-21_00-00-00_Clear__60.00s_0000.fits"
        (self.light_dir / fits_name).write_text("fits")
        relative = f"2026-09-21/LIGHT/{fits_name}"
        self._write_images(
            [["LIGHT", "60.0", "LP", relative]],
            header=["ImageType", "Duration", "FilterName", "FilePath"],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file_path"], str(self.light_dir / fits_name))

    def test_relative_path_with_no_match_dropped(self):
        """A relative path matching no declared base is dropped, not CWD."""
        # Relative path that doesn't resolve under any declared base is
        # dropped — same as the prior behavior of returning None and gating
        # at the caller. CWD is no longer probed.
        self._write_images(
            [["LIGHT", "60.0", "LP", "LIGHT/missing.fits"]],
            header=["ImageType", "Duration", "FilterName", "FilePath"],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(rows, [])


class RealNinaSampleTest(unittest.TestCase):
    """End-to-end fixture tests against a captured NINA ImageMetaData.csv.

    The fixture at tests/fixtures/nina-sample/2026-09-20/LIGHT/ mirrors the
    directory layout NINA writes under the configured imaging save root.
    The CSV contains 60 real exposure rows, all using absolute Windows
    paths in the FilePath column (``C:/Users/<user>/Documents/N.I.N.A/...``).

    A temporary drive mount supplies placeholder images so results never
    depend on the user's real imaging directory or the host platform.
    """

    SAMPLES_DIR = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "nina-sample"
        / "2026-09-20"
        / "LIGHT"
    )

    def test_read_imaging_csv_handles_real_nina_shapes(self):
        """All 60 captured rows map into the temp mount and verify as files."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("nina_planner.imaging.sys.platform", "linux"),
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": tmp}),
        ):
            # Exercise drive mapping using real files on the host filesystem.
            # The captured absolute Windows paths must never reach real images.
            with (self.SAMPLES_DIR / "ImageMetaData.csv").open(
                encoding="utf-8-sig", newline=""
            ) as f:
                samples = list(csv.DictReader(f))
            self.assertEqual(len(samples), 60)
            for sample in samples:
                image = windows_to_local(sample["FilePath"])
                image.parent.mkdir(parents=True, exist_ok=True)
                image.touch()

            rows = read_imaging_csv(
                root=self.SAMPLES_DIR.parent.parent, image_type="light"
            )
            self.assertEqual(len(rows), 60)
            for row in rows:
                image = Path(row["file_path"])
                self.assertTrue(image.is_relative_to(Path(tmp)))
                self.assertEqual(image.suffix, ".fits")
                self.assertTrue(image.is_file())
                image.unlink()

            self.assertEqual(
                read_imaging_csv(
                    root=self.SAMPLES_DIR.parent.parent, image_type="light"
                ),
                [],
            )

    def test_resolver_transforms_real_nina_absolute_path(self):
        """The resolver rewrites a real N.I.N.A. C:/ path onto the mount."""
        # Pure paths model POSIX semantics without requiring a POSIX host.
        with (
            patch("nina_planner.imaging.sys.platform", "linux"),
            patch("nina_planner.imaging.Path", PurePosixPath),
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/mnt/c"}),
        ):
            self.assertEqual(
                _resolve_image_path(
                    "C:/Users/george/Documents/N.I.N.A/2026-09-20/LIGHT/"
                    "2026-09-20_21-59-36_Clear__60.00s_0000.fits"
                ),
                "/mnt/c/Users/george/Documents/N.I.N.A/2026-09-20/LIGHT/"
                "2026-09-20_21-59-36_Clear__60.00s_0000.fits",
            )


class WindowsToLocalTest(unittest.TestCase):
    """windows_to_local drive-mount mapping across platforms and env vars."""

    def setUp(self):
        """Run on a POSIX-like host with no NINA_DRIVE_MOUNT set."""
        self.enterContext(patch("nina_planner.imaging.sys.platform", "linux"))
        self.enterContext(patch("nina_planner.imaging.Path", PurePosixPath))
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("NINA_DRIVE_MOUNT", None)

    def test_drive_letter_replaced(self):
        """An explicit mount replaces the drive letter of the path."""
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A", "/mnt/c"),
            PurePosixPath("/mnt/c/Users/me/N.I.N.A"),
        )

    def test_default_mount_from_drive(self):
        """With no argument, /mnt/<drive> is derived from the drive letter."""
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A"),
            PurePosixPath("/mnt/c/Users/me/N.I.N.A"),
        )

    def test_env_mount_when_no_arg(self):
        """NINA_DRIVE_MOUNT supplies the mount when no argument is given."""
        os.environ["NINA_DRIVE_MOUNT"] = "/mnt/windows"
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A"),
            PurePosixPath("/mnt/windows/Users/me/N.I.N.A"),
        )

    def test_no_drive_passthrough(self):
        """A path with no drive letter passes through unchanged."""
        self.assertEqual(
            windows_to_local(r"\share\N.I.N.A", "/mnt"),
            PurePosixPath(r"\share\N.I.N.A"),
        )

    def test_windows_ignores_mount_overrides(self):
        """On win32 the original Windows path is returned, mount ignored."""
        with (
            patch("nina_planner.imaging.sys.platform", "win32"),
            patch("nina_planner.imaging.Path", PureWindowsPath),
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/mnt/windows"}),
        ):
            for mount in (None, "/mnt/c"):
                with self.subTest(mount=mount):
                    self.assertEqual(
                        windows_to_local(r"C:\Users\me\N.I.N.A", mount),
                        PureWindowsPath(r"C:\Users\me\N.I.N.A"),
                    )


class ImagingDefensivePathsTest(unittest.TestCase):
    """Malformed input and platform branches in imaging.py."""

    def test_resolve_image_path_returns_none_when_file_path_none(self):
        """A None file path resolves to None rather than raising."""
        from nina_planner.imaging import _resolve_image_path

        self.assertIsNone(_resolve_image_path(None))

    def test_resolve_image_path_returns_none_when_file_path_empty(self):
        """An empty file path resolves to None rather than raising."""
        from nina_planner.imaging import _resolve_image_path

        self.assertIsNone(_resolve_image_path(""))

    def test_read_imaging_csv_skips_rows_with_mismatched_image_type(self):
        """Only rows matching the requested image_type come back."""
        from nina_planner.imaging import read_imaging_csv

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            light_dir = tmp_path / "2026-09-21" / "LIGHT"
            light_dir.mkdir(parents=True)

            # CSV has LIGHT, FLAT, and DARK rows mixed.
            with open(light_dir / "ImageMetaData.csv", "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["ImageType", "Duration", "FilterName"])
                writer.writerow(["LIGHT", "60.0", "L"])
                writer.writerow(["FLAT", "5.0", "L"])
                writer.writerow(["DARK", "60.0", ""])
                writer.writerow(["LIGHT", "120.0", "R"])

            rows = read_imaging_csv(root=tmp_path, image_type="light")
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["frame_type"] == "LIGHT" for r in rows))

    def test_read_weather_rows_missing_file_returns_empty_dict(self):
        """A missing WeatherData.csv reads as no weather, not an error."""
        from nina_planner.imaging import read_weather_rows

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_weather_rows(Path(tmp)), {})

    def test_read_weather_rows_returns_snake_cased_dict_by_exposure_number(self):
        """Weather rows come back keyed by exposure number, snake_cased."""
        from nina_planner.imaging import read_weather_rows

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with open(tmp_path / "WeatherData.csv", "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["ExposureNumber", "ExposureStartUTC", "CloudCover", "DewPoint"]
                )
                writer.writerow(["0", "2026-09-20T21:00:00Z", "15", "10.2"])
                writer.writerow(["1", "2026-09-20T21:01:00Z", "25", "10.5"])

            wx = read_weather_rows(tmp_path)
            self.assertEqual(len(wx), 2)
            self.assertIn("0", wx)
            self.assertIn("1", wx)
            self.assertEqual(wx["0"]["cloud_cover"], "15")
            self.assertEqual(wx["0"]["dew_point"], "10.2")
            self.assertEqual(wx["1"]["cloud_cover"], "25")

    def test_read_weather_rows_unreadable_file_returns_empty_dict(self):
        """An undecodable WeatherData.csv degrades to no weather, never raises."""
        from nina_planner.imaging import read_weather_rows

        # A file that exists but cannot be decoded (a truncated/renamed
        # non-UTF-8 file on a share) degrades to "no weather", never raises:
        # callers are archiving frames and must not abort on a sidecar.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "WeatherData.csv").write_bytes(b"\xff\xfe\x00\x80 junk")
            self.assertEqual(read_weather_rows(tmp_path), {})


if __name__ == "__main__":
    unittest.main()
