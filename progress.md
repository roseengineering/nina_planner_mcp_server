# progress.md

Shared observatory progress file. Both the active session and automated worker sessions read this on start and append a short timestamped entry after each significant action (status checks, plan writes, sequence loads/starts/stops, errors, interventions).

---

## Created



## 2026-09-28T21:56:28-05:00 — auto

start_nina: launching NINA on real time; launched NINA from C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\NINA.exe

## 2026-09-28T21:58:00-05:00 — session

get_site_profile after start_nina: profile "Default", site 31.5475/-99.381667 @470m, optics 500mm f/5, filters Red/Green/Blue/Clear, ASTAP solver, image_save_path=C:\Users\george\Documents\N.I.N.A, file_pattern includes $$TARGETNAME$$. No sequence started; NINA left running.

Code change verified: ObservatoryProfile now derives local_image_save_path -> /mnt/c/Users/george/Documents/N.I.N.A (NINA_DRIVE_MOUNT unset). The in-session MCP server still runs pre-edit code, so its get_site_profile response omits the field until the server is restarted.

## 2026-09-28T22:02:00-05:00 — session

get_site_profile now returns local_image_save_path=/mnt/c/Users/george/Documents/N.I.N.A (MCP server picked up the derived field). Read-only check; no sequence changes. NINA still running (pid 7380).

## 2026-09-28T22:15:00-05:00 — session

Code change (no observatory action): light-sequence target names now embed the plan id in parentheses — `Veil Nebula (plan-6a857ee34c32-1)` — instead of square brackets, because NINA mangles brackets. Attribution matching accepts both forms (`attribution_tokens`), so frames already on disk under `Veil Nebula [plan-67caecf9238e-1]/...` still count. 522 tests pass; ruff/mypy clean. MCP server restart required for the new target naming to take effect.

Observed while verifying: get_plan_progress(veil.json) reports lights 0/60 acquired although 74 light frames exist under `Veil Nebula [plan-67caecf9238e-1]/2026-09-27/LIGHT/`. Cause is unrelated to the brackets — the profile FilePattern is `$$TARGETNAME$$\$$DATEMINUS12$$\$$IMAGETYPE$$\...`, so the plan-id token lands in an ancestor directory of LIGHT, and `_light_target_matches` only inspects the filename and the first directory under LIGHT.

## 2026-09-28T22:25:00-05:00 — session

Follow-up code change (user-approved): `_light_target_matches` now accepts the plan-id token in any path segment instead of only the filename / first directory under LIGHT, so the profile's `$$TARGETNAME$$\$$DATEMINUS12$$\$$IMAGETYPE$$\...` FilePattern attributes correctly. Verified against the real imaging dir: veil.json (`plan-67caecf9238e-1`, legacy bracket folder) now reports lights 74 acquired / 60 total, remaining 0 — previously 0 acquired. Tokens are unique per plan pointing, so ancestor matching cannot cross-attribute. 522 tests pass; ruff/mypy clean. MCP server restart required for both changes to take effect.

Observatory state during this work: safety monitor is_safe=false (enclosure closed), mount parked, dome parked/shutter closed, no sequence running. No acquisition attempted; NINA left running (pid 7380).

## 2026-09-28T22:40:00-05:00 — session

Removed the legacy square-bracket attribution match at the user's request: `ObservationPlan.attribution_tokens` deleted, `_light_target_matches` now matches only `attribution_token` — `(plan-<id>-<n>)`. Consequence: the 74 light frames already on disk under `Veil Nebula [plan-67caecf9238e-1]/2026-09-27/LIGHT/` no longer attribute, so veil.json reports lights 0/60 (remaining 60) and mode="remaining" would re-shoot them. Simulated a rename of that folder to `Veil Nebula (plan-67caecf9238e-1)` -> 74 acquired / 0 remaining, so renaming restores attribution without re-imaging. No rename performed. 522 tests pass; ruff/mypy clean.
