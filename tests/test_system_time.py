import asyncio
import os
import tempfile
import unittest
from datetime import datetime
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
    validate_iso_local,
)


class CommandBuildersTest(unittest.TestCase):
    def test_shift_clock_powershell_iso_t_separator(self):
        self.assertEqual(
            shift_clock_powershell("2026-10-15T23:15:00"),
            "Start-Process powershell -ArgumentList "
            "'Set-Date \"2026-10-15T23:15:00\"' -Verb RunAs",
        )

    def test_shift_clock_powershell_iso_space_separator(self):
        self.assertEqual(
            shift_clock_powershell("2026-10-15 23:15:00"),
            "Start-Process powershell -ArgumentList "
            "'Set-Date \"2026-10-15 23:15:00\"' -Verb RunAs",
        )

    def test_shift_clock_powershell_rejects_ampm(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("10/15/2026 11:15 PM")

    def test_shift_clock_powershell_rejects_locale_format(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("15/10/2026 23:15")

    def test_shift_clock_powershell_rejects_empty(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("")

    def test_shift_clock_powershell_rejects_whitespace(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("   ")

    def test_shift_clock_powershell_rejects_single_quote(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("it's now")

    def test_shift_clock_powershell_rejects_z_suffix(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("2026-10-15T23:15:00Z")

    def test_shift_clock_powershell_rejects_offset(self):
        with self.assertRaises(ValueError):
            shift_clock_powershell("2026-10-15T23:15:00+02:00")

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


class ValidateIsoLocalTest(unittest.TestCase):
    def test_t_separator(self):
        parsed = validate_iso_local("2026-10-15T23:15:00")
        self.assertEqual(
            parsed, datetime(2026, 10, 15, 23, 15, 0)
        )
        self.assertIsNone(parsed.tzinfo)

    def test_space_separator(self):
        parsed = validate_iso_local("2026-10-15 23:15:00")
        self.assertEqual(
            parsed, datetime(2026, 10, 15, 23, 15, 0)
        )
        self.assertIsNone(parsed.tzinfo)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_iso_local("")

    def test_rejects_whitespace(self):
        with self.assertRaises(ValueError):
            validate_iso_local("   ")

    def test_rejects_single_quote(self):
        with self.assertRaises(ValueError):
            validate_iso_local("it's now")

    def test_rejects_z_suffix(self):
        with self.assertRaises(ValueError):
            validate_iso_local("2026-10-15T23:15:00Z")

    def test_rejects_positive_offset(self):
        with self.assertRaises(ValueError):
            validate_iso_local("2026-10-15T23:15:00+02:00")

    def test_rejects_negative_offset(self):
        with self.assertRaises(ValueError):
            validate_iso_local("2026-10-15T23:15:00-05:00")

    def test_rejects_ampm(self):
        with self.assertRaises(ValueError):
            validate_iso_local("10/15/2026 11:15 PM")

    def test_rejects_non_date(self):
        with self.assertRaises(ValueError):
            validate_iso_local("not a date")


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


class SimulateObservationTimeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "progress.md"
        self.progress.write_text("# progress.md\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _enabled_env(self):
        return patch.dict(
            os.environ,
            {"NINA_TIME_SIMULATOR_ENABLED": "1", "NINA_EXE_PATH": r"D:\apps\NINA.exe"},
            clear=False,
        )

    def test_simulate_mode_runs_full_path(self):
        absent_proc = MagicMock()
        absent_proc.stdout = ""
        from nina_planner.server import simulate_observation_time

        with self._enabled_env(), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch(
                    "nina_planner.server._run_blocking", return_value=absent_proc
                ), \
                patch(
                    "nina_planner.server._spawn_detached"
                ) as mock_spawn, \
                patch(
                    "nina_planner.server.anyio.sleep", new=AsyncMockSleep()
                ), \
                patch("nina_planner.server._log_payload"):
            result = asyncio.run(
                simulate_observation_time("2026-10-15T23:15:00")
            )
        self.assertIn("simulate_observation_time:", result)
        self.assertIn("shifted OS clock to 2026-10-15T23:15:00", result)
        # Three spawn_detached calls: shift (sudo powershell Set-Date), launch
        # (NINA), restore (sudo powershell w32tm /resync).
        self.assertEqual(mock_spawn.call_count, 3)
        shift_argv = mock_spawn.call_args_list[0].args[0]
        self.assertEqual(shift_argv[0], "sudo")
        self.assertIn("Set-Date \"2026-10-15T23:15:00\"", " ".join(shift_argv))
        launch_argv = mock_spawn.call_args_list[1].args[0]
        self.assertEqual(
            launch_argv, ["cmd.exe", "/c", "start", "", r"D:\apps\NINA.exe"]
        )
        restore_argv = mock_spawn.call_args_list[2].args[0]
        self.assertIn("w32tm /resync", " ".join(restore_argv))
        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("simulate_observation_time:", content)
        self.assertIn("2026-10-15T23:15:00", content)

    def test_simulate_mode_rejects_empty_when(self):
        from nina_planner.server import simulate_observation_time

        with self._enabled_env(), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch("nina_planner.server._log_payload"):
            with self.assertRaises(ValueError):
                asyncio.run(simulate_observation_time(""))

    def test_simulate_mode_rejects_ampm_when(self):
        from nina_planner.server import simulate_observation_time

        with self._enabled_env(), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch("nina_planner.server._log_payload"):
            with self.assertRaises(ValueError):
                asyncio.run(simulate_observation_time("10/15/2026 11:15 PM"))

    def test_simulate_mode_rejects_z_suffix(self):
        from nina_planner.server import simulate_observation_time

        with self._enabled_env(), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch("nina_planner.server._log_payload"):
            with self.assertRaises(ValueError):
                asyncio.run(simulate_observation_time("2026-10-15T23:15:00Z"))

    def test_reset_mode_kills_runs_resync_relaunches_polls(self):
        running_proc = MagicMock()
        running_proc.stdout = "NINA.exe                      12345\n"
        from nina_planner.server import simulate_observation_time

        # Pre-populate a fake get_nina_time that converges immediately.
        async def fake_get_nina_time():
            return {
                "nina_time": "2026-09-26T13:00:00",
                "host_time": "2026-09-26T13:00:01",
                "delta_seconds": -1.0,
                "simulated": False,
            }

        with self._enabled_env(), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch(
                    "nina_planner.server._run_blocking",
                    return_value=running_proc,
                ) as mock_run, \
                patch(
                    "nina_planner.server._spawn_detached"
                ) as mock_spawn, \
                patch(
                    "nina_planner.server.get_nina_time",
                    side_effect=fake_get_nina_time,
                ), \
                patch(
                    "nina_planner.server.anyio.sleep", new=AsyncMockSleep()
                ), \
                patch("nina_planner.server._log_payload"):
            result = asyncio.run(
                simulate_observation_time(reset=True)
            )
        # taskkill + sleep + (resync via spawn) + (launch via spawn) = 2 runs
        # of _run_blocking (probe + taskkill) and 2 spawns (resync + launch).
        self.assertEqual(mock_run.call_count, 2)
        kill_argv = mock_run.call_args_list[1].args[0]
        self.assertEqual(kill_argv, ["taskkill.exe", "/F", "/IM", "NINA.exe"])
        self.assertEqual(mock_spawn.call_count, 2)
        resync_argv = mock_spawn.call_args_list[0].args[0]
        self.assertIn("w32tm /resync", " ".join(resync_argv))
        launch_argv = mock_spawn.call_args_list[1].args[0]
        self.assertEqual(
            launch_argv, ["cmd.exe", "/c", "start", "", r"D:\apps\NINA.exe"]
        )
        self.assertIn("reset mode", result)
        self.assertIn("killed running NINA", result)
        self.assertIn("w32tm /resync", result)
        self.assertIn("relaunched NINA", result)
        self.assertIn("converged", result)
        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("simulate_observation_time(reset=True)", content)

    def test_reset_mode_kills_even_when_not_running(self):
        absent_proc = MagicMock()
        absent_proc.stdout = ""
        from nina_planner.server import simulate_observation_time

        async def fake_get_nina_time():
            return {
                "nina_time": "2026-09-26T13:00:00",
                "host_time": "2026-09-26T13:00:00",
                "delta_seconds": 0.0,
                "simulated": False,
            }

        with self._enabled_env(), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch(
                    "nina_planner.server._run_blocking", return_value=absent_proc
                ), \
                patch("nina_planner.server._spawn_detached") as mock_spawn, \
                patch(
                    "nina_planner.server.get_nina_time",
                    side_effect=fake_get_nina_time,
                ), \
                patch(
                    "nina_planner.server.anyio.sleep", new=AsyncMockSleep()
                ), \
                patch("nina_planner.server._log_payload"):
            result = asyncio.run(simulate_observation_time(reset=True))
        # taskkill ran but logged that NINA was not running.
        self.assertIn("not running", result)
        self.assertEqual(mock_spawn.call_count, 2)

    def test_reset_mode_rejects_when_argument(self):
        from nina_planner.server import simulate_observation_time

        with self._enabled_env(), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch("nina_planner.server._log_payload"):
            with self.assertRaises(ValueError):
                asyncio.run(
                    simulate_observation_time(
                        when="2026-10-15T23:15:00", reset=True
                    )
                )

    def test_disabled_when_env_not_set(self):
        from nina_planner.server import simulate_observation_time

        with patch.dict(os.environ, {}, clear=False), \
                patch("nina_planner.server.PROJECT_DIR", self.project_dir), \
                patch("nina_planner.server._log_payload"):
            os.environ.pop("NINA_TIME_SIMULATOR_ENABLED", None)
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    simulate_observation_time("2026-10-15T23:15:00")
                )


class GetNinaTimeTest(unittest.TestCase):
    def _fake_api_time(self, iso: str):
        """Build a fake _api_get callable that returns /time payload."""
        async def fake(path: str):
            assert path == "/time"
            return iso
        return fake

    def _fixed_host(self, iso: str):
        """Patch nina_planner.server.datetime so .now() returns a fixed value."""
        from nina_planner import server as server_mod
        fixed = datetime.fromisoformat(iso)

        class FakeDatetime:
            @staticmethod
            def fromisoformat(s):
                return datetime.fromisoformat(s)

            @staticmethod
            def now(tz=None):
                if tz is None:
                    return fixed
                return fixed.astimezone(tz)

        return patch.object(server_mod, "datetime", FakeDatetime)

    def test_nina_and_host_within_tolerance(self):
        from nina_planner.server import get_nina_time

        with self._fixed_host("2026-09-26T13:00:00"), patch(
            "nina_planner.server._api_get",
            side_effect=self._fake_api_time("2026-09-26T13:00:00"),
        ):
            result = asyncio.run(get_nina_time())
        self.assertIn("nina_time", result)
        self.assertIn("host_time", result)
        self.assertIn("delta_seconds", result)
        self.assertIn("simulated", result)
        self.assertFalse(result["simulated"])
        self.assertAlmostEqual(result["delta_seconds"], 0.0, places=1)

    def test_simulated_when_nina_ahead_by_60s(self):
        from datetime import timedelta

        from nina_planner.server import get_nina_time

        host_now = datetime.fromisoformat("2026-09-26T13:00:00")
        nina_ahead = host_now + timedelta(seconds=60)
        with self._fixed_host(host_now.isoformat()), patch(
            "nina_planner.server._api_get",
            side_effect=self._fake_api_time(nina_ahead.isoformat()),
        ):
            result = asyncio.run(get_nina_time())
        self.assertTrue(result["simulated"])
        self.assertAlmostEqual(result["delta_seconds"], 60.0, delta=1.0)

    def test_handles_nina_aware_timestamp(self):
        from nina_planner.server import get_nina_time

        with patch(
            "nina_planner.server._api_get",
            side_effect=self._fake_api_time("2026-09-26T13:00:00+00:00"),
        ):
            result = asyncio.run(get_nina_time())
        # Function should not crash on aware NINA timestamps. The precise
        # delta depends on the test runner's local timezone offset, so we
        # only assert structural correctness.
        self.assertIn("nina_time", result)
        self.assertIn("host_time", result)
        self.assertIn("delta_seconds", result)
        self.assertIn("simulated", result)
        self.assertIsInstance(result["delta_seconds"], float)


class AsyncMockSleep:
    """Async sleep stand-in that returns immediately."""

    async def __call__(self, *_args, **_kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
