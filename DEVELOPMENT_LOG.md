# Development Log

This document records modifications under `src/`, including implementation
notes, equations, interfaces, preprocessing utilities, and source documentation.
Entries are organized by source file and describe the current implementation.
When source behavior changes, update the corresponding entry and equations.
Use function names as stable references.

## `src/FleetSimulationBase.py`

### Network mode and initialization

- The scenario input `network_mode` accepts `static` or `dynamic_mfd` and
  defaults to `dynamic_mfd`. Initialization validates the mode and stores it
  explicitly in the result configuration.
- Static mode rejects an explicitly configured `nw_dynamic_f` and a scheduled
  `mfd_speed` road-pricing tariff.
- After attaching the zone system, initialization calls
  `initialize_static_travel_times` before initializing routing consumers.
  MFD-zone edges receive fixed free-flow travel times; other edges retain
  their loaded base travel times.

### Representative request weight

- Initialization validates `wrq` as a positive finite value with default
  `1.0` and stores the explicit value in the result configuration.
- Selected PV routes and optional background-PV demand are registered through
  `assign_route_to_network` with `self.request_weight`.
- Moving MoD positions are synchronized with the routing engine using the same
  equivalent-vehicle weight. Vehicle objects, assignments, capacity, movement,
  and status updates remain physical and unreplicated.

### Background-demand injection and traffic output

- `_assign_new_background_routes` obtains due background trips from
  `Demand.get_new_background_trips`, computes each OD route, and calls
  `assign_route_to_network(route, trip_time, arrival_time, self.request_weight)`.
  These trips enter the PV priority queues without creating traveler records.
- Initialization, injection, and skipped-route messages use the term
  `background` in the runtime log.
- `record_stats` asks routing engines that provide
  `write_zone_speed_timeseries` to write `zone_speed_timeseries.csv` into the
  active result directory.

## `src/ImmediateDecisionsSimulation.py`, `src/BatchOfferSimulation.py`, `src/BrokerSimulation.py`, and `src/RLBatchOfferSimulation.py`

### Network, traffic, and request-processing sequence

- The simulation environments invoke background-demand injection after the
  network update and before broker processing of new requests.
- The Immediate and Batch environments integrate the network/pricing update
  and fleet-controller notification into their request-processing sequence.
  Moving MoD positions are synchronized before the congestion update; fleet
  controllers receive changed travel times before processing new requests.

## `src/routing/NetworkBasic.py`

### Static and dynamic network modes

- `set_network_mode` validates `static` or `dynamic_mfd`. Static mode rejects
  an explicit dynamics file, clears automatically discovered time-dependent
  TT sources, and removes any active scalar travel-time factor.
- `initialize_static_travel_times` evaluates each configured MFD at zero
  accumulation and sets its zone's directed edges to
  `distance / (v_kmh / 3.6)` once. Zones without an MFD and unmapped edges
  retain their loaded base travel times.
- Static initialization uses the same fixed free-flow speed for the zone's PV
  queue, invalidates routing caches when needed, and invokes the backend
  edge-update hook. It is completed before the first routing query.
- Static `update_network` continues PV queue advancement, moving-MoD and
  exogenous accounting, and traffic snapshots without changing edge travel
  times in response to accumulation.
- Dynamic `update_network` applies a scheduled TT source when present and then
  updates mapped edges from the current MFD speeds.

### Dynamic travel-time inputs, updates, and reset behavior

- `_load_tt_folder_path` supports time-indexed travel-time folders and
  time-indexed scalar travel-time factors from a dynamics file.
- `load_tt_file` applies either edge travel times or the selected scalar
  factor. `reset_network` selects a recorded time-dependent network state for
  forecasting use.
- `_set_edge_tt` updates the edge object and both node lookup caches. It
  avoids writes when the new value is numerically unchanged.
- `get_section_infos` and the routing cost methods use the active travel-time
  factor when configured. An MFD edge update clears that factor before
  installing zone-derived travel times.

### Zone mapping and MFD edge travel times

- `_get_zone_to_edge_cache` groups directed edges by zone using each edge's
  origin position `(origin_node, destination_node, 0.0)`.
- The cache supplies summed directed zone lengths in kilometres through
  `set_mfd_network_lengths` before traffic counts are first refreshed.
- `_get_zone_average_speed_from_mfd` first checks a function registered with
  `set_zone_mfd_function`, then the attached zone system's
  `get_mfd_average_speed` interface.
- `_update_dynamic_edge_travel_times` converts available MFD speeds to edge
  travel times and resets routing caches only when needed. Zones without a
  valid MFD speed retain their loaded edge travel times.
- `_get_mfd_section_infos` exposes the corresponding section calculation.
  `_get_zone_queue_speed` separately selects the speed for the PV queue.
- `_compute_fixed_zone_queue_speeds` derives
  `sum(edge_distance) / sum(edge_tt)` from the initially loaded directed edges
  for zones without an MFD. This fallback advances PV through those zones
  without overwriting individual edge travel times. Static MFD queues instead
  use their initialized free-flow speeds.

### PV priority-queue bathtub model

- `_get_zone_priority_queue_state` stores cumulative weighted entries `E`,
  completions `G`, shared distance progress `z`, speed `v`, update time,
  sampled entry/completion counters, and a min-heap of completion thresholds.
- `_build_route_zone_segments` converts a route into contiguous
  `(zone_id, distance)` segments.
- `_schedule_pv_route_trip`, `_admit_scheduled_pv_route_trips`, and
  `_continue_pv_route_trip` manage route starts and zone-to-zone continuation.
- `_register_zone_trip` inserts each segment into its zone queue;
  `_advance_zone_priority_queue_state` advances shared progress and transfers
  completed segments onward.
- Each route remains one logical descriptor carrying a positive
  equivalent-vehicle weight. Weighted `E/G` counters record equivalent
  vehicles; sampled `E/G` counters retain logical PV trip counts.
- `assign_route_to_network(route, start_time, end_time, number_vehicles)`
  registers a weighted route in these queues. `end_time` is accepted for
  interface compatibility but is not used by the queue model.

### Combined traffic counts and update order

- `update_mod_zone_vehicle_counts` rebuilds moving-MoD counts from current
  vehicle positions. It retains separate physical and weighted counts and
  does not insert MoD vehicles into the PV priority queues.
- `_set_pv_zone_vehicle_count_from_priority_queue`,
  `_get_zone_priority_queue_active_count`, and
  `_get_zone_priority_queue_sampled_active_count` derive weighted and sampled
  PV counts from the queue counters.
- `_update_current_zone_vehicle_counts` admits due PV trips, advances queues,
  refreshes combined totals, and refreshes queue speeds before edge-TT updates.
- `_refresh_total_zone_vehicle_counts` combines weighted PV, weighted moving
  MoD, and interpolated exogenous counts:
  `N_z = N_PV,weighted,z + N_MoD,weighted,z + k_exogenous,z(t) * L_z`.
- `get_current_zone_vehicle_counts` exposes a copy of this combined state.
  `get_current_zone_mfd_speeds` exposes recorded dynamic MFD speeds for
  consumers such as road pricing without recalculating congestion.

### Zone-speed result time series

- `_record_zone_speed_snapshot` records the traffic state of every mapped
  zone after a dynamic edge update or static traffic-state update. A repeated
  update at the same simulation time replaces the earlier snapshot.
- `write_zone_speed_timeseries` exports `simulation_time`, `zone_id`,
  `vehicle_count`, `sampled_pv_vehicle_count`, `pv_vehicle_count`,
  `physical_mod_vehicle_count`, `mod_vehicle_count`,
  `exogenous_vehicle_count`, `avg_speed_mps`, `avg_speed_kmh`, and
  `speed_source`.
- `vehicle_count` is the final equivalent accumulation used by the MFD.
  Dynamic speed sources distinguish `mfd`, `fixed_base_tt`, and
  `unavailable`. A non-MFD zone can therefore report its fixed queue speed;
  a zone without an available speed has blank speed values.
- Static snapshots distinguish `static_mfd_free_flow` from
  `static_base_tt` for fallback zones.

### Supporting routing and position changes

- `return_position_coordinates` interpolates metric coordinates between edge
  endpoints; longitude/latitude helper methods support geographic output.
- `return_route_infos` accumulates route arrival time and distance from the
  current edge position.
- `move_along_route` advances vehicles over successive edges using the current
  section travel time and distance, with diagnostics for blocked edges.

### Equations and calculation rules

| Rule | Source reference | Formula / implementation | Purpose |
| --- | --- | --- | --- |
| Travel-time factor | `get_section_infos` | `tt_effective = tt_stored * current_tt_factor` | Applies a scalar factor without changing edge distances. |
| Static MFD edge TT | `initialize_static_travel_times` | `tt_e = distance_e / (v_free,z / 3.6)` | Fixes MFD-zone edges at free flow. |
| Dynamic edge TT | `_update_dynamic_edge_travel_times`; `_get_mfd_section_infos` | `tt_e = distance_e / speed_z` | Uses the current MFD speed. |
| Non-MFD PV queue speed | `_compute_fixed_zone_queue_speeds` | `speed_z,base = sum(d_e) / sum(tt_e)` | Advances PV while preserving individual edge TTs. |
| TT equality tolerance | `_set_edge_tt` | `abs(tt_old - tt_new) <= 1e-9 * max(1, abs(tt_old), abs(tt_new))` | Avoids redundant writes and cache resets. |
| PV queue progress | `_advance_zone_priority_queue_state` | `z_t = z_last + (t - t_last) * v` | Advances the zone's shared distance progress. |
| Active PV count | `_get_zone_priority_queue_active_count` | `N_PV,z = max(E_z - G_z, 0)` | Counts weighted active PV segments. |
| Queue-entry projection | `_register_zone_trip` | `dt_projected = max(t_start - t_last, 0)`; `z_projected = z + dt_projected * v` | Projects progress to a segment's entry time. |
| PV completion threshold | `_register_zone_trip` | `theta = distance_remaining + z_projected`; complete when `theta <= z` | Determines queue completion from the min-heap. |
| Total zone vehicles | `_refresh_total_zone_vehicle_counts` | `N_z = N_PV,weighted,z + N_MoD,weighted,z + k_exogenous,z(t) * L_z` | Combines all road-traffic contributions. |
| Vehicle-count conversion | `_get_zone_to_edge_cache`; `NetworkZoneSystem.get_mfd_average_speed` | `k_z = N_z / L_z` | Converts accumulation to density in veh/km. |
| Edge-position interpolation | `return_position_coordinates` | `coord = rel_pos * coord_destination + (1 - rel_pos) * coord_origin` | Converts an edge position to metric coordinates. |
| Partial-edge movement | `move_along_route` | `t_next = t_last + (1 - rel_pos) * tt_edge`; `rel_end = (t_end - t_last) / tt_edge + rel_pos` | Advances along a partially traversed edge. |

The routing layer accepts externally supplied MFD functions rather than
defining the concrete parabolic equation. Returned speeds must use network
distance units per second so that `distance / speed` produces seconds.

## `src/routing/NetworkBase.py` and `src/routing/NetworkTTMatrix.py`

### Weighted route-assignment interface

- The `number_vehicles` argument represents a positive equivalent-vehicle
  weight for one logical route.
- Routing backends without traffic-state modeling retain the interface and
  ignore the weight.

## `src/routing/NetworkBasicCpp.py` and `src/fleetctrl/pooling/batch/AlonsoMora/AlonsoMoraParallelization.py`

### Backend and worker initialization

- The C++ routing backend receives changed edge travel times through the
  routing update hook, including the one-time static free-flow initialization.
- Alonso-Mora workers receive the selected `network_mode`, attach the zone
  system, and initialize static travel times before routing consumers use the
  network. Main and worker routing therefore use the same static semantics.

## `src/infra/NetworkZoning.py`

### Data-driven MFD configuration

- `NetworkZoneSystem` requires a valid `mfd_parameters_file` in both
  `static` and `dynamic_mfd` modes. The default is `mfd_parameters.csv`;
  relative paths resolve against the general zone directory and absolute
  paths are accepted.
- The required fields are `zone_id`, `mfd_type`, `v_kmh`, and `gamma`.
  The loader rejects missing or empty files, missing fields, non-finite
  values, non-integer or unknown zone IDs, duplicate zones, non-positive
  coefficients, and unsupported MFD types.
- Only `mfd_type=parabolic` is supported:
  `q(k) = v_kmh * k - gamma * k^2`.
- Routing supplies directed road lengths `L_z` in kilometres. Dynamic speed is
  `max((v_kmh - gamma * (N_z / L_z)) / 3.6, 0.1)` in m/s.
  Static speed is `v_kmh / 3.6`, independent of accumulation and road length.
- `get_mfd_critical_accumulation` derives
  `N_critical,z = [v_kmh / (2 * gamma)] * L_z`.
  Zones without an MFD or assigned network length have no critical
  accumulation. Zones without an MFD retain their existing edge travel times.

### Time-varying fixed exogenous density

- The optional `mfd_exogenous_density_file` resolves relative to the
  network-specific zone directory; absolute paths are accepted.
- Required columns are `simulation_time`, `zone_id`, `normalized_profile`,
  `reference_density_veh_per_km`, `zone_scale`, and
  `exogenous_density_veh_per_km`.
- Validation requires finite values, unique zone/time rows, non-negative times
  and densities, positive scale factors, a normalized profile in `[0, 1]`,
  and coverage of the complete simulation horizon for each supplied zone.
- Dynamic mode requires exactly the configured MFD zones and excludes zones
  without an MFD. Static mode validates supplied zones against the zone system.
- The loader checks
  `exogenous_density_veh_per_km = reference_density_veh_per_km * zone_scale`.
- `get_mfd_exogenous_density` linearly interpolates the fixed curve;
  `get_mfd_exogenous_vehicle_count` multiplies it by directed zone length.
  Omitting the file leaves exogenous density at zero.

### Route-toll interfaces

- `get_route_toll_cost` retains the generic route-based toll interface.
- `get_pv_route_toll_cost` delegates to a PV-specific policy quotation when
  available and otherwise uses the generic route toll. Scheduled PV tariffs
  therefore enter PV mode choice separately from generic MoD toll handling.

## `src/infra/RoadPricing.py`

### Static and myopic zone-distance pricing

- `StaticZoneDistancePricing` applies configured fixed distance coefficients
  by zone.
- `MyopicMFDZoneDistancePricing` reads the active zone system's
  `mfd_parameters` rather than separate `rp_k_critical_dict` or
  `rp_k_critical_file` inputs.
- For the parabolic MFD, `k_critical = v_free / (2 * gamma)` and
  `k_current = N_z / L_z`. The coefficient is
  `min(max_coeff, base_coeff * k_current / k_critical)`.
- Missing MFD parameters, counts, or zone lengths follow `rp_mfd_fallback`.
  The update interval is controlled by `rp_update_interval`.
- Pricing uses the routing engine's combined traffic snapshot.
  `5_road_pricing_info.csv` records the coefficient, `vehicle_count`,
  `density_veh_per_km`, and `critical_density_veh_per_km` for auditing.

### Scheduled PV cordon and distance tariffs

- `ScheduledZoneTariffPricing` implements `scheduled_zone_tariff`.
  Inputs select `rp_charge_type` (`cordon` or `distance`),
  `rp_tariff_basis` (`time_of_day` or `mfd_speed`), and
  `rp_tariff_schedule_file`.
- Required schedule columns are `charge_type`, `tariff_basis`, `zone_id`,
  `time_start`, `time_end`, `speed_min_kmh`, `speed_max_kmh`,
  `speed_band`, `entry_fee_cent`, and `distance_rate_cent_per_m`.
- Time-based tariffs must cover the simulation horizon. MFD-speed tariffs
  require contiguous speed bands from zero to an unbounded final range for
  every MFD zone.
- Cordon charges apply at each transition into a new zone, excluding the
  route's initial zone. Distance charges sum consecutive origin-zone route
  segments using their distances and applicable rates.
- Route quotations use projected segment-entry times and round only the
  complete route total to cents.
- `mfd_speed` selects bands from
  `NetworkBasic.get_current_zone_mfd_speeds()`, using the network's recorded
  state without recalculating a speed for each PV route.
- `rp_tariff_update_interval` defaults to 300 seconds and controls the
  MFD-speed tariff refresh independently of the network update frequency.
  `set_update_interval(seconds)` supports programmatic adjustment.
- Scheduled pricing uses the PV-specific toll interface. Its generic MoD toll
  coefficients are cleared, leaving MoD fare and operating-toll calculations
  separate from the scheduled PV policy.
- `road_pricing_method` values `None`, `none`, `off`, and `disabled`
  disable road pricing.

## `src/demand/TravelerModels.py`

### Request context, weights, and toll reporting

- `RequestBase.__init__` retains the routing engine, initializes the request
  toll to zero, and validates the positive finite scenario `wrq` with default
  `1.0` without duplicating traveler objects.
- `RequestBase.record_data` writes `included_toll` and `rq_weight` with the
  other user statistics.
- `UserUtilityRequest.choose_offer` and `PTUtilityRequest.choose_offer`
  retain the selected offer's toll alongside its fare.

### Multinomial mode-choice inputs and direct-route attributes

- `INPUT_PARAMETERS_MultinomialLogitRequest` replaces the former
  `value_of_time` input with mandatory `beta_time` and `beta_money`.
  `private_vehicle_full_costs_per_m` remains a monetary cost per distance.
- `MultinomialLogitRequest.__init__` retains the demand row and treats
  missing or `NaN` values as absent. Valid request-specific coefficients
  override scenario values.
- `_apply_precomputed_direct_route_infos` accepts the standard direct-route
  fields and aliases `pv_travel_time` / `pv_tt` and
  `pv_travel_distance` / `pv_distance`.
- `_compute_pv_toll` retrieves the current best PV route and prefers
  `get_pv_route_toll_cost`, falling back to `get_route_toll_cost`.
  If zoning, a callable toll interface, or a route is unavailable, the toll
  is zero.
- Optional `private_vehicle_parking_fare` adds a fixed per-PV-trip monetary
  cost and is recorded as `included_park_costs` when PV is selected.
- One global `mode_choice_asc_<mode>` is supported for each of `pv`, `walk`,
  `bike`, `pt`, and `mod`. Missing ASCs default to zero; every ASC may be
  overridden per request. There is no distance-band ASC configuration or
  automatic reference-mode normalization.

### Choice set, utility calculation, and numerical stability

- `_compute_external_mode_utilities` always includes PV and walking and
  includes cycling when a bike speed is supplied. Walking and cycling speeds
  must be positive.
- `_compute_pt_utility` includes PT when the row supplies `pt_utility` or
  `tt_pt`. A direct `pt_utility` overrides the calculated utility.
  `tt_pt` is converted from minutes to seconds; PT fare uses `beta_money`
  and transfers use the separate `pt_transfer_penalty`.
- `_compute_mod_offer_utilities` includes every non-declined MoD offer and
  converts waiting time, driving time, and fare to floats. Missing or `NaN`
  fare is treated as zero.
- `_compute_mode_choice_probabilities` scales utilities with `log_alpha`
  and subtracts their maximum before exponentiation to avoid overflow.
- `choose_offer` samples from the combined choice set, records the highest
  available MoD utility, and retains the selected offer's fare and toll.
  Selected PV records the current route toll; walking, cycling, and PT record
  zero request fare and toll.

### Mode-choice output

- `_get_selected_mode_travel_time` records `selected_mode_travel_time` in
  seconds: PV uses direct-route TT, walking/cycling use direct distance divided
  by speed, PT uses `tt_pt * 60`, and MoD uses offered waiting plus driving time.
  A PT utility override without `tt_pt` has no recorded duration.
- `_add_record` exports utilities and probabilities for every considered
  alternative as semicolon-separated `mode:value` pairs, along with
  `highest_mod_utility`.

### Equations and units

| Rule | Source reference | Formula / implementation |
| --- | --- | --- |
| Global ASC | `_get_mode_choice_asc` | `ASC_m = mode_choice_asc_m`, or `0` when omitted |
| Walking | `_compute_external_mode_utilities` | `t_walk = d_direct / v_walk`; `U_walk = ASC_walk - beta_time * t_walk` |
| Cycling | `_compute_external_mode_utilities` | `t_bike = d_direct / v_bike`; `U_bike = ASC_bike - beta_time * t_bike` |
| PV | `_compute_external_mode_utilities` | `U_PV = ASC_PV - beta_time * t_direct - beta_money * (cost_per_m * d_direct + toll + parking_fare)` |
| PT | `_compute_pt_utility` | `U_PT = ASC_PT - beta_time * (tt_pt_min * 60) - beta_money * fare_PT - transfer_penalty * transfer` |
| MoD | `_compute_mod_offer_utilities` | `U_MOD,o = ASC_MOD - beta_time * (t_wait + t_drive) - beta_money * fare_o` |
| MNL probability | `_compute_mode_choice_probabilities` | `P_i = exp(log_alpha * U_i) / sum_j(exp(log_alpha * U_j))`, evaluated in max-shifted form |

`beta_time` is expressed per second. Routing, walking, cycling, and MoD times
are in seconds; PT minutes are converted explicitly. Monetary inputs,
including MoD/PT fares, PV distance cost, parking, and tolls, share the currency
unit used by `beta_money`. `log_alpha` is a separate Logit scale factor.

## `src/demand/demand.py`

### Optional background-PV demand

- `rq_pv_as_background` defaults to `False`. Valid demand rows, including
  rows marked `rq_pv == 1`, therefore enter the configured traveler model by
  default.
- When the switch is enabled, `Demand.load_demand_file` classifies rows whose
  numeric `rq_pv` equals one as background traffic. These rows do not create
  travelers or enter mode choice. Missing/blank markers, other values, and
  files without the column retain ordinary request behavior.
- Background rows are grouped in `future_background_trips` using their
  simulation-step-rounded `rq_time`. `get_new_background_trips` releases
  them with the same time-step semantics as ordinary requests.
- Classification does not itself scale demand. Network injection applies the
  representative route weight in `FleetSimulationBase`.

## `src/fleetctrl/PoolingIRSOnly.py`

### Dynamic network travel-time updates

- `PoolingInsertionHeuristicOnly.inform_network_travel_time_update` runs
  after the network has applied changed travel times and before new requests
  are processed.
- It first refreshes active vehicle routes through
  `SimulationVehicle.update_route`, then recalculates non-empty committed
  `VehiclePlan` objects using
  `update_tt_and_check_plan(..., keep_feasible=True)` and updates objective
  values.
- Confirmed requests are not reassigned or cancelled by this refresh. For
  infeasible plans, `keep_feasible=True` retains the stop sequence and locks
  the affected prefix according to `VehiclePlan` semantics.
- An INFO message reports rerouted vehicles, refreshed plans, and retained
  infeasible plans for each network update.

### MFD-based destination hard gating

- `op_mod_destination_gating` is operator-specific and disabled by default.
  Enabling it requires `network_mode: dynamic_mfd`, a finite positive
  `op_mod_destination_gating_close_ratio`, and
  `0 <= op_mod_destination_gating_open_ratio < close_ratio`.
- The default gate closes at `N_z / N_critical,z >= 1.0`, remains closed
  through the hysteresis band, and reopens only below `0.9`.
  Critical accumulation comes from
  `NetworkZoneSystem.get_mfd_critical_accumulation`; live accumulation comes
  from the routing engine's weighted PV + weighted MoD + exogenous snapshot.
- The check follows the identical-origin/destination check and precedes
  reservation or insertion processing. Rejection uses the existing rejection
  path without temporary assignment, excluding MoD from that request's MNL
  alternatives.
- Existing trips, empty movement, and repositioning remain unaffected.
  Unmapped destinations and zones without an MFD remain open.
- Enabled decisions are buffered by time step and exported to
  `5-<operator_id>_mod_destination_gating.csv` with destination zone, live and
  critical accumulation, load ratio, before/after state, `allow`/`reject`,
  and transition reason. Disabled operators do not create this output.

## `src/evaluation/standard.py`

### Request-weighted standard evaluation

- Standard evaluation reads `rq_weight` with a backward-compatible default
  of one.
- Representative weights apply to request/passenger totals, modal shares,
  direct-distance totals, user-time sums, fares, tolls, subsidies, and offer
  counts.
- Per-request means use the same weights in numerator and denominator and
  remain unchanged for uniform `wrq`.
- Vehicle kilometres, utilization, fleet size, and other physical operator
  outputs remain unweighted.

## `src/misc/globals.py`

### Configuration and output keys

- Added `wrq`, `mfd_exogenous_density_file`, and user-stat field
  `rq_weight` for representative traffic and demand accounting.
- `G_RQ_SELECTED_MODE_TT` names the `selected_mode_travel_time` result field
  in seconds.
- `G_MC_C_P_PV` names `private_vehicle_parking_fare`.
  `G_MC_ASC_MOD` names `mode_choice_asc_mod`.
- Global ASCs follow `mode_choice_asc_<mode>` for `pv`, `bike`, `pt`,
  `mod`, and `walk`; no distance-based ASC key is used.

## `src/preprocessing/demand/replicate_demand.py`

### Demand replication for real mode-choice events

- The CLI accepts one demand CSV or a demand directory. Default output is a
  sibling `<input_stem>_<copies>.csv` for a file or
  `<input_name>_<copies>` for a directory.
- Directory processing preserves auxiliary files and transforms request CSVs
  containing `rq_time`. `--output-path` selects the output explicitly;
  `--output-dir` remains an alias.
- A row whose numeric `rq_pv` is one remains once. Every other row, including
  missing/blank markers, produces exactly `copies` real request events.
  `--copies 3` therefore means three total events.
- Output is sorted by numeric `rq_time`, preserving original row order for
  ties and adjacency of each row's replicas.
- `request_id` is regenerated contiguously from zero or `--id-start` to
  avoid collisions in request handling and result recording.

## `src/preprocessing/demand/jitter_replicated_demand.py`

### Reproducible request-time jitter

- By default, the CLI reads groups of five identical replicated requests.
  The first copy retains its original `rq_time`; the remaining four receive
  distinct integer delays sampled uniformly without replacement from 60--300
  seconds using default seed `42`, assigned in ascending order.
- Output is globally sorted by `rq_time` and receives contiguous zero-based
  `request_id` values. All other fields remain unchanged.
- Validation checks the input schema, complete five-row groups, identical
  group payloads, and ascending source times. Group size, delay bounds, and
  seed are configurable through CLI arguments.
- A bounded heap performs the global merge without loading all demand into
  memory.
- The output and `.partial` paths are protected against accidental overwrite.
  `--overwrite` replaces a completed output atomically and preserves the
  existing file if generation fails.
- `--validate-only` reproduces and compares the full expected output stream
  without changing either file.

## `src/preprocessing/demand/build_mfd_exogenous_profile.py`

### Fixed MFD exogenous profile generation

- The utility resides under `src/preprocessing/demand/` and resolves default
  input, zone, and network paths from the repository root.
- Its default output is `mfd_exogenous_density_5x_wrq2.csv` beside the script.
  `--output-file` selects another path; replacement requires `--overwrite`.
- It reads `pv_vehicle_count` from a no-MoD baseline
  `zone_speed_timeseries.csv`, validates a complete 30-second grid, applies
  a centered 60-sample (30-minute) rolling mean, and converts counts to density
  using directed source-zone road lengths.
- Each zone is normalized by its smoothed daily peak and sampled at
  five-minute knots from 0 through 86400 seconds.
- Default zone scale factors are
  `{0: 20.0, 1: 24.0, 2: 38, 3: 20.0, 4: 20.0}`.
  Zone 5 is omitted because it has no MFD.
  `--zone-scale ZONE=SCALE` supports per-zone calibration.

## `src/preprocessing/networks/create_node_from_csv.py`

### Node table creation from trip coordinates

- The CLI reads `origin_x`, `origin_y`, `destination_x`, and
  `destination_y` from a trip CSV and writes `node_info.csv` beside the
  script by default.
- Output fields are `node_index`, `is_stop_only`, `source_node_id`,
  `pos_x`, and `pos_y`. Indices start at zero, `is_stop_only=False`,
  and `source_node_id` is blank.
- Origins and destinations are processed in input row order and deduplicated
  together using exactly matching coordinate text, including trailing zeroes.
  Original coordinate strings retain precision and whitespace.
- Comma, semicolon, and tab delimiters are detected. Missing, non-numeric, or
  non-finite coordinates raise row-specific errors.

## `src/preprocessing/networks/remove_point_outsite_zone.py`

### Municipality point filtering

- The CLI retains points within at least one Polygon or MultiPolygon feature
  of a zone GeoJSON, including exterior boundaries.
- Output is written atomically and existing destinations require
  `--overwrite`.
- Default input/output names are `node_Oberbayern.geojson` and
  `node_upperbavaria_inside_munich_municipalities.geojson`; the default zone
  boundary is the Munich municipality polygon definition.
- Default membership uses `pos_x` and `pos_y` in EPSG:32632 because the
  supplied node geometry is EPSG:3857 and the zone polygons are EPSG:32632.
  `--geometry-coordinates` supports inputs whose geometry CRS matches.
- The implementation uses the Python standard library, validates
  FeatureCollections and coordinates, supports polygon holes, and preserves
  retained feature attributes and the point collection's original CRS.

## `src/preprocessing/networks/find_rq_inside_munich.py`

### Munich request filtering and optional endpoint-node output

- The CLI retains trips only when both origin and destination coordinate pairs
  match reference-node coordinates.
- Matching uses validated `Decimal` coordinates and a spatial grid with
  default absolute x/y tolerance `0.000001` metres, configurable through
  `--coordinate-tolerance-m`.
- Current defaults are `node_info_muechen.csv` for reference nodes and
  `rq_muechen.csv` for output.
- `euclidean_distance` is required; trips with validated numeric distance
  exactly zero are excluded.
- Output preserves every input trip field, prepends regenerated
  `request_id` and `rq_time` from `departure_time`, sorts by numeric
  departure time with stable ties, and assigns contiguous IDs from zero.
- Comma, tab, and semicolon delimiters are detected. Existing outputs require
  `--overwrite`.
- `--node-trip-file` optionally writes deduplicated retained endpoint
  coordinates in sorted request order, with fields `node_index`,
  `is_stop_only=False`, blank `source_node_id`, `pos_x`, and `pos_y`.
  Its path must differ from `--output-file`. A trip-only run creates no
  endpoint-node CSV.

## `src/preprocessing/networks/add_nearest_aimsum_node.py`

### AIMSUM nearest-node annotation

- The CLI copies `node_trip_nonzero_euclidean.csv` to
  `node_trip_nonzero_euclidean_with_aimsum_index.csv` by default, preserving
  existing fields and appending `aimsum_pos_x`, `aimsum_pos_y`, and
  `aimsum_node_index`.
- Values come from the closest `(pos_x, pos_y)` row of `node_AIMSUM.csv`
  using Euclidean distance in projected-coordinate units.
- A balanced two-dimensional k-d tree supports exact nearest-neighbor lookup;
  equal distances select the first reference-node row.
- Required fields and finite coordinates are validated. Input delimiters are
  detected among comma, tab, and semicolon.
- Output is atomic and existing destinations require `--overwrite`.

## `src/preprocessing/networks/create_aimsum_gtfs_demand.py`

### AIMSUM request conversion with rail-GTFS attributes

- The CLI streams `rq_muechen_nonzero_euclidean.csv`, maps exact origin and
  destination coordinates through
  `node_trip_nonzero_euclidean_with_aimsum_index.csv`, and writes
  `rq_muechen_nonzero_euclidean_aimsum.csv`.
- Output fields are `request_id`, `rq_time`, `start`, `end`,
  `gtfs_total_duration_min`, and `nr_transfers`. IDs are regenerated from
  zero in source-row order. Unavailable rail routes have blank GTFS fields.
- The converter reuses the rail-GTFS preprocessor, selecting active services
  for the configured service date and Tram/S-Bahn, U-Bahn, and Regionalbahn
  routes. Defaults include a 1000 m access/egress radius, 1.4 m/s walking speed,
  and a 120 s transfer buffer.
- Mappings and coordinates are validated. Requests are processed sequentially
  without loading the complete source into memory, and protected output is
  written atomically.

## `src/preprocessing/pubtrans/add_rail_gtfs_to_demand.py`

### PT demand-column schema and accelerated routing

- `RailGTFSODTravelTimePreprocessor.augment_demand` writes `tt_pt` in
  minutes and `transfer` as the transfer count, matching the MNL input schema.
- `RailGTFSODTravelTimePreprocessor.compute_request` uses an optional
  Numba-compiled connection-scan implementation when Numba is available.
  Otherwise it retains the Python implementation.
- Both paths preserve earliest-arrival selection and prefer fewer transfers
  for equal arrival times.

## `src/preprocessing/zones/add_zone_info_to_nodes_geojson.py`

### Zone attributes for node inspection

- The CLI joins `node_zone_info.csv` to `nodes_all_infos.geojson` by
  `node_index`, retaining geometry and existing attributes while adding
  integer `zone_id` and `is_centroid` fields.
- It validates required fields and a zone assignment for every input node
  before writing output.
- The primary zone uses the first CSV row, matching `ZoneSystem`.
  Boundary nodes with multiple zone rows additionally receive a
  semicolon-separated `zone_id_candidates` field.

## `src/preprocessing/zones/build_zone_tariff_schedule.py`

### Tariff schedule generation

- The CLI converts active MFD parameters into a fixed table of tariff ranges
  for time-of-day and MFD-speed pricing.
- An optional untolled-route distance summary with
  `zone_id,mean_in_zone_distance_m` converts distance rates into comparable
  cordon fees.
- Generated MFD-speed rows describe ranges rather than a reference speed
  trajectory. Runtime pricing selects a range using the routing layer's
  existing MFD speed snapshot.

## `src/README.md`

### Source-change overview

- The English overview describes runtime source changes relative to the
  branch's upstream base and intentionally excludes preprocessing utilities.
- It covers runtime update order, static/dynamic MFD modes, zonal traffic,
  representative weights, multimodal choice, road pricing, destination
  gating, evaluation outputs, configuration interfaces, compatibility, and
  responsibilities of the modified source areas.
- Static-mode documentation describes the fixed MFD free-flow reference and
  fallback base-edge travel times.

## `src/DEVELOPMENT_LOG.md`

### Development-log consolidation

- The separate source-directory log was removed after its `NetworkBasic.py`
  and `TravelerModels.py` entries were consolidated into the repository-level
  `DEVELOPMENT_LOG.md`.

## `src/` comments and docstrings

### FleetPy-local documentation style

- Comments and docstrings in the modified Python source files, including
  preprocessing utilities, were normalized to their module-local conventions.
- FleetPy methods use the `This method ...` form; standalone preprocessing
  helpers use `This script/function/class ...`.
- Section separators and ordinary block comments follow neighboring code.
  Tool directives such as `# noqa: E402` are preserved.
- These documentation edits do not change executable statements.
