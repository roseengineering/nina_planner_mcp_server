from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="nina-planner",
    instructions="""Provides tools for controlling NINA (Nighttime Imaging 'N' Astronomy) software run observatories: inspect equipment, read the observatory setup, write observation plans, and load/start NINA sequences built from those plans. Plans are orchestrated at a high level through sequences; the individual commands inside them are NINA's own.

Safety: the safety monitor's `is_safe` reflects whether the enclosure (roof/dome) is open and it is safe to unpark and expose. While `is_safe` is false the scope must remain parked and sky-dependent acquisition (lights, dawn/dusk sky flats) is gated until it clears. Weather conditions are a separate input, reported by the weather device. Darks and biases are NOT sky-dependent and are exempt: they may be loaded and started with the enclosure closed — the step parks the mount first and never unparks, cuts the light at the camera (Dark filter slot, cover, or shutter), and needs no open sky.

Park is the only closing maneuver: sequences emit Park Scope whenever the mount reports can_park=true, and a mount that cannot park (or no mount at all) is rejected at load time. Find home is never a park fallback: it is emitted once per lights step, ahead of set_tracking, when can_find_home=true. Homing is a calibration — the mount drives its axes to a known reference point and re-syncs its sensors, about 30 seconds — restoring a trustworthy position readout after a lost sync (power outage, bump, driver restart); it does not close up the scope.

Lifecycle: start_nina() launches NINA into the active interactive desktop session; stop_nina() terminates NINA immediately. If NINA is already running, start_nina errors and requires stop_nina first. get_nina_status() reports REST-API liveness (running), whether this host can control NINA's lifecycle (lifecycle_control_available), and a one-line summary — cheaper than get_site_equipment_status and never raises on a down NINA. When lifecycle_control_available is false (e.g. the MCP server is remote from NINA), start_nina/stop_nina cannot operate on NINA: a down NINA must be restarted on its own host, and an already-running one cannot be stopped from here.

Progress tracking: maintain an observatory progress file (PROGRESS.md in the project directory). On session start, read it to restore context before acting. After significant actions — status checks, plan writes, sequence loads/starts/stops, errors, and interventions — append a short timestamped entry (ISO-8601) recording what was done, the observed equipment and safety state, and any decisions. Both the active session and automated worker sessions append to this file so history is shared across sessions.""",
)
