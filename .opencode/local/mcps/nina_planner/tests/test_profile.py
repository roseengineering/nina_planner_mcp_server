"""Optical-train FOV, filter lookup and local save-path mapping."""

import math
import os
import unittest
from pathlib import PurePosixPath, PureWindowsPath
from unittest.mock import patch

from nina_planner.models.profile import (
    EquipmentConfig,
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    SiteLocationInfo,
)


class OpticalTrainInfoFovTest(unittest.TestCase):
    """Deriving field_of_view_deg from focal length and sensor size."""

    def _build(self, **overrides) -> OpticalTrainInfo:
        defaults = dict(
            telescope_name="TestScope",
            focal_length_mm=540.0,
            focal_ratio=5.4,
            pixel_size_microns=3.76,
            default_gain=100,
            camera_xsize_px=6224,
            camera_ysize_px=4168,
        )
        defaults.update(overrides)
        return OpticalTrainInfo(**defaults)

    def test_normal_case_computes_fov(self):
        """FOV matches the atan formula over the sensor dimensions."""
        info = self._build()
        assert info.field_of_view_deg is not None
        fov_x, fov_y = info.field_of_view_deg
        sensor_x_mm = 6224 * 3.76 / 1000.0
        sensor_y_mm = 4168 * 3.76 / 1000.0
        expected_x = 2 * math.atan2(sensor_x_mm / 2, 540.0) * 180 / math.pi
        expected_y = 2 * math.atan2(sensor_y_mm / 2, 540.0) * 180 / math.pi
        assert fov_x == round(expected_x, 4)
        assert fov_y == round(expected_y, 4)

    def test_zero_focal_length_does_not_produce_180(self):
        """A zero focal length yields no FOV instead of 180 degrees."""
        info = self._build(focal_length_mm=0)
        assert info.field_of_view_deg is None

    def test_missing_focal_length_no_fov(self):
        """A missing focal length yields no FOV."""
        info = self._build(focal_length_mm=None)
        assert info.field_of_view_deg is None

    def test_zero_pixel_size_no_fov(self):
        """A zero pixel size yields no FOV."""
        info = self._build(pixel_size_microns=0)
        assert info.field_of_view_deg is None

    def test_zero_camera_dimensions_no_fov(self):
        """Zero camera dimensions yield no FOV."""
        info = self._build(camera_xsize_px=0, camera_ysize_px=0)
        assert info.field_of_view_deg is None


class ObservatoryProfileFilterPositionTest(unittest.TestCase):
    """filter_position lookup and the contents of its error message."""

    def _build(self, filters):
        return ObservatoryProfile(
            profile_name="TestProfile",
            profile_id="abc-123",
            site=SiteLocationInfo(),
            optics=OpticalTrainInfo(),
            filters=filters,
            image_save_path="/tmp/save",
            equipment=EquipmentConfig(
                has_mount=True,
                has_camera=True,
                has_focuser=True,
                has_guider=True,
                has_filter_wheel=True,
                has_rotator=False,
                has_dome=False,
                has_weather=False,
                has_safety_monitor=False,
                has_switch=False,
                has_flat_device=False,
            ),
        )

    def test_returns_position_for_known_filter(self):
        """Known filters map to their configured wheel positions."""
        profile = self._build(
            [
                FilterInfo(name="L", position=2, focus_offset=0),
                FilterInfo(name="R", position=3, focus_offset=10),
            ]
        )
        assert profile.filter_position("L") == 2
        assert profile.filter_position("R") == 3

    def test_missing_filter_raises_value_error_naming_filter_and_profile(self):
        """A missing filter error names the filter, profile and known names."""
        profile = self._build([FilterInfo(name="L", position=2, focus_offset=0)])
        with self.assertRaises(ValueError) as ctx:
            profile.filter_position("X")
        msg = str(ctx.exception)
        assert "X" in msg
        assert "TestProfile" in msg
        assert "L" in msg


class ObservatoryProfileLocalSavePathTest(unittest.TestCase):
    """local_image_save_path translation for non-Windows hosts."""

    def setUp(self):
        """Run against an environment with no NINA_DRIVE_MOUNT set."""
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("NINA_DRIVE_MOUNT", None)

    def _build(self, image_save_path: str) -> ObservatoryProfile:
        return ObservatoryProfile(
            profile_name="TestProfile",
            profile_id="abc-123",
            site=SiteLocationInfo(),
            optics=OpticalTrainInfo(),
            filters=[],
            image_save_path=image_save_path,
            equipment=EquipmentConfig(
                has_mount=True,
                has_camera=True,
                has_focuser=True,
                has_guider=True,
                has_filter_wheel=True,
                has_rotator=False,
                has_dome=False,
                has_weather=False,
                has_safety_monitor=False,
                has_switch=False,
                has_flat_device=False,
            ),
        )

    def test_windows_drive_translated_to_local_mount(self):
        """A Windows save path maps to the default /mnt/c mount."""
        with (
            patch("nina_planner.imaging.sys.platform", "linux"),
            patch("nina_planner.imaging.Path", PurePosixPath),
        ):
            profile = self._build(r"C:\Users\me\Documents\N.I.N.A")
            assert profile.local_image_save_path == "/mnt/c/Users/me/Documents/N.I.N.A"

    def test_env_drive_mount_honored(self):
        """NINA_DRIVE_MOUNT overrides the default drive translation."""
        with (
            patch("nina_planner.imaging.sys.platform", "linux"),
            patch("nina_planner.imaging.Path", PurePosixPath),
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/mnt/windows"}),
        ):
            profile = self._build(r"D:\N.I.N.A")
            assert profile.local_image_save_path == "/mnt/windows/N.I.N.A"

    def test_path_without_drive_passes_through(self):
        """A path without a drive letter passes through unchanged."""
        with (
            patch("nina_planner.imaging.sys.platform", "linux"),
            patch("nina_planner.imaging.Path", PurePosixPath),
        ):
            profile = self._build("/tmp/save")
            assert profile.local_image_save_path == "/tmp/save"

    def test_on_windows_the_profile_path_is_used_directly(self):
        """On Windows the profile path is used directly, mount ignored."""
        with (
            patch("nina_planner.imaging.sys.platform", "win32"),
            patch("nina_planner.imaging.Path", PureWindowsPath),
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/ignored"}),
        ):
            profile = self._build(r"C:\Users\me\N.I.N.A")
            assert profile.local_image_save_path == r"C:\Users\me\N.I.N.A"

    def test_serialized_but_not_an_input_field(self):
        """local_image_save_path serializes but is not an input field."""
        profile = self._build(r"C:\N.I.N.A")
        assert "local_image_save_path" in profile.model_dump()
        assert "local_image_save_path" in profile.model_dump_json()
        schema = ObservatoryProfile.model_json_schema()
        assert "local_image_save_path" not in schema["properties"]
        assert "local_image_save_path" not in schema["required"]
