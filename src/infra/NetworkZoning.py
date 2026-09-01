# -------------------------------------------------------------------------------------------------------------------- #
# standard distribution imports
# -----------------------------
import os
import logging

# additional module imports (> requirements)
# ------------------------------------------
import pandas as pd
import numpy as np
from scipy.sparse import load_npz

# src imports
# -----------
from src.infra.Zoning import ZoneSystem
# -------------------------------------------------------------------------------------------------------------------- #
# global variables
# ----------------
from src.misc.globals import *
LOG = logging.getLogger(__name__)

NOON = 12*3600

class NetworkZoneSystem(ZoneSystem):
    def __init__(self, zone_network_dir, scenario_parameters, dir_names):
        super().__init__(zone_network_dir, scenario_parameters, dir_names)
        # # edge specific information -> not necessary at the moment
        # edge_zone_f = os.path.join(zone_network_dir, "edge_zone_info.csv")
        # self.edge_zone_df = pd.read_csv(edge_zone_f)
        self.current_toll_cost_scale = 0
        self.current_toll_coefficients = {}
        self.road_pricing_policy = None
        self.current_park_costs = {}
        self.current_park_search_durations = {}
        self.network_mode = str(
            scenario_parameters.get(G_NETWORK_MODE, "dynamic_mfd")
        ).strip().lower()
        if self.network_mode not in {"static", "dynamic_mfd"}:
            raise ValueError(
                f"{G_NETWORK_MODE} must be one of ['dynamic_mfd', 'static'], "
                f"got {self.network_mode!r}"
            )
        self.mfd_parameters_file = None
        self.mfd_parameters = (
            self._load_mfd_parameters(scenario_parameters)
            if self.network_mode == "dynamic_mfd" else {}
        )
        self.mfd_exogenous_density_file = None
        self.mfd_exogenous_density_profiles = self._load_mfd_exogenous_density_profiles(
            scenario_parameters
        )
        # Filled by the routing engine once it has assigned network edges to
        # zones. The fitted MFDs use density [veh/km], whereas FleetPy tracks
        # an absolute vehicle count per zone.
        self.mfd_network_lengths_km = {}

    def _load_mfd_parameters(self, scenario_parameters):
        """Load the configured parabolic MFD parameters for dynamic mode.

        The file is resolved relative to the general zone directory and must
        contain ``zone_id``,
        ``mfd_type``, ``v_kmh``, and ``gamma``. Currently, ``parabolic`` is
        the only supported MFD type and represents
        ``q(k) = v_kmh * k - gamma * k**2``.
        """
        configured_name = scenario_parameters.get(G_MFD_PARAMETERS_F, "mfd_parameters.csv")
        if configured_name is None or pd.isna(configured_name) or not str(configured_name).strip():
            raise ValueError(
                f"{G_MFD_PARAMETERS_F} is required when {G_NETWORK_MODE}=dynamic_mfd"
            )
        configured_name = str(configured_name).strip()
        mfd_parameters_f = (
            configured_name if os.path.isabs(configured_name)
            else os.path.join(self.zone_general_dir, configured_name)
        )
        if not os.path.isfile(mfd_parameters_f):
            raise FileNotFoundError(f"MFD parameter file does not exist: {mfd_parameters_f}")

        try:
            mfd_df = pd.read_csv(mfd_parameters_f)
        except Exception as exc:
            raise ValueError(
                f"Could not read MFD parameter file {mfd_parameters_f}: {exc}"
            ) from exc

        required_columns = {"zone_id", "mfd_type", "v_kmh", "gamma"}
        missing_columns = required_columns - set(mfd_df.columns)
        if missing_columns:
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} is missing required "
                f"columns: {sorted(missing_columns)}"
            )
        if mfd_df.empty:
            raise ValueError(f"MFD parameter file {mfd_parameters_f} is empty")

        try:
            zone_ids = pd.to_numeric(mfd_df["zone_id"], errors="raise")
            v_kmh = pd.to_numeric(mfd_df["v_kmh"], errors="raise")
            gamma = pd.to_numeric(mfd_df["gamma"], errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} contains non-numeric "
                "zone_id, v_kmh, or gamma values"
            ) from exc

        if (
            not np.isfinite(zone_ids).all()
            or not np.isfinite(v_kmh).all()
            or not np.isfinite(gamma).all()
        ):
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} contains non-finite values"
            )
        if not np.equal(zone_ids, np.floor(zone_ids)).all():
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} contains non-integer zone IDs"
            )

        zone_ids = zone_ids.astype(int)
        if zone_ids.duplicated().any():
            duplicates = sorted(zone_ids[zone_ids.duplicated()].unique().tolist())
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} contains duplicate zone IDs: "
                f"{duplicates}"
            )
        unknown_zone_ids = sorted(set(zone_ids) - set(self.zones))
        if unknown_zone_ids:
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} references unknown zone IDs: "
                f"{unknown_zone_ids}"
            )
        if (v_kmh <= 0).any() or (gamma <= 0).any():
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} requires v_kmh and gamma to be positive"
            )

        mfd_types = mfd_df["mfd_type"].astype(str).str.strip().str.lower()
        unsupported_mfd_types = sorted(set(mfd_types) - {"parabolic"})
        if unsupported_mfd_types:
            raise ValueError(
                f"MFD parameter file {mfd_parameters_f} contains unsupported MFD types: "
                f"{unsupported_mfd_types}"
            )

        self.mfd_parameters_file = mfd_parameters_f
        return {
            zone_id: {
                "mfd_type": mfd_type,
                "v": float(v),
                "gamma": float(gamma_value),
            }
            for zone_id, mfd_type, v, gamma_value in zip(zone_ids, mfd_types, v_kmh, gamma)
        }

    def _load_mfd_exogenous_density_profiles(self, scenario_parameters):
        """Load optional time-varying exogenous MFD densities.

        The configured file is resolved relative to the network-specific zone
        directory. It contains final densities in veh/km; the reference and
        scale columns are retained and validated for calibration auditability.
        """
        exogenous_name = scenario_parameters.get(G_MFD_EXOGENOUS_DENSITY_F)
        if exogenous_name is None or pd.isna(exogenous_name) or not str(exogenous_name).strip():
            return {}

        exogenous_name = str(exogenous_name).strip()
        exogenous_f = exogenous_name if os.path.isabs(exogenous_name) else os.path.join(
            self.zone_network_dir, exogenous_name
        )
        if not os.path.isfile(exogenous_f):
            raise FileNotFoundError(f"MFD exogenous density file does not exist: {exogenous_f}")

        try:
            exogenous_df = pd.read_csv(exogenous_f)
        except Exception as exc:
            raise ValueError(
                f"Could not read MFD exogenous density file {exogenous_f}: {exc}"
            ) from exc

        required_columns = {
            "simulation_time",
            "zone_id",
            "normalized_profile",
            "reference_density_veh_per_km",
            "zone_scale",
            "exogenous_density_veh_per_km",
        }
        missing_columns = required_columns - set(exogenous_df.columns)
        if missing_columns:
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} is missing required "
                f"columns: {sorted(missing_columns)}"
            )
        if exogenous_df.empty:
            raise ValueError(f"MFD exogenous density file {exogenous_f} is empty")

        numeric_columns = sorted(required_columns)
        try:
            numeric_df = exogenous_df[numeric_columns].apply(pd.to_numeric, errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} contains non-numeric values"
            ) from exc
        if not np.isfinite(numeric_df.to_numpy(dtype=float)).all():
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} contains non-finite values"
            )

        zone_ids = numeric_df["zone_id"]
        if not np.equal(zone_ids, np.floor(zone_ids)).all():
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} contains non-integer zone IDs"
            )
        numeric_df["zone_id"] = zone_ids.astype(int)
        if numeric_df.duplicated(["zone_id", "simulation_time"]).any():
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} contains duplicate zone/time rows"
            )

        configured_zones = set(numeric_df["zone_id"].unique())
        if self.network_mode == "dynamic_mfd":
            mfd_zones = set(self.mfd_parameters)
            unknown_zones = sorted(configured_zones - mfd_zones)
            if unknown_zones:
                raise ValueError(
                    f"MFD exogenous density file {exogenous_f} references zones without an MFD: "
                    f"{unknown_zones}"
                )
            missing_zones = sorted(mfd_zones - configured_zones)
            if missing_zones:
                raise ValueError(
                    f"MFD exogenous density file {exogenous_f} is missing MFD zones: {missing_zones}"
                )
        else:
            unknown_zones = sorted(configured_zones - set(self.zones))
            if unknown_zones:
                raise ValueError(
                    f"MFD exogenous density file {exogenous_f} references unknown zones: "
                    f"{unknown_zones}"
                )

        if (numeric_df["simulation_time"] < 0).any():
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} contains negative simulation times"
            )
        for column in ("reference_density_veh_per_km", "exogenous_density_veh_per_km"):
            if (numeric_df[column] < 0).any():
                raise ValueError(
                    f"MFD exogenous density file {exogenous_f} contains negative {column} values"
                )
        if (numeric_df["zone_scale"] <= 0).any():
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} requires positive zone_scale values"
            )
        profile = numeric_df["normalized_profile"]
        if (profile < 0).any() or (profile > 1 + 1e-9).any():
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} requires normalized_profile in [0, 1]"
            )
        expected_exogenous = (
            numeric_df["reference_density_veh_per_km"] * numeric_df["zone_scale"]
        )
        if not np.allclose(
            numeric_df["exogenous_density_veh_per_km"],
            expected_exogenous,
            rtol=1e-6,
            atol=1e-9,
        ):
            raise ValueError(
                f"MFD exogenous density file {exogenous_f} has inconsistent reference, "
                "zone_scale, and exogenous density values"
            )

        simulation_start = float(scenario_parameters.get(G_SIM_START_TIME, 0))
        simulation_end = float(scenario_parameters.get(G_SIM_END_TIME, simulation_start))
        profiles = {}
        for zone_id, zone_df in numeric_df.groupby("zone_id"):
            zone_df = zone_df.sort_values("simulation_time")
            times = zone_df["simulation_time"].to_numpy(dtype=float)
            if times[0] > simulation_start or times[-1] < simulation_end:
                raise ValueError(
                    f"MFD exogenous density file {exogenous_f} does not cover "
                    f"[{simulation_start}, {simulation_end}] for zone {zone_id}"
                )
            profiles[int(zone_id)] = {
                "simulation_time": times,
                "exogenous_density_veh_per_km": zone_df[
                    "exogenous_density_veh_per_km"
                ].to_numpy(dtype=float),
            }

        self.mfd_exogenous_density_file = exogenous_f
        return profiles

    def set_mfd_network_lengths(self, network_lengths_km):
        """Set zone road lengths used to convert vehicle counts to density.

        :param network_lengths_km: mapping of zone id to assigned directed
            road length in kilometres
        :type network_lengths_km: dict
        """
        self.mfd_network_lengths_km = {
            zone_id: float(length)
            for zone_id, length in network_lengths_km.items()
            if length is not None and np.isfinite(length) and length > 0
        }

    def get_mfd_density(self, zone_id, number_vehicles):
        """Return the density used by the MFD, or ``None`` when unavailable."""
        if zone_id not in self.mfd_parameters:
            return None
        network_length_km = self.mfd_network_lengths_km.get(zone_id)
        if network_length_km is None:
            return None
        return max(float(number_vehicles), 0.0) / network_length_km

    def get_mfd_critical_accumulation(self, zone_id):
        """Return the parabolic-MFD critical accumulation for ``zone_id``.

        For ``q(k) = v * k - gamma * k**2``, the maximum-flow density is
        ``v / (2 * gamma)``. Multiplication by the directed zone network
        length converts this density in vehicles per kilometre to the vehicle
        accumulation used by the routing engine. Zones without an MFD or a
        valid assigned network length return ``None``.
        """
        parameters = self.mfd_parameters.get(zone_id)
        network_length_km = self.mfd_network_lengths_km.get(zone_id)
        if parameters is None or network_length_km is None:
            return None
        critical_density = parameters["v"] / (2.0 * parameters["gamma"])
        return critical_density * network_length_km

    def get_mfd_exogenous_density(self, zone_id, simulation_time):
        """Return the linearly interpolated fixed exogenous density in veh/km."""
        profile = self.mfd_exogenous_density_profiles.get(zone_id)
        if profile is None:
            return 0.0
        return float(np.interp(
            float(simulation_time),
            profile["simulation_time"],
            profile["exogenous_density_veh_per_km"],
        ))

    def get_mfd_exogenous_vehicle_count(self, zone_id, simulation_time):
        """Convert a zone's exogenous density to an equivalent vehicle count."""
        network_length_km = self.mfd_network_lengths_km.get(zone_id)
        if network_length_km is None:
            return 0.0
        return self.get_mfd_exogenous_density(zone_id, simulation_time) * network_length_km

    def get_mfd_average_speed(self, zone_id, number_vehicles):
        """Return the configured MFD average speed in m/s.

        The routing engine provides a total zone vehicle count. It is converted
        to density using the assigned zone road length, then the fitted
        relation is evaluated as ``speed = q(k) / k = v - gamma * k``.
        A small positive speed at and beyond jam density keeps edge travel
        times finite.
        """
        params = self.mfd_parameters.get(zone_id)
        if params is None:
            return None

        network_length_km = self.mfd_network_lengths_km.get(zone_id)
        if network_length_km is None:
            return None

        density = self.get_mfd_density(zone_id, number_vehicles)
        if density is None:
            return None
        speed_kmh = params["v"] - params["gamma"] * density
        return max(speed_kmh / 3.6, 0.1)

    def check_first_last_mile_option(self, o_node, d_node):
        """This method checks whether first/last mile service should be offered in a given zone.

        :param o_node: node_id of a trip's start location
        :type o_node: int
        :param d_node: node_id of a trip's end location
        :type d_node: int
        :return: True/False
        :rtype: bool
        """
        if G_ZONE_FLM in self.node_zone_df.columns:
            mod_access = self.node_zone_df[G_ZONE_FLM].get(o_node, True)
            mod_egress = self.node_zone_df[G_ZONE_FLM].get(d_node, True)
        else:
            mod_access = True
            mod_egress = True
        return mod_access, mod_egress
    
    def set_current_park_costs(self, general_park_cost=0, park_cost_dict={}):
        """This method sets the current park costs in cent per region per hour.

        :param general_park_cost: this is a scale factor that is multiplied by each zones park_cost_scale_factor.
        :type general_park_cost: float
        :param park_cost_dict: sets the park costs per zone directly. Code prioritizes input over general_park_cost.
        :type park_cost_dict: dict
        """
        if park_cost_dict:
            for k,v in park_cost_dict.items():
                if k in self.general_info_df.index:
                    self.current_park_costs[k] = v
        else:
            for k, zone_scale_factor in self.general_info_df[G_ZONE_PC].items():
                self.current_park_costs[k] = general_park_cost * zone_scale_factor

    def set_current_toll_cost_scale_factor(self, general_toll_cost):
        self.current_toll_cost_scale = general_toll_cost

    def set_current_toll_coefficients(self, toll_coefficients):
        """Sets direct zone toll coefficients in cent per meter.

        :param toll_coefficients: zone id -> cent per meter
        :type toll_coefficients: dict
        """
        self.current_toll_coefficients = {}
        valid_zones = set(self.get_all_zones())
        for k, v in toll_coefficients.items():
            if k in valid_zones:
                self.current_toll_coefficients[k] = float(v)

    def set_road_pricing_policy(self, policy):
        """Register the active policy for PV-only tariff quotations."""
        self.road_pricing_policy = policy

    def set_current_toll_costs(self, use_pre_defined_zone_scales=False, rel_toll_cost_dict={}):
        """This method sets the current toll costs in cent per meter.

        :param use_pre_defined_zone_scales: use each zones toll_cost_scale_factor of zone definition.
        :type use_pre_defined_zone_scales: bool
        :param rel_toll_cost_dict: sets the toll costs per zone directly. Code prioritizes input over general_toll_cost.
        :type rel_toll_cost_dict: dict
        """
        if rel_toll_cost_dict and self.current_toll_cost_scale > 0:
            for k,v in rel_toll_cost_dict.items():
                if k in self.general_info_df.index:
                    self.current_toll_coefficients[k] = self.current_toll_cost_scale * v
        elif use_pre_defined_zone_scales and self.current_toll_cost_scale > 0:
            for k, zone_scale_factor in self.general_info_df[G_ZONE_TC].items():
                self.current_toll_coefficients[k] = self.current_toll_cost_scale * zone_scale_factor

    def get_external_route_costs(self, routing_engine, sim_time, route, park_origin=True, park_destination=True):
        """This method returns the external costs of a route, namely toll and park costs. Model simplifications:
        1) Model assumes a trip-based model, in which duration of activity is unknown. For this reason, park costs
        are assigned to a trip depending on their destination (trip start in the morning) or the origin (trip starts
        in the afternoon).
        2) Toll costs are computed for the current point in time. No extrapolation for the actual route time is
        performed.

        :param routing_engine: network and routing class
        :type routing_engine: Network
        :param sim_time: relevant for park costs - am: destination relevant; pm: origin relevant
        :type sim_time: float
        :param route: list of node ids that a vehicle drives along
        :type route: list
        :param park_origin: flag showing whether vehicle could generate parking costs at origin
        :type park_origin: bool
        :param park_destination: flag showing whether vehicle could generate parking costs at destination
        :type park_destination: bool
        :return: tuple of total external costs, toll costs, parking costs in cent
        :rtype: list
        """
        park_costs = 0
        toll_costs = 0
        if route:
            # 1) park cost model
            if sim_time < NOON:
                if park_destination:
                    # assume 1 hour of parking in order to return the set park cost values (current value!)
                    d_zone = self.get_zone_from_node(route[-1])
                    park_costs += self.current_park_costs.get(d_zone, 0)
            else:
                if park_origin:
                    # assume 1 hour of parking in order to return the set park cost values (current value!)
                    o_zone = self.get_zone_from_node(route[0])
                    park_costs += self.current_park_costs.get(o_zone, 0)
            # 2) toll model
            for i in range(len(route)-1):
                o_node = route[i]
                d_node = route[i+1]
                zone = self.get_zone_from_node(o_node)
                length = routing_engine.get_section_infos(o_node, d_node)[1]
                toll_costs += np.rint(self.current_toll_coefficients.get(zone, 0) * length)
        external_pv_costs = park_costs + toll_costs
        return external_pv_costs, toll_costs, park_costs

    def get_route_toll_cost(self, routing_engine, sim_time, route):
        """Returns only the route-based toll costs in cent."""
        _, toll_costs, _ = self.get_external_route_costs(
            routing_engine, sim_time, route, park_origin=False, park_destination=False
        )
        return toll_costs

    def get_pv_route_toll_cost(self, routing_engine, sim_time, route):
        """Return the PV tariff while leaving generic MoD route tolls unchanged."""
        policy_toll = getattr(self.road_pricing_policy, "get_pv_route_toll_cost", None)
        if callable(policy_toll):
            return policy_toll(routing_engine, sim_time, route)
        return self.get_route_toll_cost(routing_engine, sim_time, route)
    
    def get_parking_average_access_egress_times(self, o_node, d_node):
        # TODO # after ISTTT: get_parking_average_access_egress_times()
        t_access = 0
        t_egress = 0
        return t_access, t_egress

    def get_cordon_sections(self):
        # TODO # after ISTTT: get_cordon_sections()
        pass

    def get_aggregation_levels(self):
        """This method returns a dictionary of

        :return:
        """
        # TODO # after ISTTT: get_aggregation_levels()
        # is this necessary?
        pass
