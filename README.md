# nina_planner — An Observatory Operator Agent

![Banner for repository](banner.png)

`nina_planner` is an MCP tool server for N.I.N.A. (Nighttime Imaging 'N' Astronomy). It helps generate observation plans for individual targets and compose them into multi-object N.I.N.A. sequences for an observing run, rather than issuing real-time raw Advanced API commands.

## Available Tools

### Equipment & Telemetry

| Tool | Purpose |
|---|---|
| `get_site_equipment_status()` | Current equipment, weather, and enclosure safety status. |
| `get_site_profile()` | Active NINA profile and observatory configuration. |
| `list_site_profiles()` | List profiles and identify the active one. |
| `switch_site_profile(profile_id)` | Switch the active NINA profile. |
| `get_events(since=300)` | Recent NINA observatory events. |
| `get_logs(since=300)` | Recent NINA application logs. |
| `screenshot_dashboard()` | Capture and return the NINA Imaging dashboard. |
| `get_imaging_metadata(file_path, pointing_index=1, image_type="light")` | Per-exposure metrics for a plan pointing from `ImageMetaData.csv` and `WeatherData.csv`. |
| `write_plan_file(plan)` | Validate and write an observation-plan JSON file. |
| `write_mosaic_plan(plan_path, mosaic_csv, output_path?)` | Create a multi-pointing mosaic plan from a base plan and CSV. |
| `get_plan_progress(file_path, max_hfr?, min_detected_stars?, max_guiding_rms_arcsec?)` | Show acquired and remaining counts by frame type and pointing. |

### Sequence Management

| Tool | Purpose |
|---|---|
| `load_sequence(request=[{action, plan, ...}, ...])` | Load an ordered, plan-backed sequence; does not start it. Empty request stows. |
| `start_sequence()` | Start the loaded sequence. |
| `stop_sequence()` | Stop the running sequence; does not stow. |
| `get_sequence_state()` | Inspect the loaded sequence and current status. |
| `stop_nina()` | Stop NINA on this host. |
| `start_nina()` | Start NINA in the interactive desktop session. |
| `get_nina_status()` | Check NINA API status and lifecycle-control availability. |

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
| `description` | no | Explanation of the observation plan, including rationale, exposure goals, equipment, or sky constraints. |
| `pointings` | **yes** | One or more pointing objects. Each requires `ra_hours` (J2000 RA in hours, `[0, 24)`) and `dec_deg` (J2000 declination in degrees, `[-90, +90]`). Optional `label` is shown in the N.I.N.A. target name; `position_angle_deg` is rotator PA in degrees east of north, `[0, 360)`. If omitted, `0` is sent to N.I.N.A., leaving the rotator at its current/synced position. Optional `row` and `column` are nonnegative mosaic metadata not sent to N.I.N.A. |
| `batch_size` | no | Exposures per batch; defaults to `5`. Set to `0` to disable batching. |
| `calibration_window_days` | no | Defaults to `7` (minimum `1`). Calibration frames count only when taken within this many days before or after the earliest attributed light. After the window closes, remaining calibration is reported as `0`. |
| `cooler` | no | Cooler settings: `on` defaults to `true`; `setpoint_celsius` defaults to `-10` and must be in `[-40, 30]`. |
| `constraints` | no | `min_altitude` defaults to `30` degrees and must be in `[0, 90]`; `horizon_offset_degrees` defaults to `2` and must be in `[0, 10]`. |
| `autofocus` | no | `reference_filter_name` defaults to `"L"`; `hfr_increase_sample_size` to `3`; `hfr_increase_threshold_percent` to `15`; `every_n_exposures` to `10`; and `threshold_celsius` to `1`. Numeric values must be greater than zero. |
| `guiding` | no | `dither_every_n_exposures` defaults to `2`; `check_drift_every_n_exposures` to `6`; and `max_drift_arcmin` to `1.5`. Numeric values must be greater than zero. |
| `light_frames` | **yes** | Nonempty list of light exposure specs, each with `filter_name`, `exposure_time_seconds` (greater than zero), and `total_count` (at least `1`). |
| `flat_frames` | **yes** | Nonempty list of flat exposure specs, each with `filter_name`, `exposure_time_seconds` (greater than zero), and `total_count` (at least `1`). |
| `dark_frames` | **yes** | Nonempty list of dark exposure specs, each with `exposure_time_seconds` (greater than zero) and `total_count` (at least `1`). |
| `bias_frames` | **yes** | Nonempty list of bias exposure specs, each with `total_count` (at least `1`). |

### Mosaic CSV format

For `write_mosaic_plan`, use a Telescopius-formatted CSV with these nine columns in this order:
See [mosaic.csv](./mosaic.csv) for an example:
[mosaic.csv](./mosaic.csv)
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

## Notes

- **Required N.I.N.A. plugins:** the MCP server requires the following three plugins to be installed in N.I.N.A.: **Advanced API**, **Sequencer+**, and **Session Metadata**.
- **Image file pattern:** for **light frames only**, the N.I.N.A. profile's `ImageFileSettings.FilePattern` must put `$$TARGETNAME$$` in a path segment so frames can be attributed to a plan (e.g. `$$TARGETNAME$$\$$IMAGETYPE$$\...`). Calibration frames (dark, bias, and flat) do not require this token; they are matched by frame type and exposure settings within the calibration window.
- **Quality thresholds:** `max_hfr` and `min_detected_stars` (optional) exclude light frames that fail the thresholds from the acquired count. Frames missing the quality fields are excluded whenever a threshold is set (fail-closed). Thresholds are applied only to light frames — calibration frames have no star quality.
- **Manually failing a frame:** delete (or move using thumbs down icon) the image file on disk — e.g. a bad `.fits`/`.tif`. The metadata row stays in the CSV (the plugin only appends), but it is omitted when the metadata is read, so progress no longer counts that frame as acquired and the next `mode="remaining"` load re-acquires it.
- **The plan file is persistent** — written to the current working directory. You can inspect, edit, and reuse it across sessions.  Changing the file will alter its plan_id, if not given.

## `nina_notify` — OpenCode v2 Autonomous Plugin

`nina-notify` is an [opencode](https://opencode.ai) v2 plugin for OpenCode coding agents only; it is not an MCP server and cannot be loaded by Claude Code or other coding agents. The separate `nina_planner` MCP server can be registered with MCP-compatible coding agents, including OpenCode. Registered in `opencode.json`, the plugin runs in the background inside OpenCode and does two things:

1. **Websocket event monitoring** — Connects to the N.I.N.A. event socket (`ws://<host:port>/v2/socket`), subscribes to all events, and forwards them to the active agent as intervention prompts. Events are batched with a 1-second debounce to avoid flooding the conversation.

2. **Interval check** — Every `NINA_INTERVAL_MINUTES` (default -1, disabled) it prompts the agent to query observatory status (`get_sequence_state`, `get_site_equipment_status`) and decide what to do next, even when no N.I.N.A. events are firing.

The plugin auto-reconnects on websocket disconnection with a 5-second retry. On server dispose, it cleans up all timers and the socket.

### `PLAN.md` — the living wishlist

A markdown file at the project root, **edited by the user at any time** (before or during the night). It is intentionally lightweight — entries can be as simple as a target name:

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

## `PROGRESS.md` — Shared Progress File

`PROGRESS.md` is a shared observatory progress file in the project directory. The active session reads it on start to restore context before acting, and appends a timestamped entry after each significant action, recording what was done, the observed equipment and safety state, and any decisions. This keeps history shared across sessions.


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

### `nina-notify` (opencode v2 plugin)

Configured in the plugin's options configuration. (see [`opencode.json`](#opencodejson--opencode-v2-sample-configuration)).

| Option | Type | Default | Description |
|---|---|---|---|
| `ninaEndpoint` | string | `127.0.0.1:1888` | N.I.N.A. host and port (no scheme) for the websocket event stream, e.g. `192.168.0.24:1888`. The plugin connects to `ws://<ninaEndpoint>/v2/socket`. |
| `intervalCheckMinutes` | number | `-1` | Minutes between autonomous status-check triggers of the `worker` agent. Accepts a float; the value is multiplied by `60_000` ms before scheduling.  Disabled if 0 or less. |
| `pluginLogs` | string | _(none)_ | File path appended on each plugin event (websocket open/message/close/error, batched flush, interval trigger failures). Set to a writable path (e.g. `/tmp/nina-plugins.log`) to capture diagnostic output. Omit or leave empty to disable file logging. |

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

Make sure mirroring is setup in WSL to allow you to connect to NINA directly through localhost (otherwise you have to use your machines' IP address).  Do this by editing wslconfig and setting networking mode to mirrored.


```
$ vi $HOME/.wslconfig
[wsl2]
networkingMode=mirrored
```

In addition, to get the system tools to work to stop and restart NINA should it stop, make sudo password less with:


```bash
$ echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/90-user
```
