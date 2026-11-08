"""Tests for the nina-purge CLI (nina_planner/purge.py).

Reuses test_archive.ArchiveBase: same tmp project + landing root + dest
fixture, with `run_archive` driving the real archive CLI to build real
manifests (end-to-end fidelity for preserved/released) and `run_purge`
driving purge with stdout captured. Every test patches
``purge_mod._sequence_is_running`` to an idle AsyncMock so no test ever
touches the live N.I.N.A. API; individual tests flip it to RUNNING or to
an unreachable error to pin the gate.

The scenarios mirror the design rule
``prunable(F) <=> preserved(F) AND all claiming plans released``:
unarchived frames held, --all waiver releases, re-opened (archived but
incomplete-now) plans held until their plan file moves out, pooled
calibration held by an unarchived plan, BAD_ twins ride, JUNK/-diverted
own-token frames ride once archived, orphans behave, the quarantine hides
frames from attribution, and --apply never touches the archive
destination (the rclone staging leg is gone).
PurgePlanFileTest covers the plan-file leg: the archive gates (folder +
byte-identical plan.json) run before any frame is deleted, ``--purge``
removes the original plan JSON only after its frames are gone, and every
refusal keeps frames and file intact.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from test_archive import ArchiveBase, _plan

from nina_planner import archive as archive_mod
from nina_planner import metadata as metadata_mod
from nina_planner import purge as purge_mod
from nina_planner.models.plan import ObservationPlan
from nina_planner.tools import plans as plans_mod


class PurgeBase(ArchiveBase):
    """ArchiveBase + purge CLI runner + sequence-state mock."""

    def setUp(self):
        super().setUp()
        self.seq_mock = mock.AsyncMock(return_value=False)
        patcher = mock.patch.object(
            purge_mod, "_sequence_is_running", new=self.seq_mock
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -------------------------------------------------------------- runs

    def run_archive(self, *extra: str) -> int:
        argv = [
            "--dest",
            str(self.dest),
            "--imaging-root",
            str(self.root),
            "--project-dir",
            str(self.proj),
            *extra,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            return archive_mod.main(argv)

    def run_purge(self, *extra: str) -> tuple[int, str, str]:
        argv = [
            "--dest",
            str(self.dest),
            "--imaging-root",
            str(self.root),
            "--project-dir",
            str(self.proj),
            *extra,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = purge_mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    # ----------------------------------------------------------- helpers

    @property
    def quarantine(self) -> Path:
        return self.root / ".quarantine"

    @property
    def purge_index(self) -> Path:
        return self.root / "purge-index.jsonl"

    def index_records(self) -> list[dict]:
        if not self.purge_index.exists():
            return []
        return [
            json.loads(line)
            for line in self.purge_index.read_text().splitlines()
            if line.strip()
        ]

    def root_dirs(self) -> list[str]:
        return sorted(
            p.name
            for p in self.root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    def seed_plan_b(self) -> ObservationPlan:
        """An incomplete, unarchived plan whose window claims A's darks.

        B's three lights anchor its calibration session at 2026-09-21T02:00Z,
        inside every window around A's same-night calibration, so B's
        type/exposure matchers claim A's darks/bias/flats — the pooled
        calibration case the rule must protect.
        """
        plan = _plan(plan_id="plan-bbb456", target="M33")
        self.write_plan(plan, name="m33.json")
        token_dir = self.root / "M33_plan-bbb456_"
        rows = []
        for i in range(3):
            name = f"2026-09-20_22-00-00_L__60.00s_{i:04d}.fits"
            path = token_dir / "2026-09-20" / "LIGHT" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fits-bytes:" + name.encode())
            rows.append(
                {
                    "ImageType": "LIGHT",
                    "FilePath": str(path),
                    "FilterName": "L",
                    "Duration": "60.0",
                    "ExposureStartUTC": "2026-09-21T02:00:00Z",
                    "HFR": "1.5",
                    "DetectedStars": "120",
                }
            )
        csv_dir = token_dir / "2026-09-20" / "LIGHT"
        import csv as csv_mod

        with (csv_dir / "ImageMetaData.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv_mod.DictWriter(handle, fieldnames=sorted(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return plan


# ------------------------------------------------------------ dry run


class PurgeDryRunTest(PurgeBase):
    def test_dry_run_prunable_exits_zero_and_writes_nothing(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)

        code, out, _err = self.run_purge()

        self.assertEqual(code, 0, out)
        self.assertIn("PRUNE", out)
        self.assertIn("M31_plan-abc123_", out)
        self.assertIn("preserved; all claims released", out)
        self.assertFalse(self.quarantine.exists())
        self.assertFalse(self.purge_index.exists())
        self.assertEqual(self.root_dirs(), ["M31_plan-abc123_"])

    def test_unarchived_frames_held_not_preserved(self):
        self.seed_full()

        code, out, _err = self.run_purge()

        self.assertEqual(code, 1)
        self.assertIn("HOLD", out)
        self.assertIn("not preserved — run nina-archive first", out)
        self.assertIn("nothing to do (dry run)", out)

    def test_unrecorded_only_dir_held(self):
        stray = self.root / "mystery_dir"
        stray.mkdir()
        (stray / "unrecorded.fits").write_bytes(b"no row ever saw this")

        code, out, _err = self.run_purge()

        self.assertEqual(code, 1)
        self.assertIn("HOLD", out)
        self.assertIn("no recorded frames", out)

    def test_unrelated_json_ignored(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        (self.proj / "notes.json").write_text('{"foo": 1}', encoding="utf-8")

        code, out, _err = self.run_purge()

        self.assertEqual(code, 0, out)
        self.assertIn("unrelated JSON file(s) ignored", out)
        self.assertIn("PRUNE", out)

    def test_broken_plan_aborts_before_any_mutation(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        (self.proj / "bad.json").write_text('{"pointings": "oops"}', encoding="utf-8")

        code, _out, err = self.run_purge("--apply")

        self.assertEqual(code, 1)
        self.assertIn("bad.json", err)
        self.assertIn("purge refuses to guess", err)
        self.assertFalse(self.quarantine.exists())
        self.assertEqual(self.root_dirs(), ["M31_plan-abc123_"])

    def test_dest_inside_root_refused(self):
        code, _out, err = self.run_purge("--dest", str(self.root / "inner"))

        self.assertEqual(code, 1)
        self.assertIn("inside the imaging root", err)
        self.assertIn("double-count", err)


# -------------------------------------------------------------- apply


class PurgeApplyTest(PurgeBase):
    def test_apply_quarantines_moves_and_indexes(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)

        code, out, _err = self.run_purge("--apply")

        self.assertEqual(code, 0, out)
        self.assertIn("📦", out)
        self.assertIn("nothing was deleted", out)
        self.assertIn("📌", out)
        self.assertIn("m31.json", out)  # plan-file hint names the owner
        self.assertEqual(self.root_dirs(), [])  # moved out of the root
        moved = self.quarantine / "M31_plan-abc123_"
        self.assertTrue(moved.is_dir())
        self.assertTrue((moved / "2026-09-20" / "LIGHT").is_dir())
        records = self.index_records()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["action"], "quarantine")
        self.assertEqual(rec["dir"], "M31_plan-abc123_")
        self.assertEqual(rec["frames"], 50)
        self.assertEqual(rec["rejects"], 0)
        self.assertEqual(rec["unrecorded"], 0)
        self.assertGreater(rec["bytes"], 0)
        self.assertIn("plan-abc123", rec["claimants"])

    def test_quarantined_dir_invisible_on_next_run(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)

        code, out, _err = self.run_purge()

        self.assertEqual(code, 1)  # nothing left to do
        self.assertNotIn("M31_plan-abc123_", out)  # dot-dir pruned everywhere
        self.assertIn("0 prunable, 0 held", out)
        self.assertIn("nothing to do", out)

    def test_quarantine_collision_gets_suffix(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        (self.quarantine / "M31_plan-abc123_").mkdir(parents=True)

        code, _out, _err = self.run_purge("--apply")

        self.assertEqual(code, 0)
        self.assertFalse((self.root / "M31_plan-abc123_").exists())
        self.assertTrue(
            (self.quarantine / "M31_plan-abc123_~1" / "2026-09-20").is_dir()
        )

    def test_sequence_running_refuses_apply(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.seq_mock.return_value = True

        code, _out, err = self.run_purge("--apply")

        self.assertEqual(code, 1)
        self.assertIn("RUNNING", err)
        self.assertIn("stop it before purging", err)
        self.assertFalse(self.quarantine.exists())
        self.assertEqual(self.root_dirs(), ["M31_plan-abc123_"])

    def test_nina_unreachable_proceeds_with_warning(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.seq_mock.side_effect = RuntimeError("connection refused")

        code, out, _err = self.run_purge("--apply")

        self.assertEqual(code, 0, out)
        self.assertIn("unreachable", out)
        self.assertIn("proceeding as if idle", out)
        self.assertTrue(self.quarantine.is_dir())

    def test_purge_deletes_quarantine_for_real(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)

        code, out, _err = self.run_purge("--purge")

        self.assertEqual(code, 0, out)
        self.assertIn("purged", out)
        self.assertFalse(self.quarantine.exists())
        self.assertIn("removed plan file", out)  # plan JSON rides out last
        self.assertFalse((self.proj / "m31.json").exists())
        actions = [r["action"] for r in self.index_records()]
        self.assertEqual(actions, ["quarantine", "purge", "plan-file"])
        self.assertTrue(self.index_records()[-1]["plan_file_deleted"])

        code, out, _err = self.run_purge("--purge")
        self.assertEqual(code, 0)  # idempotent: nothing left is success
        self.assertIn("nothing to purge", out)

    def test_purge_without_quarantine_is_idempotent(self):
        code, out, _err = self.run_purge("--purge")

        self.assertEqual(code, 0)
        self.assertIn("nothing to purge", out)
        self.assertIn("nothing to do", out)


# ------------------------------------------------------- release rules


class PurgeReleaseTest(PurgeBase):
    def test_reopened_plan_held_then_released_by_moving_plan_file(self):
        """Archived complete, then frames lost → held; plan file out → prunable."""
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        light_dir = self.root / "M31_plan-abc123_" / "2026-09-20" / "LIGHT"
        for fits in sorted(light_dir.glob("*.fits"))[:5]:
            fits.unlink()

        code, out, _err = self.run_purge()
        self.assertEqual(code, 1)
        self.assertIn("HOLD", out)
        self.assertIn("archived, incomplete now (LIGHT 15/20)", out)
        self.assertIn("already gone", out)

        (self.proj / "m31.json").unlink()
        code, out, _err = self.run_purge()
        self.assertEqual(code, 0, out)
        self.assertIn("PRUNE", out)  # orphan + preserved → prunable
        self.assertIn("all claims released", out)

    def test_pooled_calibration_held_by_unarchived_plan_until_waived(self):
        """A's dir held while B (claiming A's darks) is unarchived; --all releases."""
        self.seed_full()
        self.seed_plan_b()
        self.assertEqual(self.run_archive(), 0)  # A archived, B skipped (incomplete)

        code, out, _err = self.run_purge()
        self.assertEqual(code, 1)
        self.assertIn("HOLD", out)
        # A's dir: darks preserved, but claimed by unreleased B
        self.assertIn("m33.json", out)
        self.assertIn("plan-bbb456: not archived", out)
        # B's own dir: nothing archived yet
        self.assertIn("M33_plan-bbb456_", out)

        self.assertEqual(self.run_archive("--all"), 0)  # waive B

        code, out, _err = self.run_purge()
        self.assertEqual(code, 0, out)
        self.assertIn("2 prunable, 0 held", out)
        self.assertIn("PRUNE", out)

    def test_orphan_not_preserved_held(self):
        self.seed_full()
        (self.proj / "m31.json").unlink()  # no plan claims the frames

        code, out, _err = self.run_purge()

        self.assertEqual(code, 1)
        self.assertIn("HOLD", out)
        self.assertIn("not preserved", out)

    def test_bad_reject_twin_rides_with_prunable_dir(self):
        # Surplus light so a reject still leaves acquired >= expected.
        self.seed_full(light_count=21)
        light_dir = self.root / "M31_plan-abc123_" / "2026-09-20" / "LIGHT"
        victim = sorted(light_dir.glob("*.fits"))[5]
        victim.rename(victim.with_name(f"BAD_{victim.name}"))
        self.assertEqual(self.run_archive(), 0)  # 20/20 after the row drops

        code, out, _err = self.run_purge()
        self.assertEqual(code, 0, out)
        self.assertIn("1 BAD_ reject twin(s)", out)
        self.assertIn("PRUNE", out)

        code, out, _err = self.run_purge("--apply")
        self.assertEqual(code, 0, out)
        rec = self.index_records()[0]
        self.assertEqual(rec["rejects"], 1)
        self.assertEqual(rec["frames"], 50)
        self.assertTrue(
            (
                self.quarantine
                / "M31_plan-abc123_"
                / "2026-09-20"
                / "LIGHT"
                / f"BAD_{victim.name}"
            ).is_file()
        )

    def test_outside_root_frames_reported_never_pruned(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        # One row whose recorded path escaped the root.
        outside = self.root.parent / "outside.fits"
        outside.write_bytes(b"elsewhere")
        import csv as csv_mod

        csv_path = (
            self.root
            / "M31_plan-abc123_"
            / "2026-09-20"
            / "LIGHT"
            / "ImageMetaData.csv"
        )
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv_mod.DictReader(handle))
            fieldnames = list(rows[0])
        rows.append(
            {
                **{k: "" for k in fieldnames},
                "ImageType": "LIGHT",
                "FilePath": str(outside),
                "FilterName": "L",
                "Duration": "60.0",
                "ExposureStartUTC": "2026-09-21T01:30:00Z",
            }
        )
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv_mod.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        code, out, _err = self.run_purge()

        self.assertIn("outside the imaging root", out)
        self.assertIn("never pruned", out)
        # Inside-root frames still prunable → overall verdict unaffected.
        self.assertEqual(code, 0, out)


# ------------------------------------------ plan-file removal (--purge)


class PurgeJunkTest(PurgeBase):
    """The ``JUNK/`` invariant: a degenerate own-token frame stops holding
    its landing directory.

    Regression for the deadlock where two 0.00003s Smart Exposure darks
    sat in ImageMetaData.csv, matched no exposure group (so archive
    deliberately skipped them), never appeared in any manifest's
    ``files[]`` — and therefore made ``preserved(F)`` false, so nina-purge
    held a finished plan's directory for ever with "not preserved — run
    nina-archive first".
    """

    def seed_junk_dark(self) -> Path:
        self.seed_full()
        name = "2026-09-21_05-00-00_dark_60.00s_junk.fits"
        path = self.frame("DARK", name, date="2026-09-21")
        self.write_metadata(
            "DARK",
            [
                {
                    "ImageType": "DARK",
                    "FilePath": str(path),
                    "Duration": "0.00003",
                    "ExposureStartUTC": "2026-09-21T05:00:00Z",
                }
            ],
            date="2026-09-21",
        )
        return path

    def test_junk_holds_directory_until_archived_then_prunes(self):
        junk = self.seed_junk_dark()

        code, out, _err = self.run_purge()

        # Unarchived, junk included: held, exactly like any unarchived frame.
        self.assertEqual(code, 1)
        self.assertIn("HOLD", out)
        self.assertIn("not preserved — run nina-archive first", out)

        # After archiving, the junk row IS in the manifest (under JUNK/),
        # so the very same directory reads prunable.
        self.assertEqual(self.run_archive(), 0)
        manifest = json.loads(
            (self.dest / "m31__plan-abc123" / "manifest.json").read_text()
        )
        self.assertIn(str(junk), [f["source"] for f in manifest["files"]])
        self.assertEqual(manifest["disk"]["junk"][0]["source"], str(junk))

        code, out, _err = self.run_purge()

        self.assertEqual(code, 0, out)
        self.assertIn("PRUNE", out)
        self.assertIn("preserved; all claims released", out)
        self.assertNotIn("not preserved", out)

    def test_full_chain_quarantines_then_purges_the_junk_frame(self):
        junk = self.seed_junk_dark()
        self.assertEqual(self.run_archive(), 0)

        self.assertEqual(self.run_purge("--apply")[0], 0)
        self.assertEqual(self.root_dirs(), [])
        # The junk frame rode along with the directory into quarantine —
        # it was never a reason to keep the directory.
        self.assertTrue(
            any(p.name == junk.name for p in self.quarantine.rglob(junk.name))
        )

        code, out, _err = self.run_purge("--purge")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.root_dirs(), [])
        self.assertFalse(self.quarantine.exists())
        self.assertFalse(any(self.root.rglob(junk.name)))
        self.assertFalse((self.proj / "m31.json").exists())  # file swept too


class PurgePlanFileTest(PurgeBase):
    """The plan-file leg: gates first, frames, then the original JSON.

    Mirrors the feature contract: ``--purge`` removes a completed plan's
    original plan JSON only AFTER its frames are gone and the archive
    folder ``<dest>/<stem>__<plan-id>/plan.json`` is byte-identical to
    it (the durable copy); any refusal leaves frames and file intact and
    exits 1, while a re-run finishes the job.
    """

    def archive_plan_path(self) -> Path:
        return self.dest / "m31__plan-abc123" / "plan.json"

    def plan_file_records(self) -> list[dict]:
        return [r for r in self.index_records() if r["action"] == "plan-file"]

    def audit(self) -> dict:
        async def _resolve():
            return self.root

        with mock.patch.object(metadata_mod, "resolve_imaging_root", _resolve):
            return asyncio.run(plans_mod.audit_plans(project_dir=str(self.proj)))

    # ----------------------------------------------------------- happy path

    def test_purge_removes_plan_file_after_frames(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        archived_bytes = self.archive_plan_path().read_bytes()
        self.assertEqual(self.run_purge("--apply")[0], 0)

        code, out, _err = self.run_purge("--purge")

        self.assertEqual(code, 0, out)
        self.assertIn("removed plan file", out)
        self.assertIn(str(self.proj / "m31.json"), out)
        self.assertFalse((self.proj / "m31.json").exists())  # original gone
        self.assertEqual(self.root_dirs(), [])  # frames gone
        self.assertFalse(self.quarantine.exists())
        # The archived plan.json — the copy to restore from — is untouched.
        self.assertEqual(self.archive_plan_path().read_bytes(), archived_bytes)
        records = self.plan_file_records()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["plan_file_deleted"])
        self.assertEqual(records[0]["plan_id"], "plan-abc123")

    def test_dry_run_reports_would_remove_plan_file(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)

        code, out, _err = self.run_purge()

        self.assertEqual(code, 0, out)
        self.assertIn("would remove plan file", out)
        self.assertIn(str(self.proj / "m31.json"), out)
        self.assertTrue((self.proj / "m31.json").is_file())  # nothing deleted
        self.assertEqual(self.root_dirs(), ["M31_plan-abc123_"])
        self.assertFalse(self.purge_index.exists())  # a dry run writes nothing

    def test_report_keeps_unarchived_plan_file(self):
        self.seed_full()

        code, out, _err = self.run_purge()

        self.assertEqual(code, 1)
        self.assertIn("kept plan file", out)
        self.assertIn("no archive folder", out)
        self.assertTrue((self.proj / "m31.json").is_file())

    def test_re_run_after_full_purge_is_clean_success(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)
        self.assertEqual(self.run_purge("--purge")[0], 0)

        code, out, err = self.run_purge("--purge")

        self.assertEqual(code, 0)
        self.assertIn("nothing to purge", out)
        self.assertNotIn("✗", out + err)
        self.assertNotIn("⚠", err)

    # ------------------------------------------------------------- refusals

    def test_plan_edited_after_archive_refused(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)
        plan_path = self.proj / "m31.json"
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        data["light_frames"][0]["total_count"] = 21
        plan_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        code, out, err = self.run_purge("--purge")

        self.assertEqual(code, 1)
        self.assertIn("archive is stale, re-run nina-archive --force first", out + err)
        self.assertIn("kept in quarantine", out)  # frames intact
        self.assertIn("kept plan file", out)  # plan file intact
        self.assertTrue((self.proj / "m31.json").is_file())
        self.assertTrue((self.quarantine / "M31_plan-abc123_").is_dir())
        self.assertFalse((self.root / "M31_plan-abc123_").exists())
        self.assertFalse(self.plan_file_records()[-1]["plan_file_deleted"])

    def test_archive_folder_missing_refused(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)
        shutil.rmtree(self.dest / "m31__plan-abc123")

        code, out, _err = self.run_purge("--purge")

        self.assertEqual(code, 1)
        self.assertIn("kept in quarantine", out)
        self.assertIn("no archive folder", out)
        self.assertIn("kept plan file", out)
        self.assertTrue((self.quarantine / "M31_plan-abc123_").is_dir())
        self.assertTrue((self.proj / "m31.json").is_file())

    def test_archive_plan_json_missing_refused(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)
        self.archive_plan_path().unlink()

        code, out, _err = self.run_purge("--purge")

        self.assertEqual(code, 1)
        self.assertIn("kept in quarantine", out)
        self.assertIn("no plan.json in archive folder", out)
        self.assertIn("kept plan file", out)
        self.assertTrue((self.quarantine / "M31_plan-abc123_").is_dir())
        self.assertTrue((self.proj / "m31.json").is_file())

    def test_frame_deletion_failure_keeps_plan_file(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)

        def _boom(path, *args, **kwargs):
            raise OSError("disk said no")

        with mock.patch.object(purge_mod.shutil, "rmtree", side_effect=_boom):
            code, out, err = self.run_purge("--purge")

        self.assertEqual(code, 1)
        self.assertIn("kept plan file", out)  # kept so a re-run can finish
        self.assertIn("re-run nina-purge --purge", err)
        self.assertTrue((self.proj / "m31.json").is_file())
        self.assertTrue((self.quarantine / "M31_plan-abc123_").is_dir())

        code, out, _err = self.run_purge("--purge")  # re-run finishes the job
        self.assertEqual(code, 0, out)
        self.assertIn("removed plan file", out)
        self.assertFalse((self.proj / "m31.json").exists())
        self.assertFalse(self.quarantine.exists())

    def test_plan_file_removal_failure_fails_the_run(self):
        # The frames are gone and the gates passed, but the original JSON
        # itself cannot be unlinked: the run reports it, records the failed
        # removal in the index, and exits 1 so the next run retries.
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)

        real_unlink = Path.unlink

        def _boom(path, *args, **kwargs):
            if path.name == "m31.json":
                raise OSError("read-only filesystem")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", _boom):
            code, out, err = self.run_purge("--purge")

        self.assertEqual(code, 1)
        self.assertIn("cannot remove plan file", err)
        self.assertNotIn("removed plan file", out)
        self.assertTrue((self.proj / "m31.json").is_file())
        records = self.plan_file_records()
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["plan_file_deleted"])
        self.assertIn("read-only filesystem", records[0]["reason"])

    # ------------------------------------------------------- same-folder edge

    def test_only_byte_identical_plan_files_are_removed(self):
        """Two plan files map to one archive folder: only matching ones go."""
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)
        self.assertEqual(self.run_purge("--purge")[0], 0)  # m31.json removed
        archived = self.archive_plan_path().read_bytes()
        # Back to two files in the project: one byte-identical to the
        # archive, one edited after it — both map to m31__plan-abc123/.
        (self.proj / "m31.json").write_bytes(archived)
        stale = json.loads(archived)
        stale["light_frames"][0]["total_count"] = 99
        (self.proj / "m31__plan-abc123.json").write_text(
            json.dumps(stale, indent=2), encoding="utf-8"
        )

        code, out, err = self.run_purge("--purge")

        self.assertEqual(code, 1)
        self.assertFalse((self.proj / "m31.json").exists())  # identical → removed
        self.assertTrue((self.proj / "m31__plan-abc123.json").is_file())  # stale → kept
        self.assertIn("removed plan file", out)
        self.assertIn("kept plan file", out)
        self.assertIn("archive is stale, re-run nina-archive --force first", out + err)
        by_file = {
            r["plan_file"]: r["plan_file_deleted"] for r in self.plan_file_records()
        }
        self.assertTrue(by_file[str(self.proj / "m31.json")])
        self.assertFalse(by_file[str(self.proj / "m31__plan-abc123.json")])

    # ------------------------------------------------------------- audit

    def test_audit_plans_does_not_list_purged_plan(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(self.run_purge("--apply")[0], 0)

        # The bug this feature fixes: after --apply the leftover plan file
        # reads as never-shot / left-to-do — its rows went with the frames.
        mid = self.audit()
        self.assertEqual([e["plan_id"] for e in mid["never_shot"]], ["plan-abc123"])
        self.assertTrue(any(e["reason"] == "NEVER_SHOT" for e in mid["left_to_do"]))
        self.assertFalse(mid["all_saved"])

        self.assertEqual(self.run_purge("--purge")[0], 0)

        after = self.audit()
        self.assertEqual(after["never_shot"], [])
        self.assertEqual(after["left_to_do"], [])
        self.assertTrue(after["all_saved"])


# ------------------------------------------------- removed rclone/staging leg


class PurgeNoStagingLegTest(PurgeBase):
    def test_removed_cloud_flags_are_rejected(self):
        for flag in (["--verify-remote", "remote:arch"], ["--prune-dest"]):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    self.run_purge(*flag)
                self.assertEqual(caught.exception.code, 2)

    def test_apply_never_modifies_the_archive_dest(self):
        self.seed_full()
        self.assertEqual(self.run_archive(), 0)
        before = sorted(str(p) for p in self.dest.rglob("*"))

        code, out, _err = self.run_purge("--apply")

        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(str(p) for p in self.dest.rglob("*")), before)
        self.assertFalse((self.dest / ".cloud-verified.json").exists())
        self.assertFalse((self.dest / "purge-index.jsonl").exists())


class PurgeDetailTest(PurgeBase):
    """Branches the end-to-end purge runs never reach.

    Helper-level cases where the fixture cannot produce the state (a
    vanished manifest, a corrupt index line, an unlink that fails) and CLI
    argument shapes the happy path does not use (relative paths, no
    --imaging-root).
    """

    # ------------------------------------------------------------ helpers

    def test_unique_join_truncates_past_the_limit(self):
        self.assertEqual(purge_mod._unique_join(["a", "b"]), "a; b")
        # Duplicates collapse first: the limit counts distinct reasons.
        self.assertEqual(
            purge_mod._unique_join(["a", "b", "a", "c", "d", "e"]),
            "a; b; c; +2 more",
        )

    def test_plan_file_gate_refuses_a_plan_inside_dest(self):
        plan_file = self.dest / "m31.json"  # the archived copy itself
        _folder, ok, stale, reason = purge_mod._plan_file_gate(
            plan_file, _plan(), self.dest
        )
        self.assertFalse(ok)
        self.assertFalse(stale)
        self.assertIn("inside --dest", reason)

    def test_plan_file_gate_reports_an_unreadable_comparison(self):
        # The project's plan file is unreadable (here: a directory where a
        # file should be) — the refusal must name the comparison, not raise.
        plan = _plan()
        plan_file = self.proj / "m31.json"
        plan_file.mkdir()
        archived = self.dest / purge_mod._folder_name(plan_file, plan) / "plan.json"
        archived.parent.mkdir(parents=True)
        archived.write_text("{}", encoding="utf-8")

        _folder, ok, stale, reason = purge_mod._plan_file_gate(
            plan_file, plan, self.dest
        )

        self.assertFalse(ok)
        self.assertFalse(stale)
        self.assertIn("cannot compare against the archive", reason)

    def test_resolve_root_rejects_a_missing_imaging_root(self):
        args = purge_mod._build_parser().parse_args(
            ["--imaging-root", str(self.proj / "no-such-root")]
        )
        with self.assertRaisesRegex(purge_mod.PurgeError, "does not exist"):
            asyncio.run(purge_mod._resolve_root(args))

    def test_resolve_root_reports_a_profile_failure(self):
        args = purge_mod._build_parser().parse_args([])
        with mock.patch.object(
            metadata_mod,
            "resolve_imaging_root",
            side_effect=RuntimeError("NINA is down"),
        ):
            with self.assertRaises(purge_mod.PurgeError) as ctx:
                asyncio.run(purge_mod._resolve_root(args))
        message = str(ctx.exception)
        self.assertIn("cannot resolve the imaging root", message)
        self.assertIn("NINA is down", message)
        self.assertIn("--imaging-root", message)

    def test_read_manifests_skips_folders_without_a_usable_manifest(self):
        (self.dest / "no-manifest").mkdir(parents=True)
        broken = self.dest / "broken"
        broken.mkdir()
        (broken / "manifest.json").write_text("{oops", encoding="utf-8")
        nameless = self.dest / "nameless"
        nameless.mkdir()
        (nameless / "manifest.json").write_text(
            json.dumps({"complete": True}), encoding="utf-8"
        )

        by_plan, preserved, warnings = purge_mod._read_manifests(self.dest)

        self.assertEqual(by_plan, {})
        self.assertEqual(preserved, set())
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("unreadable manifest" in w for w in warnings), warnings)
        self.assertTrue(any("no plan_id" in w for w in warnings), warnings)

    def test_classify_tolerates_pathless_rows_and_outside_rejects(self):
        vanished = self.root / "tok" / "2026-09-20" / "LIGHT" / "gone.fits"
        outside_reject = Path("/elsewhere/BAD_renamed.fits")
        inside_reject = self.root / "tok" / "2026-09-20" / "LIGHT" / "BAD_gone.fits"
        rows = [
            {"image_type": "LIGHT"},  # no file_path: nothing to classify
            {"image_type": "LIGHT", "file_path": str(vanished)},  # stat fails
        ]

        dirs, outside = purge_mod._classify(
            self.root,
            rows,
            [outside_reject, inside_reject],
            {},
            set(),
            set(),
            {},
            {},
        )

        # The pathless row never reaches the outside-root report...
        self.assertEqual([o["path"] for o in outside], [str(outside_reject)])
        by_name = {d["dir"]: d for d in dirs}
        # ...and the vanished frame still holds its directory.
        self.assertEqual(by_name["tok"]["verdict"], "hold")

    def test_quarantine_skips_a_directory_that_vanished(self):
        entry = {"dir": "ghost", "frames": 0, "rejects": 0, "plan_ids": set()}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            moved = purge_mod._quarantine(self.root, [entry])
        self.assertEqual(moved, [])
        self.assertIn("vanished before the move", err.getvalue())

    def test_quarantine_reports_a_failed_move(self):
        (self.root / "tok").mkdir()
        entry = {"dir": "tok", "frames": 1, "rejects": 0, "plan_ids": set()}
        err = io.StringIO()
        with (
            mock.patch(
                "nina_planner.purge.shutil.move",
                side_effect=OSError("permission denied"),
            ),
            contextlib.redirect_stderr(err),
        ):
            moved = purge_mod._quarantine(self.root, [entry])
        self.assertEqual(moved, [])
        self.assertIn("permission denied", err.getvalue())
        self.assertTrue((self.root / "tok").is_dir())  # nothing moved

    def test_quarantine_warns_about_unrecorded_files_riding_along(self):
        token = self.root / "tok"
        token.mkdir()
        (token / "loose.fits").write_bytes(b"x")
        entry = {"dir": "tok", "frames": 0, "rejects": 0, "plan_ids": ["plan-abc123"]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            moved = purge_mod._quarantine(self.root, [entry])
        self.assertEqual(len(moved), 1)
        self.assertIn("unrecorded file(s) rode along", out.getvalue())
        self.assertTrue(self.quarantine.is_dir())

    def test_purge_quarantine_unlinks_loose_files(self):
        # Loose files are never claimed by any plan, so they always ride
        # along with a purge rather than being kept for manual inspection.
        self.quarantine.mkdir(parents=True)
        (self.quarantine / "stray.fits").write_bytes(b"1234")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            removed, skipped, failed = purge_mod._purge_quarantine(self.root, set(), {})
        self.assertEqual((removed, skipped, failed), (1, 0, 0))
        self.assertFalse((self.quarantine / "stray.fits").exists())
        self.assertIn("purged 1 quarantined item(s)", out.getvalue())

    def test_quarantine_gates_reads_claimants_and_skips_unknown_children(self):
        self.quarantine.mkdir(parents=True)
        (self.quarantine / "loose.fits").write_bytes(b"x")  # never claimed
        (self.quarantine / "hand-made").mkdir()  # moved in by hand
        (self.quarantine / "claimed").mkdir()
        self.purge_index.write_text(
            "{not json\n"
            + json.dumps(
                {
                    "action": "quarantine",
                    "quarantine": ".quarantine/claimed",
                    "claimants": ["plan-deadbeef"],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        qdir, claimants, purgeable, skips = purge_mod._quarantine_gates(
            self.root, [], {}, {}
        )

        self.assertEqual(qdir, self.quarantine)
        self.assertEqual(claimants["claimed"], {"plan-deadbeef"})
        self.assertIn("loose.fits", purgeable)
        self.assertIn("hand-made", skips)
        self.assertIn("no quarantine record", skips["hand-made"])
        self.assertIn("claimed", skips)
        self.assertIn("no plan file and no manifest", skips["claimed"])

    # ------------------------------------------------------ plan-file sweep

    @staticmethod
    def _record(plan_file: Path, **overrides) -> dict:
        record = {
            "file": plan_file,
            "plan_id": "plan-abc123",
            "folder": "m31__plan-abc123",
            "archive_ok": True,
            "stale": False,
            "gate_reason": "",
            "selected": [],
        }
        record.update(overrides)
        return record

    def _sweep(self, record, *, purge: bool, dirs=()):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = purge_mod._sweep_plan_files(
                [record],
                root=self.root,
                dirs=list(dirs),
                held_claimants=set(),
                purge=purge,
            )
        return result, out.getvalue(), err.getvalue()

    def test_sweep_skips_a_plan_file_that_is_already_gone(self):
        record = self._record(self.proj / "ghost.json")
        result, _out, _err = self._sweep(record, purge=True)
        self.assertEqual(result, (0, 0, 0))

    def test_sweep_treats_a_vanished_frame_as_the_state_it_waits_for(self):
        plan_file = self.write_plan(_plan())
        record = self._record(plan_file, selected=[str(self.proj / "gone.fits")])

        result, out, _err = self._sweep(record, purge=True)

        self.assertEqual(result, (1, 0, 0))  # no frames left → file removed
        self.assertIn("removed plan file", out)
        self.assertFalse(plan_file.exists())

    def test_sweep_keeps_the_file_while_frames_are_present_under_purge(self):
        frame = self.frame("LIGHT", "still-here.fits")
        plan_file = self.write_plan(_plan())
        record = self._record(plan_file, selected=[str(frame)])

        result, out, _err = self._sweep(record, purge=True)

        self.assertEqual(result, (0, 1, 0))
        self.assertIn("frames still present", out)
        self.assertTrue(plan_file.exists())

    def test_sweep_keeps_a_frame_that_lives_outside_the_root(self):
        # A frame outside the imaging root can never be proven gone by this
        # run's verdicts, so the plan file stays for the re-run.
        outside = self.proj / "outside.fits"
        outside.write_bytes(b"x")
        plan_file = self.write_plan(_plan())
        record = self._record(plan_file, selected=[str(outside)])

        result, out, _err = self._sweep(record, purge=False)

        self.assertEqual(result, (0, 1, 0))
        self.assertIn("frames still present", out)
        self.assertTrue(plan_file.exists())

    # --------------------------------------------------------------- CLI

    def test_relative_dest_and_project_dir_with_a_dead_nina(self):
        # Relative --dest roots at the cwd, relative --project-dir at
        # PROJECT_DIR, and a profile failure is reported (not raised) with
        # the --imaging-root escape hatch named.
        argv = ["--dest", self.dest.name, "--project-dir", self.proj.name]
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("nina_planner.purge.config.PROJECT_DIR", self.dest.parent),
            contextlib.chdir(self.dest.parent),
            mock.patch.object(
                metadata_mod,
                "resolve_imaging_root",
                side_effect=RuntimeError("NINA is down"),
            ),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = purge_mod.main(argv)
        self.assertEqual(code, 1)
        self.assertIn("cannot resolve the imaging root", err.getvalue())
        self.assertIn("NINA is down", err.getvalue())

    def test_missing_metadata_csv_is_reported_not_raised(self):
        with mock.patch(
            "nina_planner.purge.read_imaging_csv",
            side_effect=FileNotFoundError("ImageMetaData.csv is missing"),
        ):
            code, _out, err = self.run_purge()
        self.assertEqual(code, 1)
        self.assertIn("ImageMetaData.csv is missing", err)

    def test_dropped_rows_without_a_path_are_ignored(self):
        # The BAD_ twin scan reads dropped rows; one with no path cannot
        # name a twin and must not be treated as a gone reject either.
        with mock.patch(
            "nina_planner.purge.read_imaging_csv",
            return_value=([], [{"image_type": "LIGHT"}]),
        ):
            code, out, err = self.run_purge()
        self.assertEqual(code, 1)
        self.assertNotIn("BAD_", out)
        self.assertNotIn("gone", out)

    def test_apply_with_nothing_to_quarantine_says_so(self):
        code, out, _err = self.run_purge("--apply")
        self.assertIn("nothing to quarantine", out)
        self.assertFalse(self.quarantine.exists())

    def test_report_run_previews_quarantine_keeps(self):
        # The report leg prints what --apply → --purge would refuse, so an
        # operator sees the held directories before acting.
        self.quarantine.mkdir(parents=True)
        (self.quarantine / "hand-made").mkdir()

        code, out, _err = self.run_purge()

        self.assertIn("would keep in quarantine — no quarantine record", out)

    def test_module_entry_point_exits_with_main_status(self):
        import runpy
        import sys
        import warnings

        argv = [
            "nina-purge",
            "--dest",
            str(self.dest),
            "--imaging-root",
            str(self.root),
            "--project-dir",
            str(self.proj),
        ]
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            # runpy re-executes the already-imported module under __main__;
            # its "found in sys.modules" RuntimeWarning is expected here.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                runpy.run_module("nina_planner.purge", run_name="__main__")
        # An empty project has nothing to act on: the report leg exits 1.
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
