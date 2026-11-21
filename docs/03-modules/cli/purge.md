# Module Specification: `nina_planner/purge.py` (`nina-purge`)

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ CLI Package](./README.md) · [⬅️ `archive.py`](./archive.md) · [➡️ `pin_id.py`](./pin_id.md) · [🔗 `client.py`](../core/client.md) · [🔗 `imaging.py`](../core/imaging.md) · [🔗 `metadata.py`](../core/metadata.md) · [🔗 `plan_utils.py`](../core/plan_utils.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/purge.py`
- **Line Count:** 1,091 lines (`1-1091`)
- **Console Script Entrypoint:** `nina-purge = "nina_planner.purge:main"` (`pyproject.toml:62`)
- **Architectural Layer:** CLI Retirement & Quarantine Engine (`nina_planner` CLI layer)
- **Purpose & Module Docstring (`purge.py:1-82`):**
  Completes the observation-plan storage lifecycle by retiring archived frames and finished plan JSON files from the N.I.N.A. imaging root and project directory. Deletion is strictly human-gated into two explicit phases:
  1. **Quarantine (`--apply`):** Moves prunable top-level directories under the imaging root into `<imaging-root>/.quarantine/<dir>` (a dot-directory automatically skipped by `imaging._prunable_dir`, hiding its frames and `ImageMetaData.csv` from `get_plan_progress` and `audit_plans` while keeping recovery a single `mv` away).
  2. **Permanent Deletion (`--purge`):** Re-evaluates the three plan-file archive gates against `<imaging-root>/purge-index.jsonl`, unlinks released directories inside `.quarantine/`, removes the empty `.quarantine/` container, and—as the final step once all frames of a plan are confirmed gone—unlinks the project's original `plan.json` file (whose verbatim copy is preserved in `<dest>/<stem>__<plan-id>/plan.json`).

  A recorded frame $F$ is prunable if and only if:
  $$\text{prunable}(F) \iff \text{preserved}(F) \land \forall P \in \text{claims}(F),\ \text{released}(P)$$
  where $\text{preserved}(F)$ means $F$'s source path appears in at least one committed `<dest>/*/manifest.json` (`files[].source`, including `junk/` frames), and $\text{released}(P)$ requires that plan $P$'s archive folder passes all 3 plan-file gates **and** either $P$ still reads complete on live rows now (`_progress_facts`) or its `manifest.json` carries the explicit `--all` waiver (`"complete": false`). Top-level directories are the unit of quarantine: a directory is `PRUNE` only if **every** recorded frame inside it is prunable (an `AND` verdict; `BAD_` reject twins ride along and never block, while directories with zero recorded frames are held).

### 1.1 Inbound Callers
- **CLI Entrypoint:** Invoked as `nina-purge` (`pyproject.toml:62`) or `python -m nina_planner.purge` (`purge.py:1090-1091`).
- **Agent Skills:** Invoked by `.agents/skills/archive-lifecycle/` (run via `uv run --project .opencode/local/mcps/nina_planner`).
- **Automated Test Suite:** `.opencode/local/mcps/nina_planner/tests/test_purge.py` (1,220 lines, 8 test classes).

### 1.2 Outbound Imports
- **Python Standard Library (`purge.py:84-93`):**
  - `from __future__ import annotations` (`84`)
  - `argparse` (`86`), `asyncio` (`87`), `json` (`88`), `shutil` (`89`), `sys` (`90`), `from datetime import UTC, datetime` (`91`), `from pathlib import Path` (`92`), `from typing import Any` (`93`)
- **Internal Package Imports (`purge.py:95-108`):**
  - `from . import config, metadata, plan_utils` (`95`)
  - `from .archive import _MANIFEST_NAME, ArchiveError, _folder_name, _human_bytes, _inside, _looks_like_plan_file, _progress_facts, _select_frames` (`96-105`)
  - `from .client import _sequence_is_running` (`106`)
  - `from .imaging import _prunable_dir, read_imaging_csv` (`107`)
  - `from .models.plan import ObservationPlan` (`108`)

### 1.3 Module Constants & Globals (`purge.py:110-118`)

| Constant | Line(s) | Type | Value | Purpose |
|---|---|---|---|---|
| `_QUARANTINE_DIR` | `110` | `str` | `".quarantine"` | Name of the hidden quarantine subdirectory inside `<imaging-root>` (starts with `"."` so `imaging._prunable_dir` prunes it from CSV walks). |
| `_INDEX_NAME` | `111` | `str` | `"purge-index.jsonl"` | Filename of the append-only JSON Lines audit log inside `<imaging-root>`. |
| `_FRAME_TYPES` | `112` | `tuple[str, ...]` | `("light", "dark", "bias", "flat")` | Lowercase frame type identifiers. |
| `_SIDECAR_NAMES` | `116-118` | `frozenset[str]` | `frozenset({"ImageMetaData.csv", "WeatherData.csv", "AcquisitionDetails.csv"})` | Session Metadata plugin CSV filenames excluded from `_unrecorded_count` so legitimate sidecar CSVs are never flagged as anomalous unrecorded files. |

### 1.4 CLI Flags & Arguments (`_build_parser`, `purge.py:704-758`)

| Argument / Flag | `argparse` Configuration | Default | Description |
|---|---|---|---|
| `--dest` | `default=".archive"` | `".archive"` (`./.archive` relative to `cwd`) | Archive directory holding `manifest.json` receipts (must match the `--dest` used with `nina-archive` and resolve outside `--imaging-root`). |
| `--imaging-root` | `default=None` | `None` (queries N.I.N.A. site profile) | N.I.N.A. image save path to purge frames from; overrides the live site-profile lookup so purging works when N.I.N.A. is offline. |
| `--project-dir` | `default=None` | `None` (`config.PROJECT_DIR`) | Project directory holding observation plan JSON files. |
| `--apply` | `action="store_true"` | `False` | Move prunable top-level directories into `<imaging-root>/.quarantine/` (default without flags is a dry-run verdict report). |
| `--purge` | `action="store_true"` | `False` | Permanently delete released directories in `<imaging-root>/.quarantine/` and unlink each purged plan's original `plan.json` (independent of `--apply`; idempotent). |

### 1.5 Exit Code Contract (`purge.py:78-81`, `781-881`, `1068-1077`)

| Exit Code | Condition | Source |
|---:|---|---|
| `0` | No errors or stale-archive refusals occurred (`not (errors_occurred or refusals)`) **and** either: (a) in dry-run mode (`not (args.apply or args.purge)`), at least one directory is `prunable` (`actionable > 0`); (b) under `--apply`, at least one directory was moved to `.quarantine/` (`actionable > 0`); or (c) `--purge` was passed (`return 0 if args.purge else 1` — `--purge` is idempotent even when `actionable == 0`). | `run` (`purge.py:1068-1077`) |
| `1` | `--imaging-root` resolution failed (`781`); `--dest` is inside `--imaging-root` (`792`); `read_imaging_csv` raised `FileNotFoundError` (`802`); any plan-shaped JSON failed to load or had an illegal `_folder_name` (`errors` non-empty at `881`); any plan failed Gate 2 byte-identity check (`refusals` non-empty at `1068`); N.I.N.A. sequence is currently `RUNNING` when `--apply` (with prunable dirs) or `--purge` is requested (`985`); any quarantined item was kept/failed or plan-file unlink failed under `--purge` (`1030`, `1043`); or `actionable == 0` when `not args.purge` (`1076`). | `run` (`purge.py:781-1076`) |
| `2` | Invalid CLI arguments (`argparse` usage error). | `_build_parser().parse_args(argv)` (`purge.py:1086`) |

---

## 2. Classes, Data Models & Exceptions

### `PurgeError(Exception)` (`purge.py:121-122`)
- **Inheritance:** `builtins.Exception`
- **Docstring:** `"An action could not proceed; the message is user-facing."`
- **Role:** Passed to `metadata.resolve_cli_root(args.imaging_root, PurgeError, "purge")` inside `_resolve_root` (`purge.py:223`) and caught in `run` (`purge.py:779-781`).

---

## 3. Function & Method Reference

### 3.1 `_now` (`purge.py:128-129`)

1. **Purpose & Architectural Role**
   Returns the current UTC timestamp truncated to whole seconds (`microsecond=0`), used for calibration-window age checks (`_select_frames`, `_progress_facts`) and ISO-8601 `"at"` timestamps in `purge-index.jsonl`.

2. **Signature, Decorators & Inputs**
   ```python
   def _now() -> datetime:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - **Returns:** `datetime` — Timezone-aware `UTC` `datetime` with `microsecond=0`.

4. **Step-by-Step Internal Logic**
   1. **Line 129:** `return datetime.now(UTC).replace(microsecond=0)`.

5. **Side Effects & I/O**
   - Reads system clock.

6. **Error Handling & Edge Cases**
   - Extracted as a helper so unit tests (`tests/test_purge.py`) can monkeypatch `purge._now` deterministically.

7. **Complexity & Risk Notes**
   - $O(1)$.

---

### 3.2 `_norm` (`purge.py:132-134`)

1. **Purpose & Architectural Role**
   Normalizes any path-like value into a canonical `str(Path(str(path)))` string so `manifest.json` `files[].source` strings and live CSV `row["file_path"]` strings compare identically in `preserved` and `claims` lookups.

2. **Signature, Decorators & Inputs**
   ```python
   def _norm(path: Any) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:** `path: Any` — Path string or `Path` object.

3. **Outputs & Return Semantics**
   - **Returns:** `str` — Normalized path string (`str(Path(str(path)))`).

4. **Step-by-Step Internal Logic**
   1. **Line 134:** `return str(Path(str(path)))`.

5. **Side Effects & I/O**
   - None (pure lexical normalization via `pathlib.Path`).

6. **Error Handling & Edge Cases**
   - Collapses redundant slashes and trailing separators identically to how `archive._archive_one` recorded `str(src)`.

7. **Complexity & Risk Notes**
   - $O(1)$.

---

### 3.3 `_append_index` (`purge.py:137-139`)

1. **Purpose & Architectural Role**
   Appends a single JSON record line to `<base>/purge-index.jsonl` (`_INDEX_NAME`).

2. **Signature, Decorators & Inputs**
   ```python
   def _append_index(base: Path, record: dict[str, Any]) -> None:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `base: Path` — The N.I.N.A. imaging root directory.
     - `record: dict[str, Any]` — Serializable audit record (`"action": "quarantine" | "purge" | "plan-file"`).

3. **Outputs & Return Semantics**
   - **Returns:** `None`.

4. **Step-by-Step Internal Logic**
   1. **Lines 138-139:** Opens `(base / _INDEX_NAME).open("a", encoding="utf-8") as handle:` and writes `json.dumps(record, ensure_ascii=False) + "\n"`.

5. **Side Effects & I/O**
   - **Disk Write:** Creates or appends to `<base>/purge-index.jsonl`.

6. **Error Handling & Edge Cases**
   - Propagates `OSError` if the imaging root is unwritable.

7. **Complexity & Risk Notes**
   - $O(1)$ append I/O.

---

### 3.4 `_tree_size` (`purge.py:142-150`)

1. **Purpose & Architectural Role**
   Recursively counts total regular files and total bytes under `path` (`path.rglob("*")`) for post-quarantine and pre-purge accounting in `purge-index.jsonl` and console summaries.

2. **Signature, Decorators & Inputs**
   ```python
   def _tree_size(path: Path) -> tuple[int, int]:
   ```
   - **Decorators:** None.
   - **Parameters:** `path: Path` — Directory tree to measure.

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[int, int]` — `(files, total)` where `files` is the count of regular files (`child.is_file()`) and `total` is the sum of `child.stat().st_size` in bytes.

4. **Step-by-Step Internal Logic**
   1. **Lines 144-145:** Initializes `files = 0` and `total = 0`.
   2. **Lines 146-149:** Iterates `for child in path.rglob("*"):`; if `child.is_file():`, increments `files += 1` and `total += child.stat().st_size`.
   3. **Line 150:** Returns `files, total`.

5. **Side Effects & I/O**
   - **Filesystem Walk:** Calls `rglob("*")` and `stat()` on every file under `path`.

6. **Error Handling & Edge Cases**
   - Includes sidecar CSVs (`ImageMetaData.csv`, `WeatherData.csv`, `AcquisitionDetails.csv`) in total file/byte accounting.

7. **Complexity & Risk Notes**
   - $O(N)$ in total directory entries $N$ under `path`.

---

### 3.5 `_unrecorded_count` (`purge.py:153-164`)

1. **Purpose & Architectural Role**
   Computes the number of anomalous "unrecorded" files inside a quarantined directory `path`—files that are neither recorded CSV frames, `BAD_` reject twins, nor known Session Metadata sidecar CSVs (`_SIDECAR_NAMES`).

2. **Signature, Decorators & Inputs**
   ```python
   def _unrecorded_count(path: Path, recorded: int) -> int:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `path: Path` — Quarantined directory path.
     - `recorded: int` — Sum of recorded frames plus `BAD_` rejects in that directory (`entry["frames"] + entry["rejects"]`).

3. **Outputs & Return Semantics**
   - **Returns:** `int` — Non-negative count `max(count - recorded, 0)` of non-sidecar files exceeding `recorded`.

4. **Step-by-Step Internal Logic**
   1. **Line 160:** Initializes `count = 0`.
   2. **Lines 161-163:** Iterates `for child in path.rglob("*"):`; if `child.is_file() and child.name not in _SIDECAR_NAMES:`, increments `count += 1`.
   3. **Line 164:** Returns `max(count - recorded, 0)`.

5. **Side Effects & I/O**
   - **Filesystem Walk:** Walks `path.rglob("*")`.

6. **Error Handling & Edge Cases**
   - `max(..., 0)` prevents negative numbers if a recorded frame vanished between CSV read and quarantine move.
   - Excluding `_SIDECAR_NAMES` prevents `ImageMetaData.csv`, `WeatherData.csv`, and `AcquisitionDetails.csv` from triggering false-positive `"unrecorded file(s) rode along"` warnings (`purge.py:466-470`).

7. **Complexity & Risk Notes**
   - $O(N)$ in total directory entries $N$ under `path`.

---

### 3.6 `_unique_join` (`purge.py:167-171`)

1. **Purpose & Architectural Role**
   Deduplicates a list of hold-reason strings while preserving first-seen order, and truncates lists longer than `limit` (default `3`) with a `"; +<K> more"` suffix for compact verdict-table display.

2. **Signature, Decorators & Inputs**
   ```python
   def _unique_join(reasons: list[str], limit: int = 3) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `reasons: list[str]` — Raw per-frame hold reasons collected in `_classify`.
     - `limit: int = 3` — Maximum unique reasons to display verbatim before summarizing the remainder.

3. **Outputs & Return Semantics**
   - **Returns:** `str` — Semicolon-separated summary string.

4. **Step-by-Step Internal Logic**
   1. **Line 168:** Deduplicates while preserving order via `seen = list(dict.fromkeys(reasons))`.
   2. **Lines 169-170:** If `len(seen) > limit:`, returns `"; ".join(seen[:limit]) + f"; +{len(seen) - limit} more"`.
   3. **Line 171:** Returns `"; ".join(seen)`.

5. **Side Effects & I/O**
   - None (pure string formatting).

6. **Error Handling & Edge Cases**
   - Empty `reasons` returns `""`.

7. **Complexity & Risk Notes**
   - $O(R)$ where $R = \text{len}(\text{reasons})$.

---

### 3.7 `_plan_file_gate` (`purge.py:174-219`)

1. **Purpose & Architectural Role**
   Enforces the three archive-integrity gates for a project plan file **before** any frame is released, quarantined, or purged, and before the plan file itself can be unlinked:
   - **Gate 1 (`archive_ok`):** `<dest>/<stem>__<plan-id>/plan.json` must exist.
   - **Gate 2 (`not stale`):** `<dest>/<stem>__<plan-id>/plan.json` must be byte-for-byte identical to `plan_file`.
   - **Gate 3 (`not _inside(plan_file, dest)`):** `plan_file` must not reside inside `--dest`.

2. **Signature, Decorators & Inputs**
   ```python
   def _plan_file_gate(
       plan_file: Path, plan: ObservationPlan, dest: Path
   ) -> tuple[str, bool, bool, str]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan_file: Path` — Path to the project's plan JSON file.
     - `plan: ObservationPlan` — Loaded observation plan model.
     - `dest: Path` — Archive root directory (`--dest`).

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[str, bool, bool, str]` — `(folder, archive_ok, stale, reason)` where:
     - `folder: str` is `str(dest / _folder_name(plan_file, plan))`.
     - `archive_ok: bool` is `True` iff Gate 3 and Gate 1 pass (and reading bytes for Gate 2 did not raise `OSError`).
     - `stale: bool` is `True` iff Gate 1 passed but `plan_file.read_bytes() != archived.read_bytes()` (Gate 2 failed).
     - `reason: str` is `""` when all three gates pass, or a descriptive refusal reason otherwise.
   - **Raises:** `ArchiveError` (propagated from `_folder_name` on line 196 if `plan_id` is `"."` or `".."`).

4. **Step-by-Step Internal Logic**
   1. **Lines 196-197:** Computes `folder_path = dest / _folder_name(plan_file, plan)` and `folder = str(folder_path)`.
   2. **Lines 198-199 (Gate 3):** If `_inside(plan_file, dest):`, returns `folder, False, False, "plan file is inside --dest (never deleted)"`.
   3. **Lines 200-207 (Gate 1):** Checks `archived = folder_path / "plan.json"`. If `not archived.is_file():`, sets `reason` to `f"no archive folder at {folder_path}"` (when `not folder_path.is_dir()`) or `f"no plan.json in archive folder {folder_path}"` (when folder exists without `plan.json`), and returns `folder, False, False, reason`.
   4. **Lines 208-218 (Gate 2):** Compares `identical = plan_file.read_bytes() == archived.read_bytes()`.
      - On `OSError as exc:`, returns `folder, False, False, f"cannot compare against the archive ({exc})"`.
      - If `not identical:`, returns `folder, True, True, "archive is stale, re-run nina-archive --force first"`.
   5. **Line 219:** Returns `folder, True, False, ""`.

5. **Side Effects & I/O**
   - **Disk Read:** Stats `folder_path` and `archived`, and reads bytes of both `plan_file` and `archived`.

6. **Error Handling & Edge Cases**
   - When Gate 2 fails (`stale=True`), `archive_ok` is still `True` (indicating Gate 1 passed), and `run` appends `(plan_file, gate_reason)` to `refusals` (`purge.py:846-847`), blocking release of the entire `plan_id` (`purge.py:902-904`), blocking quarantine purge in `_quarantine_gates` (`purge.py:587-591`), keeping the plan file in `_sweep_plan_files` (`purge.py:659-660`), and forcing exit code `1` (`purge.py:1068`).

7. **Complexity & Risk Notes**
   - $O(S)$ byte comparison where $S$ is the size of `plan.json`.

---

### 3.8 `_resolve_root` (`purge.py:222-223`)

1. **Purpose & Architectural Role**
   Resolves the N.I.N.A. imaging root directory from `args.imaging_root` or the active N.I.N.A. site profile via `metadata.resolve_cli_root`, raising `PurgeError` on failure.

2. **Signature, Decorators & Inputs**
   ```python
   async def _resolve_root(args: argparse.Namespace) -> Path:
   ```
   - **Decorators:** None (`async def`).
   - **Parameters:** `args: argparse.Namespace` — Parsed CLI arguments containing `.imaging_root`.

3. **Outputs & Return Semantics**
   - **Returns:** `Path` — Existing imaging root directory.
   - **Raises:** `PurgeError` on failure.

4. **Step-by-Step Internal Logic**
   1. **Line 223:** `return await metadata.resolve_cli_root(args.imaging_root, PurgeError, "purge")`.

5. **Side Effects & I/O**
   - Queries N.I.N.A. API if `args.imaging_root is None`; stats the resolved path.

6. **Error Handling & Edge Cases**
   - Caught in `run` (`purge.py:777-781`), printing `✗ {exc}` to `stderr` and returning `1`.

7. **Complexity & Risk Notes**
   - $O(1)$.

---

### 3.9 `_idle_state` (`purge.py:226-244`)

1. **Purpose & Architectural Role**
   Safety gate that probes N.I.N.A. via `client._sequence_is_running()` before any mutation of the imaging root (`--apply` or `--purge`). Refuses mutation if a sequence is actively `RUNNING` (which could be appending rows to `ImageMetaData.csv`), while treating an unreachable/offline N.I.N.A. as idle.

2. **Signature, Decorators & Inputs**
   ```python
   async def _idle_state() -> tuple[bool, str]:
   ```
   - **Decorators:** None (`async def`).
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[bool, str]` — `(may_proceed, message)`:
     - `(True, "")` when N.I.N.A. is reachable and no sequence is running.
     - `(True, f"N.I.N.A. unreachable ({exc}) — proceeding as if idle")` when `_sequence_is_running()` raises any exception.
     - `(False, "a N.I.N.A. sequence is RUNNING — stop it before purging (it may be appending CSV rows)")` when a sequence is running.

4. **Step-by-Step Internal Logic**
   1. **Lines 235-238:** Awaits `running = await _sequence_is_running()` inside a `try` block; on any `Exception as exc`, returns `True, f"N.I.N.A. unreachable ({exc}) — proceeding as if idle"`.
   2. **Lines 239-243:** If `running:`, returns `False, "a N.I.N.A. sequence is RUNNING — stop it before purging (it may be appending CSV rows)"`.
   3. **Line 244:** Returns `True, ""`.

5. **Side Effects & I/O**
   - Performs an HTTP GET probe to N.I.N.A.'s sequence state endpoint via `_sequence_is_running()`.

6. **Error Handling & Edge Cases**
   - Only called when `root_wants_mutation = (args.apply and prunable) or args.purge` is `True` (`purge.py:980-981`), so pure dry-run reports and `--apply` runs with zero prunable directories never block on a running sequence.

7. **Complexity & Risk Notes**
   - Single HTTP request with timeout.

---

### 3.10 `_read_manifests` (`purge.py:250-296`)

1. **Purpose & Architectural Role**
   Scans all committed archive folders in `<dest>/*/manifest.json` (ignoring hidden dot-folders like `.<name>.partial` and `.<name>.replaced`), building the per-`plan_id` manifest map and the global `preserved` set of normalized source frame paths.

2. **Signature, Decorators & Inputs**
   ```python
   def _read_manifests(
       dest: Path,
   ) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
   ```
   - **Decorators:** None.
   - **Parameters:** `dest: Path` — Archive root directory (`--dest`).

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[dict[str, dict[str, Any]], set[str], list[str]]` — `(by_plan, preserved, warnings)` where:
     - `by_plan: dict[str, dict[str, Any]]` maps `plan_id` to its parsed `manifest.json` dict (if multiple archive folders share a `plan_id`, a manifest with `"complete": false`—the `--all` waiver—wins the merge).
     - `preserved: set[str]` is the union of `_norm(entry["source"])` across every valid manifest's `files[]` list (including `"type": "JUNK"` entries).
     - `warnings: list[str]` contains human-readable warnings for missing `dest`, unreadable manifests, or manifests lacking `plan_id`.

4. **Step-by-Step Internal Logic**
   1. **Lines 263-271:** Initializes `by_plan = {}`, `preserved = set()`, `warnings = []`. If `not dest.is_dir():`, returns `by_plan, preserved, [f"no archive at {dest} — nothing reads as preserved"]`.
   2. **Lines 272-277:** Iterates `for folder in sorted(dest.iterdir()):`. Skips if `not folder.is_dir() or folder.name.startswith(".")`. Checks `manifest_path = folder / _MANIFEST_NAME`; skips if `not manifest_path.is_file()`.
   3. **Lines 278-286:** Parses `data = json.loads(manifest_path.read_text(encoding="utf-8"))` (appending to `warnings` and continuing on `(OSError, ValueError)`). Extracts `plan_id = str(data.get("plan_id") or "")` (appending to `warnings` and continuing if empty).
   4. **Lines 287-291:** Merges into `by_plan[plan_id]`: sets `by_plan[plan_id] = data` if `existing is None` or `existing.get("complete") is not False and data.get("complete") is False` (ensuring an explicit `--all` waiver wins across duplicate plan IDs).
   5. **Lines 292-295:** Iterates `for entry in data.get("files") or []:`; if `source := entry.get("source")` is truthy, adds `_norm(source)` to `preserved`.
   6. **Line 296:** Returns `by_plan, preserved, warnings`.

5. **Side Effects & I/O**
   - **Disk Read:** Lists `dest` and reads `<dest>/*/manifest.json`.

6. **Error Handling & Edge Cases**
   - Ignoring `folder.name.startswith(".")` (`purge.py:273`) guarantees that an interrupted `.<name>.partial` staging directory or leftover `.<name>.replaced` backup directory can never prematurely mark frames as `preserved` or release a plan.

7. **Complexity & Risk Notes**
   - $O(M + F)$ where $M$ is the number of archive folders and $F$ is total archived file entries across all manifests.

---

### 3.11 `_classify` (`purge.py:302-409`)

1. **Purpose & Architectural Role**
   Groups all live recorded CSV frames (`rows`), `BAD_` reject twins (`rejects`), and existing visible top-level directories under `root` by top-level directory name, and computes the per-directory `prune` vs. `hold` verdict (`out`) alongside a list of frames/rejects located outside `root` (`outside`).

2. **Signature, Decorators & Inputs**
   ```python
   def _classify(
       root: Path,
       rows: list[dict[str, Any]],
       rejects: list[Path],
       claims: dict[str, set[str]],
       released: set[str],
       preserved: set[str],
       plan_names: dict[str, str],
       notes: dict[str, str],
   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `root: Path` — N.I.N.A. imaging root.
     - `rows: list[dict[str, Any]]` — Live CSV rows from `read_imaging_csv`.
     - `rejects: list[Path]` — Existing `BAD_<name>` reject files on disk derived from `dropped_rows`.
     - `claims: dict[str, set[str]]` — Mapping from normalized frame path `_norm(file_path)` to the set of claiming `plan_id`s.
     - `released: set[str]` — Set of `plan_id`s that passed all archive gates and completeness/waiver checks.
     - `preserved: set[str]` — Set of normalized source paths present in committed `manifest.json` files.
     - `plan_names: dict[str, str]` — Mapping from `plan_id` to `plan_file.name` for human-readable hold reasons.
     - `notes: dict[str, str]` — Mapping from unreleased `plan_id` to its specific hold note (e.g., `"not archived"`, `"archive is stale..."`, `"archived, incomplete now (...)"`).
   - **Inner Helper (`bucket_for`, `purge.py:324-328`):**
     ```python
     def bucket_for(path: Path) -> str | None:
     ```
     Returns `path.resolve().relative_to(root_resolved).parts[0]`, or `None` on `(ValueError, IndexError)` (when `path` is outside `root` or equals `root`).

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[list[dict[str, Any]], list[dict[str, Any]]]` — `(out, outside)` where:
     - `out`: Sorted list of per-directory verdict dicts, each containing:
       - `"dir": str` (top-level folder name under `root`)
       - `"verdict": "prune" | "hold"`
       - `"reason": str`
       - `"frames": int` (recorded live frame count)
       - `"rejects": int` (`BAD_` twin count)
       - `"bytes": int` (total bytes of live frames + `BAD_` twins)
       - `"plan_ids": list[str]` (sorted union of claiming `plan_id`s for frames in this directory)
     - `outside`: List of `{"path": str, "type": Any}` dicts for CSV rows or `BAD_` twins resolving outside `root`.

4. **Step-by-Step Internal Logic**
   1. **Lines 320-328:** Initializes `buckets = {}`, `outside = []`, `root_resolved = root.resolve()`, and defines inner `bucket_for(path)`.
   2. **Lines 330-344:** Buckets each row in `rows` and each `twin` in `rejects` by top-level directory `name = bucket_for(...)`; if `name is None`, appends to `outside`.
   3. **Lines 348-350:** Enumerates visible top-level directories on disk via `on_disk = {p.name for p in root.iterdir() if p.is_dir() and not _prunable_dir(p.name)}` so empty or wholly-unrecorded directories are included.
   4. **Lines 353-385:** Iterates `for name in sorted(on_disk | set(buckets)):`:
      - For each `row` in `entries["frames"]`:
        - Normalizes `path = _norm(row["file_path"])` and adds `Path(path).stat().st_size` to `total_bytes` (ignoring `OSError`).
        - Looks up `claimants = claims.get(path, set())` and unions `plan_ids |= claimants`.
        - If `path not in preserved:`: appends `"not preserved — run nina-archive first"` to `held_reasons` and continues.
        - Computes `unreleased = sorted(claimants - released)`. If non-empty, formats `named` with `plan_names` and `notes` and appends `f"claimed by {named}"` to `held_reasons`; else increments `prunable_frames += 1`.
      - Counts `reject_count = len(entries["rejects"])` and adds each reject twin's stat size to `total_bytes`.
   5. **Lines 386-397 (Directory Verdict Selection):**
      - If `held_reasons` is non-empty: `verdict = "hold"`, `reason = _unique_join(held_reasons)`.
      - Else if `prunable_frames or reject_count`: `verdict = "prune"`, `reason = "preserved; all claims released" if prunable_frames else "BAD_ rejects only"`.
      - Else (zero recorded frames and zero rejects, i.e., empty or unrecorded directory): `verdict = "hold"`, `reason = "no recorded frames (unrecorded files are never classified)"`.
   6. **Lines 398-409:** Appends the verdict dict to `out` and returns `out, outside`.

   ```mermaid
   flowchart TD
       A["Start _classify(root, rows, rejects, claims, released, preserved, ...)"] --> B["Bucket rows &amp; rejects by top-level dir under root<br/>Items outside root -> outside[]<br/>Enumerate on_disk visible dirs (not _prunable_dir)<br/>(lines 320-350)"]
       B --> C["For each dir name in sorted(on_disk | set(buckets))<br/>(line 353)"]
       C --> D["For each recorded frame row in entries['frames']<br/>(line 359)"]
       D --> E{"path in preserved?<br/>(line 367)"}
       E -->|No| F["held_reasons.append(<br/>'not preserved — run nina-archive first')"]
       E -->|Yes| G{"claimants - released<br/>non-empty? (line 370)"}
       G -->|Yes| H["held_reasons.append(<br/>f'claimed by {named}')"]
       G -->|No| I["prunable_frames += 1<br/>(includes preserved orphans with 0 claimants)"]
       F & H & I --> J["After all frames + rejects in dir:<br/>Evaluate Directory Verdict (lines 386-397)"]
       J --> K{"held_reasons<br/>non-empty?"}
       K -->|Yes| V_HOLD1["verdict = 'hold'<br/>reason = _unique_join(held_reasons)"]
       K -->|No| L{"prunable_frames &gt; 0<br/>or reject_count &gt; 0?"}
       L -->|Yes| V_PRUNE["verdict = 'prune'<br/>reason = 'preserved; all claims released'<br/>or 'BAD_ rejects only'"]
       L -->|No| V_HOLD2["verdict = 'hold'<br/>reason = 'no recorded frames<br/>(unrecorded files are never classified)'"]
       V_HOLD1 & V_PRUNE & V_HOLD2 --> OUT["Append entry to out[] &amp; return (out, outside)"]
   ```

5. **Side Effects & I/O**
   - **Filesystem Read/Stat:** Lists `root.iterdir()`, resolves frame paths, and stats recorded frames and `BAD_` twins.

6. **Error Handling & Edge Cases**
   - **Orphan Frames (`claimants == set()`):** If a frame is not claimed by any loaded plan (`claimants` is empty), `unreleased = sorted(set() - released)` is empty, so it is `prunable` if and only if `path in preserved` (e.g., its plan file was already removed after archiving).
   - **Wholly Unrecorded Directories:** A directory on disk with no recorded CSV frames and no `BAD_` twins falls into the final `else` branch (`verdict = "hold"`), preventing `nina-purge` from ever touching user directories that N.I.N.A.'s Session Metadata plugin never wrote to.

7. **Complexity & Risk Notes**
   - $O(R + B + D \log D)$ where $R$ is live rows, $B$ is `BAD_` twins, and $D$ is top-level directory count.

---

### 3.12 `_quarantine` (`purge.py:415-472`)

1. **Purpose & Architectural Role**
   Executes the `--apply` phase: moves each `prunable` top-level directory `root / name` into `root / ".quarantine" / name` (appending `~1`, `~2`, ... if a directory of that name already exists in `.quarantine/`), measures total bytes and unrecorded files, and appends a `"quarantine"` record to `<root>/purge-index.jsonl`.

2. **Signature, Decorators & Inputs**
   ```python
   def _quarantine(
       root: Path,
       entries: list[dict[str, Any]],
   ) -> list[dict[str, Any]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `root: Path` — N.I.N.A. imaging root.
     - `entries: list[dict[str, Any]]` — List of `prunable` directory dicts from `_classify`.

3. **Outputs & Return Semantics**
   - **Returns:** `list[dict[str, Any]]` — Subset of `entries` that were actually moved into `.quarantine/`.

4. **Step-by-Step Internal Logic**
   1. **Lines 425-426:** Sets `quarantine = root / _QUARANTINE_DIR` and initializes `moved: list[dict[str, Any]] = []`.
   2. **Lines 427-435:** Iterates `for entry in entries:`. Checks `src = root / entry["dir"]`; if `not src.is_dir():`, prints `⚠ {name}: vanished before the move — skipped` to `stderr` and continues.
   3. **Lines 436-441:** Ensures `quarantine.mkdir(parents=True, exist_ok=True)`. Computes collision-free target path: starts at `target = quarantine / name` and increments `suffix += 1` (`target = quarantine / f"{name}~{suffix}"`) while `target.exists()`.
   4. **Lines 442-446:** Moves `shutil.move(str(src), str(target))`; on `OSError as exc`, prints `✗ {name}: {exc}` to `stderr` and continues.
   5. **Lines 447-460:** Calls `_, total = _tree_size(target)` and `unrecorded = _unrecorded_count(target, entry["frames"] + entry["rejects"])`. Constructs `record` (`"action": "quarantine"`, `"dir": name`, `"quarantine": str(target.relative_to(root))`, `"claimants": entry["plan_ids"]`, etc.) and calls `_append_index(root, record)`.
   6. **Lines 461-471:** Prints `📦 {name} → {record['quarantine']}/ ...` and, if `unrecorded > 0`, prints `⚠ {name}: {unrecorded} unrecorded file(s) rode along — inspect before --purge`. Appends `entry` to `moved`.
   7. **Line 472:** Returns `moved`.

5. **Side Effects & I/O**
   - **Filesystem Move:** Creates `<root>/.quarantine/` and moves top-level frame directories into it.
   - **Audit Log Write:** Appends one `"action": "quarantine"` JSON line per moved directory to `<root>/purge-index.jsonl`.

6. **Error Handling & Edge Cases**
   - **Collision Suffixing (`~1`, `~2`):** If a directory with the same name is quarantined twice across separate sessions before `--purge` is run, the `~<N>` suffix preserves both directories, and `_quarantine_gates` matches `Path(str(record["quarantine"])).name` (`purge.py:566`) so `name~1` is properly tracked in `purge-index.jsonl`.

7. **Complexity & Risk Notes**
   - `shutil.move` within the same filesystem (`root` to `root/.quarantine`) is an $O(1)$ atomic `os.rename` syscall per directory, followed by $O(F)$ stat walk in `_tree_size` and `_unrecorded_count`.

---

### 3.13 `_purge_quarantine` (`purge.py:475-531`)

1. **Purpose & Architectural Role**
   Executes the frame-deletion step of `--purge`: iterates through `<root>/.quarantine/`, permanently unlinks only the subdirectories present in `purgeable` (plus any loose files), skips and reports any directory held back by `_quarantine_gates`, logs a `"purge"` summary record to `purge-index.jsonl`, and removes the `.quarantine` directory itself if empty.

2. **Signature, Decorators & Inputs**
   ```python
   def _purge_quarantine(
       root: Path, purgeable: set[str], skips: dict[str, str]
   ) -> tuple[int, int, int]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `root: Path` — N.I.N.A. imaging root.
     - `purgeable: set[str]` — Child names inside `.quarantine/` cleared for deletion by `_quarantine_gates`.
     - `skips: dict[str, str]` — Mapping from held child name to its gate refusal reason from `_quarantine_gates`.

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[int, int, int]` — `(removed, skipped, failed)` counts of top-level items inside `.quarantine/`.

4. **Step-by-Step Internal Logic**
   1. **Lines 485-488:** Sets `quarantine = root / _QUARANTINE_DIR`. If `not quarantine.is_dir():`, prints `nothing to purge — no quarantine at {quarantine}` and returns `0, 0, 0`.
   2. **Lines 489-497:** Initializes `removed = files = total = skipped = failed = 0`. Iterates `for child in sorted(quarantine.iterdir()):`. If `child.is_dir() and child.name not in purgeable:`, increments `skipped += 1`, prints `⏭ {child.name}: kept in quarantine — {skips.get(child.name, 'not released')}`, and continues.
   3. **Lines 498-511:** Inside `try:`:
      - If `child.is_dir():`, measures `count, size = _tree_size(child)` and deletes `shutil.rmtree(child)`.
      - Else (loose file): measures `count, size = 1, child.stat().st_size` and deletes `child.unlink()`.
      - On `OSError as exc:`, prints `✗ {child.name}: {exc}` to `stderr`, increments `failed += 1`, and continues.
      - Increments `removed += 1`, `files += count`, `total += size`.
   4. **Lines 512-527:** If `removed > 0:`, appends `{"at": ..., "action": "purge", "removed": removed, "files": files, "bytes": total}` to `purge-index.jsonl` and prints `🗑 purged {removed} quarantined item(s), {files} files, {_human_bytes(total)} freed`.
   5. **Lines 528-530:** Attempts `quarantine.rmdir()` to remove the container if now empty, ignoring `OSError` (when kept/failed items remain).
   6. **Line 531:** Returns `removed, skipped, failed`.

5. **Side Effects & I/O**
   - **Permanent Deletion:** Calls `shutil.rmtree` / `Path.unlink` on purgeable items under `<root>/.quarantine/` and `quarantine.rmdir()`.
   - **Audit Log Write:** Appends `"action": "purge"` record to `<root>/purge-index.jsonl`.

6. **Error Handling & Edge Cases**
   - Any directory not in `purgeable` is left completely untouched inside `.quarantine/`, and `quarantine.rmdir()` silently fails with `OSError` (directory not empty), preserving the container.

7. **Complexity & Risk Notes**
   - $O(F)$ filesystem unlinks across all files $F$ in `purgeable` directories.

---

### 3.14 `_quarantine_gates` (`purge.py:534-609`)

1. **Purpose & Architectural Role**
   Re-verifies plan-file gates against quarantined directories before `--purge` unlinks them (and during dry-run preview). Reconstructs each quarantined directory's claiming `plan_id`s from `<root>/purge-index.jsonl` `"action": "quarantine"` records and verifies that every claimant still has a valid, non-stale archive (or a surviving `manifest.json` if its plan file was already removed). Directories moved into `.quarantine/` manually by hand (no index record) are never auto-purged.

2. **Signature, Decorators & Inputs**
   ```python
   def _quarantine_gates(
       root: Path,
       plan_records: list[dict[str, Any]],
       by_plan: dict[str, dict[str, Any]],
       plan_names: dict[str, str],
   ) -> tuple[Path | None, dict[str, set[str]], set[str], dict[str, str]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `root: Path` — N.I.N.A. imaging root.
     - `plan_records: list[dict[str, Any]]` — Per-plan gate & selection records built in `run`.
     - `by_plan: dict[str, dict[str, Any]]` — Parsed manifests keyed by `plan_id` from `_read_manifests`.
     - `plan_names: dict[str, str]` — Mapping from `plan_id` to `plan_file.name`.

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[Path | None, dict[str, set[str]], set[str], dict[str, str]]` — `(quarantine, claimants, purgeable, skips)` where:
     - `quarantine: Path | None` is `root / _QUARANTINE_DIR` if it exists as a directory, else `None`.
     - `claimants: dict[str, set[str]]` maps quarantined child folder name (e.g., `"2026-10-02"` or `"2026-10-02~1"`) to its claiming `plan_id` set from `purge-index.jsonl`.
     - `purgeable: set[str]` is the set of child names inside `.quarantine/` safe to delete.
     - `skips: dict[str, str]` maps held child names to their refusal reason string.

4. **Step-by-Step Internal Logic**
   1. **Lines 554-556:** Checks `quarantine = root / _QUARANTINE_DIR`; if `not quarantine.is_dir():`, returns `None, {}, set(), {}`.
   2. **Lines 557-568:** Reads `index = root / _INDEX_NAME` line by line (ignoring invalid JSON lines). For every record with `record.get("action") == "quarantine" and record.get("quarantine"):`, sets `claimants[Path(str(record["quarantine"])).name] = set(record.get("claimants") or [])`.
   3. **Lines 569-574:** Builds lookup sets/dicts from `plan_records`:
      - `stale_ids = {r["plan_id"] for r in plan_records if r["stale"]}`
      - `gate1_ids = {r["plan_id"] for r in plan_records if r["archive_ok"]}`
      - `gate1_reason = {r["plan_id"]: r["gate_reason"] for r in plan_records if not r["archive_ok"]}`
      - `file_ids = {r["plan_id"] for r in plan_records}`
   4. **Lines 575-608:** Iterates `for child in sorted(quarantine.iterdir()):`:
      - If `not child.is_dir():`, adds `child.name` to `purgeable` (loose files are never claimed) and continues.
      - Looks up `ids = claimants.get(child.name)`. If `ids is None:`, sets `skips[child.name] = "no quarantine record — inspect manually"` and continues.
      - Loops `for plan_id in sorted(ids):`:
        - If `plan_id in stale_ids:`: sets `bad = f"{plan_names.get(plan_id, plan_id)} [{plan_id}: archive is stale]"` and breaks.
        - Else if `plan_id in file_ids:`: if `plan_id not in gate1_ids:`, sets `bad = f"{plan_names.get(plan_id, plan_id)} [{plan_id}: {gate1_reason.get(plan_id, 'no archive folder')}]"` and breaks.
        - Else (`plan_id` has no live plan file in `--project-dir`): if `plan_id not in by_plan:`, sets `bad = f"{plan_names.get(plan_id, plan_id)} [{plan_id}: no plan file and no manifest]"` and breaks.
      - If `bad` is non-empty, sets `skips[child.name] = bad`; else adds `child.name` to `purgeable`.
   5. **Line 609:** Returns `quarantine, claimants, purgeable, skips`.

   ```mermaid
   flowchart TD
       A["Start _quarantine_gates(root, plan_records, by_plan, plan_names)"] --> B{"(root / '.quarantine').is_dir()?<br/>(line 555)"}
       B -->|No| RET_NONE["Return (None, {}, set(), {})"]
       B -->|Yes| C["Read <root>/purge-index.jsonl<br/>Map Path(record['quarantine']).name -> set(record['claimants'])<br/>Build stale_ids, gate1_ids, gate1_reason, file_ids (lines 557-574)"]
       C --> D["For each child in sorted(quarantine.iterdir()) (line 577)"]
       D --> E{"child.is_dir()?<br/>(line 578)"}
       E -->|No (loose file)| F["purgeable.add(child.name)"]
       E -->|Yes| G{"ids = claimants.get(child.name)<br/>ids is None? (line 582)"}
       G -->|Yes| H["skips[child.name] =<br/>'no quarantine record — inspect manually'"]
       G -->|No| I["For each plan_id in sorted(ids) (line 586)"]
       I --> J{"plan_id in stale_ids?<br/>(Gate 2 failed)"}
       J -->|Yes| BAD_STALE["bad = '... [plan_id: archive is stale]'"]
       J -->|No| K{"plan_id in file_ids?"}
       K -->|Yes| L{"plan_id in gate1_ids?<br/>(Gate 1 passed)"}
       L -->|No| BAD_G1["bad = '... [plan_id: <gate1_reason>]'"]
       L -->|Yes| NEXT_ID["Check next plan_id (or pass)"]
       K -->|No (plan file already removed)| M{"plan_id in by_plan?<br/>(manifest exists)"}
       M -->|No| BAD_NOMAN["bad = '... [plan_id: no plan file and no manifest]'"]
       M -->|Yes| NEXT_ID
       BAD_STALE & BAD_G1 & BAD_NOMAN --> RECORD_SKIP["skips[child.name] = bad"]
       NEXT_ID -->|All ids passed| RECORD_OK["purgeable.add(child.name)"]
       F & H & RECORD_SKIP & RECORD_OK --> OUT["Return (quarantine, claimants, purgeable, skips)"]
   ```

5. **Side Effects & I/O**
   - **Disk Read:** Reads `<root>/purge-index.jsonl` and lists `<root>/.quarantine/`.

6. **Error Handling & Edge Cases**
   - **Orphaned Quarantine Entries (`ids == set()`):** If a quarantined directory contained only preserved orphan frames or `BAD_` rejects (`claimants == []` in `purge-index.jsonl`), `ids` is `set()` (not `None`), the inner `for plan_id in sorted(ids):` loop runs 0 times, `bad` remains `""`, and the directory is added to `purgeable`.
   - **Manually Quarantined Directories (`ids is None`):** If a user manually moved a folder into `.quarantine/`, `claimants.get(child.name)` is `None`, so `_quarantine_gates` refuses to delete it (`"no quarantine record — inspect manually"`).

7. **Complexity & Risk Notes**
   - $O(L + Q \cdot C)$ where $L$ is lines in `purge-index.jsonl`, $Q$ is items in `.quarantine/`, and $C$ is claimants per directory.

---

### 3.15 `_sweep_plan_files` (`purge.py:612-698`)

1. **Purpose & Architectural Role**
   Evaluates and (when `purge=True`) unlinks each project `plan.json` file as the **final** step per plan—only after its archive gates pass (`not stale and archive_ok`), none of its quarantined directories remain in `.quarantine/` (`plan_id not in held_claimants`), and none of its selected frames still exist on disk (`purge=True`) or in a held directory (`purge=False` preview).

2. **Signature, Decorators & Inputs**
   ```python
   def _sweep_plan_files(
       records: list[dict[str, Any]],
       *,
       root: Path,
       dirs: list[dict[str, Any]],
       held_claimants: set[str],
       purge: bool,
   ) -> tuple[int, int, int]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `records: list[dict[str, Any]]` — `plan_records` list built in `run`.
     - `root: Path` (keyword-only) — N.I.N.A. imaging root.
     - `dirs: list[dict[str, Any]]` (keyword-only) — Directory verdict list from `_classify`.
     - `held_claimants: set[str]` (keyword-only) — Set of `plan_id`s whose quarantined directories were kept in `.quarantine/`.
     - `purge: bool` (keyword-only) — `True` under `--purge` (actually unlinks files and writes `"action": "plan-file"` records to `purge-index.jsonl`); `False` in dry-run preview mode.
   - **Inner Helper (`index_plan_file`, `purge.py:635-652`):**
     ```python
     def index_plan_file(
         record: dict[str, Any],
         plan_file: Path,
         *,
         deleted: bool,
         reason: str | None = None,
     ) -> None:
     ```
     Appends `{"at": ..., "action": "plan-file", "plan_id": record["plan_id"], "plan_file": str(plan_file), "folder": record["folder"], "plan_file_deleted": deleted, "reason"?: reason}` to `<root>/purge-index.jsonl`.

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[int, int, int]` — `(removed, kept, failed)` counts of evaluated plan files.

4. **Step-by-Step Internal Logic**
   1. **Lines 631-633:** Builds `verdict_by_dir = {entry["dir"]: entry["verdict"] for entry in dirs}`, resolves `root_resolved = root.resolve()`, and initializes `removed = kept = failed = 0`.
   2. **Lines 635-652:** Defines inner helper `index_plan_file`.
   3. **Lines 654-679:** Iterates `for record in records:`:
      - Checks `plan_file = record["file"]`; if `not plan_file.is_file():`, continues (already gone).
      - Determines `reason = ""`:
        - If `record["stale"] or not record["archive_ok"]:`: sets `reason = record["gate_reason"]`.
        - Else if `record["plan_id"] in held_claimants:`: sets `reason = "frames still in quarantine"`.
        - Else loops `for path in record["selected"]:`:
          - If `not Path(path).is_file():`, continues (frame is gone).
          - If `purge:`: sets `reason = "frames still present"` and breaks (under `--purge`, any frame still on disk in the imaging root means it wasn't quarantined+purged yet).
          - Else (dry-run preview): resolves `top = frame.resolve().relative_to(root_resolved).parts[0]`; if outside `root` or `verdict_by_dir.get(top) != "prune"`, sets `reason = "frames still present"` and breaks.
   4. **Lines 680-684:** If `reason` is non-empty: increments `kept += 1`, prints `⏭ kept plan file {plan_file} ({reason})`, and (if `purge=True`) calls `index_plan_file(record, plan_file, deleted=False, reason=reason)`, then continues.
   5. **Lines 685-687:** If `not purge:` (dry-run preview and `reason == ""`): prints `🗄 would remove plan file {plan_file}` and continues.
   6. **Lines 688-697:** Calls `plan_file.unlink()`. On `OSError as exc:`, prints `✗ cannot remove plan file {plan_file}: {exc}` to `stderr`, increments `failed += 1`, and logs `index_plan_file(..., deleted=False, reason=str(exc))`. On success, increments `removed += 1`, prints `🗄 removed plan file {plan_file}`, and logs `index_plan_file(..., deleted=True)`.
   7. **Line 698:** Returns `removed, kept, failed`.

5. **Side Effects & I/O**
   - **Filesystem Unlink (when `purge=True`):** Unlinks `plan_file` and appends `"action": "plan-file"` records to `<root>/purge-index.jsonl`.

6. **Error Handling & Edge Cases**
   - Guarantees that a plan file is **never** unlinked while any of its selected frames remain in the imaging root or in `.quarantine/`, preventing a partially-purged plan from losing its claim definition.

7. **Complexity & Risk Notes**
   - $O(P \cdot S)$ where $P$ is plan count and $S$ is selected frames per plan.

---

### 3.16 `_build_parser` (`purge.py:704-758`)

1. **Purpose & Architectural Role**
   Constructs and returns the `argparse.ArgumentParser` for the `nina-purge` CLI.

2. **Signature, Decorators & Inputs**
   ```python
   def _build_parser() -> argparse.ArgumentParser:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - **Returns:** `argparse.ArgumentParser` configured with `prog="nina-purge"` and 5 optional flags (`--dest`, `--imaging-root`, `--project-dir`, `--apply`, `--purge`).

4. **Step-by-Step Internal Logic**
   1. **Lines 705-722:** Instantiates `argparse.ArgumentParser` with `prog="nina-purge"`, description, `RawDescriptionHelpFormatter`, and example epilog.
   2. **Lines 723-757:** Adds `"--dest"` (`default=".archive"`), `"--imaging-root"` (`default=None`), `"--project-dir"` (`default=None`), `"--apply"` (`action="store_true"`), and `"--purge"` (`action="store_true"`).
   3. **Line 758:** Returns `parser`.

5. **Side Effects & I/O**
   - None.

6. **Error Handling & Edge Cases**
   - Accepts no positional arguments; any unexpected positional argument triggers `argparse` exit code `2`.

7. **Complexity & Risk Notes**
   - $O(1)$.

---

### 3.17 `run` (`purge.py:761-1077`)

1. **Purpose & Architectural Role**
   Top-level async orchestrator for `nina-purge`. Resolves paths and containment, reads imaging CSVs and archive manifests, loads all project plans (aborting immediately if any plan-shaped JSON fails to load so claims are never guessed), evaluates the 3 plan-file gates and live completeness/waiver release status, classifies top-level directories, checks N.I.N.A. sequence idle state if mutating, executes `--apply` and/or `--purge` (or dry-run preview), and returns the exit code.

2. **Signature, Decorators & Inputs**
   ```python
   async def run(args: argparse.Namespace) -> int:
   ```
   - **Decorators:** None (`async def`).
   - **Parameters:** `args: argparse.Namespace` — Parsed CLI arguments from `_build_parser()`.

3. **Outputs & Return Semantics**
   - **Returns:** `int` — `0` on success (or idempotent `--purge`), `1` on any error, stale-archive refusal, or when nothing actionable was found in report/`--apply` mode.

4. **Step-by-Step Internal Logic**
   1. **Lines 768-792 (Path & Containment Setup):** Resolves `project_dir` and `dest`, awaits `root = await _resolve_root(args)` (returning `1` on `PurgeError`), and verifies `if _inside(dest, root): return 1`.
   2. **Lines 794-808 (CSV & Manifest Read):** Calls `rows, dropped_rows = read_imaging_csv(root=root, image_type=None, report_dropped=True)` (returning `1` on `FileNotFoundError`). Calls `by_plan, preserved, manifest_warnings = _read_manifests(dest)` and prints any `manifest_warnings` to `stderr`.
   3. **Lines 814-881 (Load Plans, Run Gates, Build Claims):**
      - Iterates over `[p for p in plan_utils.plan_files(project_dir) if not _inside(p, dest)]`.
      - Awaits `plan_utils.load_plan(str(plan_file))`. On failure, if `_looks_like_plan_file(plan_file)` is `True`, appends to `errors`; else increments `ignored += 1`.
      - Calls `_plan_file_gate(plan_file, plan, dest)` (catching `ArchiveError` into `errors`). If `stale` is `True`, appends `(plan_file, gate_reason)` to `refusals`.
      - Calls `selected = _select_frames(plan, rows, now)`, appends to `plan_records`, and adds `plan_id` to `claims[_norm(file_path)]` for every selected frame.
      - **Lines 871-881 (Unknown-Claims Abort):** If `errors` is non-empty, prints `✗ {plan_file.name}: {reason} — purge refuses to guess this plan's claims` to `stderr` and returns `1` immediately before touching anything.
   4. **Lines 883-891 (Stale Refusal Reporting):** If `refusals` is non-empty, prints `✗ {plan_file.name}: {reason} — refusing to release or remove this plan's frames or file` to `stderr` and continues sweeping other plans.
   5. **Lines 900-926 (Compute `released` Set):** For each unique `plan_id` in `plan_records`:
      - If `any(r["stale"] for r in records)`: records `notes[plan_id] = "archive is stale..."` and continues.
      - If `by_plan.get(plan_id) is None`: records `notes[plan_id] = "not archived"` and continues.
      - If no record for `plan_id` has `archive_ok=True`: records `notes[plan_id] = records[0]["gate_reason"]` and continues.
      - Checks `waived = manifest.get("complete") is False` and `complete_now, counts = _progress_facts(passing[0]["plan"], rows, now)`. If `waived or complete_now`, adds `plan_id` to `released`; else records `notes[plan_id] = f"archived, incomplete now ({deficit})"`.
   6. **Lines 930-975 (`BAD_` Twins & Verdict Table):** Resolves `BAD_` twins from `dropped_rows` into `rejects` (or increments `gone`), calls `dirs, outside = _classify(...)`, and prints the `🔍` directory verdict table (`✂️ ` `PRUNE` vs. `🔒` `HOLD`).
   7. **Lines 979-988 (Idle Sequence Gate):** If `root_wants_mutation = (args.apply and prunable) or args.purge` is `True`, awaits `allowed, message = await _idle_state()`. If `not allowed`, prints `✗ {message}` to `stderr` and sets `errors_occurred = True`.
   8. **Lines 989-1014 (`--apply` Execution):** If `args.apply and prunable and not errors_occurred`, calls `moved = _quarantine(root, prunable)`, adds `actionable += len(moved)`, and prints the `✅` quarantine summary and `📌` `NEVER_SHOT` plan-file hint.
   9. **Lines 1016-1044 (`--purge` Execution):** If `args.purge and not errors_occurred`:
      - Calls `_quarantine_gates(root, plan_records, by_plan, plan_names)`.
      - Calls `purged, kept_dirs, failed = _purge_quarantine(root, purgeable, skip_reasons)` and adds `actionable += purged` (setting `errors_occurred = True` if `kept_dirs or failed`).
      - Collects `held_ids` from any remaining children in `quarantine_dir`, calls `swept, _kept, sweep_failed = _sweep_plan_files(..., purge=True)`, and adds `actionable += swept` (setting `errors_occurred = True` if `sweep_failed`).
   10. **Lines 1049-1066 (Dry-Run Preview):** If `not (args.apply or args.purge)`, sets `actionable += len(prunable)` and runs `_quarantine_gates` + `_sweep_plan_files(..., purge=False)` to preview quarantine skips and plan-file removals.
   11. **Lines 1068-1077 (Exit Code):** If `errors_occurred or refusals:`, returns `1`. If `actionable == 0:`, prints `🔍 nothing to do` and returns `0 if args.purge else 1`. Otherwise returns `0`.

   ```mermaid
   flowchart TD
       START["run(args)"] --> INIT["Resolve project_dir, dest, root<br/>Verify not _inside(dest, root)<br/>read_imaging_csv(report_dropped=True)<br/>_read_manifests(dest) -> by_plan, preserved (lines 768-808)"]
       INIT --> LOAD["Discover &amp; load all project plans outside dest<br/>Run _plan_file_gate() &amp; _select_frames() -> claims, plan_records<br/>(lines 814-870)"]
       LOAD --> ERR_CHK{"Any broken plan-shaped JSON<br/>or bad folder name? (line 871)"}
       ERR_CHK -->|Yes| ABORT["Print ✗ purge refuses to guess claims &amp; return 1 (lines 872-881)"]
       ERR_CHK -->|No| REL["Compute released plan_ids:<br/>not stale AND manifest exists AND archive_ok<br/>AND (manifest complete==False OR _progress_facts complete_now)<br/>(lines 900-926)"]
       REL --> CLASS["_classify() top-level dirs &amp; print ✂️ PRUNE / 🔒 HOLD table<br/>(lines 930-975)"]
       CLASS --> IDLE{"(args.apply and prunable)<br/>or args.purge? (line 980)"}
       IDLE -->|Yes| IDLE_CHK["await _idle_state()<br/>If sequence RUNNING -> errors_occurred = True (lines 982-988)"]
       IDLE -->|No| MODE{"Check Mode Flags"}
       IDLE_CHK --> MODE
       MODE -->|--apply| APPLY["_quarantine(root, prunable) -> .quarantine/<br/>Append quarantine records to purge-index.jsonl<br/>actionable += len(moved) (lines 989-1014)"]
       MODE -->|--purge| PURGE["_quarantine_gates() -> _purge_quarantine()<br/>Collect remaining held_ids in .quarantine/<br/>_sweep_plan_files(purge=True) -> unlink plan.json last<br/>actionable += purged + swept (lines 1016-1044)"]
       MODE -->|Dry-Run (neither)| DRY["actionable += len(prunable)<br/>Preview _quarantine_gates() &amp; _sweep_plan_files(purge=False)<br/>(lines 1049-1066)"]
       APPLY & PURGE & DRY --> EXIT["If errors_occurred or refusals: return 1<br/>If actionable == 0: return 0 if args.purge else 1<br/>Else: return 0 (lines 1068-1077)"]
   ```

5. **Side Effects & I/O**
   - Reads N.I.N.A. profile/sequence status (optional), walks imaging root CSVs, reads archive manifests and project plans, and (under `--apply` / `--purge`) mutates `<root>/.quarantine/`, `<root>/purge-index.jsonl`, and project `plan.json` files.

6. **Error Handling & Edge Cases**
   - **Combined `--apply --purge` Invocation:** Because `if args.apply ...` (`989`) and `if args.purge ...` (`1016`) are sequential `if` blocks, passing both `--apply` and `--purge` in a single invocation quarantines prunable directories first and then immediately purges `.quarantine/` and sweeps plan files in the same run.
   - **Idempotent `--purge`:** Running `nina-purge --purge` when everything has already been purged (`actionable == 0` and no errors/refusals) prints `🔍 nothing to do` and returns `0` (`purge.py:1076`).

7. **Complexity & Risk Notes**
   - Single walk over imaging CSVs plus $O(L \cdot P \cdot R)$ in-memory selection across $L$ plans, $P$ pointings, and $R$ CSV rows.

---

### 3.18 `main` (`purge.py:1080-1087`)

1. **Purpose & Architectural Role**
   Synchronous CLI entrypoint registered as `nina-purge` in `pyproject.toml:62` and invoked by `if __name__ == "__main__":` (`purge.py:1090-1091`).

2. **Signature, Decorators & Inputs**
   ```python
   def main(argv: list[str] | None = None) -> int:
   ```
   - **Decorators:** None.
   - **Parameters:** `argv: list[str] | None = None` — Optional CLI argument list; defaults to `sys.argv[1:]`.

3. **Outputs & Return Semantics**
   - **Returns:** `int` — Exit code from `run(args)` (`0` or `1`), or `SystemExit(2)` from `argparse`.

4. **Step-by-Step Internal Logic**
   1. **Line 1086:** Parses CLI arguments via `args = _build_parser().parse_args(argv)`.
   2. **Line 1087:** Runs `return asyncio.run(run(args))`.

5. **Side Effects & I/O**
   - Starts an `asyncio` event loop and executes `run(args)`.

6. **Error Handling & Edge Cases**
   - `argparse` raises `SystemExit(2)` on invalid flags.

7. **Complexity & Risk Notes**
   - $O(1)$ synchronous entrypoint wrapper.
