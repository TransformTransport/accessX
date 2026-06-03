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
DEFAULT_REQUEST_PAUSE = 2.0


class POIQueryError(RuntimeError):
    """Raised when one or more OSM POI tag queries fail after retries."""


def _is_no_matching_features_error(error: InsufficientResponseError) -> bool:
    return "No matching features" in str(error)


def _query_features_with_retries(
    polygon,
    tags: Dict,
    *,
    tag_key: str,
    max_retries: int,
):
    attempt = 0
    while True:
        try:
            return ox.features_from_polygon(polygon, tags=tags)
        except InsufficientResponseError as error:
            if _is_no_matching_features_error(error):
                raise
            query_error = error
        except TRANSIENT_OSM_ERRORS as error:
            query_error = error

        if attempt >= max_retries:
            raise query_error

        sleep_seconds = DEFAULT_RETRY_DELAY * (DEFAULT_RETRY_BACKOFF ** attempt)
        warnings.warn(
            (
                f"OSM POI query for tag '{tag_key}' failed on attempt "
                f"{attempt + 1}/{max_retries + 1}: {query_error}. "
                f"Retrying in {sleep_seconds:.1f}s."
            ),
            RuntimeWarning,
            stacklevel=2,
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        attempt += 1


def _summarize_failed_queries(failed_queries):
    parts = []
    for item in failed_queries:
        parts.append(
            f"{item['query_label']} ({item['error_type']}: {item['message']})"
        )
    return "; ".join(parts)


def _pause_between_queries(current_index: int, total_tags: int) -> None:
    if DEFAULT_REQUEST_PAUSE > 0 and current_index < total_tags - 1:
        time.sleep(DEFAULT_REQUEST_PAUSE)


def _with_added_columns(gdf: gpd.GeoDataFrame, data: Dict) -> gpd.GeoDataFrame:
    columns = pd.DataFrame(data, index=gdf.index)
    return gpd.GeoDataFrame(
        pd.concat([gdf, columns], axis=1),
        geometry=gdf.geometry.name,
        crs=gdf.crs,
    )


def _iter_queries_with_progress(queries, show_progress: bool):
    if not show_progress:
        return enumerate(queries)
    try:
        from tqdm.auto import tqdm
    except ImportError:
        warnings.warn(
            "show_progress=True requires tqdm. Install tqdm or set show_progress=False.",
            RuntimeWarning,
            stacklevel=2,
        )
        return enumerate(queries)
    return enumerate(tqdm(queries, desc="Retrieving POIs", unit="query"))


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
    columns="minimal",  # "minimal" | "all" | list of extra columns
):
    """
    Collect Points of Interest (POIs) from OpenStreetMap within a given Area of Interest (AOI).

    This function queries OSM using explicit OSM tags. Use `osm_tags` for direct
    OSM-key categories, or `poi_groups` when you want custom analytical categories
    such as healthcare, open_space, or daily_needs.

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
        Number of retry attempts for transient OSM/Overpass failures per OSM tag key.
        Total attempts per OSM tag key are max_retries + 1.
    show_progress : bool, default False
        If True, show a progress bar over requested OSM queries.
    return_report : bool, default False
        If True, return (pois, report). The report contains requested queries,
        retrieved counts, empty queries, and failed query details.
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

    for idx, query in _iter_queries_with_progress(queries, show_progress):
        tag_key = query["osm_key"]
        tags = {tag_key: query["osm_values"]}

        try:
            gdf = _query_features_with_retries(
                poly,
                tags,
                tag_key=query["query_label"],
                max_retries=max_retries,
            )
        except InsufficientResponseError as error:
            if not _is_no_matching_features_error(error):
                report["failed_queries"].append(
                    {
                        "category": query["category"],
                        "osm_key": tag_key,
                        "query_label": query["query_label"],
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                )
            else:
                report["empty_queries"].append(query["query_label"])
            _pause_between_queries(idx, len(queries))
            continue
        except TRANSIENT_OSM_ERRORS as error:
            report["failed_queries"].append(
                {
                    "category": query["category"],
                    "osm_key": tag_key,
                    "query_label": query["query_label"],
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            _pause_between_queries(idx, len(queries))
            continue

        if gdf is None or len(gdf) == 0:
            report["empty_queries"].append(query["query_label"])
            _pause_between_queries(idx, len(queries))
            continue

        gdf = gdf[gdf.geometry.geom_type.isin(keep_geom_types)].copy()
        if len(gdf) == 0:
            report["empty_queries"].append(query["query_label"])
            _pause_between_queries(idx, len(queries))
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
        _pause_between_queries(idx, len(queries))

    if report["failed_queries"]:
        message = (
            "Some OSM POI tag queries failed after retries. "
            "Returned POIs may be incomplete. Failed OSM queries: "
            f"{_summarize_failed_queries(report['failed_queries'])}. "
            "Try increasing max_retries or OSMnx settings.requests_timeout."
        )
        raise POIQueryError(message)

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
        return (empty_gdf, report) if return_report else empty_gdf

    # now safe to ignore_index because osm_type/osmid are real columns
    all_gdf = gpd.GeoDataFrame(pd.concat(dfs, ignore_index=True), crs=AOI_wgs84.crs).copy()

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

    return (all_gdf, report) if return_report else all_gdf
