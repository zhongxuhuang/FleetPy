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
    "input_parameters_optional": [G_NW_DYNAMIC_F, G_DYNAMIC_TT_UPDATE_INTERVAL],
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
        self._tt_infos_from_folder = True
        self._current_tt_factor = None
        self.travel_time_file_infos = self._load_tt_folder_path(network_dynamics_file_name=network_dynamics_file_name)
        self.loadNetwork(network_name_dir, network_dynamics_file_name=network_dynamics_file_name, scenario_time=scenario_time)
        self.current_dijkstra_number = 1    #used in dijkstra-class
        self.sim_time = 0   # TODO #
        self.zones = None   # TODO #
        self.initial_zone_vehicle_counts = {}
        self.zone_vehicle_counter = {}
        self.current_pv_zone_vehicle_counts = {}
        self.current_total_zone_vehicle_counts = {}
        self.current_route_zone_edge_infos = {}
        self.current_route_zone_distances = {}
        self.zone_mfd_functions = {}
        self.zone_vehicle_counter_initialized = False
        self.zone_vehicle_counter_init_time = None
        self.dynamic_tt_update_interval = None
        self.dynamic_tt_update_start_time = 0
        self._last_dynamic_tt_update_time = None
        self._zone_to_edge_cache = None
        self._zone_to_edge_cache_zones_id = None
        self._pv_zone_time_occupations = []
        self._route_edge_occupations = []
        self._zone_priority_queue_states = {}
        self._active_mod_zone_trips = {}
        with open(os.sep.join([self.network_name_dir, "base","crs.info"]), "r") as f:
            self.crs = f.read()
        LOG.debug(
            f"network loaded vehicle counts: "
            f"pv={self.current_pv_zone_vehicle_counts}, "
            f"mod={self.zone_vehicle_counter}, "
            f"total={self.current_total_zone_vehicle_counts}"
        )

    def set_dynamic_tt_update_interval(self, update_interval, simulation_time_step=1, start_time=0):
        """Configures how often MFD-derived travel times are written to edge travel times.

        :param update_interval: interval between dynamic travel-time updates
        :param simulation_time_step: fallback update interval if no valid interval is provided
        :param start_time: first simulation time at which dynamic updates may be applied
        :return: None
        """
        if update_interval is None:
            update_interval = simulation_time_step
        try:
            update_interval = float(update_interval)
        except (TypeError, ValueError):
            LOG.warning(
                f"invalid dynamic_tt_update_interval={update_interval}; "
                f"use simulation time step {simulation_time_step}"
            )
            update_interval = simulation_time_step
        if update_interval <= 0:
            LOG.warning(
                f"dynamic_tt_update_interval={update_interval} must be positive; "
                f"use simulation time step {simulation_time_step}"
            )
            update_interval = simulation_time_step
        self.dynamic_tt_update_interval = update_interval
        self.dynamic_tt_update_start_time = start_time
        self._last_dynamic_tt_update_time = None

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
            if self.travel_time_file_infos.get(simulation_time, None) is not None:
                self.load_tt_file(simulation_time)
                new_tt_flag = True
            if self._is_dynamic_tt_update_time(simulation_time):
                new_tt_flag = self._update_dynamic_edge_travel_times(simulation_time) or new_tt_flag
        return new_tt_flag

    def _is_dynamic_tt_update_time(self, simulation_time):
        """Checks whether zone counts and MFD-derived edge travel times should be refreshed.

        :param simulation_time: current simulation time
        :return: True if a dynamic travel-time update should be performed
        """
        if self.dynamic_tt_update_interval is None:
            return True
        if simulation_time < self.dynamic_tt_update_start_time:
            return False
        if self._last_dynamic_tt_update_time is None:
            return True
        return simulation_time - self._last_dynamic_tt_update_time >= self.dynamic_tt_update_interval

    def _update_dynamic_edge_travel_times(self, simulation_time):
        """Updates edge travel times from zone MFD speeds.

        :param simulation_time: current simulation time of the dynamic update
        :return: True if at least one edge travel time was updated
        """
        self._last_dynamic_tt_update_time = simulation_time
        self._update_current_zone_vehicle_counts(simulation_time)
        zone_to_edges = self._get_zone_to_edge_cache()
        if not zone_to_edges:
            LOG.debug(
                f"dynamic edge tt update at {simulation_time}: no zone-edge mapping "
                f"(zones_attached={self.zones is not None})"
            )
            return False

        changed_edges = []
        zone_speed_summary = []
        missing_speed_zones = []
        if self._current_tt_factor is not None:
            LOG.debug("dynamic edge TT update replaces the current travel time factor")
            self._current_tt_factor = None
        for zone_id, edge_infos in zone_to_edges.items():
            number_vehicles = self.current_total_zone_vehicle_counts.get(zone_id, 0)
            avg_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles)
            if avg_speed is None or avg_speed <= 0:
                missing_speed_zones.append(zone_id)
                zone_speed_summary.append(
                    (zone_id, self.current_pv_zone_vehicle_counts.get(zone_id, 0),
                     self.zone_vehicle_counter.get(zone_id, 0), number_vehicles, avg_speed, len(edge_infos))
                )
                continue
            zone_speed_summary.append(
                (zone_id, self.current_pv_zone_vehicle_counts.get(zone_id, 0),
                 self.zone_vehicle_counter.get(zone_id, 0), number_vehicles, avg_speed, len(edge_infos))
            )
            for o_node_index, d_node_index, edge_distance in edge_infos:
                dynamic_tt = edge_distance / avg_speed
                self._set_edge_tt(o_node_index, d_node_index, dynamic_tt)
                changed_edges.append((o_node_index, d_node_index, dynamic_tt))

        if LOG.isEnabledFor(logging.DEBUG):
            sample = zone_speed_summary[:20]
            LOG.debug(
                f"dynamic zone speed summary at {simulation_time}: "
                f"zones={len(zone_speed_summary)} sample={sample} "
                f"missing_speed_zones={len(missing_speed_zones)} changed_edges={len(changed_edges)}"
            )
        if changed_edges:
            self._reset_internal_attributes_after_travel_time_update()
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

    def _after_dynamic_edge_tt_update(self, changed_edges):
        """Runs backend-specific updates after dynamic edge travel times changed.

        :param changed_edges: list of tuples containing start node, end node and new travel time
        :return: None
        """
        pass

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
        return self._zone_to_edge_cache
    
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
        edge_obj.set_tt(new_travel_time)
        new_tt, dis = edge_obj.get_tt_distance()
        o_node.travel_infos_to[d_node_index] = (new_tt, dis)
        d_node.travel_infos_from[o_node_index] = (new_tt, dis)

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

    def initialize_zone_vehicle_counter(self, list_vehicles=None, list_positions=None, simulation_time=None):
        """Initializes a per-zone vehicle counter at the beginning of a simulation.

        Zone assignment is intentionally kept behind _get_zone_from_position(); if no zone
        system is attached yet, the counter remains empty until the zone logic is added.

        :param list_vehicles: optional iterable of vehicle objects with a pos attribute
        :param list_positions: optional iterable of position tuples
        :param simulation_time: simulation time at which the counter is initialized
        :return: dictionary zone_id -> number of vehicles in this zone
        """
        zone_counts = {zone_id: 0 for zone_id in self._get_defined_zones()}
        positions = []
        if list_positions is not None:
            positions.extend(list_positions)
        if list_vehicles is not None:
            for veh_obj in list_vehicles:
                pos = getattr(veh_obj, "pos", None)
                if pos is not None:
                    positions.append(pos)

        for pos in positions:
            zone_id = self._get_zone_from_position(pos)
            if zone_id is None:
                continue
            zone_counts[zone_id] = zone_counts.get(zone_id, 0) + 1
        assigned_positions = sum(zone_counts.values())
        unassigned_positions = len(positions) - assigned_positions

        self.initial_zone_vehicle_counts = dict(zone_counts)
        self.zone_vehicle_counter = dict(zone_counts)
        self.current_pv_zone_vehicle_counts = {zone_id: 0 for zone_id in self._get_defined_zones()}
        self.current_total_zone_vehicle_counts = dict(zone_counts)
        self.zone_vehicle_counter_initialized = True
        self.zone_vehicle_counter_init_time = simulation_time
        self._initialize_zone_priority_queue_states(simulation_time)
        LOG.debug(
            f"initial zone vehicle counts at {simulation_time}: "
            f"positions={len(positions)} assigned={assigned_positions} "
            f"unassigned={unassigned_positions} top_mod={sorted(zone_counts.items(), key=lambda x: x[1], reverse=True)[:20]} "
            f"pv={self.current_pv_zone_vehicle_counts}, "
            f"mod={self.zone_vehicle_counter}, "
            f"total={self.current_total_zone_vehicle_counts}"
        )
        return self.zone_vehicle_counter

    def _initialize_zone_priority_queue_states(self, simulation_time=None):
        """Initializes priority queue bathtub states for all defined zones.

        :param simulation_time: simulation time used for initializing new zone states
        :return: None
        """
        init_time = self.sim_time if simulation_time is None else simulation_time
        for zone_id in self._get_defined_zones():
            self._get_zone_priority_queue_state(zone_id, init_time)

    def _get_zone_priority_queue_state(self, zone_id, simulation_time=None):
        """Returns the priority queue state of a zone.

        If no state exists for the given zone yet, it is initialized using the
        current MoD vehicle count and the zone-specific MFD speed.

        :param zone_id: zone identifier for which the priority queue state is requested
        :param simulation_time: simulation time used for initializing a new state
        :return: dictionary with the priority queue bathtub state of this zone
        """
        if zone_id is None:
            return None
        if zone_id not in self._zone_priority_queue_states:
            init_time = self.sim_time if simulation_time is None else simulation_time
            number_vehicles = self.zone_vehicle_counter.get(zone_id, 0)
            avg_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles)
            self._zone_priority_queue_states[zone_id] = {
                "E": 0,  # cumulative number of MoD trips that entered this zone
                "G": 0,  # cumulative number of MoD trips that completed in this zone
                "z": 0.0,  # cumulative bathtub progress since initialization
                "v": 0.0 if avg_speed is None else avg_speed,  # current zone speed from the MFD
                "last_time": init_time,  # last simulation time at which this state was advanced
                "heap": []  # completion thresholds of active MoD trips
            }
            self._log_zone_priority_queue_state(zone_id, "init", init_time)
        return self._zone_priority_queue_states[zone_id]

    def _log_zone_priority_queue_state(self, zone_id, event, simulation_time=None, extra=None):
        """Logs a compact snapshot of one zone priority queue state.

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
        active_count = max(state["E"] - state["G"], 0)
        heap_top = heapq.nsmallest(min(5, len(heap)), heap) if heap else []
        log_msg = (
            f"zone PQ {event} zone={zone_id} t={simulation_time} "
            f"E={state['E']} G={state['G']} active={active_count} "
            f"z={state['z']} v={state['v']} last={state['last_time']} "
            f"heap_size={len(heap)} heap_min={heap[0] if heap else None} "
            f"heap_top={heap_top}"
        )
        if extra is not None:
            log_msg += f" | {extra}"
        LOG.debug(log_msg)

    def _advance_zone_priority_queue_state(self, zone_id, simulation_time):
        """Advances the priority queue bathtub state of one zone.

        The state is advanced from its last update time to the given simulation
        time. All trips whose completion threshold is reached are removed from
        the priority queue and counted as completed.

        :param zone_id: zone identifier whose priority queue state is advanced
        :param simulation_time: simulation time to which the state is advanced
        :return: number of newly completed MoD trips in this zone
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
        while state["heap"] and state["heap"][0] <= state["z"]:
            heapq.heappop(state["heap"])
            completed += 1
        if completed:
            state["G"] += completed
        self._set_zone_mod_count_from_priority_queue(zone_id)
        if completed:
            self._log_zone_priority_queue_state(
                zone_id,
                "complete",
                simulation_time,
                extra=f"dt={delta_t} completed={completed}"
            )
        return completed

    def _set_zone_mod_count_from_priority_queue(self, zone_id):
        """Updates the stored MoD vehicle count from active bathtub trips.

        :param zone_id: zone identifier whose MoD vehicle count is updated
        :return: None
        """
        state = self._get_zone_priority_queue_state(zone_id, self.sim_time)
        active_count = max(state["E"] - state["G"], 0)
        self.zone_vehicle_counter[zone_id] = active_count

    def _update_zone_priority_queue_speeds(self):
        """Refreshes priority queue speeds using current MFD vehicle counts.

        :return: None
        """
        for zone_id, state in self._zone_priority_queue_states.items():
            number_vehicles = self.current_total_zone_vehicle_counts.get(zone_id, 0)
            avg_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles)
            if avg_speed is not None:
                state["v"] = avg_speed

    def register_zone_mod_trip(self, zone_id, start_time, travel_distance, number_vehicles=1, fallback_speed=None):
        """Registers MoD trips in one zone using the priority queue bathtub formulation.

        Each registered trip receives a completion threshold based on the current
        bathtub progress and the remaining travel distance in the zone.

        :param zone_id: zone identifier in which the MoD trips are registered
        :param start_time: simulation time at which the trips enter the zone
        :param travel_distance: travel distance covered by the trips inside the zone
        :param number_vehicles: number of identical MoD trips to register
        :param fallback_speed: speed used if no valid MFD speed is available
        :return: None
        """
        if zone_id is None or number_vehicles <= 0:
            return
        state = self._get_zone_priority_queue_state(zone_id, start_time)
        number_vehicles_in_zone = self.current_total_zone_vehicle_counts.get(zone_id, 0)
        avg_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles_in_zone)
        if avg_speed is not None and avg_speed > 0:
            state["v"] = avg_speed
        elif fallback_speed is not None and fallback_speed > 0:
            state["v"] = fallback_speed
        if travel_distance <= 0:
            state["E"] += number_vehicles
            state["G"] += number_vehicles
            self._set_zone_mod_count_from_priority_queue(zone_id)
            self._log_zone_priority_queue_state(
                zone_id,
                "zero_distance",
                start_time,
                extra=f"n={number_vehicles} dist={travel_distance}"
            )
            return

        projected_delta_t = max(start_time - state["last_time"], 0)
        projected_z = state["z"] + projected_delta_t * state["v"]
        theta = travel_distance + projected_z
        for _ in range(number_vehicles):
            heapq.heappush(state["heap"], theta)
        state["E"] += number_vehicles
        self._set_zone_mod_count_from_priority_queue(zone_id)
        self._log_zone_priority_queue_state(
            zone_id,
            "push",
            start_time,
            extra=(
                f"n={number_vehicles} dist={travel_distance} "
                f"projected_dt={projected_delta_t} projected_z={projected_z} "
                f"theta={theta}"
            )
        )

    def register_mod_route_to_zone_priority_queues(
        self, route, start_time, end_time=None, number_vehicles=1, traveled_distance=0.0
    ):
        """Registers the current route zone segment from traveled route distance.

        This is a static/batch helper. Dynamic MoD simulation should register
        trips from move_along_route(), where the actual vehicle position and
        entry time into a zone segment are known. This helper does not estimate
        future zone entry times. It uses traveled_distance to find the current
        contiguous zone segment and registers only the remaining distance in
        that segment.

        :param route: list of node indices describing the planned route
        :param start_time: simulation time at which the current segment is registered
        :param end_time: kept for backwards compatibility; not used for registration
        :param number_vehicles: number of identical MoD vehicles following the route
        :param traveled_distance: distance already traveled along the route
        :return: None
        """
        if not route or len(route) < 2 or number_vehicles <= 0:
            return

        zone_route_infos = []
        for i in range(len(route) - 1):
            o_node = route[i]
            d_node = route[i + 1]
            zone_id = self._get_zone_from_position((o_node, d_node, 0.0))
            if zone_id is None:
                continue
            edge_tt, edge_distance = self.get_section_infos(o_node, d_node)
            zone_route_infos.append((zone_id, edge_distance, edge_tt))

        if not zone_route_infos:
            return

        current_zone = None
        current_zone_distance = 0
        current_zone_static_tt = 0
        current_zone_entry_distance = 0
        accumulated_distance = 0
        zone_segments = []
        for zone_id, edge_distance, edge_tt in zone_route_infos:
            if current_zone is None:
                current_zone = zone_id
                current_zone_entry_distance = accumulated_distance
            elif zone_id != current_zone:
                zone_segments.append((current_zone, current_zone_entry_distance, current_zone_distance, current_zone_static_tt))
                current_zone = zone_id
                current_zone_distance = 0
                current_zone_static_tt = 0
                current_zone_entry_distance = accumulated_distance
            current_zone_distance += edge_distance
            current_zone_static_tt += edge_tt
            accumulated_distance += edge_distance
        if current_zone is not None:
            zone_segments.append((current_zone, current_zone_entry_distance, current_zone_distance, current_zone_static_tt))

        traveled_distance = max(traveled_distance, 0.0)
        for zone_id, entry_distance, zone_distance, zone_static_tt in zone_segments:
            segment_end_distance = entry_distance + zone_distance
            if traveled_distance >= segment_end_distance:
                continue
            if traveled_distance < entry_distance:
                remaining_zone_distance = zone_distance
            else:
                remaining_zone_distance = segment_end_distance - traveled_distance
            fallback_speed = zone_distance / zone_static_tt if zone_static_tt > 0 else None
            self.register_zone_mod_trip(
                zone_id,
                start_time,
                remaining_zone_distance,
                number_vehicles,
                fallback_speed=fallback_speed
            )
            return

    def _get_remaining_contiguous_zone_infos(self, route, current_position, route_index, zone_id):
        """Returns remaining distance and static travel time in one contiguous zone segment.

        :param route: list of node indices describing the remaining planned route
        :param current_position: current network position tuple of the vehicle
        :param route_index: current index in the route list
        :param zone_id: zone identifier of the current contiguous zone segment
        :return: tuple of remaining distance and static travel time in this zone segment
        """
        if current_position[1] is None or zone_id is None:
            return 0, 0

        distance = 0
        static_tt = 0
        c_zone_id = self._get_zone_from_position((current_position[0], current_position[1], 0.0))
        if c_zone_id != zone_id:
            return 0, 0
        edge_tt, edge_distance = self.get_section_infos(current_position[0], current_position[1])
        distance += (1 - current_position[2]) * edge_distance
        static_tt += (1 - current_position[2]) * edge_tt

        for i in range(route_index + 1, len(route)):
            o_node = route[i - 1]
            d_node = route[i]
            c_zone_id = self._get_zone_from_position((o_node, d_node, 0.0))
            if c_zone_id != zone_id:
                break
            edge_tt, edge_distance = self.get_section_infos(o_node, d_node)
            distance += edge_distance
            static_tt += edge_tt
        return distance, static_tt

    def _register_mod_zone_trip_if_needed(self, sim_vid_id, current_position, route, route_index, simulation_time):
        """Registers a moving MoD vehicle once when it enters a new zone segment.

        The registered travel distance starts at the actual current position.
        If a vehicle is first observed inside a zone segment, only the remaining
        distance to the end of that contiguous segment is registered.

        :param sim_vid_id: simulation vehicle identifier
        :param current_position: current network position tuple of the vehicle
        :param route: list of node indices describing the planned route
        :param route_index: current index in the route list
        :param simulation_time: current simulation time
        :return: None
        """
        if sim_vid_id is None or current_position[1] is None:
            return
        zone_id = self._get_zone_from_position((current_position[0], current_position[1], 0.0))
        if zone_id is None:
            self._active_mod_zone_trips.pop(sim_vid_id, None)
            return
        if self._active_mod_zone_trips.get(sim_vid_id) == zone_id:
            return

        zone_distance, zone_static_tt = self._get_remaining_contiguous_zone_infos(route, current_position, route_index, zone_id)
        if zone_distance <= 0:
            return
        fallback_speed = zone_distance / zone_static_tt if zone_static_tt > 0 else None
        self.register_zone_mod_trip(zone_id, simulation_time, zone_distance, 1, fallback_speed=fallback_speed)
        self._active_mod_zone_trips[sim_vid_id] = zone_id

    def _register_pv_zone_occupation(self, position, start_time, end_time, number_vehicles):
        """Stores private vehicle occupation of a zone during a time interval.

        :param position: network position used to determine the occupied zone
        :param start_time: start time of the private vehicle occupation interval
        :param end_time: end time of the private vehicle occupation interval
        :param number_vehicles: number of private vehicles occupying the zone
        :return: None
        """
        zone_id = self._get_zone_from_position(position)
        if zone_id is None:
            return
        if end_time <= start_time:
            return
        self._pv_zone_time_occupations.append((zone_id, start_time, end_time, number_vehicles))

    def _get_pv_zone_vehicle_counts(self, simulation_time):
        """Returns private vehicle counts per zone for the given simulation time.

        :param simulation_time: simulation time for which active private vehicles are counted
        :return: dictionary zone_id -> number of private vehicles in this zone
        """
        zone_counts = {zone_id: 0 for zone_id in self._get_defined_zones()}
        active_intervals = 0
        for zone_id, start_time, end_time, number_vehicles in self._pv_zone_time_occupations:
            if start_time <= simulation_time < end_time:
                active_intervals += 1
                zone_counts[zone_id] = zone_counts.get(zone_id, 0) + number_vehicles
        LOG.debug(
            f"pv counts t={simulation_time} active_intervals={active_intervals} "
            f"stored_intervals={len(self._pv_zone_time_occupations)} pv={zone_counts}"
        )
        return zone_counts

    def _update_current_zone_vehicle_counts(self, simulation_time):
        """Updates current zone vehicle counts for MoD and private vehicles.

        :param simulation_time: simulation time for which the zone counts are updated
        :return: dictionary zone_id -> total number of vehicles in this zone
        """
        tracked_zone_ids = set(self._get_defined_zones()) | set(self._zone_priority_queue_states.keys())
        for zone_id in tracked_zone_ids:
            self._advance_zone_priority_queue_state(zone_id, simulation_time)
        pv_counts = self._get_pv_zone_vehicle_counts(simulation_time)
        all_zone_ids = set(self.zone_vehicle_counter.keys()) | set(pv_counts.keys()) | set(self._zone_priority_queue_states.keys())
        self.current_pv_zone_vehicle_counts = pv_counts
        self.current_total_zone_vehicle_counts = {
            zone_id: self.zone_vehicle_counter.get(zone_id, 0) + pv_counts.get(zone_id, 0)
            for zone_id in all_zone_ids
        }
        LOG.debug(
            f"zone vehicle counts at {simulation_time}: "
            f"pv={self.current_pv_zone_vehicle_counts}, "
            f"mod={self.zone_vehicle_counter}, "
            f"total={self.current_total_zone_vehicle_counts}"
        )
        self._update_zone_priority_queue_speeds()
        return self.current_total_zone_vehicle_counts

    def _classify_route_edges_by_zone(self, route, last_position):
        """Classifies the currently planned remaining route into zone-specific edge lists.

        :param route: list of node indices describing the planned remaining route
        :param last_position: current network position before the planned route starts
        :return: tuple of zone edge information and zone distance dictionaries
        """
        zone_edge_infos = {}
        zone_distances = {}
        if len(route) == 0:
            self.current_route_zone_edge_infos = zone_edge_infos
            self.current_route_zone_distances = zone_distances
            return zone_edge_infos, zone_distances

        c_pos = last_position
        if c_pos[2] is None:
            c_pos = (c_pos[0], route[0], 0.0)

        for i in range(len(route)):
            if c_pos[2] is None:
                c_pos = (c_pos[0], route[i], 0.0)
            o_node = c_pos[0]
            d_node = c_pos[1]
            zone_id = self._get_zone_from_position((o_node, d_node, 0.0))
            if zone_id is not None:
                _, edge_distance = self.get_section_infos(o_node, d_node)
                zone_edge_infos.setdefault(zone_id, []).append((o_node, d_node, edge_distance))
                zone_distances[zone_id] = zone_distances.get(zone_id, 0) + edge_distance
            c_pos = (route[i], None, None)

        self.current_route_zone_edge_infos = zone_edge_infos
        self.current_route_zone_distances = zone_distances
        return zone_edge_infos, zone_distances

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
            avg_speed = self._get_zone_average_speed_from_mfd(zone_id, number_vehicles)
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
            f"pv={self.current_pv_zone_vehicle_counts.get(zone_id, 0)}, "
            f"mod={self.zone_vehicle_counter.get(zone_id, 0)}, "
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
        """This method can be used for dynamic network models in which the travel times will be derived from the
        number of vehicles/routes assigned to the network.

        :param route: list of nodes
        :param start_time: can be used as an offset in case the route is planned for a future time
        :param end_time: end of travel
        :param number_vehicles: accepted for interface compatibility; each call represents one route assignment
        """
        if not route or len(route) < 2:
            LOG.debug(f"pv route assignment skipped route={route} reason=too_short")
            return

        route_edge_infos = []
        total_route_distance = 0
        total_route_tt = 0
        for i in range(len(route) - 1):
            o_node = route[i]
            d_node = route[i + 1]
            edge_tt, edge_distance = self.get_section_infos(o_node, d_node)
            route_edge_infos.append((o_node, d_node, edge_tt, edge_distance))
            total_route_distance += edge_distance
            total_route_tt += edge_tt
        LOG.debug(
            f"pv route assignment route={route} start={start_time} end={end_time} "
            f"n={number_vehicles} edges={len(route_edge_infos)} "
            f"total_dist={total_route_distance} total_tt={total_route_tt}"
        )

        current_time = start_time
        if total_route_tt > 0:
            total_assigned_tt = end_time - start_time
            for i, (o_node, d_node, edge_tt, edge_distance) in enumerate(route_edge_infos):
                if i == len(route_edge_infos) - 1:
                    next_time = end_time
                else:
                    next_time = current_time + total_assigned_tt * edge_tt / total_route_tt
                self._route_edge_occupations.append((o_node, d_node, current_time, next_time))
                self._register_pv_zone_occupation((o_node, d_node, 0.0), current_time, next_time, number_vehicles)
                current_time = next_time
        else:
            for i, (o_node, d_node, edge_tt, edge_distance) in enumerate(route_edge_infos):
                next_time = end_time
                self._route_edge_occupations.append((o_node, d_node, current_time, next_time))
                self._register_pv_zone_occupation((o_node, d_node, 0.0), current_time, next_time, number_vehicles)
                current_time = next_time

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
        Specific to this framework: count moving vehicles to street network density! make sure to do this before
        updating the network!

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
        self._classify_route_edges_by_zone(route, last_position)
        c_pos = last_position
        if c_pos[2] is None:
            if len(route) == 0:
                if sim_vid_id is not None:
                    self._active_mod_zone_trips.pop(sim_vid_id, None)
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
            self._register_mod_zone_trip_if_needed(sim_vid_id, c_pos, route, i, last_time)
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
        if arrival_in_time_step != -1 and sim_vid_id is not None:
            self._active_mod_zone_trips.pop(sim_vid_id, None)
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
