# progress.md

Shared observatory progress file. Both the active session and automated worker sessions read this on start and append a short timestamped entry after each significant action (status checks, plan writes, sequence loads/starts/stops, errors, interventions).

---

## Created

## 2026-09-28T08:40Z — Live test: all sequence frame types

- Stopped running sequence (safety standby in End Sequence Wait Indefinitely).
- Loaded all five frame types from `veil.json` in `full` mode and verified via `get_sequence_state`:
  - **light**: 60× Clear 60 s, full DSO pipeline (slew+center, guiding, AF triggers, meridian flip, drift check, altitude windows).
  - **dark**: 20-iteration simple loop, parked start, no guiding/AF.
  - **bias**: 30-iteration simple loop, parked start.
  - **dawn_flat**: sun -8°→0° window, slew Az 270°/Alt 80°, 30 twilight sky flats in Clear.
  - **dusk_flat**: sun 0°→-8° window, slew Az 90°/Alt 80°, 30 twilight sky flats in Clear.
- All sequences loaded successfully with proper safety guardrails (While Unsafe / On Safe containers, park-on-unsafe, Wait Indefinitely end).
- No errors encountered. NINA responsive throughout.

