from pathlib import Path
from typing import Optional, Union

import geopandas as gpd


def save_gdf(
    gdf: gpd.GeoDataFrame,
    path: Union[str, Path],
    driver: Optional[str] = None,
) -> None:
    """
    Save a GeoDataFrame to disk. Driver inferred from extension if not given.
    """
    path = Path(path)
    if driver is None:
        ext = path.suffix.lower()
        driver = "GeoJSON" if ext in [".geojson", ".json"] else None
    gdf.to_file(path, driver=driver)


def read_gdf(
    path: Union[str, Path],
    *,
    crs: Optional[Union[int, str]] = None,
) -> gpd.GeoDataFrame:
    """
    Read a GeoDataFrame from disk and optionally enforce a CRS.

    Parameters
    ----------
    path : str | Path
        Path to a vector file readable by GeoPandas.
    crs : int | str, optional
        If provided:
        - sets CRS if file has none
        - reprojects if file already has a CRS

    Returns
    -------
    GeoDataFrame
    """
    gdf = gpd.read_file(str(path))

    if crs is not None:
        if gdf.crs is None:
            gdf = gdf.set_crs(crs, allow_override=True)
        else:
            gdf = gdf.to_crs(crs)

    return gdf