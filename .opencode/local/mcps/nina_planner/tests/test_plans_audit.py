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
        "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27}],
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
                self.rows.append(
                    _row(
                        image_type=csv_type,
                        target=(None if image_type == "bias" else _TOKEN),
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
        self.assertEqual(light["on_disk"], 20)
        self.assertEqual(light["attributed"], 20)
        self.assertEqual(res["totals"]["on_disk"], 50)

    async def test_missing_files_reported_as_left_to_do(self):
        # CSV and disk agree on 15 of 20 — frames are genuinely absent.
        self.seed_full(light_rows=15, light_files=15)

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["status"], "MISSING_FILES")
        self.assertEqual(light["remaining"], 5)
        self.assertEqual(len(res["left_to_do"]), 1)
        self.assertEqual(res["left_to_do"][0]["missing"], 5)
        self.assertIn("load_sequence light", res["left_to_do"][0]["action"])
        self.assertFalse(res["all_saved"])

    async def test_unattributed_disk_frames_are_missing_files(self):
        # 20 frames on disk but the CSV only knows 15 — the Session Metadata
        # plugin crashed before appending their rows. Disk and CSV disagree
        # and nothing in the data says which frame is unrecorded, so the
        # deficit falls through to the safe, self-correcting MISSING_FILES
        # (re-shoot) rather than a verdict that could advise against it.
        # BAD_-rejected frames do not land here: they are excluded from
        # on_disk and attributed alike, and surface via `bad` instead.
        self.seed_full(light_rows=15, light_files=20)

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["status"], "MISSING_FILES")
        self.assertEqual(light["on_disk"], 20)
        self.assertEqual(light["attributed"], 15)
        self.assertEqual(light["remaining"], 5)
        self.assertEqual(len(res["left_to_do"]), 1)
        self.assertEqual(res["left_to_do"][0]["missing"], 5)
        self.assertFalse(res["all_saved"])

    async def test_bad_rename_is_counted_not_on_disk(self):
        # N.I.N.A. rejects a frame by prefixing its filename with BAD_
        # (verified against the live landing dir). The CSV row still points at
        # the pre-rename name (so it is not attributed), and the file must NOT
        # count toward on_disk either — otherwise the reject masquerades as a
        # disk-vs-CSV divergence. It is surfaced as the light row's `bad`
        # count instead.
        # 19 good + 1 BAD_-prefixed file, but only 19 CSV rows (the bad
        # frame's row is dropped by the file-exists gate once renamed).
        self.seed_full(light_rows=19, light_files=19)
        self.frame(_TOKEN, "LIGHT", "BAD_l_0019.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        light = self.row_by(res, "light")
        self.assertEqual(light["bad"], 1)
        self.assertEqual(light["on_disk"], 19)  # the BAD_ file is excluded
        self.assertEqual(light["attributed"], 19)
        self.assertEqual(light["remaining"], 1)  # the rejected frame is a gap
        self.assertEqual(light["status"], "MISSING_FILES")
        self.assertFalse(res["all_saved"])

    async def test_pooled_bias_credits_from_another_dir(self):
        # Bias shot under a DIFFERENT plan's token dir, credited plan-wide.
        self.seed_full(skip={"bias"})
        self.rows += [
            _row(image_type="BIAS", target=None, duration="0.00001") for _ in range(10)
        ]
        # ...and it lives in the pool dir on disk, not ours:
        for i in range(10):
            self.frame("Other _plan-999999_", "BIAS", f"b_{i:04d}.fits.fz")

        res = await self.audit(project_dir=str(self.proj))

        bias = self.row_by(res, "bias")
        self.assertEqual(bias["status"], "POOL_CREDITED")
        self.assertEqual(bias["on_disk"], 0)  # not in OUR dirs
        self.assertEqual(bias["attributed"], 10)  # but counted
        self.assertEqual(res["left_to_do"], [])
        self.assertTrue(res["all_saved"])  # pool is legitimate, not a gap

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
            pointings=[{"ra_hours": 0.71, "dec_deg": 41.27}] * 2,
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
        self.assertEqual(pane1["on_disk"], 2)
        self.assertEqual(pane1["effective_plan_id"], "plan-abc123-1")
        self.assertEqual(pane2["on_disk"], 1)
        # types filter: only the requested type survives
        filtered = await self.audit(project_dir=str(self.proj), types=["bias"])
        self.assertEqual({r["type"] for r in filtered["rows"]}, {"bias"})

    async def test_no_plan_files_warns(self):
        res = await self.audit(project_dir=str(self.proj))
        self.assertIn("warning", res)
        self.assertEqual(res["rows"], [])
        self.assertTrue(res["all_saved"])  # nothing outstanding
        self.assertIsNone(res["root"])  # root never resolved for an empty set

    async def test_broken_plan_file_is_error_not_abort(self):
        self.seed_full()
        (self.proj / "broken.json").write_text("{ not json", encoding="utf-8")

        res = await self.audit(project_dir=str(self.proj))

        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0]["file"], "broken.json")
        self.assertEqual(res["totals"]["plans"], 1)  # the good one still audited
        self.assertFalse(res["all_saved"])  # plan_errors break the verdict


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


if __name__ == "__main__":
    unittest.main()
