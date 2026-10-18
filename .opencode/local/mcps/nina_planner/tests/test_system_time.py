import asyncio
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import AsyncMock, MagicMock, patch

from nina_planner.system_time import (
    NINA_EXE_DEFAULT,
    get_launcher_paths,
    kill_nina_command,
    local_to_windows,
    nina_exe_path,
    nina_running,
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

    def test_local_to_windows_wsl_mount(self):
        p = PurePosixPath("/mnt/c/Users/george/AppData/Local/Temp/launch_nina.cmd")
        with (
            patch("nina_planner.system_time.sys.platform", "linux"),
            patch("nina_planner.system_time.Path") as path_constructor,
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/ignored"}),
        ):
            # Model WSL resolution without resolving a POSIX path on Windows.
            path_constructor.return_value.resolve.return_value = p
            self.assertEqual(
                local_to_windows(p),
                r"C:\Users\george\AppData\Local\Temp\launch_nina.cmd",
            )
        path_constructor.assert_called_once_with(p)
        path_constructor.return_value.resolve.assert_called_once_with()

    def test_local_to_windows_preserves_native_windows_path(self):
        with (
            patch("nina_planner.system_time.sys.platform", "win32"),
            patch("nina_planner.system_time.Path") as path_constructor,
            patch.dict(os.environ, {"NINA_DRIVE_MOUNT": "/ignored"}),
        ):
            for p in (
                r"D:\NINA Files\launch_nina.cmd",
                PureWindowsPath(r"D:\NINA Files\launch_nina.cmd"),
            ):
                with self.subTest(path=p):
                    self.assertEqual(local_to_windows(p), str(p))
        path_constructor.assert_not_called()

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


class AppendProgressEntryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "PROGRESS.md"
        self.progress.write_text("# progress.md\n\nShared observatory progress file.\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _call(self, text: str) -> str:
        from nina_planner.progress import append_progress_entry

        with patch("nina_planner.config.PROJECT_DIR", self.project_dir):
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
        self.progress = self.project_dir / "PROGRESS.md"
        self.progress.write_text("# progress.md\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_stop_when_not_running_is_noop(self):
        from nina_planner.server import stop_nina

        with (
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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
        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("stop_nina: killed running NINA", content)

    def test_stop_when_remote_raises_runtime_error(self):
        from nina_planner.server import stop_nina

        with (
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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


class StartNinaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.progress = self.project_dir / "PROGRESS.md"
        self.progress.write_text("# progress.md\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_start_errors_when_already_running(self):
        from nina_planner.server import start_nina

        with (
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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

        content = self.progress.read_text(encoding="utf-8")
        self.assertIn("start_nina: launching NINA", content)

    def test_start_real_time_raises_when_task_creation_fails(self):
        from nina_planner.server import start_nina

        executed_cmds = []

        def fake_run(argv, timeout=30.0):
            executed_cmds.append(argv)
            return MagicMock(returncode=1, stdout="", stderr="Access denied")

        with (
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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
            patch("nina_planner.config.PROJECT_DIR", self.project_dir),
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


class AsyncMockSleep:
    """Async sleep stand-in that returns immediately."""

    async def __call__(self, *_args, **_kwargs):
        return None
