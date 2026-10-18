---
name: safety-interrupt-recovery
description: Handle automated safety interrupts, sequence halts, and recovery on a new event or interval check. Use to verify NINA is up, check equipment/safety/weather, stow the scope, or decide whether to start a plan or stow.
---

# Safety interrupts, sequence halts, and recovery

Safety model:
- The safety monitor's `is_safe` reflects whether the enclosure (roof/dome) is open. `is_safe=true` means safe to unpark and expose; `is_safe=false` means the enclosure is closed — the scope must remain stowed and acquisition is gated until it becomes safe. This is distinct from weather, which the weather device reports separately.
- Stow is capability-driven: park only if the mount reports `can_park=true`, otherwise home if `can_find_home=true`.

When triggered (new event or interval check):
0. Check if NINA is responsive via `get_nina_status()` first: its `running` field is REST-API-authoritative — if true, NINA is up (a missing local `NINA.exe` process does not mean it is down when NINA is remote). If NINA is not running, look at `lifecycle_control_available` (see the `nina-lifecycle` skill):
   - `true` — this host can control NINA. Call `start_nina()` to launch it into the interactive desktop session, then poll `get_nina_status()`/`get_site_equipment_status()` for up to ~30 s for its REST API to come up.
   - `false` — the MCP server is remote from NINA (e.g. a Mac driving a remote `NINA_ENDPOINT`), so this agent cannot start or stop it. Do not call `start_nina()`; append a `PROGRESS.md` entry and stop.
   If NINA never responds, append a `PROGRESS.md` entry and stop — do not invent equipment state from absence.
1. Read `PROGRESS.md` to restore context (and `PLAN.md` for the night loop). Check current equipment status via `get_site_equipment_status`; confirm sequence state with `get_sequence_state`. Confirm the active observatory profile with `get_site_profile` — if the site, filters, or image path look wrong for the intended site, call `list_site_profiles` to show the user the available profiles and sub-pick the right one; switch only on the user's request with `switch_site_profile(profile_id)` (never while a sequence is running), then re-read `get_site_profile` to confirm.
2. If the safety monitor reports `is_safe=false` or weather limits are violated: do not start or resume acquisition. Ensure the scope is being stowed by the running sequence; otherwise stow it yourself: `load_sequence()` (the empty request list is the stow) then `start_sequence()` (it parks if `can_park`, else homes if `can_find_home`). Never interrupt an in-progress teardown during a stow maneuver unless safety requires it.
3. If no sequence is running and acquisition is not about to start: if a viable target is ready, start its plan (see the `night-loop` skill); otherwise stow with `load_sequence()` + `start_sequence()`. A parked scope needs no guardrails — nothing will unpark it — and the stow sequence is the one sequence that completes promptly (its End area waits at most one bounded 3-minute "On Safe" pass — skipped when the enclosure is already closed — then parks, instead of idling on the enclosure until sunrise), so it leaves an unambiguous "nothing running" state for the next trigger.
4. Keep logs structured and concise. Use `get_events` and `get_logs` to reconstruct what happened before acting. Append a timestamped entry to `PROGRESS.md` after each action.
