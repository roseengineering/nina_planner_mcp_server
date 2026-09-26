import math
import unittest

from nina_planner.models.profile import (
    EquipmentConfig,
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    SiteLocationInfo,
)


class OpticalTrainInfoFovTest(unittest.TestCase):
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
        info = self._build(focal_length_mm=0)
        assert info.field_of_view_deg is None

    def test_missing_focal_length_no_fov(self):
        info = self._build(focal_length_mm=None)
        assert info.field_of_view_deg is None

    def test_zero_pixel_size_no_fov(self):
        info = self._build(pixel_size_microns=0)
        assert info.field_of_view_deg is None

    def test_zero_camera_dimensions_no_fov(self):
        info = self._build(camera_xsize_px=0, camera_ysize_px=0)
        assert info.field_of_view_deg is None


class ObservatoryProfileFilterPositionTest(unittest.TestCase):
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
            ),
        )

    def test_returns_position_for_known_filter(self):
        profile = self._build(
            [
                FilterInfo(name="L", position=2, focus_offset=0),
                FilterInfo(name="R", position=3, focus_offset=10),
            ]
        )
        assert profile.filter_position("L") == 2
        assert profile.filter_position("R") == 3

    def test_missing_filter_raises_value_error_naming_filter_and_profile(self):
        profile = self._build([FilterInfo(name="L", position=2, focus_offset=0)])
        with self.assertRaises(ValueError) as ctx:
            profile.filter_position("X")
        msg = str(ctx.exception)
        assert "X" in msg
        assert "TestProfile" in msg
        assert "L" in msg
