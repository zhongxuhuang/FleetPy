"""Attach a FleetPy zone assignment CSV to a network node GeoJSON.

The output retains the original Point geometry and properties and adds
``zone_id`` and ``is_centroid`` based on the common ``node_index`` field.
"""

import argparse
import csv
import json
from pathlib import Path


def load_zone_assignments(zone_info_file: Path) -> dict[str, dict[str, int | str]]:
    """Return zone attributes indexed by the CSV's node_index column."""
    with zone_info_file.open(encoding="utf-8", newline="") as file_handle:
        rows = csv.DictReader(file_handle)
        required_columns = {"node_index", "zone_id", "is_centroid"}
        if not rows.fieldnames or not required_columns.issubset(rows.fieldnames):
            raise ValueError(
                f"{zone_info_file} must contain {sorted(required_columns)}; "
                f"found {rows.fieldnames}."
            )

        assignments: dict[str, dict[str, int | str]] = {}
        for row in rows:
            node_index = row["node_index"]
            zone_id = int(row["zone_id"])
            is_centroid = int(row["is_centroid"])
            existing_assignment = assignments.get(node_index)
            if existing_assignment is None:
                assignments[node_index] = {
                    "zone_id": zone_id,
                    "is_centroid": is_centroid,
                    "zone_id_candidates": str(zone_id),
                }
                continue
            if existing_assignment["is_centroid"] != is_centroid:
                raise ValueError(
                    f"Conflicting is_centroid values for node_index {node_index} "
                    f"in {zone_info_file}."
                )
            candidate_zone_ids = existing_assignment["zone_id_candidates"].split(";")
            if str(zone_id) not in candidate_zone_ids:
                existing_assignment["zone_id_candidates"] = ";".join(
                    [*candidate_zone_ids, str(zone_id)]
                )
    return assignments


def add_zone_info(nodes_file: Path, zone_info_file: Path, output_file: Path) -> tuple[int, int]:
    """Write an enriched copy of *nodes_file* and return feature/match counts.

    ``zone_id`` uses the first mapping row, matching ``ZoneSystem``. Where a
    boundary node has multiple assignments, ``zone_id_candidates`` retains all
    of them in CSV order for visual review.
    """
    with nodes_file.open(encoding="utf-8") as file_handle:
        feature_collection = json.load(file_handle)

    assignments = load_zone_assignments(zone_info_file)
    matched_node_indices: set[str] = set()
    for feature in feature_collection["features"]:
        properties = feature.setdefault("properties", {})
        node_index = str(properties["node_index"])
        assignment = assignments.get(node_index)
        if assignment is None:
            raise ValueError(f"No zone assignment for node_index {node_index}.")
        properties.update(assignment)
        matched_node_indices.add(node_index)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as file_handle:
        json.dump(feature_collection, file_handle, ensure_ascii=False, separators=(",", ":"))

    return len(feature_collection["features"]), len(matched_node_indices)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nodes_file", type=Path, help="Input nodes_all_infos.geojson file.")
    parser.add_argument("zone_info_file", type=Path, help="Input node_zone_info.csv file.")
    parser.add_argument("output_file", type=Path, help="New GeoJSON file to write.")
    args = parser.parse_args()

    feature_count, matched_count = add_zone_info(
        args.nodes_file, args.zone_info_file, args.output_file
    )
    print(
        f"Wrote {args.output_file}: {feature_count} features with "
        f"{matched_count} unique node assignments."
    )


if __name__ == "__main__":
    main()
