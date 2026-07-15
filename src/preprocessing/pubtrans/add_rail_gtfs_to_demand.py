"""
Rail-based GTFS travel time preprocessor for FleetPy demand files.

This module provides ``RailGTFSODTravelTimePreprocessor`` and a command-line
entry point. It augments an existing FleetPy demand CSV with door-to-door
public transport travel time attributes that can be consumed by
``MultinomialLogitRequest``:

    - ``gtfs_total_duration_min``: total rail-based PT duration in minutes
    - ``nr_transfers``: number of rail transfers in the selected connection

The original demand rows, order, and columns are preserved. The two PT columns
are appended to the output file, so the output can be used like a regular
FleetPy request file in ``scenario_cfg``.

Relation to ``PTScheduleGen.py``
--------------------------------
``PTScheduleGen.py`` creates PT supply files for a selected line, such as
``stations.csv`` and ``*_schedules.csv``. This module does not create PT line
supply. It reads GTFS directly and computes request-level OD travel times for
mode choice. In other words:

    - ``PTScheduleGen.py``: line schedule/station preprocessing for PT supply
    - this module: demand-row PT attribute preprocessing for MNL choice

Travel time calculation
-----------------------
For each request, the script:

    1. reads ``rq_time``, ``start`` node, and ``end`` node from the demand row;
    2. finds nearby rail stations around the start/end network nodes;
    3. converts access and egress distances to walking time;
    4. scans GTFS rail connections after the request time;
    5. returns the earliest door-to-door arrival including access walk,
       waiting time, in-vehicle time, transfer buffer, and egress walk.

The default rail modes are read from the complete Munich MVV GTFS feed:

    - route_type 0: Tram and S-Bahn
    - route_type 1: U-Bahn
    - route_type 2: Regionalbahn

Example
-------
Run from the FleetPy repository root:

    python src/preprocessing/pubtrans/add_rail_gtfs_to_demand.py \
        --demand data/demand/Munich_PV_2020/matched/Aimsun_Munich_2020/d_10_s_1.csv \
        --output data/demand/Munich_PV_2020/matched/Aimsun_Munich_2020/d10s1pt.csv
"""

import argparse
import os

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, total=None, desc=None, unit=None):
        """Minimal progress display when the optional tqdm package is unavailable."""
        total = total or 0
        step = max(total // 100, 1)
        label = desc or "Progress"
        for current, item in enumerate(iterable, start=1):
            if current == 1 or current % step == 0 or current == total:
                print(f"\r{label}: {current}/{total} {unit or 'item'}", end="", flush=True)
            yield item
        if total:
            print()


DEFAULT_GTFS_DIR = os.path.join("data", "gesamt_gtfs")
DEFAULT_RAIL_ROUTE_TYPES = (0, 1, 2)


class RailGTFSODTravelTimePreprocessor:
    def __init__(
        self,
        gtfs_dir=DEFAULT_GTFS_DIR,
        network_dir=os.path.join("data", "networks", "Aimsun_Munich_2020"),
        service_date="20260706",
        route_types=DEFAULT_RAIL_ROUTE_TYPES,
        access_radius_m=1000.0,
        walking_speed=1.4,
        transfer_buffer_s=120.0,
        time_bin_s=0,
    ):
        """
        Rail-based GTFS OD travel time preprocessor.

        :param gtfs_dir: the path to the GTFS directory with files
            (routes, trips, stops, stop_times, calendar, calendar_dates)
        :param network_dir: folder path of the FleetPy network
        :param service_date: GTFS service date used to select active trips
        :param route_types: GTFS route_type values to include
            (default: 0 Tram/S-Bahn, 1 U-Bahn, 2 Regionalbahn)
        :param access_radius_m: maximum start/end node distance to usable PT stations
        :param walking_speed: access/egress walking speed in m/s
        :param transfer_buffer_s: minimum time needed before boarding after a transfer
        :param time_bin_s: optional request-time aggregation for caching repeated
            OD calculations; set to 0 to calculate each request at its exact time
        """
        self.gtfs_dir = gtfs_dir
        self.network_dir = network_dir
        self.service_date = service_date
        self.route_types = self._parse_route_types(route_types)
        self.access_radius_m = access_radius_m
        self.walking_speed = walking_speed
        self.transfer_buffer_s = transfer_buffer_s
        self.time_bin_s = time_bin_s

        print("Loading FleetPy network nodes")
        self.nodes, self.network_crs = load_network_nodes(self.network_dir)

        print("Loading GTFS rail connections")
        self.station_coords, self.station_to_idx, self.connections, self.n_trips = load_rail_gtfs(
            self.gtfs_dir, self.network_crs, self.route_types, self.service_date
        )

        self.candidate_cache = {}

        self.conn_from = self.connections["from_idx"].to_numpy(dtype=np.int32)
        self.conn_to = self.connections["to_idx"].to_numpy(dtype=np.int32)
        self.conn_trip = self.connections["trip_idx"].to_numpy(dtype=np.int32)
        self.conn_dep = self.connections["departure_s"].to_numpy(dtype=np.float64)
        self.conn_arr = self.connections["arrival_s"].to_numpy(dtype=np.float64)
        self.request_cache = {}

    def prepare_candidate_cache(self, demand):
        """Build station candidates only for nodes used by the processed demand."""
        node_ids = set(demand["start"].dropna().astype(int))
        node_ids.update(demand["end"].dropna().astype(int))
        missing_node_ids = node_ids.difference(self.candidate_cache)
        if not missing_node_ids:
            return

        available_node_ids = self.nodes.index.intersection(list(missing_node_ids))
        print(f"Building station candidate cache for {len(available_node_ids)} demand nodes")
        self.candidate_cache.update(
            build_station_candidate_cache(
                self.nodes.loc[available_node_ids], self.station_coords, self.access_radius_m
            )
        )
        for node_id in missing_node_ids.difference(available_node_ids):
            self.candidate_cache[node_id] = []

    @staticmethod
    def _parse_route_types(route_types):
        if isinstance(route_types, str):
            return tuple(int(x) for x in route_types.split(",") if x.strip())
        return tuple(int(x) for x in route_types)

    def compute_request(self, rq_time, start_node, end_node):
        """
        Compute the earliest rail PT duration and number of transfers for one OD request.

        :param rq_time: request time in seconds
        :param start_node: FleetPy origin network node id
        :param end_node: FleetPy destination network node id
        :return: tuple (gtfs_total_duration_min, nr_transfers)
        """
        if self.time_bin_s > 0:
            effective_rq_time = int(rq_time // self.time_bin_s) * self.time_bin_s
            cache_time = effective_rq_time
        else:
            effective_rq_time = float(rq_time)
            cache_time = effective_rq_time
        cache_key = (cache_time, int(start_node), int(end_node))
        if cache_key in self.request_cache:
            return self.request_cache[cache_key]

        origin_candidates = to_indexed_candidates(
            self.candidate_cache.get(int(start_node), []), self.station_to_idx, self.walking_speed
        )
        destination_candidates = to_indexed_candidates(
            self.candidate_cache.get(int(end_node), []), self.station_to_idx, self.walking_speed
        )
        duration_min, transfers = compute_earliest_arrival(
            effective_rq_time,
            origin_candidates,
            destination_candidates,
            self.conn_from,
            self.conn_to,
            self.conn_trip,
            self.conn_dep,
            self.conn_arr,
            len(self.station_to_idx),
            self.n_trips,
            self.transfer_buffer_s,
        )
        self.request_cache[cache_key] = (duration_min, transfers)
        return duration_min, transfers

    def augment_demand(self, demand_file, output=None, start_time=None, end_time=None, limit=None):
        """
        Add rail PT attributes to a FleetPy demand CSV.

        :param demand_file: path to the input FleetPy demand CSV
        :param output: path to the output demand CSV; if None, a *_railpt.csv
            file is written next to the input demand
        :param start_time: optional first request time to process; other rows
            are preserved with empty PT fields
        :param end_time: optional upper request time bound to process; other
            rows are preserved with empty PT fields
        :param limit: optional number of filtered rows to process for testing
        :return: path to the written output file
        """
        demand = pd.read_csv(demand_file)
        demand_to_process = demand
        if start_time is not None:
            demand_to_process = demand_to_process[demand_to_process["rq_time"] >= start_time]
        if end_time is not None:
            demand_to_process = demand_to_process[demand_to_process["rq_time"] < end_time]
        if limit is not None:
            demand_to_process = demand_to_process.head(limit).copy()

        self.prepare_candidate_cache(demand_to_process)

        gtfs_total_duration_min = []
        nr_transfers = []
        progress = tqdm(
            demand_to_process.iterrows(),
            total=len(demand_to_process),
            desc="Computing GTFS rail travel times",
            unit="request",
        )
        for _, row in progress:
            duration_min, transfers = self.compute_request(row["rq_time"], row["start"], row["end"])
            gtfs_total_duration_min.append(duration_min)
            nr_transfers.append(transfers)

        demand.loc[demand_to_process.index, "gtfs_total_duration_min"] = gtfs_total_duration_min
        demand.loc[demand_to_process.index, "nr_transfers"] = nr_transfers

        if output is None:
            base, ext = os.path.splitext(demand_file)
            output = f"{base}_railpt{ext}"
        demand.to_csv(output, index=False)

        found = pd.Series(gtfs_total_duration_min).notna().sum()
        print(f"Processed requests: {len(demand_to_process)}")
        print(f"Rail PT paths found: {found}")
        print(f"Output: {output}")
        return output


def parse_gtfs_time(value):
    h, m, s = str(value).split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_service_date(service_date):
    return int(str(service_date).replace("-", ""))


def active_service_ids(gtfs_dir, service_date):
    service_date = parse_service_date(service_date)
    date_ts = pd.to_datetime(str(service_date), format="%Y%m%d")
    weekday = date_ts.day_name().lower()

    calendar_f = os.path.join(gtfs_dir, "calendar.txt")
    calendar = pd.read_csv(calendar_f)
    base_active = calendar[
        (calendar["start_date"] <= service_date)
        & (calendar["end_date"] >= service_date)
        & (calendar[weekday] == 1)
    ]["service_id"].astype(str)
    active = set(base_active)

    calendar_dates_f = os.path.join(gtfs_dir, "calendar_dates.txt")
    if os.path.isfile(calendar_dates_f):
        calendar_dates = pd.read_csv(calendar_dates_f)
        day_exceptions = calendar_dates[calendar_dates["date"] == service_date]
        active.update(day_exceptions[day_exceptions["exception_type"] == 1]["service_id"].astype(str))
        active.difference_update(day_exceptions[day_exceptions["exception_type"] == 2]["service_id"].astype(str))
    return active


def project_stops(stops, network_crs):
    transformer = Transformer.from_crs("EPSG:4326", network_crs, always_xy=True)
    x, y = transformer.transform(stops["stop_lon"].astype(float).to_numpy(), stops["stop_lat"].astype(float).to_numpy())
    stops = stops.copy()
    stops["pos_x"] = x
    stops["pos_y"] = y
    return stops


def load_rail_gtfs(gtfs_dir, network_crs, route_types, service_date):
    routes = pd.read_csv(os.path.join(gtfs_dir, "routes.txt"))
    rail_route_ids = set(routes[routes["route_type"].isin(route_types)]["route_id"].astype(str))

    active_services = active_service_ids(gtfs_dir, service_date)
    trips = pd.read_csv(os.path.join(gtfs_dir, "trips.txt"), usecols=["route_id", "service_id", "trip_id"])
    trips = trips[
        trips["route_id"].astype(str).isin(rail_route_ids)
        & trips["service_id"].astype(str).isin(active_services)
    ]
    rail_trip_ids = set(trips["trip_id"].astype(str))
    if not rail_trip_ids:
        raise RuntimeError("No active rail trips found for the selected service date and route types.")

    stops = pd.read_csv(os.path.join(gtfs_dir, "stops.txt"))
    stops = project_stops(stops, network_crs)
    stops["station_id"] = stops["parent_station"].where(
        stops["parent_station"].notna() & (stops["parent_station"].astype(str) != ""),
        stops["stop_id"],
    ).astype(str)
    stop_to_station = dict(zip(stops["stop_id"].astype(str), stops["station_id"]))

    station_coords = stops.groupby("station_id")[["pos_x", "pos_y"]].mean()

    stop_times_cols = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]
    rail_stop_times = []
    for chunk in pd.read_csv(os.path.join(gtfs_dir, "stop_times.txt"), usecols=stop_times_cols, chunksize=1_000_000):
        chunk["trip_id"] = chunk["trip_id"].astype(str)
        chunk = chunk[chunk["trip_id"].isin(rail_trip_ids)]
        if len(chunk):
            rail_stop_times.append(chunk)
    if not rail_stop_times:
        raise RuntimeError("No rail stop_times found after filtering active trips.")

    stop_times = pd.concat(rail_stop_times, ignore_index=True)
    stop_times["station_id"] = stop_times["stop_id"].astype(str).map(stop_to_station)
    stop_times.dropna(subset=["station_id"], inplace=True)
    stop_times["station_id"] = stop_times["station_id"].astype(str)
    stop_times["arrival_s"] = stop_times["arrival_time"].map(parse_gtfs_time)
    stop_times["departure_s"] = stop_times["departure_time"].map(parse_gtfs_time)
    stop_times.sort_values(["trip_id", "stop_sequence"], inplace=True)

    next_stop_times = stop_times.groupby("trip_id").shift(-1)
    connections = pd.DataFrame({
        "trip_id": stop_times["trip_id"],
        "from_station": stop_times["station_id"],
        "to_station": next_stop_times["station_id"],
        "departure_s": stop_times["departure_s"],
        "arrival_s": next_stop_times["arrival_s"],
    })
    connections.dropna(subset=["to_station", "arrival_s"], inplace=True)
    connections = connections[connections["from_station"] != connections["to_station"]]
    connections = connections[connections["arrival_s"] >= connections["departure_s"]]
    used_stations = set(connections["from_station"]).union(set(connections["to_station"]))
    station_coords = station_coords.loc[station_coords.index.intersection(used_stations)]

    station_to_idx = {station_id: i for i, station_id in enumerate(station_coords.index)}
    trip_to_idx = {trip_id: i for i, trip_id in enumerate(connections["trip_id"].astype(str).unique())}
    connections = connections[
        connections["from_station"].isin(station_to_idx)
        & connections["to_station"].isin(station_to_idx)
    ].copy()
    connections["from_idx"] = connections["from_station"].map(station_to_idx).astype(int)
    connections["to_idx"] = connections["to_station"].map(station_to_idx).astype(int)
    connections["trip_idx"] = connections["trip_id"].astype(str).map(trip_to_idx).astype(int)
    connections.sort_values("departure_s", inplace=True)

    return station_coords, station_to_idx, connections, len(trip_to_idx)


def load_network_nodes(network_dir):
    base_dir = os.path.join(network_dir, "base")
    with open(os.path.join(base_dir, "crs.info"), "r") as f:
        network_crs = f.read().strip()
    nodes = pd.read_csv(os.path.join(base_dir, "nodes.csv"))
    nodes.set_index("node_index", inplace=True)
    return nodes, network_crs


def build_station_candidate_cache(nodes, station_coords, access_radius_m):
    node_tree = cKDTree(station_coords[["pos_x", "pos_y"]].to_numpy())
    station_ids = list(station_coords.index)
    cache = {}
    for node_id, node in nodes.iterrows():
        node_xy = np.array([node["pos_x"], node["pos_y"]])
        candidate_indices = node_tree.query_ball_point(node_xy, access_radius_m)
        candidates = []
        for station_idx in candidate_indices:
            station_xy = station_coords.iloc[station_idx][["pos_x", "pos_y"]].to_numpy()
            candidates.append((station_ids[station_idx], float(np.linalg.norm(node_xy - station_xy))))
        candidates.sort(key=lambda x: x[1])
        cache[int(node_id)] = candidates
    return cache


def to_indexed_candidates(candidates, station_to_idx, walking_speed):
    indexed = []
    for station_id, distance in candidates:
        station_idx = station_to_idx.get(station_id)
        if station_idx is not None:
            indexed.append((station_idx, distance / walking_speed))
    return indexed


def compute_earliest_arrival(
    rq_time,
    origin_candidates,
    destination_candidates,
    conn_from,
    conn_to,
    conn_trip,
    conn_dep,
    conn_arr,
    n_stations,
    n_trips,
    transfer_buffer_s,
):
    if not origin_candidates or not destination_candidates:
        return np.nan, np.nan

    inf = float("inf")
    large = 10**9
    station_arrival = np.full(n_stations, inf)
    station_boardings = np.full(n_stations, large, dtype=int)
    trip_boardings = np.full(n_trips, large, dtype=int)

    min_ready_time = inf
    for station_idx, access_time in origin_candidates:
        ready_time = float(rq_time) + access_time
        if ready_time < station_arrival[station_idx]:
            station_arrival[station_idx] = ready_time
            station_boardings[station_idx] = 0
        min_ready_time = min(min_ready_time, ready_time)

    start_i = int(np.searchsorted(conn_dep, min_ready_time, side="left"))
    destination_indices = {idx for idx, _ in destination_candidates}
    best_arrival = inf
    best_boardings = large

    for i in range(start_i, len(conn_dep)):
        if conn_dep[i] > best_arrival:
            break
        from_idx = conn_from[i]
        to_idx = conn_to[i]
        trip_idx = conn_trip[i]

        current_trip_boardings = trip_boardings[trip_idx]
        can_board_from_station = station_arrival[from_idx] < inf
        if can_board_from_station:
            buffer_s = 0 if station_boardings[from_idx] == 0 else transfer_buffer_s
            can_board_from_station = conn_dep[i] >= station_arrival[from_idx] + buffer_s

        if current_trip_boardings >= large and not can_board_from_station:
            continue

        boardings = current_trip_boardings
        if can_board_from_station:
            boardings = min(boardings, station_boardings[from_idx] + 1)
        if boardings < trip_boardings[trip_idx]:
            trip_boardings[trip_idx] = boardings

        if conn_arr[i] < station_arrival[to_idx] or (
            conn_arr[i] == station_arrival[to_idx] and boardings < station_boardings[to_idx]
        ):
            station_arrival[to_idx] = conn_arr[i]
            station_boardings[to_idx] = boardings
            if to_idx in destination_indices:
                for dest_idx, egress_time in destination_candidates:
                    if dest_idx == to_idx:
                        total_arrival = conn_arr[i] + egress_time
                        if total_arrival < best_arrival or (
                            total_arrival == best_arrival and boardings < best_boardings
                        ):
                            best_arrival = total_arrival
                            best_boardings = boardings

    if best_arrival == inf:
        return np.nan, np.nan
    total_duration_min = (best_arrival - float(rq_time)) / 60.0
    nr_transfers = max(best_boardings - 1, 0)
    return total_duration_min, nr_transfers


def augment_demand(args):
    preprocessor = RailGTFSODTravelTimePreprocessor(
        gtfs_dir=args.gtfs_dir,
        network_dir=args.network_dir,
        service_date=args.service_date,
        route_types=args.route_types,
        access_radius_m=args.access_radius_m,
        walking_speed=args.walking_speed,
        transfer_buffer_s=args.transfer_buffer_s,
        time_bin_s=args.time_bin_s,
    )
    return preprocessor.augment_demand(
        args.demand,
        output=args.output,
        start_time=args.start_time,
        end_time=args.end_time,
        limit=args.limit,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Add rail-based GTFS PT travel times to a FleetPy demand CSV."
    )
    parser.add_argument(
        "--gtfs-dir",
        default=DEFAULT_GTFS_DIR,
        help="Path to the GTFS directory.",
    )
    parser.add_argument(
        "--network-dir",
        default=os.path.join("data", "networks", "Aimsun_Munich_2020"),
        help="Path to the FleetPy network directory containing base/nodes.csv and base/crs.info.",
    )
    parser.add_argument("--demand", required=True, help="Input FleetPy demand CSV.")
    parser.add_argument("--output", help="Output demand CSV. Defaults to *_railpt.csv next to the input file.")
    parser.add_argument("--service-date", default="20260706", help="GTFS service date, formatted as YYYYMMDD.")
    parser.add_argument(
        "--route-types",
        default=",".join(str(x) for x in DEFAULT_RAIL_ROUTE_TYPES),
        help="Comma-separated GTFS route_type values. Default is 0,1,2 for Tram/S-Bahn, U-Bahn, and Regionalbahn.",
    )
    parser.add_argument("--access-radius-m", type=float, default=1000.0, help="Station search radius in meters.")
    parser.add_argument("--walking-speed", type=float, default=1.4, help="Access/egress walking speed in m/s.")
    parser.add_argument(
        "--transfer-buffer-s",
        type=float,
        default=120.0,
        help="Minimum transfer buffer before boarding the next rail vehicle, in seconds.",
    )
    parser.add_argument(
        "--time-bin-s",
        type=int,
        default=0,
        help="Request time bin used for OD cache keys in seconds; 0 calculates each request at its exact time.",
    )
    parser.add_argument(
        "--start-time",
        type=float,
        help="Optional lower request time bound to process; rows outside the window are preserved.",
    )
    parser.add_argument(
        "--end-time",
        type=float,
        help="Optional upper request time bound to process; rows outside the window are preserved.",
    )
    parser.add_argument("--limit", type=int, help="Optional row limit for quick test runs.")
    args = parser.parse_args()
    augment_demand(args)


if __name__ == "__main__":
    main()
