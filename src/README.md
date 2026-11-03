# MFD and Policy Extensions in `src`

This README summarizes the source-level changes implemented on the
`dynamic_mfd_network` branch relative to its `upstream/main` base. It describes
the runtime model and interfaces under `src/`; changes under `src/preprocessing/`
are intentionally excluded.

The branch extends FleetPy from a fleet-focused simulation with externally
provided network travel times to a multimodal policy simulation in which
private vehicles (PV) and Mobility-on-Demand (MoD) vehicles share a zonal,
congestion-sensitive road state.

## Runtime sequence

The extended simulation step follows this order:

1. Advance the physical MoD vehicle states.
2. Synchronize moving MoD positions with the routing engine.
3. Admit and advance scheduled PV trips in the zonal priority queues.
4. Add the configured exogenous traffic and calculate total zonal accumulation.
5. Update MFD-based edge travel times in dynamic mode, or retain the fixed
   free-flow/fallback travel times initialized in static mode.
6. Refresh the active road-pricing state where the simulation environment
   invokes a pricing update.
7. Notify fleet controllers when network travel times changed.
8. Inject optional background-PV demand and process new traveller requests.

## Network and routing changes

### Static and dynamic network modes

`FleetSimulationBase` and `NetworkBasic` support two explicit network modes:

| `network_mode` | Edge travel times | Traffic-state accounting |
| --- | --- | --- |
| `dynamic_mfd` | Recalculated from the current zonal MFD speed | PV, moving MoD, and exogenous traffic are counted |
| `static` | Fixed MFD zero-density travel times; base edge TT for zones without an MFD and unmapped edges | The same zonal counts and audit output remain available |

`dynamic_mfd` is the default. Static mode rejects an explicitly configured
`nw_dynamic_f` and cannot be combined with a scheduled `mfd_speed` tariff.
It also clears automatically discovered time-dependent travel-time sources so
that no later update changes the initialized travel times. Both modes require
a valid `mfd_parameters_file` (default `mfd_parameters.csv`), resolved relative
to the general zone directory. Missing or invalid files fail at startup.

Before any routing consumer is initialized, static mode sets each MFD-zone
edge to `distance / (v_kmh / 3.6)`, synchronizes the C++ router and routing
caches, and fixes its PV queue speed to the same free-flow value. Main and
Alonso-Mora worker processes apply the same initialization. Vehicle counts
and exogenous density never reduce static speeds. The speed audit records
`static_mfd_free_flow` for these zones and `static_base_tt` for fallback zones.
Older static results used base TT everywhere and are not equivalent to this
new baseline; existing result files are not migrated.

### Zonal MFD calculation

`NetworkZoneSystem` loads one parabolic MFD per configured zone from
`mfd_parameters_file`. For a zone with free-flow parameter `v`, curvature
`gamma`, directed network length `L_z`, and accumulation `N_z`, the implemented
relations are:

- `k_z = N_z / L_z`
- `q_z(k_z) = v * k_z - gamma * k_z^2`
- `speed_z = max((v - gamma * k_z) / 3.6, 0.1)` in m/s
- `k_critical,z = v / (2 * gamma)`
- `N_critical,z = k_critical,z * L_z`

Each directed edge is assigned to the zone of its origin node. The sum of
these directed edge lengths is passed back to `NetworkZoneSystem` in
kilometres. An edge in a zone without a valid MFD keeps its loaded travel time.

### Combined traffic accumulation

The MFD receives the following equivalent vehicle count:

`N_z = N_PV,weighted,z + N_MoD,weighted,z + N_exogenous,z`

- PV routes are divided into consecutive zonal segments and placed in a
  zone-level priority-queue bathtub model.
- Each PV route remains one logical descriptor but can carry a positive
  equivalent-vehicle weight.
- Moving MoD vehicles are counted from their current positions. Physical and
  weighted MoD counts are stored separately.
- Optional exogenous density is linearly interpolated in time and multiplied
  by the directed zonal network length.
- Zones without an MFD use a fixed base-travel-time-equivalent speed only to
  advance their PV queue; this speed does not overwrite their edge travel times.

The PV queues track cumulative entries, cumulative completions, shared
distance progress, current speed, and per-trip completion thresholds. When a
zonal segment completes, its route descriptor continues in the next zone.

### Routing backend integration

`NetworkBase` and `NetworkTTMatrix` retain the weighted route-assignment
interface even when a backend does not model congestion. `NetworkBasicCpp`
forwards changed edge travel times to the C++ router, and the Alonso-Mora
parallel routing process receives the selected network mode.

## Demand and multimodal choice changes

### Representative request weight

The scenario parameter `wrq` is validated as a positive finite number and
defaults to `1.0`. It is not used to create duplicate traveller or vehicle
objects. Instead, it represents the traffic and population weight of one
logical request:

- selected PV routes enter the network with this weight;
- moving MoD vehicles contribute this weight to MFD accumulation;
- every request writes the value as `rq_weight` in the user output; and
- request-side evaluation totals and means use this weight.

Physical fleet size, vehicle movement, assignment, capacity, utilization, and
vehicle-kilometre calculations remain unweighted.

### Optional background-PV rows

`Demand` can classify rows with `rq_pv == 1` as fixed background traffic when
`rq_pv_as_background` is enabled. These rows are scheduled directly on the
network and do not create traveller objects or enter mode choice. The default
is disabled, so all valid demand rows continue through the configured traveller
model unless the scenario opts in.

### Multinomial mode choice

`MultinomialLogitRequest` adds one joint choice set containing available MoD
offers and the configured external modes PV, walking, cycling, and public
transport. The implementation adds:

- separate `beta_time` and `beta_money` coefficients;
- one global `mode_choice_asc_<mode>` for each mode;
- optional per-request coefficient overrides from the demand row;
- PV distance cost, road toll, and fixed parking cost;
- walking and cycling times derived from direct distance and configured speed;
- PT time, fare, and transfer inputs when present; and
- max-shifted Logit probabilities for numerical stability.

The core utilities are:

- `U_PV = ASC_PV - beta_time * t_PV - beta_money * (c_distance * d + toll + parking)`
- `U_walk = ASC_walk - beta_time * t_walk`
- `U_bike = ASC_bike - beta_time * t_bike`
- `U_PT = ASC_PT - beta_time * t_PT - beta_money * fare_PT - transfer_penalty * transfers`
- `U_MoD = ASC_MoD - beta_time * (t_wait + t_drive) - beta_money * fare_MoD`

`log_alpha` scales all utilities before probability calculation. PT travel time
from `tt_pt` is converted from minutes to seconds; the other travel-time inputs
are already in seconds.

`BasicRequestDeclineToPV` provides a deterministic PV fallback when all MoD
operators decline. `PTUtilityRequest` provides the earlier maximum-utility
PT-versus-MoD alternative. The request-module registry exposes all three new
request types.

## Road-pricing changes

`src/infra/RoadPricing.py` introduces three policy implementations:

| Policy selector | Behavior |
| --- | --- |
| `static` / `StaticZoneDistancePricing` | Applies fixed distance coefficients by zone |
| `myopic_mfd` / `MyopicMFDZoneDistancePricing` | Updates each coefficient from the current density-to-critical-density ratio |
| `scheduled_zone_tariff` / `ScheduledZoneTariffPricing` | Reads fixed cordon or distance tariffs indexed by time of day or the current MFD speed band |

The myopic coefficient is
`min(base_coefficient * k_z / k_critical,z, maximum_coefficient)`. Missing
state follows the configured `rp_mfd_fallback` behavior.

Scheduled tariffs support `cordon` and `distance` charges with either
`time_of_day` or `mfd_speed` as their basis. Time schedules must cover the
simulation horizon; speed bands must be contiguous and finish with an
unbounded final band. Cordon charges apply when a route enters a new zone,
while distance charges apply to each consecutive origin-zone route segment.
The route is evaluated at projected segment-entry times and the complete toll
is rounded once to cents.

The scheduled policy uses the PV-specific quotation interface and clears the
generic MoD toll coefficients. It is therefore PV-only. Static and myopic
coefficients use the generic zonal toll interface; supported fleet controllers
can include those route tolls in MoD offers while retaining the toll as a
separate offer and user-output component.

`road_pricing_method` values `None`, `none`, `off`, and `disabled` leave road
pricing inactive.

## MFD-based MoD destination gating

`PoolingInsertionHeuristicOnly` can reject a new request when its destination
zone is already congested. The feature is operator-specific, disabled by
default, and requires `network_mode: dynamic_mfd`.

For load ratio `r_z = N_z / N_critical,z`, the gate uses hysteresis:

- an open zone closes when `r_z >= op_mod_destination_gating_close_ratio`;
- a closed zone remains closed inside the hysteresis band; and
- it reopens only when `r_z < op_mod_destination_gating_open_ratio`.

The opening ratio must satisfy
`0 <= open_ratio < close_ratio`; the closing ratio must be finite and positive.
The gate is checked before insertion. Rejected requests use the existing
rejection path and receive no temporary vehicle assignment or MoD alternative.
Existing trips, empty vehicle movement, and repositioning are not gated.
Unmapped destinations and zones without a valid MFD critical accumulation
remain open.

When dynamic network travel times change, the same controller also refreshes
active vehicle routes and recalculates committed vehicle plans with
`keep_feasible=True`. Confirmed requests are not reassigned or cancelled by
this refresh.

## Evaluation and output changes

| Output | Added information |
| --- | --- |
| `00_config.json` | Explicit `network_mode` and validated `wrq` |
| `1_user-stats.csv` | `rq_weight`, toll, parking cost, selected-mode travel time, mode-choice utilities/probabilities, and highest MoD utility where applicable |
| `zone_speed_timeseries.csv` | Total accumulation; sampled and weighted PV; physical and weighted moving MoD; exogenous count; speed in m/s and km/h; and speed source |
| `5_road_pricing_info.csv` | Active tariff or coefficient, traffic state, critical density, and fallback information |
| `5-<operator_id>_mod_destination_gating.csv` | Destination-zone load, gate state transition, allow/reject decision, and reason |

`standard_evaluation` defaults historical outputs without `rq_weight` to one.
Representative weights are applied to request/passenger counts, mode shares,
user-time and distance sums, payments, subsidies, and offer counts. The same
weights are used in both numerator and denominator of request-level means.
Physical operator and vehicle metrics remain unweighted.

## Main configuration keys

| Area | Keys |
| --- | --- |
| Network | `network_mode`, `nw_dynamic_f`, `mfd_parameters_file`, `mfd_exogenous_density_file` |
| Demand scaling | `wrq`, `rq_pv_as_background` |
| Mode choice | `beta_time`, `beta_money`, `log_alpha`, `mode_choice_asc_pv`, `mode_choice_asc_walk`, `mode_choice_asc_bike`, `mode_choice_asc_pt`, `mode_choice_asc_mod`, `private_vehicle_full_costs_per_m`, `private_vehicle_parking_fare`, `walking_speed`, `biking_speed` |
| Road pricing | `road_pricing_method`, `rp_static_toll_coeff_dict`, `rp_static_toll_coeff_file`, `rp_base_toll_coeff`, `rp_max_toll_coeff`, `rp_update_interval`, `rp_mfd_fallback`, `rp_charge_type`, `rp_tariff_basis`, `rp_tariff_schedule_file`, `rp_tariff_update_interval` |
| Destination gating | `op_mod_destination_gating`, `op_mod_destination_gating_close_ratio`, `op_mod_destination_gating_open_ratio` |

## Modified source areas

| Source area | Main responsibility of the change |
| --- | --- |
| `FleetSimulationBase.py` | Input validation, policy initialization, weighted traffic synchronization, background-PV routing, mode-decision handling, and traffic output |
| `BatchOfferSimulation.py`, `ImmediateDecisionsSimulation.py` | Network, pricing, controller-refresh, background-demand, and request-processing order |
| `BrokerSimulation.py`, `RLBatchOfferSimulation.py` | Background-demand injection in the normal network/request update sequence |
| `demand/TravelerModels.py`, `demand/demand.py` | Multimodal choice, request weights and costs, selected-mode output, and optional background traffic |
| `infra/NetworkZoning.py`, `infra/Zoning.py` | MFD loading, density/speed/critical-state calculation, node-zone lookup, and policy interfaces |
| `infra/RoadPricing.py` | Static, myopic MFD-responsive, and scheduled PV tariff policies |
| `routing/NetworkBase.py`, `routing/NetworkBasic.py`, `routing/NetworkBasicCpp.py`, `routing/NetworkTTMatrix.py` | Weighted route registration, zonal PV queues, MFD edge travel times, static mode, C++ updates, and traffic snapshots |
| `fleetctrl/FleetControlBase.py` and supported fleet controllers | Toll-aware offers and network-travel-time refresh integration |
| `fleetctrl/PoolingIRSOnly.py` | Destination gating, decision audit output, and committed-plan refresh |
| `fleetctrl/pooling/batch/AlonsoMora/AlonsoMoraParallelization.py` | Propagation of the configured network mode to parallel routing |
| `evaluation/standard.py` | Request-weighted user evaluation while preserving physical fleet metrics |
| `misc/globals.py`, `misc/init_modules.py` | Configuration/output constants and request-module registration |

Detailed implementation history and validation notes remain in the repository
`DEVELOPMENT_LOG.md` and `src/DEVELOPMENT_LOG.md`.
