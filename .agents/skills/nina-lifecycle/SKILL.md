---
name: nina-lifecycle
description: Check, start, or stop the NINA application via the nina-planner MCP server. Use when NINA may be down or unresponsive, or when start_nina/stop_nina/get_nina_status is needed.
---

# NINA lifecycle

- `start_nina()` launches NINA into the interactive desktop session (GUI visible) on the current time.
- If NINA is already running when `start_nina` is called, it errors with `"NINA is already running. Call stop_nina() first."`
- `stop_nina()` terminates `NINA.exe` immediately via taskkill. Use it to shut down NINA.
- `get_nina_status()` reports `running` (REST-API-authoritative liveness), `lifecycle_control_available`, and a one-line summary in one cheap call. `lifecycle_control_available=false` means the MCP server is remote from NINA: `start_nina`/`stop_nina` cannot operate on it, so a down NINA must be restarted on its own host and an already-running NINA cannot be stopped from here.
