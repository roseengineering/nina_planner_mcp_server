"""Tests for the nina-archive CLI (nina_planner/archive.py).

The fixture mirrors tests/test_plans_audit.py: a tmp project dir with plan
JSON files plus a tmp landing root holding real frame files and real
ImageMetaData.csv rows, so the CLI runs end-to-end (no NINA needed — every
test passes --imaging-root).

The default plan expects 20 light / 10 flat / 10 dark / 10 bias; seed_full
writes a complete, self-consistent capture set so the plan passes the
completeness gate, and per-type knobs remove pieces for the skip tests.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nina_planner import archive as archive_mod
from nina_planner.models.plan import ObservationPlan

_TOKEN = "M31 _plan-abc123_"


def _plan(**overrides):
    data = {
        "plan_id": "plan-abc123",
        "target": "M31",
        "intent": "Galaxy portrait",
        "pointings": [{"ra_hours": 0.71, "dec_deg": 41.27, "label": "M31 core"}],
        "calibration_window_days": 7,
        "light_frames": [
            {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 20}
        ],
        "flat_frames": [{"filter_name": "L", "total_count": 10}],
        "dark_frames": [{"exposure_time_seconds": 60.0, "total_count": 10}],
        "bias_frames": [{"total_count": 10}],
    }
    data.update(overrides)
    return ObservationPlan(**data)


class ArchiveBase(unittest.TestCase):
    """Tmp project dir + landing root + explicit-plan CLI invocation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.proj = base / "project"
        self.root = base / "landing"
        self.dest = base / "archive-out"
        self.proj.mkdir()
        self.root.mkdir()

    # -------------------------------------------------------- fixtures

    def write_plan(self, plan: ObservationPlan, name: str = "m31.json") -> Path:
        path = self.proj / name
        path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        return path

    def frame(self, image_type: str, name: str, *, date: str = "2026-09-20") -> Path:
        directory = self.root / "M31_plan-abc123_" / date / image_type
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(b"fits-bytes:" + name.encode())
        return path

    def csv_rows(self) -> dict[str, list[dict[str, str]]]:
        """All rows currently recorded in any ImageMetaData.csv under root."""
        out: dict[str, list[dict[str, str]]] = {}
        for csv_path in sorted(self.root.rglob("ImageMetaData.csv")):
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            out.setdefault(csv_path.parent.name, []).extend(rows)
        return out

    def write_metadata(
        self, image_type: str, rows: list[dict[str, str]], *, date: str = "2026-09-20"
    ) -> None:
        directory = self.root / "M31_plan-abc123_" / date / image_type
        directory.mkdir(parents=True, exist_ok=True)
        fieldnames = sorted({k for row in rows for k in row})
        with (directory / "ImageMetaData.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def seed_full(
        self,
        *,
        plan: ObservationPlan | None = None,
        light_count: int = 20,
        flat_count: int = 10,
        dark_count: int = 10,
        bias_count: int = 10,
        write_light_files: int | None = None,
        prefix: str = "2026-09-20_21-00-00",
    ) -> ObservationPlan:
        """Write plan + a complete capture set (frames on disk + CSV rows)."""
        plan = plan or _plan()
        self.write_plan(plan)
        light_files = (
            write_light_files if write_light_files is not None else light_count
        )

        light_rows = []
        for i in range(light_count):
            name = f"{prefix}_L__60.00s_{i:04d}.fits"
            if i < light_files:
                self.frame("LIGHT", name)
            light_rows.append(
                {
                    "ImageType": "LIGHT",
                    "FilePath": str(
                        self.root / "M31_plan-abc123_" / "2026-09-20" / "LIGHT" / name
                    ),
                    "FilterName": "L",
                    "Duration": "60.0",
                    "ExposureStartUTC": "2026-09-21T01:00:00Z",
                    "HFR": "1.5",
                    "DetectedStars": "120",
                }
            )
        if light_rows:
            self.write_metadata("LIGHT", light_rows)

        flat_rows = []
        for i in range(flat_count):
            name = f"{prefix}_L_flat_{i:04d}.fits"
            self.frame("FLAT", name)
            flat_rows.append(
                {
                    "ImageType": "FLAT",
                    "FilePath": str(
                        self.root / "M31_plan-abc123_" / "2026-09-20" / "FLAT" / name
                    ),
                    "FilterName": "L",
                    "Duration": "2.0",
                    "ExposureStartUTC": "2026-09-21T02:00:00Z",
                }
            )
        if flat_rows:
            self.write_metadata("FLAT", flat_rows)

        dark_rows = []
        for i in range(dark_count):
            name = f"{prefix}_dark_60.00s_{i:04d}.fits"
            self.frame("DARK", name)
            dark_rows.append(
                {
                    "ImageType": "DARK",
                    "FilePath": str(
                        self.root / "M31_plan-abc123_" / "2026-09-20" / "DARK" / name
                    ),
                    "Duration": "60.0",
                    "ExposureStartUTC": "2026-09-21T03:00:00Z",
                }
            )
        if dark_rows:
            self.write_metadata("DARK", dark_rows)

        bias_rows = []
        for i in range(bias_count):
            name = f"{prefix}_bias_{i:04d}.fits"
            self.frame("BIAS", name)
            bias_rows.append(
                {
                    "ImageType": "BIAS",
                    "FilePath": str(
                        self.root / "M31_plan-abc123_" / "2026-09-20" / "BIAS" / name
                    ),
                    "Duration": "0.0",
                    "ExposureStartUTC": "2026-09-21T03:30:00Z",
                }
            )
        if bias_rows:
            self.write_metadata("BIAS", bias_rows)
        return plan

    # -------------------------------------------------------------- run

    def run_cli(self, *extra: str) -> tuple[int, str, str]:
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
            code = archive_mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    def folders(self) -> list[str]:
        if not self.dest.exists():
            return []
        return sorted(
            p.name
            for p in self.dest.iterdir()
            if not p.name.startswith(".") and p.is_dir()
        )

    def index_lines(self) -> list[str]:
        index = self.dest / archive_mod._INDEX_NAME
        if not index.exists():
            return []
        return [line for line in index.read_text().splitlines() if line.strip()]


class ArchiveHappyPathTest(ArchiveBase):
    def test_complete_plan_archived_with_structure_and_sidecars(self):
        plan = self.seed_full()
        plan_file = self.proj / "m31.json"

        code, out, err = self.run_cli()

        self.assertEqual(code, 0, err)
        folder = archive_mod._folder_name(plan_file, plan)
        self.assertEqual(self.folders(), [folder])
        base = self.dest / folder
        # plan.json is a verbatim copy of the plan file.
        self.assertEqual(
            json.loads((base / "plan.json").read_text()),
            json.loads(plan_file.read_text()),
        )
        # Frame counts by type: everything the plan is credited.
        for image_type, expected in (
            ("LIGHT", 20),
            ("FLAT", 10),
            ("DARK", 10),
            ("BIAS", 10),
        ):
            files = [
                p for p in (base / image_type).iterdir() if p.suffix in (".fits", ".fz")
            ]
            self.assertEqual(len(files), expected, image_type)
            # Sidecar CSV sits next to its frames.
            self.assertTrue((base / image_type / "ImageMetaData.csv").is_file())
        # Frames are COPIED: originals still on disk.
        originals = [p for p in self.root.rglob("*.fits") if p.is_file()]
        self.assertEqual(len(originals), 50)
        # manifest.json carries identity, counts, and an empty warning list.
        manifest = json.loads((base / archive_mod._MANIFEST_NAME).read_text())
        self.assertEqual(manifest["plan_id"], "plan-abc123")
        self.assertEqual(manifest["target"], "M31")
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["counts"]["light"]["expected"], 20)
        self.assertEqual(manifest["counts"]["light"]["archived"], 20)
        self.assertEqual(manifest["counts"]["flat"]["archived"], 10)
        self.assertEqual(manifest["file_count"], 50)
        self.assertEqual(manifest["warnings"], [])
        # Disk report: nothing anomalous, nothing deliberately skipped.
        self.assertEqual(
            manifest["disk"],
            {"rejected": [], "gone": [], "not_selected": []},
        )
        # archive_path column joins CSV rows to archived files.
        with (base / "LIGHT" / "ImageMetaData.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 20)
        self.assertTrue(rows[0]["archive_path"].startswith("LIGHT/"))
        self.assertTrue((base / rows[0]["archive_path"]).is_file())
        # Append-only index has exactly one entry.
        self.assertEqual(len(self.index_lines()), 1)
        self.assertEqual(json.loads(self.index_lines()[0])["plan_id"], "plan-abc123")
        # Staging dirs never survive a successful run.
        self.assertFalse(any(p.name.startswith(".") for p in self.dest.iterdir()))

    def test_frame_bytes_survive_copy_verified_by_checksum(self):
        plan = _plan(
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 3}
            ],
            flat_frames=[{"filter_name": "L", "total_count": 2}],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 2}],
            bias_frames=[{"total_count": 2}],
        )
        self.seed_full(
            plan=plan,
            light_count=3,
            flat_count=2,
            dark_count=2,
            bias_count=2,
        )

        code, _out, err = self.run_cli("--checksum")

        self.assertEqual(code, 0, err)
        folder = self.folders()[0]
        base = self.dest / folder
        for image_type in ("LIGHT", "FLAT", "DARK", "BIAS"):
            for dst in (base / image_type).iterdir():
                if dst.suffix != ".fits":
                    continue
                matches = [p for p in self.root.rglob(dst.name) if p.is_file()]
                self.assertEqual(len(matches), 1, dst.name)
                src = matches[0]
                self.assertEqual(
                    hashlib.sha256(src.read_bytes()).hexdigest(),
                    hashlib.sha256(dst.read_bytes()).hexdigest(),
                )


class ArchiveGateTest(ArchiveBase):
    def test_incomplete_plan_skipped_without_all_flag(self):
        # 12 of 20 lights on disk -> remaining 8 -> not complete.
        self.seed_full(light_count=20, write_light_files=12)

        code, out, err = self.run_cli()

        self.assertEqual(code, 1)  # nothing archivable
        self.assertEqual(self.folders(), [])
        self.assertIn("incomplete", out)
        self.assertIn("12/20", out)  # LIGHT acquired/expected in the reason

    def test_all_flag_forces_incomplete_plan_with_warning(self):
        self.seed_full(light_count=20, write_light_files=12)

        code, out, _err = self.run_cli("--all")

        self.assertEqual(code, 0)
        self.assertEqual(len(self.folders()), 1)
        manifest = json.loads(
            (self.dest / self.folders()[0] / archive_mod._MANIFEST_NAME).read_text()
        )
        self.assertFalse(manifest["complete"])
        self.assertTrue(any("incomplete" in w for w in manifest["warnings"]))
        self.assertTrue(
            any("LIGHT: 12 archived of 20" in w for w in manifest["warnings"])
        )

    def test_never_shot_plan_never_archives(self):
        self.write_plan(_plan())  # no CSV, no frames at all

        # Default: the completeness gate reports it as incomplete.
        code, out, _err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(self.folders(), [])
        self.assertIn("incomplete", out)
        self.assertIn("LIGHT 0/20", out)

        # --all cannot conjure frames: explicit "no frames" error, no folder.
        code, _out, err = self.run_cli("--all")
        self.assertEqual(code, 1)
        self.assertIn("no frames on disk match this plan", err)
        self.assertEqual(self.folders(), [])

    def test_expired_window_plan_is_not_complete(self):
        # Lights are complete, but the plan's calibration window closed
        # (lights dated well outside calibration_window_days=7) with NO
        # calibration frames ever shot. plan_progress zeroes remaining_count
        # for a closed window, so every group reads remaining 0 — the old
        # `remaining_count == 0` gate archived this as complete. The shared
        # verdict reads acquired vs total + window_open: expired_with_deficit
        # → not archivable without --all.
        self.seed_full(light_count=20, flat_count=0, dark_count=0, bias_count=0)

        code, out, err = self.run_cli()

        self.assertEqual(code, 1, err)  # nothing archivable
        self.assertEqual(self.folders(), [])
        self.assertIn("incomplete", out)
        self.assertIn("DARK 0/10", out)  # the expired deficit, named

        # --all still archives it, honestly flagged.
        code, out, err = self.run_cli("--all")
        self.assertEqual(code, 0, err)
        manifest = json.loads(
            (self.dest / self.folders()[0] / archive_mod._MANIFEST_NAME).read_text()
        )
        self.assertFalse(manifest["complete"])
        self.assertTrue(any("incomplete" in w for w in manifest["warnings"]))

    def test_dry_run_writes_nothing(self):
        self.seed_full()

        code, out, _err = self.run_cli("--dry-run")

        self.assertEqual(code, 0)
        self.assertIn("would archive", out)
        self.assertIn("nothing written", out)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.index_lines(), [])


class ArchiveIdempotencyTest(ArchiveBase):
    def test_second_run_skips_existing_folder_unless_forced(self):
        self.seed_full()

        code1, _out1, _err1 = self.run_cli()
        code2, out2, _err2 = self.run_cli()
        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertIn("already archived", out2)
        self.assertEqual(len(self.index_lines()), 1)

        # --force rebuilds and logs a second index entry.
        code3, _out3, _err3 = self.run_cli("--force")
        self.assertEqual(code3, 0)
        self.assertEqual(len(self.index_lines()), 2)

    def test_dest_inside_project_not_rediscovered(self):
        # --dest inside the project dir: archived plan.json copies must not
        # be discovered as fresh plans on the next run.
        self.seed_full()
        inner_dest = self.proj / "archive"
        argv = [
            "--dest",
            str(inner_dest),
            "--imaging-root",
            str(self.root),
            "--project-dir",
            str(self.proj),
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code1 = archive_mod.main(argv)
            code2 = archive_mod.main(argv)
        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        # Exactly one archive folder (not one + a re-archive of plan.json).
        folders = [p for p in inner_dest.iterdir() if not p.name.startswith(".")]
        plan_folders = [p for p in folders if (p / "plan.json").exists()]
        self.assertEqual(len(plan_folders), 1)
        self.assertIn("already archived", out.getvalue())


class ArchiveAttributionTest(ArchiveBase):
    def test_plan_token_in_filename_only_archives_matching_rows(self):
        # A light row whose path lacks the plan token: recorded on disk but
        # never attributed -> must not be archived.
        plan = self.seed_full(
            # The gate now reads the shared verdict: calibration must be
            # SATISFIED (5/5 here) for the plan to archive by default —
            # a partial set under the long-closed fixture window reads
            # expired_with_deficit, not complete.
            plan=_plan(
                flat_frames=[{"filter_name": "L", "total_count": 5}],
                dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 5}],
                bias_frames=[{"total_count": 5}],
            ),
            light_count=20,
            write_light_files=20,
            flat_count=5,
            dark_count=5,
            bias_count=5,
        )
        stray = self.root / "STRAY" / "2026-09-20" / "LIGHT" / "stray.fits"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_bytes(b"stray")
        # Append the stray row to the existing LIGHT CSV, reusing its exact
        # header so DictReader maps columns positionally (a fresh 5-column
        # writer would silently misalign against the sorted-union header).
        light_csv = (
            self.root
            / "M31_plan-abc123_"
            / "2026-09-20"
            / "LIGHT"
            / "ImageMetaData.csv"
        )
        with light_csv.open(newline="") as handle:
            header = next(csv.reader(handle))
        with light_csv.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writerow(
                {
                    "ImageType": "LIGHT",
                    "FilePath": str(stray),
                    "FilterName": "L",
                    "Duration": "60.0",
                    "ExposureStartUTC": "2026-09-21T01:00:00Z",
                }
            )

        code, _out, err = self.run_cli()

        self.assertEqual(code, 0, err)
        base = self.dest / archive_mod._folder_name(self.proj / "m31.json", plan)
        archived_names = {p.name for p in (base / "LIGHT").iterdir()}
        self.assertNotIn("stray.fits", archived_names)
        self.assertEqual(len([n for n in archived_names if n.endswith(".fits")]), 20)

    def test_dark_outside_window_not_archived(self):
        # Plan anchored on 2026-09-21 lights; a dark shot 30 days later is
        # outside calibration_window_days=7 -> never selected, and the
        # plan still reads complete (window-closed semantics).
        plan = self.seed_full()
        late = self.root / "M31_plan-abc123_" / "2026-10-25" / "DARK"
        late.mkdir(parents=True, exist_ok=True)
        late_file = late / "2026-10-25_03-00-00_dark_60.00s_late.fits"
        late_file.write_bytes(b"late")
        # 10 in-window darks already written by seed_full; append the late
        # row to the late folder's CSV.
        self.write_metadata(
            "DARK",
            [
                {
                    "ImageType": "DARK",
                    "FilePath": str(late_file),
                    "Duration": "60.0",
                    "ExposureStartUTC": "2026-10-25T07:00:00Z",
                }
            ],
            date="2026-10-25",
        )

        code, _out, err = self.run_cli()

        self.assertEqual(code, 0, err)
        base = self.dest / archive_mod._folder_name(self.proj / "m31.json", plan)
        dark_names = {p.name for p in (base / "DARK").iterdir()}
        self.assertNotIn(late_file.name, dark_names)
        self.assertEqual(len([n for n in dark_names if n.endswith(".fits")]), 10)
        # ...and the disk section names where it went: recorded under this
        # plan's own token, live, and deliberately not selected — exactly
        # the plan-dir-vs-folder gap the section exists to explain.
        manifest = json.loads((base / archive_mod._MANIFEST_NAME).read_text())
        self.assertEqual(
            [d["source"] for d in manifest["disk"]["not_selected"]],
            [str(late_file)],
        )
        self.assertEqual(manifest["disk"]["rejected"], [])
        self.assertEqual(manifest["disk"]["gone"], [])


class ArchiveErrorPathTest(ArchiveBase):
    def test_all_selected_frames_missing_refuses_empty_archive_and_cleans_staging(
        self,
    ):
        # read_imaging_csv drops rows whose files are already gone, so a
        # selected row pointing at a vanished file is only reachable when
        # the file disappears between the metadata read and the copy
        # (TOCTOU) — drive _archive_one directly with a stale row to pin it.
        # Individual misses are skipped (next test), but a selection where
        # NOTHING could be copied must not commit an empty archive, and
        # staging must not survive the refusal.
        plan = self.seed_full()
        plan_file = self.proj / "m31.json"
        stale = {
            "light": [
                {
                    "file_path": str(
                        self.root / "M31_plan-abc123_" / "gone" / "LIGHT" / "x.fits"
                    )
                }
            ],
            "flat": [],
            "dark": [],
            "bias": [],
        }
        with self.assertRaisesRegex(archive_mod.ArchiveError, "could be copied"):
            archive_mod._archive_one(
                plan_file,
                plan,
                stale,
                dest=self.dest,
                complete=True,
                counts={
                    "light": {"expected": 20, "acquired": 20, "archived": 1},
                    "flat": {"expected": 10, "acquired": 10, "archived": 0},
                    "dark": {"expected": 10, "acquired": 10, "archived": 0},
                    "bias": {"expected": 10, "acquired": 10, "archived": 0},
                },
                disk={"rejected": [], "gone": [], "not_selected": []},
                imaging_root=self.root,
                write_metadata=True,
                checksum=False,
            )
        # Nothing committed, no staging left behind, no index entry.
        self.assertEqual(self.folders(), [])
        self.assertFalse(any(p.name.startswith(".") for p in self.dest.iterdir()))
        self.assertEqual(self.index_lines(), [])

    def test_frame_vanishing_before_copy_is_skipped_and_reported(self):
        # A file that disappears between the metadata read and the copy is
        # SKIPPED: the rest of the archive commits, the manifest records the
        # miss with a warning, counts reflect what shipped, and the run says
        # so — losing one frame must not lose the archive.  (Rows already
        # missing at read time never reach here: read_imaging_csv drops
        # them, which is also how BAD_ rejects stay out of selection.)
        plan = _plan(
            light_frames=[
                {"filter_name": "L", "exposure_time_seconds": 60.0, "total_count": 3}
            ],
            flat_frames=[{"filter_name": "L", "total_count": 2}],
            dark_frames=[{"exposure_time_seconds": 60.0, "total_count": 2}],
            bias_frames=[{"total_count": 2}],
        )
        self.seed_full(
            plan=plan, light_count=3, flat_count=2, dark_count=2, bias_count=2
        )
        vanished = str(
            self.root / "M31_plan-abc123_" / "2026-09-20" / "LIGHT" / "vanished.fits"
        )
        original = archive_mod._select_frames

        def inject(plan_arg, rows_arg, now_arg):
            selected = original(plan_arg, rows_arg, now_arg)
            selected["light"].append({"file_path": vanished})
            return selected

        with mock.patch.object(archive_mod, "_select_frames", side_effect=inject):
            code, out, err = self.run_cli()

        self.assertEqual(code, 0, err)
        self.assertIn("missing on disk", out)  # the ⚠ line
        base = self.dest / self.folders()[0]
        manifest = json.loads((base / archive_mod._MANIFEST_NAME).read_text())
        self.assertEqual(manifest["missing"], [{"type": "LIGHT", "source": vanished}])
        self.assertTrue(any("missing on disk" in w for w in manifest["warnings"]))
        # counts.archived is what SHIPPED (3), not what was selected (4),
        # and the sidecar lists only shipped rows.
        self.assertEqual(manifest["counts"]["light"]["archived"], 3)
        self.assertEqual(manifest["file_count"], 9)  # 3 + 2 + 2 + 2
        lights = [p for p in (base / "LIGHT").iterdir() if p.suffix == ".fits"]
        self.assertEqual(len(lights), 3)
        with (base / "LIGHT" / "ImageMetaData.csv").open(newline="") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 3)
        self.assertEqual(json.loads(self.index_lines()[0])["missing"], 1)

    def test_bad_renamed_reject_never_aborts_credits_or_ships(self):
        # NINA rejects a frame by renaming it BAD_<name> while the CSV row
        # keeps the pre-rename name. read_imaging_csv drops that row (the
        # recorded file no longer exists), so the reject is never credited
        # and never selected — which is what keeps it out of the copy loop
        # entirely: no "recorded frame missing on disk" abort, no BAD_ file
        # in the archive.
        self.seed_full()
        light_dir = self.root / "M31_plan-abc123_" / "2026-09-20" / "LIGHT"
        victim = sorted(light_dir.glob("*.fits"))[0]
        rejected = light_dir / f"BAD_{victim.name}"
        victim.rename(rejected)

        # Gate: 19 of 20 lights credited -> incomplete -> skipped, no abort.
        code, out, err = self.run_cli()
        self.assertEqual(code, 1, err)
        self.assertIn("LIGHT 19/20", out)
        self.assertEqual(self.folders(), [])

        # --all ships the 19 good frames; the reject stays behind.
        code, _out, err = self.run_cli("--all")
        self.assertEqual(code, 0, err)
        base = self.dest / self.folders()[0]
        manifest = json.loads((base / archive_mod._MANIFEST_NAME).read_text())
        self.assertFalse(manifest["complete"])
        # Never selected, so it is not a "missing" entry either.
        self.assertEqual(manifest["missing"], [])
        shipped = [p.name for p in (base / "LIGHT").iterdir() if p.suffix == ".fits"]
        self.assertEqual(len(shipped), 19)
        self.assertTrue(all(not name.startswith("BAD_") for name in shipped))
        self.assertEqual(manifest["counts"]["light"]["archived"], 19)
        # The disk section classifies the dropped row as a reject: its
        # pre-rename source, the BAD_ twin that proved it, nothing gone,
        # nothing else skipped. (And the console says so — 💽 line.)
        self.assertIn("rejected (BAD_)", _out)
        disk = manifest["disk"]
        self.assertEqual(len(disk["rejected"]), 1)
        self.assertEqual(disk["rejected"][0]["type"], "LIGHT")
        self.assertEqual(disk["rejected"][0]["source"], str(victim))
        self.assertEqual(disk["rejected"][0]["found"], f"BAD_{victim.name}")
        self.assertEqual(disk["gone"], [])
        self.assertEqual(disk["not_selected"], [])
        # Copy, never move: even the rejected original is untouched.
        self.assertTrue(rejected.is_file())

    def test_recorded_but_gone_file_reported_with_warning(self):
        # A recorded frame whose file simply vanished (no BAD_ twin) is
        # dropped by the reader exactly like a reject, but classified
        # differently: disk.gone + a manifest warning + the 💽 console line.
        # The gate still reads the shortfall honestly (19/20).
        self.seed_full()
        light_dir = self.root / "M31_plan-abc123_" / "2026-09-20" / "LIGHT"
        victim = sorted(light_dir.glob("*.fits"))[0]
        victim.unlink()

        code, out, err = self.run_cli()
        self.assertEqual(code, 1, err)
        self.assertIn("LIGHT 19/20", out)
        self.assertEqual(self.folders(), [])

        code, out, err = self.run_cli("--all")
        self.assertEqual(code, 0, err)
        self.assertIn("recorded-but-gone", out)  # the 💽 line
        base = self.dest / self.folders()[0]
        manifest = json.loads((base / archive_mod._MANIFEST_NAME).read_text())
        self.assertEqual(
            manifest["disk"]["gone"],
            [{"type": "LIGHT", "source": str(victim)}],
        )
        self.assertEqual(manifest["disk"]["rejected"], [])
        self.assertTrue(any("gone" in w for w in manifest["warnings"]))
        # Distinct from copy-time `missing`: nothing failed DURING the copy —
        # this file was already gone when the CSV was read.
        self.assertEqual(manifest["missing"], [])

    def test_copy_failure_is_wrapped_as_archive_error(self):
        # An OSError mid-copy must not crash the whole run: it surfaces as
        # ArchiveError, which the CLI records per plan and exits 1.
        plan = self.seed_full(light_count=2, flat_count=1, dark_count=1, bias_count=1)
        plan_file = self.proj / "m31.json"
        light = next(
            (self.root / "M31_plan-abc123_" / "2026-09-20" / "LIGHT").glob("*.fits")
        )
        selected = {
            "light": [{"file_path": str(light)}],
            "flat": [],
            "dark": [],
            "bias": [],
        }

        with mock.patch(
            "nina_planner.archive.shutil.copy2", side_effect=OSError("disk full")
        ):
            with self.assertRaisesRegex(archive_mod.ArchiveError, "disk full"):
                archive_mod._archive_one(
                    plan_file,
                    plan,
                    selected,
                    dest=self.dest,
                    complete=True,
                    counts={
                        "light": {"expected": 2, "acquired": 2, "archived": 1},
                        "flat": {"expected": 1, "acquired": 1, "archived": 0},
                        "dark": {"expected": 1, "acquired": 1, "archived": 0},
                        "bias": {"expected": 1, "acquired": 1, "archived": 0},
                    },
                    disk={"rejected": [], "gone": [], "not_selected": []},
                    imaging_root=self.root,
                    write_metadata=False,
                    checksum=False,
                )
        self.assertFalse(any(p.name.startswith(".") for p in self.dest.iterdir()))
        self.assertEqual(self.folders(), [])

    def test_missing_imaging_root_is_an_error_not_empty_archive(self):
        self.seed_full()
        argv = [
            "--dest",
            str(self.dest),
            "--imaging-root",
            str(self.proj / "no-such-root"),
            "--project-dir",
            str(self.proj),
        ]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = archive_mod.main(argv)
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err.getvalue())
        self.assertFalse(self.dest.exists())

    def test_dest_inside_imaging_root_refused(self):
        self.seed_full()
        argv = [
            "--dest",
            str(self.root / "archive"),  # inside the imaging root
            "--imaging-root",
            str(self.root),
            "--project-dir",
            str(self.proj),
        ]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = archive_mod.main(argv)
        self.assertEqual(code, 1)
        self.assertIn("double-count", err.getvalue())
        self.assertFalse((self.root / "archive").exists())

    def test_explicit_missing_plan_path_is_an_error(self):
        self.seed_full()
        code, _out, err = self.run_cli(str(self.proj / "ghost.json"))
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_unrelated_json_in_project_is_ignored_silently(self):
        self.seed_full()
        (self.proj / "smb_opencode.json").write_text(
            json.dumps({"model": "x", "provider": "y"}), encoding="utf-8"
        )

        code, out, err = self.run_cli()

        self.assertEqual(code, 0, err)
        self.assertIn("1 unrelated JSON file(s) ignored", out)
        self.assertEqual(len(self.folders()), 1)


class FolderNameTest(unittest.TestCase):
    def test_folder_name_is_stem_plus_plan_id(self):
        plan = _plan()
        name = archive_mod._folder_name(Path("plans/m31_galaxy_20261003.json"), plan)
        self.assertEqual(name, "m31_galaxy_20261003__plan-abc123")

    def test_folder_name_not_double_suffixed(self):
        # A stem already carrying the id (e.g. written that way) stays as-is.
        plan = _plan()
        name = archive_mod._folder_name(Path("m31__plan-abc123.json"), plan)
        self.assertEqual(name, "m31__plan-abc123")

    def test_sanitize_applied(self):
        plan = _plan(plan_id="plan-abc123")
        name = archive_mod._folder_name(Path("we/ird:name.json"), plan)
        self.assertNotIn(":", name)
        self.assertIn("plan-abc123", name)


class MainAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_returns_exit_code_without_raising(self):
        # run() takes parsed args and returns an int exit code (main() is
        # the asyncio wrapper) — pin that contract, using an empty project
        # dir so the answer does not depend on NINA or local plans.
        parser = archive_mod._build_parser()
        args = parser.parse_args(
            ["--dry-run", "--dest", "unused-dest", "--project-dir", "."]
        )
        self.assertFalse(args.all)
        self.assertFalse(args.force)
        self.assertFalse(args.checksum)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.dest, "unused-dest")

        import inspect

        self.assertTrue(inspect.iscoroutinefunction(archive_mod.run))
        with tempfile.TemporaryDirectory() as tmp:
            args.project_dir = tmp  # empty: no plan files anywhere
            out_dir = Path(tmp) / "out"
            args.dest = str(out_dir)
            code = await archive_mod.run(args)
            self.assertEqual(code, 1)  # "no plan files found" — no exception
            self.assertFalse(out_dir.exists())


if __name__ == "__main__":
    unittest.main()
