# Module Specification: `nina_planner/archive.py` (`nina-archive`)

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ CLI Package](./README.md) · [➡️ `purge.py`](./purge.md) · [➡️ `pin_id.py`](./pin_id.md) · [🔗 `progress.py`](../core/progress.md) · [🔗 `imaging.py`](../core/imaging.md) · [🔗 `metadata.py`](../core/metadata.md) · [🔗 `plan_utils.py`](../core/plan_utils.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/archive.py`
- **Line Count:** 1,204 lines (`1-1204`)
- **Console Script Entrypoint:** `nina-archive = "nina_planner.archive:main"` (`pyproject.toml:60`)
- **Architectural Layer:** CLI Archival Engine (`nina_planner` CLI layer; also exports shared selection/completeness helpers consumed by `nina_planner/purge.py`)
- **Purpose & Module Docstring (`archive.py:1-75`):**
  Copies completed observation plans—attributed light frames, pooled calibration frames (`FLAT`, `DARK`, `BIAS`), diverted own-token `junk/` frames (`degenerate` and `uncredited`), per-type `ImageMetaData.csv` sidecars (with `WeatherData.csv` joined into light and junk rows), a verbatim `plan.json` copy, and a `manifest.json` (v2) receipt—into a self-contained directory `<dest>/<plan-file-stem>__<plan-id>/` outside the N.I.N.A. imaging root. Originals in the imaging root are always **copied, never moved**. Each archive folder is assembled in a hidden `.<folder>.partial` staging sibling and atomically swapped into place (using `.<folder>.replaced` when replacing an existing folder under `--force`), and an append-only receipt is written to `<dest>/archive-index.jsonl`.

### 1.1 Inbound Callers
- **CLI Entrypoint:** Invoked as `nina-archive` (`pyproject.toml:60`) or `python -m nina_planner.archive` (`archive.py:1203-1204`).
- **Internal Python Callers:**
  - `nina_planner/purge.py:96-105` imports `_MANIFEST_NAME`, `ArchiveError`, `_folder_name`, `_human_bytes`, `_inside`, `_looks_like_plan_file`, `_progress_facts`, and `_select_frames`.
- **Agent Skills:** Invoked by `.agents/skills/archive-lifecycle/` and `.agents/skills/run-nina-tools/`.
- **Automated Test Suite:** `.opencode/local/mcps/nina_planner/tests/test_archive.py` (1,932 lines, 11 test classes).

### 1.2 Outbound Imports
- **Python Standard Library (`archive.py:77-88`):**
  - `from __future__ import annotations` (`77`)
  - `argparse` (`79`), `asyncio` (`80`), `csv` (`81`), `hashlib` (`82`), `json` (`83`), `shutil` (`84`), `sys` (`85`), `from datetime import UTC, datetime` (`86`), `from pathlib import Path` (`87`), `from typing import Any` (`88`)
- **Internal Package Imports (`archive.py:90-99`):**
  - `from . import config, metadata, plan_utils` (`90`)
  - `from .imaging import read_imaging_csv, read_weather_rows` (`91`)
  - `from .models.plan import ObservationPlan` (`92`)
  - `from .progress import filter_metadata_rows, is_junk_row, path_has_id, plan_progress, progress_verdict` (`93-99`)

### 1.3 Module Constants & Globals (`archive.py:101-115`)

| Constant | Line(s) | Type | Value | Purpose |
|---|---|---|---|---|
| `_ARCHIVE_TYPES` | `101` | `tuple[str, ...]` | `("LIGHT", "FLAT", "DARK", "BIAS")` | Canonical uppercase N.I.N.A. `ImageType` names iterated during selection and copy loops. |
| `_FOLDER_NAMES` | `106-112` | `dict[str, str]` | `{"LIGHT": "lights", "FLAT": "flats", "DARK": "darks", "BIAS": "biases", "JUNK": "junk"}` | Maps uppercase manifest `ImageType` to lowercase plural on-disk directory names required by Siril's case-sensitive preprocessing scripts. |
| `_INDEX_NAME` | `113` | `str` | `"archive-index.jsonl"` | Filename of the append-only JSON Lines log inside `--dest`. |
| `_MANIFEST_NAME` | `114` | `str` | `"manifest.json"` | Filename of the per-folder v2 archive manifest (also imported by `purge.py`). |
| `_PLAN_KEYS` | `115` | `tuple[str, ...]` | `("pointings", "light_frames", "plan_id")` | Top-level JSON keys checked by `_looks_like_plan_file` to distinguish broken observation plans from unrelated JSON files (e.g., OpenCode configs). |

### 1.4 CLI Flags & Arguments (`_build_parser`, `archive.py:842-917`)

| Argument / Flag | `argparse` Configuration | Default | Description |
|---|---|---|---|
| `plans` | `nargs="*"`, `metavar="PLAN.json"` | `[]` (discover all under `--project-dir`) | Explicit plan files to archive; if omitted, discovers all plans under `--project-dir` via `plan_utils.plan_files` (excluding `--dest`). |
| `--dest` | `default=".archive"` | `".archive"` (`./.archive` relative to `cwd`) | Archive destination directory (must resolve outside `--imaging-root`). |
| `--imaging-root` | `default=None` | `None` (queries N.I.N.A. site profile) | N.I.N.A. image save path override so archiving works while N.I.N.A. is offline. |
| `--project-dir` | `default=None` | `None` (`config.PROJECT_DIR`) | Project directory holding plan JSON files. |
| `--all` | `action="store_true"` | `False` | Also archive incomplete plans (sets `"complete": false` waiver in `manifest.json`). |
| `--force` | `action="store_true"` | `False` | Rebuild archive folders that already exist in `--dest` via atomic `.partial` / `.replaced` swap. |
| `--dry-run` | `action="store_true"` | `False` | Report what would be archived without creating directories or writing files. |
| `--checksum` | `action="store_true"` | `False` | Compute and verify SHA-256 digests (`_sha256`) of every copied frame in addition to byte-size verification. |
| `--no-image-metadata` | `action="store_true"` | `False` | Skip writing per-type `ImageMetaData.csv` sidecars inside `lights/`, `flats/`, `darks/`, `biases/`, and `junk/`. |
| `--no-weather` | `dest="weather"`, `action="store_false"`, `default=True` | `args.weather = True` | Skip merging `WeatherData.csv` columns into `LIGHT` rows before selection/archival. |

### 1.5 Exit Code Contract (`archive.py:949-994`, `1183-1188`, `1194-1200`)

| Exit Code | Condition | Source |
|---:|---|---|
| `0` | No errors occurred (`not errors`) **and** either at least one plan was newly archived (`len(archived) > 0`, or would be under `--dry-run`) **or** at least one plan was skipped as `"already archived at ..."` (`already > 0`). | `run` (`archive.py:1183-1188`) |
| `1` | Explicit plan file not found (`950`); no plan files found under `--project-dir` (`964`); `--imaging-root` resolution failed (`970`); `--dest` is inside `--imaging-root` (`979`); `read_imaging_csv` raised `FileNotFoundError` (`994`); any per-plan load, folder-name, swap-recovery, zero-frame, or copy error occurred (`errors` non-empty at `1184`); or `not archived and already == 0` (e.g., all plans skipped as incomplete, `1185-1187`). | `run` (`archive.py:949-1188`) |
| `2` | Invalid CLI arguments (`argparse` usage error). | `_build_parser().parse_args(argv)` (`archive.py:1199`) |

---

## 2. Classes, Data Models & Exceptions

### `ArchiveError(Exception)` (`archive.py:118-119`)
- **Inheritance:** `builtins.Exception`
- **Docstring:** `"A plan could not be archived; the message is user-facing."`
- **Role:** Raised by:
  - `_folder_name` (`archive.py:187`) when a derived folder name is `""`, `"."`, or `".."`.
  - `_dest_child` (`archive.py:210`) when `dest / folder` resolves to `dest` or outside `dest`.
  - `_copy_file` (`archive.py:536`, `538`) on post-copy byte-size or SHA-256 mismatch.
  - `_archive_one` (`archive.py:715`, `818`) when zero selected frames could be copied (`copied == 0`) or when any unexpected `Exception` occurs during staging/commit (after cleaning up `staging`).
  - `_resolve_root` (`archive.py:921`) via `metadata.resolve_cli_root` when the imaging root cannot be resolved.
  - Also imported and caught by `nina_planner/purge.py` (`purge.py:98`, `839`).

---

## 3. Function & Method Reference

### 3.1 `_human_bytes` (`archive.py:125-131`)

1. **Purpose & Architectural Role**
   Formats an integer byte count into a human-readable binary-unit string (`B`, `KiB`, `MiB`, `GiB`, `TiB`, `PiB`) for console progress and summary messages. Imported and reused by `purge.py` (`purge.py:100`).

2. **Signature, Decorators & Inputs**
   ```python
   def _human_bytes(count: int) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:** `count: int` — Non-negative byte count.

3. **Outputs & Return Semantics**
   - **Returns:** `str` — Integer-formatted string for bytes (`f"{size:.0f} B"`) or 1-decimal-place float string for larger binary units (`f"{size:.1f} KiB"`, etc.).

4. **Step-by-Step Internal Logic**
   1. **Line 126:** Converts `count` to `size = float(count)`.
   2. **Lines 127-130:** Iterates `for unit in ("B", "KiB", "MiB", "GiB", "TiB"):`. If `size < 1024 or unit == "TiB"`, returns `f"{size:.0f} {unit}"` when `unit == "B"` else `f"{size:.1f} {unit}"`; otherwise divides `size /= 1024`.
   3. **Line 131:** Unreachable fallback `return f"{size:.1f} PiB"  # pragma: no cover - unreachable` (since `unit == "TiB"` always returns on the final loop iteration).

5. **Side Effects & I/O**
   - None (pure function).

6. **Error Handling & Edge Cases**
   - `count = 0` returns `"0 B"`. Values $\ge 1024 \text{ TiB}$ return in `TiB` because of `or unit == "TiB"` on line 128.

7. **Complexity & Risk Notes**
   - $O(1)$ time and space (at most 5 loop iterations).

---

### 3.2 `_sha256` (`archive.py:134-139`)

1. **Purpose & Architectural Role**
   Computes the lowercase hexadecimal SHA-256 digest of a file on disk in 1 MiB chunks (`1 << 20` bytes), used by `_copy_file` when `--checksum` is enabled.

2. **Signature, Decorators & Inputs**
   ```python
   def _sha256(path: Path) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:** `path: Path` — File to hash.

3. **Outputs & Return Semantics**
   - **Returns:** `str` — 64-character lowercase hex SHA-256 digest.

4. **Step-by-Step Internal Logic**
   1. **Line 135:** Initializes `digest = hashlib.sha256()`.
   2. **Lines 136-138:** Opens `path.open("rb") as handle:` and iterates `for chunk in iter(lambda: handle.read(1 << 20), b""):`, calling `digest.update(chunk)`.
   3. **Line 139:** Returns `digest.hexdigest()`.

5. **Side Effects & I/O**
   - **Disk Read:** Sequentially reads all bytes of `path` in 1 MiB blocks.

6. **Error Handling & Edge Cases**
   - Propagates `OSError` if `path` cannot be opened or read (caught and wrapped into `ArchiveError` by `_archive_one` at `archive.py:816-818`).

7. **Complexity & Risk Notes**
   - $O(B)$ time where $B$ is file size in bytes; $O(1)$ memory (1 MiB read buffer).

---

### 3.3 `_inside` (`archive.py:142-147`)

1. **Purpose & Architectural Role**
   Tests whether `child` resolves inside (or equal to) `parent` after symlink/relative-path resolution. Used to prevent `--dest` from being placed inside `--imaging-root`, to filter out archived `plan.json` copies inside `--dest` during plan discovery, and inside `_dest_child`. Also imported by `purge.py` (`purge.py:101`).

2. **Signature, Decorators & Inputs**
   ```python
   def _inside(child: Path, parent: Path) -> bool:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `child: Path` — Candidate inner path.
     - `parent: Path` — Candidate outer container path.

3. **Outputs & Return Semantics**
   - **Returns:** `bool` — `True` if `child.resolve()` is equal to or a descendant of `parent.resolve()`; `False` otherwise.

4. **Step-by-Step Internal Logic**
   1. **Lines 143-146:** Calls `child.resolve().relative_to(parent.resolve())` inside a `try` block; catches `ValueError` and returns `False`.
   2. **Line 147:** Returns `True`.

5. **Side Effects & I/O**
   - Calls `Path.resolve()`, which queries the OS filesystem for symlinks along existing path prefixes.

6. **Error Handling & Edge Cases**
   - Note that `p.relative_to(p)` succeeds (returns `Path(".")`), so `_inside(dest, dest)` returns `True`. Callers that require strict child containment (such as `_dest_child`, `archive.py:209`) explicitly also check `child.resolve() == dest.resolve()`.

7. **Complexity & Risk Notes**
   - $O(D)$ in path depth $D$.

---

### 3.4 `_looks_like_plan_file` (`archive.py:150-163`)

1. **Purpose & Architectural Role**
   Heuristic predicate that inspects a JSON file on disk to determine whether it has observation-plan structure (contains at least one key from `_PLAN_KEYS = ("pointings", "light_frames", "plan_id")`). Used when `load_plan` fails in discovery mode so broken plans are reported as errors while unrelated project JSON files (e.g., `opencode.json`) are silently ignored. Also imported by `purge.py` (`purge.py:102`).

2. **Signature, Decorators & Inputs**
   ```python
   def _looks_like_plan_file(path: Path) -> bool:
   ```
   - **Decorators:** None.
   - **Parameters:** `path: Path` — Path to the JSON file that failed `load_plan`.

3. **Outputs & Return Semantics**
   - **Returns:** `bool` — `True` if `path` parses as a JSON object (`dict`) containing at least one of `"pointings"`, `"light_frames"`, or `"plan_id"`; `False` if unreadable, invalid JSON, not a `dict`, or lacking all three keys.

4. **Step-by-Step Internal Logic**
   1. **Lines 159-162:** Reads and parses `data = json.loads(path.read_text(encoding="utf-8"))`; on `(OSError, ValueError)` returns `False`.
   2. **Line 163:** Returns `isinstance(data, dict) and any(key in data for key in _PLAN_KEYS)`.

5. **Side Effects & I/O**
   - **Disk Read:** Reads `path` into memory as UTF-8 text.

6. **Error Handling & Edge Cases**
   - Unparseable JSON files return `False` (matching `tools/plans.py:_probe_plan_id`); if the user named the file explicitly on the CLI, `run` checks `if explicit or _looks_like_plan_file(plan_file):` (`archive.py:1010`) so explicit non-JSON or malformed files are always reported as errors.

7. **Complexity & Risk Notes**
   - $O(S)$ where $S$ is the size of `path`.

---

### 3.5 `_folder_name` (`archive.py:166-194`)

1. **Purpose & Architectural Role**
   Computes the deterministic, greppable archive directory name (`<sanitized-plan-stem>__<sanitized-plan-id>`, or `<sanitized-plan-stem>` if the stem already contains `plan_id`) and guards against dangerous directory traversal components (`""`, `"."`, `".."`). Also imported by `purge.py` (`purge.py:99`).

2. **Signature, Decorators & Inputs**
   ```python
   def _folder_name(plan_file: Path, plan: ObservationPlan) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan_file: Path` — Path to the plan JSON file (only `plan_file.stem` and `plan_file.name` are used).
     - `plan: ObservationPlan` — Loaded plan model.

3. **Outputs & Return Semantics**
   - **Returns:** `str` — Safe single-component directory name.
   - **Raises:** `ArchiveError` (`archive.py:187-193`) if the resulting `name` is in `("", ".", "..")`.

4. **Step-by-Step Internal Logic**
   1. **Line 177:** Sanitizes the filename stem via `stem = plan_utils.sanitize_filename(plan_file.stem)`.
   2. **Line 178:** Retrieves the base plan ID via `plan_id = plan._base_plan_id()`.
   3. **Lines 179-184:**
      - If `plan_id and plan_id in stem`: sets `name = stem` (avoids duplicating the ID if the filename already embeds it).
      - Else if `plan_id`: sets `name = f"{stem}__{plan_utils.sanitize_filename(plan_id)}"`.
      - Else: sets `name = stem`.
   4. **Lines 185-193:** Checks `if name in ("", ".", ".."):`. Because `ObservationPlan`'s `_PLAN_ID_ALLOWED` charset permits `.` and `sanitize_filename` only strips path separators (so a plan file named `..json` with `plan_id="."` produces `name="."`), this check refuses `""`, `"."`, and `".."` by raising `ArchiveError` with remediation instructions (`nina-pin-id` or rename).
   5. **Line 194:** Returns `name`.

5. **Side Effects & I/O**
   - None (pure string/model computation).

6. **Error Handling & Edge Cases**
   - Prevents catastrophic `shutil.rmtree(dest)` under `--force` when `dest / "." == dest` or `dest / ".." == dest.parent`.

7. **Complexity & Risk Notes**
   - $O(S)$ if `_base_plan_id()` hashes the plan model, else $O(1)$.

---

### 3.6 `_dest_child` (`archive.py:197-215`)

1. **Purpose & Architectural Role**
   Second, belt-and-braces containment guard directly in front of `_archive_one`'s staging/commit path joins: computes `dest / folder` and verifies that the resolved path is strictly inside `dest` (neither equal to `dest` nor outside `dest`).

2. **Signature, Decorators & Inputs**
   ```python
   def _dest_child(dest: Path, folder: str) -> Path:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `dest: Path` — Archive root directory.
     - `folder: str` — Candidate child directory name.

3. **Outputs & Return Semantics**
   - **Returns:** `Path` — `dest / folder`.
   - **Raises:** `ArchiveError` (`archive.py:210-214`) if `(dest / folder).resolve() == dest.resolve()` or `not _inside(child, dest)`.

4. **Step-by-Step Internal Logic**
   1. **Line 208:** Computes `child = dest / folder`.
   2. **Lines 209-214:** If `child.resolve() == dest.resolve() or not _inside(child, dest):`, raises `ArchiveError` stating that `folder` resolves outside or onto `dest`.
   3. **Line 215:** Returns `child`.

5. **Side Effects & I/O**
   - Calls `Path.resolve()` on `child` and `dest`.

6. **Error Handling & Edge Cases**
   - Protects the commit step in `_archive_one` even if `_archive_one` were called directly bypassing `_folder_name`.

7. **Complexity & Risk Notes**
   - $O(D)$ path resolution.

---

### 3.7 `_recover_replaced` (`archive.py:218-238`)

1. **Purpose & Architectural Role**
   Crash-recovery helper for the atomic `--force` swap protocol. Under `--force`, `_archive_one` renames the existing `final` (`<dest>/<folder>`) to `.<folder>.replaced` before renaming `.<folder>.partial` to `final`, then deletes `.<folder>.replaced`. If a prior run was killed between those steps, `_recover_replaced` reconciles `.<folder>.replaced` before the next skip or commit check.

2. **Signature, Decorators & Inputs**
   ```python
   def _recover_replaced(final: Path) -> None:
   ```
   - **Decorators:** None.
   - **Parameters:** `final: Path` — Target archive folder path (`<dest>/<folder>`).

3. **Outputs & Return Semantics**
   - **Returns:** `None`.

4. **Step-by-Step Internal Logic**
   1. **Line 232:** Computes sibling path `previous = final.with_name(f".{final.name}.replaced")`.
   2. **Lines 233-234:** If `not previous.exists():`, returns `None` immediately (common fast path).
   3. **Lines 235-236:** If `final.exists():` (both `.replaced` and `final` exist, meaning the new archive rename succeeded on the prior run and only cleanup was interrupted), deletes the stale backup via `shutil.rmtree(previous, ignore_errors=True)`.
   4. **Lines 237-238:** Else (`final` does not exist, meaning `.replaced` is the only surviving copy of the previous archive), restores it via `previous.rename(final)`.

5. **Side Effects & I/O**
   - **Filesystem Mutation:** Either deletes `.<folder>.replaced` (`shutil.rmtree`) or renames `.<folder>.replaced` back to `<folder>` (`Path.rename`).

6. **Error Handling & Edge Cases**
   - Called in **two** places: in `run` (`archive.py:1111`) before the `if (dest / folder).exists() and not args.force:` check (so an interrupted swap is restored before deciding whether the plan is already archived), and again in `_archive_one` (`archive.py:798`) right before committing.
   - In `run` (`archive.py:1110-1114`), any `OSError` from `previous.rename(final)` is caught and recorded as a per-plan error.

7. **Complexity & Risk Notes**
   - $O(1)$ rename when restoring; $O(F)$ unlink when removing a stale `.replaced` tree of $F$ files.

---

### 3.8 `_merge_weather` (`archive.py:241-272`)

1. **Purpose & Architectural Role**
   Enriches `LIGHT` metadata rows with ambient weather telemetry from sibling `WeatherData.csv` files (via `imaging.read_weather_rows`). Caches weather maps per parent directory so `ExposureNumber` resets across different date/target folders never cross-attach weather rows, and preserves existing image-metadata keys (`exposure_number`, `exposure_start`, `exposure_start_utc`).

2. **Signature, Decorators & Inputs**
   ```python
   def _merge_weather(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
   ```
   - **Decorators:** None.
   - **Parameters:** `rows: list[dict[str, Any]]` — List of `LIGHT` metadata row dicts returned by `read_imaging_csv`.

3. **Outputs & Return Semantics**
   - **Returns:** `list[dict[str, Any]]` — New list of row dicts (returns `rows` unchanged if `rows` is empty; individual rows without a `file_path` or without a matching `exposure_number` in `WeatherData.csv` are appended unmodified, while matched rows are shallow-copied with missing weather keys added).

4. **Step-by-Step Internal Logic**
   1. **Lines 249-250:** If `not rows:`, returns `rows`.
   2. **Lines 251-252:** Initializes per-folder cache `cache: dict[Path, dict[str, dict[str, Any]]] = {}` and output list `merged: list[dict[str, Any]] = []`.
   3. **Lines 253-257:** Iterates `for row in rows:`. Extracts `file_path_str = str(row.get("file_path") or "")`. If empty, appends `row` to `merged` and continues.
   4. **Lines 258-261:** Computes `parent_dir = Path(file_path_str).parent`. On cache miss (`parent_dir not in cache`), populates `cache[parent_dir] = read_weather_rows(parent_dir)`. Looks up `weather_map = cache[parent_dir]`.
   5. **Lines 262-266:** Extracts `exp_nr = str(row.get("exposure_number", "")).strip()` and looks up `wx = weather_map.get(exp_nr)`. If `not wx:`, appends `row` unchanged and continues.
   6. **Lines 267-271:** Creates `out = dict(row)`, copies every `(k, v)` from `wx.items()` where `k not in out` (so image-metadata identity columns win over weather columns), and appends `out` to `merged`.
   7. **Line 272:** Returns `merged`.

5. **Side Effects & I/O**
   - **Disk Read:** Calls `read_weather_rows(parent_dir)` once per unique parent directory of `rows`.

6. **Error Handling & Edge Cases**
   - Missing or malformed `WeatherData.csv` files are handled gracefully inside `read_weather_rows` (which returns `{}`), so `_merge_weather` never aborts an archive on missing weather data.
   - Because `run` calls `_merge_weather` before `_select_frames` and `_divert_frames` (`archive.py:987-991`), `LIGHT` rows diverted to `junk/` also retain their merged weather columns in `junk/ImageMetaData.csv`.

7. **Complexity & Risk Notes**
   - $O(R \cdot K + D \cdot W)$ where $R$ is the number of light rows, $K$ is the number of weather columns, $D$ is the number of unique parent folders, and $W$ is the number of weather CSV rows per folder.

---

### 3.9 `_write_image_metadata_csv` (`archive.py:275-300`)

1. **Purpose & Architectural Role**
   Writes a self-contained `ImageMetaData.csv` sidecar into an archive subdirectory (`lights/`, `flats/`, `darks/`, `biases/`, or `junk/`) containing only the rows whose files actually shipped, augmented with an `archive_path` column (`<type_dir>/<filename>`) so offline tools can join `ImageMetaData.csv` with `manifest.json` without re-deriving filenames.

2. **Signature, Decorators & Inputs**
   ```python
   def _write_image_metadata_csv(
       path: Path, rows: list[dict[str, Any]], rel_paths: dict[str, str]
   ) -> None:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `path: Path` — Destination CSV file path (e.g., `staging / "lights" / "ImageMetaData.csv"`).
     - `rows: list[dict[str, Any]]` — Shipped metadata row dicts (snake_case keys; for `junk/`, also includes `"junk_reason"`).
     - `rel_paths: dict[str, str]` — Mapping from original absolute `str(row["file_path"])` to archive-relative path (`"lights/<name>"`).

3. **Outputs & Return Semantics**
   - **Returns:** `None`.

4. **Step-by-Step Internal Logic**
   1. **Lines 285-286:** If `not rows:`, returns immediately without creating an empty CSV file.
   2. **Lines 287-293:** Collects `fieldnames: list[str] = []` in insertion order across all `row` dicts in `rows` (preserving heterogeneous columns such as weather columns present only on some rows), then appends `"archive_path"` if not already in `fieldnames`.
   3. **Lines 294-300:** Opens `path.open("w", encoding="utf-8", newline="")`, creates `csv.DictWriter(handle, fieldnames=fieldnames, restval="")`, calls `writer.writeheader()`, and writes each row copy with `out["archive_path"] = rel_paths.get(str(row.get("file_path")), "")`.

5. **Side Effects & I/O**
   - **Disk Write:** Creates and writes `path` (parent directory was already created by `_copy_file` when copying the kept frames).

6. **Error Handling & Edge Cases**
   - Only called when `write_metadata and kept_rows` (`archive.py:659`) or `write_metadata and sidecar_rows` (`archive.py:704`), so `path.parent` is guaranteed to exist on disk.

7. **Complexity & Risk Notes**
   - $O(R \cdot C)$ where $R = \text{len}(\text{rows})$ and $C = \text{len}(\text{fieldnames})$.

---

### 3.10 `_progress_facts` (`archive.py:306-344`)

1. **Purpose & Architectural Role**
   Evaluates whether `plan` is complete across all pointings using the shared `progress.progress_verdict` rule and aggregates expected vs. credited (`acquired`) frame counts per frame type (`light`, `flat`, `dark`, `bias`). Also imported and called directly by `nina_planner/purge.py` (`purge.py:102`, `916`).

2. **Signature, Decorators & Inputs**
   ```python
   def _progress_facts(
       plan: ObservationPlan, rows: list[dict[str, Any]], now: datetime
   ) -> tuple[bool, dict[str, dict[str, int]]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan: ObservationPlan` — Loaded observation plan.
     - `rows: list[dict[str, Any]]` — Live imaging metadata rows from `read_imaging_csv`.
     - `now: datetime` — Reference UTC timestamp for calibration window evaluation.

3. **Outputs & Return Semantics**
   - **Returns:** `tuple[bool, dict[str, dict[str, int]]]` — `(complete, counts)` where:
     - `complete: bool` is `True` iff `progress_verdict(progress)["complete"]` is `True` for every pointing `1..len(plan.pointings)`.
     - `counts: dict[str, dict[str, int]]` has keys `"light"`, `"flat"`, `"dark"`, `"bias"`, each mapping to `{"expected": int, "acquired": int}` (note: `_archive_one` later mutates each sub-dict in place to add `"archived": int` at `archive.py:723`).

4. **Step-by-Step Internal Logic**
   1. **Lines 322-325:** Initializes `complete = True`, `light_expected = 0`, `light_acquired = 0`, and `plan_level: dict[str, list[dict[str, Any]]] | None = None`.
   2. **Lines 326-333:** Loops `for index in range(1, len(plan.pointings) + 1):`:
      - Calls `progress = plan_progress(plan, rows, pointing_index=index, now=now)`.
      - Captures `plan_level = progress` on `index == 1`.
      - If `not progress_verdict(progress)["complete"]:`, sets `complete = False`.
      - Adds `sum(g["total_count"] for g in progress["light"])` to `light_expected` and `sum(g["acquired_count"] for g in progress["light"])` to `light_acquired`.
   3. **Line 334:** Asserts `assert plan_level is not None` (guaranteed by `ObservationPlan` validation requiring $\ge 1$ pointing).
   4. **Lines 335-343:** Initializes `counts = {"light": {"expected": light_expected, "acquired": light_acquired}}` and populates `"flat"`, `"dark"`, and `"bias"` from `plan_level[image_type]` (calibration is plan-level, read from pointing 1 just like `audit_plans`).
   5. **Line 344:** Returns `complete, counts`.

5. **Side Effects & I/O**
   - None (pure in-memory computation).

6. **Error Handling & Edge Cases**
   - Crucially checks `progress_verdict(progress)["complete"]` rather than `remaining_count == 0`, because `plan_progress` zeroes `remaining_count` when a calibration window expires (`window_closed`), whereas an expired unfulfilled calibration window must still read `complete = False` unless overridden by `--all`.

7. **Complexity & Risk Notes**
   - $O(P \cdot R)$ where $P = \text{len}(\text{plan.pointings})$ and $R = \text{len}(\text{rows})$.

---

### 3.11 `_select_frames` (`archive.py:347-384`)

1. **Purpose & Architectural Role**
   Selects all metadata rows attributed to `plan` across all pointings, grouped by lowercase frame type (`"light"`, `"flat"`, `"dark"`, `"bias"`), deduplicated by `file_path`, and sorted deterministically by `file_path`. Also imported and called by `nina_planner/purge.py` (`purge.py:104`, `848`) to compute plan claims.

2. **Signature, Decorators & Inputs**
   ```python
   def _select_frames(
       plan: ObservationPlan, rows: list[dict[str, Any]], now: datetime
   ) -> dict[str, list[dict[str, Any]]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan: ObservationPlan` — Target plan.
     - `rows: list[dict[str, Any]]` — All live rows from `read_imaging_csv`.
     - `now: datetime` — Reference UTC timestamp passed to calibration window matching.
   - **Inner Helper (`add`, `archive.py:363-366`):**
     ```python
     def add(image_type: str, row: dict[str, Any]) -> None:
     ```
     Strips `str(row.get("file_path") or "")`; if non-empty, inserts `selected[image_type][file_path] = row`.

3. **Outputs & Return Semantics**
   - **Returns:** `dict[str, list[dict[str, Any]]]` — Dictionary with keys `"light"`, `"flat"`, `"dark"`, `"bias"`, each mapping to a list of row dicts sorted by `str(r.get("file_path"))`.

4. **Step-by-Step Internal Logic**
   1. **Lines 359-361:** Initializes `selected: dict[str, dict[str, dict[str, Any]]] = {t.lower(): {} for t in _ARCHIVE_TYPES}`.
   2. **Lines 363-366:** Defines inner helper `add(image_type, row)` to key rows by non-empty `file_path` (deduplicating pooled calibration frames matched across multiple mosaic pointings).
   3. **Lines 368-380:** Loops `for index in range(1, len(plan.pointings) + 1):`:
      - Calls `filter_metadata_rows(plan, rows, "LIGHT", pointing_index=index)` and passes each row to `add("light", row)`.
      - For `image_type in ("FLAT", "DARK", "BIAS")`, calls `filter_metadata_rows(plan, rows, image_type, pointing_index=index, max_age_days=plan.calibration_window_days, now=now)` and passes each row to `add(image_type.lower(), row)`.
   4. **Lines 381-384:** Returns `{image_type: sorted(bucket.values(), key=lambda r: str(r.get("file_path"))) for image_type, bucket in selected.items()}`.

5. **Side Effects & I/O**
   - None (pure in-memory selection).

6. **Error Handling & Edge Cases**
   - Rows with empty/missing `file_path` are ignored (`if file_path:` at `archive.py:365`).
   - Note that `_select_frames` includes degenerate light frames if they carry the plan's token; `archive.run` immediately pipes `_select_frames` into `_without_degenerate` (`archive.py:1029-1030`), whereas `purge.run` uses `_select_frames` directly (`purge.py:848`) so a plan claims all of its own-token frames (including degenerate ones archived under `junk/`).

7. **Complexity & Risk Notes**
   - $O(P \cdot R + S \log S)$ where $P$ is pointing count, $R$ is total CSV rows, and $S$ is selected frame count.

---

### 3.12 `_without_degenerate` (`archive.py:387-403`)

1. **Purpose & Architectural Role**
   Filters out degenerate captures (`progress.is_junk_row(plan, row, image_type)`) from the dictionary returned by `_select_frames` so degenerate light captures (e.g., 0.00003s Smart Exposure frames carrying the plan's token) never ship into `lights/` where a stacking pipeline would ingest them, allowing `_divert_frames` to route them into `junk/` instead.

2. **Signature, Decorators & Inputs**
   ```python
   def _without_degenerate(
       plan: ObservationPlan, selected: dict[str, list[dict[str, Any]]]
   ) -> dict[str, list[dict[str, Any]]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan: ObservationPlan` — Plan being evaluated.
     - `selected: dict[str, list[dict[str, Any]]]` — Per-type selected rows from `_select_frames`.

3. **Outputs & Return Semantics**
   - **Returns:** `dict[str, list[dict[str, Any]]]` — Same structure as `selected`, with rows satisfying `is_junk_row(plan, row, image_type)` removed.

4. **Step-by-Step Internal Logic**
   1. **Lines 400-403:** Returns a dictionary comprehension `{image_type: [row for row in frames if not is_junk_row(plan, row, image_type)] for image_type, frames in selected.items()}`.

5. **Side Effects & I/O**
   - None (pure function).

6. **Error Handling & Edge Cases**
   - For calibration (`flat`, `dark`, `bias`), `_select_frames` already requires exposure matching within $\pm 0.05\text{ s}$ for darks (`progress._exposure_matches`), so degenerate darks were already excluded from `selected`; `_without_degenerate` specifically catches degenerate `light` frames selected on token alone.

7. **Complexity & Risk Notes**
   - $O(S)$ over selected frames $S$.

---

### 3.13 `_divert_frames` (`archive.py:406-441`)

1. **Purpose & Architectural Role**
   Identifies all live CSV rows carrying `plan`'s base ID token (`path_has_id(path, base_id)`) that **no loaded plan** ships into a type folder (`path not in shipped`), pairing each with its diversion reason (`"degenerate"` or `"uncredited"`) for archival into `<dest>/<folder>/junk/`.

2. **Signature, Decorators & Inputs**
   ```python
   def _divert_frames(
       plan: ObservationPlan,
       rows: list[dict[str, Any]],
       shipped: set[str],
   ) -> list[tuple[dict[str, Any], str]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan: ObservationPlan` — Target plan.
     - `rows: list[dict[str, Any]]` — All live rows from `read_imaging_csv`.
     - `shipped: set[str]` — Union of `file_path` strings selected into type folders across **every** loaded plan in Pass 2 (`archive.py:1026-1038`).

3. **Outputs & Return Semantics**
   - **Returns:** `list[tuple[dict[str, Any], str]]` — List of `(row, reason)` tuples sorted by `str(row.get("file_path"))`, where `reason` is `"degenerate"` if `is_junk_row(plan, row, image_type)` is `True`, else `"uncredited"`.

4. **Step-by-Step Internal Logic**
   1. **Lines 429-430:** Extracts `base_id = plan._base_plan_id()` and initializes `diverted: list[tuple[dict[str, Any], str]] = []`.
   2. **Lines 431-439:** Iterates `for row in rows:`:
      - Reads `path = str(row.get("file_path") or "")`.
      - Skips if `not path or path in shipped`.
      - Skips if `not path_has_id(path, base_id)` (matches bare `_{base_id}_`, pane-suffixed `_{base_id}-<N>_`, and bracketed/parenthesized forms).
      - Reads `image_type = str(row.get("image_type") or "").upper()`.
      - Sets `reason = "degenerate" if is_junk_row(plan, row, image_type) else "uncredited"`.
      - Appends `(row, reason)` to `diverted`.
   3. **Lines 440-441:** Sorts `diverted` by `str(pair[0].get("file_path"))` and returns it.

5. **Side Effects & I/O**
   - None (pure function).

6. **Error Handling & Edge Cases**
   - **Pooled Calibration Protection:** Because `shipped` is the union of selections across **all** loaded plans (including incomplete ones), an own-token calibration frame that falls outside this plan's window but inside another plan's window is in `shipped` and is **not** diverted to `junk/` here; instead, `disk_report` lists it under `disk.not_selected`.

7. **Complexity & Risk Notes**
   - $O(R + J \log J)$ where $R = \text{len}(\text{rows})$ and $J = \text{len}(\text{diverted})$.

---

### 3.14 `disk_report` (`archive.py:444-525`)

1. **Purpose & Architectural Role**
   Public helper that builds the `manifest.json` `"disk"` section (`rejected`, `gone`, `not_selected`, `junk`) purely from CSV records (`rows` and `dropped_rows`) and targeted `Path.is_file()` existence probes on `BAD_<name>` twins—never performing an unguided filesystem directory walk.

2. **Signature, Decorators & Inputs**
   ```python
   def disk_report(
       plan: ObservationPlan,
       rows: list[dict[str, Any]],
       dropped: list[dict[str, Any]],
       selected: dict[str, list[dict[str, Any]]],
       divert: list[tuple[dict[str, Any], str]],
   ) -> dict[str, list[dict[str, Any]]]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan: ObservationPlan` — Plan being archived.
     - `rows: list[dict[str, Any]]` — Live CSV rows for this plan's imaging root.
     - `dropped: list[dict[str, Any]]` — Rows dropped by `read_imaging_csv(report_dropped=True)` because their recorded `file_path` no longer existed on disk at read time (classified by lines `485-496` into `rejected` when a `BAD_<name>` twin file exists on disk, or `gone` otherwise).
     - `selected: dict[str, list[dict[str, Any]]]` — Non-degenerate frames selected for this plan's type folders.
     - `divert: list[tuple[dict[str, Any], str]]` — Frames diverted to `junk/` by `_divert_frames`.

3. **Outputs & Return Semantics**
   - **Returns:** `dict[str, list[dict[str, Any]]]` with four exact keys:
     - `"rejected"`: `[{"type": <UPPER>, "source": <path>, "found": "BAD_<name>"}, ...]`
     - `"gone"`: `[{"type": <UPPER>, "source": <path>}, ...]`
     - `"not_selected"`: `[{"type": <UPPER>, "source": <path>}, ...]`
     - `"junk"`: `[{"type": <UPPER>, "source": <path>, "reason": "degenerate" | "uncredited"}, ...]`

4. **Step-by-Step Internal Logic**
   1. **Lines 478-482:** Builds `selected_paths` set from `str(row.get("file_path") or "")` across all lists in `selected.values()`.
   2. **Lines 483-496:** Initializes `rejected = []` and `gone = []`. Iterates `for row in dropped:`:
      - Builds `entry = {"type": str(row.get("image_type") or "").upper(), "source": path}`.
      - Computes `twin = Path(path).with_name(f"BAD_{Path(path).name}") if path else None`.
      - If `twin is not None and twin.is_file():`, sets `entry["found"] = twin.name` and appends `entry` to `rejected`; otherwise appends `entry` to `gone`.
   3. **Lines 498-511:** Builds `divert_paths` set from `divert`. Extracts `base_id = plan._base_plan_id()`. Iterates `for row in rows:`:
      - Skips if `not path or path in selected_paths or path in divert_paths`.
      - If `path_has_id(path, base_id):`, appends `{"type": str(row.get("image_type") or "").upper(), "source": path}` to `not_selected` (these are own-token rows that another loaded plan selected into its type folder, so `path in shipped` kept them out of `divert`).
   4. **Lines 512-519:** Builds `junk` list from `divert`, mapping each `(row, reason)` to `{"type": str(row.get("image_type") or "").upper(), "source": str(row.get("file_path") or ""), "reason": reason}`.
   5. **Lines 520-525:** Returns `{"rejected": rejected, "gone": gone, "not_selected": not_selected, "junk": junk}`.

   ```mermaid
   flowchart TD
       subgraph DroppedRows["1. Classify Dropped CSV Rows (lines 485-496)"]
           D0["For each row in dropped"] --> D1["path = str(row.get('file_path') or '')<br/>twin = Path(path).with_name(f'BAD_{Path(path).name}')"]
           D1 --> D2{"twin is not None<br/>and twin.is_file()?"}
           D2 -->|Yes| D3["entry['found'] = twin.name<br/>Append to rejected[]"]
           D2 -->|No| D4["Append to gone[]"]
       end

       subgraph LiveRows["2. Classify Live CSV Rows (lines 498-519)"]
           L0["For each row in rows"] --> L1{"path empty OR<br/>path in selected_paths OR<br/>path in divert_paths?"}
           L1 -->|Yes| L2["Skip (already accounted for in<br/>type folders or junk/)"]
           L1 -->|No| L3{"path_has_id(path, base_id)?"}
           L3 -->|Yes| L4["Append to not_selected[]<br/>(own-token row shipped by another plan)"]
           L3 -->|No| L5["Ignore (belongs to another plan)"]
           J0["For each (row, reason) in divert"] --> J1["Append {type, source, reason} to junk[]"]
       end

       D3 & D4 & L4 & J1 --> OUT["Return {'rejected', 'gone', 'not_selected', 'junk'}"]
   ```

5. **Side Effects & I/O**
   - **Filesystem Stat:** Calls `twin.is_file()` once per row in `dropped`.

6. **Error Handling & Edge Cases**
   - Note that `dropped` comes from `read_imaging_csv(root=imaging_root, image_type=None, report_dropped=True)` across the imaging root (`archive.py:984-986`), so `rejected` and `gone` reflect dropped rows across the root.

7. **Complexity & Risk Notes**
   - $O(|dropped| + |rows| + |divert|)$ time.

---

### 3.15 `_copy_file` (`archive.py:531-539`)

1. **Purpose & Architectural Role**
   Low-level verified file copy helper: ensures the destination parent directory exists, copies `src` to `dst` preserving metadata (`shutil.copy2`), verifies that byte sizes match, optionally verifies SHA-256 digests (`checksum=True`), and returns the copied byte size.

2. **Signature, Decorators & Inputs**
   ```python
   def _copy_file(src: Path, dst: Path, *, checksum: bool) -> int:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `src: Path` — Source file path.
     - `dst: Path` — Destination file path inside the staging folder.
     - `checksum: bool` (keyword-only) — When `True`, verifies `_sha256(src) == _sha256(dst)`.

3. **Outputs & Return Semantics**
   - **Returns:** `int` — Size of the copied file in bytes (`src.stat().st_size`).
   - **Raises:** `ArchiveError` on size mismatch (`archive.py:536`) or SHA-256 mismatch (`archive.py:538`).

4. **Step-by-Step Internal Logic**
   1. **Line 532:** Ensures parent directory exists via `dst.parent.mkdir(parents=True, exist_ok=True)`.
   2. **Line 533:** Copies file data and stat metadata via `shutil.copy2(src, dst)`.
   3. **Lines 534-536:** Reads `src_size = src.stat().st_size`; if `dst.stat().st_size != src_size`, raises `ArchiveError(f"size mismatch after copy: {src} -> {dst}")`.
   4. **Lines 537-538:** If `checksum and _sha256(src) != _sha256(dst):`, raises `ArchiveError(f"checksum mismatch after copy: {src} -> {dst}")`.
   5. **Line 539:** Returns `src_size`.

5. **Side Effects & I/O**
   - Creates `dst.parent` if absent, writes `dst`, stats `src` and `dst`, and (if `checksum=True`) reads both files completely for SHA-256 hashing.

6. **Error Handling & Edge Cases**
   - Any `OSError` (e.g., `ENOSPC` disk full or `PermissionError`) propagates to `_archive_one`, which cleans up `staging` and converts it into `ArchiveError` (`archive.py:811-818`).

7. **Complexity & Risk Notes**
   - $O(B)$ I/O where $B$ is file size ($1\times$ read + $1\times$ write without `--checksum`; $3\times$ read + $1\times$ write with `--checksum`).

---

### 3.16 `_archive_one` (`archive.py:542-836`)

1. **Purpose & Architectural Role**
   Core transactional builder for a single observation plan's archive directory. Assembles `<dest>/.<folder>.partial` containing the four type folders (`lights/`, `flats/`, `darks/`, `biases/`), optional `junk/` folder, per-folder `ImageMetaData.csv` sidecars, verbatim `plan.json`, and `manifest.json` (v2); atomically swaps the staging directory into `<dest>/<folder>` (using `.<folder>.replaced` if `<dest>/<folder>` already exists); and appends a summary record to `<dest>/archive-index.jsonl`.

2. **Signature, Decorators & Inputs**
   ```python
   def _archive_one(
       plan_file: Path,
       plan: ObservationPlan,
       selected: dict[str, list[dict[str, Any]]],
       *,
       dest: Path,
       complete: bool,
       counts: dict[str, dict[str, int]],
       disk: dict[str, list[dict[str, Any]]],
       divert: list[tuple[dict[str, Any], str]],
       imaging_root: Path,
       write_metadata: bool,
       checksum: bool,
   ) -> dict[str, Any]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `plan_file: Path` — Path to source plan JSON file.
     - `plan: ObservationPlan` — Loaded observation plan model.
     - `selected: dict[str, list[dict[str, Any]]]` — Selected non-degenerate rows per lowercase type (`light`, `flat`, `dark`, `bias`).
     - `dest: Path` (keyword-only) — Archive destination root directory.
     - `complete: bool` (keyword-only) — Completeness verdict from `_progress_facts`.
     - `counts: dict[str, dict[str, int]]` (keyword-only) — Per-type `{"expected": int, "acquired": int}` dict (mutated in-place at line 723 to add `"archived"`).
     - `disk: dict[str, list[dict[str, Any]]]` (keyword-only) — Output of `disk_report`.
     - `divert: list[tuple[dict[str, Any], str]]` (keyword-only) — Output of `_divert_frames`.
     - `imaging_root: Path` (keyword-only) — Resolved N.I.N.A. imaging root.
     - `write_metadata: bool` (keyword-only) — Whether to write `ImageMetaData.csv` sidecars (`not args.no_image_metadata`).
     - `checksum: bool` (keyword-only) — Whether to verify SHA-256 hashes after copying (`args.checksum`).
   - **Inner Helper (`copy_one`, `archive.py:592-626`):**
     ```python
     def copy_one(
         row: dict[str, Any],
         *,
         type_name: str,
         type_dir: Path,
         rel_map: dict[str, str],
         used_names: set[str],
     ) -> tuple[Path, str, int] | None:
     ```
     - Checks `src = Path(str(row.get("file_path") or ""))`. If `not src.is_file()`, records `{"type": type_name, "source": str(src)}` in `missing` and returns `None` (`609-612`).
     - Deduplicates colliding filenames from different date folders (`if name in used_names:`) by partitioning on `".fits"` and inserting `f"{stem}_dup{len(used_names)}{dot}{ext}"` (or `f"{name}_{len(used_names)}"` if `".fits"` is absent), then adds `name` to `used_names` (`613-622`).
     - Calls `size = _copy_file(src, type_dir / name, checksum=checksum)`, records `rel_map[str(row["file_path"])] = f"{type_dir.name}/{name}"`, and returns `(src, rel_path, size)` (`623-626`).

3. **Outputs & Return Semantics**
   - **Returns:** `dict[str, Any]` — The summary record appended to `<dest>/archive-index.jsonl` (`archive.py:820-836`).
   - **Raises:** `ArchiveError` if `_folder_name` or `_dest_child` refuses the folder name, if `copied == 0` (zero selected type-folder frames could be copied, `archive.py:709-718`), or if any copy/verify/filesystem operation fails (cleaning up `staging` first at `archive.py:811-818`).

4. **Step-by-Step Internal Logic**
   1. **Lines 572-581 (Staging Setup):** Computes `folder = _folder_name(plan_file, plan)`, validates `final = _dest_child(dest, folder)`, sets `staging = dest / f".{folder}.partial"`, creates `dest`, removes any leftover `staging` directory (`shutil.rmtree(staging)`), and creates a fresh `staging.mkdir()`.
   2. **Lines 583-590 (Counters):** Initializes `manifest_files`, `missing`, `copied_by_type`, `selected_count`, `total_bytes = 0`, `copied = 0`, `junk_copied = 0`, and `junk_kept = []`.
   3. **Lines 592-626 (`copy_one` closure):** Defines inner helper to copy one frame, handle mid-run vanished files (`missing`), resolve filename collisions (`_dup<N>`), and populate `rel_map`.
   4. **Lines 628-664 (Type-Folder Copy Loop):** Inside `try:`, iterates `for image_type in _ARCHIVE_TYPES:`:
      - Maps `type_dir = staging / _FOLDER_NAMES[image_type]`.
      - Copies each `row` in `selected.get(image_type.lower(), [])` via `copy_one`.
      - Appends `{ "type": image_type, "path": rel_path, "source": str(src), "bytes": size }` to `manifest_files`, increments `total_bytes` and `copied`.
      - Records `copied_by_type[image_type.lower()] = len(kept_rows)`.
      - If `write_metadata and kept_rows:`, writes `type_dir / "ImageMetaData.csv"` via `_write_image_metadata_csv`.
   5. **Lines 670-707 (`junk/` Diversion Copy Loop):** If `divert` is non-empty:
      - Copies each `(row, reason)` into `junk_dir = staging / _FOLDER_NAMES["JUNK"]` via `copy_one(..., type_name="JUNK", ...)`.
      - Adds `"junk_reason": reason` to `sidecar_row`, appends to `junk_kept`, and appends `{ "type": "JUNK", "path": rel_path, "source": str(src), "bytes": size, "frame_type": ..., "reason": reason }` to `manifest_files`.
      - Increments `total_bytes` and `junk_copied`.
      - If `write_metadata and sidecar_rows:`, writes `junk_dir / "ImageMetaData.csv"`.
   6. **Lines 709-718 (Zero-Frame Refusal Gate):** If `copied == 0:`, raises `ArchiveError` (`none of the {selected_count} selected frame(s) could be copied...`). Note that `junk_copied > 0` deliberately does **not** rescue `copied == 0`.
   7. **Lines 722-723 (Update `archived` Counts):** Sets `counts[image_type.lower()]["archived"] = copied_by_type[image_type.lower()]` for each type in `_ARCHIVE_TYPES`.
   8. **Lines 726-764 (Plan Copy & Warnings Assembly):** Copies `plan_file` to `staging / "plan.json"` via `shutil.copy2`. Builds `warnings: list[str]` for:
      - `not complete` (`archived with --all while incomplete...`),
      - `numbers["archived"] < numbers["expected"]` (with calibration window note if `image_type != "light"`),
      - `missing` (`{len(missing)} frame(s) missing on disk...`),
      - `junk_kept` (`{len(junk_kept)} own-token frame(s) no plan ships into a type folder...`),
      - `disk["gone"]` (`{len(disk['gone'])} recorded frame(s) were already gone...`).
   9. **Lines 765-793 (Write `manifest.json` v2):** Constructs `manifest` dict (`"tool": "nina-archive"`, `"version": 2`, etc.) and writes it to `staging / _MANIFEST_NAME` (`manifest.json`).
   10. **Lines 798-810 (Atomic Commit & `.replaced` Swap):**
       - Calls `_recover_replaced(final)`.
       - Sets `previous = final.with_name(f".{final.name}.replaced")` and `had_previous = final.exists()`.
       - If `had_previous:`, renames `final.rename(previous)`.
       - Tries `staging.rename(final)`; on any `BaseException`, restores `if had_previous: previous.rename(final)` and re-raises.
       - If `had_previous:`, deletes `shutil.rmtree(previous, ignore_errors=True)`.
   11. **Lines 811-818 (Exception Cleanup):** On `ArchiveError`, deletes `staging` (`shutil.rmtree(staging, ignore_errors=True)`) and re-raises; on any other `Exception as exc`, deletes `staging` and raises `ArchiveError(f"{exc}") from exc`.
   12. **Lines 820-836 (Append `archive-index.jsonl`):** Builds `record` dict, appends it as a JSON line to `dest / _INDEX_NAME`, and returns `record`.

   ```mermaid
   flowchart TD
       A["Start _archive_one"] --> B["folder = _folder_name(plan_file, plan)<br/>final = _dest_child(dest, folder)<br/>staging = dest / f'.{folder}.partial'<br/>Reset & mkdir staging (lines 572-581)"]
       B --> C["Loop image_type in ('LIGHT', 'FLAT', 'DARK', 'BIAS')<br/>Copy rows via copy_one() into staging/&lt;type_dir&gt;/<br/>Write ImageMetaData.csv if write_metadata &amp; kept_rows (lines 629-664)"]
       C --> D{"divert non-empty?<br/>(line 670)"}
       D -->|Yes| E["Copy (row, reason) into staging/junk/<br/>Add junk_reason to sidecar_rows<br/>Write junk/ImageMetaData.csv (lines 671-707)"]
       D -->|No| F{"copied == 0?<br/>(line 709)"}
       E --> F
       F -->|Yes| ERR["rmtree(staging)<br/>raise ArchiveError('none of the selected frames could be copied')<br/>(lines 715-718, 814)"]
       F -->|No| G["Set counts[type]['archived'] = copied_by_type[type]<br/>copy2(plan_file, staging / 'plan.json')<br/>Assemble warnings[] &amp; write staging / 'manifest.json' (v2)<br/>(lines 722-793)"]
       G --> H["_recover_replaced(final)<br/>had_previous = final.exists()<br/>If had_previous: final.rename(.replaced) (lines 798-802)"]
       H --> I["staging.rename(final) (line 804)"]
       I -->|BaseException| ROLLBACK["If had_previous: .replaced.rename(final)<br/>rmtree(staging) &amp; raise (lines 805-818)"]
       I -->|Success| J["If had_previous: rmtree(.replaced)<br/>Append JSON line to dest / 'archive-index.jsonl'<br/>Return record (lines 809-836)"]
   ```

5. **Side Effects & I/O**
   - Mutates `counts[type]["archived"]` in place (`archive.py:723`).
   - Creates `dest`, `staging`, type subdirectories, copies all selected + diverted FITS files, writes `ImageMetaData.csv` sidecars, copies `plan.json`, writes `manifest.json`, renames `staging` to `final` (replacing any prior `final`), and appends to `archive-index.jsonl`.

6. **Error Handling & Edge Cases**
   - **Mid-Run File Disappearance:** If a file existed during `read_imaging_csv` but vanished before `copy_one`, it is recorded in `missing` and skipped without failing the archive—unless **every** selected frame vanished (`copied == 0`), which raises `ArchiveError`.
   - **Filename Collisions Across Date Folders:** Handled deterministically by `copy_one` (`_dup<N>` before `.fits`).
   - **Rollback on Rename Failure:** Catching `BaseException` (`archive.py:805`) ensures even `KeyboardInterrupt` during `staging.rename(final)` restores `previous.rename(final)`.

7. **Complexity & Risk Notes**
   - Dominated by file copy I/O: $O(\sum \text{bytes})$ across all selected and diverted frames.

---

### 3.17 `_build_parser` (`archive.py:842-917`)

1. **Purpose & Architectural Role**
   Constructs and returns the `argparse.ArgumentParser` for the `nina-archive` CLI.

2. **Signature, Decorators & Inputs**
   ```python
   def _build_parser() -> argparse.ArgumentParser:
   ```
   - **Decorators:** None.
   - **Parameters:** None.

3. **Outputs & Return Semantics**
   - **Returns:** `argparse.ArgumentParser` configured for `prog="nina-archive"` with positional `plans` (`nargs="*"`) and 9 optional flags (`--dest`, `--imaging-root`, `--project-dir`, `--all`, `--force`, `--dry-run`, `--checksum`, `--no-image-metadata`, `--no-weather`).

4. **Step-by-Step Internal Logic**
   1. **Lines 843-858:** Creates `argparse.ArgumentParser` with `prog="nina-archive"`, description, `RawDescriptionHelpFormatter`, and example epilog.
   2. **Lines 859-866:** Adds positional `"plans"` (`nargs="*"`, `metavar="PLAN.json"`).
   3. **Lines 867-916:** Adds `"--dest"` (`default=".archive"`), `"--imaging-root"` (`default=None`), `"--project-dir"` (`default=None`), `"--all"`, `"--force"`, `"--dry-run"`, `"--checksum"`, `"--no-image-metadata"`, and `"--no-weather"` (`dest="weather"`, `action="store_false"`, `default=True`).
   4. **Line 917:** Returns `parser`.

5. **Side Effects & I/O**
   - None.

6. **Error Handling & Edge Cases**
   - Note `--no-weather` stores into `args.weather` with default `True`; `run` accesses `getattr(args, "weather", True)` (`archive.py:987`) so synthetic `argparse.Namespace` objects in unit tests that omit `weather` still default to `True`.

7. **Complexity & Risk Notes**
   - $O(1)$.

---

### 3.18 `_resolve_root` (`archive.py:920-921`)

1. **Purpose & Architectural Role**
   Thin async wrapper that resolves the N.I.N.A. imaging root directory from `args.imaging_root` (if passed) or from N.I.N.A.'s active site profile via `metadata.resolve_cli_root`, binding `ArchiveError` as the exception type.

2. **Signature, Decorators & Inputs**
   ```python
   async def _resolve_root(args: argparse.Namespace) -> Path:
   ```
   - **Decorators:** None (`async def`).
   - **Parameters:** `args: argparse.Namespace` — Must have `.imaging_root: str | None`.

3. **Outputs & Return Semantics**
   - **Returns:** `Path` — Validated, existing imaging root directory.
   - **Raises:** `ArchiveError` if `args.imaging_root` is invalid or N.I.N.A. is unreachable when no `--imaging-root` override was provided.

4. **Step-by-Step Internal Logic**
   1. **Line 921:** `return await metadata.resolve_cli_root(args.imaging_root, ArchiveError, "archive")`.

5. **Side Effects & I/O**
   - May perform an HTTP GET to N.I.N.A.'s profile endpoint if `args.imaging_root is None`; stats the resolved directory on disk.

6. **Error Handling & Edge Cases**
   - Caught in `run` (`archive.py:966-970`), which prints `✗ {exc}` to `sys.stderr` and returns exit code `1`.

7. **Complexity & Risk Notes**
   - $O(1)$ local path check or single HTTP call.

---

### 3.19 `run` (`archive.py:924-1188`)

1. **Purpose & Architectural Role**
   Top-level async orchestrator for `nina-archive`. Resolves paths, enforces that `--dest` is outside `--imaging-root`, reads all imaging and weather CSVs in a single pass, executes the three-pass archival pipeline (**Pass 1:** load plans; **Pass 2:** compute cross-plan selections and `shipped` union; **Pass 3:** gate completeness, divert own-token junk, build disk report, and dry-run or commit each archive), prints the summary, and computes the exit code.

2. **Signature, Decorators & Inputs**
   ```python
   async def run(args: argparse.Namespace) -> int:
   ```
   - **Decorators:** None (`async def`).
   - **Parameters:** `args: argparse.Namespace` — Parsed CLI arguments from `_build_parser()`.

3. **Outputs & Return Semantics**
   - **Returns:** `int` — `0` on success (at least one plan archived or already archived, and zero errors), `1` on any fatal pre-check failure, per-plan error, or when nothing was archivable (`not archived and already == 0`).

4. **Step-by-Step Internal Logic**
   1. **Lines 931-939 (Path Setup):** Resolves `project_dir` (anchoring relative paths to `config.PROJECT_DIR`), resolves `dest` (anchoring relative paths to `Path.cwd()`), and records `explicit = bool(args.plans)`.
   2. **Lines 941-964 (Plan File Discovery):**
      - If `explicit`: resolves each entry in `args.plans` relative to `project_dir`; if any `not path.is_file()`, prints `✗ plan file not found: {path}` to `stderr` and returns `1`.
      - Else: discovers `[p for p in plan_utils.plan_files(project_dir) if not _inside(p, dest)]`.
      - If `not plan_files:`, prints `no plan files found under {project_dir}` to `stderr` and returns `1`.
   3. **Lines 966-979 (Imaging Root & Containment Check):** Awaits `imaging_root = await _resolve_root(args)` (returning `1` on `ArchiveError`). Checks `if _inside(dest, imaging_root):` and returns `1` if `--dest` is inside `--imaging-root`.
   4. **Lines 981-994 (Single-Pass CSV + Weather Read):** Calls `rows, dropped_rows = read_imaging_csv(root=imaging_root, image_type=None, report_dropped=True)`. If `getattr(args, "weather", True):`, merges weather into `LIGHT` rows via `_merge_weather` and splices them back into `rows`. Catches `FileNotFoundError` and returns `1`.
   5. **Lines 996-1000:** Sets `now = datetime.now(UTC).replace(microsecond=0)` and initializes `archived = []`, `skipped = []`, `errors = []`, `ignored = 0`.
   6. **Lines 1002-1018 (PASS 1 — Load All Plan Files):** Awaits `plan_utils.load_plan(str(plan_file))` for each file. On exception, appends `(plan_file, str(exc).splitlines()[0])` to `errors` if `explicit or _looks_like_plan_file(plan_file)` else increments `ignored += 1`.
   7. **Lines 1020-1038 (PASS 2 — Cross-Plan Selection & `shipped` Union):** For every `(loaded_file, loaded_plan)` in `loaded` (including incomplete plans!), computes `selected = _without_degenerate(loaded_plan, _select_frames(loaded_plan, rows, now))`, appends `selected` to `selections`, and adds all selected `file_path` strings to `shipped: set[str]`.
   8. **Lines 1040-1163 (PASS 3 — Gate, Divert, Report, Copy):** Iterates `for (plan_file, plan), selected in zip(loaded, selections, strict=True):`:
      - Calls `complete, counts = _progress_facts(plan, rows, now)`. If `not complete and not args.all:`, formats `deficit` string, appends to `skipped`, and continues (`1042-1052`).
      - Calls `divert = _divert_frames(plan, rows, shipped)` and `disk = disk_report(plan, rows, dropped_rows, selected, divert)` (`1056-1059`).
      - Computes `file_count = sum(len(v) for v in selected.values())` and calls `folder = _folder_name(plan_file, plan)` (catching `ArchiveError` into `errors`, `1060-1068`).
      - **Dry-Run Branch (`1070-1104`):** Sums byte sizes of existing selected + diverted files, prints `🔍 would archive ...`, appends summary dict to `archived`, and continues.
      - **Live Branch (`1106-1163`):**
        - Calls `_recover_replaced(dest / folder)` (catching `OSError` into `errors`, `1110-1114`).
        - If `(dest / folder).exists() and not args.force:`, appends `"already archived at ..."` to `skipped` and continues (`1116-1118`).
        - If `file_count == 0:`, appends `"no frames on disk match this plan"` to `errors` and continues (`1120-1122`).
        - Calls `_archive_one(...)` (catching `ArchiveError` into `errors`, `1124-1140`).
        - Appends `record` to `archived` and prints `📦`, plus optional `🗑` (junk), `⚠` (missing), and `💽` (rejected/gone) lines (`1141-1162`).
   9. **Lines 1164-1188 (Summary & Exit Code):** Counts `already = sum(1 for _, r in skipped if r.startswith("already archived"))`, prints dry-run or live summary header, prints `⏭ ` lines for `skipped` and `✗` lines to `stderr` for `errors`. Returns `1` if `errors` or (`not archived and already == 0`), else `0`.

   ```mermaid
   flowchart TD
       START["run(args)"] --> INIT["Resolve project_dir &amp; dest<br/>Discover plan_files (explicit or plan_utils.plan_files excluding dest)<br/>Resolve imaging_root &amp; verify not _inside(dest, imaging_root)<br/>(lines 931-979)"]
       INIT -->|Validation Failure| EXIT1_EARLY["Print ✗ to stderr &amp; return 1"]
       INIT -->|Valid| CSV["read_imaging_csv(root=imaging_root, report_dropped=True)<br/>If args.weather: _merge_weather(LIGHT rows)<br/>(lines 984-994)"]
       CSV --> PASS1["PASS 1: Load every plan_file via load_plan()<br/>Explicit or _looks_like_plan_file failures -> errors[]<br/>Unrelated broken JSON -> ignored += 1 (lines 1005-1018)"]
       PASS1 --> PASS2["PASS 2: For every loaded plan (even incomplete):<br/>selected = _without_degenerate(_select_frames())<br/>Add all selected file_paths to global shipped set<br/>(lines 1026-1038)"]
       PASS2 --> PASS3["PASS 3: Loop over (plan_file, plan), selected (line 1041)"]
       PASS3 --> GATE{"complete or args.all?<br/>(line 1043)"}
       GATE -->|No| SKIP_INC["Append 'incomplete (...)' to skipped[] (lines 1044-1052)"]
       GATE -->|Yes| DIV["divert = _divert_frames(plan, rows, shipped)<br/>disk = disk_report(plan, rows, dropped_rows, selected, divert)<br/>folder = _folder_name(plan_file, plan) (lines 1056-1068)"]
       DIV --> DRY{"args.dry_run?<br/>(line 1070)"}
       DRY -->|Yes| DRY_LOG["Stat existing files, print 🔍 would archive<br/>Append to archived[] (lines 1071-1104)"]
       DRY -->|No| REC["_recover_replaced(dest / folder) (line 1111)"]
       REC --> EXISTS{"(dest/folder).exists()<br/>and not args.force?<br/>(line 1116)"}
       EXISTS -->|Yes| SKIP_EX["Append 'already archived at ...' to skipped[] (line 1117)"]
       EXISTS -->|No| ZERO{"file_count == 0?<br/>(line 1120)"}
       ZERO -->|Yes| ERR_ZERO["Append 'no frames on disk match this plan' to errors[] (line 1121)"]
       ZERO -->|No| ARCH1["record = _archive_one(...)<br/>Append to archived[] &amp; print 📦 / 🗑 / ⚠ / 💽 (lines 1125-1162)"]
       SKIP_INC & DRY_LOG & SKIP_EX & ERR_ZERO & ARCH1 --> SUMMARY["Print Summary + skipped[] + errors[]<br/>Return 1 if errors or (not archived and already == 0) else 0<br/>(lines 1165-1188)"]
   ```

5. **Side Effects & I/O**
   - Reads N.I.N.A. profile (optional), walks imaging root CSVs once, reads plan JSONs, writes archive directories and `archive-index.jsonl` (unless `--dry-run`), and writes progress/summary output to `stdout`/`stderr`.

6. **Error Handling & Edge Cases**
   - **Fault Isolation Across Plans:** Once initial pre-checks (imaging root, containment, CSV read) succeed, any failure in Pass 1 or Pass 3 is isolated to that specific plan and recorded in `errors`, allowing all other valid complete plans in the batch to finish archiving before `run` exits `1`.

7. **Complexity & Risk Notes**
   - Single walk over the imaging root (`read_imaging_csv`), followed by $O(L \cdot P \cdot R)$ in-memory matching across $L$ loaded plans, $P$ pointings, and $R$ CSV rows, plus $O(\sum \text{bytes})$ disk copy I/O.

---

### 3.20 `main` (`archive.py:1191-1200`)

1. **Purpose & Architectural Role**
   Synchronous CLI entrypoint registered as `nina-archive` in `pyproject.toml:60` and invoked by `if __name__ == "__main__":` (`archive.py:1203-1204`).

2. **Signature, Decorators & Inputs**
   ```python
   def main(argv: list[str] | None = None) -> int:
   ```
   - **Decorators:** None.
   - **Parameters:** `argv: list[str] | None = None` — Optional CLI argument list; defaults to `sys.argv[1:]`.

3. **Outputs & Return Semantics**
   - **Returns:** `int` — Process exit code (`0` or `1` from `run`, or `SystemExit(2)` from `argparse`).

4. **Step-by-Step Internal Logic**
   1. **Line 1199:** Parses CLI arguments via `args = _build_parser().parse_args(argv)`.
   2. **Line 1200:** Runs `return asyncio.run(run(args))`.

5. **Side Effects & I/O**
   - Boots `asyncio` event loop and executes `run(args)`.

6. **Error Handling & Edge Cases**
   - Standard `argparse` exit `2` on invalid arguments.

7. **Complexity & Risk Notes**
   - $O(1)$ wrapper around `run`.
