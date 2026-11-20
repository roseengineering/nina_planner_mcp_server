
The server is an MCP facade over N.I.N.A.’s REST API, with planning and sequence construction layered on top. I’m now following the registered tools into those layers to distinguish API passthrough from local validation and orchestration.

## 1. High-Level Summary

This repository provides an MCP server for planning, running, and monitoring astrophotography sessions in N.I.N.A. (Nighttime Imaging ‘N’ Astronomy). It turns validated observation plans into N.I.N.A. sequencer payloads, tracks acquired frames against those plans, and exposes equipment, safety, sky, and lifecycle tools to an agent. The main Python package is under `.opencode/local/mcps/nina_planner/nina_planner`; the repository also contains OpenCode configuration, skills, and a separate notification plugin.

## 2. Execution Flow

1. **Startup and registration:** `python -m nina_planner` runs `__main__.py`, which imports `server.py`. Importing the tool modules registers their `@mcp.tool()` functions with the FastMCP instance in `app.py`; the server communicates over stdio.
2. **Plan creation:** `write_plan_file()` validates an `ObservationPlan`, fetches the active profile, and—if given a mosaic block—derives its pointings from the camera/telescope field of view. It writes the resulting JSON atomically under the process working directory.
3. **Sequence request:** `load_sequence()` validates each request as a strict `SequenceRequest`. `_load_many()` caches the profile, equipment status, metadata rows, and reference time for the request list, then loads each plan and checks filters, pointing, hardware capabilities, and requested action.
4. **Remaining-frame calculation:** In the default `remaining` mode, metadata from the imaging tree is matched to plan IDs and exposure groups. Quality thresholds apply to light frames; calibration has a time window anchored to the plan’s attributed light session. If a group is complete—or its calibration window has expired with a deficit—it is not added as work.
5. **Payload construction and load:** The builders create N.I.N.A. sequence JSON for lights, darks/bias, or flats. `build_sequence_many()` wraps the ordered steps in one Start area, Target area, and End area; the End area parks the mount and warms the camera. The complete payload is POSTed to N.I.N.A. once. Loading does **not** start the sequence.
6. **Run and monitor:** `start_sequence()` calls N.I.N.A.’s start endpoint and polls sequence state until nodes leave `CREATED`; `stop_sequence()` waits until the sequence is no longer running, but does not park. `get_sequence_state()` returns raw state or a compact projection, optionally merging horizon data from a second API response.
7. **Other tool paths:** Equipment/profile tools query the REST API; sky tools calculate ephemerides with Astropy and resolve non-catalog names through SIMBAD/SESAME; lifecycle tools use Windows interop and Task Scheduler when available.

The shared HTTP client in `client.py` sets a 10-second request timeout, unwraps N.I.N.A.’s response envelope, and surfaces API errors. `config.py` reads the endpoint and project directory at import time.

## 3. Key Logic & Design Choices

- **Plans are the source of truth:** `ObservationPlan` and `SequenceRequest` are strict Pydantic models. Unknown fields fail instead of being silently ignored, and action-specific variants are checked before sequence construction.
- **Progress and loading use shared counting rules:** `progress.py` centralizes attribution, quality filtering, calibration-window handling, and completion verdicts. This avoids `get_plan_progress()` and `load_sequence()` disagreeing about what remains.
- **Sequences encode operational policy:** `sequence/builders.py` and `sequence/targets.py` compose nested N.I.N.A. nodes for safety waits, observing windows, autofocus, guiding, and exposures. In particular, darks/bias explicitly stay parked, while lights and sky flats are safety-gated. A countdown array preserves remaining exposure work if a safety container exits and resumes.
- **The server distinguishes acceptance from completion:** Sequence start/stop endpoints can acknowledge before the operation actually takes effect, so the client polls state rather than treating an HTTP success as proof.
- **Device reporting can cause side effects:** `get_site_equipment_status()` attempts to connect configured but disconnected devices; despite its name, it is not read-only.

## 4. Review Checklist & Red Flags

- **Safety policy:** Check the generated payloads—not just tool descriptions—for park/unpark ordering, the `is_safe` gate, and dark/bias exemptions. Also verify the operational assumption that `can_park` is used as a proxy for unpark capability.
- **Remote API boundary:** `NINA_ENDPOINT` is used to form a plain HTTP URL. Confirm the endpoint is trusted and appropriately isolated; the client code shown does not add authentication or TLS.
- **Local filesystem scope:** Plan paths can be absolute, while metadata scanning walks the profile’s imaging root. Review which MCP callers are trusted to select files/directories and what the server process can read.
- **Fail-open checks:** The Sequencer+ plugin check allows loading when the plugin list cannot be read or has an unexpected shape. That avoids blocking on a diagnostic endpoint, but can permit a payload whose Sequencer+ nodes will not execute.
- **Operational impact:** `stop_nina()` force-kills `NINA.exe` by image name, and `get_site_equipment_status()` auto-connects hardware. Ensure operators understand those effects, especially on hosts with multiple sessions or shared equipment.
- **Progress interpretation:** Counts depend on path attribution, metadata CSV contents, and the frame file still existing. `mode="full"` is cumulative, so a plan may still read complete after an incomplete re-shoot; use the audit tool and per-date files to verify a particular run.
- **Timing and performance:** Metadata reads walk the imaging tree and inspect CSVs; repeated progress/audit calls can become I/O-heavy on large or remote drives. Sequence polling uses short intervals, while API calls create a new HTTP client each time—worth checking against actual observatory latency and load.

