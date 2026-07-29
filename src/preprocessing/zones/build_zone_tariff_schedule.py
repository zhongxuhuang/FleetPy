"""Generate one fixed tariff table from the active zone MFD parameters.

The generated ``mfd_speed`` rows contain tariff *ranges*, not a reference speed
trajectory.  At runtime FleetPy reads the already calculated MFD speed snapshot
and selects one of these rows without evaluating the MFD again.
"""
import argparse
import ast
import math

import pandas as pd


DAY_SECONDS = 24 * 3600
SPEED_BANDS = ((0.0, 0.25, "severe", 2.0), (0.25, 0.50, "congested", 1.25),
               (0.50, 0.75, "moderate", 0.75), (0.75, None, "free", 0.25))


def _mapping(value):
    result = ast.literal_eval(value)
    if not isinstance(result, dict):
        raise ValueError("base-rate-cent-per-m must be a zone-to-rate dictionary")
    return {int(zone): float(rate) for zone, rate in result.items()}


def _route_distances(path, zones, default_m):
    distances = {zone: default_m for zone in zones}
    if path is None:
        return distances
    frame = pd.read_csv(path)
    for row in frame.itertuples(index=False):
        distances[int(row.zone_id)] = float(row.mean_in_zone_distance_m)
    return distances


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mfd_parameters")
    parser.add_argument("output")
    parser.add_argument("--outside-zone-id", type=int, default=5)
    parser.add_argument("--base-rate-cent-per-m", default="{0: 0.05, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.1, 5: 0.0}")
    parser.add_argument("--route-distance-summary", help="CSV: zone_id,mean_in_zone_distance_m")
    parser.add_argument("--default-entry-distance-km", type=float, default=3.0)
    parser.add_argument("--peak-start", type=int, default=25200)
    parser.add_argument("--peak-end", type=int, default=32400)
    parser.add_argument("--offpeak-factor", type=float, default=0.5)
    args = parser.parse_args()
    if args.default_entry_distance_km < 0 or not 0 <= args.peak_start < args.peak_end <= DAY_SECONDS:
        parser.error("invalid distance or peak interval")

    mfd = pd.read_csv(args.mfd_parameters)
    if not {"zone_id", "v_kmh"}.issubset(mfd.columns):
        raise ValueError("mfd_parameters must contain zone_id and v_kmh")
    free_speeds = {int(row.zone_id): float(row.v_kmh) for row in mfd.itertuples(index=False)}
    rates = _mapping(args.base_rate_cent_per_m)
    zones = sorted(set(free_speeds) | set(rates) | {args.outside_zone_id})
    distances = _route_distances(args.route_distance_summary, zones, args.default_entry_distance_km * 1000)
    rows = []

    for zone in zones:
        base_rate = rates.get(zone, 0.0)
        # Time-of-day tariffs use the same baseline monetary scale as MFD bands.
        for start, end, factor in ((0, args.peak_start, args.offpeak_factor),
                                   (args.peak_start, args.peak_end, 1.0),
                                   (args.peak_end, DAY_SECONDS, args.offpeak_factor)):
            for charge_type in ("cordon", "distance"):
                rate = base_rate * factor
                rows.append({"charge_type": charge_type, "tariff_basis": "time_of_day", "zone_id": zone,
                             "time_start": start, "time_end": end, "speed_min_kmh": None,
                             "speed_max_kmh": None, "speed_band": "", "entry_fee_cent": round(rate * distances[zone]) if charge_type == "cordon" else None,
                             "distance_rate_cent_per_m": rate if charge_type == "distance" else None})
        if zone == args.outside_zone_id:
            for charge_type in ("cordon", "distance"):
                rows.append({"charge_type": charge_type, "tariff_basis": "mfd_speed", "zone_id": zone,
                             "time_start": 0, "time_end": DAY_SECONDS, "speed_min_kmh": None,
                             "speed_max_kmh": None, "speed_band": "outside", "entry_fee_cent": 0 if charge_type == "cordon" else None,
                             "distance_rate_cent_per_m": 0 if charge_type == "distance" else None})
            continue
        free_speed = free_speeds[zone]
        for lower_ratio, upper_ratio, label, factor in SPEED_BANDS:
            rate = base_rate * factor
            for charge_type in ("cordon", "distance"):
                rows.append({"charge_type": charge_type, "tariff_basis": "mfd_speed", "zone_id": zone,
                             "time_start": 0, "time_end": DAY_SECONDS, "speed_min_kmh": lower_ratio * free_speed,
                             "speed_max_kmh": None if upper_ratio is None else upper_ratio * free_speed,
                             "speed_band": label, "entry_fee_cent": round(rate * distances[zone]) if charge_type == "cordon" else None,
                             "distance_rate_cent_per_m": rate if charge_type == "distance" else None})
    pd.DataFrame(rows).to_csv(args.output, index=False)


if __name__ == "__main__":
    main()
