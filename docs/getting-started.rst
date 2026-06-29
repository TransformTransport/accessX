Getting Started
===============

Installation
------------

.. code-block:: bash

   pip install accessx

For local development from a cloned repository:

.. code-block:: bash

   pip install -e .

Python 3.10 or newer is required.

First Workflow
--------------

.. code-block:: python

   import accessx as acx
   import osmnx as ox

   aoi = ox.geocode_to_gdf("Municipality of Athens, Greece")
   hexes = acx.make_hex_grid(aoi, resolution=9)

   graph = acx.build_network(
       aoi,
       city_epsg=2100,
       buffer_m=1000,
       network_type="walk",
       show_progress=True,
   )

   graph = acx.add_time_cost_constant_speed(
       graph,
       speed_kmh=4.5,
       cost_col="walk_time",
       show_progress=True,
   )

   pois = acx.get_pois_osm(
       aoi,
       poi_groups={
           "healthcare": {"amenity": ["pharmacy", "clinic", "doctors"]},
           "open_space": {"leisure": ["park", "playground", "garden"]},
       },
       show_progress=True,
   )

   scores = acx.count_accessible_pois(
       graph,
       hexes.to_crs(2100),
       pois.to_crs(2100),
       max_cost=15,
       cost_attr="walk_time",
       show_progress=True,
   )

The result is a ``GeoDataFrame`` with one row per origin and one
``count_<category>`` column for each POI category.

Save Intermediate Outputs
-------------------------

.. code-block:: python

   acx.save_graph(graph, out_dir="outputs/athens", base_name="walk")
   acx.save_gdf(pois, "outputs/athens/pois.geojson")
   acx.save_gdf(scores, "outputs/athens/accessibility.geojson")

Load them later:

.. code-block:: python

   graph = acx.load_graph(out_dir="outputs/athens", base_name="walk")
   pois = acx.read_gdf("outputs/athens/pois.geojson", crs=2100)
   scores = acx.read_gdf("outputs/athens/accessibility.geojson")

Choosing a Local CRS
--------------------

Network routing, buffering, distance checks, isochrone edge buffers, and
area-weighted population allocation require a projected CRS in meters. Use a
city-appropriate EPSG code, such as ``2100`` for Greece or ``28992`` for the
Netherlands.
