import unittest

from pydantic import ValidationError

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
        cam = CameraDevice(**{k: v for k, v in self._base().items() if k != "camera_state"})
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
