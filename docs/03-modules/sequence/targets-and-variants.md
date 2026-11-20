# Module Specification: `nina_planner/sequence/targets.py` & `nina_planner/sequence/variants.py`

> **Navigation:** [⬆️ Up to Atlas Index](../../README.md) | [⬆️ Sequence Package](./README.md) | **Prev Layers:** [`nodes.md`](./nodes.md) · [`instructions.md`](./instructions.md) · [`builders.md`](./builders.md) | **Caller:** [`tools/sequence.py`](../tools/sequence.md)

---

## Part I: `nina_planner/sequence/targets.py`

### 1. Module Overview (`targets.py`)

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/sequence/targets.py`
- **Line Count:** 415 lines (`1-415`)
- **Module Docstring (`line 1`):**
  > Target-area payload builders: the steps for lights, darks/bias and flats.
- **Architectural Layer:** Layer 4a — Plan-to-Payload Compilers (`target_payload_darks`, `target_payload_flats`, `target_payload_lights`), AST Safety Invariant Verifier (`_contains_type`), and Multi-Step Sequence Assembler (`build_sequence_many`).
- **Total Functions Defined:** 5 top-level functions (`_contains_type`, `build_sequence_many`, `target_payload_darks`, `target_payload_flats`, `target_payload_lights`) + 1 nested builder closure (`instructions` inside `target_payload_darks` at lines `135-181`).
- **Inbound Callers:**
  - `nina_planner/tools/sequence.py` (imports `build_sequence_many`, `target_payload_darks`, `target_payload_flats`, `target_payload_lights`)
  - `nina_planner/sequence/__init__.py` (re-exports `build_sequence_many`, `target_payload_darks`, `target_payload_flats`, `target_payload_lights`)
  - `tests/test_sequence.py` & `tests/sequence_helpers.py`
- **Outbound Imports (`lines 3-66`):**
  - `typing.Any` (line `3`)
  - `..models.observatory.ObservatoryEquipment` (line `5`)
  - `..models.plan.ObservationPlan` (line `6`)
  - `..models.profile.ObservatoryProfile` (line `7`)
  - `..models.request.DARK_VARIANTS` (line `8`)
  - `.builders` (16 symbols: `COUNT_ARRAY`, `COUNT_VARIABLE`, `EXPOSURE_GROUP_NAME`, `_sequence_with_steps`, `_validated_pointing`, `sequence_calibration`, `sequence_cool_camera`, `sequence_count_exposure`, `sequence_deepsky`, `sequence_home_scope`, `sequence_park_scope`, `sequence_safetynet`, `sequence_unpark_scope`, `sequence_wait_until_dawn`, `sequence_wait_until_dusk`, `sequence_wait_until_sunset`, lines `9-26`)
  - `.instructions` (24 symbols, lines `27-52`)
  - `.nodes` (12 symbols: `NINA_FLATS_ALTITUDE`, `NINA_FLATS_AZIMUTH_DAWN`, `NINA_FLATS_AZIMUTH_DUSK`, `_round_robin`, `container_sequential`, and 7 `trigger_*` functions, lines `53-66`)
- **Module-Level Constants:** None (imports `DARK_VARIANTS`, `COUNT_ARRAY`, `COUNT_VARIABLE`, `EXPOSURE_GROUP_NAME`, and `NINA_FLATS_*`).
- **CLR Type Strings Referenced:** Inspected via substring matching in `_contains_type` (`"UnparkScope"`, `"Platesolving"`, `"StartGuiding"`, line `175`).

---

### 2. Function & Method Reference (`targets.py`)

#### 2.1 `_contains_type` (`targets.py:69-82`)

1. **Purpose & Architectural Role:**
   Recursive AST predicate that checks whether any dictionary node anywhere in a nested sequence payload has a `"$type"` string containing the substring `needle`. Used by `target_payload_darks` (`targets.py:175-180`) to assert the dark/bias sky-gate exemption invariant at compile time.
2. **Signature, Decorators & Inputs:**
   ```python
   def _contains_type(node: Any, needle: str) -> bool:
   ```
   - `node: Any` — Arbitrary nested dict, list, or scalar value from a generated payload.
   - `needle: str` — Substring to check against `str(node.get("$type", ""))` (e.g., `"UnparkScope"`, `"Platesolving"`, `"StartGuiding"`).
3. **Outputs & Return Semantics:**
   - Returns `bool` — `True` if any dict at or below `node` has `needle in str(node.get("$type", ""))`, else `False`.
4. **Step-by-Step Internal Logic:**
   1. Line `76`: Checks `if isinstance(node, dict):`.
   2. Lines `77-78`: If `needle in str(node.get("$type", ""))`, returns `True` immediately.
   3. Line `79`: Otherwise returns `any(_contains_type(value, needle) for value in node.values())`.
   4. Lines `80-81`: Checks `if isinstance(node, list):` and returns `any(_contains_type(item, needle) for item in node)`.
   5. Line `82`: For any scalar/other type, returns `False`.
5. **Side Effects & I/O:** None (pure tree inspection).
6. **Error Handling & Edge Cases:** Safely handles dicts without `"$type"`, `None` values, primitives, and arbitrarily nested lists/dicts.
7. **Complexity & Risk Notes:** Time complexity $O(N)$ where $N$ is the number of AST nodes in `node`; short-circuits on first match.

---

#### 2.2 `build_sequence_many` (`targets.py:85-102`)

1. **Purpose & Architectural Role:**
   Top-level sequence compiler entry point that flattens an ordered list of per-step Target-area payloads (`list[list[dict[str, Any]]]`) into a single runnable N.I.N.A. `SequenceRootContainer` with one `StartAreaContainer`, one `TargetAreaContainer`, and one closing `EndAreaContainer`.
2. **Signature, Decorators & Inputs:**
   ```python
   def build_sequence_many(
       equipment: ObservatoryEquipment, payloads: list[list[dict[str, Any]]]
   ) -> dict[str, Any]:
   ```
   - `equipment: ObservatoryEquipment` — Hardware capabilities used for Start/End area assembly (`sequence_end`).
   - `payloads: list[list[dict[str, Any]]]` — Ordered list of step payload lists returned by `target_payload_lights`, `target_payload_darks`, or `target_payload_flats` (or `[]` for a park sequence).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Complete, numbered (`$id`), parent-linked (`Parent`), and provider-deduplicated (`$ref`) `SequenceRootContainer` JSON document.
4. **Step-by-Step Internal Logic:**
   1. Line `100`: Flattens all inner payload lists into a single step list: `steps = [item for payload in payloads for item in payload]`.
   2. Line `101`: Counts non-empty payload entries: `step_count = sum(1 for payload in payloads if payload)`.
   3. Line `102`: Delegates to `_sequence_with_steps(equipment, steps, step_count)` (`builders.py:474-494`) and returns the resulting root container.
5. **Side Effects & I/O:** Mutates the dicts inside `payloads` in-place when `container_root` runs `_add_refs` and `_fix_provider`.
6. **Error Handling & Edge Cases:**
   - **Empty `payloads` (`[]`) or all-empty inner lists (`[[], []]`):** `steps` is `[]` and `step_count` is `0`. `_sequence_with_steps` emits no `RUN-STARTING` or `RUN-FINISHED` event, producing the canonical standalone park sequence.
7. **Complexity & Risk Notes:** $O(N)$ over the entire combined sequence AST.

---

#### 2.3 `target_payload_darks` (`targets.py:108-190`)

1. **Purpose & Architectural Role:**
   Compiles a plan's `dark_frames` (`bias=False`) or `bias_frames` (`bias=True`) into a 1-element list containing a `sequence_calibration` `DeepSkyObjectContainer` (`"Dark Sequence"` / `"Bias Sequence"`). Implements parked, closed-roof-safe calibration acquisition across the three light-cutting variants (`"wheel"`, `"cover"`, `"shutter"`) and enforces the compile-time sky-gate exemption assertion.
2. **Signature, Decorators & Inputs:**
   ```python
   def target_payload_darks(
       plan: ObservationPlan,
       equipment: ObservatoryEquipment,
       profile: ObservatoryProfile,
       bias: bool = False,
       variant: str = "wheel",
   ) -> list[dict[str, Any]]:
   ```
   - `plan: ObservationPlan` — Plan supplying `dark_frames` or `bias_frames`, `cooler`, `calibration_prefix()`, and `plan_token()`.
   - `equipment: ObservatoryEquipment` — Hardware state for `sequence_park_scope` and `sequence_cool_camera`.
   - `profile: ObservatoryProfile` — Profile supplying `profile.filter_position("Dark")` when `variant == "wheel"`.
   - `bias: bool = False` — When `True`, builds `"Bias Sequence"` (`image_type="BIAS"`, `tag="bias"` from `plan.bias_frames`); when `False`, builds `"Dark Sequence"` (`image_type="DARK"`, `tag="dark"` from `plan.dark_frames`).
   - `variant: str = "wheel"` — Light-cutting mechanism (`"wheel"`, `"cover"`, or `"shutter"`).
   - Inner closure: `def instructions() -> list[dict[str, Any]]` (`lines 135-181`).
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — Single-element list `[sequence_calibration(...)]`.
4. **Step-by-Step Internal Logic:**
   1. Lines `129-133`: Validates `if variant not in DARK_VARIANTS:`. Raises `ValueError(f"Unsupported dark variant: {variant!r} (use {', '.join(map(repr, DARK_VARIANTS))})")` if invalid.
   2. Lines `135-181`: Defines and calls nested `instructions() -> list[dict[str, Any]]`:
      - Line `136`: Sets `image_type = "BIAS" if bias else "DARK"`.
      - Line `139`: Selects `frames = plan.bias_frames if bias else plan.dark_frames` (in list order, no round-robin batching).
      - Line `143`: Sets `dark_filter = "Dark" if variant == "wheel" else None`.
      - Line `144`: Resolves `dark_position = profile.filter_position(dark_filter) if dark_filter else None`.
      - Lines `145-162`: Builds `exposures`, wrapping each frame entry `f` in `container_sequential(name=EXPOSURE_GROUP_NAME, instructions=[smart_exposure(count=f.total_count, exposure=getattr(f, "exposure_time_seconds", 0), image_type=image_type, filter_name=dark_filter, filter_position=dark_position)])`. Note that `BiasExposurePlan` has no `exposure_time_seconds` attribute, so `getattr(..., 0)` supplies `0`.
      - Line `163`: Builds `base = sequence_park_scope(equipment) + sequence_cool_camera(plan, equipment)`.
      - Lines `164-169`: If `variant == "cover"`, sets `built = base + [close_cover()] + exposures + [open_cover()]`; otherwise (`"wheel"` or `"shutter"`), sets `built = base + exposures`.
      - Lines `175-180`: Iterates `for needle in ("UnparkScope", "Platesolving", "StartGuiding"):` and asserts `not _contains_type(built, needle)`, pinning the dark/bias `is_safe` gate exemption invariant.
      - Line `181`: Returns `built`.
   3. Lines `183-190`: Wraps `instructions()` in `[sequence_calibration(plan=plan, instructions=instructions(), kind="Bias Sequence" if bias else "Dark Sequence", tag="bias" if bias else "dark")]` and returns it.

   ```mermaid
   flowchart TD
       Start(["target_payload_darks(plan, equipment, profile, bias=False, variant='wheel')"]) --> CheckVar{"variant in DARK_VARIANTS?\n('wheel', 'cover', 'shutter') (line 129)"}
       CheckVar -- No --> RaiseVal["raise ValueError('Unsupported dark variant...') (lines 130-133)"]
       CheckVar -- Yes --> SelectKind["image_type = 'BIAS' if bias else 'DARK' (line 136)\nframes = plan.bias_frames if bias else plan.dark_frames (line 139)"]
       SelectKind --> FilterChoice{"variant == 'wheel'? (line 143)"}
       FilterChoice -- Yes --> WheelSlot["dark_filter = 'Dark'\ndark_position = profile.filter_position('Dark') (lines 143-144)"]
       FilterChoice -- "No ('cover' or 'shutter')" --> NoWheel["dark_filter = None\ndark_position = None (lines 143-144)"]
       WheelSlot & NoWheel --> BuildExposures["Build exposures list (lines 145-162):\nOne 'Exposure Group' SequentialContainer per f in frames\nholding smart_exposure(count=f.total_count,\nexposure=getattr(f, 'exposure_time_seconds', 0), ...)"]
       BuildExposures --> BuildBase["base = sequence_park_scope(equipment) +\nsequence_cool_camera(plan, equipment) (line 163)"]
       BuildBase --> IsCover{"variant == 'cover'? (line 164)"}
       IsCover -- Yes --> WrapCover["built = base + [close_cover()] + exposures + [open_cover()] (line 165)"]
       IsCover -- No --> NoCover["built = base + exposures (line 169)"]
       WrapCover & NoCover --> AssertSafe["Assert sky-gate exemption (lines 175-180):\nfor needle in ('UnparkScope', 'Platesolving', 'StartGuiding'):\nassert not _contains_type(built, needle)"]
       AssertSafe --> WrapCal["Return [sequence_calibration(plan, built,\nkind='Bias Sequence'|'Dark Sequence',\ntag='bias'|'dark')] (lines 183-190)"]
   ```

5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - Raises `ValueError` on unknown `variant` (`lines 129-133`) or if `variant == "wheel"` and `"Dark"` is missing from `profile.filters` (via `profile.filter_position("Dark")` on line `144`).
   - Raises `AssertionError` (`lines 175-180`) if any future code edit introduces `UnparkScope`, `Platesolving`, or `StartGuiding` into a dark/bias payload.
   - Reopens the motorized cover (`open_cover()`, line `165`) at the end of `variant="cover"` so subsequent light or flat steps in the same sequence never expose against a closed cover.
7. **Complexity & Risk Notes:** $O(|F|)$ where $|F|$ is the number of dark or bias frame groups in `plan`.

---

#### 2.4 `target_payload_flats` (`targets.py:196-258`)

1. **Purpose & Architectural Role:**
   Compiles a plan's `flat_frames` into a 1-element list containing a `sequence_calibration` `DeepSkyObjectContainer` (`"Dawn Flats"` or `"Dusk Flats"`). Coordinates twilight timing waits, filter ordering (broadband-first at dawn vs. narrowband-first reversed at dusk), solar altitude cutoff loop conditions, camera cooling, mount unparking, slewing to the flat azimuth/altitude, and `SkyFlat` execution inside an `"Image Flats"` `sequence_safetynet`.
2. **Signature, Decorators & Inputs:**
   ```python
   def target_payload_flats(
       plan: ObservationPlan,
       equipment: ObservatoryEquipment,
       profile: ObservatoryProfile,
       dusk: bool = False,
   ) -> list[dict[str, Any]]:
   ```
   - `plan: ObservationPlan` — Plan supplying `flat_frames`, `cooler`, `calibration_prefix()`, and `plan_token()`.
   - `equipment: ObservatoryEquipment` — Hardware state for wait/park/cool/unpark/safetynet builders.
   - `profile: ObservatoryProfile` — Profile supplying `profile.filter_position(f.filter_name)`.
   - `dusk: bool = False` — When `True`, builds evening twilight `"Dusk Flats"` (`tag="dusk flats"`); when `False`, builds morning twilight `"Dawn Flats"` (`tag="dawn flats"`).
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — Single-element list `[sequence_calibration(...)]`.
4. **Step-by-Step Internal Logic:**
   1. Lines `207-214`: Builds `wait_instructions`:
      - If `dusk=True`: `[sequence_wait_until_sunset(equipment)]` (waits until sunset and solar altitude $\le 0°$).
      - If `dusk=False`: `[sequence_wait_until_sunset(equipment), sequence_wait_until_dawn(equipment)]` (waits past sunset if started in daytime, then waits until dawn and solar altitude $\ge -8°$).
   2. Line `219`: Orders `flats = list(reversed(plan.flat_frames)) if dusk else plan.flat_frames` (dawn shoots in plan order as sky brightens; dusk reverses the list so narrowband/slower filters shoot first while twilight is still bright and broadband shoots last as twilight fades).
   3. Lines `221-258`: Returns `[sequence_calibration(...)]` with:
      - `kind="Dusk Flats" if dusk else "Dawn Flats"`,
      - `tag="dusk flats" if dusk else "dawn flats"`,
      - `instructions = wait_instructions + [sequence_safetynet(name="Image Flats", ...)]`, where `"Image Flats"` has:
        - `conditions`: `[loop_until_sun_altitude_below(-8)]` when `dusk=True` else `[loop_until_sun_altitude_above(0)]` (lines `232-236`),
        - `instructions`:
          1. `sequence_cool_camera(plan, equipment)` (line `237`),
          2. `+ sequence_unpark_scope(equipment)` (line `238`),
          3. `+ [slew_to_azalt(az=NINA_FLATS_AZIMUTH_DUSK if dusk else NINA_FLATS_AZIMUTH_DAWN, alt=NINA_FLATS_ALTITUDE)]` (lines `239-246`),
          4. `+ [sky_flats(count=f.total_count, filter_name=f.filter_name, filter_position=profile.filter_position(f.filter_name)) for f in flats]` (lines `247-254`).

   ```mermaid
   flowchart TD
       Start(["target_payload_flats(plan, equipment, profile, dusk=False)"]) --> IsDusk{"dusk is True? (line 208)"}
       IsDusk -- "Yes (Dusk Flats)" --> DuskSetup["wait_instructions = [sequence_wait_until_sunset(equipment)]\nflats = list(reversed(plan.flat_frames))\nkind = 'Dusk Flats', tag = 'dusk flats'\nstop_cond = loop_until_sun_altitude_below(-8)\naz = NINA_FLATS_AZIMUTH_DUSK (90°)"]
       IsDusk -- "No (Dawn Flats)" --> DawnSetup["wait_instructions = [sequence_wait_until_sunset(equipment),\nsequence_wait_until_dawn(equipment)]\nflats = plan.flat_frames\nkind = 'Dawn Flats', tag = 'dawn flats'\nstop_cond = loop_until_sun_altitude_above(0)\naz = NINA_FLATS_AZIMUTH_DAWN (270°)"]
       DuskSetup & DawnSetup --> BuildSafetynet["Build sequence_safetynet(name='Image Flats', conditions=[stop_cond]) (lines 229-255):\n1. sequence_cool_camera(plan, equipment)\n2. sequence_unpark_scope(equipment)\n3. slew_to_azalt(az, alt=NINA_FLATS_ALTITUDE (80°))\n4. [sky_flats(f.total_count, f.filter_name, pos) for f in flats]"]
       BuildSafetynet --> WrapCal["Return [sequence_calibration(plan, kind, tag,\ninstructions=wait_instructions + [safetynet])] (lines 221-258)"]
   ```

5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - Raises `ValueError` via `profile.filter_position(f.filter_name)` (`line 251`) if a flat frame references a filter not present in `profile.filters`.
   - If the twilight window has already passed when N.I.N.A. reaches `"Image Flats"`, `loop_until_sun_altitude_below(-8)` (dusk) or `loop_until_sun_altitude_above(0)` (dawn) evaluates false immediately and skips the safetynet without unparking or slewing.
7. **Complexity & Risk Notes:** $O(|F|)$ where $|F| = \text{len}(\text{plan.flat\_frames})$.

---

#### 2.5 `target_payload_lights` (`targets.py:264-415`)

1. **Purpose & Architectural Role:**
   Compiles one pointing (`pointing_index`) of a plan's `light_frames` into a 1-element list containing a `sequence_deepsky` `DeepSkyObjectContainer` (`"Deep Sky Target Sequence"`). Orchestrates the complete automated deep-sky imaging lifecycle:
   1. `"Wait For Dusk"` (parked wait if before dusk),
   2. `"Wait For Object"` (immediate break-out via `"Break If in View"` if already above horizon and `min_altitude`, else parked wait until rising, bounded by dawn and meridian crossing),
   3. Priming the `Sequencer+` persistent countdown (`declare_variable("n", "0")`, `initialize_array("'count'")`, and `put_array("'count'", index, str(count))` for each round-robin group),
   4. `"Image Object"` `sequence_safetynet` carrying 7 operational triggers (meridian flip, drift re-centering, 4 autofocus triggers, and restore guiding), camera cooling, unparking, homing (`sequence_home_scope`), sidereal tracking (`set_tracking(0)`), reference-filter switch, plate-solve centering (`slew_center_and_rotate` or `slew_and_center`), initial `run_autofocus()`, `start_guiding()`, interleaved `sequence_count_exposure` batches, and closing `stop_guiding()`.
2. **Signature, Decorators & Inputs:**
   ```python
   def target_payload_lights(
       plan: ObservationPlan,
       equipment: ObservatoryEquipment,
       profile: ObservatoryProfile,
       *,
       pointing_index: int = 1,
   ) -> list[dict[str, Any]]:
   ```
   - `plan: ObservationPlan` — Full observation plan.
   - `equipment: ObservatoryEquipment` — Observatory hardware state.
   - `profile: ObservatoryProfile` — Active profile for filter slot lookups.
   - `pointing_index: int = 1` (keyword-only) — 1-based index into `plan.pointings`.
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — Single-element list `[sequence_deepsky(...)]`.
4. **Step-by-Step Internal Logic:**
   1. Line `283`: Resolves and validates `pointing = _validated_pointing(plan, pointing_index)`.
   2. Lines `284-285`: Reads `reference_filter_name = plan.autofocus.reference_filter_name` and resolves `reference_filter_position = profile.filter_position(reference_filter_name)`.
   3. Line `286`: Sets `name_object = "Wait For Object"`.
   4. Line `291`: Computes round-robin exposure batches in a single pass: `exposure_groups = _round_robin(plan.light_frames, plan.batch_size)`.
   5. Lines `293-415`: Calls `sequence_deepsky(plan=plan, pointing_index=pointing_index, instructions=[...])` with:
      - **Step A — Dusk Gate (`line 299`):** `sequence_wait_until_dusk(equipment)`.
      - **Step B — Object Altitude/Horizon Gate (`lines 301-330`):** `container_sequential(name="Wait For Object", conditions=[loop_until_dawn(), loop_until_meridian()], instructions=[...])`:
        - Inner `"Break If in View"` (`lines 309-320`): `container_sequential(name="Break If in View", conditions=[loop_while_above_horizon(plan.constraints.horizon_offset_degrees), loop_while_above_altitude(plan.constraints.min_altitude)], instructions=[end_instruction("Wait For Object")])`.
        - Fallback parked wait (`lines 323-329`): `+ sequence_park_scope(equipment) + [wait_until_above_horizon(plan.constraints.horizon_offset_degrees), wait_until_above_altitude(plan.constraints.min_altitude)]`.
      - **Step C — Countdown Priming (`lines 334-339`):**
        - `declare_variable(COUNT_VARIABLE, "0")` (`"n" = "0"`),
        - `initialize_array(COUNT_ARRAY)` (`"'count'"`),
        - `*[put_array(COUNT_ARRAY, index, str(count)) for index, (count, _, _) in enumerate(exposure_groups)]`.
      - **Step D — `"Image Object"` Safety-Net (`lines 341-412`):** `sequence_safetynet(name="Image Object", equipment=equipment, ...)`:
        - `conditions` (`lines 344-350`): `[loop_until_dawn(), loop_while_above_horizon(plan.constraints.horizon_offset_degrees), loop_while_above_altitude(plan.constraints.min_altitude)]`.
        - `triggers` (`lines 351-369`):
          1. `trigger_meridian_flip()`,
          2. `trigger_center_after_drift(after_exposures=plan.guiding.check_drift_every_n_exposures, distance_arcmin=plan.guiding.max_drift_arcmin)`,
          3. `trigger_autofocus_after_filter_change()`,
          4. `trigger_autofocus_after_exposures(plan.autofocus.every_n_exposures)`,
          5. `trigger_autofocus_after_temperature_change(plan.autofocus.threshold_celsius)`,
          6. `trigger_autofocus_after_hfr_increase(samples=plan.autofocus.hfr_increase_sample_size, amount=plan.autofocus.hfr_increase_threshold_percent)`,
          7. `trigger_restore_guiding()`.
        - `instructions` (`lines 370-411`):
          1. `sequence_cool_camera(plan, equipment)` (`line 371`),
          2. `+ sequence_unpark_scope(equipment)` (`line 372`),
          3. `+ sequence_home_scope(equipment)` (`line 375`),
          4. `+ [set_tracking(0), switch_filter(reference_filter_name, reference_filter_position)]` (`lines 376-381`),
          5. `+ [slew_center_and_rotate() if pointing.requests_rotation() else slew_and_center()]` (`lines 382-390`),
          6. `+ [run_autofocus(), start_guiding()]` (`lines 391-394`),
          7. `+ [instruction for index, (count, exposure, filter_name) in enumerate(exposure_groups) for instruction in sequence_count_exposure(index=index, count=count, exposure=exposure, filter_name=filter_name, filter_position=profile.filter_position(filter_name), dither=plan.guiding.dither_every_n_exposures, image_type="LIGHT")]` (`lines 395-409`),
          8. `+ [stop_guiding()]` (`line 410`).

   ```mermaid
   flowchart TD
       Start(["target_payload_lights(plan, equipment, profile, pointing_index=1)"]) --> Prep["1. pointing = _validated_pointing(plan, pointing_index) (line 283)\n2. Resolve reference_filter_position (lines 284-285)\n3. exposure_groups = _round_robin(plan.light_frames, plan.batch_size) (line 291)"]
       Prep --> WrapDS["Wrap in sequence_deepsky(plan, pointing_index) (line 294)"]
       WrapDS --> Step1["Step 1: sequence_wait_until_dusk(equipment) (line 299)"]
       Step1 --> Step2["Step 2: 'Wait For Object' SequentialContainer (lines 301-330)\nConditions: loop_until_dawn(), loop_until_meridian()"]
       Step2 --> InView{"Inner 'Break If in View':\nAbove horizon AND above min_altitude? (lines 309-320)"}
       InView -- Yes --> BreakOut["end_instruction('Wait For Object') (line 319)"]
       InView -- No --> ParkWait["sequence_park_scope(equipment) +\nwait_until_above_horizon(...) +\nwait_until_above_altitude(...) (lines 323-329)"]
       BreakOut & ParkWait --> Step3["Step 3: Prime Sequencer+ Countdown (lines 334-339)\n- declare_variable('n', '0')\n- initialize_array(\"'count'\")\n- put_array(\"'count'\", index, str(count)) for each batch"]
       Step3 --> Step4["Step 4: sequence_safetynet('Image Object') (lines 341-412)\nConditions: loop_until_dawn, loop_while_above_horizon, loop_while_above_altitude\nTriggers: MeridianFlip, CenterAfterDrift, 4x Autofocus, RestoreGuiding"]
       Step4 --> InnerSafe["Inside 'Image Object' -> 'On Safe' (lines 370-411):\n1. sequence_cool_camera + sequence_unpark_scope + sequence_home_scope\n2. set_tracking(0) + switch_filter(reference_filter)\n3. slew_center_and_rotate() if pointing.requests_rotation() else slew_and_center()\n4. run_autofocus() + start_guiding()\n5. For each batch: sequence_count_exposure(index, count, ...)\n6. stop_guiding()"]
       InnerSafe --> Return(["Return [sequence_deepsky(...)]"])
   ```

5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - Raises `ValueError` via `_validated_pointing` (`line 283`) if `pointing_index` is out of bounds (`< 1` or `> len(plan.pointings)`), or via `profile.filter_position` (`lines 285`, `405`) if the autofocus reference filter or any light filter is missing from the active profile.
   - **Rotator-less Mosaic Compatibility (`lines 383-390`):** Uses `pointing.requests_rotation()` (where `position_angle_deg in (None, 0)` evaluates `False`) to choose between `slew_center_and_rotate()` and `slew_and_center()`, allowing 0°-stamped mosaic panes to run on rigs without a hardware rotator.
   - **Homing Order (`lines 372-377`):** Emits `sequence_unpark_scope` -> `sequence_home_scope` -> `set_tracking(0)` so the mount is unparked before homing and tracking rate is restored to sidereal immediately after homing.
7. **Complexity & Risk Notes:** $O(|G|)$ where $|G| = \text{len}(\text{exposure\_groups})$ produced by `_round_robin`.

---

## Part II: `nina_planner/sequence/variants.py`

### 3. Module Overview (`variants.py`)

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/sequence/variants.py`
- **Line Count:** 114 lines (`1-114`)
- **Module Docstring (`lines 1-12`):**
  > Capability checks for the dark/bias light-cutting variants.
  > A `variant` says *how* a dark/bias step cuts the light off, and N.I.N.A. only learns about a bad choice when the sequence is already running — a `cover` step on a rig with no flip-flat fails at 2 a.m., and a `shutter` step on a camera without one silently produces light-contaminated darks. `validate_dark_variant` moves that failure to load time, where it names the variant, the reason, and the variants this rig can actually run.
  > Every check fails closed: an unreadable capability rejects the load with a message rather than quietly choosing a different way to take the dark.
- **Architectural Layer:** Layer 4b — Load-Time Hardware & Profile Capability Gate for Dark/Bias Light-Cutting Variants (`'wheel'`, `'cover'`, `'shutter'`).
- **Total Functions Defined:** 2 functions (`dark_variant_reasons`, `validate_dark_variant`).
- **Inbound Callers:**
  - `nina_planner/tools/sequence.py` (imports `validate_dark_variant` and calls it at load time for every `dark` and `bias` request)
  - `nina_planner/sequence/__init__.py` (re-exports `DARK_VARIANTS`, `dark_variant_reasons`, `validate_dark_variant`)
  - `tests/test_variants.py` & `tests/test_sequence.py`
- **Outbound Imports (`lines 14-16`):**
  - `..models.observatory.ObservatoryEquipment` (line `14`)
  - `..models.profile.ObservatoryProfile` (line `15`)
  - `..models.request.DARK_VARIANTS` (`("wheel", "cover", "shutter")`, line `16`)

#### 3.1 Module-Level Constants (`variants.py:20`)

| Constant | Line | Value | Purpose |
| :--- | ---: | :--- | :--- |
| `_UNUSABLE_COVER_STATES` | `20` | `("NotPresent", "Error")` | N.I.N.A. `FlatDevice.CoverState` string values that indicate a flat device has no physical cover (`"NotPresent"`) or is in a fault state (`"Error"`), disqualifying the `"cover"` dark variant. |

---

### 4. Function & Method Reference (`variants.py`)

#### 4.1 `dark_variant_reasons` (`variants.py:23-86`)

1. **Purpose & Architectural Role:**
   Inspects both `ObservatoryProfile` and live `ObservatoryEquipment` (fail-closed) across all three dark/bias light-cutting variants (`"wheel"`, `"cover"`, `"shutter"`) and returns a dictionary mapping each **unsupported** variant to a human-readable explanation of why it cannot run on this rig. An empty return dict `{}` indicates all three variants are supported.
2. **Signature, Decorators & Inputs:**
   ```python
   def dark_variant_reasons(
       equipment: ObservatoryEquipment, profile: ObservatoryProfile
   ) -> dict[str, str]:
   ```
   - `equipment: ObservatoryEquipment` — Live equipment state reported by N.I.N.A. (`equipment.flat_device`, `equipment.camera`).
   - `profile: ObservatoryProfile` — Active N.I.N.A. profile (`profile.equipment.has_filter_wheel`, `profile.filters`, `profile.profile_name`, `profile.equipment.has_flat_device`).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, str]` — Subset of keys from `{"wheel", "cover", "shutter"}` where each present key maps to its failure reason string.
4. **Step-by-Step Internal Logic:**
   1. Line `33`: Initializes `reasons: dict[str, str] = {}`.
   2. **Check 1 — `"wheel"` variant (`lines 38-45`):**
      - Lines `38-39`: If `not profile.equipment.has_filter_wheel:`, sets `reasons["wheel"] = "the profile has no filter wheel configured"`.
      - Lines `40-45`: `elif not any(f.name == "Dark" for f in profile.filters):`, formats `available = ", ".join(f.name for f in profile.filters) or "none"` and sets `reasons["wheel"] = f"profile {profile.profile_name!r} has no 'Dark' filter slot (filters: {available})"`.
   3. **Check 2 — `"cover"` variant (`lines 50-68`):**
      - Lines `50-51`: If `not profile.equipment.has_flat_device:`, sets `reasons["cover"] = "the profile has no flat device configured"`.
      - Lines `52-68`: Otherwise inspects `flat = equipment.flat_device`:
        - Lines `54-57`: If `flat is None:`, sets `reasons["cover"] = "the profile configures a flat device but NINA reports none"`.
        - Lines `58-59`: `elif not flat.connected:`, sets `reasons["cover"] = "the flat device is not connected"`.
        - Lines `60-64`: `elif flat.supports_open_close is not True:` (strictly checks `is not True` so `None` or `False` both fail closed), sets `reasons["cover"] = "the flat device reports SupportsOpenClose=false — it cannot open and close to cut the light"`.
        - Lines `65-68`: `elif flat.cover_state in _UNUSABLE_COVER_STATES:`, sets `reasons["cover"] = f"the flat device reports CoverState={flat.cover_state!r}"`.
   4. **Check 3 — `"shutter"` variant (`lines 73-84`):**
      - Line `73`: Reads `camera = equipment.camera`.
      - Lines `74-75`: If `camera is None:`, sets `reasons["shutter"] = "NINA reports no camera"`.
      - Lines `76-79`: `elif camera.has_shutter is None:`, sets `reasons["shutter"] = f"NINA does not report HasShutter for camera {camera.name!r}"`.
      - Lines `80-84`: `elif not camera.has_shutter:`, sets `reasons["shutter"] = f"camera {camera.name!r} reports HasShutter=false — it cannot cut the light itself"`.
   5. Line `86`: Returns `reasons`.

   ```mermaid
   flowchart TD
       Start(["dark_variant_reasons(equipment, profile)"]) --> Init["reasons: dict[str, str] = {} (line 33)"]
       
       Init --> W1{"profile.equipment.has_filter_wheel? (line 38)"}
       W1 -- No --> WFail1["reasons['wheel'] = 'the profile has no filter wheel configured' (line 39)"]
       W1 -- Yes --> W2{"any(f.name == 'Dark'\nfor f in profile.filters)? (line 40)"}
       W2 -- No --> WFail2["reasons['wheel'] = \"profile ... has no 'Dark' filter slot...\" (lines 41-45)"]
       W2 -- Yes --> WOk["'wheel' is supported"]
       
       WFail1 & WFail2 & WOk --> C1{"profile.equipment.has_flat_device? (line 50)"}
       C1 -- No --> CFail1["reasons['cover'] = 'the profile has no flat device configured' (line 51)"]
       C1 -- Yes --> C2{"equipment.flat_device is None? (line 54)"}
       C2 -- Yes --> CFail2["reasons['cover'] = 'the profile configures a flat device but NINA reports none' (lines 55-57)"]
       C2 -- No --> C3{"flat.connected? (line 58)"}
       C3 -- No --> CFail3["reasons['cover'] = 'the flat device is not connected' (line 59)"]
       C3 -- Yes --> C4{"flat.supports_open_close is True? (line 60)"}
       C4 -- No --> CFail4["reasons['cover'] = 'the flat device reports SupportsOpenClose=false...' (lines 61-64)"]
       C4 -- Yes --> C5{"flat.cover_state in\n('NotPresent', 'Error')? (line 65)"}
       C5 -- Yes --> CFail5["reasons['cover'] = f'the flat device reports CoverState={flat.cover_state!r}' (lines 66-68)"]
       C5 -- No --> COk["'cover' is supported"]

       CFail1 & CFail2 & CFail3 & CFail4 & CFail5 & COk --> S1{"equipment.camera is None? (line 74)"}
       S1 -- Yes --> SFail1["reasons['shutter'] = 'NINA reports no camera' (line 75)"]
       S1 -- No --> S2{"camera.has_shutter is None? (line 76)"}
       S2 -- Yes --> SFail2["reasons['shutter'] = f'NINA does not report HasShutter for camera {camera.name!r}' (lines 77-79)"]
       S2 -- No --> S3{"camera.has_shutter is True? (line 80)"}
       S3 -- No --> SFail3["reasons['shutter'] = f'camera {camera.name!r} reports HasShutter=false...' (lines 81-84)"]
       S3 -- Yes --> SOk["'shutter' is supported"]

       SFail1 & SFail2 & SFail3 & SOk --> Return(["return reasons (line 86)"])
   ```

5. **Side Effects & I/O:** None (pure evaluation of in-memory `equipment` and `profile` models).
6. **Error Handling & Edge Cases:**
   - **Fail-Closed Semantics:** Every missing device (`flat is None`, `camera is None`) or `None` boolean capability (`flat.supports_open_close is not True`, `camera.has_shutter is None`) records a rejection reason rather than assuming capability.
   - Note that `"wheel"` checks only `profile` (`has_filter_wheel` and `"Dark"` in `profile.filters`), because `connect_all_equipment()` connects the profile's filter wheel at sequence start, whereas `"cover"` and `"shutter"` depend on hardware properties (`SupportsOpenClose`, `CoverState`, `HasShutter`) that N.I.N.A. only exposes via `/equipment/info`.
7. **Complexity & Risk Notes:** $O(|F|)$ where $|F| = \text{len}(\text{profile.filters})$.

---

#### 4.2 `validate_dark_variant` (`variants.py:89-114`)

1. **Purpose & Architectural Role:**
   Load-time gate called by `load_sequence` (`nina_planner/tools/sequence.py`) before building a `dark` or `bias` payload. Raises a `ValueError` if `variant` is not a member of `DARK_VARIANTS` or if `dark_variant_reasons(equipment, profile)` reports a failure reason for `variant`. When rejecting a hardware-unsupported variant, the error message includes both the specific failure reason for `variant` and the list of variants supported on the current rig (`"Supported here: ..."`).
2. **Signature, Decorators & Inputs:**
   ```python
   def validate_dark_variant(
       variant: str, equipment: ObservatoryEquipment, profile: ObservatoryProfile
   ) -> None:
   ```
   - `variant: str` — Requested dark/bias variant (including the default `"wheel"`).
   - `equipment: ObservatoryEquipment` — Live observatory equipment state.
   - `profile: ObservatoryProfile` — Active N.I.N.A. profile.
3. **Outputs & Return Semantics:**
   - Returns `None` when `variant` is valid and supported by the rig; raises `ValueError` otherwise.
4. **Step-by-Step Internal Logic:**
   1. Lines `100-105`: Checks `if variant not in DARK_VARIANTS:`. If unknown, builds `names = [repr(v) for v in DARK_VARIANTS]` and raises `ValueError(f"Unsupported dark variant: {variant!r} (use {', '.join(names[:-1])}, or {names[-1]})")`.
   2. Line `106`: Calls `reasons = dark_variant_reasons(equipment, profile)`.
   3. Lines `107-108`: Checks `if variant not in reasons: return` (variant is supported).
   4. Line `109`: Computes `supported = [v for v in DARK_VARIANTS if v not in reasons]`.
   5. Line `110`: Formats `usable = ", ".join(supported) if supported else "none"`.
   6. Lines `111-114`: Raises `ValueError(f"dark/bias variant {variant!r} is not available on this rig: {reasons[variant]}. Supported here: {usable}.")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - Even the default `variant="wheel"` passes through this check so an omitted variant never silently falls back to taking uncovered darks if the rig lacks a `"Dark"` filter slot.
   - If no variant is usable on the rig (`len(reasons) == 3`), `usable` formats cleanly as `"none"` (`"Supported here: none."`).
7. **Complexity & Risk Notes:** $O(|F|)$ via `dark_variant_reasons`.
