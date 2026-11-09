# accessx/graph.py
from __future__ import annotations

import json
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
    format: str = "geojson",
    save_nodes: bool = True,
    save_edges: bool = True,
    driver: Optional[str] = None,
) -> None:
    """Save a graph as GeoJSON, GeoPackage or OSM PBF.

    ``format="geojson"`` (default) and ``format="gpkg"`` write separate
    ``<base_name>_nodes_OSM`` and ``<base_name>_edges_OSM`` files with the
    corresponding extension. ``save_nodes``/``save_edges`` select these tables.
    The legacy ``driver`` argument also accepts GeoJSON or GPKG.

    ``format="pbf"`` writes only ``<base_name>.osm.pbf`` from the entire graph;
    the node/edge switches do not apply. Intermediate geometry and costs are
    retained, with accessX tags for reloading attribute types and graph metadata.
    Generated OSM IDs represent one way per graph edge. Directed edges use
    oneway=yes (including foot/bicycle); undirected edges use oneway=no.
    Turn restrictions are not reconstructed. r5r does not automatically use
    custom cost tags as routing weights.
    """
    format = (driver or format).lower()
    if format not in {"geojson", "gpkg", "pbf"}:
        raise ValueError("format must be 'geojson', 'gpkg', or 'pbf'.")
    if format != "pbf" and not save_nodes and not save_edges:
        raise ValueError("At least one of save_nodes or save_edges must be True.")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if format == "pbf":
        _save_graph_pbf(G, out_dir / f"{base_name}.osm.pbf")
        return

    ogr_driver = "GeoJSON" if format == "geojson" else "GPKG"
    nodes, edges = ox.graph_to_gdfs(G)
    if format == "gpkg":
        # GeoPackage has no list field type. Mark JSON lists for lossless reload.
        for frame in (nodes, edges):
            for column in frame.columns.drop(frame.geometry.name):
                frame[column] = frame[column].map(
                    lambda value: "accessx:list:" + json.dumps(list(value), default=str)
                    if isinstance(value, (list, tuple, set)) else value
                )
    if save_nodes:
        save_gdf(nodes, out_dir / f"{base_name}_nodes_OSM.{format}", driver=ogr_driver)
    if save_edges:
        save_gdf(edges.reset_index(), out_dir / f"{base_name}_edges_OSM.{format}", driver=ogr_driver)


def load_graph(
    *,
    out_dir: Optional[Union[str, Path]] = None,
    base_name: Optional[str] = None,
    nodes_path: Optional[Union[str, Path]] = None,
    edges_path: Optional[Union[str, Path]] = None,
    pbf_path: Optional[Union[str, Path]] = None,
    format: str = "auto",
    crs: Optional[Union[int, str]] = None,
    node_id_col: str = "osmid",
    undirected: bool = False,
) -> Union[nx.MultiDiGraph, nx.MultiGraph]:
    """Load an accessX graph, detecting GeoJSON, GeoPackage or PBF by filename.

    Supply ``out_dir`` and ``base_name``, a pair of ``nodes_path``/``edges_path``,
    or a single ``pbf_path``. If a directory contains multiple matching formats,
    select one explicitly with ``format="geojson"``, ``"gpkg"`` or ``"pbf"``.
    Otherwise ``format="auto"`` detects the only available format.

    PBF loading supports accessX exports, not arbitrary regional OSM extracts.
    New PBF exports retain graph direction, CRS, IDs and cost attribute types.
    ``crs`` optionally projects the loaded graph to a different CRS. For node/edge
    files, pass ``undirected=True`` when the saved graph was undirected.
    """
    format = format.lower()
    if format not in {"auto", "geojson", "gpkg", "pbf"}:
        raise ValueError("format must be 'auto', 'geojson', 'gpkg', or 'pbf'.")
    if pbf_path is not None:
        if nodes_path is not None or edges_path is not None or format not in {"auto", "pbf"}:
            raise ValueError("Use pbf_path alone with format='auto' or 'pbf'.")
        return _load_graph_pbf(Path(pbf_path), crs=crs, undirected=undirected)
    if nodes_path is not None or edges_path is not None:
        if nodes_path is None or edges_path is None:
            raise ValueError("Provide both nodes_path and edges_path.")
        extension = Path(nodes_path).suffix.lower().lstrip(".")
        if extension not in {"geojson", "gpkg"} or Path(edges_path).suffix.lower() != f".{extension}":
            raise ValueError("Node/edge files must both be .geojson or both be .gpkg.")
        if format not in {"auto", extension}:
            raise ValueError("format does not match the node/edge filename extensions.")
    else:
        if out_dir is None or base_name is None:
            raise ValueError("Provide out_dir and base_name, node/edge paths, or pbf_path.")
        out_dir = Path(out_dir)
        if format == "auto":
            available = [extension for extension in ("geojson", "gpkg")
                         if (out_dir / f"{base_name}_nodes_OSM.{extension}").is_file()
                         and (out_dir / f"{base_name}_edges_OSM.{extension}").is_file()]
            if (out_dir / f"{base_name}.osm.pbf").is_file():
                available.append("pbf")
            if not available:
                raise FileNotFoundError(f"No saved graph found for '{base_name}' in {out_dir}.")
            if len(available) > 1:
                raise ValueError(f"Multiple graph formats found: {available}. Select format explicitly.")
            format = available[0]
        if format == "pbf":
            return _load_graph_pbf(out_dir / f"{base_name}.osm.pbf", crs=crs, undirected=undirected)
        nodes_path = out_dir / f"{base_name}_nodes_OSM.{format}"
        edges_path = out_dir / f"{base_name}_edges_OSM.{format}"

    nodes_path = Path(nodes_path)
    edges_path = Path(edges_path)

    if not nodes_path.exists():
        raise FileNotFoundError(f"Nodes file not found: {nodes_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"Edges file not found: {edges_path}")

    # Read
    nodes = read_gdf(nodes_path)
    edges = read_gdf(edges_path)
    # Restore marked GPKG lists and normalize arrays returned by some GDAL readers.
    for frame in (nodes, edges):
        for column in frame.columns.drop(frame.geometry.name):
            frame[column] = frame[column].map(
                lambda value: json.loads(value.removeprefix("accessx:list:"))
                if nodes_path.suffix.lower() == ".gpkg" and isinstance(value, str)
                and value.startswith("accessx:list:")
                else value.tolist() if hasattr(value, "tolist") else value
            )

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

    # Project geometry and x/y together; assigning a new CRS alone misplaces nodes.
    if crs is not None:
        nodes = nodes.set_crs(crs) if nodes.crs is None else nodes.to_crs(crs)
        edges = edges.set_crs(crs) if edges.crs is None else edges.to_crs(crs)
        nodes["x"], nodes["y"] = nodes.geometry.x, nodes.geometry.y

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

def _pbf_tags(attributes):
    """Store readable OSM tags plus original attribute types for accessX reloads."""
    import pandas as pd

    tags, original = {}, {}
    for key, value in attributes.items():
        if key in {"geometry", "x", "y"} or isinstance(value, dict):
            continue
        if isinstance(value, (list, tuple, set)):
            original[key] = list(value)
            tags[str(key)] = ";".join(str(item) for item in value)
        elif value is not None and pd.notna(value):
            value = value.item() if hasattr(value, "item") else value
            original[key] = value
            tags[str(key)] = ("yes" if value else "no") if isinstance(value, bool) else str(value)
    tags["accessx:attributes"] = json.dumps(original, default=str)
    return tags


def _load_graph_pbf(path, *, crs, undirected):
    """Read the OSM way stored for each accessX graph edge, without downloads."""
    import osmium
    from shapely.geometry import LineString

    if not path.is_file():
        raise FileNotFoundError(f"PBF file not found: {path}")
    locations, endpoints = {}, {}
    graph = nx.MultiDiGraph(crs=4326, simplified=True)
    metadata = {}
    saved_directed = True
    for element in osmium.FileProcessor(str(path)):
        tags = dict(element.tags)
        if element.is_node():
            locations[element.id] = (element.location.lon, element.location.lat)
            if "accessx:graph" in tags:
                saved = json.loads(tags["accessx:graph"])
                metadata, saved_directed = saved["attributes"], saved["directed"]
            if "accessx:node_id" in tags:
                node_id = json.loads(tags["accessx:node_id"])
                endpoints[element.id] = node_id
                graph.add_node(node_id, x=element.location.lon, y=element.location.lat,
                               **json.loads(tags.get("accessx:attributes", "{}")))
        elif element.is_way():
            if "accessx:edge_key" not in tags:
                raise ValueError("PBF loading supports accessX exports, not regional OSM extracts.")
            references = [node.ref for node in element.nodes]
            u, v = (endpoints.get(ref, ref) for ref in (references[0], references[-1]))
            for node_id, ref in ((u, references[0]), (v, references[-1])):
                if node_id not in graph:
                    lon, lat = locations[ref]
                    graph.add_node(node_id, x=lon, y=lat)
            attributes = json.loads(tags.get("accessx:attributes", "{}"))
            if "accessx:attributes" not in tags:
                # Earlier accessX exports only stored string tags.
                for key, value in tags.items():
                    if not key.startswith("accessx:"):
                        try:
                            value = json.loads(value)
                        except ValueError:
                            pass
                        attributes[key] = value
            geometry = LineString([locations[ref] for ref in references])
            key = json.loads(tags["accessx:edge_key"])
            graph.add_edge(u, v, key=key, geometry=geometry, **attributes)
    if not graph:
        raise ValueError("PBF contains no accessX graph nodes.")
    graph = ox.project_graph(graph, to_crs=crs or metadata.get("crs", 4326))
    graph.graph.update({key: value for key, value in metadata.items() if key != "crs"})
    if undirected or not saved_directed:
        graph = ox.convert.to_undirected(graph)
    return graph


def _save_graph_pbf(G, path):
    """Export current topology, geometry and attributes using generated OSM IDs."""
    import tempfile
    import osmium

    nodes, edges = ox.graph_to_gdfs(G)
    nodes, edges = nodes.to_crs(4326), edges.to_crs(4326)
    node_ids = {node: index for index, node in enumerate(nodes.index, start=1)}
    locations = [(point.x, point.y) for point in nodes.geometry]
    ways = []
    for (u, v, key), edge in edges.iterrows():
        coords = list(edge.geometry.coords)
        start = locations[node_ids[u] - 1]
        # OSMnx edge geometry can be oriented opposite to the graph edge.
        if sum((coords[-1][i] - start[i]) ** 2 for i in (0, 1)) < sum(
            (coords[0][i] - start[i]) ** 2 for i in (0, 1)
        ):
            coords.reverse()
        references = [node_ids[u]]
        for coordinate in coords[1:-1]:
            locations.append(coordinate[:2])
            references.append(len(locations))
        references.append(node_ids[v])
        tags = _pbf_tags(edge)
        key = key.item() if hasattr(key, "item") else key
        tags["accessx:edge_key"] = json.dumps(key)
        # Direction follows the current graph, including directed cost variants.
        for tag in ("oneway", "oneway:foot", "oneway:bicycle"):
            tags[tag] = "yes" if G.is_directed() else "no"
        ways.append((references, tags))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / "network.osm.pbf"
        with osmium.SimpleWriter(str(temporary)) as writer:
            for node_id, location in enumerate(locations, start=1):
                attributes = nodes.iloc[node_id - 1] if node_id <= len(nodes) else {}
                tags = _pbf_tags(attributes)
                if node_id <= len(nodes):
                    original_id = nodes.index[node_id - 1]
                    original_id = original_id.item() if hasattr(original_id, "item") else original_id
                    tags["accessx:node_id"] = json.dumps(original_id)
                if node_id == 1:
                    tags["accessx:graph"] = json.dumps(
                        {"directed": G.is_directed(), "attributes": G.graph}, default=str,
                    )
                writer.add_node(osmium.osm.mutable.Node(id=node_id, location=location, tags=tags))
            for way_id, (references, tags) in enumerate(ways, start=1):
                writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=references, tags=tags))
        temporary.replace(path)


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
