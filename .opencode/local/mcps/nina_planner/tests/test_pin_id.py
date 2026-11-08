"""Tests for the nina-pin-id CLI (nina_planner/pin_id.py).

Reuses test_archive.ArchiveBase: same tmp project + landing root +
``seed_full`` fixture, with the landing token dir pointed at the plan's
DERIVED id — a blank ``plan_id`` is the whole point of this tool, so the
seeded frames must carry the id the blank plan derives, exactly as they
would if N.I.N.A. had stamped it.

The scenarios pin the contract that makes editing a completed plan safe:
the derived id is frozen BEFORE the edit, frames attributed before the
pin stay attributed after it, a raised ``total_count`` keeps the id, an
already-pinned plan (or a hand-written id) is never overwritten, a dry
run writes nothing, and bad input exits 1 (bad arguments exit 2).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from test_archive import ArchiveBase, _plan

from nina_planner import pin_id as pin_mod
from nina_planner.imaging import read_imaging_csv
from nina_planner.models.plan import ObservationPlan
from nina_planner.tools import plans as plans_mod


class PinBase(ArchiveBase):
    """ArchiveBase + derived-id seeding + a pin CLI runner."""

    def setUp(self):
        super().setUp()
        self.plan_file = self.proj / "m31.json"
        self.derive_from(_plan(plan_id=""))

    def derive_from(self, plan: ObservationPlan) -> str:
        """Point the fixture's token dir at ``plan``'s derived id.

        Frames only attribute to a blank-id plan through the id it derives
        from its content, so the landing dir must be that id — otherwise
        every attribution assertion below would test nothing.
        """
        self.derived = plan._base_plan_id()
        self.token_dir = f"M31_{self.derived}_"
        return self.derived

    def seed_blank(
        self, plan: ObservationPlan | None = None, **seed
    ) -> ObservationPlan:
        """Write a blank-id plan plus its complete capture set."""
        plan = plan or _plan(plan_id="")
        self.derive_from(plan)
        self.seed_full(plan=plan, **seed)
        return plan

    # -------------------------------------------------------------- run

    def run_pin(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pin_mod.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def progress(self) -> dict:
        """get_plan_progress over the fixture's real CSV rows on disk."""
        rows: list[dict] = []
        for image_type in ("light", "dark", "bias", "flat"):
            rows += read_imaging_csv(root=self.root, image_type=image_type)

        async def _read(image_type: str | None = None):
            if image_type is None:
                return list(rows)
            up = image_type.upper()
            return [r for r in rows if (r.get("image_type") or "").upper() == up]

        async def _go():
            with mock.patch("nina_planner.metadata.read_metadata", _read):
                return await plans_mod.get_plan_progress(str(self.plan_file))

        return asyncio.run(_go())

    def light_group(self, progress: dict) -> dict:
        groups = progress["pointings"][0]["frame_types"]["light"]
        self.assertEqual(len(groups), 1, "one light group expected")
        return groups[0]


class PinCliTest(PinBase):
    def test_blank_plan_pinned_with_pre_pin_derived_id(self):
        plan = _plan(plan_id="")
        derived = self.derive_from(plan)
        self.write_plan(plan)
        before = json.loads(self.plan_file.read_text())

        code, out, err = self.run_pin(str(self.plan_file))

        self.assertEqual(code, 0, err)
        self.assertIn(f"pinned {self.plan_file}: {derived}", out)
        after = json.loads(self.plan_file.read_text())
        self.assertEqual(after["plan_id"], derived)
        # NOTHING else about the plan changed: same content, only the id.
        expected = dict(before)
        expected["plan_id"] = derived
        self.assertEqual(after, expected)

    def test_frames_attributed_before_pin_still_attributed_after(self):
        self.seed_blank()
        derived = self.derived

        # Before the pin the blank plan attributes through its derived id.
        before = self.progress()
        self.assertEqual(before["plan_id"], derived)
        self.assertEqual(self.light_group(before)["acquired_count"], 20)

        code, out, err = self.run_pin(str(self.plan_file))
        self.assertEqual(code, 0, err)
        self.assertIn(f"pinned {self.plan_file}: {derived}", out)

        after = self.progress()
        self.assertEqual(after["plan_id"], derived)
        self.assertEqual(self.light_group(after)["acquired_count"], 20)
        self.assertTrue(after["complete"])

    def test_pin_then_raise_total_count_keeps_id(self):
        self.seed_blank()

        code, _out, err = self.run_pin(str(self.plan_file))
        self.assertEqual(code, 0, err)

        # The edit: ask for 30 lights instead of 20.
        data = json.loads(self.plan_file.read_text())
        data["light_frames"][0]["total_count"] = 30
        self.plan_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        edited = ObservationPlan.model_validate(json.loads(self.plan_file.read_text()))
        self.assertEqual(edited._base_plan_id(), self.derived)
        # The already-shot lights still count against the raised ask.
        progress = self.progress()
        self.assertEqual(progress["plan_id"], self.derived)
        group = self.light_group(progress)
        self.assertEqual(group["acquired_count"], 20)
        self.assertEqual(group["total_count"], 30)
        self.assertEqual(group["remaining_count"], 10)

        # Contrast: the SAME edit on an unpinned plan would change the id.
        unpinned = _plan(
            plan_id="",
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 30}
            ],
        )
        self.assertNotEqual(unpinned._base_plan_id(), self.derived)

    def test_already_pinned_plan_is_noop(self):
        plan = _plan()  # hand-written / previously-pinned id
        self.write_plan(plan)
        before = self.plan_file.read_bytes()

        code, out, err = self.run_pin(str(self.plan_file))

        self.assertEqual(code, 0, err)
        self.assertIn("already pinned: plan-abc123", out)
        self.assertEqual(self.plan_file.read_bytes(), before)

    def test_dry_run_writes_nothing(self):
        plan = _plan(plan_id="")
        derived = self.derive_from(plan)
        self.write_plan(plan)
        before = self.plan_file.read_bytes()

        code, out, err = self.run_pin("--dry-run", str(self.plan_file))

        self.assertEqual(code, 0, err)
        self.assertIn(f"would pin {self.plan_file}: {derived}", out)
        self.assertEqual(self.plan_file.read_bytes(), before)
        self.assertEqual(json.loads(self.plan_file.read_text())["plan_id"], "")

    def test_missing_file_exits_1(self):
        code, _out, err = self.run_pin(str(self.proj / "ghost.json"))
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_broken_json_exits_1(self):
        bad = self.proj / "bad.json"
        bad.write_text("{not json", encoding="utf-8")

        code, _out, err = self.run_pin(str(bad))

        self.assertEqual(code, 1)
        self.assertIn("bad.json", err)

    def test_non_plan_json_exits_1(self):
        config_like = self.proj / "smb_opencode.json"
        config_like.write_text(json.dumps({"model": "x"}), encoding="utf-8")

        code, _out, err = self.run_pin(str(config_like))

        self.assertEqual(code, 1)
        self.assertIn("smb_opencode.json", err)

    def test_no_plan_arguments_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            pin_mod.main([])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("usage:", err.getvalue())

    # ------------------------------------------------------- failure paths

    def test_relative_plan_path_resolves_under_the_project_dir(self):
        with mock.patch.object(pin_mod.config, "PROJECT_DIR", self.proj):
            self.assertEqual(pin_mod._resolve("m31.json"), self.proj / "m31.json")
        # An absolute path is never re-rooted; ~ is expanded first.
        self.assertEqual(pin_mod._resolve(str(self.plan_file)), self.plan_file)
        self.assertEqual(
            pin_mod._resolve("~/somewhere/x.json"),
            Path.home() / "somewhere" / "x.json",
        )

    def test_write_atomic_leaves_no_temp_file_when_the_replace_fails(self):
        target = self.proj / "plan.json"
        target.write_text("original", encoding="utf-8")

        with mock.patch(
            "nina_planner.pin_id.os.replace", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                pin_mod._write_atomic(target, "replacement")

        # The plan keeps its bytes and the half-written sibling is gone —
        # a stray .tmp would be discovered as junk by the next plan scan.
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.proj.glob(".*.tmp")), [])

    def test_write_failure_is_reported_as_a_pin_error(self):
        blank = self.proj / "blank.json"
        blank.write_text(_plan(plan_id="").model_dump_json(indent=2), encoding="utf-8")

        with mock.patch(
            "nina_planner.pin_id.os.replace", side_effect=OSError("disk full")
        ):
            with self.assertRaises(pin_mod.PinError) as ctx:
                asyncio.run(pin_mod.pin_plan_file(str(blank)))

        message = str(ctx.exception)
        self.assertIn("blank.json", message)
        self.assertIn("could not write", message)
        self.assertIn("disk full", message)

    def test_module_entry_point_exits_with_main_status(self):
        import runpy
        import sys
        import warnings

        # `python -m nina_planner.pin_id` → `raise SystemExit(main())`.
        plan_file = self.proj / "entry.json"
        plan_file.write_text(
            _plan(plan_id="").model_dump_json(indent=2), encoding="utf-8"
        )
        out = io.StringIO()
        with (
            mock.patch.object(
                sys, "argv", ["nina-pin-id", "--dry-run", str(plan_file)]
            ),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            # runpy re-executes the already-imported module under __main__;
            # its "found in sys.modules" RuntimeWarning is expected here.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                runpy.run_module("nina_planner.pin_id", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("would pin", out.getvalue())

    def test_multiple_files_pin_in_one_run(self):
        first = _plan(plan_id="")
        self.derive_from(first)
        path_a = self.write_plan(first, name="a.json")
        path_b = self.write_plan(_plan(plan_id="plan-abc123"), name="b.json")

        code, out, err = self.run_pin(str(path_a), str(path_b))

        self.assertEqual(code, 0, err)
        self.assertIn(f"pinned {path_a}: {self.derived}", out)
        self.assertIn("already pinned: plan-abc123", out)

    def test_one_bad_file_fails_the_run_but_pins_the_rest(self):
        good = _plan(plan_id="")
        self.derive_from(good)
        path_a = self.write_plan(good, name="a.json")

        code, out, err = self.run_pin(str(path_a), str(self.proj / "ghost.json"))

        self.assertEqual(code, 1)
        self.assertIn(f"pinned {path_a}: {self.derived}", out)
        self.assertIn("not found", err)


class PinAcceptanceTest(PinBase):
    def test_pin_edit_progress_and_archive_force_rebuilds_same_folder(self):
        """The full acceptance chain: pin → edit → progress → archive --force.

        25 lights are shot against a 20-frame ask (a surplus), so the plan
        reads complete both before and after the edit that raises the ask
        to 25 — letting ``nina-archive --force`` rebuild the folder the
        pre-pin run built, proving the pinned id kept the name stable.
        """
        self.seed_blank(light_count=25)

        # Archive BEFORE the pin: folder named from the derived id.
        code, _out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        folder = self.folders()[0]
        self.assertEqual(folder, f"m31__{self.derived}")

        # Pin, then raise the light ask to what was already shot.
        code, _out, err = self.run_pin(str(self.plan_file))
        self.assertEqual(code, 0, err)
        data = json.loads(self.plan_file.read_text())
        data["light_frames"][0]["total_count"] = 25
        self.plan_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # get_plan_progress still counts the already-shot lights.
        progress = self.progress()
        group = self.light_group(progress)
        self.assertEqual(group["acquired_count"], 25)
        self.assertEqual(group["total_count"], 25)
        self.assertTrue(progress["complete"])

        # nina-archive --force rebuilds the SAME folder name.
        code, _out, err = self.run_cli("--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.folders(), [folder])


if __name__ == "__main__":
    unittest.main()
