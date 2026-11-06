# Tonight — catalog-driven

The wishlist **is** `best_targets`: call it fresh on each re-selection and
shoot whatever it returns above the floor. Nothing outside this file gets
imaged. No editing needed night to night.

## Wishlist policy

- No difficulty filter — take everything that clears the floor.
- `visible_now` first; else `later_tonight` (`peak_utc`/`peak_alt_deg`) — wait
  for the peak or shoot a lower-scoring target in the gap.
- De-dup: a target whose `get_plan_progress` reports `complete` is not
  re-picked; move down the ranked list.
- **Full night, not just to midnight** — when generating a run, queue targets
  to cover the whole night, until darkness ends at dawn (or the enclosure goes
  unsafe); only then end it.
- Named targets (below) beat catalog picks.

## Named targets

_None — catalog only._

## Plan shape (`write_plan_file`, Seestar S30)

- Lights: `L`, 10.0 s, 90 frames. Calibration: 20 × `L` flats, 20 × 10.0 s
  darks, 20 bias.
- `cooler.on: false`, `batch_size: 10`, `calibration_window_days: 7`,
  `constraints` from Rules below, `autofocus`/`guiding` as in
  `plans/m15_..._20261003T112008.json`.
- `large_for_field` → framing/mosaic care, not a single blind pointing.

## Rules

- min_altitude: 30
- horizon_offset_degrees: 2
- tie_break: score (from `best_targets`), then priority, then altitude, then
  earliest available
