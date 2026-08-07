"""Append the nearest AIMSUM-node information to a node CSV.

Run from the repository root:

    python src/preprocessing/networks/add_nearest_aimsum_node.py

The default output is a new CSV beside the input.  Existing files are never
replaced unless ``--overwrite`` is supplied.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_INPUT_FILE = SCRIPT_DIRECTORY / "node_trip_nonzero_euclidean.csv"
DEFAULT_AIMSUM_FILE = SCRIPT_DIRECTORY / "node_AIMSUM.csv"
DEFAULT_OUTPUT_FILE = SCRIPT_DIRECTORY / "node_trip_nonzero_euclidean_with_aimsum_index.csv"
SOURCE_COLUMNS = ("pos_x", "pos_y")
AIMSUM_COLUMNS = ("node_index", "pos_x", "pos_y")
APPENDED_COLUMNS = ("aimsum_pos_x", "aimsum_pos_y", "aimsum_node_index")


def detect_delimiter(file_path: Path) -> str:
    """Detect a common CSV delimiter without relying on an ambiguous sniffer."""
    with file_path.open("r", encoding="utf-8-sig", newline="") as input_file:
        sample = input_file.read(8192)
    if not sample:
        raise ValueError(f"Input file is empty: {file_path}")
    delimiters = (",", "\t", ";")
    delimiter = max(delimiters, key=sample.count)
    if sample.count(delimiter) == 0:
        raise ValueError(f"Could not detect a comma, tab, or semicolon delimiter in {file_path}")
    return delimiter


def coordinate(value: str | None, *, file_path: Path, row_number: int, column: str) -> float:
    """Parse one finite projected coordinate and identify malformed input precisely."""
    if value is None or not value.strip():
        raise ValueError(f"{file_path}: row {row_number} has an empty {column!r} value")
    try:
        number = float(value.strip())
    except ValueError as error:
        raise ValueError(
            f"{file_path}: row {row_number} has a non-numeric {column!r} value: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise ValueError(
            f"{file_path}: row {row_number} has a non-finite {column!r} value: {value!r}"
        )
    return number


def validate_header(fieldnames: list[str] | None, required_columns: Sequence[str], file_path: Path) -> None:
    if fieldnames is None:
        raise ValueError(f"Input file has no header row: {file_path}")
    missing_columns = [column for column in required_columns if column not in fieldnames]
    if missing_columns:
        raise ValueError(f"{file_path} is missing required column(s): {', '.join(missing_columns)}")


@dataclass(frozen=True)
class AimsunNode:
    """A reference node with raw values retained for lossless CSV output."""

    x: float
    y: float
    node_index: str
    x_text: str
    y_text: str
    order: int


@dataclass
class KdNode:
    """A two-dimensional k-d-tree node."""

    point: AimsunNode
    axis: int
    left: KdNode | None = None
    right: KdNode | None = None


def build_kd_tree(points: list[AimsunNode], depth: int = 0) -> KdNode | None:
    """Build a balanced tree so nearest-neighbour lookup stays sublinear."""
    if not points:
        return None
    axis = depth % 2
    points.sort(key=lambda point: ((point.x, point.y) if axis == 0 else (point.y, point.x), point.order))
    median = len(points) // 2
    return KdNode(
        point=points[median],
        axis=axis,
        left=build_kd_tree(points[:median], depth + 1),
        right=build_kd_tree(points[median + 1 :], depth + 1),
    )


def squared_distance(x: float, y: float, point: AimsunNode) -> float:
    return (x - point.x) ** 2 + (y - point.y) ** 2


def nearest_node(tree: KdNode, x: float, y: float) -> AimsunNode:
    """Return the exact nearest reference node; input order resolves equal distances."""
    best_point = tree.point
    best_distance = squared_distance(x, y, best_point)

    def visit(node: KdNode | None) -> None:
        nonlocal best_point, best_distance
        if node is None:
            return
        distance = squared_distance(x, y, node.point)
        if distance < best_distance or (distance == best_distance and node.point.order < best_point.order):
            best_point = node.point
            best_distance = distance

        difference = (x - node.point.x) if node.axis == 0 else (y - node.point.y)
        near_branch, far_branch = (node.left, node.right) if difference <= 0 else (node.right, node.left)
        visit(near_branch)
        if difference * difference <= best_distance:
            visit(far_branch)

    visit(tree)
    return best_point


def load_aimsun_nodes(aimsun_file: Path) -> KdNode:
    """Load AIMSUM reference nodes and index them for exact nearest-node searches."""
    delimiter = detect_delimiter(aimsun_file)
    points: list[AimsunNode] = []
    with aimsun_file.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file, delimiter=delimiter)
        validate_header(reader.fieldnames, AIMSUM_COLUMNS, aimsun_file)
        for row_number, row in enumerate(reader, start=2):
            points.append(
                AimsunNode(
                    x=coordinate(row["pos_x"], file_path=aimsun_file, row_number=row_number, column="pos_x"),
                    y=coordinate(row["pos_y"], file_path=aimsun_file, row_number=row_number, column="pos_y"),
                    node_index=row["node_index"],
                    x_text=row["pos_x"],
                    y_text=row["pos_y"],
                    order=len(points),
                )
            )
    tree = build_kd_tree(points)
    if tree is None:
        raise ValueError(f"No AIMSUM nodes found in {aimsun_file}")
    return tree


def append_nearest_aimsum_nodes(input_file: Path, aimsun_file: Path, output_file: Path, overwrite: bool) -> int:
    """Copy source rows and append their nearest AIMSUM-node coordinates and ID."""
    if output_file.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists: {output_file}. Use --overwrite to replace it.")

    tree = load_aimsun_nodes(aimsun_file)
    delimiter = detect_delimiter(input_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    row_count = 0
    try:
        with input_file.open("r", encoding="utf-8-sig", newline="") as input_handle, NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=output_file.parent, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            reader = csv.DictReader(input_handle, delimiter=delimiter)
            validate_header(reader.fieldnames, SOURCE_COLUMNS, input_file)
            assert reader.fieldnames is not None
            duplicate_columns = set(reader.fieldnames).intersection(APPENDED_COLUMNS)
            if duplicate_columns:
                raise ValueError(
                    f"{input_file} already contains output column(s): {', '.join(sorted(duplicate_columns))}"
                )
            writer = csv.DictWriter(temporary_file, fieldnames=[*reader.fieldnames, *APPENDED_COLUMNS])
            writer.writeheader()
            for row_number, row in enumerate(reader, start=2):
                x = coordinate(row["pos_x"], file_path=input_file, row_number=row_number, column="pos_x")
                y = coordinate(row["pos_y"], file_path=input_file, row_number=row_number, column="pos_y")
                nearest = nearest_node(tree, x, y)
                writer.writerow(
                    {
                        **row,
                        "aimsum_pos_x": nearest.x_text,
                        "aimsum_pos_y": nearest.y_text,
                        "aimsum_node_index": nearest.node_index,
                    }
                )
                row_count += 1
        os.replace(temporary_path, output_file)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return row_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append the nearest AIMSUM node's x, y, and node_index to every source node."
    )
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT_FILE, help=f"Source node CSV (default: {DEFAULT_INPUT_FILE}).")
    parser.add_argument("--aimsun-file", type=Path, default=DEFAULT_AIMSUM_FILE, help=f"AIMSUM node CSV (default: {DEFAULT_AIMSUM_FILE}).")
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE, help=f"New output CSV (default: {DEFAULT_OUTPUT_FILE}).")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacement of an existing output file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for label, path in (("Input", args.input_file), ("AIMSUM", args.aimsun_file)):
        if not path.is_file():
            raise SystemExit(f"{label} file does not exist or is not a file: {path}")
    try:
        row_count = append_nearest_aimsum_nodes(
            args.input_file, args.aimsun_file, args.output_file, args.overwrite
        )
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not append AIMSUM-node information: {error}") from error
    print(f"Wrote {row_count} rows to {args.output_file}.")


if __name__ == "__main__":
    main()
