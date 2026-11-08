#!/usr/bin/env python3
"""Fetch and parse the Clear Outside 7-day hourly forecast for an observing site.

Stdlib only -- no third-party dependencies. Companion to the `site-conditions`
skill; see SKILL.md before changing the parsing rules.

    ./fetch_conditions.py <lat> <lon>
    ./fetch_conditions.py <lat> <lon> --night-only
    ./fetch_conditions.py <lat> <lon> --json
    ./fetch_conditions.py <lat> <lon> --hours 4            # near-term window
    ./fetch_conditions.py <lat> <lon> --html saved.html    # parse offline

<lat>/<lon> are read from the active profile (get_site_profile() ->
site.latitude_deg / site.longitude_deg). Never hardcode them here: a stale
literal silently forecasts the wrong sky.

Exit codes: 0 ok, 1 fetch/parse failure, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE_URL = "https://clearoutside.com/forecast"
USER_AGENT = "Mozilla/5.0 (observatory-controller; site-conditions skill)"

# --- site header -----------------------------------------------------------
RE_LOCATION = re.compile(r"<h1>Forecast for (.*?)</h1>")
RE_GENERATED = re.compile(r"<h2>Generated:(.*?)</h2>")
# "Forecast: 07/11/26 to 13/11/26" -- dd/mm/yy. Anchors block 0 to a real
# calendar date so darkness can be resolved by date rather than block position.
RE_FORECAST_RANGE = re.compile(
    r"Forecast:\s*(\d{2})/(\d{2})/(\d{2})\s*to\s*(\d{2})/(\d{2})/(\d{2})"
)
RE_SKY_QUALITY = re.compile(
    r"Est\. Sky Quality:.*?<strong>([\d.]+)</strong> Magnitude\."
    r".*?<strong>Class (\d+)</strong> Bortle",
    re.S,
)

# --- per-day blocks --------------------------------------------------------
RE_DAY_SPLIT = re.compile(r'<div class="fc_day" id="day_(\d+)">')
RE_DAY_DATE = re.compile(r'<div class="fc_day_date"[^>]*><span>(\w+)</span> (\d+)')
RE_ASTRO = re.compile(r"Astro Dark:\s*(\d{2}:\d{2}) - (\d{2}:\d{2})")
RE_MOON = re.compile(
    r'<span class="fc_moon_phase">([^<]+)</span>.*?'
    r'<span class="fc_moon_percentage">(\d+)%</span>',
    re.S,
)
# The hour labels live in their own list BEFORE the detail rows. They are a
# rolling window starting at the generation hour (02..01), NOT 00..23 -- always
# read them rather than assuming midnight alignment.
RE_HOURS = re.compile(r'<div class="fc_hours fc_hour_ratings">\s*<ul>(.*?)</ul>', re.S)
RE_HOUR_LABEL = re.compile(
    r'<span class="glyphicon glyphicon-time"></span>\s*(\d{2})\s*<span>'
)

# NOTE: the <li> CLASS here is clearness (fc_cl_0 = 0% clear), while the TEXT is
# cloud cover. Trust the text only; reading the class inverts every value.
RE_TOTAL_CLOUDS = re.compile(
    r"<span>Total Clouds \(% Sky Obscured\)</span>.*?<ul>(.*?)</ul>", re.S
)

# SIGNED on purpose. `(\d+)` returns [] for "-14", not "-14" -- an empty list
# that then reads as "row missing" and reports the whole day as no-data. Proven
# on 80.00/-3.52 (Jan Mayen), where temperature is -14..-17 every hour: the
# unsigned pattern would have reported a total forecast blackout on a fully
# populated page. Any row that can go negative MUST use this pattern.
RE_INT_CELL = re.compile(r"<li[^>]*>(-?\d+)</li>")

# Generic cell access for rows whose value is NOT bare text in the <li>:
#   wind  -> value is inside a nested <span>, so tags must be stripped to read
#            the speed; the title is only a fallback for the speed itself
#   frost -> <li> is EMPTY, state is in title; and the two states differ in
#            both class spelling and ATTRIBUTE ORDER (class-then-title at one
#            site, title-then-class at another), so read attrs as a string.
RE_LI = re.compile(r"<li\b([^>]*)>(.*?)</li>", re.S)
RE_TITLE = re.compile(r'title="([^"]*)"')
# Speed only. Direction/degrees are deliberately NOT extracted: every threshold
# and every output line runs on speed, and nothing in this skill consumes a
# bearing. Add it back if crosswind-vs-pointing or front-arrival logic ever
# needs it.
RE_WIND_TITLE_SPEED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mph|km/h)")
RE_WIND_UNIT = re.compile(r"Wind Speed/Direction \(([^)]+)\)")

# Row labels as patterns: the unit inside the wind label changes with the
# site's unit setting (mph vs km/h), so match the shape, not the literal.
ROW_PRECIP = r"Precipitation Probability \(%\)"
ROW_TEMP = r"Temperature \(&deg;C\)"
ROW_DEW = r"Dew Point \(&deg;C\)"
ROW_FROST = r"Chance of Frost"
ROW_WIND = r"Wind Speed/Direction \([^)]+\)"


class FetchError(Exception):
    pass


class ParseError(Exception):
    pass


def _fmt_coord(value: str) -> str:
    """Render a coordinate at 2 decimal places -- the site's canonical form.

    Full precision is 301-redirected to the 2-dp URL and that redirect DROPS
    the query string, so `?view=midnight` silently came back as `current`.
    Measured 2026-11-07: `/forecast/31.54738/-99.382423?view=midnight`
    -> `/forecast/31.55/-99.38` (no query), while the 2-dp URL honoured the
    view. 0.01 deg is ~1.1 km -- irrelevant to a forecast grid, and the site
    performs this same normalisation itself.
    """
    try:
        v = round(float(value), 2)
    except (TypeError, ValueError):
        return str(value)
    if v == 0:  # suppress "-0.00"
        v = 0.0
    return f"{v:.2f}"


def fetch(lat: str, lon: str, view: str = "current") -> tuple[str, str]:
    """Download the forecast page. Returns (url, html).

    `view` sets where each row's 24 columns start: `current` at the current
    local hour, `midnight` at 00:00, `midday` at 12:00. Passed explicitly
    even though the site already defaults to `current`, so the window can
    never drift if their default changes -- but it only survives if the
    coordinates are already canonical (see _fmt_coord).
    """
    url = (
        f"{BASE_URL}/{_fmt_coord(lat)}/{_fmt_coord(lon)}"
        f"?view={view if view in ('current', 'midnight', 'midday') else 'current'}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            final = resp.geturl()
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError(f"network failure fetching {url}: {exc}") from exc

    # A redirect that strips ?view= would silently substitute the default view
    # for the one asked for. Loud here rather than a quietly wrong row later.
    if "view=" not in final and "view=" in url:
        print(
            f"site-conditions: redirected to {final} -- the view parameter was "
            f"dropped, so the window may not be '{view}'",
            file=sys.stderr,
        )
    if "Forecast for" not in body:
        raise ParseError(f"{url} did not return a Clear Outside forecast page")
    return url, body


def _mins(hhmm: str | None) -> int | None:
    """'18:30' -> 1110. None stays None (polar / unparseable)."""
    if not hhmm:
        return None
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _is_dark(hour: int, day: date | None, starts: dict, ends: dict) -> bool | None:
    """Is this hour column inside astronomical darkness?

    Resolve by CALENDAR DATE, never by position in the block: where the
    midnight wrap falls depends on `view` (current/midnight/midday all start
    the row at a different hour), so a positional rule silently misattributes
    hours the moment the view changes.

    Astro dark for day D spans [start_D, midnight) on D plus [00:00, end_D] on
    D+1. So a column falling on date D is dark when it is >= start_D (that
    night's dusk) OR <= end_{D-1} (the tail of the previous night).

    Returns None only when neither boundary is known -- the column sits outside
    every night the page describes.
    """
    if day is None:
        return None
    s = _mins(starts.get(day))
    e = _mins(ends.get(day - timedelta(days=1)))
    if e is None:
        # The forecast's first morning: end_{D-1} predates the window. Astro
        # dawn drifts only minutes across a week, so that same day's own end
        # is a sound stand-in rather than leaving the hours unknown.
        e = _mins(ends.get(day))
    if s is None and e is None:
        return None
    conds = []
    if s is not None:
        conds.append(hour * 60 >= s)
    if e is not None:
        conds.append(hour * 60 <= e)
    return any(conds)


def _fmt_run(first: str, last: str, dark: bool) -> str:
    text = first if first == last else f"{first}-{last}"
    return text + ("*" if dark else "")


def _compress(pairs: list[tuple[str, bool]]) -> str:
    """[('02', True), ('03', True), ..., ('06', False)] -> '02-05*,06-...'.

    Adjacency is positional: 23 -> 00 continues a run even though it wraps,
    because the block's column order is chronological. A run only spans hours
    that share the same darkness flag, so a trailing '*' always describes the
    whole run it follows -- never just the last hour.
    """
    if not pairs:
        return "—"
    runs: list[str] = []
    start_lab, start_dark = pairs[0]
    prev_lab = start_lab
    for lab, dark in pairs[1:]:
        p, c = int(prev_lab), int(lab)
        contiguous = c == p + 1 or (p == 23 and c == 0)
        if contiguous and dark == start_dark:
            prev_lab = lab
            continue
        runs.append(_fmt_run(start_lab, prev_lab, start_dark))
        start_lab, start_dark, prev_lab = lab, dark, lab
    runs.append(_fmt_run(start_lab, prev_lab, start_dark))
    return ",".join(runs)


def _row_cells(block: str, label: str) -> list[tuple[str, str | None]] | None:
    """Return [(text, title)] per <li> for the named row, or None if absent.

    `label` is a regex fragment matched against the row's label span. The
    attribute string is handed back verbatim so callers can read `title`
    without assuming attribute order.
    """
    m = re.search(
        r'<span class="fc_detail_label"><span>' + label + r"</span></span>"
        r".*?<ul>(.*?)</ul>",
        block,
        re.S,
    )
    if not m:
        return None
    cells: list[tuple[str, str | None]] = []
    for attrs, inner in RE_LI.findall(m.group(1)):
        t = RE_TITLE.search(attrs)
        cells.append((re.sub(r"<[^>]+>", "", inner).strip(), t.group(1) if t else None))
    return cells


def _fit(seq: list, n: int, name: str = "") -> list:
    """Align a row to the hour columns: pad short, truncate long, all-None
    when the row is missing entirely.

    A present-but-short row is suspicious: padding appends at the END, which
    keeps every cell at its correct hour, but a markup change that dropped a
    cell mid-row would misalign the rest. Loudly report it rather than let the
    hours silently disagree with the values beside them.
    """
    if not seq:
        return [None] * n
    if 0 < len(seq) < n:
        print(
            f"site-conditions: row {name or '?'} has {len(seq)} cells for "
            f"{n} hour labels -- check alignment",
            file=sys.stderr,
        )
    return (list(seq) + [None] * n)[:n]


def _row_ints(cells: list[tuple[str, str | None]] | None, n: int, name: str) -> list:
    out = []
    for text, _ in cells or []:
        try:
            out.append(int(text))
        except ValueError:
            out.append(None)
    return _fit(out, n, name)


def _row_frost(cells: list[tuple[str, str | None]] | None, n: int, name: str) -> list:
    """Frost is a title, not a value: 'No Frost' vs 'Chance of Frost'."""
    out = []
    for _, title in cells or []:
        if title is None:
            out.append(None)
        elif title.strip().lower() == "no frost":
            out.append(False)
        elif "frost" in title.lower():
            out.append(True)
        else:
            out.append(None)
    return _fit(out, n, name)


def _row_wind(cells: list[tuple[str, str | None]] | None, n: int, name: str) -> list:
    """Wind speed only -- see RE_WIND_TITLE_SPEED for why direction is dropped.

    Cell text is the rendered speed; the title is a fallback for the case where
    the nested span is absent, so an empty cell degrades to a real value rather
    than silently hiding wind from the whole forecast.
    """
    speeds: list = []
    for text, title in cells or []:
        speed: object | None = None
        try:
            speed = int(text)
        except ValueError:
            pass
        if speed is None and title:
            m = RE_WIND_TITLE_SPEED.search(title)
            if m:
                speed = float(m.group(1))
                if speed == int(speed):
                    speed = int(speed)
        speeds.append(speed)
    return _fit(speeds, n, name)


def parse(html: str) -> dict:
    """Parse a Clear Outside forecast page into a dict (see SKILL.md)."""
    loc = RE_LOCATION.search(html)
    if not loc:
        raise ParseError("no 'Forecast for <site>' header -- not a forecast page")

    gen = RE_GENERATED.search(html)
    generated = " ".join(gen.group(1).split()) if gen else None

    sq = RE_SKY_QUALITY.search(html)
    sky_quality = (
        {
            "magnitude": float(sq.group(1)),
            "bortle_class": int(sq.group(2)),
        }
        if sq
        else None
    )

    parts = RE_DAY_SPLIT.split(html)
    if len(parts) < 3:
        raise ParseError("no day blocks found in forecast page")
    blocks = dict(zip(parts[1::2], parts[2::2]))

    # Anchor block 0 to a real calendar date from the forecast range; block i
    # is start_date + i days. Darkness is then resolved by date, which is the
    # only thing that stays correct across every `view`.
    fr = RE_FORECAST_RANGE.search(html)
    start_date: date | None = None
    if fr:
        try:
            start_date = date(
                2000 + int(fr.group(3)), int(fr.group(2)), int(fr.group(1))
            )
        except ValueError:
            start_date = None

    days: list[dict] = []
    for key in sorted(blocks, key=int):
        block = blocks[key]
        date_m = RE_DAY_DATE.search(block)
        astro = RE_ASTRO.search(block)
        moon = RE_MOON.search(block)

        hours_m = RE_HOURS.search(block)
        hour_labels = RE_HOUR_LABEL.findall(hours_m.group(1)) if hours_m else []

        clouds_m = RE_TOTAL_CLOUDS.search(block)
        cloud_values = (
            [int(v) for v in RE_INT_CELL.findall(clouds_m.group(1))] if clouds_m else []
        )

        # The last forecast day can be an empty shell (<ul></ul>) when the
        # 7-day window ends mid-day. That is missing data, not a parse error.
        has_data = bool(hour_labels) and bool(cloud_values)
        if has_data and len(hour_labels) != len(cloud_values):
            raise ParseError(
                f"day {key}: {len(hour_labels)} hour labels vs "
                f"{len(cloud_values)} cloud values -- markup changed"
            )

        start = astro.group(1) if astro else None
        end = astro.group(2) if astro else None
        idx = int(key)

        # Condition rows for this block, aligned to the hour columns later.
        n = len(hour_labels)
        wind_unit_m = RE_WIND_UNIT.search(block)
        w_spd = _row_wind(_row_cells(block, ROW_WIND), n, f"wind (day {idx})")
        cond = {
            "precip_probability": _row_ints(
                _row_cells(block, ROW_PRECIP), n, f"precip (day {idx})"
            ),
            "temperature_c": _row_ints(
                _row_cells(block, ROW_TEMP), n, f"temperature (day {idx})"
            ),
            "dew_point_c": _row_ints(
                _row_cells(block, ROW_DEW), n, f"dew point (day {idx})"
            ),
            "frost": _row_frost(_row_cells(block, ROW_FROST), n, f"frost (day {idx})"),
            "wind_speed": w_spd,
        }
        wind_unit = wind_unit_m.group(1) if wind_unit_m else None

        block_date = start_date + timedelta(days=idx) if start_date else None
        label_dow = date_m.group(1) if date_m else None
        label_dom = date_m.group(2) if date_m else None
        if block_date and label_dom and block_date.day != int(label_dom):
            # Anchoring disagrees with the page's own date label. Refuse to
            # guess: unknown dates degrade to astro_dark=null, which shows as
            # "unknown" rather than a confidently wrong darkness flag.
            print(
                f"site-conditions: date anchor mismatch at day {idx} "
                f"(derived {block_date.isoformat()}, page says day {label_dom}) "
                f"-- darkness will read unknown",
                file=sys.stderr,
            )
            block_date = None

        days.append(
            {
                "day_index": idx,
                "weekday": label_dow,
                "day_of_month": label_dom,
                "date": block_date.isoformat() if block_date else None,
                "astro_dark": [start, end],
                "moon_phase": moon.group(1) if moon else None,
                "moon_pct": int(moon.group(2)) if moon else None,
                "has_data": has_data,
                "_date": block_date,
                "_start": start,
                "_end": end,
                "_labels": hour_labels,
                "_values": cloud_values,
                "_cond": cond,
                "_wind_unit": wind_unit,
            }
        )

    # Boundaries by date, so a column can consult the night that actually
    # contains it -- including nights owned by a neighbouring block.
    starts = {d["_date"]: d["_start"] for d in days if d["_date"] and d["_start"]}
    ends = {d["_date"]: d["_end"] for d in days if d["_date"] and d["_end"]}

    wind_unit = None
    for d in days:
        labels, values = d.pop("_labels"), d.pop("_values")
        cond, unit = d.pop("_cond"), d.pop("_wind_unit")
        wind_unit = wind_unit or unit
        day0, first = d.pop("_date"), None
        d.pop("_start"), d.pop("_end")
        if labels:
            first = int(labels[0])

        columns = []
        for i, lab in enumerate(labels):
            h = int(lab)
            # A column sits on the block's date, or the next one once the row
            # has wrapped past midnight -- which is exactly where `view`
            # changes the answer, so derive it from the labels themselves.
            col_date = (
                day0 + timedelta(days=1) if (first is not None and h < first) else day0
            )
            temp = cond["temperature_c"][i]
            dew = cond["dew_point_c"][i]
            # Derived, not on the page: temperature minus dew point. The single
            # best dew-risk number available here.
            spread = (temp - dew) if temp is not None and dew is not None else None
            columns.append(
                {
                    "hour": h,
                    "total_clouds": values[i],
                    "astro_dark": _is_dark(h, col_date, starts, ends),
                    "precip_probability": cond["precip_probability"][i],
                    "temperature_c": temp,
                    "dew_point_c": dew,
                    "dewpoint_spread_c": spread,
                    "frost": cond["frost"][i],
                    "wind_speed": cond["wind_speed"][i],
                }
            )
        d["columns"] = columns

    return {
        "location": loc.group(1),
        "generated": generated,
        "sky_quality": sky_quality,
        "wind_unit": wind_unit,
        "days": days,
    }


def slice_hours(data: dict, n: int) -> dict:
    """Keep only the first `n` hourly columns of the report.

    Days arrive in forecast order and each day's columns are in row order, so
    flattening in that sequence is continuous under every `view` (current,
    midnight and midday each start the rows at a different hour, but none of
    them breaks the chain). With the default `--view current` the first column
    is the current hour, so the first n columns are the next n hours from now.

    Days that fall entirely outside the window are omitted rather than printed
    as `no forecast data`, which means *absent from the page* -- a different
    claim from *outside your window*. Parsing is untouched: this runs after
    parse(), so every maintenance invariant still sees the full page.
    """
    kept: list[dict] = []
    remaining = n
    for day in data["days"]:
        if remaining <= 0:
            break
        if not day["columns"]:
            continue
        sliced = dict(day)
        sliced["columns"] = day["columns"][:remaining]
        kept.append(sliced)
        remaining -= len(sliced["columns"])
    out = dict(data)
    out["days"] = kept
    out["hours"] = n
    return out


def summarize(data: dict, threshold: int, night_only: bool) -> str:
    """Render the compact human-readable report."""
    lines = []
    sq = data.get("sky_quality")
    lines.append(f"Clear Outside — {data['location']}")
    if data.get("generated"):
        lines.append(data["generated"])
    if sq:
        lines.append(
            f"Sky quality {sq['magnitude']:.2f} mag/arcsec² | "
            f"Bortle {sq['bortle_class']}"
        )
    if night_only:
        lines.append(f"Total cloud ≤{threshold}% per hour, astro-dark hours only")
    else:
        lines.append(
            f"Total cloud ≤{threshold}% per hour  (* = run lies inside astro dark)"
        )
    if data.get("hours"):
        lines.append(f"Window: first {data['hours']} hourly columns")
    lines.append("")

    any_clear = False
    for day in data["days"]:
        tag = f"{day['weekday'][:3] if day['weekday'] else '???':<3} {day['day_of_month'] or '??':>2}"
        astro = day["astro_dark"]
        astro_txt = f"{astro[0]}→{astro[1]}" if astro[0] and astro[1] else "unknown"

        if not day["has_data"]:
            lines.append(f"{tag}  astro {astro_txt}   no forecast data")
            continue

        clear = [c for c in day["columns"] if c["total_clouds"] <= threshold]
        if night_only:
            clear = [c for c in clear if c["astro_dark"] is True]

        if clear:
            any_clear = True
            pairs = [(f"{c['hour']:02d}", c["astro_dark"] is True) for c in clear]
            dark_n = sum(1 for _, d in pairs if d)
            txt = _compress(pairs)
            extra = f", {dark_n} dark" if dark_n else ""
            lines.append(
                f"{tag}  astro {astro_txt}   clear: {txt}  "
                f"({len(clear)}/{len(day['columns'])}h{extra})"
            )
        else:
            lines.append(
                f"{tag}  astro {astro_txt}   clear: —  (0/{len(day['columns'])}h)"
            )

    if not any_clear:
        lines.append("")
        lines.append(f"No hour in the forecast is at or under {threshold}% cloud.")
    return "\n".join(lines)


def _tag(day: dict) -> str:
    return f"{day['weekday'][:3] if day['weekday'] else '???':<3} {day['day_of_month'] or '??':>2}"


def summarize_conditions(
    data: dict,
    max_wind: float,
    max_precip: int,
    min_dew_spread: int,
    night_only: bool = False,
) -> str:
    """One line per night: worst values, with breaches marked.

    Exceptions-first -- the question this answers is "will conditions hold for
    the next few hours", so the verdict and the worst case matter, not 120 raw
    numbers.

    `dew` (from dew-point spread) and `frost` are reported separately on
    purpose: they are different questions. Frost needs freezing -- the page's
    own flag is authoritative for it -- while dew is pure saturation and can
    wreck an optic at +12 C. Measured across 453 hours, spread alone called
    164 false frosts, every one of them above freezing.

    `night_only` restricts every count and every max/min to hours inside
    astro darkness: the section then answers "will the session hold", not
    "will the 24-column row hold" -- without it, a `wind max` or a `precip
    n/24h` can come entirely from daytime hours that are irrelevant to the
    session. The header says which window is in force, and a row that has
    columns but no dark hours reports `no astro-dark hours` -- never `no
    forecast data`, which means the page itself was empty (invariant 3).
    """
    unit = data.get("wind_unit") or "mph"
    scope = "astro-dark hours only · " if night_only else ""
    lines = [
        f"Conditions  ({scope}wind>={max_wind:g}{unit} · precip>={max_precip}% · "
        f"dew-spread<={min_dew_spread}°C · any frost)"
    ]
    any_breach = False
    for day in data["days"]:
        tag = _tag(day)
        cols = day["columns"]
        if night_only:
            dark = [c for c in cols if c["astro_dark"] is True]
            if cols and not dark:
                lines.append(f"  {tag}  —  no astro-dark hours")
                continue
            cols = dark
        if not cols:
            lines.append(f"  {tag}  —  no forecast data")
            continue
        n = len(cols)
        ws = [c["wind_speed"] for c in cols if c["wind_speed"] is not None]
        pp = [
            c["precip_probability"] for c in cols if c["precip_probability"] is not None
        ]
        sp = [
            c["dewpoint_spread_c"] for c in cols if c["dewpoint_spread_c"] is not None
        ]
        fr = sum(1 for c in cols if c["frost"] is True)
        w_b = sum(
            1
            for c in cols
            if c["wind_speed"] is not None and c["wind_speed"] >= max_wind
        )
        p_b = sum(
            1
            for c in cols
            if c["precip_probability"] is not None
            and c["precip_probability"] >= max_precip
        )
        s_b = sum(
            1
            for c in cols
            if c["dewpoint_spread_c"] is not None
            and c["dewpoint_spread_c"] <= min_dew_spread
        )
        breach = bool(w_b or p_b or s_b or fr)
        any_breach = any_breach or breach

        parts = []
        if ws:
            parts.append(
                f"wind {w_b}/{n}h max{max(ws):g}" if w_b else f"wind max{max(ws):g}"
            )
        if pp:
            parts.append(
                f"precip {p_b}/{n}h max{max(pp)}%" if p_b else f"precip max{max(pp)}%"
            )
        if sp:
            parts.append(
                f"dew {s_b}/{n}h min{min(sp)}°C" if s_b else f"dew min{min(sp)}°C"
            )
        parts.append(f"frost {fr}/{n}h" if fr else "no frost")
        lines.append(f"  {tag}  {'⚠' if breach else '✓'} " + " · ".join(parts))

    if not any_breach:
        where = "the astro-dark hours" if night_only else "this forecast"
        lines.append(f"  → no wind / precip / frost / dew breach in {where}.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Clear Outside hourly forecast for an observing site."
    )
    ap.add_argument("lat", help="latitude from get_site_profile()")
    ap.add_argument("lon", help="longitude from get_site_profile()")
    ap.add_argument(
        "--threshold",
        type=int,
        default=20,
        metavar="PCT",
        help="cloud-cover ceiling in percent (default 20)",
    )
    ap.add_argument(
        "--night-only",
        action="store_true",
        help="restrict both report sections (clear hours and conditions) to "
        "hours inside astronomical darkness",
    )
    ap.add_argument(
        "--view",
        choices=("current", "midnight", "midday"),
        default="current",
        help="where each row's 24 columns start: current local hour "
        "(default), 00:00, or 12:00",
    )
    ap.add_argument(
        "--hours",
        type=int,
        default=None,
        metavar="N",
        help="keep only the first N hourly columns of the report; with the "
        "default --view current that is the next N hours from now. Days "
        "outside the window are omitted (default: all 7 days)",
    )
    ap.add_argument(
        "--max-wind",
        type=float,
        default=12,
        metavar="MPH",
        help="wind breach threshold, in the page's own wind unit (default 12)",
    )
    ap.add_argument(
        "--max-precip",
        type=int,
        default=40,
        metavar="PCT",
        help="precipitation-probability breach threshold in percent (default 40)",
    )
    ap.add_argument(
        "--min-dew-spread",
        type=int,
        default=2,
        metavar="C",
        help="dew-point spread at or below this means optics fogging risk "
        "(default 2 C). Separate from frost, which comes from the page",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument(
        "--html", metavar="FILE", help="parse a saved page instead of fetching"
    )
    args = ap.parse_args(argv)
    if args.hours is not None and args.hours < 1:
        ap.error("--hours must be at least 1")

    try:
        if args.html:
            with open(args.html, encoding="utf-8", errors="replace") as fh:
                html = fh.read()
            url = None
        else:
            url, html = fetch(args.lat, args.lon, args.view)
        data = parse(html)
    except (FetchError, ParseError, OSError) as exc:
        print(f"site-conditions: {exc}", file=sys.stderr)
        return 1

    if args.hours is not None:
        data = slice_hours(data, args.hours)

    if args.json:
        data = dict(data)
        data["url"] = url or (
            f"{BASE_URL}/{_fmt_coord(args.lat)}/{_fmt_coord(args.lon)}?view={args.view}"
        )
        data["view"] = args.view
        data["hours"] = args.hours
        data["threshold"] = args.threshold
        data["breach_thresholds"] = {
            "max_wind": args.max_wind,
            "max_precip": args.max_precip,
            "min_dew_spread": args.min_dew_spread,
        }
        print(json.dumps(data, indent=2))
    else:
        print(summarize(data, args.threshold, args.night_only))
        print()
        print(
            summarize_conditions(
                data,
                args.max_wind,
                args.max_precip,
                args.min_dew_spread,
                args.night_only,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
