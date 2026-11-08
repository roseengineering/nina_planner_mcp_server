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
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.light_dir = self.root / "2026-09-21" / "LIGHT"
        self.light_dir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_images(self, rows: list[list[str]], header: list[str] | None = None):
        if header is None:
            header = ["ImageType", "Duration", "FilterName"]
        _write_csv(self.light_dir / "ImageMetaData.csv", header, rows)

    def test_missing_root_raises_instead_of_returning_empty(self):
        # A nonexistent root (unmounted drive, wrong NINA_DRIVE_MOUNT, stale
        # profile path) must raise, not masquerade as "no frames yet" — which
        # would make callers re-acquire the whole plan.
        missing = self.root / "does-not-exist"
        with self.assertRaises(FileNotFoundError) as ctx:
            read_imaging_csv(root=missing, image_type="light")
        self.assertIn("imaging root does not exist", str(ctx.exception))

    def test_returns_rows_with_frametype(self):
        self._write_images([["LIGHT", "60.0", "LP"]])

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertNotIn("date", rows[0])
        self.assertEqual(rows[0]["frame_type"], "LIGHT")
        self.assertEqual(rows[0]["duration"], "60.0")
        self.assertEqual(rows[0]["filter_name"], "LP")

    def test_hidden_snapshot_copy_not_read(self):
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
        self._write_images(
            [
                ["LIGHT", "60.0", "L"],
                ["LIGHT", "60.0", "L"],
            ]
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 2)

    def test_missing_file_row_dropped(self):
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

    def test_row_kept_when_path_absent(self):
        _write_csv(
            self.light_dir / "ImageMetaData.csv",
            ["ImageType", "Duration", "FilterName"],
            [["LIGHT", "60.0", "LP"]],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["duration"], "60.0")

    def test_rows_have_no_folder_date(self):
        self._write_images([["LIGHT", "60.0", "LP"]])

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertNotIn("date", rows[0])

    def test_relative_path_resolved_against_csv_sibling(self):
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
    def setUp(self):
        self.enterContext(patch("nina_planner.imaging.sys.platform", "linux"))
        self.enterContext(patch("nina_planner.imaging.Path", PurePosixPath))
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("NINA_DRIVE_MOUNT", None)

    def test_drive_letter_replaced(self):
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A", "/mnt/c"),
            PurePosixPath("/mnt/c/Users/me/N.I.N.A"),
        )

    def test_default_mount_from_drive(self):
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A"),
            PurePosixPath("/mnt/c/Users/me/N.I.N.A"),
        )

    def test_env_mount_when_no_arg(self):
        os.environ["NINA_DRIVE_MOUNT"] = "/mnt/windows"
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A"),
            PurePosixPath("/mnt/windows/Users/me/N.I.N.A"),
        )

    def test_no_drive_passthrough(self):
        self.assertEqual(
            windows_to_local(r"\share\N.I.N.A", "/mnt"),
            PurePosixPath(r"\share\N.I.N.A"),
        )

    def test_windows_ignores_mount_overrides(self):
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
    """Defensive branches in imaging.py that handle malformed input,
    mismatched types, or non-Windows platforms. Each test targets one
    of the missed lines reported by coverage."""

    # ---- windows_to_local (line 19): sys.platform == "win32" passthrough ----

    def test_windows_to_local_passthrough_on_windows(self):
        """When sys.platform is 'win32', windows_to_local returns
        Path(windows_path) directly without any path rewriting."""
        from nina_planner.imaging import windows_to_local

        with patch("nina_planner.imaging.sys.platform", "win32"):
            result = windows_to_local(r"D:\apps\NINA\foo.fits")
        self.assertEqual(str(result), r"D:\apps\NINA\foo.fits")

    # ---- _resolve_image_path (line 46): file_path None or empty ----

    def test_resolve_image_path_returns_none_when_file_path_none(self):
        from nina_planner.imaging import _resolve_image_path

        self.assertIsNone(_resolve_image_path(None))

    def test_resolve_image_path_returns_none_when_file_path_empty(self):
        from nina_planner.imaging import _resolve_image_path

        self.assertIsNone(_resolve_image_path(""))

    # ---- read_imaging_csv (line 80): skip rows where image_type mismatches ----

    def test_read_imaging_csv_skips_rows_with_mismatched_image_type(self):
        """When the CSV contains rows of multiple image types, only rows
        matching the requested image_type are returned."""
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


if __name__ == "__main__":
    unittest.main()
