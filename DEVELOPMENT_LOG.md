# Development Log

This document records source-level changes, equations, and implementation notes
for files that require quick future reference. Add a new top-level entry for each
additional source file.

## `data/demand/Munich_PV_2020/matched/Aimsun_Munich_2020/rq_munich_matsim_5x.csv`

### Fivefold MATSim request-demand expansion

- Added 2026-08-06 +02:00 (Europe/Berlin). This generated CSV preserves the
  source request order from `rq_munich_matsim.csv`, writes five consecutive
  copies of each request, and regenerates `request_id` consecutively from zero.
  The source file remains unchanged.

## `data/demand/Munich_PV_2020/matched/Aimsun_Munich_2020/rq_munich_matsim_10x.csv`

### Tenfold MATSim request-demand expansion

- Added 2026-08-07 +02:00 (Europe/Berlin). This generated CSV preserves the
  source request order from `rq_munich_matsim.csv`, writes ten consecutive
  copies of each request, and regenerates `request_id` consecutively from zero.
  The source file remains unchanged.

## `src/preprocessing/networks/add_nearest_aimsum_node.py`

### AIMSUM nearest-node annotation

- Added 2026-08-02 +02:00 (Europe/Berlin). The command-line utility copies
  `node_trip_nonzero_euclidean.csv` to
  `node_trip_nonzero_euclidean_with_aimsum_index.csv` by default, retaining
  every original field and appending `aimsum_pos_x`, `aimsum_pos_y`, and
  `aimsum_node_index`.  These values are copied from the closest `(pos_x,
  pos_y)` row of `node_AIMSUM.csv` using Euclidean distance in the source
  projected-coordinate units.
- The utility validates finite coordinates and required headers, detects comma,
  tab, or semicolon input delimiters, and writes atomically.  Existing outputs
  are protected unless `--overwrite` is passed.  A balanced two-dimensional
  k-d tree makes the exact nearest-neighbour lookup practical for the supplied
  node tables; equal distances use the first reference-node row.

## `src/preprocessing/networks/create_aimsum_gtfs_demand.py` and `src/preprocessing/pubtrans/add_rail_gtfs_to_demand.py`

### AIMSUM request conversion with rail-GTFS attributes

- Added 2026-08-02 +02:00 (Europe/Berlin). `create_aimsum_gtfs_demand.py`
  streams `rq_muechen_nonzero_euclidean.csv`, maps each exact origin and
  destination coordinate through
  `node_trip_nonzero_euclidean_with_aimsum_index.csv`, and writes
  `rq_muechen_nonzero_euclidean_aimsum.csv`. The output contains only
  `request_id`, `rq_time`, `start`, `end`, `gtfs_total_duration_min`, and
  `nr_transfers`; IDs are regenerated from zero in source-row order, and an
  unavailable rail route has blank GTFS fields.
- The converter uses the existing rail-GTFS logic and its defaults: active
  2026-07-06 services, Tram/S-Bahn, U-Bahn, and Regionalbahn routes, a 1000 m
  access/egress radius, 1.4 m/s walking speed, and a 120 s transfer buffer.
  It validates mappings and coordinates, processes requests sequentially
  without loading the 86 MB request source into memory, and atomically writes
  a protected output file.
- Updated 2026-08-02 +02:00 (Europe/Berlin).
  `RailGTFSODTravelTimePreprocessor.compute_request` now calls an optional
  Numba-compiled implementation of the existing connection-scan calculation
  when Numba is installed. The compiled path retains the same earliest-arrival
  and equal-arrival/fewer-transfer rules; installations without Numba retain
  the original Python implementation.

## `src/preprocessing/networks/find_rq_inside_munich.py`

### Munich request filtering from node coordinates

- Added 2026-07-31 +02:00 (Europe/Berlin). The command-line utility compares
  every input trip's `(origin_x, origin_y)` and `(destination_x, destination_y)`
  pairs against the `(pos_x, pos_y)` pairs in `node_info_munich.csv`. It retains
  a row only when both pairs occur in the reference-node set.
- It writes `rq_inside_munich.csv` in this directory by default, with only
  `request_id`, `rq_time`, `origin_x`, `origin_y`, `destination_x`, and
  `destination_y`. `rq_time` is the input `departure_time`; rows are sorted by
  its numeric value (keeping input order for ties) and receive contiguous IDs
  beginning at zero.
- The input and node CSV delimiters are detected among comma, tab, and
  semicolon. Updated 2026-07-31 +02:00 (Europe/Berlin): matching used raw
  coordinate texts. Updated 2026-07-31 +02:00 (Europe/Berlin): reference-node
  exports can truncate projected coordinates, so matching now uses validated
  `Decimal` coordinates and a spatial grid with a default absolute x/y
  tolerance of `0.000001` metres (configurable with
  `--coordinate-tolerance-m`). This preserves membership when only exported
  precision differs while avoiding a scan of every reference node per trip.
  The output retains every input trip field, prepends regenerated
  `request_id` and `rq_time` (from `departure_time`), and remains sorted by
  numeric departure time. Existing outputs are protected unless `--overwrite`
  is passed.
- Updated 2026-07-31 +02:00 (Europe/Berlin): the current defaults are
  `node_info_muechen.csv` and `rq_muechen.csv`; the earlier filename wording
  above described the initial implementation rather than the current defaults.
- Updated 2026-07-31 +02:00 (Europe/Berlin): after filtering, the utility also
  writes `node_trip.csv` by default. It deduplicates retained origin and
  destination coordinate pairs in the sorted request order, then writes
  `node_index`, `is_stop_only=False`, blank `source_node_id`, `pos_x`, and
  `pos_y`. `--node-trip-file` selects another path; it may not equal
  `--output-file`.
- Updated 2026-07-31 +02:00 (Europe/Berlin): `euclidean_distance` is now
  required and trips whose validated numeric Euclidean distance is exactly zero
  are excluded. Endpoint-node output is now opt-in through `--node-trip-file`,
  so a trip-only run does not create or replace a node CSV.

## `src/preprocessing/networks/remove_point_outsite_zone.py`

### Munich municipality point filtering

- Added 2026-07-31 +02:00 (Europe/Berlin). The command-line utility retains
  only point features that lie within at least one Polygon or MultiPolygon
  feature of a zone GeoJSON, including an exterior boundary. It writes a new
  GeoJSON atomically and will not replace an existing destination unless
  `--overwrite` is specified.
- Its defaults filter `node_upperbavaria.geojson` against
  `data/zones/Munich_Municipalities/polygon_definition.geojson` and write
  `node_upperbavaria_inside_munich_municipalities.geojson`. The supplied node
  geometry is EPSG:3857 whereas the zone file is EPSG:32632; default membership
  checks therefore use `pos_x` and `pos_y`, which are the node file's EPSG:32632
  coordinates. `--geometry-coordinates` is available only for matching CRS
  inputs.
- The implementation uses only the Python standard library. It validates input
  FeatureCollections and coordinates, supports polygon holes, and preserves all
  retained source-feature fields and the point collection's original CRS.

## `src/preprocessing/networks/create_node_from_csv.py`

### Node table creation from trip coordinates

- Added 2026-07-30 +02:00 (Europe/Berlin). The command-line utility reads a
  column-based trip CSV containing `origin_x`, `origin_y`, `destination_x`, and
  `destination_y`, then writes `node_info.csv` beside the script by default.
  It emits `node_index`, `is_stop_only`, `source_node_id`, `pos_x`, and
  `pos_y`; node indices start at zero, `is_stop_only` is always `False`, and
  `source_node_id` is blank.
- Origin and destination coordinate pairs are processed in input row order and
  deduplicated together. Updated 2026-07-31 +02:00 (Europe/Berlin): both
  coordinates must have exactly identical input text to match. This includes all
  digits after the decimal point, including trailing zeroes. The first input
  coordinate strings are written unchanged, including their decimal precision
  and whitespace. The input delimiter is detected among comma, semicolon, and
  tab, and missing, non-numeric, or non-finite coordinates fail with a
  row-specific error.

## `studies/mt/plot_compare_60x.py`

### Three-scenario pricing comparison by zone

- Added 2026-07-30 +02:00 (Europe/Berlin). The script compares
  `mt_test_60x_base`, `mt_test_60x_mfd`, and `mt_test_60x_time`,
  writing reproducible figures and source CSV tables to the baseline run's
  `compare/` directory by default. It produces origin-zone mode shares,
  accumulation and average-speed time series, road-mode delay time series, and
  mean selected generalized cost by origin zone and mode.
- MFD zones 0--4 are included by default; zone 5 is omitted because it has no
  MFD parameters. `--include-zone-5` retains its directly exported traffic
  data in applicable figures but does not manufacture an MFD congestion line.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Each comparison topic now has
  its own output folder (`mode_share`, `accumulation`, `speed`, `delay`, and
  `generalized_cost`) and writes one PNG plus its source CSV for every zone.
  Scenario metadata is in `metadata/`, while the MFD threshold table is kept
  with the accumulation comparison.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Time-series axes begin exactly
  at the configured simulation start time and use 30-minute clock ticks. The
  baseline uses a black series; scenario legends are below the plotting area.
  Mode-share and generalized-cost bars have no horizontal grid lines and show
  their finite values immediately above each bar.
- Updated 2026-07-30 +02:00 (Europe/Berlin). `OTHER` is excluded from the
  mode-share and generalized-cost comparison categories because these runs
  have no selected requests in that category. The three scenario colors are
  harmonized as charcoal (`#2f3437`), academic blue (`#3b6ea5`), and brick red
  (`#b35c4e`) across every comparison figure.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Every origin-zone mode-share
  title now gives its three scenario request totals, while `mode_share/overall`
  reports the share over all requests. Their source CSV files include both
  per-mode and total request counts. The generalized-cost comparison converts
  the simulation's euro-cent monetary units to euro (`/ 100`) in both its plot
  and CSV output; its axis and data labels use `€`. All comparison y-axes now
  explicitly identify their displayed unit.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Figure legends and output tables
  use the policy descriptions `No road pricing`, `MFD-responsive cordon pricing`,
  and `Time-of-day cordon pricing`. When request totals match across scenarios,
  mode-share titles use one `Requests per scenario` note that explicitly states
  their equality rather than repeating the same count three times.
- Updated 2026-07-30 +02:00 (Europe/Berlin). The third comparison input is now
  `mt_test_60x_mfd` rather than `mt_test_60x_distance_time`; its label is
  `MFD-responsive cordon pricing`. Time-of-day shading remains in the figures
  solely as a reference for the scheduled cordon scenario, because MFD pricing
  can change in any simulation period.
- Updated 2026-07-30 +02:00 (Europe/Berlin). `distance_distribution/overall`
  now uses only `mt_test_60x_base`, because the same request cohort produces an
  identical distance distribution in all compared policies. Its title is
  `Distance distribution`, and every bar is labelled with its request count.
  The bar height remains the percentage share of all requests, and the
  upper-right annotation reports the total request count.
- Updated 2026-07-30 +02:00 (Europe/Berlin). The comparison now produces one
  `pricing/zone_<id>` time series per zone using only the two priced scenarios;
  it reads the recorded cordon charge from `5_road_pricing_info.csv` and does
  not plot a synthetic zero-price baseline. Accumulation and average-speed
  panels now add a blue right density axis:
  accumulation converts `N` to `k = N / L_z`, while speed converts
  `v` to `k = (v_free - v) / gamma`.
- Updated 2026-07-30 +02:00 (Europe/Berlin). The right-hand density `k` axis
  is a common zone-MFD scale for every scenario curve in a panel, rather than
  being restricted to the MFD-responsive pricing curve. Its label is now
  simply `Density k (veh/km)`; the blue styling identifies the MFD scale, not
  a single data series.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Comparison figures use larger
  12-by-7-inch canvases and 18-point titles. Equal request totals are now
  annotated only as `Requests per scenario: <count>`. Legends have no frame;
  generalized-cost bar labels show numeric euro values without a repeated `€`
  symbol, while the axis retains the euro unit.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Typography now scales with the
  enlarged canvas: 22-point titles, 18-point axis labels, 15-point ticks and
  legends, and 12-point value/critical annotations. PNG exports use 300 DPI.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Bar-top labels are vertically
  staggered by scenario to prevent enlarged labels for neighbouring bars with
  similar values from overlapping; the 28-point separation keeps the enlarged
  labels distinct in closely matched bars.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Replaced the wide vertical
  staggering with a 10-point directly-above-bar label: this remains larger than
  the original labels while preserving an unambiguous association with each
  bar.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Speed-panel annotations report
  the exact two-decimal congestion threshold with `km/h` (for example, Zone 0
  is `9.53 km/h`) rather than a rounded integer that can appear inconsistent
  with its horizontal line. Accumulation annotations now state their equivalent
  vehicle unit as well.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Average-speed panels use a
  dedicated 15-by-9-inch canvas, 24-point title, 1.8-point scenario lines, and
  12% vertical margins to make the trajectories, threshold, and external
  legend less crowded. The remaining comparison topics keep their 12-by-7-inch
  layout.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Accumulation panels now use the
  same enlarged 15-by-9-inch layout, 24-point title, thicker lines, and
  vertical margin. Their threshold annotation uses the concise unit `vehicles`
  rather than `equivalent vehicles`.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Scenario output order is now
  no-road-pricing (charcoal), time-of-day cordon pricing (brick red), and
  time-of-day distance pricing (blue). The order applies consistently to
  grouped bars, line-plot legends, annotations, and exported comparison CSVs.
- Updated 2026-07-30 +02:00 (Europe/Berlin). All time-series comparisons shade
  the scheduled pricing windows over their full plot height: 06:30--07:15 and
  08:15--09:00 in light blue, with the 07:15--08:15 morning peak in a slightly
  deeper light blue. Accumulation, average-speed, and road-mode-delay plots now
  have an explicit y-axis lower bound of zero.
- Updated 2026-07-30 +02:00 (Europe/Berlin). All figure canvases have added
  vertical space without changing the data-axis scaling: standard panels are
  12-by-8.25 inches and enlarged traffic panels are 15-by-10.5 inches. Titles
  use a 26-point pad and the export layout reserves a 6% top margin, moving
  titles upward while leaving sufficient room for multi-line annotations.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Time-series x-axis tick labels
  use a 10-point pad and `Time of day` uses a 22-point label pad, lowering both
  and separating them from one another. Mode-share request totals are no longer
  a second title line; they are compact 12-point annotations at the upper-right
  of the plotting area, matching the placement of congestion-threshold notes.
- Updated 2026-07-30 +02:00 (Europe/Berlin). The comparison script now reuses
  each scenario's `choice_distribution_by_distance.csv` to create a direct
  distance-distribution comparison and one mode-share comparison for every
  populated distance band. These outputs are written to
  `distance_distribution/` and `mode_share_by_distance/`, respectively.
- Updated 2026-07-30 +02:00 (Europe/Berlin). When a distance band has unequal
  scenario request counts, its compact upper-right annotation uses the ordered
  `No pricing / Cordon / Distance` labels and a 10-point font so it remains
  legible without overlapping the plotting area.
- Updated 2026-07-30 +02:00 (Europe/Berlin). Distance-distribution and
  distance-conditioned mode-share comparisons now use a fixed
  `request_id -> baseline direct-route distance band` mapping. This validates
  that every scenario contains the same unique request IDs and places each ID
  in the identical cohort for every policy. The former scenario-specific
  `choice_distribution_by_distance.csv` grouping could move requests between
  bands when dynamic routing recalculated their direct-route distance.
- Updated 2026-07-30 +02:00 (Europe/Berlin). The fixed-cohort distance
  distribution is retained, while distance-conditioned mode-share titles use
  the concise wording `Mode share by distance: <band>`.
- The congestion annotations use `v_critical = v_free / 2` and
  `N_critical = (v_free / (2 * gamma)) * L_zone`, where zone length is the
  sum of outgoing network-edge distances assigned to the source zone. The
  traffic records use the run's existing vehicle counts.
- Delay is evaluated only for PV and MOD selected trips as
  `max(selected_mode_travel_time - direct_route_travel_time, 0)` and averaged
  in 15-minute request-time bins by origin zone. This avoids interpreting the
  inherently slower non-road alternatives as road congestion delay. Selected
  generalized cost is reconstructed from the recorded selected utility as
  `(ASC_selected - U_selected) / beta_money`, consistent with the existing
  welfare analysis. Validated 2026-07-30 +02:00 (Europe/Berlin) against the
  three named result directories; the common figure writer reserves space for
  and preserves the shared scenario legend in the output bounds.

## `studies/mt/plot_all_metrics.py`

### One-command single-scenario metrics generation

- Added 2026-07-29 +02:00 (Europe/Berlin). The command-line wrapper accepts one
  FleetPy result directory and sequentially runs the demand/mode, MFD traffic,
  MoD-operation, and user-welfare plot scripts using the active Python
  interpreter. It deliberately delegates all calculation and plot generation to
  those scripts rather than duplicating their metric logic.
- `--output-dir` is an optional common parent directory; the wrapper creates the
  four topic subdirectories below it. `--time-bin-min` is passed to the three
  scripts that support time aggregation (demand, MoD, and welfare), while MFD
  retains its existing time resolution.
- Updated 2026-07-29 +02:00 (Europe/Berlin). Zone 5 is excluded from MFD
  traffic output by default. Pass `--include-zone-5` to forward that override
  only to the MFD traffic script in a batch run.

## `studies/mt/plot_mfd_traffic_metrics.py`

### Default Zone 5 exclusion

- Added 2026-07-29 +02:00 (Europe/Berlin). Zone 5 is removed before every MFD
  calculation by default, so all generated plots, tables, congestion summaries,
  and MFD parameter output exclude that zone consistently. Pass
  `--include-zone-5` to retain it for a specific run.

## `src/preprocessing/demand/replicate_demand.py`

### Demand-file and demand-directory replication for real mode-choice events

- Added 2026-07-29 +02:00 (Europe/Berlin).
- Updated 2026-07-29 +02:00 (Europe/Berlin). The command-line utility accepts
  either one demand CSV or a demand directory. A CSV produces a sibling named
  `<input_stem>_<copies>.csv` by default; a directory produces
  `<input_name>_<copies>` and preserves auxiliary files while transforming
  every request CSV containing `rq_time`. Use `--output-path` to select either
  output explicitly (`--output-dir` remains an alias).
- A row whose `rq_pv` value is numerically `1` remains once. Every other row,
  including a missing or blank `rq_pv`, yields exactly `copies` real request
  events. Thus `--copies 3` means three total events rather than the original
  row plus three additional events.
- Each transformed request CSV is sorted by numeric `rq_time`; requests with
  the same time retain their original row order and each row's replicas remain
  adjacent. Its `request_id` is regenerated as contiguous integers, starting
  at zero by default or at `--id-start`. This avoids ID collisions in FleetPy's
  request, broker, and result-recording path.
- Run from the repository root, for example:
  `python src/preprocessing/demand/replicate_demand.py data/demand/Munich_PV_2020/matched/Aimsun_Munich_2020/d_1000.csv --copies 3`.

## `src/infra/RoadPricing.py`, `src/infra/NetworkZoning.py`, and `src/demand/TravelerModels.py`

### Pre-set scheduled zone tariffs for PV mode choice

- Added 2026-07-29 +02:00 (Europe/Berlin). The Munich tariff dataset
  `scheduled_zone_tariff_val1.csv` is a time-of-day cordon validation policy.
  Updated 2026-07-30 +02:00 (Europe/Berlin): zones 0--4 now share zone 1's
  fee in every charging window, for a uniform priced-versus-unpriced test.
  The same file now also contains the `distance/time_of_day` rows used by
  `mt_test_60x_distance_time`: zones 0--4 use 0.10 cent/m in the shoulder
  periods, 0.20 cent/m in the morning peak, 0.1666666667 cent/m in the evening
  peak, and zero otherwise; zone 5 remains zero. These rates equal the uniform
  cordon fees for a 3 km in-zone trip.
  Updated 2026-07-30 +02:00 (Europe/Berlin): it additionally contains
  `cordon/mfd_speed` rows for an MFD-responsive test. For zones 0--4 the
  cordon charge is zero below `0.75 * k_critical`, 150 cent from `0.75` to
  `1.00 * k_critical`, 300 cent from `1.00` to `1.25 * k_critical`, and
  600 cent at or above `1.25 * k_critical`; the stored speed boundaries are
  the exact equivalent of those density bands. Zone 5 remains an outside,
  zero-charge row.
  It charges only on a transition into a new zone (never in zone 5): free
  overnight and daytime, shoulder charges from 06:30--07:15 and 08:15--09:00,
  at 300 cent, a 07:15--08:15 morning peak at 600 cent, and a provisional
  15:30--18:30 evening peak at 500 cent. It is intended to be selected
  explicitly by a scenario through `rp_tariff_schedule_file`; no scenario
  configuration was changed when the dataset was added.
- Updated 2026-07-30 +02:00 (Europe/Berlin). `scenario_cfg_mt.csv` retains
  `mt_test_60x_time` with `rp_charge_type=cordon` and adds
  `mt_test_60x_distance_time` with `rp_charge_type=distance`; both inherit the
  same scheduled time-of-day policy and all non-pricing parameters.
- Added 2026-07-29 +02:00 (Europe/Berlin). `ScheduledZoneTariffPricing`
  implements the `scheduled_zone_tariff` policy. The scenario selects
  `rp_charge_type` (`cordon` or `distance`), `rp_tariff_basis`
  (`time_of_day` or `mfd_speed`), and one CSV schedule file.
- The required schedule columns are `charge_type`, `tariff_basis`, `zone_id`,
  `time_start`, `time_end`, `speed_min_kmh`, `speed_max_kmh`, `speed_band`,
  `entry_fee_cent`, and `distance_rate_cent_per_m`. `time_of_day` tariffs must
  cover the simulation horizon; `mfd_speed` tariffs define contiguous speed
  bands from zero to an unbounded final range for every MFD zone.
- A cordon tariff applies once at each transition into a new zone, excluding a
  route's initial zone. A distance tariff sums each contiguous origin-zone
  route segment's distance times the rate at its projected entry time. Both
  forms round only the complete route total to cents.
- `NetworkZoneSystem.get_pv_route_toll_cost` delegates to the scheduled policy
  while its existing generic toll interface remains available to fleet-control
  code. `MultinomialLogitRequest` now prefers that PV-specific interface, so
  the scheduled tariff enters only the PV utility and `included_toll`; MoD
  fare and operating toll calculations remain unchanged.
- `mfd_speed` reads `NetworkBasic.get_current_zone_mfd_speeds()`, the read-only
  snapshot already calculated for network edge travel times, once per network
  pricing update and caches the selected tariff for PV quotations. The
  `rp_tariff_update_interval` interface defaults to 300 seconds; Munich sets
  it explicitly to 300, so network TTs may continue updating every simulation
  step while the region tariff remains fixed for five minutes. The policy also
  exposes `set_update_interval(seconds)` for programmatic adjustment. It does
  not use a reference MFD trajectory or recalculate a speed per PV.
- The Munich scenario comments document the two supported charge choices
  (`cordon`, `distance`) and tariff bases (`time_of_day`, `mfd_speed`), and
  clarify that the refresh interval applies only to MFD-speed tariffs.
- `road_pricing_method: None` (also `none`, `off`, or `disabled`) explicitly
  disables road pricing: no policy is created and no PV toll is added.
- `build_zone_tariff_schedule.py` converts the active MFD parameters into one
  common range tariff table. It accepts an optional untolled-route distance
  summary (`zone_id,mean_in_zone_distance_m`) to turn distance rates into
  comparable cordon fees. Munich activates `distance/mfd_speed` from
  `data/zones/Munich_reservoirs/Aimsun_Munich_2020/munich_zone_tariffs.csv`.

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
| Last updated | 2026-08-02 +02:00 (Europe/Berlin) |

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
| MFD vehicle-count conversion | `_get_zone_to_edge_cache` (`:354-379`); `NetworkZoneSystem.get_mfd_average_speed` | `k_z = N_z / L_z` | Converts the current simulated vehicle count to the fitted MFD density in veh/km. |
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
| Last updated | 2026-07-28 +02:00 (Europe/Berlin) |

### Data-driven MFD configuration

`NetworkZoneSystem` optionally reads `mfd_parameters.csv` from the active zone
system directory. Its required fields are `zone_id`, `mfd_type`, `v_kmh`, and
`gamma`. Only `mfd_type=parabolic` is currently supported, representing
`q(k) = v_kmh * k - gamma * k²`; the loader validates zone IDs, duplicate rows,
finite positive coefficients, and the supported type.

The routing engine supplies directed road lengths so the implementation can
evaluate `k_z = N_z / L_z` and
`v_z = max((v_kmh - gamma * k_z) / 3.6, 0.1)`. A zone without an MFD has no
MFD speed and retains its existing edge travel times.

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
