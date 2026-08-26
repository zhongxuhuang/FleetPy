"""Build fixed per-zone MFD exogenous-density curves for the 5x MT study."""

import argparse
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = REPO_ROOT / "studies" / "mt" / "results" / \
    "mt_rq_raw_matsim_cali_no_mod_base" / "zone_speed_timeseries.csv"
DEFAULT_ZONE_NETWORK_DIR = REPO_ROOT / "data" / "zones" / \
    "mt" / "Aimsun_Munich_2020"
DEFAULT_MFD_PARAMETERS = DEFAULT_ZONE_NETWORK_DIR.parent / "mfd_parameters.csv"
DEFAULT_EDGES = REPO_ROOT / "data" / "networks" / \
    "Aimsun_Munich_2020" / "base" / "edges.csv"
DEFAULT_OUTPUT = SCRIPT_DIR / "mfd_exogenous_density_5x_wrq2.csv"
DEFAULT_ZONE_SCALES = {0: 20.0, 1: 24.0, 2: 38, 3: 20.0, 4: 20.0}


def parse_zone_scale(value):
    try:
        zone_text, scale_text = value.split("=", 1)
        zone_id = int(zone_text)
        scale = float(scale_text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("zone scale must use ZONE=SCALE") from exc
    if not np.isfinite(scale) or scale <= 0:
        raise argparse.ArgumentTypeError("zone scale must be positive and finite")
    return zone_id, scale


def load_mfd_parameters(path):
    frame = pd.read_csv(path)
    required = {"zone_id", "v_kmh", "gamma"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    frame = frame.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if not np.isfinite(frame[list(required)].to_numpy(dtype=float)).all():
        raise ValueError(f"{path} contains non-finite MFD parameters")
    if not np.equal(frame["zone_id"], np.floor(frame["zone_id"])).all():
        raise ValueError(f"{path} contains non-integer zone IDs")
    frame["zone_id"] = frame["zone_id"].astype(int)
    if frame["zone_id"].duplicated().any():
        raise ValueError(f"{path} contains duplicate zone IDs")
    if (frame[["v_kmh", "gamma"]] <= 0).any().any():
        raise ValueError(f"{path} requires positive v_kmh and gamma values")
    return frame.set_index("zone_id")


def load_zone_lengths(node_zone_path, edges_path, mfd_zones):
    nodes = pd.read_csv(node_zone_path)
    edges = pd.read_csv(edges_path)
    missing_nodes = {"node_index", "zone_id"} - set(nodes.columns)
    missing_edges = {"from_node", "distance"} - set(edges.columns)
    if missing_nodes:
        raise ValueError(f"{node_zone_path} is missing columns: {sorted(missing_nodes)}")
    if missing_edges:
        raise ValueError(f"{edges_path} is missing columns: {sorted(missing_edges)}")
    mapping = nodes.drop_duplicates("node_index", keep="first").set_index("node_index")["zone_id"]
    edge_zones = pd.to_numeric(edges["from_node"], errors="raise").map(mapping)
    distances = pd.to_numeric(edges["distance"], errors="raise")
    if not np.isfinite(distances).all() or (distances < 0).any():
        raise ValueError(f"{edges_path} contains invalid distances")
    lengths = distances.groupby(edge_zones).sum() / 1000.0
    result = {}
    for zone_id in sorted(mfd_zones):
        length = float(lengths.get(zone_id, 0.0))
        if not np.isfinite(length) or length <= 0:
            raise ValueError(f"No positive directed road length was found for MFD zone {zone_id}")
        result[zone_id] = length
    return result


def build_profile(source, zone_lengths_km, zone_scales, start_time=0, end_time=86400):
    required = {"simulation_time", "zone_id", "pv_vehicle_count"}
    missing = required - set(source.columns)
    if missing:
        raise ValueError(f"source time series is missing columns: {sorted(missing)}")
    source = source[list(required)].copy()
    for column in required:
        source[column] = pd.to_numeric(source[column], errors="raise")
    if not np.isfinite(source.to_numpy(dtype=float)).all():
        raise ValueError("source time series contains non-finite values")
    if (source["simulation_time"] < 0).any() or (source["pv_vehicle_count"] < 0).any():
        raise ValueError("source time series contains negative time or PV count values")
    if not np.equal(source["zone_id"], np.floor(source["zone_id"])).all():
        raise ValueError("source time series contains non-integer zone IDs")
    source["zone_id"] = source["zone_id"].astype(int)
    source = source[source["zone_id"].isin(zone_lengths_km)]
    if source.duplicated(["zone_id", "simulation_time"]).any():
        raise ValueError("source time series contains duplicate zone/time rows")
    missing_zones = sorted(set(zone_lengths_km) - set(source["zone_id"].unique()))
    if missing_zones:
        raise ValueError(f"source time series is missing MFD zones: {missing_zones}")
    if set(zone_scales) != set(zone_lengths_km):
        raise ValueError("zone scales must be specified exactly for every MFD zone")
    if end_time <= start_time:
        raise ValueError("end_time must be greater than start_time")

    knot_times = np.arange(float(start_time), float(end_time) + 300.0, 300.0)
    rows = []
    for zone_id in sorted(zone_lengths_km):
        zone = source[source["zone_id"] == zone_id].sort_values("simulation_time")
        times = zone["simulation_time"].to_numpy(dtype=float)
        if len(times) < 60:
            raise ValueError(f"zone {zone_id} has fewer than 60 source observations")
        time_steps = np.diff(times)
        if not np.allclose(time_steps, 30.0, rtol=0, atol=1e-9):
            raise ValueError(f"zone {zone_id} source observations must use a complete 30-second grid")
        if times[0] > start_time or times[-1] < end_time - 30:
            raise ValueError(f"zone {zone_id} source observations do not cover the requested day")

        smoothed_count = zone["pv_vehicle_count"].rolling(
            window=60, center=True, min_periods=1
        ).mean().to_numpy(dtype=float)
        smoothed_density = smoothed_count / zone_lengths_km[zone_id]
        peak_density = float(smoothed_density.max())
        if peak_density <= 0:
            raise ValueError(f"zone {zone_id} has no positive smoothed PV density")
        reference_density = np.interp(knot_times, times, smoothed_density)
        normalized_profile = reference_density / peak_density
        scale = float(zone_scales[zone_id])
        exogenous_density = reference_density * scale
        for time_value, normalized, reference, exogenous in zip(
            knot_times, normalized_profile, reference_density, exogenous_density
        ):
            rows.append({
                "simulation_time": int(time_value),
                "zone_id": zone_id,
                "normalized_profile": normalized,
                "reference_density_veh_per_km": reference,
                "zone_scale": scale,
                "exogenous_density_veh_per_km": exogenous,
            })
    return pd.DataFrame(rows).sort_values(["simulation_time", "zone_id"]).reset_index(drop=True)


def atomic_write_csv(frame, output_path, overwrite=False):
    output_path = Path(output_path).resolve()
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists; pass --overwrite to replace it: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".tmp", prefix=output_path.name + ".", dir=output_path.parent,
        delete=False, encoding="utf-8", newline=""
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            frame.to_csv(handle, index=False)
        os.replace(temporary_path, output_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--node-zone-file", type=Path, default=DEFAULT_ZONE_NETWORK_DIR / "node_zone_info.csv")
    parser.add_argument("--edges-file", type=Path, default=DEFAULT_EDGES)
    parser.add_argument("--mfd-parameters-file", type=Path, default=DEFAULT_MFD_PARAMETERS)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--zone-scale", action="append", type=parse_zone_scale, default=[], metavar="ZONE=SCALE")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    mfd_parameters = load_mfd_parameters(args.mfd_parameters_file)
    zone_lengths = load_zone_lengths(args.node_zone_file, args.edges_file, set(mfd_parameters.index))
    scales = dict(DEFAULT_ZONE_SCALES)
    scales.update(dict(args.zone_scale))
    profile = build_profile(pd.read_csv(args.source), zone_lengths, scales)
    output_path = atomic_write_csv(profile, args.output_file, overwrite=args.overwrite)

    print(f"Wrote {len(profile)} rows to {output_path}")
    for zone_id, zone in profile.groupby("zone_id"):
        critical_density = mfd_parameters.loc[zone_id, "v_kmh"] / (
            2.0 * mfd_parameters.loc[zone_id, "gamma"]
        )
        peak = zone["exogenous_density_veh_per_km"].max()
        print(
            f"Zone {zone_id}: scale={scales[zone_id]:g}, "
            f"exogenous_peak={peak:.6f} veh/km "
            f"({100.0 * peak / critical_density:.2f}% of critical)"
        )


if __name__ == "__main__":
    main()
