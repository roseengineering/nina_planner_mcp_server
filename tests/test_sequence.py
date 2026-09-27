import unittest
from dataclasses import dataclass

from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.models.plan import ObservationPlan
from nina_planner.sequence import build_sequence_lights


def _plan(**overrides):
    data = {
        "plan_id": "plan-test123",
        "target": "Test Target",
        "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
        "light": [
            {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
        ],
        "flat": [{"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 5}],
        "dark": [{"exposure_time_seconds": 60.0, "total_count": 5}],
        "bias": [{"total_count": 5}],
    }
    data.update(overrides)
    return ObservationPlan(**data)


class BuildSequenceLightsTest(unittest.TestCase):
    def test_pointing_with_no_position_angle_does_not_raise_name_error(self):
        plan = _plan(
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0, "position_angle_deg": None}]
        )
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        self.assertIsInstance(result, dict)

    def test_pointing_with_position_angle_does_not_raise_name_error(self):
        plan = _plan(
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0, "position_angle_deg": 90.0}]
        )
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        self.assertIsInstance(result, dict)

    def test_pointing_without_position_angle_field_does_not_raise_name_error(self):
        plan = _plan(pointings=[{"ra_hours": 5.0, "dec_deg": 10.0}])
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        self.assertIsInstance(result, dict)


@dataclass
class _Exp:
    filter_name: str
    exposure_time_seconds: float
    total_count: int


class RoundRobinBatchingTest(unittest.TestCase):
    """Covers lines 121-126 in sequence.py: the _round_robin algorithm."""

    def test_unbatched_emits_full_sequence(self):
        """batch_size=0 → unbatched: emits the full count for each filter."""
        from nina_planner.sequence import _round_robin

        exps = [_Exp("L", 60.0, 5), _Exp("R", 60.0, 3)]
        result = _round_robin(exps, batch_size=0)
        self.assertEqual(result, [(5, 60.0, "L"), (3, 60.0, "R")])

    def test_single_filter_special_cases_to_full_count(self):
        """len(exposures) == 1 → emits the full count regardless of batch_size
        (single-filter batched is meaningless)."""
        from nina_planner.sequence import _round_robin

        exps = [_Exp("L", 60.0, 5)]
        result = _round_robin(exps, batch_size=2)
        self.assertEqual(result, [(5, 60.0, "L")])

    def test_multi_filter_batched_split_entries(self):
        """batch_size < total_count: emits multiple batched entries per filter."""
        from nina_planner.sequence import _round_robin

        exps = [_Exp("L", 60.0, 10), _Exp("R", 60.0, 10)]
        result = _round_robin(exps, batch_size=3)
        self.assertEqual(
            result,
            [
                (3, 60.0, "L"),
                (3, 60.0, "R"),
                (3, 60.0, "L"),
                (3, 60.0, "R"),
                (3, 60.0, "L"),
                (3, 60.0, "R"),
                (1, 60.0, "L"),
                (1, 60.0, "R"),
            ],
        )

    def test_reverse_flag_reverses_output(self):
        from nina_planner.sequence import _round_robin

        exps = [_Exp("L", 60.0, 4), _Exp("R", 60.0, 4)]
        forward = _round_robin(exps, batch_size=2, reverse=False)
        backward = _round_robin(exps, batch_size=2, reverse=True)
        self.assertEqual(backward, list(reversed(forward)))


class ContainerDefaultsTest(unittest.TestCase):
    """Covers the `if X is None: X = []` defensive patterns in container
    helpers (lines 145, 169, 247, etc.).
    """

    def _assert_empty_values_list(self, container_func, field_name):
        """When the field is None, the resulting dict contains a _values
        wrapper with $values=[]."""
        result = container_func()
        self.assertEqual(result[field_name]["$values"], [])

    def test_container_base_default_empty_conditions(self):
        from nina_planner.sequence import _container_base

        self._assert_empty_values_list(
            lambda: _container_base("Test.Container, Test"), "Conditions"
        )

    def test_container_base_default_empty_items(self):
        """_container_base uses key 'Items' (not 'Instructions') for the
        instructions list."""
        from nina_planner.sequence import _container_base

        self._assert_empty_values_list(
            lambda: _container_base("Test.Container, Test"), "Items"
        )

    def test_container_base_default_empty_triggers(self):
        from nina_planner.sequence import _container_base

        self._assert_empty_values_list(
            lambda: _container_base("Test.Container, Test"), "Triggers"
        )

    def test_container_base_explicit_values_preserved(self):
        from nina_planner.sequence import _container_base

        sentinel = {"$ref": "42"}
        result = _container_base(
            "Test.Container, Test",
            conditions=[sentinel],
            triggers=[sentinel],
            instructions=[sentinel],
        )
        self.assertEqual(result["Conditions"]["$values"], [sentinel])
        self.assertEqual(result["Triggers"]["$values"], [sentinel])
        self.assertEqual(result["Items"]["$values"], [sentinel])

    def test_container_root_default_items(self):
        """container_root applies _fix_provider; Items is a list under $values."""
        from nina_planner.sequence import container_root

        result = container_root()
        self.assertEqual(result["Items"]["$values"], [])

    def test_container_start_default_items(self):
        from nina_planner.sequence import container_start

        result = container_start()
        self.assertEqual(result["Items"]["$values"], [])

    def test_container_target_default_items(self):
        from nina_planner.sequence import container_target

        result = container_target()
        self.assertEqual(result["Items"]["$values"], [])

    def test_container_end_default_items(self):
        from nina_planner.sequence import container_end

        result = container_end()
        self.assertEqual(result["Items"]["$values"], [])

    def test_container_sequential_default_items(self):
        from nina_planner.sequence import container_sequential

        result = container_sequential()
        self.assertEqual(result["Conditions"]["$values"], [])
        self.assertEqual(result["Triggers"]["$values"], [])
        self.assertEqual(result["Items"]["$values"], [])

    def test_container_deepsky_default_items(self):
        from nina_planner.sequence import container_deepsky

        result = container_deepsky(name="DSO", target="M31", ra=5.0, dec=10.0, posang=0)
        self.assertEqual(result["Items"]["$values"], [])


class PointingIndexValidationTest(unittest.TestCase):
    """Covers line 838 in sequence.py: pointing_index out-of-range."""

    def test_pointing_index_zero_raises(self):
        from nina_planner.sequence import build_sequence_lights

        plan = _plan()
        with self.assertRaises(ValueError):
            build_sequence_lights(
                plan, equipment=ObservatoryEquipment(), pointing_index=0
            )

    def test_pointing_index_too_high_raises(self):
        from nina_planner.sequence import build_sequence_lights

        plan = _plan()
        with self.assertRaises(ValueError):
            build_sequence_lights(
                plan, equipment=ObservatoryEquipment(), pointing_index=99
            )


class JsonTemplateBuildersTest(unittest.TestCase):
    """Coverage of the one-line JSON template wrappers in sequence.py.

    These tests are busywork — they don't catch algorithmic bugs — but
    they lock in CLR type names so a future NINA upgrade that renames a
    type will be caught.
    """

    def _assert_clr_type(self, result, expected_substring):
        self.assertIn("$type", result)
        self.assertIn(expected_substring, result["$type"])

    # ---- mount ----

    def test_unpark_scope(self):
        from nina_planner.sequence import unpark_scope

        self._assert_clr_type(unpark_scope(), "UnparkScope")

    def test_park_scope(self):
        from nina_planner.sequence import park_scope

        self._assert_clr_type(park_scope(), "ParkScope")

    def test_home_scope(self):
        from nina_planner.sequence import home_scope

        self._assert_clr_type(home_scope(), "FindHome")

    # ---- camera ----

    def test_warm_camera(self):
        from nina_planner.sequence import warm_camera

        result = warm_camera()
        self._assert_clr_type(result, "WarmCamera")
        self.assertEqual(result["Duration"], 0)

    def test_cool_camera(self):
        from nina_planner.sequence import cool_camera

        result = cool_camera(-10.0)
        self._assert_clr_type(result, "CoolCamera")
        self.assertEqual(result["Temperature"], -10.0)
        self.assertEqual(result["Duration"], 0)

    # ---- slew/track ----

    def test_slew_to_azalt(self):
        from nina_planner.sequence import slew_to_azalt

        result = slew_to_azalt(180.0, 45.0)
        self._assert_clr_type(result, "SlewScopeToAltAz")
        # Coordinates are converted to degrees + minutes + seconds.
        self.assertEqual(result["Coordinates"]["AzDegrees"], 180)
        self.assertEqual(result["Coordinates"]["AltDegrees"], 45)

    def test_set_readout(self):
        from nina_planner.sequence import set_readout

        result = set_readout(0)
        self._assert_clr_type(result, "SetReadoutMode")
        self.assertEqual(result["Mode"], 0)

    def test_annotation(self):
        from nina_planner.sequence import annotation

        result = annotation()
        self._assert_clr_type(result, "Annotation")

    def test_switch_filter(self):
        from nina_planner.sequence import switch_filter

        result = switch_filter("L", 3)
        self._assert_clr_type(result, "SwitchFilter")
        self.assertEqual(result["Filter"]["_name"], "L")
        self.assertEqual(result["Filter"]["_position"], 3)

    def test_sky_flats(self):
        from nina_planner.sequence import sky_flats

        result = sky_flats(10, "L", 3)
        self._assert_clr_type(result, "SkyFlat")
        self.assertEqual(result["Name"], "Twilight Sky Flats")

    # ---- time/wait helpers ----
    # wait_until_dawn/sunset return a WaitForTime with SelectedProvider
    # nested — the provider name lives in SelectedProvider.$type, not the top
    # level $type.

    def test_wait_until_dawn(self):
        from nina_planner.sequence import wait_until_dawn

        result = wait_until_dawn()
        self._assert_clr_type(result, "WaitForTime")
        self.assertIn("SelectedProvider", result)
        self.assertIn("DawnProvider", result["SelectedProvider"]["$type"])

    def test_wait_until_sunset(self):
        from nina_planner.sequence import wait_until_sunset

        result = wait_until_sunset()
        self._assert_clr_type(result, "WaitForTime")
        self.assertIn("SelectedProvider", result)
        self.assertIn("SunsetProvider", result["SelectedProvider"]["$type"])

    def test_wait_if_sun_altitude_above(self):
        from nina_planner.sequence import wait_if_sun_altitude_above

        self._assert_clr_type(wait_if_sun_altitude_above(15.0), "WaitForSunAltitude")

    def test_wait_if_sun_altitude_below(self):
        from nina_planner.sequence import wait_if_sun_altitude_below

        self._assert_clr_type(wait_if_sun_altitude_below(-8.0), "WaitForSunAltitude")

    def test_loop_until_sun_altitude_above(self):
        from nina_planner.sequence import loop_until_sun_altitude_above

        self._assert_clr_type(
            loop_until_sun_altitude_above(15.0), "SunAltitudeCondition"
        )

    def test_loop_until_sun_altitude_below(self):
        from nina_planner.sequence import loop_until_sun_altitude_below

        self._assert_clr_type(
            loop_until_sun_altitude_below(-8.0), "SunAltitudeCondition"
        )

    # ---- imaging ----

    def test_take_exposure(self):
        from nina_planner.sequence import take_exposure

        result = take_exposure(60.0, "LIGHT")
        self._assert_clr_type(result, "TakeExposure")
        self.assertEqual(result["ImageType"], "LIGHT")
        self.assertEqual(result["ExposureTime"], 60.0)


class SequenceBuildersTest(unittest.TestCase):
    """Coverage of build_sequence_* entry points: standby, teardown, darks,
    flats. The flats builder was missing the pointing_index parameter;
    fixed in the same commit.
    """

    def _plan(self, **overrides):
        data = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27}],
            "light": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias": [{"total_count": 1}],
        }
        data.update(overrides)
        return ObservationPlan(**data)

    def _profile(self):
        return _FlatProfileStub()

    def test_build_sequence_standby(self):
        from nina_planner.sequence import build_sequence_standby

        result = build_sequence_standby(ObservatoryEquipment())
        self.assertIsInstance(result, dict)
        self.assertIn("$type", result)

    def test_build_sequence_teardown(self):
        from nina_planner.sequence import build_sequence_teardown

        result = build_sequence_teardown(ObservatoryEquipment())
        self.assertIsInstance(result, dict)

    def test_build_sequence_darks(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(plan, equipment=ObservatoryEquipment())
        self.assertIsInstance(result, dict)

    def test_build_sequence_darks_with_bias(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(plan, equipment=ObservatoryEquipment(), bias=True)
        self.assertIsInstance(result, dict)

    def test_build_sequence_flats_dawn(self):
        from nina_planner.sequence import build_sequence_flats

        plan = self._plan()
        result = build_sequence_flats(
            plan,
            equipment=ObservatoryEquipment(),
            profile=self._profile(),
            dusk=False,
        )
        self.assertIsInstance(result, dict)

    def test_build_sequence_flats_dusk(self):
        from nina_planner.sequence import build_sequence_flats

        plan = self._plan()
        result = build_sequence_flats(
            plan,
            equipment=ObservatoryEquipment(),
            profile=self._profile(),
            dusk=True,
        )
        self.assertIsInstance(result, dict)


class _FlatProfileStub:
    """Minimal profile stub for build_sequence_flats — only needs
    filter_position(name) → int."""

    def filter_position(self, name: str) -> int:
        return {"L": 3}.get(name, 0)


if __name__ == "__main__":
    unittest.main()
