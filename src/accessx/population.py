from __future__ import annotations

import json
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Iterable, Optional, Union
from urllib.parse import urlencode
from urllib.request import urlopen, urlretrieve

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask
from shapely.geometry import box, mapping

from accessx.io import save_gdf


WORLDPOP_REST_ROOT = "https://www.worldpop.org/rest/data"
NATURAL_EARTH_URL = (
    "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip"
)


def _make_download_progress(show_progress: bool):
    if not show_progress:
        return None, None
    try:
        from tqdm.auto import tqdm
    except ImportError:
        warnings.warn(
            "show_progress=True requires tqdm. Install tqdm or set show_progress=False.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None, None

    progress_bar = tqdm(
        desc="Downloading WorldPop raster",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    )
    downloaded_bytes = 0

    def reporthook(block_count: int, block_size: int, total_size: int) -> None:
        nonlocal downloaded_bytes
        if total_size > 0 and progress_bar.total != total_size:
            progress_bar.total = total_size
            progress_bar.refresh()
        current_bytes = block_count * block_size
        progress_bar.update(max(0, current_bytes - downloaded_bytes))
        downloaded_bytes = current_bytes

    return reporthook, progress_bar


def _pixel_polygon(transform, row: int, col: int):
    upper_left_x, upper_left_y = rasterio.transform.xy(transform, row, col, offset="ul")
    lower_right_x, lower_right_y = rasterio.transform.xy(transform, row, col, offset="lr")
    return box(
        min(upper_left_x, lower_right_x),
        min(upper_left_y, lower_right_y),
        max(upper_left_x, lower_right_x),
        max(upper_left_y, lower_right_y),
    )


def _validate_raster_file(raster_path: Union[str, Path]) -> None:
    """
    Open the raster and force a full-band read to catch truncated TIFFs.
    """
    with rasterio.open(raster_path) as src:
        _ = src.read(1, masked=True)


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
    save_path: Optional[Union[str, Path]] = None,
    show_progress: bool = True,
) -> Path:
    """
    Download a WorldPop raster for the country covering the supplied geometry.

    If `clip=True`, the downloaded country raster is clipped to the `aoi` or
    `hexes` extent and the clipped raster path is returned.

    Parameters
    ----------
    show_progress : bool, default True
        If True, show a progress bar while downloading the WorldPop raster.
    """
    geometry_gdf = _get_input_geometry(aoi=aoi, hexes=hexes)
    iso3 = infer_country_from_geometry(aoi=aoi, hexes=hexes)["iso3"]
    print(f"[accessx.population] Resolved country: {iso3}")

    # Resolve the matching WorldPop country raster for the requested year.
    metadata_url = f"{WORLDPOP_REST_ROOT}/pop/wpgp?{urlencode({'iso3': iso3})}"
    print(f"[accessx.population] Requesting metadata for year {year}...")
    with urlopen(metadata_url) as response:
        payload = json.load(response)
    matches = [entry for entry in payload.get("data", []) if str(entry.get("popyear")) == str(year)]
    if not matches:
        raise ValueError(f"No WorldPop population raster found for iso3={iso3} and year={year}.")
    files = matches[0].get("files") or []
    if not files:
        raise ValueError(f"WorldPop metadata for {iso3} {year} did not include a download URL.")
    source_url = str(files[0])

    # Download the source raster fresh for each call to avoid stale cache issues.
    with tempfile.TemporaryDirectory(prefix="accessx_worldpop_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        country_raster_path = tmp_dir_path / f"worldpop_{iso3.lower()}_{year}.tif"
        print(f"[accessx.population] Downloading raster to {country_raster_path}...")
        reporthook, progress_bar = _make_download_progress(show_progress)
        try:
            urlretrieve(source_url, country_raster_path, reporthook=reporthook)
        finally:
            if progress_bar is not None:
                progress_bar.close()
        _validate_raster_file(country_raster_path)
        print("[accessx.population] Download complete and validated.")

        if not clip:
            if save_path is None:
                temp_file = tempfile.NamedTemporaryFile(
                    prefix=f"worldpop_{iso3.lower()}_{year}_",
                    suffix=".tif",
                    delete=False,
                )
                temp_file.close()
                final_raster_path = Path(temp_file.name)
            else:
                final_raster_path = Path(save_path)
                final_raster_path.parent.mkdir(parents=True, exist_ok=True)

            shutil.copyfile(country_raster_path, final_raster_path)
            print(f"[accessx.population] Saved raster to {final_raster_path}")
            return final_raster_path

        print("[accessx.population] Clipping raster to input geometry...")
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

        if save_path is None:
            temp_file = tempfile.NamedTemporaryFile(
                prefix=f"worldpop_{iso3.lower()}_{year}_clipped_",
                suffix=".tif",
                delete=False,
            )
            temp_file.close()
            final_raster_path = Path(temp_file.name)
        else:
            final_raster_path = Path(save_path)
            final_raster_path.parent.mkdir(parents=True, exist_ok=True)

        with rasterio.open(final_raster_path, "w", **clipped_meta) as dst:
            dst.write(clipped_data)

        print(f"[accessx.population] Saved clipped raster to {final_raster_path}")
        return final_raster_path


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


def _normalize_population_cols(
    *,
    population_col: Optional[str] = None,
    population_cols: Optional[Union[str, Iterable[str]]] = None,
) -> list[str]:
    if population_cols is None:
        if population_col is None:
            raise ValueError("Provide `population_col` or `population_cols`.")
        return [str(population_col)]

    if isinstance(population_cols, str):
        normalized = [population_cols]
    else:
        normalized = [str(col_name) for col_name in population_cols]

    if not normalized:
        raise ValueError("population_cols is empty.")
    if len(set(normalized)) != len(normalized):
        raise ValueError("population_cols contains duplicates.")
    return normalized


def map_population_grid_to_hexes(
    hexes: gpd.GeoDataFrame,
    population_grid: gpd.GeoDataFrame,
    *,
    metric_crs: Union[int, str],
    id_col: str = "hex_id",
    population_col: str = "population",
    population_cols: Optional[Union[str, Iterable[str]]] = None,
    save_path: Optional[Union[str, Path]] = None,
) -> gpd.GeoDataFrame:
    """
    Allocate population-grid totals to hexes using area-weighted overlap.

    This is the generic population-mapping function for vector population grids,
    such as raster-derived cells or datasets like CBS 100x100m grids.
    """
    if hexes is None or len(hexes) == 0:
        raise ValueError("hexes is empty.")
    if population_grid is None or len(population_grid) == 0:
        raise ValueError("population_grid is empty.")
    if hexes.crs is None:
        raise ValueError("hexes must have a CRS.")
    if population_grid.crs is None:
        raise ValueError("population_grid must have a CRS.")
    if id_col not in hexes.columns:
        raise ValueError(f"hexes missing required id column '{id_col}'.")
    if metric_crs is None:
        raise ValueError("metric_crs must be provided.")

    population_col_names = _normalize_population_cols(
        population_col=population_col,
        population_cols=population_cols,
    )
    for population_col_name in population_col_names:
        if population_col_name not in population_grid.columns:
            raise ValueError(
                f"population_grid missing required population column '{population_col_name}'."
            )

    output = hexes.copy()
    for population_col_name in population_col_names:
        output[population_col_name] = 0.0

    # Keep only overlapping geometries before running the overlay.
    valid_hexes = hexes.to_crs(population_grid.crs)
    valid_hexes = valid_hexes[valid_hexes.geometry.notna()].copy()
    if len(valid_hexes) == 0:
        raise ValueError("hexes has no valid geometries.")

    valid_hexes = valid_hexes[valid_hexes.geometry.intersects(box(*population_grid.total_bounds))].copy()
    if len(valid_hexes) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    cells = population_grid.copy()
    cells = cells[cells.geometry.notna()].copy()
    cells = cells[cells.geometry.intersects(box(*valid_hexes.total_bounds))].copy()
    if len(cells) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    # Reproject to a metric CRS so area ratios are meaningful.
    hexes_metric = valid_hexes[[id_col, "geometry"]].to_crs(metric_crs)
    cells_metric = cells[population_col_names + ["geometry"]].to_crs(metric_crs)
    for population_col_name in population_col_names:
        cells_metric[population_col_name] = pd.to_numeric(
            cells_metric[population_col_name], errors="coerce"
        ).fillna(0.0)
        if (cells_metric[population_col_name] < 0).any():
            raise ValueError(f"Population column '{population_col_name}' must be >= 0.")

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

    overlaps["overlap_area"] = overlaps.geometry.area
    overlaps = overlaps[overlaps["overlap_area"] > 0].copy()
    if len(overlaps) == 0:
        if save_path is not None:
            save_gdf(output, save_path)
        return output

    for population_col_name in population_col_names:
        overlaps[f"allocated_{population_col_name}"] = (
            overlaps[population_col_name] * overlaps["overlap_area"] / overlaps["cell_area"]
        )
        population_by_hex = overlaps.groupby(id_col)[f"allocated_{population_col_name}"].sum()
        output[population_col_name] = output[id_col].map(population_by_hex).fillna(0.0).astype(float)

    if save_path is not None:
        save_gdf(output, save_path)

    return output


def map_population_to_hexes(
    hexes: gpd.GeoDataFrame,
    population_data: Union[str, Path, gpd.GeoDataFrame],
    *,
    metric_crs: Union[int, str],
    id_col: str = "hex_id",
    band: int = 1,
    population_col: str = "population",
    population_cols: Optional[Union[str, Iterable[str]]] = None,
    save_path: Optional[Union[str, Path]] = None,
) -> gpd.GeoDataFrame:
    """
    Allocate raster population totals to hexes using area-weighted overlap.

    `population_data` can be either a raster path or a GeoDataFrame of
    population-cell polygons such as the output of `raster_to_population_grid`.
    """
    population_col_names = _normalize_population_cols(
        population_col=population_col,
        population_cols=population_cols,
    )

    # Accept either a raster path or a pre-built population grid.
    if isinstance(population_data, gpd.GeoDataFrame):
        cells = population_data.copy()
    else:
        if len(population_col_names) != 1:
            raise ValueError(
                "Raster input supports a single population column. "
                "Use `population_cols` only with a population-grid GeoDataFrame."
            )
        cells = raster_to_population_grid(
            population_data,
            band=band,
            population_col=population_col_names[0],
        )

    return map_population_grid_to_hexes(
        hexes,
        cells,
        metric_crs=metric_crs,
        id_col=id_col,
        population_col=population_col_names[0],
        population_cols=population_col_names,
        save_path=save_path,
    )
