# Module Specification: `nina_planner/pin_id.py` (`nina-pin-id`)

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ CLI Package](./README.md) · [⬅️ `archive.py`](./archive.md) · [⬅️ `purge.py`](./purge.md) · [🔗 `plan_utils.py`](../core/plan_utils.md) · [🔗 `models/plan.py`](../models/plan.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/pin_id.py`
- **Line Count:** 166 lines (`1-166`)
- **Console Script Entrypoint:** `nina-pin-id = "nina_planner.pin_id:main"` (`pyproject.toml:61`)
- **Architectural Layer:** CLI Identity-Preservation Utility (`nina_planner` CLI layer)
- **Purpose & Module Docstring (`pin_id.py:1-25`):**
  Stamps a plan file's content-derived ID (`plan-<hex12>`, computed by `ObservationPlan._base_plan_id()` when `plan_id` is empty) into its explicit `plan_id` JSON field before the user edits the plan. Because a blank `plan_id` hashes the plan's JSON content (excluding `plan_id`), editing the plan changes the derived hash while frames already captured on disk retain the old `_{plan_id}_` filename token. Without pinning first, edited plans lose frame attribution in `get_plan_progress`, generate a different archive folder name in `nina-archive`, and drop their frame claims in `nina-purge`. Pinning freezes the derived ID into `plan_id` atomically via a temporary sibling file and `os.replace`, while treating already-pinned plans as idempotent no-ops.

### 1.1 Inbound Callers
- **CLI Entrypoint:** Invoked as `nina-pin-id` via `pyproject.toml:61` or `python -m nina_planner.pin_id` (`pin_id.py:165-166`).
- **Agent Skills:** Invoked via `.agents/skills/run-nina-tools/` workflow when pinning a plan before editing.
- **Automated Test Suite:** `.opencode/local/mcps/nina_planner/tests/test_pin_id.py` (`PinBase`, `PinCliTest`, `PinAcceptanceTest`, lines `1-368`).

### 1.2 Outbound Imports
- **Python Standard Library (`pin_id.py:27-32`):**
  - `from __future__ import annotations` (`27`)
  - `argparse` (`29`), `asyncio` (`30`), `sys` (`31`), `from pathlib import Path` (`32`)
- **Third-Party Libraries (`pin_id.py:34`):**
  - `anyio` (`34`) — used for `anyio.to_thread.run_sync` (`pin_id.py:81`)
- **Internal Package Imports (`pin_id.py:36`):**
  - `from . import config, plan_utils` (`36`) — uses `config.PROJECT_DIR`, `plan_utils.load_plan`, and `plan_utils.write_text_atomic`.

### 1.3 Module Constants & Globals
*(None — `pin_id.py` defines no module-level constants.)*

### 1.4 CLI Flags & Arguments (`_build_parser`, `pin_id.py:92-123`)

| Argument / Flag | `argparse` Configuration | Default | Description |
|---|---|---|---|
| `plans` | `nargs="+"`, `metavar="PLAN.json"` | *(required, 1 or more)* | Plan JSON file paths to pin (resolved relative to `config.PROJECT_DIR` when relative). |
| `--dry-run` | `action="store_true"` | `False` | Report what would be pinned (`🔍 would pin <path>: <id>` or `⏭  already pinned: <id>`) without writing to disk. |

### 1.5 Exit Code Contract (`pin_id.py:22-24`, `151`, `157-160`)

| Exit Code | Condition | Source |
|---:|---|---|
| `0` | Every named plan file was newly pinned, was already pinned (`already pinned: <id>`), or would be pinned under `--dry-run` (`errors == 0`). | `run` (`pin_id.py:151`) |
| `1` | At least one named plan file was missing on disk, failed `plan_utils.load_plan` validation/parsing, or failed atomic disk write (`errors > 0`). | `run` (`pin_id.py:151`) |
| `2` | Invalid CLI flags or missing required positional `PLAN.json` arguments (`argparse` default usage exit). | `_build_parser().parse_args(argv)` (`pin_id.py:161`) |

---

## 2. Classes, Data Models & Exceptions

### `PinError(Exception)` (`pin_id.py:39-40`)
- **Inheritance:** `builtins.Exception`
- **Docstring:** `"A plan could not be pinned; the message is user-facing."`
- **Role:** Raised by `pin_plan_file` when `plan_utils.load_plan` fails (`pin_id.py:71`) or when `plan_utils.write_text_atomic` raises `OSError` (`pin_id.py:85`). Caught per-file in `run` (`pin_id.py:141-144`) so one broken plan prints `✗ <message>` to `sys.stderr` and increments `errors` without aborting remaining plan files.

---

## 3. Function & Method Reference

### 3.1 `_resolve` (`pin_id.py:46-51`)

1. **Purpose & Architectural Role**
   Private path-resolution helper that converts a raw CLI path string into an absolute `Path`, applying the exact same resolution rule as `plan_utils.load_plan` (expanding `~` and anchoring relative paths to `config.PROJECT_DIR`).

2. **Signature, Decorators & Inputs**
   ```python
   def _resolve(entry: str) -> Path:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `entry: str` — Raw plan file path string (e.g., from `args.plans` or `str(plan_file)`).

3. **Outputs & Return Semantics**
   - **Returns:** `Path` — An expanded, absolute `Path` object (note: calls `.expanduser()` and prepends `config.PROJECT_DIR` if `not path.is_absolute()`, without calling `.resolve()`).

4. **Step-by-Step Internal Logic**
   1. **Line 48:** Constructs `path = Path(entry).expanduser()`.
   2. **Lines 49-50:** Checks `if not path.is_absolute():`; if relative, rebinds `path = config.PROJECT_DIR / path`.
   3. **Line 51:** Returns `path`.

5. **Side Effects & I/O**
   - Reads environment home directory via `Path.expanduser()` if `~` is present; performs no disk I/O.

6. **Error Handling & Edge Cases**
   - Never raises on missing files; existence checks are performed by the caller (`run` at `pin_id.py:135` or `load_plan` at `pin_id.py:69`).

7. **Complexity & Risk Notes**
   - $O(1)$ path manipulation.

---

### 3.2 `pin_plan_file` (`pin_id.py:57-86`)

1. **Purpose & Architectural Role**
   Public async core function that loads a single observation plan file, checks whether it already carries an explicit `plan_id`, computes its current content-derived `_base_plan_id()` if blank, and atomically rewrites the JSON file on a worker thread (unless `dry_run=True`).

2. **Signature, Decorators & Inputs**
   ```python
   async def pin_plan_file(plan_file: str | Path, *, dry_run: bool = False) -> str:
   ```
   - **Decorators:** None (`async def`).
   - **Parameters:**
     - `plan_file: str | Path` — Path to the plan JSON file (relative to `config.PROJECT_DIR` or absolute).
     - `dry_run: bool = False` (keyword-only) — When `True`, skips copying the model and writing to disk, returning the `"would pin ..."` status string.

3. **Outputs & Return Semantics**
   - **Returns:** `str` — User-facing status message in one of three exact formats:
     - `"already pinned: <plan.plan_id>"` (when `plan.plan_id` is non-empty, `pin_id.py:73`)
     - `"would pin <path>: <plan_id>"` (when `plan.plan_id` is empty and `dry_run=True`, `pin_id.py:76`)
     - `"pinned <path>: <plan_id>"` (when `plan.plan_id` is empty and atomically written, `pin_id.py:86`)
   - **Raises:** `PinError` when loading fails (`pin_id.py:71`) or atomic writing raises `OSError` (`pin_id.py:85`).

4. **Step-by-Step Internal Logic**
   1. **Line 67:** Normalizes `plan_file` via `path = _resolve(str(plan_file))`.
   2. **Lines 68-71:** Awaits `plan = await plan_utils.load_plan(str(path))` inside a `try` block. On any `Exception as exc`, extracts the first line of the error (`str(exc).splitlines()[0]`) and raises `PinError(f"{path.name}: {str(exc).splitlines()[0]}") from exc`.
   3. **Lines 72-73:** Checks `if plan.plan_id:`. If truthy (already has an explicit `plan_id`), immediately returns `f"already pinned: {plan.plan_id}"` without modifying the file.
   4. **Line 74:** Computes the deterministic content hash ID via `plan_id = plan._base_plan_id()`.
   5. **Lines 75-76:** Checks `if dry_run:`. If `True`, returns `f"would pin {path}: {plan_id}"`.
   6. **Line 77:** Clones the Pydantic model with the new `plan_id` via `pinned = plan.model_copy(update={"plan_id": plan_id})`.
   7. **Lines 78-85:** Offloads the blocking temp-file + `os.replace` write to a worker thread via `await anyio.to_thread.run_sync(plan_utils.write_text_atomic, path, pinned.model_dump_json(indent=2))`. If an `OSError as exc` occurs, raises `PinError(f"{path.name}: could not write ({exc})") from exc`.
   8. **Line 86:** Returns `f"pinned {path}: {plan_id}"`.

   ```mermaid
   flowchart TD
       A["pin_plan_file(plan_file, dry_run)"] --> B["path = _resolve(str(plan_file))<br/>(line 67)"]
       B --> C["await plan_utils.load_plan(str(path))<br/>(lines 68-69)"]
       C -->|Exception as exc| D["raise PinError(f'{path.name}: {first_line}')<br/>(lines 70-71)"]
       C -->|Loaded ObservationPlan| E{"plan.plan_id truthy?<br/>(line 72)"}
       E -->|Yes| F["return f'already pinned: {plan.plan_id}'<br/>(line 73)"]
       E -->|No| G["plan_id = plan._base_plan_id()<br/>(line 74)"]
       G --> H{"dry_run?<br/>(line 75)"}
       H -->|Yes| I["return f'would pin {path}: {plan_id}'<br/>(line 76)"]
       H -->|No| J["pinned = plan.model_copy(update={'plan_id': plan_id})<br/>(line 77)"]
       J --> K["await anyio.to_thread.run_sync(<br/>  plan_utils.write_text_atomic,<br/>  path, pinned.model_dump_json(indent=2)<br/>) (lines 81-83)"]
       K -->|OSError as exc| L["raise PinError(f'{path.name}: could not write ({exc})')<br/>(lines 84-85)"]
       K -->|Success| M["return f'pinned {path}: {plan_id}'<br/>(line 86)"]
   ```

5. **Side Effects & I/O**
   - **Disk Read:** Reads and validates `path` via `plan_utils.load_plan`.
   - **Disk Write (when `not plan.plan_id and not dry_run`):** Atomically creates a temporary sibling file in `path.parent` and replaces `path` via `plan_utils.write_text_atomic` on an `anyio` worker thread, formatting JSON with 2-space indentation (`indent=2`).

6. **Error Handling & Edge Cases**
   - **Invalid JSON / Schema Violations:** `load_plan` raises `ValueError` or `pydantic.ValidationError`. `pin_plan_file` catches `Exception`, takes only `.splitlines()[0]` to keep CLI output single-line and readable, and wraps it in `PinError`, leaving the invalid file untouched on disk.
   - **Already Pinned Idempotency:** If `plan.plan_id` is already set (whether by a previous `nina-pin-id` run or hand-authored), `pin_plan_file` never overwrites it and performs no write I/O (`pin_id.py:72-73`).
   - **Atomic Crash Safety:** Because `write_text_atomic` writes to a temp file in the same directory before `os.replace`, a process kill or disk-full error during serialization never truncates the existing plan file.

7. **Complexity & Risk Notes**
   - Time and space complexity are $O(S)$ where $S$ is the byte size of the plan JSON file (SHA-256 content hashing in `_base_plan_id()` plus Pydantic serialization).

---

### 3.3 `_build_parser` (`pin_id.py:92-123`)

1. **Purpose & Architectural Role**
   Constructs and returns the `argparse.ArgumentParser` for the `nina-pin-id` CLI.

2. **Signature, Decorators & Inputs**
   ```python
   def _build_parser() -> argparse.ArgumentParser:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - **Returns:** `argparse.ArgumentParser` configured with `prog="nina-pin-id"`, description, raw-description epilog with examples and pre-edit warning, positional `plans` (`nargs="+"`), and `--dry-run`.

4. **Step-by-Step Internal Logic**
   1. **Lines 93-111:** Instantiates `argparse.ArgumentParser` with `prog="nina-pin-id"`, a multi-sentence description explaining why to pin before editing, `formatter_class=argparse.RawDescriptionHelpFormatter`, and an `epilog` containing usage examples and a warning that once a blank-ID plan is edited its old hash survives only in archive folder names or frame paths (`_<plan_id>_`).
   2. **Lines 112-117:** Registers positional argument `"plans"` with `nargs="+"`, `metavar="PLAN.json"`, `help="plan files to pin"`.
   3. **Lines 118-122:** Registers optional flag `"--dry-run"` with `action="store_true"`, `help="report what would be pinned; write nothing"`.
   4. **Line 123:** Returns `parser`.

5. **Side Effects & I/O**
   - None (pure in-memory parser construction).

6. **Error Handling & Edge Cases**
   - Because `nargs="+"` is used on `plans`, invoking `nina-pin-id` with zero positional arguments causes `parser.parse_args()` to print usage to `stderr` and raise `SystemExit(2)`.

7. **Complexity & Risk Notes**
   - $O(1)$.

---

### 3.4 `run` (`pin_id.py:126-151`)

1. **Purpose & Architectural Role**
   Async batch runner for `nina-pin-id`: iterates over all positional plan paths in `args.plans`, calls `pin_plan_file` for each existing file, prints emoji-prefixed status lines (`⏭ `, `🔍`, `📌`, or `✗`), and returns the process exit code.

2. **Signature, Decorators & Inputs**
   ```python
   async def run(args: argparse.Namespace) -> int:
   ```
   - **Decorators:** None (`async def`).
   - **Parameters:**
     - `args: argparse.Namespace` — Parsed CLI arguments containing `args.plans: list[str]` and `args.dry_run: bool`.

3. **Outputs & Return Semantics**
   - **Returns:** `int` — `0` if `errors == 0`, else `1` (`1 if errors else 0` at `pin_id.py:151`).

4. **Step-by-Step Internal Logic**
   1. **Line 132:** Initializes `errors = 0`.
   2. **Line 133:** Loops `for entry in args.plans:`.
   3. **Lines 134-138:** Resolves `path = _resolve(entry)`. If `not path.is_file()`, prints `f"✗ plan file not found: {path}"` to `sys.stderr`, increments `errors += 1`, and `continue`s to the next plan.
   4. **Lines 139-144:** Awaits `message = await pin_plan_file(path, dry_run=args.dry_run)`. If `PinError as exc` is raised, prints `f"✗ {exc}"` to `sys.stderr`, increments `errors += 1`, and `continue`s.
   5. **Lines 145-150:** Formats stdout output based on `message` and `args.dry_run`:
      - If `message.startswith("already pinned")`: prints `f"⏭  {message}"` (note: even under `--dry-run`, an already-pinned file takes this branch first and prints `⏭  already pinned: <id>`).
      - Else if `args.dry_run`: prints `f"🔍 {message}"`.
      - Else: prints `f"📌 {message}"`.
   6. **Line 151:** Returns `1 if errors else 0`.

5. **Side Effects & I/O**
   - **Console I/O:** Writes status lines to `sys.stdout` (`⏭ `, `🔍`, `📌`) and error lines to `sys.stderr` (`✗`).
   - **Filesystem I/O:** Checks `path.is_file()` and delegates plan loading/writing to `pin_plan_file`.

6. **Error Handling & Edge Cases**
   - **Non-Aborting Sweep:** Missing files and invalid/unwritable plans increment `errors` and continue the loop so all valid plans named on the command line are still pinned in a single invocation.

7. **Complexity & Risk Notes**
   - $O(N)$ sequential async execution over $N = \text{len}(\text{args.plans})$.

---

### 3.5 `main` (`pin_id.py:154-162`)

1. **Purpose & Architectural Role**
   Synchronous console-script entrypoint registered as `nina-pin-id` in `pyproject.toml:61` and invoked by the `if __name__ == "__main__":` guard (`pin_id.py:165-166`).

2. **Signature, Decorators & Inputs**
   ```python
   def main(argv: list[str] | None = None) -> int:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `argv: list[str] | None = None` — Optional CLI argument list for programmatic/test invocation; defaults to `sys.argv[1:]` inside `parse_args(argv)`.

3. **Outputs & Return Semantics**
   - **Returns:** `int` — Exit code returned by `asyncio.run(run(args))` (`0` or `1`), or raises `SystemExit(2)` inside `parse_args` on invalid CLI arguments.

4. **Step-by-Step Internal Logic**
   1. **Line 161:** Parses CLI arguments via `args = _build_parser().parse_args(argv)`.
   2. **Line 162:** Runs the async `run(args)` coroutine in a fresh event loop via `return asyncio.run(run(args))`.

5. **Side Effects & I/O**
   - Starts and closes an `asyncio` event loop; triggers all console and disk I/O in `run`.

6. **Error Handling & Edge Cases**
   - `argparse` raises `SystemExit(0)` on `--help` and `SystemExit(2)` on bad arguments.

7. **Complexity & Risk Notes**
   - Standard synchronous wrapper around `asyncio.run`.
