import ast
import logging
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
                "k_current": None,
                "k_critical": None,
                "toll_coeff": self.static_coefficients.get(zone_id, 0.0),
                "fallback": "",
            })
        self._write_records(records)
        return True


class MyopicMFDZoneDistancePricing(RoadPricingPolicy):
    """Density-responsive cents-per-meter toll coefficients by zone."""

    policy_name = "myopic_mfd"

    def __init__(self, zone_system, scenario_parameters, dir_names):
        super().__init__(zone_system, scenario_parameters, dir_names)
        self.update_interval = float(scenario_parameters.get(G_RP_UPDATE_INT, 300))
        self.fallback = scenario_parameters.get(G_RP_FALLBACK, "keep")
        self.last_update_time = None
        self.k_critical = self._load_k_critical()
        self.base_coefficients = self._load_coefficients(G_RP_BASE_TOLL_COEFF, default=0.0)
        self.max_coefficients = self._load_coefficients(G_RP_MAX_TOLL_COEFF, default=float("inf"))
        self.current_coefficients = {}

    def _load_k_critical(self):
        k_file = self.scenario_parameters.get(G_RP_K_CRIT_F)
        if k_file is not None and not os.path.isabs(k_file):
            k_file = os.path.join(self.dir_names.get(G_DIR_ZONES, ""), k_file)
        k_crit = _read_zone_value_file(k_file, ["k_critical", "critical_density", "critical_k"])
        k_crit.update(_normalize_zone_mapping(_parse_mapping(self.scenario_parameters.get(G_RP_K_CRIT))))
        return k_crit

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

    def update(self, sim_time, routing_engine):
        if not self._is_update_time(sim_time):
            return False
        self.last_update_time = sim_time
        if hasattr(routing_engine, "_update_current_zone_vehicle_counts"):
            zone_counts = routing_engine._update_current_zone_vehicle_counts(sim_time)
        else:
            zone_counts = getattr(routing_engine, "current_total_zone_vehicle_counts", {})
        records = []
        for zone_id in self.zone_system.get_all_zones():
            fallback_reason = ""
            k_current = zone_counts.get(zone_id)
            k_critical = self.k_critical.get(zone_id)
            old_coeff = self.current_coefficients.get(zone_id, 0.0)
            if k_current is None or k_critical is None or k_critical <= 0:
                if self.fallback == "zero":
                    coeff = 0.0
                else:
                    coeff = old_coeff
                fallback_reason = "missing_mfd_state"
            else:
                base_coeff = _get_zone_value(self.base_coefficients, zone_id, 0.0)
                max_coeff = _get_zone_value(self.max_coefficients, zone_id, float("inf"))
                coeff = base_coeff * max(0.0, float(k_current) / float(k_critical))
                coeff = min(coeff, max_coeff)
            self.current_coefficients[zone_id] = coeff
            records.append({
                "sim_time": sim_time,
                "zone_id": zone_id,
                "pricing_mode": self.policy_name,
                "k_current": k_current,
                "k_critical": k_critical,
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
