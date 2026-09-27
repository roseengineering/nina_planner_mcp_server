import unittest
from dataclasses import dataclass
from typing import Any

from nina_planner.models.camera import CameraDevice
from nina_planner.models.mount import MountDevice
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

    def test_container_deepsky_explicit_lists_are_preserved(self):
        from nina_planner.sequence import container_deepsky

        result = container_deepsky(
            name="DSO",
            target="M31",
            ra=5.0,
            dec=10.0,
            triggers=[],
            conditions=[],
            instructions=[],
        )

        self.assertEqual(result["Conditions"]["$values"], [])
        self.assertEqual(result["Triggers"]["$values"], [])
        self.assertEqual(result["Items"]["$values"], [])


class ReferenceNormalizationTest(unittest.TestCase):
    def test_container_with_instructions_keeps_its_parent_reference(self):
        from nina_planner.sequence import _add_refs

        result = _add_refs(
            {
                "$id": None,
                "Parent": None,
                "Items": [
                    {
                        "$id": None,
                        "Parent": None,
                        "Instructions": [{"$id": None, "Parent": None}],
                    }
                ],
            }
        )

        container = result["Items"][0]
        self.assertEqual(container["Parent"], {"$ref": "1"})
        self.assertEqual(container["Instructions"][0]["Parent"], {"$ref": "1"})


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


def _find_items(d: Any, type_substring: str) -> list[dict]:
    """Recursively find all dicts whose $type contains the given substring."""
    out = []
    if isinstance(d, dict):
        t = d.get("$type")
        if isinstance(t, str) and type_substring in t:
            out.append(d)
        for v in d.values():
            out.extend(_find_items(v, type_substring))
    elif isinstance(d, list):
        for item in d:
            out.extend(_find_items(item, type_substring))
    return out


def _deepsky_targets(seq: dict) -> list[dict]:
    return _find_items(seq, "DeepSkyObjectContainer")


def _items(seq: dict) -> list[dict]:
    return seq["Items"]["$values"]


class SequenceBehaviorTest(unittest.TestCase):
    """Behavioral assertions on generated sequence contents.

    The SnapshotBuildersTest only checks ``isinstance(result, dict)``; these
    tests confirm that the right instructions and triggers actually appear in
    the right places.
    """

    def _plan(self, **overrides):
        data = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27, "label": "core"}],
            "light": [
                {
                    "filter_name": "L",
                    "exposure_time_seconds": 60.0,
                    "total_count": 3,
                }
            ],
            "flat": [
                {
                    "filter_name": "L",
                    "exposure_time_seconds": 5.0,
                    "total_count": 3,
                }
            ],
            "dark": [{"exposure_time_seconds": 60.0, "total_count": 3}],
            "bias": [{"total_count": 3}],
        }
        data.update(overrides)
        return ObservationPlan(**data)

    def test_lights_target_name_includes_label_and_plan_id(self):
        plan = self._plan()
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        dsos = _deepsky_targets(result)
        self.assertEqual(len(dsos), 1)
        target = dsos[0]["Target"]["TargetName"]
        self.assertIn("M31 - core", target)
        self.assertIn("[plan-test123-1]", target)

    def test_lights_without_pointing_label_omits_label_segment(self):
        plan = self._plan(pointings=[{"ra_hours": 0.71, "dec_deg": 41.27}])
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        dsos = _deepsky_targets(result)
        self.assertEqual(len(dsos), 1)
        target = dsos[0]["Target"]["TargetName"]
        self.assertTrue(target.startswith("M31 [plan-test123-1]"))

    def test_lights_slew_and_center_when_position_angle_none(self):
        plan = self._plan(pointings=[{"ra_hours": 0.71, "dec_deg": 41.27}])
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        centers = _find_items(result, "Platesolving.Center, NINA.Sequencer")
        rotates = _find_items(result, "CenterAndRotate")
        self.assertEqual(len(centers), 1)
        self.assertEqual(rotates, [])

    def test_lights_slew_center_and_rotate_when_position_angle_set(self):
        plan = self._plan(
            pointings=[{"ra_hours": 0.71, "dec_deg": 41.27, "position_angle_deg": 90.0}]
        )
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        rotates = _find_items(result, "CenterAndRotate")
        centers = _find_items(result, "Platesolving.Center, NINA.Sequencer")
        self.assertEqual(len(rotates), 1)
        self.assertEqual(centers, [])

    def test_lights_contains_acquisition_starting_and_finished_events(self):
        plan = self._plan()
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        events = _find_items(result, "SendEventInstruction")
        messages = [e["Message"] for e in events]
        self.assertTrue(
            any("ACQUISITION-STARTING" in m for m in messages),
            f"missing ACQUISITION-STARTING in {messages!r}",
        )
        self.assertTrue(
            any("ACQUISITION-FINISHED" in m for m in messages),
            f"missing ACQUISITION-FINISHED in {messages!r}",
        )

    def test_lights_image_object_uses_meridian_flip_trigger(self):
        plan = self._plan()
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        flips = _find_items(result, "MeridianFlipTrigger")
        self.assertGreaterEqual(len(flips), 1)

    def test_lights_image_object_includes_all_autofocus_triggers(self):
        plan = self._plan()
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        af_filter = _find_items(result, "AutofocusAfterFilterChange, NINA.Sequencer")
        af_exposures = _find_items(result, "AutofocusAfterExposures, NINA.Sequencer")
        af_temp = _find_items(
            result, "AutofocusAfterTemperatureChangeTrigger, NINA.Sequencer"
        )
        af_hfr = _find_items(result, "AutofocusAfterHFRIncreaseTrigger, NINA.Sequencer")
        self.assertEqual(len(af_filter), 1)
        self.assertEqual(len(af_exposures), 1)
        self.assertEqual(len(af_temp), 1)
        self.assertEqual(len(af_hfr), 1)

    def test_lights_image_object_includes_center_after_drift_trigger(self):
        plan = self._plan()
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        drift = _find_items(result, "CenterAfterDriftTrigger")
        self.assertEqual(len(drift), 1)
        self.assertEqual(
            drift[0]["AfterExposures"], plan.guiding.check_drift_every_n_exposures
        )
        self.assertEqual(drift[0]["DistanceArcMinutes"], plan.guiding.max_drift_arcmin)

    def test_lights_respects_plan_constraints(self):
        plan = self._plan(
            constraints={
                "min_altitude": 45.0,
                "horizon_offset_degrees": 5.0,
            }
        )
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        waits = _find_items(result, "WaitUntilAboveHorizon")
        alts = _find_items(result, "WaitForAltitude")
        self.assertTrue(any(w["Data"]["Offset"] == 5.0 for w in waits))
        self.assertTrue(any(a["Data"]["Offset"] == 45.0 for a in alts))

    def test_lights_uses_smart_exposure_with_filter_and_dither(self):
        plan = self._plan()
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        smart = _find_items(result, "SmartExposure, WhenPlugin")
        self.assertEqual(len(smart), 1)
        self.assertEqual(smart[0]["IterationsExpr"], 3)
        self.assertEqual(smart[0]["FilterExpr"], "L")
        self.assertEqual(smart[0]["DitherExpr"], plan.guiding.dither_every_n_exposures)

    def test_lights_safetynet_wraps_with_park_scope_when_mount_can_park(self):
        plan = self._plan()
        mount = MountDevice(connected=True, name="m", can_park=True, can_find_home=True)
        eq = ObservatoryEquipment(mount=mount)
        result = build_sequence_lights(plan, equipment=eq)
        parks = _find_items(result, "ParkScope")
        homes = _find_items(result, "FindHome, NINA.Sequencer")
        self.assertGreaterEqual(len(parks), 1)
        self.assertEqual(homes, [])

    def test_lights_safetynet_uses_find_home_when_only_can_find_home(self):
        plan = self._plan()
        mount = MountDevice(
            connected=True,
            name="m",
            can_park=False,
            can_find_home=True,
        )
        eq = ObservatoryEquipment(mount=mount)
        result = build_sequence_lights(plan, equipment=eq)
        parks = _find_items(result, "ParkScope")
        homes = _find_items(result, "FindHome, NINA.Sequencer")
        self.assertEqual(parks, [])
        self.assertGreaterEqual(len(homes), 1)

    def test_lights_safetynet_with_no_mount_omits_park_and_home(self):
        plan = self._plan()
        eq = ObservatoryEquipment(mount=None)
        result = build_sequence_lights(plan, equipment=eq)
        parks = _find_items(result, "ParkScope")
        homes = _find_items(result, "FindHome, NINA.Sequencer")
        self.assertEqual(parks, [])
        self.assertEqual(homes, [])

    def test_lights_with_cooler_emits_cool_camera_instruction(self):
        plan = self._plan(cooler={"on": True, "setpoint_celsius": -15.0})
        camera = CameraDevice(
            connected=True,
            name="cam",
            can_set_temperature=True,
            temperature=20.0,
        )
        eq = ObservatoryEquipment(camera=camera)
        result = build_sequence_lights(plan, equipment=eq)
        cools = _find_items(result, "CoolCamera")
        self.assertEqual(len(cools), 1)
        self.assertEqual(cools[0]["Temperature"], -15.0)

    def test_lights_cooler_off_with_has_cooler_emits_warm_camera(self):
        plan = self._plan(cooler={"on": False, "setpoint_celsius": -15.0})
        camera = CameraDevice(connected=True, name="cam", can_set_temperature=True)
        eq = ObservatoryEquipment(camera=camera)
        result = build_sequence_lights(plan, equipment=eq)
        warms = _find_items(result, "WarmCamera")
        cools = _find_items(result, "CoolCamera")
        self.assertGreaterEqual(len(warms), 1)
        self.assertEqual(cools, [])

    def test_lights_without_cooler_omits_camera_temperature_instructions(self):
        plan = self._plan()
        camera = CameraDevice(connected=True, name="cam", can_set_temperature=False)
        eq = ObservatoryEquipment(camera=camera)
        result = build_sequence_lights(plan, equipment=eq)
        warms = _find_items(result, "WarmCamera")
        cools = _find_items(result, "CoolCamera")
        self.assertEqual(warms, [])
        self.assertEqual(cools, [])

    def test_darks_uses_dark_image_type(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(plan, equipment=ObservatoryEquipment())
        smart = _find_items(result, "SmartExposure, WhenPlugin")
        self.assertEqual(len(smart), 1)
        # SmartExposure stores ImageType inside its instructions
        takes = _find_items(smart[0], "TakeExposure, WhenPlugin")
        self.assertTrue(all(t["ImageType"] == "DARK" for t in takes))

    def test_darks_with_bias_flag_uses_bias_image_type(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(plan, equipment=ObservatoryEquipment(), bias=True)
        smart = _find_items(result, "SmartExposure, WhenPlugin")
        self.assertEqual(len(smart), 1)
        takes = _find_items(smart[0], "TakeExposure, WhenPlugin")
        self.assertTrue(all(t["ImageType"] == "BIAS" for t in takes))

    def test_darks_start_area_parks_scope_when_mount_can_park(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        mount = MountDevice(connected=True, name="m", can_park=True)
        eq = ObservatoryEquipment(mount=mount)
        result = build_sequence_darks(plan, equipment=eq)
        parks = _find_items(result, "ParkScope")
        self.assertGreaterEqual(len(parks), 1)

    def test_flats_dawn_uses_sunrise_wait_and_dawn_azimuth(self):
        from nina_planner.sequence import build_sequence_flats

        plan = self._plan()
        result = build_sequence_flats(
            plan,
            equipment=ObservatoryEquipment(),
            profile=_FlatProfileStub(),
            dusk=False,
        )
        # Wait for dawn means SunsetProvider should not appear; DawnProvider should.
        dawn = _find_items(result, "DawnProvider")
        sunset = _find_items(result, "SunsetProvider")
        self.assertGreaterEqual(len(dawn), 1)
        self.assertEqual(sunset, [])

    def test_flats_dusk_uses_sunset_wait_and_dusk_azimuth(self):
        from nina_planner.sequence import build_sequence_flats

        plan = self._plan()
        result = build_sequence_flats(
            plan,
            equipment=ObservatoryEquipment(),
            profile=_FlatProfileStub(),
            dusk=True,
        )
        sunset = _find_items(result, "SunsetProvider")
        dawn = _find_items(result, "DawnProvider")
        self.assertGreaterEqual(len(sunset), 1)
        self.assertEqual(dawn, [])


class CoordinateHelpersTest(unittest.TestCase):
    """The radec/coord helpers aren't directly imported elsewhere; cover them
    through the public sequence_deepsky entry point that uses them."""

    def test_negative_declination_sets_negative_flag(self):
        plan = ObservationPlan(
            plan_id="plan-test123",
            target="Test",
            pointings=[{"ra_hours": 5.0, "dec_deg": -10.0}],
            light=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat=[{"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}],
            dark=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias=[{"total_count": 1}],
        )
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        dso = _deepsky_targets(result)[0]
        coords = dso["Target"]["InputCoordinates"]
        self.assertTrue(coords["NegativeDec"])
        self.assertEqual(coords["DecDegrees"], 10)
        self.assertEqual(coords["RAHours"], 5)

    def test_fractional_minutes_and_seconds_extracted(self):
        plan = ObservationPlan(
            plan_id="plan-test123",
            target="Test",
            pointings=[{"ra_hours": 5.575, "dec_deg": 10.0125}],
            light=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat=[{"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}],
            dark=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias=[{"total_count": 1}],
        )
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        coords = _deepsky_targets(result)[0]["Target"]["InputCoordinates"]
        # 0.575 hours = 34.5 minutes → 34 minutes, 30 seconds
        self.assertEqual(coords["RAMinutes"], 34)
        self.assertEqual(coords["RASeconds"], 30)
        # 0.0125 degrees = 0.75 minutes → 0 minutes, 45 seconds
        self.assertEqual(coords["DecMinutes"], 0)
        self.assertEqual(coords["DecSeconds"], 45)


class RoundRobinEdgeCasesTest(unittest.TestCase):
    def test_empty_exposures_returns_empty(self):
        from nina_planner.sequence import _round_robin

        self.assertEqual(_round_robin([], batch_size=0), [])
        self.assertEqual(_round_robin([], batch_size=2), [])

    def test_batch_size_larger_than_count_emits_single_entry(self):
        from nina_planner.sequence import _round_robin

        exps = [_Exp("L", 60.0, 2), _Exp("R", 60.0, 2)]
        self.assertEqual(
            _round_robin(exps, batch_size=10),
            [(2, 60.0, "L"), (2, 60.0, "R")],
        )

    def test_uneven_counts_across_filters(self):
        from nina_planner.sequence import _round_robin

        exps = [_Exp("L", 60.0, 5), _Exp("R", 60.0, 2)]
        # batch_size=2, R exhausts at iter 1; L continues with 2 + 1
        self.assertEqual(
            _round_robin(exps, batch_size=2),
            [
                (2, 60.0, "L"),
                (2, 60.0, "R"),
                (2, 60.0, "L"),
                (1, 60.0, "L"),
            ],
        )

    def test_exposure_without_filter_attribute_yields_none(self):
        from nina_planner.sequence import _round_robin

        @dataclass
        class _NoFilter:
            exposure_time_seconds: float = 30.0
            total_count: int = 4

        self.assertEqual(
            _round_robin([_NoFilter()], batch_size=0),
            [(4, 30.0, None)],
        )


class _FlatProfileStub:
    """Minimal profile stub for build_sequence_flats — only needs
    filter_position(name) → int."""

    def filter_position(self, name: str) -> int:
        return {"L": 3}.get(name, 0)


if __name__ == "__main__":
    unittest.main()
