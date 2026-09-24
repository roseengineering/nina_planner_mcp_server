import csv
import os
import tempfile
import unittest
from pathlib import Path

from nina_planner.imaging import (
    _norm_ts,
    read_imaging_csv,
    read_weather_csv,
    widen_imaging_metadata,
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
        res = widen_imaging_metadata(self._rows())
        constants = res["summary"]["constants"]
        self.assertEqual(constants["binning"], "1x1")
        self.assertEqual(constants["gain"], 0.0)
        self.assertEqual(constants["offset"], 0.0)
        self.assertEqual(constants["focuser_position"], 15625.0)
        self.assertNotIn("binning", res["rows"][0])

    def test_unpopulated_columns_listed(self):
        res = widen_imaging_metadata(self._rows())
        unpopulated = res["summary"]["unpopulated"]
        for col in ("camera_temp", "detected_stars", "hfr", "fwhm",
                    "guiding_rms_arc_sec", "pier_side"):
            self.assertIn(col, unpopulated)

    def test_varying_metrics_become_columns(self):
        res = widen_imaging_metadata(self._rows())
        rows = res["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["pointing.airmass"], 1.03)
        self.assertEqual(rows[1]["pointing.airmass"], 1.02)
        self.assertEqual(rows[0]["focus.focuser_temp"], 4.0)
        self.assertEqual(rows[1]["focus.focuser_temp"], 3.0)
        self.assertEqual(rows[0]["background.adu_mean"], 5000.0)
        self.assertEqual(rows[1]["background.adu_mean"], 4999.0)
        self.assertEqual(rows[0]["pointing.mount_ra"], 312.75)
        self.assertEqual(rows[1]["pointing.mount_ra"], 312.76)
        self.assertEqual(rows[0]["pointing.mount_dec"], 31.2)
        self.assertEqual(rows[1]["pointing.mount_dec"], 31.21)

    def test_rows_listed_once_with_identity(self):
        res = widen_imaging_metadata(self._rows())
        self.assertEqual(len(res["rows"]), 2)
        self.assertEqual(res["rows"][0]["file_path"],
                         "/d/2026-09-21/LIGHT/a.fits")
        self.assertEqual(res["rows"][1]["filter_name"], "Clear")
        self.assertEqual(res["rows"][0]["exposure_number"], "0")
        self.assertEqual(res["rows"][1]["exposure_number"], "1")

    def test_exposure_start_normalized_utc(self):
        res = widen_imaging_metadata(self._rows())
        self.assertEqual(res["rows"][0]["exposure_start"], "2026-09-22T02:16:25Z")
        self.assertEqual(res["rows"][1]["exposure_start"], "2026-09-22T02:17:25Z")
        self.assertNotIn("exposure_start_utc", res["rows"][0])
        self.assertNotIn("exposure_start_utc", res["rows"][1])

    def test_summary_counts(self):
        res = widen_imaging_metadata(self._rows())
        self.assertEqual(res["summary"]["count"], 2)
        self.assertEqual(res["summary"]["by_filter"], {"Clear": 2})
        self.assertEqual(res["summary"]["by_date"], {"2026-09-21": 2})

    def test_quality_metric_surfaces_when_populated(self):
        rows = self._rows()
        rows[0]["hfr"] = "2.1"
        res = widen_imaging_metadata(rows)
        self.assertEqual(res["rows"][0]["quality.hfr"], 2.1)
        self.assertIsNone(res["rows"][1]["quality.hfr"])
        self.assertNotIn("hfr", res["summary"]["unpopulated"])

    def test_empty_rows(self):
        res = widen_imaging_metadata([])
        self.assertEqual(res["summary"]["count"], 0)
        self.assertEqual(res["rows"], [])


class ReadWeatherCsvTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.date_dir = self.root / "2026-09-21"
        self.date_dir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_rows_with_date_and_snake_cased_keys(self):
        _write_csv(
            self.date_dir / "WeatherData.csv",
            [
                "ExposureNumber",
                "ExposureStart",
                "Temperature",
                "Humidity",
                "ExposureStartUTC",
            ],
            [["0", "2026-09-21 21:16", "24.5", "73", "2026-09-22T02:16:25Z"]],
        )

        rows = read_weather_csv(root=self.root)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-09-21")
        self.assertEqual(rows[0]["exposure_number"], "0")
        self.assertEqual(rows[0]["temperature"], "24.5")
        self.assertEqual(rows[0]["humidity"], "73")
        self.assertEqual(rows[0]["exposure_start_utc"], "2026-09-22T02:16:25Z")

    def test_no_weather_data_returns_empty(self):
        self.assertEqual(read_weather_csv(root=self.root), [])


class MeltWeatherTest(unittest.TestCase):
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
            "exposure_number": "0",
            "airmass": "1.03",
        }
        b = dict(base)
        b.update(
            {
                "file_path": "/d/2026-09-21/LIGHT/b.fits",
                "exposure_number": "1",
                "exposure_start": "2026-09-21 21:17",
                "exposure_start_utc": "2026-09-22T02:17:25Z",
                "airmass": "1.02",
            }
        )
        return [base, b]

    def _weather(self):
        return [
            {
                "exposure_start_utc": "2026-09-22T02:16:25Z",
                "exposure_start": "2026-09-21 21:16",
                "temperature": "24.5",
                "humidity": "73",
                "cloud_cover": "0",
                "sky_temperature": "NaN",
            },
            {
                "exposure_start_utc": "2026-09-22T02:17:25Z",
                "exposure_start": "2026-09-21 21:17",
                "temperature": "24.1",
                "humidity": "75",
                "cloud_cover": "0",
                "sky_temperature": "NaN",
            },
        ]

    def test_weather_metrics_merged_by_timestamp(self):
        res = widen_imaging_metadata(self._rows(), weather_rows=self._weather())
        rows = res["rows"]
        self.assertEqual(rows[0]["weather.temperature"], 24.5)
        self.assertEqual(rows[1]["weather.temperature"], 24.1)
        self.assertEqual(rows[0]["weather.humidity"], 73.0)
        self.assertEqual(rows[1]["weather.humidity"], 75.0)

    def test_weather_constant_goes_to_summary(self):
        res = widen_imaging_metadata(self._rows(), weather_rows=self._weather())
        self.assertEqual(res["summary"]["constants"]["weather.cloud_cover"], 0.0)
        self.assertNotIn("weather.cloud_cover", res["rows"][0])

    def test_weather_unpopulated_namespaced(self):
        res = widen_imaging_metadata(self._rows(), weather_rows=self._weather())
        self.assertIn("weather.sky_temperature", res["summary"]["unpopulated"])

    def test_rows_carry_identity_fields(self):
        res = widen_imaging_metadata(self._rows(), weather_rows=self._weather())
        self.assertEqual(len(res["rows"]), 2)
        self.assertEqual(res["rows"][0]["file_path"], "/d/2026-09-21/LIGHT/a.fits")
        self.assertEqual(res["rows"][0]["exposure_start"], "2026-09-22T02:16:25Z")
        self.assertEqual(res["rows"][1]["exposure_start"], "2026-09-22T02:17:25Z")

    def test_unmatched_weather_frame_gets_no_weather(self):
        weather = self._weather()
        weather[0]["exposure_start_utc"] = "2000-01-01T00:00:00Z"
        res = widen_imaging_metadata(self._rows(), weather_rows=weather)
        self.assertIsNone(res["rows"][0]["weather.temperature"])
        self.assertEqual(res["rows"][1]["weather.temperature"], 24.1)
        self.assertEqual(res["rows"][1]["weather.cloud_cover"], 0.0)

    def test_no_weather_rows_leaves_output_unchanged(self):
        res = widen_imaging_metadata(self._rows())
        self.assertNotIn("temperature", res["summary"].get("constants", {}))
        self.assertNotIn("weather.temperature", res["rows"][0])
        self.assertEqual(res["rows"][0]["file_path"], "/d/2026-09-21/LIGHT/a.fits")
        self.assertEqual(res["rows"][0]["exposure_start"], "2026-09-22T02:16:25Z")


class NormTsTest(unittest.TestCase):
    def test_utc_z_preserved(self):
        self.assertEqual(_norm_ts("2026-09-22T02:16:25Z"), "2026-09-22T02:16:25Z")

    def test_plus_zero_offset_to_z(self):
        self.assertEqual(
            _norm_ts("2026-09-22T02:16:25+00:00"), "2026-09-22T02:16:25Z"
        )

    def test_microseconds_stripped(self):
        self.assertEqual(
            _norm_ts("2026-09-22T02:16:25.123456Z"), "2026-09-22T02:16:25Z"
        )

    def test_naive_treated_as_utc(self):
        self.assertEqual(
            _norm_ts("2026-09-22 02:16:25"), "2026-09-22T02:16:25Z"
        )

    def test_naive_without_seconds(self):
        self.assertEqual(_norm_ts("2026-09-22 02:16"), "2026-09-22T02:16:00Z")

    def test_unparseable_falls_back_raw(self):
        self.assertEqual(_norm_ts("n/a"), "n/a")

    def test_none_and_empty(self):
        self.assertIsNone(_norm_ts(None))
        self.assertIsNone(_norm_ts("  "))


if __name__ == "__main__":
    unittest.main()
