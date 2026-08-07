"""Keep point features that fall within a GeoJSON zone layer.

The default files are the Upper Bavaria node GeoJSON and the Munich
municipalities zone definition.  The node file stores its geometry in
EPSG:3857, while its ``pos_x``/``pos_y`` properties use EPSG:32632, which is
also the zone layer's CRS.  Therefore the script uses those properties by
default and preserves the original point geometry in the output.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POINTS_PATH = Path(__file__).with_name("node_Oberbayern.geojson")
DEFAULT_ZONE_PATH = (
    REPOSITORY_ROOT
    / "data"
    / "zones"
    / "Munich_Municipalities"
    / "polygon_definition.geojson"
)
DEFAULT_OUTPUT_PATH = Path(__file__).with_name(
    "node_upperbavaria_inside_munich_municipalities.geojson"
)

Point = tuple[float, float]
Ring = Sequence[Sequence[float]]
Polygon = tuple[tuple[float, float, float, float], Ring, tuple[Ring, ...]]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Remove GeoJSON point features outside a polygon zone layer."
    )
    parser.add_argument(
        "--points",
        type=Path,
        default=DEFAULT_POINTS_PATH,
        help="Input point GeoJSON (default: node_Oberbayern.geojson).",
    )
    parser.add_argument(
        "--zone",
        type=Path,
        default=DEFAULT_ZONE_PATH,
        help="Polygon or MultiPolygon GeoJSON defining the permitted area.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Filtered GeoJSON destination.",
    )
    parser.add_argument(
        "--x-property",
        default="pos_x",
        help="Feature property holding the x coordinate (default: pos_x).",
    )
    parser.add_argument(
        "--y-property",
        default="pos_y",
        help="Feature property holding the y coordinate (default: pos_y).",
    )
    parser.add_argument(
        "--geometry-coordinates",
        action="store_true",
        help=(
            "Use Point geometry coordinates instead of feature properties. This is "
            "only valid when the point and zone GeoJSON files use the same CRS."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing output file.",
    )
    return parser.parse_args()


def load_feature_collection(path: Path) -> dict[str, Any]:
    """Read and validate a GeoJSON FeatureCollection."""
    try:
        with path.open(encoding="utf-8") as file_handle:
            data = json.load(file_handle)
    except FileNotFoundError as error:
        raise ValueError(f"GeoJSON file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error

    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError(f"{path} must be a GeoJSON FeatureCollection.")
    return data


def ring_bounds(ring: Ring) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) for a non-empty coordinate ring."""
    if len(ring) < 4:
        raise ValueError("Polygon rings must contain at least four coordinates.")
    try:
        coordinates = [(float(point[0]), float(point[1])) for point in ring]
    except (IndexError, TypeError, ValueError) as error:
        raise ValueError("Polygon coordinates must contain numeric x and y values.") from error

    x_values, y_values = zip(*coordinates)
    return min(x_values), min(y_values), max(x_values), max(y_values)


def polygons_from_geometry(geometry: dict[str, Any]) -> Iterable[tuple[Ring, tuple[Ring, ...]]]:
    """Yield exterior and interior rings from Polygon or MultiPolygon geometry."""
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        if not coordinates:
            return
        yield coordinates[0], tuple(coordinates[1:])
    elif geometry_type == "MultiPolygon":
        for polygon_coordinates in coordinates or []:
            if polygon_coordinates:
                yield polygon_coordinates[0], tuple(polygon_coordinates[1:])
    else:
        raise ValueError(
            "Zone features must use Polygon or MultiPolygon geometry; "
            f"received {geometry_type!r}."
        )


def load_polygons(zone_collection: dict[str, Any]) -> list[Polygon]:
    """Convert all zone features to polygons with inexpensive bounding boxes."""
    polygons: list[Polygon] = []
    for feature_index, feature in enumerate(zone_collection["features"]):
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError(f"Zone feature {feature_index} has no geometry.")
        for exterior, holes in polygons_from_geometry(geometry):
            polygons.append((ring_bounds(exterior), exterior, holes))

    if not polygons:
        raise ValueError("The zone GeoJSON contains no polygon geometry.")
    return polygons


def point_on_segment(point: Point, start: Sequence[float], end: Sequence[float]) -> bool:
    """Return whether a point lies on a segment, with a scale-aware tolerance."""
    px, py = point
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    cross_product = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    segment_length = math.hypot(x2 - x1, y2 - y1)
    tolerance = 1e-9 * max(1.0, segment_length)
    if not math.isclose(cross_product, 0.0, abs_tol=tolerance):
        return False
    return (
        min(x1, x2) - tolerance <= px <= max(x1, x2) + tolerance
        and min(y1, y2) - tolerance <= py <= max(y1, y2) + tolerance
    )


def point_in_ring(point: Point, ring: Ring) -> bool:
    """Test a point against a linear ring, counting its boundary as inside."""
    point_x, point_y = point
    inside = False
    previous = ring[-1]
    for current in ring:
        if point_on_segment(point, previous, current):
            return True
        current_x, current_y = float(current[0]), float(current[1])
        previous_x, previous_y = float(previous[0]), float(previous[1])
        crosses_horizontal_ray = (current_y > point_y) != (previous_y > point_y)
        if crosses_horizontal_ray:
            intersection_x = (
                (previous_x - current_x) * (point_y - current_y) / (previous_y - current_y)
                + current_x
            )
            if point_x < intersection_x:
                inside = not inside
        previous = current
    return inside


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    """Return whether a point is in a polygon, including its exterior boundary."""
    min_x, min_y, max_x, max_y = polygon[0]
    point_x, point_y = point
    if not (min_x <= point_x <= max_x and min_y <= point_y <= max_y):
        return False
    exterior, holes = polygon[1], polygon[2]
    if not point_in_ring(point, exterior):
        return False
    return not any(point_in_ring(point, hole) for hole in holes)


def feature_point(
    feature: dict[str, Any],
    feature_index: int,
    args: argparse.Namespace,
) -> Point:
    """Extract the coordinate used for the zone membership test."""
    try:
        if args.geometry_coordinates:
            geometry = feature["geometry"]
            if geometry["type"] != "Point":
                raise ValueError("geometry is not a Point")
            x_value, y_value = geometry["coordinates"][:2]
        else:
            properties = feature["properties"]
            x_value, y_value = properties[args.x_property], properties[args.y_property]
        point = float(x_value), float(y_value)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        source = "geometry coordinates" if args.geometry_coordinates else (
            f"properties {args.x_property!r} and {args.y_property!r}"
        )
        raise ValueError(f"Point feature {feature_index} has invalid {source}.") from error

    if not all(math.isfinite(value) for value in point):
        raise ValueError(f"Point feature {feature_index} has non-finite coordinates.")
    return point


def crs_name(feature_collection: dict[str, Any]) -> str | None:
    """Return the legacy GeoJSON CRS name when supplied."""
    crs = feature_collection.get("crs")
    if not isinstance(crs, dict):
        return None
    properties = crs.get("properties")
    return properties.get("name") if isinstance(properties, dict) else None


def write_feature_collection(path: Path, collection: dict[str, Any], overwrite: bool) -> None:
    """Write JSON atomically and do not replace an existing file by default."""
    if path.exists() and not overwrite:
        raise ValueError(f"Output already exists: {path}. Pass --overwrite to replace it.")
    if not path.parent.exists():
        raise ValueError(f"Output directory does not exist: {path.parent}")

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        json.dump(collection, temporary_file, ensure_ascii=False, separators=(",", ":"))
        temporary_file.write("\n")
    try:
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> None:
    """Filter the point collection and print a concise processing summary."""
    args = parse_args()
    point_collection = load_feature_collection(args.points)
    zone_collection = load_feature_collection(args.zone)
    if args.geometry_coordinates and crs_name(point_collection) != crs_name(zone_collection):
        raise ValueError(
            "Point and zone GeoJSON CRS values differ. Use matching coordinate "
            "properties (the defaults are pos_x/pos_y for the supplied node file), "
            "or reproject the points before using --geometry-coordinates."
        )

    polygons = load_polygons(zone_collection)
    kept_features = []
    for feature_index, feature in enumerate(point_collection["features"]):
        point = feature_point(feature, feature_index, args)
        if any(point_in_polygon(point, polygon) for polygon in polygons):
            kept_features.append(feature)

    filtered_collection = dict(point_collection)
    filtered_collection["features"] = kept_features
    write_feature_collection(args.output, filtered_collection, args.overwrite)
    removed_count = len(point_collection["features"]) - len(kept_features)
    print(
        f"Wrote {len(kept_features)} retained point features to {args.output} "
        f"({removed_count} removed)."
    )


if __name__ == "__main__":
    main()
