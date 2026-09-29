import unittest

from nina_planner.models.camera import CameraDevice


class CameraDeviceValidationTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestCam",
            "description": "test camera",
            "camera_state": "CameraIdle",
        }
        data.update(overrides)
        return data

    def test_camera_state_passes_through(self):
        cam = CameraDevice(**self._base(camera_state="CameraExposing"))
        self.assertEqual(cam.state, "CameraExposing")

    def test_null_camera_state_defaults_to_cameraidle(self):
        # Regression: previously a None camera_state crashed Pydantic
        # because CameraDevice.state is a required str.
        cam = CameraDevice(**self._base(camera_state=None))
        self.assertEqual(cam.state, "CameraIdle")

    def test_missing_camera_state_defaults_to_cameraidle(self):
        cam = CameraDevice(
            **{k: v for k, v in self._base().items() if k != "camera_state"}
        )
        self.assertEqual(cam.state, "CameraIdle")

    def test_empty_string_camera_state_defaults_to_cameraidle(self):
        # The `or "CameraIdle"` defensive pattern also catches "" and 0,
        # which are unlikely from NINA but possible from other producers.
        cam = CameraDevice(**self._base(camera_state=""))
        self.assertEqual(cam.state, "CameraIdle")

    def test_disconnected_does_not_invoke_validator_normalization(self):
        cam = CameraDevice(**self._base(connected=False, camera_state=None))
        # Disconnected devices keep the model default; no validator crash.
        self.assertEqual(cam.state, "Idle")


class CameraDeviceDeriveFieldsTest(unittest.TestCase):
    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestCam",
            "description": "test camera",
        }
        data.update(overrides)
        return data

    def test_bin_x_int_passes_through(self):
        cam = CameraDevice(**self._base(bin_x=2, bin_y=2))
        self.assertEqual(cam.binning.current, (2, 2))

    def test_bin_x_parseable_string_coerced(self):
        # bin_x="2" → int("2")=2; hits the try body but not except.
        cam = CameraDevice(**self._base(bin_x="2", bin_y="3"))
        self.assertEqual(cam.binning.current, (2, 3))

    def test_bin_x_non_parseable_string_falls_back_to_1(self):
        # bin_x="abc" → int("abc") raises ValueError → bin_x=1.
        cam = CameraDevice(**self._base(bin_x="abc", bin_y="xyz"))
        self.assertEqual(cam.binning.current, (1, 1))

    def test_binning_modes_tuple_parsing(self):
        cam = CameraDevice(**self._base(binning_modes=[[1, 1], (2, 2)]))
        self.assertEqual(cam.binning.supported_modes, [(1, 1), (2, 2)])

    def test_binning_modes_string_parsing(self):
        cam = CameraDevice(**self._base(binning_modes=["1x1", "2x2"]))
        self.assertEqual(cam.binning.supported_modes, [(1, 1), (2, 2)])

    def test_binning_modes_string_non_numeric_parts_dropped(self):
        # "axb" → int("a") raises → silently dropped.
        cam = CameraDevice(**self._base(binning_modes=[[1, 1], "axb"]))
        self.assertEqual(cam.binning.supported_modes, [(1, 1)])

    def test_binning_modes_malformed_tuple_silently_dropped(self):
        # [1, 2, 3] has len != 2 → silently dropped.
        cam = CameraDevice(**self._base(binning_modes=[[1, 2, 3], [2, 2]]))
        self.assertEqual(cam.binning.supported_modes, [(2, 2)])

    def test_binning_modes_string_no_x_silently_dropped(self):
        # "abc" has no "x" → elif condition False → silently dropped.
        cam = CameraDevice(**self._base(binning_modes=["abc", [2, 2]]))
        self.assertEqual(cam.binning.supported_modes, [(2, 2)])

    def test_binning_modes_empty_list(self):
        cam = CameraDevice(**self._base(binning_modes=[]))
        self.assertEqual(cam.binning.supported_modes, [])

    def test_readout_modes_list_passes_through(self):
        cam = CameraDevice(**self._base(readout_modes=["Mode 1", "Mode 2"]))
        self.assertEqual(cam.readout.supported_modes, ["Mode 1", "Mode 2"])

    def test_readout_modes_non_list_coerced_to_empty(self):
        # readout_modes=None → raw_readout_modes=[] (line 171).
        cam = CameraDevice(**self._base(readout_modes=None))
        self.assertEqual(cam.readout.supported_modes, [])

    def test_readout_modes_string_coerced_to_empty(self):
        # readout_modes="foo" → not a list → coerced to [].
        cam = CameraDevice(**self._base(readout_modes="foo"))
        self.assertEqual(cam.readout.supported_modes, [])

    def test_readout_current_string_passes_through(self):
        cam = CameraDevice(**self._base(readout_mode="Low Noise"))
        self.assertEqual(cam.readout.current, "Low Noise")

    def test_readout_current_int_coerced_to_string(self):
        cam = CameraDevice(**self._base(readout_mode=1))
        self.assertEqual(cam.readout.current, "1")

    def test_readout_current_none_yields_none(self):
        cam = CameraDevice(**self._base(readout_mode=None))
        self.assertIsNone(cam.readout.current)


class CameraDeviceBinningModesTest(unittest.TestCase):
    """`BinningModes` arrives from ninaAPI as `{Name, X, Y}` objects."""

    def _base(self, **overrides) -> dict:
        data = {
            "connected": True,
            "name": "TestCam",
            "description": "test camera",
        }
        data.update(overrides)
        return data

    def test_nina_object_modes_are_parsed(self):
        cam = CameraDevice(
            **self._base(
                binning_modes=[
                    {"name": "1x1", "x": 1, "y": 1},
                    {"name": "2x2", "x": 2, "y": 2},
                ]
            )
        )
        self.assertEqual(cam.binning.supported_modes, [(1, 1), (2, 2)])

    def test_nina_object_modes_with_string_axes_are_parsed(self):
        cam = CameraDevice(
            **self._base(binning_modes=[{"name": "2x2", "x": "2", "y": "2"}])
        )
        self.assertEqual(cam.binning.supported_modes, [(2, 2)])

    def test_object_mode_without_axes_falls_back_to_name(self):
        cam = CameraDevice(
            **self._base(binning_modes=[{"name": "4x4", "x": None, "y": None}])
        )
        self.assertEqual(cam.binning.supported_modes, [(4, 4)])

    def test_object_mode_with_unparseable_axes_and_name_is_dropped(self):
        cam = CameraDevice(
            **self._base(binning_modes=[{"name": "n/a", "x": "abc", "y": None}])
        )
        self.assertEqual(cam.binning.supported_modes, [])

    def test_object_mode_without_axes_or_name_is_dropped(self):
        cam = CameraDevice(**self._base(binning_modes=[{"x": None, "y": None}]))
        self.assertEqual(cam.binning.supported_modes, [])

    def test_mixed_shapes_are_all_parsed_in_order(self):
        cam = CameraDevice(
            **self._base(
                binning_modes=[
                    {"name": "1x1", "x": 1, "y": 1},
                    [2, 2],
                    "3x3",
                ]
            )
        )
        self.assertEqual(cam.binning.supported_modes, [(1, 1), (2, 2), (3, 3)])

    def test_null_axis_list_is_dropped_not_crashing(self):
        # Regression: `int(None)` used to raise TypeError out of the
        # equipment-status tool for the whole observatory.
        cam = CameraDevice(**self._base(binning_modes=[[None, None], [2, 2]]))
        self.assertEqual(cam.binning.supported_modes, [(2, 2)])

    def test_non_numeric_axis_list_is_dropped_not_crashing(self):
        cam = CameraDevice(**self._base(binning_modes=[["a", "b"], [2, 2]]))
        self.assertEqual(cam.binning.supported_modes, [(2, 2)])

    def test_non_iterable_entries_are_dropped(self):
        cam = CameraDevice(**self._base(binning_modes=[1, None, [2, 2]]))
        self.assertEqual(cam.binning.supported_modes, [(2, 2)])
