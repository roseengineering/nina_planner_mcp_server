import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import requires_wsl

from nina_planner.system_time import (
    NINA_EXE_DEFAULT,
    get_launcher_paths,
    kill_nina_command,
    local_to_windows,
    nina_exe_path,
    nina_running,
    read_windows_clock_powershell,
    restore_set_date_powershell,
    schtasks_create_command,
    schtasks_delete_command,
    schtasks_run_command,
    shift_clock_powershell,
    validate_iso_local,
    write_nina_launcher,
)


class CommandBuildersTest(unittest.TestCase):
    def test_shift_clock_powershell_iso_t_separator(self):
        self.assertEqual(
            shift_clock_powershell("2026-10-15T23:15:00"),
            "Set-Date -Date '2026-10-15T23:15:00'",
        )

    def test_shift_clock_powershell_iso_space_separator(self):
        self.assertEqual(
            shift_clock_powershell("2026-10-15 23:15:00"),
            "Set-Date -Date '2026-10-15 23:15:00'",
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

    def test_restore_set_date_powershell(self):
        self.assertEqual(
            restore_set_date_powershell("2026-10-15T23:15:00"),
            "Set-Date -Date '2026-10-15T23:15:00'",
        )

    def test_restore_set_date_powershell_rejects_empty(self):
        with self.assertRaises(ValueError):
            restore_set_date_powershell("")

    def test_read_windows_clock_powershell(self):
        self.assertEqual(read_windows_clock_powershell(), "Get-Date -Format o")

    def test_kill_nina_command(self):
        self.assertEqual(
            kill_nina_command(),
            ["taskkill.exe", "/F", "/IM", "NINA.exe"],
        )

    def test_schtasks_create_command(self):
        cmd = schtasks_create_command(r"C:\temp\launch_nina.cmd")
        self.assertEqual(
            cmd,
            [
                "schtasks.exe",
                "/Create",
                "/TN",
                "NINAPlanner-Launch",
                "/TR",
                r"C:\temp\launch_nina.cmd",
                "/SC",
                "ONCE",
                "/ST",
                "00:00",
                "/IT",
                "/F",
            ],
        )

    def test_schtasks_run_command(self):
        self.assertEqual(
            schtasks_run_command(),
            ["schtasks.exe", "/Run", "/TN", "NINAPlanner-Launch"],
        )

    def test_schtasks_delete_command(self):
        self.assertEqual(
            schtasks_delete_command(),
            ["schtasks.exe", "/Delete", "/TN", "NINAPlanner-Launch", "/F"],
        )

    def test_local_to_windows_wsl_mount(self):
        p = Path("/mnt/c/Users/george/AppData/Local/Temp/launch_nina.cmd")
        self.assertEqual(
            local_to_windows(p),
            r"C:\Users\george\AppData\Local\Temp\launch_nina.cmd",
        )

    def test_write_nina_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            real_is_dir = Path.is_dir
            wsl_temp = Path(
                f"/mnt/c/Users/{os.environ.get('USER', 'george')}/AppData/Local/Temp"
            )

            def is_dir(path):
                return False if path == wsl_temp else real_is_dir(path)

            with (
                patch("nina_planner.system_time.tempfile.gettempdir", return_value=tmp),
                patch.object(Path, "is_dir", autospec=True, side_effect=is_dir),
            ):
                local_p, win_p = write_nina_launcher(r"C:\Test\NINA.exe")
                self.assertTrue(local_p.exists())
                content = local_p.read_text(encoding="utf-8")
                self.assertIn("@echo off", content)
                self.assertIn(r'start "" "C:\Test\NINA.exe"', content)

    def test_launcher_defaults_to_system_temp_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with (
                patch("nina_planner.system_time.tempfile.gettempdir", return_value=tmp),
                patch.object(Path, "is_dir", return_value=False),
            ):
                local_p, _ = get_launcher_paths()

        self.assertEqual(local_p, tmp_path / "launch_nina.cmd")


class ValidateIsoLocalTest(unittest.TestCase):
    def test_t_separator(self):
        parsed = validate_iso_local("2026-10-15T23:15:00")
        self.assertEqual(parsed, datetime(2026, 10, 15, 23, 15, 0))
        self.assertIsNone(parsed.tzinfo)

    def test_space_separator(self):
        parsed = validate_iso_local("2026-10-15 23:15:00")
        self.assertEqual(parsed, datetime(2026, 10, 15, 23, 15, 0))
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
        fake.stdout = "INFO: No tasks are running which match the specified criteria.\n"
        with patch("nina_planner.system_time.subprocess.run", return_value=fake):
            self.assertFalse(nina_running())

    def test_absent_with_empty_stdout(self):
        fake = MagicMock()
        fake.stdout = ""
        with patch("nina_planner.system_time.subprocess.run", return_value=fake):
            self.assertFalse(nina_running())


class AppendProgressEntryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "progress.md"
        self.progress.write_text("# progress.md\n\nShared observatory progress file.\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _call(self, text: str) -> str:
        from nina_planner.progress import append_progress_entry

        with patch("nina_planner.server.PROJECT_DIR", self.project_dir):
            asyncio.run(append_progress_entry(text))
        return self.progress.read_text(encoding="utf-8")

    def test_appends_iso_timestamped_section(self):
        body = "started NINA on real time"
        new_content = self._call(body)
        self.assertIn("# progress.md", new_content)
        self.assertIn("## ", new_content)
        self.assertIn("— auto", new_content)
        self.assertIn(body, new_content)
        self.assertTrue(new_content.endswith("\n"))

    def test_appends_subsequent_entries(self):
        self._call("first entry body")
        self._call("second entry body")
        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("first entry body", content)
        self.assertIn("second entry body", content)
        self.assertEqual(content.count("— auto"), 2)


class StopNinaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "progress.md"
        self.progress.write_text("# progress.md\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_stop_when_not_running_is_noop(self):
        from nina_planner.server import stop_nina

        absent_proc = MagicMock()
        absent_proc.stdout = "INFO: No tasks running\n"

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch(
                "nina_planner.server._run_blocking", return_value=absent_proc
            ) as mock_run,
            patch("nina_planner.server._log_payload"),
        ):
            result = asyncio.run(stop_nina())

        self.assertFalse(result["stopped"])
        self.assertIn("not running", result["summary"])
        # Only tasklist probe was executed, no taskkill
        self.assertEqual(mock_run.call_count, 1)

    def test_stop_when_running_kills_process(self):
        from nina_planner.server import stop_nina

        running_proc = MagicMock()
        running_proc.stdout = "NINA.exe                      12345 Console\n"

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch(
                "nina_planner.server._run_blocking", return_value=running_proc
            ) as mock_run,
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            result = asyncio.run(stop_nina())

        self.assertTrue(result["stopped"])
        self.assertIn("terminated successfully", result["summary"])
        self.assertEqual(mock_run.call_count, 2)
        kill_argv = mock_run.call_args_list[1].args[0]
        self.assertEqual(kill_argv, ["taskkill.exe", "/F", "/IM", "NINA.exe"])
        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("stop_nina: killed running NINA", content)

    def test_stop_when_taskkill_fails_raises_runtime_error(self):
        from nina_planner.server import stop_nina

        running_proc = MagicMock()
        running_proc.stdout = "NINA.exe                      12345 Console\n"

        def fake_run(argv, timeout=30.0):
            if "tasklist.exe" in argv:
                return running_proc
            raise RuntimeError("Access denied")

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch("nina_planner.server._run_blocking", side_effect=fake_run),
            patch("nina_planner.server._log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(stop_nina())
            self.assertIn("taskkill error", str(ctx.exception))


class StartNinaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "progress.md"
        self.progress.write_text("# progress.md\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_start_errors_when_already_running(self):
        from nina_planner.server import start_nina

        running_proc = MagicMock()
        running_proc.stdout = "NINA.exe                      12345 Console\n"

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch("nina_planner.server._run_blocking", return_value=running_proc),
            patch("nina_planner.server._log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina())
            self.assertIn(
                "NINA is already running. Call stop_nina() first.", str(ctx.exception)
            )

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina("2026-10-15T23:15:00"))
            self.assertIn(
                "NINA is already running. Call stop_nina() first.", str(ctx.exception)
            )

    def test_start_real_time_launches_interactive(self):
        from nina_planner.server import start_nina

        absent_proc = MagicMock()
        absent_proc.stdout = ""

        executed_cmds = []

        def fake_run(argv, timeout=30.0):
            executed_cmds.append(argv)
            return absent_proc

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch("nina_planner.server._run_blocking", side_effect=fake_run),
            patch(
                "nina_planner.server.write_nina_launcher",
                return_value=(Path("/tmp/launch.cmd"), r"C:\tmp\launch.cmd"),
            ),
            patch("nina_planner.server._log_payload"),
        ):
            result = asyncio.run(start_nina())

        self.assertFalse(result["simulated"])
        self.assertIn("launched NINA on real time", result["summary"])
        # Commands executed: tasklist probe, schtasks /Create, schtasks /Run, schtasks /Delete
        cmd_names = [c[0] for c in executed_cmds]
        self.assertEqual(
            cmd_names, ["tasklist.exe", "schtasks.exe", "schtasks.exe", "schtasks.exe"]
        )
        create_args = executed_cmds[1]
        self.assertIn("/IT", create_args)
        self.assertIn(r"C:\tmp\launch.cmd", create_args)

        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("start_nina: launching NINA on real time", content)

    @requires_wsl
    def test_start_simulated_time_runs_full_cycle(self):
        from datetime import datetime as _dt

        from nina_planner.server import start_nina

        # Host clock reads: first call returns real0 (before shift), subsequent
        # calls return the simulated target (during shift-check), then return
        # the post-restore target (during restore-check), and finally the real
        # value for the trailing get_nina_time host reference.
        host_sequence = [
            _dt.fromisoformat("2026-09-26T16:55:00-05:00"),  # real0
            _dt.fromisoformat("2026-10-15T23:15:00-05:00"),  # shifted target
            _dt.fromisoformat("2026-10-15T23:15:00-05:00"),  # shifted target
            _dt.fromisoformat("2026-09-26T16:55:01-05:00"),  # restored
            _dt.fromisoformat("2026-09-26T16:55:01-05:00"),  # restored
            _dt.fromisoformat("2026-09-26T16:55:01-05:00"),  # restored (extra poll)
        ]
        idx = {"v": 0}

        async def fake_read_windows_clock():
            v = host_sequence[min(idx["v"], len(host_sequence) - 1)]
            idx["v"] += 1
            return v

        # Track the powershell commands executed.
        ps_commands: list[str] = []

        def fake_run_blocking(argv, timeout=30.0):
            if isinstance(argv, list) and argv and argv[0] == "powershell.exe":
                ps_commands.append(argv[-1] if argv[-1] is not None else "")
            return MagicMock(stdout="")

        async def fake_get_nina_time():
            # Phase 1 (during capture-check): return simulated time.
            # Phase 2 (after restore, final read): return simulated nina vs real host.
            if not hasattr(fake_get_nina_time, "_calls"):
                fake_get_nina_time._calls = 0  # type: ignore[attr-defined]
            fake_get_nina_time._calls += 1  # type: ignore[attr-defined]
            if fake_get_nina_time._calls <= 3:  # type: ignore[attr-defined]
                return {
                    "nina_time": "2026-10-15T23:15:00",
                    "host_time": "2026-09-26T16:55:00",
                    "delta_seconds": 17_328_000.0,
                    "simulated": True,
                }
            return {
                "nina_time": "2026-10-15T23:15:00",
                "host_time": "2026-09-26T16:55:01",
                "delta_seconds": 17_327_999.0,
                "simulated": True,
            }

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch(
                "nina_planner.server._read_windows_clock",
                side_effect=fake_read_windows_clock,
            ),
            patch("nina_planner.server._run_blocking", side_effect=fake_run_blocking),
            patch(
                "nina_planner.server.write_nina_launcher",
                return_value=(Path("/tmp/launch.cmd"), r"C:\tmp\launch.cmd"),
            ),
            patch("nina_planner.server.get_nina_time", side_effect=fake_get_nina_time),
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            result = asyncio.run(start_nina("2026-10-15T23:15:00"))

        self.assertTrue(result["simulated"])
        self.assertEqual(result["nina_time"], "2026-10-15T23:15:00")
        self.assertIn(
            "simulating observation time 2026-10-15T23:15:00", result["summary"]
        )
        self.assertIn("restored host clock to", result["summary"])
        self.assertIn("verified=True", result["summary"])

        # Assert no fixed sleeps were used as gates — the shift and restore
        # checks are poll-based via _read_windows_clock.
        self.assertGreaterEqual(idx["v"], 4)

        # Assert at least two powershell commands were executed: a shift and
        # a restore, both with the direct (no -Verb RunAs) form.
        shift_cmds = [
            c for c in ps_commands if "Set-Date -Date '2026-10-15T23:15:00'" in c
        ]
        restore_cmds = [c for c in ps_commands if c.startswith("Set-Date -Date '")]
        self.assertEqual(len(shift_cmds), 1)
        self.assertGreaterEqual(len(restore_cmds), 2)
        for cmd in ps_commands:
            self.assertNotIn("-Verb RunAs", cmd)
            self.assertNotIn("sudo", cmd)

    @requires_wsl
    def test_start_simulated_shift_timeout_restores_and_raises(self):
        from datetime import datetime as _dt

        from nina_planner.server import start_nina

        # Host clock reads: real0, then stuck on the original real value (shift failed).
        async def fake_read_windows_clock():
            return _dt.fromisoformat("2026-09-26T16:55:00-05:00")

        ps_commands: list[str] = []

        def fake_run_blocking(argv, timeout=30.0):
            if isinstance(argv, list) and argv and argv[0] == "powershell.exe":
                ps_commands.append(argv[-1] if argv[-1] is not None else "")
            return MagicMock(stdout="")

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch(
                "nina_planner.server._read_windows_clock",
                side_effect=fake_read_windows_clock,
            ),
            patch("nina_planner.server._run_blocking", side_effect=fake_run_blocking),
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina("2026-10-15T23:15:00"))
        self.assertIn("did not converge to '2026-10-15T23:15:00'", str(ctx.exception))
        # A best-effort restore Set-Date must have been issued.
        restore_cmds = [c for c in ps_commands if c.startswith("Set-Date -Date '")]
        self.assertGreaterEqual(len(restore_cmds), 1)

    @requires_wsl
    def test_start_simulated_nina_capture_timeout_restores_and_raises(self):
        from datetime import datetime as _dt

        from nina_planner.server import start_nina

        host_calls = {"n": 0}

        async def fake_read_windows_clock():
            host_calls["n"] += 1
            # First call: real0. After that: shifted target (so shift-check passes).
            if host_calls["n"] == 1:
                return _dt.fromisoformat("2026-09-26T16:55:00-05:00")
            return _dt.fromisoformat("2026-10-15T23:15:00-05:00")

        ps_commands: list[str] = []

        def fake_run_blocking(argv, timeout=30.0):
            if isinstance(argv, list) and argv and argv[0] == "powershell.exe":
                ps_commands.append(argv[-1] if argv[-1] is not None else "")
            return MagicMock(stdout="")

        # NINA never reports the simulated date.
        async def fake_get_nina_time():
            return {
                "nina_time": "2026-09-26T16:55:00",
                "host_time": "2026-09-26T16:55:00",
                "delta_seconds": 0.0,
                "simulated": False,
            }

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch(
                "nina_planner.server._read_windows_clock",
                side_effect=fake_read_windows_clock,
            ),
            patch("nina_planner.server._run_blocking", side_effect=fake_run_blocking),
            patch(
                "nina_planner.server.write_nina_launcher",
                return_value=(Path("/tmp/launch.cmd"), r"C:\tmp\launch.cmd"),
            ),
            patch("nina_planner.server.get_nina_time", side_effect=fake_get_nina_time),
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina("2026-10-15T23:15:00"))
        self.assertIn("NINA never captured simulated date", str(ctx.exception))
        # Restore Set-Date must have run even though capture failed.
        restore_cmds = [c for c in ps_commands if c.startswith("Set-Date -Date '")]
        self.assertGreaterEqual(len(restore_cmds), 1)

    @requires_wsl
    def test_start_simulated_restore_timeout_raises(self):
        from datetime import datetime as _dt

        from nina_planner.server import start_nina

        host_calls = {"n": 0}

        async def fake_read_windows_clock():
            host_calls["n"] += 1
            # 1) real0, 2) shifted target (shift-check), 3+) never converges to
            # the restore target — stays on the simulated date instead.
            if host_calls["n"] == 1:
                return _dt.fromisoformat("2026-09-26T16:55:00-05:00")
            return _dt.fromisoformat("2026-10-15T23:15:00-05:00")

        ps_commands: list[str] = []

        def fake_run_blocking(argv, timeout=30.0):
            if isinstance(argv, list) and argv and argv[0] == "powershell.exe":
                ps_commands.append(argv[-1] if argv[-1] is not None else "")
            return MagicMock(stdout="")

        async def fake_get_nina_time():
            return {
                "nina_time": "2026-10-15T23:15:00",
                "host_time": "2026-09-26T16:55:00",
                "delta_seconds": 17_328_000.0,
                "simulated": True,
            }

        with (
            patch("nina_planner.server.PROJECT_DIR", self.project_dir),
            patch(
                "nina_planner.server._read_windows_clock",
                side_effect=fake_read_windows_clock,
            ),
            patch("nina_planner.server._run_blocking", side_effect=fake_run_blocking),
            patch(
                "nina_planner.server.write_nina_launcher",
                return_value=(Path("/tmp/launch.cmd"), r"C:\tmp\launch.cmd"),
            ),
            patch("nina_planner.server.get_nina_time", side_effect=fake_get_nina_time),
            patch("nina_planner.server.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.server._log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina("2026-10-15T23:15:00"))
        self.assertIn("host clock did not restore", str(ctx.exception))

    def test_start_rejects_empty_string(self):
        from nina_planner.server import start_nina

        with self.assertRaises(ValueError):
            asyncio.run(start_nina(""))

        with self.assertRaises(ValueError):
            asyncio.run(start_nina("   "))

    def test_start_rejects_ampm(self):
        from nina_planner.server import start_nina

        with self.assertRaises(ValueError):
            asyncio.run(start_nina("10/15/2026 11:15 PM"))

    def test_start_rejects_z_suffix(self):
        from nina_planner.server import start_nina

        with self.assertRaises(ValueError):
            asyncio.run(start_nina("2026-10-15T23:15:00Z"))


class GetNinaTimeTest(unittest.TestCase):
    def _fake_api_time(self, iso: str):
        async def fake(path: str):
            assert path == "/time"
            return iso

        return fake

    def _fixed_host(self, iso: str):
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

        with (
            self._fixed_host("2026-09-26T13:00:00"),
            patch(
                "nina_planner.server._api_get",
                side_effect=self._fake_api_time("2026-09-26T13:00:00"),
            ),
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
        with (
            self._fixed_host(host_now.isoformat()),
            patch(
                "nina_planner.server._api_get",
                side_effect=self._fake_api_time(nina_ahead.isoformat()),
            ),
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
