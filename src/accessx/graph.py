# accessx/graph.py
from __future__ import annotations

from typing import Optional, Union

import geopandas as gpd
import networkx as nx
import osmnx as ox
from pathlib import Path
from typing import Union

import osmnx as ox
import networkx as nx

from accessx.io import save_gdf


def save_graph(
    G: nx.MultiDiGraph,
    *,
    out_dir: Union[str, Path],
    base_name: str = "street",
    save_nodes: bool = True,
    save_edges: bool = True,
    driver: str = "GeoJSON",
) -> None:
    """
    Save an OSMnx graph as nodes and/or edges files.

    Parameters
    ----------
    G : MultiDiGraph
        Projected OSMnx graph.
    out_dir : str | Path
        Output directory.
    base_name : str
        Base filename (suffixes '_nodes_OSM' and '_edges_OSM' are added automatically).
    save_nodes : bool
        If True, save nodes GeoDataFrame.
    save_edges : bool
        If True, save edges GeoDataFrame.
    driver : str
        OGR driver (default: GeoJSON).
    """
    if not save_nodes and not save_edges:
        raise ValueError("At least one of save_nodes or save_edges must be True.")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G, nodes=True, edges=True)

    if save_nodes:
        nodes_path = out_dir / f"{base_name}_nodes_OSM.geojson"
        save_gdf(nodes_gdf, nodes_path, driver=driver)

    if save_edges:
        # keep u, v, key as explicit columns (important)
        edges_gdf = edges_gdf.reset_index()
        edges_path = out_dir / f"{base_name}_edges_OSM.geojson"
        save_gdf(edges_gdf, edges_path, driver=driver)


def build_network(
    AOI: gpd.GeoDataFrame,
    *,
    city_epsg: Union[int, str],
    buffer_m: float = 0.0,
    network_type: str = "walk",
    simplify: bool = False,
    retain_all: bool = True,
    remove_isolates: bool = True,
) -> nx.MultiDiGraph:
    """
    Build a walkable OSMnx graph from an AOI, with optional buffer (meters),
    remove isolates, and project to a city/local CRS.

    Keeps your original workflow:
    AOI -> buffer -> to 4326 -> graph_from_polygon -> remove isolates -> project.

    Parameters
    ----------
    AOI : GeoDataFrame
        AOI polygons. Can be multiple rows (will be dissolved).
        Must have a valid CRS.
    city_epsg : int | str
        Projected CRS for metric computations (e.g., 3044, 32632, etc.).
    buffer_m : float
        Buffer distance in meters, applied in city_epsg before downloading network.
    network_type : str
        OSMnx network type, default "walk".
    simplify : bool
        Whether to simplify the graph.
    retain_all : bool
        Keep all components from OSMnx download.
    remove_isolates : bool
        Remove isolated nodes (degree 0).

    Returns
    -------
    MultiDiGraph
        Projected graph in city_epsg.
    """
    if AOI is None or len(AOI) == 0:
        raise ValueError("AOI is empty.")
    if AOI.crs is None:
        raise ValueError("AOI must have a CRS.")

    # Ensure single polygon (OSMnx wants a single geometry)
    aoi_one = AOI.copy()
    if len(aoi_one) > 1:
        aoi_one = aoi_one.dissolve(by=None).reset_index(drop=True)

    # --- IMPORTANT BIT: buffer in a metric CRS ---
    if buffer_m and buffer_m != 0:
        aoi_metric = aoi_one.to_crs(city_epsg)
        aoi_metric["geometry"] = aoi_metric.geometry.buffer(buffer_m)
        AOI_wgs84_buffer = aoi_metric.to_crs(4326)
    else:
        AOI_wgs84_buffer = aoi_one.to_crs(4326)

    # collecting the street network (your code style)
    G_wgs84 = ox.graph_from_polygon(
        polygon=AOI_wgs84_buffer.iloc[0].geometry,
        network_type=network_type,
        simplify=simplify,
        retain_all=retain_all,
    )

    # remove isolates (your code style)
    if remove_isolates:
        G_wgs84.remove_nodes_from(list(nx.isolates(G_wgs84)))

    # reproject the walking network (your code style)
    G_proj = ox.project_graph(G_wgs84, to_crs=city_epsg)

    return G_proj