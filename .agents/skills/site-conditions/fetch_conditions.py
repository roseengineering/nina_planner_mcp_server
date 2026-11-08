#!/usr/bin/env python3
"""Fetch and parse the Clear Outside 7-day hourly forecast for an observing site.

Stdlib only -- no third-party dependencies. Companion to the `site-conditions`
skill; see SKILL.md before changing the parsing rules.

    ./fetch_conditions.py <lat> <lon>
    ./fetch_conditions.py <lat> <lon> --night-only
    ./fetch_conditions.py <lat> <lon> --json
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
RE_INT_CELL = re.compile(r"<li[^>]*>(\d+)</li>")


class FetchError(Exception):
    pass


class ParseError(Exception):
    pass


def fetch(lat: str, lon: str, view: str = "current") -> tuple[str, str]:
    """Download the forecast page. Returns (url, html).

    `view` sets where each row's 24 columns start: `current` at the current
    local hour, `midnight` at 00:00, `midday` at 12:00. Passed explicitly
    even though the site already defaults to `current`, so the window can
    never drift if their default changes.
    """
    url = f"{BASE_URL}/{lat}/{lon}?view={view}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError(f"network failure fetching {url}: {exc}") from exc
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
            }
        )

    # Boundaries by date, so a column can consult the night that actually
    # contains it -- including nights owned by a neighbouring block.
    starts = {d["_date"]: d["_start"] for d in days if d["_date"] and d["_start"]}
    ends = {d["_date"]: d["_end"] for d in days if d["_date"] and d["_end"]}

    for d in days:
        labels, values = d.pop("_labels"), d.pop("_values")
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
            columns.append(
                {
                    "hour": h,
                    "total_clouds": values[i],
                    "astro_dark": _is_dark(h, col_date, starts, ends),
                }
            )
        d["columns"] = columns

    return {
        "location": loc.group(1),
        "generated": generated,
        "sky_quality": sky_quality,
        "days": days,
    }


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
        help="report only hours inside astronomical darkness",
    )
    ap.add_argument(
        "--view",
        choices=("current", "midnight", "midday"),
        default="current",
        help="where each row's 24 columns start: current local hour "
        "(default), 00:00, or 12:00",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument(
        "--html", metavar="FILE", help="parse a saved page instead of fetching"
    )
    args = ap.parse_args(argv)

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

    if args.json:
        data = dict(data)
        data["url"] = url or f"{BASE_URL}/{args.lat}/{args.lon}?view={args.view}"
        data["view"] = args.view
        data["threshold"] = args.threshold
        print(json.dumps(data, indent=2))
    else:
        print(summarize(data, args.threshold, args.night_only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
