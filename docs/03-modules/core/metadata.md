# `nina_planner.metadata` — Live & CLI Imaging Root Resolution and Async Metadata Reader

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ Core Package](./README.md) · **Callees:** [`imaging.py`](./imaging.md) · [`tools/equipment.py`](../tools/equipment.md) · **Callers:** [`tools/plans.py`](../tools/plans.md) · [`tools/sequence.py`](../tools/sequence.md) · [`archive.py`](../cli/archive.md) · [`purge.py`](../cli/purge.md) · [`scripts/gen_sequence.py`](../scripts-and-skills/scripts.md)

---

## 1. Module Overview

| Attribute | Value |
|---|---|
| **Source File** | `.opencode/local/mcps/nina_planner/nina_planner/metadata.py` |
| **Line Count** | 66 lines |
| **Architectural Layer** | Core Domain Service — Async Bridge Between N.I.N.A. Profile Resolution and Filesystem Metadata Scans |
| **Package** | `nina_planner` |

### Purpose & Module Docstring (`metadata.py:1`)

`metadata.py` resolves the observatory's active imaging directory—either dynamically from N.I.N.A.'s REST API site profile (`get_site_profile().local_image_save_path`) or via an explicit CLI `--imaging-root` flag when N.I.N.A. is offline—and dispatches blocking `ImageMetaData.csv` directory tree walks (`imaging.read_imaging_csv`) onto a background worker thread (`anyio.to_thread.run_sync`).

### Inbound Callers

| Caller Module | Imported Symbols | Call Sites & Usage Context |
|---|---|---|
| `nina_planner/tools/plans.py` | `metadata` (`13`) | `get_plan_progress` calls `await metadata.read_metadata()` (`213`); `audit_plans` calls `await metadata.resolve_imaging_root()` (`821`, `836`) and `await metadata.read_metadata()` (`822`, `837`). |
| `nina_planner/tools/sequence.py` | `metadata` (`6`) | `_full_mode_note` calls `await metadata.read_metadata("light")` (`90`); `_load_many` lazy `rows()` helper calls `await metadata.read_metadata()` (`206`). |
| `nina_planner/archive.py` | `metadata` (`90`) | `_resolve_root` calls `await metadata.resolve_cli_root(args.imaging_root, ArchiveError, "archive")` (`921`). |
| `nina_planner/purge.py` | `metadata` (`95`) | `_resolve_root` calls `await metadata.resolve_cli_root(args.imaging_root, PurgeError, "purge")` (`223`). |
| `scripts/gen_sequence.py` | `metadata` (`44`) | `_generate` lazy `get_rows()` helper calls `await metadata.read_metadata()` (`147`). |

### Outbound Imports

| Category | Import Statement | Line(s) | Purpose |
|---|---|---|---|
| **Stdlib** | `from pathlib import Path` | `3` | Represents resolved host-local imaging root directories. |
| **Stdlib** | `from typing import Any` | `4` | Type annotation for metadata row dictionaries (`list[dict[str, Any]]`). |
| **Third-Party** | `import anyio` | `6` | `anyio.to_thread.run_sync` offloads synchronous CSV tree traversal in `read_metadata`. |
| **Internal** | `from .imaging import read_imaging_csv` | `8` | Synchronous tree-walking CSV parser invoked inside worker thread. |
| **Internal** | `from .tools import equipment as equipment_tools` | `9` | Calls `await equipment_tools.get_site_profile()` to fetch the active N.I.N.A. profile and its `local_image_save_path`. |

### Module-Level Constants, Type Aliases & Overloads

*(None defined in `metadata.py`.)*

---

## 2. Classes, Data Models & Exceptions

`metadata.py` defines **no classes, Pydantic models, or custom exceptions**.
- `resolve_cli_root` is parameterized over a caller-supplied exception class `error: type[Exception]` (`ArchiveError` from `archive.py` or `PurgeError` from `purge.py`) so CLI resolution errors raise the exact domain exception expected by each CLI's top-level error handler.

---

## 3. Function & Method Reference

| Symbol | Kind | Lines | Summary |
|---|---|---|---|
| [`resolve_imaging_root`](#31-resolve_imaging_root) | `async def` | `12-19` | Fetches the live N.I.N.A. site profile and returns `Path(profile.local_image_save_path)`. |
| [`resolve_cli_root`](#32-resolve_cli_root) | `async def` | `22-47` | Resolves `--imaging-root` if provided, or falls back to `resolve_imaging_root()`, wrapping failures in `error`. |
| [`read_metadata`](#33-read_metadata) | `async def` | `50-66` | Resolves the live imaging root and runs `read_imaging_csv` in an `anyio` worker thread. |

---

### 3.1 `resolve_imaging_root`

**Location:** `nina_planner/metadata.py:12-19`

#### 1. Purpose & Architectural Role
Queries N.I.N.A. for the active observatory site profile via `equipment_tools.get_site_profile()` and returns its host-mapped `local_image_save_path` as a `Path`. Note that `ObservatoryProfile.local_image_save_path` (`models/profile.py:119-128`) has already passed N.I.N.A.'s raw Windows `ImageSavePath` through `imaging.windows_to_local` (rebasing drive letters onto `NINA_DRIVE_MOUNT` or `/mnt/<drive>` on non-Windows hosts).

#### 2. Signature, Decorators & Inputs
```python
async def resolve_imaging_root() -> Path:
```
- **Decorators:** None.
- **Inputs:** None.

#### 3. Outputs & Return Semantics
- Returns `Path(profile.local_image_save_path)`. Does **not** check whether the directory exists on disk (`imaging._require_root` performs the existence check inside `read_imaging_csv`).

#### 4. Step-by-Step Internal Logic
1. **Fetch site profile (`18`):** Awaits `profile = await equipment_tools.get_site_profile()`.
2. **Convert to `Path` (`19`):** Returns `Path(profile.local_image_save_path)`.

#### 5. Side Effects & I/O
- Performs HTTP `GET /profile/show?active=true` and `GET /profile/horizon` via `equipment_tools.get_site_profile()`.

#### 6. Error Handling & Edge Cases
- Propagates any `RuntimeError`, `httpx.HTTPError`, or `ValidationError` raised by `get_site_profile()` when N.I.N.A.'s API is unreachable or returns an error.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(1)$ local work, bounded by two HTTP requests to N.I.N.A.

---

### 3.2 `resolve_cli_root`

**Location:** `nina_planner/metadata.py:22-47`

#### 1. Purpose & Architectural Role
Resolves the imaging root directory for standalone CLI tools (`nina-archive` and `nina-purge`). Supports two operational modes:
1. **Explicit Override (`--imaging-root`):** Expands `~` and verifies the directory exists locally without contacting N.I.N.A., enabling archiving and purging when N.I.N.A. is shut down.
2. **Live Profile Fallback (`imaging_root is None`):** Calls `await resolve_imaging_root()`, and if N.I.N.A. cannot be reached, raises the caller's CLI exception class (`ArchiveError` / `PurgeError`) with an operator hint to pass `--imaging-root`.

#### 2. Signature, Decorators & Inputs
```python
async def resolve_cli_root(
    imaging_root: str | None, error: type[Exception], command: str
) -> Path:
```
- **`imaging_root` (`str | None`):** Value of the CLI `--imaging-root` flag, or `None` if omitted.
- **`error` (`type[Exception]`):** Exception class to instantiate and raise on failure (`ArchiveError` or `PurgeError`).
- **`command` (`str`):** CLI command name interpolated into the error recovery hint (e.g., `"archive"` or `"purge"`).

#### 3. Outputs & Return Semantics
- Returns a `Path` to the imaging root directory. When `imaging_root` is explicitly supplied, the returned `Path` is guaranteed to be an existing directory (`root.is_dir() == True`).

#### 4. Step-by-Step Internal Logic
1. **Handle explicit `--imaging-root` override (`35-39`):**
   - If `imaging_root` is truthy (`35`), expands user home (`~`) via `root = Path(imaging_root).expanduser()` (`36`).
   - Validates `if not root.is_dir():` (`37`); if missing or not a directory, raises `error(f"--imaging-root does not exist or is not a directory: {root}")` (`38`).
   - Returns `root` (`39`).
2. **Fallback to live N.I.N.A. site profile (`40-47`):**
   - Attempts `return await resolve_imaging_root()` inside a `try` block (`40-41`).
   - Catches `Exception as exc` (`42`) and raises `error(f"cannot resolve the imaging root from the N.I.N.A. site profile ({exc}). Is NINA running? Pass --imaging-root to {command} without NINA.") from exc` (`43-47`).

#### 5. Side Effects & I/O
- When `imaging_root` is provided: synchronous `Path.expanduser()` and `Path.is_dir()` stat check.
- When `imaging_root` is `None`: network I/O to N.I.N.A. via `resolve_imaging_root()`.

#### 6. Error Handling & Edge Cases
- **Broad `except Exception` (`42`):** Intentionally catches any HTTP/network/profile error (`# noqa: BLE001`) and chains it (`from exc`) into `error` so the CLI prints a clean `✗ ...` message instead of an unhandled traceback.
- **Note on existence check in fallback branch:** When `resolve_imaging_root()` succeeds, `resolve_cli_root` returns that path directly; subsequent `read_imaging_csv` calls in `archive.py`/`purge.py` validate `root.is_dir()` via `imaging._require_root`.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(1)$.

---

### 3.3 `read_metadata`

**Location:** `nina_planner/metadata.py:50-66`

#### 1. Purpose & Architectural Role
High-level async entrypoint used by MCP tools (`get_plan_progress`, `audit_plans`, `load_sequence`) and `scripts/gen_sequence.py` to retrieve all attributed frame rows from every visible `ImageMetaData.csv` under the active N.I.N.A. profile's imaging root. Offloads the blocking filesystem walk (`read_imaging_csv`) to an `anyio` worker thread so the async event loop remains responsive.

#### 2. Signature, Decorators & Inputs
```python
async def read_metadata(image_type: str | None = None) -> list[dict[str, Any]]:
```
- **`image_type` (`str | None`, default `None`):** Optional frame type filter (`"light"`, `"dark"`, `"bias"`, `"flat"`, case-insensitive). When `None`, returns rows for all four `FRAME_TYPES` in a single filesystem walk.

#### 3. Outputs & Return Semantics
- Returns `list[dict[str, Any]]`, where each dict represents an existing frame row from `ImageMetaData.csv` with snake_cased keys, resolved host-local `"file_path"`, uppercase `"frame_type"`, and `"source": "image_metadata"`.

#### 4. Step-by-Step Internal Logic
1. **Resolve live imaging root (`63`):** Awaits `root = await resolve_imaging_root()`.
2. **Run CSV scan on worker thread (`64-66`):** Awaits and returns `await anyio.to_thread.run_sync(lambda: read_imaging_csv(root=root, image_type=image_type))`.

#### 5. Side Effects & I/O
- Network I/O to N.I.N.A. (`resolve_imaging_root`) followed by recursive directory traversal, CSV reads, and per-frame `Path.is_file()` stat calls in a background thread (`read_imaging_csv`).

#### 6. Error Handling & Edge Cases
- **`FileNotFoundError`:** Raised by `read_imaging_csv` (`_require_root`) if `root` does not exist or is not a directory on this host (e.g., unmounted SMB share or misconfigured `NINA_DRIVE_MOUNT`).
- **Profile fetch errors:** Propagate directly if N.I.N.A. is unreachable. Note that `_full_mode_note` (`tools/sequence.py:88-91`) catches `Exception` around `read_metadata("light")` because the full-mode overwrite note is advisory, whereas `remaining`-mode loads, `get_plan_progress`, and `audit_plans` let errors fail closed.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(D + R)$ where $D$ is visible directories under `root` and $R$ is total CSV rows across all `ImageMetaData.csv` files.
