import unittest

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
        "flat": [
            {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 5}
        ],
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
        plan = _plan(
            pointings=[{"ra_hours": 5.0, "dec_deg": 10.0}]
        )
        result = build_sequence_lights(plan, equipment=ObservatoryEquipment())
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()