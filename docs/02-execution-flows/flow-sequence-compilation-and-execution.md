# Execution Flow: Sequence Compilation, AST Structure & Execution Control

[⬆️ Up to Atlas Index](../README.md) · [⬅️ Entrypoints & Lifecycle](./entrypoints-and-lifecycle.md) · [⬅️ Async, State & Error Model](./async-state-and-error-model.md) · **Related Module Specs:** [`tools/sequence.py`](../03-modules/tools/sequence.md) · [`sequence/` Package](../03-modules/sequence/README.md) · [`sequence/targets.py` & `variants.py`](../03-modules/sequence/targets-and-variants.md) · [`sequence/builders.py`](../03-modules/sequence/builders.md) · [`sequence/instructions.py`](../03-modules/sequence/instructions.md) · [`sequence/nodes.py`](../03-modules/sequence/nodes.md) · [`core/sequence_state.py`](../03-modules/core/sequence_state.md) · [`core/client.py`](../03-modules/core/client.md) · [`core/plan_utils.py`](../03-modules/core/plan_utils.md) · [`core/progress.py`](../03-modules/core/progress.md) · [`models/request.py`](../03-modules/models/strict-and-request.md)

---

## Overview

The sequence subsystem translates declarative `SequenceRequest` entries (`light`, `dark`, `bias`, `flat`) and `ObservationPlan` JSON files into a single runnable N.I.N.A. Advanced Sequencer AST (`SequenceRootContainer`), validates every hardware/plugin/attribution guardrail **before** transmitting the sequence to N.I.N.A., and synchronizes sequence start/stop transitions against N.I.N.A.'s live state endpoints (`/sequence/state` and `/sequence/json`).

This document traces the three core execution workflows:
1. **Workflow 1: Multi-Step Sequence Compilation & Loading (`load_sequence` -> `_load_many`)** — Covering both the empty teardown/park request (`request=[]`) and the non-empty multi-step compilation pipeline (`request=[...]`).
2. **Workflow 2: Inside the Sequence AST (`lights`, `darks`/`bias`, `flats`, and `EndAreaContainer`)** — Structural breakdown of the compiled JSON.NET sequence tree across `StartAreaContainer`, `TargetAreaContainer`, and `EndAreaContainer`.
3. **Workflow 3: Starting, Stopping & Inspecting Sequences (`start_sequence`, `stop_sequence`, `get_sequence_state`)** — State-polling barriers and dual-endpoint AST projection.

---

## Workflow 1: Multi-Step Sequence Compilation & Loading (`load_sequence`)

### 1.1 End-to-End Sequence Diagram (`tools/sequence.py:24-416`)

```mermaid
sequenceDiagram
    autonumber
    actor Caller as MCP Caller / Night-Loop
    participant Tool as tools.sequence.load_sequence<br/>(sequence.py:24-63)
    participant LoadMany as tools.sequence._load_many<br/>(sequence.py:176-416)
    participant ReqModel as SequenceRequest<br/>(models/request.py:38-124)
    participant PlanUtils as plan_utils<br/>(plan_utils.py:65-309)
    participant Variants as sequence.variants<br/>(variants.py:23-114)
    participant EqTools as tools.equipment / metadata<br/>(equipment.py, metadata.py)
    participant Progress as progress<br/>(progress.py:478-728)
    participant Targets as sequence.targets & builders<br/>(targets.py, builders.py, nodes.py)
    participant Client as client<br/>(client.py:118-252)
    participant NINA as N.I.N.A. REST API<br/>(/v2/api)

    Caller->>Tool: load_sequence(request) (lines 24-28)
    alt not isinstance(request, list) (lines 49-53)
        Tool-->>Caller: raise ValueError("`request` must be a list...")
    end
    loop for i, item in enumerate(request) (lines 55-62)
        alt not isinstance(item, SequenceRequest)
            Tool->>ReqModel: SequenceRequest.model_validate(item) (line 60)
            ReqModel->>ReqModel: StrictModel (extra="forbid") + _check_variant() (lines 100-124)
            alt ValidationError
                ReqModel-->>Tool: raise ValidationError
                Tool-->>Caller: raise ValueError(f"request[{i}]: not a valid SequenceRequest: {e}") (line 62)
            end
        end
    end

    Tool->>LoadMany: await _load_many(requests) (line 63)
    Note over LoadMany: Initialize lazy per-call memoization dict state = {}<br/>Closures: profile(), equipment(), rows(), now() (lines 189-218)

    alt Path A: Empty Request List (requests == []) — Standalone Park / Teardown
        Note over LoadMany: Loop over enumerate(requests) (line 364) does not execute;<br/>payloads = [], lines = []
        Note over LoadMany: Check `if any(payloads) or not requests:` -> True (line 380)
        LoadMany->>EqTools: eq = await equipment() (line 388 -> profile() -> _get_site_equipment_status)
        EqTools->>Client: GET /profile/show?active=true, /profile/horizon, /equipment/info
        Client->>NINA: HTTP GETs
        NINA-->>EqTools: Profile & Equipment JSON
        LoadMany->>PlanUtils: require_parkable(eq) (line 389; plan_utils.py:152-175)
        alt not (eq.mount and eq.mount.can_park)
            PlanUtils-->>Caller: raise ValueError("Mount cannot park (can_park=False)...")
        end
        Note over LoadMany: Sequencer+ check skipped (requests is empty, lines 394-398)
        LoadMany->>Targets: build_sequence_many(eq, []) (line 400; targets.py:85-102)
        Targets->>Targets: _sequence_with_steps(eq, steps=[], step_count=0) (builders.py:474-494)
        Targets->>Targets: container_root(...) -> _add_refs -> _fix_provider (nodes.py:17-84, 200-214)
        Targets-->>LoadMany: Teardown SequenceRootContainer JSON
        LoadMany->>Client: await load_sequence_payload(seq) (line 400; client.py:245-252)
        Client->>NINA: POST /sequence/load
        NINA-->>Client: HTTP 200 {"Success": true}
        LoadMany-->>Caller: "`park` sequence loaded (empty request list) — call start_sequence() to begin." (lines 407-410)

    else Path B: Non-Empty Request List (requests = [req_0, ...])
        loop for i, request in enumerate(requests) (lines 364-375)
            Note over LoadMany: Execute await step(request) inside try...except (ValueError, OSError)<br/>Prefix any raised error with `f"request[{i}]: {e}"` (lines 365-372)
            LoadMany->>PlanUtils: plan = await load_plan(request.plan) (line 235; plan_utils.py:258-287)
            Note over LoadMany: Verify pointing_index is None for dark/bias/flat (lines 240-246)
            LoadMany->>EqTools: site_profile = await profile() (line 251, memoized in state["profile"])
            LoadMany->>PlanUtils: await validate_filters(plan, site_profile) (line 252; plan_utils.py:65-90)
            LoadMany->>PlanUtils: validate_position_angle(plan, site_profile) (line 253; plan_utils.py:93-136)
            opt action == "light" (lines 254-255)
                LoadMany->>PlanUtils: check_pointing_index(plan, pointing_index) (plan_utils.py:139-149)
            end
            LoadMany->>EqTools: eq = await equipment() (line 256, memoized in state["equipment"])
            opt action in ("dark", "bias") (lines 264-265)
                LoadMany->>Variants: validate_dark_variant(request.variant or "wheel", eq, site_profile) (variants.py:89-114)
            end
            opt action == "light" (lines 273-274)
                LoadMany->>PlanUtils: require_light_capabilities(site_profile) (plan_utils.py:178-220)
            end

            alt request.mode == "remaining" (lines 276-314)
                LoadMany->>EqTools: await rows() (line 279, memoized in state["rows"])
                LoadMany->>Progress: plan_progress(plan, rows, pointing_index, **quality_kwargs(plan), now) (lines 277-283)
                alt all(item["remaining_count"] == 0 for item in items) (line 285)
                    LoadMany->>Progress: verdicts = [group_verdict(item) for item in items] (line 291)
                    alt "expired_with_deficit" in verdicts (lines 292-306)
                        Note over LoadMany: payload = [], note = "...calibration window EXPIRED..."
                    else All groups "complete" (lines 307-310)
                        Note over LoadMany: payload = [], note = "...is complete — N of N frames..."
                    end
                else Frames remain (lines 311-314)
                    LoadMany->>Progress: plan = plan_with_remaining(plan, progress) (line 314; progress.py:690-728)
                end
            else request.mode == "full" (lines 315-326)
                LoadMany->>LoadMany: note = f"{total} frame(s), mode=full" + await _full_mode_note(...) (lines 324-326)
            end

            opt Non-empty step continues to compilation (lines 332-360)
                opt action == "light" (lines 332-338)
                    Note over LoadMany: Verify "$$TARGETNAME$$" in site_profile.file_pattern
                end
                LoadMany->>Targets: target_payload_lights / target_payload_darks / target_payload_flats (lines 340-359)
                Targets-->>LoadMany: payload (1-element list [DeepSkyObjectContainer])
            end
            Note over LoadMany: Append payload to payloads, f"request[{i}]: {note}" to lines (lines 373-374)
        end

        alt any(payloads) is True (lines 380-405)
            LoadMany->>EqTools: eq = await equipment() (line 388)
            LoadMany->>PlanUtils: require_parkable(eq) (line 389; plan_utils.py:152-175)
            opt Any non-empty payload has action in ("light", "flat") (lines 394-398)
                LoadMany->>LoadMany: await _require_sequencer_plus() (lines 126-173)
                LoadMany->>Client: GET /application/plugins (line 145)
                Client->>NINA: GET /application/plugins
                NINA-->>LoadMany: Plugin list (verify "Sequencer+" or "Sequencer Plus")
            end
            LoadMany->>Targets: build_sequence_many(eq, payloads) (line 400; targets.py:85-102)
            Targets->>Targets: _sequence_with_steps(eq, steps, step_count) -> container_root -> _add_refs -> _fix_provider
            Targets-->>LoadMany: Compiled SequenceRootContainer JSON
            LoadMany->>Client: await load_sequence_payload(seq) (line 400)
            Client->>NINA: POST /sequence/load
            NINA-->>Client: HTTP 200 {"Success": true}
            LoadMany-->>Caller: Per-request lines + "Sequence loaded (K of N step(s)) — call start_sequence() to begin."
        else All payloads are [] (lines 411-415)
            LoadMany-->>Caller: Per-request lines + "Nothing to load — every step is already complete; no sequence was sent to N.I.N.A."
        end
    end
```

---

### 1.2 Detailed Walkthrough of `load_sequence` & `_load_many`

#### Entrypoint & Input Normalization (`tools/sequence.py:24-63`)
1. **List Type Check (`lines 49-53`):** `load_sequence(request: list[SequenceRequest] = [])` verifies `isinstance(request, list)`. Passing a single dict or non-list raises `ValueError` immediately.
2. **Per-Item Schema & Variant Validation (`lines 54-62`):** Iterates `for i, item in enumerate(request):`. Each item not already a `SequenceRequest` is validated through `SequenceRequest.model_validate(item)` (`models/request.py:38-124`):
   - **Strict Extra-Field Refusal (`models/strict.py:6-16`):** `SequenceRequest` inherits from `StrictModel` (`ConfigDict(extra="forbid")`), rejecting unknown fields (such as passing `max_hfr` on the request instead of in the plan's `quality` block).
   - **Action-Scoped Variant Validation (`SequenceRequest._check_variant`, `models/request.py:100-124`):**
     - `action="light"` permits no variant (`VARIANTS["light"] == frozenset()`).
     - `action="flat"` **requires** `variant` to be `"dawn"` or `"dusk"` (`variant is None` raises `ValueError`).
     - `action="dark"` and `action="bias"` accept `None` (defaulting downstream to `"wheel"`) or `"wheel"`, `"cover"`, `"shutter"` (`DARK_VARIANTS`).
   - Any `ValueError` / `ValidationError` is re-raised as `ValueError(f"request[{i}]: not a valid SequenceRequest: {e}")`.
3. **Delegation (`line 63`):** Calls `return await _load_many(requests)`.

#### Per-Call Lazy Memoization (`tools/sequence.py:189-218`)
Inside `_load_many(requests)`, a local dictionary `state: dict[str, Any] = {}` backs four async closures so external resources are fetched at most once across all entries in `requests`:
- `profile()` (`lines 191-194`): Fetches `ObservatoryProfile` via `await equipment_tools.get_site_profile()`.
- `equipment()` (`lines 196-200`): Fetches `ObservatoryEquipment` via `await equipment_tools._get_site_equipment_status(await profile())`.
- `rows()` (`lines 202-212`): Reads `ImageMetaData.csv` via `await metadata.read_metadata()`, converting `RuntimeError` or `FileNotFoundError` into a `ValueError` advising the caller to pass `mode='full'` if the imaging root is unavailable.
- `now()` (`lines 214-218`): Captures `datetime.now(UTC).replace(microsecond=0)` once so every step in the sequence evaluates calibration windows against the same UTC timestamp.

---

#### Path A: Empty Request (`request=[]`) — Standalone Park / Teardown Sequence
When `load_sequence()` or `load_sequence([])` is called:
1. The per-request `for i, request in enumerate(requests):` loop (`line 364`) does not execute (`payloads = []`, `lines = []`).
2. The condition `if any(payloads) or not requests:` (`line 380`) evaluates to `True` because `not requests` is `True`.
3. **Park Capability Gate (`lines 388-389`):** Fetches `eq = await equipment()` and calls `plan_utils.require_parkable(eq)` (`plan_utils.py:152-175`), which verifies `bool(eq.mount and eq.mount.can_park)`. If `False`, raises `ValueError("Mount cannot park (can_park=False) — nothing to park...")` before sending anything to N.I.N.A.
4. **Plugin Check Skipped (`lines 394-398`):** Because `requests` is empty, `_require_sequencer_plus()` is not called.
5. **Teardown AST Assembly (`line 400`):** Calls `build_sequence_many(eq, [])` (`sequence/targets.py:85-102`):
   - Computes `steps = []` and `step_count = 0` (`targets.py:100-101`).
   - Calls `_sequence_with_steps(eq, steps=[], step_count=0)` (`sequence/builders.py:474-494`), which constructs:
     - `StartAreaContainer` containing only `[connect_all_equipment()]` (no `RUN-STARTING` event when `step_count == 0`).
     - `TargetAreaContainer` with `Items: []`.
     - `EndAreaContainer` via `sequence_end(eq, step_count=0)` (`builders.py:426-471`), which omits `RUN-FINISHED`, runs the 1-pass `"On Safe"` 3-minute pre-park delay (`PREPARK-DELAY: 3 min` + `WaitForTimeSpan(180)`), emits `PARK-STARTING`, `ConnectAllEquipment`, `ParkScope`, conditional `WarmCamera`, and `PARK-FINISHED`.
   - Wraps the three areas in `container_root(...)` (`sequence/nodes.py:200-214`), running `_add_refs` and `_fix_provider`.
6. **POST to N.I.N.A. (`line 400`):** Calls `await client.load_sequence_payload(...)` (`client.py:245-252`), which sends `POST /sequence/load` and returns `"`park` sequence loaded (empty request list) — call start_sequence() to begin."` (`lines 407-410`).

---

#### Path B: Non-Empty Request List (`request=[...]`)

For each entry `(i, request)` in `enumerate(requests)` (`lines 364-375`), `_load_many` runs `await step(request)` (`lines 220-360`) inside `try ... except (ValueError, OSError) as e:`, prefixing any failure with `f"request[{i}]: {e}"`:

1. **Plan Loading & Pointing Scope (`lines 230-250`):**
   - Requires `request.plan is not None` (`lines 230-234`).
   - Loads and validates the plan JSON via `plan = await plan_utils.load_plan(request.plan)` (`plan_utils.py:258-287`), which also refuses unexpanded mosaic plans (`if not plan.pointings:`).
   - Rejects `request.pointing_index is not None` when `action != "light"` (`lines 240-246`), because calibration (`dark`, `bias`, `flat`) is plan-level. Defaults `pointing_index` to `1` when omitted (`lines 247-249`).

2. **Plan-vs-Profile Validation (`lines 251-256`):**
   - Fetches `site_profile = await profile()` (`line 251`).
   - `await plan_utils.validate_filters(plan, site_profile)` (`line 252`, `plan_utils.py:65-90`): Verifies every filter in `plan.light_frames`, `plan.flat_frames`, and `plan.autofocus.reference_filter_name` exists in `site_profile.filters`.
   - `plan_utils.validate_position_angle(plan, site_profile)` (`line 253`, `plan_utils.py:93-136`): Enforces all-or-none `position_angle_deg` across pointings and requires `site_profile.equipment.has_rotator` if any pointing has `p.requests_rotation() == True` (nonzero angle).
   - If `action == "light"`, calls `plan_utils.check_pointing_index(plan, pointing_index)` (`lines 254-255`, `plan_utils.py:139-149`) to verify `1 <= pointing_index <= len(plan.pointings)`.
   - Fetches `eq = await equipment()` (`line 256`).

3. **Hardware Capability Gates (`lines 264-274`):**
   - **Dark/Bias Variant Capability (`lines 264-265`):** If `action in ("dark", "bias")`, calls `validate_dark_variant(request.variant or "wheel", eq, site_profile)` (`sequence/variants.py:89-114`), which evaluates `dark_variant_reasons(eq, site_profile)` (`variants.py:23-86`) fail-closed:
     - `"wheel"`: Requires `profile.equipment.has_filter_wheel` and a `"Dark"` filter in `profile.filters`.
     - `"cover"`: Requires `profile.equipment.has_flat_device`, a connected `eq.flat_device`, `supports_open_close is True`, and `cover_state not in ("NotPresent", "Error")`.
     - `"shutter"`: Requires `eq.camera` present and `eq.camera.has_shutter is True`.
     - Runs *before* reading `rows()` so an unsupported variant fails even when 0 frames remain or the imaging root is offline.
   - **Light Hardware Capability (`lines 273-274`):** If `action == "light"`, calls `plan_utils.require_light_capabilities(site_profile)` (`plan_utils.py:178-220`), requiring at least one plate solver (`plate_solver` or `blind_plate_solver`), `equipment.has_guider`, and `equipment.has_focuser`.

4. **Frame Pruning: `mode="remaining"` vs. `mode="full"` (`lines 276-330`):**
   - **`mode == "remaining"` (`lines 276-314`):**
     1. Calls `progress = plan_progress(plan, await rows(), pointing_index=pointing_index, **plan_utils.quality_kwargs(plan), now=await now())` (`lines 277-283`, `progress.py:478-631`) and extracts `items = progress[group]` (`line 284`).
     2. If `all(item["remaining_count"] == 0 for item in items)` (`line 285`):
        - Evaluates `verdicts = [group_verdict(item) for item in items]` (`line 291`, `progress.py:634-660`).
        - If `"expired_with_deficit" in verdicts` (`lines 292-306`), sums `deficit_count` across expired groups and returns `[], f"{label}{where} — calibration window EXPIRED with {expired_deficit} frame(s) short of {total} total. Nothing to load..."`.
        - Otherwise (`lines 307-310`), returns `[], f"{label}{where} is complete — {total} of {total} frames already acquired; nothing to load."`.
     3. If frames remain (`lines 311-314`), sets `note = f"{sum(item['remaining_count'] for item in items)} frame(s) to acquire"` and rewrites the plan via `plan = plan_with_remaining(plan, progress)` (`progress.py:690-728`), which drops completed groups, shrinks remaining groups' `total_count` to `remaining_count`, and stamps `plan._base_plan_id()` onto `plan.plan_id` if unpinned so content-hashed attribution tokens never shift across runs.
   - **`mode == "full"` (`lines 315-326`):**
     1. Sums `total = sum(f.total_count for f in frames)` across the full plan group (`lines 316-323`).
     2. Appends `await _full_mode_note(plan, group, pointing_index, total, await now())` (`lines 66-113`, `324-326`), which best-effort checks `metadata.read_metadata("light")` for `group == "light"` and adds a `" ⚠️ already complete — mode=full re-acquires all ..."` warning if all light groups already have `remaining_count == 0`.
   - **Unsupported `mode` (`lines 327-330`):** Raises `ValueError(f"Unsupported mode: {request.mode} (use 'remaining' or 'full')")`.

5. **`$$TARGETNAME$$` Attribution Gate (`lines 332-338`):**
   - For `action == "light"`, verifies `"$$TARGETNAME$$" in (site_profile.file_pattern or "")`; otherwise raises `ValueError("N.I.N.A. FilePattern must contain $$TARGETNAME$$ for light-frame plan attribution.")`.

6. **Per-Step Payload Compilation (`lines 340-360`):**
   - `action == "light"`: `target_payload_lights(plan, eq, site_profile, pointing_index=pointing_index)` (`sequence/targets.py:264-415`).
   - `action == "bias"`: `target_payload_darks(plan, eq, site_profile, bias=True, variant=request.variant or "wheel")` (`sequence/targets.py:108-190`).
   - `action == "dark"`: `target_payload_darks(plan, eq, site_profile, variant=request.variant or "wheel")` (`sequence/targets.py:108-190`).
   - `action == "flat"`: `target_payload_flats(plan, eq, site_profile, dusk=request.variant == "dusk")` (`sequence/targets.py:196-258`).

7. **Late Guards, Root AST Assembly & POST (`lines 380-416`):**
   - If every step in `requests` returned `[]` (all complete or expired in `remaining` mode), `any(payloads) or not requests` is `False` (`line 380`): `_load_many` skips `require_parkable`, `_require_sequencer_plus`, and `load_sequence_payload`, returning `"Nothing to load — every step is already complete; no sequence was sent to N.I.N.A."` (`lines 411-416`).
   - When at least one step produced a non-empty payload:
     1. Calls `plan_utils.require_parkable(eq)` (`lines 388-389`).
     2. If any non-empty payload has `request.action in ("light", "flat")` (`lines 394-397`), awaits `await _require_sequencer_plus()` (`line 398`, `sequence.py:126-173`), which checks `GET /application/plugins` for `"Sequencer+"` or `"Sequencer Plus"` (`SEQUENCER_PLUS_PLUGIN_NAMES`).
     3. Assembles the root sequence via `build_sequence_many(eq, payloads)` (`targets.py:85-102`) -> `_sequence_with_steps(eq, steps, step_count)` (`builders.py:474-494`) -> `container_root(...)` (`nodes.py:200-214`) -> `_add_refs` (`nodes.py:39-84`) -> `_fix_provider` (`nodes.py:17-36`).
     4. Sends the compiled payload via `await client.load_sequence_payload(...)` (`line 400`, `POST /sequence/load`) and returns the multi-line summary (`lines 401-405`).

---

## Workflow 2: Inside the Sequence AST (`lights`, `darks`/`bias`, `flats`, and `EndAreaContainer`)

### 2.1 Complete Compiled N.I.N.A. Sequence Tree (`sequence/*.py`)

```mermaid
flowchart TD
    Root["<b>SequenceRootContainer ('Root Sequence')</b><br/>container_root() (nodes.py:200-214)<br/>Post-processed by _add_refs() ($id, Parent ordering)<br/>& _fix_provider() (SelectedProvider $ref dedup)"]

    Root --> StartArea["<b>StartAreaContainer ('Start Sequence')</b><br/>container_start() (builders.py:487-490)"]
    Root --> TargetArea["<b>TargetAreaContainer ('Target Sequence')</b><br/>container_target(steps) (builders.py:491)"]
    Root --> EndArea["<b>EndAreaContainer ('End Sequence')</b><br/>sequence_end(eq, step_count) (builders.py:426-471)"]

    %% START AREA
    subgraph SG_Start ["1. StartAreaContainer (builders.py:487-490)"]
        direction TB
        S1["ConnectAllEquipment<br/>connect_all_equipment()"] --> S2["SendEventInstruction (if step_count > 0):<br/>'RUN-STARTING: &lt;N&gt; step|steps'<br/>run_starting_event(step_count)"]
    end
    StartArea --> S1

    %% TARGET AREA
    TargetArea --> LightDSO["<b>Option A: Light Step</b><br/>DeepSkyObjectContainer ('Deep Sky Target Sequence')<br/>target_payload_lights() (targets.py:264-415)<br/>TargetName: '&lt;prefix&gt; _&lt;plan_id&gt;[-&lt;idx&gt;]_'"]
    TargetArea --> DarkDSO["<b>Option B: Dark / Bias Step</b><br/>DeepSkyObjectContainer ('Dark Sequence' | 'Bias Sequence')<br/>target_payload_darks() (targets.py:108-190)<br/>TargetName: '&lt;cal_prefix&gt; _&lt;plan_id&gt;_'"]
    TargetArea --> FlatDSO["<b>Option C: Dawn / Dusk Flat Step</b><br/>DeepSkyObjectContainer ('Dawn Flats' | 'Dusk Flats')<br/>target_payload_flats() (targets.py:196-258)<br/>TargetName: '&lt;cal_prefix&gt; _&lt;plan_id&gt;_'"]

    %% LIGHT STEP AST
    subgraph SG_Light ["2A. target_payload_lights (sequence/targets.py:264-415)"]
        direction TB
        L_Start["SendEvent: 'ACQUISITION-STARTING: [light] &lt;target&gt;'<br/>+ ConnectAllEquipment (builders.py:93-94)"]
        L_Dusk["SequentialContainer ('Wait For Dusk')<br/>Conditions: [TimeCondition(DuskProvider)]<br/>Items: [ParkScope?, WaitForTime(DuskProvider)]<br/>(builders.py:379-386)"]
        L_WaitObj["SequentialContainer ('Wait For Object') (targets.py:301-330)<br/>Conditions: [TimeCondition(DawnProvider), TimeCondition(MeridianProvider)]"]
        L_Break["Inner SequentialContainer ('Break If in View') (targets.py:309-320)<br/>Conditions: [AboveHorizonCondition(offset), AltitudeCondition(min_alt)]<br/>Items: [EndInstructionSet('Wait For Object')]"]
        L_ParkWait["Fallback Parked Wait (targets.py:323-329):<br/>ParkScope? + WaitUntilAboveHorizon(offset) + WaitForAltitude(min_alt)"]
        L_Prime["Sequencer+ Countdown Priming (targets.py:334-339):<br/>SetVariable('n', '0') + InitializeArray(\"'count'\")<br/>+ PutArray(\"'count'\", idx, str(count)) per _round_robin batch"]
        L_SafeNet["<b>sequence_safetynet ('Image Object')</b> (targets.py:341-412, builders.py:389-423)<br/>Conditions: [TimeCondition(Sunrise), TimeCondition(Dawn),<br/>AboveHorizonCondition(offset), AltitudeCondition(min_alt)]<br/>Triggers: [MeridianFlip, CenterAfterDrift, AutofocusAfterFilterChange,<br/>AutofocusAfterExposures, AutofocusAfterTempChange, AutofocusAfterHFRIncrease, RestoreGuiding]"]
        L_OnSafe["Inner SequentialContainer ('On Safe') (builders.py:413-417)<br/>Conditions: [SafetyMonitorCondition (loop_while_safe)]<br/>Items:<br/>1. CoolCamera(setpoint) / WarmCamera()<br/>2. UnparkScope?<br/>3. FindHome? (sequence_home_scope, if can_find_home)<br/>4. SetTracking(0) (Sidereal) + SwitchFilter(ref_filter)<br/>5. CenterAndRotate (if requests_rotation()) else Center<br/>6. RunAutofocus + StartGuiding<br/>7. Round-Robin Batches (sequence_count_exposure, builders.py:290-338):<br/>   GetArray(\"'count'\", idx, 'n') + SequentialContainer ('Exposure Group')<br/>   [Conditions: LoopCondition(count), LoopWhile('n > 0'); Trigger: DitherAfterExposures?]<br/>   [Items: SwitchFilter + TakeExposure('LIGHT') + PutArray(\"'count'\", idx, 'n-1') + GetArray(\"'count'\", idx, 'n')]<br/>8. StopGuiding<br/>9. EndInstructionSet('Image Object')"]
        L_OnUnsafe["On Unsafe Fallback (builders.py:419-422):<br/>ParkScope? + WaitUntilSafe"]
        L_Fin["SendEvent: 'ACQUISITION-FINISHED: [light] &lt;target&gt;' (builders.py:96)"]

        L_Start --> L_Dusk --> L_WaitObj
        L_WaitObj --> L_Break --> L_ParkWait --> L_Prime --> L_SafeNet
        L_SafeNet --> L_OnSafe --> L_OnUnsafe --> L_Fin
    end
    LightDSO --> L_Start

    %% DARK / BIAS STEP AST
    subgraph SG_Dark ["2B. target_payload_darks (sequence/targets.py:108-190)"]
        direction TB
        D_Start["SendEvent: 'ACQUISITION-STARTING: [dark|bias] &lt;target&gt;'<br/>+ ConnectAllEquipment (builders.py:93-94)"]
        D_Base["1. ParkScope? (sequence_park_scope)<br/>2. CoolCamera(setpoint) / WarmCamera() (sequence_cool_camera)<br/>(targets.py:163)"]
        D_CoverClose["Optional CloseCover (only when variant == 'cover') (targets.py:165)"]
        D_Exp["Per-Entry SequentialContainer ('Exposure Group') (targets.py:145-162):<br/>SmartExposure (Conditions: [LoopCondition(total_count)], Triggers: [DitherAfterExposures(0)])<br/>Items: [SwitchFilter('Dark' if wheel else None), TakeExposure(exp or 0, 'DARK'|'BIAS')]"]
        D_CoverOpen["Optional OpenCover (only when variant == 'cover') (targets.py:165)"]
        D_Assert["Compile-Time Invariant Assertion (targets.py:175-180):<br/>NO UnparkScope, NO Platesolving, NO StartGuiding<br/>(Never unparks, never slews, never guides; WarmCamera deferred to EndAreaContainer)"]
        D_Fin["SendEvent: 'ACQUISITION-FINISHED: [dark|bias] &lt;target&gt;' (builders.py:96)"]

        D_Start --> D_Base --> D_CoverClose --> D_Exp --> D_CoverOpen --> D_Assert --> D_Fin
    end
    DarkDSO --> D_Start

    %% FLAT STEP AST
    subgraph SG_Flat ["2C. target_payload_flats (sequence/targets.py:196-258)"]
        direction TB
        F_Start["SendEvent: 'ACQUISITION-STARTING: [dawn flats|dusk flats] &lt;target&gt;'<br/>+ ConnectAllEquipment (builders.py:93-94)"]
        F_Waits["Twilight Wait Containers (targets.py:207-214):<br/>• Dusk: [Wait For Sunset (loop_until_sunset: ParkScope? + WaitForTime(Sunset) + WaitForSunAltitude(&gt; 0°))]<br/>• Dawn: [Wait For Sunset, Wait For Dawn (loop_until_dawn: ParkScope? + WaitForTime(Dawn) + WaitForSunAltitude(&lt; -8°))]"]
        F_SafeNet["<b>sequence_safetynet ('Image Flats')</b> (targets.py:229-255)<br/>Conditions: [TimeCondition(Sunrise),<br/>SunAltitudeCondition(&lt; -8° for dusk | &gt; 0° for dawn)]"]
        F_OnSafe["Inner SequentialContainer ('On Safe') (builders.py:413-417)<br/>Conditions: [SafetyMonitorCondition (loop_while_safe)]<br/>Items:<br/>1. CoolCamera(setpoint) / WarmCamera()<br/>2. UnparkScope?<br/>3. SlewScopeToAltAz (Az=90° dusk / 270° dawn, Alt=80°)<br/>   (Note: N.I.N.A. SkyFlat / SlewScopeToAltAz operates with tracking stopped;<br/>   set_tracking(4) is available in instructions.py:66-79)<br/>4. Per-filter SkyFlat ('Twilight Sky Flats', reversed for dusk):<br/>   [Annotation, Annotation, SwitchFilter, Annotation,<br/>   SequentialContainer(LoopCondition(count), TakeExposure(0.0, 'FLAT'))]<br/>5. EndInstructionSet('Image Flats')"]
        F_OnUnsafe["On Unsafe Fallback: ParkScope? + WaitUntilSafe"]
        F_Fin["SendEvent: 'ACQUISITION-FINISHED: [dawn flats|dusk flats] &lt;target&gt;'"]

        F_Start --> F_Waits --> F_SafeNet --> F_OnSafe --> F_OnUnsafe --> F_Fin
    end
    FlatDSO --> F_Start

    %% END AREA
    subgraph SG_End ["3. EndAreaContainer / sequence_end (sequence/builders.py:426-471)"]
        direction TB
        E_Container["SequentialContainer ('Park Telescope') (builders.py:449-469)"]
        E_RunFin["1. SendEvent (if step_count > 0):<br/>'RUN-FINISHED: &lt;N&gt; step|steps complete'<br/>run_finished_event(step_count) (builders.py:446)"]
        E_PrePark["2. SequentialContainer ('On Safe') (builders.py:454-461)<br/>Conditions: [SafetyMonitorCondition (loop_while_safe), LoopCondition(Iterations=1)]<br/>Items:<br/>• SendEvent('PREPARK-DELAY: 3 min')<br/>• WaitForTimeSpan(180)<br/>(Logs PREPARK-WAITING in nina-notify; skipped immediately if is_safe=False)"]
        E_ParkStart["3. SendEvent('PARK-STARTING') (builders.py:462)"]
        E_Connect["4. ConnectAllEquipment (builders.py:463)"]
        E_Park["5. ParkScope? (sequence_park_scope, builders.py:465)"]
        E_Warm["6. WarmCamera? (sequence_warm_camera, if has_cooler, builders.py:466)"]
        E_ParkFin["7. SendEvent('PARK-FINISHED') (builders.py:467)"]

        E_Container --> E_RunFin --> E_PrePark --> E_ParkStart --> E_Connect --> E_Park --> E_Warm --> E_ParkFin
    end
    EndArea --> E_Container
```

---

### 2.2 Detailed Breakdown of Each AST Area

#### 1. `StartAreaContainer` (`sequence/builders.py:487-490`)
Built by `_sequence_with_steps(equipment, steps, step_count)`:
- Always starts with `connect_all_equipment()` (`NINA.Sequencer.SequenceItem.Connect.ConnectAllEquipment, NINA.Sequencer`).
- When `step_count > 0`, emits `run_starting_event(step_count)` (`SendEventInstruction` with `"Message": f"RUN-STARTING: {_step_word(step_count)}"`, i.e., `"RUN-STARTING: 1 step"` or `"RUN-STARTING: N steps"`). When `step_count == 0` (the standalone park sequence), `RUN-STARTING` is omitted so a park never looks like an imaging run starting.

#### 2. `TargetAreaContainer` (`sequence/targets.py` & `sequence/builders.py`)
Every Target-area step is wrapped in a `DeepSkyObjectContainer` via `acquisition_container` (`builders.py:57-98`), which brackets the step's inner instructions with:
- `send_event("ACQUISITION-STARTING: [<tag>] <target>")`
- `connect_all_equipment()` (making every step self-connecting even if started mid-sequence)
- `*instructions`
- `send_event("ACQUISITION-FINISHED: [<tag>] <target>")`

##### A. Lights Step (`target_payload_lights`, `sequence/targets.py:264-415`)
Wrapped by `sequence_deepsky(plan, instructions, pointing_index)` (`builders.py:121-145`) with `name="Deep Sky Target Sequence"`, `tag="light"`, and `Target.TargetName = f"{plan.target_prefix(pointing.label)} {plan.attribution_token(pointing_index)}"`.
1. **Dusk Wait (`line 299`):** `sequence_wait_until_dusk(equipment)` (`builders.py:379-386`) — `SequentialContainer("Wait For Dusk")` bounded by `loop_until_dusk()`, running `sequence_park_scope(equipment) + [wait_until_dusk()]`.
2. **Object Visibility Wait (`lines 301-330`):** `SequentialContainer("Wait For Object")` bounded by `[loop_until_dawn(), loop_until_meridian()]`:
   - **Immediate Breakout (`"Break If in View"`, `lines 309-320`):** Inner `SequentialContainer` with conditions `[loop_while_above_horizon(horizon_offset_degrees), loop_while_above_altitude(min_altitude)]` and instruction `[end_instruction("Wait For Object")]`. If the target is already in view, `EndInstructionSet` exits `"Wait For Object"` immediately without parking.
   - **Parked Wait (`lines 323-329`):** If not yet in view, runs `sequence_park_scope(equipment)` followed by `wait_until_above_horizon(horizon_offset_degrees)` and `wait_until_above_altitude(min_altitude)`.
3. **Sequencer+ Countdown Array Initialization (`lines 291, 334-339`):**
   - `_round_robin(plan.light_frames, plan.batch_size)` (`nodes.py:134-160`) interleaves filter exposures into `(count, exposure, filter_name)` batches.
   - Outside the safetynet, emits `declare_variable("n", "0")`, `initialize_array("'count'")`, and `put_array("'count'", index, str(count))` for each batch `index`.
4. **`"Image Object"` SafetyNet (`lines 341-412`, `builders.py:389-423`):**
   - **Outer Conditions:** `[loop_until_sunrise(), loop_until_dawn(), loop_while_above_horizon(horizon_offset_degrees), loop_while_above_altitude(min_altitude)]`.
   - **Outer Triggers (`lines 351-369`):** `trigger_meridian_flip()`, `trigger_center_after_drift(...)`, `trigger_autofocus_after_filter_change()`, `trigger_autofocus_after_exposures(...)`, `trigger_autofocus_after_temperature_change(...)`, `trigger_autofocus_after_hfr_increase(...)`, and `trigger_restore_guiding()`.
   - **Inner `"On Safe"` Container (`builders.py:413-417`, `targets.py:370-411`):** Gated by `loop_while_safe()`:
     1. `sequence_cool_camera(plan, equipment)` (`CoolCamera` or `WarmCamera` if `has_cooler`).
     2. `sequence_unpark_scope(equipment)` (`UnparkScope` if `can_park`).
     3. `sequence_home_scope(equipment)` (`FindHome` if `can_find_home` — always after unpark and before `set_tracking(0)`).
     4. `set_tracking(0)` (ASCOM mode `0` = Sidereal) and `switch_filter(reference_filter_name, reference_filter_position)`.
     5. `slew_center_and_rotate()` if `pointing.requests_rotation()` else `slew_and_center()`.
     6. `run_autofocus()` and `start_guiding()`.
     7. Round-robin `sequence_count_exposure` batches (`builders.py:290-338`): for each `index`, emits `get_array("'count'", index, "n")` followed by `SequentialContainer("Exposure Group")` with conditions `[loop_for_iterations(count), loop_while_expression("n > 0")]`, optional `dither_after_exposures` trigger, and instructions `[switch_filter, take_exposure(exposure, "LIGHT"), put_array("'count'", index, "n-1"), get_array("'count'", index, "n")]`. If `"Image Object"` is interrupted by a safety drop and later resumes, `'count'[index]` preserves the exact remaining frame count.
     8. `stop_guiding()`.
     9. `end_instruction("Image Object")` (appended by `sequence_safetynet` at `builders.py:416` to exit the outer safetynet when `"On Safe"` finishes).
   - **On Unsafe Fallback (`builders.py:419-422`):** When `is_safe=False` aborts `"On Safe"`, runs `sequence_park_scope(equipment)` and `wait_until_safe()`, then loops back to `"On Safe"` once safe again (unless sunrise/dawn/horizon conditions have expired).

##### B. Darks & Bias Step (`target_payload_darks`, `sequence/targets.py:108-190`)
Wrapped by `sequence_calibration(plan, instructions, kind="Bias Sequence" | "Dark Sequence", tag="bias" | "dark")` (`builders.py:148-187`) with plan-level `Target.TargetName = f"{plan.calibration_prefix()} {plan.plan_token()}"`:
1. **Park & Thermal Setup (`line 163`):** Emits `sequence_park_scope(equipment) + sequence_cool_camera(plan, equipment)`.
2. **Light-Cutting Mechanism (`lines 143-169`):**
   - `variant == "wheel"` (default): Passes `filter_name="Dark"` and `filter_position=profile.filter_position("Dark")` into `smart_exposure`.
   - `variant == "cover"`: Wraps the exposure groups with `[close_cover()] + exposures + [open_cover()]` (`lines 164-165`), leaving `filter_name=None` (`SwitchFilter.Filter = null`, which N.I.N.A. marks `SKIPPED`). Reopening the cover afterward ensures a subsequent sky step in the same sequence never shoots through a closed cover.
   - `variant == "shutter"`: Emits `base + exposures` with `filter_name=None`, relying on the camera's internal mechanical shutter.
3. **`SmartExposure` Groups (`lines 145-162`, `instructions.py:414-450`):** One `SequentialContainer("Exposure Group")` per plan entry (in list order, no round-robin), each containing `smart_exposure(count=f.total_count, exposure=getattr(f, "exposure_time_seconds", 0), image_type="BIAS" | "DARK", filter_name=dark_filter, filter_position=dark_position)`. Note that `smart_exposure` always populates positional `Triggers[0]` (`DitherAfterExposures(0)`) and `Items[0]` (`SwitchFilter`) because N.I.N.A.'s `SmartExposure.Validate()` dereferences them by index.
4. **Sky-Gate Exemption Invariant (`lines 175-180`):** Asserts `not _contains_type(built, needle)` for `"UnparkScope"`, `"Platesolving"`, and `"StartGuiding"`. Darks/biases never unpark, never home, never slew, and never guide, and camera warming (`WarmCamera`) happens at the end of the sequence in `EndAreaContainer` (`sequence_end`) — unless `plan.cooler.on` is `False`, in which case `sequence_cool_camera` emits `warm_camera()`.

##### C. Dawn & Dusk Flats Step (`target_payload_flats`, `sequence/targets.py:196-258`)
Wrapped by `sequence_calibration(plan, instructions, kind="Dusk Flats" | "Dawn Flats", tag="dusk flats" | "dawn flats")`:
1. **Twilight Waits (`lines 207-214`):**
   - **Dusk (`dusk=True`):** `[sequence_wait_until_sunset(equipment)]` (`builders.py:341-358`), which parks the mount and waits until sunset and solar altitude $\le 0°$ (`wait_if_sun_altitude_above(0)`).
   - **Dawn (`dusk=False`):** `[sequence_wait_until_sunset(equipment), sequence_wait_until_dawn(equipment)]` (`builders.py:361-376`), waiting past sunset (if started during the day) and then waiting for astronomical dawn and solar altitude $\ge -8°$ (`wait_if_sun_altitude_below(-8)`).
2. **Filter Ordering (`line 219`):** Dawn shoots `plan.flat_frames` in plan order (broadband first while dim, narrowband last as twilight brightens); dusk reverses the list (`list(reversed(plan.flat_frames))`) so narrowband shoots first while twilight is bright and broadband shoots last as twilight fades.
3. **`"Image Flats"` SafetyNet (`lines 229-255`):**
   - **Outer Conditions:** `loop_until_sunrise()` plus `loop_until_sun_altitude_below(-8)` (for dusk) or `loop_until_sun_altitude_above(0)` (for dawn).
   - **Inner `"On Safe"` Instructions (`lines 237-254`):**
     1. `sequence_cool_camera(plan, equipment)`.
     2. `sequence_unpark_scope(equipment)`.
     3. `slew_to_azalt(az=90.0 if dusk else 270.0, alt=80.0)` (`NINA_FLATS_AZIMUTH_DUSK` / `NINA_FLATS_AZIMUTH_DAWN`, `NINA_FLATS_ALTITUDE` in `nodes.py:12-14`). *(Note: `instructions.py:66-79` defines `set_tracking(4)` for ASCOM Stopped mode, while `target_payload_flats` slews directly to Alt/Az where N.I.N.A.'s `SlewScopeToAltAz` and `SkyFlat` manage flat-field slewing and exposure).*
     4. One `sky_flats(count=f.total_count, filter_name=f.filter_name, filter_position=profile.filter_position(f.filter_name))` container per filter (`instructions.py:154-182`), which dynamically varies exposure between `0.0s` and `30.0s` to hit a `50% ± 10%` histogram target.

#### 3. `EndAreaContainer` (`sequence_end`, `sequence/builders.py:426-471`)
Every sequence concludes with `sequence_end(equipment, step_count=step_count)`, which wraps a single `SequentialContainer(name="Park Telescope")`:
1. **`RUN-FINISHED` Milestone (`line 446`):** If `step_count > 0`, emits `run_finished_event(step_count)` (`SendEventInstruction(f"RUN-FINISHED: {_step_word(step_count)} complete")`, i.e., `"RUN-FINISHED: 1 step complete"` or `"RUN-FINISHED: N steps complete"`). Firing this at the very start of the End area — before the 3-minute delay and before parking — wakes the `night-loop` controller while the telescope is still unparked and the camera is still cold, allowing the next target sequence to be loaded seamlessly. When `step_count == 0` (the standalone park sequence), `RUN-FINISHED` is omitted.
2. **Bounded 3-Minute `"On Safe"` Pre-Park Wait (`lines 454-461`):**
   - `SequentialContainer(name="On Safe")` with conditions `[loop_while_safe(), loop_for_iterations(1)]`.
   - Instructions: `[send_event("PREPARK-DELAY: 3 min"), wait_for_timespan(180)]` (which the `nina-notify` plugin surfaces as `PREPARK-WAITING`).
   - If the enclosure is already closed (`is_safe=False`), `loop_while_safe()` is false and the 3-minute wait is skipped immediately. If safe, `loop_for_iterations(1)` ensures it runs at most once.
3. **Park & Warm Teardown (`lines 462-467`):**
   - `send_event("PARK-STARTING")` (`line 462`)
   - `connect_all_equipment()` (`line 463`, ensuring a standalone park sequence connects equipment before parking)
   - `sequence_park_scope(equipment)` (`line 465`, `[park_scope()]` when `can_park=True`)
   - `sequence_warm_camera(equipment)` (`line 466`, `[warm_camera()]` when `camera.thermal.has_cooler=True`)
   - `send_event("PARK-FINISHED")` (`line 467`)

#### 4. Root JSON.NET Graph Finalization (`container_root`, `sequence/nodes.py:17-84, 200-214`)
`container_root` wraps `[container_start(...), container_target(steps), sequence_end(...)]` and runs two in-place AST passes required by N.I.N.A.'s JSON.NET deserializer:
1. **`_add_refs(data)` (`nodes.py:39-84`):** Assigns sequential 1-based string `"$id"`s (`"1"`, `"2"`, ...) in pre-order traversal, sets `"Parent"` to `{"$ref": "<enclosing_id>"}` (or `None` at the root), keeps `"Parent"` **before** `"Items"`/`"Conditions"` on containers, and moves `"Parent"` to the **last key** on leaf nodes (`data["Parent"] = data.pop("Parent")`) so JSON.NET populates leaf properties like `SetVariable.Identifier` before running the `Parent` setter.
2. **`_fix_provider(data)` (`nodes.py:17-36`):** Deduplicates `"SelectedProvider"` dicts (`DuskProvider`, `DawnProvider`, `SunsetProvider`, `SunriseProvider`, `MeridianProvider`) across the tree, keeping the first occurrence of each `$type` and replacing subsequent occurrences with `{"$ref": "<first_id>"}`.

---

## Workflow 3: Starting, Stopping & Inspecting Sequences (`start_sequence`, `stop_sequence`, `get_sequence_state`)

### 3.1 `start_sequence` (`tools/sequence.py:426-434` -> `client.py:209-242`)

```mermaid
sequenceDiagram
    autonumber
    actor Caller as MCP Caller
    participant Tool as tools.sequence.start_sequence<br/>(sequence.py:426-434)
    participant Client as client.wait_until_sequence_started<br/>(client.py:188-242)
    participant NINA as N.I.N.A. REST API<br/>(/v2/api)

    Caller->>Tool: start_sequence() (line 427)
    Tool->>Client: await client.api_get(config.SEQUENCE_START_PATH) (line 432)
    Client->>NINA: GET /sequence/start
    NINA-->>Client: HTTP 200 {"Success": true, "Response": ...}
    Note over Tool,Client: N.I.N.A. acknowledges /sequence/start before start-time validation finishes.<br/>Block until at least one node leaves CREATED.
    Tool->>Client: await client.wait_until_sequence_started() (line 433; client.py:209-242)
    Note over Client: budget = SEQUENCE_START_TIMEOUT (10.0s)<br/>deadline = time.monotonic() + budget (lines 227-228)
    loop while True (lines 229-242)
        Client->>NINA: GET /sequence/json (line 230)
        NINA-->>Client: Sequence JSON tree
        Client->>Client: statuses = _node_statuses(payload) (lines 191-206, 230)
        alt any(status != "CREATED" for status in statuses) (lines 231-232)
            Note over Client: At least one node is RUNNING, FINISHED, SKIPPED, or FAILED
            Client-->>Tool: return statuses
            Tool-->>Caller: return "Sequence started." (line 434)
        else All nodes still "CREATED" (or empty)
            alt time.monotonic() >= deadline (lines 233-241)
                Client-->>Caller: raise RuntimeError("NINA sequence did not start within 10s — every node is still CREATED...")
            else Budget remains (line 242)
                Client->>Client: await anyio.sleep(0.2)
            end
        end
    end
```

- **Why `status != "CREATED"` instead of `status == "RUNNING"` (`client.py:218-222`):** A short teardown/park sequence when the mount is already parked can start and finish before the first `GET /sequence/json` poll, leaving behind `"FINISHED"` and `"SKIPPED"` nodes and zero `"RUNNING"` nodes. Checking `any(status != "CREATED" ...)` treats both actively running sequences and already-finished sequences as a confirmed start, while catching silent start-time validation aborts where every node remains `"CREATED"`.

---

### 3.2 `stop_sequence` (`tools/sequence.py:419-423` -> `client.py:166-182`)

```mermaid
sequenceDiagram
    autonumber
    actor Caller as MCP Caller
    participant Tool as tools.sequence.stop_sequence<br/>(sequence.py:419-423)
    participant Client as client.stop_running_sequence_and_wait<br/>(client.py:157-182)
    participant NINA as N.I.N.A. REST API<br/>(/v2/api)

    Caller->>Tool: stop_sequence() (line 420)
    Tool->>Client: await client.stop_running_sequence_and_wait(timeout=15.0) (line 422; client.py:166-182)
    Client->>Client: await _sequence_is_running() (line 174; client.py:162-163)
    Client->>NINA: GET /sequence/json
    NINA-->>Client: Sequence JSON tree
    Client->>Client: _sequence_running(state) -> "RUNNING" in _node_statuses(state) (line 159)
    alt No node has Status == "RUNNING" (lines 174-175)
        Client-->>Tool: return False (already stopped)
        Tool-->>Caller: return "Sequence stopped." (line 423)
    else At least one node is "RUNNING"
        Client->>NINA: GET /sequence/stop (line 176)
        NINA-->>Client: HTTP 200 {"Success": true}
        Note over Client: deadline = time.monotonic() + 15.0 (line 177)
        loop while time.monotonic() < deadline (lines 178-181)
            Client->>NINA: GET /sequence/json (via _sequence_is_running, line 179)
            NINA-->>Client: Sequence JSON tree
            alt not _sequence_running(state) (lines 179-180)
                Client-->>Tool: return True
                Tool-->>Caller: return "Sequence stopped." (line 423)
            else Still unwinding ("RUNNING" present)
                Client->>Client: await anyio.sleep(0.2) (line 181)
            end
        end
        Client-->>Caller: raise RuntimeError("NINA sequence did not stop within 15s") (line 182)
    end
```

- **Why the stop barrier matters (`client.py:169-172`):** N.I.N.A. responds to `GET /sequence/stop` before active instructions finish unwinding, and `POST /sequence/load` refuses to load while any node is still `RUNNING`. Polling `/sequence/json` every `0.2s` until `"RUNNING"` disappears guarantees that a subsequent `load_sequence()` call will not be rejected. Note that `stop_sequence()` leaves the telescope in place without parking; to park afterward, the caller loads `load_sequence([])` and calls `start_sequence()`.

---

### 3.3 `get_sequence_state` (`tools/sequence.py:437-454` -> `sequence_state.py:164-241`)

```mermaid
sequenceDiagram
    autonumber
    actor Caller as MCP Caller
    participant Tool as tools.sequence.get_sequence_state<br/>(sequence.py:437-454)
    participant Client as client.api_get<br/>(client.py:118-134)
    participant NINA as N.I.N.A. REST API<br/>(/v2/api)
    participant Proj as sequence_state.project_sequence_state<br/>(sequence_state.py:164-241)

    Caller->>Tool: get_sequence_state(detail="summary" | "raw") (line 438)
    alt detail not in ("summary", "raw") (lines 445-446)
        Tool-->>Caller: raise ValueError('detail must be "summary" or "raw"')
    end
    Tool->>Client: state = await client.api_get("/sequence/state") (line 447)
    Client->>NINA: GET /sequence/state
    NINA-->>Tool: state payload
    alt detail == "raw" (lines 448-449)
        Tool-->>Caller: return state (unfiltered /sequence/state tree)
    else detail == "summary" (lines 450-454)
        Tool->>Client: try: json_state = await client.api_get("/sequence/json") (lines 450-453)
        Client->>NINA: GET /sequence/json
        alt /sequence/json succeeds
            NINA-->>Tool: json_state payload
        else Exception (best-effort fallback)
            Tool->>Tool: json_state = None (lines 452-453)
        end
        Tool->>Proj: project_sequence_state(state, json_state) (line 454; sequence_state.py:164-241)
        Note over Proj: 1. Locate _target_area(state) ('Target Sequence_Container', lines 28-29, 173)<br/>2. Set parse_error = (area is None and bool(state)) (lines 174-177)<br/>3. Check index pairing: paired = [s.Name in json_steps] == [s.Name in state_steps] (line 185)<br/>4. For each (i, step) in enumerate(state_steps) (lines 187-229):<br/>   • Parse Target.TargetName via ATTRIBUTION_RE -> plan_id, pointing_index (lines 188-189, 213-216)<br/>   • Find 'Take Exposure' -> frame_type (lines 190, 217)<br/>   • Find first RUNNING leaf ('Items' not in n) -> phase (lines 191-198)<br/>   • Partition step _issues(step) -> failed issues (line 199)<br/>   • Collect FINISHED 'Send WebSocket Event' messages -> fired_events (lines 200-206)<br/>   • Discover _exposure_groups(step) ('Smart Exposure_Container' + _counts_exposures)<br/>     and extract _exposure() for primary exposure & exposure_groups (lines 99-150, 207, 224-225)<br/>   • Extract _horizon(json_steps[i]) if paired else None (lines 153-161, 226)<br/>5. Partition global _issues(state) -> (failed_all, preflight_all) (lines 32-47, 230-231)
        Proj-->>Tool: Return {running, status, step_count, completed_steps,<br/>parse_error, issues, preflight_issues, steps}
        Tool-->>Caller: Return summary dict
    end
```

- **Key Projection Invariants in `project_sequence_state` (`sequence_state.py:164-241`):**
  1. **Batched vs. Single Exposure Group Discovery (`_counts_exposures`, `_exposure_groups`, `lines 73-114`):** Identifies both `"Smart Exposure_Container"` (darks/bias) and `"Exposure Group_Container"` (`sequence_count_exposure` batches in lights) by checking that `node["Conditions"]` has an `"Iterations"`/`"CompletedIterations"` dict **and** `"Take Exposure"` is a direct child (`_children(node)`), avoiding false matches on outer loop wrappers.
  2. **Failed vs. Pre-Flight Issue Split (`_issues`, `lines 32-47`):** Only issues on nodes with `Status == "FAILED"` populate `issues` (per-step and top-level); issues on non-`FAILED` nodes (such as `"Focuser not connected"` before `ConnectAllEquipment` runs) are isolated in top-level `preflight_issues`.
  3. **Safe Horizon Pairing (`_horizon`, `lines 153-161, 179-185, 226`):** Because `Wait for Altitude` details (`CurrentAltitude`, `Altitude`, `ExpectedTime`) only appear in `/sequence/json`, `project_sequence_state` verifies that the step `Name` list matches 1-for-1 between `/sequence/json` and `/sequence/state` (`paired`) before merging horizon dicts by index.
