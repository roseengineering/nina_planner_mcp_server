import unittest

from nina_planner.models.camera import CameraDevice
from nina_planner.models.flat_device import FlatDevice
from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.models.profile import (
    EquipmentConfig,
    FilterInfo,
    ObservatoryProfile,
    OpticalTrainInfo,
    SiteLocationInfo,
)
from nina_planner.sequence import (
    DARK_VARIANTS,
    dark_variant_reasons,
    validate_dark_variant,
)


def _profile(
    filters=("Dark", "L"),
    has_filter_wheel=True,
    has_flat_device=False,
    name="Backyard",
):
    """A profile whose equipment flags and filter list are chosen per test."""
    return ObservatoryProfile(
        profile_name=name,
        profile_id="profile-test123",
        site=SiteLocationInfo(),
        optics=OpticalTrainInfo(),
        filters=[FilterInfo(name=n, position=i) for i, n in enumerate(filters)],
        image_save_path="C:\\images",
        equipment=EquipmentConfig(
            has_mount=True,
            has_camera=True,
            has_focuser=True,
            has_filter_wheel=has_filter_wheel,
            has_flat_device=has_flat_device,
            has_guider=True,
            has_rotator=True,
            has_dome=True,
            has_weather=True,
            has_safety_monitor=True,
            has_switch=True,
        ),
    )


def _camera(has_shutter=True):
    return CameraDevice(connected=True, name="cam", has_shutter=has_shutter)


def _cover(connected=True, supports_open_close=True, cover_state="Closed"):
    return FlatDevice(
        connected=connected,
        cover_state=cover_state,
        supports_open_close=supports_open_close,
    )


def _rig(*, camera=True, shutter=True, cover=True):
    """Equipment for a rig that supports every variant, unless told otherwise."""
    return ObservatoryEquipment(
        camera=_camera(has_shutter=shutter) if camera else None,
        flat_device=_cover() if cover else None,
    )


class DarkVariantReasonsTest(unittest.TestCase):
    def test_a_capable_rig_reports_no_reasons(self):
        reasons = dark_variant_reasons(_rig(), _profile(has_flat_device=True))
        self.assertEqual(reasons, {})

    def test_wheel_needs_a_dark_slot_and_names_the_filters(self):
        reasons = dark_variant_reasons(
            _rig(), _profile(filters=("L", "LP"), has_flat_device=True)
        )
        self.assertIn("wheel", reasons)
        self.assertIn("'Dark' filter slot", reasons["wheel"])
        self.assertIn("L, LP", reasons["wheel"])
        self.assertNotIn("cover", reasons)
        self.assertNotIn("shutter", reasons)

    def test_wheel_needs_a_configured_filter_wheel(self):
        reasons = dark_variant_reasons(
            _rig(), _profile(has_filter_wheel=False, has_flat_device=True)
        )
        self.assertIn("wheel", reasons)
        self.assertIn("no filter wheel", reasons["wheel"])

    def test_cover_needs_a_configured_flat_device(self):
        reasons = dark_variant_reasons(_rig(), _profile())
        self.assertIn("cover", reasons)
        self.assertIn("no flat device configured", reasons["cover"])

    def test_cover_rejects_a_profile_without_a_payload(self):
        """Profile says a flat device exists but NINA reports none: unread
        capability, so fail closed rather than emit CloseCover anyway."""
        reasons = dark_variant_reasons(
            _rig(cover=False), _profile(has_flat_device=True)
        )
        self.assertIn("cover", reasons)
        self.assertIn("reports none", reasons["cover"])

    def test_cover_rejects_an_unsupported_or_unusable_panel(self):
        cases = [
            (_cover(supports_open_close=False), "SupportsOpenClose=false"),
            (_cover(cover_state="NotPresent"), "NotPresent"),
            (_cover(cover_state="Error"), "Error"),
            (_cover(connected=False), "not connected"),
        ]
        for flat, expected in cases:
            with self.subTest(flat=flat):
                reasons = dark_variant_reasons(
                    ObservatoryEquipment(camera=_camera(), flat_device=flat),
                    _profile(has_flat_device=True),
                )
                self.assertIn("cover", reasons)
                self.assertIn(expected, reasons["cover"])

    def test_cover_tolerates_a_panel_that_is_open_or_unknown(self):
        """Open/Unknown/Closed are all states a working cover passes through;
        only NotPresent and Error mean no usable cover."""
        for state in ("Open", "Unknown", "Closed", "NeitherOpenNorClosed"):
            with self.subTest(state=state):
                reasons = dark_variant_reasons(
                    ObservatoryEquipment(
                        camera=_camera(), flat_device=_cover(cover_state=state)
                    ),
                    _profile(has_flat_device=True),
                )
                self.assertNotIn("cover", reasons)

    def test_shutter_needs_the_camera_to_report_one(self):
        cases = [
            (_camera(has_shutter=False), "HasShutter=false"),
            (_camera(has_shutter=None), "does not report HasShutter"),
        ]
        for camera, expected in cases:
            with self.subTest(camera=camera):
                reasons = dark_variant_reasons(
                    ObservatoryEquipment(camera=camera), _profile()
                )
                self.assertIn("shutter", reasons)
                self.assertIn(expected, reasons["shutter"])

    def test_shutter_rejects_a_missing_camera(self):
        reasons = dark_variant_reasons(ObservatoryEquipment(), _profile())
        self.assertIn("shutter", reasons)
        self.assertIn("no camera", reasons["shutter"])


class ValidateDarkVariantTest(unittest.TestCase):
    def test_every_variant_loads_on_a_capable_rig(self):
        equipment = _rig()
        profile = _profile(has_flat_device=True)
        for variant in DARK_VARIANTS:
            with self.subTest(variant=variant):
                validate_dark_variant(variant, equipment, profile)

    def test_the_default_is_the_wheel_variant(self):
        """`wheel` leads the vocabulary, which is what load_sequence falls
        back to when a request omits `variant`."""
        self.assertEqual(DARK_VARIANTS[0], "wheel")

    def test_a_failed_check_names_the_reason_and_supported_variants(self):
        """Configured in the profile but absent from N.I.N.A.'s payload is
        the unread case: reject it and say what would have worked instead."""
        with self.assertRaises(ValueError) as ctx:
            validate_dark_variant(
                "cover", _rig(cover=False), _profile(has_flat_device=True)
            )
        message = str(ctx.exception)
        self.assertIn("'cover'", message)
        self.assertIn("reports none", message)
        self.assertIn("Supported here: wheel, shutter", message)

    def test_a_rig_with_nothing_usable_says_so(self):
        with self.assertRaises(ValueError) as ctx:
            validate_dark_variant(
                "wheel", _rig(camera=False, cover=False), _profile(filters=())
            )
        message = str(ctx.exception)
        self.assertIn("Supported here: none", message)

    def test_an_unknown_variant_is_still_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_dark_variant("lens", _rig(), _profile())
        message = str(ctx.exception)
        self.assertIn("lens", message)
        self.assertIn("wheel", message)
        self.assertIn("cover", message)
        self.assertIn("shutter", message)


if __name__ == "__main__":
    unittest.main()
