import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nina_planner.system_time import (
    NINA_EXE_DEFAULT,
    kill_nina_command,
    launch_nina_command,
    nina_exe_path,
    nina_running,
    restore_clock_powershell,
    shift_clock_powershell,
    time_simulator_enabled,
)


class CommandBuildersTest(unittest.TestCase):
    def test_shift_clock_powershell_with_ampm(self):
        # AM/PM time string with spaces — single-quote outer, double-quote inner.
        self.assertEqual(
            shift_clock_powershell("10/15/2026 11:15 PM"),
            "Start-Process powershell -ArgumentList "
            "'Set-Date \"10/15/2026 11:15 PM\"' -Verb RunAs",
        )

    def test_shift_clock_powershell_iso(self):
        self.assertEqual(
            shift_clock_powershell("2026-10-15 23:15"),
            "Start-Process powershell -ArgumentList "
            "'Set-Date \"2026-10-15 23:15\"' -Verb RunAs",
        )

    def test_shift_clock_powershell_rejects_empty(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("")

    def test_shift_clock_powershell_rejects_whitespace(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("   ")

    def test_shift_clock_powershell_rejects_single_quote(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("it's now")

    def test_restore_clock_powershell(self):
        self.assertEqual(
            restore_clock_powershell(),
            "Start-Process powershell -ArgumentList 'w32tm /resync' -Verb RunAs",
        )

    def test_launch_nina_command(self):
        nina_exe = (
            r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy"
            r"\NINA.exe"
        )
        self.assertEqual(
            launch_nina_command(nina_exe),
            ["cmd.exe", "/c", "start", "", nina_exe],
        )

    def test_kill_nina_command(self):
        self.assertEqual(
            kill_nina_command(),
            ["taskkill.exe", "/F", "/IM", "NINA.exe"],
        )


class NinaExePathTest(unittest.TestCase):
    def test_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NINA_EXE_PATH", None)
            self.assertEqual(nina_exe_path(), NINA_EXE_DEFAULT)

    def test_env_override(self):
        with patch.dict(
            os.environ, {"NINA_EXE_PATH": r"D:\apps\NINA.exe"}, clear=False
        ):
            self.assertEqual(nina_exe_path(), r"D:\apps\NINA.exe")


class NinaRunningTest(unittest.TestCase):
    def test_present(self):
        fake = MagicMock()
        fake.stdout = (
            "INFO: This is a Windows-elevated tasklist.\n"
            "Image Name                     PID Session Name        "
            "Session#    Mem Usage\n"
            "========================= ======== ================ "
            "========== ============\n"
            "NINA.exe                      12345 Console            "
            "1         123,456 K\n"
        )
        with patch("nina_planner.system_time.subprocess.run", return_value=fake):
            self.assertTrue(nina_running())

    def test_absent(self):
        fake = MagicMock()
        fake.stdout = (
            "INFO: No tasks are running which match the specified criteria.\n"
        )
        with patch("nina_planner.system_time.subprocess.run", return_value=fake):
            self.assertFalse(nina_running())

    def test_absent_with_empty_stdout(self):
        fake = MagicMock()
        fake.stdout = ""
        with patch("nina_planner.system_time.subprocess.run", return_value=fake):
            self.assertFalse(nina_running())


class TimeSimulatorGateTest(unittest.TestCase):
    def test_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NINA_TIME_SIMULATOR_ENABLED", None)
            self.assertFalse(time_simulator_enabled())

    def test_on_with_one(self):
        with patch.dict(
            os.environ, {"NINA_TIME_SIMULATOR_ENABLED": "1"}, clear=False
        ):
            self.assertTrue(time_simulator_enabled())

    def test_off_with_other_values(self):
        for value in ("0", "true", "yes", "", "ON"):
            with patch.dict(
                os.environ, {"NINA_TIME_SIMULATOR_ENABLED": value}, clear=False
            ):
                self.assertFalse(
                    time_simulator_enabled(),
                    f"value {value!r} should not enable",
                )


class AppendProgressEntryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "progress.md"
        self.progress.write_text(
            "# progress.md\n\nShared observatory progress file.\n"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _call(self, text: str) -> str:
        from nina_planner.progress import append_progress_entry

        with patch("nina_planner.server.PROJECT_DIR", self.project_dir):
            asyncio.run(append_progress_entry(text))
        return self.progress.read_text(encoding="utf-8")

    def test_appends_iso_timestamped_section(self):
        body = "shifted OS clock to '2026-10-15 23:15'"
        new_content = self._call(body)
        # Original content preserved.
        self.assertIn("# progress.md", new_content)
        # New entry has the auto header and the body verbatim.
        self.assertIn("## ", new_content)
        self.assertIn("— auto", new_content)
        self.assertIn(body, new_content)
        # Ends with a newline.
        self.assertTrue(new_content.endswith("\n"))

    def test_appends_subsequent_entries(self):
        self._call("first entry body")
        self._call("second entry body")
        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("first entry body", content)
        self.assertIn("second entry body", content)
        # Both have auto headers.
        self.assertEqual(content.count("— auto"), 2)


class EnsureNinaRunningTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "progress.md"
        self.progress.write_text("# progress.md\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_action_when_running(self):
        running_proc = MagicMock()
        running_proc.stdout = (
            "Image Name                     PID\n"
            "NINA.exe                      12345\n"
        )
        from nina_planner.server import ensure_nina_running

        with patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch(
                    "nina_planner.server._run_blocking",
                    return_value=running_proc,
                ), \
                patch("nina_planner.server._spawn_detached") as mock_spawn, \
                patch("nina_planner.server._log_payload"):
            result = asyncio.run(ensure_nina_running())
        self.assertIn("already running", result)
        self.assertIn("no action", result)
        mock_spawn.assert_not_called()
        self.assertIn(
            "NINA already running, no launch",
            self.progress.read_text(encoding="utf-8"),
        )

    def test_launches_when_not_running(self):
        absent_proc = MagicMock()
        absent_proc.stdout = "INFO: No tasks are running which match.\n"
        from nina_planner.server import ensure_nina_running

        with patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch(
                    "nina_planner.server._run_blocking", return_value=absent_proc
                ), \
                patch("nina_planner.server._spawn_detached") as mock_spawn, \
                patch("nina_planner.server._log_payload"), \
                patch.dict(
                    os.environ,
                    {"NINA_EXE_PATH": r"D:\apps\NINA.exe"},
                    clear=False,
                ):
            result = asyncio.run(ensure_nina_running())
        self.assertIn("was not running", result)
        self.assertIn("launched", result)
        self.assertIn(r"D:\apps\NINA.exe", result)
        mock_spawn.assert_called_once()
        argv = mock_spawn.call_args.args[0]
        self.assertEqual(
            argv,
            ["cmd.exe", "/c", "start", "", r"D:\apps\NINA.exe"],
        )
        self.assertIn(
            "launched NINA from D:\\apps\\NINA.exe",
            self.progress.read_text(encoding="utf-8"),
        )

    def test_default_path_when_env_unset(self):
        absent_proc = MagicMock()
        absent_proc.stdout = ""
        from nina_planner.server import ensure_nina_running

        with patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch(
                    "nina_planner.server._run_blocking", return_value=absent_proc
                ), \
                patch("nina_planner.server._spawn_detached") as mock_spawn, \
                patch("nina_planner.server._log_payload"), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NINA_EXE_PATH", None)
            asyncio.run(ensure_nina_running())
        argv = mock_spawn.call_args.args[0]
        self.assertEqual(argv[-1], NINA_EXE_DEFAULT)

    def test_probe_failure_treated_as_not_running(self):
        from nina_planner.server import ensure_nina_running

        with patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch(
                    "nina_planner.server._run_blocking",
                    side_effect=RuntimeError("tasklist crashed"),
                ), \
                patch("nina_planner.server._spawn_detached") as mock_spawn, \
                patch("nina_planner.server._log_payload"):
            result = asyncio.run(ensure_nina_running())
        self.assertIn("was not running", result)
        mock_spawn.assert_called_once()
        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("nina probe failed", content)
        self.assertIn("launched NINA", content)


if __name__ == "__main__":
    unittest.main()
