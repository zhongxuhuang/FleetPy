import ast
import logging
import math
import os
from collections import defaultdict

import pandas as pd

from src.misc.globals import *

LOG = logging.getLogger(__name__)


def _parse_mapping(value):
    """Return a dict from a dict-like scenario value."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value.copy()
    if isinstance(value, str):
        if value.strip() == "":
            return {}
        return ast.literal_eval(value)
    return {}


def _normalize_zone_mapping(mapping):
    """Convert zone keys to ints when possible and values to floats."""
    normalized = {}
    for zone_id, value in mapping.items():
        try:
            zone_key = int(zone_id)
        except (TypeError, ValueError):
            zone_key = zone_id
        normalized[zone_key] = float(value)
    return normalized


def _read_zone_value_file(file_path, value_columns):
    """Read a zone_id,value CSV into a mapping."""
    if file_path is None:
        return {}
    df = pd.read_csv(file_path)
    zone_col = G_ZONE_ZID if G_ZONE_ZID in df.columns else "zone_id"
    value_col = None
    for col in value_columns:
        if col in df.columns:
            value_col = col
            break
    if value_col is None:
        raise KeyError(f"{file_path} must contain one of {value_columns}.")
    return _normalize_zone_mapping(df.set_index(zone_col)[value_col].to_dict())


def _get_zone_value(value, zone_id, default=0.0):
    if isinstance(value, dict):
        return float(value.get(zone_id, default))
    return float(value)


class RoadPricingPolicy:
    """Base class for zone-based road pricing policies."""

    policy_name = "none"

    def __init__(self, zone_system, scenario_parameters, dir_names):
        self.zone_system = zone_system
        self.scenario_parameters = scenario_parameters
        self.dir_names = dir_names
        self.output_dir = dir_names.get(G_DIR_OUTPUT)
        self.record_f = None
        if self.output_dir is not None:
            self.record_f = os.path.join(self.output_dir, "5_road_pricing_info.csv")
        self._record_header_written = False
        register_policy = getattr(zone_system, "set_road_pricing_policy", None)
        if callable(register_policy):
            register_policy(self)

    def update(self, sim_time, routing_engine):
        return False

    def _write_records(self, records):
        if not records or self.record_f is None:
            return
        df = pd.DataFrame(records)
        write_header = not os.path.isfile(self.record_f) and not self._record_header_written
        df.to_csv(self.record_f, mode="a", header=write_header, index=False)
        self._record_header_written = True


class StaticZoneDistancePricing(RoadPricingPolicy):
    """Fixed cents-per-meter toll coefficients by zone."""

    policy_name = "static"

    def __init__(self, zone_system, scenario_parameters, dir_names):
        super().__init__(zone_system, scenario_parameters, dir_names)
        self.static_coefficients = self._load_static_coefficients()

    def _load_static_coefficients(self):
        coefficient_file = self.scenario_parameters.get(G_RP_STATIC_TOLL_F)
        if coefficient_file is not None and not os.path.isabs(coefficient_file):
            coefficient_file = os.path.join(self.dir_names.get(G_DIR_ZONES, ""), coefficient_file)
        coefficients = _read_zone_value_file(coefficient_file, ["toll_coeff", "toll_coefficient", "toll_cent_per_meter"])
        coefficients.update(_normalize_zone_mapping(_parse_mapping(self.scenario_parameters.get(G_RP_STATIC_TOLL_COEFF))))
        if not coefficients and self.scenario_parameters.get(G_TOLL_COST_SCALE) is not None:
            self.zone_system.set_current_toll_cost_scale_factor(float(self.scenario_parameters[G_TOLL_COST_SCALE]))
            self.zone_system.set_current_toll_costs(use_pre_defined_zone_scales=True)
            coefficients = self.zone_system.current_toll_coefficients.copy()
        return coefficients

    def update(self, sim_time, routing_engine):
        self.zone_system.set_current_toll_coefficients(self.static_coefficients)
        records = []
        for zone_id in self.zone_system.get_all_zones():
            records.append({
                "sim_time": sim_time,
                "zone_id": zone_id,
                "pricing_mode": self.policy_name,
                "vehicle_count": None,
                "exogenous_vehicle_count": None,
                "mfd_vehicle_count": None,
                "density_veh_per_km": None,
                "critical_density_veh_per_km": None,
                "toll_coeff": self.static_coefficients.get(zone_id, 0.0),
                "fallback": "",
            })
        self._write_records(records)
        return True


class MyopicMFDZoneDistancePricing(RoadPricingPolicy):
    """MFD-density-responsive cents-per-metre toll coefficients by zone.

    The coefficient is linear in the density ratio, so it is below the base
    coefficient in uncongested conditions, equals it at critical density, and
    rises above it after the MFD maximum-flow point.  ``rp_max_toll_coeff``
    limits that increase.
    """

    policy_name = "myopic_mfd"

    def __init__(self, zone_system, scenario_parameters, dir_names):
        super().__init__(zone_system, scenario_parameters, dir_names)
        self.update_interval = float(scenario_parameters.get(G_RP_UPDATE_INT, 300))
        self.fallback = scenario_parameters.get(G_RP_FALLBACK, "keep")
        self.last_update_time = None
        self.base_coefficients = self._load_coefficients(G_RP_BASE_TOLL_COEFF, default=0.0)
        self.max_coefficients = self._load_coefficients(G_RP_MAX_TOLL_COEFF, default=float("inf"))
        self.current_coefficients = {}

    def _load_coefficients(self, parameter_name, default):
        value = self.scenario_parameters.get(parameter_name, default)
        mapping = _parse_mapping(value)
        if mapping:
            return _normalize_zone_mapping(mapping)
        return float(value)

    def _is_update_time(self, sim_time):
        if self.last_update_time is None:
            return True
        return sim_time - self.last_update_time >= self.update_interval

    def _get_critical_density(self, zone_id):
        """Return the parabolic-MFD critical density in vehicles per kilometre.

        For ``q(k) = v_free * k - gamma * k**2``, maximum flow occurs at
        ``k_critical = v_free / (2 * gamma)``. ``NetworkZoneSystem`` retains
        ``v_free`` in km/h and ``gamma`` in the units used by the shared
        ``mfd_parameters.csv`` input, yielding critical density in veh/km.
        """
        mfd_parameters = getattr(self.zone_system, "mfd_parameters", {})
        parameter = mfd_parameters.get(zone_id)
        if parameter is None:
            return None
        try:
            free_speed_kmh = float(parameter["v"])
            gamma = float(parameter["gamma"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            not math.isfinite(free_speed_kmh)
            or not math.isfinite(gamma)
            or free_speed_kmh <= 0
            or gamma <= 0
        ):
            return None
        return free_speed_kmh / (2.0 * gamma)

    def update(self, sim_time, routing_engine):
        if not self._is_update_time(sim_time):
            return False
        self.last_update_time = sim_time
        count_getter = getattr(routing_engine, "get_current_zone_vehicle_counts", None)
        zone_vehicle_counts = count_getter() if callable(count_getter) else None
        zone_lengths_km = getattr(self.zone_system, "mfd_network_lengths_km", {})
        records = []
        for zone_id in self.zone_system.get_all_zones():
            fallback_reason = ""
            old_coeff = self.current_coefficients.get(zone_id, 0.0)
            vehicle_count = None
            exogenous_vehicle_count = None
            mfd_vehicle_count = None
            density = None
            critical_density = self._get_critical_density(zone_id)
            if critical_density is None:
                if self.fallback == "zero":
                    coeff = 0.0
                else:
                    coeff = old_coeff
                fallback_reason = "missing_mfd_parameters"
            elif zone_vehicle_counts is None:
                if self.fallback == "zero":
                    coeff = 0.0
                else:
                    coeff = old_coeff
                fallback_reason = "missing_zone_vehicle_counts"
            else:
                network_length_km = zone_lengths_km.get(zone_id)
                vehicle_count = zone_vehicle_counts.get(zone_id, 0.0)
                try:
                    vehicle_count = max(float(vehicle_count), 0.0)
                    network_length_km = float(network_length_km)
                except (TypeError, ValueError):
                    network_length_km = None
                if (
                    network_length_km is None
                    or not math.isfinite(network_length_km)
                    or network_length_km <= 0
                    or not math.isfinite(vehicle_count)
                ):
                    if self.fallback == "zero":
                        coeff = 0.0
                    else:
                        coeff = old_coeff
                    fallback_reason = "missing_mfd_network_length"
                else:
                    try:
                        exogenous_count_getter = getattr(
                            self.zone_system, "get_mfd_exogenous_vehicle_count", None
                        )
                        exogenous_vehicle_count = (
                            float(exogenous_count_getter(zone_id))
                            if callable(exogenous_count_getter) else 0.0
                        )
                        mfd_count_getter = getattr(self.zone_system, "get_mfd_vehicle_count", None)
                        mfd_vehicle_count = (
                            float(mfd_count_getter(zone_id, vehicle_count))
                            if callable(mfd_count_getter) else vehicle_count + exogenous_vehicle_count
                        )
                        density_getter = getattr(self.zone_system, "get_mfd_density", None)
                        density = (
                            density_getter(zone_id, vehicle_count)
                            if callable(density_getter) else mfd_vehicle_count / network_length_km
                        )
                        if density is None or not math.isfinite(density):
                            raise ValueError("invalid MFD density")
                    except (TypeError, ValueError):
                        if self.fallback == "zero":
                            coeff = 0.0
                        else:
                            coeff = old_coeff
                        fallback_reason = "invalid_mfd_density"
                    else:
                        base_coeff = _get_zone_value(self.base_coefficients, zone_id, 0.0)
                        max_coeff = _get_zone_value(self.max_coefficients, zone_id, float("inf"))
                        coeff = min(base_coeff * density / critical_density, max_coeff)
            self.current_coefficients[zone_id] = coeff
            records.append({
                "sim_time": sim_time,
                "zone_id": zone_id,
                "pricing_mode": self.policy_name,
                "vehicle_count": vehicle_count,
                "exogenous_vehicle_count": exogenous_vehicle_count,
                "mfd_vehicle_count": mfd_vehicle_count,
                "density_veh_per_km": density,
                "critical_density_veh_per_km": critical_density,
                "toll_coeff": coeff,
                "fallback": fallback_reason,
            })
        self.zone_system.set_current_toll_coefficients(self.current_coefficients)
        self._write_records(records)
        return True


class ScheduledZoneTariffPricing(RoadPricingPolicy):
    """PV-only cordon or distance tariffs read from a fixed zone-time schedule.

    The CSV fixes tariff bands and values. ``mfd_speed`` reads the routing
    engine's already-computed speed snapshot once per network update; it never
    evaluates an MFD equation in the pricing layer.
    """

    policy_name = "scheduled_zone_tariff"
    _REQUIRED_COLUMNS = {
        "charge_type", "tariff_basis", "zone_id", "time_start", "time_end",
        "speed_min_kmh", "speed_max_kmh", "speed_band", "entry_fee_cent",
        "distance_rate_cent_per_m",
    }
    _CHARGE_TYPES = {"cordon", "distance"}
    _TARIFF_BASES = {"time_of_day", "mfd_speed", "reference_mfd_speed"}
    _DAY_SECONDS = 24 * 3600

    def __init__(self, zone_system, scenario_parameters, dir_names):
        super().__init__(zone_system, scenario_parameters, dir_names)
        self.charge_type = str(scenario_parameters.get(G_RP_CHARGE_TYPE, ""))
        self.tariff_basis = str(scenario_parameters.get(G_RP_TARIFF_BASIS, ""))
        if self.tariff_basis == "reference_mfd_speed":
            self.tariff_basis = "mfd_speed"
        if self.charge_type not in self._CHARGE_TYPES:
            raise ValueError(
                f"{G_RP_CHARGE_TYPE} must be one of {sorted(self._CHARGE_TYPES)}."
            )
        if self.tariff_basis not in self._TARIFF_BASES:
            raise ValueError(
                f"{G_RP_TARIFF_BASIS} must be one of {sorted(self._TARIFF_BASES)}."
            )
        self.schedule_file = self._resolve_schedule_file(
            scenario_parameters.get(G_RP_TARIFF_SCHEDULE_F)
        )
        self.schedule_by_zone = self._load_schedule()
        self.active_tariffs = {}
        self.active_mfd_speeds_kmh = {}
        self.last_tariff_update_time = None
        self.set_update_interval(scenario_parameters.get(G_RP_TARIFF_UPDATE_INT, 300))
        # The general route-toll interface is also used for MoD fares.  Clearing
        # its legacy coefficients makes this new policy PV-only by construction.
        self.zone_system.set_current_toll_coefficients({})

    def set_update_interval(self, update_interval):
        """Set the MFD-speed tariff refresh interval in seconds."""
        try:
            update_interval = float(update_interval)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{G_RP_TARIFF_UPDATE_INT} must be a positive number of seconds.") from exc
        if not math.isfinite(update_interval) or update_interval <= 0:
            raise ValueError(f"{G_RP_TARIFF_UPDATE_INT} must be a positive number of seconds.")
        self.update_interval = update_interval

    def _is_tariff_update_time(self, sim_time):
        return (
            self.last_tariff_update_time is None
            or float(sim_time) - self.last_tariff_update_time >= self.update_interval
        )

    def _resolve_schedule_file(self, configured_file):
        if not configured_file:
            raise ValueError(f"{G_RP_TARIFF_SCHEDULE_F} is required for scheduled zone tariffs.")
        if os.path.isabs(configured_file):
            return configured_file
        zone_dir = self.dir_names.get(G_DIR_ZONES, "")
        zone_candidate = os.path.join(zone_dir, configured_file)
        if os.path.isfile(zone_candidate):
            return zone_candidate
        main_dir = self.dir_names.get(G_DIR_MAIN, "")
        main_candidate = os.path.join(main_dir, configured_file)
        if os.path.isfile(main_candidate):
            return main_candidate
        return zone_candidate

    @staticmethod
    def _as_optional_float(value, column, row_number):
        if pd.isna(value) or value == "":
            return None
        try:
            converted = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Schedule row {row_number}: {column} must be numeric.") from exc
        if not math.isfinite(converted):
            raise ValueError(f"Schedule row {row_number}: {column} must be finite.")
        return converted

    def _load_schedule(self):
        if not os.path.isfile(self.schedule_file):
            raise FileNotFoundError(f"Road-pricing schedule not found: {self.schedule_file}")
        try:
            schedule = pd.read_csv(self.schedule_file)
        except Exception as exc:
            raise ValueError(f"Could not read road-pricing schedule {self.schedule_file}: {exc}") from exc
        missing = self._REQUIRED_COLUMNS - set(schedule.columns)
        if missing:
            raise ValueError(
                f"Road-pricing schedule {self.schedule_file} is missing columns: {sorted(missing)}"
            )

        selected = schedule.loc[
            (schedule["charge_type"] == self.charge_type)
            & (schedule["tariff_basis"].replace({"reference_mfd_speed": "mfd_speed"}) == self.tariff_basis)
        ].copy()
        if selected.empty:
            raise ValueError(
                f"Road-pricing schedule has no rows for {self.charge_type}/"
                f"{self.tariff_basis}."
            )
        valid_zones = set(self.zone_system.get_all_zones())
        by_zone = defaultdict(list)
        for row_number, row in selected.iterrows():
            display_row = row_number + 2
            try:
                zone_id = int(row["zone_id"])
                time_start = float(row["time_start"])
                time_end = float(row["time_end"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Schedule row {display_row} must contain integer zone_id and numeric time bounds."
                ) from exc
            if zone_id not in valid_zones:
                raise ValueError(f"Schedule row {display_row} references unknown zone {zone_id}.")
            if not (0 <= time_start < time_end <= self._DAY_SECONDS):
                raise ValueError(
                    f"Schedule row {display_row} must satisfy 0 <= time_start < time_end <= 86400."
                )
            entry_fee = self._as_optional_float(row["entry_fee_cent"], "entry_fee_cent", display_row)
            distance_rate = self._as_optional_float(
                row["distance_rate_cent_per_m"], "distance_rate_cent_per_m", display_row
            )
            required_value = entry_fee if self.charge_type == "cordon" else distance_rate
            required_column = "entry_fee_cent" if self.charge_type == "cordon" else "distance_rate_cent_per_m"
            if required_value is None or required_value < 0:
                raise ValueError(
                    f"Schedule row {display_row}: {required_column} must be finite and non-negative."
                )
            speed_min = self._as_optional_float(row["speed_min_kmh"], "speed_min_kmh", display_row)
            speed_max = self._as_optional_float(row["speed_max_kmh"], "speed_max_kmh", display_row)
            if self.tariff_basis == "mfd_speed":
                is_outside = str(row["speed_band"]) == "outside"
                if is_outside and speed_min is None and speed_max is None:
                    pass
                elif speed_min is None or speed_min < 0 or (speed_max is not None and speed_max <= speed_min):
                    raise ValueError(
                        f"Schedule row {display_row}: mfd_speed rows require speed_min_kmh and a "
                        "larger speed_max_kmh, unless they are the outside row."
                    )
            by_zone[zone_id].append({
                "time_start": time_start,
                "time_end": time_end,
                "entry_fee_cent": entry_fee,
                "distance_rate_cent_per_m": distance_rate,
                "speed_min_kmh": speed_min,
                "speed_max_kmh": speed_max,
                "speed_band": "" if pd.isna(row["speed_band"]) else str(row["speed_band"]),
            })

        for zone_id in valid_zones:
            rows = sorted(by_zone.get(zone_id, []), key=lambda item: item["time_start"])
            if not rows:
                raise ValueError(f"Road-pricing schedule is missing zone {zone_id}.")
            if self.tariff_basis == "time_of_day":
                for previous, current in zip(rows, rows[1:]):
                    if current["time_start"] < previous["time_end"]:
                        raise ValueError(f"Road-pricing schedule has overlapping rows for zone {zone_id}.")
            else:
                rows.sort(key=lambda item: float("inf") if item["speed_min_kmh"] is None else item["speed_min_kmh"])
                self._validate_speed_bands(zone_id, rows)
            by_zone[zone_id] = rows
        if self.tariff_basis == "time_of_day":
            self._validate_coverage(by_zone)
        return dict(by_zone)

    @staticmethod
    def _validate_speed_bands(zone_id, rows):
        outside_rows = [row for row in rows if row["speed_min_kmh"] is None]
        if outside_rows:
            if len(rows) != 1:
                raise ValueError(f"Outside zone {zone_id} must have exactly one tariff row.")
            return
        if rows[0]["speed_min_kmh"] != 0:
            raise ValueError(f"MFD speed bands for zone {zone_id} must start at 0 km/h.")
        previous_max = 0.0
        for index, row in enumerate(rows):
            if row["speed_min_kmh"] != previous_max:
                raise ValueError(f"MFD speed bands for zone {zone_id} must be contiguous and non-overlapping.")
            maximum = row["speed_max_kmh"]
            if maximum is None:
                if index != len(rows) - 1:
                    raise ValueError(f"Only the final MFD speed band for zone {zone_id} may be unbounded.")
                return
            previous_max = maximum
        raise ValueError(f"MFD speed bands for zone {zone_id} must end with an unbounded band.")

    @staticmethod
    def _covers_interval(rows, interval_start, interval_end):
        covered_until = interval_start
        for row in rows:
            if row["time_end"] <= covered_until:
                continue
            if row["time_start"] > covered_until:
                return False
            covered_until = min(interval_end, row["time_end"])
            if covered_until >= interval_end:
                return True
        return covered_until >= interval_end

    def _validate_coverage(self, by_zone):
        """Require every selected zone tariff to cover the configured simulation horizon."""
        start_time = self.scenario_parameters.get(G_SIM_START_TIME)
        end_time = self.scenario_parameters.get(G_SIM_END_TIME)
        if start_time is None or end_time is None:
            return
        try:
            start_time, end_time = float(start_time), float(end_time)
        except (TypeError, ValueError) as exc:
            raise ValueError("Simulation start and end times must be numeric for tariff validation.") from exc
        if end_time <= start_time:
            raise ValueError("Simulation end time must be after start time for tariff validation.")
        if end_time - start_time >= self._DAY_SECONDS:
            intervals = [(0.0, float(self._DAY_SECONDS))]
        else:
            start_tod, end_tod = start_time % self._DAY_SECONDS, end_time % self._DAY_SECONDS
            intervals = [(start_tod, end_tod)] if start_tod < end_tod else [
                (start_tod, float(self._DAY_SECONDS)), (0.0, end_tod)
            ]
        for zone_id, rows in by_zone.items():
            if any(not self._covers_interval(rows, start, end) for start, end in intervals):
                raise ValueError(
                    f"Road-pricing schedule does not cover the simulation horizon for zone {zone_id}."
                )

    def _row_for_time(self, zone_id, simulation_time):
        time_of_day = float(simulation_time) % self._DAY_SECONDS
        for row in self.schedule_by_zone[zone_id]:
            if row["time_start"] <= time_of_day < row["time_end"]:
                return row
        raise ValueError(
            f"Road-pricing schedule has no {self.charge_type}/{self.tariff_basis} tariff "
            f"for zone {zone_id} at simulation time {simulation_time}."
        )

    def _row_for_mfd_speed(self, zone_id, speed_kmh):
        rows = self.schedule_by_zone[zone_id]
        if speed_kmh is None:
            outside_rows = [row for row in rows if row["speed_min_kmh"] is None]
            if len(outside_rows) == 1:
                return outside_rows[0]
            raise ValueError(f"No current MFD speed is available for priced zone {zone_id}.")
        for row in rows:
            lower, upper = row["speed_min_kmh"], row["speed_max_kmh"]
            if lower is not None and speed_kmh >= lower and (upper is None or speed_kmh < upper):
                return row
        raise ValueError(f"MFD speed {speed_kmh} km/h has no tariff band in zone {zone_id}.")

    @staticmethod
    def _route_zone_segments(zone_system, routing_engine, route):
        """Return contiguous origin-zone route segments with their TT and length."""
        segments = []
        for origin, destination in zip(route, route[1:]):
            zone_id = zone_system.get_zone_from_node(origin)
            section_tt, section_distance = routing_engine.get_section_infos(origin, destination)
            section_tt = float(section_tt)
            section_distance = float(section_distance)
            if segments and segments[-1]["zone_id"] == zone_id:
                segments[-1]["travel_time"] += section_tt
                segments[-1]["distance"] += section_distance
            else:
                segments.append({
                    "zone_id": zone_id,
                    "travel_time": section_tt,
                    "distance": section_distance,
                })
        return segments

    def get_pv_route_toll_cost(self, routing_engine, sim_time, route):
        """Quote the fixed scheduled tariff for one PV route, in cent."""
        if not route or len(route) < 2:
            return 0
        total_cent = 0.0
        projected_time = float(sim_time)
        previous_zone = None
        for segment in self._route_zone_segments(self.zone_system, routing_engine, route):
            zone_id = segment["zone_id"]
            tariff = (
                self._row_for_time(zone_id, projected_time)
                if self.tariff_basis == "time_of_day"
                else self.active_tariffs[zone_id]
            )
            if self.charge_type == "distance":
                total_cent += tariff["distance_rate_cent_per_m"] * segment["distance"]
            elif previous_zone is not None and previous_zone != zone_id:
                total_cent += tariff["entry_fee_cent"]
            projected_time += segment["travel_time"]
            previous_zone = zone_id
        return int(round(total_cent))

    def update(self, sim_time, routing_engine):
        """Write the active scheduled tariffs for audit; no traffic-price update occurs."""
        if self.tariff_basis == "mfd_speed" and not self._is_tariff_update_time(sim_time):
            return False
        if self.tariff_basis == "mfd_speed":
            speed_getter = getattr(routing_engine, "get_current_zone_mfd_speeds", None)
            if not callable(speed_getter):
                raise ValueError("mfd_speed tariffs require get_current_zone_mfd_speeds().")
            current_speeds = speed_getter()
            self.active_mfd_speeds_kmh = {
                zone_id: float(speed) * 3.6 for zone_id, speed in current_speeds.items()
            }
            self.active_tariffs = {
                zone_id: self._row_for_mfd_speed(zone_id, self.active_mfd_speeds_kmh.get(zone_id))
                for zone_id in self.zone_system.get_all_zones()
            }
            self.last_tariff_update_time = float(sim_time)
        records = []
        for zone_id in self.zone_system.get_all_zones():
            tariff = (
                self._row_for_time(zone_id, sim_time)
                if self.tariff_basis == "time_of_day"
                else self.active_tariffs[zone_id]
            )
            records.append({
                "sim_time": sim_time,
                "zone_id": zone_id,
                "pricing_mode": self.policy_name,
                "charge_type": self.charge_type,
                "tariff_basis": self.tariff_basis,
                "mfd_speed_kmh": self.active_mfd_speeds_kmh.get(zone_id),
                "speed_band": tariff["speed_band"],
                "entry_fee_cent": tariff["entry_fee_cent"],
                "distance_rate_cent_per_m": tariff["distance_rate_cent_per_m"],
            })
        self._write_records(records)
        return True


def load_road_pricing_policy(zone_system, scenario_parameters, dir_names):
    pricing_method = scenario_parameters.get(G_RP_PRICING_M)
    if pricing_method is None or zone_system is None:
        return None
    pricing_method = str(pricing_method).strip()
    if pricing_method.lower() in {"", "none", "off", "disabled"}:
        return None
    if pricing_method in {"StaticZoneDistancePricing", "static"}:
        return StaticZoneDistancePricing(zone_system, scenario_parameters, dir_names)
    if pricing_method in {"MyopicMFDZoneDistancePricing", "myopic_mfd"}:
        return MyopicMFDZoneDistancePricing(zone_system, scenario_parameters, dir_names)
    if pricing_method in {"ScheduledZoneTariffPricing", "scheduled_zone_tariff"}:
        return ScheduledZoneTariffPricing(zone_system, scenario_parameters, dir_names)
    raise EnvironmentError(f"Unknown road pricing method {pricing_method}.")
