# `nina_planner.sequence_state` — Compact Projection of N.I.N.A. `/sequence/state` & `/sequence/json`

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ Core Package](./README.md) · **Callees:** [`models/plan.py`](../models/plan.md) · **Callers:** [`tools/sequence.py`](../tools/sequence.md)

---

## 1. Module Overview

| Attribute | Value |
|---|---|
| **Source File** | `.opencode/local/mcps/nina_planner/nina_planner/sequence_state.py` |
| **Line Count** | 241 lines |
| **Architectural Layer** | Core Domain Service — N.I.N.A. Sequencer AST Inspection & State Projection |
| **Package** | `nina_planner` |

### Purpose & Module Docstring (`sequence_state.py:1-4`)

`sequence_state.py` transforms N.I.N.A.'s raw, deeply nested `/sequence/state` REST payload—optionally paired with `/sequence/json`—into the compact, structured `"summary"` dictionary returned by the `get_sequence_state` MCP tool (`tools/sequence.py:438-454`).

Key domain challenges solved by this projection:
1. **Structural Exposure Group Discovery (`_children`, `_counts_exposures`, `_exposure_groups`):** A sequence step may expose frames either through a single `Smart Exposure_Container` or, when batched (`batch_size`), across multiple `Exposure Group_Container` nodes (`sequence_count_exposure`). Furthermore, outer loop wrappers also carry `Iterations`. By requiring `Conditions` to contain an `Iterations` / `CompletedIterations` dict *and* `Take Exposure` to be a **direct** child (`_children`), `_counts_exposures` isolates actual exposure-counting groups without being fooled by outer loop wrappers.
2. **Unambiguous Iteration Accounting (`_exposure`):** At the node level, N.I.N.A.'s `Iterations` key means *completed* iterations in `/sequence/state` but *total* iterations in `/sequence/json`. `_exposure` reads `CompletedIterations` and `Iterations` exclusively from the group's `Conditions[]` loop condition (falling back to `Take Exposure.ExposureCount` for `total`).
3. **Failed vs. Pre-Flight Issue Partitioning (`_issues`):** N.I.N.A. populates `Issues` on nodes before a sequence starts whenever a device is disconnected (e.g., `"Focuser not connected"`). `_issues` separates real execution failures (`node["Status"] == "FAILED"` $\rightarrow$ `issues`) from harmless pre-flight validation notes (any other status $\rightarrow$ `preflight_issues`).
4. **Safe Index-Verified Horizon Merging (`_horizon`, `project_sequence_state`):** `/sequence/state` omits `Wait for Altitude` details, which live in `/sequence/json`. Because `/sequence/json` steps lack `Target` metadata and step names can repeat, `project_sequence_state` verifies that the full list of step `Name`s matches 1-for-1 between both endpoints (`paired`) before merging horizon altitude data by index—failing safe to `horizon: None` on any mismatch.
5. **Non-Raising Parse Error Reporting (`parse_error`):** If a non-empty `/sequence/state` payload lacks `"Target Sequence_Container"`, `project_sequence_state` sets `parse_error: True` and records a diagnostic in `issues` rather than raising, distinguishing an unrecognized sequence tree from an empty sequencer (`step_count: 0, parse_error: False`).

### Inbound Callers

| Caller Module | Imported Symbols | Call Sites & Usage Context |
|---|---|---|
| `nina_planner/tools/sequence.py` | `project_sequence_state` (`20`) | `get_sequence_state(detail="summary")` (`454`) passes `state` (`/sequence/state`) and `json_state` (`/sequence/json`, or `None` on fetch error) to `project_sequence_state(state, json_state)`. |

*(Also tested directly by `tests/test_sequence_state.py`, `tests/test_sequence_state_contract.py`, and `tests/test_fake_nina.py`.)*

### Outbound Imports

| Category | Import Statement | Line(s) | Purpose |
|---|---|---|---|
| **Stdlib** | `from collections.abc import Iterator` | `6` | Generator return type for `_walk` and `_children`. |
| **Stdlib** | `from typing import Any` | `7` | Arbitrary JSON AST node and payload typing. |
| **Internal** | `from .models.plan import ATTRIBUTION_RE` | `9` | Compiled regex (`re.compile(r"\[([A-Za-z0-9-]+?)(?:-([1-9]\d*))?\]$")`) extracting `(plan_id, pointing_index)` from a step's `Target.TargetName`. |

### Module-Level Constants (`sequence_state.py:11`)

| Constant | Line | Type | Value | Purpose |
|---|---:|---|---|---|
| `_TARGET_AREA` | `11` | `str` | `"Target Sequence_Container"` | Exact `Name` of N.I.N.A.'s Target container node inside `/sequence/state` and `/sequence/json`. |

---

## 2. Classes, Data Models & Exceptions

`sequence_state.py` defines **no classes, Pydantic models, or custom exception subclasses**.
- All functions operate on raw decoded JSON structures (`dict[str, Any]`, `list[Any]`, or scalars) and fail closed without raising exceptions even on malformed container shapes.

---

## 3. Function & Method Reference

| Symbol | Kind | Lines | Summary |
|---|---|---|---|
| [`_walk`](#31-_walk) | `def` | `14-21` | Pre-order depth-first generator yielding every `dict` node in an arbitrary JSON tree. |
| [`_find`](#32-_find) | `def` | `24-25` | Returns the first `dict` node in `_walk(node)` whose `"Name"` equals `name`, or `None`. |
| [`_target_area`](#33-_target_area) | `def` | `28-29` | Finds the `"Target Sequence_Container"` dict in `payload` via `_find`. |
| [`_issues`](#34-_issues) | `def` | `32-47` | Walks `node` and partitions deduplicated `"{Name}: {issue}"` strings into `(failed, preflight)`. |
| [`_children`](#35-_children) | `def` | `50-70` | Yields direct child `dict` items from `node["Items"]`, supporting both plain `list` and `{"$values": [...]}` shapes. |
| [`_counts_exposures`](#36-_counts_exposures) | `def` | `73-96` | Returns `True` if `node` has an iteration loop condition and a direct `"Take Exposure"` child. |
| [`_exposure_groups`](#37-_exposure_groups) | `def` | `99-114` | Collects `"Smart Exposure_Container"` (if present) plus all structural `_counts_exposures` nodes in `step`. |
| [`_exposure`](#38-_exposure) | `def` | `117-150` | Builds the `{seconds, gain, offset, binning, filter, completed, total}` dict for one exposure group (or step fallback). |
| [`_horizon`](#39-_horizon) | `def` | `153-161` | Extracts `{current_altitude, min_altitude, expected_time}` from `"Wait for Altitude"` in a `/sequence/json` step. |
| [`project_sequence_state`](#310-project_sequence_state) | `def` | `164-241` | Projects `/sequence/state` and optional `/sequence/json` into the top-level summary dictionary. |

---

### 3.1 `_walk`

**Location:** `nina_planner/sequence_state.py:14-21`

#### 1. Purpose & Architectural Role
Recursive pre-order depth-first generator that yields every `dict` in an arbitrary JSON structure (`dict`, `list`, or primitive). Used throughout `sequence_state.py` (`_find`, `_issues`, `_exposure_groups`, `project_sequence_state`) to traverse N.I.N.A. sequence trees regardless of nesting depth.

#### 2. Signature, Decorators & Inputs
```python
def _walk(node: Any) -> Iterator[dict[str, Any]]:
```
- **`node` (`Any`):** Root JSON value (`dict`, `list`, `None`, or primitive scalar).

#### 3. Outputs & Return Semantics
- Yields every `dict[str, Any]` encountered in pre-order (parent `dict` is yielded before recursively walking its `.values()`). Yields nothing if `node` is `None` or a primitive scalar.

#### 4. Step-by-Step Internal Logic
1. **Handle `dict` (`15-18`):** If `isinstance(node, dict)`, yields `node` (`16`), then iterates `for value in node.values():` (`17`) and delegates `yield from _walk(value)` (`18`).
2. **Handle `list` (`19-21`):** Else if `isinstance(node, list)`, iterates `for item in node:` (`20`) and delegates `yield from _walk(item)` (`21`).

#### 5. Side Effects & I/O
- Pure in-memory generator; no I/O.

#### 6. Error Handling & Edge Cases
- Safe on `None`, strings, numbers, and empty containers.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(N)$ where $N$ is the total number of JSON nodes/values in `node`.

---

### 3.2 `_find`

**Location:** `nina_planner/sequence_state.py:24-25`

#### 1. Purpose & Architectural Role
Finds the first `dict` node in `_walk(node)` whose `"Name"` field equals `name`. Used by `_target_area` (`29`), `_exposure_groups` (`110`), `_exposure` (`126`, `127`, `140`), `_horizon` (`154`), and `project_sequence_state` (`190`).

#### 2. Signature, Decorators & Inputs
```python
def _find(node: Any, name: str) -> dict[str, Any] | None:
```
- **`node` (`Any`):** Subtree to search.
- **`name` (`str`):** Exact `"Name"` value to match.

#### 3. Outputs & Return Semantics
- Returns the first matching `dict[str, Any]` in pre-order depth-first walk order, or `None` if no node has `n.get("Name") == name`.

#### 4. Step-by-Step Internal Logic
1. **Short-circuit generator search (`25`):** `return next((n for n in _walk(node) if n.get("Name") == name), None)`.

#### 5. Side Effects & I/O
- Pure in-memory search; no I/O.

#### 6. Error Handling & Edge Cases
- Stops traversal immediately on the first match via `next(..., None)`.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(N)$ worst-case in subtree size $N$.

---

### 3.3 `_target_area`

**Location:** `nina_planner/sequence_state.py:28-29`

#### 1. Purpose & Architectural Role
Locates N.I.N.A.'s `"Target Sequence_Container"` node (`_TARGET_AREA`) inside either a `/sequence/state` or `/sequence/json` payload.

#### 2. Signature, Decorators & Inputs
```python
def _target_area(payload: Any) -> dict[str, Any] | None:
```
- **`payload` (`Any`):** Full `/sequence/state` or `/sequence/json` response payload.

#### 3. Outputs & Return Semantics
- Returns the `"Target Sequence_Container"` dictionary, or `None` if absent.

#### 4. Step-by-Step Internal Logic
1. **Delegate to `_find` (`29`):** `return _find(payload, _TARGET_AREA)`.

#### 5. Side Effects & I/O
- Pure in-memory lookup; no I/O.

#### 6. Error Handling & Edge Cases
- Returns `None` when `payload` is `None`, `{}`, `[]`, or contains no `"Target Sequence_Container"`.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(N)$ in payload size.

---

### 3.4 `_issues`

**Location:** `nina_planner/sequence_state.py:32-47`

#### 1. Purpose & Architectural Role
Walks a sequence subtree (`node`) and splits all node-level `"Issues"` strings into two deduplicated lists: `(failed, preflight)`.
- **`failed`:** Issues attached to nodes whose `"Status" == "FAILED"` (real execution errors).
- **`preflight`:** Issues attached to nodes in any other status (`"CREATED"`, `"RUNNING"`, `"FINISHED"`, etc.), which represent N.I.N.A.'s static pre-flight validation checks (such as `"Focuser not connected"` before equipment is connected by the Start area).

#### 2. Signature, Decorators & Inputs
```python
def _issues(node: Any) -> tuple[list[str], list[str]]:
```
- **`node` (`Any`):** Subtree to inspect (either a single step at line `199` or the entire `state` payload at line `230`).

#### 3. Outputs & Return Semantics
- Returns `(failed, preflight)` where each element is a `list[str]` of unique strings formatted as `f"{n.get('Name')}: {issue}"` in discovery order.

#### 4. Step-by-Step Internal Logic
1. **Initialize buckets (`39-40`):** `failed: list[str] = []` and `preflight: list[str] = []`.
2. **Walk subtree (`41-46`):**
   - For each dict `n in _walk(node)` (`41`), iterates `for issue in n.get("Issues") or []:` (`42`).
   - Formats `text = f"{n.get('Name')}: {issue}"` (`43`).
   - Selects `bucket = failed if n.get("Status") == "FAILED" else preflight` (`44`).
   - Appends `text` to `bucket` if `text not in bucket` (`45-46`, order-preserving deduplication).
3. **Return tuple (`47`):** Returns `failed, preflight`.

#### 5. Side Effects & I/O
- Pure in-memory traversal; no I/O.

#### 6. Error Handling & Edge Cases
- Handles missing `"Issues"` key or `"Issues": None` via `n.get("Issues") or []`.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(N + I^2)$ where $N$ is subtree nodes and $I$ is total unique issues (typically $I < 10$).

---

### 3.5 `_children`

**Location:** `nina_planner/sequence_state.py:50-70`

#### 1. Purpose & Architectural Role
Yields a container node's **direct** child dictionaries across both N.I.N.A. serialization shapes:
- `/sequence/state` serializes `node["Items"]` as a plain `list` of child dicts.
- `/sequence/json` (Json.NET `$type` format) wraps `node["Items"]` in `{"$values": [...]}`.
Fails closed (yields nothing) on any malformed or unexpected `"Items"` value so `_counts_exposures` never raises `TypeError`.

#### 2. Signature, Decorators & Inputs
```python
def _children(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
```
- **`node` (`dict[str, Any]`):** Container node dictionary.

#### 3. Outputs & Return Semantics
- Yields each direct `dict` child inside `node["Items"]` (or `node["Items"]["$values"]`).

#### 4. Step-by-Step Internal Logic
1. **Read `"Items"` (`62`):** `items = node.get("Items")`.
2. **Unwrap Json.NET `$values` dict (`63-64`):** If `isinstance(items, dict)`, rebinds `items = items.get("$values")`.
3. **Fail closed on non-sequence (`66-67`):** If `not isinstance(items, (list, tuple))`, returns immediately (empty generator).
4. **Yield dict elements (`68-70`):** Iterates `for item in items:`; if `isinstance(item, dict)`, yields `item`.

#### 5. Side Effects & I/O
- Pure in-memory generator; no I/O.

#### 6. Error Handling & Edge Cases
- Tested thoroughly in `tests/test_sequence_state.py:ChildrenShapeTests` (`602-650`): handles `Items: None`, `Items: {}` (no `$values`), `Items: "bad"`, `Items: 42`, and non-dict elements inside `Items` lists without raising.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(|Items|)$ direct children only (no recursion).

---

### 3.6 `_counts_exposures`

**Location:** `nina_planner/sequence_state.py:73-96`

#### 1. Purpose & Architectural Role
Structural predicate identifying whether a container `node` is an **exposure group** whose loop condition counts exposures for its step. Two conditions must hold simultaneously:
1. `node.get("Conditions")` is a `list` containing at least one `dict` with key `"Iterations"` or `"CompletedIterations"`. (Other N.I.N.A. condition types—`TimeCondition`, `AboveHorizonCondition`, `AltitudeCondition`, `SafetyMonitorCondition`—never carry these keys.)
2. At least one **direct child** (`_children(node)`) has `"Name" == "Take Exposure"`. Requiring a direct child (rather than searching descendants via `_walk`) prevents an outer multi-iteration loop wrapper around an inner 5-frame exposure group from matching first in `_walk` order and falsely reporting `total: 2` (`83-87`).

#### 2. Signature, Decorators & Inputs
```python
def _counts_exposures(node: dict[str, Any]) -> bool:
```
- **`node` (`dict[str, Any]`):** Candidate AST container node.

#### 3. Outputs & Return Semantics
- Returns `True` iff `node` has an iteration condition in `node["Conditions"]` and a direct `"Take Exposure"` child in `_children(node)`.

#### 4. Step-by-Step Internal Logic
1. **Evaluate compound boolean (`88-96`):**
   - Checks `isinstance(node.get("Conditions"), list)` (`89`), AND
   - `any("Iterations" in c or "CompletedIterations" in c for c in node["Conditions"] if isinstance(c, dict))` (`90-94`), AND
   - `any(c.get("Name") == "Take Exposure" for c in _children(node))` (`95`).

#### 5. Side Effects & I/O
- Pure in-memory structural check; no I/O.

#### 6. Error Handling & Edge Cases
- Non-dict entries inside `node["Conditions"]` are guarded by `if isinstance(c, dict)` (`93`).

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(|Conditions| + |Items|)$ for `node`.

---

### 3.7 `_exposure_groups`

**Location:** `nina_planner/sequence_state.py:99-114`

#### 1. Purpose & Architectural Role
Collects all exposure-counting group containers within a target `step`, in walk order:
- N.I.N.A.'s built-in `SmartExposure` instruction serializes as `"Smart Exposure_Container"`, whereas `nina_planner`'s countdown exposure groups (`sequence/builders.py:sequence_count_exposure`) serialize as `"Exposure Group_Container"`.
- A step built with `batch_size` distributes its exposures across multiple sequential exposure groups (e.g., `L×5 + LP×3` at `batch_size=2` produces 5 groups).
- `_exposure_groups` looks up `"Smart Exposure_Container"` by name first (`named`), collects all `_counts_exposures` nodes in `_walk(step)` (`structural`), and returns `[named] + [node for node in structural if node is not named]` (or `structural` if `named is None`). This guarantees that an unexpanded `"Smart Exposure_Container"` (which may lack direct children in synthetic/unexpanded states) is still found by name, while expanded `"Smart Exposure_Container"` nodes are not duplicated (`node is not named`).

#### 2. Signature, Decorators & Inputs
```python
def _exposure_groups(step: dict[str, Any]) -> list[dict[str, Any]]:
```
- **`step` (`dict[str, Any]`):** One step container from `"Target Sequence_Container"`.

#### 3. Outputs & Return Semantics
- Returns `list[dict[str, Any]]` of all exposure group nodes in `step`. Returns `[]` if `step` contains no exposure groups (e.g., a wait/park-only step).

#### 4. Step-by-Step Internal Logic
1. **Find named Smart Exposure container (`110`):** `named = _find(step, "Smart Exposure_Container")`.
2. **Collect all structural matches (`111`):** `structural = [node for node in _walk(step) if _counts_exposures(node)]`.
3. **Merge without identity duplication (`112-114`):** If `named is None`, returns `structural` (`113`); otherwise returns `[named] + [node for node in structural if node is not named]` (`114`).

#### 5. Side Effects & I/O
- Pure in-memory tree walk; no I/O.

#### 6. Error Handling & Edge Cases
- Uses object identity (`node is not named`) rather than value equality (`!=`) so two distinct structural exposure groups with identical initial dict contents are both kept while `named` itself is not duplicated.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(S)$ where $S$ is the number of AST nodes in `step`.

---

### 3.8 `_exposure`

**Location:** `nina_planner/sequence_state.py:117-150`

#### 1. Purpose & Architectural Role
Extracts the compact exposure telemetry dictionary (`seconds`, `gain`, `offset`, `binning`, `filter`, `completed`, `total`) for a single exposure `group` within `step` (or falls back to searching `step` directly when `group=None` or when `group` has no `"Take Exposure"` child).

#### 2. Signature, Decorators & Inputs
```python
def _exposure(
    step: dict[str, Any], group: dict[str, Any] | None = None
) -> dict[str, Any] | None:
```
- **`step` (`dict[str, Any]`):** Enclosing step dictionary.
- **`group` (`dict[str, Any] | None`, default `None`):** Specific exposure group container from `_exposure_groups(step)`, or `None`.

#### 3. Outputs & Return Semantics
- Returns `None` if both `group is None` and `_find(step, "Take Exposure")` is `None`.
- Otherwise returns a 7-key `dict[str, Any]`:
  - `"seconds"`: `ExposureTime` from `Take Exposure` (or `None`).
  - `"gain"`: `Gain` from `Take Exposure` (or `None`).
  - `"offset"`: `Offset` from `Take Exposure` (or `None`).
  - `"binning"`: `f"{binning['X']}x{binning['Y']}"` if `Take Exposure.Binning` is non-empty, else `None`.
  - `"filter"`: `Switch Filter.Filter._name` inside `group` (if `group` is given and `Filter` is a `dict`), else `None`.
  - `"completed"`: `loop.get("CompletedIterations")` from `group`'s iteration loop condition (or `None`).
  - `"total"`: `loop.get("Iterations", (take or {}).get("ExposureCount"))`.

#### 4. Step-by-Step Internal Logic
1. **Locate `"Take Exposure"` node (`126-129`):**
   - `take = _find(group, "Take Exposure") if group else None` (`126`).
   - `take = take or _find(step, "Take Exposure")` (`127`, step-wide fallback for unexpanded groups).
   - If `group is None and take is None` (`128`), returns `None` (`129`).
2. **Locate iteration loop condition on `group` (`132-139`):**
   - Searches `(group or {}).get("Conditions") or []` for the first condition dict containing `"Iterations"` or `"CompletedIterations"`, defaulting to `{}`.
3. **Extract filter and binning (`140-141`):**
   - `flt = (_find(group, "Switch Filter") or {}).get("Filter") if group else None` (`140`).
   - `binning = (take or {}).get("Binning") or {}` (`141`).
4. **Construct and return exposure summary dict (`142-150`):**
   - Populates `"seconds"`, `"gain"`, `"offset"`, `"binning"`, `"filter"`, `"completed"`, and `"total"`.

#### 5. Side Effects & I/O
- Pure in-memory dictionary extraction; no I/O.

#### 6. Error Handling & Edge Cases
- **Why `loop.get("Iterations")` is preferred over node-level `Iterations` (`130-131`):** At the container node level, N.I.N.A.'s `/sequence/state` reports *completed* iterations under `"Iterations"`, whereas `/sequence/json` reports *total* iterations under `"Iterations"`. Inside `Conditions[]`, `LoopCondition` consistently uses `"CompletedIterations"` for completed and `"Iterations"` for total.
- **Unexpanded `SmartExposure` fallback (`149`):** When `loop` has no `"Iterations"`, `loop.get("Iterations", (take or {}).get("ExposureCount"))` falls back to `Take Exposure.ExposureCount`.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(|group| + |step|)$ AST nodes.

---

### 3.9 `_horizon`

**Location:** `nina_planner/sequence_state.py:153-161`

#### 1. Purpose & Architectural Role
Extracts altitude wait telemetry from a `"Wait for Altitude"` instruction node inside a step from `/sequence/json` (`json_step`). `/sequence/state` omits these parameters on `WaitForAltitude`, so `get_sequence_state` fetches `/sequence/json` alongside `/sequence/state` and passes each paired `json_steps[i]` to `_horizon`.

#### 2. Signature, Decorators & Inputs
```python
def _horizon(json_step: Any) -> dict[str, Any] | None:
```
- **`json_step` (`Any`):** Corresponding step dictionary from `/sequence/json`.

#### 3. Outputs & Return Semantics
- Returns `None` if no `"Wait for Altitude"` node exists under `json_step`.
- Otherwise returns:
  - `"current_altitude"`: `wait.get("CurrentAltitude")`
  - `"min_altitude"`: `wait.get("Altitude")`
  - `"expected_time"`: `wait.get("ExpectedTime")`

#### 4. Step-by-Step Internal Logic
1. **Search for `"Wait for Altitude"` (`154-156`):** Calls `wait = _find(json_step, "Wait for Altitude")` (`154`); if `wait is None`, returns `None` (`155-156`).
2. **Build horizon dict (`157-161`):** Returns `{"current_altitude": wait.get("CurrentAltitude"), "min_altitude": wait.get("Altitude"), "expected_time": wait.get("ExpectedTime")}`.

#### 5. Side Effects & I/O
- Pure in-memory lookup; no I/O.

#### 6. Error Handling & Edge Cases
- Steps without `"Wait for Altitude"` (such as darks, biases, or flats) cleanly return `None`.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(|json\_step|)$.

---

### 3.10 `project_sequence_state`

**Location:** `nina_planner/sequence_state.py:164-241`

#### 1. Purpose & Architectural Role
Main projection entrypoint converting N.I.N.A.'s raw `/sequence/state` response (`state`) and optional `/sequence/json` response (`json_state`) into the compact `"summary"` view returned by `get_sequence_state(detail="summary")`.

For every step in `"Target Sequence_Container"`, it projects:
- Step index, container `name`, and `target_name` (`Target.TargetName`).
- Plan attribution (`plan_id` and 1-based `pointing_index`) parsed from `target_name` via `models.plan.ATTRIBUTION_RE`.
- `frame_type` (`Take Exposure.ImageType`), step `status`, and active leaf instruction `phase` (first `RUNNING` leaf node under `step["Items"]` without its own `"Items"` key).
- Per-step failed `issues`, primary `exposure` (`groups[0]`), full `exposure_groups` list, paired `horizon` data, and `fired_events` (`Send WebSocket Event` nodes with `Status == "FINISHED"` and non-empty `Message`).

At the top level, it rolls up overall `running` status, target area `status`, `step_count`, `completed_steps`, `parse_error`, global failed `issues` (prefixed with any target-area `parse_error` diagnostic), and global `preflight_issues`.

#### 2. Signature, Decorators & Inputs
```python
def project_sequence_state(state: Any, json_state: Any = None) -> dict[str, Any]:
```
- **`state` (`Any`):** Decoded JSON payload from N.I.N.A. `GET /sequence/state`.
- **`json_state` (`Any`, default `None`):** Optional decoded JSON payload from N.I.N.A. `GET /sequence/json` (or `None` if unavailable/failed).

#### 3. Outputs & Return Semantics
- Returns a top-level summary `dict[str, Any]` with 8 keys:
  - `"running"` (`bool`): `True` if any node anywhere in `state` has `"Status" == "RUNNING"`.
  - `"status"` (`str | None`): `"Status"` of `"Target Sequence_Container"` (or `None`).
  - `"step_count"` (`int`): `len(steps)`.
  - `"completed_steps"` (`int`): Count of steps where `s["status"] == "FINISHED"`.
  - `"parse_error"` (`bool`): `True` iff `state` is truthy (non-empty) but contains no `"Target Sequence_Container"`.
  - `"issues"` (`list[str]`): Target-area missing diagnostic (if `parse_error`) followed by all `FAILED`-node issues across the entire `state` tree.
  - `"preflight_issues"` (`list[str]`): All non-`FAILED` node issues across the entire `state` tree.
  - `"steps"` (`list[dict[str, Any]]`): Per-step projection dicts, each containing:
    `index`, `name`, `target_name`, `plan_id`, `pointing_index`, `frame_type`, `status`, `phase`, `issues`, `exposure`, `exposure_groups`, `horizon`, and `fired_events`.

#### 4. Step-by-Step Internal Logic
1. **Locate target area and check for `parse_error` (`173-178`):**
   - `area = _target_area(state)` (`173`).
   - `parse_error = area is None and bool(state)` (`174`).
   - Initializes `issues: list[str] = []` (`175`); if `parse_error`, appends `f"target area '{_TARGET_AREA}' not found in /sequence/state"` (`176-177`).
   - Normalizes `area = area or {}` (`178`).
2. **Extract step lists and verify index pairing (`179-185`):**
   - `json_steps = (_target_area(json_state) or {}).get("Items") or []` (`179`).
   - `state_steps = area.get("Items") or []` (`180`).
   - `paired = [s.get("Name") for s in json_steps] == [s.get("Name") for s in state_steps]` (`185`).
3. **Project each target step (`186-229`):**
   - Initializes `steps: list[dict[str, Any]] = []` (`186`) and loops `for i, step in enumerate(state_steps):` (`187`):
     1. **Target name & attribution (`188-189`):** Reads `name = ((step.get("Target") or {}).get("TargetName")) or None` (`188`) and matches `match = ATTRIBUTION_RE.search(name or "")` (`189`), extracting `plan_id = match.group(1)` (`213`) and `pointing_index = int(match.group(2)) if match and match.group(2) else None` (`214-216`).
     2. **Frame type (`190, 217`):** Finds `take = _find(step, "Take Exposure")` (`190`) and reads `(take or {}).get("ImageType")` (`217`).
     3. **Running leaf phase (`191-198`):** Finds the first node `n` in `_walk(step.get("Items"))` where `n.get("Status") == "RUNNING" and "Items" not in n` (leaf instruction, not a container), extracting `n.get("Name")`.
     4. **Step issues (`199`):** Calls `failed, _ = _issues(step)`.
     5. **Fired WebSocket events (`200-206`):** Collects `n["Message"]` for every node `n` in `_walk(step)` where `n.get("Name") == "Send WebSocket Event"`, `n.get("Status") == "FINISHED"`, and `n.get("Message")` is truthy.
     6. **Exposure groups (`207, 224-225`):** Calls `groups = _exposure_groups(step)` (`207`), sets `"exposure": _exposure(step, groups[0] if groups else None)` (`224`), and sets `"exposure_groups": [_exposure(step, group) for group in groups]` (`225`).
     7. **Horizon (`226`):** Sets `"horizon": _horizon(json_steps[i]) if paired else None`.
     8. Appends the 13-key step dictionary to `steps` (`208-229`).
4. **Collect whole-tree issues and return top-level summary (`230-241`):**
   - Calls `failed_all, preflight_all = _issues(state)` (`230`) and extends `issues.extend(failed_all)` (`231`).
   - Returns the 8-key summary dictionary (`232-241`).

```mermaid
flowchart TD
    A["project_sequence_state(state, json_state=None)"] --> B["area = _target_area(state)<br/>parse_error = (area is None and bool(state))"]
    B --> C{"parse_error?"}
    C -- Yes --> D["issues = [\"target area 'Target Sequence_Container' not found in /sequence/state\"]"]
    C -- No --> E["issues = []"]
    D --> F["state_steps = (area or {}).get('Items') or []<br/>json_steps = (_target_area(json_state) or {}).get('Items') or []"]
    E --> F
    F --> G["paired = ([s.get('Name') for s in json_steps] == [s.get('Name') for s in state_steps])"]
    G --> H["For each (i, step) in enumerate(state_steps):"]
    H --> I["1. Parse TargetName via ATTRIBUTION_RE -> (plan_id, pointing_index)<br/>2. Find 'Take Exposure' -> frame_type<br/>3. Find first RUNNING leaf ('Items' not in n) -> phase<br/>4. Collect step _issues(step) -> failed<br/>5. Collect FINISHED 'Send WebSocket Event' messages -> fired"]
    I --> J["groups = _exposure_groups(step)<br/>exposure = _exposure(step, groups[0] if groups else None)<br/>exposure_groups = [_exposure(step, g) for g in groups]<br/>horizon = _horizon(json_steps[i]) if paired else None"]
    J --> K["Append step summary dict to steps"]
    K --> H
    H -- Done --> L["failed_all, preflight_all = _issues(state)<br/>issues.extend(failed_all)"]
    L --> M(["Return {running, status, step_count, completed_steps, parse_error, issues, preflight_issues, steps}"])
```

#### 5. Side Effects & I/O
- Pure in-memory transformation; performs no network or filesystem I/O.

#### 6. Error Handling & Edge Cases
- **Empty sequencer (`state == []` or `state == {}`):** `bool(state)` is `False`, so `parse_error` is `False`, `step_count` is `0`, and `running` is `False`.
- **Unrecognized non-empty payload (`state == [{"Name": "CustomRoot"}]`):** `area` is `None` and `bool(state)` is `True`, so `parse_error` is `True` and `issues` explains that `'Target Sequence_Container'` was not found (`174-177`).
- **Start/End area running while Target area is idle:** `"running"` (`233`) walks the *entire* `state` tree (`_walk(state)`), so a running `Cool Camera` in the Start area or `Park Scope` in the End area correctly sets `running: True` even when no target step is `RUNNING`.
- **Mismatched `/sequence/json` step list:** If `/sequence/json` fails to load (`json_state=None`) or has a different step `Name` sequence than `/sequence/state`, `paired` is `False` (unless `state_steps` is also empty) and every step safely receives `"horizon": None`.

#### 7. Complexity & Risk Notes
- **Time Complexity:** $O(N)$ where $N$ is the total number of AST nodes across `state` and `json_state`.
