import unittest
from dataclasses import dataclass
from typing import Any

from nina_planner.models.camera import CameraDevice
from nina_planner.models.mount import MountDevice
from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.models.plan import ObservationPlan
from nina_planner.models.profile import FilterInfo, ObservatoryProfile
from nina_planner.sequence import build_sequence_lights


def _plan(**overrides):
    data = {
        "plan_id": "plan-test123",
        "target": "Test Target",
        "pointings": [{"ra_hours": 5.0, "dec_deg": 10.0}],
        "light_frames": [
            {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 5}
        ],
        "flat_frames": [
            {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 5}
        ],
        "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 5}],
        "bias_frames": [{"total_count": 5}],
    }
    data.update(overrides)
    return ObservationPlan(**data)


def _profile() -> ObservatoryProfile:
    """Real profile with the filters used by the test plans.

    ``build_sequence_lights``/``build_sequence_flats`` resolve filter
    positions through this, so every filter referenced by a plan must be
    present here.
    """
    return ObservatoryProfile(
        profile_name="test",
        profile_id="profile-test123",
        site={},
        optics={},
        filters=[FilterInfo(name="L", position=3)],
        image_save_path="C:\\images",
        equipment={
            "has_mount": True,
            "has_camera": True,
            "has_focuser": True,
            "has_guider": True,
            "has_filter_wheel": True,
            "has_rotator": True,
            "has_dome": True,
            "has_weather": True,
            "has_safety_monitor": True,
            "has_switch": True,
        },
    )


def _stow_equipment() -> ObservatoryEquipment:
    """The best case for the teardown builders: a mount that can park and a
    camera that can cool."""
    return ObservatoryEquipment(
        mount=MountDevice(connected=True, name="m", can_park=True),
        camera=CameraDevice(connected=True, name="cam", can_set_temperature=True),
    )


class BuildSequenceLightsTest(unittest.TestCase):
    def test_pointing_with_no_position_angle_does_not_raise_name_error(self):
        plan = _plan(
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0, "position_angle_deg": None}]
        )
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        self.assertIsInstance(result, dict)

    def test_pointing_with_position_angle_does_not_raise_name_error(self):
        plan = _plan(
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0, "position_angle_deg": 90.0}]
        )
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        self.assertIsInstance(result, dict)

    def test_pointing_without_position_angle_field_does_not_raise_name_error(self):
        plan = _plan(pointings=[{"ra_hours": 5.0, "dec_deg": 10.0}])
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
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
                plan,
                equipment=ObservatoryEquipment(),
                profile=_profile(),
                pointing_index=0,
            )

    def test_pointing_index_too_high_raises(self):
        from nina_planner.sequence import build_sequence_lights

        plan = _plan()
        with self.assertRaises(ValueError):
            build_sequence_lights(
                plan,
                equipment=ObservatoryEquipment(),
                profile=_profile(),
                pointing_index=99,
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
    """Coverage of build_sequence_* entry points: teardown, darks,
    flats. The flats builder was missing the pointing_index parameter;
    fixed in the same commit.
    """

    def _plan(self, **overrides):
        data = {
            "plan_id": "plan-test123",
            "target": "M31",
            "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27}],
            "light_frames": [
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            "flat_frames": [
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
            "bias_frames": [{"total_count": 1}],
        }
        data.update(overrides)
        return ObservationPlan(**data)

    def _profile(self):
        return _profile()

    def test_build_sequence_teardown(self):
        """build_sequence_stow loads sequence_root(): the three sequence
        areas, with stowing and warming carried by the End area."""
        from nina_planner.sequence import sequence_root

        result = sequence_root(ObservatoryEquipment())
        self.assertIsInstance(result, dict)
        areas = [item.get("Name") for item in result["Items"]["$values"]]
        self.assertEqual(areas, ["Start Sequence", "Target Sequence", "End Sequence"])

    def test_build_sequence_stow_parks_then_warms(self):
        from nina_planner.sequence import build_sequence_stow

        result = build_sequence_stow(_stow_equipment())

        end = _items(result)[-1]
        self.assertEqual(
            _find_items(end, "ParkScope"), _find_items(result, "ParkScope")
        )
        self.assertEqual(
            _find_items(end, "WarmCamera"), _find_items(result, "WarmCamera")
        )
        self.assertTrue(_find_items(result, "ParkScope"), "stow parks (or homes)")
        self.assertTrue(_find_items(result, "WarmCamera"), "stow then warms")

    def test_build_sequence_park_does_not_warm(self):
        from nina_planner.sequence import build_sequence_park

        result = build_sequence_park(_stow_equipment())

        self.assertTrue(_find_items(result, "ParkScope"))
        self.assertEqual(_find_items(result, "WarmCamera"), [])
        # Empty target area: the End area parks as soon as the sequence starts.
        self.assertEqual(_items(result)[1]["Items"]["$values"], [])

    def test_build_sequence_park_falls_back_to_home(self):
        from nina_planner.sequence import build_sequence_park

        home_only = ObservatoryEquipment(
            mount=MountDevice(
                connected=True, name="m", can_park=False, can_find_home=True
            )
        )
        result = build_sequence_park(home_only)

        self.assertEqual(_find_items(result, "ParkScope"), [])
        self.assertEqual(len(_find_items(result, "FindHome, NINA.Sequencer")), 1)

    def test_build_sequence_warm_does_not_touch_the_mount(self):
        from nina_planner.sequence import build_sequence_warm

        result = build_sequence_warm(_stow_equipment())

        self.assertTrue(_find_items(result, "WarmCamera"))
        self.assertEqual(_find_items(result, "ParkScope"), [])
        self.assertEqual(_find_items(result, "FindHome, NINA.Sequencer"), [])
        # Empty End area: warming must not park on the way out either.
        self.assertEqual(_items(result)[-1]["Items"]["$values"], [])

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
            "light_frames": [
                {
                    "filter_name": "L",
                    "exposure_time_seconds": 60.0,
                    "total_count": 3,
                }
            ],
            "flat_frames": [
                {
                    "filter_name": "L",
                    "exposure_time_seconds": 5.0,
                    "total_count": 3,
                }
            ],
            "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 3}],
            "bias_frames": [{"total_count": 3}],
        }
        data.update(overrides)
        return ObservationPlan(**data)

    def test_lights_target_name_includes_label_and_plan_id(self):
        plan = self._plan()
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        dsos = _deepsky_targets(result)
        self.assertEqual(len(dsos), 1)
        target = dsos[0]["Target"]["TargetName"]
        self.assertIn("M31 - core", target)
        self.assertIn("(plan-test123-1)", target)

    def test_lights_without_pointing_label_omits_label_segment(self):
        plan = self._plan(pointings=[{"ra_hours": 0.71, "dec_deg": 41.27}])
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        dsos = _deepsky_targets(result)
        self.assertEqual(len(dsos), 1)
        target = dsos[0]["Target"]["TargetName"]
        self.assertTrue(target.startswith("M31 (plan-test123-1)"))

    def test_lights_slew_and_center_when_position_angle_none(self):
        plan = self._plan(pointings=[{"ra_hours": 0.71, "dec_deg": 41.27}])
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        centers = _find_items(result, "Platesolving.Center, NINA.Sequencer")
        rotates = _find_items(result, "CenterAndRotate")
        self.assertEqual(len(centers), 1)
        self.assertEqual(rotates, [])

    def test_lights_slew_center_and_rotate_when_position_angle_set(self):
        plan = self._plan(
            pointings=[{"ra_hours": 0.71, "dec_deg": 41.27, "position_angle_deg": 90.0}]
        )
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        rotates = _find_items(result, "CenterAndRotate")
        centers = _find_items(result, "Platesolving.Center, NINA.Sequencer")
        self.assertEqual(len(rotates), 1)
        self.assertEqual(centers, [])

    def test_lights_contains_acquisition_starting_and_finished_events(self):
        plan = self._plan()
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
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
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        flips = _find_items(result, "MeridianFlipTrigger")
        self.assertGreaterEqual(len(flips), 1)

    def test_lights_image_object_includes_all_autofocus_triggers(self):
        plan = self._plan()
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
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
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
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
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        waits = _find_items(result, "WaitUntilAboveHorizon")
        alts = _find_items(result, "WaitForAltitude")
        self.assertTrue(any(w["Data"]["Offset"] == 5.0 for w in waits))
        self.assertTrue(any(a["Data"]["Offset"] == 45.0 for a in alts))

    def test_lights_uses_smart_exposure_with_filter_and_dither(self):
        plan = self._plan()
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        smart = _find_items(result, "Imaging.SmartExposure, NINA.Sequencer")
        self.assertEqual(len(smart), 1)
        # count -> the LoopCondition inside the SmartExposure container
        conditions = smart[0]["Conditions"]["$values"]
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0]["Iterations"], 3)
        # filter -> the SwitchFilter instruction inside the container
        switches = _find_items(smart[0], "FilterWheel.SwitchFilter, NINA.Sequencer")
        self.assertEqual(len(switches), 1)
        self.assertEqual(switches[0]["Filter"]["_name"], "L")
        # dither -> the native DitherAfterExposures trigger
        dithers = _find_items(smart[0], "Guider.DitherAfterExposures, NINA.Sequencer")
        self.assertEqual(len(dithers), 1)
        self.assertEqual(
            dithers[0]["AfterExposures"], plan.guiding.dither_every_n_exposures
        )

    def test_lights_safetynet_wraps_with_park_scope_when_mount_can_park(self):
        plan = self._plan()
        mount = MountDevice(connected=True, name="m", can_park=True, can_find_home=True)
        eq = ObservatoryEquipment(mount=mount)
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())
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
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())
        parks = _find_items(result, "ParkScope")
        homes = _find_items(result, "FindHome, NINA.Sequencer")
        self.assertEqual(parks, [])
        self.assertGreaterEqual(len(homes), 1)

    def test_lights_safetynet_with_no_mount_omits_park_and_home(self):
        plan = self._plan()
        eq = ObservatoryEquipment(mount=None)
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())
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
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())
        cools = _find_items(result, "CoolCamera")
        self.assertEqual(len(cools), 1)
        self.assertEqual(cools[0]["Temperature"], -15.0)

    def test_lights_cooler_off_with_has_cooler_emits_warm_camera(self):
        plan = self._plan(cooler={"on": False, "setpoint_celsius": -15.0})
        camera = CameraDevice(connected=True, name="cam", can_set_temperature=True)
        eq = ObservatoryEquipment(camera=camera)
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())
        warms = _find_items(result, "WarmCamera")
        cools = _find_items(result, "CoolCamera")
        self.assertGreaterEqual(len(warms), 1)
        self.assertEqual(cools, [])

    def test_lights_without_cooler_omits_camera_temperature_instructions(self):
        plan = self._plan()
        camera = CameraDevice(connected=True, name="cam", can_set_temperature=False)
        eq = ObservatoryEquipment(camera=camera)
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())
        warms = _find_items(result, "WarmCamera")
        cools = _find_items(result, "CoolCamera")
        self.assertEqual(warms, [])
        self.assertEqual(cools, [])

    def test_end_area_waits_three_minutes_then_stows_and_warms(self):
        """The End area waits out one bounded "On Safe" pass — a single
        3-minute iteration gated on the safety monitor — then parks and warms.
        Bounded matters: a teardown sequence completes within that one pass
        (immediately when the enclosure is already closed) instead of idling
        until sunrise, so "nothing running" stays an unambiguous state."""
        from nina_planner.sequence import sequence_end

        mount = MountDevice(connected=True, name="m", can_park=True)
        camera = CameraDevice(connected=True, name="cam", can_set_temperature=True)
        end = sequence_end(ObservatoryEquipment(mount=mount, camera=camera))

        # The wait lives in a nested container; the End area itself has none.
        self.assertEqual(end["Conditions"]["$values"], [], "End must not wait")

        containers = _find_items(end, "SequentialContainer")
        self.assertEqual(len(containers), 1, "exactly one waiting container")
        wait = containers[0]
        self.assertEqual(wait["Name"], "On Safe")
        wait_types = [c["$type"] for c in wait["Conditions"]["$values"]]
        self.assertEqual(len(wait_types), 2, "safety gate + bounded loop")
        self.assertTrue(
            any("SafetyMonitorCondition" in t for t in wait_types), "gated on safe"
        )
        self.assertTrue(
            any("LoopCondition" in t for t in wait_types), "bounded, not forever"
        )
        loop = next(
            c for c in wait["Conditions"]["$values"] if "LoopCondition" in c["$type"]
        )
        self.assertEqual(loop["Iterations"], 1, "a single pass")
        self.assertEqual(loop["CompletedIterations"], 0)
        waits = _find_items(wait, "WaitForTimeSpan")
        self.assertEqual([w["Time"] for w in waits], [3 * 60], "one 3-minute pass")

        # The End area itself carries no safety condition of its own — the
        # single gate lives inside the wait container — and stows before
        # warming, after the wait.
        self.assertEqual(len(_find_items(end, "SafetyMonitorCondition")), 1)
        order = [item.get("$type", "") for item in end["Items"]["$values"]]
        self.assertEqual(len(order), 3, "wait, then park, then warm")
        self.assertIn("SequentialContainer", order[0])
        self.assertIn("ParkScope", order[1])
        self.assertIn("WarmCamera", order[2])

    def test_end_area_stow_is_capability_driven(self):
        from nina_planner.sequence import sequence_end

        can_park = MountDevice(connected=True, name="m", can_park=True)
        home_only = MountDevice(
            connected=True, name="m", can_park=False, can_find_home=True
        )

        parked = sequence_end(ObservatoryEquipment(mount=can_park))
        self.assertEqual(len(_find_items(parked, "ParkScope")), 1)
        self.assertEqual(_find_items(parked, "FindHome, NINA.Sequencer"), [])

        homed = sequence_end(ObservatoryEquipment(mount=home_only))
        self.assertEqual(_find_items(homed, "ParkScope"), [])
        self.assertEqual(len(_find_items(homed, "FindHome, NINA.Sequencer")), 1)

        stowed = sequence_end(ObservatoryEquipment(mount=None))
        self.assertEqual(_find_items(stowed, "ParkScope"), [])
        self.assertEqual(_find_items(stowed, "FindHome, NINA.Sequencer"), [])

    def test_end_area_warms_only_when_camera_has_cooler(self):
        from nina_planner.sequence import sequence_end

        cooler = CameraDevice(connected=True, name="cam", can_set_temperature=True)
        no_cooler = CameraDevice(connected=True, name="cam", can_set_temperature=False)

        self.assertTrue(
            _find_items(sequence_end(ObservatoryEquipment(camera=cooler)), "WarmCamera")
        )
        self.assertEqual(
            _find_items(
                sequence_end(ObservatoryEquipment(camera=no_cooler)), "WarmCamera"
            ),
            [],
        )

    def test_darks_uses_dark_image_type(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(plan, equipment=ObservatoryEquipment())
        smart = _find_items(result, "Imaging.SmartExposure, NINA.Sequencer")
        self.assertEqual(len(smart), 1)
        # SmartExposure stores ImageType inside its instructions
        takes = _find_items(smart[0], "Imaging.TakeExposure, NINA.Sequencer")
        self.assertTrue(all(t["ImageType"] == "DARK" for t in takes))

    def test_darks_with_bias_flag_uses_bias_image_type(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(plan, equipment=ObservatoryEquipment(), bias=True)
        smart = _find_items(result, "Imaging.SmartExposure, NINA.Sequencer")
        self.assertEqual(len(smart), 1)
        takes = _find_items(smart[0], "Imaging.TakeExposure, NINA.Sequencer")
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
            profile=_profile(),
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
            profile=_profile(),
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
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat_frames=[
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias_frames=[{"total_count": 1}],
        )
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
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
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
            ],
            flat_frames=[
                {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
            ],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 1}],
            bias_frames=[{"total_count": 1}],
        )
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
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


class BuildSequenceManyTest(unittest.TestCase):
    """A list of requests becomes one sequence: every payload's containers
    land in a single Target area, in order, under one stow End area."""

    def test_flattens_payloads_into_one_target_area_in_order(self):
        from nina_planner.sequence import (
            build_sequence_many,
            step_warm,
            target_payload_darks,
        )

        equipment = _stow_equipment()
        plan = _plan()
        payloads = [
            target_payload_darks(plan, equipment),
            step_warm(equipment),
            target_payload_darks(plan, equipment, bias=True),
        ]

        result = build_sequence_many(equipment, payloads)

        areas = [item["Name"] for item in result["Items"]["$values"]]
        self.assertEqual(areas, ["Start Sequence", "Target Sequence", "End Sequence"])
        steps = _items(result)[1]["Items"]["$values"]
        self.assertEqual(len(steps), 3, "steps in list order, nothing merged")
        self.assertIn("DeepSkyObjectContainer", steps[0]["$type"])
        self.assertIn("WarmCamera", steps[1]["$type"])
        self.assertIn("DeepSkyObjectContainer", steps[2]["$type"])

        end_types = [i["$type"] for i in _items(result)[2]["Items"]["$values"]]
        self.assertTrue(any("ParkScope" in t for t in end_types), "End parks")
        self.assertTrue(any("WarmCamera" in t for t in end_types), "End warms")

    def test_still_stows_when_a_payload_is_empty(self):
        from nina_planner.sequence import build_sequence_many

        equipment = _stow_equipment()
        result = build_sequence_many(equipment, [[]])
        self.assertEqual(_items(result)[1]["Items"]["$values"], [])
        end_types = [i["$type"] for i in _items(result)[2]["Items"]["$values"]]
        self.assertTrue(any("ParkScope" in t for t in end_types))

    def test_darks_target_area_holds_the_container_directly(self):
        """darks used to nest a second Target area inside the real one; the
        payload form is a plain list, so the wrapper is gone."""
        from nina_planner.sequence import build_sequence_darks

        result = build_sequence_darks(_plan(), _stow_equipment())
        children = _items(result)[1]["Items"]["$values"]
        self.assertEqual(len(children), 1)
        self.assertIn("DeepSkyObjectContainer", children[0]["$type"])


class ListStepTest(unittest.TestCase):
    """step_* turn the teardown actions into Target-area payloads for a
    composed list, each refusing to build when the equipment cannot do it."""

    @staticmethod
    def _types(steps: list[dict]) -> list[str]:
        return [str(d.get("$type", "")) for d in steps]

    def test_step_park_is_capability_driven(self):
        from nina_planner.sequence import step_park

        self.assertTrue(
            any("ParkScope" in t for t in self._types(step_park(_stow_equipment())))
        )

        home_only = ObservatoryEquipment(
            mount=MountDevice(
                connected=True, name="m", can_park=False, can_find_home=True
            )
        )
        self.assertTrue(any("FindHome" in t for t in self._types(step_park(home_only))))

        with self.assertRaises(ValueError) as ctx:
            step_park(ObservatoryEquipment(mount=MountDevice(connected=True, name="m")))
        self.assertIn("nothing to park", str(ctx.exception))

    def test_step_warm_requires_a_cooler(self):
        from nina_planner.sequence import step_warm

        self.assertTrue(
            any("WarmCamera" in t for t in self._types(step_warm(_stow_equipment())))
        )

        no_cooler = ObservatoryEquipment(
            camera=CameraDevice(connected=True, name="cam", can_set_temperature=False)
        )
        with self.assertRaises(ValueError) as ctx:
            step_warm(no_cooler)
        self.assertIn("nothing to warm", str(ctx.exception))

    def test_step_unpark_requires_a_parkable_mount(self):
        from nina_planner.sequence import step_unpark

        self.assertTrue(
            any("UnparkScope" in t for t in self._types(step_unpark(_stow_equipment())))
        )

        home_only = ObservatoryEquipment(
            mount=MountDevice(
                connected=True, name="m", can_park=False, can_find_home=True
            )
        )
        with self.assertRaises(ValueError) as ctx:
            step_unpark(home_only)
        self.assertIn("nothing to unpark", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
