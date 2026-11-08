#!/usr/bin/env python3
"""Refresh the sequence-state test fixtures from a live N.I.N.A. API.

Usage:
    scripts/refresh_fixtures.py HOST[:PORT] [--label unrun] [--out DIR]

HOST defaults to port 1888 when none is given. Load the sequence you want to
capture in N.I.N.A. first (e.g. via load_sequence), and pick a --label that
describes its state (unrun, running, finished). Writes the full API envelope to
sequence_state_<label>.json and sequence_json_<label>.json.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / ".opencode/local/mcps/nina_planner/tests/fixtures"
)
ENDPOINTS = {"state": "/sequence/state", "json": "/sequence/json"}


def fetch(host: str, path: str) -> dict:
    if ":" not in host:
        host += ":1888"
    url = f"http://{host}/v2/api{path}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.load(resp)
    if not data.get("Success"):
        sys.exit(f"{url}: {data.get('Error') or 'API request failed'}")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "host", help="NINA API host, optionally HOST:PORT (default port 1888)"
    )
    ap.add_argument(
        "--label", default="unrun", help="state label in the filename (default: unrun)"
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output directory (default: {DEFAULT_OUT})",
    )
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for key, path in ENDPOINTS.items():
        data = fetch(args.host, path)
        target = args.out / f"sequence_{key}_{args.label}.json"
        target.write_text(json.dumps(data))
        print(f"{target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
