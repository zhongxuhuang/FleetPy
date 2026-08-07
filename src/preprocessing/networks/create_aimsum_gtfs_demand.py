"""Create an AIMSUM-node demand file with rail-GTFS travel-time attributes.

Run from the repository root with the Python environment that contains the
dependencies declared in ``environment.yml``:

    C:\\Users\\zhong\\anaconda3\\python.exe src/preprocessing/networks/create_aimsum_gtfs_demand.py
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
PUBTRANS_DIRECTORY = SCRIPT_DIRECTORY.parent / "pubtrans"
sys.path.insert(0, str(PUBTRANS_DIRECTORY))
from add_rail_gtfs_to_demand import RailGTFSODTravelTimePreprocessor  # noqa: E402


DEFAULT_REQUEST_FILE = SCRIPT_DIRECTORY / "rq_muechen_nonzero_euclidean.csv"
DEFAULT_NODE_MAPPING_FILE = SCRIPT_DIRECTORY / "node_trip_nonzero_euclidean_with_aimsum_index.csv"
DEFAULT_OUTPUT_FILE = SCRIPT_DIRECTORY / "rq_muechen_nonzero_euclidean_aimsum.csv"
OUTPUT_COLUMNS = (
    "request_id",
    "rq_time",
    "start",
    "end",
    "gtfs_total_duration_min",
    "nr_transfers",
)
REQUEST_COLUMNS = ("rq_time", "origin_x", "origin_y", "destination_x", "destination_y")
MAPPING_COLUMNS = ("pos_x", "pos_y", "aimsum_node_index")


def detect_delimiter(file_path: Path) -> str:
    """Detect one of the delimiters supported by the preprocessing CSVs."""
    with file_path.open("r", encoding="utf-8-sig", newline="") as input_file:
        sample = input_file.read(8192)
    if not sample:
        raise ValueError(f"Input file is empty: {file_path}")
    delimiters = (",", "\t", ";")
    delimiter = max(delimiters, key=sample.count)
    if sample.count(delimiter) == 0:
        raise ValueError(f"Could not detect a comma, tab, or semicolon delimiter in {file_path}")
    return delimiter


def validate_header(fieldnames: list[str] | None, required_columns: tuple[str, ...], file_path: Path) -> None:
    if fieldnames is None:
        raise ValueError(f"Input file has no header row: {file_path}")
    missing = [column for column in required_columns if column not in fieldnames]
    if missing:
        raise ValueError(f"{file_path} is missing required column(s): {', '.join(missing)}")


def parse_finite_number(value: str | None, *, file_path: Path, row_number: int, column: str) -> float:
    if value is None or not value.strip():
        raise ValueError(f"{file_path}: row {row_number} has an empty {column!r} value")
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(
            f"{file_path}: row {row_number} has a non-numeric {column!r} value: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise ValueError(f"{file_path}: row {row_number} has a non-finite {column!r} value: {value!r}")
    return number


def load_aimsum_node_mapping(mapping_file: Path) -> dict[tuple[str, str], int]:
    """Map the exact source-coordinate spelling to the selected AIMSUM node ID."""
    delimiter = detect_delimiter(mapping_file)
    mapping: dict[tuple[str, str], int] = {}
    with mapping_file.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file, delimiter=delimiter)
        validate_header(reader.fieldnames, MAPPING_COLUMNS, mapping_file)
        for row_number, row in enumerate(reader, start=2):
            coordinate = (row["pos_x"], row["pos_y"])
            if coordinate in mapping:
                raise ValueError(f"{mapping_file}: duplicate source coordinate at row {row_number}")
            try:
                mapping[coordinate] = int(row["aimsum_node_index"])
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"{mapping_file}: row {row_number} has an invalid aimsum_node_index"
                ) from error
    if not mapping:
        raise ValueError(f"No node mappings found in {mapping_file}")
    return mapping


def create_demand(
    request_file: Path,
    mapping_file: Path,
    output_file: Path,
    *,
    gtfs_dir: str,
    network_dir: str,
    service_date: str,
    access_radius_m: float,
    walking_speed: float,
    transfer_buffer_s: float,
    overwrite: bool,
) -> tuple[int, int]:
    """Write the six-column demand file and return (row count, found PT paths)."""
    if output_file.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists: {output_file}. Use --overwrite to replace it.")

    mapping = load_aimsum_node_mapping(mapping_file)
    processor = RailGTFSODTravelTimePreprocessor(
        gtfs_dir=gtfs_dir,
        network_dir=network_dir,
        service_date=service_date,
        access_radius_m=access_radius_m,
        walking_speed=walking_speed,
        transfer_buffer_s=transfer_buffer_s,
        time_bin_s=0,
    )
    mapped_node_ids = sorted(set(mapping.values()))
    processor.prepare_candidate_cache(pd.DataFrame({"start": mapped_node_ids, "end": mapped_node_ids}))

    delimiter = detect_delimiter(request_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    row_count = 0
    found_paths = 0
    try:
        with request_file.open("r", encoding="utf-8-sig", newline="") as input_file, NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=output_file.parent, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            reader = csv.DictReader(input_file, delimiter=delimiter)
            validate_header(reader.fieldnames, REQUEST_COLUMNS, request_file)
            writer = csv.DictWriter(temporary_file, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            for row_number, row in enumerate(reader, start=2):
                origin = (row["origin_x"], row["origin_y"])
                destination = (row["destination_x"], row["destination_y"])
                try:
                    start_node = mapping[origin]
                    end_node = mapping[destination]
                except KeyError as error:
                    raise ValueError(
                        f"{request_file}: row {row_number} has a coordinate absent from {mapping_file}"
                    ) from error
                rq_time = parse_finite_number(
                    row["rq_time"], file_path=request_file, row_number=row_number, column="rq_time"
                )
                duration_min, transfers = processor.compute_request(rq_time, start_node, end_node)
                if np.isfinite(duration_min):
                    duration_value = str(float(duration_min))
                    transfer_value = str(int(transfers))
                    found_paths += 1
                else:
                    duration_value = ""
                    transfer_value = ""
                writer.writerow(
                    {
                        "request_id": row_count,
                        "rq_time": row["rq_time"],
                        "start": start_node,
                        "end": end_node,
                        "gtfs_total_duration_min": duration_value,
                        "nr_transfers": transfer_value,
                    }
                )
                row_count += 1
                if row_count % 10000 == 0:
                    print(f"Processed {row_count} requests; rail paths found: {found_paths}", flush=True)
        os.replace(temporary_path, output_file)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return row_count, found_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an AIMSUM-node request CSV with GTFS rail duration and transfer fields."
    )
    parser.add_argument("--request-file", type=Path, default=DEFAULT_REQUEST_FILE)
    parser.add_argument("--mapping-file", type=Path, default=DEFAULT_NODE_MAPPING_FILE)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--gtfs-dir", default="data/gesamt_gtfs")
    parser.add_argument("--network-dir", default="data/networks/Aimsun_Munich_2020")
    parser.add_argument("--service-date", default="20260706")
    parser.add_argument("--access-radius-m", type=float, default=1000.0)
    parser.add_argument("--walking-speed", type=float, default=1.4)
    parser.add_argument("--transfer-buffer-s", type=float, default=120.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for label, path in (("Request", args.request_file), ("Mapping", args.mapping_file)):
        if not path.is_file():
            raise SystemExit(f"{label} file does not exist or is not a file: {path}")
    try:
        row_count, found_paths = create_demand(
            args.request_file,
            args.mapping_file,
            args.output_file,
            gtfs_dir=args.gtfs_dir,
            network_dir=args.network_dir,
            service_date=args.service_date,
            access_radius_m=args.access_radius_m,
            walking_speed=args.walking_speed,
            transfer_buffer_s=args.transfer_buffer_s,
            overwrite=args.overwrite,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Could not create AIMSUM GTFS demand: {error}") from error
    print(f"Wrote {row_count} requests to {args.output_file}; rail paths found: {found_paths}.")


if __name__ == "__main__":
    main()
