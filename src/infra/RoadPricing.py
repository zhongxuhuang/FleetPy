import ast
import logging
import math
import os

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
                    density = vehicle_count / network_length_km
                    base_coeff = _get_zone_value(self.base_coefficients, zone_id, 0.0)
                    max_coeff = _get_zone_value(self.max_coefficients, zone_id, float("inf"))
                    coeff = min(base_coeff * density / critical_density, max_coeff)
            self.current_coefficients[zone_id] = coeff
            records.append({
                "sim_time": sim_time,
                "zone_id": zone_id,
                "pricing_mode": self.policy_name,
                "vehicle_count": vehicle_count,
                "density_veh_per_km": density,
                "critical_density_veh_per_km": critical_density,
                "toll_coeff": coeff,
                "fallback": fallback_reason,
            })
        self.zone_system.set_current_toll_coefficients(self.current_coefficients)
        self._write_records(records)
        return True


def load_road_pricing_policy(zone_system, scenario_parameters, dir_names):
    pricing_method = scenario_parameters.get(G_RP_PRICING_M)
    if pricing_method is None or zone_system is None:
        return None
    pricing_method = str(pricing_method)
    if pricing_method in {"StaticZoneDistancePricing", "static"}:
        return StaticZoneDistancePricing(zone_system, scenario_parameters, dir_names)
    if pricing_method in {"MyopicMFDZoneDistancePricing", "myopic_mfd"}:
        return MyopicMFDZoneDistancePricing(zone_system, scenario_parameters, dir_names)
    raise EnvironmentError(f"Unknown road pricing method {pricing_method}.")
