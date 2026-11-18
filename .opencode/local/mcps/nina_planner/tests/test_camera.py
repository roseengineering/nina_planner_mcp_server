import unittest

from nina_planner.models.camera import CameraDevice


def _base(**overrides) -> dict:
    data = {
        "connected": True,
        "name": "TestCam",
        "description": "test camera",
    }
    data.update(overrides)
    return data


class CameraDeviceValidationTest(unittest.TestCase):
    def test_camera_state_passes_through(self):
        cam = CameraDevice(**_base(camera_state="CameraExposing"))
        self.assertEqual(cam.state, "CameraExposing")

    def test_null_camera_state_is_none(self):
        cam = CameraDevice(**_base(camera_state=None))
        self.assertIsNone(cam.state)

    def test_missing_camera_state_is_none(self):
        cam = CameraDevice(**_base())
        self.assertIsNone(cam.state)

    def test_empty_string_camera_state_stays_empty(self):
        cam = CameraDevice(**_base(camera_state=""))
        self.assertEqual(cam.state, "")

    def test_disconnected_does_not_invoke_validator_normalization(self):
        cam = CameraDevice(**_base(connected=False, camera_state=None))
        # Disconnected devices keep the model default None; no validator crash.
        self.assertIsNone(cam.state)


class CameraDeviceDeriveFieldsTest(unittest.TestCase):
    def test_bin_x_int_passes_through(self):
        cam = CameraDevice(**_base(bin_x=2, bin_y=2))
        self.assertEqual(cam.binning.current, (2, 2))

    def test_bin_x_parseable_string_coerced(self):
        cam = CameraDevice(**_base(bin_x="2", bin_y="3"))
        self.assertEqual(cam.binning.current, (2, 3))

    def test_bin_x_non_parseable_string_falls_back_to_1(self):
        cam = CameraDevice(**_base(bin_x="abc", bin_y="xyz"))
        self.assertEqual(cam.binning.current, (1, 1))

    def test_binning_modes_empty_list(self):
        cam = CameraDevice(**_base(binning_modes=[]))
        self.assertEqual(cam.binning.supported_modes, [])

    def test_readout_modes_list_passes_through(self):
        cam = CameraDevice(**_base(readout_modes=["Mode 1", "Mode 2"]))
        self.assertEqual(cam.readout.supported_modes, ["Mode 1", "Mode 2"])

    def test_readout_modes_non_list_coerced_to_empty(self):
        for bad in (None, "foo"):
            with self.subTest(readout_modes=bad):
                cam = CameraDevice(**_base(readout_modes=bad))
                self.assertEqual(cam.readout.supported_modes, [])

    def test_readout_current_string_passes_through(self):
        cam = CameraDevice(**_base(readout_mode="Low Noise"))
        self.assertEqual(cam.readout.current, "Low Noise")

    def test_readout_current_int_coerced_to_string(self):
        cam = CameraDevice(**_base(readout_mode=1))
        self.assertEqual(cam.readout.current, "1")

    def test_readout_current_none_yields_none(self):
        cam = CameraDevice(**_base(readout_mode=None))
        self.assertIsNone(cam.readout.current)


class CameraDeviceBinningModesTest(unittest.TestCase):
    """`BinningModes` arrives from ninaAPI as `{Name, X, Y}` objects."""

    def test_nina_object_modes_are_parsed(self):
        cam = CameraDevice(
            **_base(
                binning_modes=[
                    {"name": "1x1", "x": 1, "y": 1},
                    {"name": "2x2", "x": 2, "y": 2},
                ]
            )
        )
        self.assertEqual(cam.binning.supported_modes, [(1, 1), (2, 2)])

    def test_nina_object_modes_with_string_axes_are_parsed(self):
        cam = CameraDevice(**_base(binning_modes=[{"name": "2x2", "x": "2", "y": "2"}]))
        self.assertEqual(cam.binning.supported_modes, [(2, 2)])

    def test_object_modes_with_bad_axes_are_dropped(self):
        # Missing/unparseable axes drop the mode; there is no name fallback.
        for mode in (
            {"name": "4x4", "x": None, "y": None},
            {"name": "n/a", "x": "abc", "y": None},
            {"x": None, "y": None},
        ):
            with self.subTest(mode=mode):
                cam = CameraDevice(**_base(binning_modes=[mode]))
                self.assertEqual(cam.binning.supported_modes, [])

    def test_non_dict_entries_are_dropped(self):
        cam = CameraDevice(
            **_base(binning_modes=[[2, 2], "3x3", 1, None, {"x": 4, "y": 4}])
        )
        self.assertEqual(cam.binning.supported_modes, [(4, 4)])


class CameraDeviceDewHeaterTest(unittest.TestCase):
    """`HasDewHeater` (capability) and `DewHeaterOn` (state) are distinct."""

    def test_heater_present_but_off_is_not_reported_as_on(self):
        cam = CameraDevice(**_base(has_dew_heater=True, dew_heater_on=False))
        self.assertTrue(cam.thermal.has_dew_heater)
        self.assertFalse(cam.thermal.dew_heater_on)

    def test_heater_present_and_on(self):
        cam = CameraDevice(**_base(has_dew_heater=True, dew_heater_on=True))
        self.assertTrue(cam.thermal.has_dew_heater)
        self.assertTrue(cam.thermal.dew_heater_on)

    def test_heater_absent_but_flag_on(self):
        cam = CameraDevice(**_base(has_dew_heater=False, dew_heater_on=True))
        self.assertFalse(cam.thermal.has_dew_heater)
        self.assertTrue(cam.thermal.dew_heater_on)

    def test_heater_keys_absent_default_to_false(self):
        cam = CameraDevice(**_base())
        self.assertFalse(cam.thermal.has_dew_heater)
        self.assertFalse(cam.thermal.dew_heater_on)

    def test_disconnected_camera_keeps_defaults(self):
        cam = CameraDevice(connected=False, name="TestCam")
        self.assertFalse(cam.thermal.has_dew_heater)
        self.assertFalse(cam.thermal.dew_heater_on)
