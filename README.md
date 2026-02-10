# accessX
accessX is a Python library for X-minute accessibility analysis built on open spatial data.

Inspired by the usability of tools like OSMnx and NetworkX, accessX aims for a simple workflow with sensible defaults, while staying extensible for advanced users. In a few steps, you can go from an area of interest to a street network, assign travel costs, and compute isochrones and accessibility metrics such as reachable opportunities, nearest services, Hansen scores, and 2SFCA indicators.

At its core, accessX is designed to answer practical questions clearly:

* What can people reach within X minutes?
* How far is the nearest service of each type?
* How does accessibility vary across neighborhoods and population demand?
  
The library is OSM-first, data-agnostic, and built for reproducible urban accessibility analysis with clean Python APIs.
