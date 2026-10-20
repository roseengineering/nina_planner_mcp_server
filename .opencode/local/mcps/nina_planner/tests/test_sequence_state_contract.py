"""Guards the assumptions ``project_sequence_state`` makes about generated sequences.

The projection reads NINA's ``/sequence/state`` output, which the fake cannot
simulate, so these assert the *generator* still emits the structure that
output is derived from. If one fails, update the projection and refresh the
``sequence_*_unrun.json`` fixtures from a real NINA.
"""

import re
import unittest

from sequence_helpers import build_sequence_darks, build_sequence_lights
from test_sequence import _plan, _profile

from nina_planner.models.observatory import ObservatoryEquipment


def _nodes(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _nodes(v)


def _types(node):
    return {
        n["$type"].split(",")[0].split(".")[-1]
        for n in _nodes(node)
        if isinstance(n.get("$type"), str)
    }


def _target_steps(sequence):
    area = next(n for n in _nodes(sequence) if n.get("Name") == "Target Sequence")
    return area["Items"]["$values"]


class GeneratedSequenceContract(unittest.TestCase):
    def _build(self, fn):
        return fn(_plan(), ObservatoryEquipment(), _profile())

    def test_every_target_child_is_a_step_with_attribution_token(self):
        for fn in (build_sequence_lights, build_sequence_darks):
            steps = _target_steps(self._build(fn))
            self.assertTrue(steps)
            for step in steps:
                name = step["Target"]["TargetName"]
                self.assertRegex(name, r"_(plan-[A-Za-z0-9]+)(?:-(\d+))?_")

    def test_light_step_has_what_the_summary_reads(self):
        (step,) = _target_steps(self._build(build_sequence_lights))
        self.assertLessEqual(
            {"SmartExposure", "WaitForAltitude", "SendEventInstruction"}, _types(step)
        )

    def test_dark_step_has_smart_exposure(self):
        (step,) = _target_steps(self._build(build_sequence_darks))
        self.assertIn("SmartExposure", _types(step))
        self.assertIn("SendEventInstruction", _types(step))
