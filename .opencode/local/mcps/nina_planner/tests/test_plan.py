import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from nina_planner.models.plan import ObservationPlan
from nina_planner.plan_utils import load_plan


def _payload(**overrides):
    """A minimal valid plan payload; ObservationPlan requires every frame list."""
    data = {
        "plan_id": "plan-abc123",
        "target": "M31",
        "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27}],
        "light_frames": [
            {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 1}
        ],
        "flat_frames": [{"filter_name": "L", "total_count": 1}],
        "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 1}],
        "bias_frames": [{"total_count": 1}],
    }
    data.update(overrides)
    return data


def _plan(**overrides):
    return ObservationPlan.model_validate(_payload(**overrides))


class PlanIdCharsetTest(unittest.TestCase):
    """plan_id must survive N.I.N.A.'s path sanitisation verbatim and must not
    contain the '_' that delimits the attribution token."""

    def test_accepted_charset(self):
        for value in (
            "plan-abc123",
            "plan-a745ffa2357a",  # the shape a real derived id has
            "M31.center",
            "2026plan",
            "plan-A1.b2-c3",
        ):
            with self.subTest(plan_id=value):
                self.assertEqual(_plan(plan_id=value).plan_id, value)

    def test_empty_id_allowed_and_the_derived_id_passes_too(self):
        # Empty means "derive one on write"; the id that derivation produces
        # has to be acceptable, or write_plan_file would hand back a file that
        # its own load path then refuses.
        plan = _plan(plan_id="")
        derived = plan._base_plan_id()
        self.assertTrue(derived.startswith("plan-"))
        self.assertEqual(_plan(plan_id=derived).plan_id, derived)

    def test_underscore_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            _plan(plan_id="m31_lrgb")
        msg = str(ctx.exception)
        self.assertIn("plan_id", msg)
        self.assertIn("m31_lrgb", msg)

    def test_characters_nina_may_rewrite_are_rejected(self):
        # Brackets are the motivating case (N.I.N.A. turned them into "_" on
        # disk even though NTFS allows them); the rest are refused on the same
        # unproven-charset grounds, "_" excepted for the delimiter reason.
        for value in ("plan[1]", "(plan-1)", "plan:x", "plan/1", "plan 1", "plün"):
            with self.subTest(plan_id=value):
                with self.assertRaises(ValidationError):
                    _plan(plan_id=value)

    def test_error_explains_both_reasons(self):
        with self.assertRaises(ValidationError) as ctx:
            _plan(plan_id="m31_lrgb")
        msg = str(ctx.exception)
        self.assertIn("delimiter", msg)  # why "_" specifically is refused
        self.assertIn("N.I.N.A.", msg)  # why the rest of the charset is

    def test_token_still_built_from_a_valid_id(self):
        plan = _plan(plan_id="m31.lrgb")
        self.assertEqual(plan.attribution_token(1), "_m31.lrgb_")


class FlatExposurePlanTest(unittest.TestCase):
    """Flats carry no exposure time: N.I.N.A.'s SkyFlat chooses it."""

    def test_flat_spec_is_filter_and_count_only(self):
        flat = _plan().flat_frames[0]
        self.assertFalse(hasattr(flat, "exposure_time_seconds"))
        self.assertEqual(flat.filter_name, "L")
        self.assertEqual(flat.total_count, 1)

    def test_stale_exposure_time_in_a_flat_entry_is_refused(self):
        # A flat entry is filter + count only, so an exposure time on one is
        # a nonconforming plan: it is refused at load rather than dropped,
        # which would let a plan mean something other than what it says.
        with self.assertRaises(ValidationError) as ctx:
            _plan(
                flat_frames=[
                    {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 1}
                ]
            )
        msg = str(ctx.exception)
        self.assertIn("flat_frames", msg)
        self.assertIn("exposure_time_seconds", msg)
        self.assertIn("Extra inputs are not permitted", msg)


class UnknownFieldTest(unittest.TestCase):
    """Every part of a plan file is strict: a key outside the schema is a
    nonconforming plan (typo or removed field), not something to drop."""

    def test_unknown_top_level_field_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            _plan(expossure_window_days=7)
        self.assertIn("expossure_window_days", str(ctx.exception))

    def test_unknown_field_in_a_nested_config_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            _plan(guiding={"dither_every_n_exposures": 2, "nax_drift_arcmin": 3.0})
        self.assertIn("nax_drift_arcmin", str(ctx.exception))

    def test_unknown_field_in_a_frame_spec_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            _plan(dark_frames=[{"exposure_time_seconds": 60.0, "count": 5}])
        self.assertIn("count", str(ctx.exception))

    def test_unknown_field_in_a_pointing_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            _plan(pointings=[{"ra_hours": 0.71, "dec_deg": 41.27, "ra_deg": 10.65}])
        self.assertIn("ra_deg", str(ctx.exception))


class LoadPlanPlanIdTest(unittest.IsolatedAsyncioTestCase):
    """Both entry points enforce the rule: write (model construction) and load."""

    async def test_load_plan_refuses_bad_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(_payload(plan_id="m31_lrgb")), encoding="utf-8")
            with self.assertRaises(ValidationError) as ctx:
                await load_plan(str(path))
        self.assertIn("plan_id", str(ctx.exception))

    async def test_load_plan_accepts_recorded_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(_payload()), encoding="utf-8")
            plan = await load_plan(str(path))
        self.assertEqual(plan.plan_id, "plan-abc123")
