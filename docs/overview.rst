Overview
========

``accessX`` is a Python library for network-based accessibility analysis in
X-minute city, proximity planning, and urban opportunity workflows.

It helps answer questions such as:

* How many services are reachable within a walking, cycling, distance, or
  custom cost threshold?
* What is the network cost to the nearest service?
* Where is supply high or low relative to population demand?
* Which destinations can be reached by multiple population groups?
* How evenly is accessibility distributed across places or people?

Core Workflow
-------------

::

   Area of interest
       -> H3 origin grid
       -> OSM street network
       -> edge travel costs
       -> POIs and population demand
       -> accessibility metrics
       -> isochrones and equity summaries

Most functions return ordinary GeoPandas ``GeoDataFrame`` or pandas
``DataFrame`` objects, so intermediate outputs can be inspected, saved, mapped,
edited, or passed into custom analysis.

Package Layout
--------------

.. list-table::
   :header-rows: 1

   * - Module
     - Purpose
   * - ``accessx.aoi``
     - Load analysis areas and create H3 origin grids.
   * - ``accessx.graph``
     - Build, save, and load OSMnx street networks.
   * - ``accessx.cost``
     - Add routing cost attributes to graph edges.
   * - ``accessx.poi``
     - Query and organize OpenStreetMap POIs.
   * - ``accessx.population``
     - Download, vectorize, and aggregate population data.
   * - ``accessx.accessibility``
     - Compute origin-side and destination-side accessibility metrics.
   * - ``accessx.isochrone``
     - Generate walkshed or isochrone geometries.
   * - ``accessx.equity``
     - Compute Lorenz/Gini and sufficientarian equity summaries.
   * - ``accessx.io``
     - Read and save GeoDataFrames.

Minimal Example
---------------

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
   )
   graph = acx.add_time_cost_constant_speed(
       graph,
       speed_kmh=4.5,
       cost_col="walk_time",
   )

   pois = acx.get_pois_osm(
       aoi,
       poi_groups={
           "healthcare": {"amenity": ["pharmacy", "clinic", "doctors"]},
           "open_space": {"leisure": ["park", "playground", "garden"]},
       },
   )

   scores = acx.count_accessible_pois(
       graph,
       hexes.to_crs(2100),
       pois.to_crs(2100),
       max_cost=15,
       cost_attr="walk_time",
   )
