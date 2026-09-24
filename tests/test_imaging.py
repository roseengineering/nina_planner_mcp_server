import csv
import os
import tempfile
import unittest
from pathlib import Path

from nina_planner.imaging import melt_imaging_metadata, read_imaging_csv, windows_to_local


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

    def test_returns_rows_with_date_and_frametype(self):
        self._write_images([["LIGHT", "60.0", "LP"]])

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-09-21")
        self.assertEqual(rows[0]["frame_type"], "LIGHT")
        self.assertEqual(rows[0]["duration"], "60.0")
        self.assertEqual(rows[0]["filter_name"], "LP")

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
                ["LIGHT", "60.0", "LP", f"C:/NINA/2026-09-21/LIGHT/{present}"],
                ["LIGHT", "60.0", "LP", "C:/NINA/2026-09-21/LIGHT/missing.fits"],
            ],
        )
        self.addCleanup(os.environ.pop, "NINA_DRIVE_MOUNT", None)
        os.environ["NINA_DRIVE_MOUNT"] = str(mount)

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["duration"], "60.0")
        self.assertTrue(rows[0]["file_path"].startswith(str(mount)))

    def test_row_kept_when_path_absent(self):
        _write_csv(
            self.light_dir / "ImageMetaData.csv",
            ["ImageType", "Duration", "FilterName"],
            [["LIGHT", "60.0", "LP"]],
        )

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["duration"], "60.0")

    def test_no_date_filter(self):
        self._write_images([["LIGHT", "60.0", "LP"]])

        rows = read_imaging_csv(root=self.root, image_type="light")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-09-21")


class WindowsToLocalTest(unittest.TestCase):
    def test_drive_letter_replaced(self):
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A", "/mnt/c"),
            Path("/mnt/c/Users/me/N.I.N.A"),
        )

    def test_default_mount_from_drive(self):
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A"),
            Path("/mnt/c/Users/me/N.I.N.A"),
        )

    def test_env_mount_when_no_arg(self):
        self.addCleanup(os.environ.pop, "NINA_DRIVE_MOUNT", None)
        os.environ["NINA_DRIVE_MOUNT"] = "/mnt/windows"
        self.assertEqual(
            windows_to_local(r"C:\Users\me\N.I.N.A"),
            Path("/mnt/windows/Users/me/N.I.N.A"),
        )

    def test_no_drive_passthrough(self):
        self.assertEqual(
            windows_to_local(r"\share\N.I.N.A", "/mnt"),
            Path(r"\share\N.I.N.A"),
        )


class MeltImagingMetadataTest(unittest.TestCase):
    def _rows(self):
        base = {
            "file_path": "/d/2026-09-21/LIGHT/a.fits",
            "date": "2026-09-21",
            "exposure_start": "2026-09-21 21:16",
            "exposure_start_utc": "2026-09-22T02:16:25Z",
            "duration": "60",
            "filter_name": "Clear",
            "image_type": "LIGHT",
            "frame_type": "LIGHT",
            "source": "image_metadata",
            "binning": "1x1",
            "gain": "0",
            "offset": "0",
            "pier_side": "n/a",
            "focuser_position": "15625",
            "rotator_position": "0",
            "camera_temp": "NaN",
            "camera_target_temp": "NaN",
            "detected_stars": "0",
            "hfr": "0",
            "fwhm": "NaN",
            "eccentricity": "NaN",
            "guiding_rms": "0",
            "guiding_rms_arc_sec": "0",
            "airmass": "1.03",
            "focuser_temp": "4.0",
            "adu_mean": "5000",
            "mount_ra": "312.75",
            "mount_dec": "31.2",
        }
        b = dict(base)
        b.update(
            {
                "file_path": "/d/2026-09-21/LIGHT/b.fits",
                "exposure_number": "1",
                "exposure_start": "2026-09-21 21:17",
                "exposure_start_utc": "2026-09-22T02:17:25Z",
                "airmass": "1.02",
                "focuser_temp": "3.0",
                "adu_mean": "4999",
                "mount_ra": "312.76",
                "mount_dec": "31.21",
            }
        )
        base["exposure_number"] = "0"
        return [base, b]

    def test_constant_columns_move_to_summary(self):
        res = melt_imaging_metadata(self._rows())
        constants = res["summary"]["constants"]
        self.assertEqual(constants["binning"], "1x1")
        self.assertEqual(constants["gain"], 0.0)
        self.assertEqual(constants["offset"], 0.0)
        self.assertEqual(constants["focuser_position"], 15625.0)
        self.assertNotIn("binning", {m["metric"] for m in res["metrics"]})

    def test_unpopulated_columns_listed(self):
        res = melt_imaging_metadata(self._rows())
        unpopulated = res["summary"]["unpopulated"]
        for col in ("camera_temp", "detected_stars", "hfr", "fwhm",
                    "guiding_rms_arc_sec", "pier_side"):
            self.assertIn(col, unpopulated)

    def test_varying_metrics_become_narrow_rows(self):
        res = melt_imaging_metadata(self._rows())
        metrics = res["metrics"]
        self.assertEqual(len(metrics), 10)  # 5 varying metrics x 2 frames
        by_name = {}
        for m in metrics:
            by_name.setdefault(m["metric"], []).append(m["value"])
        self.assertEqual(by_name["pointing.airmass"], [1.03, 1.02])
        self.assertEqual(by_name["focus.focuser_temp"], [4.0, 3.0])
        self.assertEqual(by_name["background.adu_mean"], [5000.0, 4999.0])
        self.assertEqual(by_name["pointing.mount_ra"], [312.75, 312.76])

    def test_frames_listed_once_with_index(self):
        res = melt_imaging_metadata(self._rows())
        self.assertEqual([f["index"] for f in res["frames"]], [0, 1])
        self.assertEqual(res["frames"][0]["file_path"],
                         "/d/2026-09-21/LIGHT/a.fits")
        self.assertEqual(res["frames"][1]["filter_name"], "Clear")
        for m in res["metrics"]:
            self.assertIn(m["frame"], (0, 1))

    def test_summary_counts(self):
        res = melt_imaging_metadata(self._rows())
        self.assertEqual(res["summary"]["count"], 2)
        self.assertEqual(res["summary"]["by_filter"], {"Clear": 2})
        self.assertEqual(res["summary"]["by_date"], {"2026-09-21": 2})

    def test_quality_metric_surfaces_when_populated(self):
        rows = self._rows()
        rows[0]["hfr"] = "2.1"
        res = melt_imaging_metadata(rows)
        hfr_rows = [m for m in res["metrics"] if m["metric"] == "quality.hfr"]
        self.assertEqual(hfr_rows, [{"frame": 0, "metric": "quality.hfr", "value": 2.1}])
        self.assertNotIn("hfr", res["summary"]["unpopulated"])

    def test_empty_rows(self):
        res = melt_imaging_metadata([])
        self.assertEqual(res["summary"]["count"], 0)
        self.assertEqual(res["frames"], [])
        self.assertEqual(res["metrics"], [])


if __name__ == "__main__":
    unittest.main()
