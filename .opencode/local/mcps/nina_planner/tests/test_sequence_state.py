import json
import unittest
from pathlib import Path

from nina_planner.sequence_state import project_sequence_state

_F = Path(__file__).parent / "fixtures"
STATE = json.loads((_F / "sequence_state_unrun.json").read_text())
JSON = json.loads((_F / "sequence_json_unrun.json").read_text())

# Derived from capture_plan.json's content (plan_id is deliberately absent
# from that file): one single-pointing plan, so all three steps carry the
# bare token. Frozen with the plan — edit the plan and this changes.
PLAN_IDS = {"plan-b3776ba18cb8"}


def _grouped_step_state(groups):
    """A target area holding one step whose groups are ``groups``.

    Group containers are named structurally (never ``Exposure Group``) so the
    reading is pinned on shape — an iteration condition over a ``Take
    Exposure`` — rather than on any spelling N.I.N.A. or this repo uses.
    ``filter: None`` omits the Switch Filter entirely.
    """
    items = []
    for index, group in enumerate(groups):
        instructions = []
        if group["filter"] is not None:
            instructions.append(
                {"Name": "Switch Filter", "Filter": {"_name": group["filter"]}}
            )
        instructions.append(
            {
                "Name": "Take Exposure",
                "ImageType": "LIGHT",
                "ExposureTime": group["seconds"],
                "Gain": 100,
                "Offset": 50,
                "Binning": {"X": 1, "Y": 1},
            }
        )
        items.append(
            {
                "Name": f"countdown group {index}",
                "Status": "CREATED",
                "Conditions": [
                    {
                        "Name": "Loop For Iterations_Condition",
                        "Iterations": group["total"],
                        "CompletedIterations": group.get("completed", 0),
                    },
                    {"Name": "Loop while_Condition"},
                ],
                "Items": instructions,
            }
        )
    return [
        {
            "Name": "Target Sequence_Container",
            "Status": "CREATED",
            "Items": [
                {
                    "Name": "Deep Sky Target Sequence_Container",
                    "Status": "CREATED",
                    "Target": {"TargetName": "M31 _plan-1234567890ab_"},
                    "Items": items,
                }
            ],
        }
    ]


class ProjectSequenceStateTests(unittest.TestCase):
    def setUp(self):
        self.out = project_sequence_state(STATE["Response"], JSON["Response"])

    def test_plan_ids(self):
        self.assertEqual(self.out["step_count"], 3)
        self.assertEqual({s["plan_id"] for s in self.out["steps"]}, PLAN_IDS)
        self.assertFalse(self.out["parse_error"])

    def test_totals_never_zero(self):
        light, dark, bias = self.out["steps"]
        # The light step reports no exposure today: NINA drops the Sequencer+
        # `Parent` back-references while loading, which empties its exposure
        # groups (15 "Failed to deserialize sequence entity" per load). The
        # projection must report that honestly as `None` rather than invent
        # zeros — and both calibration steps, which load cleanly, still count.
        self.assertIsNone(light["exposure"])
        self.assertEqual(light["exposure_groups"], [])
        for step in (dark, bias):
            self.assertGreater(step["exposure"]["total"], 0)
            # A step that has groups reports exactly one entry per group, and
            # `exposure` is that first group.
            self.assertEqual(step["exposure_groups"], [step["exposure"]])
        self.assertEqual(dark["exposure"]["filter"], "Dark")
        self.assertEqual(dark["exposure"]["seconds"], 0.5)
        # Bias is loaded with the wheel variant — the only one this rig can
        # perform (no flat device, no shutter) — so it switches to the Dark
        # slot too and carries that filter rather than null.
        self.assertEqual(bias["exposure"]["filter"], "Dark")
        self.assertEqual(bias["exposure"]["seconds"], 0.0)

    def test_unrun_has_no_events_or_completed_steps(self):
        self.assertEqual(self.out["completed_steps"], 0)
        self.assertFalse(self.out["running"])
        self.assertTrue(all(s["fired_events"] == [] for s in self.out["steps"]))

    def test_preflight_issues_are_not_errors(self):
        # Equipment state at capture time decides what lands here (the rig's
        # focuser was connected, but the Sequencer+ variable declaration is
        # not), so assert the shape, not the content.
        self.assertEqual(self.out["issues"], [])
        self.assertIsInstance(self.out["preflight_issues"], list)
        self.assertNotIn("preflight_issues", self.out["steps"][0])

    def test_failed_node_surfaces_at_step_and_root(self):
        state = json.loads(json.dumps(STATE))
        step = next(
            n for n in state["Response"] if n.get("Name") == "Target Sequence_Container"
        )["Items"][0]
        af = next(n for n in _nodes(step) if n.get("Name") == "Run Autofocus")
        af["Status"] = "FAILED"
        # The capture's own preflight list is equipment-dependent, so the
        # issue text is injected here: only a FAILED node's Issues count as
        # real errors, at the step and at the root.
        af["Issues"] = ["Focuser not connected"]
        out = project_sequence_state(state["Response"])
        self.assertEqual(
            out["steps"][0]["issues"], ["Run Autofocus: Focuser not connected"]
        )
        self.assertEqual(out["issues"], ["Run Autofocus: Focuser not connected"])

    def test_horizon_from_json_and_graceful_without(self):
        horizon = self.out["steps"][0]["horizon"]
        # min_altitude comes from the plan's constraints, so it is stable;
        # the altitude and the wait estimate are capture-time values.
        self.assertEqual(horizon["min_altitude"], 30)
        self.assertIsInstance(horizon["current_altitude"], (int, float))
        self.assertIsInstance(horizon["expected_time"], str)
        # Calibration steps have no altitude wait to report.
        self.assertIsNone(self.out["steps"][1]["horizon"])
        self.assertIsNone(self.out["steps"][2]["horizon"])
        # Without /sequence/json there is nothing to merge in.
        self.assertIsNone(
            project_sequence_state(STATE["Response"])["steps"][0]["horizon"]
        )

    def test_empty_payload(self):
        out = project_sequence_state([])
        self.assertEqual(out["steps"], [])

    def test_countdown_group_exposure_is_read_structurally(self):
        """A group's name carries nothing, so the summary reads its shape.

        Production names the group ``Exposure Group`` — no counts, no
        filter — and the name here is deliberately a third spelling, to
        pin the reading on structure (an iteration condition over a
        ``Take Exposure`` in the same container) rather than on
        N.I.N.A.'s ``Smart Exposure_Container`` name or our own.
        """
        state = [
            {
                "Name": "Target Sequence_Container",
                "Status": "RUNNING",
                "Items": [
                    {
                        "Name": "Deep Sky Target Sequence_Container",
                        "Status": "RUNNING",
                        "Target": {"TargetName": "M31 - core _plan-1234567890ab_"},
                        "Items": [
                            {
                                "Name": (
                                    "5 x 60 sec exposures using `L` filter_Container"
                                ),
                                "Status": "RUNNING",
                                "Conditions": [
                                    {
                                        "Name": "Loop For Iterations_Condition",
                                        "Iterations": 5,
                                        "CompletedIterations": 2,
                                    },
                                    {"Name": "Loop while_Condition"},
                                ],
                                "Items": [
                                    {
                                        "Name": "Switch Filter",
                                        "Filter": {"_name": "L"},
                                    },
                                    {
                                        "Name": "Take Exposure",
                                        "ImageType": "LIGHT",
                                        "ExposureTime": 60.0,
                                        "Gain": 100,
                                        "Offset": 50,
                                        "Binning": {"X": 1, "Y": 1},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        out = project_sequence_state(state)
        step = out["steps"][0]
        self.assertEqual(step["frame_type"], "LIGHT")
        self.assertEqual(step["exposure"]["seconds"], 60.0)
        self.assertEqual(step["exposure"]["filter"], "L")
        self.assertEqual(step["exposure"]["total"], 5)
        self.assertEqual(step["exposure"]["completed"], 2)

    def test_step_without_an_exposure_node_has_none_exposure(self):
        # A step that never takes an exposure (a pure wait/park step, or a
        # target area whose items have not been expanded yet) must report
        # exposure=None rather than inventing zeros for the summary.
        state = [
            {
                "Name": "Target Sequence_Container",
                "Status": "FINISHED",
                "Items": [
                    {
                        "Name": "Park",
                        "Status": "FINISHED",
                        "Target": {"TargetName": "M31 _plan-1234567890ab_"},
                        "Items": [],
                    }
                ],
            }
        ]
        out = project_sequence_state(state)
        self.assertEqual(out["step_count"], 1)
        self.assertIsNone(out["steps"][0]["exposure"])
        self.assertIsNone(out["steps"][0]["frame_type"])

    def test_exposure_groups_lists_every_group_and_exposure_is_the_first(self):
        """C1: a batched step owes its frames across several groups.

        ``_exposure_group`` used to stop at the first structural match, so a
        step of ``L×5 + LP×3`` at batch 2 reported one group's counts as the
        whole step's.
        """
        state = _grouped_step_state(
            [
                {"filter": "L", "total": 2, "completed": 1, "seconds": 60.0},
                {"filter": "LP", "total": 1, "completed": 0, "seconds": 30.0},
            ]
        )
        step = project_sequence_state(state)["steps"][0]
        groups = step["exposure_groups"]
        self.assertEqual([g["total"] for g in groups], [2, 1])
        self.assertEqual([g["filter"] for g in groups], ["L", "LP"])
        self.assertEqual([g["seconds"] for g in groups], [60.0, 30.0])
        # `exposure` keeps its old meaning: the first group, byte for byte.
        self.assertEqual(step["exposure"], groups[0])
        self.assertEqual(step["exposure"]["completed"], 1)

    def test_group_without_a_switch_filter_reports_null_filter(self):
        # A cover/shutter dark (or any group that never selects a filter)
        # has a Take Exposure but no Switch Filter: report null, not a
        # stale filter carried over from another group.
        step = project_sequence_state(
            _grouped_step_state([{"filter": None, "total": 1, "seconds": 0.0}])
        )["steps"][0]
        self.assertIsNone(step["exposure"]["filter"])
        self.assertIsNone(step["exposure_groups"][0]["filter"])

    def test_unrecognised_target_area_is_reported_not_raised(self):
        """C2: an area name we do not recognise must not look like 'empty'."""
        renamed = [
            {"Name": "Target Sequence Renamed", "Status": "CREATED", "Items": []}
        ]
        out = project_sequence_state(renamed)
        self.assertTrue(out["parse_error"])
        self.assertEqual(out["step_count"], 0)
        self.assertIn(
            "target area 'Target Sequence_Container' not found in /sequence/state",
            out["issues"],
        )
        # An empty payload means "nothing loaded" — not a projection failure.
        self.assertFalse(project_sequence_state([])["parse_error"])
        # And a payload carrying the real area reports no error.
        self.assertFalse(self.out["parse_error"])

    def test_horizon_is_dropped_when_the_json_step_names_disagree(self):
        """C3: pair horizon data by index only while both endpoints agree.

        Neither endpoint can pair it any better (/sequence/json has no Target
        key and the step names repeat), so a mismatched order must fail safe
        to a missing horizon rather than reporting another step's altitude.
        """
        swapped = json.loads(json.dumps(JSON))
        area = next(
            n
            for n in _nodes(swapped["Response"])
            if n.get("Name") == "Target Sequence_Container"
        )
        area["Items"].reverse()
        out = project_sequence_state(STATE["Response"], swapped["Response"])
        self.assertGreater(out["step_count"], 0)
        self.assertTrue(all(s["horizon"] is None for s in out["steps"]))
        # Control: with the names in agreement the horizon is merged in.
        self.assertIsNotNone(self.out["steps"][0]["horizon"])


class FinishedRunTests(unittest.TestCase):
    """A real completed run, captured from N.I.N.A.: the darks micro-sequence.

    It replaces the legacy 8-step capture: one 0.5 s dark shot end to end
    (wheel parked on the Dark slot, one frame saved, then the automatic
    park). The equipment-dependent assertion this class used to carry — a
    FAILED autofocus node — has no counterpart in a darks run that finished
    clean, so the FAILED-node rule lives in
    ``ProjectSequenceStateTests.test_failed_node_surfaces_at_step_and_root``,
    which injects its own issue instead of depending on the rig.
    """

    def setUp(self):
        state = json.loads((_F / "sequence_state_finished.json").read_text())
        done = json.loads((_F / "sequence_json_finished.json").read_text())
        self.out = project_sequence_state(state["Response"], done["Response"])
        (self.step,) = self.out["steps"]

    def test_single_step_finished_with_full_counts(self):
        self.assertEqual(self.out["step_count"], 1)
        self.assertEqual(self.out["completed_steps"], 1)
        self.assertEqual(self.out["status"], "FINISHED")
        self.assertFalse(self.out["running"])
        self.assertFalse(self.out["parse_error"])

        exposure = self.step["exposure"]
        self.assertEqual(self.step["status"], "FINISHED")
        self.assertGreater(exposure["total"], 0)
        # a finished run's group has spent every frame it owed
        self.assertEqual(exposure["completed"], exposure["total"])
        # …and a step that has groups lists one entry per group, with
        # `exposure` being that first group
        self.assertEqual(self.step["exposure_groups"], [exposure])
        self.assertEqual(exposure["filter"], "Dark")
        self.assertEqual(exposure["seconds"], 0.5)
        self.assertEqual(exposure["binning"], "1x1")

        self.assertEqual(self.step["frame_type"], "DARK")
        self.assertEqual(self.step["plan_id"], next(iter(PLAN_IDS)))
        self.assertIsNone(self.step["horizon"])
        self.assertEqual(self.step["issues"], [])
        # A clean run reports nothing at either level: no FAILED node
        # anywhere, and no pre-flight validation notes left behind.
        self.assertEqual(self.out["issues"], [])
        self.assertEqual(self.out["preflight_issues"], [])

    def test_events_fire_once_finished(self):
        first = self.step["fired_events"]
        self.assertEqual(len(first), 2)
        self.assertTrue(first[0].startswith("ACQUISITION-STARTING"))
        self.assertTrue(first[1].startswith("ACQUISITION-FINISHED"))


def _nodes(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _nodes(v)
