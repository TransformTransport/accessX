from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlencode
from urllib.request import urlopen, urlretrieve

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import box, mapping

from accessx.io import save_gdf


WORLDPOP_REST_ROOT = "https://www.worldpop.org/rest/data"
NATURAL_EARTH_URL = (
    "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip"
)


def _pixel_polygon(transform, row: int, col: int):
    upper_left_x, upper_left_y = rasterio.transform.xy(transform, row, col, offset="ul")
    lower_right_x, lower_right_y = rasterio.transform.xy(transform, row, col, offset="lr")
    return box(
        min(upper_left_x, lower_right_x),
        min(upper_left_y, lower_right_y),
        max(upper_left_x, lower_right_x),
        max(upper_left_y, lower_right_y),
    )


def _get_input_geometry(
    *,
    aoi: Optional[gpd.GeoDataFrame] = None,
    hexes: Optional[gpd.GeoDataFrame] = None,
) -> gpd.GeoDataFrame:
    if (aoi is None) == (hexes is None):
        raise ValueError("Provide exactly one of `aoi` or `hexes`.")

    geometry_gdf = aoi if aoi is not None else hexes
    if geometry_gdf is None or len(geometry_gdf) == 0:
        raise ValueError("Input geometry is empty.")
    if geometry_gdf.crs is None:
        raise ValueError("Input geometry must have a CRS.")

    return geometry_gdf


def infer_country_from_geometry(
    *,
    aoi: Optional[gpd.GeoDataFrame] = None,
    hexes: Optional[gpd.GeoDataFrame] = None,
) -> dict[str, str]:
    """
    Infer the country intersecting the supplied geometry the most.
    """
    geometry_gdf = _get_input_geometry(aoi=aoi, hexes=hexes)

    # Normalize the input geometry before matching it to country boundaries.
    geometry_union = geometry_gdf.to_crs(4326).geometry.union_all()
    if geometry_union.is_empty:
        raise ValueError("Input geometry has no valid area.")

    countries = gpd.read_file(NATURAL_EARTH_URL)[["ISO_A3", "NAME", "geometry"]].to_crs(4326)
    overlaps = countries[countries.geometry.intersects(geometry_union)].copy()
    if len(overlaps) == 0:
        raise ValueError("Could not match the input geometry to a country boundary.")

    # Compare overlap areas in an equal-area CRS so the largest match is robust.
    equal_area_crs = "EPSG:6933"
    geometry_equal_area = gpd.GeoSeries([geometry_union], crs=4326).to_crs(equal_area_crs).iloc[0]
    overlaps = overlaps.to_crs(equal_area_crs)
    overlaps["overlap_area"] = overlaps.geometry.intersection(geometry_equal_area).area
    best_match = overlaps.sort_values("overlap_area", ascending=False).iloc[0]

    return {"iso3": str(best_match["ISO_A3"]), "country": str(best_match["NAME"])}


def get_worldpop_raster(
    *,
    aoi: Optional[gpd.GeoDataFrame] = None,
    hexes: Optional[gpd.GeoDataFrame] = None,
    year: int,
    clip: bool = True,
    output_dir: Union[str, Path] = "cache/worldpop",
    save_path: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Download a WorldPop raster for the country covering the supplied geometry.

    If `clip=True`, the downloaded country raster is clipped to the `aoi` or
    `hexes` extent and the clipped raster path is returned.
    """
    geometry_gdf = _get_input_geometry(aoi=aoi, hexes=hexes)
    iso3 = infer_country_from_geometry(aoi=aoi, hexes=hexes)["iso3"]

    # Resolve the matching WorldPop country raster for the requested year.
    metadata_url = f"{WORLDPOP_REST_ROOT}/pop/wpgp?{urlencode({'iso3': iso3})}"
    with urlopen(metadata_url) as response:
        payload = json.load(response)
    matches = [entry for entry in payload.get("data", []) if str(entry.get("popyear")) == str(year)]
    if not matches:
        raise ValueError(f"No WorldPop population raster found for iso3={iso3} and year={year}.")
    files = matches[0].get("files") or []
    if not files:
        raise ValueError(f"WorldPop metadata for {iso3} {year} did not include a download URL.")
    source_url = str(files[0])

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    country_raster_path = output_dir / f"worldpop_{iso3.lower()}_{year}.tif"
    if not country_raster_path.exists():
        urlretrieve(source_url, country_raster_path)

    if not clip:
        return country_raster_path

    clipped_raster_path = (
        Path(save_path) if save_path is not None else output_dir / f"worldpop_{iso3.lower()}_{year}_clipped.tif"
    )

    # Clip the country raster down to the supplied AOI or hex extent.
    with rasterio.open(country_raster_path) as src:
        geometry_in_raster_crs = geometry_gdf.to_crs(src.crs)
        clipped_data, clipped_transform = mask(
            src,
            [mapping(geometry_in_raster_crs.geometry.union_all())],
            crop=True,
        )
        clipped_meta = src.meta.copy()
        clipped_meta.update(
            {
                "height": clipped_data.shape[1],
                "width": clipped_data.shape[2],
                "transform": clipped_transform,
            }
        )

    with rasterio.open(clipped_raster_path, "w", **clipped_meta) as dst:
        dst.write(clipped_data)

    return clipped_raster_path


def raster_to_population_grid(
    population_raster: Union[str, Path],
    *,
    band: int = 1,
    population_col: str = "population",
) -> gpd.GeoDataFrame:
    """
    Convert a population raster into a GeoDataFrame of raster-cell polygons.
    """
    raster_path = Path(population_raster)
    if not raster_path.exists():
        raise FileNotFoundError(f"Population raster not found: {raster_path}")

    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise ValueError("population_raster has no CRS.")

        raster_data = src.read(band, masked=True)
        rows, cols = np.where((~raster_data.mask) & np.isfinite(raster_data))

        # Convert every valid raster cell into a polygon with its population value.
        records = [
            {
                population_col: float(raster_data[row, col]),
                "geometry": _pixel_polygon(src.transform, row, col),
            }
            for row, col in zip(rows.tolist(), cols.tolist())
        ]
        return gpd.GeoDataFrame(records, geometry="geometry", crs=src.crs)


def map_population_to_hexes(
    hexes: gpd.GeoDataFrame,
    population_data: Union[str, Path, gpd.GeoDataFrame],
    *,
    metric_crs: Union[int, str],
    id_col: str = "hex_id",
    band: int = 1,
    population_col: str = "population",
    save_path: Optional[Union[str, Path]] = None,
) -> gpd.GeoDataFrame:
    """
    Allocate raster population totals to hexes using area-weighted overlap.

    `population_data` can be either a raster path or a GeoDataFrame of
    population-cell polygons such as the output of `raster_to_population_grid`.
    """
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if id_col not in hexes.columns:
        raise ValueError(f"hexes missing required id column '{id_col}'.")
    if metric_crs is None:
        raise ValueError("metric_crs must be provided.")

    output = hexes.copy()
    output[population_col] = 0.0

    # Accept either a raster path or a pre-built population grid.
    if isinstance(population_data, gpd.GeoDataFrame):
        cells = population_data.copy()
    else:
        cells = raster_to_population_grid(population_data, band=band, population_col=population_col)

    if len(cells) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output
    if cells.crs is None:
        raise ValueError("population_data must have a CRS.")
    if population_col not in cells.columns:
        raise ValueError(f"population_data missing required column '{population_col}'.")

    # Keep only the overlapping parts to avoid unnecessary overlay work.
    valid_hexes = hexes.to_crs(cells.crs)
    valid_hexes = valid_hexes[valid_hexes.geometry.notna()].copy()
    if len(valid_hexes) == 0:
        raise ValueError("hexes has no valid geometries.")

    valid_hexes = valid_hexes[valid_hexes.geometry.intersects(box(*cells.total_bounds))].copy()
    if len(valid_hexes) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    cells = cells[cells.geometry.notna()].copy()
    cells = cells[cells.geometry.intersects(box(*valid_hexes.total_bounds))].copy()
    if len(cells) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    # Reproject to a metric CRS so area ratios are meaningful.
    hexes_metric = valid_hexes[[id_col, "geometry"]].to_crs(metric_crs)
    cells_metric = cells[[population_col, "geometry"]].to_crs(metric_crs)
    cells_metric["cell_area"] = cells_metric.geometry.area
    cells_metric = cells_metric[cells_metric["cell_area"] > 0].copy()

    if len(cells_metric) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    overlaps = gpd.overlay(
        hexes_metric,
        cells_metric,
        how="intersection",
        keep_geom_type=False,
    )
    if len(overlaps) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    # Split each cell's population across hexes by overlap area.
    overlaps["overlap_area"] = overlaps.geometry.area
    overlaps = overlaps[overlaps["overlap_area"] > 0].copy()
    if len(overlaps) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    overlaps["allocated_population"] = (
        overlaps[population_col] * overlaps["overlap_area"] / overlaps["cell_area"]
    )

    # Sum the allocated population back to one value per hex.
    population_by_hex = overlaps.groupby(id_col)["allocated_population"].sum()
    output[population_col] = output[id_col].map(population_by_hex).fillna(0.0).astype(float)

    if save_path is not None:
        save_gdf(output, save_path)

    return output
