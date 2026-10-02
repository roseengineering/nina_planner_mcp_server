import csv
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

from nina_planner.imaging import (
    _ascom_none,
    _clean_metric,
    _norm_ts,
    _resolve_image_path,
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
                ["LIGHT", "60.0", "LP", str(img_dir / present)],
                ["LIGHT", "60.0", "LP", str(img_dir / "missing.fits")],
            ],
        )

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
        for col in (
            "camera_temp",
            "detected_stars",
            "hfr",
            "fwhm",
            "guiding_rms_arc_sec",
            "pier_side",
        ):
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
        self.assertEqual(res["rows"][0]["file_path"], "/d/2026-09-21/LIGHT/a.fits")
        self.assertEqual(res["rows"][1]["filter_name"], "Clear")
        self.assertEqual(res["rows"][0]["exposure_number"], "0")
        self.assertEqual(res["rows"][1]["exposure_number"], "1")

    def test_exposure_start_normalized_utc(self):
        res = widen_imaging_metadata(self._rows())
        self.assertEqual(res["rows"][0]["exposure_start"], "2026-09-22T02:16:25.000Z")
        self.assertEqual(res["rows"][1]["exposure_start"], "2026-09-22T02:17:25.000Z")
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
                "exposure_number": "0",
                "exposure_start_utc": "2026-09-22T02:16:25Z",
                "exposure_start": "2026-09-21 21:16",
                "temperature": "24.5",
                "humidity": "73",
                "cloud_cover": "0",
                "sky_temperature": "NaN",
            },
            {
                "exposure_number": "1",
                "exposure_start_utc": "2026-09-22T02:17:25Z",
                "exposure_start": "2026-09-21 21:17",
                "temperature": "24.1",
                "humidity": "75",
                "cloud_cover": "0",
                "sky_temperature": "NaN",
            },
        ]

    def test_weather_metrics_merged_by_exposure_identity(self):
        res = widen_imaging_metadata(self._rows(), weather_rows=self._weather())
        rows = res["rows"]
        self.assertEqual(rows[0]["weather.temperature"], 24.5)
        self.assertEqual(rows[1]["weather.temperature"], 24.1)
        self.assertEqual(rows[0]["weather.humidity"], 73.0)
        self.assertEqual(rows[1]["weather.humidity"], 75.0)

    def test_weather_join_uses_exposure_number_when_timestamps_match(self):
        rows = self._rows()
        rows[1]["exposure_start_utc"] = rows[0]["exposure_start_utc"]
        weather = self._weather()
        weather[1]["exposure_start_utc"] = weather[0]["exposure_start_utc"]

        res = widen_imaging_metadata(rows, weather_rows=weather)

        self.assertEqual(res["rows"][0]["weather.temperature"], 24.5)
        self.assertEqual(res["rows"][1]["weather.temperature"], 24.1)

    def test_weather_join_normalizes_both_timestamps_to_milliseconds(self):
        rows = self._rows()[:1]
        rows[0]["exposure_start_utc"] = "2026-09-22T02:16:25.123456Z"
        weather = [dict(self._weather()[0])]
        weather[0]["exposure_start_utc"] = "2026-09-22T02:16:25.123999+00:00"

        res = widen_imaging_metadata(rows, weather_rows=weather)

        self.assertEqual(res["rows"][0]["exposure_start"], "2026-09-22T02:16:25.123Z")
        self.assertEqual(res["summary"]["constants"]["weather.temperature"], 24.5)

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
        self.assertEqual(res["rows"][0]["exposure_start"], "2026-09-22T02:16:25.000Z")
        self.assertEqual(res["rows"][1]["exposure_start"], "2026-09-22T02:17:25.000Z")

    def test_unmatched_weather_frame_gets_no_weather(self):
        weather = self._weather()
        weather[0]["exposure_start_utc"] = "2000-01-01T00:00:00Z"
        res = widen_imaging_metadata(self._rows(), weather_rows=weather)
        self.assertIsNone(res["rows"][0]["weather.temperature"])
        self.assertEqual(res["rows"][1]["weather.temperature"], 24.1)
        self.assertEqual(res["rows"][1]["weather.cloud_cover"], 0.0)

    def test_conflicting_duplicate_weather_identity_is_not_joined(self):
        weather = self._weather()
        weather.append(dict(weather[0], temperature="99"))

        res = widen_imaging_metadata(self._rows(), weather_rows=weather)

        self.assertIsNone(res["rows"][0]["weather.temperature"])
        self.assertEqual(res["rows"][1]["weather.temperature"], 24.1)

    def test_no_weather_rows_leaves_output_unchanged(self):
        res = widen_imaging_metadata(self._rows())
        self.assertNotIn("temperature", res["summary"].get("constants", {}))
        self.assertNotIn("weather.temperature", res["rows"][0])
        self.assertEqual(res["rows"][0]["file_path"], "/d/2026-09-21/LIGHT/a.fits")
        self.assertEqual(res["rows"][0]["exposure_start"], "2026-09-22T02:16:25.000Z")


class NormTsTest(unittest.TestCase):
    def test_utc_z_preserved(self):
        self.assertEqual(_norm_ts("2026-09-22T02:16:25Z"), "2026-09-22T02:16:25.000Z")

    def test_plus_zero_offset_to_z(self):
        self.assertEqual(
            _norm_ts("2026-09-22T02:16:25+00:00"), "2026-09-22T02:16:25.000Z"
        )

    def test_microseconds_truncated_to_milliseconds(self):
        self.assertEqual(
            _norm_ts("2026-09-22T02:16:25.123456Z"), "2026-09-22T02:16:25.123Z"
        )

    def test_naive_treated_as_utc(self):
        self.assertEqual(_norm_ts("2026-09-22 02:16:25"), "2026-09-22T02:16:25.000Z")

    def test_naive_without_seconds(self):
        self.assertEqual(_norm_ts("2026-09-22 02:16"), "2026-09-22T02:16:00.000Z")

    def test_unparseable_falls_back_raw(self):
        self.assertEqual(_norm_ts("n/a"), "n/a")

    def test_none_and_empty(self):
        self.assertIsNone(_norm_ts(None))
        self.assertIsNone(_norm_ts("  "))


class AscomNoneTest(unittest.TestCase):
    """Tests for the ASCOM sentinel normalizer used in imaging metadata.

    Only universal sentinels (None, empty/whitespace, "nan", "n/a", NaN) are
    normalized to None. ``-1`` is treated as a real value since errors arrive
    as NaN in practice.
    """

    def test_none_passes_through(self):
        self.assertIsNone(_ascom_none(None))

    def test_string_minus_one_passes_through(self):
        self.assertEqual(_ascom_none("-1"), "-1")

    def test_string_minus_one_with_whitespace_passes_through(self):
        self.assertEqual(_ascom_none(" -1 "), " -1 ")

    def test_string_nan_is_sentinel(self):
        self.assertIsNone(_ascom_none("nan"))

    def test_string_NaN_is_sentinel(self):
        self.assertIsNone(_ascom_none("NaN"))

    def test_string_nan_with_whitespace_is_sentinel(self):
        self.assertIsNone(_ascom_none(" NaN "))

    def test_string_n_a_slash_a_is_sentinel(self):
        self.assertIsNone(_ascom_none("n/a"))

    def test_string_empty_is_sentinel(self):
        self.assertIsNone(_ascom_none(""))

    def test_string_whitespace_only_is_sentinel(self):
        self.assertIsNone(_ascom_none("   "))

    def test_string_negative_passes_through(self):
        self.assertEqual(_ascom_none("-1.5"), "-1.5")
        self.assertEqual(_ascom_none("-10"), "-10")
        self.assertEqual(_ascom_none("+1"), "+1")

    def test_string_positive_passes_through(self):
        self.assertEqual(_ascom_none("42"), "42")
        self.assertEqual(_ascom_none("1.5"), "1.5")

    def test_float_minus_one_passes_through(self):
        self.assertEqual(_ascom_none(-1.0), -1.0)

    def test_float_nan_is_sentinel(self):
        self.assertIsNone(_ascom_none(float("nan")))

    def test_int_minus_one_passes_through(self):
        self.assertEqual(_ascom_none(-1), -1)

    def test_float_value_passes_through(self):
        self.assertEqual(_ascom_none(1.5), 1.5)
        self.assertEqual(_ascom_none(-10.0), -10.0)

    def test_clean_metric_minus_one_string_passes_through(self):
        self.assertEqual(_clean_metric("hfr", "-1"), -1.0)

    def test_clean_metric_minus_one_string_with_whitespace_passes_through(self):
        self.assertEqual(_clean_metric("hfr", " -1 "), -1.0)

    def test_clean_metric_minus_one_int_passes_through(self):
        self.assertEqual(_clean_metric("hfr", -1), -1.0)

    def test_clean_metric_real_value_preserved(self):
        self.assertEqual(_clean_metric("hfr", "1.5"), 1.5)

    def test_clean_metric_minus_one_temperature_passes_through(self):
        # Weather temperatures flow through widen_imaging_metadata; -1°C is
        # a legitimate value, not a sentinel.
        self.assertEqual(_clean_metric("temperature", "-1"), -1.0)
        self.assertEqual(_clean_metric("dew_point", "-1"), -1.0)


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

    # ---- _find_date_ancestor (line 38): no parent matches date regex ----

    def test_find_date_ancestor_returns_none_when_no_parent_matches(self):
        from nina_planner.imaging import _find_date_ancestor

        # Path with parents that don't match YYYY-MM-DD.
        path = Path("/tmp/random/folder/file.fits")
        self.assertIsNone(_find_date_ancestor(path))

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

    # ---- _counts (line 304): skip None values ----

    def test_counts_skips_none_values(self):
        from nina_planner.imaging import _counts

        rows = [
            {"filter": "L"},
            {"filter": None},
            {"filter": "R"},
            {"filter": "L"},
            {},
        ]
        result = _counts(rows, "filter")
        self.assertEqual(result, {"L": 2, "R": 1})

    # ---- widen_imaging_metadata (line 340): skip weather rows with bad ts ----

    def test_widen_imaging_metadata_skips_weather_with_unparseable_ts(self):
        """Weather rows whose exposure_start_utc can't be normalized (None
        or empty string) are skipped — they don't get merged into any
        frame. The good weather row still matches; the bad one is
        silently dropped.

        Note: ``_norm_ts`` falls back to the raw text for unparseable but
        non-empty strings (preserving join-key stability), so only None or
        empty strings trigger the skip — not e.g. ``"garbage"``.
        """
        from nina_planner.imaging import widen_imaging_metadata

        rows = [
            {
                "image_type": "LIGHT",
                "duration": "60.0",
                "filter_name": "L",
                "exposure_number": "0",
                "exposure_start_utc": "2026-09-21T02:16:25Z",
            },
        ]
        weather_rows = [
            {"exposure_start_utc": None},  # None → _norm_ts returns None → skip
            {"exposure_start_utc": ""},  # empty → skip
            {
                "exposure_number": "0",
                "exposure_start_utc": "2026-09-21T02:16:25Z",
                "temperature": 15.0,
            },
        ]
        # Should not raise — the unparseable rows are silently dropped.
        result = widen_imaging_metadata(rows, weather_rows=weather_rows)
        self.assertEqual(len(result["rows"]), 1)
        # The matched weather row's temperature is constant across the single
        # frame, so it's recorded in summary.constants with the namespaced
        # key (weather.temperature) rather than per-row.
        self.assertEqual(
            result["summary"]["constants"].get("weather.temperature"),
            15.0,
        )


if __name__ == "__main__":
    unittest.main()
