"""Clipping semantics and attribute integrity.

The attribute tests are the important ones. A clipped polygon carrying its
original ``Acres`` value is the exact failure this repo family is built to
prevent: nothing crashes, every number looks reasonable, and any total computed
from them is wrong.
"""

from __future__ import annotations

import pytest

from biosextract import vector
from biosextract.archive import select
from biosextract.bbox import BBox
from tests.fixtures import TEST_BBOX


@pytest.fixture
def clipped(archive):
    payload = select(archive, "vector")
    return vector.clip(
        payload.vsi_path, BBox.parse(TEST_BBOX), geometry_fields=("Acres",), verbose=False
    )


def test_features_outside_the_box_are_dropped(clipped):
    names = list(clipped.column("NAME"))
    assert "Far Away SMR" not in names
    assert set(names) == {"Matlahuayl SMR", "Scripps SMCA"}


def test_counts_are_reported_honestly(clipped):
    assert clipped.source_features == 3
    assert clipped.selected == 2
    assert clipped.kept == 2
    assert clipped.clipped_count == 1


def test_straddling_feature_is_cut_and_flagged(clipped):
    idx = list(clipped.column("NAME")).index("Scripps SMCA")
    assert bool(clipped.column("clipped")[idx]) is True
    assert 0.0 < float(clipped.column("clip_fraction")[idx]) < 1.0


def test_interior_feature_is_untouched(clipped):
    idx = list(clipped.column("NAME")).index("Matlahuayl SMR")
    assert bool(clipped.column("clipped")[idx]) is False
    assert float(clipped.column("clip_fraction")[idx]) == pytest.approx(1.0)


def test_stale_area_field_is_renamed_not_left_to_mislead(clipped):
    assert "Acres" not in clipped.fields, "the original field must not keep its name"
    assert "orig_Acres" in clipped.fields
    assert clipped.recomputed_fields == ["Acres"]


def test_recomputed_area_is_live_and_metric(clipped):
    idx = list(clipped.column("NAME")).index("Scripps SMCA")
    # The original said 200 "acres"; the recomputed value is real square metres
    # of the surviving piece, and must not equal the stale number.
    assert float(clipped.column("orig_Acres")[idx]) == 200.0
    assert float(clipped.column("area_m2")[idx]) > 1000.0
    assert clipped.measure_crs == "EPSG:32611"


def test_clip_is_bounded_by_the_box(clipped):
    from shapely import from_wkb

    box_poly = BBox.parse(TEST_BBOX).as_polygon()
    for wkb in clipped.geometry:
        geom = from_wkb(wkb)
        # Allow a hair of tolerance for the densified edge.
        assert geom.difference(box_poly).area < 1e-9


def test_whole_features_keeps_geometry_intact(archive):
    payload = select(archive, "vector")
    whole = vector.clip(
        payload.vsi_path,
        BBox.parse(TEST_BBOX),
        geometry_fields=("Acres",),
        whole_features=True,
        verbose=False,
    )
    cut = vector.clip(
        payload.vsi_path, BBox.parse(TEST_BBOX), geometry_fields=("Acres",), verbose=False
    )
    i_whole = list(whole.column("NAME")).index("Scripps SMCA")
    i_cut = list(cut.column("NAME")).index("Scripps SMCA")
    assert whole.column("area_m2")[i_whole] > cut.column("area_m2")[i_cut]
    assert whole.clipped_count == 0
    assert whole.whole_features is True


def test_output_is_reprojected_to_wgs84(clipped):
    from shapely import from_wkb

    assert clipped.crs == "EPSG:4326"
    assert clipped.source_crs == "EPSG:3310"
    x, y = from_wkb(clipped.geometry[0]).representative_point().coords[0]
    assert -118 < x < -117 and 32 < y < 33


def test_box_that_selects_nothing_raises_rather_than_returning_empty(archive):
    payload = select(archive, "vector")
    with pytest.raises(vector.EmptyClipError):
        vector.clip(
            payload.vsi_path,
            BBox.parse("-100.0,40.0,-99.0,41.0"),
            verbose=False,
        )


# --------------------------------------------------------------------------
# a layer with nothing in the box
# --------------------------------------------------------------------------

#: Nothing of the fixture layer is anywhere near here.
FAR_AWAY = "-100.0,40.0,-99.0,41.0"


@pytest.fixture
def empty(archive):
    payload = select(archive, "vector")
    return vector.clip(
        payload.vsi_path,
        BBox.parse(FAR_AWAY),
        geometry_fields=("Acres",),
        allow_empty=True,
        verbose=False,
    )


def test_allow_empty_returns_a_shaped_result_instead_of_raising(empty):
    result = empty

    assert len(result) == 0
    assert result.kept == 0
    assert result.clipped_count == 0
    # Shaped exactly like a result that found something: same columns, same
    # renaming of the attributes a clip invalidates, same CRS.
    assert "orig_Acres" in result.fields
    assert result.recomputed_fields == ["Acres"]
    assert result.fields[-4:] == ["clipped", "clip_fraction", "area_m2", "length_m"]
    assert result.crs == "EPSG:4326"
    assert result.source_crs == "EPSG:3310"
    assert all(len(col) == 0 for col in result.field_data)


def test_an_empty_result_can_be_written_in_every_format(empty, tmp_path):
    """"No features here" is only a usable answer if it reaches disk."""
    from biosextract.outputs import VECTOR_WRITERS

    for fmt, (writer, ext) in VECTOR_WRITERS.items():
        path = writer(empty, tmp_path / f"empty_{fmt}{ext}")
        assert path.exists(), fmt
        assert path.stat().st_size > 0, fmt
