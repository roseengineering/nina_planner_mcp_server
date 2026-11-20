# Module Specification: `nina_planner/sequence/nodes.py`

> **Navigation:** [⬆️ Up to Atlas Index](../../README.md) | [⬆️ Sequence Package](./README.md) | **Next Layer:** [`instructions.md`](./instructions.md) · [`builders.md`](./builders.md) · [`targets-and-variants.md`](./targets-and-variants.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/sequence/nodes.py`
- **Line Count:** 346 lines (`1-346`)
- **Module Docstring (`lines 1-5`):**
  > Sequence node builders: containers, triggers and coordinate encodings.
  > Nodes are plain JSON dicts shaped for N.I.N.A.'s JSON.NET deserializer — `$type`/`$id`/`Parent` bookkeeping included (see `_add_refs`).
- **Architectural Layer:** Layer 1 — AST Primitives, Coordinate Encoders, Round-Robin Batcher, Containers, Triggers, and JSON.NET Post-Processing Passes.
- **Inbound Callers:**
  - `nina_planner/sequence/instructions.py` (imports `_azalt_coordinates`, `_child`, `_container_base`)
  - `nina_planner/sequence/builders.py` (imports `container_deepsky`, `container_end`, `container_root`, `container_sequential`, `container_start`, `container_target`)
  - `nina_planner/sequence/targets.py` (imports `NINA_FLATS_ALTITUDE`, `NINA_FLATS_AZIMUTH_DAWN`, `NINA_FLATS_AZIMUTH_DUSK`, `_round_robin`, `container_sequential`, and all 7 `trigger_*` functions)
  - `nina_planner/sequence/__init__.py` (re-exports 20 symbols)
  - `tests/test_sequence.py` & `tests/sequence_helpers.py`
- **Outbound Imports (`lines 7-10`):**
  - `os` (standard library, line `7`)
  - `typing.Any` (standard library, line `8`)
  - `..models.plan.LightExposurePlan` (domain model, line `10`)

### 1.1 Module-Level Constants (`lines 12-14`)

Evaluated once at module import time from environment variables (with numeric string fallbacks converted to `float`):

| Constant | Line | Env Var | Default | Purpose |
| :--- | ---: | :--- | ---: | :--- |
| `NINA_FLATS_ALTITUDE` | `12` | `"NINA_FLATS_ALTITUDE"` | `80.0` | Altitude in degrees to which the mount slews for twilight sky flats (`targets.py:244`). |
| `NINA_FLATS_AZIMUTH_DAWN` | `13` | `"NINA_FLATS_AZIMUTH_DAWN"` | `270.0` | Azimuth in degrees (West, away from rising sun) for dawn twilight flats (`targets.py:243`). |
| `NINA_FLATS_AZIMUTH_DUSK` | `14` | `"NINA_FLATS_AZIMUTH_DUSK"` | `90.0` | Azimuth in degrees (East, away from setting sun) for dusk twilight flats (`targets.py:241`). |

### 1.2 CLR `$type` Strings Emitted in `nodes.py`

| Helper / Factory | CLR `$type` String |
| :--- | :--- |
| `_radec_coordinates` | `"NINA.Astrometry.InputCoordinates, NINA.Astrometry"` |
| `_azalt_coordinates` | `"NINA.Astrometry.InputTopocentricCoordinates, NINA.Astrometry"` |
| `_container_base` (`Strategy`) | `"NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"` |
| `_container_base` (`Conditions`) | `"System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.Conditions.ISequenceCondition, NINA.Sequencer]], System.ObjectModel"` |
| `_container_base` (`Items`) | `"System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.SequenceItem.ISequenceItem, NINA.Sequencer]], System.ObjectModel"` |
| `_container_base` (`Triggers`) | `"System.Collections.ObjectModel.ObservableCollection`1[[NINA.Sequencer.Trigger.ISequenceTrigger, NINA.Sequencer]], System.ObjectModel"` |
| `container_root` | `"NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer"` |
| `container_start` | `"NINA.Sequencer.Container.StartAreaContainer, NINA.Sequencer"` |
| `container_target` | `"NINA.Sequencer.Container.TargetAreaContainer, NINA.Sequencer"` |
| `container_end` | `"NINA.Sequencer.Container.EndAreaContainer, NINA.Sequencer"` |
| `container_sequential` | `"NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer"` |
| `container_deepsky` | `"NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer"` |
| `container_deepsky` (`ExposureInfoList`) | `"NINA.Core.Utility.AsyncObservableCollection`1[[NINA.Sequencer.Utility.ExposureInfo, NINA.Sequencer]], NINA.Core"` |
| `container_deepsky` (`Target`) | `"NINA.Astrometry.InputTarget, NINA.Astrometry"` |
| `trigger_meridian_flip` | `"NINA.Sequencer.Trigger.MeridianFlip.MeridianFlipTrigger, NINA.Sequencer"` |
| `trigger_autofocus_after_filter_change` | `"NINA.Sequencer.Trigger.Autofocus.AutofocusAfterFilterChange, NINA.Sequencer"` |
| `trigger_autofocus_after_temperature_change` | `"NINA.Sequencer.Trigger.Autofocus.AutofocusAfterTemperatureChangeTrigger, NINA.Sequencer"` |
| `trigger_autofocus_after_exposures` | `"NINA.Sequencer.Trigger.Autofocus.AutofocusAfterExposures, NINA.Sequencer"` |
| `trigger_autofocus_after_hfr_increase` | `"NINA.Sequencer.Trigger.Autofocus.AutofocusAfterHFRIncreaseTrigger, NINA.Sequencer"` |
| `trigger_center_after_drift` | `"NINA.Sequencer.Trigger.Platesolving.CenterAfterDriftTrigger, NINA.Sequencer"` |
| `trigger_restore_guiding` | `"NINA.Sequencer.Trigger.Guider.RestoreGuiding, NINA.Sequencer"` |

---

## 2. Function & Method Reference

### 2.1 `_fix_provider` (`nodes.py:17-36`)

1. **Purpose & Architectural Role:**
   Post-processing pass invoked by `container_root` (after `_add_refs`) that deduplicates N.I.N.A. date/time provider nodes (`SelectedProvider`) across the entire sequence tree by replacing duplicate provider dicts of the same `$type` with JSON.NET `{"$ref": "<id>"}` pointers.
2. **Signature, Decorators & Inputs:**
   ```python
   def _fix_provider(data: dict[str, Any]) -> dict[str, Any]:
   ```
   - `data: dict[str, Any]` — Root sequence dictionary that has already been numbered by `_add_refs` (so every `"SelectedProvider"` dict already has a populated string `"$id"`).
   - Inner closure: `def fn(data: dict[str, Any]) -> None` (`lines 20-33`).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — The mutated `data` dictionary where the first occurrence of each `"SelectedProvider"` `$type` remains a full dict and all subsequent occurrences with that `$type` are replaced by `{"$ref": "<first_id>"}`.
4. **Step-by-Step Internal Logic:**
   1. Line `18`: Initializes `store: dict[str, str] = {}` mapping provider `$type` strings to their first-seen `$id` string.
   2. Lines `20-33`: Defines recursive helper `fn(data: dict[str, Any]) -> None`:
      - Line `21`: Iterates `for k, d in data.items():`.
      - Line `22`: If `isinstance(d, dict)`:
        - Line `23`: Checks `if k == "SelectedProvider":`.
        - Line `24`: Reads `ptype = d["$type"]`.
        - Lines `25-26`: If `ptype in store`, mutates `data[k] = {"$ref": store[ptype]}`.
        - Lines `27-28`: Otherwise, records `store[ptype] = d["$id"]`.
        - Lines `29-30`: When `k != "SelectedProvider"`, recurses `fn(d)`.
      - Lines `31-33`: `elif isinstance(d, list):` iterates `for item in d: fn(item)`.
   3. Line `35`: Invokes `fn(data)` on the root dict.
   4. Line `36`: Returns `data`.

   ```mermaid
   flowchart TD
       Start(["_fix_provider(data)"]) --> Init["store: dict[str, str] = {} (line 18)"]
       Init --> CallFn["fn(data) (line 35)"]
       CallFn --> Loop["For k, d in data.items() (line 21)"]
       Loop --> IsDict{"isinstance(d, dict)? (line 22)"}
       IsDict -- Yes --> IsSP{"k == 'SelectedProvider'? (line 23)"}
       IsSP -- Yes --> GetType["ptype = d['$type'] (line 24)"]
       GetType --> InStore{"ptype in store? (line 25)"}
       InStore -- Yes --> Replace["data[k] = {'$ref': store[ptype]} (line 26)"]
       InStore -- No --> Save["store[ptype] = d['$id'] (line 28)"]
       IsSP -- No --> RecurseDict["fn(d) (line 30)"]
       IsDict -- No --> IsList{"isinstance(d, list)? (line 31)"}
       IsList -- Yes --> LoopList["For item in d: fn(item) (lines 32-33)"]
       IsList -- No --> NextItem["Next item in data.items()"]
       Replace & Save & RecurseDict & LoopList --> NextItem
       NextItem --> DoneLoop{"More items?"}
       DoneLoop -- Yes --> Loop
       DoneLoop -- No --> Return(["return data (line 36)"])
   ```

5. **Side Effects & I/O:**
   - Mutates `data` in-place (replaces duplicate `"SelectedProvider"` dict values). Performs no I/O.
6. **Error Handling & Edge Cases:**
   - Assumes every `"SelectedProvider"` dict has `"$type"` and `"$id"` keys (`KeyError` if called before `_add_refs` only to the extent `"$id"` would be `None`).
   - Assumes list elements inside the sequence tree are dicts (since N.I.N.A. collections live inside `{"$values": [...]}` where elements are node dicts).
7. **Complexity & Risk Notes:**
   - Time complexity: $O(N)$ where $N$ is the total number of dicts/keys in the AST; space complexity: $O(D + P)$ where $D$ is maximum tree depth and $P \le 5$ is the number of distinct `DateTimeProvider` types.

---

### 2.2 `_add_refs` (`nodes.py:39-84`)

1. **Purpose & Architectural Role:**
   Primary JSON.NET graph linker invoked by `container_root`. Assigns sequential 1-based string `"$id"` values (`"1"`, `"2"`, ...) in pre-order traversal, sets `"Parent"` to `{"$ref": "<enclosing_id>"}` (or `None` at the root), and reorders `"Parent"` to the **end** of leaf dictionaries while keeping `"Parent"` **before** `"Items"`/`"Conditions"` on container dictionaries.
2. **Signature, Decorators & Inputs:**
   ```python
   def _add_refs(data: dict[str, Any]) -> dict[str, Any]:
   ```
   - `data: dict[str, Any]` — Unnumbered root node dictionary tree where nodes created by `_child` and `_values` carry `"$id": None`.
   - Inner closure: `def fn(data: dict[str, Any], parent: dict[str, Any] | None = None) -> None` (`lines 62-81`).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — The in-place mutated `data` tree with every `"$id"` assigned a unique stringified positive integer and every `"Parent"` wired and ordered for JSON.NET deserialization.
4. **Step-by-Step Internal Logic:**
   1. Line `60`: Initializes counter `ident = 0`.
   2. Lines `62-81`: Defines recursive closure `fn(data: dict[str, Any], parent: dict[str, Any] | None = None) -> None`:
      - Line `63`: Declares `nonlocal ident`.
      - Line `64`: Checks `if "$id" in data:`.
      - Lines `65-66`: Increments `ident += 1` and assigns `data["$id"] = str(ident)`.
      - Line `67`: Checks `if "Parent" in data:`:
        - Line `68`: Assigns `data["Parent"] = parent` (`None` for the root node, `{"$ref": "<id>"}` for descendants).
        - Lines `69-70`: Checks `if "Instructions" not in data:` and updates the downward `parent = {"$ref": str(ident)}` for child calls.
        - Lines `71-75`: Checks `if "Items" not in data and "Conditions" not in data:`. If true (the node is a leaf instruction, trigger, condition, or helper object rather than a container), executes `data["Parent"] = data.pop("Parent")` to move `"Parent"` to the final key position in Python's insertion-ordered dict.
      - Lines `76-81`: Iterates `for d in data.values():`:
        - Lines `77-78`: If `isinstance(d, dict): fn(d, parent)`.
        - Lines `79-81`: `elif isinstance(d, list):` iterates `for item in d: fn(item, parent)`.
   3. Line `83`: Invokes `fn(data)` with default `parent=None`.
   4. Line `84`: Returns `data`.

   ```mermaid
   flowchart TD
       Start(["_add_refs(data)"]) --> Init["ident = 0 (line 60)"]
       Init --> CallRoot["fn(data, parent=None) (line 83)"]
       CallRoot --> HasId{"'$id' in data? (line 64)"}
       HasId -- Yes --> AssignId["ident += 1\ndata['$id'] = str(ident) (lines 65-66)"]
       AssignId --> HasParent{"'Parent' in data? (line 67)"}
       HasParent -- Yes --> SetParent["data['Parent'] = parent (line 68)"]
       SetParent --> HasInstr{"'Instructions' not in data? (line 69)"}
       HasInstr -- Yes --> NewParent["parent = {'$ref': str(ident)} (line 70)"]
       HasInstr -- No --> CheckLeaf
       NewParent --> CheckLeaf{"'Items' not in data and\n'Conditions' not in data? (line 71)"}
       CheckLeaf -- "Yes (Leaf)" --> Reorder["data['Parent'] = data.pop('Parent')\n(Parent moved to last key, line 75)"]
       CheckLeaf -- "No (Container)" --> KeepOrder["Keep 'Parent' before 'Items'/'Conditions'"]
       HasId -- No --> WalkValues
       HasParent -- No --> WalkValues
       Reorder & KeepOrder --> WalkValues["For d in data.values() (line 76)"]
       WalkValues --> ValType{"Type of d? (lines 77-81)"}
       ValType -- "dict" --> RecDict["fn(d, parent) (line 78)"]
       ValType -- "list" --> RecList["For item in d: fn(item, parent) (lines 80-81)"]
       ValType -- "scalar / None" --> NextVal["Next value"]
       RecDict & RecList --> NextVal
       NextVal --> Return(["return data (line 84)"])
   ```

5. **Side Effects & I/O:**
   - Mutates `data` and all nested dicts in-place (`"$id"`, `"Parent"`, and dict key insertion order). No external I/O.
6. **Error Handling & Edge Cases:**
   - **Critical JSON.NET Deserialization Edge Case 1 (Leaf Key Order):** When `_child(ctype) | {"Identifier": name, ...}` is constructed, `"Parent"` is the 3rd key (before `"Identifier"`). Moving `"Parent"` to the end via `data.pop("Parent")` on leaves guarantees JSON.NET populates `SetVariable.Identifier` *before* invoking the `Parent` setter.
   - **Critical JSON.NET Deserialization Edge Case 2 (Container Key Order):** Because `"Items"` (and `"Conditions"`) are present on containers, line `71` evaluates `False`, keeping `"Parent"` ahead of `"Items"` so `data.values()` traverses `"Parent"` before `"Items"` and JSON.NET attaches the container to its parent before deserializing its children.
   - **Wrapper Dicts Without `"Parent"` (`_values`, `_radec_coordinates`, `_azalt_coordinates`):** Have `"$id": None` but no `"Parent"` key. They receive a unique `"$id"` (lines `65-66`), skip lines `67-75`, and pass the unchanged `parent` reference through to their children (such as the items inside `"$values"`).
7. **Complexity & Risk Notes:**
   - Time complexity: $O(N)$ over all AST nodes. Mutates dicts in-place; calling `_add_refs` twice on the same structure remains idempotent in numbering and key ordering.

---

### 2.3 `_child` (`nodes.py:87-88`)

1. **Purpose & Architectural Role:**
   Low-level factory for any N.I.N.A. node that participates in the `$id` and `Parent` hierarchy (containers, instructions, conditions, triggers, strategies, targets, and expressions).
2. **Signature, Decorators & Inputs:**
   ```python
   def _child(ctype: str) -> dict[str, Any]:
   ```
   - `ctype: str` — Assembly-qualified .NET CLR type string (e.g., `"NINA.Sequencer.SequenceItem.Telescope.ParkScope, NINA.Sequencer"`).
3. **Outputs & Return Semantics:**
   - Returns `{"$id": None, "$type": ctype, "Parent": None}`.
4. **Step-by-Step Internal Logic:**
   1. Line `88`: Constructs and returns a new 3-key dictionary `{"$id": None, "$type": ctype, "Parent": None}`. Callers merge item-specific properties via Python's `|` dict union operator (`_child(...) | {...}`).
5. **Side Effects & I/O:** None (pure factory).
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$. Key insertion order puts `"Parent"` before merged keys, which `_add_refs` relies on for containers and explicitly reverses (`pop`) for leaves.

---

### 2.4 `_values` (`nodes.py:91-94`)

1. **Purpose & Architectural Role:**
   Low-level factory for JSON.NET collection wrappers (`ObservableCollection<T>` and `AsyncObservableCollection<T>`), which serialize with `"$id"`, `"$type"`, and `"$values"` (and no `"Parent"` key).
2. **Signature, Decorators & Inputs:**
   ```python
   def _values(ctype: str, values: list[Any] | None = None) -> dict[str, Any]:
   ```
   - `ctype: str` — CLR collection `$type` string.
   - `values: list[Any] | None = None` — List of child node dicts; defaults to `None` (converted to `[]`).
3. **Outputs & Return Semantics:**
   - Returns `{"$id": None, "$type": ctype, "$values": values}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `92-93`: If `values is None`, initializes a fresh list `values = []`.
   2. Line `94`: Returns `{"$id": None, "$type": ctype, "$values": values}`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Avoids mutable default argument hazards by substituting `None` with `[]` on lines `92-93`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.5 `_to_minutes` (`nodes.py:97-98`)

1. **Purpose & Architectural Role:**
   Extracts the integer arcminutes/time-minutes component ($0 \dots 59$) from a floating-point degree or hour value for N.I.N.A. coordinate nodes.
2. **Signature, Decorators & Inputs:**
   ```python
   def _to_minutes(deg: float) -> int:
   ```
   - `deg: float` — Coordinate value in decimal hours (RA) or decimal degrees (Dec, Az, Alt).
3. **Outputs & Return Semantics:**
   - Returns `int` — Truncated whole minutes of the fractional part of `abs(deg)`.
4. **Step-by-Step Internal Logic:**
   1. Line `98`: Computes `abs(deg) % 1` to isolate the fractional degree/hour, multiplies by `60`, and truncates to `int`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Uses `abs(deg)` so negative declinations produce positive minute components (`NegativeDec` carries the sign separately in `_radec_coordinates`).
7. **Complexity & Risk Notes:** $O(1)$. Note that if `_to_seconds` rounds up to `60` on a boundary value, `_to_minutes` does not carry the overflow into minutes.

---

### 2.6 `_to_seconds` (`nodes.py:101-102`)

1. **Purpose & Architectural Role:**
   Extracts the integer arcseconds/time-seconds component from a floating-point degree or hour value for N.I.N.A. coordinate nodes.
2. **Signature, Decorators & Inputs:**
   ```python
   def _to_seconds(deg: float) -> int:
   ```
   - `deg: float` — Coordinate value in decimal hours (RA) or decimal degrees (Dec, Az, Alt).
3. **Outputs & Return Semantics:**
   - Returns `int` — Integer seconds derived from the fractional minute remainder, rounded to 4 decimal places before `int()` truncation.
4. **Step-by-Step Internal Logic:**
   1. Line `102`: Computes `(abs(deg) * 60 % 1) * 60`, rounds to 4 decimal places via `round(..., 4)` to counteract IEEE-754 binary floating-point representation drift (e.g., `29.999999999999993 -> 30.0`), and converts to `int`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `round(..., 4)` prevents `int()` from truncating exact integer seconds down by 1 due to float imprecision, though values within $< 5 \times 10^{-5}$ seconds of `60.0` can round to `60`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.7 `_radec_coordinates` (`nodes.py:105-118`)

1. **Purpose & Architectural Role:**
   Encodes decimal J2000 Right Ascension (`ra` in hours) and Declination (`dec` in degrees) into N.I.N.A.'s sexagesimal `InputCoordinates` AST node for `container_deepsky`.
2. **Signature, Decorators & Inputs:**
   ```python
   def _radec_coordinates(ra: float, dec: float) -> dict[str, Any]:
   ```
   - `ra: float` — Right Ascension in decimal hours ($[0, 24)$).
   - `dec: float` — Declination in decimal degrees ($[-90, +90]$).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` with `"$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry"`, sexagesimal integer fields (`RAHours`, `RAMinutes`, `RASeconds`, `DecDegrees`, `DecMinutes`, `DecSeconds`), and boolean `NegativeDec`.
4. **Step-by-Step Internal Logic:**
   1. Line `106`: Evaluates `negative = dec < 0`. This preserves Southern Hemisphere negative zero-degree declinations (e.g., `-0.5°`, where `int(abs_dec)` is `0` but `NegativeDec` must be `True`).
   2. Line `107`: Computes `abs_dec = abs(dec)`.
   3. Lines `108-118`: Returns dictionary with `"$id": None`, `"$type": "NINA.Astrometry.InputCoordinates, NINA.Astrometry"`, `"RAHours": int(ra)`, `"RAMinutes": _to_minutes(ra)`, `"RASeconds": _to_seconds(ra)`, `"DecDegrees": int(abs_dec)`, `"DecMinutes": _to_minutes(abs_dec)`, `"DecSeconds": _to_seconds(abs_dec)`, and `"NegativeDec": negative`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Properly handles `-1.0 < dec < 0.0` via explicit `"NegativeDec": dec < 0` alongside non-negative `DecDegrees`/`DecMinutes`/`DecSeconds`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.8 `_azalt_coordinates` (`nodes.py:121-131`)

1. **Purpose & Architectural Role:**
   Encodes topocentric Azimuth (`az`) and Altitude (`alt`) in decimal degrees into N.I.N.A.'s sexagesimal `InputTopocentricCoordinates` AST node for `slew_to_azalt` (`instructions.py:98-102`).
2. **Signature, Decorators & Inputs:**
   ```python
   def _azalt_coordinates(az: float, alt: float) -> dict[str, Any]:
   ```
   - `az: float` — Topocentric azimuth in degrees.
   - `alt: float` — Topocentric altitude in degrees.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` with `"$type": "NINA.Astrometry.InputTopocentricCoordinates, NINA.Astrometry"` and integer fields `AzDegrees`, `AzMinutes`, `AzSeconds`, `AltDegrees`, `AltMinutes`, `AltSeconds`.
4. **Step-by-Step Internal Logic:**
   1. Lines `122-131`: Constructs and returns the dictionary splitting `az` and `alt` into integer degrees (`int(...)`), minutes (`_to_minutes(...)`), and seconds (`_to_seconds(...)`).
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Assumes non-negative altitude (such as `NINA_FLATS_ALTITUDE = 80.0`).
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.9 `_round_robin` (`nodes.py:134-160`)

1. **Purpose & Architectural Role:**
   Splits a list of `LightExposurePlan` filter entries into interleaved `(count, exposure_time_seconds, filter_name)` batch tuples according to `batch_size`. Used exclusively by `target_payload_lights` (`targets.py:291`) so multi-filter light runs interleave filter acquisitions (e.g., `L5/R5` with `batch_size=2` produces `L2, R2, L2, R2, L1, R1`).
2. **Signature, Decorators & Inputs:**
   ```python
   def _round_robin(
       exposures: list[LightExposurePlan], batch_size: int = 0
   ) -> list[tuple[int, float, str]]:
   ```
   - `exposures: list[LightExposurePlan]` — Ordered list of per-filter light exposure plans, each exposing `.total_count: int`, `.exposure_time_seconds: float`, and `.filter_name: str`.
   - `batch_size: int = 0` — Maximum number of exposures to shoot per filter before cycling to the next filter (`0` disables batching and emits one full group per filter).
3. **Outputs & Return Semantics:**
   - Returns `list[tuple[int, float, str]]` — Ordered list of `(count, exposure_time_seconds, filter_name)` tuples representing each `"Exposure Group"` to emit and each cell of the `'count'` Sequencer+ array.
4. **Step-by-Step Internal Logic:**
   1. Line `146`: Initializes `groups: list[tuple[int, float, str]] = []`.
   2. Line `147`: Initializes cumulative per-filter offset `current_count = 0`.
   3. Line `148`: Enters outer `while True:` pass loop.
   4. Line `149`: Sets pass termination flag `done = True`.
   5. Line `150`: Iterates `for d in exposures:`:
      - Line `151`: Computes `count = min(d.total_count - current_count, batch_size)`.
      - Lines `152-153`: If `batch_size == 0 or len(exposures) == 1:`, appends the full entry `(d.total_count, d.exposure_time_seconds, d.filter_name)` to `groups` and leaves `done = True` so the outer `while` loop exits immediately after the `for` loop completes.
      - Lines `154-156`: `elif count > 0:`, appends `(count, d.exposure_time_seconds, d.filter_name)` to `groups` and sets `done = False` (indicating at least one filter still had remaining frames in this pass).
   6. Lines `157-158`: If `done` is still `True` after checking all `exposures`, breaks out of `while True`.
   7. Line `159`: Increments `current_count += batch_size` and repeats for the next round-robin slice.
   8. Line `160`: Returns `groups`.

   ```mermaid
   flowchart TD
       Start(["_round_robin(exposures, batch_size=0)"]) --> Init["groups = []\ncurrent_count = 0 (lines 146-147)"]
       Init --> PassStart["done = True (line 149)"]
       PassStart --> ForEach["For d in exposures (line 150)"]
       ForEach --> Calc["count = min(d.total_count - current_count, batch_size) (line 151)"]
       Calc --> NoBatch{"batch_size == 0 or\nlen(exposures) == 1? (line 152)"}
       NoBatch -- Yes --> AppendFull["groups.append((d.total_count, d.exposure_time_seconds, d.filter_name))\n(line 153; done remains True)"]
       NoBatch -- No --> HasCount{"count > 0? (line 154)"}
       HasCount -- Yes --> AppendSlice["groups.append((count, d.exposure_time_seconds, d.filter_name))\ndone = False (lines 155-156)"]
       HasCount -- No --> NextExp["Next d in exposures"]
       AppendFull & AppendSlice --> NextExp
       NextExp --> MoreExp{"More d in exposures?"}
       MoreExp -- Yes --> ForEach
       MoreExp -- No --> CheckDone{"done is True? (line 157)"}
       CheckDone -- Yes --> Return(["return groups (line 160)"])
       CheckDone -- No --> Advance["current_count += batch_size (line 159)"]
       Advance --> PassStart
   ```

5. **Side Effects & I/O:** None (pure calculation).
6. **Error Handling & Edge Cases:**
   - **Empty `exposures` (`[]`):** Inner `for` loop does not run, `done` stays `True`, breaks on line `158`, returns `[]`.
   - **Single filter with `batch_size > 0` (`len(exposures) == 1`):** Line `152` short-circuits batching and emits a single tuple `(d.total_count, d.exposure_time_seconds, d.filter_name)` because switching from a filter to itself between batches is pointless.
   - **Multi-filter with `batch_size > 0`:** Executes one extra pass after the largest `d.total_count` is exhausted where `count <= 0` for all `d`, leaving `done = True` and terminating cleanly.
7. **Complexity & Risk Notes:**
   - Time complexity: $O(|E| \cdot \lceil \max(\text{total\_count}) / \text{batch\_size} \rceil)$ when `batch_size > 0` and $|E| > 1$; $O(|E|)$ when `batch_size == 0` or $|E| \le 1$. Note that `batch_size` is validated $\ge 0$ by the Pydantic `ObservationPlan` model (a negative `batch_size` is rejected at model validation).

---

### 2.10 `_container_base` (`nodes.py:166-197`)

1. **Purpose & Architectural Role:**
   Foundational builder for all N.I.N.A. container nodes (`SequenceRootContainer`, `StartAreaContainer`, `TargetAreaContainer`, `EndAreaContainer`, `SequentialContainer`, `DeepSkyObjectContainer`, `SkyFlat`, and `SmartExposure`). Attaches a `SequentialStrategy` and wraps `conditions`, `instructions` (`Items`), and `triggers` in typed `ObservableCollection` dicts.
2. **Signature, Decorators & Inputs:**
   ```python
   def _container_base(
       ctype: str,
       name: str | None = None,
       conditions: list[Any] | None = None,
       triggers: list[Any] | None = None,
       instructions: list[Any] | None = None,
   ) -> dict[str, Any]:
   ```
   - `ctype: str` — CLR container `$type` string.
   - `name: str | None = None` — Container display name (`"Name"` property).
   - `conditions: list[Any] | None = None` — List of `ISequenceCondition` node dicts.
   - `triggers: list[Any] | None = None` — List of `ISequenceTrigger` node dicts.
   - `instructions: list[Any] | None = None` — List of `ISequenceItem` node dicts (stored under `"Items"`).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` with keys `["$id", "$type", "Parent", "Strategy", "Name", "Conditions", "Items", "Triggers"]`.
4. **Step-by-Step Internal Logic:**
   1. Lines `173-178`: Normalizes `None` arguments (`conditions`, `triggers`, `instructions`) to fresh empty lists `[]`.
   2. Lines `179-181`: Sets `strategy = "NINA.Sequencer.Container.ExecutionStrategy.SequentialStrategy, NINA.Sequencer"`.
   3. Lines `182-197`: Merges `_child(ctype)` with `"Strategy": _child(strategy)`, `"Name": name`, `"Conditions": _values(..., conditions)`, `"Items": _values(..., instructions)`, and `"Triggers": _values(..., triggers)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Preserves `"Parent"` ahead of `"Conditions"`, `"Items"`, and `"Triggers"`, which `_add_refs` (`nodes.py:71`) keeps in place because `"Items"` and `"Conditions"` are present in the dict.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.11 `container_root` (`nodes.py:200-214`)

1. **Purpose & Architectural Role:**
   Builds the top-level `SequenceRootContainer` (`"Root Sequence"`) and immediately executes the two JSON.NET graph finalization passes (`_add_refs` then `_fix_provider`). This is the **only** container function that runs those fixups.
2. **Signature, Decorators & Inputs:**
   ```python
   def container_root(instructions: list[Any] | None = None) -> dict[str, Any]:
   ```
   - `instructions: list[Any] | None = None` — Typically the 3 area containers `[container_start(...), container_target(...), sequence_end(...)]`.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Complete, numbered, parent-linked, and provider-deduplicated N.I.N.A. sequence JSON document ready to POST to N.I.N.A.
4. **Step-by-Step Internal Logic:**
   1. Lines `208-212`: Calls `_container_base(ctype="NINA.Sequencer.Container.SequenceRootContainer, NINA.Sequencer", name="Root Sequence", instructions=instructions)`.
   2. Line `207`: Passes the root container tree through `_add_refs(...)` to assign `"$id"`s and `"Parent"` pointers.
   3. Line `206`: Passes the numbered tree through `_fix_provider(...)` to deduplicate `"SelectedProvider"` dicts into `{"$ref": ...}` pointers, and returns the result.
5. **Side Effects & I/O:** Mutates the passed `instructions` subtrees in-place via `_add_refs` and `_fix_provider`.
6. **Error Handling & Edge Cases:** If `instructions` is `None`, builds an empty root container with `"$id": "1"` and `"Parent": None`.
7. **Complexity & Risk Notes:** $O(N)$ over the entire sequence AST.

---

### 2.12 `container_start` (`nodes.py:217-223`)

1. **Purpose & Architectural Role:**
   Builds N.I.N.A.'s `StartAreaContainer` (`"Start Sequence"`), which holds setup instructions that run once before the Target area (`connect_all_equipment` and optional `run_starting_event`).
2. **Signature, Decorators & Inputs:**
   ```python
   def container_start(instructions: list[Any] | None = None) -> dict[str, Any]:
   ```
   - `instructions: list[Any] | None = None` — Start-area sequence items.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Unnumbered `StartAreaContainer` node dict.
4. **Step-by-Step Internal Logic:**
   1. Lines `219-223`: Delegates to `_container_base(ctype="NINA.Sequencer.Container.StartAreaContainer, NINA.Sequencer", name="Start Sequence", instructions=instructions)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Defaults `instructions=None` to `[]`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.13 `container_target` (`nodes.py:226-232`)

1. **Purpose & Architectural Role:**
   Builds N.I.N.A.'s `TargetAreaContainer` (`"Target Sequence"`), which holds the ordered list of target/calibration step containers executed during the run and inspected by `sequence_state.py`.
2. **Signature, Decorators & Inputs:**
   ```python
   def container_target(instructions: list[Any] | None = None) -> dict[str, Any]:
   ```
   - `instructions: list[Any] | None = None` — Ordered list of `DeepSkyObjectContainer` step dicts.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Unnumbered `TargetAreaContainer` node dict.
4. **Step-by-Step Internal Logic:**
   1. Lines `228-232`: Delegates to `_container_base(ctype="NINA.Sequencer.Container.TargetAreaContainer, NINA.Sequencer", name="Target Sequence", instructions=instructions)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** When `instructions` is `[]` (the park sequence), `Items.$values` is `[]`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.14 `container_end` (`nodes.py:235-241`)

1. **Purpose & Architectural Role:**
   Builds N.I.N.A.'s `EndAreaContainer` (`"End Sequence"`), which holds teardown instructions (`"Park Telescope"`) executed once all Target-area steps finish or skip.
2. **Signature, Decorators & Inputs:**
   ```python
   def container_end(instructions: list[Any] | None = None) -> dict[str, Any]:
   ```
   - `instructions: list[Any] | None = None` — End-area sequence items (called by `builders.sequence_end`).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Unnumbered `EndAreaContainer` node dict.
4. **Step-by-Step Internal Logic:**
   1. Lines `237-241`: Delegates to `_container_base(ctype="NINA.Sequencer.Container.EndAreaContainer, NINA.Sequencer", name="End Sequence", instructions=instructions)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Defaults `instructions=None` to `[]`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.15 `container_sequential` (`nodes.py:244-257`)

1. **Purpose & Architectural Role:**
   Builds a general-purpose `SequentialContainer` holding conditions, triggers, and child instructions (used for `"Exposure Group"`, `"Wait For Sunset"`, `"Wait For Dawn"`, `"Wait For Dusk"`, `"Wait For Object"`, `"Break If in View"`, `"On Safe"`, `"Image Object"`, `"Image Flats"`, and `"Park Telescope"`).
2. **Signature, Decorators & Inputs:**
   ```python
   def container_sequential(
       name: str | None = None,
       conditions: list[Any] | None = None,
       triggers: list[Any] | None = None,
       instructions: list[Any] | None = None,
   ) -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Unnumbered `SequentialContainer` node dict.
4. **Step-by-Step Internal Logic:**
   1. Lines `251-257`: Delegates to `_container_base(ctype="NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer", name=name, conditions=conditions, triggers=triggers, instructions=instructions)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** All parameters are optional and default to `None` (`[]` for collections).
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.16 `container_deepsky` (`nodes.py:260-291`)

1. **Purpose & Architectural Role:**
   Builds a `DeepSkyObjectContainer` carrying target metadata (`TargetName`, `PositionAngle`, sexagesimal J2000 `InputCoordinates`, and an empty `ExposureInfoList`) around a step's instructions. Called by `builders.acquisition_container` for both light steps and calibration steps (`$$TARGETNAME$$` resolves from `Target.TargetName`).
2. **Signature, Decorators & Inputs:**
   ```python
   def container_deepsky(
       name: str,
       target: str,
       ra: float,
       dec: float,
       posang: float | None = None,
       triggers: list[Any] | None = None,
       conditions: list[Any] | None = None,
       instructions: list[Any] | None = None,
   ) -> dict[str, Any]:
   ```
   - `name: str` — UI step title (e.g., `"Deep Sky Target Sequence"`, `"Dark Sequence"`, `"Bias Sequence"`, `"Dawn Flats"`, `"Dusk Flats"`).
   - `target: str` — Target string written into `Target.TargetName` and expanded by N.I.N.A.'s `$$TARGETNAME$$` file pattern token.
   - `ra: float` — J2000 Right Ascension in decimal hours.
   - `dec: float` — J2000 Declination in decimal degrees.
   - `posang: float | None = None` — Rotator position angle in degrees.
   - `triggers`, `conditions`, `instructions` — Optional lists of child AST nodes.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — `DeepSkyObjectContainer` node dict with `"ExposureInfoList"` and `"Target"` sub-nodes merged in.
4. **Step-by-Step Internal Logic:**
   1. Lines `275-281`: Calls `_container_base(ctype="NINA.Sequencer.Container.DeepSkyObjectContainer, NINA.Sequencer", name=name, triggers=triggers, conditions=conditions, instructions=instructions)`.
   2. Lines `282-284`: Merges `"ExposureInfoList"` initialized via `_values("NINA.Core.Utility.AsyncObservableCollection`1[[NINA.Sequencer.Utility.ExposureInfo, NINA.Sequencer]], NINA.Core")`.
   3. Lines `285-290`: Merges `"Target"` initialized via `_child("NINA.Astrometry.InputTarget, NINA.Astrometry") | {"TargetName": target, "PositionAngle": posang, "InputCoordinates": _radec_coordinates(ra, dec)}`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `InputTarget` is created via `_child(...)`, so it carries `"Parent"`, which `_add_refs` points back to the `DeepSkyObjectContainer` and moves to the end of `Target` (since `Target` is a leaf without `"Items"` or `"Conditions"`).
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.17 `trigger_meridian_flip` (`nodes.py:297-301`)

1. **Purpose & Architectural Role:**
   Builds a `MeridianFlipTrigger` that performs an automated meridian flip during `"Image Object"` in `target_payload_lights`.
2. **Signature, Decorators & Inputs:**
   ```python
   def trigger_meridian_flip() -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `{"$id": None, "$type": "NINA.Sequencer.Trigger.MeridianFlip.MeridianFlipTrigger, NINA.Sequencer", "Parent": None}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `299-301`: Calls `_child("NINA.Sequencer.Trigger.MeridianFlip.MeridianFlipTrigger, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.18 `trigger_autofocus_after_filter_change` (`nodes.py:304-308`)

1. **Purpose & Architectural Role:**
   Builds an `AutofocusAfterFilterChange` trigger that runs autofocus whenever the active filter changes during `"Image Object"`.
2. **Signature, Decorators & Inputs:**
   ```python
   def trigger_autofocus_after_filter_change() -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `{"$id": None, "$type": "NINA.Sequencer.Trigger.Autofocus.AutofocusAfterFilterChange, NINA.Sequencer", "Parent": None}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `306-308`: Calls `_child("NINA.Sequencer.Trigger.Autofocus.AutofocusAfterFilterChange, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.19 `trigger_autofocus_after_temperature_change` (`nodes.py:311-315`)

1. **Purpose & Architectural Role:**
   Builds an `AutofocusAfterTemperatureChangeTrigger` that refocuses when ambient/focuser temperature drifts by `degrees` Celsius.
2. **Signature, Decorators & Inputs:**
   ```python
   def trigger_autofocus_after_temperature_change(degrees: float) -> dict[str, Any]:
   ```
   - `degrees: float` — Temperature delta threshold in degrees Celsius (`plan.autofocus.threshold_celsius`).
3. **Outputs & Return Semantics:**
   - Returns `_child("NINA.Sequencer.Trigger.Autofocus.AutofocusAfterTemperatureChangeTrigger, NINA.Sequencer") | {"Amount": degrees}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `313-315`: Merges `{"Amount": degrees}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.20 `trigger_autofocus_after_exposures` (`nodes.py:318-322`)

1. **Purpose & Architectural Role:**
   Builds an `AutofocusAfterExposures` trigger that refocuses every `exposures` light frames.
2. **Signature, Decorators & Inputs:**
   ```python
   def trigger_autofocus_after_exposures(exposures: int) -> dict[str, Any]:
   ```
   - `exposures: int` — Exposure count interval (`plan.autofocus.every_n_exposures`).
3. **Outputs & Return Semantics:**
   - Returns `_child("NINA.Sequencer.Trigger.Autofocus.AutofocusAfterExposures, NINA.Sequencer") | {"AfterExposures": exposures}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `320-322`: Merges `{"AfterExposures": exposures}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.21 `trigger_autofocus_after_hfr_increase` (`nodes.py:325-329`)

1. **Purpose & Architectural Role:**
   Builds an `AutofocusAfterHFRIncreaseTrigger` that refocuses when Half-Flux Radius (HFR) increases by `amount` percent over a rolling window of `samples` frames.
2. **Signature, Decorators & Inputs:**
   ```python
   def trigger_autofocus_after_hfr_increase(samples: int, amount: float) -> dict[str, Any]:
   ```
   - `samples: int` — Rolling sample window size (`plan.autofocus.hfr_increase_sample_size`).
   - `amount: float` — Percentage HFR increase threshold (`plan.autofocus.hfr_increase_threshold_percent`).
3. **Outputs & Return Semantics:**
   - Returns `_child("NINA.Sequencer.Trigger.Autofocus.AutofocusAfterHFRIncreaseTrigger, NINA.Sequencer") | {"SampleSize": samples, "Amount": amount}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `327-329`: Merges `{"SampleSize": samples, "Amount": amount}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.22 `trigger_center_after_drift` (`nodes.py:332-341`)

1. **Purpose & Architectural Role:**
   Builds a `CenterAfterDriftTrigger` that plate-solves every `after_exposures` frames and re-centers the telescope if pointing drift exceeds `distance_arcmin` arcminutes.
2. **Signature, Decorators & Inputs:**
   ```python
   def trigger_center_after_drift(
       after_exposures: int, distance_arcmin: float
   ) -> dict[str, Any]:
   ```
   - `after_exposures: int` — Frame interval between drift checks (`plan.guiding.check_drift_every_n_exposures`).
   - `distance_arcmin: float` — Maximum allowed drift in arcminutes (`plan.guiding.max_drift_arcmin`).
3. **Outputs & Return Semantics:**
   - Returns `_child("NINA.Sequencer.Trigger.Platesolving.CenterAfterDriftTrigger, NINA.Sequencer") | {"AfterExposures": after_exposures, "DistanceArcMinutes": distance_arcmin}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `336-341`: Merges `{"AfterExposures": after_exposures, "DistanceArcMinutes": distance_arcmin}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.23 `trigger_restore_guiding` (`nodes.py:344-346`)

1. **Purpose & Architectural Role:**
   Builds a `RestoreGuiding` trigger that automatically restarts PHD2/autoguiding if guiding stops or is interrupted during `"Image Object"`.
2. **Signature, Decorators & Inputs:**
   ```python
   def trigger_restore_guiding() -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `{"$id": None, "$type": "NINA.Sequencer.Trigger.Guider.RestoreGuiding, NINA.Sequencer", "Parent": None}`.
4. **Step-by-Step Internal Logic:**
   1. Line `346`: Calls `_child("NINA.Sequencer.Trigger.Guider.RestoreGuiding, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.
