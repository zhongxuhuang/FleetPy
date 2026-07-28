# Development Log

This document records source-level changes, equations, and implementation notes
for files that require quick future reference. Add a new top-level entry for each
additional source file.

## Local GitHub credential recovery

- Updated 2026-07-27 +02:00 (Europe/Berlin).
- Removed the stale Windows Git Credential Manager record for `https://github.com`
  after GitHub rejected a push with `Invalid username or token`. Repository source,
  Git remotes, and commit history were not changed. The next GitHub operation that
  requires credentials must complete a fresh browser sign-in with write access to
  `zhongxuhuang/FleetPy`.

## `src/preprocessing/zones/add_zone_info_to_nodes_geojson.py`

### Zone attributes for QGIS node inspection

- Added 2026-07-27 +02:00 (Europe/Berlin).
- The command-line utility joins `node_zone_info.csv` to a network's
  `nodes_all_infos.geojson` by `node_index`, preserving geometry and all
  existing attributes while adding integer `zone_id` and `is_centroid` fields.
- It validates the required CSV fields and a zone assignment for every input
  node before writing the GeoJSON. The primary `zone_id` retains the first CSV
  row, matching `ZoneSystem`; nodes with multiple zone rows also receive a
  semicolon-delimited `zone_id_candidates` field for boundary review.

## `studies/mt/analysis_common.py` and the four single-scenario analysis scripts

### Demand, MFD, MoD-operation, and user-welfare result figures

- Added 2026-07-23 +02:00 (Europe/Berlin).
- `plot_demand_mode_metrics.py`, `plot_mfd_traffic_metrics.py`,
  `plot_mod_operations_metrics.py`, and `plot_user_welfare_metrics.py` each
  accept one FleetPy result directory plus optional `--output-dir`; the demand,
  MoD, and welfare scripts additionally accept `--time-bin-min` (default 15).
  They write independent PNG and same-named CSV outputs below
  `<result>/analysis/<topic>/` by default.
- Every script emits `availability.csv`. Each metric is marked `direct`,
  `derived`, or `unavailable` with the source file or missing-data reason, so a
  missing optional simulation export does not cause a partial analysis to fail.
- `analysis_common.py` maps choice IDs to `PV`, `MOD`, `WALK`, `BIKE`, and `PT`;
  it maps request positions to zones using the first `node_zone_info.csv` row
  for ambiguous boundary nodes, matching `ZoneSystem.node_zone_lookup`.
- The MFD script computes production as `P_z = N_z * v_z` in veh-km/h and
  identifies congestion where `v_z <= v_free,z / 2`. It only reports critical
  accumulation when the configured zone mapping and network edges can be
  resolved; gross zone inflow/outflow and actual PV VKT/VHT remain explicitly
  unavailable without dedicated simulation logs.
- The welfare script reconstructs selected non-preference generalized cost as
  `(ASC_selected - U_selected) / beta_money` from the recorded MNL utility and
  the configured global ASC for the selected mode. Its MNL inclusive value uses a max-shifted
  log-sum-exp calculation. Equity plots are limited to OD zone, distance, and
  pricing-zone exposure; missing income and trip-purpose fields are reported as
  unavailable rather than inferred.

## `src/infra/RoadPricing.py`, `src/routing/NetworkBasic.py`, and `studies/mt/scenarios/const_cfg_mt.yaml`

### Munich road pricing and MFD-density-responsive tolls

- Added 2026-07-23 +02:00 (Europe/Berlin).
- Updated 2026-07-23 +02:00 (Europe/Berlin). `const_cfg_mt.yaml` now enables
  `myopic_mfd` road pricing: it updates every 300 seconds with a `0.05` base
  and `0.20` cap (euro cents per metre) for reservoir zones 0--3, a `0.10`
  base and `0.25` cap for zone 4, and zero for zone 5 (Outside Reservoirs).
  The static alternative is retained commented: zones 0--3 use `0.05`, zone 4
  uses `0.10`, and zone 5 uses zero.
- `MyopicMFDZoneDistancePricing` now reads the selected zone system's existing
  `mfd_parameters.csv` through `NetworkZoneSystem.mfd_parameters`; it no
  longer requires or reads `rp_k_critical_dict` / `rp_k_critical_file`.
- Updated 2026-07-25 +02:00 (Europe/Berlin). `NetworkBasic`
  `get_current_zone_vehicle_counts()` exposes a read-only copy of the total
  zone vehicle count already used by the MFD: background and selected PV route
  segments plus moving MoD vehicles.
- Updated 2026-07-27 +02:00 (Europe/Berlin). Zones without an MFD now retain
  their original edge TTs and additionally receive one fixed PV-priority-queue
  speed. The speed is calculated once, after the t=0 edge TTs are loaded, as
  `sum(edge_distance) / sum(edge_tt)` over that zone's directed edges. Thus
  background and selected PV can traverse zone 5 rather than remaining in a
  zero-speed queue; the fixed queue speed never overwrites an edge TT. For
  `Aimsun_Munich_2020` with `Munich_reservoirs`, zone 5 is 15.65561 m/s
  (56.36 km/h).
- Updated 2026-07-28 +02:00 (Europe/Berlin). Demand files can use the optional
  scenario parameter `rq_pv_as_background` (default `False`). By default, rows
  marked `rq_pv=1` remain regular requests and enter the configured mode
  choice; setting it to `True` restores fixed-background-PV behavior.
  `scenario_cfg_mt_all_mnl.csv` explicitly keeps the all-MNL MT trial. This
  switch changes classification only and does not apply demand scaling.
- `zone_speed_timeseries.csv` now additionally records
  `pv_vehicle_count`, `mod_vehicle_count`, and `speed_source`. A non-MFD zone
  using this queue fallback has `speed_source=fixed_base_tt`; MFD zones retain
  `speed_source=mfd`.
- Added `studies/mt/scenarios/scenario_cfg_mt_fixed_zone5.csv` for a separate
  all-PV validation run whose output directory is
  `mt_d1000_00_24_all_pv_fixed_zone5`.
- For `q(k) = v_free*k - gamma*k^2`, the maximum-flow critical density is
  `k_critical = v_free / (2 * gamma)`. Myopic coefficients use
  `min(max_coeff, base_coeff * k_current / k_critical)`, where
  `k_current = N_z / L_z` in veh/km. Thus the coefficient is below the base at
  low density, equals it at critical density, and rises linearly after the MFD
  maximum-flow point until capped. Missing parameters, vehicle counts, or zone
  lengths follow `rp_mfd_fallback` (`zero` in the Munich alternative).
- `5_road_pricing_info.csv` records `vehicle_count`,
  `density_veh_per_km`, and `critical_density_veh_per_km` for the density
  calculation.

## `studies/mt/plot_choice_distribution.py`

### Generic choice-distribution histogram

- Updated 2026-07-25 +02:00 (Europe/Berlin). MNL user statistics now include
  `selected_mode_travel_time` in seconds: PV uses the direct road TT; WALK and
  BIKE use direct-route distance divided by their configured speeds; PT uses
  demand `tt_pt` converted from minutes; and MOD uses the selected offer's
  `t_wait + t_drive`. A PT choice supplied only as `pt_utility` has no
  interpretable duration and is left blank. The choice-distribution script now
  summarises this selected-mode field by default; historical results must
  explicitly select `--tt-column direct_route_travel_time` to report the legacy
  road TT.
- Updated 2026-07-25 +02:00 (Europe/Berlin). The overall choice summary and
  histogram labels now report each mode's selected count and its percentage of
  all non-empty choices.
- Updated 2026-07-21 +02:00 (Europe/Berlin).
- Absorbed and replaced `studies/mt/analyze_choice_trip_metrics.py`, which was
  removed. The unified command now prints selected count plus average
  `direct_route_distance` in km and `direct_route_travel_time` in min for every
  choice. Its `--choice-column` alias, `--tt-column`, `--no-trip-metrics`, and
  optional `--trip-metrics-output` preserve the former metric-analysis options.
- In addition to the overall choice histogram, the script now analyses FleetPy
  direct-route distance by default.  It uses the nine non-overlapping kilometre
  bands `<=0.5`, `(0.5,1]`, `(1,2]`, `(2,5]`, `(5,10]`, `(10,20]`,
  `(20,50]`, `(50,100]`, and `>100`.
- By default it prints the results and writes a CSV containing precise RQ
  counts, all-RQ percentages, per-mode counts, and per-mode shares.  The
  distance input is in metres and defaults to `direct_route_distance`;
  `--no-distance-analysis` preserves the former overall-count-only behavior.
- `--plot` additionally writes the original overall histogram, the stacked
  choice-count and all-RQ distance-share charts, and a 100-percent stacked
  mode-share chart for every distance band. The CSV includes a
  `<mode>_share_within_band_percent` column for every mode; its denominator is
  RQs in that band with a non-empty choice, so mode shares within a populated
  band sum to 100%.
- Added 2026-07-18 +02:00 (Europe/Berlin).
- The command-line script reads any CSV choice column (default:
  `chosen_operator_id`), prints per-choice counts and their total, and writes a
  labelled PNG bar chart whose title also shows the total.
- By default it maps FleetPy choice IDs to `PV`, `MOD`, `WALK`, `BIKE`, `PT`,
  and `OTHER`. Use `--raw-labels` for an arbitrary non-FleetPy choice column.

## `studies/mt/scenarios/const_cfg_mt.yaml`

### Munich mode-choice scenario initialisation

- Updated 2026-07-24 +02:00 (Europe/Berlin).
- Updated 2026-07-28 +02:00 (Europe/Berlin). The aggregate mode shares from
  104,408 choices (PV 29.47%, MOD 18.60%, WALK 25.14%, BIKE 7.83%, PT 18.96%) were
  recalibrated toward the German reference shares (40%, 13%, 26%, 11%, 11%).
  One 90%-damped log-share update, using WALK as the fixed reference because it
  was already 96.7% of its target, sets the global ASCs to PV `169.3`, WALK
  `745.6`, BIKE `-373.0`, PT `-265.6`, and MOD `1077.1`.
- Updated 2026-07-28 +02:00 (Europe/Berlin). The second run produced PV
  32.76%, MOD 15.19%, WALK 26.83%, BIKE 9.22%, and PT 16.01%. A second
  90%-damped log-share update retains WALK as the fixed reference (103.2% of
  target) and sets the global ASCs to PV `190.1`, WALK `745.6`, BIKE `-354.3`,
  PT `-296.5`, and MOD `1065.9`.
- Mode choice now uses exactly one global `mode_choice_asc_<mode>` value for
  each of `pv`, `walk`, `bike`, `pt`, and `mod`; there is no distance-band ASC
  configuration. Missing values have a zero intercept, and every global ASC,
  including PT, may be overridden per demand row.
- The pre-2026-07-28 Munich configuration used PV `144.8`, WALK `745.6`, BIKE
  `-400.6`, PT `-213.6`, and MOD `1112.4`; it was derived from a
  request-weighted former ninth-calibration table over the 104,408 requests in
  `mt_d1000_00_24_9_base`.
- `private_vehicle_parking_fare: 350` adds a fixed €3.50 (350-cent) destination
  parking cost to the PV utility and records it as `included_park_costs` when
  PV is selected.

### PT demand-column schema

- Updated 2026-07-17 +02:00 (Europe/Berlin).
- `tt_pt` is the PT travel time in minutes and `transfer` is its transfer
  count, matching the Munich demand files and the MNL implementation.
- `pt_transfer_penalty` remains a separate utility penalty per transfer and is
  configured independently from `tt_pt`.
- `pt_base_fare` is `200` monetary units (cents), representing a €2.00 PT
  fare. It uses the same monetary unit as MOD fares and `beta_money`.

## `src/preprocessing/pubtrans/add_rail_gtfs_to_demand.py`

### PT demand-column schema

- Updated 2026-07-17 +02:00 (Europe/Berlin).
- `RailGTFSODTravelTimePreprocessor.augment_demand` writes `tt_pt` for the
  computed PT duration in minutes and `transfer` for the transfer count, so
  its output is accepted directly by `MultinomialLogitRequest`.

## `src/misc/globals.py`

### MNL result, parking-fare, and ASC keys

- Updated 2026-07-25 +02:00 (Europe/Berlin).
- `G_RQ_SELECTED_MODE_TT` names the user-stat result field
  `selected_mode_travel_time` in seconds.
- `G_MC_C_P_PV` names the optional fixed per-trip PV parking fare parameter
  (`private_vehicle_parking_fare`), and `G_MC_ASC_MOD` names the optional MoD
  alternative-specific constant (`mode_choice_asc_mod`).
- ASC keys use the unified global `mode_choice_asc_<mode>` convention: `pv`,
  `bike`, `pt`, `mod`, and `walk`. No distance-based ASC key exists.

## `src/routing/NetworkBasic.py`

### Snapshot metadata

| Field | Value |
| --- | --- |
| Source file | `src/routing/NetworkBasic.py` |
| Baseline | Initial FleetPy version: commit `c52f5006fd6380da6573815c7409bb1ca5c74a3c` (2021-12-07) |
| Compared against | Current working tree, including uncommitted changes |
| Baseline-to-current diff | 675 insertions, 51 deletions |
| Created | 2026-07-14 21:17:14 +02:00 (Europe/Berlin) |
| Last updated | 2026-07-27 +02:00 (Europe/Berlin) |

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
  the edge origin position `(origin_node, destination_node, 0.0)`. When the
  attached ZoneSystem supports it, the cache also supplies each zone's summed
  directed edge length in kilometres through `set_mfd_network_lengths`.
- `_get_zone_average_speed_from_mfd` (`:751`) resolves the zone speed first from
  a function registered through `set_zone_mfd_function` (`:779`), then from the
  attached ZoneSystem. `_get_zone_queue_speed` (`:771`) retains the existing
  edge TT when no MFD speed is available.
- `_update_dynamic_edge_travel_times` (`:272`) writes zone-speed-derived TTs to
  every mapped edge and resets routing caches only if a value changed.
- `_get_mfd_section_infos` (`:794`) exposes the same MFD-based calculation for
  a single edge while retaining its base TT when no valid zone speed exists.

#### PV priority-queue bathtub model

- `_get_zone_priority_queue_state` (`:454`) creates a zone state with cumulative
  entries `E`, completions `G`, distance progress `z`, speed `v`, update time,
  and a min-heap of completion thresholds.
- `_compute_fixed_zone_queue_speeds` derives a fixed base-edge-TT-equivalent
  speed for zones that lack an MFD. `_get_zone_queue_speed` uses it only for
  their PV queue; MFD edge updates and section routing continue to use MFD
  speeds exclusively, so non-MFD edge TTs remain untouched.
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

#### Zone-speed result time series

- `_record_zone_speed_snapshot` records each mapped zone's MFD speed and total
  vehicle count after every dynamic edge-TT update. Repeated updates for the
  same simulation time replace the earlier snapshot.
- `write_zone_speed_timeseries` exports the snapshots as
  `zone_speed_timeseries.csv`. It contains `simulation_time`, `zone_id`,
  `vehicle_count`, `avg_speed_mps`, and the converted `avg_speed_kmh`. Zones
  without a configured MFD speed retain blank speed values.

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
| Dynamic edge TT | `_update_dynamic_edge_travel_times` (`:272-326`); `_get_mfd_section_infos` (`:794-815`) | `tt_e = distance_e / v_z` | Sets or returns the TT of an edge from its MFD speed. Zones without an MFD retain base edge TTs. |
| Non-MFD PV queue speed | `_compute_fixed_zone_queue_speeds`; `_get_zone_queue_speed` | `v_{z,base} = sum(d_e) / sum(tt_e)` at t=0 | Advances PV through a zone without an MFD while preserving its individual edge TTs. |
| TT equality tolerance | `_set_edge_tt` (`:428-440`) | `|tt_old - tt_new| <= 1e-9 * max(1, |tt_old|, |tt_new|)` | Avoids an unnecessary edge write and routing-cache reset for floating-point-equivalent TTs. |
| PV queue progress | `_advance_zone_priority_queue_state` (`:511-555`) | `z_t = z_last + (t - t_last) * v` | Advances the shared distance-progress counter of a zone's PV bathtub queue. |
| Active PV count | `_get_zone_priority_queue_active_count` (`:569-571`) | `N_PV,z = max(E_z - G_z, 0)` | Counts PV route segments currently active in a zone queue. |
| Queue-entry projection | `_register_zone_trip` (`:584-635`) | `Δt_projected = max(t_start - t_last, 0)`; `z_projected = z + Δt_projected * v` | Computes queue progress at a PV segment's entry time before it is inserted. |
| PV completion threshold | `_register_zone_trip` (`:584-635`) | `θ = distance_remaining + z_projected`; complete when `θ <= z` | Stores each PV segment's required cumulative progress in a min-heap and determines when it leaves the zone. |
| Total zone vehicles | `_refresh_total_zone_vehicle_counts` (`:735-749`) | `N_z = N_PV,z + N_MoD,z` | Gives the MFD and dynamic-TT update the total current number of vehicles in each zone. |
| MFD vehicle-count conversion | `_get_zone_to_edge_cache` (`:354-379`); `NetworkZoneSystem.get_mfd_average_speed` | `k_z = N_z / L_z` | Converts the simulated total count to the fitted MFD density in veh/km. |
| Edge-position interpolation | `return_position_coordinates` (`:850-863`) | `coord = rel_pos * coord_destination + (1 - rel_pos) * coord_origin` | Converts an in-edge network position into metric coordinates. |
| Partial-edge movement | `move_along_route` (`:1392-1464`) | `t_next = t_last + (1 - rel_pos) * tt_edge`; `rel_end = (t_end - t_last) / tt_edge + rel_pos` | Advances a vehicle along a partially traversed edge within one simulation time step. |

### MFD ownership and units

The routing layer does not define a concrete MFD equation. The zone speed is
an externally supplied function, conceptually `v_z(N_z)`, registered in
`zone_mfd_functions` or obtained from the attached ZoneSystem through
`get_mfd_average_speed(zone_id, number_vehicles)` (`_get_zone_average_speed_from_mfd`,
`src/routing/NetworkBasic.py:757`). Its return unit must be network-distance
units per second so that `distance / speed` produces seconds. The supplied
Munich-reservoir implementation is documented below.

### Maintenance rule

When a later edit changes the documented source behavior, update this entry's
**Last updated** timestamp, source references, and the relevant change and
equation rows in the same change set. Add separate top-level entries for other
source files rather than mixing their details into this entry.

## `src/infra/NetworkZoning.py`

### Snapshot metadata

| Field | Value |
| --- | --- |
| Source file | `src/infra/NetworkZoning.py` |
| Last updated | 2026-07-25 +02:00 (Europe/Berlin) |

### Data-driven MFD configuration

`NetworkZoneSystem` optionally reads `mfd_parameters.csv` from the active zone
system directory. Its required fields are `zone_id`, `mfd_type`, `v_kmh`, and
`gamma`. Only `mfd_type=parabolic` is currently supported, representing
`q(k) = v_kmh * k - gamma * k²`; the loader validates zone IDs, duplicate rows,
finite positive coefficients, and the supported type.

The routing engine supplies directed road lengths so the implementation can
evaluate `k_z = N_z / L_z` and `v_z = max((v_kmh - gamma * k_z) / 3.6, 0.1)`.
If the CSV is absent or a zone has no row, that zone has no MFD speed and its
existing edge travel times remain unchanged.

## `src/demand/TravelerModels.py`

### Snapshot metadata

| Field | Value |
| --- | --- |
| Source file | `src/demand/TravelerModels.py` |
| Baseline | Initial FleetPy version: commit `c52f5006fd6380da6573815c7409bb1ca5c74a3c` (2021-12-07) |
| Compared against | Current working tree, including uncommitted changes |
| Baseline-to-current diff | 169 insertions, 34 deletions |
| Created | 2026-07-14 21:52:16 +02:00 (Europe/Berlin) |
| Last updated | 2026-07-17 +02:00 (Europe/Berlin) |

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

- Updated 2026-07-21 +02:00 (Europe/Berlin).
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
- `MultinomialLogitRequest` accepts optional `private_vehicle_parking_fare` and
  global `mode_choice_asc_<mode>` parameters. They may be set in the scenario
  or overridden per demand row. The parking fare is a fixed per-PV-trip monetary
  cost and is recorded as `included_park_costs` when PV is selected.

#### Choice set, utility calculation, and numerical stability

- `_compute_external_mode_utilities` (`:881`) always adds PV and walking, and
  adds bike when a valid bike speed is configured. It validates walking and
  biking speeds before forming travel times.
- `_compute_pt_utility` (`:914`) adds PT when the demand row supplies either a
  direct `pt_utility` or `tt_pt` with a configured PT intercept. A direct
  `pt_utility` remains an explicit precomputed override. `tt_pt` is converted
  from minutes to seconds before applying `beta_time`; PT fare uses
  `beta_money`; the optional `transfer` term has a separate coefficient
  controlled by `pt_transfer_penalty`.
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

- Updated 2026-07-25 +02:00 (Europe/Berlin). After selecting an alternative,
  `MultinomialLogitRequest` records `selected_mode_travel_time` in seconds:
  PV uses the direct-route TT, WALK/BIKE use direct-route distance divided by
  configured speed, PT uses demand `tt_pt` converted from minutes, and MOD
  uses the selected offer's waiting plus driving time. A PT utility override
  without `tt_pt` records no duration.
- `_add_record` (`:998`) now exports the utility and probability of every
  considered alternative, serialized as semicolon-separated `mode:value`
  pairs, and exports `highest_mod_utility`.

### Equations and calculation rules

| Rule | Current source reference | Formula / implementation | Purpose |
| --- | --- | --- | --- |
| Global ASC selection | `_get_mode_choice_asc` | `ASC_m = mode_choice_asc_m`, or `0` when omitted | Selects one global intercept for each mode; no reference-mode normalisation is applied. |
| Walking time and utility | `_compute_external_mode_utilities` | `t_walk = d_direct / v_walk`; `U_walk = ASC_walk - beta_time * t_walk` | Applies the selected or scalar walking ASC. |
| Bike time and utility | `_compute_external_mode_utilities` | `t_bike = d_direct / v_bike`; `U_bike = ASC_bike - beta_time * t_bike` | Adds bike only for a valid configured bike speed. |
| PV utility | `_compute_external_mode_utilities` | `U_PV = ASC_PV - beta_time * t_direct - beta_money * (cost_per_m * d_direct + toll + parking_fare)` | Combines selected/scalar ASC, direct PV time, distance cost, route toll, and parking fare. |
| PT utility | `_compute_pt_utility` | `U_PT = ASC_PT - beta_time * (tt_pt,min * 60) - beta_money * fare_PT - transfer_penalty * transfer` | Uses the selected/scalar PT ASC when PT time is available. The optional transfer term has a separate coefficient. |
| MOD utility | `_compute_mod_offer_utilities` | `U_MOD,o = ASC_MOD - beta_time * (t_wait + t_drive) - beta_money * fare_o` | Scores each non-declined MOD offer with the selected/scalar MoD ASC. |
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

## `src/demand/demand.py` and `src/FleetSimulationBase.py`

### Background-demand loading and network injection

Demand CSV files may optionally contain an `rq_pv` column. A row with
`rq_pv == 1` is background traffic; it is not converted into a traveler or
sent to the broker. Empty values, other values, and files without the column
remain ordinary RQ input for backward compatibility.

`Demand.load_demand_file` (`src/demand/demand.py:94-145`) groups background
rows by their rounded `rq_time` in `future_background_trips`.
`get_new_background_trips` (`:244-251`) releases them with the same time-step
semantics as ordinary requests. `_assign_new_background_routes`
(`src/FleetSimulationBase.py:759-789`) computes the OD route and calls
`assign_route_to_network(route, rq_time, arrival_time, 1)`, thereby adding one
background vehicle to the network priority queue without creating a request
record.

The Immediate, Broker, Batch, and RL Batch simulation environments invoke this
background injection after their network update and before broker processing.
Initialization, per-step injection, and skipped-route messages use the term
`background` in the runtime log.

## `src/fleetctrl/PoolingIRSOnly.py`

### Snapshot metadata

| Field | Value |
| --- | --- |
| Source file | `src/fleetctrl/PoolingIRSOnly.py` |
| Baseline | Initial FleetPy version: commit `c52f5006fd6380da6573815c7409bb1ca5c74a3c` (2021-12-07) |
| Compared against | Current working tree, including uncommitted changes |
| Created | 2026-07-17 +02:00 (Europe/Berlin) |
| Last updated | 2026-07-17 +02:00 (Europe/Berlin) |

### Dynamic network travel-time updates

`PoolingInsertionHeuristicOnly.inform_network_travel_time_update` is called by
the broker after the routing engine has applied a dynamic/MFD travel-time
update and before the simulation processes new requests for that step. The
method first refreshes each vehicle's currently active route with
`SimulationVehicle.update_route`, then recalculates every non-empty committed
`VehiclePlan` through `update_tt_and_check_plan(..., keep_feasible=True)` and
updates its objective value.

The method does not reassign, cancel, or alter the confirmed offer of any
request. If changed travel times make a plan infeasible, `keep_feasible=True`
retains its stop sequence and locks the affected prefix according to
`VehiclePlan` semantics. One INFO message per network update reports the count
of rerouted vehicles, refreshed plans, and retained infeasible plans.

## `src/FleetSimulationBase.py`

### Zone-speed result export

- Added 2026-07-19 +02:00 (Europe/Berlin).
- `record_stats` asks routing engines that provide
  `write_zone_speed_timeseries` to write `zone_speed_timeseries.csv` into the
  active result directory. Other routing engines are unaffected.
