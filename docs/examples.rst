Examples and Notebooks
======================

The repository includes case-study notebooks that demonstrate complete
workflows.

.. list-table::
   :header-rows: 1

   * - Notebook
     - Topic
   * - ``notebooks/case_study_isochrones.ipynb``
     - AOIs, H3 hex grids, street networks, travel costs, and isochrones.
   * - ``notebooks/case_study_population.ipynb``
     - WorldPop download, raster conversion, and population-to-hex aggregation.
   * - ``notebooks/case_study_pois.ipynb``
     - OSM tags, custom POI groups, retries, and query reports.
   * - ``notebooks/case_study_accessibility.ipynb``
     - Cumulative opportunity, nearest POIs, Hansen accessibility, and 2SFCA.
   * - ``notebooks/case_study_co_accessibility.ipynb``
     - Destination-side population access and co-accessibility.
   * - ``notebooks/case_study_equity.ipynb``
     - Territorial and population-weighted accessibility equity.

Typical Analysis Recipe
-----------------------

.. code-block:: python

   import accessx as acx
   import osmnx as ox

   aoi = ox.geocode_to_gdf("Amsterdam, Netherlands")
   metric_crs = 28992

   hexes = acx.make_hex_grid(aoi, resolution=9)
   graph = acx.build_network(aoi, city_epsg=metric_crs, buffer_m=1000)
   graph = acx.add_time_cost_constant_speed(graph, speed_kmh=4.5, cost_col="walk_time")

   pois = acx.get_pois_osm(
       aoi,
       poi_groups={
           "daily_needs": {
               "shop": ["supermarket", "bakery", "convenience"],
               "amenity": ["pharmacy", "school"],
           },
           "open_space": {
               "leisure": ["park", "playground", "garden"],
           },
       },
   )

   counts = acx.count_accessible_pois(
       graph,
       hexes.to_crs(metric_crs),
       pois.to_crs(metric_crs),
       max_cost=15,
       cost_attr="walk_time",
   )

Population-Adjusted Accessibility
----------------------------------

.. code-block:: python

   raster_path = acx.get_worldpop_raster(
       aoi=aoi,
       year=2020,
       save_path="outputs/worldpop_2020.tif",
   )

   hexes_pop = acx.map_population_to_hexes(
       hexes,
       raster_path,
       metric_crs=metric_crs,
       population_col="population",
   )

   sfca = acx.compute_2sfca_accessibility(
       graph,
       hexes_pop.to_crs(metric_crs),
       pois.to_crs(metric_crs),
       max_cost=15,
       cost_attr="walk_time",
       demand_col="population",
       decay="exp",
       beta=0.15,
   )
