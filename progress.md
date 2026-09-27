# progress.md

Shared observatory progress file. Both the active session and automated worker sessions read this on start and append a short timestamped entry after each significant action (status checks, plan writes, sequence loads/starts/stops, errors, interventions).

---

## Created


## 2026-09-26T21:23:11-05:00 — auto

ensure_nina_running: NINA already running, no launch

## 2026-09-26T21:25:11-05:00 — auto

ensure_nina_running: launched NINA from C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe

## 2026-09-26T21:29:33-05:00 — auto

simulate_observation_time(reset=True): killed NINA, ran w32tm /resync, relaunched NINA on real OS clock; converged=True, delta=-0.23s.

## 2026-09-26T21:32:39-05:00 — auto

ensure_nina_running: launched NINA from C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe

## 2026-09-26T22:33:18-05:00 — auto

start_nina: launching NINA on real time; launched NINA from C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe

## 2026-09-26T22:33:59-05:00 — auto

stop_nina: killed running NINA

## 2026-09-27T09:11:35-05:00 — auto

stop_nina: killed running NINA

## 2026-09-27T11:11:35-05:00 — auto

start_nina: simulating observation time 2026-09-27T11:11:19; shifted OS clock to 2026-09-27T11:11:19; launched NINA from C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe; restored host clock to 2026-09-27T11:11:05.600265-05:00 (verified=True)

## 2026-09-27T11:11:54-05:00 — auto

stop_nina: killed running NINA

## 2026-09-27T11:29:08-05:00 — auto

start_nina: launching NINA on real time; launched NINA from C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe

## 2026-09-27T11:32:00-05:00 — auto

Triggered by PROFILE-CHANGED event (NINA loading profile after start_nina launch). get_nina_time() responds OK; get_site_equipment_status() via MCP tool fails but raw curl to /v2/api/equipment/info returns full payload (all equipment Connected=false, no devices configured on this profile). No sequence loaded, no acquisition. Plan.md is empty (no targets). Nothing to do — appended this entry and stopped.

## 2026-09-27T11:34:00-05:00 — auto

restart_nina: stopped NINA (PID 14324), started NINA on real time. New PID 7620, Session# 1 (Console, visible desktop). get_nina_time() matches host clock (simulated=False).

## 2026-09-27T11:35:30-05:00 — auto

Triggered by equipment-reconnect cycle (mount/camera/focuser/guider/filterwheel/rotator/dome/weather/safety/switch all disconnected then reconnected). Final state: all devices connected, mount parked, dome parked with shutter closed, safety is_safe=false (enclosure closed). Sequence state is empty default containers — no sequence running, no targets in plan.md. Nothing to act on; logging and stopping.

## 2026-09-27T11:36:30-05:00 — auto

Profile name/id changed from `Default-2026-09-26T21:03:09` (1f4d07da-...) to `Default` (df137496-...) after restart. New profile is fully populated: site 31.5475N, -99.381667W, 470m; optics mine 500mm f/5 with 640x480 sim camera; filters Red/Green/Blue/Clear with focus offsets; ASTAP solver; image save C:\Users\george\Documents\N.I.N.A. All equipment configured (mount, camera, focuser, guider, filterwheel, rotator, dome, weather, safety, switch). User fetched profile after PROFILE-REMOVED event.

## 2026-09-27T11:33:33-05:00 — auto

stop_nina: killed running NINA

## 2026-09-27T11:33:44-05:00 — auto

start_nina: launching NINA on real time; launched NINA from C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe
