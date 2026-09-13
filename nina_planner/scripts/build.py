import json
import sys
import urllib.request

from ..models.plan import *
from ..sequence import build_sequence_darks, build_sequence_lights


def post_sequence(data):

    url = "http://korolev.local:1888/v2/api/sequence/load"
    url = "http://localhost:1888/v2/api/sequence/load"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data)
    req = urllib.request.Request(
        url, data=body.encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        print(json.dumps(data, indent=2))
    print(url, file=sys.stderr)


def show_model(model, indent=0):
    from pydantic import BaseModel

    for d in model:
        if isinstance(d[1], BaseModel):
            print(" " * indent, d[0], ":")
            show_model(d[1], indent + 2)
        elif isinstance(d[1], list):
            for n in range(len(d[1])):
                print(" " * indent, f"{d[0]}[{n}]")
                show_model(d[1][n], indent + 2)
        else:
            print(" " * indent, d[0], ":", d[1])


data = {
    "target": "M27",
    "intent": "narrowband",
    "description": "description",
    "ra_hours": 19.9933,
    "dec_deg": 22.721,
    "batch_size": 0,
    "cooler": {"on": True, "setpoint_celsius": -10.0},
    "constraints": {"min_altitude": 30.0, "horizon_offset_degrees": 2.0},
    "autofocus": {
        "reference_filter_name": "L",
        "hfr_increase_sample_size": 3,
        "hfr_increase_threshold_percent": 15.0,
        "every_n_exposures": 10,
        "threshold_celsius": 1.0,
    },
    "guiding": {
        "dither_every_n_exposures": 2,
        "check_drift_every_n_exposures": 6,
        "max_drift_arcmin": 1.5,
    },
    "lights": [
        {"filter_name": "L", "exposure_time_seconds": 30.0, "total_count": 120},
        {"filter_name": "LP", "exposure_time_seconds": 30.0, "total_count": 120},
    ],
    "flats": [
        {"filter_name": "L", "exposure_time_seconds": 5.0, "total_count": 15},
        {"filter_name": "LP", "exposure_time_seconds": 5.0, "total_count": 15},
    ],
    "darks": [{"exposure_time_seconds": 30.0, "total_count": 10}],
    "bias": [{"total_count": 20}],
}


def main():
    print("loading plan...")
    plan = ObservationPlan.model_validate(data)
    show_model(plan)
    seq = build_sequence_lights(plan)
    seq = build_sequence_darks(plan)
    seq = build_sequence_darks(plan, bias=True)
    # seq = build_sequence_flats(plan)
    print(json.dumps(seq, indent=2))
    post_sequence(seq)


if __name__ == "__main__":
    main()
