---
name: site-conditions
description: Read the 7-day hourly forecast for the observatory site from clearoutside.com -- cloud cover, wind, precipitation probability, temperature/dew point, frost chance, Bortle class and astro-dark windows. Use when deciding whether to start or continue sky-dependent imaging, when picking between targets on a forecast-driven night, when planning dawn/dusk flats, or when the user asks about cloud cover, transparency, wind, dew or frost ahead. Advisory only -- never a substitute for is_safe.
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
python3 .agents/skills/site-conditions/fetch_conditions.py <lat> <lon> --hours 4
```

Get the coordinates from `get_site_profile()` — never hardcode them, and never call `resolve_target`, which is for object names only. Flags: `--threshold PCT` (default 20), `--night-only` (restrict both sections to astro-dark hours), `--view {current,midnight,midday}` (default `current`), `--hours N` (report only the first N hourly columns), `--max-wind` (default 12), `--max-precip` (default 40), `--min-dew-spread` (default 2 °C), `--json`, `--html FILE` (parse a saved page, no network).

`--view` sets where each row's 24 columns start: `current` = your current local hour, `midnight` = 00:00, `midday` = 12:00. **Use `current` for a live night loop** — it is also the site's own default, but it is passed explicitly so the window cannot drift if their default changes. It changes only how the row is *sliced*; darkness, cloud values and dates are identical under every view.

`--hours N` truncates the **report** (text and JSON) to the first N hourly columns of the flattened day rows — a continuous chronological sequence under every view — so with the default `current` that is exactly the next N hours from now, and the report says `Window: first N hourly columns`. It is a display slice only: the page is still fetched and parsed in full, so the parser invariants below are untouched, and days that fall entirely outside the window are **omitted** rather than printed as `no forecast data` (which means *absent from the page* — a different claim). The clear-hours denominator becomes the window length: `Sat 7 · 3/4h` under `--hours 4` means "3 of the next 4 hours", not a 4-hour calendar day. Use it for the near-term *keep-shooting* verdict on an interval check; leave it off for start-of-night planning, "next clear window" hunting and dawn-flat scheduling, which all need hours beyond a short horizon.

Two consequences of `current`, both observed rather than assumed:

- **Coordinates must be 2 decimal places or the view silently breaks.** Full precision — `31.54738/-99.382423`, exactly what `get_site_profile()` returns — is 301-redirected to `/forecast/31.55/-99.38`, **and that redirect drops the query string**, so `?view=midnight` came back as `current`. Measured both ways on 2026-11-07: the 2-dp URL returned rows starting `00` / `12` / `22` for midnight / midday / current; the 6-dp URL returned `22` for **all three**, even with `Cache-Control: no-cache` and a random query-buster ruling out caching. `_fmt_coord()` normalises before the URL is built, and `fetch()` warns if the final URL lacks `view=` when one was requested. 0.01° ≈ 1.1 km — irrelevant to a forecast grid, and the site normalises the same way itself (`/annual_darkness/50.70/-3.52`).
- **Past hours are never counted** under `current`: the row is `[now, now+24h)`, so elapsed hours are absent rather than included — though the *current* hour is in it, partly elapsed. Under an honoured `midnight`/`midday` view today's row would include hours already gone. Either way the fraction's denominator is the **row length**, and the row crosses midnight, so `Sat 7 · 14/24h` means "the next 24 hours from now", not "calendar Saturday".
- **View changes only the slicing, never the values** — verified by keying every column to its *absolute* date+hour (a `current` block spans two calendar days, so the block's own date is the wrong key) and comparing across all three views: **146 and 158 shared instants, zero disagreements** in `astro_dark`, cloud, temperature, frost, wind or precip. Coverage differs (164 vs 168 keys) because the windows start at different hours; what overlaps agrees exactly.
- **Diff your flags before you suspect the forecast.** The parser is deterministic — parsing the same bytes twice yields identical output, and three back-to-back fetches hashed the same — so a swing in the clear-hours line is nearly always your own invocation. A `Wed 11` line went `21/24h → 7/24h` and looked like the sky had closed up; it was `--night-only` dropping exactly the 14 daytime hours `06-19` — and the same flag now shrinks the Conditions counts the same way. `--threshold`, `--hours` (which drops whole days from the output) and the wind/precip/dew flags move rows the same way. The page does carry an hourly-updating forecast, so don't cache one for long — but re-run it with the *same* flags before concluding anything changed.

Stdlib only — no pip install. Exit `0` ok, `1` fetch/parse failure, `2` bad usage. **Exit 1 is not a reason to stall the night**: log it, and fall back to `get_site_equipment_status` for current conditions plus `sun_times` for darkness. Never block on this network call.

## Reading the output

A sample from one moment on 2026-11-07 (your hours **will** differ — see the sliding-window note above; the *shape* is what matters):

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

## Reading the conditions block

A second section answers *"will it stay dry and still enough to keep shooting"* — reported **exceptions-first**, one line per night with its worst values:

```
Conditions  (wind>=12mph · precip>=40% · dew-spread<=2°C · any frost)
  Sun  8  ⚠ wind 24/24h max28 · precip 7/24h max60% · dew 24/24h min1°C · frost 24/24h
  Wed 11  ✓ wind max8 · precip max0% · dew min4°C · no frost
```

`⚠` = at least one breach; the counts are hours breaching, `max`/`min` are the worst value **regardless of breach**. A `✓` line still reports its worst values, so you can see how close it came.

**What window those numbers cover**: by default the whole 24-column row — **daytime hours included**, so a `wind max` can be tomorrow afternoon and `precip 13/24h` can be entirely daytime rain that has nothing to do with your session. `--night-only` restricts this block to astro-dark hours too: the header says `astro-dark hours only`, counts read over the dark hours actually in the row (`wind 1/10h`), and `max`/`min` become the *session's* worst values. A row that has columns but no dark hours prints `— no astro-dark hours` — which is **not** `no forecast data` (that means the page itself was empty, invariant 3). Note the two sections keep different denominators on purpose: the clear-hours line's denominator stays the row length under `--night-only` (the documented `21/24h → 7/24h` behaviour), while the conditions counts divide by the dark-hour count.

**`dew` and `frost` are reported separately because they are different questions** — never substitute one for the other:

- **`frost`** comes from the page's own `Chance of Frost` flag — a binary `title`, never a probability. It needs **freezing**: across 453 sampled hours it only ever appeared between −17 °C and +2 °C, and on mixed days the boundary sits at **2 °C** with dew-point spread breaking the tie (Kiruna: frost up to 1 °C, no-frost from 2 °C; Denver: frost ≤2 °C). The small margin above 0 is ground frost — the surface radiatively cools below air temperature on a calm clear night. **Read the flag; do not recompute it** — it is the model's verdict and your spread-only reconstruction would be wrong (164 false frosts).
- **`dew`** is `temperature − dew_point`, derived — the page has no such row. It is pure **saturation** and needs no freezing: Texas showed `dew 21/24h min0°C · no frost` at **+12 °C**, i.e. the corrector will fog all night while nothing freezes.

Measured, not assumed: **spread alone called 164 false frosts — every one of them above freezing.** So the page's frost flag is authoritative for frost, and spread is the number for optics. A tight `dew` is a *dew-heater* decision, not a stop-imaging one.

Thresholds are **defaults, not physics** — tune `--max-wind` to your mount and `--min-dew-spread` to what your dew heaters actually hold (tighten to `1` for "optics will fog", loosen to `3` for "heaters merely strained"). And note these are forecasts: a live reading in `get_site_equipment_status().weather` beats any of them for *right now*.

## Division of labour — do not re-fetch what is already known

| Fact | Source | Why not here |
|---|---|---|
| Bortle class, SQM | shown in output | static per site; the *estimate* is only worth quoting once, not re-fetched each check |
| Astro dark for **tonight** | `sun_times()` | authoritative, site-computed, works offline |
| Live cloud cover, humidity, dew point | `get_site_equipment_status().weather` | a sensor reading beats a model, and it is *now* |
| Enclosure / roof state | `safety_monitor.is_safe` | the only safety gate |
| **Hourly cloud forecast, 7 days ahead** | **this script** | **the only forward-looking weather NINA can supply nothing for** |
| **Wind, precip, temperature, dew point, frost — ahead** | **this script** | same reasoning: all five exist *live* in `…weather`, but only the forecast says what they'll do at 03:00 |
| **Moon phase + illumination** | `moon_times()` | site-computed, works offline, and it also returns rise/set and the next four phase instants — see the caveat below for the one case where the script still wins |
| **Which of the next 7 nights is moonlit** | **this script** | `moon_times()` answers *now* (+48 h of rise/set, and phase instants out to ~32 d); only the forecast carries a ready **per-day illumination %** across a full week — the phase instants let you infer it, but the script states it |

Two caveats on the moon rows: **`moon_times()` is the authoritative lunar source** — phase name, `illumination_fraction`, `age_days`, current altitude/azimuth/distance, `moon_up_now`, and the next `moonrise`/`moonset` plus `new_moon`/`first_quarter`/`full_moon`/`last_quarter`, all UTC and all offline. It replaces the script for everything except the week-ahead glance: its rise/set window is 48 h, so for "which night next week" the script's daily illumination column is still the only source, and its astro-dark column covers all 7 days where `sun_times()` covers only the current/next window — for tonight, prefer `sun_times()`. Neither tool parses a lunar *meridian* passage (time/altitude at transit); for the Moon's position right now use `moon_times().altitude_deg`.

Cross-check the two weather sources when they disagree sharply: a live sensor at 95% cloud against a forecast of 0% means the forecast is stale or wrong — trust the sensor, and treat the forecast as suspect for the rest of the night.

## Maintenance invariants

The parser is regex over server-rendered HTML, so Clear Outside *will* break it eventually. Before changing anything, run against saved pages with `--html` and confirm the day counts and the clear-hours lines still match. **Test three fixtures, because they fail in different ways:**

- your own site — the normal case, mild temps, frost absent
- **`80.00 -3.52` (Jan Mayen)** — the adversarial case: overcast 7/7, temps −14…−7 °C, frost *present* every hour, wind 23–28 mph, polar-latitude twilight. One page exercising negatives, the frost-positive state, and the empty last day at once
- a saved page from a different month, so the rolling-window and date-anchor logic see a different generation hour

**Fetch rule, before any of it: coordinates go out at 2 decimal places.** Passing `get_site_profile()`'s raw precision triggers a redirect that strips `?view=`, silently substituting the default window. Normalise in `_fmt_coord()`, and warn when the final URL has no `view=` but one was asked for — a `--view midnight` that quietly returns `current` is worse than no flag at all.

Six invariants that must survive any rewrite:

1. Hour labels come from `fc_hour_ratings`, which precedes the detail rows — read them, never synthesise `00…23`, and derive each column's calendar date from them.
2. Cloud values come from the `<li>` **text**, never the class name.
3. A day with zero columns is `has_data: false`, not an error and not a clear night.
4. **Any row that can go negative MUST use `(-?\d+)`.** `(\d+)` returns `[]` for `-14` — not `"-14"` — and an empty list reads as *row missing*, which then reports the whole day as no-data. On Jan Mayen, where it is −14 °C all week, the unsigned pattern would have printed `no forecast data` for seven fully populated days: a confident answer, entirely backwards. This is the bug that page exists to catch.
5. **Frost is a `title`, not a value** — the `<li>` is always empty. Exactly **two** states exist, verified across 12 sites from tropics to polar, both hemispheres: `title="No Frost"` and `title="Chance of Frost"`. Despite the word "Chance" there is **no probability and no third state** — "or otherwise" does not exist. The `class` is a parallel encoding that always agrees (`fc_none` ↔ No Frost, `climacon snowflake` ↔ Chance of Frost), and `class` preceded `title` in **8/8 raw samples** — so ordering is currently uniform, but read `title` out of the attribute *string* anyway and never code against an assumed order. Days are frequently **mixed** (Kiruna Sun 8 = 4/24 frost, Denver Sat 7 = 6/24), so evaluate **per cell**, never per day; map anything unrecognised to `None` rather than guessing, and keep `unknown=0` on real data as the health check.
6. **Wind's speed is in a nested `<span>`**, so tags must be stripped — a bare-text cell pattern returns `[]` for the whole row. Direction and degrees live only in `title="…mph from the X (NNN°)"` and are **deliberately not extracted**: every threshold and every output line runs on speed, and nothing here consumes a bearing. The title is read purely as a fallback for the speed itself, so an empty span still yields a value instead of silently hiding wind from the whole forecast.

A `ParseError` naming "hour labels vs cloud values" means the markup shifted: re-inspect the page and update the regexes rather than loosening the check — a silent mismatch would misalign every hour of the forecast. The same reasoning covers the condition rows: `_fit` pads short rows at the **end** (keeping every cell at its own hour) and prints `row X has N cells for M hour labels` to stderr if a row is present but short. **Treat that warning as a parse failure** — it means a cell vanished mid-row and the values after it no longer belong to the hours beside them.
