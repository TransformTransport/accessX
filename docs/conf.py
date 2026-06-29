from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

project = "accessX"
author = "Vasileios Milias"
copyright = "2026, Vasileios Milias"
release = "0.1.1"
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

source_suffix = ".rst"

master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

autosummary_generate = True
autodoc_typehints = "description"
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = True

autodoc_mock_imports = [
    "geopandas",
    "h3",
    "matplotlib",
    "networkx",
    "numpy",
    "osmnx",
    "pandas",
    "pyproj",
    "rasterio",
    "requests",
    "seaborn",
    "shapely",
    "tobler",
    "tqdm",
]

try:
    import sphinx_rtd_theme  # noqa: F401
except ModuleNotFoundError:
    html_theme = "alabaster"
else:
    html_theme = "sphinx_rtd_theme"
html_title = "accessX"
html_short_title = "accessX"
html_static_path = []
