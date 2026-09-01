import logging
import math
import os
import time

import pandas as pd

from src.simulation.Offers import TravellerOffer
from src.fleetctrl.FleetControlBase import FleetControlBase
from src.fleetctrl.planning.PlanRequest import PlanRequest
from src.fleetctrl.pooling.objectives import return_pooling_objective_function
from src.fleetctrl.pooling.immediate.insertion import insertion_with_heuristics
from src.misc.globals import *

LOG = logging.getLogger(__name__)
LARGE_INT = 100000
MOD_DESTINATION_GATING_OUTPUT_COLUMNS = [
    "sim_time",
    "request_id",
    "destination_zone",
    "vehicle_count",
    "critical_accumulation",
    "load_ratio",
    "gate_state_before",
    "gate_state_after",
    "gating_decision",
    "reason",
]

INPUT_PARAMETERS_PoolingInsertionHeuristicOnly = {
    "doc" : "this class represents a ride-pooling MoD-operator. the operators uses an insertion heuristic for assignment",
    "inherit" : "FleetControlBase",
    "input_parameters_mandatory": [],
    "input_parameters_optional": [
        G_OP_MOD_DEST_GATING,
        G_OP_MOD_DEST_GATING_CLOSE,
        G_OP_MOD_DEST_GATING_OPEN,
    ],
    "mandatory_modules": [],
    "optional_modules": []
}

class PoolingInsertionHeuristicOnly(FleetControlBase):
    """This class applies an Insertion Heuristic, in which new requests are inserted in the currently assigned
    vehicle plans and the insertion with the best control objective value is selected.

    IMPORTANT NOTE:
    Both the new and the previously assigned plan are stored and await an instant response of the request. Therefore,
    this fleet control module is only consistent for the ImmediateOfferSimulation class.
    """
    # TODO # clarify dependency to fleet simulation module
    def __init__(self, op_id, operator_attributes, list_vehicles, routing_engine, zone_system, scenario_parameters,
                 dir_names, op_charge_depot_infra=None, list_pub_charging_infra= []):
        """The specific attributes for the fleet control module are initialized. Strategy specific attributes are
        introduced in the children classes.

        :param op_id: operator id
        :type op_id: int
        :param operator_attributes: dictionary with keys from globals and respective values
        :type operator_attributes: dict
        :param list_vehicles: simulation vehicles; their assigned plans should be instances of the VehicleRouteLeg class
        :type list_vehicles: list
        :param routing_engine: routing engine
        :type routing_engine: Network
        :param scenario_parameters: access to all scenario parameters (if necessary)
        :type scenario_parameters: dict
        :param dirnames: directories for output and input
        :type dirnames: dict
        :param op_charge_depot_infra: reference to a OperatorChargingAndDepotInfrastructure class (optional) (unique for each operator)
        :type OperatorChargingAndDepotInfrastructure: OperatorChargingAndDepotInfrastructure
        :param list_pub_charging_infra: list of PublicChargingInfrastructureOperator classes (optional) (accesible for all agents)
        :type list_pub_charging_infra: list of PublicChargingInfrastructureOperator
        """
        super().__init__(op_id, operator_attributes, list_vehicles, routing_engine, zone_system, scenario_parameters,
                         dir_names=dir_names, op_charge_depot_infra=op_charge_depot_infra, list_pub_charging_infra=list_pub_charging_infra)
        # TODO # make standard in FleetControlBase
        self.rid_to_assigned_vid = {} # rid -> vid
        self.pos_veh_dict = {}  # pos -> list_veh
        self.vr_ctrl_f = return_pooling_objective_function(operator_attributes[G_OP_VR_CTRL_F])
        self.sim_time = scenario_parameters[G_SIM_START_TIME]
        # others # TODO # standardize IRS assignment memory?
        self.tmp_assignment = {}  # rid -> VehiclePlan
        self._init_dynamic_fleetcontrol_output_key(G_FCTRL_CT_RQU)
        self._initialize_mod_destination_gating(
            operator_attributes, scenario_parameters, zone_system, dir_names
        )

    def _initialize_mod_destination_gating(
            self, operator_attributes, scenario_parameters, zone_system, dir_names):
        """Validate and initialize optional MFD-based destination gating."""
        enabled = operator_attributes.get(G_OP_MOD_DEST_GATING, False)
        if type(enabled) is not bool:
            raise ValueError(f"{G_OP_MOD_DEST_GATING} must be True or False.")

        self.mod_destination_gating_enabled = enabled
        self.mod_destination_gating_close_ratio = None
        self.mod_destination_gating_open_ratio = None
        self._mod_destination_gate_closed = {}
        self._mod_destination_gating_records = []
        self._mod_destination_gating_zone_system = zone_system
        self.mod_destination_gating_output_f = None
        if not enabled:
            return

        network_mode = str(
            scenario_parameters.get(G_NETWORK_MODE, "dynamic_mfd")
        ).strip().lower()
        if network_mode != "dynamic_mfd":
            raise ValueError(
                f"{G_OP_MOD_DEST_GATING}=True requires {G_NETWORK_MODE}=dynamic_mfd."
            )

        close_ratio = operator_attributes.get(G_OP_MOD_DEST_GATING_CLOSE, 1.0)
        open_ratio = operator_attributes.get(G_OP_MOD_DEST_GATING_OPEN, 0.9)
        try:
            close_ratio = float(close_ratio)
            open_ratio = float(open_ratio)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{G_OP_MOD_DEST_GATING_CLOSE} and {G_OP_MOD_DEST_GATING_OPEN} "
                "must be finite numbers."
            ) from exc
        if not math.isfinite(close_ratio) or close_ratio <= 0:
            raise ValueError(f"{G_OP_MOD_DEST_GATING_CLOSE} must be finite and positive.")
        if not math.isfinite(open_ratio) or not 0 <= open_ratio < close_ratio:
            raise ValueError(
                f"{G_OP_MOD_DEST_GATING_OPEN} must satisfy 0 <= open ratio < close ratio."
            )

        required_zone_methods = ("get_zone_from_pos", "get_mfd_critical_accumulation")
        missing_zone_methods = [
            name for name in required_zone_methods
            if not callable(getattr(zone_system, name, None))
        ]
        if missing_zone_methods:
            raise ValueError(
                f"{G_OP_MOD_DEST_GATING}=True requires zone-system methods "
                f"{missing_zone_methods}."
            )
        if not callable(getattr(self.routing_engine, "get_current_zone_vehicle_counts", None)):
            raise ValueError(
                f"{G_OP_MOD_DEST_GATING}=True requires "
                "routing_engine.get_current_zone_vehicle_counts()."
            )

        self.mod_destination_gating_close_ratio = close_ratio
        self.mod_destination_gating_open_ratio = open_ratio
        self.mod_destination_gating_output_f = os.path.join(
            dir_names[G_DIR_OUTPUT], f"5-{self.op_id}_mod_destination_gating.csv"
        )

    @staticmethod
    def _gate_state_label(is_closed):
        return "closed" if is_closed else "open"

    def _append_mod_destination_gating_record(
            self, sim_time, request_id, destination_zone, vehicle_count,
            critical_accumulation, load_ratio, state_before, state_after,
            decision, reason):
        self._mod_destination_gating_records.append({
            "sim_time": sim_time,
            "request_id": request_id,
            "destination_zone": destination_zone,
            "vehicle_count": vehicle_count,
            "critical_accumulation": critical_accumulation,
            "load_ratio": load_ratio,
            "gate_state_before": self._gate_state_label(state_before),
            "gate_state_after": self._gate_state_label(state_after),
            "gating_decision": decision,
            "reason": reason,
        })

    def _evaluate_mod_destination_gating(self, prq, sim_time):
        """Return ``True`` when a new request must be rejected by hard gating."""
        if not getattr(self, "mod_destination_gating_enabled", False):
            return False

        request_id = prq.get_rid_struct()
        zone_system = self._mod_destination_gating_zone_system
        destination_zone = zone_system.get_zone_from_pos(prq.d_pos)
        if destination_zone is None or destination_zone == -1:
            self._append_mod_destination_gating_record(
                sim_time, request_id, destination_zone, None, None, None,
                False, False, "allow", "unmapped_destination"
            )
            return False

        critical_accumulation = zone_system.get_mfd_critical_accumulation(
            destination_zone
        )
        if critical_accumulation is None:
            self._append_mod_destination_gating_record(
                sim_time, request_id, destination_zone, None, None, None,
                False, False, "allow", "zone_without_mfd"
            )
            return False
        critical_accumulation = float(critical_accumulation)
        if not math.isfinite(critical_accumulation) or critical_accumulation <= 0:
            raise ValueError(
                f"Invalid MFD critical accumulation for zone {destination_zone}: "
                f"{critical_accumulation}"
            )

        zone_counts = self.routing_engine.get_current_zone_vehicle_counts()
        if destination_zone not in zone_counts:
            raise ValueError(
                f"No current vehicle count is available for MFD zone {destination_zone}."
            )
        vehicle_count = float(zone_counts[destination_zone])
        if not math.isfinite(vehicle_count) or vehicle_count < 0:
            raise ValueError(
                f"Invalid current vehicle count for zone {destination_zone}: {vehicle_count}"
            )
        load_ratio = vehicle_count / critical_accumulation

        state_before = self._mod_destination_gate_closed.get(destination_zone, False)
        if state_before:
            if load_ratio < self.mod_destination_gating_open_ratio:
                state_after = False
                reason = "reopen_threshold_reached"
            else:
                state_after = True
                reason = "held_closed"
        elif load_ratio >= self.mod_destination_gating_close_ratio:
            state_after = True
            reason = "close_threshold_reached"
        else:
            state_after = False
            reason = "held_open"

        self._mod_destination_gate_closed[destination_zone] = state_after
        decision = "reject" if state_after else "allow"
        self._append_mod_destination_gating_record(
            sim_time, request_id, destination_zone, vehicle_count,
            critical_accumulation, load_ratio, state_before, state_after,
            decision, reason
        )
        return state_after

    def _flush_mod_destination_gating_records(self):
        """Append buffered request-level gating decisions to the audit CSV."""
        if (
            not getattr(self, "mod_destination_gating_enabled", False)
            or not getattr(self, "_mod_destination_gating_records", None)
        ):
            return
        records = pd.DataFrame(
            self._mod_destination_gating_records,
            columns=MOD_DESTINATION_GATING_OUTPUT_COLUMNS,
        )
        write_header = not os.path.isfile(self.mod_destination_gating_output_f)
        records.to_csv(
            self.mod_destination_gating_output_f,
            mode="a",
            header=write_header,
            index=False,
        )
        self._mod_destination_gating_records = []

    def _record_user_request_time(self, sim_time, start_time):
        """Add one request's computation time to the standard operator output."""
        duration = round(time.perf_counter() - start_time, 5)
        previous = self._get_current_dynamic_fleetcontrol_value(sim_time, G_FCTRL_CT_RQU)
        total = duration if previous is None else previous + duration
        self._add_to_dynamic_fleetcontrol_output(sim_time, {G_FCTRL_CT_RQU: total})

    def receive_status_update(self, vid, simulation_time, list_finished_VRL, force_update=True):
        """This method can be used to update plans and trigger processes whenever a simulation vehicle finished some
         VehicleRouteLegs.

        :param vid: vehicle id
        :type vid: int
        :param simulation_time: current simulation time
        :type simulation_time: float
        :param list_finished_VRL: list of VehicleRouteLeg objects
        :type list_finished_VRL: list
        :param force_update: indicates if also current vehicle plan feasibilities have to be checked
        :type force_update: bool
        """
        super().receive_status_update(vid, simulation_time, list_finished_VRL, force_update=force_update)
        veh_obj = self.sim_vehicles[vid]
        try:
            self.pos_veh_dict[veh_obj.pos].append(veh_obj)
        except KeyError:
            self.pos_veh_dict[veh_obj.pos] = [veh_obj]
        LOG.debug(f"veh {veh_obj} | after status update: {self.veh_plans[vid]}")

    def user_request(self, rq, sim_time):
        """This method is triggered for a new incoming request. It generally adds the rq to the database. It has to
        return an offer to the user. This operator class only works with immediate responses and therefore either
        sends an offer or a rejection.

        :param rq: request object containing all request information
        :type rq: RequestDesign
        :param sim_time: current simulation time
        :type sim_time: float
        :return: offer
        :rtype: TravellerOffer
        """
        t0 = time.perf_counter()
        LOG.debug(f"Incoming request {rq.__dict__} at time {sim_time}")
        self.sim_time = sim_time
        prq = PlanRequest(rq, self.routing_engine, min_wait_time=self.min_wait_time,
                          max_wait_time=self.max_wait_time,
                          max_detour_time_factor=self.max_dtf, max_constant_detour_time=self.max_cdt,
                          add_constant_detour_time=self.add_cdt, min_detour_time_window=self.min_dtw,
                          boarding_time=self.const_bt)

        rid_struct = rq.get_rid_struct()
        self.rq_dict[rid_struct] = prq

        if prq.o_pos == prq.d_pos:
            LOG.debug(f"automatic decline for rid {rid_struct}!")
            self._create_rejection(prq, sim_time)
            return

        if self._evaluate_mod_destination_gating(prq, sim_time):
            LOG.debug(
                "destination gating rejects rid %s at time %s for destination %s",
                rid_struct,
                sim_time,
                prq.d_pos,
            )
            self._create_rejection(prq, sim_time)
            self._record_user_request_time(sim_time, t0)
            return

        o_pos, t_pu_earliest, t_pu_latest = prq.get_o_stop_info()
        if t_pu_earliest - sim_time > self.opt_horizon:
            self.reservation_module.add_reservation_request(prq, sim_time)
            offer = self.reservation_module.return_immediate_reservation_offer(prq.get_rid_struct(), sim_time)
            LOG.debug(f"reservation offer for rid {rid_struct} : {offer}")
        else:
            list_tuples = insertion_with_heuristics(sim_time, prq, self, force_feasible_assignment=True)
            if len(list_tuples) > 0:
                (vid, vehplan, delta_cfv) = min(list_tuples, key=lambda x:x[2])
                self.tmp_assignment[rid_struct] = vehplan
                offer = self._create_user_offer(prq, sim_time, vehplan)
                LOG.debug(f"new offer for rid {rid_struct} : {offer}")
            else:
                LOG.debug(f"rejection for rid {rid_struct}")
                self._create_rejection(prq, sim_time)
                
        if self.repo and not prq.get_reservation_flag():
            self.repo.register_user_request(prq, sim_time)
                            
        # record cpu time
        self._record_user_request_time(sim_time, t0)

    def user_confirms_booking(self, rid, simulation_time):
        """This method is used to confirm a customer booking. This can trigger some database processes.

        :param rid: request id
        :type rid: int
        :param simulation_time: current simulation time
        :type simulation_time: float
        """
        super().user_confirms_booking(rid, simulation_time)
        LOG.debug(f"user confirms booking {rid} at {simulation_time}")
        prq = self.rq_dict[rid]
        if prq.get_reservation_flag():
            self.reservation_module.user_confirms_booking(rid, simulation_time)
        else:
            new_vehicle_plan = self.tmp_assignment[rid]
            vid = new_vehicle_plan.vid
            veh_obj = self.sim_vehicles[vid]
            self.assign_vehicle_plan(veh_obj, new_vehicle_plan, simulation_time)
            del self.tmp_assignment[rid]

    def user_cancels_request(self, rid, simulation_time):
        """This method is used to confirm a customer cancellation. This can trigger some database processes.

        :param rid: request id
        :type rid: int
        :param simulation_time: current simulation time
        :type simulation_time: float
        """
        LOG.debug(f"user cancels request {rid} at {simulation_time}")
        prq = self.rq_dict[rid]
        if prq.get_reservation_flag():
            self.reservation_module.user_cancels_request(rid, simulation_time)
        else:
            prev_assignment = self.tmp_assignment.get(rid)
            if prev_assignment:
                del self.tmp_assignment[rid]
        del self.rq_dict[rid]

    def acknowledge_boarding(self, rid, vid, simulation_time):
        """This method can trigger some database processes whenever a passenger is starting to board a vehicle.

        :param rid: request id
        :type rid: int
        :param vid: vehicle id
        :type vid: int
        :param simulation_time: current simulation time
        :type simulation_time: float
        """
        LOG.debug(f"acknowledge boarding {rid} in {vid} at {simulation_time}")
        self.rq_dict[rid].set_pickup(vid, simulation_time)

    def acknowledge_alighting(self, rid, vid, simulation_time):
        """This method can trigger some database processes whenever a passenger is finishing to alight a vehicle.

        :param rid: request id
        :type rid: int
        :param vid: vehicle id
        :type vid: int
        :param simulation_time: current simulation time
        :type simulation_time: float
        """
        LOG.debug(f"acknowledge alighting {rid} from {vid} at {simulation_time}")
        del self.rq_dict[rid]
        del self.rid_to_assigned_vid[rid]

    def _prq_from_reservation_to_immediate(self, rid, sim_time):
        """This method is triggered when a reservation request becomes an immediate request.
        All database relevant methods can be triggered from here.

        :param rid: request id
        :param sim_time: current simulation time
        :return: None
        """
        LOG.debug(f"activate {rid} for global optimisation at time {sim_time}!")
        self.rq_dict[rid].set_reservation_flag(False)

    def _call_time_trigger_request_batch(self, simulation_time):
        """This method can be used to perform time-triggered proccesses, e.g. the optimization of the current
        assignments of simulation vehicles of the fleet.

        :param simulation_time: current simulation time
        :type simulation_time: float
        """
        self._flush_mod_destination_gating_records()
        self.sim_time = simulation_time
        self.pos_veh_dict = {}  # pos -> list_veh

    def inform_network_travel_time_update(self, simulation_time: int):
        """Refresh active routes and committed plans after a network TT update.

        Confirmed requests remain assigned to their current vehicles. The
        refreshed plan can become infeasible under the new travel times; in
        that case ``keep_feasible`` preserves its stop sequence rather than
        cancelling or reassigning a customer.
        """
        self.sim_time = simulation_time
        rerouted_vehicles = 0
        refreshed_plans = 0
        infeasible_plans = 0

        for veh_obj in self.sim_vehicles:
            vid = veh_obj.vid
            if veh_obj.assigned_route:
                veh_obj.update_route()
                rerouted_vehicles += 1

            veh_plan = self.veh_plans[vid]
            if not veh_plan.list_plan_stops:
                continue

            is_feasible = veh_plan.update_tt_and_check_plan(
                veh_obj, simulation_time, self.routing_engine, keep_feasible=True
            )
            veh_plan.set_utility(
                self.compute_VehiclePlan_utility(simulation_time, veh_obj, veh_plan)
            )
            refreshed_plans += 1
            if not is_feasible:
                infeasible_plans += 1

        LOG.info(
            "network TT update t=%s: rerouted %s active vehicles, refreshed %s "
            "committed plans, retained %s infeasible plans",
            simulation_time,
            rerouted_vehicles,
            refreshed_plans,
            infeasible_plans,
        )

    def compute_VehiclePlan_utility(self, simulation_time, veh_obj, vehicle_plan):
        """This method computes the utility of a given plan and returns the value.

        :param simulation_time: current simulation time
        :type simulation_time: float
        :param veh_obj: vehicle object
        :type veh_obj: SimulationVehicle
        :param vehicle_plan: vehicle plan in question
        :type vehicle_plan: VehiclePlan
        :return: utility of vehicle plan
        :rtype: float
        """
        return self.vr_ctrl_f(simulation_time, veh_obj, vehicle_plan, self.rq_dict, self.routing_engine)

    def _create_user_offer(self, prq, simulation_time, assigned_vehicle_plan=None, offer_dict_without_plan={}):
        """ creating the offer for a requests

        :param prq: plan request
        :type prq: PlanRequest obj
        :param simulation_time: current simulation time
        :type simulation_time: int
        :param assigned_vehicle_plan: vehicle plan of initial solution to serve this request
        :type assigned_vehicle_plan: VehiclePlan None
        :param offer_dict_without_plan: can be used to create an offer that is not derived from a vehicle plan
                    entries will be used to create/extend offer
        :type offer_dict_without_plan: dict or None
        :return: offer for request
        :rtype: TravellerOffer
        """
        if assigned_vehicle_plan is not None:
            pu_time, do_time = assigned_vehicle_plan.pax_info.get(prq.get_rid_struct())
            # offer = {G_OFFER_WAIT: pu_time - simulation_time, G_OFFER_DRIVE: do_time - pu_time,
            #          G_OFFER_FARE: int(prq.init_direct_td * self.dist_fare + self.base_fare)}
            toll = self._estimate_request_road_toll(simulation_time, prq)
            fare = self._compute_fare(simulation_time, prq, assigned_vehicle_plan) + toll
            offer = TravellerOffer(prq.get_rid_struct(), self.op_id, pu_time - prq.rq_time, do_time - pu_time,
                                   fare,
                                   additional_parameters={
                                       G_OFFER_TOLL: toll
                                   })
            prq.set_service_offered(offer)  # has to be called
        else:
            offer = self._create_rejection(prq, simulation_time)
        return offer

    def change_prq_time_constraints(self, sim_time, rid, new_lpt, new_ept=None):
        """This method should be called when the hard time constraints of a customer should be changed.
        It changes the PlanRequest attributes. Moreover, this method called on child classes should adapt the
        PlanStops of VehiclePlans containing this PlanRequest and recheck feasibility. The VehiclePlan method
        update_prq_hard_constraints() can be used for this purpose.

        :param sim_time: current simulation time
        :param rid: request id
        :param new_lpt: new latest pickup time, None is ignored
        :param new_ept: new earliest pickup time, None is ignored
        :return: None
        """
        LOG.debug("change time constraints for rid {}".format(rid))
        prq = self.rq_dict[rid]
        prq.set_new_pickup_time_constraint(new_lpt, new_earliest_pu_time=new_ept)
        ass_vid = self.rid_to_assigned_vid.get(rid)
        if ass_vid is not None:
            self.veh_plans[ass_vid].update_prq_hard_constraints(self.sim_vehicles[ass_vid], sim_time,
                                                                self.routing_engine, prq, new_lpt, new_ept=new_ept,
                                                                keep_feasible=True)

    def assign_vehicle_plan(self, veh_obj, vehicle_plan, sim_time, force_assign=False, assigned_charging_task=None, add_arg=None):
        super().assign_vehicle_plan(veh_obj, vehicle_plan, sim_time, force_assign=force_assign, assigned_charging_task=assigned_charging_task, add_arg=add_arg)

    def lock_current_vehicle_plan(self, vid):
        super().lock_current_vehicle_plan(vid)

    def _lock_vid_rid_pickup(self, sim_time, vid, rid):
        super()._lock_vid_rid_pickup(sim_time, vid, rid)

