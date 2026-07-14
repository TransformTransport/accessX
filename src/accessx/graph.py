# accessx/graph.py
from __future__ import annotations

import warnings
from typing import Optional, Union

import geopandas as gpd
import networkx as nx
import osmnx as ox
from pathlib import Path

from accessx.io import save_gdf, read_gdf


def _make_progress_bar(show_progress: bool, *, total: int, desc: str):
    if not show_progress:
        return None
    try:
        from tqdm.auto import tqdm
    except ImportError:
        warnings.warn(
            "show_progress=True requires tqdm. Install tqdm or set show_progress=False.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    return tqdm(total=total, desc=desc, unit="step")


def _advance_progress(progress_bar, description: str) -> None:
    if progress_bar is not None:
        progress_bar.set_description(description)
        progress_bar.update(1)


def save_graph(
    G: Union[nx.MultiDiGraph, nx.MultiGraph],
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
    G : MultiDiGraph | MultiGraph
        Projected OSMnx graph. Note that whether G is directed or undirected
        is not stored in the saved node/edge files — pass the matching
        `undirected` value to `load_graph` to rebuild it correctly.
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


def load_graph(
    *,
    out_dir: Optional[Union[str, Path]] = None,
    base_name: Optional[str] = None,
    nodes_path: Optional[Union[str, Path]] = None,
    edges_path: Optional[Union[str, Path]] = None,
    crs: Optional[Union[int, str]] = None,
    node_id_col: str = "osmid",
    undirected: bool = False,
) -> Union[nx.MultiDiGraph, nx.MultiGraph]:
    """
    Load a graph from saved nodes/edges files and rebuild an OSMnx graph.

    Reads nodes/edges (GeoJSON/GPKG/etc.), restores required indexes:
    - nodes indexed by node_id_col (default 'osmid')
    - edges indexed by ['u','v','key']

    Provide either:
    - (out_dir + base_name) following AccessX naming convention, OR
    - explicit nodes_path and edges_path.
    
    undirected : bool
        If True, convert the rebuilt graph to an undirected MultiGraph.
        ox.graph_from_gdfs always rebuilds a MultiDiGraph, so this must be
        requested explicitly to match the graph that was originally saved.
    """
    # Determine paths
    if nodes_path is None or edges_path is None:
        if out_dir is None or base_name is None:
            raise ValueError("Provide either (nodes_path AND edges_path) OR (out_dir AND base_name).")
        out_dir = Path(out_dir)
        nodes_path = out_dir / f"{base_name}_nodes_OSM.geojson"
        edges_path = out_dir / f"{base_name}_edges_OSM.geojson"

    nodes_path = Path(nodes_path)
    edges_path = Path(edges_path)

    if not nodes_path.exists():
        raise FileNotFoundError(f"Nodes file not found: {nodes_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"Edges file not found: {edges_path}")

    # Read
    nodes = read_gdf(nodes_path, crs=crs)
    edges = read_gdf(edges_path, crs=crs)

    # --- Restore nodes index ---
    if node_id_col in nodes.columns:
        nodes = nodes.set_index(node_id_col, drop=True)
    else:
        raise ValueError(
            f"Nodes file must contain a '{node_id_col}' column to rebuild the graph. "
            f"Columns found: {list(nodes.columns)}"
        )

    # --- Restore edges MultiIndex ---
    required = ["u", "v", "key"]
    missing = [c for c in required if c not in edges.columns]
    if missing:
        raise ValueError(
            f"Edges file must contain columns {required}. Missing: {missing}. "
            "Make sure edges were saved with edges_gdf.reset_index()."
        )

    # Ensure integer-like types (GeoJSON can read these as floats/strings)
    for c in required:
        edges[c] = edges[c].astype("int64")

    edges = edges.set_index(required, drop=True)

    # Rebuild node geometry from x/y to avoid precision mismatches after file round-trip.
    if "x" not in nodes.columns or "y" not in nodes.columns:
        raise ValueError(
            "Nodes file must contain 'x' and 'y' columns to rebuild geometry consistently."
        )
    nodes["geometry"] = gpd.points_from_xy(nodes["x"], nodes["y"], crs=nodes.crs)

    # Rebuild graph
    G = ox.graph_from_gdfs(nodes, edges)

    # Keep CRS at graph level for downstream work
    if nodes.crs is not None:
        G.graph["crs"] = nodes.crs

    # graph_from_gdfs always returns a MultiDiGraph; convert here if the saved
    # graph was undirected, since directedness is not round-tripped through files.
    if undirected:
        G = ox.convert.to_undirected(G)

    return G

def build_network(
    AOI: gpd.GeoDataFrame,
    *,
    city_epsg: Union[int, str],
    buffer_m: float = 0.0,
    network_type: str = "walk",
    simplify: bool = False,
    retain_all: bool = True,
    remove_isolates: bool = True,
    undirected: bool = False,
    show_progress: bool = False,
) -> Union[nx.MultiDiGraph, nx.MultiGraph]:
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
    undirected : bool
        If True, convert the projected graph to an undirected MultiGraph after
        building. Only lossless for network_type="walk" (OSMnx's only
        bidirectional-by-default network type); direction-dependent edge costs
        (e.g. slope-based) cannot be added correctly afterward.
    show_progress : bool
        If True, show high-level progress while preparing, downloading, cleaning,
        and projecting the network.

    Returns
    -------
    MultiDiGraph | MultiGraph
        Projected graph in city_epsg. MultiGraph if undirected=True.
    """
    if AOI is None or len(AOI) == 0:
        raise ValueError("AOI is empty.")
    if AOI.crs is None:
        raise ValueError("AOI must have a CRS.")

    if undirected and network_type != "walk":
        warnings.warn(
            f"undirected=True with network_type='{network_type}': OSMnx only builds 'walk' "
            "networks as fully bidirectional by default, so collapsing this graph to "
            "undirected can discard real one-way restrictions.",
            RuntimeWarning,
            stacklevel=2,
        )

    progress_bar = _make_progress_bar(
        show_progress, total=5 if undirected else 4, desc="Building street network"
    )

    try:
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
        _advance_progress(progress_bar, "Prepared AOI")

        # collecting the street network (your code style)
        G_wgs84 = ox.graph_from_polygon(
            polygon=AOI_wgs84_buffer.iloc[0].geometry,
            network_type=network_type,
            simplify=simplify,
            retain_all=retain_all,
        )
        _advance_progress(progress_bar, "Downloaded OSM network")

        # remove isolates (your code style)
        if remove_isolates:
            G_wgs84.remove_nodes_from(list(nx.isolates(G_wgs84)))
        _advance_progress(progress_bar, "Cleaned graph")

        # reproject the walking network (your code style)
        G_proj = ox.project_graph(G_wgs84, to_crs=city_epsg)
        _advance_progress(progress_bar, "Projected graph")

        if undirected:
            G_proj = ox.convert.to_undirected(G_proj)
            _advance_progress(progress_bar, "Converted to undirected")

        return G_proj
    finally:
        if progress_bar is not None:
            progress_bar.close()
