"""Raster clipping, and the grid-anchoring rule.

Anchoring the output grid to the requested box rather than to the data extent is
what lets two runs be differenced pixel for pixel. It is easy to lose by
accident, so it is asserted directly.
"""

from __future__ import annotations

import pytest

from biosextract import raster
from biosextract.archive import select
from biosextract.bbox import BBox
from tests.fixtures import TEST_BBOX


@pytest.fixture
def clipped(raster_archive):
    payload = select(raster_archive, "raster")
    return raster.clip(payload.vsi_path, BBox.parse(TEST_BBOX), verbose=False)


def test_reads_and_reports_the_source_crs(clipped):
    assert clipped.source_crs.upper().endswith("3310")
    assert clipped.width > 0 and clipped.height > 0


def test_grid_is_anchored_to_the_box_not_the_data(raster_archive):
    """Two different boxes must both start exactly at their own west/north edge."""
    payload = select(raster_archive, "raster")
    for spec in (TEST_BBOX, "-117.28,32.82,-117.25,32.86"):
        bbox = BBox.parse(spec)
        result = raster.clip(payload.vsi_path, bbox, verbose=False)
        left, _bottom, _right, top = bbox.to_crs_bounds(result.crs)
        assert result.transform.c == pytest.approx(left, abs=1e-6)
        assert result.transform.f == pytest.approx(top, abs=1e-6)


def test_two_runs_over_one_box_are_pixel_aligned(raster_archive):
    payload = select(raster_archive, "raster")
    bbox = BBox.parse(TEST_BBOX)
    a = raster.clip(payload.vsi_path, bbox, verbose=False)
    b = raster.clip(payload.vsi_path, bbox, verbose=False)
    assert a.transform == b.transform
    assert (a.width, a.height) == (b.width, b.height)


def test_nodata_is_preserved_not_filled(clipped):
    assert clipped.nodata == -1
    assert clipped.valid_pixels <= clipped.total_pixels


def test_explicit_resolution_is_honoured(raster_archive):
    payload = select(raster_archive, "raster")
    result = raster.clip(
        payload.vsi_path, BBox.parse(TEST_BBOX), resolution=100.0, verbose=False
    )
    assert abs(result.transform.a) == pytest.approx(100.0)


def test_non_overlapping_box_raises_with_both_extents(raster_archive):
    payload = select(raster_archive, "raster")
    with pytest.raises(raster.RasterError) as exc:
        raster.clip(payload.vsi_path, BBox.parse("-100.0,40.0,-99.0,41.0"), verbose=False)
    assert "does not overlap" in str(exc.value)


def test_geotiff_round_trips(clipped, tmp_path):
    import rasterio

    path = raster.write_geotiff(clipped, tmp_path / "kelp.tif")
    with rasterio.open(path) as src:
        assert src.width == clipped.width
        assert src.height == clipped.height
        assert src.nodata == clipped.nodata
        assert src.transform == clipped.transform
