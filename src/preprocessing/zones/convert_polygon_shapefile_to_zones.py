import argparse
import os
import sys

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

MAIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.append(MAIN_DIR)

from src.misc.globals import G_NODE_X, G_NODE_Y, G_ZONE_CEN, G_ZONE_NAME, G_ZONE_NID, G_ZONE_ZID


def _read_network_crs(network_base_dir):
    crs_f = os.path.join(network_base_dir, "crs.info")
    if os.path.isfile(crs_f):
        with open(crs_f) as fhin:
            return fhin.read().strip()
    nodes_geojson = os.path.join(network_base_dir, "nodes_all_infos.geojson")
    if os.path.isfile(nodes_geojson):
        return gpd.read_file(nodes_geojson).crs
    return None


def _prepare_zone_gdf(shapefile, network_crs, id_col, name_col):
    zones_gdf = gpd.read_file(shapefile)
    if zones_gdf.crs is None:
        zones_gdf = zones_gdf.set_crs(network_crs, allow_override=True)
    elif network_crs is not None and zones_gdf.crs != network_crs:
        zones_gdf = zones_gdf.to_crs(network_crs)

    zones_gdf = zones_gdf.reset_index(drop=True)
    if id_col is not None:
        zones_gdf[G_ZONE_ZID] = zones_gdf[id_col].astype(int)
    else:
        zones_gdf[G_ZONE_ZID] = zones_gdf.index.astype(int)

    if name_col is not None and name_col in zones_gdf.columns:
        zones_gdf[G_ZONE_NAME] = zones_gdf[name_col].astype(str)
    else:
        zones_gdf[G_ZONE_NAME] = zones_gdf[G_ZONE_ZID].astype(str)

    return zones_gdf


def convert_polygon_shapefile_to_zones(shapefile, zone_system_name, network_name, id_col="id", name_col="name"):
    """Convert a polygon shapefile into FleetPy's data/zones structure."""
    network_base_dir = os.path.join(MAIN_DIR, "data", "networks", network_name, "base")
    nodes_f = os.path.join(network_base_dir, "nodes.csv")
    if not os.path.isfile(nodes_f):
        raise FileNotFoundError(f"Could not find network nodes file: {nodes_f}")

    network_crs = _read_network_crs(network_base_dir)
    zones_gdf = _prepare_zone_gdf(shapefile, network_crs, id_col, name_col)

    nodes_df = pd.read_csv(nodes_f)
    nodes_gdf = gpd.GeoDataFrame(
        nodes_df,
        geometry=gpd.points_from_xy(nodes_df[G_NODE_X], nodes_df[G_NODE_Y]),
        crs=network_crs,
    )

    matched = gpd.sjoin(
        nodes_gdf[[G_ZONE_NID, "geometry"]],
        zones_gdf[[G_ZONE_ZID, G_ZONE_NAME, "geometry"]],
        how="left",
        predicate="intersects",
    )
    node_zone_df = matched[[G_ZONE_NID, G_ZONE_ZID]].copy()
    node_zone_df[G_ZONE_ZID] = node_zone_df[G_ZONE_ZID].fillna(-1).astype(int)
    node_zone_df = node_zone_df.drop_duplicates().sort_values([G_ZONE_NID, G_ZONE_ZID])
    node_zone_df[G_ZONE_CEN] = 0

    centroid_node_ids = []
    for zone_id, zone_row in zones_gdf.set_index(G_ZONE_ZID).iterrows():
        zone_nodes = node_zone_df[node_zone_df[G_ZONE_ZID] == zone_id][G_ZONE_NID].to_list()
        if not zone_nodes:
            continue
        centroid = zone_row.geometry.centroid
        candidate_nodes = nodes_df[nodes_df[G_ZONE_NID].isin(zone_nodes)].copy()
        candidate_nodes["distance_to_zone_centroid"] = candidate_nodes.apply(
            lambda row: Point(row[G_NODE_X], row[G_NODE_Y]).distance(centroid), axis=1
        )
        centroid_node_ids.append(candidate_nodes["distance_to_zone_centroid"].idxmin())

    if centroid_node_ids:
        centroid_nodes = nodes_df.loc[centroid_node_ids, G_ZONE_NID].to_list()
        node_zone_df.loc[node_zone_df[G_ZONE_NID].isin(centroid_nodes), G_ZONE_CEN] = 1

    zone_output_dir = os.path.join(MAIN_DIR, "data", "zones", zone_system_name)
    zone_network_dir = os.path.join(zone_output_dir, network_name)
    os.makedirs(zone_network_dir, exist_ok=True)

    general_info_df = zones_gdf[[G_ZONE_ZID, G_ZONE_NAME]].drop_duplicates().sort_values(G_ZONE_ZID)
    general_info_df.to_csv(os.path.join(zone_output_dir, "general_information.csv"), index=False)
    zones_gdf.to_file(os.path.join(zone_output_dir, "polygon_definition.geojson"), driver="GeoJSON")
    node_zone_df.to_csv(os.path.join(zone_network_dir, "node_zone_info.csv"), index=False)

    print(f"Wrote zone system: {zone_system_name}")
    print(f"  {os.path.join(zone_output_dir, 'general_information.csv')}")
    print(f"  {os.path.join(zone_output_dir, 'polygon_definition.geojson')}")
    print(f"  {os.path.join(zone_network_dir, 'node_zone_info.csv')}")
    print(f"Matched nodes: {(node_zone_df[G_ZONE_ZID] != -1).sum()} / {len(nodes_df)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert polygon shapefiles to FleetPy zone-system files.")
    parser.add_argument("shapefile")
    parser.add_argument("zone_system_name")
    parser.add_argument("network_name")
    parser.add_argument("--id-col", default="id")
    parser.add_argument("--name-col", default="name")
    args = parser.parse_args()
    convert_polygon_shapefile_to_zones(
        args.shapefile,
        args.zone_system_name,
        args.network_name,
        id_col=args.id_col,
        name_col=args.name_col,
    )
