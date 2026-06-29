Accessibility Models
====================

Cumulative Opportunity
----------------------

``count_accessible_pois`` counts POIs by category within a maximum network
cost.

.. code-block:: python

   counts = acx.count_accessible_pois(
       graph,
       hexes.to_crs(2100),
       pois.to_crs(2100),
       max_cost=15,
       cost_attr="walk_time",
   )

Output columns are named ``count_<category>``.

Nearest POI Cost
----------------

``compute_nearest_poi_cost`` finds the nearest reachable POI per category.

.. code-block:: python

   nearest = acx.compute_nearest_poi_cost(
       graph,
       hexes.to_crs(2100),
       pois.to_crs(2100),
       max_cost=30,
       cost_attr="walk_time",
       poi_id_col="id",
       number_of_nearest=3,
       output="wide",
   )

Output modes are ``list``, ``long``, and ``wide``.

Hansen Accessibility
--------------------

``compute_hansen_accessibility`` applies exponential distance decay:

.. code-block:: python

   hansen = acx.compute_hansen_accessibility(
       graph,
       hexes.to_crs(2100),
       pois.to_crs(2100),
       max_cost=30,
       cost_attr="walk_time",
       beta=0.15,
   )

::

   A_i = sum_j O_j * exp(-beta * c_ij)

Use ``poi_weight_col``, ``category_weights``, and ``default_poi_weight`` when
destinations have capacities, areas, or category importance weights.

2SFCA Accessibility
-------------------

``compute_2sfca_accessibility`` estimates supply relative to accessible demand.

.. code-block:: python

   sfca = acx.compute_2sfca_accessibility(
       graph,
       hexes_pop.to_crs(2100),
       pois.to_crs(2100),
       max_cost=15,
       cost_attr="walk_time",
       demand_col="population",
       default_supply=1.0,
       decay="binary",
   )

Set ``decay="exp"`` and tune ``beta`` for an E2SFCA-like distance-decayed
variant.

Co-Accessibility
----------------

``compute_co_accessibility`` summarizes how much population can access each
POI.

.. code-block:: python

   coacc = acx.compute_co_accessibility(
       graph,
       hexes_pop.to_crs(2100),
       pois.to_crs(2100),
       max_cost=15,
       cost_attr="walk_time",
       population_groups=["children", "adults", "older_adults"],
       poi_id_col="id",
       approach="cumulative",
   )

Output columns are named ``coacc_<population_group>``.

Isochrones
----------

.. code-block:: python

   isochrones = acx.calculate_isochrones(
       graph,
       hexes.to_crs(2100),
       max_cost=15,
       interval_size=5,
       cost_attr="walk_time",
       city_epsg=2100,
       method="edges",
       edge_buff=25,
   )

Use ``save_dir`` to write a GeoPackage with QGIS-friendly layers.

Equity Summaries
----------------

.. code-block:: python

   A, P, gini, sorted_vals = acx.calculate_lorenz(
       ["count_healthcare", "count_open_space"],
       scores,
       weights="population",
   )

   sufficient = acx.compute_sufficientarian_score(
       scores,
       thresholds_ge={
           "count_healthcare": 1,
           "count_open_space": 1,
       },
       thresholds_le={
           "nearest_cost_healthcare_1": 15,
       },
   )
