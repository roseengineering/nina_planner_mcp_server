# nina_planner — An Observatory Operator Agent

![Banner for repository](banner.png)

`nina_planner` is an MCP tool server for N.I.N.A. (Nighttime Imaging 'N' Astronomy). It lets you inspect equipment, write observation plans, load sequences, and control the telescope — all through tool calls in your AI client.  The focus is on tools to generate and manage sequences on N.I.N.A. rather than issuing real-time raw Advanced API commands.  This MCP server can generate "plans" — in the style of ACP (DC-3 Dreams) observatory plans, but as JSON — which can then be loaded and run as N.I.N.A. Advanced sequences.

---

## Available Tools

### Equipment & Telemetry

| Tool | Purpose |
|---|---|
| `get_site_equipment_status()` | Return the current connection, operating state, measurements, and capabilities of active observatory equipment (mount, camera, focuser, guider, filter wheel, rotator, dome, switches), including weather and safety-monitor status (whether the enclosure is open and it is safe to unpark) and mount capabilities (`can_park`, `can_find_home`) that determine how the scope can be stowed. Use for live operational and safety checks. Auto-connects any device that is present but disconnected; if any device is mid-connect the call errors and should be retried after a few seconds. |
| `get_site_profile()` | Return the active NINA observatory profile and static configuration: site location, optics, camera geometry, filters, plate solvers, and image-save path. `image_save_path` is the raw Windows path from the profile; `local_image_save_path` is the derived path usable on this host (drive letter mapped through `NINA_DRIVE_MOUNT`, default `/mnt/<drive>`, on Linux/WSL; unchanged on Windows) and is the one to use for local file access. Use `get_site_equipment_status` for live equipment state. Read-only. |
| `list_site_profiles()` | Return every NINA observatory profile as a list of summaries — id, name, description, and last-used time — marking the active profile with `active: true`. Use it to find the id to pass to `switch_site_profile`. Only the active profile exposes full site/optics/filter detail, so use `get_site_profile` to inspect it. Read-only. |
| `switch_site_profile(profile_id)` | Switch the active NINA profile to the one with the given `profile_id` (a GUID from `list_site_profiles`). The id is passed straight through to NINA; an unknown id or refusal surfaces NINA's own error. Switching disconnects and reconnects equipment and changes site, filters, image-save path, and file pattern, so do not call it while a sequence is running. Confirm with `get_site_profile` afterward. |
| `get_events(since=300)` | Return recent timestamped NINA observatory events and their event-specific details from the last `since` seconds. Use to reconstruct sequence, equipment, safety, imaging, and error activity; use `get_site_equipment_status` and `get_sequence_state` for current status. Read-only. |
| `get_logs(since=300)` | Return recent NINA application log entries (informational messages, warnings, and errors, with timestamps) from the last `since` seconds. Use to diagnose sequence failures, equipment communication problems, and unexpected behavior. Read-only. |
| `screenshot_dashboard()` | Capture the N.I.N.A. dashboard as a PNG and return **two content blocks: the absolute file path as text, then the image itself** (e.g. `/var/folders/.../T/nina_screenshots/dashboard_20261005T120000123456.png`) — the image is written to the system temp dir under `nina_screenshots/`, one uniquely named file per call, nothing overwritten. Clients that render MCP image blocks show the capture inline; otherwise open the returned path with the read tool to view it. The tool is unstructured (no `outputSchema`) because it carries mixed text/image content. Switches NINA to the Imaging tab first so the capture shows the main imaging workspace; that tab switch is the only side effect, no sequence or equipment state changes. |
| `get_imaging_metadata(file_path, pointing_index=1, image_type="light")` | Return per-exposure imaging metadata for one pointing of an observation plan, parsed from `ImageMetaData.csv` and `WeatherData.csv` in the N.I.N.A. imaging directory. `image_type` is `light` (default), `dark`, `bias`, or `flat` (case-insensitive). **Attribution:** lights are attributed via the plan id in the recorded file path (`{base_plan_id}` for a single-pointing plan, `{base_plan_id}-{pointing_index}` per pane for a mosaic; no target-name fallback). Dark/bias/flat frames are matched plan-wide by image type, filter, and exposure, and count only when shot within `calibration_window_days` of the session start (the earliest attributed light), on either side of it. `pointing_index` is range-checked (1..pointing count) for every image type and labels the response; for lights it selects the pointing, for calibration types it only selects which pointing's light session anchors the window. This reports the frames that are attributable, not what the plan still needs — use `get_plan_progress` for that. **Output:** a wide table with one row per exposure: identity fields (`file_path`, `exposure_number`, `exposure_start`, `duration`, `filter_name`) plus metrics as columns namespaced by group (`quality.hfr`, `guiding.rms`, `background.adu_mean`, `pointing.airmass`, `weather.temperature`, ...). Weather samples are joined by exposure number and UTC exposure start. Timestamps come from the CSV's `ExposureStartUTC` column and are **displayed in the host's local time with its UTC offset** (e.g. `2026-09-21T21:16:25.123-05:00`); calibration-window arithmetic runs on UTC instants. Columns constant across the returned frames are listed once under `summary.constants`; columns with no real values (NaN/-1, empty, `n/a`, or the `0` sentinel N.I.N.A. writes for unmeasured metrics) are listed under `summary.unpopulated`. The result also carries `plan_id`, `pointing_index`, and `target`. Read-only. |
| `write_plan_file(plan)` | Validate and write an observation plan to a JSON file for later use by `load_sequence`. The plan defines one or more target pointings (RA/Dec/PA, optional label), acquisition intent, light and calibration frames, batching, cooling, autofocus, guiding, and observing constraints. Only creates the plan file; it does not load or start a sequence. |
| `write_mosaic_plan(plan_path, mosaic_csv, output_path?)` | Expand a base observation plan into a multi-pointing mosaic plan from a Telescopius-formatted mosaic CSV. The base plan supplies every setting except the pointings, which are replaced wholesale by one pointing per CSV row. The CSV must have nine columns in this order: `Pane`, `RA`, `DEC`, `Position Angle (East)`, `Pane width (arcmins)`, `Pane height (arcmins)`, `Overlap`, `Row`, `Column`; RA must be hours in [0, 24) (sexagesimal like `0hr 56' 01"` or decimal), and a non-matching header or out-of-range RA rejects the whole file. `row`/`column` are stored as metadata (N.I.N.A. does not consume them). The base plan's `plan_id` is cleared so the mosaic gets its own content-derived id, and each pane is attributed independently via `[{plan_id}-{pointing_index}]`. With no rotator, every pane's `position_angle_deg` must be `0`. Writes `<target>_<intent>_mosaic_<timestamp>.json` in the project directory unless `output_path` is given. Only creates the plan file. |
| `get_plan_progress(file_path, max_hfr?, min_detected_stars?, max_guiding_rms_arcsec?)` | Return per-frame-type acquisition progress for every pointing of an observation-plan JSON file: for each exposure group, `total_count` from the plan, `acquired_count` attributed to that plan/pointing from the imaging metadata (`ImageMetaData.csv` + `AcquisitionDetails.csv`), and `remaining_count`. Lights are attributed via `{base_plan_id}` (single-pointing) or `{base_plan_id}-{pointing_index}` (mosaic pane); flats/darks/bias are matched by image type, filter, and exposure and count only when shot within `calibration_window_days` of the earliest attributed light, on either side of it (inclusive; a 1-day window spans `anchor − 1d … anchor + 1d`). Calibration groups also carry `window_open` (bool) and `window_end` (`earliest light + calibration_window_days`, **displayed in the host's local time with its UTC offset**). While the current time is inside the window `remaining_count` is `total − acquired`; once outside, no frame shot now could be credited, so `remaining_count` is `0` (with `window_open: false`) — `acquired_count` stays truthful, and lights carry neither field. Raise `calibration_window_days` or write a new plan to reopen the window. Pass `max_hfr`, `min_detected_stars`, and/or `max_guiding_rms_arcsec` to exclude light frames that fail quality thresholds (frames missing the relevant field are excluded when a threshold is set). Use before `load_sequence` to decide what still needs acquiring. Returns `complete` (all panes done) plus a `pointings` list with per-pane progress and per-pane `complete`, so panes can be acquired sequentially. |

### Sequence Management

| Tool | Purpose |
|---|---|
| `load_sequence(request=[{action, plan, variant?, pointing_index?, mode="remaining", max_hfr?, min_detected_stars?, max_guiding_rms_arcsec?}, ...])` | Load a sequence with safety guardrails — it **never starts it**; call `start_sequence()` to begin. `request` is a **list** of request objects composing one N.I.N.A. sequence for the whole night: a single Start area, one Target area holding each step's containers in list order, and one End area that always stows — one bounded 3-minute "On Safe" wait, then park (or home) the mount and warm the camera. **An empty list (the default) is therefore the stow**; there is no `stow`/`park`/`warm`/`unpark` action, and `load_sequence()` with no arguments is the teardown. `action` (`light`, `dark`, `bias`, `flat`) decides what is built from `plan` (the observation-plan JSON path, required by every action; omitting it raises an error naming the entry). `pointing_index` (1-based, default 1) selects the pointing and applies to lights only; calibration is plan-level, so a calibration request must omit it (passing one is rejected). Lights embed `[{base_plan_id}]` (single-pointing plan, shared with its calibration) or `[{base_plan_id}-{pointing_index}]` (per mosaic pane) in the target name for attribution, which requires `$$TARGETNAME$$` in the profile's `FilePattern`. `variant` is scoped to the action and a bad pairing fails naming the entry: `flat` requires `dawn` (waits for morning twilight) or `dusk` (waits for sunset); `dark`/`bias` take `wheel` (default — park the wheel on the profile's `Dark` slot), `cover` (close the motorized cover, e.g. a flip-flat, around the exposures, then reopen it), or `shutter` (expose without moving the filter wheel, relying on the camera's shutter); `light` takes none. In the default `remaining` mode only frames still needed are acquired (total minus frames already attributed); if nothing remains the plan is reported complete and nothing is loaded. `mode="full"` acquires the whole plan again. `max_hfr`, `min_detected_stars`, and `max_guiding_rms_arcsec` exclude failing light frames from the acquired count. Steps run in order as the sequence reaches them; a step with its own waits (lights, flats) waits, or is skipped once its window has passed, without blocking the steps behind it. Entries already complete are skipped and reported; if every entry is complete nothing is sent to N.I.N.A. Profile, equipment, and metadata are fetched once, one POST carries the whole sequence, and a validation error names the entry by 0-based position (`request[1]: ...`). Fails if a sequence is already running — call `stop_sequence()` first. |
| `start_sequence()` | Start the currently loaded sequence immediately. Use after `load_sequence()`, which never starts it. |
| `stop_sequence()` | Stop the running sequence immediately and wait until NINA reports the sequencer has actually stopped. The scope is left where it is — this does **not** stow. For an urgent halt or a stuck sequence; to stow afterward, `load_sequence()` (empty list) and start it. Avoid interrupting an in-progress stow unless safety requires it, since stopping mid-slew can leave the scope in an unsafe position. |
| `get_sequence_state()` | Return the loaded sequence structure and the current status of its containers, instructions, conditions, and triggers. Use to determine whether a sequence is loaded, running, completed, failed, or waiting. Read-only. |
| `stop_nina()` | Terminate NINA.exe immediately via `taskkill`. Liveness comes from the REST API: a no-op when NINA is already down; raises when NINA is up but remote (no local Windows interop), since it cannot be terminated from here. |
| `start_nina()` | Launch NINA into the interactive desktop session. Raises if NINA is already running (call `stop_nina()` first), or if this host has no Windows interop to launch a remote NINA. |
| `get_nina_status()` | Lightweight lifecycle probe, cheaper than `get_site_equipment_status` (no device probes). Returns `running` (true iff `GET /time` answered — a missing local `NINA.exe` does not mean NINA is down when it runs remotely), `lifecycle_control_available` (this host has Windows interop, so `start_nina`/`stop_nina` can work), `probe_error` (N.I.N.A.'s failure text, or `None` when running), and a one-line `summary`. Never raises on a down NINA. |

---

## The Observation Plan

A plan is a JSON document that describes one complete imaging session. It encodes the **target**, **exposure settings** for all four frame types (light, flat, dark, bias), and **equipment configuration** (cooler, autofocus, guiding, constraints).

### Plan structure

An example json plan:

[veil-nebula_widefield-supernova-remnant_20260913T080623.json](./veil-nebula_widefield-supernova-remnant_20260913T080623.json)
```json veil-nebula_widefield-supernova-remnant_20260913T080623.json
{
  "target": "Veil Nebula",
  "intent": "Widefield supernova remnant",
  "description": "Veil Nebula complex centered between Western NGC 6960 and Eastern NGC 6992",
  "pointings": [
    {
      "ra_hours": 20.85,
      "dec_deg": 31.22
    }
  ],
  "batch_size": 5,
  "cooler": {
    "on": true,
    "setpoint_celsius": -10.0
  },
  "constraints": {
    "min_altitude": 30.0,
    "horizon_offset_degrees": 2.0
  },
  "autofocus": {
    "reference_filter_name": "LP",
    "hfr_increase_sample_size": 3,
    "hfr_increase_threshold_percent": 15.0,
    "every_n_exposures": 10,
    "threshold_celsius": 1.0
  },
  "guiding": {
    "dither_every_n_exposures": 2,
    "check_drift_every_n_exposures": 6,
    "max_drift_arcmin": 1.5
  },
  "light_frames": [
    {
      "filter_name": "LP",
      "exposure_time_seconds": 60.0,
      "total_count": 60
    }
  ],
  "flat_frames": [
    {
      "filter_name": "LP",
      "exposure_time_seconds": 5.0,
      "total_count": 30
    }
  ],
  "dark_frames": [
    {
      "exposure_time_seconds": 60.0,
      "total_count": 20
    }
  ],
  "bias_frames": [
    {
      "total_count": 30
    }
  ]
}
``` 

### Fields

| Field | Required | Description |
|---|---|---|
| `target` | no | Catalog designation, e.g. "M31", "NGC 7000" |
| `intent` | no | 2-3 words describing the goal |
| `plan_id` | no | Stable identifier stamped on write. Used for frame attribution; when absent, a deterministic hash of plan content is used. |
| `description` | no | explanation of the observation plan, including rationale, exposure goals, equipment, or sky constraints |
| `pointings` | **yes** | One or more target pointings. Each is a `Pointing` object: `label` (optional string shown in the NINA target name when set), `ra_hours` (J2000 RA in hours, `[0, 24)`), `dec_deg` (J2000 dec in degrees, `[-90, +90]`), `position_angle_deg` (optional rotator PA in degrees east of north, 

`[mosaic.csv](./mosaic.csv)
```csv mosaic.csv
Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane height (arcmins), Overlap, Row, Column
Pane 1, 0hr 56' 01", 45º 51' 18", 0.00, 309.00, 205.80, 10%, 1, 1
Pane 2, 0hr 29' 28", 45º 51' 18", 0.00, 309.00, 205.80, 10%, 1, 2
Pane 3, 0hr 55' 22", 42º 46' 13", 0.00, 309.00, 205.80, 10%, 2, 1
Pane 4, 0hr 30' 07", 42º 46' 13", 0.00, 309.00, 205.80, 10%, 2, 2
Pane 5, 0hr 54' 47", 39º 41' 07", 0.00, 309.00, 205.80, 10%, 3, 1
Pane 6, 0hr 30' 42", 39º 41' 07", 0.00, 309.00, 205.80, 10%, 3, 2
Pane 7, 0hr 54' 15", 36º 36' 00", 0.00, 309.00, 205.80, 10%, 4, 1
Pane 8, 0hr 31' 14", 36º 36' 00", 0.00, 309.00, 205.80, 10%, 4, 2
```

Each row becomes a `Pointing`: RA/DEC are parsed server-side (sexagesimal like `0hr 56' 01"` / `45º 51' 18"` or decimal), `Pane` becomes the `label`, and `Row`/`Column` are stored as `row`/`column` metadata (N.I.N.A. does not use them — they are kept for ordering, labeling, and later mosaic assembly). The base plan's `plan_id` is cleared so the mosaic gets its own content-derived id; each pane is attributed independently via `[{plan_id}-{pointing_index}]`.

- **No rotator:** every pane's `position_angle_deg` must be `0`. Any nonzero PA fails loudly, since PA can't be enforced without a rotator.
- **Acquiring panes:** query `get_plan_progress(file_path)` to see which panes still need frames, then load the first incomplete pane with `load_sequence(request=[{"plan": file_path, "action": "light", "pointing_index": N, "mode": "remaining"}])` and `start_sequence()`. Repeat pane by pane across the night; when the top-level `complete` is true, the mosaic is done.

---

## Workflow: A Complete Imaging Run

### 1. Write the plan

Use `write_plan_file` with the plan object. This validates the filter names against your active N.I.N.A. profile and writes a JSON file named like `veil-nebula_widefield-supernova-remnant_20260913T080623.json`.

### 2. Load and run each calibration type, including lights

Each call to `load_sequence` composes the appropriate steps (lights, darks, flats, or bias) into one N.I.N.A. sequence and posts it — it only loads the sequence; it never starts it. Every step below is therefore two calls: `load_sequence(...)`, then `start_sequence()` when you are ready to begin (or press start in the NINA GUI yourself). N.I.N.A. rejects a load while a sequence is running, so stop any running sequence first with `stop_sequence()` (which blocks until it has actually stopped); `load_sequence` returns a clear error telling you to do so otherwise.

**Or hand N.I.N.A. the whole night in one call:** pass the whole night as one **list** of request objects:

`load_sequence(request=[{"plan": "<plan>.json", "action": "light", "pointing_index": 1}, {"plan": "<plan>.json", "action": "light", "pointing_index": 2}, {"plan": "<plan>.json", "action": "dark"}])` then a single `start_sequence()`

The steps run in list order inside one sequence (one Start area, one Target area, one End area), one POST carries the lot, and entries already complete in `remaining` mode are skipped and reported. The End area always stows (park + warm) — so the night ends parked and warm no matter what ran, and an empty list with no steps at all is exactly the stow. Each step keeps its own conditions: a step whose window has passed (dawn flats after sunrise) is skipped instead of blocking the steps behind it.

**Example order:**

1. **Run lights** (main imaging overnight):
   `load_sequence(request=[{"plan": "<plan>.json", "action": "light"}])` then `start_sequence()`
   _(runs all night; autofocus and guiding triggers are built in)_

2. **Run darks** (done during the day or while flats are not possible):
   `load_sequence(request=[{"plan": "<plan>.json", "action": "dark"}])` then `start_sequence()`
   _(wait for completion; add `"variant": "cover"` to cut the light with a motorized cover instead of the wheel's `Dark` slot)_

3. **Run bias** (also done during the day):
   `load_sequence(request=[{"plan": "<plan>.json", "action": "bias"}])` then `start_sequence()`
   _(wait for completion)_

4. **Run dawn flats** (morning twilight):
   `load_sequence(request=[{"plan": "<plan>.json", "action": "flat", "variant": "dawn"}])` then `start_sequence()`
   _(wait for completion)_

5. **Run dusk flats** (as evening twilight begins):
   `load_sequence(request=[{"plan": "<plan>.json", "action": "flat", "variant": "dusk"}])` then `start_sequence()`
   _(wait for completion)_

### 3. Teardown

At session end:
- The running sequence should teardown automatically at dawn.
- `load_sequence()` then `start_sequence()` — the empty list is the stow; only needed if want to immediately close for the night or the running sequence fails to park. Every action is plan-backed, so an empty list is the only teardown form.

---

## Notes

- **Required N.I.N.A. plugins:** the MCP server requires the following three plugins to be installed in N.I.N.A.: **Advanced API**, **Sequencer+**, and **Session Metadata**.
- **Image file pattern:** in the N.I.N.A. profile's `ImageFileSettings`, `FilePattern` must put `$$IMAGETYPE$$` in a path segment (e.g. `$$DATEMINUS12$$\$$IMAGETYPE$$\...`) so frames can be read per type, and — for light attribution — must include `$$TARGETNAME$$` (see below). The `$$DATEMINUS12$$` date folder is no longer required: calibration age is measured from each frame's own `ExposureStart` timestamp, not the folder name.
- **Filter validation:** `write_plan_file` and `load_sequence` check that every filter name in the plan (lights, flats, autofocus reference) matches a filter in your active N.I.N.A. profile. Unknown filters will be rejected with an error listing what is available.
- **Frame attribution:** each light sequence names its target `<target>` (or `<target> - <label>` when the pointing has a label), followed by ` [{base_plan_id}-{pointing_index}]`, and N.I.N.A. expands `$$TARGETNAME$$` in `FilePattern` to that string. For the embedded id to be usable for attribution, `$$TARGETNAME$$` **must appear somewhere in the file pattern** — either as a filename token (e.g. `..._$$TARGETNAME$$_...`) or as a folder segment (e.g. `$$DATEMINUS12$$\$$IMAGETYPE$$\$$TARGETNAME$$\...`). Attribution reads the embedded id from the recorded file path (`file_path` in `ImageMetaData.csv`) and accepts it in **any** path segment — the filename, the target directory under `$$IMAGETYPE$$`, or an ancestor directory when `$$TARGETNAME$$` leads the pattern (e.g. `$$TARGETNAME$$\$$DATEMINUS12$$\$$IMAGETYPE$$\...`). The token is unique to a plan pointing, so ancestor matching cannot cross-attribute between plans; if the target name is not embedded in the path at all, attribution cannot determine which plan/pointing a light frame belongs to. The token is square-bracketed; the earlier parenthesised form `({base_plan_id}-{pointing_index})` is also recognised, so frames acquired before the switch to brackets continue to attribute. The sequence also records the target in `TargetName` in `AcquisitionDetails.csv` and as `OBJECT` in FITS headers, but those are informational — file-path attribution is what drives `get_plan_progress` and `mode="remaining"`.
- **Resuming a pointing:** call `get_plan_progress(file_path, ...)` and read the entry for pointing `N` in the returned `pointings` list to see what it has already acquired, then `load_sequence(request=[{"plan": <plan_path>, "action": "light", "pointing_index": N, "mode": "remaining"}])` to acquire only the deficit. Lights are attributed by the `{base_plan_id}-{pointing_index}` id embedded in the file path (so `$$TARGETNAME$$` must be in the file pattern — see above). Different pointings of the same plan file are attributed independently because their embedded ids differ. Flats, darks, and bias are matched by image type/filter/exposure **and** are anchored to the plan's light session: they count only when shot within `calibration_window_days` (default 7) of the earliest attributed light, on either side of it. Calibration acquired inside that window stays counted, so it is not re-shot on every later run. While the current time is still inside the window the deficit is reported (and `window_open` is `true`); once the current time falls outside it, `remaining_count` reads 0 with `window_open: false` and `load_sequence(request=[{"plan": <plan_path>, "action": "dark", "mode": "remaining"}])` loads nothing — raise `calibration_window_days` or write a new plan to reopen the window. Pass `mode="full"` to deliberately re-acquire. For a mosaic, `get_plan_progress(file_path)` summarizes every pane at once — see [Mosaics](#mosaics).
- **Quality thresholds:** `max_hfr` and `min_detected_stars` (optional) exclude light frames that fail the thresholds from the acquired count. Frames missing the quality fields are excluded whenever a threshold is set (fail-closed). Thresholds are applied only to light frames — calibration frames have no star quality.
- **Manually failing a frame:** delete (or move) the image file on disk — e.g. a bad `.fits`/`.tif`. The metadata row stays in the CSV (the plugin only appends), but it is omitted when the metadata is read, so progress no longer counts that frame as acquired and the next `mode="remaining"` load re-acquires it.
- **The plan file is persistent** — written to the current working directory. You can inspect, edit, and reuse it across sessions.
- **Experimental:** This code is highly experimental.  At the moment I am testing it at my observatory.  However I don't have a camera cooler.  So those operations are untested.  The agent generates an advanced sequence that it loads into N.I.N.A.  This sequence is still in alpha.  

---

## `nina-plugin.ts` — OpenCode v2 Autonomous Plugin

`nina-plugin.ts` is an [opencode](https://opencode.ai) v2 plugin (not compatible with Claude Code or other MCP clients). Once registered in `opencode.json`, it runs as a background server inside opencode and does two things:

1. **Websocket event monitoring** — Connects to the N.I.N.A. event socket (`ws://<host:port>/v2/socket`), subscribes to all events, and forwards them to the active agent as intervention prompts. Events are batched with a 1-second debounce to avoid flooding the conversation.

2. **Interval check** — Every `NINA_INTERVAL_MINUTES` (default 10) it prompts the agent to query observatory status (`get_sequence_state`, `get_site_equipment_status`) and decide what to do next, even when no N.I.N.A. events are firing.

The plugin triggers a dedicated opencode "agent" called `worker` (see [`worker.md`](#workermd--opencode-worker-agent)), if it is configured, otherwise it uses the main session model. Both websocket-event triggers and interval checks prompt the same `worker` agent in an isolated session, which checks observatory status and takes action.

The plugin auto-reconnects on websocket disconnection with a 5-second retry. On server dispose, it cleans up all timers and the socket.

---

## `AGENTS.md` — OpenCode Worker Agent

`AGENTS.md` (in `.opencode/agents/`) defines the observer agent that the active console user becomes and the `nina-plugin.ts` triggers for automated safety interrupts, sequence halts, and recovery routines.

---

### `PLAN.md` — the living wishlist

A markdown file at the project root, **edited by the user at any time** (before or during the night). It is intentionally lightweight — entries can be as simple as a target name:

1. **Metadata** — night date, overall intent (optional).
2. **Candidate targets** — each with:
   - name / catalog designation (minimum required — everything else optional)
   - optional: coordinates, filter + exposure + count (if you want specifics)
   - optional: reference to an existing plan JSON file, or let the worker generate one
   - priority (1 = highest, optional)
   - optional time window / note
3. **Selection rules** — how the worker picks among candidates:
   - altitude floor (default e.g. 30°)
   - horizon buffer
   - tie-break order (priority, then highest current altitude, then earliest available)

Example shapes (illustrative — both are valid):

[PLAN.md](./PLAN.md)
```json PLAN.md
# Tonight - 2026-10-08

- M31          "before the moon comes up"
- Veil Nebula  plan=veil-nebula_..._20260913.json   prio 1
- M33          "if high targets done"

Rules:
- min_altitude: 30
- horizon_offset_degrees: 2
- tie_break: priority, then altitude
```

### How the night runs

- The worker evaluates each un-imaged candidate, computes its current altitude (or loads its plan sequence and lets N.I.N.A. report it), and picks the best target: highest priority, then highest current altitude, then earliest available. It never interrupts a running observation — re-selection only happens after a sequence finishes.
- **Time and coordinates**: no MCP tool returns a clock — the worker reads the OS clock of the machine it runs on (the same clock the MCP server uses internally, and the same timezone N.I.N.A. runs in). Altitude is computed from the target's J2000 `ra_hours`/`dec_deg` (from the plan JSON's `pointings`, or from `PLAN.md`) and the site's `latitude_deg`/`longitude_deg` from `get_site_profile()`. That estimate only decides *ordering*: the generated sequence independently embeds `WaitForAltitude` / `AltitudeCondition` guards, so N.I.N.A. re-checks altitude at runtime and will not image below the floor even if the worker's arithmetic or clock is off.
- For the chosen target, the worker loads the referenced plan JSON if given, otherwise generates one via `write_plan_file` (using coords/filter/count from `PLAN.md`, or sensible defaults), then `load_sequence(request=[{"plan": <plan_path>, "action": "light"}])` followed by `start_sequence()`. A target listed by name only (no `plan=` and no coordinates) leaves the worker to supply its own coordinates — add a plan path or RA/Dec to avoid that.
- When no candidate is viable (all below the altitude floor, or the night is over), the worker writes `report.md` (overwriting any previous one) with the night's results, stows the scope, and stops. Editing `PLAN.md` later triggers re-evaluation.
- You can add/remove/reorder lines at any moment. The worker records completion in `PROGRESS.md` instead and leaves `PLAN.md` untouched, so your editing isn't fought over.

---

## `PROGRESS.md` — Shared Progress File

`PROGRESS.md` is a shared observatory progress file in the project directory. Both the active session and the automated `worker` sessions read it on start to restore context before acting, and append a short ISO-8601-timestamped entry after each significant action (status checks, plan writes, sequence loads/starts/stops, errors, and interventions), recording what was done, the observed equipment and safety state, and any decisions. This keeps history shared across sessions.

---

## `opencode.json` — Opencode v2 Sample Configuration

[.opencode/opencode.json](./.opencode/opencode.json)
```json .opencode/opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [
    {
      "package": "./local/plugins/nina_notify",
      "options": {
        "pluginLogs": "C:/Windows/Temp/nina-notify-debug.log"
      }
    }
  ],
  "mcp": {
    "servers": {
      "nina-planner": {
        "type": "local",
        "environment": {
          "NINA_PLANNER_LOG": "C:/Windows/Temp/nina-planner-debug.log"
        },
        "command": [
          "uv", "--directory", ".opencode/local/mcps/nina_planner", "run", "-m", "nina_planner"
        ]
      }
    }
  }
}
```

Registers the opencode plugin and the `nina_planner` MCP server so both run together. The MCP server provides the tools (`load_sequence`, `get_site_equipment_status`, etc.) that the plugin-prompted agent calls.

---

## Plugin Options

### `nina-plugin.ts` (opencode v2 plugin)

Configured under `opencode.json > plugin` as the second array element (see [`opencode.json`](#opencodejson--opencode-v2-sample-configuration)).

| Option | Type | Default | Description |
|---|---|---|---|
| `ninaEndpoint` | string | `127.0.0.1:1888` | N.I.N.A. host and port (no scheme) for the websocket event stream, e.g. `192.168.0.24:1888`. The plugin connects to `ws://<ninaEndpoint>/v2/socket`. |
| `intervalCheckMinutes` (or `intervalCheck`) | number | `-1` | Minutes between autonomous status-check triggers of the `worker` agent. Accepts a float; the value is multiplied by `60_000` ms before scheduling.  Disabled if 0 or less. |
| `pluginLogs` | string | _(none)_ | File path appended on each plugin event (websocket open/message/close/error, batched flush, interval trigger failures). Set to a writable path (e.g. `/tmp/nina-plugins.log`) to capture diagnostic output. Omit or leave empty to disable file logging. |
| `agentHistory` | string | _(none)_ | File path that receives an append-only JSON-lines record of every `worker` session the plugin spawns. Each line is `{ timestamp, sessionId, agent, messages }`. Omit or leave empty to skip history capture. |

---

## Environment Variables

### `nina_planner` (MCP server)

| Variable | Default | Description |
|---|---|---|
| `NINA_ENDPOINT` | `127.0.0.1:1888` | N.I.N.A. host and port for the REST API (`host:port`).  For WSL, try `192.168.0.24:1888`. When this points at a remote host (not the MCP server's own machine), `get_nina_status().lifecycle_control_available` is false and `start_nina`/`stop_nina` cannot operate on NINA. |
| `NINA_DRIVE_MOUNT` | — | Local mount root for the drive letter in the profile's `image_save_path`, used by `get_imaging_metadata` to resolve the imaging directory on Linux and WSL (e.g. `/mnt` on a Mac with C: mounted there, `/mnt/c` in WSL). Defaults to `/mnt/<drive>` (e.g. `/mnt/c`). On Windows (`win32`) the profile `image_save_path` is used directly. The resolved value is also exposed as the derived `local_image_save_path` field on the profile returned by `get_site_profile`. |
| `NINA_FLATS_ALTITUDE` | `80` | Altitude in degrees for flat panel calibration frames |
| `NINA_FLATS_AZIMUTH_DAWN` | `270` | Azimuth in degrees pointing west for dawn flats |
| `NINA_FLATS_AZIMUTH_DUSK` | `90` | Azimuth in degrees pointing east for dusk flats |
| `NINA_EXE_PATH` | `C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe` | Windows path to `NINA.exe` used by `start_nina` to launch NINA. |

## Setting up WSL

Make sure you have setup mirroring to allow you to connect to NINA via localhost.  Do this by editing wslconfig and setting networking mode to mirrored.  Otherwise you will have to know the external ip of your machine in order to connect to your NINA API endpoint.


```
$ vi $HOME/.wslconfig
[wsl2]
networkingMode=mirrored
```

Then shutdown wsl and restart

[$ wsl --shutdown](./$ wsl --shutdown)
```bash
$ wsl --shutdown
<Error: File '$ wsl --shutdown' not found on disk>
```

In addition, to get the system tools to work to stop and restart NINA should it stop, make sudo password less with:


```bash
$ echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/90-user
```
