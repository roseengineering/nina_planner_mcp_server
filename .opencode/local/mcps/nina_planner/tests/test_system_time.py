import asyncio
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from nina_planner.system_time import (
    NINA_EXE_DEFAULT,
    get_launcher_paths,
    kill_nina_command,
    nina_exe_path,
    schtasks_create_command,
    schtasks_delete_command,
    schtasks_run_command,
    windows_interop_available,
    write_nina_launcher,
)


class CommandBuildersTest(unittest.TestCase):
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

    def test_write_nina_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.system_time.sys.platform", "linux"),
                # Keep the host Path for real I/O into the temporary directory.
                patch(
                    "nina_planner.system_time.subprocess.run",
                    return_value=MagicMock(
                        returncode=0,
                        stdout=r"C:\Users\test\AppData\Local\Temp" + "\r\n",
                    ),
                ),
                patch.dict(os.environ, {"NINA_DRIVE_MOUNT": tmp}),
            ):
                local_p, win_p = write_nina_launcher(r"C:\Test\NINA.exe")
                self.assertEqual(
                    local_p,
                    Path(tmp)
                    / "Users"
                    / "test"
                    / "AppData"
                    / "Local"
                    / "Temp"
                    / "launch_nina.cmd",
                )
                self.assertEqual(
                    win_p,
                    r"C:\Users\test\AppData\Local\Temp\launch_nina.cmd",
                )
                self.assertTrue(local_p.exists())
                content = local_p.read_text(encoding="utf-8")
                self.assertIn("@echo off", content)
                self.assertIn(r'start "" "C:\Test\NINA.exe"', content)

    def test_write_nina_launcher_escapes_percent_signs(self):
        # %VAR% expands even inside quotes in a .cmd file, so a literal
        # percent must be doubled (batch's escape) or NINA_EXE_PATH could
        # launch whatever the variable names.
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.system_time.sys.platform", "win32"),
                patch("nina_planner.system_time.tempfile.gettempdir", return_value=tmp),
                patch("nina_planner.system_time.subprocess.run") as run,
                patch.dict(os.environ, {"NINA_EXE_PATH": r"C:\apps\100%pure\NINA.exe"}),
            ):
                local_p, _ = write_nina_launcher()
            run.assert_not_called()
            content = local_p.read_text(encoding="utf-8")
            self.assertIn(r'start "" "C:\apps\100%%pure\NINA.exe"', content)
            self.assertNotIn('"C:\\apps\\100%pure', content)

    def test_write_nina_launcher_rejects_quote_injection(self):
        # A quote closes the start "" "…" quoting; whatever follows
        # (& calc.exe, a newline) would run as Task Scheduler commands.
        # Windows paths cannot contain quotes, so this must fail closed.
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.system_time.sys.platform", "win32"),
                patch("nina_planner.system_time.tempfile.gettempdir", return_value=tmp),
                patch("nina_planner.system_time.subprocess.run"),
            ):
                with self.assertRaises(ValueError) as ctx:
                    write_nina_launcher(r'C:\NINA" & calc.exe & "')
            self.assertIn("NINA_EXE_PATH", str(ctx.exception))
            self.assertFalse((Path(tmp) / "launch_nina.cmd").exists())

    def test_write_nina_launcher_rejects_newline_injection(self):
        # A CR/LF would terminate the start line and run the rest as its
        # own command line.
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.system_time.sys.platform", "win32"),
                patch("nina_planner.system_time.tempfile.gettempdir", return_value=tmp),
                patch("nina_planner.system_time.subprocess.run"),
            ):
                with self.assertRaises(ValueError):
                    write_nina_launcher("C:\\NINA.exe\r\n& calc.exe")
            self.assertFalse((Path(tmp) / "launch_nina.cmd").exists())

    def test_launcher_uses_windows_temp_directory_under_wsl(self):
        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch("nina_planner.system_time.Path", PurePosixPath),
            patch(
                "nina_planner.system_time.subprocess.run",
                return_value=MagicMock(
                    returncode=0,
                    stdout=r"C:\Users\test\AppData\Local\Temp" + "\r\n",
                ),
            ),
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/mnt/c"}),
        ):
            local_p, windows_p = get_launcher_paths()

        self.assertEqual(
            local_p,
            PurePosixPath("/mnt/c/Users/test/AppData/Local/Temp/launch_nina.cmd"),
        )
        self.assertEqual(
            windows_p,
            r"C:\Users\test\AppData\Local\Temp\launch_nina.cmd",
        )

    def test_launcher_uses_native_windows_temp_directory(self):
        with (
            patch("nina_planner.system_time.sys.platform", "win32"),
            patch("nina_planner.system_time.Path", PureWindowsPath),
            patch(
                "nina_planner.system_time.tempfile.gettempdir",
                return_value=r"D:\NINA Temp",
            ) as gettempdir,
            patch("nina_planner.system_time.subprocess.run") as run,
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/ignored"}),
        ):
            local_p, windows_p = get_launcher_paths()

        self.assertEqual(local_p, PureWindowsPath(r"D:\NINA Temp\launch_nina.cmd"))
        self.assertEqual(windows_p, r"D:\NINA Temp\launch_nina.cmd")
        gettempdir.assert_called_once_with()
        run.assert_not_called()

    def test_write_nina_launcher_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("nina_planner.system_time.sys.platform", "win32"),
                patch(
                    "nina_planner.system_time.tempfile.gettempdir", return_value=tmp
                ) as gettempdir,
                patch("nina_planner.system_time.subprocess.run") as run,
                patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/ignored"}),
            ):
                # Real writes use the host Path even when simulating win32.
                local_p, windows_p = write_nina_launcher(r"C:\Test\NINA.exe")

            self.assertEqual(local_p, Path(tmp) / "launch_nina.cmd")
            self.assertEqual(windows_p, str(local_p))
            content = local_p.read_text(encoding="utf-8")
            self.assertIn("@echo off", content)
            self.assertIn(r'start "" "C:\Test\NINA.exe"', content)
            gettempdir.assert_called_once_with()
            run.assert_not_called()


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


class WindowsInteropAvailableTest(unittest.TestCase):
    def test_true_on_win32(self):
        with patch("nina_planner.system_time.sys.platform", "win32"):
            self.assertTrue(windows_interop_available())

    def test_false_on_non_wsl(self):
        with (
            patch("nina_planner.system_time.sys.platform", "darwin"),
            patch("nina_planner.system_time._is_wsl", return_value=False),
        ):
            self.assertFalse(windows_interop_available())

    def test_wsl_true_when_binaries_present(self):
        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch("nina_planner.system_time._is_wsl", return_value=True),
            patch(
                "nina_planner.system_time.shutil.which",
                side_effect=lambda exe: f"/mnt/c/{exe}",
            ),
        ):
            self.assertTrue(windows_interop_available())

    def test_wsl_false_when_a_binary_is_missing(self):
        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch("nina_planner.system_time._is_wsl", return_value=True),
            patch(
                "nina_planner.system_time.shutil.which",
                side_effect=lambda exe: (
                    None if exe == "schtasks.exe" else f"/mnt/c/{exe}"
                ),
            ),
        ):
            self.assertFalse(windows_interop_available())


class StopNinaTest(unittest.TestCase):
    def test_stop_when_not_running_is_noop(self):
        from nina_planner.server import stop_nina

        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(False, "connection refused")),
            ),
            patch("nina_planner.client.run_blocking") as mock_run,
            patch("nina_planner.client.log_payload"),
        ):
            result = asyncio.run(stop_nina())

        self.assertFalse(result["stopped"])
        self.assertIn("not running", result["summary"])
        self.assertIn("Probe error: connection refused", result["summary"])
        # API said NINA is down, so no taskkill was attempted.
        mock_run.assert_not_called()

    def test_stop_when_running_kills_process(self):
        from nina_planner.server import stop_nina

        with (
            patch(
                # First probe: NINA is up (stop proceeds to taskkill). Second
                # probe: the kill verification poll sees the API gone.
                "nina_planner.client.api_probe",
                AsyncMock(
                    side_effect=[
                        (True, None),
                        (False, "connection refused"),
                    ]
                ),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.client.run_blocking",
                AsyncMock(return_value=MagicMock(returncode=0)),
            ) as mock_run,
            patch("nina_planner.tools.lifecycle.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.client.log_payload"),
        ):
            result = asyncio.run(stop_nina())

        self.assertTrue(result["stopped"])
        self.assertIn("terminated successfully", result["summary"])
        mock_run.assert_awaited_once()
        kill_argv = mock_run.call_args.args[0]
        self.assertEqual(kill_argv, ["taskkill.exe", "/F", "/IM", "NINA.exe"])

    def test_stop_when_remote_raises_runtime_error(self):
        from nina_planner.server import stop_nina

        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=False,
            ),
            patch("nina_planner.client.run_blocking") as mock_run,
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(stop_nina())
            self.assertIn("remote host", str(ctx.exception))
        mock_run.assert_not_called()

    def test_stop_when_taskkill_fails_raises_runtime_error(self):
        from nina_planner.server import stop_nina

        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.client.run_blocking",
                AsyncMock(side_effect=RuntimeError("Access denied")),
            ),
            patch("nina_planner.tools.lifecycle.anyio.sleep", new=AsyncMockSleep()),
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(stop_nina())
            self.assertIn("taskkill error", str(ctx.exception))

    def test_stop_raises_when_the_api_still_answers_after_taskkill(self):
        from nina_planner.server import stop_nina

        # taskkill returned cleanly but NINA keeps answering: the kill did
        # not take, and the caller must be told rather than told "stopped".
        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.client.run_blocking",
                AsyncMock(return_value=MagicMock(returncode=0, stderr="")),
            ),
            patch(
                "nina_planner.tools.lifecycle._poll_until_async",
                AsyncMock(return_value=False),
            ),
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(stop_nina())
        message = str(ctx.exception)
        self.assertIn("still answers after taskkill", message)
        self.assertIn("taskkill exited 0", message)

    def test_stop_notes_a_taskkill_failure_that_still_ended_nina(self):
        from nina_planner.server import stop_nina

        # taskkill's own exit code is not the end state: NINA may have
        # exited on its own between the probe and the kill.
        with (
            patch(
                "nina_planner.client.api_probe",
                AsyncMock(return_value=(True, None)),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch(
                "nina_planner.client.run_blocking",
                AsyncMock(
                    return_value=MagicMock(
                        returncode=1, stderr="ERROR: The process NINA.exe not found."
                    )
                ),
            ),
            patch(
                "nina_planner.tools.lifecycle._poll_until_async",
                AsyncMock(return_value=True),
            ),
            patch("nina_planner.client.log_payload"),
        ):
            result = asyncio.run(stop_nina())
        self.assertTrue(result["stopped"])
        self.assertIn(
            "(note: taskkill exited 1: ERROR: The process NINA.exe not found.",
            result["summary"],
        )

    def test_poll_until_async_gives_up_at_the_deadline(self):
        from nina_planner.tools.lifecycle import _poll_until_async

        async def _never():
            return False

        # One fruitless poll past the deadline, then False — never an
        # unbounded wait for a state that is not coming.
        self.assertFalse(asyncio.run(_poll_until_async(_never, 0.01, 0.05)))

    def test_poll_until_async_returns_true_on_the_first_hit(self):
        from nina_planner.tools.lifecycle import _poll_until_async

        async def _always():
            return True

        self.assertTrue(asyncio.run(_poll_until_async(_always, 5.0, 0.5)))


class StartNinaTest(unittest.TestCase):
    def test_start_errors_when_already_running(self):
        from nina_planner.server import start_nina

        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=True),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina())
            self.assertIn(
                "NINA is already running. Call stop_nina() first.", str(ctx.exception)
            )

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina())
            self.assertIn(
                "NINA is already running. Call stop_nina() first.", str(ctx.exception)
            )

    def test_start_when_running_on_a_remote_host_without_interop(self):
        from nina_planner.server import start_nina

        # NINA answers, but this host cannot touch it: the message must say
        # so instead of pretending the agent will restart it.
        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=True),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=False,
            ),
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina())
        self.assertIn("no Windows interop", str(ctx.exception))
        self.assertIn("its own host", str(ctx.exception))

    def test_start_logs_a_failed_task_delete_without_failing(self):
        from nina_planner.server import start_nina

        def fake_run(argv, timeout=30.0):
            if "/Delete" in argv:
                raise RuntimeError("Access denied")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch("nina_planner.client.run_blocking", side_effect=fake_run),
            patch(
                "nina_planner.tools.lifecycle.write_nina_launcher",
                return_value=self._STUB_LAUNCHER_PATHS,
            ),
            patch("nina_planner.client.log_payload") as log,
        ):
            result = asyncio.run(start_nina())

        # The launch itself succeeded; the cleanup failure is a warning.
        self.assertIn("launched NINA", result["summary"])
        warnings = [
            call.args[0]
            for call in log.call_args_list
            if call.args and call.args[0].startswith("_launch_nina_interactive")
        ]
        self.assertIn("_launch_nina_interactive: delete task warning", warnings)

    def test_start_aborts_when_lifecycle_control_unavailable(self):
        from nina_planner.server import start_nina

        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=False,
            ),
            patch(
                "nina_planner.tools.lifecycle._launch_nina_interactive",
                new_callable=AsyncMock,
            ) as launch,
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot be launched from here"):
                asyncio.run(start_nina())

        launch.assert_not_awaited()

    _STUB_LAUNCHER_PATHS = (
        Path("/tmp/launch_nina.cmd"),
        r"C:\Users\user\AppData\Local\Temp\launch_nina.cmd",
    )

    def test_start_real_time_launches_interactive(self):
        from nina_planner.server import start_nina

        absent_proc = MagicMock()
        absent_proc.returncode = 0
        absent_proc.stdout = ""

        executed_cmds = []

        def fake_run(argv, timeout=30.0):
            executed_cmds.append(argv)
            return absent_proc

        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch("nina_planner.client.run_blocking", side_effect=fake_run),
            patch(
                "nina_planner.tools.lifecycle.write_nina_launcher",
                return_value=self._STUB_LAUNCHER_PATHS,
            ),
            patch("nina_planner.client.log_payload"),
        ):
            result = asyncio.run(start_nina())

        self.assertIn("launched NINA", result["summary"])
        # Commands executed: schtasks /Create, /Run, and /Delete.
        cmd_names = [c[0] for c in executed_cmds]
        self.assertEqual(cmd_names, ["schtasks.exe", "schtasks.exe", "schtasks.exe"])
        create_args = executed_cmds[0]
        self.assertIn("/IT", create_args)
        self.assertIn(self._STUB_LAUNCHER_PATHS[1], create_args)

    def test_start_real_time_raises_when_task_creation_fails(self):
        from nina_planner.server import start_nina

        executed_cmds = []

        def fake_run(argv, timeout=30.0):
            executed_cmds.append(argv)
            return MagicMock(returncode=1, stdout="", stderr="Access denied")

        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch("nina_planner.client.run_blocking", side_effect=fake_run),
            patch(
                "nina_planner.tools.lifecycle.write_nina_launcher",
                return_value=self._STUB_LAUNCHER_PATHS,
            ),
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "task creation command.*exit code 1.*Access denied"
            ):
                asyncio.run(start_nina())

        self.assertEqual([cmd[0] for cmd in executed_cmds], ["schtasks.exe"])

    def test_start_real_time_raises_when_task_execution_fails(self):
        from nina_planner.server import start_nina

        executed_cmds = []

        def fake_run(argv, timeout=30.0):
            executed_cmds.append(argv)
            if "/Run" in argv:
                return MagicMock(returncode=1, stdout="", stderr="Task failed")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch("nina_planner.client.run_blocking", side_effect=fake_run),
            patch(
                "nina_planner.tools.lifecycle.write_nina_launcher",
                return_value=self._STUB_LAUNCHER_PATHS,
            ),
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "task execution command.*exit code 1.*Task failed"
            ):
                asyncio.run(start_nina())

        self.assertEqual(
            [cmd[0] for cmd in executed_cmds],
            ["schtasks.exe", "schtasks.exe", "schtasks.exe"],
        )
        self.assertIn("/Delete", executed_cmds[-1])

    def test_start_routes_timeout_expired_into_a_contextual_error(self):
        """A hung command fails like any other launch failure.

        `subprocess.TimeoutExpired` used to escape `start_nina` raw — no
        action, no command, nothing to act on — while `stop_nina` already
        folds its own timeout into a RuntimeError. Both must report the
        same way: a RuntimeError naming the action and the command.
        """
        import subprocess

        from nina_planner.server import start_nina

        executed_cmds = []

        def fake_run(argv, timeout=30.0):
            executed_cmds.append(argv)
            if "/Run" in argv:
                raise subprocess.TimeoutExpired(cmd=argv, timeout=15.0)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch(
                "nina_planner.client.nina_api_responsive",
                AsyncMock(return_value=False),
            ),
            patch(
                "nina_planner.tools.lifecycle.windows_interop_available",
                return_value=True,
            ),
            patch("nina_planner.client.run_blocking", side_effect=fake_run),
            patch(
                "nina_planner.tools.lifecycle.write_nina_launcher",
                return_value=self._STUB_LAUNCHER_PATHS,
            ),
            patch("nina_planner.client.log_payload"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(start_nina())
            # A TimeoutExpired is NOT a RuntimeError, so catching one here
            # would itself fail — but assert the routing explicitly anyway.
            self.assertNotIsInstance(ctx.exception, subprocess.TimeoutExpired)

        message = str(ctx.exception)
        self.assertIn("Task Scheduler task execution", message)
        self.assertIn("timed out after 15s", message)
        self.assertIn("schtasks.exe", message)
        # The failure happened inside the try, so the temporary task was
        # still cleaned up (the finally block) before the error surfaced.
        self.assertIn("/Delete", executed_cmds[-1])


class IsWslTest(unittest.TestCase):
    """``_is_wsl`` itself — the existing interop tests patch it out."""

    def test_non_linux_is_never_wsl(self):
        from nina_planner.system_time import _is_wsl

        with (
            patch("nina_planner.system_time.sys.platform", "darwin"),
            patch.dict(os.environ, {"WSL_INTEROP": "1"}),
        ):
            # The env vars are a Windows-host signal; on a Mac they lie.
            self.assertFalse(_is_wsl())

    def test_wsl_interop_env_marks_wsl(self):
        from nina_planner.system_time import _is_wsl

        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch.dict(os.environ, {"WSL_INTEROP": "1"}),
        ):
            self.assertTrue(_is_wsl())

    def test_wsl_distro_env_marks_wsl(self):
        from nina_planner.system_time import _is_wsl

        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}),
        ):
            self.assertTrue(_is_wsl())

    def test_osrelease_identifies_wsl(self):
        from nina_planner.system_time import _is_wsl

        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "builtins.open",
                mock_open(read_data="Linux version 5.15.90.1-microsoft-standard-WSL2"),
            ),
        ):
            self.assertTrue(_is_wsl())

    def test_unreadable_osrelease_is_not_wsl(self):
        from nina_planner.system_time import _is_wsl

        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch.dict(os.environ, {}, clear=True),
            patch("builtins.open", side_effect=OSError("no /proc here")),
        ):
            # /proc does not exist on a non-Linux host even when platform is
            # faked; the probe must degrade to False, not raise.
            self.assertFalse(_is_wsl())

    def test_plain_linux_osrelease_is_not_wsl(self):
        from nina_planner.system_time import _is_wsl

        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch.dict(os.environ, {}, clear=True),
            patch("builtins.open", mock_open(read_data="6.8.0-generic")),
        ):
            self.assertFalse(_is_wsl())


class LauncherPathsErrorTest(unittest.TestCase):
    """``get_launcher_paths`` failure paths — the happy paths are covered by
    the launcher tests above."""

    def _run(self, stdout, stderr, returncode=0):
        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch("nina_planner.system_time.Path", PurePosixPath),
            patch(
                "nina_planner.system_time.subprocess.run",
                return_value=MagicMock(
                    returncode=returncode, stdout=stdout, stderr=stderr
                ),
            ),
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/mnt/c"}),
        ):
            return get_launcher_paths()

    def test_cmd_failure_raises_with_detail(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(stdout="", stderr="Access is denied", returncode=1)
        self.assertIn(
            "Could not resolve the Windows temporary directory", str(ctx.exception)
        )
        self.assertIn("Access is denied", str(ctx.exception))

    def test_cmd_failure_without_output_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(stdout="", stderr="", returncode=1)
        # No detail to report — the message still ends in a period, not a colon.
        self.assertIn("NINA launch.", str(ctx.exception))

    def test_non_drive_temp_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(stdout="just-a-name\r\n", stderr="")
        self.assertIn("is not an absolute drive path", str(ctx.exception))
        self.assertIn("just-a-name", str(ctx.exception))


class AsyncMockSleep:
    """Async sleep stand-in that returns immediately."""

    async def __call__(self, *_args, **_kwargs):
        return None
