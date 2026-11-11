#!/usr/bin/env python3
"""Step the mount through azimuths one at a time, waiting for your Enter key.

For eyeballing / screenshotting where each azimuth actually points. No exposures
are taken - the camera is untouched, only the mount slews. Reuses the pointing
math and closed pointing loop from ``horizon_pan.py`` (JNOW epoch, verified
against the mount's own Alt/Az readback, corrected once).

    python3 scripts/horizon_step.py                 # 15 deg steps, alt 15
    python3 scripts/horizon_step.py --alt 5 --az-step 30
    python3 scripts/horizon_step.py --start 180 --park-at-end

At each stop:
    <Enter>  next azimuth          b / p   previous azimuth
    <number> jump to that azimuth  h       park now
    q        quit (parks if --park-at-end)
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import horizon_pan as hp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="horizon_step.py",
        description="Slew azimuth by azimuth and wait for Enter between steps.",
    )
    ap.add_argument("--endpoint", default=hp.DEFAULT_ENDPOINT)
    ap.add_argument("--alt", type=float, default=15.0, help="altitude deg (default 15)")
    ap.add_argument(
        "--az-step", type=float, default=15.0, help="azimuth step deg (default 15)"
    )
    ap.add_argument(
        "--start", type=float, default=0.0, help="first azimuth (default 0)"
    )
    ap.add_argument("--settle", type=float, default=1.5, help="post-slew settle s")
    ap.add_argument("--epoch", default="JNOW", choices=("J2000", "JNOW"))
    ap.add_argument(
        "--az",
        type=float,
        default=None,
        help="slew to ONE azimuth and exit (no loop) - the chat-driven mode",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="seconds to hold each azimuth when stdin is not a TTY"
        " (auto-advance for screenshots; default 5)",
    )
    ap.add_argument(
        "--park-at-end",
        action="store_true",
        help="park the mount (closes the lens) when the loop ends or is quit",
    )
    args = ap.parse_args(argv)

    nina = hp.Nina(args.endpoint)
    info = nina.info()
    mount = info.get("Mount") or {}
    if not mount.get("Connected"):
        print("  x mount not connected", file=sys.stderr)
        return 2
    lat = float(mount.get("SiteLatitude") or 0.0)

    azs, v = [], args.start % 360.0
    while v < 360.0 - 1e-9:
        azs.append(round(v, 3))
        v += args.az_step
    if azs[0] != 0.0:
        azs.append(360.0)  # close the circle back on the starting azimuth

    print(
        f"  {len(azs)} azimuths from {azs[0]:g} step {args.az_step:g} at alt"
        f" {args.alt:g}  (epoch {args.epoch})\n"
        "   <Enter>=next  b=back  <number>=jump  h=park  q=quit\n"
    )

    def stop(idx: int) -> tuple[float, float, float]:
        az = azs[idx]
        lst = float(hp.mount_info(nina).get("SiderealTime") or 0.0)
        a_alt, a_az, _ra, _dec, err = hp.slew_to(
            nina, args.alt, az, lat, lst, args.epoch, args.settle
        )
        print(
            f"  [{idx + 1}/{len(azs)}]  az {az:6.1f} -> actual {a_az:6.1f}"
            f"  alt {a_alt:5.1f}  err {err:.2f} deg",
            flush=True,
        )
        return az, a_alt, err

    if args.az is not None:  # one-shot: slew to this azimuth, report, leave it
        target = round(args.az % 360.0, 3)
        i = min(
            range(len(azs)),
            key=lambda k: min(abs(azs[k] - target), abs(azs[k] - 360 - target)),
        )
        azs[i] = target
        stop(i)
        return 0

    interactive = sys.stdin.isatty()
    i = 0
    try:
        while 0 <= i < len(azs):
            stop(i)
            if not interactive:
                if i + 1 >= len(azs):
                    break
                time.sleep(args.interval)
                i += 1
                continue
            ans = input("      > ").strip().lower()
            if ans in ("q", "quit", "exit"):
                break
            if ans in ("b", "p", "prev", "-"):
                i -= 1
                continue
            if ans in ("h", "park"):
                hp.park_mount(nina)
                return 0
            if ans:
                try:
                    want = float(ans)
                except ValueError:
                    print("      ? Enter=next b=back number=jump h=park q=quit")
                    continue
                j = min(
                    range(len(azs)),
                    key=lambda k: min(abs(azs[k] - want), abs(azs[k] - 360 - want)),
                )
                i = j
                continue
            i += 1
    except KeyboardInterrupt:
        print("\n  interrupted.")
    finally:
        if args.park_at_end:
            hp.park_mount(nina)
        else:
            m = hp.mount_info(nina)
            print(
                f"  left at alt={m.get('Altitude')} az={m.get('Azimuth')}"
                " (NOT parked - pass --park-at-end to park)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
