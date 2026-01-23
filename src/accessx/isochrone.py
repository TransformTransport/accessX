# accessx/isochrone.py
from __future__ import annotations

from typing import Iterable, Literal, Optional, Union
from pathlib import Path

import geopandas as gpd
import pandas as pd
import networkx as nx
import osmnx as ox
from shapely.geometry import Point, LineString, Polygon
from accessx.io import save_gdf


Method = Literal["hull", "edges"]


def find_nearest_node_within_distance(G, hex, max_distance, city_epsg=None):
    """
    Find nearest node to a point geometry and return it only if within max_distance (meters).
    Note: city_epsg is kept for signature compatibility; distance check uses Euclidean on x/y.
    """
    source_node = ox.distance.nearest_nodes(G, hex.x, hex.y)
    nearest_node_coords = (G.nodes[source_node]["y"], G.nodes[source_node]["x"])
    distance = ox.distance.euclidean(hex.y, hex.x, *nearest_node_coords)
    return source_node if distance <= max_distance else None

    if distance <= max_distance:
        return source_node
    return None


def make_walksheds(
    G: nx.MultiDiGraph,
    hexes: gpd.GeoDataFrame,
    *,
    cost_thresholds: Iterable[Union[int, float]],
    cost_attr: str,
    city_epsg: Union[int, str],
    id_col: str = "hex_id",
    max_distance: float = 200.0,
    method: Method = "edges",
    edge_buff: float = 25.0,
    infill: bool = False,
    save_dir: Optional[Union[str, Path]] = None,
    base_name: str = "walksheds",
) -> gpd.GeoDataFrame:
    """
    Create walkshed polygons for each hex centroid for multiple cost thresholds.

    If save_dir is provided, saves ONE GeoJSON PER cost_threshold:
      {base_name}_{cost_attr}_{threshold}.geojson

    Returns one combined GeoDataFrame with all thresholds (handy for analysis),
    even if files are saved per threshold.
    """
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if G.graph.get("crs") is None:
        raise ValueError("Graph has no CRS in G.graph['crs']. Project graph to a metric CRS first.")

    # ensure point origins (centroids) without mutating input
    # if hexes.geom_type.iloc[0] != "Point":
    #     ctrd = hexes[[id_col, "geometry"]].copy()
    #     ctrd["geometry"] = ctrd.geometry.centroid
    #     ctrd = gpd.GeoDataFrame(ctrd, geometry="geometry", crs=hexes.crs)
    # else:
    #     ctrd = hexes[[id_col, "geometry"]].copy()

    # # try to align CRS with graph CRS
    # try:
    #     ctrd = ctrd.to_crs(G.graph["crs"])
    # except Exception:
    #     pass

    # prepare saving
    save_dir_path = None
    if save_dir is not None:
        save_dir_path = Path(save_dir)
        save_dir_path.mkdir(parents=True, exist_ok=True)

    gdf_res = hexes[[id_col, "geometry"]].copy()
    gdf_res = gpd.GeoDataFrame(gdf_res, geometry="geometry", crs=hexes.crs)

    for trip_time in cost_thresholds:
        rows = []  # rows for this threshold (so we can save per-threshold)
        trip_time_f = float(trip_time)
        thr_str = str(trip_time).replace(".", "p")   # e.g. 5 -> "5", 12.5 -> "12p5"
        geom_col = f"geom_{thr_str}"
        
        for _, hex in hexes.iterrows():
            hex_geom = hex.geometry
            hex_id = hex[id_col]
            # use point directly if already point, otherwise use centroid on-the-fly
            hex_pt = hex_geom if hex_geom.geom_type == "Point" else hex_geom.centroid

            source_node = find_nearest_node_within_distance(G, hex_pt, max_distance, city_epsg)
            if source_node is None:
                continue

            subgraph = nx.ego_graph(G, source_node, radius=trip_time_f, distance=cost_attr)
            if subgraph.number_of_nodes() == 0:
                continue

            if method == "hull":
                node_points = [Point(d["x"], d["y"]) for _, d in subgraph.nodes(data=True)]
                geom = gpd.GeoSeries(node_points).unary_union.convex_hull

            elif method == "edges":
                edge_lines = []
                for n_fr, n_to in subgraph.edges():
                    f = Point(subgraph.nodes[n_fr]["x"], subgraph.nodes[n_fr]["y"])
                    t = Point(subgraph.nodes[n_to]["x"], subgraph.nodes[n_to]["y"])
                    edge_lines.append(LineString([f, t]))

                if not edge_lines:
                    continue

                e = gpd.GeoSeries(edge_lines).buffer(edge_buff).geometry
                geom = gpd.GeoSeries(list(e)).union_all()

                if infill:
                    try:
                        geom = Polygon(geom.exterior)
                    except Exception:
                        pass
            else:
                raise ValueError("method must be 'hull' or 'edges'")

            # rec = {id_col: hex_id[id_col], "geometry": geometry, "geom_" + str(trip_time_f): geom}
            rec = {
                id_col: hex_id,
                "geometry": hex_geom,   # main hex geometry
                geom_col: geom,     # walkshed geometry for this threshold
            }
            rows.append(rec)
        
        out_thr = gpd.GeoDataFrame(rows, geometry="geometry", crs=hexes.crs)
            # merge only the new walkshed column into the wide table
        gdf_res = gdf_res.merge(out_thr[[id_col, geom_col]], on=id_col, how="left",)

        # # save one file per threshold
        # if save_dir_path is not None:
        #     # nice filename: keep threshold readable (e.g., 5, 10, 15, 12.5)
        #     fname = f"{base_name}_{cost_attr}_{thr_str}.csv"
        #     save_gdf(out_thr, save_dir_path / fname)
        
        if save_dir_path is not None:
            fname = f"{base_name}_{method}_{cost_attr}_{thr_str}.csv"
            out_csv = out_thr.copy()

            # convert ALL geometry-like columns to WKT
            for col in out_csv.columns:
                if out_csv[col].dtype == "geometry" or col.startswith("geom_"):
                    out_csv[col] = out_csv[col].apply(
                        lambda g: g.wkt if g is not None else None
                    )

            # drop GeoDataFrame behavior → plain DataFrame
            out_csv = pd.DataFrame(out_csv)

            out_csv.to_csv(save_dir_path / fname, index=False)


    return gdf_res
