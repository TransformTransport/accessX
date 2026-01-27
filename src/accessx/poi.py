# accessx/poi.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Union, Iterable

import numpy as np
import pandas as pd
import geopandas as gpd
import osmnx as ox
from shapely.geometry import MultiPolygon


# -----------------------------
# default OSM tag library
# -----------------------------

DEFAULT_TAGS: Dict[str, Dict] = {
    "greenspace": {
        "leisure": ["park", "nature_reserve"],
        "landuse": ["recreation_ground", "grass", "forest"],
        "natural": ["wood", "grassland", "scrub", "heath", "hill"],
    },
    "playground": {"leisure": ["playground"]},
    "supermarket": {"shop": ["supermarket"]},
    "bakery": {"shop": ["bakery"]},
    "butcher": {"shop": ["butcher"]},
    "greengrocer": {"shop": ["greengrocer"]},
    "seafood": {"shop": ["seafood"]},
    "school": {"amenity": ["kindergarten", "school"]},
    "culture": {"tourism": ["museum", "gallery", "artwork"], "amenity": ["arts_centre"]},
    "sport": {
        "leisure": ["sports_centre", "stadium", "sports_hall", "swimming_pool", "fitness_centre",
                    "fitness_station", "pitch", "track"],
        "sport": True,
    },
    "pharmacy": {"amenity": ["pharmacy"]},
    "nightlife": {
        "amenity": ["bar", "pub", "nightclub", "music_venue", "cinema", "theatre"],
        "tourism": ["nightlife"],
    },
    "metro": {"station": ["subway"]},
    "bus_tram": {
        "highway": ["bus_stop"],
        "railway": ["tram_stop"],
        "amenity": ["bus_station"],
        "public_transport": ["stop_position"],
    },
    "public_square": {"place": ["square"], "amenity": ["marketplace", "town_square"]},
    "cafe_restaurant": {"amenity": ["cafe", "restaurant", "fast_food"]},
    "clothing_stores": {"shop": ["clothes", "boutique", "fashion_accessories", "outfitter", "tailor", "second_hand"]},
    "university": {"amenity": ["college", "university"]},
    "library": {"amenity": ["library"]},
}


# -----------------------------
# small geometry helpers (your style)
# -----------------------------

def multipolygon_to_polygon(geometry):
    if isinstance(geometry, MultiPolygon):
        return geometry.convex_hull
    return geometry


def safe_convex(geom):
    if geom.geom_type in ["Polygon", "MultiPolygon", "GeometryCollection"]:
        return geom.convex_hull
    return geom


# -----------------------------
# core functions
# -----------------------------

from osmnx._errors import InsufficientResponseError

def get_pois_osm(
    AOI_wgs84: gpd.GeoDataFrame,
    *,
    categories=None,
    tags_library=None,
    keep_geom_types=("Point", "Polygon", "MultiPolygon"),
    raise_on_empty: bool = False,
    columns="minimal",  # "minimal" | "all" | list of extra columns
) -> gpd.GeoDataFrame:
    """
    Collect Points of Interest (POIs) from OpenStreetMap within a given Area of Interest (AOI).

    This function queries OSM using OSMnx for a set of semantic categories (e.g. pharmacy,
    playground, school). Each category is mapped to a dictionary of OSM tags. Results from
    all requested categories are concatenated into a single GeoDataFrame.

    Categories with no matching features inside the AOI are silently skipped by default.
    If no POIs are found for any category, an empty GeoDataFrame is returned (unless
    raise_on_empty=True).

    Parameters
    ----------
    AOI_wgs84 : geopandas.GeoDataFrame
        Area of Interest in EPSG:4326 (WGS84). Must contain at least one polygon geometry.
        If multiple geometries are present, only the first is used.
    categories : iterable of str, optional
        List of category names to query. Must correspond to keys in tags_library.
        If None, all categories in tags_library are queried.
    tags_library : dict, optional
        Dictionary mapping category names to OSM tag dictionaries.
        If None, DEFAULT_TAGS is used.
    keep_geom_types : tuple of str, optional
        Geometry types to retain from OSM features. Default keeps Points and (Multi)Polygons.
    raise_on_empty : bool, default False
        If True, raise an InsufficientResponseError when no POIs are found for any category.
        If False, return an empty GeoDataFrame.
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
    tags_library = tags_library or DEFAULT_TAGS
    if categories is None:
        categories = list(tags_library.keys())
    else:
        categories = list(categories)

    poly = AOI_wgs84.iloc[0].geometry

    dfs = []
    for cat in categories:
        tags = tags_library.get(cat)
        if tags is None:
            continue

        try:
            gdf = ox.features_from_polygon(poly, tags=tags)
        except InsufficientResponseError:
            continue

        if gdf is None or len(gdf) == 0:
            continue

        gdf = gdf[gdf.geometry.geom_type.isin(keep_geom_types)].copy()
        if len(gdf) == 0:
            continue

        # IMPORTANT: preserve OSM identity BEFORE concat(ignore_index=True)
        if isinstance(gdf.index, pd.MultiIndex) and gdf.index.nlevels >= 2:
            gdf["osm_type"] = gdf.index.get_level_values(0)
            gdf["osmid"] = gdf.index.get_level_values(1)
        else:
            gdf["osm_type"] = None
            gdf["osmid"] = None

        gdf["category"] = cat
        dfs.append(gdf)

    if not dfs:
        if raise_on_empty:
            raise InsufficientResponseError("No matching features for any selected category.")
        return gpd.GeoDataFrame(
            columns=["id", "osmid", "osm_type", "category", "geometry"],
            geometry="geometry",
            crs=AOI_wgs84.crs,
        )

    # now safe to ignore_index because osm_type/osmid are real columns
    all_gdf = gpd.GeoDataFrame(pd.concat(dfs, ignore_index=True), crs=AOI_wgs84.crs)

    # stable internal id
    all_gdf["id"] = np.arange(len(all_gdf))

    # ---- column selection + ordering ----
    core_cols = ["id", "osmid", "osm_type", "category", "geometry"]

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

    return all_gdf





