# `nina_planner/system_time.py` — Windows/WSL Process Lifecycle, Interop Probing & Task Scheduler Launcher Helpers

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ Core Package](./README.md) · **Callers:** [`tools/lifecycle.py`](../tools/lifecycle.md) · **Callees:** [`imaging.py` (`windows_to_local`)](./imaging.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/system_time.py`
- **Line Count:** 174 lines
- **Module Docstring & Purpose (`system_time.py:1-7`):**
  > *"Windows-side helpers for starting, stopping and locating N.I.N.A. The MCP server may run on macOS, WSL or Windows while N.I.N.A. itself runs on a Windows host, so these functions build Windows argv (`taskkill`, `schtasks`) and paths without importing Windows-only modules. Commands returned here are executed by the caller, never run at import time."*
- **Architectural Layer:** Core OS / Lifecycle Adapter (`nina_planner/`). Bridges cross-platform Python (`win32`, Linux/WSL2, `darwin`) with Windows process management (`tasklist.exe`, `taskkill.exe`, `schtasks.exe`, `cmd.exe`) so `tools/lifecycle.py` can start N.I.N.A. into the active interactive desktop session, terminate it, or report `lifecycle_control_available`.
- **Test Coverage:** `.opencode/local/mcps/nina_planner/tests/test_system_time.py` (`CommandBuildersTest`, `NinaExePathTest`, `WindowsInteropAvailableTest`, `IsWslTest`, `LauncherPathsErrorTest`, plus integration coverage in `StopNinaTest`, `StartNinaTest`, `GetNinaStatusTest`).

### Outbound Imports

| Category | Import Statement | Lines | Usage |
|---|---|---|---|
| `__future__` | `from __future__ import annotations` | `9` | Deferred evaluation of PEP 604 type annotations (`str \| None`, `tuple[Path, str]`, `list[str]`). |
| Stdlib | `import os` | `11` | Reads `os.environ.get("NINA_EXE_PATH")`, `WSL_INTEROP`, `WSL_DISTRO_NAME`. |
| Stdlib | `import shutil` | `12` | `shutil.which(exe)` to probe Windows interop executables on `PATH` (`system_time.py:74`). |
| Stdlib | `import subprocess` | `13` | `subprocess.run(["cmd.exe", "/d", "/c", "echo", "%TEMP%"], ...)` under WSL (`system_time.py:88-94`). |
| Stdlib | `import sys` | `14` | Checks `sys.platform` (`"win32"`, `"linux"`, etc.) in `_is_wsl`, `windows_interop_available`, and `get_launcher_paths`. |
| Stdlib | `import tempfile` | `15` | `tempfile.gettempdir()` on native `win32` (`system_time.py:85`). |
| Stdlib | `from pathlib import Path, PureWindowsPath` | `16` | Host filesystem `Path` and cross-platform Windows path parser `PureWindowsPath`. |
| Third-Party | *(none)* | — | — |
| Internal | `from .imaging import windows_to_local` | `18` | Maps Windows `%TEMP%` drive paths (e.g., `C:\Users\...\Temp`) into local WSL mount paths (e.g., `/mnt/c/Users/.../Temp`) in `get_launcher_paths` (`system_time.py:110`). |

### Inbound Callers Across the Codebase

| Importing Module | Imported Symbols | Import Lines | Call Sites & Role |
|---|---|---|---|
| `nina_planner/tools/lifecycle.py` | `kill_nina_command`, `nina_exe_path`, `schtasks_create_command`, `schtasks_delete_command`, `schtasks_run_command`, `windows_interop_available`, `write_nina_launcher` | `11-19` | `_launch_nina_interactive` (`lifecycle.py:38-41`), `stop_nina` (`lifecycle.py:113, 123`), `start_nina` (`lifecycle.py:166, 174, 181`), `get_nina_status` (`lifecycle.py:213`). |

### Module-Level Constants & Globals

| Symbol | Type | Lines | Value | Purpose |
|---|---|---|---|---|
| `NINA_EXE_DEFAULT` | `str` | `20-22` | `r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe"` | Default Windows installation path for `NINA.exe` when `NINA_EXE_PATH` is unset or empty. |
| `NINA_TASK_NAME` | `str` | `24` | `"NINAPlanner-Launch"` | Default Windows Task Scheduler task name (`\TN`) created, run, and deleted during interactive N.I.N.A. launch. |
| `_WINDOWS_INTEROP_EXES` | `tuple[str, str, str]` | `40-44` | `("tasklist.exe", "taskkill.exe", "schtasks.exe")` | The three Windows executables required on `PATH` for WSL interop lifecycle control (`windows_interop_available`, line `74`). |

### Environment Variables

| Variable | Read At | Default | Effect |
|---|---|---|---|
| `NINA_EXE_PATH` | `nina_exe_path` (`system_time.py:32`) | `NINA_EXE_DEFAULT` | Overrides the Windows path to `NINA.exe` embedded into `launch_nina.cmd`. |
| `WSL_INTEROP` | `_is_wsl` (`system_time.py:51`) | Unset | Non-empty value on `sys.platform == "linux"` immediately confirms WSL execution. |
| `WSL_DISTRO_NAME` | `_is_wsl` (`system_time.py:51`) | Unset | Non-empty value on `sys.platform == "linux"` immediately confirms WSL execution. |
| `NINA_DRIVE_MOUNT` | Indirect via `windows_to_local` (`imaging.py:79-80`) called at `system_time.py:110` | `f"/mnt/{drive}"` | Base directory used when mapping the drive letter of Windows `%TEMP%` to a local WSL path. |

---

## 2. Classes, Data Models & Exceptions

*(None — `system_time.py` contains only module-level constants and functions; it raises standard `RuntimeError` and `ValueError` exceptions.)*

---

## 3. Function & Method Reference

### 3.1 `nina_exe_path` (`nina_planner/system_time.py:27-32`)

1. **Purpose & Architectural Role**
   Resolves the Windows-formatted path to `NINA.exe` on the target Windows host, honoring the `NINA_EXE_PATH` environment variable override or falling back to `NINA_EXE_DEFAULT`.

2. **Signature, Decorators & Inputs**
   ```python
   def nina_exe_path() -> str:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - Returns `str`: Non-empty `NINA_EXE_PATH` environment variable value if set, otherwise `NINA_EXE_DEFAULT` (`r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe"`).

4. **Step-by-Step Internal Logic**
   1. **Environment lookup with fallback (`system_time.py:32`):** Evaluates `return os.environ.get("NINA_EXE_PATH") or NINA_EXE_DEFAULT`. Note that an empty string `""` in `NINA_EXE_PATH` is falsy and falls back to `NINA_EXE_DEFAULT`.

5. **Side Effects & I/O**
   - Reads `os.environ`. Does not touch the filesystem.

6. **Error Handling & Edge Cases**
   - Never raises. Sanitization against batch injection happens downstream in `_batch_quoted` (`system_time.py:115-130`) when `write_nina_launcher` is called.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.2 `kill_nina_command` (`nina_planner/system_time.py:35-37`)

1. **Purpose & Architectural Role**
   Builds the Windows `taskkill.exe` argument vector used by `tools/lifecycle.py:stop_nina` (`lifecycle.py:123`) to forcefully terminate all running `NINA.exe` processes.

2. **Signature, Decorators & Inputs**
   ```python
   def kill_nina_command() -> list[str]:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - Returns `list[str]`: Always `["taskkill.exe", "/F", "/IM", "NINA.exe"]`.

4. **Step-by-Step Internal Logic**
   1. **Construct argv (`system_time.py:37`):** Returns a fresh 4-element `list[str]` specifying `taskkill.exe` with `/F` (force terminate) and `/IM NINA.exe` (by image name).

5. **Side Effects & I/O**
   - Pure function; does not spawn a subprocess.

6. **Error Handling & Edge Cases**
   - Never raises.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.3 `_is_wsl` (`nina_planner/system_time.py:47-57`)

1. **Purpose & Architectural Role**
   Private platform detector that determines whether the Python interpreter is running inside the Windows Subsystem for Linux (WSL). Called by `windows_interop_available` (`system_time.py:72`).

2. **Signature, Decorators & Inputs**
   ```python
   def _is_wsl() -> bool:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - Returns `bool`: `True` if and only if `sys.platform == "linux"` and either a WSL environment variable is set or `/proc/sys/kernel/osrelease` contains `"microsoft"` or `"wsl"` (case-insensitive); `False` otherwise.

4. **Step-by-Step Internal Logic**
   1. **Platform gate (`system_time.py:49-50`):** If `sys.platform != "linux"`, immediately returns `False` (preventing stray `WSL_*` env vars on macOS or Windows from causing a false positive).
   2. **Environment variable fast-path (`system_time.py:51-52`):** If `os.environ.get("WSL_INTEROP")` or `os.environ.get("WSL_DISTRO_NAME")` is truthy, returns `True`.
   3. **Kernel release file fallback (`system_time.py:53-57`):** Opens `/proc/sys/kernel/osrelease` (`encoding="utf-8"`), reads and lowercases its contents (`line 54`). If `open()` or `read()` raises `OSError`, catches it and returns `False` (`lines 55-56`). Otherwise returns `"microsoft" in release or "wsl" in release` (`line 57`).

5. **Side Effects & I/O**
   - Reads `os.environ` and, on Linux when `WSL_INTEROP` / `WSL_DISTRO_NAME` are absent, performs a synchronous file read of `/proc/sys/kernel/osrelease`. Note that the file handle in `open("/proc/sys/kernel/osrelease", encoding="utf-8").read()` is closed upon garbage collection of the temporary file object.

6. **Error Handling & Edge Cases**
   - Catches `OSError` when `/proc/sys/kernel/osrelease` is missing or unreadable and returns `False` (`test_system_time.py:791-803`).

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.4 `windows_interop_available` (`nina_planner/system_time.py:60-74`)

1. **Purpose & Architectural Role**
   Determines whether the current host can directly invoke Windows process/task management binaries (`tasklist.exe`, `taskkill.exe`, `schtasks.exe`). Gates `start_nina` and `stop_nina` and populates `lifecycle_control_available` in `get_nina_status` (`tools/lifecycle.py:113, 166, 174, 213`).

2. **Signature, Decorators & Inputs**
   ```python
   def windows_interop_available() -> bool:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - Returns `bool`:
     - `True` on native Windows (`sys.platform == "win32"`).
     - `True` on WSL (`_is_wsl() is True`) when all three binaries in `_WINDOWS_INTEROP_EXES` (`"tasklist.exe"`, `"taskkill.exe"`, `"schtasks.exe"`) resolve via `shutil.which()`.
     - `False` on macOS, plain Linux, or WSL without Windows interop binaries on `PATH`.

4. **Step-by-Step Internal Logic**
   1. **Native Windows check (`system_time.py:70-71`):** If `sys.platform == "win32"`, returns `True`.
   2. **WSL check (`system_time.py:72-73`):** Calls `if not _is_wsl(): return False`.
   3. **`PATH` probe (`system_time.py:74`):** Evaluates `return all(shutil.which(exe) for exe in _WINDOWS_INTEROP_EXES)`.

5. **Side Effects & I/O**
   - Calls `_is_wsl()` and scans `PATH` directories via `shutil.which` for up to 3 executable names.

6. **Error Handling & Edge Cases**
   - Never raises. Short-circuits on the first missing executable in `all(...)`.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(|\text{PATH}|)$ filesystem stat calls under WSL; $\mathcal{O}(1)$ on `win32` and `darwin`.

---

### 3.5 `get_launcher_paths` (`nina_planner/system_time.py:77-112`)

1. **Purpose & Architectural Role**
   Computes the paired `(local_path, windows_path)` for the temporary `launch_nina.cmd` helper batch script so that Python can write the file via `local_path` and Windows Task Scheduler (`schtasks.exe /Create /TR ...`) can execute it via `windows_path`. Under WSL, Python's `tempfile.gettempdir()` points to `/tmp` inside the Linux filesystem (inaccessible to Windows Task Scheduler as a standard drive path), so `get_launcher_paths` queries `cmd.exe` for Windows' own `%TEMP%` and maps it into WSL's drive mount via `windows_to_local`.

2. **Signature, Decorators & Inputs**
   ```python
   def get_launcher_paths() -> tuple[Path, str]:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - Returns `tuple[Path, str]`:
     - Element 0 (`local_p: Path`): Host-accessible `pathlib.Path` to `launch_nina.cmd` (e.g., `Path("/mnt/c/Users/test/AppData/Local/Temp/launch_nina.cmd")` on WSL, or `Path(r"C:\Users\...\Temp\launch_nina.cmd")` on native Windows).
     - Element 1 (`windows_p: str`): Native Windows path string to `launch_nina.cmd` (e.g., `r"C:\Users\test\AppData\Local\Temp\launch_nina.cmd"`).

4. **Step-by-Step Internal Logic**
   1. **Native Windows fast-path (`system_time.py:84-86`):** If `sys.platform == "win32"`, constructs `local_p = Path(tempfile.gettempdir()) / "launch_nina.cmd"` and returns `(local_p, str(local_p))` without spawning any subprocess.
   2. **Query Windows `%TEMP%` via `cmd.exe` (`system_time.py:88-95`):** On non-`win32` (WSL), runs:
      ```python
      result = subprocess.run(
          ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
          capture_output=True,
          text=True,
          check=False,
          timeout=10,
      )
      windows_temp = (result.stdout or "").strip()
      ```
      (`/d` disables AutoRun registry commands in `cmd.exe`.)
   3. **Check `cmd.exe` exit status and non-empty stdout (`system_time.py:96-101`):** If `result.returncode != 0 or not windows_temp`, extracts `detail = (result.stderr or result.stdout or "").strip()` and raises `RuntimeError("Could not resolve the Windows temporary directory for NINA launch" + (f": {detail}" if detail else "."))`.
   4. **Validate absolute Windows drive path (`system_time.py:103-107`):** Parses `windows_temp_path = PureWindowsPath(windows_temp)`. If `not windows_temp_path.drive or not windows_temp_path.is_absolute()` (for example, if `%TEMP%` was unexpanded or relative), raises `RuntimeError(f"Windows TEMP is not an absolute drive path: {windows_temp!r}")`.
   5. **Map to local and Windows launcher paths (`system_time.py:109-112`):** Sets `launcher_name = "launch_nina.cmd"`, computes `local_p = windows_to_local(windows_temp) / launcher_name` and `windows_p = str(windows_temp_path / launcher_name)`, and returns `(local_p, windows_p)`.

   ```mermaid
   flowchart TD
       A["get_launcher_paths()"] --> B{"sys.platform == 'win32'?<br/>(line 84)"}
       B -- Yes --> C["local_p = Path(tempfile.gettempdir()) / 'launch_nina.cmd'<br/>return (local_p, str(local_p)) (lines 85-86)"]
       B -- No --> D["subprocess.run(['cmd.exe', '/d', '/c', 'echo', '%TEMP%'], timeout=10)<br/>windows_temp = (result.stdout or '').strip() (lines 88-95)"]
       D --> E{"result.returncode != 0<br/>or not windows_temp?<br/>(line 96)"}
       E -- Yes --> F["raise RuntimeError('Could not resolve the Windows temporary directory...')<br/>(lines 97-101)"]
       E -- No --> G["windows_temp_path = PureWindowsPath(windows_temp)<br/>(line 103)"]
       G --> H{"not drive or<br/>not is_absolute()?<br/>(line 104)"}
       H -- Yes --> I["raise RuntimeError('Windows TEMP is not an absolute drive path...')<br/>(lines 105-107)"]
       H -- No --> J["local_p = windows_to_local(windows_temp) / 'launch_nina.cmd'<br/>windows_p = str(windows_temp_path / 'launch_nina.cmd')<br/>return (local_p, windows_p) (lines 109-112)"]
   ```

5. **Side Effects & I/O**
   - On `win32`: reads temp dir environment variables via `tempfile.gettempdir()`.
   - On non-`win32`: executes synchronous `subprocess.run(["cmd.exe", ...], timeout=10)`.

6. **Error Handling & Edge Cases**
   - Raises `RuntimeError` if `cmd.exe` returns a non-zero exit code, emits blank stdout, or returns a non-drive/relative path.
   - May also propagate `subprocess.TimeoutExpired` or `FileNotFoundError` if `cmd.exe` hangs >10s or is missing when called outside `windows_interop_available()`.

7. **Complexity & Risk Notes**
   - **Blocking Subprocess Note:** Under WSL, `get_launcher_paths()` runs `subprocess.run` synchronously (with a 10s timeout) when called from `write_nina_launcher` inside `async def _launch_nina_interactive` (`tools/lifecycle.py:38`). In practice `cmd.exe /d /c echo %TEMP%` completes in a few milliseconds.

---

### 3.6 `_batch_quoted` (`nina_planner/system_time.py:115-130`)

1. **Purpose & Architectural Role**
   Security sanitizer that validates and escapes a string (`NINA_EXE_PATH`) before embedding it inside double quotes (`start "" "{exe}"`) in `launch_nina.cmd`. Prevents batch command injection and unintended `%VAR%` environment variable expansion under Windows Task Scheduler.

2. **Signature, Decorators & Inputs**
   ```python
   def _batch_quoted(value: str, var: str) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `value: str` — Candidate executable path string to sanitize.
     - `var: str` — Name of the source variable/parameter (e.g., `"NINA_EXE_PATH"`) included in the `ValueError` diagnostic on failure.

3. **Outputs & Return Semantics**
   - Returns `str`: The validated string with every literal `"%"` doubled to `"%%"`.

4. **Step-by-Step Internal Logic**
   1. **Reject quotes and ASCII control characters (`system_time.py:125-129`):** Checks `if '"' in value or any(ord(c) < 32 for c in value):`. If true, fails closed by raising:
      ```python
      ValueError(
          f"{var} contains a quote or control character and cannot be "
          f"safely written into launch_nina.cmd: {value!r}"
      )
      ```
      Because Windows filenames cannot legally contain `"` or control characters (`0x00`–`0x1F`, including `\r`, `\n`, `\t`), rejecting them outright prevents breaking out of `"..."` or starting a second batch line. With surrounding quotes guaranteed intact, batch metacharacters (`&`, `|`, `<`, `>`, `^`) are inert inside quotes.
   2. **Escape batch percent expansion (`system_time.py:130`):** Returns `value.replace("%", "%%")` so `cmd.exe` cannot expand `%VAR%` inside the double-quoted string.

5. **Side Effects & I/O**
   - Pure function; no side effects.

6. **Error Handling & Edge Cases**
   - Raises `ValueError` on any `"` or character with `ord(c) < 32` (`\r`, `\n`, `\x00`, etc.). Tested by `test_write_nina_launcher_rejects_quote_injection` and `test_write_nina_launcher_rejects_newline_injection` (`test_system_time.py:121-149`).

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(k)$ where $k = \text{len}(value)$.
   - Critical security boundary against untrusted `NINA_EXE_PATH` environment values.

---

### 3.7 `write_nina_launcher` (`nina_planner/system_time.py:133-144`)

1. **Purpose & Architectural Role**
   Sanitizes the target `NINA.exe` path, resolves the local and Windows launcher paths, ensures the parent directory exists, and writes the two-line CRLF batch script `launch_nina.cmd` used by Task Scheduler to start N.I.N.A. with a GUI window.

2. **Signature, Decorators & Inputs**
   ```python
   def write_nina_launcher(nina_exe: str | None = None) -> tuple[Path, str]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `nina_exe: str | None = None` — Optional explicit Windows path to `NINA.exe`; defaults to `nina_exe_path()` when `None` or empty.

3. **Outputs & Return Semantics**
   - Returns `tuple[Path, str]`: `(local_path, win_path)` where `local_path` is the host `Path` written on disk and `win_path` is the Windows path string passed to `schtasks_create_command`.

4. **Step-by-Step Internal Logic**
   1. **Sanitize executable path (`system_time.py:140`):** Calls `exe = _batch_quoted(nina_exe or nina_exe_path(), "NINA_EXE_PATH")`. Note that sanitization happens *before* `get_launcher_paths()` or any disk write, so an invalid `nina_exe` never touches the filesystem (`test_system_time.py:135, 149`).
   2. **Resolve launcher paths (`system_time.py:141`):** Calls `local_path, win_path = get_launcher_paths()`.
   3. **Ensure parent directory exists (`system_time.py:142`):** Calls `local_path.parent.mkdir(parents=True, exist_ok=True)`.
   4. **Write batch script (`system_time.py:143`):** Calls `local_path.write_text(f'@echo off\r\nstart "" "{exe}"\r\n', encoding="utf-8")`. The empty `""` first argument to `start` is the window title required by `cmd.exe`'s `start` built-in whenever the executable path is quoted.
   5. **Return paths (`system_time.py:144`):** Returns `(local_path, win_path)`.

5. **Side Effects & I/O**
   - May spawn `cmd.exe` (via `get_launcher_paths` on WSL).
   - Creates `local_path.parent` directories if absent and overwrites `launch_nina.cmd` on disk with UTF-8 CRLF text.

6. **Error Handling & Edge Cases**
   - Propagates `ValueError` from `_batch_quoted`, `RuntimeError` from `get_launcher_paths`, or `OSError` if the temp directory is not writable.

7. **Complexity & Risk Notes**
   - Overwrites deterministic filename `launch_nina.cmd` in the user's Windows `%TEMP%` directory.

---

### 3.8 `schtasks_create_command` (`nina_planner/system_time.py:147-164`)

1. **Purpose & Architectural Role**
   Builds the `schtasks.exe /Create` argument vector to register a one-shot interactive scheduled task (`/IT`) for the currently logged-on user session. Using Task Scheduler with `/IT` allows a background SSH/WSL or service process to launch `NINA.exe` into the active Windows desktop GUI session.

2. **Signature, Decorators & Inputs**
   ```python
   def schtasks_create_command(
       launcher_win_path: str, task_name: str = NINA_TASK_NAME
   ) -> list[str]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `launcher_win_path: str` — Windows path to `launch_nina.cmd` (for `/TR`).
     - `task_name: str = NINA_TASK_NAME` — Scheduled task name (defaults to `"NINAPlanner-Launch"`).

3. **Outputs & Return Semantics**
   - Returns `list[str]`:
     `["schtasks.exe", "/Create", "/TN", task_name, "/TR", launcher_win_path, "/SC", "ONCE", "/ST", "00:00", "/IT", "/F"]`.

4. **Step-by-Step Internal Logic**
   1. **Return argv list (`system_time.py:151-164`):** Constructs the 12-element command list:
      - `/Create`: Create a scheduled task.
      - `/TN <task_name>`: Task name.
      - `/TR <launcher_win_path>`: Task run action path.
      - `/SC ONCE` and `/ST 00:00`: Required schedule type/time flags for on-demand tasks triggered via `/Run`.
      - `/IT`: Run interactively only when the user is logged on (essential for GUI visibility).
      - `/F`: Force overwrite if a task with `task_name` already exists.

5. **Side Effects & I/O**
   - Pure function; returns `list[str]`.

6. **Error Handling & Edge Cases**
   - Never raises.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.9 `schtasks_run_command` (`nina_planner/system_time.py:167-169`)

1. **Purpose & Architectural Role**
   Builds the `schtasks.exe /Run` argument vector to immediately trigger the scheduled task created by `schtasks_create_command`.

2. **Signature, Decorators & Inputs**
   ```python
   def schtasks_run_command(task_name: str = NINA_TASK_NAME) -> list[str]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `task_name: str = NINA_TASK_NAME` — Scheduled task name (defaults to `"NINAPlanner-Launch"`).

3. **Outputs & Return Semantics**
   - Returns `list[str]`: `["schtasks.exe", "/Run", "/TN", task_name]`.

4. **Step-by-Step Internal Logic**
   1. **Return argv list (`system_time.py:169`):** Returns `["schtasks.exe", "/Run", "/TN", task_name]`.

5. **Side Effects & I/O**
   - Pure function; returns `list[str]`.

6. **Error Handling & Edge Cases**
   - Never raises.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.10 `schtasks_delete_command` (`nina_planner/system_time.py:172-174`)

1. **Purpose & Architectural Role**
   Builds the `schtasks.exe /Delete` argument vector executed in the `finally:` block of `tools/lifecycle.py:_launch_nina_interactive` (`lifecycle.py:55-60`) to clean up the temporary scheduled task after running it.

2. **Signature, Decorators & Inputs**
   ```python
   def schtasks_delete_command(task_name: str = NINA_TASK_NAME) -> list[str]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `task_name: str = NINA_TASK_NAME` — Scheduled task name (defaults to `"NINAPlanner-Launch"`).

3. **Outputs & Return Semantics**
   - Returns `list[str]`: `["schtasks.exe", "/Delete", "/TN", task_name, "/F"]`.

4. **Step-by-Step Internal Logic**
   1. **Return argv list (`system_time.py:174`):** Returns `["schtasks.exe", "/Delete", "/TN", task_name, "/F"]` (where `/F` suppresses confirmation prompts).

5. **Side Effects & I/O**
   - Pure function; returns `list[str]`.

6. **Error Handling & Edge Cases**
   - Never raises.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.
