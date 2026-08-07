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


# --------------------------------------------------------------------------
# padding a station envelope into a study box
# --------------------------------------------------------------------------

#: The reference study's positioned stations, as a WEST,SOUTH,EAST,NORTH
#: envelope: a 0.9 x 0.2 km sliver off Scripps Pier.
LA_JOLLA_ENVELOPE = (-117.267, 32.866, -117.257, 32.868)


def test_each_side_is_padded_independently():
    box = BBox.from_envelope(
        LA_JOLLA_ENVELOPE, north_km=5.0, south_km=10.0, east_km=20.0, west_km=1.0
    )
    west, south, east, north = LA_JOLLA_ENVELOPE

    # Four different distances give four different edge movements, and each one
    # moves the edge it names.
    assert north - box.north < 0
    assert (box.north - north) < (south - box.south)  # 5 km north, 10 km south
    assert (box.east - east) > (west - box.west)  # 20 km east, 1 km west
    assert (box.north - north) * 2 == pytest.approx(south - box.south, rel=1e-9)


def test_padding_is_symmetric_about_the_envelope():
    box = BBox.from_envelope(
        LA_JOLLA_ENVELOPE, north_km=7.0, south_km=7.0, east_km=7.0, west_km=7.0
    )
    west, south, east, north = LA_JOLLA_ENVELOPE

    assert box.north - north == pytest.approx(south - box.south, rel=1e-12)
    assert box.east - east == pytest.approx(west - box.west, rel=1e-12)
    # ... and the envelope still sits in the middle of what came out.
    assert (box.west + box.east) / 2 == pytest.approx((west + east) / 2, abs=1e-12)
    assert (box.south + box.north) / 2 == pytest.approx((south + north) / 2, abs=1e-12)


def test_padding_is_kilometres_converted_per_axis():
    """A degree of longitude at 32.87 N is about 16% shorter than one of latitude."""
    box = BBox.from_envelope(
        LA_JOLLA_ENVELOPE, north_km=10.0, south_km=10.0, east_km=10.0, west_km=10.0
    )
    west, south, east, north = LA_JOLLA_ENVELOPE

    grown_lat_deg = box.north - north
    grown_lon_deg = box.east - east

    # Equal kilometres, so more degrees east than north at this latitude ...
    assert grown_lon_deg > grown_lat_deg
    assert grown_lon_deg / grown_lat_deg == pytest.approx(
        1 / math.cos(math.radians(32.867)), rel=1e-3
    )
    # ... and equal kilometres once measured back in kilometres.
    assert box.height_km - 0.222 == pytest.approx(20.0, abs=0.05)


def test_a_degenerate_envelope_still_produces_a_usable_box():
    """Every station sharing one position is a real case, not an error."""
    point = (-117.257, 32.867, -117.257, 32.867)

    box = BBox.from_envelope(point, north_km=5.0, south_km=5.0, east_km=5.0, west_km=5.0)

    assert box.width_km == pytest.approx(10.0, abs=0.05)
    assert box.height_km == pytest.approx(10.0, abs=0.05)


def test_zero_padding_on_a_degenerate_envelope_explains_itself():
    point = (-117.257, 32.867, -117.257, 32.867)

    with pytest.raises(BBoxError) as exc:
        BBox.from_envelope(point, north_km=5.0, south_km=5.0, east_km=0.0, west_km=0.0)

    assert "no area" in str(exc.value)


def test_negative_padding_is_refused():
    with pytest.raises(BBoxError) as exc:
        BBox.from_envelope(
            LA_JOLLA_ENVELOPE, north_km=5.0, south_km=-5.0, east_km=5.0, west_km=5.0
        )

    assert "south padding must not be negative" in str(exc.value)


def test_an_inside_out_envelope_is_refused():
    with pytest.raises(BBoxError):
        BBox.from_envelope(
            (-117.20, 32.90, -117.30, 32.80),
            north_km=5.0,
            south_km=5.0,
            east_km=5.0,
            west_km=5.0,
        )
