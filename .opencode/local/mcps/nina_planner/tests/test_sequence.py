import json
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
    present here — as must ``Dark``, which the default (``wheel``) darks/
    bias step parks on (the ``cover`` variant never looks for it).
    """
    return ObservatoryProfile(
        profile_name="test",
        profile_id="profile-test123",
        site={},
        optics={},
        filters=[FilterInfo(name="L", position=3), FilterInfo(name="Dark", position=4)],
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

    def test_connect_all_equipment(self):
        from nina_planner.sequence import connect_all_equipment

        self._assert_clr_type(connect_all_equipment(), "ConnectAllEquipment")

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
    flats. Calibration builders are plan-level: they carry no
    pointing_index (calibration is not per-pane).
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

    def test_build_sequence_darks(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(
            plan, equipment=ObservatoryEquipment(), profile=self._profile()
        )
        self.assertIsInstance(result, dict)

    def test_build_sequence_darks_with_bias(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(
            plan, equipment=ObservatoryEquipment(), profile=self._profile(), bias=True
        )
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

    def test_flats_container_is_plan_level_with_kind_name(self):
        from nina_planner.sequence import build_sequence_flats

        plan = self._plan()
        for dusk, kind in ((False, "Dawn Flats"), (True, "Dusk Flats")):
            result = build_sequence_flats(
                plan,
                equipment=ObservatoryEquipment(),
                profile=self._profile(),
                dusk=dusk,
            )
            dsos = _deepsky_targets(result)
            self.assertEqual(len(dsos), 1)
            # Plan-level token — no pointing suffix. The kind lives in the
            # container name, not the target name: $$TARGETNAME$$ would put
            # the target name in the file path and split the folder.
            self.assertEqual(dsos[0]["Target"]["TargetName"], "M31 (plan-test123)")
            self.assertEqual(dsos[0]["Name"], kind)

    def test_calibration_events_carry_kind_tag_but_target_does_not(self):
        from nina_planner.sequence import build_sequence_flats, target_payload_darks

        plan = self._plan()
        equipment = ObservatoryEquipment()

        def messages(payload) -> list[str]:
            found: list[str] = []

            def walk(node):
                if isinstance(node, dict):
                    if "Message" in node:
                        found.append(node["Message"])
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(payload)
            return found

        cases = [
            (target_payload_darks(plan, equipment, self._profile()), "dark"),
            (
                target_payload_darks(plan, equipment, self._profile(), bias=True),
                "bias",
            ),
        ]
        for dusk, tag in ((False, "dawn flats"), (True, "dusk flats")):
            cases.append(
                (
                    build_sequence_flats(plan, equipment, self._profile(), dusk=dusk),
                    tag,
                )
            )
        for payload, tag in cases:
            msgs = messages(payload)
            # The event text is self-describing: kind-tagged, plan-level.
            self.assertIn(f"ACQUISITION-STARTING: [{tag}] M31 (plan-test123)", msgs)
            self.assertIn(f"ACQUISITION-FINISHED: [{tag}] M31 (plan-test123)", msgs)
            # The tag stays out of the target name — $$TARGETNAME$$ would put
            # it in the file path and split the folder.
            dsos = _deepsky_targets(payload)
            self.assertEqual(dsos[0]["Target"]["TargetName"], "M31 (plan-test123)")


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


def _find_named(d: Any, name: str) -> list[dict]:
    """Recursively find all dicts whose ``Name`` equals the given name."""
    out = []
    if isinstance(d, dict):
        if d.get("Name") == name:
            out.append(d)
        for v in d.values():
            out.extend(_find_named(v, name))
    elif isinstance(d, list):
        for item in d:
            out.extend(_find_named(item, name))
    return out


def _deepsky_targets(seq: dict) -> list[dict]:
    return _find_items(seq, "DeepSkyObjectContainer")


def _items(seq: dict) -> list[dict]:
    return seq["Items"]["$values"]


def _event_messages(seq: dict) -> list[str]:
    """Every SendEvent message anywhere in the sequence, in tree order."""
    return [e["Message"] for e in _find_items(seq, "SendEventInstruction")]


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
        # Single-pointing plan: bare plan-level token, shared with calibration.
        self.assertIn("(plan-test123)", target)

    def test_lights_mosaic_target_name_appends_pane_index(self):
        plan = self._plan(
            pointings=[
                {"label": "core", "ra_hours": 0.71, "dec_deg": 41.27},
                {"ra_hours": 0.72, "dec_deg": 41.28},
            ]
        )
        result = build_sequence_lights(
            plan,
            equipment=ObservatoryEquipment(),
            profile=_profile(),
            pointing_index=2,
        )
        dsos = _deepsky_targets(result)
        target = dsos[0]["Target"]["TargetName"]
        # Mosaic pane: per-pane token.
        self.assertTrue(target.startswith("M31 (plan-test123-2)"), target)

    def test_lights_without_pointing_label_omits_label_segment(self):
        plan = self._plan(pointings=[{"ra_hours": 0.71, "dec_deg": 41.27}])
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        dsos = _deepsky_targets(result)
        self.assertEqual(len(dsos), 1)
        target = dsos[0]["Target"]["TargetName"]
        self.assertTrue(target.startswith("M31 (plan-test123)"))

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
        # Light events carry the same kind tag as calibration events, so the
        # night loop matches the positive form rather than a missing bracket.
        self.assertIn(
            "ACQUISITION-STARTING: [light] M31 - core (plan-test123)", messages
        )
        self.assertIn(
            "ACQUISITION-FINISHED: [light] M31 - core (plan-test123)", messages
        )
        # The tag stays out of the target name — $$TARGETNAME$$ would put it
        # in the file path and split the folder.
        self.assertEqual(
            _deepsky_targets(result)[0]["Target"]["TargetName"],
            "M31 - core (plan-test123)",
        )

    def test_lights_mosaic_pane_event_carries_pointing_token(self):
        plan = self._plan(
            pointings=[
                {"label": "core", "ra_hours": 0.71, "dec_deg": 41.27},
                {"ra_hours": 0.72, "dec_deg": 41.28},
            ]
        )
        result = build_sequence_lights(
            plan,
            equipment=ObservatoryEquipment(),
            profile=_profile(),
            pointing_index=2,
        )
        messages = [e["Message"] for e in _find_items(result, "SendEventInstruction")]
        self.assertIn("ACQUISITION-FINISHED: [light] M31 (plan-test123-2)", messages)

    def test_single_step_sequence_brackets_its_one_step(self):
        """The Start/End run bracket wraps a one-entry load too, and stays
        out of the Target area, which holds exactly the step."""
        from nina_planner.sequence import build_sequence_lights

        result = build_sequence_lights(
            self._plan(), equipment=ObservatoryEquipment(), profile=_profile()
        )
        start, target, _end = _items(result)
        self.assertEqual(
            start["Items"]["$values"][0]["Message"], "RUN-STARTING: 1 step"
        )
        self.assertEqual(len(target["Items"]["$values"]), 1)
        self.assertIn("RUN-FINISHED: 1 step complete", _event_messages(result))

    def test_lights_connects_all_equipment(self):
        plan = self._plan()
        result = build_sequence_lights(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        dsos = _deepsky_targets(result)
        self.assertEqual(len(dsos), 1)
        items = dsos[0]["Items"]["$values"]
        self.assertIn("ConnectAllEquipment", items[1]["$type"])

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

    def test_lights_parks_when_object_is_not_in_view(self):
        """The leading "Park Unless in View" container parks (or homes) the
        mount when the target is below the plan's constraints, and skips the
        park — via an End set naming that container — when it is in view."""
        plan = self._plan(
            constraints={"min_altitude": 45.0, "horizon_offset_degrees": 5.0}
        )
        mount = MountDevice(connected=True, name="m", can_park=True)
        eq = ObservatoryEquipment(mount=mount)
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())

        found = _find_named(result, "Park Unless in View")
        self.assertEqual(len(found), 1, "exactly one park-unless-in-view container")
        park = found[0]
        self.assertEqual(park["Conditions"]["$values"], [], "no loop: one pass only")

        items = park["Items"]["$values"]
        self.assertEqual(len(items), 2, "guard then park")
        guard = items[0]
        conditions = guard["Conditions"]["$values"]
        kinds = [c["$type"].split(",")[0].split(".")[-1] for c in conditions]
        self.assertEqual(kinds, ["AboveHorizonCondition", "AltitudeCondition"])
        self.assertEqual(conditions[0]["Data"]["Offset"], 5.0)
        self.assertEqual(conditions[1]["Data"]["Offset"], 45.0)

        # in view -> end the container before the park instruction runs
        ends = _find_items(guard, "EndInstructionSet")
        self.assertEqual(
            [e["InstructionSetName"] for e in ends], ["Park Unless in View"]
        )
        # out of view -> fall through to the capability-driven park
        self.assertTrue(_find_items(items[1], "ParkScope"))
        self.assertEqual(_find_items(items[1], "FindHome, NINA.Sequencer"), [])

        # and it runs before anything waits for dusk
        dsos = _deepsky_targets(result)
        self.assertEqual(len(dsos), 1)
        dso_items = dsos[0]["Items"]["$values"]
        names = [i.get("Name") for i in dso_items]
        self.assertLess(
            names.index("Park Unless in View"),
            names.index("Wait For Dusk"),
            "park first, then the safetynets",
        )

    def test_lights_parks_with_find_home_when_mount_cannot_park(self):
        plan = self._plan()
        mount = MountDevice(
            connected=True, name="m", can_park=False, can_find_home=True
        )
        eq = ObservatoryEquipment(mount=mount)
        result = build_sequence_lights(plan, equipment=eq, profile=_profile())

        park = _find_named(result, "Park Unless in View")[0]
        self.assertEqual(_find_items(park, "ParkScope"), [])
        self.assertEqual(len(_find_items(park, "FindHome, NINA.Sequencer")), 1)

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
        self.assertEqual(len(order), 4, "wait, then connect, then park, then warm")
        self.assertIn("SequentialContainer", order[0])
        self.assertIn("ConnectAllEquipment", order[1])
        self.assertIn("ParkScope", order[2])
        self.assertIn("WarmCamera", order[3])

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
        result = build_sequence_darks(
            plan, equipment=ObservatoryEquipment(), profile=_profile()
        )
        smart = _find_items(result, "Imaging.SmartExposure, NINA.Sequencer")
        self.assertEqual(len(smart), 1)
        # SmartExposure stores ImageType inside its instructions
        takes = _find_items(smart[0], "Imaging.TakeExposure, NINA.Sequencer")
        self.assertTrue(all(t["ImageType"] == "DARK" for t in takes))

    def test_darks_with_bias_flag_uses_bias_image_type(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        result = build_sequence_darks(
            plan, equipment=ObservatoryEquipment(), profile=_profile(), bias=True
        )
        smart = _find_items(result, "Imaging.SmartExposure, NINA.Sequencer")
        self.assertEqual(len(smart), 1)
        takes = _find_items(smart[0], "Imaging.TakeExposure, NINA.Sequencer")
        self.assertTrue(all(t["ImageType"] == "BIAS" for t in takes))

    def test_darks_switch_to_the_profiles_dark_filter(self):
        """The Dark filter's position comes from the profile, not a hardcode."""
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        for bias in (False, True):
            result = build_sequence_darks(
                plan, equipment=ObservatoryEquipment(), profile=_profile(), bias=bias
            )
            switches = _find_items(result, "FilterWheel.SwitchFilter, NINA.Sequencer")
            self.assertTrue(switches, "darks switch the wheel to the Dark filter")
            self.assertEqual(
                [s["Filter"]["_name"] for s in switches], ["Dark"] * len(switches)
            )
            self.assertEqual(
                [s["Filter"]["_position"] for s in switches], [4] * len(switches)
            )

    def test_darks_cover_variant_wraps_exposures_in_the_cover(self):
        """The cover method closes the motorized cover, exposes with the
        wheel untouched, then reopens — and needs no `Dark` filter slot."""
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        no_dark_slot = _profile().model_copy(
            update={"filters": [FilterInfo(name="L", position=3)]}
        )
        for bias in (False, True):
            result = build_sequence_darks(
                plan,
                equipment=ObservatoryEquipment(),
                profile=no_dark_slot,
                bias=bias,
                variant="cover",
            )
            covers = _find_items(result, "FlatDevice.")
            self.assertEqual(
                [c["$type"].split(",")[0].split(".")[-1] for c in covers],
                ["CloseCover", "OpenCover"],
                "close, then expose, then reopen — in document order",
            )
            # The wheel stays where it was: no Dark-filter switch at all.
            self.assertEqual(
                _find_items(result, "FilterWheel.SwitchFilter, NINA.Sequencer"), []
            )
            smart = _find_items(result, "Imaging.SmartExposure, NINA.Sequencer")
            self.assertTrue(smart)
            takes = _find_items(smart[0], "Imaging.TakeExposure, NINA.Sequencer")
            expected = "BIAS" if bias else "DARK"
            self.assertTrue(all(t["ImageType"] == expected for t in takes))

    def test_darks_shutter_variant_exposes_without_filter_wheel_or_cover(self):
        """The shutter method exposes with the wheel untouched and no cover,
        relying on the camera's shutter — and needs no `Dark` filter slot."""
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        no_dark_slot = _profile().model_copy(
            update={"filters": [FilterInfo(name="L", position=3)]}
        )
        for bias in (False, True):
            result = build_sequence_darks(
                plan,
                equipment=ObservatoryEquipment(),
                profile=no_dark_slot,
                bias=bias,
                variant="shutter",
            )
            # No cover operations
            self.assertEqual(_find_items(result, "FlatDevice."), [])
            # The wheel stays where it was: no Dark-filter switch at all.
            self.assertEqual(
                _find_items(result, "FilterWheel.SwitchFilter, NINA.Sequencer"), []
            )
            smart = _find_items(result, "Imaging.SmartExposure, NINA.Sequencer")
            self.assertTrue(smart)
            takes = _find_items(smart[0], "Imaging.TakeExposure, NINA.Sequencer")
            expected = "BIAS" if bias else "DARK"
            self.assertTrue(all(t["ImageType"] == expected for t in takes))

    def test_darks_reject_an_unknown_variant(self):
        from nina_planner.sequence import build_sequence_darks

        with self.assertRaises(ValueError) as ctx:
            build_sequence_darks(
                self._plan(),
                equipment=ObservatoryEquipment(),
                profile=_profile(),
                variant="lens",
            )
        self.assertIn("lens", str(ctx.exception))

    def test_darks_start_area_parks_scope_when_mount_can_park(self):
        from nina_planner.sequence import build_sequence_darks

        plan = self._plan()
        mount = MountDevice(connected=True, name="m", can_park=True)
        eq = ObservatoryEquipment(mount=mount)
        result = build_sequence_darks(plan, equipment=eq, profile=_profile())
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
    land in a single Target area, in order, under an End area that always
    stows — and an empty payload list is exactly the stow."""

    @staticmethod
    def _end_types(result: dict) -> list[str]:
        return [i["$type"] for i in _items(result)[2]["Items"]["$values"]]

    def test_flattens_payloads_into_one_target_area_in_order(self):
        from nina_planner.sequence import (
            build_sequence_many,
            target_payload_darks,
            target_payload_lights,
        )

        equipment = _stow_equipment()
        plan = _plan()
        payloads = [
            target_payload_lights(plan, equipment, _profile()),
            target_payload_darks(plan, equipment, _profile()),
            target_payload_darks(plan, equipment, _profile(), bias=True),
        ]

        result = build_sequence_many(equipment, payloads)

        areas = [item["Name"] for item in result["Items"]["$values"]]
        self.assertEqual(areas, ["Start Sequence", "Target Sequence", "End Sequence"])
        steps = _items(result)[1]["Items"]["$values"]
        self.assertEqual(len(steps), 3, "steps in list order, nothing merged")
        self.assertIn("DeepSkyObjectContainer", steps[0]["$type"])
        self.assertIn(
            "(plan-test123)", steps[0]["Target"]["TargetName"], "lights step first"
        )
        self.assertIn("DeepSkyObjectContainer", steps[1]["$type"])
        self.assertIn("DeepSkyObjectContainer", steps[2]["$type"])
        # Calibration containers are plan-level and named by kind.
        self.assertEqual(steps[1]["Name"], "Dark Sequence")
        self.assertEqual(steps[2]["Name"], "Bias Sequence")

        end_types = self._end_types(result)
        self.assertTrue(any("ParkScope" in t for t in end_types), "End parks")
        self.assertTrue(any("WarmCamera" in t for t in end_types), "End warms")

    def test_still_stows_when_a_payload_is_empty(self):
        from nina_planner.sequence import build_sequence_many

        equipment = _stow_equipment()
        result = build_sequence_many(equipment, [[]])
        self.assertEqual(_items(result)[1]["Items"]["$values"], [])
        end_types = self._end_types(result)
        self.assertTrue(any("ParkScope" in t for t in end_types))

    def test_run_is_bracketed_by_starting_and_finished_events(self):
        """`RUN-STARTING` opens the Start area and `RUN-FINISHED` heads the
        End area — the only place N.I.N.A. reaches when nothing in the run
        is left, so it is the trustworthy end-of-run signal."""
        from nina_planner.sequence import (
            build_sequence_many,
            target_payload_darks,
            target_payload_lights,
        )

        equipment = _stow_equipment()
        plan = _plan()
        payloads = [
            target_payload_lights(plan, equipment, _profile()),
            target_payload_darks(plan, equipment, _profile()),
            target_payload_darks(plan, equipment, _profile(), bias=True),
        ]

        result = build_sequence_many(equipment, payloads)

        start, target, end = _items(result)
        self.assertEqual(
            start["Items"]["$values"][0]["Message"], "RUN-STARTING: 3 steps"
        )
        self.assertEqual(
            end["Items"]["$values"][0]["Message"],
            "RUN-FINISHED: 3 steps complete",
        )
        # The bracket wraps the run rather than joining it: the Target area
        # still holds exactly the steps, with no event to parse out of them.
        self.assertEqual(len(target["Items"]["$values"]), 3)
        self.assertEqual(
            [m for m in _event_messages(target) if m.startswith("RUN-")], []
        )
        # RUN-FINISHED precedes the bounded wait and the park.
        self.assertIn("SendEventInstruction", end["Items"]["$values"][0]["$type"])

    def test_step_count_counts_only_entries_that_produced_steps(self):
        from nina_planner.sequence import (
            build_sequence_many,
            target_payload_lights,
        )

        equipment = _stow_equipment()
        payloads = [
            [],  # e.g. an entry already complete in `remaining` mode
            target_payload_lights(_plan(), equipment, _profile()),
        ]

        messages = _event_messages(build_sequence_many(equipment, payloads))

        self.assertIn("RUN-STARTING: 1 step", messages)
        self.assertIn("RUN-FINISHED: 1 step complete", messages)

    def test_stow_and_empty_entries_emit_no_run_events(self):
        """A stow must never read as a finished run — the agent would
        re-plan immediately after being told to close down."""
        from nina_planner.sequence import build_sequence_many, build_sequence_stow

        equipment = _stow_equipment()
        for result in (
            build_sequence_stow(equipment),
            build_sequence_many(equipment, []),
            build_sequence_many(equipment, [[]]),
        ):
            self.assertEqual(
                [m for m in _event_messages(result) if m.startswith("RUN-")],
                [],
                "stow-equivalent sequences must not bracket a run",
            )

    def test_empty_payload_list_is_the_stow(self):
        """An empty request list loads exactly the stow sequence."""
        from nina_planner.sequence import build_sequence_many, build_sequence_stow

        equipment = _stow_equipment()
        self.assertEqual(
            json.dumps(build_sequence_many(equipment, [])),
            json.dumps(build_sequence_stow(equipment)),
        )

    def test_darks_target_area_holds_the_container_directly(self):
        """darks used to nest a second Target area inside the real one; the
        payload form is a plain list, so the wrapper is gone."""
        from nina_planner.sequence import build_sequence_darks

        result = build_sequence_darks(_plan(), _stow_equipment(), _profile())
        children = _items(result)[1]["Items"]["$values"]
        self.assertEqual(len(children), 1)
        self.assertIn("DeepSkyObjectContainer", children[0]["$type"])


if __name__ == "__main__":
    unittest.main()
