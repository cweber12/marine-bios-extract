"""Bounding box parsing, validation and CRS selection.

Bounding box order is always WEST,SOUTH,EAST,NORTH in decimal degrees, matching
the sibling cudem-extract and kelp-density-extract toolkits. West and east are
both negative in California. A second convention inside one repo family is a bug
generator, so this module is a deliberate port rather than a reinvention.

No study area is defined here. Callers supply the box.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Mean length of a degree of latitude, WGS84. Good to ~0.3% anywhere, which is
# far finer than the precision of a hand-drawn study box.
_KM_PER_DEG_LAT = 110.574


class BBoxError(ValueError):
    """Raised when a bounding box is malformed or geographically impossible."""


@dataclass(frozen=True)
class BBox:
    """An axis-aligned lon/lat rectangle in WGS84 (EPSG:4326)."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        for name, value in (
            ("west", self.west),
            ("south", self.south),
            ("east", self.east),
            ("north", self.north),
        ):
            if not math.isfinite(value):
                raise BBoxError(f"{name} is not a finite number: {value!r}")

        if not -180.0 <= self.west <= 180.0 or not -180.0 <= self.east <= 180.0:
            raise BBoxError(
                f"longitudes must be in [-180, 180], got west={self.west}, east={self.east}"
            )
        if not -90.0 <= self.south <= 90.0 or not -90.0 <= self.north <= 90.0:
            raise BBoxError(
                f"latitudes must be in [-90, 90], got south={self.south}, north={self.north}"
            )
        if self.west >= self.east:
            raise BBoxError(
                f"west ({self.west}) must be less than east ({self.east}). "
                "Order is WEST,SOUTH,EAST,NORTH; both are negative in California. "
                "A box crossing the antimeridian is not supported."
            )
        if self.south >= self.north:
            raise BBoxError(
                f"south ({self.south}) must be less than north ({self.north}). "
                "Order is WEST,SOUTH,EAST,NORTH."
            )

    # ---- constructors -------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "BBox":
        """Parse "west,south,east,north". Whitespace around values is allowed."""
        if not isinstance(text, str):
            raise BBoxError(f"expected a string, got {type(text).__name__}")

        parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
        if len(parts) != 4:
            raise BBoxError(
                f"expected 4 comma-separated numbers (WEST,SOUTH,EAST,NORTH), "
                f"got {len(parts)}: {text!r}"
            )
        try:
            values = [float(p) for p in parts]
        except ValueError as exc:
            raise BBoxError(f"could not read {text!r} as four numbers: {exc}") from exc

        return cls(*values)

    @classmethod
    def from_center(cls, lat: float, lon: float, radius_km: float) -> "BBox":
        """Build a box of half-width `radius_km` around a point.

        The box is square in kilometres, not in degrees, so it is wider in
        longitude than in latitude once you are away from the equator.
        """
        if not math.isfinite(radius_km) or radius_km <= 0:
            raise BBoxError(f"radius_km must be positive, got {radius_km!r}")
        if not -90.0 <= lat <= 90.0:
            raise BBoxError(f"latitude must be in [-90, 90], got {lat}")

        dlat = radius_km / _KM_PER_DEG_LAT
        cos_lat = math.cos(math.radians(lat))
        if abs(cos_lat) < 1e-9:
            raise BBoxError("cannot build a box around a pole")
        dlon = radius_km / (_KM_PER_DEG_LAT * cos_lat)

        return cls(
            west=max(lon - dlon, -180.0),
            south=max(lat - dlat, -90.0),
            east=min(lon + dlon, 180.0),
            north=min(lat + dlat, 90.0),
        )

    @classmethod
    def from_envelope(
        cls,
        envelope: tuple[float, float, float, float],
        north_km: float,
        south_km: float,
        east_km: float,
        west_km: float,
    ) -> "BBox":
        """Pad a WEST,SOUTH,EAST,NORTH envelope by four independent distances.

        The envelope is normally the tightest rectangle around a set of points -
        a study's positioned stations, say - which on its own is routinely a
        sliver a few hundred metres across, and has no area at all where two
        points coincide. The padding is what makes it a usable study area.

        Each side gets its own distance in **kilometres**, so a box can be
        extended offshore without dragging it inland. Kilometres and not degrees
        because a degree of longitude at 32.87 N is about 16% shorter than a
        degree of latitude, and padding in degrees would produce a box whose
        east-west margin is quietly smaller than its north-south one. The
        conversion is done per axis at the envelope's centre latitude.

        Miles are deliberately not offered: the registry holds a *nautical* mile
        limit layer, everything internal is metric, and the sibling toolkits pad
        in metres. An ambiguous "mile" in a marine repo is a defect waiting to
        happen.

        No minimum-size floor is applied. The caller chooses the padding, and
        any padding worth typing already makes a usable box from a single point.
        """
        try:
            west, south, east, north = (float(v) for v in envelope)
        except (TypeError, ValueError) as exc:
            raise BBoxError(
                f"envelope must be four numbers (WEST,SOUTH,EAST,NORTH), got {envelope!r}"
            ) from exc

        pads = {
            "north": north_km,
            "south": south_km,
            "east": east_km,
            "west": west_km,
        }
        for side, value in pads.items():
            if not math.isfinite(value):
                raise BBoxError(f"{side} padding is not a finite number: {value!r}")
            if value < 0:
                raise BBoxError(
                    f"{side} padding must not be negative, got {value} km. "
                    "Padding grows the box; it never trims it."
                )

        if west > east or south > north:
            raise BBoxError(
                f"envelope {envelope!r} is inside out; order is WEST,SOUTH,EAST,NORTH"
            )

        centre_lat = (south + north) / 2.0
        cos_lat = math.cos(math.radians(centre_lat))
        if abs(cos_lat) < 1e-9:
            raise BBoxError("cannot pad an envelope at a pole")

        km_per_deg_lon = _KM_PER_DEG_LAT * cos_lat
        padded = (
            west - west_km / km_per_deg_lon,
            south - south_km / _KM_PER_DEG_LAT,
            east + east_km / km_per_deg_lon,
            north + north_km / _KM_PER_DEG_LAT,
        )
        if padded[0] >= padded[2] or padded[1] >= padded[3]:
            raise BBoxError(
                "the padded envelope has no area. The stations span "
                f"{east - west:.6f} x {north - south:.6f} degrees and the padding "
                "adds nothing to at least one axis; give a positive padding on "
                "every side."
            )
        return cls(
            west=max(padded[0], -180.0),
            south=max(padded[1], -90.0),
            east=min(padded[2], 180.0),
            north=min(padded[3], 90.0),
        )

    # ---- geometry -----------------------------------------------------

    @property
    def centroid(self) -> tuple[float, float]:
        """(lon, lat) of the box centre."""
        return ((self.west + self.east) / 2.0, (self.south + self.north) / 2.0)

    @property
    def width_km(self) -> float:
        _, lat = self.centroid
        return (self.east - self.west) * _KM_PER_DEG_LAT * math.cos(math.radians(lat))

    @property
    def height_km(self) -> float:
        return (self.north - self.south) * _KM_PER_DEG_LAT

    def as_tuple(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) - the order pyogrio and shapely both expect."""
        return (self.west, self.south, self.east, self.north)

    # Aliases matching the sibling hf-radar-extract naming, so code moved
    # between the two toolkits does not silently swap an axis.
    @property
    def lon_min(self) -> float:
        return self.west

    @property
    def lon_max(self) -> float:
        return self.east

    @property
    def lat_min(self) -> float:
        return self.south

    @property
    def lat_max(self) -> float:
        return self.north

    def contains(self, lon, lat):
        """Boolean mask of points inside the box. Accepts scalars or arrays.

        Edges are inclusive, so a point exactly on the boundary is kept.
        """
        return (
            (lon >= self.west)
            & (lon <= self.east)
            & (lat >= self.south)
            & (lat <= self.north)
        )

    def as_polygon(self):
        """The box as a shapely polygon in EPSG:4326, for clipping."""
        from shapely.geometry import box as _box

        return _box(self.west, self.south, self.east, self.north)

    def to_crs_bounds(self, crs) -> tuple[float, float, float, float]:
        """This box's bounds expressed in ``crs``.

        Transforming only the four corners understates the extent when the
        source CRS curves relative to lon/lat - a real effect for California
        Teale Albers, which most BIOS layers use. The edges are therefore
        densified before transforming, and the result is the envelope of the
        transformed ring. Erring wide is safe: the exact clip happens later in
        the source CRS against the true polygon.
        """
        from pyproj import CRS, Transformer

        target = CRS.from_user_input(crs)
        if target.equals(CRS.from_epsg(4326)):
            return self.as_tuple()

        n = 25
        xs, ys = [], []
        for i in range(n + 1):
            f = i / n
            # Walk all four edges so curvature on any side is captured.
            xs += [
                self.west + (self.east - self.west) * f,
                self.west + (self.east - self.west) * f,
                self.west,
                self.east,
            ]
            ys += [self.south, self.north, self.south + (self.north - self.south) * f,
                   self.south + (self.north - self.south) * f]

        tf = Transformer.from_crs(CRS.from_epsg(4326), target, always_xy=True)
        tx, ty = tf.transform(xs, ys)
        finite = [
            (a, b) for a, b in zip(tx, ty) if math.isfinite(a) and math.isfinite(b)
        ]
        if not finite:
            raise BBoxError(
                f"bounding box {self} does not project into {target.to_string()}; "
                "the source data may cover a different part of the world"
            )
        fx = [a for a, _ in finite]
        fy = [b for _, b in finite]
        return (min(fx), min(fy), max(fx), max(fy))

    def polygon_in_crs(self, crs):
        """The box as a shapely polygon in ``crs``, densified along its edges.

        Used as the clip geometry. Densifying matters for the same reason as
        above: a four-corner rectangle reprojected into Albers has straight
        edges where the true boundary is curved, which would shave slivers off
        features near the edge of the box.
        """
        from pyproj import CRS, Transformer
        from shapely.geometry import Polygon

        target = CRS.from_user_input(crs)
        if target.equals(CRS.from_epsg(4326)):
            return self.as_polygon()

        n = 50
        ring = []
        for i in range(n):  # south edge, west -> east
            ring.append((self.west + (self.east - self.west) * i / n, self.south))
        for i in range(n):  # east edge, south -> north
            ring.append((self.east, self.south + (self.north - self.south) * i / n))
        for i in range(n):  # north edge, east -> west
            ring.append((self.east - (self.east - self.west) * i / n, self.north))
        for i in range(n):  # west edge, north -> south
            ring.append((self.west, self.north - (self.north - self.south) * i / n))

        tf = Transformer.from_crs(CRS.from_epsg(4326), target, always_xy=True)
        xs, ys = tf.transform([p[0] for p in ring], [p[1] for p in ring])
        pts = [
            (x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)
        ]
        if len(pts) < 4:
            raise BBoxError(
                f"bounding box {self} does not project into {target.to_string()}"
            )
        return Polygon(pts)

    # ---- projections --------------------------------------------------

    @property
    def utm_zone(self) -> int:
        lon, _ = self.centroid
        return int(math.floor((lon + 180.0) / 6.0) % 60) + 1

    @property
    def utm_epsg(self) -> str:
        """Metric CRS for area-correct work, chosen from the box centroid.

        Deriving this instead of hardcoding a zone is what lets the toolkit work
        outside the original San Diego study area.
        """
        _, lat = self.centroid
        base = 32600 if lat >= 0 else 32700
        return f"EPSG:{base + self.utm_zone}"

    @property
    def spans_utm_zones(self) -> int:
        """How many UTM zones the box touches.

        More than one means the single projected grid will stretch toward the
        edges. Tolerable for display, worth reporting for area calculations.
        """
        west_zone = int(math.floor((self.west + 180.0) / 6.0) % 60) + 1
        east_zone = int(math.floor((self.east + 180.0) / 6.0) % 60) + 1
        return east_zone - west_zone + 1

    def __str__(self) -> str:
        return (
            f"{self.west:.5f},{self.south:.5f},{self.east:.5f},{self.north:.5f} "
            f"({self.width_km:.1f} x {self.height_km:.1f} km)"
        )
