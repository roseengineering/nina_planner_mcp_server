# nina-planner

An [MCP](https://modelcontextprotocol.io) server for controlling
[N.I.N.A.](https://nighttime-imaging.com/) (Nighttime Imaging 'N' Astronomy) —
expose observation plans, run acquisition sequences, and manage the observatory
lifecycle (site profiles, stowing, start/stop) from any MCP-compatible agent.

The server talks to N.I.N.A.'s v2 REST API and orchestrates it at a high level:
you describe *what* to observe (a plan), and the server builds and runs the
underlying N.I.N.A. sequence.

## Install

```sh
uv add nina-planner
# or
pip install nina-planner
```

## Usage

The package installs a `nina_planner` console script:

```sh
nina_planner
```

Or register it with your MCP client, for example in OpenCode:

```json
{
  "mcp": {
    "servers": {
      "nina-planner": {
        "type": "local",
        "command": ["nina_planner"]
      }
    }
  }
}
```

## Tools

**Observatory & site**
- `get_site_equipment_status` — full equipment status (mount, camera, focuser, ...) plus safety and weather
- `get_site_profile` / `list_site_profiles` / `switch_site_profile` — read and switch N.I.N.A. observatory profiles
- `screenshot_dashboard` — capture the N.I.N.A. dashboard as an image

**Planning & acquisition**
- `write_plan_file` — create an observation plan JSON file
- `write_mosaic_plan` — expand a plan into a multi-pointing mosaic from a Telescopius CSV
- `run_plan` — load and start the acquisition sequence for a plan (or one mosaic pane)
- `get_plan_progress` — per-frame-type acquired/remaining counts per pointing
- `get_imaging_metadata` — per-exposure frame metrics (HFR, guiding RMS, weather, ...)

**Sequence control**
- `get_sequence_state` / `start_sequence` / `stop_sequence` / `stow_telescope` —
  inspect and control the running sequence; stow parks or homes the mount

**N.I.N.A. lifecycle**
- `start_nina` / `stop_nina` / `get_nina_status` — launch, terminate, and probe N.I.N.A.,
  including simulated-time starts for planning on a shifted clock

**Diagnostics**
- `get_events` / `get_logs` — recent N.I.N.A. events and application log entries

## Safety model

- The safety monitor's `is_safe` reflects whether the enclosure (roof/dome) is open:
  `true` means it is safe to unpark and expose; `false` means the scope must stay
  stowed and acquisition is gated. Weather conditions are reported separately.
- Stow is capability-driven: the sequence parks the mount when `can_park` is true,
  otherwise it finds home when `can_find_home` is true.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `NINA_ENDPOINT` | `127.0.0.1:1888` | Host:port of the N.I.N.A. REST API |
| `NINA_PLANNER_LOG` | *(unset)* | Log file path |
| `NINA_EXE_PATH` | N.I.N.A. default install path | NINA executable (lifecycle control) |
| `NINA_DRIVE_MOUNT` | auto-detected | Windows drive mount (e.g. `/mnt/c`) for WSL |
| `NINA_FLATS_ALTITUDE` | `80` | Altitude for flat acquisition |
| `NINA_FLATS_AZIMUTH_DAWN` / `_DUSK` | `270` / `90` | Azimuth for dawn/dusk flats |

## Development

From the repository root:

```sh
make install_uv       # uv venv + uv sync (dev group: pytest, ruff, mypy)
make test             # run the test suite
make coverage         # test suite with coverage
make live             # live integration tests (requires running N.I.N.A.)
```

Or directly from this package directory:

```sh
uv sync
uv run ruff check .   # lint
uv run mypy .         # type-check
uv run pytest         # test suite
```

Live tests are marked `live` and excluded by default; `make live_korolev` /
`make live_soyuz` point them at specific hosts.
