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
calibration held by an unarchived plan, BAD_ twins ride, orphans behave,
the quarantine hides frames from attribution, and the rclone receipt
gates the dest leg. PurgePlanFileTest covers the plan-file leg: the
archive gates (folder + byte-identical plan.json) run before any frame
is deleted, ``--purge`` removes the original plan JSON only after its
frames are gone, and every refusal keeps frames and file intact.
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
        return self.root / ".prune-quarantine"

    @property
    def purge_index(self) -> Path:
        return self.root / "prune-index.jsonl"

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

    def make_dest_folder(
        self, name: str = "m31__plan-abc123", *, extra_files: int = 0
    ) -> Path:
        """Hand-build an archive folder for the rclone receipt tests."""
        folder = self.dest / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "manifest.json").write_text(
            json.dumps({"plan_id": "plan-abc123", "complete": True, "files": []}),
            encoding="utf-8",
        )
        (folder / "plan.json").write_text("{}", encoding="utf-8")
        for i in range(extra_files):
            (folder / f"frame_{i}.fits").write_bytes(b"x" * 10)
        return folder


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


# ------------------------------------------------- the staging (dest) leg


class PurgeDestTest(PurgeBase):
    def _mock_rclone(self, returncode: int = 0, stderr: str = ""):
        return mock.patch.object(
            purge_mod.subprocess,
            "run",
            return_value=mock.Mock(returncode=returncode, stdout="", stderr=stderr),
        )

    def test_verify_remote_writes_receipt_only_with_apply(self):
        folder = self.make_dest_folder(extra_files=1)

        with self._mock_rclone() as run:
            code, out, _err = self.run_purge("--verify-remote", "remote:arch")
        self.assertEqual(code, 0, out)
        self.assertIn("rclone check passed", out)
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv[:3], ["rclone", "check", str(self.dest)])
        self.assertIn("remote:arch", argv)
        receipt_path = self.dest / ".cloud-verified.json"
        self.assertFalse(receipt_path.exists())  # dry: report only

        with self._mock_rclone():
            code, out, _err = self.run_purge(
                "--verify-remote", "remote:arch", "--apply"
            )
        self.assertEqual(code, 0, out)
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["remote"], "remote:arch")
        self.assertIn(folder.name, receipt["folders"])
        self.assertEqual(receipt["folders"][folder.name]["file_count"], 3)
        records = [r for r in self._dest_index_records() if r["action"] == "verify"]
        self.assertEqual(len(records), 1)

    def test_verify_remote_failure_exits_one_without_receipt(self):
        self.make_dest_folder()

        with self._mock_rclone(returncode=1, stderr="ERROR : files differ"):
            code, _out, err = self.run_purge(
                "--verify-remote", "remote:arch", "--apply"
            )

        self.assertEqual(code, 1)
        self.assertIn("rclone check failed", err)
        self.assertIn("files differ", err)
        self.assertFalse((self.dest / ".cloud-verified.json").exists())

    def _dest_index_records(self) -> list[dict]:
        index = self.dest / "prune-index.jsonl"
        if not index.exists():
            return []
        return [
            json.loads(line) for line in index.read_text().splitlines() if line.strip()
        ]

    def test_prune_dest_requires_receipt(self):
        self.make_dest_folder()

        code, _out, err = self.run_purge("--prune-dest", "--apply")

        self.assertEqual(code, 1)
        self.assertIn("no receipt", err)
        self.assertIn("--verify-remote", err)
        self.assertTrue((self.dest / "m31__plan-abc123").is_dir())

    def test_prune_dest_prunes_matching_folder(self):
        folder = self.make_dest_folder(extra_files=2)
        with self._mock_rclone():
            self.assertEqual(
                self.run_purge("--verify-remote", "remote:arch", "--apply")[0], 0
            )

        code, out, _err = self.run_purge("--prune-dest", "--apply")

        self.assertEqual(code, 0, out)
        self.assertIn("pruned", out)
        self.assertFalse(folder.exists())  # frames+manifest gone
        records = [r for r in self._dest_index_records() if r["action"] == "prune-dest"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["folder"], folder.name)
        self.assertEqual(records[0]["remote"], "remote:arch")

    def test_prune_dest_skips_changed_and_unverified_folders(self):
        changed = self.make_dest_folder("changed__plan-abc123")
        with self._mock_rclone():
            self.assertEqual(
                self.run_purge("--verify-remote", "remote:arch", "--apply")[0], 0
            )
        # Rebuilt after verification…
        (changed / "frame_new.fits").write_bytes(b"y" * 50)
        # …and a folder archived after verification.
        late = self.make_dest_folder("late__plan-abc123")

        code, out, _err = self.run_purge("--prune-dest", "--apply")

        self.assertEqual(code, 1)  # nothing pruned, both skipped
        self.assertIn("changed since verification", out)
        self.assertIn("not in the receipt", out)
        self.assertIn("re-verify", out)
        self.assertTrue(changed.is_dir())
        self.assertTrue(late.is_dir())

    def test_prune_dest_dry_run_reports_would(self):
        folder = self.make_dest_folder()
        with self._mock_rclone():
            self.assertEqual(
                self.run_purge("--verify-remote", "remote:arch", "--apply")[0], 0
            )

        code, out, _err = self.run_purge("--prune-dest")

        self.assertEqual(code, 0, out)
        self.assertIn("would prune", out)
        self.assertTrue(folder.is_dir())  # nothing deleted


if __name__ == "__main__":
    unittest.main()
