"""Tests for the audit_plans / list_plans capture-audit tools.

Builders mirror tests/test_progress.py (_plan/_row) but the tools under test
are async and need a filesystem landing root, so every test runs on
IsolatedAsyncioTestCase with ``metadata.resolve_imaging_root`` /
``metadata.read_metadata`` patched onto a tmp tree: the audit must then
reconcile plan ↔ disk ↔ attribution exactly as it does against the live
observatory share.

The plan always expects 20 light / 10 flat / 10 dark / 10 bias, so tests
that only care about ONE type still seed the rest (``seed_full``) — leaving
a type unseeded would (correctly!) surface as MISSING_FILES and pollute the
assertions. ``skip`` is how a test deliberately removes one type.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from nina_planner import metadata
from nina_planner.models.plan import ObservationPlan
from nina_planner.tools import plans as plans_mod

_TOKEN = "M31 _plan-abc123_"


def _row(
    image_type="LIGHT",
    filter_name="LP",
    duration="60.0",
    target: str | None = _TOKEN,
    exposure_start: str | None = None,
):
    if exposure_start is None:
        exposure_start = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {
        "image_type": image_type,
        "filter_name": filter_name,
        "duration": duration,
        "exposure_start_utc": exposure_start,
    }
    if target is not None:
        row["file_path"] = f"C:/NINA/LIGHT/{target}__60.00s_0000.fits"
    return row


def _plan(**overrides):
    data = {
        "plan_id": "plan-abc123",
        "target": "M31",
        "intent": "Galaxy portrait",
        "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27, "label": "M31 core"}],
        "calibration_window_days": 7,
        "light_frames": [
            {"filter_name": "LP", "exposure_time_seconds": 60.0, "total_count": 20}
        ],
        "flat_frames": [{"filter_name": "LP", "total_count": 10}],
        "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 10}],
        "bias_frames": [{"total_count": 10}],
    }
    data.update(overrides)
    return ObservationPlan(**data)


def _call(fn, *args, **kwargs):
    """Invoke a @mcp.tool()-decorated coroutine function directly."""
    target = fn
    for attr in ("fn", "func", "__wrapped__"):
        inner = getattr(target, attr, None)
        if inner is not None:
            target = inner
            break
    return target(*args, **kwargs)


class AuditBase(unittest.IsolatedAsyncioTestCase):
    """Tmp project dir + landing root with metadata patched over."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.proj = base / "project"
        self.root = base / "landing"
        self.proj.mkdir()
        self.root.mkdir()
        self.rows: list[dict] = []

    def write_plan(self, plan: ObservationPlan, name: str = "m31.json") -> Path:
        path = self.proj / name
        path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        return path

    def frame(
        self, token_dir: str, image_type: str, name: str = "f_0000.fits.fz"
    ) -> Path:
        directory = self.root / token_dir / "2026-09-20" / image_type
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(b"")
        return path

    def seed_full(
        self,
        *,
        plan: ObservationPlan | None = None,
        light_rows: int = 20,
        light_files: int = 20,
        light_junk: int = 0,
        exposure_start: str | None = None,
        skip: set[str] | None = None,
    ) -> ObservationPlan:
        """Write the plan plus a complete, self-consistent capture set.

        Everything lands both in the CSV rows and on the landing disk, so the
        default audit verdict is OK everywhere; per-type knobs (counts,
        ``skip``) create exactly one deliberate discrepancy per test.
        """
        plan = plan or _plan()
        self.write_plan(plan)
        skip = skip or set()

        if "light" not in skip:
            good = light_rows - light_junk
            self.rows += [_row(exposure_start=exposure_start) for _ in range(good)]
            self.rows += [
                _row(duration="0.00003", exposure_start=exposure_start)
                for _ in range(light_junk)
            ]
            for i in range(light_files):
                self.frame(_TOKEN, "LIGHT", f"l_{i:04d}.fits.fz")

        calibration = {
            "flat": ("FLAT", "60.0"),
            "dark": ("DARK", "60.0"),
            "bias": ("BIAS", "0.00001"),
        }
        for image_type, (csv_type, duration) in calibration.items():
            if image_type in skip:
                continue
            for i in range(10):
                # Every calibration row carries the plan token, including
                # bias: a real ImageMetaData.csv always has a FilePath, and
                # `load_sequence` stamps $$TARGETNAME$$ = "_{base}_" onto
                # calibration, so frames a plan shot itself are recorded
                # under its own token. Without a path `own_rows` would be 0
                # and every happy-path bias row would read POOL_CREDITED.
                self.rows.append(
                    _row(
                        image_type=csv_type,
                        target=_TOKEN,
                        duration=duration,
                        exposure_start=exposure_start,
                    )
                )
                self.frame(_TOKEN, csv_type, f"{image_type[:3]}_{i:04d}.fits.fz")
        return plan

    def patch_metadata(self):
        root = self.root
        rows = self.rows

        async def _resolve():
            return root

        async def _read(image_type: str | None = None):
            if image_type is None:
                return list(rows)
            up = image_type.upper()
            return [r for r in rows if r.get("image_type") == up]

        return (
            mock.patch.object(metadata, "resolve_imaging_root", _resolve),
            mock.patch.object(metadata, "read_metadata", _read),
        )

    async def audit(self, **kwargs):
        p1, p2 = self.patch_metadata()
        with p1, p2:
            return await _call(plans_mod.audit_plans, **kwargs)

    def row_by(self, result, image_type: str, pointing_index=None):
        matches = [
            r
            for r in result["rows"]
            if r["type"] == image_type and r["pointing_index"] == pointing_index
        ]
        self.assertEqual(len(matches), 1, f"one {image_type} row expected")
        return matches[0]


class AuditTest(AuditBase):
    async def test_all_synced_is_all_saved(self):
        self.seed_full()

        res = await self.audit(project_dir=str(self.proj))

        self.assertTrue(res["all_saved"])
        self.assertEqual(res["left_to_do"], [])
        self.assertEqual(res["blocked"], [])
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["root"], str(self.root))
        light = self.row_by(res, "light")
        self.assertEqual(light["status"], "OK")
        self.assertEqual(light["attributed"], 20)
        self.assertEqual(light["intent"], "Galaxy portrait")  # plan intent surfaced
        self.assertEqual(light["label"], "M31 core")  # pointing label on light rows
        self.assertNotIn("own_rows", light)  # calibration-only field
        dark = self.row_by(res, "dark")
        self.assertNotIn("label", dark)  # calib rows are plan-level: no label
        self.assertEqual(dark["own_rows"], 10)  # this plan shot its own darks
        self.assertEqual(res["totals"]["attributed"], 50)

    async def test_missing_files_reported_as_left_to_do(self):
        # CSV and disk agree on 15 of 20 — frames are genuinely absent.
        self.seed_full(light_rows=15, light_files=15)

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["status"], "MISSING_FILES")
        self.assertEqual(light["remaining"], 5)
        self.assertEqual(len(res["left_to_do"]), 1)
        self.assertEqual(res["left_to_do"][0]["missing"], 5)
        self.assertEqual(res["left_to_do"][0]["intent"], "Galaxy portrait")
        self.assertEqual(res["left_to_do"][0]["label"], "M31 core")
        self.assertIn("load_sequence light", res["left_to_do"][0]["action"])
        self.assertFalse(res["all_saved"])

    async def test_unrecorded_disk_frames_are_missing_files(self):
        # 20 frames exist but the CSV only knows 15 — the Session Metadata
        # plugin crashed before appending five rows. The audit is driven by
        # the CSV now, so it cannot name the divergence; what matters is that
        # it never calls the plan done. The deficit falls through to the
        # safe, self-correcting MISSING_FILES (re-shoot) rather than a
        # verdict that could advise against re-shooting.
        # BAD_-rejected frames do not land here: their CSV row is dropped by
        # the file-exists gate once renamed, and they surface via `bad`.
        self.seed_full(light_rows=15, light_files=20)

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["status"], "MISSING_FILES")
        self.assertEqual(light["attributed"], 15)
        self.assertEqual(light["remaining"], 5)
        self.assertEqual(len(res["left_to_do"]), 1)
        self.assertEqual(res["left_to_do"][0]["missing"], 5)
        self.assertFalse(res["all_saved"])

    async def test_bad_rename_reported_as_bad_not_attributed(self):
        # N.I.N.A. rejects a frame by prefixing its filename with BAD_
        # (verified against the live landing dir). The CSV row still points
        # at the pre-rename name, so the file-exists gate drops it and the
        # frame never reaches `attributed`. The light row's `bad` count is
        # the only place that names why: 19 good + 1 rejected file, 19 CSV
        # rows, one genuine gap.
        self.seed_full(light_rows=19, light_files=19)
        self.frame(_TOKEN, "LIGHT", "BAD_l_0019.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["bad"], 1)
        self.assertEqual(light["attributed"], 19)
        self.assertEqual(light["remaining"], 1)  # the rejected frame is a gap
        self.assertEqual(light["status"], "MISSING_FILES")
        self.assertFalse(res["all_saved"])

    async def test_pooled_bias_credits_from_another_dir(self):
        # Bias shot under a DIFFERENT plan's token dir, credited plan-wide:
        # attributed reaches expected from the pool while this plan's own
        # recorded bias frames (own_rows) stay at zero.
        self.seed_full(skip={"bias"})
        self.rows += [
            _row(
                image_type="BIAS",
                target="Other _plan-999999_",
                duration="0.00001",
            )
            for _ in range(10)
        ]
        # ...and it lives in the pool dir on disk, not ours:
        for i in range(10):
            self.frame("Other _plan-999999_", "BIAS", f"b_{i:04d}.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        bias = self.row_by(res, "bias")
        self.assertEqual(bias["status"], "POOL_CREDITED")
        self.assertEqual(bias["own_rows"], 0)  # not recorded under OUR token
        self.assertEqual(bias["attributed"], 10)  # but credited plan-wide
        self.assertEqual(res["left_to_do"], [])
        self.assertTrue(res["all_saved"])  # pool is legitimate, not a gap

    async def test_pooled_dark_credits_from_another_plan(self):
        # Darks pool plan-wide by type + EXPOSURE + window, never by token —
        # this is the case bias cannot cover, since bias matches on type
        # alone and so cannot fail on exposure. The plan asks for 60.0s
        # darks; another plan's 60.0s darks satisfy it, and OUR token then
        # holds no dark rows at all, which is what POOL_CREDITED says.
        self.seed_full(skip={"dark"})
        self.rows += [
            _row(image_type="DARK", target="Other _plan-999999_", duration="60.0")
            for _ in range(10)
        ]
        for i in range(10):
            self.frame("Other _plan-999999_", "DARK", f"d_{i:04d}.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        dark = self.row_by(res, "dark")
        self.assertEqual(dark["status"], "POOL_CREDITED")
        self.assertEqual(dark["own_rows"], 0)  # not recorded under OUR token
        self.assertEqual(dark["attributed"], 10)  # credited from the pool
        self.assertEqual(dark["remaining"], 0)
        self.assertEqual(res["left_to_do"], [])
        self.assertTrue(res["all_saved"])

    async def test_pooled_dark_wrong_exposure_does_not_credit(self):
        # The exposure gate that bias cannot exercise: the same 10 pooled
        # darks at 300.0s against a plan asking for 60.0s are a different
        # calibration, so nothing is credited and the deficit is real.
        # own_rows stays 0 either way — it is the absence of OUR rows, not
        # the presence of someone else's, that POOL_CREDITED keys on.
        self.seed_full(skip={"dark"})
        self.rows += [
            _row(image_type="DARK", target="Other _plan-999999_", duration="300.0")
            for _ in range(10)
        ]
        for i in range(10):
            self.frame("Other _plan-999999_", "DARK", f"d_{i:04d}.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        dark = self.row_by(res, "dark")
        self.assertEqual(dark["status"], "MISSING_FILES")
        self.assertEqual(dark["attributed"], 0)  # exposure mismatch: no credit
        self.assertEqual(dark["remaining"], 10)
        self.assertEqual(dark["own_rows"], 0)
        self.assertEqual(len(res["left_to_do"]), 1)
        self.assertIn("load_sequence dark", res["left_to_do"][0]["action"])
        self.assertFalse(res["all_saved"])

    async def test_own_dark_rows_counted_separately_from_pooled(self):
        # The middle case the two tests above leave open: 4 of our own darks
        # recorded under our own token, 6 pooled from another plan. own_rows
        # must count ONLY ours (4, not 10) — token containment plus the
        # row's own image_type, with no pooled matcher involved — and since
        # 4 < expected the row stays POOL_CREDITED: the pool is still what
        # carries the plan to 10/10.
        self.seed_full(skip={"dark"})
        for i in range(6):
            self.rows.append(
                _row(image_type="DARK", target="Other _plan-999999_", duration="60.0")
            )
            self.frame("Other _plan-999999_", "DARK", f"d_{i:04d}.fits.fz")
        for i in range(4):
            self.rows.append(_row(image_type="DARK", target=_TOKEN, duration="60.0"))
            self.frame(_TOKEN, "DARK", f"own_{i:04d}.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        dark = self.row_by(res, "dark")
        self.assertEqual(dark["own_rows"], 4)  # only ours count as ours
        self.assertEqual(dark["attributed"], 10)  # 6 pooled + 4 own
        self.assertEqual(dark["remaining"], 0)
        self.assertEqual(dark["status"], "POOL_CREDITED")  # 4 < 10 still short
        self.assertTrue(res["all_saved"])

    async def test_expired_window_surfaced_despite_remaining_zero(self):
        # Session anchored 10 days ago (lights set the anchor) → the 7-day
        # window closed 3 days ago; darks shot TODAY fall outside it, so the
        # deficit can never be credited again.
        old = datetime.now(UTC).replace(day=1).strftime("%Y-%m-%dT%H:%M:%SZ")
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.seed_full(exposure_start=old, skip={"dark"})
        self.rows += [
            _row(image_type="DARK", target=None, exposure_start=now) for _ in range(10)
        ]

        res = await self.audit(project_dir=str(self.proj))

        dark = self.row_by(res, "dark")
        # The window quirk: remaining reports 0 even though a deficit exists —
        # the audit must still flag it instead of calling it done.
        self.assertEqual(dark["remaining"], 0)
        self.assertEqual(dark["attributed"], 0)
        self.assertFalse(dark["window_open"])
        self.assertEqual(dark["status"], "EXPIRED")
        self.assertEqual(len(res["blocked"]), 1)
        self.assertEqual(res["left_to_do"], [])
        self.assertFalse(res["all_saved"])

    async def test_junk_lights_count_but_junk_darks_force_reshoot(self):
        # Lights: seed 20 good + 2 junk (22 rows/files) — the ±0.05s
        # exposure matcher excludes the junk from attribution, so the good
        # set completes attribution (20/20) while junk rides alongside in
        # our token dirs → SUSPECT_JUNK.
        # Darks: junk is excluded the same way, but no good surplus exists,
        # leaving a genuine 2-frame deficit → re-shoot with junk as cause.
        self.seed_full(light_junk=2, light_rows=22, light_files=22, skip={"dark"})
        self.rows += [
            _row(image_type="DARK", duration=("0.00003" if i < 2 else "60.0"))
            for i in range(10)
        ]
        for i in range(10):
            self.frame(_TOKEN, "DARK", f"dk_{i:04d}.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["junk"], 2)
        self.assertEqual(light["attributed"], 20)  # the good set completes it
        self.assertEqual(light["remaining"], 0)
        self.assertEqual(light["status"], "SUSPECT_JUNK")
        dark = self.row_by(res, "dark")
        self.assertEqual(dark["junk"], 2)
        self.assertEqual(dark["attributed"], 8)  # junk excluded by exposure
        self.assertEqual(dark["status"], "MISSING_FILES")
        self.assertEqual(res["left_to_do"][0]["type"], "dark")
        self.assertEqual(res["left_to_do"][0]["missing"], 2)
        bias = self.row_by(res, "bias")
        self.assertEqual(bias["junk"], 0)  # 0s is correct for bias
        self.assertEqual(bias["status"], "OK")
        self.assertFalse(res["all_saved"])

    async def test_full_mode_overshoot_is_harmless(self):
        self.seed_full(light_rows=25, light_files=25)

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["status"], "OVERSHOT")
        self.assertEqual(light["attributed"], 25)
        self.assertTrue(res["all_saved"])  # extras are not a defect
        self.assertEqual(res["left_to_do"], [])

    async def test_orphan_token_dir_reported(self):
        self.seed_full()
        # Frames from a plan file that no longer exists (the Oct-19 incident).
        for i in range(3):
            self.frame("Ghost _plan-deadbeef_", "LIGHT", f"g_{i}.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(len(res["orphans"]), 1)
        self.assertIn("_plan-deadbeef_", res["orphans"][0]["dir"])
        self.assertEqual(res["orphans"][0]["frames"], 3)
        self.assertTrue(res["all_saved"])  # orphans are informational

    async def test_frames_in_non_type_dir_still_count(self):
        # Regression: `_count_fits` used to skip any file whose parent
        # directory was not literally LIGHT/DARK/BIAS/FLAT, so frames landing
        # under any other folder name vanished from the count and the audit
        # read as "no fits images" for a directory that plainly held them.
        # The parent name is now only a bucket label, never a gate.
        self.seed_full()
        # Orphan frames in a folder named after a frame type nobody asks for:
        odd = self.root / "Ghost _plan-deadbeef_" / "2026-09-20" / "LIGHTS"
        odd.mkdir(parents=True)
        for i in range(3):
            (odd / f"o_{i}.fits.fz").write_bytes(b"")
        # ...and the same shape inside a claimed token dir: sidecar files are
        # still excluded by suffix, real frames are not.
        stray = self.root / _TOKEN / "unsorted"
        stray.mkdir(parents=True)
        (stray / "stray_0000.fits.fz").write_bytes(b"")
        (stray / "ImageMetaData.csv").write_text("FilePath\n", encoding="utf-8")

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(len(res["orphans"]), 1)
        self.assertEqual(res["orphans"][0]["frames"], 3)
        self.assertEqual(res["orphans"][0]["types"], {"lights": 3})
        # The stray frame is counted somewhere, but never as an attributed
        # light — bucket labels alone must not manufacture attribution.
        light = self.row_by(res, "light")
        self.assertEqual(light["attributed"], 20)
        self.assertTrue(res["all_saved"])

    async def test_dead_mount_raises_never_empty_counts(self):
        self.seed_full()

        async def _resolve():
            return self.root

        async def _boom(image_type: str | None = None):
            raise FileNotFoundError(
                "imaging root does not exist or is not a directory: "
                "(check the site profile's image save path and, on "
                "non-Windows hosts, NINA_DRIVE_MOUNT)"
            )

        with (
            mock.patch.object(metadata, "resolve_imaging_root", _resolve),
            mock.patch.object(metadata, "read_metadata", _boom),
        ):
            with self.assertRaises(FileNotFoundError) as ctx:
                await _call(plans_mod.audit_plans, project_dir=str(self.proj))
        self.assertIn("NINA_DRIVE_MOUNT", str(ctx.exception))

    async def test_types_filter_and_mosaic_panes(self):
        plan = _plan(
            plan_id="plan-abc123",
            pointings=[
                {"ra_hours": 0.71, "dec_deg": 41.27, "label": "Pane 1"},
                {"ra_hours": 0.72, "dec_deg": 41.28, "label": "Pane 2"},
            ],
        )
        # Per-pane light rows + pane dirs on disk.
        self.rows = [
            _row(target="M31 _plan-abc123-1_"),
            _row(target="M31 _plan-abc123-1_"),
            _row(target="M31 _plan-abc123-2_"),
        ]
        self.frame("M31 _plan-abc123-1_", "LIGHT", "p1.fits.fz")
        self.frame("M31 _plan-abc123-1_", "LIGHT", "p1b.fits.fz")
        self.frame("M31 _plan-abc123-2_", "LIGHT", "p2.fits.fz")
        self.write_plan(plan, name="mosaic.json")

        res = await self.audit(project_dir=str(self.proj))
        pane1 = self.row_by(res, "light", pointing_index=1)
        pane2 = self.row_by(res, "light", pointing_index=2)
        # Per-pane attribution: each pane's frames carry their own -N token.
        self.assertEqual(pane1["attributed"], 2)
        self.assertEqual(pane1["effective_plan_id"], "plan-abc123-1")
        self.assertEqual(pane1["label"], "Pane 1")  # mosaic pane label surfaces
        self.assertEqual(pane2["attributed"], 1)
        self.assertEqual(pane2["label"], "Pane 2")
        # types filter: only the requested type survives
        filtered = await self.audit(project_dir=str(self.proj), types=["bias"])
        self.assertEqual({r["type"] for r in filtered["rows"]}, {"bias"})

    async def test_no_plan_files_warns(self):
        res = await self.audit(project_dir=str(self.proj))
        self.assertIn("warning", res)
        self.assertEqual(res["rows"], [])
        self.assertEqual(res["never_shot"], [])
        self.assertTrue(res["all_saved"])  # nothing exists, so nothing is owed
        self.assertIsNone(res["root"])  # root never resolved for an empty set

    async def test_unreferenced_broken_file_is_dropped_not_error(self):
        # Left join: a file the CSV never references is nobody's problem —
        # junk/unparseable JSON (config files, "{ not json") must not break
        # the verdict or surface as a plan error. The good plan still audits.
        self.seed_full()
        (self.proj / "broken.json").write_text("{ not json", encoding="utf-8")

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["never_shot"], [])  # junk JSON never loads as a plan
        self.assertEqual(res["totals"]["plans"], 1)  # the good one audited
        self.assertTrue(res["all_saved"])  # unreferenced breakage is not our verdict

    async def test_referenced_broken_plan_file_is_error(self):
        # Frames in the CSV reference plan-broken1, and its file fails to
        # load with no loadable twin → those frames cannot be audited.
        # (Reported ONCE as the load failure, not double-counted as a gap.)
        self.seed_full()
        (self.proj / "broken.json").write_text(
            '{"plan_id": "plan-broken1", "target": "M42"}', encoding="utf-8"
        )
        self.rows += [_row(target="M42 _plan-broken1_") for _ in range(2)]

        res = await self.audit(project_dir=str(self.proj))

        broken = [e for e in res["errors"] if e["file"] == "broken.json"]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]["plan_id"], "plan-broken1")
        # no duplicate gap entry for the same frames
        self.assertEqual(
            len([e for e in res["errors"] if e["plan_id"] == "plan-broken1"]), 1
        )
        self.assertFalse(res["all_saved"])  # referenced load failure breaks the verdict

    async def test_corrupt_duplicate_with_valid_twin_is_not_error(self):
        # A corrupt file whose id a valid plan file still serves: the
        # frames are fully auditable → not an error, verdict unaffected.
        self.seed_full()
        (self.proj / "broken.json").write_text(
            '{"plan_id": "plan-abc123", "target": "M31"}', encoding="utf-8"
        )

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["totals"]["plans"], 1)
        self.assertTrue(res["all_saved"])

    async def test_light_frames_without_plan_file_are_a_gap_error(self):
        # Left side of the join: the CSV references plan-deadbeef but no
        # plan file claims it (deleted plan). Frames exist → error, and no
        # phantom row can be fabricated for it (expected is unknowable).
        self.seed_full()
        self.rows += [_row(target="Ghost _plan-deadbeef_") for _ in range(3)]

        res = await self.audit(project_dir=str(self.proj))

        gaps = [e for e in res["errors"] if e["plan_id"] == "plan-deadbeef"]
        self.assertEqual(len(gaps), 1)
        self.assertIn("3 light frames", gaps[0]["reason"])
        self.assertFalse(res["all_saved"])
        self.assertEqual(res["totals"]["plans"], 1)  # the good plan still audited

    async def test_never_shot_plan_reported_not_audited(self):
        # Plan file loads fine but the CSV never mentions it → NOT audited
        # (no rows: expected/attributed means nothing for frames
        # never attempted) and not an error — but it must surface. Hiding it
        # is the Oct-24 blind spot: the audit showed two 1-frame deficits
        # while six whole unshot plans (240 frames) waited, found only by
        # diffing list_plans by hand.
        self.seed_full()
        self.write_plan(
            _plan(
                plan_id="plan-fresh1",
                target="M99",
                pointings=[{"ra_hours": 12.3, "dec_deg": 17.8, "label": "M99 center"}],
            ),
            name="fresh.json",
        )

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["totals"]["plans"], 1)  # fresh.json not audited
        self.assertNotIn("M99", {r["target"] for r in res["rows"]})
        self.assertFalse(res["all_saved"])  # 20 frames of it do not exist
        self.assertEqual(res["totals"]["never_shot"], 1)
        entry = res["never_shot"][0]
        self.assertEqual(entry["plan_id"], "plan-fresh1")
        self.assertEqual(entry["target"], "M99")
        self.assertEqual(entry["intent"], "Galaxy portrait")
        self.assertEqual(entry["file"], "fresh.json")
        self.assertEqual(entry["pointing_count"], 1)
        self.assertEqual(entry["light_expected"], 20)
        self.assertIsNotNone(entry["written"])
        # ...and it is in the work list, as the whole light ask.
        todo = [t for t in res["left_to_do"] if t["reason"] == "NEVER_SHOT"]
        self.assertEqual(len(todo), 1)
        self.assertEqual(todo[0]["missing"], 20)
        self.assertEqual(todo[0]["label"], "M99 center")
        self.assertIn("load_sequence light", todo[0]["action"])

    async def test_never_shot_mosaic_reports_each_pane(self):
        # A mosaic is acquired pane by pane, so its NEVER_SHOT work list is
        # per-pane (missing = that pane's ask) while never_shot stays one
        # plan-level entry carrying the total.
        self.seed_full()
        self.write_plan(
            _plan(
                plan_id="plan-mosaic1",
                target="M33",
                pointings=[
                    {"ra_hours": 1.0, "dec_deg": 30.6, "label": "r1_c1"},
                    {"ra_hours": 1.1, "dec_deg": 30.7, "label": "r1_c2"},
                ],
            ),
            name="m33_mosaic.json",
        )

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(len(res["never_shot"]), 1)
        entry = res["never_shot"][0]
        self.assertEqual(entry["pointing_count"], 2)
        self.assertEqual(entry["light_expected"], 40)
        panes = [t for t in res["left_to_do"] if t["reason"] == "NEVER_SHOT"]
        self.assertEqual([p["pointing_index"] for p in panes], [1, 2])
        self.assertEqual([p["missing"] for p in panes], [20, 20])
        self.assertEqual([p["label"] for p in panes], ["r1_c1", "r1_c2"])

    async def test_never_shot_deduped_by_plan_id(self):
        # Two files carry the same plan id (live plan + archived copy),
        # neither shot: one report, from the shallowest file — not two.
        self.seed_full()
        self.write_plan(_plan(plan_id="plan-fresh1", target="M99"), name="fresh.json")
        (self.proj / "archive").mkdir()
        self.write_plan(
            _plan(plan_id="plan-fresh1", target="M99"),
            name="archive/fresh_copy.json",
        )

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual([e["plan_id"] for e in res["never_shot"]], ["plan-fresh1"])
        self.assertEqual(res["never_shot"][0]["file"], "fresh.json")
        self.assertEqual(
            len([t for t in res["left_to_do"] if t["reason"] == "NEVER_SHOT"]), 1
        )

    async def test_all_plans_unshot_still_reported(self):
        # Nothing referenced at all: no rows, but the early-return branch
        # reports the same never_shot/left_to_do — an all-unshot project is
        # 100% outstanding work, not an empty audit.
        self.write_plan(_plan(target="M31"), name="m31.json")

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(res["rows"], [])
        self.assertEqual(len(res["never_shot"]), 1)
        self.assertEqual(len(res["left_to_do"]), 1)
        self.assertEqual(res["left_to_do"][0]["reason"], "NEVER_SHOT")
        self.assertEqual(res["left_to_do"][0]["missing"], 20)
        self.assertFalse(res["all_saved"])
        self.assertIn("unshot", res["warning"])

    async def test_explicit_unshot_plan_audits_as_missing_files(self):
        # Naming a file IS the request to audit it: an unshot plan named
        # explicitly becomes an ordinary MISSING_FILES row, never a
        # never_shot entry (which would be the same work, reported twice).
        self.seed_full()
        self.write_plan(_plan(plan_id="plan-fresh1", target="M99"), name="fresh.json")

        res = await self.audit(plans=["fresh.json"], project_dir=str(self.proj))

        self.assertEqual(res["never_shot"], [])
        light = self.row_by(res, "light")
        self.assertEqual(light["target"], "M99")
        self.assertEqual(light["status"], "MISSING_FILES")
        self.assertEqual(light["remaining"], 20)
        self.assertEqual({t["reason"] for t in res["left_to_do"]}, {"MISSING_FILES"})

    async def test_nested_plan_file_is_discovered(self):
        # Discovery is recursive: a plan in a subfolder joins the audit the
        # same way a flat one does (CSV references it → audited).
        (self.proj / "archive").mkdir()
        self.write_plan(
            _plan(plan_id="plan-nest1", target="M51"), name="archive/m51.json"
        )
        self.rows += [_row(target="M51 _plan-nest1_") for _ in range(3)]

        res = await self.audit(project_dir=str(self.proj))

        self.assertIn("M51", {r["target"] for r in res["rows"]})
        self.assertEqual(res["totals"]["plans"], 1)
        self.assertEqual(res["errors"], [])

    async def test_shallowest_duplicate_id_wins(self):
        # A nested archived copy shares the live plan's id: the top-level
        # file must define the audit (20 lights), not the archive (99), and
        # the pair must not be double-audited.
        self.seed_full()
        dup = _plan(
            plan_id="plan-abc123",
            target="M31",
            light_frames=[
                {"filter_name": "LP", "exposure_time_seconds": 60.0, "total_count": 99}
            ],
        )
        (self.proj / "archive").mkdir()
        self.write_plan(dup, name="archive/m31_copy.json")

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["expected"], 20)  # top-level definition wins
        self.assertEqual(res["totals"]["plans"], 1)  # not double-audited

    async def test_duplicate_id_twin_is_not_never_shot(self):
        # The mirror of the test above: a rewritten plan beside its
        # original shares the id, and only the shallowest file claims the
        # rows — but the frames DO exist under that shared id, so the twin
        # must not surface as an unshot 20-frame plan (one target's work,
        # counted twice). Live case: NGC 1931's Oct-24 drift-threshold
        # rewrite beside its Oct-23 original.
        self.seed_full()
        self.write_plan(
            _plan(
                plan_id="plan-abc123",
                target="M31",
                light_frames=[
                    {
                        "filter_name": "LP",
                        "exposure_time_seconds": 60.0,
                        "total_count": 60,
                    }
                ],
            ),
            name="m31_rewrite.json",
        )

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(res["never_shot"], [])
        self.assertEqual(
            [t for t in res["left_to_do"] if t["reason"] == "NEVER_SHOT"], []
        )
        self.assertTrue(res["all_saved"])

    async def test_hidden_dirs_never_count_nor_orphan(self):
        # The landing root and its token dirs are walked with the hidden-dir
        # prune: a share's .snapshot/.Trashes copies must neither reach the
        # counts nor masquerade as orphaned frames. The copies are BAD_
        # prefixed so `bad` is what proves the token-dir prune — with the
        # disk-vs-CSV cross-check gone, `bad` is the only field fed by the
        # walk of this plan's own files.
        self.seed_full()  # 20 lights on disk
        # BAD_-prefixed copy inside the token dir -> must NOT reach `bad`
        snap = self.root / _TOKEN / ".snapshot" / "2026-09-20" / "LIGHT"
        snap.mkdir(parents=True)
        (snap / "BAD_copy_0000.fits.fz").write_bytes(b"")
        # trash at the landing root -> must NOT surface as an orphan
        trash = self.root / ".Trashes" / "2026-09-20" / "LIGHT"
        trash.mkdir(parents=True)
        (trash / "t_0000.fits.fz").write_bytes(b"")

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["attributed"], 20)  # snapshot copy unattributed
        self.assertEqual(light["bad"], 0)  # BAD_ copy inside .snapshot pruned
        self.assertEqual(res["orphans"], [])  # .Trashes invisible
        self.assertTrue(res["all_saved"])


class ListPlansTest(AuditBase):
    async def test_lists_and_reports_broken_files(self):
        self.write_plan(_plan(target="M31", intent="Galaxy"), name="m31.json")
        self.write_plan(
            _plan(plan_id="plan-555555", target="M42", intent="Nebula"),
            name="m42.json",
        )
        (self.proj / "junk.json").write_text("nope", encoding="utf-8")

        p1, p2 = self.patch_metadata()
        with p1, p2:
            res = await _call(plans_mod.list_plans, project_dir=str(self.proj))

        self.assertEqual(len(res["plans"]), 2)
        self.assertEqual(len(res["errors"]), 1)
        names = {entry["file"] for entry in res["plans"]}
        self.assertEqual(names, {"m31.json", "m42.json"})
        entry = next(e for e in res["plans"] if e["file"] == "m42.json")
        self.assertEqual(entry["plan_id"], "plan-555555")
        self.assertEqual(entry["target"], "M42")
        self.assertEqual(entry["intent"], "Nebula")
        self.assertEqual(entry["pointing_count"], 1)
        self.assertIsNotNone(entry["written"])

    async def test_pruned_dirs_and_shallowest_order(self):
        # node_modules / hidden dirs are never searched (the project dir
        # holds the agent's own installation: hundreds of package JSONs),
        # nested plans are found, and top-level files come first.
        self.write_plan(_plan(target="M31", intent="Galaxy"), name="m31.json")
        (self.proj / "archive").mkdir()
        self.write_plan(
            _plan(plan_id="plan-555555", target="M42", intent="Nebula"),
            name="archive/m42.json",
        )
        for junk_dir in ("node_modules", "__pycache__", ".opencode"):
            d = self.proj / junk_dir
            d.mkdir()
            (d / "package.json").write_text('{"name": "junk"}', encoding="utf-8")

        p1, p2 = self.patch_metadata()
        with p1, p2:
            res = await _call(plans_mod.list_plans, project_dir=str(self.proj))

        self.assertEqual(
            [e["file"] for e in res["plans"]], ["m31.json", "m42.json"]
        )  # shallowest first; pruned junk absent
        self.assertEqual(res["errors"], [])  # pruned files never load, so never error


if __name__ == "__main__":
    unittest.main()
