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
        # This legacy fixture froze the pre-fix era: the light step reports no
        # exposure because N.I.N.A. dropped the Sequencer+ `Parent`
        # back-references while loading, which emptied its exposure groups (15
        # "Failed to deserialize sequence entity" per load). That is fixed now —
        # `LightsSequenceTests` captures a light step carrying its groups — but
        # the projection must still report a broken payload honestly as `None`
        # rather than invent zeros, and both calibration steps, which load
        # cleanly, count.
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

    def test_a_looping_wrapper_is_not_mistaken_for_the_group_it_holds(self):
        """W1: a wrapper's own iteration count must not become the group's.

        ``_counts_exposures`` matches any node that BOTH carries an iteration
        condition and merely CONTAINS a ``Take Exposure``, and ``_walk`` yields
        a container before its children — so a 2-iteration wrapper around a
        5-frame countdown group put the wrapper first, and ``exposure`` (the
        first group) reported ``total: 2`` instead of the group's 5, with a
        phantom second group beside it.
        """
        state = _grouped_step_state(
            [{"filter": "L", "total": 5, "completed": 2, "seconds": 60.0}]
        )
        deep_sky = state[0]["Items"][0]
        (group,) = deep_sky["Items"]
        deep_sky["Items"] = [
            {
                "Name": "Outer Loop_Container",
                "Status": "CREATED",
                "Conditions": [
                    {
                        "Name": "Loop For Iterations_Condition",
                        "Iterations": 2,
                        "CompletedIterations": 1,
                    }
                ],
                "Items": [group],
            }
        ]

        step = project_sequence_state(state)["steps"][0]
        # the step owes 5 frames across ONE countdown group, not 2
        self.assertEqual([g["total"] for g in step["exposure_groups"]], [5])
        self.assertEqual(step["exposure"]["total"], 5)
        self.assertEqual(step["exposure"]["completed"], 2)
        # and `exposure` stays byte-for-byte that single group
        self.assertEqual(step["exposure"], step["exposure_groups"][0])

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


class LightsSequenceTests(unittest.TestCase):
    """A real lights sequence, captured in all three of its states.

    NGC 7662, 4 x 10.0 s ``L`` in one batched group, captured from a live run
    on 2026-11-15 with ``scripts/refresh_fixtures.py --label lights`` (before
    the start), ``--label lightsrunning`` (exposure 1 of 4 in flight) and
    ``--label lightsfinished`` (all four counted, end area still in its
    pre-park delay).

    This is the first pair whose **light** step carries its exposure groups:
    the legacy ``unrun`` pair froze the pre-fix era in which N.I.N.A. dropped
    the Sequencer+ ``Parent`` back-references while loading and emptied them
    (15 "Failed to deserialize sequence entity" per load — see
    ``test_totals_never_zero``). Across these three captures the countdown
    itself is visible too: the group's iteration condition walks
    ``0 -> 1 -> 4`` against a total of 4 while ``Loop while "n > 0"`` and
    ``Variable n`` sit alongside it, evaluating clean at every stage.
    """

    LABELS = ("lights", "lightsrunning", "lightsfinished")
    PLAN_ID = "plan-0f7e6d5c4b3a"

    @classmethod
    def setUpClass(cls):
        cls.out = {}
        for label in cls.LABELS:
            state = json.loads((_F / f"sequence_state_{label}.json").read_text())
            done = json.loads((_F / f"sequence_json_{label}.json").read_text())
            cls.out[label] = project_sequence_state(state["Response"], done["Response"])

    def step(self, label):
        (step,) = self.out[label]["steps"]
        return step

    def test_single_light_step_with_its_groups_in_every_state(self):
        for label in self.LABELS:
            out = self.out[label]
            self.assertEqual(out["step_count"], 1, label)
            self.assertFalse(out["parse_error"], label)
            self.assertEqual(out["issues"], [], label)
            self.assertEqual(out["preflight_issues"], [], label)

            step = self.step(label)
            self.assertEqual(step["frame_type"], "LIGHT", label)
            self.assertEqual(step["plan_id"], self.PLAN_ID, label)
            self.assertIsNone(step["pointing_index"], label)
            self.assertEqual(step["issues"], [], label)

            exposure = step["exposure"]
            self.assertEqual(exposure["total"], 4, label)
            self.assertEqual(exposure["seconds"], 10.0, label)
            self.assertEqual(exposure["filter"], "L", label)
            self.assertEqual(exposure["binning"], "1x1", label)
            # a step that has groups lists exactly one entry per group, and
            # `exposure` is that first group
            self.assertEqual(step["exposure_groups"], [exposure], label)

    def test_countdown_walks_zero_to_full_across_the_captures(self):
        unrun, running, finished = (self.step(x) for x in self.LABELS)

        self.assertEqual(
            (unrun["status"], running["status"], finished["status"]),
            ("CREATED", "RUNNING", "FINISHED"),
        )
        counts = [s["exposure"]["completed"] for s in (unrun, running, finished)]
        self.assertEqual(counts, [0, 1, 4])
        # …against the same fixed total: the group spent exactly what it owed
        self.assertEqual(
            [s["exposure"]["total"] for s in (unrun, running, finished)],
            [4, 4, 4],
        )

        # `running` tracks ANY running node, so it stays true after the last
        # frame while the end area is still in its 3-minute pre-park delay.
        self.assertFalse(self.out["lights"]["running"])
        self.assertTrue(self.out["lightsrunning"]["running"])
        self.assertTrue(self.out["lightsfinished"]["running"])
        self.assertEqual(
            [self.out[x]["completed_steps"] for x in self.LABELS], [0, 0, 1]
        )

    def test_running_step_reports_its_phase_and_partial_counts(self):
        step = self.step("lightsrunning")
        self.assertEqual(step["phase"], "Take Exposure")
        self.assertLess(step["exposure"]["completed"], step["exposure"]["total"])

    def test_fired_events_accumulate_with_the_run(self):
        self.assertEqual(self.step("lights")["fired_events"], [])

        (only_start,) = self.step("lightsrunning")["fired_events"]
        self.assertTrue(only_start.startswith("ACQUISITION-STARTING"))

        both = self.step("lightsfinished")["fired_events"]
        self.assertEqual(len(both), 2)
        self.assertTrue(both[0].startswith("ACQUISITION-STARTING"))
        self.assertTrue(both[1].startswith("ACQUISITION-FINISHED"))
        # the token in the message is the plan id the projection reads back
        self.assertIn(self.PLAN_ID, both[0])

    def test_horizon_is_merged_into_every_state(self):
        # The target was near the meridian (73-76 deg) with the floor at 30,
        # so `expected_time` reads "Now" in all three captures.
        for label in self.LABELS:
            horizon = self.step(label)["horizon"]
            self.assertIsNotNone(horizon, label)
            self.assertEqual(horizon["min_altitude"], 30, label)
            self.assertEqual(horizon["expected_time"], "Now", label)
            self.assertGreater(horizon["current_altitude"], 30, label)

    def test_raw_countdown_nodes_are_present_and_clean(self):
        """The payload behind the projection: variable, read, loop condition."""
        for label in self.LABELS:
            state = json.loads((_F / f"sequence_state_{label}.json").read_text())[
                "Response"
            ]
            nodes = list(_nodes(state))

            (variable,) = [n for n in nodes if n.get("Name") == "Variable"]
            self.assertEqual(variable["Identifier"], "n", label)
            self.assertEqual(variable["OriginalDefinition"], "0", label)
            # `OriginalDefinition` is the DECLARED value and never changes;
            # `Definition` is what N.I.N.A. has evaluated it to so far. Before
            # the sequence runs, the node is unexecuted and `Definition` ships
            # EMPTY — "" means "not evaluated yet", not a value of zero. A
            # reader that coerced it to 0 would take `n` as "nothing left" and
            # call the group complete while it still owes every one of its
            # frames (remaining is really total - completed = 4 - 0).
            # Nothing else in the suite reads `Definition` at all, so pin it
            # here, next to the declaration it must not be confused with.
            self.assertEqual(
                variable["Definition"],
                {"lights": "", "lightsrunning": "3", "lightsfinished": "0"}[label],
                label,
            )
            # …and keep the two consistent: only an executed node carries an
            # evaluated value, so emptiness and `Executed` must agree.
            self.assertEqual(
                variable["Definition"] != "",
                variable["Executed"],
                f"{label}: Definition/Executed disagree",
            )

            (loop,) = [n for n in nodes if n.get("Name") == "Loop While_Condition"]
            self.assertEqual(
                (loop.get("PredicateExpr") or {}).get("Expression"), "n > 0", label
            )

            reads = [
                (n.get("VExpr") or {}).get("Expression")
                for n in nodes
                if n.get("Name") == "Get from Array"
            ]
            self.assertTrue(reads and all(r == "n" for r in reads), label)

            # the group's iteration condition is what `exposure.completed`
            # reports: it counts 0 -> 1 -> 4 across the three captures. Two
            # loop-for-iterations nodes ship in this payload — the group's
            # frame loop (4) and a 1-iteration outer loop — so select on the
            # total rather than on position.
            conds = [
                n for n in nodes if n.get("Name") == "Loop For Iterations_Condition"
            ]
            (cond,) = [c for c in conds if c.get("Iterations") == 4]
            self.assertEqual(
                cond["CompletedIterations"],
                {"lights": 0, "lightsrunning": 1, "lightsfinished": 4}[label],
                label,
            )

            # nothing anywhere carries an issue — the pre-fix payload threw
            # 15 deserialize errors per load and left a checklist prompt
            self.assertEqual(
                [(n.get("Name"), n.get("Issues")) for n in nodes if n.get("Issues")],
                [],
                label,
            )


def _nodes(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _nodes(v)
