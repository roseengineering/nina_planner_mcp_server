# Module Specification: `nina_planner/sequence/builders.py`

> **Navigation:** [⬆️ Up to Atlas Index](../../README.md) | [⬆️ Sequence Package](./README.md) | **Prev Layers:** [`nodes.md`](./nodes.md) · [`instructions.md`](./instructions.md) | **Next Layer:** [`targets-and-variants.md`](./targets-and-variants.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/sequence/builders.py`
- **Line Count:** 494 lines (`1-494`)
- **Module Docstring (`lines 1-7`):**
  > Composable sequence builders: acquire, wait, safeguard, park.
  > The canonical park/home/safety policy for generated sequences lives in the comment block above `sequence_park_scope` — the one source of truth that `app.py`'s server instructions and `load_sequence`'s docstring summarise.
- **Architectural Layer:** Layer 3 — Mid-Level Composable Sequence Step Builders, Hardware Capability Conditional Wrappers, Sequencer+ Countdown Groups, Twilight Wait Containers, Safety-Net Containers, End-Area Teardown, and Root Sequence Assembly.
- **Total Functions Defined:** 16 functions (13 re-exported in `sequence/__init__.py` + 3 package-internal helpers `_validated_pointing`, `sequence_home_scope`, `_sequence_with_steps` imported directly by `targets.py`).
- **Inbound Callers:**
  - `nina_planner/sequence/targets.py` (imports `COUNT_ARRAY`, `COUNT_VARIABLE`, `EXPOSURE_GROUP_NAME`, `_sequence_with_steps`, `_validated_pointing`, `sequence_calibration`, `sequence_cool_camera`, `sequence_count_exposure`, `sequence_deepsky`, `sequence_home_scope`, `sequence_park_scope`, `sequence_safetynet`, `sequence_unpark_scope`, `sequence_wait_until_dawn`, `sequence_wait_until_dusk`, `sequence_wait_until_sunset`)
  - `nina_planner/sequence/__init__.py` (re-exports 13 functions)
  - `tests/test_sequence.py` & `tests/sequence_helpers.py`
- **Outbound Imports (`lines 9-52`):**
  - `typing.Any` (line `9`)
  - `..models.observatory.ObservatoryEquipment` (line `11`)
  - `..models.plan.ObservationPlan` (line `12`)
  - `..models.pointing.Pointing` (line `13`)
  - `.instructions` (29 instruction/condition factories, lines `14-44`)
  - `.nodes` (6 container factories: `container_deepsky`, `container_end`, `container_root`, `container_sequential`, `container_start`, `container_target`, lines `45-52`)

### 1.1 Module-Level Constants (`lines 280-287`)

| Constant | Line | Value | Purpose |
| :--- | ---: | :--- | :--- |
| `COUNT_ARRAY` | `280` | `"'count'"` | Single-quoted string literal expression naming the `Sequencer+` array that tracks remaining exposures per batch across safety-net restarts. |
| `COUNT_VARIABLE` | `281` | `"n"` | Bare `Sequencer+` variable identifier bound by `declare_variable("n", "0")`, updated by `get_array`, and tested by `loop_while_expression("n > 0")`. |
| `EXPOSURE_GROUP_NAME` | `287` | `"Exposure Group"` | Display name placed on each exposure `SequentialContainer` in both `sequence_count_exposure` (lights) and `target_payload_darks` (darks/bias). |

---

## 2. Function & Method Reference

### 2.1 `acquisition_container` (`builders.py:57-98`)

1. **Purpose & Architectural Role:**
   Wraps a plan segment's `instructions` inside a `DeepSkyObjectContainer` bracketed by `ACQUISITION-STARTING: [<tag>] <target>` (followed immediately by `connect_all_equipment()`) and `ACQUISITION-FINISHED: [<tag>] <target>`. Keeps the frame-kind `tag` (`"light"`, `"dark"`, `"bias"`, `"dawn flats"`, `"dusk flats"`) strictly inside the telemetry event text and out of `target` so N.I.N.A.'s `$$TARGETNAME$$` path macro never bakes the frame kind into output folder or file names.
2. **Signature, Decorators & Inputs:**
   ```python
   def acquisition_container(
       *,
       name: str,
       target: str,
       ra: float,
       dec: float,
       posang: float,
       tag: str,
       instructions: list[Any],
   ) -> dict[str, Any]:
   ```
   - All parameters are keyword-only (`*`).
   - `name: str` — Container UI name (e.g., `"Deep Sky Target Sequence"`, `"Dark Sequence"`, `"Dawn Flats"`).
   - `target: str` — Untainted target attribution string written to `Target.TargetName` (`$$TARGETNAME$$`).
   - `ra: float`, `dec: float`, `posang: float` — Target coordinates and position angle.
   - `tag: str` — Telemetry bracket tag (`"light"`, `"dark"`, `"bias"`, `"dawn flats"`, `"dusk flats"`).
   - `instructions: list[Any]` — Inner step instruction nodes.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Unnumbered `DeepSkyObjectContainer` dict with `[send_event("ACQUISITION-STARTING: ..."), connect_all_equipment(), *instructions, send_event("ACQUISITION-FINISHED: ...")]`.
4. **Step-by-Step Internal Logic:**
   1. Line `85`: Formats `text = f"[{tag}] {target}"`.
   2. Lines `86-98`: Calls and returns `container_deepsky(name=name, target=target, ra=ra, dec=dec, posang=posang, instructions=[send_event(f"ACQUISITION-STARTING: {text}"), connect_all_equipment(), *instructions, send_event(f"ACQUISITION-FINISHED: {text}")])`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** If `instructions` is empty, still emits `ACQUISITION-STARTING`, `ConnectAllEquipment`, and `ACQUISITION-FINISHED`.
7. **Complexity & Risk Notes:** $O(|I|)$ where $|I| = \text{len}(\text{instructions})$ for list unpacking.

---

### 2.2 `_validated_pointing` (`builders.py:101-118`)

1. **Purpose & Architectural Role:**
   Central guard resolving a caller-supplied 1-based `pointing_index` into its `Pointing` object from `plan.pointings`. Shared by `sequence_deepsky` (`builders.py:130`) and `target_payload_lights` (`targets.py:283`) so out-of-range indices or empty pointing lists raise a descriptive `ValueError` instead of a raw Python `IndexError` (or negative-index wrap-around).
2. **Signature, Decorators & Inputs:**
   ```python
   def _validated_pointing(plan: ObservationPlan, pointing_index: int) -> Pointing:
   ```
   - `plan: ObservationPlan` — Plan carrying `plan.pointings: list[Pointing]`.
   - `pointing_index: int` — 1-based pointing index ($1 \dots \text{len}(\text{plan.pointings})$).
3. **Outputs & Return Semantics:**
   - Returns `Pointing` — `plan.pointings[pointing_index - 1]`.
4. **Step-by-Step Internal Logic:**
   1. Line `112`: Checks `if pointing_index < 1 or pointing_index > len(plan.pointings):`.
   2. Lines `113-117`: If out of bounds, raises `ValueError(f"pointing_index={pointing_index} out of range (plan has {len(plan.pointings)} pointings; valid 1..{len(plan.pointings)})")`.
   3. Line `118`: Returns `plan.pointings[pointing_index - 1]`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Explicitly blocks `pointing_index <= 0` (which in raw `pointing_index - 1` indexing would silently access the tail of `plan.pointings` in Python) and empty `plan.pointings`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.3 `sequence_deepsky` (`builders.py:121-145`)

1. **Purpose & Architectural Role:**
   Wraps one pointing's light instructions in an `acquisition_container` named `"Deep Sky Target Sequence"` with `tag="light"` and a `Target.TargetName` combining `plan.target_prefix(pointing.label)` and `plan.attribution_token(pointing_index)`.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_deepsky(
       plan: ObservationPlan, instructions: list, pointing_index: int = 1
   ) -> dict[str, Any]:
   ```
   - `plan: ObservationPlan` — Observation plan.
   - `instructions: list` — Light step child instructions.
   - `pointing_index: int = 1` — 1-based mosaic pane or single-pointing index.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — `DeepSkyObjectContainer` produced by `acquisition_container`.
4. **Step-by-Step Internal Logic:**
   1. Line `130`: Resolves `pointing = _validated_pointing(plan, pointing_index)`.
   2. Line `134`: Computes `prefix = plan.target_prefix(pointing.label)`.
   3. Line `135`: Computes `target = f"{prefix} {plan.attribution_token(pointing_index)}"`.
   4. Line `136`: Normalizes `posang = 0 if pointing.position_angle_deg is None else pointing.position_angle_deg`.
   5. Lines `137-145`: Calls and returns `acquisition_container(name="Deep Sky Target Sequence", target=target, ra=pointing.ra_hours, dec=pointing.dec_deg, posang=posang, tag="light", instructions=instructions)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Raises `ValueError` via `_validated_pointing` if `pointing_index` is invalid. Converts `position_angle_deg=None` to `0` for the container field.
7. **Complexity & Risk Notes:** $O(|I|)$.

---

### 2.4 `sequence_calibration` (`builders.py:148-187`)

1. **Purpose & Architectural Role:**
   Wraps plan-level calibration instructions (darks, bias, dawn flats, dusk flats) in an `acquisition_container`. Unlike `sequence_deepsky`, it is plan-level rather than pointing-indexed: it uses `f"{plan.calibration_prefix()} {plan.plan_token()}"` as `target` (which matches a single-pointing plan's light target name so both share one `$$TARGETNAME$$` directory) and uses `plan.pointings[0]` for decorative container coordinates when present, falling back to `0.0, 0.0, 0` when `plan.pointings` is empty.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_calibration(
       plan: ObservationPlan, instructions: list, kind: str, tag: str
   ) -> dict[str, Any]:
   ```
   - `plan: ObservationPlan` — Observation plan.
   - `instructions: list` — Calibration instructions.
   - `kind: str` — N.I.N.A. container UI name (`"Dark Sequence"`, `"Bias Sequence"`, `"Dawn Flats"`, `"Dusk Flats"`).
   - `tag: str` — Telemetry tag for `ACQUISITION-*` events (`"dark"`, `"bias"`, `"dawn flats"`, `"dusk flats"`).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — `DeepSkyObjectContainer` produced by `acquisition_container`.
4. **Step-by-Step Internal Logic:**
   1. Line `171`: Computes `target = f"{plan.calibration_prefix()} {plan.plan_token()}"`.
   2. Lines `172-178`: If `plan.pointings` is non-empty, extracts `ra`, `dec`, and `posang` (defaulting `None` to `0`) from `plan.pointings[0]`; otherwise sets `ra, dec, posang = 0.0, 0.0, 0`.
   3. Lines `179-187`: Calls and returns `acquisition_container(name=kind, target=target, ra=ra, dec=dec, posang=posang, tag=tag, instructions=instructions)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Safely tolerates calibration-only plans with `plan.pointings == []`.
7. **Complexity & Risk Notes:** $O(|I|)$.

---

### 2.5 `sequence_warm_camera` (`builders.py:190-196`)

1. **Purpose & Architectural Role:**
   Capability-gated camera warm builder: emits `[warm_camera()]` if `equipment.camera` is present and `equipment.camera.thermal.has_cooler` is `True`, otherwise `[]`. Used in `sequence_end` (`builders.py:466`).
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_warm_camera(equipment: ObservatoryEquipment) -> list[dict[str, Any]]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — 1-element list `[warm_camera()]` or empty list `[]`.
4. **Step-by-Step Internal Logic:**
   1. Lines `192-196`: Evaluates `[warm_camera()] if equipment.camera and equipment.camera.thermal.has_cooler else []`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Uncooled cameras or missing camera info cleanly emit `[]` so N.I.N.A. never attempts to warm a camera without a TEC cooler.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.6 `sequence_cool_camera` (`builders.py:199-207`)

1. **Purpose & Architectural Role:**
   Capability- and plan-gated thermal step builder: when `equipment.camera` has a cooler (`thermal.has_cooler`), emits `[cool_camera(plan.cooler.setpoint_celsius)]` if `plan.cooler.on` is `True` or `[warm_camera()]` if `plan.cooler.on` is `False`; returns `[]` if the camera has no cooler. Used in `target_payload_darks`, `target_payload_flats`, and `target_payload_lights`.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_cool_camera(
       plan: ObservationPlan, equipment: ObservatoryEquipment
   ) -> list[dict[str, Any]]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — `[cool_camera(...)]`, `[warm_camera()]`, or `[]`.
4. **Step-by-Step Internal Logic:**
   1. Lines `203-207`: Checks `if equipment.camera and equipment.camera.thermal.has_cooler`. When true, branches on `plan.cooler.on` to return `[cool_camera(plan.cooler.setpoint_celsius)]` or `[warm_camera()]`; otherwise returns `[]`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Actively warms a cooled camera if a plan specifies `cooler.on = False`, ensuring previous cooled runs do not leave the camera at an unintended temperature.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.7 `sequence_park_scope` (`builders.py:244-247`)

1. **Purpose & Architectural Role:**
   Canonical park builder (see lines `210-241`): emits `[park_scope()]` when `equipment.mount and equipment.mount.can_park` is truthy, else `[]`. Park is the **only** closing maneuver in generated sequences; `FindHome` is never substituted as a fallback.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_park_scope(equipment: ObservatoryEquipment) -> list[dict[str, Any]]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — `[park_scope()]` or `[]`.
4. **Step-by-Step Internal Logic:**
   1. Line `247`: Returns `[park_scope()] if equipment.mount and equipment.mount.can_park else []`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** In production loads (`load_sequence`), `plan_utils.require_parkable` rejects any rig without a parkable mount before sequence compilation; the `else []` fallback exists only for unit tests that construct payloads directly with mock equipment.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.8 `sequence_home_scope` (`builders.py:250-258`)

1. **Purpose & Architectural Role:**
   Canonical mount-homing builder: emits `[home_scope()]` when `equipment.mount and equipment.mount.can_find_home` is truthy, else `[]`. Emitted once per lights step in `target_payload_lights` (`targets.py:375`) *after* `sequence_unpark_scope` and *before* `set_tracking(0)`.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_home_scope(equipment: ObservatoryEquipment) -> list[dict[str, Any]]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — `[home_scope()]` or `[]`.
4. **Step-by-Step Internal Logic:**
   1. Line `258`: Returns `[home_scope()] if equipment.mount and equipment.mount.can_find_home else []`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Must always be placed after `sequence_unpark_scope` because N.I.N.A.'s `/equipment/mount/home` endpoint rejects parked mounts with HTTP 409 `"Mount parked"`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.9 `sequence_unpark_scope` (`builders.py:261-272`)

1. **Purpose & Architectural Role:**
   Emits `[unpark_scope()]` when `equipment.mount and equipment.mount.can_park` is truthy, else `[]`. Uses `can_park` as a deliberate proxy for unpark capability because neither ASCOM nor N.I.N.A. `MountInfo` defines a `CanUnpark` property.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_unpark_scope(equipment: ObservatoryEquipment) -> list[dict[str, Any]]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — `[unpark_scope()]` or `[]`.
4. **Step-by-Step Internal Logic:**
   1. Line `272`: Returns `[unpark_scope()] if equipment.mount and equipment.mount.can_park else []`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Never called in dark/bias payloads.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.10 `sequence_count_exposure` (`builders.py:290-338`)

1. **Purpose & Architectural Role:**
   Builds one light exposure batch guarded simultaneously by `loop_for_iterations(count)` and the `Sequencer+` persistent countdown check `loop_while_expression("n > 0")` backed by cell `index` of array `'count'`. Ensures that if `"Image Object"` is interrupted by `is_safe=False` or horizon/altitude limits and later re-entered, already-acquired frames in cell `index` are not re-shot.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_count_exposure(
       index: int,
       count: int,
       exposure: float,
       image_type: str,
       filter_name: str | None = None,
       filter_position: int | None = None,
       dither: int = 0,
   ) -> list[dict[str, Any]]:
   ```
   - `index: int` — 0-based batch index into the `'count'` `Sequencer+` array.
   - `count: int` — Initial frame count for this batch.
   - `exposure: float` — Per-frame exposure time in seconds.
   - `image_type: str` — Frame type (`"LIGHT"`).
   - `filter_name: str | None = None`, `filter_position: int | None = None` — Filter selection (omits `SwitchFilter` when `filter_name is None`).
   - `dither: int = 0` — Dither interval in frames (omits `DitherAfterExposures` trigger when `dither == 0`).
3. **Outputs & Return Semantics:**
   - Returns `list[dict[str, Any]]` — 2-element list: `[get_array("'count'", index, "n"), container_sequential(name="Exposure Group", ...)]`.
4. **Step-by-Step Internal Logic:**
   1. Line `317`: Emits `get_array(COUNT_ARRAY, index, COUNT_VARIABLE)` (`get_array("'count'", index, "n")`) ahead of the container so `"n"` holds cell `index`'s current value when the container's loop conditions are first evaluated.
   2. Lines `318-337`: Emits `container_sequential` with:
      - Line `319`: `name=EXPOSURE_GROUP_NAME` (`"Exposure Group"`).
      - Lines `320-323`: `conditions=[loop_for_iterations(count), loop_while_expression(f"{COUNT_VARIABLE} > 0")]`.
      - Line `324`: `triggers=[dither_after_exposures(dither)] if dither else []`.
      - Lines `325-336`: `instructions` consisting of:
        - `[switch_filter(filter_name, filter_position)]` if `filter_name is not None` else `[]`,
        - `[take_exposure(exposure, image_type)]`,
        - `put_array(COUNT_ARRAY, index, f"{COUNT_VARIABLE}-1")` (decrements `'count'[index]`),
        - `get_array(COUNT_ARRAY, index, COUNT_VARIABLE)` (reloads `"n"` from `'count'[index]` for the next loop condition test).
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Unlike `SmartExposure` (which requires positional `Triggers[0]` and `Items[0]`), `sequence_count_exposure` uses a standard `SequentialContainer`, so `dither=0` cleanly emits `triggers=[]` and `filter_name=None` cleanly omits `switch_filter`.
7. **Complexity & Risk Notes:** $O(1)$. Requires `declare_variable("n", "0")`, `initialize_array("'count'")`, and `put_array("'count'", index, str(count))` to have been emitted earlier in the enclosing container (`target_payload_lights`, `targets.py:334-339`).

---

### 2.11 `sequence_wait_until_sunset` (`builders.py:341-358`)

1. **Purpose & Architectural Role:**
   Builds the `"Wait For Sunset"` `SequentialContainer` that parks the mount and waits until sunset *and* solar altitude $\le 0°$, bounded by `loop_until_sunset()`. Used at the start of both dawn and dusk flats in `target_payload_flats` (`targets.py:208-212`).
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_wait_until_sunset(equipment: ObservatoryEquipment) -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — `SequentialContainer(name="Wait For Sunset")`.
4. **Step-by-Step Internal Logic:**
   1. Line `343`: Sets `name = "Wait For Sunset"`.
   2. Lines `344-358`: Returns `container_sequential(name=name, conditions=[loop_until_sunset()], instructions=sequence_park_scope(equipment) + [wait_until_sunset(), wait_if_sun_altitude_above(0)])`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - **Boundary Synchronization Rule (lines `346-351`):** N.I.N.A. repeats a container while its `TimeCondition` is still fulfilled. Pairing `wait_until_sunset()` with `wait_if_sun_altitude_above(0)` guarantees that when the inner instructions finish, the sun is below $0°$ and `loop_until_sunset()` is already past its target time, preventing a second pass where `WaitForTime` would re-resolve to tomorrow's sunset.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.12 `sequence_wait_until_dawn` (`builders.py:361-376`)

1. **Purpose & Architectural Role:**
   Builds the `"Wait For Dawn"` `SequentialContainer` that parks the mount and waits for astronomical dawn and then waits while solar altitude is below $-8°$, bounded by `loop_until_dawn()`. Used in `target_payload_flats(..., dusk=False)` (`targets.py:212`).
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_wait_until_dawn(equipment: ObservatoryEquipment) -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — `SequentialContainer(name="Wait For Dawn")`.
4. **Step-by-Step Internal Logic:**
   1. Line `363`: Sets `name = "Wait For Dawn"`.
   2. Lines `364-376`: Returns `container_sequential(name=name, conditions=[loop_until_dawn()], instructions=sequence_park_scope(equipment) + [wait_until_dawn(), wait_if_sun_altitude_below(-8)])`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - **Boundary Synchronization Rule (lines `366-369`):** `wait_if_sun_altitude_below(-8)` exits only once the sun climbs above $-8°$ (which is past astronomical dawn at $-18°$), so `loop_until_dawn()` is guaranteed unfulfilled at the end of the first pass and never loops into tomorrow's dawn.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.13 `sequence_wait_until_dusk` (`builders.py:379-386`)

1. **Purpose & Architectural Role:**
   Builds the `"Wait For Dusk"` `SequentialContainer` that parks the mount and waits for astronomical dusk, bounded by `loop_until_dusk()`. Used at the start of `target_payload_lights` (`targets.py:299`).
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_wait_until_dusk(equipment: ObservatoryEquipment) -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — `SequentialContainer(name="Wait For Dusk")`.
4. **Step-by-Step Internal Logic:**
   1. Line `381`: Sets `name = "Wait For Dusk"`.
   2. Lines `382-386`: Returns `container_sequential(name=name, conditions=[loop_until_dusk()], instructions=sequence_park_scope(equipment) + [wait_until_dusk()])`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** If already past dusk when the step starts, `loop_until_dusk()` is unfulfilled and the container is skipped immediately without parking.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.14 `sequence_safetynet` (`builders.py:389-423`)

1. **Purpose & Architectural Role:**
   Builds a safety-gated resilient container (`"Image Object"` in `target_payload_lights`, `"Image Flats"` in `target_payload_flats`). Runs `instructions` inside an inner `"On Safe"` container gated by `loop_while_safe()`. If `instructions` finish while safe, `end_instruction(name)` terminates the outer container immediately. If the safety monitor turns unsafe (`is_safe=False`), `"On Safe"` aborts, the outer container runs `sequence_park_scope(equipment)` and blocks at `wait_until_safe()`, and when safe again (before sunrise) loops back into `"On Safe"`.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_safetynet(
       name: str,
       equipment: ObservatoryEquipment,
       instructions: list[Any] | None = None,
       conditions: list[Any] | None = None,
       triggers: list[Any] | None = None,
   ) -> dict[str, Any]:
   ```
   - `name: str` — Outer container name (`"Image Object"` or `"Image Flats"`), also passed to `end_instruction(name)`.
   - `equipment: ObservatoryEquipment` — Used for `sequence_park_scope(equipment)` on safety drop.
   - `instructions: list[Any] | None = None` — Sky-dependent instructions to execute inside `"On Safe"`.
   - `conditions: list[Any] | None = None` — Additional outer loop conditions (appended after `loop_until_sunrise()`).
   - `triggers: list[Any] | None = None` — Outer container triggers (e.g., meridian flip, autofocus, drift check, restore guiding).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Outer `SequentialContainer` wrapping `"On Safe"`, `sequence_park_scope`, and `wait_until_safe`.
4. **Step-by-Step Internal Logic:**
   1. Lines `402-407`: Normalizes `None` arguments (`instructions`, `conditions`, `triggers`) to `[]`.
   2. Lines `408-423`: Constructs and returns `container_sequential` with:
      - `name=name`,
      - `triggers=triggers`,
      - `conditions=[loop_until_sunrise()] + conditions`,
      - `instructions`:
        1. Inner `container_sequential(name="On Safe", conditions=[loop_while_safe()], instructions=instructions + [end_instruction(name)])` (lines `413-417`),
        2. `+ sequence_park_scope(equipment)` (line `419`),
        3. `+ [wait_until_safe()]` (lines `420-422`).

   ```mermaid
   flowchart TD
       Enter(["Enter sequence_safetynet(name)"]) --> CheckOuter{"Outer Conditions:\nloop_until_sunrise() +\ncaller conditions (line 411)"}
       CheckOuter -- "Unfulfilled (Sunrise / Window Passed)" --> Exit(["Exit sequence_safetynet"])
       CheckOuter -- "Fulfilled" --> CheckSafe{"Inner 'On Safe' Condition:\nloop_while_safe() (line 415)"}
       CheckSafe -- "is_safe == True" --> RunInstr["Run caller instructions (line 416)"]
       RunInstr --> Interrupted{"Did is_safe become False\nduring instructions?"}
       Interrupted -- "No (Completed)" --> EndSet["end_instruction(name) (line 416)\nTerminates outer container"]
       EndSet --> Exit
       Interrupted -- "Yes (Safety Trip)" --> Park
       CheckSafe -- "is_safe == False" --> Park["sequence_park_scope(equipment) (line 419)"]
       Park --> WaitSafe["wait_until_safe() (line 421)\n(Bounded by outer loop_until_sunrise)"]
       WaitSafe --> CheckOuter
   ```

5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Always prepends `loop_until_sunrise()` to `conditions` (`line 411`) so `wait_until_safe()` can never hang past sunrise if the roof stays closed all night.
7. **Complexity & Risk Notes:** $O(|I| + |C|)$.

---

### 2.15 `sequence_end` (`builders.py:426-471`)

1. **Purpose & Architectural Role:**
   Builds the `EndAreaContainer` (`"End Sequence"`) containing `"Park Telescope"`. Provides a deterministic night-closing sequence: optional `RUN-FINISHED` event (only when `step_count > 0`), a bounded 1-iteration 3-minute `"On Safe"` pre-park delay (`PREPARK-DELAY: 3 min`), `PARK-STARTING`, `connect_all_equipment()`, `sequence_park_scope(equipment)`, `sequence_warm_camera(equipment)`, and `PARK-FINISHED`.
2. **Signature, Decorators & Inputs:**
   ```python
   def sequence_end(
       equipment: ObservatoryEquipment, *, step_count: int = 0
   ) -> dict[str, Any]:
   ```
   - `equipment: ObservatoryEquipment` — Hardware capabilities for conditional park and camera warm.
   - `step_count: int = 0` (keyword-only) — Number of non-empty Target-area steps (`0` for a bare park sequence).
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — `EndAreaContainer` wrapping the `"Park Telescope"` `SequentialContainer`.
4. **Step-by-Step Internal Logic:**
   1. Line `445`: Sets `delay_minutes = 3`.
   2. Line `446`: Computes `head = [run_finished_event(step_count)] if step_count else []`.
   3. Lines `447-471`: Returns `container_end` wrapping a single `container_sequential(name="Park Telescope")` whose `instructions` are:
      - `head` (`[run_finished_event(step_count)]` when `step_count > 0`, else `[]`),
      - Inner `container_sequential(name="On Safe", conditions=[loop_while_safe(), loop_for_iterations(1)], instructions=[send_event("PREPARK-DELAY: 3 min"), wait_for_timespan(180)])` (lines `454-461`),
      - `send_event("PARK-STARTING")` (line `462`),
      - `connect_all_equipment()` (line `463`),
      - `+ sequence_park_scope(equipment)` (line `465`),
      - `+ sequence_warm_camera(equipment)` (line `466`),
      - `+ [send_event("PARK-FINISHED")]` (line `467`).

   ```mermaid
   flowchart TD
       Start(["Enter EndAreaContainer -> 'Park Telescope'"]) --> HasSteps{"step_count > 0? (line 446)"}
       HasSteps -- Yes --> EmitRunFin["send_event('RUN-FINISHED: N step(s) complete')\n(line 446; wakes night-loop controller)"]
       HasSteps -- No --> CheckSafe
       EmitRunFin --> CheckSafe{"Inner 'On Safe':\nloop_while_safe() AND\nloop_for_iterations(1) (line 456)"}
       CheckSafe -- "is_safe == True (1 pass)" --> PrePark["send_event('PREPARK-DELAY: 3 min')\nwait_for_timespan(180) (lines 458-459)"]
       CheckSafe -- "is_safe == False (Skip delay)" --> ParkStart
       PrePark --> ParkStart["send_event('PARK-STARTING') (line 462)"]
       ParkStart --> Connect["connect_all_equipment() (line 463)"]
       Connect --> Park["sequence_park_scope(equipment) (line 465)"]
       Park --> Warm["sequence_warm_camera(equipment) (line 466)"]
       Warm --> ParkFin["send_event('PARK-FINISHED') (line 467)"]
       ParkFin --> Done(["End Sequence Complete"])
   ```

5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - **Why `RUN-FINISHED` precedes `PREPARK-DELAY`:** Gives the automated controller a 3-minute window while the roof is open (`is_safe=True`) to pick and load the next target before the mount physically parks and the camera warms.
   - **Why `"On Safe"` has `loop_while_safe()` + `loop_for_iterations(1)`:** If the roof is already closed (`is_safe=False`), the 3-minute delay is skipped immediately and the mount parks right away; if the roof is open, it runs at most once (`Iterations=1`).
   - **Self-contained park (`connect_all_equipment()` on line `463`):** Ensures a standalone park sequence (`step_count=0`) connects equipment before issuing `ParkScope`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 2.16 `_sequence_with_steps` (`builders.py:474-494`)

1. **Purpose & Architectural Role:**
   Assembles the complete root N.I.N.A. sequence (`SequenceRootContainer`) from `[container_start(...), container_target(steps), sequence_end(equipment, step_count=step_count)]` and runs the root JSON.NET serialization passes (`_add_refs` and `_fix_provider` via `container_root`).
2. **Signature, Decorators & Inputs:**
   ```python
   def _sequence_with_steps(
       equipment: ObservatoryEquipment, steps: list[Any], step_count: int
   ) -> dict[str, Any]:
   ```
   - `equipment: ObservatoryEquipment` — Observatory equipment state.
   - `steps: list[Any]` — Flattened list of Target-area step containers.
   - `step_count: int` — Number of non-empty payloads that contributed to `steps`.
3. **Outputs & Return Semantics:**
   - Returns `dict[str, Any]` — Finalized `SequenceRootContainer` JSON dict.
4. **Step-by-Step Internal Logic:**
   1. Lines `487-490`: Builds `container_start([connect_all_equipment()] + ([run_starting_event(step_count)] if step_count else []))`.
   2. Line `491`: Builds `container_target(steps)`.
   3. Line `492`: Builds `sequence_end(equipment, step_count=step_count)`.
   4. Lines `485-494`: Wraps all three in `container_root([...])` and returns the numbered, provider-deduplicated dict.
5. **Side Effects & I/O:** Mutates the AST nodes in `steps` in-place via `container_root` (`_add_refs` and `_fix_provider`).
6. **Error Handling & Edge Cases:** When `step_count == 0` (e.g., `build_sequence_many(equipment, [])`), omits both `RUN-STARTING` and `RUN-FINISHED` while still connecting equipment, running the pre-park check, parking, and warming.
7. **Complexity & Risk Notes:** $O(N)$ over the full AST via `container_root`.
