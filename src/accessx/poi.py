# accessx/poi.py
from __future__ import annotations

import time
import warnings
from typing import Dict

import numpy as np
import pandas as pd
import geopandas as gpd
import osmnx as ox
import requests
from shapely.geometry import MultiPolygon
from osmnx._errors import InsufficientResponseError, ResponseStatusCodeError


def multipolygon_to_polygon(geometry):
    if isinstance(geometry, MultiPolygon):
        return geometry.convex_hull
    return geometry


def safe_convex(geom):
    if geom.geom_type in ["Polygon", "MultiPolygon", "GeometryCollection"]:
        return geom.convex_hull
    return geom


TRANSIENT_OSM_ERRORS = (
    ResponseStatusCodeError,
    requests.exceptions.RequestException,
    TimeoutError,
)

DEFAULT_RETRY_DELAY = 5.0
DEFAULT_RETRY_BACKOFF = 2.0


class POIQueryError(RuntimeError):
    """Raised when OSM POI retrieval cannot complete."""


def _is_no_matching_features_error(error: InsufficientResponseError) -> bool:
    return "No matching features" in str(error)


def _query_features_with_retries(
    polygon,
    tags: Dict,
    *,
    category: str,
    max_retries: int,
    report=None,
):
    attempt = 0
    while True:
        try:
            if report is not None:
                report["query_attempts"] += 1
            return ox.features_from_polygon(polygon, tags=tags)
        except InsufficientResponseError as error:
            if _is_no_matching_features_error(error):
                raise
            query_error = error
        except TRANSIENT_OSM_ERRORS as error:
            query_error = error

        # A size rejection will not improve by resending the identical request.
        if isinstance(query_error, ResponseStatusCodeError) and "413" in str(query_error):
            raise query_error
        if attempt >= max_retries:
            raise query_error

        sleep_seconds = DEFAULT_RETRY_DELAY * (DEFAULT_RETRY_BACKOFF ** attempt)
        warnings.warn(
            (
                f"OSM POI query for category '{category}' failed on attempt "
                f"{attempt + 1}/{max_retries + 1}: {query_error}. "
                f"Retrying in {sleep_seconds:.1f}s."
            ),
            RuntimeWarning,
            stacklevel=2,
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        attempt += 1
        if report is not None:
            report["retries"] += 1


def _overpass_cache_paths(polygon, tags):
    """Match OSMnx's response cache keys without sending requests.

    OSMnx has no public per-query refresh API, so keep its private helpers here.
    """
    url = ox.settings.overpass_url.rstrip("/") + "/interpreter"
    paths = []
    for coordinates in ox._overpass._make_overpass_polygon_coord_strs(polygon):
        query = ox._overpass._create_overpass_features_query(coordinates, tags)
        key = requests.Request("GET", url, params={"data": query}).prepare().url
        paths.append(ox._http._resolve_cache_filepath(key))
    return paths


def _retrieve_overpass_categories(polygon, queries, *, refresh, max_retries, progress, report):
    """One OSMnx call per category; every category must succeed."""
    categories = {}
    for query in queries:
        categories.setdefault(query["category"], {})[query["osm_key"]] = query["osm_values"]

    results = {}
    original_use_cache = ox.settings.use_cache
    ox.settings.use_cache = True
    report["from_cache"] = True
    try:
        for category, tags in categories.items():
            paths = _overpass_cache_paths(polygon, tags)
            cached = not refresh and all(path.is_file() for path in paths)
            report["from_cache"] = report["from_cache"] and cached
            if refresh:
                # Evict only these responses; OSMnx writes fresh responses back.
                for path in paths:
                    path.unlink(missing_ok=True)
                progress(f"Refreshing Overpass category: {category}...")
            elif cached:
                progress(f"Using cached Overpass category: {category}.")
            else:
                progress(f"Querying Overpass category: {category}...")
            try:
                results[category] = _query_features_with_retries(
                    polygon, tags, category=category, max_retries=max_retries, report=report,
                ).sort_index()
            except InsufficientResponseError as error:
                if not _is_no_matching_features_error(error):
                    raise POIQueryError(f"Overpass category '{category}' failed: {error}") from error
                results[category] = gpd.GeoDataFrame(geometry=[], crs=4326)
            except TRANSIENT_OSM_ERRORS as error:
                if isinstance(error, ResponseStatusCodeError) and "413" in str(error):
                    raise POIQueryError(
                        f"Overpass category '{category}' request is too large (HTTP 413). "
                        "Reduce the tags in this category or use source='pbf'."
                    ) from error
                raise POIQueryError(f"Overpass category '{category}' failed: {error}") from error
    finally:
        ox.settings.use_cache = original_use_cache
    return results


def _with_added_columns(gdf: gpd.GeoDataFrame, data: Dict) -> gpd.GeoDataFrame:
    columns = pd.DataFrame(data, index=gdf.index)
    return gpd.GeoDataFrame(
        pd.concat([gdf, columns], axis=1),
        geometry=gdf.geometry.name,
        crs=gdf.crs,
    )


def _validate_osm_tags(osm_tags: Dict) -> Dict:
    if not isinstance(osm_tags, dict) or not osm_tags:
        raise ValueError("osm_tags must be a non-empty dictionary of OSM tag keys to values.")

    normalized = {}
    for tag_key, tag_values in osm_tags.items():
        if not isinstance(tag_key, str) or not tag_key:
            raise ValueError("Every osm_tags key must be a non-empty string.")
        if tag_values is True:
            normalized[tag_key] = True
        elif isinstance(tag_values, str):
            normalized[tag_key] = tag_values
        elif isinstance(tag_values, (list, tuple, set)):
            values = list(tag_values)
            if not values or not all(isinstance(value, str) and value for value in values):
                raise ValueError(
                    "OSM tag value lists must contain at least one non-empty string."
                )
            normalized[tag_key] = values
        else:
            raise ValueError(
                "Each osm_tags value must be True, a string, or a list/tuple/set of strings."
            )
    return normalized


def _build_poi_queries(*, osm_tags=None, poi_groups=None):
    if (osm_tags is None) == (poi_groups is None):
        raise ValueError("Provide exactly one of osm_tags or poi_groups.")

    if osm_tags is not None:
        normalized_tags = _validate_osm_tags(osm_tags)
        return [
            {
                "category": tag_key,
                "osm_key": tag_key,
                "osm_values": tag_values,
                "query_label": tag_key,
            }
            for tag_key, tag_values in normalized_tags.items()
        ]

    if not isinstance(poi_groups, dict) or not poi_groups:
        raise ValueError("poi_groups must be a non-empty dictionary of category names to OSM tags.")

    queries = []
    for category, group_tags in poi_groups.items():
        if not isinstance(category, str) or not category:
            raise ValueError("Every poi_groups category must be a non-empty string.")
        normalized_tags = _validate_osm_tags(group_tags)
        for tag_key, tag_values in normalized_tags.items():
            queries.append(
                {
                    "category": category,
                    "osm_key": tag_key,
                    "osm_values": tag_values,
                    "query_label": f"{category}:{tag_key}",
                }
            )
    return queries


def _merge_query_tags(queries):
    """Union tag values, retaining insertion order; True includes every value."""
    tags = {}
    for query in queries:
        key, values = query["osm_key"], query["osm_values"]
        if values is True or tags.get(key) is True:
            tags[key] = True
        else:
            values = [values] if isinstance(values, str) else values
            tags[key] = list(dict.fromkeys(tags.get(key, []) + values))
    return tags


def _subcategory_values(gdf: gpd.GeoDataFrame, tag_key: str) -> pd.Series:
    if tag_key not in gdf.columns:
        return pd.Series([None] * len(gdf), index=gdf.index, dtype="object")
    return gdf[tag_key].astype("object")


# -----------------------------
# core functions
# -----------------------------

def get_pois_osm(
    AOI_wgs84: gpd.GeoDataFrame,
    *,
    osm_tags: Dict | None = None,
    poi_groups: Dict | None = None,
    keep_geom_types=("Point", "Polygon", "MultiPolygon"),
    raise_on_empty: bool = False,
    max_retries: int = 2,
    show_progress: bool = False,
    return_report: bool = False,
    source="pbf",
    pbf_path=None,
    cache_dir=None,
    refresh=False,
    columns="minimal",  # "minimal" | "all" | list of extra columns
):
    """
    Collect Points of Interest (POIs) from OpenStreetMap within a given Area of Interest (AOI).

    This function queries OSM using explicit OSM tags. Use `osm_tags` for direct
    OSM-key categories, or `poi_groups` when you want custom analytical categories
    such as healthcare, open_space, or daily_needs. Overpass queries one category
    at a time, combining that category's tag filters.

    Queries with no matching features inside the AOI are silently skipped by default.
    If no POIs are found for any query, an empty GeoDataFrame is returned (unless
    raise_on_empty=True).

    Parameters
    ----------
    AOI_wgs84 : geopandas.GeoDataFrame
        Area of Interest in EPSG:4326 (WGS84). Must contain at least one polygon geometry.
        If multiple geometries are present, only the first is used.
    osm_tags : dict, optional
        OSM tags to query. Keys are OSM tag keys. Values can be:
        - True: any value for this key, e.g. {"healthcare": True}
        - str: one OSM value, e.g. {"amenity": "pharmacy"}
        - list/tuple/set[str]: selected OSM values, e.g. {"amenity": ["pharmacy", "clinic"]}
        When `osm_tags` is used, output `category` is the OSM key.
    poi_groups : dict, optional
        Custom POI groups. Top-level keys become output `category` values. Each
        value is an OSM tag dictionary with the same value rules as `osm_tags`, e.g.
        {"healthcare": {"amenity": ["pharmacy", "clinic"], "healthcare": True}}.
    keep_geom_types : tuple of str, optional
        Geometry types to retain from OSM features. Default keeps Points and (Multi)Polygons.
    raise_on_empty : bool, default False
        If True, raise an InsufficientResponseError when no POIs are found for any OSM tag.
        If False, return an empty GeoDataFrame.
    max_retries : int, default 2
        Number of retries for transient Overpass failures per category.
        Total attempts per category are max_retries + 1. HTTP 413 is not retried.
    show_progress : bool, default False
        If True, print concise source, retrieval and completion messages.
    return_report : bool, default False
        If True, return (pois, report). The report contains requested queries,
        retrieved counts, empty queries, and failed query details.
    source : {"pbf", "overpass"}, default "pbf"
        "pbf" reuses a covering cached regional PBF or downloads the smallest
        covering Geofabrik region. "overpass" queries the API one category at a
        time. Neither source falls back to the other.
    pbf_path : path-like, optional
        Local PBF; the caller is responsible for its coverage and completeness.
    cache_dir : path-like, optional
        PBF cache, default ~/.cache/accessx/pbf.
    refresh : bool, default False
        False: reuse cached Overpass responses or downloaded/processed PBF data;
        retrieve missing data as needed. True: replace matching Overpass responses
        or redownload the regional PBF and recompute POIs. A supplied local
        pbf_path is reread, not downloaded or replaced.
    columns : {"minimal","all"} or list[str], default "minimal"
        Controls which columns are returned:
        - "minimal": return only core columns (recommended default)
        - "all": return all columns returned by OSMnx/OSM
        - list: return core columns + these extra columns (if present)

    Returns
    -------
    geopandas.GeoDataFrame
        GeoDataFrame containing OSM-derived POIs.
    """
    started = time.perf_counter()
    if source not in {"pbf", "overpass"}:
        raise ValueError("source must be 'pbf' or 'overpass'.")
    def progress(message):
        if show_progress:
            print(f"accessX | {message}")
    progress("Retrieving OSM POIs...")
    if AOI_wgs84 is None or len(AOI_wgs84) == 0:
        raise ValueError("AOI_wgs84 is empty.")
    if AOI_wgs84.crs is None:
        raise ValueError("AOI_wgs84 must have a CRS.")
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0.")

    AOI_wgs84 = AOI_wgs84.to_crs(4326)
    queries = _build_poi_queries(osm_tags=osm_tags, poi_groups=poi_groups)

    poly = AOI_wgs84.iloc[0].geometry
    if poly is None or poly.is_empty:
        raise ValueError("AOI_wgs84 first geometry is empty.")

    dfs = []
    report = {
        "requested_queries": [
            {
                "category": query["category"],
                "osm_key": query["osm_key"],
                "osm_values": query["osm_values"],
                "query_label": query["query_label"],
            }
            for query in queries
        ],
        "retrieved_counts": {},
        "empty_queries": [],
        "failed_queries": [],
    }

    report.update(source=source, from_cache=None, query_attempts=0, retries=0,
                  warnings=[], success=False, download_seconds=0.0)
    tags = _merge_query_tags(queries)
    raw = None
    if source == "pbf":
        from ._poi_pbf import retrieve_pbf
        try:
            raw = retrieve_pbf(poly, tags, pbf_path=pbf_path, cache_dir=cache_dir,
                               refresh=refresh, progress=progress, report=report)
        except Exception as error:
            raise POIQueryError(f"PBF retrieval failed: {error}") from error
        raw = raw.sort_index()
    else:
        progress("Source: Overpass API.")
        category_results = _retrieve_overpass_categories(
            poly, queries, refresh=refresh, max_retries=max_retries,
            progress=progress, report=report,
        )
    # Both sources use the same local classification, including overlapping groups.
    for query in queries:
        features = category_results[query["category"]] if report["source"] == "overpass" else raw
        tag_key = query["osm_key"]
        values = query["osm_values"]
        if tag_key not in features:
            gdf = features.iloc[:0].copy()
        else:
            mask = features[tag_key].notna() if values is True else features[tag_key].isin(
                [values] if isinstance(values, str) else values)
            gdf = features.loc[mask].copy()

        if gdf is None or len(gdf) == 0:
            report["empty_queries"].append(query["query_label"])
            continue

        gdf = gdf[gdf.geometry.geom_type.isin(keep_geom_types)].copy()
        if len(gdf) == 0:
            report["empty_queries"].append(query["query_label"])
            continue

        # IMPORTANT: preserve OSM identity BEFORE concat(ignore_index=True)
        if isinstance(gdf.index, pd.MultiIndex) and gdf.index.nlevels >= 2:
            osm_type = gdf.index.get_level_values(0)
            osmid = gdf.index.get_level_values(1)
        else:
            osm_type = None
            osmid = None

        gdf = _with_added_columns(
            gdf,
            {
                "osmid": osmid,
                "osm_type": osm_type,
                "category": query["category"],
                "osm_key": tag_key,
                "osm_value": _subcategory_values(gdf, tag_key),
                "subcategory": _subcategory_values(gdf, tag_key),
            },
        )
        report["retrieved_counts"][query["query_label"]] = int(len(gdf))
        dfs.append(gdf)

    report.update(success=True, elapsed_seconds=time.perf_counter() - started)
    if not dfs:
        if raise_on_empty:
            raise InsufficientResponseError("No matching features for any selected OSM tag.")
        empty_gdf = gpd.GeoDataFrame(
            columns=[
                "id",
                "osmid",
                "name",
                "osm_type",
                "category",
                "subcategory",
                "osm_key",
                "osm_value",
                "geometry",
            ],
            geometry="geometry",
            crs=AOI_wgs84.crs,
        )
        report["n_pois"] = 0
        progress(f"Found 0 POIs in {report['elapsed_seconds']:.1f} seconds.")
        return (empty_gdf, report) if return_report else empty_gdf

    # now safe to ignore_index because osm_type/osmid are real columns
    all_gdf = gpd.GeoDataFrame(pd.concat(dfs, ignore_index=True), crs=AOI_wgs84.crs).copy()

    # Keep every category/key match, including overlapping OSM identities.

    # stable internal id
    added_cols = {"id": np.arange(len(all_gdf))}
    if "name" not in all_gdf.columns:
        added_cols["name"] = None
    all_gdf = _with_added_columns(all_gdf, added_cols)

    # ---- column selection + ordering ----
    core_cols = [
        "id",
        "osmid",
        "name",
        "osm_type",
        "category",
        "subcategory",
        "osm_key",
        "osm_value",
        "geometry",
    ]

    if columns == "minimal":
        all_gdf = all_gdf[core_cols]

    elif columns == "all":
        remaining_cols = [c for c in all_gdf.columns if c not in core_cols]
        all_gdf = all_gdf[core_cols + remaining_cols]

    elif isinstance(columns, (list, tuple, set)):
        extras = [c for c in columns if c in all_gdf.columns and c not in core_cols]
        all_gdf = all_gdf[core_cols + extras]

    else:
        raise ValueError("columns must be 'minimal', 'all', or a list/tuple/set of column names.")

    report.update(n_pois=len(all_gdf), elapsed_seconds=time.perf_counter() - started)
    progress(f"Found {len(all_gdf):,} POIs in {report['elapsed_seconds']:.1f} seconds.")
    return (all_gdf, report) if return_report else all_gdf
