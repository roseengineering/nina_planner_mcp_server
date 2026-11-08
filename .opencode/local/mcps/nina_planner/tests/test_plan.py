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


class TargetPrefixTest(unittest.TestCase):
    """``$$TARGETNAME$$`` writes this into the path, so the prefix is the
    folder's readable head: ``<target> <intent> [- <label>]``."""

    def test_intent_follows_the_target(self):
        self.assertEqual(_plan().target_prefix(), "M31")
        self.assertEqual(
            _plan(intent="Galaxy portrait").target_prefix(), "M31 Galaxy portrait"
        )

    def test_label_keeps_its_separator_after_the_intent(self):
        self.assertEqual(_plan().target_prefix("core"), "M31 - core")
        self.assertEqual(
            _plan(intent="Galaxy portrait").target_prefix("core"),
            "M31 Galaxy portrait - core",
        )

    def test_empty_parts_leave_no_stray_separator(self):
        # Plans that leave intent unset (the old shape) or a plan with no
        # label must reproduce the pre-intent name verbatim — a leading or
        # doubled separator would be a new folder for the same plan.
        self.assertEqual(_plan().target_prefix(), "M31")
        self.assertEqual(_plan(target="", intent="").target_prefix("core"), "core")
        self.assertEqual(_plan(target="").target_prefix(), "")

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


class QualityConfigTest(unittest.TestCase):
    """The plan's `quality` section: the one home of light-frame thresholds."""

    def test_defaults_to_no_filtering(self):
        quality = _plan().quality
        self.assertIsNone(quality.max_hfr)
        self.assertIsNone(quality.max_hfr_st_dev)
        self.assertIsNone(quality.min_detected_stars)
        self.assertIsNone(quality.max_guiding_rms_arcsec)

    def test_plan_written_before_quality_field_still_loads(self):
        # No `quality` key at all — every plan file written before this
        # feature must load and behave exactly as it did.
        payload = _payload()
        self.assertNotIn("quality", payload)
        plan = ObservationPlan.model_validate(payload)
        self.assertEqual(
            plan.quality.model_dump(),
            {
                "max_hfr": None,
                "max_hfr_st_dev": None,
                "min_detected_stars": None,
                "max_guiding_rms_arcsec": None,
            },
        )

    def test_quality_section_round_trips(self):
        plan = _plan(quality={"max_hfr": 2.5, "max_hfr_st_dev": 0.6})
        self.assertEqual(plan.quality.max_hfr, 2.5)
        self.assertEqual(plan.quality.max_hfr_st_dev, 0.6)
        self.assertIsNone(plan.quality.min_detected_stars)
        # Round-trip through JSON the way write_plan_file -> load_plan does.
        again = ObservationPlan.model_validate(json.loads(plan.model_dump_json()))
        self.assertEqual(again.quality.max_hfr, 2.5)
        self.assertEqual(again.quality.max_hfr_st_dev, 0.6)

    def test_unknown_quality_key_refused(self):
        # extra="forbid": a typo'd threshold must not silently mean "off",
        # or a plan would count frames under rules nobody wrote down.
        with self.assertRaises(ValidationError) as ctx:
            _plan(quality={"max_adu": 100})
        self.assertIn("max_adu", str(ctx.exception))

    def test_quality_value_type_enforced(self):
        with self.assertRaises(ValidationError):
            _plan(quality={"max_hfr": "sharp"})
        with self.assertRaises(ValidationError):
            _plan(quality={"min_detected_stars": 1.5})

    def test_explicit_plan_id_unaffected_by_quality(self):
        # The guarantee real plan files lean on: an explicit id is returned
        # as-is, so adding thresholds to a written plan can never re-derive
        # its id and orphan the frames already attributed to it.
        self.assertEqual(
            _plan(quality={"max_hfr": 2.5})._base_plan_id(),
            _plan()._base_plan_id(),
        )
        self.assertEqual(_plan(quality={"max_hfr": 2.5}).plan_id, "plan-abc123")
