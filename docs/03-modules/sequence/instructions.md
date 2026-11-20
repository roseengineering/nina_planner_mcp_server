# Module Specification: `nina_planner/sequence/instructions.py`

> **Navigation:** [⬆️ Up to Atlas Index](../../README.md) | [⬆️ Sequence Package](./README.md) | **Prev Layer:** [`nodes.md`](./nodes.md) | **Next Layer:** [`builders.md`](./builders.md) · [`targets-and-variants.md`](./targets-and-variants.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/sequence/instructions.py`
- **Line Count:** 582 lines (`1-582`)
- **Module Docstring (`lines 1-6`):**
  > N.I.N.A. sequence-item factories: one function per instruction or condition.
  > Each returns the JSON node dict N.I.N.A.'s sequencer expects — a type name plus its properties — with no side effects; `builders`/`targets` compose them into a runnable sequence.
- **Architectural Layer:** Layer 2 — Pure AST Factory Functions for Leaf Instructions, Wait Items, Loop Conditions, Exposure Containers (`SmartExposure`, `SkyFlat`), `ninaAPI` Event Emitters, and `Sequencer+` Plugin Expressions/Arrays/Variables.
- **Total Functions Defined:** 54 functions (48 re-exported in `sequence/__init__.py` + 1 private helper `_step_word` + 5 internal `Sequencer+` factories `declare_variable`, `loop_while_expression`, `initialize_array`, `get_array`, `put_array` imported directly by `builders.py` and `targets.py`).
- **Inbound Callers:**
  - `nina_planner/sequence/builders.py` (imports 29 functions)
  - `nina_planner/sequence/targets.py` (imports 24 functions)
  - `nina_planner/sequence/__init__.py` (re-exports 48 functions)
  - `tests/test_sequence.py` & `tests/sequence_helpers.py`
- **Outbound Imports (`lines 8-10`):**
  - `typing.Any` (standard library, line `8`)
  - `.nodes._azalt_coordinates`, `.nodes._child`, `.nodes._container_base` (Layer 1, line `10`)
- **Module-Level Constants:** None.

---

## 2. Complete CLR `$type` Catalog (`instructions.py`)

| Category | Function(s) | Emitted CLR `$type` String |
| :--- | :--- | :--- |
| **Connect** | `connect_all_equipment` | `"NINA.Sequencer.SequenceItem.Connect.ConnectAllEquipment, NINA.Sequencer"` |
| **Telescope / Mount** | `unpark_scope` | `"NINA.Sequencer.SequenceItem.Telescope.UnparkScope, NINA.Sequencer"` |
| **Telescope / Mount** | `park_scope` | `"NINA.Sequencer.SequenceItem.Telescope.ParkScope, NINA.Sequencer"` |
| **Telescope / Mount** | `home_scope` | `"NINA.Sequencer.SequenceItem.Telescope.FindHome, NINA.Sequencer"` |
| **Telescope / Mount** | `set_tracking` | `"NINA.Sequencer.SequenceItem.Telescope.SetTracking, NINA.Sequencer"` |
| **Telescope / Mount** | `slew_to_azalt` | `"NINA.Sequencer.SequenceItem.Telescope.SlewScopeToAltAz, NINA.Sequencer"` |
| **Camera Thermal** | `warm_camera` | `"NINA.Sequencer.SequenceItem.Camera.WarmCamera, NINA.Sequencer"` |
| **Camera Thermal** | `cool_camera` | `"NINA.Sequencer.SequenceItem.Camera.CoolCamera, NINA.Sequencer"` |
| **Guider** | `start_guiding` | `"NINA.Sequencer.SequenceItem.Guider.StartGuiding, NINA.Sequencer"` |
| **Guider** | `stop_guiding` | `"NINA.Sequencer.SequenceItem.Guider.StopGuiding, NINA.Sequencer"` |
| **Guider Trigger** | `dither_after_exposures` | `"NINA.Sequencer.Trigger.Guider.DitherAfterExposures, NINA.Sequencer"` |
| **Platesolving** | `slew_and_center` | `"NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer"` |
| **Platesolving** | `slew_center_and_rotate` | `"NINA.Sequencer.SequenceItem.Platesolving.CenterAndRotate, NINA.Sequencer"` |
| **Autofocus** | `run_autofocus` | `"NINA.Sequencer.SequenceItem.Autofocus.RunAutofocus, NINA.Sequencer"` |
| **Utility** | `annotation` | `"NINA.Sequencer.SequenceItem.Utility.Annotation, NINA.Sequencer"` |
| **Filter Wheel** | `switch_filter` | `"NINA.Sequencer.SequenceItem.FilterWheel.SwitchFilter, NINA.Sequencer"` |
| **Filter Info** | `switch_filter` (`Filter`) | `"NINA.Core.Model.Equipment.FilterInfo, NINA.Core"` |
| **Flat Device** | `close_cover` | `"NINA.Sequencer.SequenceItem.FlatDevice.CloseCover, NINA.Sequencer"` |
| **Flat Device** | `open_cover` | `"NINA.Sequencer.SequenceItem.FlatDevice.OpenCover, NINA.Sequencer"` |
| **Flat Device** | `sky_flats` (outer) | `"NINA.Sequencer.SequenceItem.FlatDevice.SkyFlat, NINA.Sequencer"` |
| **Flat Device** | `sky_flats` (inner loop) | `"NINA.Sequencer.Container.SequentialContainer, NINA.Sequencer"` |
| **Safety Wait** | `wait_until_safe` | `"NINA.Sequencer.SequenceItem.SafetyMonitor.WaitUntilSafe, NINA.Sequencer"` |
| **Altitude / Time Waits** | `wait_until_above_horizon` | `"NINA.Sequencer.SequenceItem.Utility.WaitUntilAboveHorizon, NINA.Sequencer"` |
| **Altitude / Time Waits** | `wait_until_above_altitude` | `"NINA.Sequencer.SequenceItem.Utility.WaitForAltitude, NINA.Sequencer"` |
| **Altitude / Time Waits** | `wait_until_time` | `"NINA.Sequencer.SequenceItem.Utility.WaitForTime, NINA.Sequencer"` |
| **Altitude / Time Waits** | `wait_if_sun_altitude` | `"NINA.Sequencer.SequenceItem.Utility.WaitForSunAltitude, NINA.Sequencer"` |
| **Altitude / Time Waits** | `wait_for_timespan` | `"NINA.Sequencer.SequenceItem.Utility.WaitForTimeSpan, NINA.Sequencer"` |
| **Wait/Loop Data** | `wait_*` / `loop_*` (`Data`) | `"NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer"` |
| **DateTime Providers** | `wait_until_dusk`, `loop_until_dusk` | `"NINA.Sequencer.Utility.DateTimeProvider.DuskProvider, NINA.Sequencer"` |
| **DateTime Providers** | `wait_until_dawn`, `loop_until_dawn` | `"NINA.Sequencer.Utility.DateTimeProvider.DawnProvider, NINA.Sequencer"` |
| **DateTime Providers** | `wait_until_sunset`, `loop_until_sunset` | `"NINA.Sequencer.Utility.DateTimeProvider.SunsetProvider, NINA.Sequencer"` |
| **DateTime Providers** | `loop_until_sunrise` | `"NINA.Sequencer.Utility.DateTimeProvider.SunriseProvider, NINA.Sequencer"` |
| **DateTime Providers** | `loop_until_meridian` | `"NINA.Sequencer.Utility.DateTimeProvider.MeridianProvider, NINA.Sequencer"` |
| **Conditions** | `loop_until_sun_altitude` | `"NINA.Sequencer.Conditions.SunAltitudeCondition, NINA.Sequencer"` |
| **Conditions** | `loop_until_time` | `"NINA.Sequencer.Conditions.TimeCondition, NINA.Sequencer"` |
| **Conditions** | `loop_while_safe` | `"NINA.Sequencer.Conditions.SafetyMonitorCondition, NINA.Sequencer"` |
| **Conditions** | `loop_while_above_horizon` | `"NINA.Sequencer.Conditions.AboveHorizonCondition, NINA.Sequencer"` |
| **Conditions** | `loop_while_above_altitude` | `"NINA.Sequencer.Conditions.AltitudeCondition, NINA.Sequencer"` |
| **Conditions** | `loop_for_iterations` | `"NINA.Sequencer.Conditions.LoopCondition, NINA.Sequencer"` |
| **Imaging** | `take_exposure` | `"NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer"` |
| **Imaging** | `take_exposure` (`Binning`) | `"NINA.Core.Model.Equipment.BinningMode, NINA.Core"` |
| **Imaging** | `smart_exposure` | `"NINA.Sequencer.SequenceItem.Imaging.SmartExposure, NINA.Sequencer"` |
| **ninaAPI Plugin** | `send_event`, `run_*_event` | `"ninaAPI.SequenceItems.SendEventInstruction, ninaAPI"` |
| **Sequencer+ Plugin** | `end_instruction` | `"NINA.Plugin.SequencerPlus.EndInstructionSet, NINA.Plugin.SequencerPlus"` |
| **Sequencer+ Plugin** | `declare_variable` | `"NINA.Plugin.SequencerPlus.SetVariable, NINA.Plugin.SequencerPlus"` |
| **Sequencer+ Plugin** | `loop_while_expression` | `"NINA.Plugin.SequencerPlus.LoopWhile, NINA.Plugin.SequencerPlus"` |
| **Sequencer+ Plugin** | `initialize_array` | `"NINA.Plugin.SequencerPlus.InitializeArray, NINA.Plugin.SequencerPlus"` |
| **Sequencer+ Plugin** | `get_array` | `"NINA.Plugin.SequencerPlus.GetArray, NINA.Plugin.SequencerPlus"` |
| **Sequencer+ Plugin** | `put_array` | `"NINA.Plugin.SequencerPlus.PutArray, NINA.Plugin.SequencerPlus"` |
| **Sequencer+ Plugin** | `Expr` wrapper | `"NINA.Plugin.SequencerPlus.Expr, NINA.Plugin.SequencerPlus"` |

---

## 3. Function & Method Reference

### 3.1 Equipment, Mount, Camera, Guider & Platesolving Instructions (`lines 15-183`)

#### 3.1.1 `connect_all_equipment` (`instructions.py:15-19`)
1. **Purpose & Architectural Role:** Emits N.I.N.A.'s `ConnectAllEquipment` instruction. Placed at the start of `StartAreaContainer` (`builders.py:488`), inside every `acquisition_container` (`builders.py:94`), and before parking in `sequence_end` (`builders.py:463`) so each phase is self-connecting.
2. **Signature, Decorators & Inputs:** `def connect_all_equipment() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Connect.ConnectAllEquipment, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Lines `17-19`: Calls and returns `_child("NINA.Sequencer.SequenceItem.Connect.ConnectAllEquipment, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.2 `unpark_scope` (`instructions.py:22-24`)
1. **Purpose & Architectural Role:** Leaf factory for `UnparkScope`. Wrapped conditionally by `builders.sequence_unpark_scope` (`builders.py:261-272`).
2. **Signature, Decorators & Inputs:** `def unpark_scope() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Telescope.UnparkScope, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `24`: Returns `_child("NINA.Sequencer.SequenceItem.Telescope.UnparkScope, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Forbidden in dark/bias payloads (enforced by `_contains_type` assertion in `targets.py:175`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.3 `park_scope` (`instructions.py:27-29`)
1. **Purpose & Architectural Role:** Leaf factory for `ParkScope` — the canonical closing maneuver. Wrapped conditionally by `builders.sequence_park_scope` (`builders.py:244-247`).
2. **Signature, Decorators & Inputs:** `def park_scope() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Telescope.ParkScope, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `29`: Returns `_child("NINA.Sequencer.SequenceItem.Telescope.ParkScope, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.4 `home_scope` (`instructions.py:32-36`)
1. **Purpose & Architectural Role:** Leaf factory for `FindHome` (physical axis calibration, never a park fallback). Wrapped conditionally by `builders.sequence_home_scope` (`builders.py:250-258`).
2. **Signature, Decorators & Inputs:** `def home_scope() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Telescope.FindHome, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `36`: Returns `_child("NINA.Sequencer.SequenceItem.Telescope.FindHome, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Must be preceded by `unpark_scope` in the sequence because homing a parked mount returns HTTP 409 in N.I.N.A.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.5 `warm_camera` (`instructions.py:39-43`)
1. **Purpose & Architectural Role:** Emits `WarmCamera` with `"Duration": 0` (immediate warm-up without timed ramp). Wrapped by `builders.sequence_warm_camera` and `builders.sequence_cool_camera` when `plan.cooler.on` is `False`.
2. **Signature, Decorators & Inputs:** `def warm_camera() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Camera.WarmCamera, NINA.Sequencer") | {"Duration": 0}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `41-43`: Merges `{"Duration": 0}` onto `_child("NINA.Sequencer.SequenceItem.Camera.WarmCamera, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.6 `cool_camera` (`instructions.py:46-51`)
1. **Purpose & Architectural Role:** Emits `CoolCamera` to target `temperature` (°C) with `"Duration": 0`. Wrapped by `builders.sequence_cool_camera`.
2. **Signature, Decorators & Inputs:** `def cool_camera(temperature: float) -> dict[str, Any]:` (`temperature: float` in Celsius).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Camera.CoolCamera, NINA.Sequencer") | {"Temperature": temperature, "Duration": 0}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `48-51`: Merges `{"Temperature": temperature, "Duration": 0}` onto `_child("NINA.Sequencer.SequenceItem.Camera.CoolCamera, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.7 `start_guiding` (`instructions.py:54-58`)
1. **Purpose & Architectural Role:** Emits `StartGuiding` with `"ForceCalibration": calibration`. Used inside `"Image Object"` in `target_payload_lights` (`targets.py:393`).
2. **Signature, Decorators & Inputs:** `def start_guiding(calibration: bool = False) -> dict[str, Any]:` (`calibration: bool = False`).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Guider.StartGuiding, NINA.Sequencer") | {"ForceCalibration": calibration}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `56-58`: Merges `{"ForceCalibration": calibration}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Forbidden in dark/bias payloads (`targets.py:175`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.8 `stop_guiding` (`instructions.py:61-63`)
1. **Purpose & Architectural Role:** Emits `StopGuiding` at the end of `"Image Object"` after all exposure groups complete (`targets.py:410`).
2. **Signature, Decorators & Inputs:** `def stop_guiding() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Guider.StopGuiding, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `63`: Returns `_child("NINA.Sequencer.SequenceItem.Guider.StopGuiding, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.9 `set_tracking` (`instructions.py:66-79`)
1. **Purpose & Architectural Role:** Emits `SetTracking` with ASCOM tracking rate code `mode` (`0` = Sidereal, `1` = Lunar, `2` = Solar, `3` = King, `4` = Stopped). Used with `mode=0` in `target_payload_lights` (`targets.py:377`) after `sequence_home_scope`.
2. **Signature, Decorators & Inputs:** `def set_tracking(mode: int) -> dict[str, Any]:` (`mode: int`).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Telescope.SetTracking, NINA.Sequencer") | {"TrackingMode": mode}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `75-79`: Merges `{"TrackingMode": mode}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Caller is responsible for supplying a valid ASCOM code (`0..4`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.10 `slew_and_center` (`instructions.py:82-86`)
1. **Purpose & Architectural Role:** Emits `Center` (`"Inherited": True`, inheriting coordinates from the enclosing `DeepSkyObjectContainer`). Used in `target_payload_lights` (`targets.py:389`) when `pointing.requests_rotation()` is `False`.
2. **Signature, Decorators & Inputs:** `def slew_and_center() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Platesolving.Center, NINA.Sequencer") | {"Inherited": True}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `84-86`: Merges `{"Inherited": True}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Forbidden in dark/bias payloads (`"Platesolving"` check in `targets.py:175`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.11 `slew_center_and_rotate` (`instructions.py:89-95`)
1. **Purpose & Architectural Role:** Emits `CenterAndRotate` (`"Inherited": True`, inheriting coordinates and `PositionAngle` from the enclosing `DeepSkyObjectContainer`). Used in `target_payload_lights` (`targets.py:387`) when `pointing.requests_rotation()` is `True`.
2. **Signature, Decorators & Inputs:** `def slew_center_and_rotate() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Platesolving.CenterAndRotate, NINA.Sequencer") | {"Inherited": True}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `91-95`: Merges `{"Inherited": True}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Forbidden in dark/bias payloads (`"Platesolving"` check in `targets.py:175`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.12 `slew_to_azalt` (`instructions.py:98-102`)
1. **Purpose & Architectural Role:** Emits `SlewScopeToAltAz` with topocentric coordinates encoded by `nodes._azalt_coordinates(az, alt)`. Used in `target_payload_flats` (`targets.py:240-245`) to point at the twilight sky-flat azimuth/altitude.
2. **Signature, Decorators & Inputs:** `def slew_to_azalt(az: float, alt: float) -> dict[str, Any]:` (`az: float`, `alt: float` in degrees).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Telescope.SlewScopeToAltAz, NINA.Sequencer") | {"Coordinates": _azalt_coordinates(az, alt)}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `100-102`: Calls `_azalt_coordinates(az, alt)` and merges `"Coordinates"` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.13 `run_autofocus` (`instructions.py:105-107`)
1. **Purpose & Architectural Role:** Emits `RunAutofocus`. Executed once on the reference filter after centering and before starting guiding in `target_payload_lights` (`targets.py:392`).
2. **Signature, Decorators & Inputs:** `def run_autofocus() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Autofocus.RunAutofocus, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `107`: Returns `_child("NINA.Sequencer.SequenceItem.Autofocus.RunAutofocus, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.14 `annotation` (`instructions.py:110-114`)
1. **Purpose & Architectural Role:** Emits a textless `Annotation` item (`"Text": None`), which N.I.N.A.'s built-in `SkyFlat` template places at indices 0, 1, and 3 of its `Items` collection (`sky_flats`, `instructions.py:164-167`).
2. **Signature, Decorators & Inputs:** `def annotation() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Utility.Annotation, NINA.Sequencer") | {"Text": None}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `112-114`: Merges `{"Text": None}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Preserves positional slot layout inside `SkyFlat`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.15 `switch_filter` (`instructions.py:117-140`)
1. **Purpose & Architectural Role:** Emits `SwitchFilter`. When `filter_name` is provided, populates `"Filter"` with a `FilterInfo` node carrying `"_name"` and `"_position"`. When `filter_name is None` (used in `smart_exposure` for `cover` and `shutter` dark/bias variants), emits `"Filter": None` so N.I.N.A.'s `SwitchFilter.Execute` throws `SequenceItemSkippedException` (marking the child `SKIPPED` without moving the wheel while keeping `SmartExposure.Items[0]` positionally populated).
2. **Signature, Decorators & Inputs:**
   ```python
   def switch_filter(
       filter_name: str | None,
       filter_position: int | None,
   ) -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:** Returns `SwitchFilter` node dict with `"Filter": None` or `"Filter": {"$id": None, "$type": "NINA.Core.Model.Equipment.FilterInfo, NINA.Core", "Parent": None, "_name": filter_name, "_position": filter_position}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `131-140`: Calls `_child("NINA.Sequencer.SequenceItem.FilterWheel.SwitchFilter, NINA.Sequencer")` and merges `"Filter"`, which evaluates to `None` if `filter_name is None` else `_child("NINA.Core.Model.Equipment.FilterInfo, NINA.Core") | {"_name": filter_name, "_position": filter_position}`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `filter_name=None` safely bypasses filter wheel movement in `SmartExposure` without causing N.I.N.A.'s positional child validator to crash.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.16 `close_cover` (`instructions.py:143-146`)
1. **Purpose & Architectural Role:** Emits `CloseCover` to close a motorized flat panel/flip-flat cover before exposures in `target_payload_darks(..., variant="cover")` (`targets.py:165`).
2. **Signature, Decorators & Inputs:** `def close_cover() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.FlatDevice.CloseCover, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `146`: Returns `_child("NINA.Sequencer.SequenceItem.FlatDevice.CloseCover, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Capability-checked at load time by `variants.validate_dark_variant`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.17 `open_cover` (`instructions.py:149-151`)
1. **Purpose & Architectural Role:** Emits `OpenCover` to reopen a motorized cover after `cover`-variant dark/bias exposures finish (`targets.py:165`) so subsequent sky steps never shoot through a closed cover.
2. **Signature, Decorators & Inputs:** `def open_cover() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.FlatDevice.OpenCover, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `151`: Returns `_child("NINA.Sequencer.SequenceItem.FlatDevice.OpenCover, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.1.18 `sky_flats` (`instructions.py:154-182`)
1. **Purpose & Architectural Role:** Builds N.I.N.A.'s composite `SkyFlat` container (`"Twilight Sky Flats"`) for `count` `FLAT` frames on one filter, dynamically solving exposure time in `[0.0, 30.0]` seconds to hit a `50% ± 10%` histogram target with dithering disabled.
2. **Signature, Decorators & Inputs:**
   ```python
   def sky_flats(count: int, filter_name: str, filter_position: int) -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:** Returns a `SkyFlat` container node dict containing 5 positional `Items` (`annotation()`, `annotation()`, `switch_filter(...)`, `annotation()`, and an inner `SequentialContainer` looping `count` times over `take_exposure(0.0, "FLAT")`) plus 7 sky-flat histogram/dither properties.
4. **Step-by-Step Internal Logic:**
   1. Lines `160-174`: Calls `_container_base` with `ctype="NINA.Sequencer.SequenceItem.FlatDevice.SkyFlat, NINA.Sequencer"`, `name="Twilight Sky Flats"`, and `instructions` containing three `annotation()` placeholders, `switch_filter(filter_name, filter_position)`, and an inner `SequentialContainer` with `conditions=[loop_for_iterations(count)]` and `instructions=[take_exposure(0.0, "FLAT")]`.
   2. Lines `174-182`: Merges `"MinExposure": 0.0`, `"MaxExposure": 30.0`, `"HistogramTargetPercentage": 0.5`, `"HistogramTolerancePercentage": 0.1`, `"ShouldDither": False`, `"DitherPixels": 5.0`, `"DitherSettleTime": 10.0`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** The 5-item internal array (`Annotation`, `Annotation`, `SwitchFilter`, `Annotation`, `SequentialContainer`) matches N.I.N.A.'s positional `SkyFlat` item layout.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 3.2 Wait Instructions (`lines 188-282`)

#### 3.2.1 `wait_until_safe` (`instructions.py:188-192`)
1. **Purpose & Architectural Role:** Emits `WaitUntilSafe`, blocking sequence execution while the safety monitor reports `is_safe=False`. Placed at the tail of `builders.sequence_safetynet` (`builders.py:421`).
2. **Signature, Decorators & Inputs:** `def wait_until_safe() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.SafetyMonitor.WaitUntilSafe, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Lines `190-192`: Calls `_child("NINA.Sequencer.SequenceItem.SafetyMonitor.WaitUntilSafe, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.2 `wait_until_above_horizon` (`instructions.py:195-205`)
1. **Purpose & Architectural Role:** Emits `WaitUntilAboveHorizon` inside `"Wait For Object"` (`targets.py:325-327`), blocking until the parent DSO target clears the site horizon plus `degrees` offset (`Comparator: 3`).
2. **Signature, Decorators & Inputs:** `def wait_until_above_horizon(degrees: float) -> dict[str, Any]:` (`degrees: float`).
3. **Outputs & Return Semantics:** Returns `WaitUntilAboveHorizon` node dict with `"HasDsoParent": True` and `"Data": WaitLoopData(Offset=degrees, Comparator=3)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `197-205`: Merges `"HasDsoParent": True` and `"Data": _child("NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer") | {"Offset": degrees, "Comparator": 3}` onto `_child("NINA.Sequencer.SequenceItem.Utility.WaitUntilAboveHorizon, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Requires an enclosing `DeepSkyObjectContainer` (`HasDsoParent=True`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.3 `wait_until_above_altitude` (`instructions.py:208-219`)
1. **Purpose & Architectural Role:** Emits `WaitForAltitude` inside `"Wait For Object"` (`targets.py:328`), blocking until the parent DSO target rises above `degrees` absolute altitude (`"AboveOrBelow": ">"`, `"Comparator": 3`).
2. **Signature, Decorators & Inputs:** `def wait_until_above_altitude(degrees: float) -> dict[str, Any]:` (`degrees: float`).
3. **Outputs & Return Semantics:** Returns `WaitForAltitude` node dict with `"HasDsoParent": True`, `"AboveOrBelow": ">"`, and `"Data": WaitLoopData(Offset=degrees, Comparator=3)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `210-219`: Merges `"HasDsoParent": True`, `"AboveOrBelow": ">"`, and `"Data": _child("NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer") | {"Offset": degrees, "Comparator": 3}` onto `_child("NINA.Sequencer.SequenceItem.Utility.WaitForAltitude, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Requires an enclosing `DeepSkyObjectContainer`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.4 `wait_until_time` (`instructions.py:222-227`)
1. **Purpose & Architectural Role:** Base factory for `WaitForTime` keyed on a `DateTimeProvider` CLR type `source` with `"MinutesOffset": 0`.
2. **Signature, Decorators & Inputs:** `def wait_until_time(source: str) -> dict[str, Any]:` (`source: str` CLR type name).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Utility.WaitForTime, NINA.Sequencer") | {"MinutesOffset": 0, "SelectedProvider": _child(source)}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `224-227`: Merges `"MinutesOffset": 0` and `"SelectedProvider": _child(source)` onto `_child("NINA.Sequencer.SequenceItem.Utility.WaitForTime, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `"SelectedProvider"` is later deduplicated across the sequence by `nodes._fix_provider`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.5 `wait_until_dusk` (`instructions.py:230-234`)
1. **Purpose & Architectural Role:** Emits `WaitForTime` using `DuskProvider` (astronomical dusk). Called by `builders.sequence_wait_until_dusk` (`builders.py:385`).
2. **Signature, Decorators & Inputs:** `def wait_until_dusk() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `wait_until_time("NINA.Sequencer.Utility.DateTimeProvider.DuskProvider, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Lines `232-234`: Delegates to `wait_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.6 `wait_until_dawn` (`instructions.py:237-241`)
1. **Purpose & Architectural Role:** Emits `WaitForTime` using `DawnProvider` (astronomical dawn). Called by `builders.sequence_wait_until_dawn` (`builders.py:373`).
2. **Signature, Decorators & Inputs:** `def wait_until_dawn() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `wait_until_time("NINA.Sequencer.Utility.DateTimeProvider.DawnProvider, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Lines `239-241`: Delegates to `wait_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.7 `wait_until_sunset` (`instructions.py:244-248`)
1. **Purpose & Architectural Role:** Emits `WaitForTime` using `SunsetProvider`. Called by `builders.sequence_wait_until_sunset` (`builders.py:355`).
2. **Signature, Decorators & Inputs:** `def wait_until_sunset() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `wait_until_time("NINA.Sequencer.Utility.DateTimeProvider.SunsetProvider, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Lines `246-248`: Delegates to `wait_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.8 `wait_if_sun_altitude` (`instructions.py:251-263`)
1. **Purpose & Architectural Role:** Base factory for `WaitForSunAltitude` parameterized by solar altitude `degrees` and N.I.N.A. `comparator` (`3` = above, `1` = below).
2. **Signature, Decorators & Inputs:** `def wait_if_sun_altitude(degrees: float, comparator: int) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Utility.WaitForSunAltitude, NINA.Sequencer") | {"Data": _child("NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer") | {"Offset": degrees, "Comparator": comparator}}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `256-263`: Constructs `WaitForSunAltitude` with nested `WaitLoopData` carrying `Offset` and `Comparator`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.9 `wait_if_sun_altitude_above` (`instructions.py:266-268`)
1. **Purpose & Architectural Role:** Waits while solar altitude is above `degrees` (`comparator=3`). Used in `sequence_wait_until_sunset` (`builders.py:356`) with `0` degrees so the container does not exit until the sun is actually below `0°` (preventing `loop_until_sunset()` from looping a second pass).
2. **Signature, Decorators & Inputs:** `def wait_if_sun_altitude_above(degrees: float) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `wait_if_sun_altitude(degrees, 3)`.
4. **Step-by-Step Internal Logic:**
   1. Line `268`: Calls `wait_if_sun_altitude(degrees, 3)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.10 `wait_if_sun_altitude_below` (`instructions.py:271-273`)
1. **Purpose & Architectural Role:** Waits while solar altitude is below `degrees` (`comparator=1`). Used in `sequence_wait_until_dawn` (`builders.py:374`) with `-8` degrees so dawn flats wait until the sky climbs above `-8°` solar altitude.
2. **Signature, Decorators & Inputs:** `def wait_if_sun_altitude_below(degrees: float) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `wait_if_sun_altitude(degrees, 1)`.
4. **Step-by-Step Internal Logic:**
   1. Line `273`: Calls `wait_if_sun_altitude(degrees, 1)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.2.11 `wait_for_timespan` (`instructions.py:276-282`)
1. **Purpose & Architectural Role:** Emits `WaitForTimeSpan` with `"Time": int(seconds)`. Used in `builders.sequence_end` (`builders.py:459`) for the 180-second (`3 min`) pre-park delay.
2. **Signature, Decorators & Inputs:** `def wait_for_timespan(seconds: float) -> dict[str, Any]:` (`seconds: float`).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.SequenceItem.Utility.WaitForTimeSpan, NINA.Sequencer") | {"Time": int(seconds)}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `278-282`: Truncates `seconds` to `int(seconds)` under `"Time"`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Fractional seconds are truncated via `int(seconds)`.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 3.3 Loop Conditions & Exposure Items (`lines 288-450`)

#### 3.3.1 `loop_until_sun_altitude` (`instructions.py:288-298`)
1. **Purpose & Architectural Role:** Base factory for `SunAltitudeCondition` keyed on solar altitude `degrees` and `comparator` (`3` = above, `1` = below).
2. **Signature, Decorators & Inputs:** `def loop_until_sun_altitude(degrees: float, comparator: int) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.Conditions.SunAltitudeCondition, NINA.Sequencer") | {"Data": _child("NINA.Sequencer.SequenceItem.Utility.WaitLoopData, NINA.Sequencer") | {"Offset": degrees, "Comparator": comparator}}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `293-298`: Merges `"Data"` (`WaitLoopData`) onto `_child("NINA.Sequencer.Conditions.SunAltitudeCondition, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.2 `loop_until_sun_altitude_above` (`instructions.py:301-303`)
1. **Purpose & Architectural Role:** Loop condition ending when solar altitude rises above `degrees` (`comparator=3`). Used in `target_payload_flats(..., dusk=False)` (`targets.py:235`) with `0` degrees to stop dawn flats at sunrise (`0°`).
2. **Signature, Decorators & Inputs:** `def loop_until_sun_altitude_above(degrees: float) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `loop_until_sun_altitude(degrees, 3)`.
4. **Step-by-Step Internal Logic:**
   1. Line `303`: Delegates to `loop_until_sun_altitude(degrees, 3)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.3 `loop_until_sun_altitude_below` (`instructions.py:306-308`)
1. **Purpose & Architectural Role:** Loop condition ending when solar altitude drops below `degrees` (`comparator=1`). Used in `target_payload_flats(..., dusk=True)` (`targets.py:233`) with `-8` degrees to stop dusk flats when the sky becomes too dark (`-8°`).
2. **Signature, Decorators & Inputs:** `def loop_until_sun_altitude_below(degrees: float) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `loop_until_sun_altitude(degrees, 1)`.
4. **Step-by-Step Internal Logic:**
   1. Line `308`: Delegates to `loop_until_sun_altitude(degrees, 1)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.4 `loop_until_time` (`instructions.py:311-316`)
1. **Purpose & Architectural Role:** Base factory for `TimeCondition` keyed on a `DateTimeProvider` CLR type `source` and `MinutesOffset: minutes`.
2. **Signature, Decorators & Inputs:** `def loop_until_time(source: str, minutes: float) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.Conditions.TimeCondition, NINA.Sequencer") | {"MinutesOffset": minutes, "SelectedProvider": _child(source)}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `313-316`: Merges `"MinutesOffset": minutes` and `"SelectedProvider": _child(source)` onto `_child("NINA.Sequencer.Conditions.TimeCondition, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `"SelectedProvider"` is deduplicated across the sequence by `nodes._fix_provider`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.5 `loop_until_sunset` (`instructions.py:319-324`)
1. **Purpose & Architectural Role:** Emits `TimeCondition` with `SunsetProvider` + `minutes`. Bounds `sequence_wait_until_sunset` (`builders.py:352`).
2. **Signature, Decorators & Inputs:** `def loop_until_sunset(minutes: float = 0) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `loop_until_time("NINA.Sequencer.Utility.DateTimeProvider.SunsetProvider, NINA.Sequencer", minutes)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `321-324`: Delegates to `loop_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.6 `loop_until_sunrise` (`instructions.py:327-332`)
1. **Purpose & Architectural Role:** Emits `TimeCondition` with `SunriseProvider` + `minutes`. Bounds every `sequence_safetynet` (`builders.py:411`) so an unsafe wait never idles past sunrise.
2. **Signature, Decorators & Inputs:** `def loop_until_sunrise(minutes: float = 0) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `loop_until_time("NINA.Sequencer.Utility.DateTimeProvider.SunriseProvider, NINA.Sequencer", minutes)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `329-332`: Delegates to `loop_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.7 `loop_until_dawn` (`instructions.py:335-339`)
1. **Purpose & Architectural Role:** Emits `TimeCondition` with `DawnProvider` + `minutes`. Bounds `sequence_wait_until_dawn` (`builders.py:370`), `"Wait For Object"` (`targets.py:304`), and `"Image Object"` (`targets.py:345`).
2. **Signature, Decorators & Inputs:** `def loop_until_dawn(minutes: float = 0) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `loop_until_time("NINA.Sequencer.Utility.DateTimeProvider.DawnProvider, NINA.Sequencer", minutes)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `337-339`: Delegates to `loop_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.8 `loop_until_dusk` (`instructions.py:342-346`)
1. **Purpose & Architectural Role:** Emits `TimeCondition` with `DuskProvider` + `minutes`. Bounds `sequence_wait_until_dusk` (`builders.py:384`).
2. **Signature, Decorators & Inputs:** `def loop_until_dusk(minutes: float = 0) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `loop_until_time("NINA.Sequencer.Utility.DateTimeProvider.DuskProvider, NINA.Sequencer", minutes)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `344-346`: Delegates to `loop_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.9 `loop_until_meridian` (`instructions.py:349-354`)
1. **Purpose & Architectural Role:** Emits `TimeCondition` with `MeridianProvider` + `minutes`. Bounds `"Wait For Object"` (`targets.py:305`) so waiting for a rising target aborts if the target transits the meridian before clearing minimum altitude/horizon constraints.
2. **Signature, Decorators & Inputs:** `def loop_until_meridian(minutes: float = 0) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `loop_until_time("NINA.Sequencer.Utility.DateTimeProvider.MeridianProvider, NINA.Sequencer", minutes)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `351-354`: Delegates to `loop_until_time(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.10 `loop_while_safe` (`instructions.py:357-359`)
1. **Purpose & Architectural Role:** Emits `SafetyMonitorCondition`, keeping an `"On Safe"` container active only while `is_safe=True`. Used in `sequence_safetynet` (`builders.py:415`) and `sequence_end` (`builders.py:456`).
2. **Signature, Decorators & Inputs:** `def loop_while_safe() -> dict[str, Any]:` (no inputs).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.Conditions.SafetyMonitorCondition, NINA.Sequencer")`.
4. **Step-by-Step Internal Logic:**
   1. Line `359`: Calls `_child("NINA.Sequencer.Conditions.SafetyMonitorCondition, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.11 `loop_while_above_horizon` (`instructions.py:362-370`)
1. **Purpose & Architectural Role:** Emits `AboveHorizonCondition` (`HasDsoParent=True`, `Comparator=3`, `Offset=degrees`). Used in `"Break If in View"` (`targets.py:312-314`) and `"Image Object"` (`targets.py:346-348`).
2. **Signature, Decorators & Inputs:** `def loop_while_above_horizon(degrees: float) -> dict[str, Any]:` (`degrees: float`).
3. **Outputs & Return Semantics:** Returns `AboveHorizonCondition` dict with `"HasDsoParent": True` and `"Data": WaitLoopData(Offset=degrees, Comparator=3)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `364-370`: Merges `"HasDsoParent": True` and `"Data"` onto `_child("NINA.Sequencer.Conditions.AboveHorizonCondition, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.12 `loop_while_above_altitude` (`instructions.py:373-381`)
1. **Purpose & Architectural Role:** Emits `AltitudeCondition` (`HasDsoParent=True`, `Comparator=3`, `Offset=degrees`). Used in `"Break If in View"` (`targets.py:315-317`) and `"Image Object"` (`targets.py:349`).
2. **Signature, Decorators & Inputs:** `def loop_while_above_altitude(degrees: float) -> dict[str, Any]:` (`degrees: float`).
3. **Outputs & Return Semantics:** Returns `AltitudeCondition` dict with `"HasDsoParent": True` and `"Data": WaitLoopData(Offset=degrees, Comparator=3)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `375-381`: Merges `"HasDsoParent": True` and `"Data"` onto `_child("NINA.Sequencer.Conditions.AltitudeCondition, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.13 `loop_for_iterations` (`instructions.py:384-389`)
1. **Purpose & Architectural Role:** Emits `LoopCondition` with `"CompletedIterations": 0` and `"Iterations": count`. Used in `sky_flats`, `smart_exposure`, `sequence_count_exposure`, and the 1-iteration `"On Safe"` pre-park delay in `sequence_end`.
2. **Signature, Decorators & Inputs:** `def loop_for_iterations(count: int) -> dict[str, Any]:` (`count: int`).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.Conditions.LoopCondition, NINA.Sequencer") | {"CompletedIterations": 0, "Iterations": count}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `386-389`: Merges `{"CompletedIterations": 0, "Iterations": count}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Note that N.I.N.A. resets `CompletedIterations` when a parent container exits and re-enters, which is why light frames pair this with `loop_while_expression("n > 0")` in `sequence_count_exposure`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.14 `dither_after_exposures` (`instructions.py:392-396`)
1. **Purpose & Architectural Role:** Emits `DitherAfterExposures` trigger with `"AfterExposures": exposures` (`0` disables dithering in N.I.N.A.'s validator). Used in `smart_exposure` (`instructions.py:445`) and `sequence_count_exposure` (`builders.py:324`).
2. **Signature, Decorators & Inputs:** `def dither_after_exposures(exposures: int) -> dict[str, Any]:` (`exposures: int`).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Sequencer.Trigger.Guider.DitherAfterExposures, NINA.Sequencer") | {"AfterExposures": exposures}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `394-396`: Merges `{"AfterExposures": exposures}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `AfterExposures=0` is valid in N.I.N.A. (`SmartExposure.Validate` checks `if (dither.AfterExposures > 0)` before requiring a connected guider).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.15 `take_exposure` (`instructions.py:399-411`)
1. **Purpose & Architectural Role:** Emits `TakeExposure` for a single frame of `exposure` seconds and `image_type` (`"LIGHT"`, `"DARK"`, `"BIAS"`, `"FLAT"`), using camera-default gain/offset (`-1`) and `1x1` binning.
2. **Signature, Decorators & Inputs:** `def take_exposure(exposure: float, image_type: str) -> dict[str, Any]:`.
3. **Outputs & Return Semantics:** Returns `TakeExposure` node dict with nested `BinningMode(X=1, Y=1)`.
4. **Step-by-Step Internal Logic:**
   1. Lines `401-411`: Merges `"ImageType": image_type`, `"ExposureTime": exposure`, `"Gain": -1`, `"Offset": -1`, `"ExposureCount": 0`, and `"Binning": _child("NINA.Core.Model.Equipment.BinningMode, NINA.Core") | {"X": 1, "Y": 1}` onto `_child("NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `exposure=0` (or `0.0`) is used for `BIAS` frames and inside `SkyFlat` (where `SkyFlat` dynamically overrides exposure duration).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.3.16 `smart_exposure` (`instructions.py:414-450`)
1. **Purpose & Architectural Role:** Builds N.I.N.A.'s `SmartExposure` container for `count` exposures (`target_payload_darks`, `targets.py:149-158`). Always populates positional `Conditions[0]` (`LoopCondition`), `Triggers[0]` (`DitherAfterExposures`), `Items[0]` (`SwitchFilter`), and `Items[1]` (`TakeExposure`).
2. **Signature, Decorators & Inputs:**
   ```python
   def smart_exposure(
       count: int,
       exposure: float,
       image_type: str,
       filter_name: str | None = None,
       filter_position: int | None = None,
       dither: int = 0,
   ) -> dict[str, Any]:
   ```
3. **Outputs & Return Semantics:** Returns `SmartExposure` container node dict.
4. **Step-by-Step Internal Logic:**
   1. Lines `441-450`: Calls `_container_base` with `ctype="NINA.Sequencer.SequenceItem.Imaging.SmartExposure, NINA.Sequencer"`, `name="Smart Exposure"`, `conditions=[loop_for_iterations(count)]`, `triggers=[dither_after_exposures(dither)]`, and `instructions=[switch_filter(filter_name, filter_position), take_exposure(exposure, image_type)]`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:**
   - **Critical N.I.N.A. Positional Validation Invariant (lines `429-440`):** `SmartExposure.Validate()` in N.I.N.A. dereferences `Triggers[0]`, `Items[0]`, and `Items[1]` unconditionally by index. Omitting `dither_after_exposures` or `switch_filter` throws an unhandled exception during validation that aborts the sequence run silently (`SEQUENCE-STARTING -> FINISHED`, step stays `CREATED`). Passing `dither=0` sets `AfterExposures=0` (disabling dither validation), and passing `filter_name=None` sets `Filter=null` on `SwitchFilter` (causing N.I.N.A. to skip the filter switch cleanly at runtime).
7. **Complexity & Risk Notes:** $O(1)$.

---

### 3.4 `ninaAPI` Event Emitters (`lines 456-490`)

#### 3.4.1 `send_event` (`instructions.py:456-460`)
1. **Purpose & Architectural Role:** Emits `ninaAPI.SequenceItems.SendEventInstruction` carrying `"Message": text`, which pushes a custom WebSocket/REST event (`ERROR-SEND-EVENT` / `SEND-EVENT`) consumed by `nina-notify` and `get_events`.
2. **Signature, Decorators & Inputs:** `def send_event(text: str) -> dict[str, Any]:` (`text: str`).
3. **Outputs & Return Semantics:** Returns `_child("ninaAPI.SequenceItems.SendEventInstruction, ninaAPI") | {"Message": text}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `458-460`: Merges `{"Message": text}` onto `_child("ninaAPI.SequenceItems.SendEventInstruction, ninaAPI")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** None.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.4.2 `_step_word` (`instructions.py:463-464`)
1. **Purpose & Architectural Role:** Private pluralization helper formatting `step_count` as `"1 step"` or `"N steps"` for `run_starting_event` and `run_finished_event`.
2. **Signature, Decorators & Inputs:** `def _step_word(count: int) -> str:` (`count: int`).
3. **Outputs & Return Semantics:** Returns `"1 step"` when `count == 1`, otherwise `f"{count} steps"`.
4. **Step-by-Step Internal Logic:**
   1. Line `464`: Evaluates `return f"{count} step" if count == 1 else f"{count} steps"`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Handles singular `1` vs. any other integer.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.4.3 `run_starting_event` (`instructions.py:467-473`)
1. **Purpose & Architectural Role:** Emits `send_event("RUN-STARTING: <N> step(s)")` inside `StartAreaContainer` (`builders.py:489`) when `step_count > 0`.
2. **Signature, Decorators & Inputs:** `def run_starting_event(step_count: int) -> dict[str, Any]:` (`step_count: int`).
3. **Outputs & Return Semantics:** Returns `SendEventInstruction` node with `"Message": f"RUN-STARTING: {_step_word(step_count)}"`.
4. **Step-by-Step Internal Logic:**
   1. Line `473`: Calls `send_event(f"RUN-STARTING: {_step_word(step_count)}")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Caller (`_sequence_with_steps`, `builders.py:489`) guards emission with `if step_count` so a 0-step park sequence never emits `RUN-STARTING`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.4.4 `run_finished_event` (`instructions.py:476-490`)
1. **Purpose & Architectural Role:** Emits `send_event("RUN-FINISHED: <N> step(s) complete")` at the head of `EndAreaContainer` (`builders.py:446`). Because N.I.N.A. enters `EndAreaContainer` only after every Target-area step has completed or skipped, and before the 3-minute `"On Safe"` pre-park delay and `ParkScope`, this event is the authoritative wake signal for the `night-loop` controller to select and load the next target.
2. **Signature, Decorators & Inputs:** `def run_finished_event(step_count: int) -> dict[str, Any]:` (`step_count: int`).
3. **Outputs & Return Semantics:** Returns `SendEventInstruction` node with `"Message": f"RUN-FINISHED: {_step_word(step_count)} complete"`.
4. **Step-by-Step Internal Logic:**
   1. Line `490`: Calls `send_event(f"RUN-FINISHED: {_step_word(step_count)} complete")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Caller (`sequence_end`, `builders.py:446`) guards emission with `if step_count` so an empty park request never triggers a `RUN-FINISHED` wake.
7. **Complexity & Risk Notes:** $O(1)$.

---

### 3.5 `Sequencer+` Plugin Instructions & Conditions (`lines 496-582`)

#### 3.5.1 `end_instruction` (`instructions.py:496-500`)
1. **Purpose & Architectural Role:** Emits `NINA.Plugin.SequencerPlus.EndInstructionSet` targeting container `name`. Used in `sequence_safetynet` (`builders.py:416`) to break out of the outer safetynet container once the inner `"On Safe"` instructions finish cleanly, and in `"Break If in View"` (`targets.py:319`) to break out of `"Wait For Object"` immediately if the target is already above horizon and minimum altitude.
2. **Signature, Decorators & Inputs:** `def end_instruction(name: str) -> dict[str, Any]:` (`name: str` matching the target enclosing container's `"Name"`).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Plugin.SequencerPlus.EndInstructionSet, NINA.Plugin.SequencerPlus") | {"InstructionSetName": name}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `498-500`: Merges `{"InstructionSetName": name}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `name` must exactly match the enclosing container's `"Name"`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.5.2 `declare_variable` (`instructions.py:503-518`)
1. **Purpose & Architectural Role:** Emits `NINA.Plugin.SequencerPlus.SetVariable` defining symbol `name` (`"n"`) with initial expression `value` (`"0"`). Placed in `target_payload_lights` (`targets.py:334`) ahead of `"Image Object"` so `GetArray` (`get_array`) can resolve `"n"` via `Symbol.FindSymbol("n", Parent)`.
2. **Signature, Decorators & Inputs:** `def declare_variable(name: str, value: str) -> dict[str, Any]:` (`name: str`, `value: str`).
3. **Outputs & Return Semantics:** Returns `_child("NINA.Plugin.SequencerPlus.SetVariable, NINA.Plugin.SequencerPlus") | {"Identifier": name, "OriginalDefinition": value}`.
4. **Step-by-Step Internal Logic:**
   1. Lines `516-518`: Merges `{"Identifier": name, "OriginalDefinition": value}` onto `_child(...)`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** Depends on `nodes._add_refs` keeping `"Parent"` and reordering `"Parent"` after `"Identifier"` (`nodes.py:71-75`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.5.3 `loop_while_expression` (`instructions.py:521-533`)
1. **Purpose & Architectural Role:** Emits `NINA.Plugin.SequencerPlus.LoopWhile` with `PredicateExpr: Expr(Expression=expression, Type="Any")`. Used in `sequence_count_exposure` (`builders.py:322`) with `expression="n > 0"`.
2. **Signature, Decorators & Inputs:** `def loop_while_expression(expression: str) -> dict[str, Any]:` (`expression: str`).
3. **Outputs & Return Semantics:** Returns `LoopWhile` condition dict with nested `Expr` node.
4. **Step-by-Step Internal Logic:**
   1. Lines `528-533`: Merges `"PredicateExpr": _child("NINA.Plugin.SequencerPlus.Expr, NINA.Plugin.SequencerPlus") | {"Expression": expression, "Type": "Any"}` onto `_child("NINA.Plugin.SequencerPlus.LoopWhile, NINA.Plugin.SequencerPlus")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `expression` must be an unquoted boolean expression (`"n > 0"`), never a quoted string literal.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.5.4 `initialize_array` (`instructions.py:536-548`)
1. **Purpose & Architectural Role:** Emits `NINA.Plugin.SequencerPlus.InitializeArray` creating or clearing the array named by expression `name` (`"'count'"`). Placed in `target_payload_lights` (`targets.py:335`).
2. **Signature, Decorators & Inputs:** `def initialize_array(name: str) -> dict[str, Any]:` (`name: str` — must be a single-quoted string literal expression such as `"'count'"`).
3. **Outputs & Return Semantics:** Returns `InitializeArray` node dict with nested `NameExpr`.
4. **Step-by-Step Internal Logic:**
   1. Lines `543-548`: Merges `"NameExpr": _child("NINA.Plugin.SequencerPlus.Expr, NINA.Plugin.SequencerPlus") | {"Expression": name, "Type": "Any"}` onto `_child("NINA.Plugin.SequencerPlus.InitializeArray, NINA.Plugin.SequencerPlus")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `Sequencer+` reads the array identifier through `NameExpr.StringValue`, so `name` must evaluate to a string (`"'count'"`), not a bare symbol (`"count"`).
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.5.5 `get_array` (`instructions.py:551-566`)
1. **Purpose & Architectural Role:** Emits `NINA.Plugin.SequencerPlus.GetArray` reading `array[index]` into symbol `variable` (`"n"`). Used before and inside each `"Exposure Group"` in `sequence_count_exposure` (`builders.py:317`, `334`).
2. **Signature, Decorators & Inputs:** `def get_array(name: str, index: int, variable: str) -> dict[str, Any]:` (`name: str`, `index: int`, `variable: str`).
3. **Outputs & Return Semantics:** Returns `GetArray` node dict with `NameExpr` (`name`), `IExpr` (`str(index)`), and `VExpr` (`variable`).
4. **Step-by-Step Internal Logic:**
   1. Lines `559-566`: Merges `"NameExpr"`, `"IExpr"` (`"Expression": str(index)`), and `"VExpr"` (`"Expression": variable`) — each wrapped in `_child("NINA.Plugin.SequencerPlus.Expr, NINA.Plugin.SequencerPlus") | {"Expression": ..., "Type": "Any"}` — onto `_child("NINA.Plugin.SequencerPlus.GetArray, NINA.Plugin.SequencerPlus")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `variable` must be a bare symbol name (`"n"`), because `GetArray.Execute` passes `VExpr.Expression` directly to `Symbol.FindSymbol(VExpr.Expression, Parent)`.
7. **Complexity & Risk Notes:** $O(1)$.

#### 3.5.6 `put_array` (`instructions.py:569-582`)
1. **Purpose & Architectural Role:** Emits `NINA.Plugin.SequencerPlus.PutArray` writing evaluated expression `value` into `array[index]`. Used in `target_payload_lights` (`targets.py:337`) with `str(count)` to prime cell `index`, and in `sequence_count_exposure` (`builders.py:333`) with `"n-1"` to decrement cell `index` after each exposure.
2. **Signature, Decorators & Inputs:** `def put_array(name: str, index: int, value: str) -> dict[str, Any]:` (`name: str`, `index: int`, `value: str`).
3. **Outputs & Return Semantics:** Returns `PutArray` node dict with `NameExpr` (`name`), `IExpr` (`str(index)`), and `VExpr` (`value`).
4. **Step-by-Step Internal Logic:**
   1. Lines `575-582`: Merges `"NameExpr"`, `"IExpr"` (`"Expression": str(index)`), and `"VExpr"` (`"Expression": value`) — each wrapped in `_child("NINA.Plugin.SequencerPlus.Expr, NINA.Plugin.SequencerPlus") | {"Expression": ..., "Type": "Any"}` — onto `_child("NINA.Plugin.SequencerPlus.PutArray, NINA.Plugin.SequencerPlus")`.
5. **Side Effects & I/O:** None.
6. **Error Handling & Edge Cases:** `value` is evaluated as an expression by `Sequencer+` (both numeric string `"5"` and arithmetic `"n-1"` evaluate to numbers).
7. **Complexity & Risk Notes:** $O(1)$.
