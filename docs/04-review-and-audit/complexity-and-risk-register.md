# Complexity and Risk Register

[⬆️ Up to Atlas Index](../README.md) · [➡️ Verification Audit Report](./verification-audit-report.md) · [➡️ Codebase Review Guide](./codebase-review-guide.md)

---

## Executive Risk Summary & Severity Matrix

This register catalogs structural complexity hotspots, asynchronous I/O hazards, hidden state-coupling vectors, hardware/data-loss safety gates, and edge-case/test-coverage gaps across `.opencode/local/mcps/nina_planner/nina_planner/`, `scripts/`, and `.agents/skills/site-conditions/fetch_conditions.py`.

| ID | Severity | Category | Module & Location (`file:line`) | Risk / Finding Summary | Mitigation Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **RISK-01** | **High** | Safety / Hardware | `tools/equipment.py:129-141` (`_get_site_equipment_status`) | Read-seeming status tool mutates hardware state by issuing `GET /equipment/<dev>/connect` for every configured disconnected device, then raising `RuntimeError("Equipment connecting: ...")`. | **Documented / By Design** (Tested in `test_server.py`, `test_sequence.py`; callers must handle transient error). |
| **RISK-02** | **High** | Safety / Hardware | `tools/sequence.py:419-423` (`stop_sequence`) & `client.py:166-182` | `stop_sequence` halts the running N.I.N.A. sequence in place without parking the mount or stopping tracking; interrupting a teardown mid-slew leaves the telescope unparked at an arbitrary pointing. | **Mitigated by Protocol** (`safety-interrupt-recovery` skill and docstring mandate loading `load_sequence()` (`[]` = park) + `start_sequence()`). |
| **RISK-03** | **High** | Safety / Data Loss | `purge.py:174-219`, `302-409`, `534-698`, `761-1077` | Irreversible deletion of FITS frames and plan JSONs in `nina-purge --purge` if archive manifests or shared calibration claims are misclassified. | **Mitigated in Code** (2-phase `.quarantine/` staging, 3-gate byte-identical `plan.json` check, live sequence running refusal, shared calibration pool holding). |
| **RISK-04** | **High** | Complexity / Logic | `tools/plans.py:759-1165` (`audit_plans`) & `545-599` (`_status_for`) | 407-line reconciliation engine joining `ImageMetaData.csv`, loadable/corrupt plan JSONs, and on-disk `BAD_` FITS files across 7 precedence-ordered status verdicts. | **Mitigated by Tests** (Comprehensive coverage in `tests/test_plans_audit.py`; strict first-match-wins ordering in `_status_for`). |
| **RISK-05** | **Medium** | Async / Event Loop | `system_time.py:77-112` (`get_launcher_paths`) & `133-144` (`write_nina_launcher`) | Synchronous `subprocess.run(["cmd.exe", "/d", "/c", "echo", "%TEMP%"], timeout=10)` and synchronous file writes run directly on the asyncio event loop inside `start_nina` (`tools/lifecycle.py:98`). | **Unmitigated in Code** (Blocks event loop up to 10s on WSL if `cmd.exe` hangs; mitigated only by rare invocation). |
| **RISK-06** | **Medium** | Async / Event Loop | `client.py:42-59` (`log_payload`) | Synchronous `mkdir` + file open/append executed on every `api_get`, `api_post`, and major MCP tool response on the main event loop thread. | **Partially Mitigated** (`OSError` swallowed; local `/tmp` fast path on POSIX, but can stall if `NINA_PLANNER_LOG` targets a network share). |
| **RISK-07** | **Medium** | Async / Event Loop | `plan_utils.py:235-255` (`plan_files`), `tools/plans.py:362-376` (`_probe_plan_id`), `993-1019` | Synchronous `os.walk` (`_walk_visible`), `Path.read_text()`, `Path.stat()`, and `root.iterdir()` executed directly inside `async def` MCP tools (`audit_plans`, `list_plans`). | **Partially Mitigated** (`_walk_visible` prunes hidden dirs, `node_modules`, `__pycache__`, and does not follow symlinks). |
| **RISK-08** | **Medium** | Async / Latency | `tools/equipment.py:129-135` (`_get_site_equipment_status`) | Sequential `await client.api_get(path + "/connect")` inside a `for` loop across up to 11 devices; each call has a 10s HTTP timeout (`client.py:130`). | **Unmitigated in Code** (Worst-case 110s serial stall when multiple devices fail/hang on connect). |
| **RISK-09** | **Medium** | Coupling / Internals | `tools/plans.py:314-341` (`_forbid_unknown_arguments`) | Accesses FastMCP private internals (`mcp._tool_manager.get_tool(...).fn_metadata.arg_model`) at import time to inject `ConfigDict(extra="forbid")`. | **Fail-Loud Guard** (Raises `RuntimeError` at import time if FastMCP internal structure changes; covered by `test_progress.py`). |
| **RISK-10** | **Medium** | State Mutation | `models/camera.py:125-220`, `mount.py:33-77`, `guider.py:36-69`, `dome.py:28-49`, `rotator.py:25-51`, `focuser.py:28-40`, `filter_wheel.py:22-29`, `weather.py:33-48` | `@model_validator(mode="before")` methods mutate the caller-supplied `data: dict` in place rather than copying it (`FrameStats._normalize` in `models/frame.py:92-109` is the only model that builds a new dict). | **Low Impact in Prod / Hazard in Tests** (Production `_build` passes a fresh dict from `_convert_keys`, but direct callers reusing fixture dicts see mutated keys). |
| **RISK-11** | **Medium** | State Mutation | `sequence/nodes.py:17-36` (`_fix_provider`) & `39-84` (`_add_refs`) | Recursive AST passes mutate nested sequence node dicts in place, rewriting `$id`, `Parent` key ordering, and replacing duplicate `SelectedProvider` dicts with `{"$ref": ...}`. | **Mitigated by Fresh Builders** (Builders construct fresh node dicts per call; re-running `_add_refs` on an already-mutated tree is not done). |
| **RISK-12** | **Medium** | Edge Case / Semantics | `tools/plans.py:255-282` (`get_plan_progress`) & `tools/sequence.py:66-113` (`_full_mode_note`) | `acquired_count` is cumulative across all runs sharing a `plan_id`. A truncated or failed `mode="full"` re-shoot still reports `complete: true` because prior frames satisfy `total_count`. | **Mitigated by Warning & `overshot` Flag** (`get_plan_progress` sets `overshot: true` and `warning`; `_full_mode_note` warns at `load_sequence` time). |
| **RISK-13** | **Low / Advisory** | Import-Time Config | `config.py:10-19` & `sequence/nodes.py:12-14` | `NINA_ENDPOINT`, `NINA_API_URL`, `PROJECT_DIR`, and `NINA_FLATS_*` are evaluated once at module import time; post-import `os.environ` changes have no effect on those constants. | **Documented** (Tests monkeypatch module attributes directly; `metadata.py` and `system_time.py` read env vars dynamically). |
| **RISK-14** | **Low / Advisory** | Edge Case / Sentinel | `progress.py:172-217` (`_quality_accepted`) vs. `models/frame.py:53-109` (`FrameStats`) | Dual `0` vs `NaN` semantics: `ImageMetaData.csv` writes `0` for unmeasured HFR/RMS/stars (rejected as invalid when quality filters are active), whereas `/image-history` (`FrameStats`) uses `NaN -> None` for unmeasured and preserves `0` as a genuine reading (`stars: 0`). | **Documented & Tested** (Explicitly documented in `FrameStats` and `get_frame_stats`; tested in `test_frame.py` and `test_progress.py`). |
| **RISK-15** | **Low / Advisory** | Timezone / Naive UTC | `progress.py:220-297`, `545-622` vs. `nina_utils.py:74-123` & `tools/equipment.py:30-79` | Split datetime convention: `progress.py` strips `tzinfo` and computes calibration windows in **naive UTC**, whereas `to_local_display()` and `_convert_to_met()` treat naive datetimes as **host-local**. | **Mitigated at Boundary** (`progress.py:620` explicitly re-attaches `tzinfo=UTC` via `window_end.replace(tzinfo=UTC)` before calling `to_local_display`). |
| **RISK-16** | **Low / Advisory** | Test Coverage | `.agents/skills/site-conditions/fetch_conditions.py`, `scripts/update_readme.py`, `scripts/refresh_fixtures.py` | Zero automated pytest unit tests in `.opencode/local/mcps/nina_planner/tests/` for the 676-line HTML forecast parser (`fetch_conditions.py`) and two repository maintenance scripts. | **Unmitigated** (`scripts/gen_sequence.py` has `tests/test_gen_sequence.py`, but `fetch_conditions.py` relies on manual CLI verification). |

---

## Category 1: High-Complexity Functions & Cyclomatic Hotspots

The following table ranks the highest-complexity functions across the codebase by line span, branching density (`if`/`elif`/`for`/`while`/`try`/`except` + comprehension predicates), and state-tracking dimensionality.

| Rank | Function | Location (`file:line`) | Span (Lines) | Branch / Loop Constructs | Primary Complexity Driver |
| :---: | :--- | :--- | :---: | :---: | :--- |
| **1** | `audit_plans` (+ `_status_for`) | `nina_planner/tools/plans.py:759-1165` (`545-599`) | 407 (+55) | ~68 branches/loops | 3-way reconciliation (Plan JSONs × `ImageMetaData.csv` × disk `BAD_` FITS & orphan folders) across explicit vs. discovery modes. |
| **2** | `run` (+ `_classify`, `_quarantine_gates`, `_sweep_plan_files`) | `nina_planner/purge.py:761-1077` (`302-409`, `534-609`, `612-698`) | 317 (+270) | ~74 branches/loops | Multi-plan claim inversion, 3-gate archive verification, 2-phase `.quarantine` vs. `--purge` state machine, and plan-file unlinking. |
| **3** | `_archive_one` | `nina_planner/archive.py:542-836` | 295 | ~38 branches/loops | Transactional `.partial` staging, filename deduplication, `junk/` diversion, sidecar CSV generation, and `.replaced` atomic rollback swap. |
| **4** | `run` (`nina-archive`) | `nina_planner/archive.py:924-1188` | 265 | ~42 branches/loops | 3-pass pipeline (load plans → cross-plan `shipped` union → completeness gate, dry-run/force recovery, copy & summary). |
| **5** | `_load_many` | `nina_planner/tools/sequence.py:176-416` | 241 | ~34 branches/loops | Lazy async memoization (`state` dict), per-step capability/filter/PA/variant validation, `remaining` vs. `full` rewriting, and park/plugin guards. |
| **6** | `parse` | `.agents/skills/site-conditions/fetch_conditions.py:338-507` | 170 | ~36 branches/loops | Multi-regex HTML scraping across 7 day blocks, midnight-wrap calendar date anchoring, cross-block astro-dark lookup, and dewpoint spread derivation. |
| **7** | `plan_progress` | `nina_planner/progress.py:478-631` | 154 | ~20 branches/loops | First-fit occurrence-wise frame claiming (`id(row)` counter), session-anchored calibration windowing, and dual `remaining_count` vs. `deficit_count` math. |
| **8** | `target_payload_lights` | `nina_planner/sequence/targets.py:264-415` | 152 | ~12 branches/loops | Deeply nested N.I.N.A. + Sequencer+ AST generation: dusk wait, meridian/horizon break-if-in-view wait, countdown array priming, and `Image Object` safetynet. |

### Detailed Hotspot Analysis

#### 1. `tools/plans.py:audit_plans` (`759-1165`, 407 lines) & `_status_for` (`545-599`, 55 lines)
- **Why It Is Complex:**
  - **Dual Execution Modes (`811-931`):** In `explicit` mode (`plans` list provided), every named plan is audited even if unshot, and any load error is surfaced immediately (`815-822`). In discovery mode (`823-931`), `audit_plans` performs a **LEFT JOIN** driven by `ImageMetaData.csv`:
    1. For every `LIGHT` row in `path_rows`, it scans `all_loaded` across all `pointing_index` values (`846-864`) to determine which plans are `referenced` (`865`) vs. `unreferenced` (`873-875`, deduplicated by `_base_plan_id()` so rewritten twin files do not false-trigger `never_shot`).
    2. For broken JSON files in `load_errors` (`884-900`), it calls `_probe_plan_id` (`885`) and only promotes a broken file to `errors` if no valid twin provides that `plan_id` **and** `ImageMetaData.csv` actually references frames with that `plan_id`.
    3. For unclaimed `LIGHT` rows (`906-930`), it extracts conservative candidate tokens via `_plan_ids_in_path` (`_PLAN_ID_CANDIDATE_RE`, `359`) and groups orphaned CSV light rows into `gaps`.
  - **Filesystem Token Matching (`976-1019`):** Builds `known` tokens for both per-pointing (`_{effective_plan_id}_`) and base (`_{base}_`) IDs, sorts them longest-first (`989`) so mosaic pane directories (`_plan-abc-1_`) match before base tokens (`_plan-abc_`), and walks `root.iterdir()` (`993`) to count `BAD_` rejected FITS files (`_count_fits`, `1016`) and identify `unclaimed` orphan directories (`1001-1010`).
  - **7-Way Precedence Verdict in `_status_for` (`545-599`):** Evaluates strict first-match-wins rules across 7 parameters (`image_type`, `expected`, `own_rows`, `attributed`, `remaining`, `window_open`, `junk`):
    1. `EXPIRED` (`572-583`): `is_calibration` and `group_verdict(...) == "expired_with_deficit"`.
    2. `NEVER_SHOT` (`584-588`): `not is_calibration and expected > 0 and attributed == 0`.
    3. `MISSING_FILES` (`589-590`): `remaining > 0`.
    4. `SUSPECT_JUNK` (`591-592`): `junk > 0` (when `remaining == 0`, e.g., degenerate `<0.01s` or `<0.5 * min_exposure` frames riding along).
    5. `POOL_CREDITED` (`593-596`): `is_calibration and attributed >= expected and own_rows < expected`.
    6. `OVERSHOT` (`597-598`): `not is_calibration and attributed > expected`.
    7. `OK` (`599`).

#### 2. `archive.py:_archive_one` (`542-836`, 295 lines) & `archive.py:run` (`924-1188`, 265 lines)
- **Why `_archive_one` Is Complex:**
  - Manages a crash-safe filesystem state machine using three sibling paths: `final` (`dest / folder`), `staging` (`dest / .<folder>.partial`), and `previous` (`dest / .<folder>.replaced`).
  - Inner closure `copy_one` (`592-626`) handles mid-run vanishing source files (`610-612`), deduplicates colliding filenames from different date folders (`stem_dup{N}.fits.fz`, `614-621`), verifies byte size and optional SHA-256 (`_copy_file`, `531-539`), and populates `rel_map` so sidecar `ImageMetaData.csv` files (`659-664`, `704-707`) only include rows that actually shipped.
  - Copies both the 4 standard frame types (`629-664`) and diverted `JUNK` frames (`670-707`), refuses to commit if `copied == 0` even when `junk_copied > 0` (`709-718`), synthesizes 5 distinct warning classes (`727-764`), and executes the two-step atomic rename with rollback on `BaseException` (`798-810`).
- **Why `archive.py:run` Is Complex:**
  - Requires a **3-pass architecture** (`1002-1163`):
    - **Pass 1 (`1005-1018`):** Loads all candidate plans; in discovery mode, uses `_looks_like_plan_file` (`150-163`) to distinguish corrupt plan JSONs (recorded in `errors`) from unrelated JSON files like `opencode.json` (counted in `ignored`).
    - **Pass 2 (`1026-1038`):** Computes `_without_degenerate(loaded_plan, _select_frames(...))` across **all** loaded plans (including incomplete ones that Pass 3 will skip) to build the global `shipped` set. Without this global pass, `_divert_frames` in Pass 3 would misclassify a shared calibration frame claimed by an unarchived plan as `uncredited` junk.
    - **Pass 3 (`1041-1163`):** Applies completeness gates, handles `--dry-run`, reconciles interrupted `.replaced` swaps (`1111`), and invokes `_archive_one`.

#### 3. `purge.py:run` (`761-1077`, 317 lines), `_classify` (`302-409`), `_quarantine_gates` (`534-609`), `_sweep_plan_files` (`612-698`)
- **Why It Is Complex:**
  - `purge.py` implements an inverted reference-counting garbage collector over the imaging root:
    $$\text{prunable}(F) \iff \text{preserved}(F) \land \forall P \in \text{claims}(F),\; \text{released}(P)$$
  - **`_classify` (`302-409`, 108 lines):** Maps every CSV row and `BAD_` reject twin to its top-level directory under `root` (`bucket_for`, `324-328`), unions with physical directories on disk (`on_disk`, `348-350`), and computes a directory-level **logical AND** verdict (`prune` vs. `hold`). A single unpreserved or unreleased frame holds the entire directory (`367-379`); a directory with zero recorded CSV rows is also held (`395-397`) because unrecorded files cannot be proven preserved.
  - **`_quarantine_gates` (`534-609`, 76 lines):** Reconstructs historical directory-to-plan claims by parsing `<root>/purge-index.jsonl` (`558-568`) and re-verifies every claiming `plan_id` against current plan-file gates (`stale_ids`, `gate1_ids`, `by_plan`) before allowing `_purge_quarantine` to `shutil.rmtree` anything in `.quarantine/`.
  - **`_sweep_plan_files` (`612-698`, 87 lines):** Ensures a project plan JSON is only unlinked (`689`) as the **final step** after verifying its archive gates pass, its `plan_id` is not in `held_claimants`, and every one of its `selected` frame paths is either already gone from disk or inside a directory with `verdict == "prune"`.

#### 4. `tools/sequence.py:_load_many` (`176-416`, 241 lines)
- **Why It Is Complex:**
  - Uses 4 lazy async closures (`profile()`, `equipment()`, `rows()`, `now()`, `191-218`) backed by a shared `state` dict so a batch of `SequenceRequest` items fetches N.I.N.A. profile, equipment status, and `ImageMetaData.csv` at most once—and never touches `ImageMetaData.csv` if all requests use `mode="full"`, or never touches equipment if all requests are already complete in `mode="remaining"`.
  - Inner `step(request)` (`220-360`) enforces strict ordering of 8 validation and transformation stages per request: action/plan presence (`230`), calibration `pointing_index` refusal (`240`), filter & PA validation (`252-253`), dark/bias hardware capability check (`validate_dark_variant`, `265`), light hardware capability check (`require_light_capabilities`, `274`), `remaining` vs. `full` frame calculation (`276-330`) including `expired_with_deficit` detection (`291-305`), `$$TARGETNAME$$` pattern enforcement (`332`), and payload dispatch (`340-359`).
  - Post-loop guards (`380-400`) handle the empty-request `park` special case (`request=[]`), enforce `plan_utils.require_parkable(eq)` (`389`), and conditionally probe `/application/plugins` via `_require_sequencer_plus()` (`392-398`) only when a non-empty `light` or `flat` payload is actually being sent.

#### 5. `progress.py:plan_progress` (`478-631`, 154 lines)
- **Why It Is Complex:**
  - Must prevent double-crediting when a plan contains overlapping exposure groups (e.g., two `L` light groups with identical exposure, or multiple flat groups on the same filter).
  - Implements an occurrence-wise first-fit pool (`unclaimed`, `560-599`): for each group `g`, it finds `matched = _select_matching(unclaimed, ...)` (`567`), credits `acquired = len(matched)`, and removes at most `total` matched rows from `unclaimed` using an `id(row)` reference counter (`claim: dict[int, int]`, `587-598`) so even duplicate object references in unit tests (`[row] * n`) are decremented accurately without mutating the caller's `rows` list.
  - Splits shortfall reporting into two distinct fields (`600-623`): `deficit_count = max(total - acquired, 0)` (raw shortfall, never zeroed by window closure) and `remaining_count` (zeroed when `not window_open`, `621-622`, so `load_sequence` does not shoot uncreditable calibration frames after the window closes).

#### 6. `sequence/targets.py:target_payload_lights` (`264-415`, 152 lines)
- **Why It Is Complex:**
  - Declaratively constructs a deeply nested 5-level N.I.N.A. + Sequencer+ execution tree that must survive mid-night safety interrupts, meridian flips, and target horizon rises without losing exposure progress:
    1. `sequence_wait_until_dusk(equipment)` (`299`).
    2. `Wait For Object` (`301-330`): a sequential container bounded by `loop_until_dawn()` and `loop_until_meridian()`, containing an inner `Break If in View` container (`309-320`) that executes `end_instruction("Wait For Object")` immediately if the target is already above both horizon and `min_altitude`, followed by `sequence_park_scope` + `wait_until_above_horizon` + `wait_until_above_altitude` if out of view.
    3. Sequencer+ countdown initialization (`334-339`): `declare_variable("n", "0")`, `initialize_array("'count'")`, and `put_array("'count'", index, str(count))` for each round-robin exposure group.
    4. `Image Object` safetynet (`341-412`): attaches 3 loop conditions and 7 triggers (meridian flip, drift recenter, 4 autofocus triggers, restore guiding), unparks, homes (`sequence_home_scope`), sets sidereal tracking (`set_tracking(0)`), switches to the reference filter, slews/centers (with or without rotation via `pointing.requests_rotation()`), runs initial autofocus, starts guiding, iterates through `sequence_count_exposure` groups (each decrementing `'count'[index]`), and stops guiding.

#### 7. `.agents/skills/site-conditions/fetch_conditions.py:parse` (`338-507`, 170 lines)
- **Why It Is Complex:**
  - Scrapes Clear Outside's HTML via 15 compiled regular expressions (`34-100`) without an HTML DOM parser (stdlib-only constraint).
  - Must solve the **rolling-hour / midnight-wrap alignment problem**: under `--view current`, a day block's 24 columns start at the generation hour (e.g. `02..01`) rather than `00..23`. `parse()` anchors block 0 to a calendar `date` via `RE_FORECAST_RANGE` (`365-373`), cross-checks `block_date.day != int(label_dom)` (`425-435`, degrading `block_date` to `None` on mismatch rather than guessing), builds date-keyed `starts` and `ends` maps across all blocks (`459-460`), and then resolves each hourly column's `col_date` (`478-480`, advancing `day0 + timedelta(days=1)` when `h < first`) so `_is_dark(h, col_date, starts, ends)` (`181-212`) can check both tonight's dusk (`>= start_D`) and last night's dawn (`<= end_{D-1}`).

---

## Category 2: Async / Event-Loop & Blocking I/O Risks

The `nina-planner` MCP server runs on a single-threaded `asyncio` / `anyio` event loop. While `plan_utils.load_plan` (`plan_utils.py:273`) and `plan_utils.write_text_atomic` (`tools/plans.py:196-198`) properly use `anyio.open_file` and `anyio.to_thread.run_sync`, several synchronous filesystem, subprocess, and sequential network operations execute directly on the event loop thread.

### 2.1 Synchronous Filesystem & Subprocess Calls on the Event Loop

| Location (`file:line`) | Function | Synchronous Operation | Async Callers Affected | Risk & Worst-Case Impact |
| :--- | :--- | :--- | :--- | :--- |
| `system_time.py:88-94` & `142-143` | `get_launcher_paths()` / `write_nina_launcher()` | `subprocess.run(["cmd.exe", "/d", "/c", "echo", "%TEMP%"], timeout=10)` and `local_path.write_text(...)` | `tools/lifecycle.py:98` (`start_nina`) | **Medium:** Unlike `schtasks.exe` and `tasklist.exe` which use `await client.run_blocking(...)` (`anyio.to_thread.run_sync`), `write_nina_launcher()` is called directly on the event loop in `start_nina`. On WSL, a slow or wedged Windows interop `cmd.exe` blocks the entire MCP server for up to **10 seconds**. |
| `client.py:42-59` | `log_payload()` | `path.parent.mkdir(...)` and `with path.open("a", encoding="utf-8") as f: f.write(...)` | `client.api_get` (`133`), `client.api_post` (`153`), and almost all MCP tools (`tools/*.py`) | **Medium:** Invoked synchronously on every REST API call and tool return when `NINA_PLANNER_LOG` is set. Large JSON payloads (`get_sequence_state`, `audit_plans`) are serialized with `json.dumps(..., indent=2)` and appended on the main loop thread. |
| `plan_utils.py:235-255` | `plan_files(plan_dir)` | `_walk_visible(plan_dir)` (`os.walk` + `os.scandir` in `imaging.py:55-76`) | `tools/plans.py:393` (`_load_project_plans`), `823` (`audit_plans`), `1183` (`list_plans`) | **Medium:** Walks the project directory tree synchronously inside `async def` tools. Mitigated by `_prunable_dir` skipping hidden directories (`.git`, `.venv`, `.archive`, `.quarantine`), `node_modules`, and `__pycache__`. |
| `tools/plans.py:362-376` & `673-682` | `_probe_plan_id(path)` & `_written_iso(path)` | `path.read_text(encoding="utf-8")` and `path.stat().st_mtime` | `tools/plans.py:885` (`audit_plans`), `737` (`_never_shot_report`), `1194` (`list_plans`) | **Low-Medium:** Synchronous file reads/stats during plan sweeps on the event loop. |
| `tools/plans.py:993-1018` & `1125-1134` | `audit_plans` & `_count_fits` (`444-472`) | `root.iterdir()`, `_walk_visible(entry)` | `tools/plans.py:760` (`audit_plans`) | **Medium:** Note that `metadata.read_metadata()` (`metadata.py:95-100`) offloads `read_imaging_csv` via `await anyio.to_thread.run_sync(...)`, **but** `audit_plans` then walks `root.iterdir()` and `_count_fits(entry)` **synchronously** on the event loop (`993-1018`, `1126`). If `root` is a slow SMB/CIFS mount (`NINA_DRIVE_MOUNT`), this directory walk stalls the event loop. |
| `system_time.py:54` & `74` | `_is_wsl()` & `windows_interop_available()` | `open("/proc/sys/kernel/osrelease").read()` and `shutil.which(exe)` × 3 | `tools/lifecycle.py:58, 86, 134` (`get_nina_status`, `start_nina`, `stop_nina`) | **Low:** Fast in-memory `/proc` and `PATH` lookups, though uncached across calls. |

### 2.2 Sequential Network I/O in `_get_site_equipment_status`
- **Location:** `nina_planner/tools/equipment.py:129-135`
  ```python
  for key, (_, path) in DEVICE_MAP.items():
      if not EXISTS[key]:
          continue
      data = raw.get(key, {})
      if data and not data.get("Connected"):
          await client.api_get(path + "/connect")
          devices_connecting.append(key)
  ```
- **Risk:** `DEVICE_MAP` contains 11 devices (`Mount`, `Camera`, `Focuser`, `Guider`, `SafetyMonitor`, `WeatherData`, `Dome`, `FilterWheel`, `FlatDevice`, `Rotator`, `Switch`). When multiple configured devices are disconnected at startup, `_get_site_equipment_status` issues `GET /equipment/<device>/connect` **sequentially** rather than concurrently, and each `client.api_get` creates a new `httpx.AsyncClient(timeout=10.0)` (`client.py:130`). If several ASCOM drivers take several seconds each to acknowledge `/connect`, the status call compounds their latencies serially before raising `RuntimeError("Equipment connecting: ...")` (`137-141`).

---

## Category 3: In-Place Mutation & Hidden Coupling Hazards

### 3.1 Pydantic `@model_validator(mode="before")` In-Place Dict Mutation
Across 8 of the 9 equipment/telemetry models in `nina_planner/models/`, the `@model_validator(mode="before")` classmethod `_derive_fields(cls, data: dict) -> dict` mutates the caller's `data` dictionary **in place** before returning it:

| Model Class | Location (`file:line`) | Keys Mutated / Injected In-Place on Caller's `data` Dict |
| :--- | :--- | :--- |
| `CameraDevice` | `models/camera.py:125-220` | `state`, `sensor`, `exposure`, `gain`, `offset`, `binning`, `thermal`, `readout` |
| `MountDevice` | `models/mount.py:33-77` | `is_parked`, `is_homed`, `mount_status`, `pointing`, `meridian` (plus mutating `data["meridian"].hours_to_flip = None` at `74-75`) |
| `GuiderDevice` | `models/guider.py:36-69` | `state`, `pixel_scale`, `ra_rms_error`, `dec_rms_error`, `total_rms_error`, `peak_ra_excursion`, `peak_dec_excursion` |
| `RotatorDevice` | `models/rotator.py:25-51` | `position_deg`, `mechanical_position_deg`, `is_synced` |
| `DomeDevice` | `models/dome.py:28-49` | `is_parked`, `is_homed`, `azimuth_deg`, `dome_status` |
| `Weather` | `models/weather.py:33-48` | `temperature_c`, `dew_point_c`, `humidity_pct`, `pressure_hpa`, `wind_speed_mps`, `wind_gust_mps`, `wind_direction_deg`, `sky_temperature_c`, `sky_quality_mpsas`, `rain_rate_mm_per_hr`, `cloud_cover_pct` |
| `FocuserDevice` | `models/focuser.py:28-40` | `temperature_c`, `step_size_microns` |
| `FilterWheelDevice` | `models/filter_wheel.py:22-29` | `position` (lifted from `selected_filter.id`) |

- **Contrast with `FrameStats._normalize` (`models/frame.py:92-109`):** `FrameStats` constructs a fresh `out: dict[str, object] = {}` (`97`) and leaves its input dict untouched.
- **Why It Matters:** In production, `tools/equipment.py:_build` (`143-153`) passes `converted = _convert_keys(data)` (a fresh dict) into `model_cls(**converted)`, which unpacks keyword arguments so Pydantic receives a new dict. However, any caller or test that invokes `CameraDevice.model_validate(shared_dict)` or `RotatorDevice.model_validate(shared_dict)` directly has its dictionary mutated in place. For example, in `RotatorDevice._derive_fields` (`rotator.py:36`), `if "is_synced" not in data:` sets `data["is_synced"]`, so reusing `shared_dict` after modifying `shared_dict["synced"]` silently retains the stale `is_synced` value from the first validation pass.

### 3.2 In-Place AST Dict Mutation in `sequence/nodes.py`
- **`_fix_provider` (`sequence/nodes.py:17-36`):** Walks the sequence tree in place. The first time it encounters a `"SelectedProvider"` dict with a given `$type`, it records `store[ptype] = d["$id"]` (`28`). On every subsequent occurrence of that `$type`, it overwrites `data["SelectedProvider"] = {"$ref": store[ptype]}` in place (`26`).
- **`_add_refs` (`sequence/nodes.py:39-84`):** Walks the sequence tree in place, incrementing `ident` (`65`), assigning `data["$id"] = str(ident)` (`66`), linking `data["Parent"] = parent` (`68`), and—for leaf nodes (`"Items" not in data and "Conditions" not in data`, `71-75`)—executing `data["Parent"] = data.pop("Parent")` to force `"Parent"` to the **last** key in Python's insertion-ordered dict.
- **Hidden Coupling to JSON.NET Deserialization Order:** As documented in `_add_refs` (`40-59`), N.I.N.A.'s JSON.NET deserializer executes C# property setters in JSON key order. If `"Parent"` precedes `"Identifier"` on a Sequencer+ `Symbol` leaf node (`GetArray` / `SetVariable`), the setter mis-stores the symbol identifier, causing `LoopWhile(n > 0)` to evaluate false and skip all exposures—or causing `Validate()` to leave `Issues` null and crash N.I.N.A.'s sequence start with `ArgumentNullException` in ~20 ms. Furthermore, because `_fix_provider` reads `d["$id"]` (`28`), `_add_refs` **must** run before `_fix_provider` (`builders.py:488`: `return _fix_provider(_add_refs(data))`); reversing that call order stores `None` in every `{"$ref": None}`.

### 3.3 FastMCP Private Attribute Coupling
- **Location:** `nina_planner/tools/plans.py:314-341` (`_forbid_unknown_arguments`)
  ```python
  tool = mcp._tool_manager.get_tool("get_plan_progress")
  ...
  arg_model = tool.fn_metadata.arg_model
  strict_model = type(
      arg_model.__name__,
      (arg_model,),
      {"model_config": ConfigDict(extra="forbid")},
  )
  tool.fn_metadata = tool.fn_metadata.model_copy(update={"arg_model": strict_model})
  ```
- **Risk:** FastMCP does not expose a public API to set `extra="forbid"` on a single tool's generated argument schema. To prevent callers from passing legacy quality kwargs (`max_hfr`, etc.) directly to `get_plan_progress(file_path)` and having them silently ignored, `_forbid_unknown_arguments()` reaches into `mcp._tool_manager` and `tool.fn_metadata.arg_model` at module import time (`341`). If a FastMCP upgrade renames `_tool_manager`, `fn_metadata`, or `arg_model`, importing `nina_planner.tools.plans` (and thus starting the MCP server) will immediately raise `AttributeError` or `RuntimeError`.

### 3.4 Import-Time Environment Variable Evaluation
- **Locations:**
  - `nina_planner/config.py:10-19`:
    - `NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")`
    - `NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"`
    - `NINA_PLANNER_LOG = os.environ.get("NINA_PLANNER_LOG")`
    - `PROJECT_DIR = Path.cwd()`
  - `nina_planner/sequence/nodes.py:12-14`:
    - `NINA_FLATS_ALTITUDE = float(os.environ.get("NINA_FLATS_ALTITUDE", "80"))`
    - `NINA_FLATS_AZIMUTH_DAWN = float(os.environ.get("NINA_FLATS_AZIMUTH_DAWN", "270"))`
    - `NINA_FLATS_AZIMUTH_DUSK = float(os.environ.get("NINA_FLATS_AZIMUTH_DUSK", "90"))`
- **Risk:** Unlike `imaging.windows_to_local` (`imaging.py:29`, which reads `os.environ.get("NINA_DRIVE_MOUNT")` dynamically on each call) or `tools/plans.py:get_nina_settings` (`1227-1230`, which reads `os.environ` dynamically), `config.py` and `sequence/nodes.py` bind these values once at import time. Additionally, `NINA_FLATS_*` calls `float(...)` at import time (`nodes.py:12-14`), meaning a non-numeric `NINA_FLATS_ALTITUDE` in the environment crashes the entire MCP server at import time with an unhandled `ValueError`.

---

## Category 4: Safety-Critical, Hardware & Data-Loss Gates

Because `nina-planner` controls physical observatory hardware (robotic mount, motorized roof/dome cover, camera cooler, filter wheel) and performs automated disk cleanup (`nina-purge`), five safety-critical invariants govern hardware movement and data deletion.

### 4.1 Enclosure Safety Monitor (`is_safe`) vs. `dark`/`bias` Sky-Gate Exemption
- **Locations:** `sequence/builders.py:210-241` (canonical policy), `sequence/builders.py:389-423` (`sequence_safetynet`), `sequence/targets.py:108-190` (`target_payload_darks`).
- **Mechanism:**
  - **Sky-Dependent Steps (`light` and `flat`):** Wrapped in `sequence_safetynet` (`targets.py:229`, `341`), which runsacquisition instructions inside an `"On Safe"` container conditioned on `loop_while_safe()` (`builders.py:415`). When `is_safe=false` (enclosure closed), `"On Safe"` exits immediately, the outer container executes `sequence_park_scope(equipment)` + `wait_until_safe()` (`builders.py:419-421`), and loops until sunrise (`loop_until_sunrise()`, `411`).
  - **Sky-Independent Steps (`dark` and `bias`):** `target_payload_darks` (`targets.py:108-190`) is **exempt** from the `is_safe` gate so calibration frames can be acquired during the day or under a closed roof. This exemption is physically safe **only** because the step:
    1. Immediately parks the mount via `sequence_park_scope(equipment)` (`targets.py:163`).
    2. Never emits `home_scope()` (`GET /equipment/mount/home` requires unparking first and slews the axes).
    3. Cuts light at the optical train (`variant="wheel"` -> `"Dark"` filter; `variant="cover"` -> `close_cover()` ... `open_cover()`; `variant="shutter"` -> camera shutter).
    4. Enforces a runtime AST structural assertion (`targets.py:175-180`):
       ```python
       for needle in ("UnparkScope", "Platesolving", "StartGuiding"):
           assert not _contains_type(built, needle), ...
       ```

### 4.2 Mandatory Park Capability Guard (`require_parkable`)
- **Locations:** `plan_utils.py:152-175` (`require_parkable`), `sequence/builders.py:244-272` (`sequence_park_scope`, `sequence_home_scope`, `sequence_unpark_scope`), `tools/sequence.py:388-389`, `scripts/gen_sequence.py:232`.
- **Mechanism:**
  - `sequence_park_scope(equipment)` (`builders.py:244-247`) returns `[park_scope()]` if `equipment.mount and equipment.mount.can_park` else `[]`.
  - If a sequence were loaded onto a mount with `can_park=False` (or `mount=None`), every wait stage (`Wait For Sunset`, `Wait For Dusk`, `Wait For Object`, `sequence_safetynet`) and the closing `Park Telescope` End area (`builders.py:426-470`) would emit `PARK-STARTING` and `PARK-FINISHED` events around a **no-op** empty instruction list, leaving the telescope unparked and tracking into mechanical limits.
  - Therefore, `plan_utils.require_parkable(equipment)` (`plan_utils.py:152-175`) refuses any sequence load (`load_sequence` and `gen_sequence.py`) when `not (equipment.mount and equipment.mount.can_park)`.
  - **Homing Is Never a Park Fallback:** `sequence_home_scope` (`builders.py:250-258`) is emitted only once per `light` step after `sequence_unpark_scope` and before `set_tracking(0)` (`targets.py:372-377`) because N.I.N.A.'s `/equipment/mount/home` returns HTTP 409 `"Mount parked"` if called while parked.

### 4.3 `get_site_equipment_status` Mutating Hardware Side Effect
- **Locations:** `tools/equipment.py:82-141` (`_get_site_equipment_status`) & `170-176` (`get_site_equipment_status`).
- **Hazard:** Unlike `get_nina_status` (`tools/lifecycle.py:51-77`) and `get_site_profile` (`tools/equipment.py:180-290`), which are strictly read-only, `get_site_equipment_status()` **mutates observatory hardware state**: for every device marked `has_<device>=True` in `ObservatoryProfile.equipment` whose `/equipment/info` entry has `Connected == False`, it sends `GET /equipment/<device>/connect` (`133-134`) and then raises `RuntimeError("Equipment connecting: ...")` (`137-141`).
- **Safeguard in `load_sequence` (`tools/sequence.py:251-256`):** Both `load_sequence` and `scripts/gen_sequence.py` (`128-132`) deliberately validate plan JSON schema, filter names (`validate_filters`), position angles (`validate_position_angle`), and `pointing_index` **before** calling `await equipment()` so that a malformed plan file never triggers hardware auto-connects on its way to failing validation.

### 4.4 `stop_sequence` Leaves the Mount Unparked and Tracking
- **Locations:** `tools/sequence.py:419-423` (`stop_sequence`) & `client.py:166-182` (`stop_running_sequence_and_wait`).
- **Hazard:** `stop_sequence()` calls `GET /sequence/stop` and polls `/sequence/json` every 0.2s (up to `timeout=15.0s`) until no node reports `"RUNNING"`. However, `/sequence/stop` aborts sequence execution **in place** without running the sequence's `End` area (`Park Telescope`).
  - If called during `Image Object`, the mount remains unparked with sidereal tracking active (`TRACKING`) and the camera cooler remains at its cold setpoint.
  - If called while the `End` area is mid-slew to the park position, interrupting the park maneuver can leave the scope stopped in an unparked orientation.
- **Required Controller Recovery:** Any `stop_sequence()` call during an unsafe condition or end-of-night transition **must** be followed by `load_sequence([])` (the empty-list park sequence) and `start_sequence()`.

### 4.5 `nina-purge` 3-Gate Deletion & Shared Calibration Pool Holding
- **Locations:** `purge.py:174-219` (`_plan_file_gate`), `226-244` (`_idle_state`), `302-409` (`_classify`), `534-698` (`_quarantine_gates`, `_sweep_plan_files`), `809-945` (`run`).
- **Data-Loss Prevention Architecture:**
  1. **Active Sequence Refusal (`_idle_state`, `226-244`):** Before `--apply` or `--purge` mutates the imaging root, `purge.py` checks `_sequence_is_running()`. If a sequence is `RUNNING` in N.I.N.A., it refuses to run so it cannot race with live `ImageMetaData.csv` appends. (If N.I.N.A. is unreachable, it proceeds as idle).
  2. **Abort on Any Corrupt Plan JSON (`825-881`):** If any plan-shaped JSON file (`_looks_like_plan_file`) in `project_dir` fails to parse or validate, `nina-purge` aborts the entire run (`return 1`, `881`) before moving or deleting a single file, because an unreadable plan's frame claims are unknown.
  3. **Shared Calibration Pool Holding (`848-870`, `900-926`):** Claims are computed via `archive._select_frames(plan, rows, now)` for every plan in `project_dir`. Because calibration frames (`FLAT`, `DARK`, `BIAS`) are pooled across plans within `calibration_window_days`, a completed and archived plan's darks/flats/biases remain held (`verdict = "hold"`) as long as **any** unarchived or incomplete plan in the project also selects them (`unreleased = sorted(claimants - released)`, `370`).
  4. **3-Gate Plan-File & Archive Verification (`_plan_file_gate`, `174-219`):** Checked before any frame is quarantined or purged:
     - **Gate 1:** `<dest>/<stem>__<plan-id>/plan.json` must exist on disk (`200-207`).
     - **Gate 2:** `plan_file.read_bytes() == archived.read_bytes()` (`209`). If a plan JSON was edited after archiving, `stale=True` refuses the entire `plan_id`—neither its quarantined directories nor its plan JSON can be purged (`587-591`, `883-892`).
     - **Gate 3:** `_inside(plan_file, dest)` is refused (`198-199`) so an archived `plan.json` can never be unlinked.

---

## Category 5: Edge Cases, Numerical/Timezone Subtleties & Test Coverage Gaps

### 5.1 ASCOM `NaN` vs. `0` Sentinel Asymmetry (`progress.py` vs. `models/frame.py`)
N.I.N.A. and ASCOM drivers use two incompatible sentinel conventions across their CSV and REST surfaces:
1. **`ImageMetaData.csv` (`progress.py:172-217`, `_quality_accepted`):**
   - N.I.N.A.'s Session Metadata plugin serializes unmeasured quality metrics (`HFR`, `GuidingRMSArcSec`, `HFRStDev`, `DetectedStars`) as `0` (or `0.0`) in `ImageMetaData.csv`.
   - Consequently, when a quality threshold is active (`limit is not None`), `_quality_accepted` treats `value == 0` (and `stars == 0`) as **unmeasured / fail-closed** (`progress.py:205, 215`):
     ```python
     if not math.isfinite(value) or value == 0 or value > limit:
         return False
     ```
   - When no threshold is configured (`limit is None`), `0` is ignored and the frame counts.
2. **`GET /v2/api/image-history` (`models/frame.py:53-109`, `FrameStats`):**
   - The live REST endpoint serializes unmeasured metrics as `"NaN"` (which `ascom_float` / `ascom_int` in `nina_utils.py:33-72` map to `None`), whereas a genuine `0` on `/image-history` (such as `Stars: 0` during heavy cloud cover) is a real measurement and is preserved as `0` (`FrameStats.stars == 0`).
3. **Profile Coercion (`tools/equipment.py:203-204`):**
   - `get_site_profile()` uses `ascom_float(camera.get("PixelSize")) or 0` and `ascom_int(camera.get("Gain")) or 0`, collapsing `None` (when N.I.N.A. reports `"NaN"`) to `0` so `OpticalTrainInfo` receives numeric defaults, and `mosaic.py:pane_fov_arcmins` (`20-49`) then explicitly rejects `<= 0` optical parameters before computing mosaic grids.

### 5.2 Naive-UTC vs. Aware-Local Datetime Boundary (`progress.py` vs. `nina_utils.py`)
The codebase uses two contrasting conventions for `datetime` objects with `tzinfo is None`:
- **Inside `progress.py` (Naive = UTC):**
  - `_exposure_instant(row)` (`progress.py:220-239`) reads `ExposureStartUTC`, parses ISO-8601 (replacing `"Z"` with `"+00:00"`), converts any aware timestamp to UTC, and strips `tzinfo` (`stamp.astimezone(UTC).replace(tzinfo=None)`). The local `ExposureStart` column is ignored altogether so DST shifts or mixed offsets can never skew calibration window math.
  - `_within_age` (`268-297`) and `plan_progress` (`545-551`) convert `session_start` and `reference_now` to **naive UTC** before computing `session_start - window <= reference_now <= session_start + window`.
- **Inside `nina_utils.py` & `tools/equipment.py` (Naive = Host-Local):**
  - `to_local_display(dt)` (`nina_utils.py:107-123`) treats a naive `dt` (`dt.tzinfo is None`) as **already host-local** and returns `dt.isoformat(timespec="seconds")` without offset conversion, whereas an aware `dt` is converted via `dt.astimezone().isoformat(timespec="seconds")`.
  - `_convert_to_met` (`tools/equipment.py:30-79`) tags any naive event timestamp with the host's local timezone (`ts.replace(tzinfo=tzinfo)`, `60`).
- **The Critical Hand-Off (`progress.py:620`):**
  - Because `window_end` in `plan_progress` is computed in naive UTC (`551`), passing `window_end` directly to `to_local_display(window_end)` would misrender UTC as bare local time without an offset. Line `620` explicitly bridges the two conventions:
    ```python
    item["window_end"] = to_local_display(window_end.replace(tzinfo=UTC))
    ```
    Any future caller of `to_local_display()` from inside `progress.py` must remember to attach `.replace(tzinfo=UTC)` first.

### 5.3 Cumulative `acquired_count` & `overshot` Ambiguity After `mode="full"` Re-Runs
- **Locations:** `progress.py:527-540, 609-615`, `tools/plans.py:255-282`, `tools/sequence.py:66-113`.
- **Subtlety:** Frame attribution in `ImageMetaData.csv` is keyed by the plan's `_{plan_id}_` token, which is invariant across runs of the same plan file.
  - If a plan requires `total_count = 20` light frames and already has 20 frames on disk, and the operator triggers `load_sequence` with `mode="full"` to re-shoot the target, N.I.N.A. is instructed to capture 20 more frames.
  - If that second run aborts after only 3 frames, `ImageMetaData.csv` now has `23` attributed frames. Calling `get_plan_progress` will report `acquired_count: 23`, `remaining_count: 0`, `complete: true`, and `overshot: true` (`surplus_count: 3`).
  - Neither `get_plan_progress` nor `load_sequence(mode="remaining")` can tell whether those 23 frames represent a completed 20-frame run plus 3 extra frames, or a failed 20-frame re-shoot that only delivered 3 frames.
- **Mitigation:** `_full_mode_note` (`tools/sequence.py:66-113`) warns at `load_sequence` time when `mode="full"` is requested on an already-complete light group, and `get_plan_progress` (`tools/plans.py:272-282`) sets `overshot = True` with an explicit `warning` directing the operator/agent to inspect per-date folders and `audit_plans`.

### 5.4 Unpinned `ObservationPlan` Hash Identity Under `plan_with_remaining`
- **Locations:** `models/plan.py:322-336` (`_base_plan_id`), `progress.py:690-726` (`plan_with_remaining`).
- **Subtlety:** When `plan.plan_id` is empty (`""`), `plan._base_plan_id()` computes a deterministic SHA-256 hash (`plan-<12 hex>`) over `self.model_dump(exclude={"plan_id"})`.
- When `load_sequence` runs in `mode="remaining"`, `plan_with_remaining(plan, progress)` (`progress.py:690-726`) rewrites each group's `total_count` to `remaining_count` and drops finished groups. If `plan.plan_id` were left empty during that rewrite, `plan._base_plan_id()` on the rewritten `ObservationPlan` would hash the reduced `total_count` values and mint a **completely different `plan_id`**, causing N.I.N.A. to save the remaining frames under a new token that `get_plan_progress` and `audit_plans` would never attribute to the original plan file.
- **Mitigation:** `plan_with_remaining` (`progress.py:723`) explicitly pins `"plan_id": plan._base_plan_id()` in `plan.model_copy(update=...)` before mutating frame counts, and `write_plan_file` (`tools/plans.py:190-191`) stamps `plan_id` onto disk when writing new plans.

### 5.5 Test Coverage Gaps Across `scripts/` and `.agents/skills/`
While the core `nina_planner` package has 34 dedicated test modules in `.opencode/local/mcps/nina_planner/tests/` (plus `scripts/gen_sequence.py` covered by `tests/test_gen_sequence.py`), three standalone Python modules in the repository have **no automated unit test files**:

| Untested File | Lines | Complexity / Failure Modes Currently Uncovered by `pytest` |
| :--- | :---: | :--- |
| `.agents/skills/site-conditions/fetch_conditions.py` | 676 | HTML regex extraction (`parse`, `338-507`), coordinate 2-dp rounding to prevent query-stripping 301 redirects (`_fmt_coord`, `115-131`), negative temperature regex parsing (`RE_INT_CELL`, `77`), midnight-wrap date resolution (`_is_dark`, `181-212`), hour-run compression (`_compress`, `220-242`), and `--hours` window slicing (`slice_hours`, `510-538`). |
| `scripts/update_readme.py` | 105 | Regex replacement of fenced ` ```json <filename> ` blocks (`_PATTERN`, `58-63`) and repo-relative link injection (`insert_file_contents_and_links`, `66-89`). |
| `scripts/refresh_fixtures.py` | 72 | Live N.I.N.A. `/sequence/state` and `/sequence/json` capture, port defaulting (`host += ":1888"`), and `Success: false` envelope error exit (`fetch`, `26-43`). |
