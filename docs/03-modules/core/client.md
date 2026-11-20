# Async HTTP Client & Sequencer Wait Barriers (`nina_planner/client.py`)

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ Core Package](./README.md) · [⬅️ Bootstrap & Server](./bootstrap-and-server.md) · [⬅️ Configuration (`config.py`)](./config.md) · [➡️ Sequence Tools (`tools/sequence.py`)](../tools/sequence.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/client.py`
- **Line Count:** 281 lines (`1-281`)
- **Module Docstring (`lines 1-7`):**
  > Thin async HTTP client for N.I.N.A.'s REST API plus sequence start/stop waits.
  >
  > Every call unwraps the `Success`/`Error`/`Response` envelope through `unwrap_response`, so failures surface as `RuntimeError` carrying N.I.N.A.'s own refusal text. Requests carry a 10 s timeout; payloads are best-effort logged to `NINA_PLANNER_LOG`.
- **Architectural Layer:** Core Infrastructure — Async HTTP Transport, Envelope Unwrapping, Sequencer State Synchronization, Debug Logging, and Threaded Subprocess Execution
- **Inbound Callers:**
  - `nina_planner/horizon.py:25, 132, 134` (`client.api_get("/profile/horizon")`, `client.log_payload`)
  - `nina_planner/purge.py:106, 236` (`from .client import _sequence_is_running`)
  - `nina_planner/tools/equipment.py:8, 65, 97, 134, 175, 182, 237, 288, 295, 297, 304, 309` (`api_get`, `log_payload`)
  - `nina_planner/tools/lifecycle.py:9, 29, 42, 49, 58, 60, 103, 110, 119, 123, 126, 138, 146, 154, 165, 187, 209, 211` (`run_blocking`, `log_payload`, `api_probe`, `nina_api_responsive`)
  - `nina_planner/tools/plans.py:13, 50, 96, 310, 962, 1164, 1198` (`api_get`, `log_payload`)
  - `nina_planner/tools/sequence.py:6, 145, 147, 152, 400, 422, 432, 433, 447, 451` (`api_get`, `log_payload`, `load_sequence_payload`, `stop_running_sequence_and_wait`, `wait_until_sequence_started`)
  - `scripts/gen_sequence.py:44, 83, 94, 375` (`api_get`, `load_sequence_payload`)
- **Outbound Imports:**
  - **Stdlib (`lines 9-17`):**
    - `import json` (`line 9`)
    - `import os` (`line 10`)
    - `import re` (`line 11`)
    - `import subprocess` (`line 12`)
    - `import tempfile` (`line 13`)
    - `import time` (`line 14`)
    - `from datetime import UTC, datetime` (`line 15`)
    - `from pathlib import Path` (`line 16`)
    - `from typing import Any, cast` (`line 17`)
  - **Third-Party (`lines 19-20`):**
    - `import anyio` (`line 19`)
    - `import httpx` (`line 20`)
  - **Internal (`line 22`):**
    - `from . import config` (`line 22`)
- **Module-Level Constants:**
  - `_WINDOWS_ABSOLUTE_PATH: re.Pattern[str] = re.compile(r"^[A-Za-z]:[\\/]")` (`line 24`): Matches Windows drive-letter absolute paths (`C:\...` or `C:/...`) so `_log_file()` can relocate Windows paths to the local temp directory when running on POSIX/WSL hosts.
  - `SEQUENCE_START_TIMEOUT: float = 10.0` (`line 188`): Default monotonic polling budget in seconds for `wait_until_sequence_started()`. Defined as a module constant read at call time (rather than a function default argument) so tests can patch `nina_planner.client.SEQUENCE_START_TIMEOUT` without re-binding the function signature (`lines 185-188`).
- **Environment Variables Read at Import Time:** None directly (reads `config.NINA_PLANNER_LOG` and `config.NINA_API_URL` dynamically at call time).
- **Global Caches / Singletons:** None (each `api_get` and `api_post` creates a scoped `httpx.AsyncClient(base_url=config.NINA_API_URL, timeout=10.0)` context manager).

---

## 2. Classes, Data Models & Exceptions

None (`client.py` raises standard `RuntimeError` with descriptive context strings on HTTP or N.I.N.A. envelope failures and sequence start/stop timeouts).

---

## 3. Function & Method Reference

### 3.1 Debug Logging Functions

#### `_log_file` (`nina_planner/client.py:27-39`)

1. **Purpose & Architectural Role**
   Resolves `config.NINA_PLANNER_LOG` into a host-usable `pathlib.Path` (or `None` when logging is disabled). Prevents sample Windows configurations (`C:/Windows/Temp/nina-planner-debug.log`) from creating a literal `C:` directory in the working directory when the MCP server runs on macOS, Linux, or WSL.

2. **Signature, Decorators & Inputs**
   ```python
   def _log_file() -> Path | None:
   ```
   - **Decorators:** None.
   - **Inputs:** None (reads `config.NINA_PLANNER_LOG` and `os.name`).

3. **Outputs & Return Semantics**
   - `None` if `config.NINA_PLANNER_LOG` is `None` or `""`.
   - `Path(tempfile.gettempdir()) / Path(config.NINA_PLANNER_LOG).name` when running on a non-Windows host (`os.name != "nt"`) and `config.NINA_PLANNER_LOG` begins with a Windows drive prefix (`^[A-Za-z]:[\\/]`).
   - `Path(config.NINA_PLANNER_LOG)` otherwise.

4. **Step-by-Step Internal Logic**
   1. **Lines 35-36:** Checks `if not config.NINA_PLANNER_LOG: return None`.
   2. **Lines 37-38:** Checks `if os.name != "nt" and _WINDOWS_ABSOLUTE_PATH.match(config.NINA_PLANNER_LOG):`. When true, extracts the basename via `Path(config.NINA_PLANNER_LOG).name` and joins it onto `Path(tempfile.gettempdir())`.
   3. **Line 39:** Otherwise wraps and returns `Path(config.NINA_PLANNER_LOG)`.

   ```mermaid
   flowchart TD
       Start(["_log_file()"]) --> CheckEmpty{"not config.NINA_PLANNER_LOG?\n(line 35)"}
       CheckEmpty -- "True (None or '')" --> RetNone(["return None\n(line 36)"])
       CheckEmpty -- "False" --> CheckWinOnPosix{"os.name != 'nt' AND\n_WINDOWS_ABSOLUTE_PATH.match(...)?\n(line 37)"}
       CheckWinOnPosix -- "True (e.g. C:/Windows/Temp/foo.log on POSIX)" --> RetTemp(["return Path(tempfile.gettempdir()) / Path(...).name\n(line 38)"])
       CheckWinOnPosix -- "False" --> RetDirect(["return Path(config.NINA_PLANNER_LOG)\n(line 39)"])
   ```

5. **Side Effects & I/O**
   - Pure path resolution (`tempfile.gettempdir()` inspects environment/temp candidate dirs on first call); creates no files or directories.

6. **Error Handling & Edge Cases**
   - Notice that on a POSIX host, `Path("C:/Windows/Temp/foo.log").name` is `"foo.log"` because forward slashes are POSIX separators. If a Windows path with backslashes (`C:\Windows\Temp\foo.log`) were passed on POSIX, `Path(...).name` on `PosixPath` does not split on `\`. The sample configurations use forward slashes (`C:/Windows/Temp/nina-planner-debug.log`, see `tests/test_server.py:263`).

7. **Complexity & Risk Notes**
   - $O(1)$ time and space.

---

#### `log_payload` (`nina_planner/client.py:42-59`)

1. **Purpose & Architectural Role**
   Appends a single UTC-timestamped diagnostic entry to the resolved debug log file (`config.NINA_PLANNER_LOG`). Used across `client.py` and `tools/*.py` to record raw API responses, parsed Pydantic models, and lifecycle commands without ever polluting MCP's `stdout`/`stderr` streams or failing a tool call.

2. **Signature, Decorators & Inputs**
   ```python
   def log_payload(label: str, payload: str) -> None:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `label: str`: Short prefix tag identifying the source (e.g., `"_api_get:"`, `"_api_post:"`, `"Equipment:"`, `"Profile:"`, `"Audit:"`).
     - `payload: str`: Serialized string or multi-line JSON text to append.

3. **Outputs & Return Semantics**
   - Always returns `None`.

4. **Step-by-Step Internal Logic**
   1. **Lines 48-50:** Calls `path = _log_file()`. If `path is None`, returns immediately (`None`).
   2. **Line 51:** Generates an ISO-8601 UTC timestamp via `timestamp = datetime.now(UTC).isoformat()`.
   3. **Lines 54-57:** Inside a `try` block, creates missing parent directories (`path.parent.mkdir(parents=True, exist_ok=True)`), opens `path` in UTF-8 append mode (`path.open("a", encoding="utf-8")`), and writes `f"{timestamp} {label} {payload}\n"`.
   4. **Lines 58-59:** Catches any `OSError` (such as `PermissionError`, `IsADirectoryError`, or `FileExistsError` when a parent path component is a regular file) and returns silently.

5. **Side Effects & I/O**
   - Synchronous filesystem `mkdir` and file append when `config.NINA_PLANNER_LOG` is configured.

6. **Error Handling & Edge Cases**
   - **Best-effort contract (`lines 52-59`):** Guaranteed never to raise `OSError`. Verified by `HelperFunctionsTest.test_log_payload_swallows_an_unwritable_log_path` (`tests/test_server.py:270-291`).

7. **Complexity & Risk Notes**
   - $O(|\text{payload}|)$ synchronous file write. Because `NINA_PLANNER_LOG` is local debug tracing, synchronous append is fast, though very large payloads (such as 1,000 log lines or multi-pointing sequence JSONs) are formatted with `json.dumps(..., indent=2)` by the caller before calling `log_payload`.

---

### 3.2 Envelope Unwrapping & HTTP Request Functions

#### `_error_detail` (`nina_planner/client.py:62-78`)

1. **Purpose & Architectural Role**
   Extracts the most informative human-readable failure reason from an `httpx.Response`. Prefers N.I.N.A.'s structured `"Error"` field when present; falls back to the raw response text truncated to 500 characters (or `"an empty response body"` when blank).

2. **Signature, Decorators & Inputs**
   ```python
   def _error_detail(resp: httpx.Response) -> str:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `resp: httpx.Response`: Completed HTTP response object from `httpx`.

3. **Outputs & Return Semantics**
   - Returns `"an empty response body"` if `resp.text.strip()` is empty (`lines 69-71`).
   - Returns `str(payload["Error"])` if `resp.json()` parses as a `dict` containing a truthy `"Error"` value (`lines 72-77`).
   - Otherwise returns `body[:500]` (`line 78`).

4. **Step-by-Step Internal Logic**
   1. **Line 69:** Strips whitespace from `resp.text`: `body = resp.text.strip()`.
   2. **Lines 70-71:** If `not body`, returns `"an empty response body"`.
   3. **Lines 72-75:** Attempts `payload = resp.json()`, catching `ValueError` (which includes `json.JSONDecodeError`) and setting `payload = None`.
   4. **Lines 76-77:** Checks `if isinstance(payload, dict) and payload.get("Error"): return str(payload["Error"])`.
   5. **Line 78:** Returns `body[:500]`, capping large HTML gateway/proxy error pages so they do not flood agent prompts.

5. **Side Effects & I/O**
   - None (in-memory decoding of `resp.text` / `resp.json()`).

6. **Error Handling & Edge Cases**
   - Never raises; malformed JSON falls back cleanly to `body[:500]`.

7. **Complexity & Risk Notes**
   - $O(|resp.text|)$ for string strip and JSON parse.

---

#### `unwrap_response` (`nina_planner/client.py:81-115`)

1. **Purpose & Architectural Role**
   Validates an `httpx.Response` against N.I.N.A.'s Advanced API v2 envelope contract (`{"Success": bool, "Error": str, "Response": Any, "StatusCode": int, "Type": str}`). Ensures every failure mode — non-2xx HTTP status, non-JSON body, non-dict JSON body, or HTTP 200 with `Success=False` — raises a `RuntimeError` carrying N.I.N.A.'s refusal reason or full request/response context.

2. **Signature, Decorators & Inputs**
   ```python
   def unwrap_response(method: str, path: str, resp: httpx.Response) -> dict[str, Any]:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `method: str`: HTTP verb (`"GET"` or `"POST"`), used in error diagnostics.
     - `path: str`: API path relative to `config.NINA_API_URL` (e.g., `"/time"` or `"/sequence/load"`).
     - `resp: httpx.Response`: The HTTP response returned by `httpx.AsyncClient`.

3. **Outputs & Return Semantics**
   - Returns `data: dict[str, Any]` — the full decoded N.I.N.A. envelope dictionary where `bool(data.get("Success", False))` is guaranteed `True`.
   - Callers (`api_get`, `api_post`) extract `data.get("Response", {})` after logging `data`.

4. **Step-by-Step Internal Logic**
   1. **Line 91:** Computes full URL string: `url = f"{config.NINA_API_URL}{path}"`.
   2. **Lines 92-95 (Non-2xx HTTP status):** If `not resp.is_success`, raises `RuntimeError(f"{method} {url} failed with HTTP {resp.status_code}: {_error_detail(resp)}")`. Unlike `resp.raise_for_status()`, this preserves N.I.N.A.'s JSON `"Error"` message on HTTP 400/404/409/500 responses.
   3. **Lines 96-101 (Non-JSON body on 2xx):** Calls `data = resp.json()`. On `ValueError`, raises `RuntimeError(f"{method} {url} returned a non-JSON body: {_error_detail(resp)}") from None`.
   4. **Lines 102-106 (Non-object JSON body on 2xx):** If `not isinstance(data, dict)` (e.g., JSON list or scalar), raises `RuntimeError(f"{method} {url} returned HTTP {resp.status_code} with a non-object JSON body: {_error_detail(resp)}")`.
   5. **Lines 107-114 (`Success=False` on 2xx):** If `not data.get("Success", False)`:
      - **Lines 108-109:** If `data.get("Error")` is truthy, raises `RuntimeError(str(data["Error"]))` **verbatim** (without prefixing `method`/`url`) so callers like `get_frame_stats` (`tools/plans.py:55`) or tool consumers see N.I.N.A.'s exact domain message (e.g., `"No images available"`, `"Filter 'L' not found"`).
      - **Lines 110-114:** If `Error` is missing or empty, raises `RuntimeError(f"{method} {url} returned HTTP {resp.status_code} with Success=false and no Error field: {_error_detail(resp)}")`.
   6. **Line 115:** Returns `data`.

   ```mermaid
   flowchart TD
       Start(["unwrap_response(method, path, resp)"]) --> BuildUrl["url = f'{config.NINA_API_URL}{path}'\n(line 91)"]
       BuildUrl --> Check2xx{"resp.is_success?\n(line 92)"}
       Check2xx -- "False (non-2xx)" --> ErrHttp["raise RuntimeError(\n'{method} {url} failed with HTTP {status}: {_error_detail(resp)}'\n) (lines 93-95)"]
       Check2xx -- "True (2xx)" --> ParseJson["try: data = resp.json()\n(lines 96-97)"]
       ParseJson -- "ValueError" --> ErrNonJson["raise RuntimeError(\n'{method} {url} returned a non-JSON body: {_error_detail(resp)}'\n) from None (lines 98-101)"]
       ParseJson -- "Parsed JSON" --> CheckDict{"isinstance(data, dict)?\n(line 102)"}
       CheckDict -- "False" --> ErrNonObj["raise RuntimeError(\n'{method} {url} returned HTTP {status} with a non-object JSON body: ...'\n) (lines 103-106)"]
       CheckDict -- "True" --> CheckSuccess{"data.get('Success', False)?\n(line 107)"}
       CheckSuccess -- "False" --> CheckErrorField{"data.get('Error')?\n(line 108)"}
       CheckErrorField -- "Truthy" --> ErrVerbatim["raise RuntimeError(str(data['Error']))\n(line 109)"]
       CheckErrorField -- "Empty / Missing" --> ErrNoField["raise RuntimeError(\n'{method} {url} returned HTTP {status} with Success=false and no Error field: ...'\n) (lines 111-114)"]
       CheckSuccess -- "True" --> RetData(["return data\n(line 115)"])
   ```

5. **Side Effects & I/O**
   - Pure response parsing and validation; performs no network or disk I/O.

6. **Error Handling & Edge Cases**
   - Every failure path raises `RuntimeError`.
   - Note the asymmetry between non-2xx with `"Error"` (`"POST http://.../sequence/load failed with HTTP 400: Sequence is already running"`) and HTTP 200 with `Success=False` + `"Error"` (`"Filter 'L' not found"` verbatim). Both are explicitly tested in `tests/test_server.py:359-448`.

7. **Complexity & Risk Notes**
   - $O(|resp.text|)$ time complexity.

---

#### `api_get` (`nina_planner/client.py:118-134`)

1. **Purpose & Architectural Role**
   Primary async HTTP `GET` entry point for reading state and triggering `GET`-based commands on N.I.N.A.'s Advanced API (`/equipment/info`, `/profile/show?active=true`, `/profile/horizon`, `/event-history`, `/application/logs`, `/application/plugins`, `/image-history`, `/sequence/start`, `/sequence/stop`, `/sequence/state`, `/sequence/json`, `/time`).

2. **Signature, Decorators & Inputs**
   ```python
   async def api_get(path: str) -> Any:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `path: str`: Endpoint path relative to `config.NINA_API_URL` (must start with `/`, e.g., `"/time"`).

3. **Outputs & Return Semantics**
   - Returns `data.get("Response", {})` (`Any`: `dict`, `list`, `str`, `int`, `bool`, or `{}` if `"Response"` key is absent).

4. **Step-by-Step Internal Logic**
   1. **Line 130:** Opens `async with httpx.AsyncClient(base_url=config.NINA_API_URL, timeout=10.0) as client:`.
   2. **Line 131:** Awaits `resp = await client.get(path)`.
   3. **Line 132:** Validates and unwraps the envelope via `data = unwrap_response("GET", path, resp)`.
   4. **Line 133:** Logs the full unwrapped envelope via `log_payload("_api_get:", json.dumps(data, indent=2))`.
   5. **Line 134:** Returns `data.get("Response", {})`.

5. **Side Effects & I/O**
   - Performs an async TCP/HTTP `GET` request to `config.NINA_API_URL` with a `10.0s` timeout.
   - Appends pretty-printed response JSON to `config.NINA_PLANNER_LOG` if configured.

6. **Error Handling & Edge Cases**
   - Raises `RuntimeError` if `unwrap_response` rejects the HTTP status or envelope.
   - Raises `httpx.HTTPError` (e.g., `httpx.ConnectError`, `httpx.TimeoutException`) if N.I.N.A. is unreachable or times out after `10.0s`.

7. **Complexity & Risk Notes**
   - Instantiates a fresh `httpx.AsyncClient` per request rather than pooling connections across calls; because N.I.N.A. runs on localhost or a local LAN without TLS handshake overhead (`http://`), per-call client creation avoids event-loop affinity issues while keeping state completely isolated.

---

#### `api_post` (`nina_planner/client.py:137-154`)

1. **Purpose & Architectural Role**
   Primary async HTTP `POST` entry point for sending JSON payloads to N.I.N.A.'s Advanced API (specifically `POST /sequence/load` via `load_sequence_payload`).

2. **Signature, Decorators & Inputs**
   ```python
   async def api_post(path: str, body: Any) -> dict[str, Any]:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `path: str`: Endpoint path relative to `config.NINA_API_URL` (e.g., `"/sequence/load"`).
     - `body: Any`: JSON-serializable payload sent as the HTTP request body.

3. **Outputs & Return Semantics**
   - Returns `cast(dict[str, Any], data.get("Response", {}))`.

4. **Step-by-Step Internal Logic**
   1. **Line 150:** Opens `async with httpx.AsyncClient(base_url=config.NINA_API_URL, timeout=10.0) as client:`.
   2. **Line 151:** Awaits `resp = await client.post(path, json=body)`.
   3. **Line 152:** Calls `data = unwrap_response("POST", path, resp)`.
   4. **Line 153:** Calls `log_payload("_api_post:", json.dumps(data, indent=2))`.
   5. **Line 154:** Returns `cast(dict[str, Any], data.get("Response", {}))`.

5. **Side Effects & I/O**
   - Sends an async HTTP `POST` with `Content-Type: application/json` and a `10.0s` timeout.
   - Appends the response envelope to `config.NINA_PLANNER_LOG`.

6. **Error Handling & Edge Cases**
   - Raises `RuntimeError` on HTTP or N.I.N.A. envelope errors (e.g., when N.I.N.A. refuses a `/sequence/load` over an already-running sequence).
   - Raises `httpx.HTTPError` on network/timeout errors.

7. **Complexity & Risk Notes**
   - $O(|\text{body}| + |\text{response}|)$ serialization and deserialization cost.

---

### 3.3 Sequencer Inspection & Wait-Barrier Functions

#### `_sequence_running` (`nina_planner/client.py:157-159`)

1. **Purpose & Architectural Role**
   Pure predicate that inspects a decoded `/sequence/json` payload and returns `True` if any node anywhere in the sequence tree has `"Status": "RUNNING"`.

2. **Signature, Decorators & Inputs**
   ```python
   def _sequence_running(state: Any) -> bool:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `state: Any`: Decoded `Response` payload from `GET /sequence/json`.

3. **Outputs & Return Semantics**
   - `True` if `"RUNNING"` appears anywhere in `_node_statuses(state)`, else `False`.

4. **Step-by-Step Internal Logic**
   1. **Line 159:** Evaluates `return "RUNNING" in _node_statuses(state)`.

5. **Side Effects & I/O**
   - None (pure in-memory tree walk).

6. **Error Handling & Edge Cases**
   - Handles non-dict/non-list/empty `state` safely because `_node_statuses` returns `[]`.

7. **Complexity & Risk Notes**
   - $O(N)$ where $N$ is the number of nodes/containers/items in the sequence JSON tree.

---

#### `_sequence_is_running` (`nina_planner/client.py:162-163`)

1. **Purpose & Architectural Role**
   Async helper that fetches the live sequence tree from `GET /sequence/json` (`config.SEQUENCE_JSON_PATH`) and returns whether any node is currently `"RUNNING"`. Used both internally by `stop_running_sequence_and_wait` and externally by `nina_planner/purge.py:236` (`_idle_state`) to refuse purging while a sequence is active.

2. **Signature, Decorators & Inputs**
   ```python
   async def _sequence_is_running() -> bool:
   ```
   - **Decorators:** None.
   - **Inputs:** None.

3. **Outputs & Return Semantics**
   - `True` if N.I.N.A.'s sequencer currently has at least one `"RUNNING"` node; `False` otherwise.

4. **Step-by-Step Internal Logic**
   1. **Line 163:** Awaits `await api_get(config.SEQUENCE_JSON_PATH)` and passes the result to `_sequence_running(...)`.

5. **Side Effects & I/O**
   - Issues `GET /sequence/json` via `api_get`.

6. **Error Handling & Edge Cases**
   - Propagates `RuntimeError` or `httpx.HTTPError` if `GET /sequence/json` fails (in `purge.py:235-244`, the caller catches `Exception` and converts it into a `PurgeError` idle-gate failure).

7. **Complexity & Risk Notes**
   - One HTTP `GET` round-trip plus $O(N)$ JSON tree walk.

---

#### `stop_running_sequence_and_wait` (`nina_planner/client.py:166-182`)

1. **Purpose & Architectural Role**
   Stops a running N.I.N.A. sequence (if one is running) and blocks until every node in `/sequence/json` leaves the `"RUNNING"` state. Because N.I.N.A.'s `GET /sequence/stop` returns immediately before the sequencer finishes unwinding (and a subsequent `POST /sequence/load` is rejected if any node is still unwinding), this synchronization barrier guarantees that when `stop_sequence()` returns, the sequencer is genuinely idle.

2. **Signature, Decorators & Inputs**
   ```python
   async def stop_running_sequence_and_wait(timeout: float = 15.0) -> bool:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `timeout: float = 15.0`: Maximum monotonic wait time in seconds after sending `/sequence/stop`.

3. **Outputs & Return Semantics**
   - Returns `False` immediately if no sequence was running when called (`lines 174-175`).
   - Returns `True` once `/sequence/stop` has been issued and a subsequent `_sequence_is_running()` poll returns `False` (`lines 176-180`).

4. **Step-by-Step Internal Logic**
   1. **Lines 174-175:** Checks `if not await _sequence_is_running(): return False`. Avoids sending an unnecessary `/sequence/stop` when already idle.
   2. **Line 176:** Sends `await api_get("/sequence/stop")`.
   3. **Line 177:** Computes `deadline = time.monotonic() + timeout`.
   4. **Lines 178-181:** Loops while `time.monotonic() < deadline`:
      - Polls `if not await _sequence_is_running(): return True`.
      - Sleeps `await anyio.sleep(0.2)` (200 ms poll interval).
   5. **Line 182:** If the deadline expires with the sequence still running, raises `RuntimeError(f"NINA sequence did not stop within {timeout:.0f}s")`.

   ```mermaid
   flowchart TD
       Start(["stop_running_sequence_and_wait(timeout=15.0)"]) --> PreCheck{"await _sequence_is_running()?\n(line 174)"}
       PreCheck -- "False (already idle)" --> RetFalse(["return False\n(line 175)"])
       PreCheck -- "True (running)" --> SendStop["await api_get('/sequence/stop')\n(line 176)"]
       SendStop --> SetDeadline["deadline = time.monotonic() + timeout\n(line 177)"]
       SetDeadline --> CheckClock{"time.monotonic() < deadline?\n(line 178)"}
       CheckClock -- "True" --> PollRunning{"not await _sequence_is_running()?\n(line 179)"}
       PollRunning -- "True (stopped)" --> RetTrue(["return True\n(line 180)"])
       PollRunning -- "False (still unwinding)" --> Sleep["await anyio.sleep(0.2)\n(line 181)"]
       Sleep --> CheckClock
       CheckClock -- "False (deadline expired)" --> RaiseTimeout["raise RuntimeError(\nf'NINA sequence did not stop within {timeout:.0f}s'\n) (line 182)"]
   ```

5. **Side Effects & I/O**
   - Calls `GET /sequence/json`, conditionally `GET /sequence/stop`, and polls `GET /sequence/json` every `0.2s` up to `timeout`.

6. **Error Handling & Edge Cases**
   - Raises `RuntimeError` if the sequence remains `"RUNNING"` past `timeout` (`tests/test_server.py:459-472`).
   - Note that `timeout` is formatted with `{timeout:.0f}s` in the error message (so `timeout=0.01` formats as `"within 0s"`).

7. **Complexity & Risk Notes**
   - Polls at most $\lceil \text{timeout} / 0.2 \rceil$ times (up to 75 polls for the default `15.0s` budget).

---

#### `_node_statuses` (`nina_planner/client.py:191-206`)

1. **Purpose & Architectural Role**
   Recursively walks an arbitrarily nested `/sequence/json` payload (dicts, lists, containers, items, triggers, conditions) and collects every string value stored under a `"Status"` key in pre-order traversal. Serves as the foundational state extractor for both `_sequence_running` and `wait_until_sequence_started`.

2. **Signature, Decorators & Inputs**
   ```python
   def _node_statuses(state: Any) -> list[str]:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `state: Any`: Any JSON-decoded value (`dict`, `list`, or primitive) from `/sequence/json`.

3. **Outputs & Return Semantics**
   - Returns `list[str]` containing every string value bound to `"Status"` anywhere in `state`.
   - Non-string `"Status"` values (e.g., `None`, `int`, `dict`) are ignored (`isinstance(state.get("Status"), str)` at `line 199`).

4. **Step-by-Step Internal Logic**
   1. **Lines 198-203 (`dict` branch):** If `isinstance(state, dict)`:
      - Initializes `statuses = [state["Status"]] if isinstance(state.get("Status"), str) else []` (`line 199`).
      - Iterates `for key, value in state.items():` (`line 200`); for every `key != "Status"` (`line 201`), recursively extends `statuses.extend(_node_statuses(value))` (`line 202`).
      - Returns `statuses` (`line 203`).
   2. **Lines 204-205 (`list` branch):** If `isinstance(state, list)`, returns `[status for item in state for status in _node_statuses(item)]`.
   3. **Line 206 (scalar/other branch):** Returns `[]`.

5. **Side Effects & I/O**
   - Pure recursive in-memory traversal.

6. **Error Handling & Edge Cases**
   - Safely handles empty dicts/lists, scalars, or malformed node objects without raising.

7. **Complexity & Risk Notes**
   - $O(V)$ time and $O(D + S)$ space where $V$ is total JSON nodes/leaves, $D$ is maximum nesting depth, and $S$ is the number of `"Status"` strings.

---

#### `wait_until_sequence_started` (`nina_planner/client.py:209-242`)

1. **Purpose & Architectural Role**
   Blocks after `GET /sequence/start` until at least one node in `GET /sequence/json` transitions away from `"CREATED"`.
   - **Why this barrier exists (`lines 212-216`):** N.I.N.A.'s `GET /sequence/start` returns HTTP 200 as soon as the start command is accepted, *before* start-time validation runs. If validation throws in the first milliseconds, every node stays `"CREATED"` and the sequence silently aborts without running.
   - **Why the criterion is `status != "CREATED"` rather than `status == "RUNNING"` (`lines 218-222`):** A fast sequence (such as a closing park sequence when the mount is already parked) can legitimately complete before the very first `/sequence/json` poll, leaving behind `"FINISHED"` or `"SKIPPED"` nodes and zero `"RUNNING"` nodes. Checking `any(status != "CREATED" for status in statuses)` accepts both currently running sequences (`"RUNNING"`) and sequences that already finished (`"FINISHED"`, `"SKIPPED"`, `"FAILED"`).

2. **Signature, Decorators & Inputs**
   ```python
   async def wait_until_sequence_started(timeout: float | None = None) -> list[str]:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `timeout: float | None = None`: Optional timeout override in seconds. When `None`, resolves to the module constant `SEQUENCE_START_TIMEOUT` (`10.0s`) at call time (`line 227`).

3. **Outputs & Return Semantics**
   - Returns `statuses: list[str]` — the list of all node `"Status"` strings observed on the poll iteration where at least one node had left `"CREATED"`.

4. **Step-by-Step Internal Logic**
   1. **Line 227:** Resolves `budget = SEQUENCE_START_TIMEOUT if timeout is None else timeout`.
   2. **Line 228:** Computes `deadline = time.monotonic() + budget`.
   3. **Lines 229-242:** Enters an unconditional `while True:` loop (ensuring at least one `/sequence/json` read is always performed even if `budget <= 0`):
      - **Line 230:** Polls `statuses = _node_statuses(await api_get(config.SEQUENCE_JSON_PATH))`.
      - **Lines 231-232:** Checks `if any(status != "CREATED" for status in statuses): return statuses`.
      - **Lines 233-241:** Checks `if time.monotonic() >= deadline:`. If expired, summarizes status counts via `counts = {s: statuses.count(s) for s in sorted(set(statuses))}` and raises `RuntimeError` advising the caller to check `get_logs()` and `get_sequence_state()`.
      - **Line 242:** Awaits `await anyio.sleep(0.2)`.

   ```mermaid
   flowchart TD
       Start(["wait_until_sequence_started(timeout=None)"]) --> Init["budget = SEQUENCE_START_TIMEOUT if timeout is None else timeout\ndeadline = time.monotonic() + budget\n(lines 227-228)"]
       Init --> Poll["statuses = _node_statuses(await api_get(config.SEQUENCE_JSON_PATH))\n(line 230)"]
       Poll --> CheckLeftCreated{"any(status != 'CREATED'\nfor status in statuses)?\n(line 231)"}
       CheckLeftCreated -- "True (e.g. RUNNING, FINISHED, SKIPPED)" --> RetStatuses(["return statuses\n(line 232)"])
       CheckLeftCreated -- "False (all CREATED or empty)" --> CheckDeadline{"time.monotonic() >= deadline?\n(line 233)"}
       CheckDeadline -- "False" --> Sleep["await anyio.sleep(0.2)\n(line 242)"]
       Sleep --> Poll
       CheckDeadline -- "True (budget exhausted)" --> BuildCounts["counts = {s: statuses.count(s) for s in sorted(set(statuses))}\n(line 234)"]
       BuildCounts --> RaiseErr["raise RuntimeError(\n'NINA sequence did not start within {budget:.0f}s — every node is still CREATED ...'\n) (lines 235-241)"]
   ```

5. **Side Effects & I/O**
   - Polls `GET /sequence/json` every `0.2s` until a node leaves `"CREATED"` or `budget` elapses.

6. **Error Handling & Edge Cases**
   - **Empty status list (`statuses == []`):** If `/sequence/json` returns no nodes with `"Status"`, `any(...)` is `False` and `counts` is `{}`, so on timeout the error formats `statuses: none reported` (`line 238`).
   - **All nodes stuck in `CREATED`:** Raises `RuntimeError` with total node count, status histogram, and remediation instructions (`check get_logs() and get_sequence_state() before retrying`). Tested in `tests/test_server.py:2588-2609`.

7. **Complexity & Risk Notes**
   - Up to $\lceil 10.0 / 0.2 \rceil = 50$ polls over the default `10.0s` budget; typically succeeds on the 1st or 2nd poll (~0–200 ms).

---

#### `load_sequence_payload` (`nina_planner/client.py:245-252`)

1. **Purpose & Architectural Role**
   Posts a compiled N.I.N.A. sequence JSON AST to `POST /sequence/load`. Intentionally performs **no** pre-check and **no** auto-stop of a running sequence (`lines 248-250`): if a sequence is already running, N.I.N.A.'s own refusal error is passed straight through to the caller, enforcing the policy that a running sequence is never stopped implicitly without an explicit `stop_sequence()` call.

2. **Signature, Decorators & Inputs**
   ```python
   async def load_sequence_payload(seq: Any) -> None:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `seq: Any`: Compiled N.I.N.A. root container dictionary produced by `build_sequence_many()`.

3. **Outputs & Return Semantics**
   - Returns `None` (discards the `dict[str, Any]` returned by `api_post`, which has already been recorded in the debug log by `api_post`).

4. **Step-by-Step Internal Logic**
   1. **Line 252:** Awaits `await api_post("/sequence/load", seq)`.

5. **Side Effects & I/O**
   - Sends `POST /sequence/load` with the full sequence JSON body to N.I.N.A., replacing the loaded sequence in N.I.N.A.'s Advanced Sequencer UI.

6. **Error Handling & Edge Cases**
   - Propagates `RuntimeError` from `api_post` / `unwrap_response` if N.I.N.A. rejects the sequence (e.g., sequence currently running or invalid JSON structure).

7. **Complexity & Risk Notes**
   - Single HTTP `POST` call.

---

### 3.4 Liveness Probing & Subprocess Helper Functions

#### `api_probe` (`nina_planner/client.py:255-267`)

1. **Purpose & Architectural Role**
   Host-agnostic REST API liveness probe via `GET /time`. Returns both a boolean liveness flag and the failure reason string when unreachable or unhealthy, enabling `get_nina_status()` and `stop_nina()` (`tools/lifecycle.py`) to report *why* N.I.N.A.'s API did not respond without raising an exception.

2. **Signature, Decorators & Inputs**
   ```python
   async def api_probe() -> tuple[bool, str | None]:
   ```
   - **Decorators:** None.
   - **Inputs:** None.

3. **Outputs & Return Semantics**
   - `(True, None)` when `await api_get("/time")` succeeds (`lines 264-265`).
   - `(False, str(e))` when `await api_get("/time")` raises any `Exception` (`lines 266-267`).

4. **Step-by-Step Internal Logic**
   1. **Lines 263-265:** Inside a `try` block, awaits `await api_get("/time")` and returns `(True, None)`.
   2. **Lines 266-267:** Catches `Exception as e` (covering `httpx.ConnectError`, `httpx.TimeoutException`, `RuntimeError` from `unwrap_response`, etc.) and returns `(False, str(e))`.

5. **Side Effects & I/O**
   - Issues `GET /time` with a `10.0s` timeout.

6. **Error Handling & Edge Cases**
   - Catches all `Exception` subclasses (while allowing `BaseException` such as `asyncio.CancelledError` or `KeyboardInterrupt` to propagate).

7. **Complexity & Risk Notes**
   - Lightweight single `GET /time` round-trip.

---

#### `nina_api_responsive` (`nina_planner/client.py:270-272`)

1. **Purpose & Architectural Role**
   Convenience boolean wrapper around `api_probe()` that returns `True` if and only if N.I.N.A.'s REST API answers `GET /time`. Used by `stop_nina()` and `start_nina()` in `nina_planner/tools/lifecycle.py:138, 165`.

2. **Signature, Decorators & Inputs**
   ```python
   async def nina_api_responsive() -> bool:
   ```
   - **Decorators:** None.
   - **Inputs:** None.

3. **Outputs & Return Semantics**
   - Returns `(await api_probe())[0]` (`bool`).

4. **Step-by-Step Internal Logic**
   1. **Line 272:** Awaits `api_probe()` and returns element `0` of the `(bool, str | None)` tuple.

5. **Side Effects & I/O**
   - Same as `api_probe()` (`GET /time`).

6. **Error Handling & Edge Cases**
   - Never raises `Exception`; returns `False` on any probe failure.

7. **Complexity & Risk Notes**
   - $O(1)$ wrapper.

---

#### `run_blocking` (`nina_planner/client.py:275-281`)

1. **Purpose & Architectural Role**
   Executes a blocking OS subprocess (`subprocess.run`) on an AnyIO worker thread so that Windows lifecycle commands (`schtasks.exe`, `taskkill.exe` in `nina_planner/tools/lifecycle.py:29, 58, 123`) never block the async MCP event loop.

2. **Signature, Decorators & Inputs**
   ```python
   async def run_blocking(argv: list[str], *, timeout: float = 30.0) -> Any:
   ```
   - **Decorators:** None.
   - **Inputs:**
     - `argv: list[str]`: Command and argument vector passed directly to `subprocess.run` (with `shell=False`).
     - `timeout: float = 30.0` (keyword-only): Subprocess execution timeout in seconds passed to `subprocess.run(..., timeout=timeout)`.

3. **Outputs & Return Semantics**
   - Returns `subprocess.CompletedProcess[str]` (annotated as `Any`) with `stdout: str`, `stderr: str`, and `returncode: int`.

4. **Step-by-Step Internal Logic**
   1. **Lines 278-279:** Defines closure `def _run() -> Any: return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)`.
   2. **Line 281:** Offloads `_run` to a worker thread and awaits completion via `return await anyio.to_thread.run_sync(_run)`.

5. **Side Effects & I/O**
   - Spawns an OS child process with `stdout` and `stderr` captured in text mode.

6. **Error Handling & Edge Cases**
   - Does **not** pass `check=True`, so non-zero exit codes return a `CompletedProcess` with `returncode != 0` for callers (`tools/lifecycle.py`) to inspect (for example, `stop_nina` treats `taskkill.exe` exit code `128` as a benign race where the process already exited).
   - Raises `subprocess.TimeoutExpired` if the child process exceeds `timeout` seconds, or `OSError` (`FileNotFoundError`) if `argv[0]` does not exist.

7. **Complexity & Risk Notes**
   - Uses `shell=False` via argument list `argv`, preventing shell-injection vulnerabilities.
