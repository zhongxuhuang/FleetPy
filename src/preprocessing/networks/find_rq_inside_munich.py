"""Keep trip requests whose origin and destination are Munich network nodes.

Run from the repository root, for example:

    python src/preprocessing/networks/find_rq_inside_munich.py path/to/trips.csv

The default output is ``src/preprocessing/networks/rq_muechen.csv``. Pass
``--node-trip-file`` to also write a unique endpoint-node table.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_NODE_FILE = SCRIPT_DIRECTORY / "node_info_muechen.csv"
DEFAULT_OUTPUT_FILE = SCRIPT_DIRECTORY / "rq_muechen.csv"
DEFAULT_COORDINATE_TOLERANCE_METERS = Decimal("0.000001")
TRIP_COLUMNS = (
    "origin_x",
    "origin_y",
    "destination_x",
    "destination_y",
    "departure_time",
    "euclidean_distance",
)
NODE_COLUMNS = ("pos_x", "pos_y")
OUTPUT_PREFIX_COLUMNS = ("request_id", "rq_time")
NODE_TRIP_COLUMNS = ("node_index", "is_stop_only", "source_node_id", "pos_x", "pos_y")


def detect_delimiter(file_path: Path) -> str:
    """Detect the delimiter used by a CSV exported from a spreadsheet."""
    with file_path.open("r", encoding="utf-8-sig", newline="") as input_file:
        sample = input_file.read(8192)
    if not sample:
        raise ValueError(f"Input file is empty: {file_path}")

    delimiters = (",", "\t", ";")
    delimiter = max(delimiters, key=sample.count)
    if sample.count(delimiter) == 0:
        raise ValueError(
            f"Could not detect a comma, tab, or semicolon delimiter in {file_path}"
        )
    return delimiter


def decimal_value(value: str | None, *, file_path: Path, row_number: int, column: str) -> Decimal:
    """Return a finite decimal, with a useful error for malformed coordinates/times."""
    if value is None or not value.strip():
        raise ValueError(f"{file_path}: row {row_number} has an empty {column!r} value")
    try:
        number = Decimal(value.strip())
    except InvalidOperation as error:
        raise ValueError(
            f"{file_path}: row {row_number} has a non-numeric {column!r} value: {value!r}"
        ) from error
    if not number.is_finite():
        raise ValueError(
            f"{file_path}: row {row_number} has a non-finite {column!r} value: {value!r}"
        )
    return number


class CoordinateMatcher:
    """Look up reference coordinates within a small projected-coordinate tolerance."""

    def __init__(self, coordinates: list[tuple[Decimal, Decimal]], tolerance: Decimal) -> None:
        self.tolerance = tolerance
        self._grid: dict[tuple[int, int], list[tuple[Decimal, Decimal]]] = defaultdict(list)
        for coordinate in coordinates:
            self._grid[self._grid_key(*coordinate)].append(coordinate)

    def _grid_key(self, x: Decimal, y: Decimal) -> tuple[int, int]:
        return int(x // self.tolerance), int(y // self.tolerance)

    def __len__(self) -> int:
        return sum(len(coordinates) for coordinates in self._grid.values())

    def contains(self, x: Decimal, y: Decimal) -> bool:
        grid_x, grid_y = self._grid_key(x, y)
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for reference_x, reference_y in self._grid.get(
                    (grid_x + x_offset, grid_y + y_offset), ()
                ):
                    if (
                        abs(x - reference_x) <= self.tolerance
                        and abs(y - reference_y) <= self.tolerance
                    ):
                        return True
        return False


def validate_header(fieldnames: list[str] | None, required_columns: Iterable[str], file_path: Path) -> None:
    if fieldnames is None:
        raise ValueError(f"Input file has no header row: {file_path}")
    missing_columns = [column for column in required_columns if column not in fieldnames]
    if missing_columns:
        raise ValueError(
            f"{file_path} is missing required column(s): {', '.join(missing_columns)}"
        )


def load_node_coordinates(node_file: Path, tolerance: Decimal) -> CoordinateMatcher:
    delimiter = detect_delimiter(node_file)
    node_coordinates: list[tuple[Decimal, Decimal]] = []
    with node_file.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file, delimiter=delimiter)
        validate_header(reader.fieldnames, NODE_COLUMNS, node_file)
        for row_number, row in enumerate(reader, start=2):
            node_coordinates.append(
                (
                    decimal_value(row["pos_x"], file_path=node_file, row_number=row_number, column="pos_x"),
                    decimal_value(row["pos_y"], file_path=node_file, row_number=row_number, column="pos_y"),
                )
            )
    if not node_coordinates:
        raise ValueError(f"No node coordinates found in {node_file}")
    return CoordinateMatcher(node_coordinates, tolerance)


def collect_matching_requests(
    trip_file: Path, node_coordinates: CoordinateMatcher
) -> tuple[list[dict[str, str]], int, tuple[str, ...]]:
    delimiter = detect_delimiter(trip_file)
    matching_requests: list[tuple[Decimal, int, dict[str, str]]] = []
    total_rows = 0
    with trip_file.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file, delimiter=delimiter)
        validate_header(reader.fieldnames, TRIP_COLUMNS, trip_file)
        assert reader.fieldnames is not None
        output_columns = OUTPUT_PREFIX_COLUMNS + tuple(
            column for column in reader.fieldnames if column not in OUTPUT_PREFIX_COLUMNS
        )
        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            origin = (
                decimal_value(row["origin_x"], file_path=trip_file, row_number=row_number, column="origin_x"),
                decimal_value(row["origin_y"], file_path=trip_file, row_number=row_number, column="origin_y"),
            )
            destination = (
                decimal_value(row["destination_x"], file_path=trip_file, row_number=row_number, column="destination_x"),
                decimal_value(row["destination_y"], file_path=trip_file, row_number=row_number, column="destination_y"),
            )
            departure_time = decimal_value(
                row["departure_time"],
                file_path=trip_file,
                row_number=row_number,
                column="departure_time",
            )
            euclidean_distance = decimal_value(
                row["euclidean_distance"],
                file_path=trip_file,
                row_number=row_number,
                column="euclidean_distance",
            )
            if euclidean_distance == 0:
                continue
            if node_coordinates.contains(*origin) and node_coordinates.contains(*destination):
                matching_requests.append(
                    (
                        departure_time,
                        row_number,
                        {
                            **row,
                            "rq_time": row["departure_time"],
                        },
                    )
                )

    matching_requests.sort(key=lambda request: (request[0], request[1]))
    output_rows: list[dict[str, str]] = []
    for request_id, (_, _, request) in enumerate(matching_requests):
        output_rows.append({"request_id": str(request_id), **request})
    return output_rows, total_rows, output_columns


def collect_trip_nodes(trip_rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Return one node row for each unique retained trip endpoint."""
    unique_coordinates: dict[tuple[str, str], None] = {}
    for trip_row in trip_rows:
        for x_column, y_column in (("origin_x", "origin_y"), ("destination_x", "destination_y")):
            unique_coordinates.setdefault((trip_row[x_column], trip_row[y_column]), None)

    return [
        {
            "node_index": str(node_index),
            "is_stop_only": "False",
            "source_node_id": "",
            "pos_x": x_value,
            "pos_y": y_value,
        }
        for node_index, (x_value, y_value) in enumerate(unique_coordinates)
    ]


def write_output(
    output_file: Path, rows: list[dict[str, str]], output_columns: tuple[str, ...], overwrite: bool
) -> None:
    if output_file.exists() and not overwrite:
        raise FileExistsError(
            f"Output file already exists: {output_file}. Use --overwrite to replace it."
        )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=output_file.parent, delete=False
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        writer = csv.DictWriter(temporary_file, fieldnames=output_columns)
        writer.writeheader()
        writer.writerows(rows)
    try:
        os.replace(temporary_path, output_file)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trip_file", type=Path, help="Trip CSV containing the origin/destination coordinates.")
    parser.add_argument(
        "--node-file",
        type=Path,
        default=DEFAULT_NODE_FILE,
        help=f"Reference node CSV (default: {DEFAULT_NODE_FILE}).",
    )
    parser.add_argument(
        "--coordinate-tolerance-m",
        type=Decimal,
        default=DEFAULT_COORDINATE_TOLERANCE_METERS,
        help=(
            "Maximum absolute x/y coordinate difference in metres when matching a reference node "
            f"(default: {DEFAULT_COORDINATE_TOLERANCE_METERS})."
        ),
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Filtered request CSV (default: {DEFAULT_OUTPUT_FILE}).",
    )
    parser.add_argument(
        "--node-trip-file",
        type=Path,
        help="Optional CSV destination for unique retained trip endpoint nodes.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file.")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    if not arguments.trip_file.is_file():
        raise FileNotFoundError(f"Trip input file does not exist: {arguments.trip_file}")
    if not arguments.node_file.is_file():
        raise FileNotFoundError(f"Node reference file does not exist: {arguments.node_file}")
    if not arguments.coordinate_tolerance_m.is_finite() or arguments.coordinate_tolerance_m <= 0:
        raise ValueError("--coordinate-tolerance-m must be a finite value greater than zero")
    if (
        arguments.node_trip_file is not None
        and arguments.output_file.resolve() == arguments.node_trip_file.resolve()
    ):
        raise ValueError("--output-file and --node-trip-file must refer to different files")

    node_coordinates = load_node_coordinates(arguments.node_file, arguments.coordinate_tolerance_m)
    output_rows, total_rows, output_columns = collect_matching_requests(arguments.trip_file, node_coordinates)
    write_output(arguments.output_file, output_rows, output_columns, arguments.overwrite)
    print(
        f"Wrote {len(output_rows)} of {total_rows} trip rows to {arguments.output_file} "
        f"using {len(node_coordinates)} Munich node coordinate pairs."
    )
    if arguments.node_trip_file is not None:
        node_rows = collect_trip_nodes(output_rows)
        write_output(arguments.node_trip_file, node_rows, NODE_TRIP_COLUMNS, arguments.overwrite)
        print(f"Wrote {len(node_rows)} unique trip endpoint nodes to {arguments.node_trip_file}.")


if __name__ == "__main__":
    main()
