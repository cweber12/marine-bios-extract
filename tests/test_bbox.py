"""Bounding box contract.

The order is WEST,SOUTH,EAST,NORTH everywhere in this repo family. These tests
exist mostly to make a silent axis swap impossible.
"""

from __future__ import annotations

import math

import pytest

from biosextract.bbox import BBox, BBoxError


def test_parse_orders_west_south_east_north():
    b = BBox.parse("-117.30,32.80,-117.24,32.88")
    assert (b.west, b.south, b.east, b.north) == (-117.30, 32.80, -117.24, 32.88)


def test_parse_tolerates_whitespace():
    assert BBox.parse(" -117.30 , 32.80 , -117.24 , 32.88 ") == BBox.parse(
        "-117.30,32.80,-117.24,32.88"
    )


@pytest.mark.parametrize(
    "text",
    ["-117.30,32.80,-117.24", "", "a,b,c,d", "-117.30,32.80,-117.24,32.88,1"],
)
def test_parse_rejects_malformed(text):
    with pytest.raises(BBoxError):
        BBox.parse(text)


def test_reversed_longitudes_rejected_with_a_useful_message():
    with pytest.raises(BBoxError, match="WEST,SOUTH,EAST,NORTH"):
        BBox.parse("-117.24,32.80,-117.30,32.88")


def test_reversed_latitudes_rejected():
    with pytest.raises(BBoxError):
        BBox.parse("-117.30,32.88,-117.24,32.80")


def test_out_of_range_rejected():
    with pytest.raises(BBoxError):
        BBox(west=-200.0, south=32.8, east=-117.2, north=32.9)
    with pytest.raises(BBoxError):
        BBox(west=-117.3, south=-91.0, east=-117.2, north=32.9)


def test_nan_rejected():
    with pytest.raises(BBoxError):
        BBox(west=float("nan"), south=32.8, east=-117.2, north=32.9)


def test_from_center_is_square_in_km_not_degrees():
    b = BBox.from_center(32.8523, -117.26935, 5.0)
    assert b.width_km == pytest.approx(10.0, rel=0.02)
    assert b.height_km == pytest.approx(10.0, rel=0.02)
    # Wider in degrees of longitude than latitude away from the equator.
    assert (b.east - b.west) > (b.north - b.south)


def test_from_center_rejects_bad_radius():
    with pytest.raises(BBoxError):
        BBox.from_center(32.85, -117.27, 0.0)


def test_utm_epsg_is_derived_not_hardcoded():
    assert BBox.parse("-117.30,32.80,-117.24,32.88").utm_epsg == "EPSG:32611"
    # A box off Massachusetts must not come back as a California zone.
    assert BBox.parse("-71.10,42.30,-71.00,42.40").utm_epsg == "EPSG:32619"
    # Southern hemisphere uses the 327xx band.
    assert BBox.parse("151.10,-33.90,151.30,-33.80").utm_epsg == "EPSG:32756"


def test_contains_is_inclusive_on_the_edge():
    b = BBox.parse("-117.30,32.80,-117.24,32.88")
    assert bool(b.contains(-117.30, 32.80))
    assert bool(b.contains(-117.27, 32.84))
    assert not bool(b.contains(-117.31, 32.84))


def test_to_crs_bounds_widens_rather_than_shrinks():
    """Densified reprojection must not understate the extent.

    A four-corner transform into Albers clips the curved edges; the densified
    envelope has to be at least as large in every direction.
    """
    from pyproj import CRS, Transformer

    b = BBox.parse("-124.0,32.5,-117.0,42.0")  # deliberately large, so curvature bites
    dense = b.to_crs_bounds("EPSG:3310")

    tf = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(3310), always_xy=True)
    xs, ys = tf.transform(
        [b.west, b.east, b.west, b.east], [b.south, b.south, b.north, b.north]
    )
    naive = (min(xs), min(ys), max(xs), max(ys))

    assert dense[0] <= naive[0]
    assert dense[1] <= naive[1]
    assert dense[2] >= naive[2]
    assert dense[3] >= naive[3]


def test_to_crs_bounds_is_identity_for_4326():
    b = BBox.parse("-117.30,32.80,-117.24,32.88")
    assert b.to_crs_bounds("EPSG:4326") == b.as_tuple()


def test_polygon_in_crs_round_trips():
    b = BBox.parse("-117.30,32.80,-117.24,32.88")
    poly = b.polygon_in_crs("EPSG:3310")
    assert poly.is_valid and poly.area > 0
    assert math.isfinite(poly.bounds[0])


def test_spans_utm_zones():
    assert BBox.parse("-117.30,32.80,-117.24,32.88").spans_utm_zones == 1
    assert BBox.parse("-121.0,32.0,-114.0,35.0").spans_utm_zones > 1
