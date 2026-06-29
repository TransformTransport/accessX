Troubleshooting
===============

``Graph has no CRS``
--------------------

Accessibility functions require ``graph.graph["crs"]``. Build graphs with
``build_network`` or load saved graphs with ``load_graph``.

.. code-block:: python

   graph = acx.build_network(aoi, city_epsg=2100)

``Edge cost attribute ... not found``
-------------------------------------

The selected ``cost_attr`` must exist on at least one graph edge.

.. code-block:: python

   graph = acx.add_time_cost_constant_speed(
       graph,
       cost_col="walk_time",
   )

CRS Mismatch
------------

Isochrone generation requires hex CRS to match the graph CRS.

.. code-block:: python

   hexes_metric = hexes.to_crs(graph.graph["crs"])

For population allocation, pass a projected ``metric_crs`` so areas are
meaningful.

Empty POI Results
-----------------

Use ``return_report=True`` to inspect requested, empty, and failed OSM queries.

.. code-block:: python

   pois, report = acx.get_pois_osm(
       aoi,
       poi_groups={"healthcare": {"amenity": ["clinic", "pharmacy"]}},
       return_report=True,
   )

Features Too Far From the Graph
-------------------------------

Origins and POIs are ignored when their centroid or point is farther than
``max_distance_from_graph`` from the nearest graph node.

.. code-block:: python

   counts = acx.count_accessible_pois(
       graph,
       hexes_metric,
       pois_metric,
       max_cost=15,
       cost_attr="walk_time",
       max_distance_from_graph=500,
   )

Missing Identifier Columns
--------------------------

Use ``id_col`` and ``poi_id_col`` when your data uses non-default names.

.. code-block:: python

   nearest = acx.compute_nearest_poi_cost(
       graph,
       origins,
       pois,
       max_cost=15,
       cost_attr="walk_time",
       id_col="origin_id",
       poi_id_col="poi_id",
   )

Negative Population, Demand, or Supply
--------------------------------------

Population, demand, and supply values must be non-negative.

.. code-block:: python

   import pandas as pd

   hexes["population"] = (
       pd.to_numeric(hexes["population"], errors="coerce")
       .fillna(0)
       .clip(lower=0)
   )
