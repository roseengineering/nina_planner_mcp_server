# Independent Source-vs-Documentation Verification Audit Report

[⬆️ Up to Atlas Index](../README.md) · [➡️ Complexity & Risk Register](./complexity-and-risk-register.md) · [➡️ Codebase Review Guide](./codebase-review-guide.md) · [⬅️ Coverage Ledger](../00-inventory/coverage-ledger.md)

---

## 1. Executive Summary

In **Wave 4**, four independent code auditor partitions (`Auditor-1` through `Auditor-4`) cross-checked every Markdown document in `docs/01-architecture/`, `docs/02-execution-flows/`, and `docs/03-modules/` against the raw Python AST and source files in `.opencode/local/mcps/nina_planner/nina_planner/`, `.opencode/local/mcps/nina_planner/tests/`, `scripts/`, and `.agents/skills/site-conditions/fetch_conditions.py`.

| Audit Metric | Result |
|---|---|
| **Production Python Modules Audited** | **55 / 55 (100%)** (`13,517` lines across `nina_planner/`, `scripts/`, `.agents/skills/`) |
| **Test Python Modules Audited** | **36 / 36 (100%)** (`20,099` lines across `conftest.py`, `fake_nina.py`, `sequence_helpers.py`, and 33 `test_*.py` files) |
| **MCP Tools Verified (`@mcp.tool`)** | **22 / 22 (100%)** |
| **Console Script Entrypoints Verified** | **4 / 4 (100%)** (`nina-planner`, `nina-archive`, `nina-pin-id`, `nina-purge`) |
| **Classes / Pydantic Models / Dataclasses / Exceptions Verified** | **51 / 51 (100%)** |
| **Functions & Methods Verified (`def` / `async def`)** | **268 / 268 (100%)** |
| **Documentation Files Audited** | **42 Markdown files** across `00-inventory/`, `01-architecture/`, `02-execution-flows/`, and `03-modules/` |
| **Discrepancies Found & Corrected In-Place** | **71 corrections** across 25 Markdown files |
| **Production Code Modifications** | **0** (verified via `git status --porcelain`) |

---

## 2. Partition-by-Partition Audit Ledger

### 2.1 Auditor-1: System Architecture & Core Foundation (`01-architecture/*` & `03-modules/core/*`)

Audited **13 Markdown files** against `__init__.py`, `__main__.py`, `app.py`, `server.py`, `config.py`, `client.py`, `nina_utils.py`, `system_time.py`, `horizon.py`, `dso_catalog.py`, `mosaic.py`, and `dso_catalog.csv`. **16 corrections applied:**

| File Audited | Status | Corrections Applied Against Source |
|---|---|---|
| `docs/01-architecture/system-overview.md` | **Fixed (5 edits)** | 1. Corrected `dso_catalog.csv` object count from `173` to `497` objects (`498` lines including header) in prose (`line 37`) and Mermaid component diagram (`line 284`).<br>2. Corrected outer `sequence_safetynet` condition for lights from `loop_until_sunrise()` to `loop_until_dawn()` (`sequence/targets.py:345`) and added exact flat condition citations (`loop_until_sun_altitude_below(-8)` / `loop_until_sun_altitude_above(0)` at `sequence/targets.py:233-235`).<br>3. Updated `SendEventInstruction` line citations for `acquisition_container` (`builders.py:85, 93, 96`) and `sequence_end` (`builders.py:458, 462, 467`). |
| `docs/01-architecture/module-dependency-graph.md` | **Fixed (1 edit)** | Corrected `ObservatoryProfile.horizon` default from `Field(default_factory=Horizon)` (`models/profile.py:103`) to `horizon: Horizon = Horizon()` (`models/profile.py:117`). |
| `docs/01-architecture/data-models-and-schemas.md` | **Fixed (1 edit)** | Updated `Horizon.is_custom` (`62-71`) and `Horizon.altitude_at` (`73-96`) line ranges and `ObservatoryProfile.horizon = Horizon()` (`profile.py:117`). |
| `docs/01-architecture/external-integrations-and-io.md` | **Fixed (1 edit)** | Corrected `PARK-STARTING` / `PARK-FINISHED` emission site from `builders.py:238-247` to `builders.py:458, 462, 467` (`sequence_end`), and corrected `COUNT_ARRAY = "'count'"` (`builders.py:280`). |
| `docs/03-modules/core/README.md` | **Fixed (2 edits)** | 1. Corrected `dso_catalog.csv` count from `173` to `497` (`lines 17, 93`).<br>2. Replaced inaccurate `Int32.MinValue` reference with the actual ASCOM sentinels handled by `nina_utils.py` (`NaN`, `"NaN"`, `"N/A"`, `.NET DateTime.MinValue`). |
| `docs/03-modules/core/bootstrap-and-server.md` | **Verified Clean** | 100% match across `__init__.py` (`1`), `app.py` (`1-21`), `server.py` (`1-61`), and `__main__.py` (`1-12`). |
| `docs/03-modules/core/config.md` | **Verified Clean** | 100% match across all 7 module-level constants in `config.py` (`1-19`). |
| `docs/03-modules/core/client.md` | **Fixed (2 edits)** | Updated inbound caller line citations for `horizon.py` (`132, 134`), `tools/lifecycle.py` (`123, 126, 138, 146, 154, 165, 187, 209, 211`), and `scripts/gen_sequence.py` (`375`). |
| `docs/03-modules/core/nina_utils.md` | **Verified Clean** | 100% match across all 6 functions and `_MISSING_STRINGS` in `nina_utils.py` (`1-123`). |
| `docs/03-modules/core/system_time.md` | **Fixed (4 edits)** | Updated inbound `tools/lifecycle.py` call-site lines (`113, 123, 166, 174, 181, 213`) and `imaging.py` `NINA_DRIVE_MOUNT` line range (`79-80`). |
| `docs/03-modules/core/horizon.md` | **Fixed (1 edit)** | Updated `ObservatoryProfile.horizon: Horizon = Horizon()` (`models/profile.py:117`). |
| `docs/03-modules/core/dso_catalog.md` | **Verified Clean** | 100% match across `dso_catalog.py` (`1-59`) and `dso_catalog.csv` (497 objects). |
| `docs/03-modules/core/mosaic.md` | **Verified Clean** | 100% match across all 5 functions in `mosaic.py` (`1-200`). |

---

### 2.2 Auditor-2: Domain Services, CLI Engines & MCP Tools (`03-modules/{core,cli,tools}/*`)

Audited **15 Markdown files** against `plan_utils.py`, `metadata.py`, `imaging.py`, `progress.py`, `sequence_state.py`, `archive.py`, `purge.py`, `pin_id.py`, and `tools/*.py`. **13 corrections applied:**

| File Audited | Status | Corrections Applied Against Source |
|---|---|---|
| `docs/03-modules/core/plan_utils.md` | **Verified Clean** | 100% match across all 11 functions and 2 constants in `plan_utils.py` (`1-309`). |
| `docs/03-modules/core/metadata.md` | **Fixed (1 edit)** | Corrected N.I.N.A. active profile endpoint query parameter from `/profile/show?isactive=true` to `/profile/show?active=true` (`equipment.py:182`). |
| `docs/03-modules/core/imaging.md` | **Fixed (1 edit)** | Replaced inline draft note in `windows_to_local` §6 with clean specification of the `not win.drive` (`imaging.py:76-77`) branch. |
| `docs/03-modules/core/progress.md` | **Fixed (1 edit)** | Cleaned code-span formatting in Inbound Callers table; verified all 17 functions + 4 inner closures in `progress.py` (`1-728`). |
| `docs/03-modules/core/sequence_state.md` | **Verified Clean** | 100% match across all 10 functions in `sequence_state.py` (`1-241`). |
| `docs/03-modules/cli/README.md` | **Fixed (1 edit)** | Corrected mosaic pane attribution token from `_{plan_id}-p<N>_` to `_{plan_id}-<N>_` (`models/plan.py:391`, `progress.py:44`). |
| `docs/03-modules/cli/archive.md` | **Fixed (2 edits)** | 1. Corrected pane-suffixed token pattern in `_divert_frames` to `_{base_id}-<N>_` (`progress.py:47-54`).<br>2. Replaced inline draft note in `disk_report` with clean specification of `dropped` $\to$ `rejected` vs. `gone` (`archive.py:485-496`). |
| `docs/03-modules/cli/purge.md` | **Verified Clean** | 100% match across `PurgeError` and all 18 functions in `purge.py` (`1-1091`). |
| `docs/03-modules/cli/pin_id.md` | **Verified Clean** | 100% match across `PinError` and all 5 functions in `pin_id.py` (`1-166`). |
| `docs/03-modules/tools/README.md` | **Fixed (1 edit)** | Corrected `_own_token_rows` (`475-512`) and `_junk_count` (`515-542`) line ranges in the `plans.py` inventory row. |
| `docs/03-modules/tools/lifecycle.md` | **Verified Clean** | 100% match across all 7 functions in `tools/lifecycle.py` (`1-231`). |
| `docs/03-modules/tools/equipment.md` | **Fixed (1 edit)** | Updated `metadata.py` caller line citation to `metadata.py:9, 18`. |
| `docs/03-modules/tools/sequence.md` | **Fixed (2 edits)** | 1. Corrected inner closure line ranges in `_load_many` (`equipment()` at `196-201`, `rows()` at `203-212`).<br>2. Added top-level `running`/`completed_steps` and per-step `index`/`name` keys to `get_sequence_state(detail="summary")`. |
| `docs/03-modules/tools/sky.md` | **Verified Clean** | 100% match across all 28 functions and 19 constants in `tools/sky.py` (`1-862`). |
| `docs/03-modules/tools/plans.md` | **Fixed (4 edits)** | 1. Clarified in-memory profile validation in `write_plan_file`.<br>2. Corrected `_own_token_rows` (`475-512`) and `_junk_count` (`515-542`) line ranges.<br>3. Corrected `audit_plans` Mermaid branch line range (`815-822`).<br>4. Clarified `FileNotFoundError` vs. `RuntimeError` in `audit_plans` root resolution. |

---

### 2.3 Auditor-3: Sequence Compiler, Pydantic Models, Scripts & Tests (`03-modules/{sequence,models,scripts-and-skills,tests}/*`)

Audited **17 Markdown files** against `sequence/*.py`, `models/*.py`, `scripts/*.py`, `.agents/skills/site-conditions/fetch_conditions.py`, and `tests/*.py`. **18 corrections applied:**

| File Audited | Status | Corrections Applied Against Source |
|---|---|---|
| `docs/03-modules/sequence/README.md` | **Fixed (2 edits)** | Corrected `instructions.py` function count from `55` to `54` (48 re-exported + `_step_word` + 5 internal `Sequencer+` factories). |
| `docs/03-modules/sequence/nodes.md` | **Verified Clean** | 100% match across all 23 functions + 2 closures in `sequence/nodes.py` (`1-346`). |
| `docs/03-modules/sequence/instructions.md` | **Fixed (2 edits)** | 1. Corrected total function count header to `54`.<br>2. Corrected `sky_flats` (`154-182`) item count description from `4 positional Items` to `5 positional Items`. |
| `docs/03-modules/sequence/builders.md` | **Verified Clean** | 100% match across all 16 functions and 3 constants in `sequence/builders.py` (`1-494`). |
| `docs/03-modules/sequence/targets-and-variants.md` | **Verified Clean** | 100% match across all 5 functions in `targets.py` (`1-415`) and 2 functions in `variants.py` (`1-114`). |
| `docs/03-modules/models/README.md` | **Verified Clean** | 100% match across all 20 model modules (`1,750` lines) and 31 re-exports in `models/__init__.py`. |
| `docs/03-modules/models/strict-and-request.md` | **Verified Clean** | 100% match across `strict.py` (`1-16`) and `request.py` (`1-124`). |
| `docs/03-modules/models/plan.md` | **Verified Clean** | 100% match across all 11 `StrictModel` classes and 9 validators/methods in `models/plan.py` (`1-455`). |
| `docs/03-modules/models/pointing-and-frame.md` | **Fixed (1 edit)** | Corrected `Pointing.requests_rotation` caller description to parameterless `slew_center_and_rotate()` / `slew_and_center()` at `sequence/targets.py:387-389`. |
| `docs/03-modules/models/profile.md` | **Verified Clean** | 100% match across all 5 classes and 4 methods/properties in `models/profile.py` (`1-143`). |
| `docs/03-modules/models/core-rig-devices.md` | **Fixed (4 edits)** | Replaced stale caller names in `observatory.py` Inbound Callers with actual `builders.py`/`targets.py`/`variants.py` functions and fixed relative Markdown links. |
| `docs/03-modules/models/auxiliary-and-safety-devices.md` | **Fixed (5 edits)** | Refined end-line numbers for `DomeDevice` (`10-49`), `ReadonlySwitchInfo` (`8-14`), `WritableSwitchInfo` (`17-40`), `variants.py` line citations (`53-68, 60`), and relative Markdown links. |
| `docs/03-modules/scripts-and-skills/README.md` | **Verified Clean** | 100% match across all 4 scripts and 7 skills. |
| `docs/03-modules/scripts-and-skills/scripts.md` | **Fixed (1 edit)** | Corrected `ObservatoryProfile` attribute reference in `_fetch_equipment` from `profile.equipment_config` to `profile.equipment` (`models/profile.py:113`). |
| `docs/03-modules/scripts-and-skills/fetch_conditions.md` | **Verified Clean** | 100% match across all 17 functions, 2 exceptions, and 19 regexes/constants in `fetch_conditions.py` (`1-817`). |
| `docs/03-modules/tests/README.md` | **Fixed (1 edit)** | Updated test file count to `36 Python files (3 shared harness modules + 33 test_*.py suites) totaling 20,099 lines`. |
| `docs/03-modules/tests/test-suite-reference.md` | **Fixed (2 edits)** | Updated header summary to `33 test_*.py modules (19,913 lines; 20,099 total)` and added `0m` annotation to `_Exp (75-78, 0m)`. |

---

### 2.4 Auditor-4: End-to-End Execution Flows (`02-execution-flows/*`)

Audited **7 Markdown files** in `docs/02-execution-flows/`. **24 corrections applied:**

| File Audited | Status | Corrections Applied Against Source |
|---|---|---|
| `docs/02-execution-flows/entrypoints-and-lifecycle.md` | **Fixed (2 edits)** | Corrected `fetch_conditions.py` exit-code `1` catch block (`772-782`) and exit-code `2` validation (`768-770`), and updated `imaging.py` `NINA_DRIVE_MOUNT` line reference to `79`. |
| `docs/02-execution-flows/async-state-and-error-model.md` | **Fixed (7 edits)** | Aligned all 9 `anyio.to_thread.run_sync` call sites, `anyio.open_file` (`plan_utils.py:273-274`), `_load_many` `state` dict closures (`tools/sequence.py:189-218`), `write_text_atomic` `os.fdopen` flow (`plan_utils.py:30-53`), `_archive_one` `copied == 0` check (`archive.py:709-718`), and bidirectional links. |
| `docs/02-execution-flows/flow-sky-and-target-planning.md` | **Fixed (3 edits)** | Clarified N.I.N.A. horizon/altitude condition names, updated `sequence_safetynet` line range (`builders.py:389-423`), `client.api_get` (`118-134`), and `large_for_field` guard (`sky.py:729-732`). |
| `docs/02-execution-flows/flow-equipment-safety-and-lifecycle.md` | **Fixed (2 edits)** | Updated `GuiderDevice` (`17-69`), `require_parkable` (`152-175`), `target_payload_darks` park line (`163`), and `target_payload_lights` home line (`375`). |
| `docs/02-execution-flows/flow-sequence-compilation-and-execution.md` | **Fixed (2 edits)** | Aligned `RUN-STARTING`/`RUN-FINISHED` event strings with `_step_word` and updated `TargetName` attribution token format (`_<plan_id>_`). |
| `docs/02-execution-flows/flow-progress-audit-and-frame-telemetry.md` | **Fixed (2 edits)** | Fixed `/profile/show?active=true` query string (`equipment.py:182`) and `ObservatoryProfile.local_image_save_path` (`@computed_field` at `models/profile.py:119-128`). |
| `docs/02-execution-flows/flow-archive-purge-and-pin-cli.md` | **Fixed (6 edits)** | Aligned `get_nina_settings` `missing` vs. `ready` lines (`1236-1237`), `pin_id` sequence diagram (`57-86, 126-151`), `archive.run` Pass 1–3 ordering (`931-1188`), and `purge.py` Gate 1–4 semantics (`174-219, 386-398`). |
