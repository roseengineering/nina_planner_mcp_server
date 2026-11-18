---
name: nina-lifecycle
description: Check, start, or stop the NINA application via the nina-planner MCP server. Use when NINA may be down or unresponsive, or when start_nina/stop_nina/get_nina_status is needed.
---

# NINA lifecycle

- `start_nina()` launches NINA into the interactive desktop session (GUI visible).
- If NINA is already running when `start_nina` is called, it errors with `"NINA is already running. Call stop_nina() first."`
- `stop_nina()` terminates `NINA.exe` immediately via taskkill. Use it to shut down NINA. It is a no-op when NINA is already down, and it errors when NINA is up but remote (no local Windows interop).
- `get_nina_status()` reports `running` (REST-API-authoritative liveness), `lifecycle_control_available`, and a one-line summary in one cheap call. `lifecycle_control_available=false` means **this host has no Windows interop** (`tasklist`/`taskkill`/`schtasks` not reachable) — `start_nina`/`stop_nina` cannot operate on NINA from here, so a down NINA must be restarted on its own host and an already-running NINA cannot be stopped from here. It does not depend on `NINA_ENDPOINT`: a remote endpoint with interop still reports `true`.
