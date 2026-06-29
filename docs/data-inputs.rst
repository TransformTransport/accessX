Data Inputs
===========

Areas of Interest
-----------------

Use ``load_aoi`` to load an AOI from a vector file or a bounding box.

.. code-block:: python

   aoi = acx.load_aoi(
       filepath="data/city_boundary.geojson",
       target_crs=4326,
   )

.. code-block:: python

   aoi = acx.load_aoi(
       bbox=(23.68, 37.94, 23.80, 38.04),
       input_crs=4326,
       target_crs=4326,
   )

To buffer in meters, pass a projected CRS:

.. code-block:: python

   aoi_buffered = acx.load_aoi(
       filepath="data/city_boundary.geojson",
       buffer_m=1000,
       utm_crs=2100,
       target_crs=4326,
   )

H3 Hex Grids
------------

.. code-block:: python

   hexes = acx.make_hex_grid(aoi, resolution=9)

The result includes a ``hex_id`` column.

Street Networks
---------------

.. code-block:: python

   graph = acx.build_network(
       aoi,
       city_epsg=2100,
       buffer_m=1000,
       network_type="walk",
   )

Save and load graph files:

.. code-block:: python

   acx.save_graph(graph, out_dir="outputs/athens", base_name="walk")
   graph = acx.load_graph(out_dir="outputs/athens", base_name="walk")

OpenStreetMap POIs
------------------

Direct OSM tags create categories named after the OSM key:

.. code-block:: python

   pois = acx.get_pois_osm(
       aoi,
       osm_tags={
           "amenity": ["pharmacy", "clinic"],
           "leisure": ["park", "playground"],
       },
   )

Custom POI groups create analytical categories:

.. code-block:: python

   pois, report = acx.get_pois_osm(
       aoi,
       poi_groups={
           "healthcare": {
               "amenity": ["pharmacy", "clinic", "doctors"],
               "healthcare": True,
           },
           "open_space": {
               "leisure": ["park", "playground", "garden"],
           },
       },
       return_report=True,
   )

Population
----------

.. code-block:: python

   raster_path = acx.get_worldpop_raster(
       aoi=aoi,
       year=2020,
       save_path="outputs/athens/worldpop_2020.tif",
   )

   population_grid = acx.raster_to_population_grid(
       raster_path,
       population_col="population",
   )

   hexes_pop = acx.map_population_grid_to_hexes(
       hexes,
       population_grid,
       metric_crs=2100,
       population_col="population",
   )

For vector population grids with multiple demographic columns:

.. code-block:: python

   hexes_pop = acx.map_population_grid_to_hexes(
       hexes,
       population_grid,
       metric_crs=2100,
       population_cols=["children", "adults", "older_adults"],
   )
