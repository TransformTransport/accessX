from __future__ import annotations

import math
import warnings
from typing import Callable, Optional, Union

from pyproj import CRS
import networkx as nx


def _iter_with_progress(iterable, *, total: int, show_progress: bool, desc: str):
    if not show_progress:
        return iterable
    try:
        from tqdm.auto import tqdm
    except ImportError:
        warnings.warn(
            "show_progress=True requires tqdm. Install tqdm or set show_progress=False.",
            RuntimeWarning,
            stacklevel=2,
        )
        return iterable
    return tqdm(iterable, total=total, desc=desc, unit="edge")


def _is_wgs84(crs) -> bool:
    try:
        return CRS.from_user_input(crs).to_epsg() == 4326
    except Exception:
        return False

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
    G: Union[nx.MultiDiGraph, nx.MultiGraph],
    *,
    cost_fn: Callable[[dict], Union[int, float]],
    cost_col: str,
    show_progress: bool = False,
) -> Union[nx.MultiDiGraph, nx.MultiGraph]:
    """
    Add/overwrite an edge cost attribute using a user-provided function.

    Parameters
    ----------
    G : MultiDiGraph | MultiGraph
        Input graph. If undirected, parallel/reciprocal edges share a single
        cost value, so a direction-dependent cost_fn (e.g. slope-based) will
        lose directionality.
    cost_fn : callable
        cost_fn(edge_data_dict) -> numeric cost (any units you define).
    cost_col : str
        Name of the edge attribute to store the computed cost.
    show_progress : bool
        If True, show progress while assigning edge costs.

    Returns
    -------
    MultiDiGraph | MultiGraph
        Graph with added/updated edge attribute `cost_col`.
    """
    crs = G.graph.get("crs")
    if crs is None or _is_wgs84(crs):
        raise ValueError("Graph must be projected to a metric CRS (not EPSG:4326).")
    if not G.is_directed():
        warnings.warn(
            f"add_edge_cost: G is undirected, so parallel/reciprocal edges share a single "
            f"'{cost_col}' value. Direction-dependent cost functions (e.g. slope-based "
            "costs) will lose directionality.",
            RuntimeWarning,
            stacklevel=2,
        )

    edge_iter = G.edges(keys=True, data=True)
    edge_iter = _iter_with_progress(
        edge_iter,
        total=G.number_of_edges(),
        show_progress=show_progress,
        desc=f"Adding {cost_col}",
    )
    for _, _, _, data in edge_iter:
        val = cost_fn(data)
        if val is None:
            raise ValueError(f"cost_fn returned None for an edge; must return a number for '{cost_col}'.")
        data[cost_col] = float(val)
    return G


# -------------------- common time convenience --------------------------------

def add_time_cost_constant_speed(
    G: Union[nx.MultiDiGraph, nx.MultiGraph],
    *,
    speed_kmh: float = 4.5,
    cost_col: str = "time_min",
    length_col: str = "length",
    show_progress: bool = False,
) -> Union[nx.MultiDiGraph, nx.MultiGraph]:
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

    return add_edge_cost(G, cost_fn=_time_cost, cost_col=cost_col, show_progress=show_progress)
