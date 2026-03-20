# accessX

`accessX` is a Python library for **proximity-based accessibility analysis**, where **X** is a any type of cost threshold  used to study proximity to opportunities across a city.

It is designed for workflows such as **15-minute city planning** and broader proximity-based policy analysis, helping quantify what people can reach within a target travel cost. Under the hood, accessX builds on proven geospatial/network tools, especially **OSMnx**  and **NetworkX**, while providing a high-level API focused on accessibility assessments.

At its core, accessX is designed to help explore accessibility-related questions such as:

- What can people reach within X-minutes?
- How far is the nearest service of each type?
- How does accessibility vary across neighborhoods and population demand?

The library is OSM-first, data-agnostic, and built for reproducible urban accessibility analysis.

## Typical Workflow

`AOI -> hex grid -> street network -> edge costs -> (optional) isochrones -> POIs -> accessibility scores`

## Library Modules

### `accessx.io`
- Lightweight read/write helpers for GeoDataFrames.

### `accessx.aoi`
- Load an AOI from file or bbox (`load_aoi`).
- Build H3 hex grids over the AOI (`make_hex_grid`).

### `accessx.graph`
- Download and preprocess OSM street networks (`build_network`).
- Save/load graph nodes and edges (`save_graph`, `load_graph`).

### `accessx.cost`
- Add custom edge-cost layers (`add_edge_cost`).
- Built-in helpers for travel-time costs (constant speed, slope-based).

### `accessx.isochrone`
- Generate walksheds/isochrones per hex (`calculate_isochrones`).
- Supports multiple thresholds and polygon methods (`edges`, `hull`).
- Useful for map visualization and communication.

### `accessx.poi`
- Collect POIs from OSM by category (`get_pois_osm`).
- Keeps OSM identity fields and supports clean/minimal output schemas.

### `accessx.accessibility`
- Count reachable opportunities by category (`count_accessible_pois`).
- Estimate nearest POI cost by category (`compute_nearest_poi_cost`).
- Compute Hansen accessibility scores (`compute_hansen_accessibility`).
- Compute catchment-based 2SFCA accessibility (`compute_2sfca_accessibility`).


### `accessx.equity`
- Estimate Lorenz curves and Gini indices for accessibility metrics (`calculate_lorenz`).
- Plot Lorenz curves and Gini tables for selected metrics (`plot_lorenz_curves`, `plot_gini_table`).
- Compute sufficientarian scores from explicit accessibility thresholds (`compute_sufficientarian_score`).
- Visualize sufficientarian score distributions and attainment levels (`plot_sufficientarian_score`).



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
