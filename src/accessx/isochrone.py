# accessx/isochrone.py
from __future__ import annotations

from typing import  Literal, Optional, Union
from pathlib import Path

import geopandas as gpd
import pandas as pd
import networkx as nx
import osmnx as ox
from shapely.geometry import Point, LineString, Polygon

from pathlib import Path
from typing import Optional, Union


# assumes you already have:
# - find_nearest_node_within_distance(G, hex_pt, max_distance, city_epsg)

from pathlib import Path
from typing import Optional, Union, List

import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
from shapely.geometry import Point, LineString, Polygon


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





def _make_thresholds(max_cost: float, step: int) -> List[float]:
    """
    If step == 1, emit every 1 unit up to max_cost (e.g., max_cost=5 -> [1,2,3,4,5]).
    Otherwise, treat step as a count of thresholds:
    Example: max_cost=15, step=3 -> [5, 10, 15]
    """
    if step <= 0:
        raise ValueError("step must be a positive integer.")
    max_cost_f = float(max_cost)
    if max_cost_f <= 0:
        raise ValueError("max_cost must be > 0.")

    # Special case: step=1 means increment of 1 unit
    if step == 1:
        end = int(max_cost_f)
        vals = list(range(1, end + 1))
        if max_cost_f > end:
            vals.append(max_cost_f)
        return vals

    vals = [(i + 1) * (max_cost_f / step) for i in range(step)]
    # make last one exactly max_cost
    vals[-1] = max_cost_f
    # if values are integers, keep them clean (5.0 -> 5)
    out = []
    for v in vals:
        out.append(int(round(v)) if abs(v - round(v)) < 1e-9 else float(v))
    return out


def calculate_isochrones(
    G: nx.MultiDiGraph,
    hexes: gpd.GeoDataFrame,
    *,
    max_cost: Union[int, float],
    step: int,
    cost_attr: str,
    city_epsg: Union[int, str],
    id_col: str = "hex_id",
    max_distance: float = 200.0,
    method: str = "edges",   # "edges" or "hull"
    edge_buff: float = 25.0,
    infill: bool = False,
    save_dir: Optional[Union[str, Path]] = None,
    base_name: str = "walksheds",
) -> gpd.GeoDataFrame:
    """
    Efficient isochrone polygons:
    - For each hex: run one Dijkstra up to max_cost
    - Slice reachable nodes at thresholds derived from `step`
    - Build polygon per threshold using method="edges" or "hull"
    - Return a wide GeoDataFrame with one row per hex and one geom column per threshold.

    Parameters
    ----------
    G : nx.MultiDiGraph
        Projected graph (meters). Must have edge weight `cost_attr`.
    hexes : gpd.GeoDataFrame
        Hex polygons or points.
    max_cost : float
        Maximum travel cost cutoff (e.g., 15).
    step : int
        Number of thresholds to output. Example: max_cost=15, step=3 -> 5,10,15.
    cost_attr : str
        Edge weight attribute used for routing.
    method : {"edges","hull"}
        Polygon building method.
    save_dir : optional
        If provided, saves one CSV per threshold (WKT geometry columns).

    Returns
    -------
    geopandas.GeoDataFrame
        Columns: [hex_id, geometry, geom_{cost_attr}_{thr1}, ..., geom_{cost_attr}_{thrN}]
        geometry is the original hex geometry (active).
    """
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if G.graph.get("crs") is None:
        raise ValueError("Graph has no CRS in G.graph['crs']. Project graph to a metric CRS first.")

    max_cost_f = float(max_cost)
    thresholds = _make_thresholds(max_cost_f, step)

    # prepare saving
    save_dir_path = None
    if save_dir is not None:
        save_dir_path = Path(save_dir)
        save_dir_path.mkdir(parents=True, exist_ok=True)

    # base output: keep hex geometry as active geometry
    gdf_res = hexes[[id_col, "geometry"]].copy()
    gdf_res = gpd.GeoDataFrame(gdf_res, geometry="geometry", crs=hexes.crs)

    # prepare per-threshold storage: dict threshold -> list of (hex_id, polygon)
    per_thr = {}
    colnames = {}
    for t in thresholds:
        thr_str = str(t).replace(".", "p")
        col = f"geom_{cost_attr}_{thr_str}"
        per_thr[t] = []
        colnames[t] = col

    # loop hexes once, run Dijkstra once per hex
    for _, hex in hexes.iterrows():
        hex_geom = hex.geometry
        hex_id = hex[id_col]
        hex_pt = hex_geom if hex_geom.geom_type == "Point" else hex_geom.centroid

        source_node = find_nearest_node_within_distance(G, hex_pt, max_distance, city_epsg)
        if source_node is None:
            # no node snapped -> store None for all thresholds
            for t in thresholds:
                per_thr[t].append({id_col: hex_id, colnames[t]: None})
            continue

        lengths = nx.single_source_dijkstra_path_length(
            G,
            source_node,
            cutoff=max_cost_f,
            weight=cost_attr,
        )

        if not lengths:
            for t in thresholds:
                per_thr[t].append({id_col: hex_id, colnames[t]: None})
            continue

        # compute polygons for each threshold by slicing lengths
        for t in thresholds:
            nodes_reachable = [n for n, c in lengths.items() if c <= float(t)]
            if not nodes_reachable:
                per_thr[t].append({id_col: hex_id, colnames[t]: None})
                continue

            if method == "hull":
                node_points = [Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes_reachable]
                geom = gpd.GeoSeries(node_points).unary_union.convex_hull

            elif method == "edges":
                subgraph = G.subgraph(nodes_reachable)
                edge_lines = []
                for n_fr, n_to in subgraph.edges():
                    f = Point(subgraph.nodes[n_fr]["x"], subgraph.nodes[n_fr]["y"])
                    tpt = Point(subgraph.nodes[n_to]["x"], subgraph.nodes[n_to]["y"])
                    edge_lines.append(LineString([f, tpt]))

                if not edge_lines:
                    per_thr[t].append({id_col: hex_id, colnames[t]: None})
                    continue

                e = gpd.GeoSeries(edge_lines).buffer(edge_buff).geometry
                geom = gpd.GeoSeries(list(e)).union_all()

                if infill:
                    try:
                        geom = Polygon(geom.exterior)
                    except Exception:
                        pass
            else:
                raise ValueError("method must be 'edges' or 'hull'")

            per_thr[t].append({id_col: hex_id, colnames[t]: geom})

    # merge each threshold column into gdf_res
    for t in thresholds:
        out_thr = pd.DataFrame(per_thr[t])  # columns: [hex_id, geom_col]
        gdf_res = gdf_res.merge(out_thr, on=id_col, how="left")

        # optional save per threshold as CSV with WKT
        if save_dir_path is not None:
            thr_str = str(t).replace(".", "p")
            fname = f"{base_name}_{method}_{cost_attr}_{thr_str}.csv"

            out_csv = gdf_res[[id_col, "geometry", colnames[t]]].copy()

            # convert geometry columns to WKT for CSV
            out_csv["geometry"] = out_csv["geometry"].apply(lambda g: g.wkt if g is not None else None)
            out_csv[colnames[t]] = out_csv[colnames[t]].apply(lambda g: g.wkt if g is not None else None)

            pd.DataFrame(out_csv).to_csv(save_dir_path / fname, index=False)

    return gdf_res
