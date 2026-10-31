# Tonight — catalog-driven ("shoot whatever is up")

Wishlist source: **the `best_targets` catalog**, not a hand-written list.
The controller picks targets live from `best_targets` and images whatever it
can. No editing of this file is needed night to night.

## Wishlist policy

- The wishlist **is** `best_targets` — call it fresh on every re-selection and
  treat its results as authorized targets. Nothing outside `PLAN.md` gets
  imaged; by delegating the list here, the catalog output *is* `PLAN.md`.
- **No difficulty filter** — shoot `beginner` through `advanced` alike. Take
  everything the catalog returns that clears the floor.
- Take from `visible_now` first. If nothing is visible now, use
  `later_tonight` (`peak_utc` / `peak_alt_deg`) to decide whether to wait for a
  peak or shoot a lower-scoring target in the gap.
- De-duplicate: once a target has been fully shot (`get_plan_progress`
  reports `complete`), do not re-pick it. Re-selection moves down the ranked
  list.
- Named targets below (if any) take priority over catalog picks.

## Named targets

_None — catalog only._

## Auto-generated plan shape

When the controller writes a plan for a catalog target (`write_plan_file`),
use the proven Seestar S30 shape already in `plans/`:

- **Lights:** filter `L`, 10.0 s, 90 frames (15 min per target).
- **Calibration:** 20 × `L` flats, 20 × 10.0 s darks, 20 bias.
- `cooler.on: false`, `batch_size: 10`, `calibration_window_days: 7`.
- `constraints`: from the `Rules` block below.
- `autofocus` / `guiding`: same values as `plans/m15_..._20261003T112008.json`.
- Resolve coordinates via `resolve_target` (SIMBAD) before writing — never
  point from catalog coords alone.
- If `large_for_field` is set on a catalog row, treat it as framing/mosaic
  care rather than a single blind pointing.

## Rules

- min_altitude: 30
- horizon_offset_degrees: 2
- tie_break: score (from `best_targets`), then priority, then altitude, then
  earliest available
