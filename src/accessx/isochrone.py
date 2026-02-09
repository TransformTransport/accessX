# accessx/isochrone.py
from __future__ import annotations

from typing import List, Literal, Optional, Union
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import geopandas as gpd
import pandas as pd
import networkx as nx
import osmnx as ox
from shapely.geometry import Point, LineString, Polygon


Method = Literal["hull", "edges"]


def find_nearest_node_within_distance(G, hex, max_distance):
    """
    Find nearest node to a point geometry and return it only if within max_distance (meters).
    """
    source_node = ox.distance.nearest_nodes(G, hex.x, hex.y)
    nearest_node_coords = (G.nodes[source_node]["y"], G.nodes[source_node]["x"])
    distance = ox.distance.euclidean(hex.y, hex.x, *nearest_node_coords)
    return source_node if distance <= max_distance else None





def _make_thresholds(max_cost: float, interval_size: Union[int, float]) -> List[float]:
    """
    Treat step as an interval size.
    Example: max_cost=15, step=3 -> [3, 6, 9, 12, 15]
    Always includes max_cost as the last threshold.
    """
    if interval_size <= 0:
        raise ValueError("interval_size must be a positive number.")
    max_cost_f = float(max_cost)
    if max_cost_f <= 0:
        raise ValueError("max_cost must be > 0.")

    step_f = float(interval_size)
    vals = []
    cur = step_f
    while cur < max_cost_f:
        vals.append(cur)
        cur += step_f
    vals.append(max_cost_f)

    # if values are integers, keep them clean (5.0 -> 5)
    out = []
    for v in vals:
        out.append(int(round(v)) if abs(v - round(v)) < 1e-9 else float(v))
    return out


def _format_threshold(t: float) -> str:
    return f"{t:g}".replace(".", "p")

_WORKER_STATE = {}


def _init_worker(state: dict) -> None:
    global _WORKER_STATE
    _WORKER_STATE = state


def _process_hex_worker(item):
    hex_id, hex_geom = item
    G = _WORKER_STATE["G"]
    thresholds = _WORKER_STATE["thresholds"]
    colnames = _WORKER_STATE["colnames"]
    cost_attr = _WORKER_STATE["cost_attr"]
    method = _WORKER_STATE["method"]
    edge_buff = _WORKER_STATE["edge_buff"]
    infill = _WORKER_STATE["infill"]
    max_distance = _WORKER_STATE["max_distance"]
    id_col = _WORKER_STATE["id_col"]

    hex_pt = hex_geom if hex_geom.geom_type == "Point" else hex_geom.centroid

    source_node = find_nearest_node_within_distance(G, hex_pt, max_distance)
    if source_node is None:
        rec = {id_col: hex_id}
        for t in thresholds:
            rec[colnames[t]] = None
        return rec

    lengths = nx.single_source_dijkstra_path_length(
        G,
        source_node,
        cutoff=_WORKER_STATE["max_cost_f"],
        weight=cost_attr,
    )
    if not lengths:
        rec = {id_col: hex_id}
        for t in thresholds:
            rec[colnames[t]] = None
        return rec

    rec = {id_col: hex_id}
    for t in thresholds:
        nodes_reachable = [n for n, c in lengths.items() if c <= float(t)]
        if not nodes_reachable:
            rec[colnames[t]] = None
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
                rec[colnames[t]] = None
                continue

            e = gpd.GeoSeries(edge_lines).buffer(edge_buff).geometry
            geom = gpd.GeoSeries(list(e)).union_all()

            if infill and geom is not None:
                geom = geom.convex_hull
        else:
            raise ValueError("method must be 'edges' or 'hull'")

        rec[colnames[t]] = geom

    return rec


def calculate_isochrones(
    G: nx.MultiDiGraph,
    hexes: gpd.GeoDataFrame,
    *,
    max_cost: Union[int, float],
    interval_size: Optional[Union[int, float]] = None,
    cost_attr: str,
    city_epsg: Union[int, str],
    id_col: str = "hex_id",
    max_distance: float = 200.0,
    method: str = "edges",   # "edges" or "hull"
    edge_buff: float = 25.0,
    infill: bool = False,
    save_dir: Optional[Union[str, Path]] = None,
    base_name: str = "walksheds",
    n_jobs: int = 1,
) -> gpd.GeoDataFrame:
    """
    Ιsochrone polygons:
    - For each hex: run one Dijkstra up to max_cost
    - Slice reachable nodes at thresholds derived from `interval_size`
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
    interval_size : int | float, optional
        Interval size. Example: max_cost=15, interval_size=3 -> 3,6,9,12,15.
        If not provided, defaults to max_cost (single threshold at max_cost).
    cost_attr : str
        Edge weight attribute used for routing.
    method : {"edges","hull"}
        Polygon building method.
    save_dir : optional
        If provided, saves one CSV per threshold (WKT geometry columns).
    n_jobs : int, default 1
        Number of processes for parallel execution. Use >1 to parallelize across hexes.

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
    if hexes.crs != G.graph.get("crs"):
        raise ValueError("hexes CRS must match graph CRS. Reproject hexes to G.graph['crs'] first.")

    max_cost_f = float(max_cost)
    interval_val = max_cost_f if interval_size is None else interval_size
    thresholds = _make_thresholds(max_cost_f, interval_val)

    # prepare saving
    save_dir_path = None
    if save_dir is not None:
        save_dir_path = Path(save_dir)
        save_dir_path.mkdir(parents=True, exist_ok=True)

    # base output: keep hex geometry as active geometry
    gdf_res = hexes[[id_col, "geometry"]].copy()
    gdf_res = gpd.GeoDataFrame(gdf_res, geometry="geometry", crs=hexes.crs)

    colnames = {t: f"geom_{cost_attr}_{_format_threshold(t)}" for t in thresholds}

    items = [(row[id_col], row.geometry) for _, row in hexes.iterrows()]

    if n_jobs is None or n_jobs <= 1:
        state = {
            "G": G,
            "thresholds": thresholds,
            "colnames": colnames,
            "cost_attr": cost_attr,
            "method": method,
            "edge_buff": edge_buff,
            "infill": infill,
            "max_distance": max_distance,
            "id_col": id_col,
            "max_cost_f": max_cost_f,
        }
        _init_worker(state)
        rows = [_process_hex_worker(item) for item in items]
    else:
        state = {
            "G": G,
            "thresholds": thresholds,
            "colnames": colnames,
            "cost_attr": cost_attr,
            "method": method,
            "edge_buff": edge_buff,
            "infill": infill,
            "max_distance": max_distance,
            "id_col": id_col,
            "max_cost_f": max_cost_f,
        }
        rows = []
        with ProcessPoolExecutor(max_workers=n_jobs, initializer=_init_worker, initargs=(state,)) as ex:
            futures = [ex.submit(_process_hex_worker, item) for item in items]
            for f in as_completed(futures):
                rows.append(f.result())

    out_thr = pd.DataFrame(rows)
    gdf_res = gdf_res.merge(out_thr, on=id_col, how="left")

    # optional save per threshold as CSV with WKT
    if save_dir_path is not None:
        for t in thresholds:
            thr_str = _format_threshold(t)
            fname = f"{base_name}_{method}_{cost_attr}_{thr_str}.csv"

            out_csv = gdf_res[[id_col, "geometry", colnames[t]]].copy()

            # convert geometry columns to WKT for CSV
            out_csv["geometry"] = out_csv["geometry"].apply(lambda g: g.wkt if g is not None else None)
            out_csv[colnames[t]] = out_csv[colnames[t]].apply(lambda g: g.wkt if g is not None else None)

            pd.DataFrame(out_csv).to_csv(save_dir_path / fname, index=False)

    return gdf_res
