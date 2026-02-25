# accessX

`accessX` is a Python library for **X-minute accessibility analysis**, where **X** is a flexible time threshold (for example 5, 10, 15, or 20 minutes) used to study proximity to opportunities across a city.

It is designed for workflows such as **15-minute city planning** and broader proximity-based policy analysis, helping quantify what people can reach within a target travel time. Under the hood, accessX builds on proven geospatial/network tools, especially **OSMnx** (for OpenStreetMap network and feature workflows) and **NetworkX** (for graph routing and cost-based accessibility computation), while providing a cleaner high-level API focused on accessibility modeling.

At its core, accessX is designed to answer practical questions clearly:

- What can people reach within X minutes?
- How far is the nearest service of each type?
- How does accessibility vary across neighborhoods and population demand?

The library is OSM-first, data-agnostic, and built for reproducible urban accessibility analysis with clean Python APIs.

## Typical Workflow

`AOI -> hex grid -> street network -> edge costs -> (optional) isochrones -> POIs -> accessibility scores`

## Library Modules

- `accessx.aoi`
- Load an AOI from file or bbox (`load_aoi`).
- Build H3 hex grids over the AOI (`make_hex_grid`).

- `accessx.graph`
- Download and preprocess OSM street networks (`build_network`).
- Save/load graph nodes and edges (`save_graph`, `load_graph`).

- `accessx.cost`
- Add custom edge-cost layers (`add_edge_cost`).
- Built-in helpers for travel-time costs (constant speed, slope-based).

- `accessx.isochrone`
- Generate walksheds/isochrones per hex (`calculate_isochrones`).
- Supports multiple thresholds and polygon methods (`edges`, `hull`).
- Useful for map visualization and communication.

- `accessx.poi`
- Collect POIs from OSM by category (`get_pois_osm`).
- Keeps OSM identity fields and supports clean/minimal output schemas.

- `accessx.accessibility`
- `count_accessible_pois`: reachable opportunity counts by category.
- `compute_nearest_poi_cost`: nearest POI cost by category.
- `compute_hansen_accessibility`: gravity-based accessibility scores.
- `compute_2sfca_accessibility`: catchment-based 2SFCA accessibility.

- `accessx.io`
- Lightweight read/write helpers for GeoDataFrames.

## Accessibility Models Included

- **Reachable counts**: number of POIs reachable within a cost threshold.
- **Nearest POI cost**: travel cost to nearest POI(s), by category.
- **Hansen accessibility**: decayed opportunity sums over network costs.
- **2SFCA**: supply-demand catchment accessibility (binary or exponential decay).

## Design Principles

- OSM-first, but data-agnostic.
- Network-based accessibility by default.
- Sensible defaults with extensible hooks.
- Reproducible analysis with clear modules and functions.
