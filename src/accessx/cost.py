from __future__ import annotations

import math
from typing import Callable, Optional, Union

import networkx as nx

def add_slope_based_time(
    *,
    slope_col: str = "slope_pct",
    length_col: str = "length",
    min_speed_mps: float = 0.05,
    default_slope: float = 0.0,
) -> Callable[[dict], float]:
    """
    Factory that returns a cost function:
    cost_fn(edge) -> travel time in minutes,
    using Tobler's hiking function.

    Parameters
    ----------
    slope_col : str
        Edge attribute with slope in percent.
    length_col : str
        Edge attribute with length in meters.
    min_speed_mps : float
        Minimum speed to avoid divide-by-zero.
    default_slope : float
        Used if slope_col is missing on an edge.

    Returns
    -------
    Callable[[dict], float]
        Function that maps an edge attribute dict to time (minutes).
    """
    if min_speed_mps <= 0:
        raise ValueError("min_speed_mps must be > 0")

    def cost_fn(edge: dict) -> float:
        length_m = edge.get(length_col)
        if length_m is None:
            raise KeyError(f"Edge missing '{length_col}' (meters).")

        slope_pct = edge.get(slope_col, default_slope)
        g = float(slope_pct) / 100.0

        # Tobler speed (km/h → m/s)
        v_kmh = 6.0 * math.exp(-3.5 * abs(g + 0.05))
        v_mps = max(v_kmh / 3.6, min_speed_mps)

        return float(length_m) / (v_mps * 60.0)  # minutes

    return cost_fn

# -------------------- generic primitive --------------------------------------

def add_edge_cost(
    G: nx.MultiDiGraph,
    *,
    cost_fn: Callable[[dict], Union[int, float]],
    cost_col: str,
) -> nx.MultiDiGraph:
    """
    Add/overwrite an edge cost attribute using a user-provided function.

    Parameters
    ----------
    G : MultiDiGraph
        Input graph.
    cost_fn : callable
        cost_fn(edge_data_dict) -> numeric cost (any units you define).
    cost_col : str
        Name of the edge attribute to store the computed cost.

    Returns
    -------
    MultiDiGraph
        Graph with added/updated edge attribute `cost_col`.
    """
    crs = G.graph.get("crs")
    if crs is None or crs.to_epsg() == 4326:
        raise ValueError("Graph must be projected to a metric CRS (not EPSG:4326).")

    for _, _, _, data in G.edges(keys=True, data=True):
        val = cost_fn(data)
        if val is None:
            raise ValueError(f"cost_fn returned None for an edge; must return a number for '{cost_col}'.")
        data[cost_col] = float(val)
    return G


# -------------------- common time convenience --------------------------------

def add_time_cost_constant_speed(
    G: nx.MultiDiGraph,
    *,
    speed_kmh: float = 4.5,
    cost_col: str = "time_min",
    length_col: str = "length",
) -> nx.MultiDiGraph:
    """
    Add edge travel time cost (minutes) assuming constant speed (km/h).
    """
    if speed_kmh <= 0:
        raise ValueError("speed_kmh must be > 0")

    v_mpm = (speed_kmh * 1000.0) / 60.0
    v_mpm = max(v_mpm, 1e-6)

    def _time_cost(edge: dict) -> float:
        length_m = edge.get(length_col)
        if length_m is None:
            raise KeyError(f"Edge missing '{length_col}'.")
        return float(length_m) / v_mpm

    return add_edge_cost(G, cost_fn=_time_cost, cost_col=cost_col)

