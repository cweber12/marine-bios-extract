"""marine-bios-extract - bounding-box extraction of CDFW BIOS marine GIS layers.

The toolkit downloads the authoritative published archive for a dataset, caches
it, and clips locally. It deliberately does not use ArcGIS REST bounding-box
queries as the primary path: those services cap the number of records returned
and signal the cut with ``exceededTransferLimit``, so an imperfectly paged query
yields a plausible subset rather than an error. A wrong answer that looks right
is the failure mode this repo family exists to avoid.

Nothing about a study area is defined in code. Callers supply the box.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .bbox import BBox, BBoxError

__all__ = ["BBox", "BBoxError", "__version__"]
