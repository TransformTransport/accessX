from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, Union

import geopandas as gpd
from shapely.geometry import box

from accessx.io import save_gdf


BBox = Tuple[float, float, float, float]  # (minx, miny, maxx, maxy)


def _maybe_save(gdf, save_path):
    if save_path is not None:
        save_gdf(gdf, save_path)


def load_aoi(
    *,
    filepath: Optional[Union[str, Path]] = None,
    bbox: Optional[BBox] = None,
    input_crs: Optional[Union[int, str]] = None,
    buffer_m: float = 0.0,
    utm_crs: Optional[Union[int, str]] = None,
    dissolve: bool = True,
    target_crs: Union[int, str] = 4326,
    save_path: Optional[Union[str, Path]] = None,
) -> gpd.GeoDataFrame:
    """
    Load an AOI from file or bbox, optionally buffer in meters, optionally dissolve to a single geometry,
    return in target_crs (default EPSG:4326). Optionally save to disk.

    Parameters
    ----------
    filepath : str | Path, optional
        Path to AOI file (GeoPackage, GeoJSON, Shapefile, KML, etc.)
    bbox : (minx, miny, maxx, maxy), optional
        Bounding box. Assumed to be in `input_crs` if provided, else EPSG:4326.
    input_crs : int | str, optional
        CRS to force onto input geometry if missing/incorrect.
    buffer_m : float
        Buffer distance in meters (applied in `utm_crs`).
    utm_crs : int | str, optional
        Projected CRS to use for metric operations (meters). Required if buffer_m != 0.
        Despite the name, any projected CRS in meters works; UTM recommended.
    dissolve : bool
        If True, dissolve into a single (multi)polygon.
    target_crs : int | str
        CRS of returned AOI (default 4326).
    save_path : str | Path, optional
        If provided, saves the AOI to this path.

    Returns
    -------
    GeoDataFrame
        AOI geometry in target_crs.
    """
    if (filepath is None) == (bbox is None):
        raise ValueError("Provide exactly one of `filepath` or `bbox`.")

    # --- Load geometry ---
    if filepath is not None:
        gdf = gpd.read_file(str(filepath))
        if input_crs is not None:
            gdf = gdf.set_crs(input_crs, allow_override=True)
    else:
        crs = input_crs or 4326
        gdf = gpd.GeoDataFrame({"geometry": [box(*bbox)]}, crs=crs)

    # Basic cleaning
    gdf = gdf[gdf.geometry.notna()].copy()
    if len(gdf) == 0:
        raise ValueError("AOI has no valid geometries after removing null geometries.")

    # Dissolve to single polygon (recommended)
    if dissolve and len(gdf) > 1:
        gdf = gdf.dissolve(by=None).reset_index(drop=True)
    else:
        gdf = gdf.reset_index(drop=True)

    # Buffer (in meters) if requested
    if buffer_m and buffer_m != 0:
        if utm_crs is None:
            raise ValueError("utm_crs must be provided when buffer_m != 0 (meters).")
        g_proj = gdf.to_crs(utm_crs)
        g_proj["geometry"] = g_proj.geometry.buffer(buffer_m)
        gdf = g_proj

    # Return in desired CRS
    gdf = gdf.to_crs(target_crs)

    # Optional save
    _maybe_save(gdf, save_path)

    return gdf


def make_hex_grid(
    aoi: gpd.GeoDataFrame,
    *,
    resolution: int = 9,
    clip: bool = True,
    buffer: bool = True,
    return_geoms: bool = True,
    save_path: Optional[Union[str, Path]] = None,
) -> gpd.GeoDataFrame:
    """
    Create an H3 hex grid from an AOI using tobler.util.h3fy.

    Parameters
    ----------
    aoi : GeoDataFrame
        AOI polygon(s). If ensure_wgs84=True, will be converted to EPSG:4326 for H3.
    resolution : int
        H3 resolution.
    clip : bool
        If True, clip hexes to AOI footprint (depends on tobler implementation).
    buffer : bool
        f True, force hexagons to completely fill the interior of the source area. if False, (h3 default) may result in empty areas within the source area.
    return_geoms : bool
        If True, return hex geometries.
    save_path : str | Path, optional
        If provided, saves the hex grid to this path.

    Returns
    -------
    GeoDataFrame
        Hex grid with a `hex_id` column and geometry (if return_geoms=True), in EPSG:4326.
    """
    try:
        import tobler
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "make_hex_grid requires the 'tobler' package. Install accessx with its "
            "geospatial dependencies before calling this function."
        ) from exc

    aoi_in = aoi.to_crs(4326)

    hex_gdf = tobler.util.h3fy(
        aoi_in,
        resolution=resolution,
        clip=clip,
        buffer=buffer,
        return_geoms=return_geoms,
    )

    # Make sure hex_id is a normal column, not an index
    hex_gdf = hex_gdf.reset_index(drop=False).copy()

    # Normalize id column name
    if "hex_id" not in hex_gdf.columns:
        # Common patterns depending on versions
        for cand in ["h3_id", "h3", "index", "hex", "hexid"]:
            if cand in hex_gdf.columns:
                hex_gdf = hex_gdf.rename(columns={cand: "hex_id"})
                break

    # If reset_index created an "index" column holding ids
    if "hex_id" not in hex_gdf.columns and "index" in hex_gdf.columns:
        hex_gdf = hex_gdf.rename(columns={"index": "hex_id"})

    # Force CRS to 4326 when geometries are present
    if return_geoms and getattr(hex_gdf, "crs", None) is None:
        hex_gdf = hex_gdf.set_crs(4326, allow_override=True)
    if return_geoms:
        hex_gdf = hex_gdf.to_crs(4326)

    _maybe_save(hex_gdf, save_path)
    return hex_gdf
