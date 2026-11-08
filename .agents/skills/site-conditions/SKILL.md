---
name: site-conditions
description: Read the 7-day hourly cloud forecast for the observatory site from clearoutside.com, plus Bortle class and astro-dark windows. Use when deciding whether to start or continue sky-dependent imaging, when picking between targets on a forecast-driven night, when planning dawn/dusk flats, or when the user asks about cloud cover, transparency or seeing ahead. Advisory only -- never a substitute for is_safe.
---

# Site conditions (Clear Outside)

Forward-looking weather for the site — what the sky will do over the next seven days. This is the one thing N.I.N.A. cannot tell you: the live weather device reports **only now** (`get_site_equipment_status().weather.cloud_cover_pct`), so a forecast is what lets you decide *whether tonight is worth starting* rather than *whether to abort*.

⚠️ **Advisory only.** `is_safe` from the safety monitor is the sole gate on starting or resuming sky-dependent work. A forecast never overrides it, never unparks, and never justifies resuming a sequence — a forecast can be wrong, an enclosure sensor is not.

## Running it

```bash
# lat/lon come from get_site_profile() -> site.latitude_deg / site.longitude_deg.
# <lat>/<lon> below are PLACEHOLDERS: substitute the active profile's values.
# Never paste a literal coordinate here -- a stale one forecasts the wrong sky.
python3 .agents/skills/site-conditions/fetch_conditions.py <lat> <lon>
python3 .agents/skills/site-conditions/fetch_conditions.py <lat> <lon> --night-only
python3 .agents/skills/site-conditions/fetch_conditions.py <lat> <lon> --json
```

Get the coordinates from `get_site_profile()` — never hardcode them, and never call `resolve_target`, which is for object names only. Flags: `--threshold PCT` (default 20), `--night-only` (drop non-dark hours), `--view {current,midnight,midday}` (default `current`), `--json`, `--html FILE` (parse a saved page, no network).

`--view` sets where each row's 24 columns start: `current` = your current local hour, `midnight` = 00:00, `midday` = 12:00. **Use `current` for a live night loop** — it is also the site's own default, but it is passed explicitly so the window cannot drift if their default changes. It changes only how the row is *sliced*; darkness, cloud values and dates are identical under every view.

Stdlib only — no pip install. Exit `0` ok, `1` fetch/parse failure, `2` bad usage. **Exit 1 is not a reason to stall the night**: log it, and fall back to `get_site_equipment_status` for current conditions plus `sun_times` for darkness. Never block on this network call.

## Reading the output

```
Tue 10  astro 19:04→05:38   clear: 08-11  (4/24h)
Wed 11  astro 19:03→05:39   clear: 21*,23*,01-03*,05*,06-19  (20/24h, 6 dark)
Thu 12  astro 19:03→05:40   clear: 20-05*,06-19  (24/24h, 10 dark)
Fri 13  astro 19:03→05:40   clear: 20-05*,06-07  (12/22h, 10 dark)
```

- **`*` marks a whole run, not one hour.** `20-05*` = 20:00 through 05:00, all of it inside astro dark. A run never mixes dark and light hours, so a star always describes everything it follows.
- **Hour labels are a rolling window, not midnight-to-midnight.** Where the row starts depends on `--view` — `current` began at `20` on 2026-11-07, `midnight` at `00`, `midday` at `12` — and it runs chronologically across the day boundary, so trailing `00`/`01` belong to the *next* calendar day. The script reads the labels and derives each column's calendar date from them; **never assume `00…23`, and never infer darkness from a column's position in the row.**
- **The last day is often an empty shell** when the 7-day window ends mid-day. `no forecast data` means *absent*, not *clear* — never read it as zero cloud.
- **The site's `fc_cl_N` class is inverted** — `fc_cl_0` accompanies the value `100` because the class is *clearness* while the text is *cloud cover*. The script uses the text only; if you ever hand-parse, trusting the class inverts every value.
- **Hour labels are site-local.** The `Timezone: UTC±X.XX` on the `Generated:` line is the offset already applied. To compare with `sun_times()` (which returns UTC), subtract that offset.

## Division of labour — do not re-fetch what is already known

| Fact | Source | Why not here |
|---|---|---|
| Bortle class, SQM | shown in output | static per site; the *estimate* is only worth quoting once, not re-fetched each check |
| Astro dark for **tonight** | `sun_times()` | authoritative, site-computed, works offline |
| Live cloud cover, humidity, dew point | `get_site_equipment_status().weather` | a sensor reading beats a model, and it is *now* |
| Enclosure / roof state | `safety_monitor.is_safe` | the only safety gate |
| **Hourly cloud forecast, 7 days ahead** | **this script** | **the only forward-looking weather NINA can supply nothing for** |
| **Moon phase + illumination** | **this script** | **no N.I.N.A. tool returns lunar data at all** — `sun_times` is sun-only (`grep -i moon` over `nina_planner` = 0 matches) |

Two caveats on the moon row: rise/set and meridian (time, altitude, distance) are on the page but **not yet parsed** — only phase name and illumination % come back. And the script's astro-dark column covers all 7 days where `sun_times()` covers only the current/next window; for tonight, prefer `sun_times()`.

Cross-check the two weather sources when they disagree sharply: a live sensor at 95% cloud against a forecast of 0% means the forecast is stale or wrong — trust the sensor, and treat the forecast as suspect for the rest of the night.

## Maintenance invariants

The parser is regex over server-rendered HTML, so Clear Outside *will* break it eventually. Before changing anything, run against a saved page with `--html` and confirm the day counts and the `Fri 13` line still match. Three invariants that must survive any rewrite:

1. Hour labels come from `fc_hour_ratings`, which precedes the detail rows — read them, never synthesise `00…23`.
2. Cloud values come from the `<li>` **text**, never the class name.
3. A day with zero columns is `has_data: false`, not an error and not a clear night.

A `ParseError` naming "hour labels vs cloud values" means the markup shifted: re-inspect the page and update the regexes rather than loosening the check — a silent mismatch would misalign every hour of the forecast.
