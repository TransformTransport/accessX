from __future__ import annotations

import math
from typing import Dict, List, Literal, Optional, Tuple, Union

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd


def _nearest_graph_node_and_distance(
    graph: nx.MultiDiGraph,
    point_x: float,
    point_y: float,
) -> Tuple[int, float]:
    """
    Find nearest graph node and return:
    (node_id, Euclidean distance in graph CRS units).
    """
    nearest_node_id = ox.distance.nearest_nodes(graph, point_x, point_y)
    nearest_node_y = graph.nodes[nearest_node_id]["y"]
    nearest_node_x = graph.nodes[nearest_node_id]["x"]
    distance_to_graph = ox.distance.euclidean(point_y, point_x, nearest_node_y, nearest_node_x)
    return int(nearest_node_id), float(distance_to_graph)


def count_accessible_pois(
    graph: nx.MultiDiGraph,
    hexes: gpd.GeoDataFrame,
    pois: gpd.GeoDataFrame,
    *,
    max_cost: Union[int, float],
    cost_attr: str,
    id_col: str = "hex_id",
    category_col: str = "category",
    max_distance_from_graph: float = 200.0,
) -> gpd.GeoDataFrame:
    """
    Count reachable POIs by category for each hex using direct graph routing costs.

    Steps
    -----
    1) Validate inputs.
    2) Reproject hexes and pois to graph CRS.
    3) Attach nearest graph node to each POI.
    4) Precompute POI counts by (node, category).
    5) For each hex, route once up to max_cost and aggregate counts from reachable nodes.

    Returns
    -------
    GeoDataFrame
        One row per hex with:
        [id_col, geometry, count_<category1>, count_<category2>, ...]
        Geometry is in the original hexes CRS.
    """
    # -------------------------
    # 1) validate inputs
    # -------------------------
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if pois is None or len(pois) == 0:
        raise ValueError("pois is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if pois.crs is None:
        raise ValueError("pois must have a CRS.")
    if graph.graph.get("crs") is None:
        raise ValueError("Graph has no CRS in graph.graph['crs']. Project graph first.")
    if id_col not in hexes.columns:
        raise ValueError(f"hexes missing required id column '{id_col}'.")
    if category_col not in pois.columns:
        raise ValueError(f"pois missing required category column '{category_col}'.")
    if max_cost <= 0:
        raise ValueError("max_cost must be > 0.")
    if max_distance_from_graph <= 0:
        raise ValueError("max_distance_from_graph must be > 0.")

    graph_has_cost_attribute = any(
        cost_attr in edge_data for _, _, _, edge_data in graph.edges(keys=True, data=True)
    )
    if not graph_has_cost_attribute:
        raise ValueError(f"Edge cost attribute '{cost_attr}' not found on graph edges.")

    # -------------------------
    # 2) reproject to graph CRS
    # -------------------------
    graph_crs = graph.graph["crs"]
    original_hex_crs = hexes.crs
    if hexes.crs != graph_crs:
        hexes = hexes.to_crs(graph_crs)
    if pois.crs != graph_crs:
        pois = pois.to_crs(graph_crs)

    # -------------------------
    # 3) attach nearest node to each POI
    # -------------------------
    pois = pois[[category_col, "geometry"]].copy()
    pois = pois[pois["geometry"].notna()].copy()
    if len(pois) == 0:
        raise ValueError("pois has no valid geometries.")

    pois[category_col] = pois[category_col].astype(str)
    poi_categories = sorted(pois[category_col].dropna().unique().tolist())
    if not poi_categories:
        raise ValueError("No valid POI categories found.")

    poi_points = pois["geometry"].apply(lambda geom: geom if geom.geom_type == "Point" else geom.centroid)
    poi_x_coords = poi_points.x.tolist()
    poi_y_coords = poi_points.y.tolist()
    pois["nearest_node"] = pd.Series(ox.distance.nearest_nodes(graph, poi_x_coords, poi_y_coords)).astype("int64")

    # -------------------------
    # 4) precompute POI counts by node and category
    # -------------------------
    poi_node_category_counts = (
        pois.groupby(["nearest_node", category_col])
        .size()
        .reset_index(name="poi_count")
    )

    node_to_category_count_pairs: Dict[int, List[Tuple[str, int]]] = {}
    for count_row in poi_node_category_counts.itertuples(index=False):
        graph_node_id = int(count_row.nearest_node)
        poi_category_name = str(getattr(count_row, category_col))
        poi_count_on_node = int(count_row.poi_count)
        node_to_category_count_pairs.setdefault(graph_node_id, []).append(
            (poi_category_name, poi_count_on_node)
        )

    # -------------------------
    # 5) one route per hex + aggregate counts
    # -------------------------
    # Start output with hex id + geometry, then add one count column per POI category.
    accessibility_counts = hexes[[id_col, "geometry"]].copy()
    for poi_category_name in poi_categories:
        accessibility_counts[f"count_{poi_category_name}"] = 0

    # Normalize numeric params once outside the loop.
    max_cost_value = float(max_cost)
    max_graph_distance_value = float(max_distance_from_graph)

    # Process each hex independently: snap -> route -> aggregate category counts.
    for hex_index, hex_row in hexes[[id_col, "geometry"]].iterrows():
        hex_geometry = hex_row.geometry
        hex_centroid_point = hex_geometry if hex_geometry.geom_type == "Point" else hex_geometry.centroid

        # Snap hex centroid to the graph and skip hexes too far from the network.
        source_node_id, distance_from_hex_to_graph = _nearest_graph_node_and_distance(
            graph,
            hex_centroid_point.x,
            hex_centroid_point.y,
        )
        if distance_from_hex_to_graph > max_graph_distance_value:
            continue

        # One Dijkstra per hex, capped at max_cost.
        shortest_path_costs = nx.single_source_dijkstra_path_length(
            graph,
            source_node_id,
            cutoff=max_cost_value,
            weight=cost_attr,
        )

        # Sum POIs by category only on nodes reachable within max_cost.
        category_counts_for_hex = {poi_category_name: 0 for poi_category_name in poi_categories}
        for reachable_node_id in shortest_path_costs.keys():
            category_pairs = node_to_category_count_pairs.get(int(reachable_node_id), [])
            for poi_category_name, poi_count_on_node in category_pairs:
                category_counts_for_hex[poi_category_name] += poi_count_on_node

        # Write final counts into the output row for this hex.
        for poi_category_name in poi_categories:
            accessibility_counts.at[hex_index, f"count_{poi_category_name}"] = int(
                category_counts_for_hex[poi_category_name]
            )

    accessibility_counts = accessibility_counts.to_crs(original_hex_crs)
    return gpd.GeoDataFrame(accessibility_counts, geometry="geometry", crs=original_hex_crs)


def _clean_cost_value(cost_value: float) -> Union[int, float]:
    """
    Keep integer-like costs clean (e.g., 2.0 -> 2).
    """
    return int(round(cost_value)) if abs(cost_value - round(cost_value)) < 1e-9 else float(cost_value)


def compute_nearest_poi_cost(
    graph: nx.MultiDiGraph,
    hexes: gpd.GeoDataFrame,
    pois: gpd.GeoDataFrame,
    *,
    max_cost: Union[int, float],
    cost_attr: str,
    id_col: str = "hex_id",
    category_col: str = "category",
    poi_id_col: str = "id",
    max_distance_from_graph: float = 200.0,
    number_of_nearest: Optional[int] = None,
) -> gpd.GeoDataFrame:
    """
    Compute nearest reachable POI cost for each hex using direct graph routing.

    Output columns:
    - nearest_pois_<category>: list of tuples [(cost, poi_id), ...]

    Behavior:
    - if number_of_nearest is None -> returns only the nearest POI per category as a one-item list
    - if number_of_nearest is set -> returns up to that many nearest POIs
    - if no POI is reachable within max_cost -> returns []
    """
    # -------------------------
    # 1) validate inputs
    # -------------------------
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if pois is None or len(pois) == 0:
        raise ValueError("pois is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if pois.crs is None:
        raise ValueError("pois must have a CRS.")
    if graph.graph.get("crs") is None:
        raise ValueError("Graph has no CRS in graph.graph['crs']. Project graph first.")
    if id_col not in hexes.columns:
        raise ValueError(f"hexes missing required id column '{id_col}'.")
    if category_col not in pois.columns:
        raise ValueError(f"pois missing required category column '{category_col}'.")
    if poi_id_col not in pois.columns:
        raise ValueError(f"pois missing required POI id column '{poi_id_col}'.")
    if max_cost <= 0:
        raise ValueError("max_cost must be > 0.")
    if max_distance_from_graph <= 0:
        raise ValueError("max_distance_from_graph must be > 0.")
    if number_of_nearest is not None and number_of_nearest <= 0:
        raise ValueError("number_of_nearest must be > 0 when provided.")

    graph_has_cost_attribute = any(
        cost_attr in edge_data for _, _, _, edge_data in graph.edges(keys=True, data=True)
    )
    if not graph_has_cost_attribute:
        raise ValueError(f"Edge cost attribute '{cost_attr}' not found on graph edges.")

    # -------------------------
    # 2) reproject to graph CRS
    # -------------------------
    graph_crs = graph.graph["crs"]
    original_hex_crs = hexes.crs
    if hexes.crs != graph_crs:
        hexes = hexes.to_crs(graph_crs)
    if pois.crs != graph_crs:
        pois = pois.to_crs(graph_crs)

    # -------------------------
    # 3) attach nearest node to each POI
    # -------------------------
    pois = pois[[poi_id_col, category_col, "geometry"]].copy()
    pois = pois[pois["geometry"].notna()].copy()
    if len(pois) == 0:
        raise ValueError("pois has no valid geometries.")
    pois[category_col] = pois[category_col].astype(str)

    poi_categories = sorted(pois[category_col].dropna().unique().tolist())
    if not poi_categories:
        raise ValueError("No valid POI categories found.")

    poi_points = pois["geometry"].apply(lambda geom: geom if geom.geom_type == "Point" else geom.centroid)
    poi_x_coords = poi_points.x.tolist()
    poi_y_coords = poi_points.y.tolist()
    pois["nearest_node"] = pd.Series(ox.distance.nearest_nodes(graph, poi_x_coords, poi_y_coords)).astype("int64")

    # Build node -> category -> list of POI ids for fast lookup during routing.
    node_to_category_poi_ids: Dict[int, Dict[str, List[Union[int, str]]]] = {}
    for poi_row in pois[[poi_id_col, category_col, "nearest_node"]].itertuples(index=False):
        poi_id = getattr(poi_row, poi_id_col)
        poi_category_name = str(getattr(poi_row, category_col))
        nearest_node = int(poi_row.nearest_node)
        node_to_category_poi_ids.setdefault(nearest_node, {}).setdefault(poi_category_name, []).append(poi_id)

    # -------------------------
    # 4) one route per hex + collect nearest POIs
    # -------------------------
    nearest_poi_costs = hexes[[id_col, "geometry"]].copy()
    for poi_category_name in poi_categories:
        nearest_poi_costs[f"nearest_pois_{poi_category_name}"] = [[] for _ in range(len(nearest_poi_costs))]

    max_cost_value = float(max_cost)
    max_graph_distance_value = float(max_distance_from_graph)
    nearest_limit = 1 if number_of_nearest is None else int(number_of_nearest)

    for hex_index, hex_row in hexes[[id_col, "geometry"]].iterrows():
        hex_geometry = hex_row.geometry
        hex_centroid_point = hex_geometry if hex_geometry.geom_type == "Point" else hex_geometry.centroid

        source_node_id, distance_from_hex_to_graph = _nearest_graph_node_and_distance(
            graph,
            hex_centroid_point.x,
            hex_centroid_point.y,
        )
        if distance_from_hex_to_graph > max_graph_distance_value:
            continue

        shortest_path_costs = nx.single_source_dijkstra_path_length(
            graph,
            source_node_id,
            cutoff=max_cost_value,
            weight=cost_attr,
        )

        # Candidate lists from reachable nodes, split by category.
        reachable_poi_cost_pairs_by_category: Dict[str, List[Tuple[Union[int, float], Union[int, str]]]] = {
            poi_category_name: [] for poi_category_name in poi_categories
        }
        for reachable_node_id, route_cost in shortest_path_costs.items():
            category_to_poi_ids = node_to_category_poi_ids.get(int(reachable_node_id), {})
            if not category_to_poi_ids:
                continue

            clean_cost = _clean_cost_value(float(route_cost))
            for poi_category_name, poi_ids in category_to_poi_ids.items():
                for poi_id in poi_ids:
                    reachable_poi_cost_pairs_by_category[poi_category_name].append((clean_cost, poi_id))

        # Stable ordering by cost, then POI id as string for deterministic ties.
        for poi_category_name in poi_categories:
            category_pairs = reachable_poi_cost_pairs_by_category[poi_category_name]
            if not category_pairs:
                continue
            category_pairs.sort(key=lambda x: (float(x[0]), str(x[1])))
            nearest_poi_costs.at[hex_index, f"nearest_pois_{poi_category_name}"] = category_pairs[:nearest_limit]

    nearest_poi_costs = nearest_poi_costs.to_crs(original_hex_crs)
    return gpd.GeoDataFrame(nearest_poi_costs, geometry="geometry", crs=original_hex_crs)


def compute_hansen_accessibility(
    graph: nx.MultiDiGraph,
    hexes: gpd.GeoDataFrame,
    pois: gpd.GeoDataFrame,
    *,
    max_cost: Union[int, float],
    cost_attr: str,
    id_col: str = "hex_id",
    category_col: str = "category",
    poi_weight_col: Optional[str] = None,
    category_weights: Optional[Dict[str, float]] = None,
    default_poi_weight: float = 1.0,
    max_distance_from_graph: float = 200.0,
    beta: float = 0.15,
) -> gpd.GeoDataFrame:
    """
    Compute Hansen accessibility score for each hex using direct graph routing.

    Formula (per category):
    A_i = sum_j( O_j * exp(-beta * c_ij) )

    where:
    - O_j is the POI opportunity weight
    - c_ij is travel cost from hex i to POI j (from graph shortest path)

    Output columns:
    - hansen_<category> for each POI category
    - hansen_total as the sum across all categories
    """
    # -------------------------
    # 1) validate inputs
    # -------------------------
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if pois is None or len(pois) == 0:
        raise ValueError("pois is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if pois.crs is None:
        raise ValueError("pois must have a CRS.")
    if graph.graph.get("crs") is None:
        raise ValueError("Graph has no CRS in graph.graph['crs']. Project graph first.")
    if id_col not in hexes.columns:
        raise ValueError(f"hexes missing required id column '{id_col}'.")
    if category_col not in pois.columns:
        raise ValueError(f"pois missing required category column '{category_col}'.")
    if poi_weight_col is not None and poi_weight_col not in pois.columns:
        raise ValueError(f"pois missing required weight column '{poi_weight_col}'.")
    if max_cost <= 0:
        raise ValueError("max_cost must be > 0.")
    if max_distance_from_graph <= 0:
        raise ValueError("max_distance_from_graph must be > 0.")
    if beta <= 0:
        raise ValueError("beta must be > 0.")
    if default_poi_weight < 0:
        raise ValueError("default_poi_weight must be >= 0.")

    graph_has_cost_attribute = any(
        cost_attr in edge_data for _, _, _, edge_data in graph.edges(keys=True, data=True)
    )
    if not graph_has_cost_attribute:
        raise ValueError(f"Edge cost attribute '{cost_attr}' not found on graph edges.")

    category_weights = category_weights or {}
    for category_name, category_weight in category_weights.items():
        if category_weight < 0:
            raise ValueError(f"category weight for '{category_name}' must be >= 0.")

    # -------------------------
    # 2) reproject to graph CRS
    # -------------------------
    graph_crs = graph.graph["crs"]
    original_hex_crs = hexes.crs
    if hexes.crs != graph_crs:
        hexes = hexes.to_crs(graph_crs)
    if pois.crs != graph_crs:
        pois = pois.to_crs(graph_crs)

    # -------------------------
    # 3) attach nearest node to each POI + compute effective weight
    # -------------------------
    keep_columns = [category_col, "geometry"]
    if poi_weight_col is not None:
        keep_columns.append(poi_weight_col)
    pois = pois[keep_columns].copy()
    pois = pois[pois["geometry"].notna()].copy()
    if len(pois) == 0:
        raise ValueError("pois has no valid geometries.")

    pois[category_col] = pois[category_col].astype(str)
    poi_categories = sorted(pois[category_col].dropna().unique().tolist())
    if not poi_categories:
        raise ValueError("No valid POI categories found.")

    if poi_weight_col is None:
        pois["poi_weight"] = float(default_poi_weight)
    else:
        pois["poi_weight"] = pd.to_numeric(pois[poi_weight_col], errors="coerce")
        pois["poi_weight"] = pois["poi_weight"].fillna(float(default_poi_weight))

    if (pois["poi_weight"] < 0).any():
        raise ValueError("POI weights must be >= 0.")

    pois["category_weight"] = pois[category_col].map(category_weights).fillna(1.0).astype(float)
    pois["effective_weight"] = pois["poi_weight"] * pois["category_weight"]

    poi_points = pois["geometry"].apply(lambda geom: geom if geom.geom_type == "Point" else geom.centroid)
    poi_x_coords = poi_points.x.tolist()
    poi_y_coords = poi_points.y.tolist()
    pois["nearest_node"] = pd.Series(ox.distance.nearest_nodes(graph, poi_x_coords, poi_y_coords)).astype("int64")

    # Pre-aggregate opportunities at node/category level for faster routing loops.
    poi_node_category_weights = (
        pois.groupby(["nearest_node", category_col])["effective_weight"]
        .sum()
        .reset_index(name="total_weight")
    )

    node_to_category_weight_pairs: Dict[int, List[Tuple[str, float]]] = {}
    for weight_row in poi_node_category_weights.itertuples(index=False):
        graph_node_id = int(weight_row.nearest_node)
        poi_category_name = str(getattr(weight_row, category_col))
        total_weight_on_node = float(weight_row.total_weight)
        node_to_category_weight_pairs.setdefault(graph_node_id, []).append(
            (poi_category_name, total_weight_on_node)
        )

    # -------------------------
    # 4) one route per hex + Hansen aggregation
    # -------------------------
    hansen_scores = hexes[[id_col, "geometry"]].copy()
    for poi_category_name in poi_categories:
        hansen_scores[f"hansen_{poi_category_name}"] = 0.0
    hansen_scores["hansen_total"] = 0.0

    max_cost_value = float(max_cost)
    max_graph_distance_value = float(max_distance_from_graph)

    for hex_index, hex_row in hexes[[id_col, "geometry"]].iterrows():
        hex_geometry = hex_row.geometry
        hex_centroid_point = hex_geometry if hex_geometry.geom_type == "Point" else hex_geometry.centroid

        source_node_id, distance_from_hex_to_graph = _nearest_graph_node_and_distance(
            graph,
            hex_centroid_point.x,
            hex_centroid_point.y,
        )
        if distance_from_hex_to_graph > max_graph_distance_value:
            continue

        shortest_path_costs = nx.single_source_dijkstra_path_length(
            graph,
            source_node_id,
            cutoff=max_cost_value,
            weight=cost_attr,
        )

        category_hansen_scores_for_hex = {poi_category_name: 0.0 for poi_category_name in poi_categories}
        for reachable_node_id, route_cost in shortest_path_costs.items():
            decay_value = math.exp(-beta * float(route_cost))
            category_weight_pairs = node_to_category_weight_pairs.get(int(reachable_node_id), [])
            for poi_category_name, total_weight_on_node in category_weight_pairs:
                category_hansen_scores_for_hex[poi_category_name] += total_weight_on_node * decay_value

        combined_hansen_score = 0.0
        for poi_category_name in poi_categories:
            score_value = float(category_hansen_scores_for_hex[poi_category_name])
            hansen_scores.at[hex_index, f"hansen_{poi_category_name}"] = score_value
            combined_hansen_score += score_value
        hansen_scores.at[hex_index, "hansen_total"] = float(combined_hansen_score)

    hansen_scores = hansen_scores.to_crs(original_hex_crs)
    return gpd.GeoDataFrame(hansen_scores, geometry="geometry", crs=original_hex_crs)


def _compute_decay_value(
    route_cost: float,
    *,
    decay: Literal["binary", "exp"],
    beta: float,
) -> float:
    """
    Compute distance-decay value used in catchment-based accessibility.
    """
    if decay == "binary":
        return 1.0
    return math.exp(-beta * float(route_cost))


def compute_2sfca_accessibility(
    graph: nx.MultiDiGraph,
    hexes: gpd.GeoDataFrame,
    pois: gpd.GeoDataFrame,
    *,
    max_cost: Union[int, float],
    cost_attr: str,
    demand_col: str,
    id_col: str = "hex_id",
    category_col: str = "category",
    supply_col: Optional[str] = None,
    default_supply: float = 1.0,
    category_weights: Optional[Dict[str, float]] = None,
    max_distance_from_graph: float = 200.0,
    decay: Literal["binary", "exp"] = "binary",
    beta: float = 0.15,
) -> gpd.GeoDataFrame:
    """
    Compute 2SFCA accessibility for each hexagon.

    Two-step logic:
    1) Provider ratio at each POI node:
       R_j = S_j / sum_k( D_k * f(c_kj) )
    2) Hex accessibility:
       A_i = sum_j( R_j * f(c_ij) )

    Output columns:
    - sfca_<category> for each POI category
    - sfca_total as the sum across categories
    """
    # -------------------------
    # 1) validate inputs
    # -------------------------
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if pois is None or len(pois) == 0:
        raise ValueError("pois is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if pois.crs is None:
        raise ValueError("pois must have a CRS.")
    if graph.graph.get("crs") is None:
        raise ValueError("Graph has no CRS in graph.graph['crs']. Project graph first.")
    if id_col not in hexes.columns:
        raise ValueError(f"hexes missing required id column '{id_col}'.")
    if demand_col not in hexes.columns:
        raise ValueError(f"hexes missing required demand column '{demand_col}'.")
    if category_col not in pois.columns:
        raise ValueError(f"pois missing required category column '{category_col}'.")
    if supply_col is not None and supply_col not in pois.columns:
        raise ValueError(f"pois missing required supply column '{supply_col}'.")
    if max_cost <= 0:
        raise ValueError("max_cost must be > 0.")
    if max_distance_from_graph <= 0:
        raise ValueError("max_distance_from_graph must be > 0.")
    if default_supply < 0:
        raise ValueError("default_supply must be >= 0.")
    if decay not in {"binary", "exp"}:
        raise ValueError("decay must be either 'binary' or 'exp'.")
    if decay == "exp" and beta <= 0:
        raise ValueError("beta must be > 0 when decay='exp'.")

    graph_has_cost_attribute = any(
        cost_attr in edge_data for _, _, _, edge_data in graph.edges(keys=True, data=True)
    )
    if not graph_has_cost_attribute:
        raise ValueError(f"Edge cost attribute '{cost_attr}' not found on graph edges.")

    category_weights = category_weights or {}
    for category_name, category_weight in category_weights.items():
        if category_weight < 0:
            raise ValueError(f"category weight for '{category_name}' must be >= 0.")

    # -------------------------
    # 2) reproject to graph CRS
    # -------------------------
    graph_crs = graph.graph["crs"]
    original_hex_crs = hexes.crs
    if hexes.crs != graph_crs:
        hexes = hexes.to_crs(graph_crs)
    if pois.crs != graph_crs:
        pois = pois.to_crs(graph_crs)

    # -------------------------
    # 3) prepare and snap hexes
    # -------------------------
    hexes = hexes[[id_col, demand_col, "geometry"]].copy()
    hexes[demand_col] = pd.to_numeric(hexes[demand_col], errors="coerce").fillna(0.0)
    if (hexes[demand_col] < 0).any():
        raise ValueError("hex demand values must be >= 0.")

    hex_centroid_points = hexes["geometry"].apply(lambda geom: geom if geom.geom_type == "Point" else geom.centroid)
    hex_x_coords = hex_centroid_points.x.tolist()
    hex_y_coords = hex_centroid_points.y.tolist()
    hexes["nearest_node"] = pd.Series(ox.distance.nearest_nodes(graph, hex_x_coords, hex_y_coords)).astype("int64")

    nearest_node_x = hexes["nearest_node"].map(lambda node_id: graph.nodes[int(node_id)]["x"])
    nearest_node_y = hexes["nearest_node"].map(lambda node_id: graph.nodes[int(node_id)]["y"])
    hexes["distance_from_graph"] = ox.distance.euclidean(
        hex_centroid_points.y,
        hex_centroid_points.x,
        nearest_node_y,
        nearest_node_x,
    )

    valid_hexes = hexes[hexes["distance_from_graph"] <= float(max_distance_from_graph)].copy()
    valid_hexes["nearest_node"] = valid_hexes["nearest_node"].astype("int64")

    # Aggregate demand on graph nodes for faster provider-side catchment sums.
    node_demand_totals = (
        valid_hexes.groupby("nearest_node")[demand_col]
        .sum()
        .reset_index(name="total_demand")
    )
    node_to_total_demand: Dict[int, float] = {
        int(row.nearest_node): float(row.total_demand) for row in node_demand_totals.itertuples(index=False)
    }

    # -------------------------
    # 4) prepare and snap POIs
    # -------------------------
    keep_columns = [category_col, "geometry"]
    if supply_col is not None:
        keep_columns.append(supply_col)
    pois = pois[keep_columns].copy()
    pois = pois[pois["geometry"].notna()].copy()
    if len(pois) == 0:
        raise ValueError("pois has no valid geometries.")

    pois[category_col] = pois[category_col].astype(str)
    poi_categories = sorted(pois[category_col].dropna().unique().tolist())
    if not poi_categories:
        raise ValueError("No valid POI categories found.")

    if supply_col is None:
        pois["poi_supply"] = float(default_supply)
    else:
        pois["poi_supply"] = pd.to_numeric(pois[supply_col], errors="coerce").fillna(float(default_supply))
    if (pois["poi_supply"] < 0).any():
        raise ValueError("POI supply values must be >= 0.")

    pois["category_weight"] = pois[category_col].map(category_weights).fillna(1.0).astype(float)
    pois["effective_supply"] = pois["poi_supply"] * pois["category_weight"]

    poi_points = pois["geometry"].apply(lambda geom: geom if geom.geom_type == "Point" else geom.centroid)
    poi_x_coords = poi_points.x.tolist()
    poi_y_coords = poi_points.y.tolist()
    pois["nearest_node"] = pd.Series(ox.distance.nearest_nodes(graph, poi_x_coords, poi_y_coords)).astype("int64")

    # Aggregate effective supply at node/category level.
    poi_node_category_supply = (
        pois.groupby(["nearest_node", category_col])["effective_supply"]
        .sum()
        .reset_index(name="total_supply")
    )

    node_to_category_supply_pairs: Dict[int, List[Tuple[str, float]]] = {}
    for supply_row in poi_node_category_supply.itertuples(index=False):
        graph_node_id = int(supply_row.nearest_node)
        poi_category_name = str(getattr(supply_row, category_col))
        total_supply_on_node = float(supply_row.total_supply)
        node_to_category_supply_pairs.setdefault(graph_node_id, []).append(
            (poi_category_name, total_supply_on_node)
        )

    provider_node_ids = sorted(node_to_category_supply_pairs.keys())

    # -------------------------
    # 5) step 1 of 2SFCA: provider ratios R_j
    # -------------------------
    node_to_category_ratio_pairs: Dict[int, List[Tuple[str, float]]] = {}
    max_cost_value = float(max_cost)

    for provider_node_id in provider_node_ids:
        shortest_path_costs = nx.single_source_dijkstra_path_length(
            graph,
            provider_node_id,
            cutoff=max_cost_value,
            weight=cost_attr,
        )

        catchment_demand_sum = 0.0
        for reachable_node_id, route_cost in shortest_path_costs.items():
            demand_on_node = node_to_total_demand.get(int(reachable_node_id), 0.0)
            if demand_on_node <= 0:
                continue
            decay_value = _compute_decay_value(float(route_cost), decay=decay, beta=float(beta))
            catchment_demand_sum += demand_on_node * decay_value

        if catchment_demand_sum <= 0:
            ratio_pairs = [
                (poi_category_name, 0.0)
                for poi_category_name, _ in node_to_category_supply_pairs[provider_node_id]
            ]
            node_to_category_ratio_pairs[provider_node_id] = ratio_pairs
            continue

        ratio_pairs = []
        for poi_category_name, total_supply_on_node in node_to_category_supply_pairs[provider_node_id]:
            provider_ratio = float(total_supply_on_node) / float(catchment_demand_sum)
            ratio_pairs.append((poi_category_name, provider_ratio))
        node_to_category_ratio_pairs[provider_node_id] = ratio_pairs

    # -------------------------
    # 6) step 2 of 2SFCA: hex accessibility A_i
    # -------------------------
    sfca_scores = hexes[[id_col, "geometry"]].copy()
    for poi_category_name in poi_categories:
        sfca_scores[f"sfca_{poi_category_name}"] = 0.0
    sfca_scores["sfca_total"] = 0.0

    for hex_index, hex_row in hexes[[id_col, "geometry"]].iterrows():
        hex_geometry = hex_row.geometry
        hex_centroid_point = hex_geometry if hex_geometry.geom_type == "Point" else hex_geometry.centroid

        source_node_id, distance_from_hex_to_graph = _nearest_graph_node_and_distance(
            graph,
            hex_centroid_point.x,
            hex_centroid_point.y,
        )
        if distance_from_hex_to_graph > float(max_distance_from_graph):
            continue

        shortest_path_costs = nx.single_source_dijkstra_path_length(
            graph,
            source_node_id,
            cutoff=max_cost_value,
            weight=cost_attr,
        )

        category_scores_for_hex = {poi_category_name: 0.0 for poi_category_name in poi_categories}
        for reachable_node_id, route_cost in shortest_path_costs.items():
            category_ratio_pairs = node_to_category_ratio_pairs.get(int(reachable_node_id), [])
            if not category_ratio_pairs:
                continue

            decay_value = _compute_decay_value(float(route_cost), decay=decay, beta=float(beta))
            for poi_category_name, provider_ratio in category_ratio_pairs:
                category_scores_for_hex[poi_category_name] += float(provider_ratio) * decay_value

        total_score = 0.0
        for poi_category_name in poi_categories:
            score_value = float(category_scores_for_hex[poi_category_name])
            sfca_scores.at[hex_index, f"sfca_{poi_category_name}"] = score_value
            total_score += score_value
        sfca_scores.at[hex_index, "sfca_total"] = total_score

    sfca_scores = sfca_scores.to_crs(original_hex_crs)
    return gpd.GeoDataFrame(sfca_scores, geometry="geometry", crs=original_hex_crs)
