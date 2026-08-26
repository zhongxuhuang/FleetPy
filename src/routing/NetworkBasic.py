"""
Authors: Roman Engelhardt, Florian Dandl
TUM, 2020
In order to guarantee transferability of models, Network models should follow the following conventions.
Classes should be called
Node
Edge
Network
in order to guarantee correct import in other modules.
"""

# -------------------------------------------------------------------------------------------------------------------- #
# standard distribution imports
# -----------------------------
import os
import logging
import heapq

# additional module imports (> requirements)
# ------------------------------------------
import pandas as pd
import numpy as np
from pyproj import Transformer

# src imports
# -----------
from src.routing.NetworkBase import NetworkBase
from src.routing.routing_imports.Router import Router

# -------------------------------------------------------------------------------------------------------------------- #
# global variables
# ----------------
from src.misc.globals import *
LOG = logging.getLogger(__name__)

# import os
# import pandas as pd
# import imports.Router as Router

INPUT_PARAMETERS_NetworkBasic = {
    "doc" : "this routing class does all routing computations based on dijkstras algorithm",
    "inherit" : "NetworkBase",
    "input_parameters_mandatory": [G_NETWORK_NAME],
    "input_parameters_optional": [G_NW_DYNAMIC_F, G_NETWORK_MODE],
    "mandatory_modules": [],
    "optional_modules": []
}


def read_node_line(columns):
    return Node(int(columns["node_index"]), int(columns["is_stop_only"]), float(columns["pos_x"]), float(columns["pos_y"]))

class Node():
    def __init__(self, node_index, is_stop_only, pos_x, pos_y, node_order=None):
        self.node_index = node_index
        self.is_stop_only = is_stop_only
        self.pos_x = pos_x
        self.pos_y = pos_y
        # 
        self.edges_to = {}  #node_obj -> edge
        self.edges_from = {}    #node_obj -> edge
        #
        self.travel_infos_from = {} #node_index -> (tt, dis)
        self.travel_infos_to = {}   #node_index -> (tt, dis)
        #
        # attributes set during path calculations
        self.is_target_node = False     # is set and reset in computeFromNodes
        #attributes for forwards dijkstra
        self.prev = None
        self.settled = 1
        self.cost_index = -1
        self.cost = None
        # attributes for backwards dijkstra (for bidirectional dijkstra)
        self.next = None
        self.settled_back = 1
        self.cost_index_back = -1
        self.cost_back = None

    def __str__(self):
        return str(self.node_index)

    def must_stop(self):
        return self.is_stop_only

    def get_position(self):
        return (self.pos_x, self.pos_y)

    def get_next_node_edge_pairs(self, ch_flag = False):
        """
        :return: list of (node, edge) tuples [references to objects] in forward direction
        """
        return self.edges_to.items()

    def get_prev_node_edge_pairs(self, ch_flag = False):
        """
        :return: list of (node, edge) tuples [references to objects] in backward direction
        """
        return self.edges_from.items()

    def add_next_edge_to(self, other_node, edge):
        #print("add next edge to: {} -> {}".format(self.node_index, other_node.node_index))
        self.edges_to[other_node] = edge
        self.travel_infos_to[other_node.node_index] = edge.get_tt_distance()

    def add_prev_edge_from(self, other_node, edge):
        self.edges_from[other_node] = edge
        self.travel_infos_from[other_node.node_index] = edge.get_tt_distance()

    def get_travel_infos_to(self, other_node_index):
        return self.travel_infos_to[other_node_index]

    def get_travel_infos_from(self, other_node_index):
        return self.travel_infos_from[other_node_index]



class Edge():
    def __init__(self, edge_index, distance, travel_time):
        self.edge_index = edge_index
        self.distance = distance
        self.travel_time = travel_time
        #

    def __str__(self):
        return "-".join(self.edge_index)

    def set_tt(self, travel_time):
        self.travel_time = travel_time

    def get_tt(self):
        """
        :return: (current) travel time on edge
        """
        return self.travel_time

    def get_distance(self):
        """
        :return: distance of edge
        """
        return self.distance

    def get_tt_distance(self):
        """
        :return: (travel time, distance) tuple
        """
        return (self.travel_time, self.distance)


# Position: (start_node_id, end_node_id, relative_pos)
#   -> (node_id, None, None) in case vehicle is on a node
#   -> relative_pos in [0.0, 1.0]
# A Route is defined as list of node-indices (int)
# while all given start-and end-position nodes are included


class NetworkBasic(NetworkBase):
    def __init__(self, network_name_dir, network_dynamics_file_name=None, scenario_time=None):
        """
        The network will be initialized.
        This network only uses basic routing algorithms (dijkstra and bidirectional dijkstra)
        :param network_name_dir: name of the network_directory to be loaded
        :param type: determining whether the base or a pre-processed network will be used
        :param scenario_time: applying travel times for a certain scenario at a given time in the scenario
        :param network_dynamics_file_name: file-name of the network dynamics file
        :type network_dynamics_file_name: str
        """
        self.nodes = []     #list of all nodes in network (index == node.node_index)
        self.network_name_dir = network_name_dir
        self.network_mode = "dynamic_mfd"
        self._network_dynamics_file_was_explicit = (
            network_dynamics_file_name is not None
            and not pd.isna(network_dynamics_file_name)
            and bool(str(network_dynamics_file_name).strip())
        )
        self._tt_infos_from_folder = True
        self._current_tt_factor = None
        self.travel_time_file_infos = self._load_tt_folder_path(network_dynamics_file_name=network_dynamics_file_name)
        self.loadNetwork(network_name_dir, network_dynamics_file_name=network_dynamics_file_name, scenario_time=scenario_time)
        # Dynamic MFD updates only overwrite travel times for zones that provide
        # an MFD speed. All other edges retain their loaded travel times.
        self.current_dijkstra_number = 1    #used in dijkstra-class
        self.sim_time = 0   # TODO #
        self.zones = None   # TODO #
        self.current_sampled_pv_zone_vehicle_counts = {}
        self.current_pv_zone_vehicle_counts = {}
        self.current_physical_mod_zone_vehicle_counts = {}
        self.current_mod_zone_vehicle_counts = {}
        self.current_exogenous_zone_vehicle_counts = {}
        self.current_total_zone_vehicle_counts = {}
        self.zone_mfd_functions = {}
        self._zone_to_edge_cache = None
        self._zone_to_edge_cache_zones_id = None
        # Zones without an MFD still need a positive speed for the PV bathtub
        # queue. These speeds are calculated once from the t=0 edge travel
        # times when the zone-edge cache is first built.
        self._fixed_zone_queue_speeds = {}
        self._zone_priority_queue_states = {}
        self._queued_route_trips = {}
        self._pv_route_start_events = []
        self._priority_queue_sequence = 0
        # One row per simulation time and zone, exported once the result
        # directory is available in FleetSimulationBase.
        self._zone_speed_time_series = []
        with open(os.sep.join([self.network_name_dir, "base","crs.info"]), "r") as f:
            self.crs = f.read()
        LOG.debug(
            f"network loaded zone vehicle counts={self.current_total_zone_vehicle_counts}"
        )

    def set_network_mode(self, network_mode):
        """Configure whether zone state updates may overwrite edge travel times.

        Static mode must be selected before the first network update. It keeps
        the base edge travel times loaded by ``loadNetwork`` and disables both
        explicitly configured and automatically discovered time-dependent TT
        sources.
        """
        mode = str(network_mode).strip().lower()
        if mode not in {"static", "dynamic_mfd"}:
            raise ValueError(
                f"{G_NETWORK_MODE} must be one of ['dynamic_mfd', 'static'], got {mode!r}"
            )
        if mode == "static" and self._network_dynamics_file_was_explicit:
            raise ValueError(
                f"{G_NETWORK_MODE}=static conflicts with explicitly configured {G_NW_DYNAMIC_F}"
            )
        self.network_mode = mode
        if mode == "static":
            self.travel_time_file_infos = {}
            self._current_tt_factor = None
            self._tt_infos_from_folder = True
        return self.network_mode

    def loadNetwork(self, network_name_dir, network_dynamics_file_name=None, scenario_time=None):
        nodes_f = os.path.join(network_name_dir, "base", "nodes.csv")
        LOG.info(f"Loading nodes from {nodes_f} ...")
        nodes_df = pd.read_csv(nodes_f)
        self.nodes = nodes_df.apply(read_node_line, axis=1)
        #
        edges_f = os.path.join(network_name_dir, "base", "edges.csv")
        LOG.info(f"Loading edges from {edges_f} ...")
        edges_df = pd.read_csv(edges_f)
        for _, row in edges_df.iterrows():
            o_node = self.nodes[row[G_EDGE_FROM]]
            d_node = self.nodes[row[G_EDGE_TO]]
            tmp_edge = Edge((o_node, d_node), row[G_EDGE_DIST], row[G_EDGE_TT])
            o_node.add_next_edge_to(d_node, tmp_edge)
            d_node.add_prev_edge_from(o_node, tmp_edge)
        LOG.info("... {} nodes loaded!".format(len(self.nodes)))
        if scenario_time is not None:
            latest_tt = None
            if len(self.travel_time_file_infos.keys()) > 0:
                tts = sorted(list(self.travel_time_file_infos.keys()))
                for tt in tts:
                    if tt > scenario_time:
                        break
                    latest_tt = tt
                self.load_tt_file(latest_tt)

    def _load_tt_folder_path(self, network_dynamics_file_name=None):
        """ this method searches in the network-folder for travel_times folder. the name of the folder is defined by the simulation time from which these travel times are valid
        stores the corresponding time to trigger loading of new travel times ones the simulation time is reached.
        """
        tt_folders = {}
        if network_dynamics_file_name is None:
            LOG.info("... no network dynamics file given -> read folder structure")
            for f in os.listdir(self.network_name_dir):
                time = None
                try:
                    time = int(f)
                except:
                    continue
                tt_folders[time] = os.path.join(self.network_name_dir, f)
        else:
            LOG.info("... load network dynamics file: {}".format(os.path.join(self.network_name_dir, network_dynamics_file_name)))
            nw_dynamics_df = pd.read_csv(os.path.join(self.network_name_dir, network_dynamics_file_name))
            nw_dynamics_df.set_index("simulation_time", inplace=True)
            if "travel_time_folder" in nw_dynamics_df.columns:
                LOG.info(f"   ... folder structure found in {network_dynamics_file_name}")
                for sim_time, tt_folder_name in nw_dynamics_df["travel_time_folder"].items():
                    tt_folders[int(sim_time)] = os.path.join(self.network_name_dir, str(tt_folder_name))
            elif "travel_time_factor" in nw_dynamics_df.columns:
                LOG.info(f"   ... travel time factor found in {network_dynamics_file_name}")
                self._tt_infos_from_folder = False
                for sim_time, tt_factor in nw_dynamics_df["travel_time_factor"].items():
                    tt_folders[int(sim_time)] = tt_factor
            else:
                LOG.warning(f" ... neither folder structure nor travel time factor found in {network_dynamics_file_name} -> use free flow travel times")
        return tt_folders

    def update_network(self, simulation_time, update_state = True):
        """This method can be called during simulations to update travel times (dynamic networks).

        :param simulation_time: time of simulation
        :type simulation_time: float
        :return: new_tt_flag True, if new travel times found; False if not
        :rtype: bool
        """
        LOG.debug(f"update network {simulation_time}")
        self.sim_time = simulation_time
        new_tt_flag = False
        if update_state:
            if self.network_mode == "static":
                self._update_static_network_state(simulation_time)
                return False
            if self.travel_time_file_infos.get(simulation_time, None) is not None:
                self.load_tt_file(simulation_time)
                new_tt_flag = True
            new_tt_flag = self._update_dynamic_edge_travel_times(simulation_time) or new_tt_flag
        return new_tt_flag

    def _update_static_network_state(self, simulation_time):
        """Update traffic counts and audit speeds without changing any edge TT."""
        zone_to_edges = self._get_zone_to_edge_cache()
        if not zone_to_edges:
            return False
        self._update_current_zone_vehicle_counts(simulation_time)
        zone_speed_summary = [
            (
                zone_id,
                self.current_total_zone_vehicle_counts.get(zone_id, 0),
                self._fixed_zone_queue_speeds.get(zone_id),
                "static_base_tt",
                len(edge_infos),
            )
            for zone_id, edge_infos in zone_to_edges.items()
        ]
        self._record_zone_speed_snapshot(simulation_time, zone_speed_summary)
        LOG.debug(
            f"static network state update at {simulation_time}: "
            f"zones={len(zone_to_edges)}; base edge travel times unchanged"
        )
        return False

    def _update_dynamic_edge_travel_times(self, simulation_time):
        """Updates edge travel times from zone MFD speeds.

        :param simulation_time: current simulation time of the dynamic update
        :return: True if at least one edge travel time was updated
        """
        zone_to_edges = self._get_zone_to_edge_cache()
        if not zone_to_edges:
            LOG.debug(
                f"dynamic edge tt update at {simulation_time}: no zone-edge mapping "
                f"(zones_attached={self.zones is not None})"
            )
            return False
        # Building the zone-edge cache also supplies the MFD network lengths
        # needed to convert exogenous density to equivalent vehicle counts.
        self._update_current_zone_vehicle_counts(simulation_time)

        changed_edges = []
        zone_speed_summary = []
        missing_speed_zones = []
        replaced_tt_factor = self._current_tt_factor is not None
        if self._current_tt_factor is not None:
            LOG.debug("dynamic edge TT update replaces the current travel time factor")
            self._current_tt_factor = None
        for zone_id, edge_infos in zone_to_edges.items():
            number_vehicles = self.current_total_zone_vehicle_counts.get(zone_id, 0)
            mfd_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles)
            if mfd_speed is None or mfd_speed <= 0:
                missing_speed_zones.append(zone_id)
                queue_speed = self._get_zone_queue_speed(zone_id, number_vehicles)
                source = "fixed_base_tt" if queue_speed is not None and queue_speed > 0 else "unavailable"
                zone_speed_summary.append((zone_id, number_vehicles, queue_speed, source, len(edge_infos)))
                continue
            zone_speed_summary.append((zone_id, number_vehicles, mfd_speed, "mfd", len(edge_infos)))
            for o_node_index, d_node_index, edge_distance in edge_infos:
                dynamic_tt = edge_distance / mfd_speed
                if self._set_edge_tt(o_node_index, d_node_index, dynamic_tt):
                    changed_edges.append((o_node_index, d_node_index, dynamic_tt))

        self._record_zone_speed_snapshot(simulation_time, zone_speed_summary)

        if LOG.isEnabledFor(logging.DEBUG):
            sample = zone_speed_summary[:20]
            LOG.debug(
                f"dynamic zone speed summary at {simulation_time}: "
                f"zones={len(zone_speed_summary)} sample={sample} "
                f"missing_speed_zones={len(missing_speed_zones)} changed_edges={len(changed_edges)}"
            )
        if changed_edges or replaced_tt_factor:
            self._reset_internal_attributes_after_travel_time_update()
            if changed_edges:
                self._after_dynamic_edge_tt_update(changed_edges)
            LOG.debug(
                f"dynamic edge tt update at {simulation_time}: "
                f"zones={len(zone_to_edges)} edges={len(changed_edges)}"
            )
            return True
        LOG.debug(
            f"dynamic edge tt update at {simulation_time}: no edges changed "
            f"(missing_speed_zones={len(missing_speed_zones)})"
        )
        return False

    def _record_zone_speed_snapshot(self, simulation_time, zone_speed_summary):
        """Store the current MFD speed of every mapped zone for result export."""
        self._zone_speed_time_series = [
            record for record in self._zone_speed_time_series
            if record["simulation_time"] != simulation_time
        ]
        for zone_id, number_vehicles, avg_speed, speed_source, _ in zone_speed_summary:
            self._zone_speed_time_series.append({
                "simulation_time": simulation_time,
                "zone_id": zone_id,
                "vehicle_count": number_vehicles,
                "sampled_pv_vehicle_count": self.current_sampled_pv_zone_vehicle_counts.get(zone_id, 0),
                "pv_vehicle_count": self.current_pv_zone_vehicle_counts.get(zone_id, 0),
                "physical_mod_vehicle_count": self.current_physical_mod_zone_vehicle_counts.get(zone_id, 0),
                "mod_vehicle_count": self.current_mod_zone_vehicle_counts.get(zone_id, 0),
                "exogenous_vehicle_count": self.current_exogenous_zone_vehicle_counts.get(zone_id, 0),
                "avg_speed_mps": avg_speed,
                "avg_speed_kmh": None if avg_speed is None else avg_speed * 3.6,
                "speed_source": speed_source,
            })

    def get_current_zone_mfd_speeds(self):
        """Return the latest MFD average speed per zone in network units per second.

        The values are the same speeds most recently used to update the zone's
        edge travel times.  Consumers such as road-pricing policies can use
        this read-only snapshot without recalculating MFD states or accessing
        vehicle counts.
        """
        return {
            record["zone_id"]: record["avg_speed_mps"]
            for record in self._zone_speed_time_series
            if record["speed_source"] == "mfd" and record["avg_speed_mps"] is not None
        }

    def get_current_zone_vehicle_counts(self):
        """Return a snapshot of total current vehicle counts by zone.

        The count combines active PV route segments and moving MoD vehicles.
        Callers receive a copy so they cannot modify the routing engine's MFD
        state while using it for read-only calculations such as road pricing.
        """
        return self.current_total_zone_vehicle_counts.copy()

    def write_zone_speed_timeseries(self, output_file):
        """Write recorded zone MFD speeds to a result CSV.

        :param output_file: absolute path of ``zone_speed_timeseries.csv``
        :return: True when a CSV was written, otherwise False
        """
        if not self._zone_speed_time_series:
            return False
        pd.DataFrame(self._zone_speed_time_series).sort_values(
            ["simulation_time", "zone_id"]
        ).to_csv(output_file, index=False)
        return True

    def _after_dynamic_edge_tt_update(self, changed_edges):
        """Runs backend-specific updates after dynamic edge travel times changed.

        :param changed_edges: list of tuples containing start node, end node and new travel time
        :return: None
        """
        pass

    def _get_edge_distance(self, o_node_index, d_node_index):
        """Returns an edge distance without reading its (possibly dynamic) TT."""
        return self.nodes[o_node_index].edges_to[self.nodes[d_node_index]].get_distance()

    def _get_zone_to_edge_cache(self):
        """Builds a zone-to-edge cache for periodic MFD edge travel-time updates.

        :return: dictionary zone_id -> list of edge information tuples
        """
        if self.zones is None:
            return {}
        if self._zone_to_edge_cache is not None and self._zone_to_edge_cache_zones_id == id(self.zones):
            return self._zone_to_edge_cache

        zone_to_edges = {}
        for o_node in self.nodes:
            for d_node, edge_obj in o_node.edges_to.items():
                zone_id = self._get_zone_from_position((o_node.node_index, d_node.node_index, 0.0))
                if zone_id is None or zone_id < 0:
                    continue
                _, edge_distance = edge_obj.get_tt_distance()
                zone_to_edges.setdefault(zone_id, []).append((o_node.node_index, d_node.node_index, edge_distance))
        self._zone_to_edge_cache = zone_to_edges
        self._zone_to_edge_cache_zones_id = id(self.zones)
        self._fixed_zone_queue_speeds = self._compute_fixed_zone_queue_speeds(zone_to_edges)
        if hasattr(self.zones, "set_mfd_network_lengths"):
            # Edge distances are in metres. MFD fits use density in veh/km.
            self.zones.set_mfd_network_lengths({
                zone_id: sum(edge_distance for _, _, edge_distance in edge_infos) / 1000.0
                for zone_id, edge_infos in zone_to_edges.items()
            })
        return self._zone_to_edge_cache

    def _compute_fixed_zone_queue_speeds(self, zone_to_edges):
        """Return t=0 base-TT-equivalent speeds for zones without an MFD.

        The returned speed is ``sum(distance) / sum(edge_tt)`` in network
        distance units per second.  It is only used to advance the zone-level
        PV queue; it never overwrites the individual edge travel times.
        """
        fixed_speeds = {}
        for zone_id, edge_infos in zone_to_edges.items():
            if (
                getattr(self, "network_mode", "dynamic_mfd") != "static"
                and self._get_zone_average_speed_from_mfd(zone_id, 0) is not None
            ):
                continue
            total_distance = 0.0
            total_tt = 0.0
            for o_node_index, d_node_index, edge_distance in edge_infos:
                edge_tt, _ = self.nodes[o_node_index].edges_to[self.nodes[d_node_index]].get_tt_distance()
                if edge_tt is None or edge_tt <= 0:
                    continue
                total_distance += edge_distance
                total_tt += edge_tt
            if total_distance > 0 and total_tt > 0:
                fixed_speeds[zone_id] = total_distance / total_tt
        return fixed_speeds

    def reset_network(self, simulation_time: float):
        """ this method is used in case a module changed the travel times to future states for forecasts
        it resets the network to the travel times a stimulation_time
        :param simulation_time: current simulation time"""
        sorted_tts = sorted(self.travel_time_file_infos.keys())
        if len(sorted_tts) > 2:
            for i in range(len(sorted_tts) - 1):
                if sorted_tts[i] <= simulation_time and sorted_tts[i+1] > simulation_time:
                    self.update_network(sorted_tts[i])
                    return
            if sorted_tts[-1] <= simulation_time:
                self.update_network(sorted_tts[-1])
                return

    def load_tt_file(self, scenario_time):
        """
        loads new travel time files for scenario_time
        """
        self._reset_internal_attributes_after_travel_time_update()
        f = self.travel_time_file_infos[scenario_time]
        if self._tt_infos_from_folder:
            tt_file = os.path.join(f, "edges_td_att.csv")
            tmp_df = pd.read_csv(tt_file)
            tmp_df.set_index(["from_node","to_node"], inplace=True)
            for edge_index_tuple, new_tt in tmp_df["edge_tt"].items():
                self._set_edge_tt(edge_index_tuple[0], edge_index_tuple[1], new_tt)
        else:
            self._current_tt_factor = f

    def _set_edge_tt(self, o_node_index, d_node_index, new_travel_time):
        o_node = self.nodes[o_node_index]
        d_node = self.nodes[d_node_index]
        edge_obj = o_node.edges_to[d_node]
        old_tt, _ = edge_obj.get_tt_distance()
        if abs(old_tt - new_travel_time) <= 1e-9 * max(1.0, abs(old_tt), abs(new_travel_time)):
            return False
        edge_obj.set_tt(new_travel_time)
        new_tt, dis = edge_obj.get_tt_distance()
        o_node.travel_infos_to[d_node_index] = (new_tt, dis)
        d_node.travel_infos_from[o_node_index] = (new_tt, dis)
        return True

    def get_node_list(self):
        """
        :return: list of node objects.
        """
        return self.nodes

    def get_number_network_nodes(self):
        return len(self.nodes)

    def get_must_stop_nodes(self):
        """ returns a list of node-indices with all nodes with a stop_only attribute """
        return [n.node_index for n in self.nodes if n.must_stop()]

    def _get_zone_priority_queue_state(self, zone_id, simulation_time=None):
        """Returns the priority queue state of a zone.

        If no state exists for the given zone yet, it is initialized empty with
        the zone-specific queue speed.

        :param zone_id: zone identifier for which the priority queue state is requested
        :param simulation_time: simulation time used for initializing a new state
        :return: dictionary with the priority queue bathtub state of this zone
        """
        if zone_id is None:
            return None
        if zone_id not in self._zone_priority_queue_states:
            init_time = self.sim_time if simulation_time is None else simulation_time
            number_vehicles = self.current_total_zone_vehicle_counts.get(zone_id, 0)
            avg_speed = self._get_zone_queue_speed(zone_id, number_vehicles)
            self._zone_priority_queue_states[zone_id] = {
                "E": 0,  # cumulative number of trips that entered this zone
                "G": 0,  # cumulative number of trips that completed in this zone
                "sample_E": 0,  # cumulative logical sampled trips entering
                "sample_G": 0,  # cumulative logical sampled trips completing
                "z": 0.0,  # cumulative bathtub progress since initialization
                "v": 0.0 if avg_speed is None else avg_speed,  # current MFD zone speed
                "last_time": init_time,  # last simulation time at which this state was advanced
                "heap": []  # (completion threshold, sequence, queued route trip id, vehicle weight)
            }
            self._log_zone_priority_queue_state(zone_id, "init", init_time)
        return self._zone_priority_queue_states[zone_id]

    def _log_zone_priority_queue_state(self, zone_id, event, simulation_time=None, extra=None):
        """Logs a compact snapshot of one zone's PV priority queue state.

        :param zone_id: zone identifier whose state is logged
        :param event: short label describing the state transition or log reason
        :param simulation_time: simulation time associated with the log entry
        :param extra: optional additional information appended to the log message
        :return: None
        """
        if event == "push":
            return
        if not LOG.isEnabledFor(logging.DEBUG):
            return
        state = self._zone_priority_queue_states.get(zone_id)
        if state is None:
            return
        heap = state["heap"]
        active_count = self._get_zone_priority_queue_active_count(state)
        heap_top = heapq.nsmallest(min(5, len(heap)), heap) if heap else []
        log_msg = (
            f"PV zone PQ {event} zone={zone_id} t={simulation_time} "
            f"E={state['E']} G={state['G']} active={active_count} "
            f"z={state['z']} v={state['v']} last={state['last_time']} "
            f"heap_size={len(heap)} heap_min={heap[0] if heap else None} "
            f"heap_top={heap_top}"
        )
        if extra is not None:
            log_msg += f" | {extra}"
        LOG.debug(log_msg)

    def _advance_zone_priority_queue_state(self, zone_id, simulation_time):
        """Advances the PV priority queue bathtub state of one zone.

        The state is advanced from its last update time to the given simulation
        time. All PV trips whose completion threshold is reached are removed from
        the priority queue and counted as completed.

        :param zone_id: zone identifier whose priority queue state is advanced
        :param simulation_time: simulation time to which the state is advanced
        :return: number of newly completed PV trips in this zone
        """
        state = self._get_zone_priority_queue_state(zone_id, simulation_time)
        if state is None:
            return 0
        last_time = state["last_time"]
        if simulation_time < last_time:
            self._log_zone_priority_queue_state(
                zone_id,
                "backward_time",
                simulation_time,
                extra=f"last_time={last_time}"
            )
            return 0
        delta_t = simulation_time - last_time
        if delta_t > 0:
            state["z"] += delta_t * state["v"]
            state["last_time"] = simulation_time

        completed = 0
        completed_weight = 0.0
        completed_trip_ids = []
        while state["heap"] and state["heap"][0][0] <= state["z"]:
            _, _, trip_id, vehicle_weight = heapq.heappop(state["heap"])
            completed += 1
            completed_weight += vehicle_weight
            if trip_id is not None:
                completed_trip_ids.append(trip_id)
        if completed:
            state["G"] += completed_weight
            state["sample_G"] += completed
        self._set_pv_zone_vehicle_count_from_priority_queue(zone_id)
        for trip_id in completed_trip_ids:
            self._continue_pv_route_trip(trip_id, simulation_time)
        if completed:
            self._log_zone_priority_queue_state(
                zone_id,
                "complete",
                simulation_time,
                extra=f"dt={delta_t} completed={completed} weight={completed_weight}"
            )
        return completed

    def _set_pv_zone_vehicle_count_from_priority_queue(self, zone_id):
        """Updates the stored PV count from active priority-queue trips.

        :param zone_id: zone identifier whose PV vehicle count is updated
        :return: None
        """
        state = self._get_zone_priority_queue_state(zone_id, self.sim_time)
        active_count = self._get_zone_priority_queue_active_count(state)
        sampled_active_count = self._get_zone_priority_queue_sampled_active_count(state)
        self.current_pv_zone_vehicle_counts[zone_id] = active_count
        self.current_sampled_pv_zone_vehicle_counts[zone_id] = sampled_active_count
        self._refresh_total_zone_vehicle_counts()

    @staticmethod
    def _get_zone_priority_queue_active_count(state):
        """Returns the active equivalent PV weight in a zone priority queue."""
        return max(state["E"] - state["G"], 0)

    @staticmethod
    def _get_zone_priority_queue_sampled_active_count(state):
        """Returns the number of logical sampled PV trips in a zone queue."""
        return max(state["sample_E"] - state["sample_G"], 0)

    def _update_zone_priority_queue_speeds(self):
        """Refreshes PV priority queue speeds using total zone vehicle counts.

        :return: None
        """
        for zone_id, state in self._zone_priority_queue_states.items():
            number_vehicles = self.current_total_zone_vehicle_counts.get(zone_id, 0)
            avg_speed = self._get_zone_queue_speed(zone_id, number_vehicles)
            if avg_speed is not None:
                state["v"] = avg_speed

    def _register_zone_trip(self, zone_id, start_time, travel_distance, vehicle_weight=1.0, trip_id=None):
        """Register one weighted logical PV trip in a zone bathtub queue.

        Each registered trip receives a completion threshold based on the current
        bathtub progress and the remaining travel distance in the zone.

        :param zone_id: zone identifier in which the PV trips are registered
        :param start_time: simulation time at which the trips enter the zone
        :param travel_distance: travel distance covered by the trips inside the zone
        :param vehicle_weight: equivalent vehicle contribution of this sampled trip
        :param trip_id: queued-PV route trip identifier
        :return: None
        """
        try:
            vehicle_weight = float(vehicle_weight)
        except (TypeError, ValueError) as exc:
            raise ValueError("vehicle_weight must be a positive finite number") from exc
        if not np.isfinite(vehicle_weight) or vehicle_weight <= 0:
            raise ValueError("vehicle_weight must be a positive finite number")
        if zone_id is None:
            return
        state = self._get_zone_priority_queue_state(zone_id, start_time)
        number_vehicles_in_zone = self.current_total_zone_vehicle_counts.get(zone_id, 0)
        queue_speed = self._get_zone_queue_speed(zone_id, number_vehicles_in_zone)
        if queue_speed is not None and queue_speed > 0:
            state["v"] = queue_speed
        if travel_distance <= 0:
            state["E"] += vehicle_weight
            state["G"] += vehicle_weight
            state["sample_E"] += 1
            state["sample_G"] += 1
            self._set_pv_zone_vehicle_count_from_priority_queue(zone_id)
            self._log_zone_priority_queue_state(
                zone_id,
                "zero_distance",
                start_time,
                extra=f"weight={vehicle_weight} dist={travel_distance}"
            )
            return

        projected_delta_t = max(start_time - state["last_time"], 0)
        projected_z = state["z"] + projected_delta_t * state["v"]
        theta = travel_distance + projected_z
        self._priority_queue_sequence += 1
        heapq.heappush(
            state["heap"],
            (theta, self._priority_queue_sequence, trip_id, vehicle_weight),
        )
        state["E"] += vehicle_weight
        state["sample_E"] += 1
        self._set_pv_zone_vehicle_count_from_priority_queue(zone_id)
        self._log_zone_priority_queue_state(
            zone_id,
            "push",
            start_time,
            extra=(
                f"weight={vehicle_weight} dist={travel_distance} "
                f"projected_dt={projected_delta_t} projected_z={projected_z} "
                f"theta={theta}"
            )
        )

    def _build_route_zone_segments(self, route):
        """Builds ordered contiguous zone segments using edge distances only."""
        segments = []
        for i in range(len(route) - 1):
            o_node, d_node = route[i], route[i + 1]
            zone_id = self._get_zone_from_position((o_node, d_node, 0.0))
            if zone_id is None or zone_id < 0:
                LOG.warning(
                    f"route edge ({o_node}->{d_node}) has no queueable zone; "
                    "it is skipped by the zone PQ"
                )
                continue
            edge_distance = self._get_edge_distance(o_node, d_node)
            if segments and segments[-1][0] == zone_id:
                segments[-1] = (zone_id, segments[-1][1] + edge_distance)
            else:
                segments.append((zone_id, edge_distance))
        return segments

    def _schedule_pv_route_trip(self, segments, start_time, vehicle_weight=1.0):
        """Add one weighted PV route descriptor to the start-event queue."""
        self._priority_queue_sequence += 1
        trip_id = self._priority_queue_sequence
        self._queued_route_trips[trip_id] = {
            "vehicle_type": "pv",
            "segments": segments,
            "segment_index": 0,
            "vehicle_weight": float(vehicle_weight),
        }
        heapq.heappush(self._pv_route_start_events, (start_time, self._priority_queue_sequence, trip_id))

    def _admit_scheduled_pv_route_trips(self, simulation_time):
        """Moves due PV route descriptors into their first zone queue segment."""
        while self._pv_route_start_events and self._pv_route_start_events[0][0] <= simulation_time:
            _, _, trip_id = heapq.heappop(self._pv_route_start_events)
            trip = self._queued_route_trips.get(trip_id)
            if trip is None:
                continue
            zone_id, distance = trip["segments"][trip["segment_index"]]
            self._register_zone_trip(
                zone_id,
                simulation_time,
                distance,
                vehicle_weight=trip["vehicle_weight"],
                trip_id=trip_id,
            )

    def _continue_pv_route_trip(self, trip_id, simulation_time):
        """Moves a completed PV segment into its next zone queue segment."""
        trip = self._queued_route_trips.get(trip_id)
        if trip is None:
            return
        trip["segment_index"] += 1
        if trip["segment_index"] >= len(trip["segments"]):
            self._queued_route_trips.pop(trip_id, None)
            return
        zone_id, distance = trip["segments"][trip["segment_index"]]
        self._register_zone_trip(
            zone_id,
            simulation_time,
            distance,
            vehicle_weight=trip["vehicle_weight"],
            trip_id=trip_id,
        )

    def _update_current_zone_vehicle_counts(self, simulation_time):
        """Updates PV queue counts and combines them with synced MoD positions.

        :param simulation_time: simulation time for which the zone counts are updated
        :return: dictionary zone_id -> total number of vehicles in this zone
        """
        self._admit_scheduled_pv_route_trips(simulation_time)
        tracked_zone_ids = set(self._get_defined_zones()) | set(self._zone_priority_queue_states.keys())
        for zone_id in tracked_zone_ids:
            self._advance_zone_priority_queue_state(zone_id, simulation_time)
        self._refresh_total_zone_vehicle_counts(simulation_time)
        self._update_zone_priority_queue_speeds()
        LOG.debug(
            f"zone vehicle counts at {simulation_time}: pv={self.current_pv_zone_vehicle_counts} "
            f"mod={self.current_mod_zone_vehicle_counts} total={self.current_total_zone_vehicle_counts}"
        )
        return self.current_total_zone_vehicle_counts

    def update_mod_zone_vehicle_counts(self, mod_positions, vehicle_weight=1.0):
        """Rebuilds moving-MoD zone counts from their current network positions.

        MoD vehicles are deliberately not represented in the priority queue.
        Call this after vehicle states have been advanced and before the next
        dynamic network update.

        :param mod_positions: iterable of MoD network position tuples
        :param vehicle_weight: equivalent MFD weight of each physical moving vehicle
        :return: dictionary zone_id -> current equivalent moving-MoD count
        """
        try:
            vehicle_weight = float(vehicle_weight)
        except (TypeError, ValueError) as exc:
            raise ValueError("vehicle_weight must be a positive finite number") from exc
        if not np.isfinite(vehicle_weight) or vehicle_weight <= 0:
            raise ValueError("vehicle_weight must be a positive finite number")
        physical_zone_counts = {zone_id: 0 for zone_id in self._get_defined_zones()}
        for position in mod_positions:
            if position is None:
                continue
            zone_id = self._get_zone_from_position(position)
            if zone_id is None or zone_id < 0:
                continue
            physical_zone_counts[zone_id] = physical_zone_counts.get(zone_id, 0) + 1
        self.current_physical_mod_zone_vehicle_counts = physical_zone_counts
        self.current_mod_zone_vehicle_counts = {
            zone_id: physical_count * vehicle_weight
            for zone_id, physical_count in physical_zone_counts.items()
        }
        self._refresh_total_zone_vehicle_counts()
        LOG.debug(
            f"synced moving MoD zone counts: mod={self.current_mod_zone_vehicle_counts} "
            f"total={self.current_total_zone_vehicle_counts}"
        )
        return self.current_mod_zone_vehicle_counts

    def _update_exogenous_zone_vehicle_counts(self, simulation_time=None):
        """Refresh fixed exogenous equivalent counts from zone density curves."""
        if simulation_time is None:
            simulation_time = self.sim_time
        exogenous_getter = getattr(self.zones, "get_mfd_exogenous_vehicle_count", None)
        self.current_exogenous_zone_vehicle_counts = {
            zone_id: (
                float(exogenous_getter(zone_id, simulation_time))
                if callable(exogenous_getter) else 0.0
            )
            for zone_id in self._get_defined_zones()
        }

    def _refresh_total_zone_vehicle_counts(self, simulation_time=None):
        """Combine weighted PV/MoD and fixed exogenous counts per zone."""
        self._update_exogenous_zone_vehicle_counts(simulation_time)
        all_zone_ids = (
            set(self._get_defined_zones())
            | set(self.current_pv_zone_vehicle_counts.keys())
            | set(self.current_mod_zone_vehicle_counts.keys())
            | set(self.current_exogenous_zone_vehicle_counts.keys())
            | set(self._zone_priority_queue_states.keys())
        )
        self.current_total_zone_vehicle_counts = {
            zone_id: (
                self.current_pv_zone_vehicle_counts.get(zone_id, 0)
                + self.current_mod_zone_vehicle_counts.get(zone_id, 0)
                + self.current_exogenous_zone_vehicle_counts.get(zone_id, 0)
            )
            for zone_id in all_zone_ids
        }

    def _get_zone_average_speed_from_mfd(self, zone_id, number_vehicles):
        """Placeholder for zone MFD equations.

        Expected return unit is distance unit per second, matching edge distance / travel time.
        Return None to keep using the network's original edge travel time.

        :param zone_id: zone identifier for which the MFD speed is requested
        :param number_vehicles: number of vehicles currently counted in this zone
        :return: average zone speed or None if no MFD speed is available
        """
        if zone_id is None or zone_id < 0:
            return None
        if zone_id in self.zone_mfd_functions:
            return self.zone_mfd_functions[zone_id](number_vehicles)
        # TODO: the default MFD speed should be implemented in the attached ZoneSystem
        # via get_mfd_average_speed(zone_id, number_vehicles).
        if self.zones is not None and hasattr(self.zones, "get_mfd_average_speed"):
            return self.zones.get_mfd_average_speed(zone_id, number_vehicles)
        return None

    def _get_zone_queue_speed(self, zone_id, number_vehicles):
        """Return the speed used to advance a zone-level PV queue.

        MFD zones use their density-dependent speed. Zones without an MFD use
        their cached t=0 base-edge-TT-equivalent speed, so their PV queue can
        advance without changing any individual edge travel time.
        """
        if getattr(self, "network_mode", "dynamic_mfd") != "static":
            avg_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles)
            if avg_speed is not None and avg_speed > 0:
                return avg_speed
        if zone_id not in self._fixed_zone_queue_speeds and self._zone_to_edge_cache is None:
            self._get_zone_to_edge_cache()
        return self._fixed_zone_queue_speeds.get(zone_id)

    def set_zone_mfd_function(self, zone_id, mfd_function):
        """Registers an MFD function for one zone.

        :param zone_id: zone identifier for which the MFD function is registered
        :param mfd_function: callable mapping number_vehicles to average zone speed
        :return: None
        """
        self.zone_mfd_functions[zone_id] = mfd_function
        state = self._zone_priority_queue_states.get(zone_id)
        if state is not None:
            number_vehicles = self.current_total_zone_vehicle_counts.get(zone_id, 0)
            avg_speed = self._get_zone_queue_speed(zone_id, number_vehicles)
            if avg_speed is not None:
                state["v"] = avg_speed

    def _get_mfd_section_infos(self, start_node_index, end_node_index):
        """Returns edge travel time and distance using zone MFD speed when available.

        :param start_node_index: index of the edge start node
        :param end_node_index: index of the edge end node
        :return: tuple of travel time and edge distance
        """
        base_tt, edge_distance = self.get_section_infos(start_node_index, end_node_index)
        zone_id = self._get_zone_from_position((start_node_index, end_node_index, 0.0))
        if zone_id is None or zone_id < 0:
            return base_tt, edge_distance

        number_vehicles = self.current_total_zone_vehicle_counts.get(zone_id, 0)
        avg_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles)
        if avg_speed is None or avg_speed <= 0:
            return base_tt, edge_distance
        dynamic_tt = edge_distance / avg_speed
        LOG.debug(
            f"mfd edge ({start_node_index}->{end_node_index}) zone={zone_id}: "
            f"total={number_vehicles}, dynamic_v={avg_speed}, tt={dynamic_tt}"
        )
        return dynamic_tt, edge_distance

    def _get_defined_zones(self):
        """Returns currently known zone identifiers.

        :return: list of currently defined zone identifiers
        """
        if self.zones is None:
            return []
        if hasattr(self.zones, "get_all_zones"):
            return self.zones.get_all_zones()
        if isinstance(self.zones, dict):
            return sorted(self.zones.keys())
        return list(self.zones)

    def _get_zone_from_position(self, position):
        """Returns the zone identifier for a network position.

        :param position: network position tuple for which the zone is requested
        :return: zone identifier of the position or None if no zone is available
        """
        if self.zones is not None and hasattr(self.zones, "get_zone_from_pos"):
            return self.zones.get_zone_from_pos(position)
        return None

    def return_position_from_str(self, position_str):
        a, b, c = position_str.split(";")
        if b == "-1":
            return (int(a), None, None)
        else:
            return (int(a), int(b), float(c))

    def return_node_coordinates(self, node_index):
        return self.nodes[node_index].get_position()

    def return_position_coordinates(self, position_tuple):
        """Returns the spatial coordinates of a position.

        :param position_tuple: (o_node, d_node, rel_pos) | (o_node, None, None)
        :return: (x,y) for metric systems
        """
        if position_tuple[1] is None:
            return self.return_node_coordinates(position_tuple[0])
        else:
            c0 = np.array(self.return_node_coordinates(position_tuple[0]))
            c1 = np.array(self.return_node_coordinates(position_tuple[1]))
            c_rel = position_tuple[2] * c1 + (1 - position_tuple[2]) * c0
            return c_rel[0], c_rel[1]

    def return_network_bounding_box(self):
        min_x = min([node.pos_x for node in self.nodes])
        max_x = max([node.pos_x for node in self.nodes])
        min_y = min([node.pos_y for node in self.nodes])
        max_y = max([node.pos_y for node in self.nodes])
        proj_transformer = Transformer.from_proj(self.crs, 'epsg:4326')
        lats, lons = proj_transformer.transform([min_x, max_x], [min_y, max_y])
        return list(zip(lons, lats))

    def return_positions_lon_lat(self, position_tuple_list: list) -> list:
        pos_list = [self.return_position_coordinates(pos) for pos in position_tuple_list]
        x, y = list(zip(*pos_list))
        proj_transformer = Transformer.from_proj(self.crs, 'epsg:4326')
        lats, lons = proj_transformer.transform(x, y)
        return list(zip(lons, lats))

    def get_section_infos(self, start_node_index, end_node_index):
        """
        :param start_node_index_index: index of start_node of section
        :param end_node_index: index of end_node of section
        :return: (travel time, distance); if no section between nodes (None, None)
        """
        if self._current_tt_factor is None:
            return self.nodes[start_node_index].get_travel_infos_to(end_node_index)
        else:
            tt, dis = self.nodes[start_node_index].get_travel_infos_to(end_node_index)
            return tt * self._current_tt_factor, dis

    def return_route_infos(self, route, rel_start_edge_position, start_time):
        """
        This method returns the information travel information along a route. The start position is given by a relative
        value on the first edge [0,1], where 0 means that the vehicle is at the first node.
        :param route: list of nodes
        :param rel_start_edge_position: float [0,1] determining the start position
        :param start_time: can be used as an offset in case the route is planned for a future time
        :return: (arrival time, distance to travel)
        """
        arrival_time = start_time
        distance = 0
        _, start_tt, start_dis = self.get_section_overhead( (route[0], route[1], rel_start_edge_position), from_start=False)
        arrival_time += start_tt
        distance += start_dis
        if len(route) > 2:
            for i in range(2, len(route)):
                tt, dis = self.get_section_infos(route[i-1], route[i])
                arrival_time += tt
                distance += dis
        return (arrival_time, distance)

    def assign_route_to_network(self, route, start_time, end_time, number_vehicles=1):
        """Schedules PV routes in the shared zone priority queues.

        :param route: list of nodes
        :param start_time: simulation time at which the PV route starts
        :param end_time: retained for interface compatibility; ignored by the PQ model
        :param number_vehicles: equivalent MFD weight of this one logical PV route
        """
        try:
            vehicle_weight = float(number_vehicles)
        except (TypeError, ValueError) as exc:
            raise ValueError("number_vehicles must be a positive finite weight") from exc
        if not np.isfinite(vehicle_weight) or vehicle_weight <= 0:
            raise ValueError("number_vehicles must be a positive finite weight")
        if not route or len(route) < 2:
            LOG.debug(f"pv route assignment skipped route={route} reason=too_short")
            return
        segments = self._build_route_zone_segments(route)
        if not segments:
            LOG.warning(f"pv route assignment skipped route={route} reason=no_queueable_zone_segments")
            return
        total_route_distance = sum(distance for _, distance in segments)
        LOG.debug(
            f"pv route assignment route={route} start={start_time} weight={vehicle_weight} "
            f"segments={segments} total_dist={total_route_distance}; end_time={end_time} ignored"
        )
        self._schedule_pv_route_trip(list(segments), start_time, vehicle_weight)

    def get_section_overhead(self, position, from_start=True, customized_section_cost_function=None):
        """This method computes the section overhead for a certain position.

        :param position: (current_edge_origin_node_index, current_edge_destination_node_index, relative_position)
        :param from_start: computes already traveled travel_time and distance,
                           if False: computes rest travel time (relative_position -> 1.0-relative_position)
        :param customized_section_cost_function: customized routing objective function
        :return: (cost_function_value, travel time, travel_distance)
        """
        if position[1] is None:
            return 0.0, 0.0, 0.0
        all_travel_time, all_travel_distance = self.get_section_infos(position[0], position[1])
        overhead_fraction = position[2]
        if not from_start:
            overhead_fraction = 1.0 - overhead_fraction
        all_travel_cost = all_travel_time
        if customized_section_cost_function is not None:
            all_travel_cost = customized_section_cost_function(all_travel_time, all_travel_distance, self.nodes[position[1]])
        return all_travel_cost * overhead_fraction, all_travel_time * overhead_fraction, all_travel_distance * overhead_fraction

    def return_travel_costs_1to1(self, origin_position, destination_position, customized_section_cost_function = None):
        """
        This method will return the travel costs of the fastest route between two nodes.
        :param origin_position: (current_edge_origin_node_index, current_edge_destination_node_index, relative_position)
        :param destination_position: (destination_edge_origin_node_index, destination_edge_destination_node_index, relative_position)
        :param customized_section_cost_function: function to compute the travel cost of an section: args: (travel_time, travel_distance, current_dijkstra_node) -> cost_value
                if None: travel_time is considered as the cost_function of a section
        :return: (cost_function_value, travel time, travel_distance) between the two nodes
        """
        trivial_test = self.test_and_get_trivial_route_tt_and_dis(origin_position, destination_position)
        if trivial_test is not None:
            return trivial_test[1]
        origin_node = origin_position[0]
        origin_overhead = (0.0, 0.0, 0.0)
        if origin_position[1] is not None:
            origin_node = origin_position[1]
            origin_overhead = self.get_section_overhead(origin_position, from_start=False)
        destination_node = destination_position[0]
        destination_overhead = (0.0, 0.0, 0.0)
        if destination_position[1] is not None:
            destination_overhead = self.get_section_overhead(destination_position, from_start=True)
        if self._current_tt_factor is None:
            R = Router(self, origin_node, destination_nodes=[destination_node], mode='bidirectional', customized_section_cost_function=customized_section_cost_function)
            s = R.compute(return_route=False)[0][1]
        else:
            R = Router(self, origin_node, destination_nodes=[destination_node], mode='bidirectional', customized_section_cost_function=customized_section_cost_function)
            s = R.compute(return_route=False)[0][1]
            s = (s[0] * self._current_tt_factor, s[1] * self._current_tt_factor, s[2])
        res = (s[0] + origin_overhead[0] + destination_overhead[0], s[1] + origin_overhead[1] + destination_overhead[1], s[2] + origin_overhead[2] + destination_overhead[2])
        if customized_section_cost_function is None:
            self._add_to_database(origin_node, destination_node, s[0], s[1], s[2])
        return res

    def return_travel_costs_Xto1(self, list_origin_positions, destination_position, max_routes=None, max_cost_value=None, customized_section_cost_function = None):
        """
        This method will return a list of tuples of origin node and travel time of the X fastest routes between
        a list of possible origin nodes and a certain destination node, whereas the route starts at certain origins can
        be offset. Combinations that dont fullfill all constraints will not be returned.
        :param list_origin_positions: list of origin_positions (current_edge_origin_node_index, current_edge_destination_node_index, relative_position)
        :param destination_position: destination position : (destination_edge_origin_node_index, destination_edge_destination_node_index, relative_position)
        :param max_routes: maximal number of fastest route triples that should be returned
        :param max_cost_value: latest cost function value of a route at destination to be considered as solution (max time if customized_section_cost_function == None)
        :param customized_section_cost_function: function to compute the travel cost of an section: args: (travel_time, travel_distance, current_dijkstra_node) -> cost_value
                if None: travel_time is considered as the cost_function of a section
        :return: list of (origin_position, cost_function_value, travel time, travel_distance) tuples
        """
        origin_nodes = {}
        return_list = []
        for pos in list_origin_positions:
            trivial_test = self.test_and_get_trivial_route_tt_and_dis(pos, destination_position)
            if trivial_test is not None:
                if max_cost_value is not None and trivial_test[1][0] > max_cost_value:
                    continue
                return_list.append( (pos, trivial_test[1][0], trivial_test[1][1], trivial_test[1][2]))
                continue
            start_node = pos[0]
            if pos[1] is not None:
                start_node = pos[1]
            try:
                origin_nodes[start_node].append(pos)
            except:
                origin_nodes[start_node] = [pos]
        destination_node = destination_position[0]
        destination_overhead = (0.0, 0.0, 0.0)
        if destination_position[1] is not None:
            destination_overhead = self.get_section_overhead(destination_position, from_start=True)
        if len(origin_nodes.keys()) > 0:
            if self._current_tt_factor is None:
                R = Router(self, destination_node, destination_nodes=origin_nodes.keys(), time_radius = max_cost_value, max_settled_targets = max_routes, forward_flag = False, customized_section_cost_function=customized_section_cost_function)
                s = R.compute(return_route=False)
            else:
                if max_cost_value is not None:
                    new_max_cost_value = max_cost_value/self._current_tt_factor
                else:
                    new_max_cost_value = None
                R = Router(self, destination_node, destination_nodes=origin_nodes.keys(), time_radius = new_max_cost_value, max_settled_targets = max_routes, forward_flag = False, customized_section_cost_function=customized_section_cost_function)
                s = R.compute(return_route=False)
                s = [(entry[0], (entry[1][0] * self._current_tt_factor, entry[1][1] * self._current_tt_factor, entry[1][2])) for entry in s]
            for entry in s:
                cfv, tt, dis = entry[1]
                if cfv < 0 or cfv == float("inf"):
                    continue
                org_node = entry[0][0]
                if customized_section_cost_function is None:
                    self._add_to_database(org_node, destination_node, cfv, tt, dis)
                cfv += destination_overhead[0]
                tt += destination_overhead[1]
                dis += destination_overhead[2]
                for origin_position in origin_nodes[org_node]:
                    origin_overhead = (0.0, 0.0, 0.0)
                    if origin_position[1] is not None:
                        origin_overhead = self.get_section_overhead(origin_position, from_start=False)
                    cfv += origin_overhead[0]
                    tt += origin_overhead[1]
                    dis += origin_overhead[2]
                    if max_cost_value is not None and cfv > max_cost_value:
                        #pass
                        continue
                    return_list.append( (origin_position, cfv, tt, dis) )
        if max_routes is not None and len(return_list) > max_routes:
            return sorted(return_list, key = lambda x:x[1])[:max_routes]
        return return_list

    def return_travel_costs_1toX(self, origin_position, list_destination_positions, max_routes=None, max_cost_value=None, customized_section_cost_function = None):
        """
        This method will return a list of tuples of destination node and travel time of the X fastest routes between
        a list of possible origin nodes and a certain destination node, whereas the route starts at certain origins can
        be offset. Combinations that dont fullfill all constraints will not be returned.
        :param origin_position: origin_position: (current_edge_origin_node_index, current_edge_destination_node_index, relative_position)
        :param list_destination_positions: list of destination positions : (destination_edge_origin_node_index, destination_edge_destination_node_index, relative_position)
        :param max_routes: maximal number of fastest route triples that should be returned
        :param max_cost_value: latest cost function value of a route at destination to be considered as solution (max time if customized_section_cost_function == None)
        :param customized_section_cost_function: function to compute the travel cost of an section: args: (travel_time, travel_distance, current_dijkstra_node) -> cost_value
                if None: travel_time is considered as the cost_function of a section
        :return: list of (destination_position, cost_function_value, travel time, travel_distance) tuples
        """
        destination_nodes = {}
        return_list = []
        for pos in list_destination_positions:
            trivial_test = self.test_and_get_trivial_route_tt_and_dis(origin_position, pos)
            if trivial_test is not None:
                if max_cost_value is not None and trivial_test[1][0] > max_cost_value:
                    continue
                return_list.append( (pos, trivial_test[1][0], trivial_test[1][1], trivial_test[1][2]))
                continue
            start_node = pos[0]
            try:
                destination_nodes[start_node].append(pos)
            except:
                destination_nodes[start_node] = [pos]
        origin_node = origin_position[0]
        origin_overhead = (0.0, 0.0, 0.0)
        if origin_position[1] is not None:
            origin_node = origin_position[1]
            origin_overhead = self.get_section_overhead(origin_position, from_start=False)
        if len(destination_nodes.keys()) > 0:
            if self._current_tt_factor is None:
                R = Router(self, origin_node, destination_nodes=destination_nodes.keys(), time_radius = max_cost_value, max_settled_targets = max_routes, forward_flag = True, customized_section_cost_function=customized_section_cost_function)
                s = R.compute(return_route=False)
            else:
                if max_cost_value is not None:
                    new_max_cost_value = max_cost_value/self._current_tt_factor
                else:
                    new_max_cost_value = None
                R = Router(self, origin_node, destination_nodes=destination_nodes.keys(), time_radius = new_max_cost_value, max_settled_targets = max_routes, forward_flag = True, customized_section_cost_function=customized_section_cost_function)
                s = R.compute(return_route=False)
                s = [(entry[0], (entry[1][0] * self._current_tt_factor, entry[1][1] * self._current_tt_factor, entry[1][2])) for entry in s]
            for entry in s:
                cfv, tt, dis = entry[1]
                if tt < 0 or cfv == float("inf"):
                    continue
                dest_node = entry[0][-1]
                if customized_section_cost_function is None:
                    self._add_to_database(origin_node, dest_node, cfv, tt, dis)
                cfv += origin_overhead[0]
                tt += origin_overhead[1]
                dis += origin_overhead[2]
                for destination_position in destination_nodes[dest_node]:
                    destination_overhead = (0.0, 0.0, 0.0)
                    if destination_position[1] is not None:
                        destination_overhead = self.get_section_overhead(destination_position, from_start=True)
                    cfv += destination_overhead[0]
                    tt += destination_overhead[1]
                    dis += destination_overhead[2]
                    if max_cost_value is not None and cfv > max_cost_value:
                        continue
                    return_list.append( (destination_position, cfv, tt, dis) )
        if max_routes is not None and len(return_list) > max_routes:
            return sorted(return_list, key = lambda x:x[1])[:max_routes]
        return return_list

    def return_best_route_1to1(self, origin_position, destination_position, customized_section_cost_function = None):
        """
        This method will return the best route [list of node_indices] between two nodes,
        while origin_position[0] and destination_postion[1](or destination_position[0] if destination_postion[1]==None) is included.
        :param origin_position: (current_edge_origin_node_index, current_edge_destination_node_index, relative_position)
        :param destination_position: (destination_edge_origin_node_index, destination_edge_destination_node_index, relative_position)
        :param customized_section_cost_function: function to compute the travel cost of an section: args: (travel_time, travel_distance, current_dijkstra_node) -> cost_value
                if None: travel_time is considered as the cost_function of a section
        :return : route (list of node_indices) of best route
        """
        trivial_test = self.test_and_get_trivial_route_tt_and_dis(origin_position, destination_position)
        if trivial_test is not None:
            return trivial_test[0]
        origin_node = origin_position[0]
        destination_node = destination_position[0]
        if origin_position[1] is not None:
            origin_node = origin_position[1]
        R = Router(self, origin_node, destination_nodes=[destination_node], mode='bidirectional', customized_section_cost_function=customized_section_cost_function)
        node_list = R.compute(return_route=True)[0][0]
        if origin_node != origin_position[0]:
            node_list = [origin_position[0]] + node_list
        if destination_position[1] is not None:
            node_list.append(destination_position[1])
        return node_list

    def return_best_route_Xto1(self, list_origin_positions, destination_position, max_cost_value=None, customized_section_cost_function = None):
        """This method will return the best route between a list of possible origin nodes and a certain destination
        node. A best route is defined by [list of node_indices] between two nodes,
        while origin_position[0] and destination_position[1](or destination_position[0]
        if destination_position[1]==None) is included. Combinations that do not fulfill all constraints
        will not be returned.

        :param list_origin_positions: list of origin_positions
                (current_edge_origin_node_index, current_edge_destination_node_index, relative_position)
        :type list_origin_positions: list
        :param destination_position: (origin_node_index, destination_node_index, relative_position) of destination_edge
        :type destination_position: list
        :param max_cost_value: latest cost function value of a route at destination to be considered as solution
                (max time if customized_section_cost_function == None)
        :type max_cost_value: float/None
        :param customized_section_cost_function: function to compute the travel cost of an section
                which takes the args: (travel_time, travel_distance, current_dijkstra_node_index) -> cost_value
                if None: travel_time is considered as the cost_function of a section
        :type customized_section_cost_function: func
        :return: list of node-indices of the fastest route (empty, if no route is found, that fullfills the constraints)
        :rtype: list
        """
        origin_nodes = {}
        return_route = None
        best_cfv = float("inf")
        for pos in list_origin_positions:
            trivial_test = self.test_and_get_trivial_route_tt_and_dis(pos, destination_position)
            if trivial_test is not None:
                if max_cost_value is not None and trivial_test[1][0] > max_cost_value:
                    continue
                if trivial_test[1][0] < best_cfv:
                    return_route = trivial_test[0]
                    best_cfv = trivial_test[1][0]
                continue
            start_node = pos[0]
            if pos[1] is not None:
                start_node = pos[1]
            try:
                origin_nodes[start_node].append(pos)
            except:
                origin_nodes[start_node] = [pos]
        if len(origin_nodes.keys()) > 0:
            destination_node = destination_position[0]
            destination_overhead = (0.0, 0.0, 0.0)
            if destination_position[1] is not None:
                destination_overhead = self.get_section_overhead(destination_position, from_start=True)
            if self._current_tt_factor is None:
                R = Router(self, destination_node, destination_nodes=origin_nodes.keys(), time_radius = max_cost_value, forward_flag = False, customized_section_cost_function=customized_section_cost_function)
                s = R.compute(return_route=True)
            else:
                if max_cost_value is not None:
                    new_max_cost_value = max_cost_value/self._current_tt_factor
                else:
                    new_max_cost_value = None
                R = Router(self, destination_node, destination_nodes=origin_nodes.keys(), time_radius = new_max_cost_value, forward_flag = False, customized_section_cost_function=customized_section_cost_function)
                s = R.compute(return_route=True)
                s = [(entry[0], (entry[1][0] * self._current_tt_factor, entry[1][1] * self._current_tt_factor, entry[1][2])) for entry in s]
            for entry in s:
                cfv, tt, dis = entry[1]
                if tt < 0:
                    continue
                cfv += destination_overhead[0]
                tt += destination_overhead[1]
                dis += destination_overhead[2]
                org_node = entry[0][0]
                for origin_position in origin_nodes[org_node]:
                    origin_overhead = (0.0, 0.0, 0.0)
                    if origin_position[1] is not None:
                        origin_overhead = self.get_section_overhead(origin_position, from_start=False)
                    cfv += origin_overhead[0]
                    tt += origin_overhead[1]
                    dis += origin_overhead[2]
                    if max_cost_value is not None and cfv > max_cost_value:
                        continue
                    if cfv > best_cfv:
                        continue
                    node_list = entry[0][:]
                    if origin_position[1] is not None:
                        if destination_position[1] is not None:
                            node_list = [origin_position[0]] + node_list + [destination_position[1]]
                        else:
                            node_list = [origin_position[0]] + node_list
                    else:
                        if destination_position[1] is not None:
                            node_list = node_list + [destination_position[1]]
                    best_cfv = cfv
                    if len(node_list) < 2:
                        return_route = []
                    else:
                        return_route = node_list 
        return return_route

    def return_best_route_1toX(self, origin_position, list_destination_positions, max_cost_value=None, customized_section_cost_function = None):
        """This method will return the best route between a list of possible destination nodes and a certain origin
        node. A best route is defined by [list of node_indices] between two nodes,
        while origin_position[0] and destination_position[1](or destination_position[0]
        if destination_position[1]==None) is included. Combinations that do not fulfill all constraints
        will not be returned.
        Specific to this framework: max_cost_value = None translates to max_cost_value = DEFAULT_MAX_X_SEARCH

        :param origin_position: (origin_node_index, destination_node_index, relative_position) of origin edge
        :type origin_position: list
        :param list_destination_positions: list of destination positions
                (origin_node_index, destination_node_index, relative_position) of destination_edge
        :type list_destination_positions: list
        :param max_cost_value: latest cost function value of a route at destination to be considered as solution
                (max time if customized_section_cost_function == None)
        :type max_cost_value: float/None
        :param customized_section_cost_function: function to compute the travel cost of an section
                which takes the args: (travel_time, travel_distance, current_dijkstra_node_index) -> cost_value
                if None: travel_time is considered as the cost_function of a section
        :type customized_section_cost_function: func
        :return: list of node-indices of the fastest route (empty, if no route is found, that fullfills the constraints)
        :rtype: list
        """
        destination_nodes = {}
        return_route = []
        best_cfv = float("inf")
        for pos in list_destination_positions:
            trivial_test = self.test_and_get_trivial_route_tt_and_dis(origin_position, pos)
            if trivial_test is not None:
                if max_cost_value is not None and trivial_test[1][0] > max_cost_value:
                    continue
                if trivial_test[1][0] < best_cfv:
                    return_route = trivial_test[0]
                    best_cfv = trivial_test[1][0]
                continue
            start_node = pos[0]
            try:
                destination_nodes[start_node].append(pos)
            except:
                destination_nodes[start_node] = [pos]
        origin_node = origin_position[0]
        origin_overhead = (0.0, 0.0, 0.0)
        if origin_position[1] is not None:
            origin_node = origin_position[1]
            origin_overhead = self.get_section_overhead(origin_position, from_start=False)
        if len(destination_nodes.keys()) > 0:
            if self._current_tt_factor is None:
                R = Router(self, origin_node, destination_nodes=destination_nodes.keys(), time_radius = max_cost_value, forward_flag = True)
                s = R.compute(return_route=True)
            else:
                if max_cost_value is not None:
                    new_max_cost_value = max_cost_value/self._current_tt_factor
                else:
                    new_max_cost_value = None
                R = Router(self, origin_node, destination_nodes=destination_nodes.keys(), time_radius = new_max_cost_value, forward_flag = True, customized_section_cost_function=customized_section_cost_function)
                s = R.compute(return_route=True)
                s = [(entry[0], (entry[1][0] * self._current_tt_factor, entry[1][1] * self._current_tt_factor, entry[1][2])) for entry in s]
            for entry in s:
                cfv, tt, dis = entry[1]
                if tt < 0:
                    continue
                cfv += origin_overhead[0]
                tt += origin_overhead[1]
                dis += origin_overhead[2]
                dest_node = entry[0][-1]
                for destination_position in destination_nodes[dest_node]:
                    destination_overhead = (0.0, 0.0, 0.0)
                    if destination_position[1] is not None:
                        destination_overhead = self.get_section_overhead(destination_position, from_start=True)
                    cfv += destination_overhead[0]
                    tt += destination_overhead[1]
                    dis += destination_overhead[2]
                    if max_cost_value is not None and cfv > max_cost_value:
                        continue
                    if cfv > best_cfv:
                        continue
                    node_list = entry[0][:]
                    if origin_position[1] is not None:
                        if destination_position[1] is not None:
                            node_list = [origin_position[0]] + node_list + [destination_position[1]]
                        else:
                            node_list = [origin_position[0]] + node_list
                    else:
                        if destination_position[1] is not None:
                            node_list = node_list + [destination_position[1]]
                    best_cfv = cfv
                    if len(node_list) < 2:
                        return_route = []
                    else:
                        return_route = node_list 

        return return_route

    def test_and_get_trivial_route_tt_and_dis(self, origin_position, destination_position):
        """ this functions test for trivial routing solutions between origin_position and destination_position
        if no trivial solution is found
        :return None
        else
        :return (route, (travel_time, travel_distance))
        """
        if origin_position[0] == destination_position[0]:
            if origin_position[1] is None:
                if destination_position[1] is None:
                    return ([], (0.0, 0.0, 0.0) )
                else:
                    return ([destination_position[0], destination_position[1]], self.get_section_overhead(destination_position) )
            else:
                if destination_position[1] is None:
                    return None
                else:
                    if destination_position[1] == origin_position[1]:
                        if origin_position[2] > destination_position[2]:
                            return None
                        else:
                            effective_position = (origin_position[0], origin_position[1], destination_position[2] - origin_position[2])
                            cfv, tt, dis = self.get_section_overhead(effective_position, from_start = True)
                            return ([destination_position[0], destination_position[1]], (cfv, tt, dis)) 
                    else:
                        return None
        elif origin_position[1] is not None and origin_position[1] == destination_position[0]:
            rest = self.get_section_overhead(origin_position, from_start = False)
            rest_dest = self.get_section_overhead(destination_position, from_start = True)
            route = [origin_position[0], origin_position[1]]
            #print(f"nw basic argh {rest} {rest_dest}")
            if destination_position[1] is not None:
                route.append( destination_position[1] )
            return (route, (rest[0] + rest_dest[0], rest[1] + rest_dest[1], rest[2] + rest_dest[2]))
        return None

    def return_travel_cost_matrix(self, list_positions, customized_section_cost_function = None):
        """This method will return the cost_function_value between all positions specified in list_positions

        :param list_positions: list of positions to be computed
        :type list_positions: list
        :param customized_section_cost_function: function to compute the travel cost of an section
                which takes the args: (travel_time, travel_distance, current_dijkstra_node_index) -> cost_value
                if None: travel_time is considered as the cost_function of a section
        :type customized_section_cost_function: func
        :return: dictionary: (o_pos,d_pos) -> (cfv, tt, dist)
        :rtype: dict
        """
        return_dict = {}
        for o_pos in list_positions:
            res = self.return_travel_costs_1toX(o_pos, list_positions, customized_section_cost_function=customized_section_cost_function)
            for d_pos, cfv, tt, dist in res:
                return_dict[(o_pos, d_pos)] = (cfv, tt, dist)
        return return_dict

    def move_along_route(self, route, last_position, time_step, sim_vid_id=None, new_sim_time=None,
                         record_node_times=False): # TODO # correct first entry of route!!!!
        """This method computes the new position of a (vehicle) on a given route (node_index_list) from it's
        last_position (position_tuple). The first entry of route has to be the same as the first entry of last_position!
        :param route: list of node_indices of the current route
        :type route: list
        :param last_position: position_tuple of starting point
        :type last_position: list
        :param time_step: time [s] passed since last observed at last_position
        :type time_step: float
        :param sim_vid_id: id of simulation vehicle; required for simulation environments with external traffic simulator
        :type sim_vid_id: int
        :param new_sim_time: new time to coordinate simulation times
        :type new_sim_time: float
        :param record_node_times: if this flag is set False, the output list_passed_node_times will always return []
        :type record_node_times: bool
        :return: returns a tuple with
                i) new_position_tuple
                ii) driven distance
                iii) arrival_in_time_step [s]: -1 if vehicle did not reach end of route | time since beginning of time
                        step after which the vehicle reached the end of the route
                iv) list_passed_nodes: if during the time step a number of nodes were passed, these are
                v) list_passed_node_times: list of checkpoint times at the respective passed nodes
        """
        if new_sim_time is not None:
            end_time = new_sim_time + time_step
            last_time = new_sim_time
        else:
            end_time = self.sim_time + time_step
            last_time = self.sim_time
        c_pos = last_position
        if c_pos[2] is None:
            if len(route) == 0:
                return c_pos, 0, last_time, [], []
            c_pos = (c_pos[0], route[0], 0.0)
        list_passed_nodes = []
        list_passed_node_times = []
        arrival_in_time_step = -1
        driven_distance = 0
        #
        c_cluster = None
        last_dyn_step = None
        for i in range(len(route)):
            # check remaining time on current edge
            if c_pos[2] is None:
                c_pos = (c_pos[0], route[i], 0)
            rel_factor = (1 - c_pos[2])
            tt, td = self.get_section_infos(c_pos[0], c_pos[1])
            if tt > 86400:
                LOG.warning(f"move_along_route: very large travel time on edge ({c_pos[0]} -> {c_pos[1]} for vid {sim_vid_id} at time {new_sim_time}) (blocked after tt update?) -> vehicle jumps this edge")
                tt = 0
            c_edge_tt = tt
            c_edge_td = td
            next_node_time = last_time + rel_factor * c_edge_tt
            if next_node_time > end_time:
                # move vehicle to final position of current edge
                end_rel_factor = (end_time - last_time) / tt + c_pos[2]
                #print(end_rel_factor, end_time, last_time, c_edge_tt, c_pos[2])
                driven_distance += (end_rel_factor - c_pos[2]) * c_edge_td
                c_pos = (c_pos[0], c_pos[1], end_rel_factor)
                arrival_in_time_step = -1
                break
            else:
                # move vehicle to next node/edge and record data
                driven_distance += rel_factor * c_edge_td
                next_node = route[i]
                list_passed_nodes.append(next_node)
                if record_node_times:
                    list_passed_node_times.append(next_node_time)
                last_time = next_node_time
                c_pos = (next_node, None, None)
                arrival_in_time_step = last_time
        return c_pos, driven_distance, arrival_in_time_step, list_passed_nodes, list_passed_node_times

    def add_travel_infos_to_database(self, travel_info_dict):
        """ this function can be used to include externally computed (e.g. multiprocessing) route travel times
        into the database if present

        it adds all infos from travel_info_dict to its database self.travel_time_infos
        its database is from node to node, therefore overheads have to be removed from routing results

        :param travel_info_dict: dictionary with keys (origin_position, target_positions) -> values (cost_function_value, travel_time, travel_distance)
        """
        pass

    def _reset_internal_attributes_after_travel_time_update(self):
        pass

    def _add_to_database(self, o_node, d_node, cfv, tt, dis):
        """ this function is call when new routing results have been computed
        depending on the class the function can be overwritten to store certain results in the database
        """
        pass
