"""Create a unique node table from origin and destination coordinates in a trip CSV."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


REQUIRED_COLUMNS = ("origin_x", "origin_y", "destination_x", "destination_y")
OUTPUT_COLUMNS = ("node_index", "is_stop_only", "source_node_id", "pos_x", "pos_y")
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().with_name("node_info.csv")


def _coordinate(value: str | None, column_name: str, row_number: int) -> Decimal:
    """Validate and parse one coordinate without changing its input spelling."""
    if value is None or not value.strip():
        raise ValueError(f"Row {row_number}: {column_name} is empty.")
    try:
        coordinate = Decimal(value.strip())
    except InvalidOperation as error:
        raise ValueError(
            f"Row {row_number}: {column_name} is not a valid coordinate: {value!r}."
        ) from error
    if not coordinate.is_finite():
        raise ValueError(f"Row {row_number}: {column_name} must be finite.")
    return coordinate


def _deduplication_coordinate(value: str, column_name: str, row_number: int) -> str:
    """Validate one coordinate and return its exact raw text for comparison."""
    _coordinate(value, column_name, row_number)
    return value


def _read_trip_rows(input_path: Path) -> Iterable[dict[str, str]]:
    """Read a CSV exported with a comma or another common CSV delimiter."""
    with input_path.open("r", encoding="utf-8-sig", newline="") as input_file:
        sample = input_file.read(4096)
        input_file.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(input_file, dialect=dialect)
        missing_columns = set(REQUIRED_COLUMNS).difference(reader.fieldnames or ())
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Input file is missing required column(s): {missing}.")
        yield from reader


def create_node_info(input_path: Path, output_path: Path) -> int:
    """Write one row per unique origin/destination coordinate pair."""
    unique_coordinates: dict[tuple[str, str], tuple[str, str]] = {}

    for row_number, row in enumerate(_read_trip_rows(input_path), start=2):
        for x_column, y_column in (("origin_x", "origin_y"), ("destination_x", "destination_y")):
            x_value = row[x_column]
            y_value = row[y_column]
            coordinate_key = (
                _deduplication_coordinate(x_value, x_column, row_number),
                _deduplication_coordinate(y_value, y_column, row_number),
            )
            unique_coordinates.setdefault(coordinate_key, (x_value, y_value))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for node_index, (x_text, y_text) in enumerate(unique_coordinates.values()):
            writer.writerow(
                {
                    "node_index": node_index,
                    "is_stop_only": "False",
                    "source_node_id": "",
                    "pos_x": x_text,
                    "pos_y": y_text,
                }
            )
    return len(unique_coordinates)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create node_info.csv from unique origin and destination coordinates."
    )
    parser.add_argument("input_csv", type=Path, help="Trip CSV containing origin_x/y and destination_x/y.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input_csv.is_file():
        raise SystemExit(f"Input CSV does not exist or is not a file: {args.input_csv}")
    try:
        node_count = create_node_info(args.input_csv, args.output)
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not create node info: {error}") from error
    print(f"Wrote {node_count} unique nodes to {args.output}.")


if __name__ == "__main__":
    main()
