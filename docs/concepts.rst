Concepts
========

Origins
-------

Most origin-side models use a GeoDataFrame of H3 hexagons or points. By
default, the origin identifier column is ``hex_id``.

When origin geometry is polygonal, ``accessX`` routes from the geometry
centroid. For points, it routes from the point itself.

Destinations
------------

Destinations are POIs stored in a GeoDataFrame. The default category column is
``category``, and some functions also require a POI identifier column named
``id``.

Polygonal POIs are represented by their centroids during graph snapping.

Graph Costs
-----------

Accessibility functions do not assume a fixed travel mode. They route over any
numeric edge attribute named by ``cost_attr``.

.. code-block:: python

   graph = acx.add_time_cost_constant_speed(
       graph,
       speed_kmh=4.5,
       cost_col="walk_time",
   )

Custom costs are also supported:

.. code-block:: python

   def comfort_cost(edge):
       return edge["length"] * edge.get("comfort_penalty", 1.0)

   graph = acx.add_edge_cost(
       graph,
       cost_fn=comfort_cost,
       cost_col="comfort_cost",
   )

The units of ``max_cost`` always match the units of ``cost_attr``.

CRS and Snapping
----------------

The graph must be projected and must have ``graph.graph["crs"]`` set. Origins
and POIs are snapped to their nearest graph node. The
``max_distance_from_graph`` parameter controls how far a feature may be from
the network before it is ignored for routing.

Progress Bars
-------------

Many long-running functions accept ``show_progress=True``. Progress bars use
``tqdm`` when it is installed.
