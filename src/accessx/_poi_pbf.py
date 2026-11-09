"""Small Geofabrik download helper; QuackOSM handles disk-based extraction."""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
from shapely.geometry import shape


def _download(url, path):
    """Publish only complete downloads, leaving any previous file intact on failure."""
    partial = path.with_suffix(path.suffix + '.part')
    try:
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            expected = int(response.headers.get('Content-Length', 0))
            size = 0
            with partial.open('wb') as output:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    output.write(chunk)
            if size == 0 or (expected and size != expected):
                raise OSError('Incomplete download.')
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def retrieve_pbf(polygon, tags, *, pbf_path, cache_dir,
                 refresh, progress, report):
    from quackosm import PbfFileReader

    cache = Path(cache_dir or Path.home() / '.cache' / 'accessx' / 'pbf').expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    if pbf_path is not None:
        path = Path(pbf_path).expanduser()
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'PBF file is missing or empty: {path}')
        cached = True
    else:
        index_path = cache / 'geofabrik-index.json'
        if refresh or not index_path.exists():
            progress('Fetching Geofabrik extract catalogue...')
            _download('https://download.geofabrik.de/index-v1.json', index_path)
        features = json.loads(index_path.read_text())['features']
        # Require complete AOI coverage; never silently use a partial regional extract.
        candidates = sorted(
            (f for f in features if shape(f['geometry']).covers(polygon)),
            key=lambda f: shape(f['geometry']).area,
        )
        if not candidates:
            raise ValueError('No Geofabrik extract covers the complete study area.')
        def location(feature):
            url = feature['properties']['urls']['pbf']
            # Preserve provider subdirectories to avoid duplicate regional names.
            return url, cache / urlparse(url).path.lstrip('/')
        local = [(f, location(f)[1]) for f in candidates]
        existing = [item for item in local if item[1].is_file() and item[1].stat().st_size]
        feature, path = existing[0] if existing else local[0]
        cached = bool(existing) and not refresh
        if not cached:
            progress('No reusable local PBF found.' if not existing else 'Refreshing local PBF.')
            url, path = location(feature)
            path.parent.mkdir(parents=True, exist_ok=True)
            progress(f'Downloading {path.name}...')
            started = time.perf_counter()
            _download(url, path)
            report['download_seconds'] = time.perf_counter() - started
            progress('Download complete.')
    report.update(from_cache=cached, pbf_path=str(path))
    progress(f'Source: {"local" if cached else "downloaded"} PBF ({path.name}).')
    progress('Extracting and filtering POIs within study area...')
    # Separate snapshots so refreshing a file also invalidates other AOI/tag caches.
    stat = path.stat()
    snapshot = hashlib.sha256(
        f'{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
    # AOI and tags are pushed into DuckDB; only matching features enter Python memory.
    reader = PbfFileReader(tags_filter=tags, geometry_filter=polygon,
                           working_directory=cache / 'processed' / snapshot, verbosity_mode='silent')
    gdf = reader.convert_pbf_to_geodataframe(path, keep_all_tags=True, explode_tags=True,
                                            ignore_cache=refresh)
    if gdf.empty:
        gdf.index = pd.MultiIndex.from_arrays([[], []], names=['element', 'id'])
        return gdf.to_crs(4326)
    identities = gdf.index.to_series().str.split('/', n=1, expand=True)
    gdf.index = pd.MultiIndex.from_arrays(
        [identities[0], identities[1].astype('int64')], names=['element', 'id'])
    return gdf.to_crs(4326)
