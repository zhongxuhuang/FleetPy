# Development Log

This document records source-level changes, equations, and implementation notes
for files that require quick future reference. Add a new top-level entry for each
additional source file.

## `src/routing/NetworkBasic.py`

### Snapshot metadata

| Field | Value |
| --- | --- |
| Source file | `src/routing/NetworkBasic.py` |
| Baseline | Initial FleetPy version: commit `c52f5006fd6380da6573815c7409bb1ca5c74a3c` (2021-12-07) |
| Compared against | Current working tree, including uncommitted changes |
| Baseline-to-current diff | 675 insertions, 51 deletions |
| Created | 2026-07-14 21:17:14 +02:00 (Europe/Berlin) |
| Last updated | 2026-07-14 21:17:14 +02:00 (Europe/Berlin) |

Line numbers below refer to the source snapshot recorded above. Function names
are the stable references when later edits shift line numbers.

### Baseline-to-current changes

#### Dynamic travel-time inputs, updates, and reset behavior

- `INPUT_PARAMETERS_NetworkBasic` (`src/routing/NetworkBasic.py:40`) documents
  the dynamic-network input option.
- `NetworkBasic.__init__` (`:157`) now loads travel-time update sources, stores
  an active travel-time factor, and preserves the original edge `(tt, distance)`
  values before dynamic updates overwrite edge travel times.
- `_load_tt_folder_path` (`:215`) supports either time-indexed travel-time
  folders or time-indexed scalar travel-time factors from a dynamics file.
- `update_network` (`:254`) applies a scheduled TT update and then refreshes
  zone-based dynamic edge TTs. `reset_network` (`:399`) restores the appropriate
  time-dependent network state for forecasting use.
- `_set_edge_tt` (`:428`) updates the edge object and both node lookup caches;
  it avoids cache resets when the new value is numerically unchanged.
- `get_section_infos` (`:880`) and the one-to-one / one-to-many routing methods
  use the active travel-time factor when one is configured.

#### Zone mapping, MFD speed selection, and dynamic edge TTs

- `_get_zone_to_edge_cache` (`:354`) builds a cache from zone ID to directed
  edges. An edge is assigned through `_get_zone_from_position` (`:830`), using
  the edge origin position `(origin_node, destination_node, 0.0)`.
- `_initialize_zone_fallback_speeds` (`:376`) derives one fixed base-network
  speed per zone when no MFD speed is available.
- `_get_zone_average_speed_from_mfd` (`:751`) resolves the zone speed first from
  a function registered through `set_zone_mfd_function` (`:779`), then from the
  attached ZoneSystem. `_get_zone_queue_speed` (`:771`) falls back to the fixed
  base-network speed.
- `_update_dynamic_edge_travel_times` (`:272`) writes zone-speed-derived TTs to
  every mapped edge and resets routing caches only if a value changed.
- `_get_mfd_section_infos` (`:794`) exposes the same MFD-based calculation for
  a single edge while retaining its base TT when no valid zone speed exists.

#### PV priority-queue bathtub model

- `_get_zone_priority_queue_state` (`:454`) creates a zone state with cumulative
  entries `E`, completions `G`, distance progress `z`, speed `v`, update time,
  and a min-heap of completion thresholds.
- `_register_zone_trip` (`:584`) inserts PV route segments into the relevant
  zone queue; `_advance_zone_priority_queue_state` (`:511`) advances the queue
  to the requested simulation time and transfers completed trips onward.
- `_build_route_zone_segments` (`:639`) converts a route into contiguous
  `(zone_id, distance)` segments. `_schedule_pv_route_trip` (`:658`),
  `_admit_scheduled_pv_route_trips` (`:669`), and `_continue_pv_route_trip`
  (`:679`) schedule route starts and zone-to-zone continuation.
- `assign_route_to_network` (`:913`) changed from a start-time-only route marker
  to this PV queue assignment interface: `(route, start_time, end_time,
  number_vehicles)`. `end_time` remains accepted for compatibility but is not
  used by the queue model.

#### MoD/PV counts and update order

- `update_mod_zone_vehicle_counts` (`:709`) rebuilds the moving-MoD count from
  supplied current vehicle positions; it does not place MoD vehicles in the PV
  priority queue.
- `_set_pv_zone_vehicle_count_from_priority_queue` (`:557`) and
  `_get_zone_priority_queue_active_count` (`:569`) derive the current PV count
  from each queue.
- `_refresh_total_zone_vehicle_counts` (`:735`) combines current PV and MoD
  counts. `_update_current_zone_vehicle_counts` (`:691`) admits due PV trips,
  advances queues, refreshes totals, and then refreshes queue speeds before TT
  updates use the totals.

#### Supporting routing and position changes

- `return_position_coordinates` (`:850`) converts a network position to metric
  coordinates by interpolating between its edge endpoints; longitude/latitude
  helper methods were added alongside it.
- `return_route_infos` (`:892`) accumulates route arrival time and distance from
  the current edge position.
- `move_along_route` (`:1392`) continues to move a vehicle over successive
  edges using the current section TT and distance, with diagnostics for blocked
  edges. Zone-based PV and MoD accounting is handled by the dedicated methods
  documented above.

### Equations and calculation rules

| Rule | Current source reference | Formula / implementation | Purpose |
| --- | --- | --- | --- |
| Travel-time factor | `get_section_infos` (`:880-890`) | `tt_effective = tt_stored * current_tt_factor` | Applies a configured scalar network-wide factor without rewriting stored edge distances. |
| Zone fallback speed | `_initialize_zone_fallback_speeds` (`:376-396`) | `v_fallback,z = Σ_e distance_e / Σ_e base_tt_e` | Supplies a stable distance-weighted base-network speed when zone `z` has no valid MFD speed. |
| Dynamic edge TT | `_update_dynamic_edge_travel_times` (`:272-326`); `_get_mfd_section_infos` (`:794-815`) | `tt_e = distance_e / v_z` | Sets or returns the TT of an edge from its zone speed. Invalid or unavailable speeds leave the base TT unchanged. |
| TT equality tolerance | `_set_edge_tt` (`:428-440`) | `|tt_old - tt_new| <= 1e-9 * max(1, |tt_old|, |tt_new|)` | Avoids an unnecessary edge write and routing-cache reset for floating-point-equivalent TTs. |
| PV queue progress | `_advance_zone_priority_queue_state` (`:511-555`) | `z_t = z_last + (t - t_last) * v` | Advances the shared distance-progress counter of a zone's PV bathtub queue. |
| Active PV count | `_get_zone_priority_queue_active_count` (`:569-571`) | `N_PV,z = max(E_z - G_z, 0)` | Counts PV route segments currently active in a zone queue. |
| Queue-entry projection | `_register_zone_trip` (`:584-635`) | `Δt_projected = max(t_start - t_last, 0)`; `z_projected = z + Δt_projected * v` | Computes queue progress at a PV segment's entry time before it is inserted. |
| PV completion threshold | `_register_zone_trip` (`:584-635`) | `θ = distance_remaining + z_projected`; complete when `θ <= z` | Stores each PV segment's required cumulative progress in a min-heap and determines when it leaves the zone. |
| Total zone vehicles | `_refresh_total_zone_vehicle_counts` (`:735-749`) | `N_z = N_PV,z + N_MoD,z` | Gives the MFD and dynamic-TT update the total current number of vehicles in each zone. |
| Edge-position interpolation | `return_position_coordinates` (`:850-863`) | `coord = rel_pos * coord_destination + (1 - rel_pos) * coord_origin` | Converts an in-edge network position into metric coordinates. |
| Partial-edge movement | `move_along_route` (`:1392-1464`) | `t_next = t_last + (1 - rel_pos) * tt_edge`; `rel_end = (t_end - t_last) / tt_edge + rel_pos` | Advances a vehicle along a partially traversed edge within one simulation time step. |

### MFD ownership and units

The file deliberately does **not** define a concrete MFD equation. The zone
speed is an externally supplied function, conceptually `v_z(N_z)`, registered
in `zone_mfd_functions` or obtained from the attached ZoneSystem through
`get_mfd_average_speed(zone_id, number_vehicles)` (`_get_zone_average_speed_from_mfd`,
`src/routing/NetworkBasic.py:751`). Its return unit must be network-distance
units per second so that `distance / speed` produces seconds.

### Maintenance rule

When a later edit changes the documented source behavior, update this entry's
**Last updated** timestamp, source references, and the relevant change and
equation rows in the same change set. Add separate top-level entries for other
source files rather than mixing their details into this entry.

## `src/demand/TravelerModels.py`

### Snapshot metadata

| Field | Value |
| --- | --- |
| Source file | `src/demand/TravelerModels.py` |
| Baseline | Initial FleetPy version: commit `c52f5006fd6380da6573815c7409bb1ca5c74a3c` (2021-12-07) |
| Compared against | Current working tree, including uncommitted changes |
| Baseline-to-current diff | 169 insertions, 34 deletions |
| Created | 2026-07-14 21:52:16 +02:00 (Europe/Berlin) |
| Last updated | 2026-07-14 21:52:16 +02:00 (Europe/Berlin) |

Line numbers below refer to the source snapshot recorded above. Function names
are the stable references when later edits shift line numbers.

### Baseline-to-current changes

#### Request routing context and toll reporting

- `RequestBase.__init__` (`src/demand/TravelerModels.py:53`) retains the
  routing engine and initializes a request toll to zero.
- `RequestBase.record_data` (`:125`) writes `included_toll` alongside the fare.
- `UserUtilityRequest.choose_offer` (`:678`) and `PTUtilityRequest.choose_offer`
  (`:761`) retain the selected offer's toll in addition to its fare.

#### Multinomial mode-choice inputs and direct-route attributes

- `INPUT_PARAMETERS_MultinomialLogitRequest` (`:793`) replaces the former
  single `value_of_time` input with mandatory `beta_time` and `beta_money`.
  `private_vehicle_full_costs_per_m` is retained as a physical monetary cost
  per distance, rather than a utility coefficient.
- `MultinomialLogitRequest.__init__` (`:812`) treats `NaN` demand values as
  absent, so a valid demand-row coefficient overrides the scenario value but a
  blank cell does not. It also retains the request row and allows precomputed
  direct-route attributes to replace routing-derived values.
- `_apply_precomputed_direct_route_infos` (`:860`) accepts standard direct
  route columns and the aliases `pv_travel_time` / `pv_tt` and
  `pv_travel_distance` / `pv_distance`.
- `_compute_pv_toll` (`:870`) retrieves the current best PV route and calls the
  attached zone system's `get_route_toll_cost` when that interface is present;
  missing zoning, interface, or route produces zero toll.

#### Choice set, utility calculation, and numerical stability

- `_compute_external_mode_utilities` (`:881`) always adds PV and walking, and
  adds bike when a valid bike speed is configured. It validates walking and
  biking speeds before forming travel times.
- `_compute_pt_utility` (`:914`) adds PT when the demand row supplies either a
  direct `pt_utility` or `gtfs_total_duration_min` with a configured PT
  intercept. A direct `pt_utility` remains an explicit precomputed override.
  GTFS duration is converted from minutes to seconds before applying
  `beta_time`; PT fare uses `beta_money`; transfer count remains subject to the
  separate `pt_transfer_penalty`.
- `_compute_mod_offer_utilities` (`:945`) includes every non-declined MOD
  offer. MOD waiting time, driving time, and fare are converted to floats;
  missing or `NaN` fare is treated as zero.
- `_compute_mode_choice_probabilities` (`:961`) implements the multinomial
  logit probabilities using `log_alpha` and subtracts the largest scaled
  utility before exponentiation to prevent numerical overflow.
- `choose_offer` (`:969`) combines external modes, optional PT, and available
  MOD offers into one choice set, samples with the resulting probabilities,
  records the best MOD utility, and stores the selected fare/toll. A selected
  PV alternative records the current route toll; all other external modes have
  zero fare and toll.

#### Mode-choice output

- `_add_record` (`:998`) now exports the utility and probability of every
  considered alternative, serialized as semicolon-separated `mode:value`
  pairs, and exports `highest_mod_utility`.

### Equations and calculation rules

| Rule | Current source reference | Formula / implementation | Purpose |
| --- | --- | --- | --- |
| Walking time and utility | `_compute_external_mode_utilities` (`:897-901`) | `t_walk = d_direct / v_walk`; `U_walk = -beta_time * t_walk` | Applies the common time coefficient to walking. |
| Bike time and utility | `_compute_external_mode_utilities` (`:903-910`) | `t_bike = d_direct / v_bike`; `U_bike = ASC_bike - beta_time * t_bike` | Adds bike only for a valid configured bike speed. |
| PV utility | `_compute_external_mode_utilities` (`:884-895`) | `U_PV = ASC_PV - beta_time * t_direct - beta_money * (cost_per_m * d_direct + toll)` | Combines direct PV time, distance cost, and route toll. |
| PT utility | `_compute_pt_utility` (`:914-943`) | `U_PT = ASC_PT - beta_time * (t_GTFS,min * 60) - beta_money * fare_PT - penalty_transfer * n_transfer` | Evaluates GTFS-based PT with the common time and money coefficients. |
| MOD utility | `_compute_mod_offer_utilities` (`:945-959`) | `U_MOD,o = -beta_time * (t_wait + t_drive) - beta_money * fare_o` | Scores each non-declined MOD operator offer. |
| MNL probability | `_compute_mode_choice_probabilities` (`:961-967`) | `P_i = exp(log_alpha * U_i) / Σ_j exp(log_alpha * U_j)` | Converts all available alternative utilities into choice probabilities. The implementation evaluates an equivalent max-shifted form for stability. |

### Units and maintenance rule

`beta_time` must be expressed per second because routing, walking, biking, and
MOD offer times are in seconds; GTFS minutes are explicitly converted. All
monetary inputs—MOD/PT fares, PV per-distance cost, and toll—must share one
currency unit with `beta_money`. `log_alpha` is a separate Logit scale factor,
not a time or money coefficient.

When a later edit changes the documented source behavior, update this entry's
**Last updated** timestamp, source references, and the relevant change and
equation rows in the same change set.
