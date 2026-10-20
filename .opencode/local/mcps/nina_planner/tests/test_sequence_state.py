import json
import unittest
from pathlib import Path

from nina_planner.sequence_state import project_sequence_state

_F = Path(__file__).parent / "fixtures"
STATE = json.loads((_F / "sequence_state_unrun.json").read_text())
JSON = json.loads((_F / "sequence_json_unrun.json").read_text())

PLAN_IDS = {
    "plan-15ae9e9621b4", "plan-bb21c47192dc", "plan-a0d99398be48", "plan-7839febc3d6c",
    "plan-f83cf665d97a", "plan-b62a22cbc61d", "plan-26fc8c17a01c", "plan-bb3cd3587fd3",
}  # fmt: skip


class ProjectSequenceStateTests(unittest.TestCase):
    def setUp(self):
        self.out = project_sequence_state(STATE["Response"], JSON["Response"])

    def test_plan_ids(self):
        self.assertEqual(self.out["step_count"], 8)
        self.assertEqual({s["plan_id"] for s in self.out["steps"]}, PLAN_IDS)

    def test_totals_never_zero(self):
        totals = [s["exposure"]["total"] for s in self.out["steps"]]
        self.assertEqual(totals, [60, 100, 40, 80, 60, 40, 40, 20])
        self.assertEqual(self.out["steps"][0]["exposure"]["filter"], "L")
        self.assertEqual(self.out["steps"][7]["exposure"]["filter"], "Dark")

    def test_unrun_has_no_events_or_completed_steps(self):
        self.assertEqual(self.out["completed_steps"], 0)
        self.assertFalse(self.out["running"])
        self.assertTrue(all(s["fired_events"] == [] for s in self.out["steps"]))

    def test_preflight_issues_are_not_errors(self):
        self.assertEqual(self.out["issues"], [])
        self.assertNotIn("preflight_issues", self.out["steps"][0])
        self.assertIn("Run Autofocus: Focuser not connected", self.out["preflight_issues"])

    def test_failed_node_surfaces_at_step_and_root(self):
        state = json.loads(json.dumps(STATE))
        step = next(n for n in state["Response"] if n.get("Name") == "Target Sequence_Container")["Items"][0]
        af = next(n for n in _nodes(step) if n.get("Name") == "Run Autofocus")
        af["Status"] = "FAILED"
        out = project_sequence_state(state["Response"])
        self.assertEqual(out["steps"][0]["issues"], ["Run Autofocus: Focuser not connected"])
        self.assertEqual(out["issues"], ["Run Autofocus: Focuser not connected"])

    def test_horizon_from_json_and_graceful_without(self):
        self.assertEqual(self.out["steps"][0]["horizon"]["min_altitude"], 30)
        self.assertIsNone(self.out["steps"][7]["horizon"])
        self.assertIsNone(project_sequence_state(STATE["Response"])["steps"][0]["horizon"])

    def test_empty_payload(self):
        out = project_sequence_state([])
        self.assertEqual(out["steps"], [])


class FinishedRunTests(unittest.TestCase):
    def setUp(self):
        state = json.loads((_F / "sequence_state_finished.json").read_text())
        done = json.loads((_F / "sequence_json_finished.json").read_text())
        self.out = project_sequence_state(state["Response"], done["Response"])

    def test_all_steps_finished_with_full_counts(self):
        self.assertEqual(self.out["completed_steps"], 8)
        self.assertEqual(self.out["status"], "FINISHED")
        self.assertFalse(self.out["running"])
        for step in self.out["steps"]:
            self.assertEqual(step["exposure"]["completed"], step["exposure"]["total"])
            self.assertGreater(step["exposure"]["total"], 0)

    def test_events_fire_once_finished(self):
        first = self.out["steps"][0]["fired_events"]
        self.assertEqual(len(first), 2)
        self.assertTrue(first[0].startswith("ACQUISITION-STARTING"))
        self.assertTrue(first[1].startswith("ACQUISITION-FINISHED"))

    def test_failed_autofocus_is_a_real_issue(self):
        self.assertEqual(
            self.out["steps"][0]["issues"],
            ["Run Autofocus: Camera not connected", "Run Autofocus: Focuser not connected"],
        )
        self.assertEqual(self.out["steps"][1]["issues"], [])


def _nodes(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _nodes(v)
