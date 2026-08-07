"""The expansion rule on its own: a box, some geometry, a budget.

Every fixture here is built in California Teale Albers, like the rest of the
suite, so the reprojection that real BIOS layers force is exercised rather than
assumed. Nothing in this file reaches the network or the run.
"""

from __future__ import annotations

import pytest

from biosextract import expansion
from biosextract.bbox import BBox
from tests.fixtures import make_cluster_shapefile

#: The reference-study rectangle. ``PAIR_CUT`` straddles its east edge.
BOX = BBox.parse("-117.30,32.80,-117.24,32.88")


@pytest.fixture
def shapefile(tmp_path):
    return make_cluster_shapefile(tmp_path / "layer")


def read(shapefile, budget, box=BOX, key="mpa"):
    """A layer read to the window the budget allows, the way the stage does."""
    return expansion.read_window(
        str(shapefile), expansion.window(box, budget), key=key
    )


def run(shapefile, budget_km, box=BOX, **kwargs):
    budget = expansion.Budget.uniform(budget_km)
    return expansion.expand(box, [read(shapefile, budget, box)], budget, **kwargs)


# --------------------------------------------------------------------------
# the case the rule exists for
# --------------------------------------------------------------------------


def test_a_cut_polygon_pulls_in_its_touching_partner(shapefile):
    result = run(shapefile, 5.0)

    captured = [name for group in result.captured for name in group.names]
    assert "South La Jolla SMR" in captured
    assert "South La Jolla SMCA" in captured
    # The partner lies wholly outside the original box, so a run without this
    # rule loses it entirely.
    assert BOX.east < -117.23
    assert result.box.east > -117.21


def test_the_captured_group_gets_a_margin_and_is_no_longer_cut(shapefile):
    result = run(shapefile, 5.0)

    assert result.still_cut == {"mpa": 0}
    # Not sitting exactly on the boundary, where one rounding error would put
    # it back outside.
    assert result.box.east > -117.21
    assert result.still_cut["mpa"] == 0


def test_a_box_that_cuts_nothing_is_returned_unchanged(shapefile):
    """The stations are elsewhere; there is nothing for the rule to do."""
    elsewhere = BBox.parse("-119.60,34.30,-119.40,34.50")

    result = run(shapefile, 5.0, box=elsewhere)

    assert result.box.as_tuple() == elsewhere.as_tuple()
    assert result.moved is False
    assert result.captured == []
    assert result.refused == []


def test_a_feature_wholly_inside_the_box_is_not_cut(shapefile):
    """Only the boundary matters. Containing a feature is not cutting it."""
    wide = BBox.parse("-117.30,32.80,-117.19,32.88")

    result = run(shapefile, 5.0, box=wide)

    assert result.still_cut == {"mpa": 0}
    assert result.moved is False


# --------------------------------------------------------------------------
# the cap
# --------------------------------------------------------------------------


def test_a_group_larger_than_the_budget_is_left_cut_and_reported(shapefile):
    result = run(shapefile, 1.0)

    assert result.moved is False
    assert result.captured == []
    assert len(result.refused) == 1
    refused = result.refused[0]
    assert refused.names == ["South La Jolla SMR", "South La Jolla SMCA"]
    assert refused.width_km == pytest.approx(3.7, abs=0.2)
    # Left cut, and the feature that is cut keeps its ordinary handling
    # downstream - the rule declines, it does not paper over.
    assert result.still_cut == {"mpa": 1}


def test_the_refusal_says_what_it_is_and_how_big(shapefile):
    report = run(shapefile, 1.0).as_dict()

    assert report["refused"][0]["layer"] == "mpa"
    assert report["refused"][0]["features"] == 2
    assert report["refused"][0]["names"][0] == "South La Jolla SMR"
    assert report["refused"][0]["size_km"][0] == pytest.approx(3.7, abs=0.2)
    assert "budget" in report["refused"][0]["reason"]
    assert report["budget_km"]["east_km"] == 1.0


def test_each_side_is_capped_by_the_padding_chosen_for_that_side(shapefile):
    """Two kilometres inland must stay two kilometres inland."""
    budget = expansion.Budget(north_km=10.0, south_km=10.0, east_km=1.0, west_km=2.0)

    result = expansion.expand(BOX, [read(shapefile, budget)], budget)

    grew = BOX.growth_km(result.box)
    assert grew["east"] <= 1.0 + 1e-6
    assert grew["west"] <= 2.0 + 1e-6
    # The generous sides were not spent either; nothing north or south is cut.
    assert grew["north"] == pytest.approx(0.0)
    assert grew["south"] == pytest.approx(0.0)


def test_the_box_never_leaves_the_window_the_budget_allows(shapefile):
    budget = expansion.Budget.uniform(5.0)
    limit = expansion.window(BOX, budget)

    result = expansion.expand(BOX, [read(shapefile, budget)], budget)

    assert result.box.west >= limit.west
    assert result.box.south >= limit.south
    assert result.box.east <= limit.east
    assert result.box.north <= limit.north


def test_a_zero_budget_declines_without_looking(shapefile):
    budget = expansion.Budget.uniform(0.0)

    result = expansion.expand(BOX, [read(shapefile, expansion.Budget.uniform(5.0))], budget)

    assert result.moved is False
    assert result.captured == []


# --------------------------------------------------------------------------
# rounds
# --------------------------------------------------------------------------


def test_expansion_that_reveals_a_newly_cut_feature_triggers_another_round(shapefile):
    result = run(shapefile, 5.0)

    captured = [group.names for group in result.captured]
    assert ["South La Jolla SMR", "South La Jolla SMCA"] in captured
    # Untouched by the original box; only reachable once the box grew for the
    # pair, which is a second round by definition.
    assert ["Second Round SMR"] in captured
    assert result.rounds >= 2
    assert result.still_cut == {"mpa": 0}


def test_hitting_the_round_limit_is_reported_rather_than_silent(shapefile):
    result = run(shapefile, 5.0, max_rounds=1)

    assert result.rounds == 1
    assert result.rounds_exhausted is True
    # It stopped honestly: the box moved for the pair and the straggler is
    # still cut, which is exactly what the flag says.
    assert result.still_cut == {"mpa": 1}
    assert result.as_dict()["rounds_exhausted"] is True


def test_settling_before_the_limit_does_not_claim_exhaustion(shapefile):
    result = run(shapefile, 5.0)

    assert result.rounds < expansion.DEFAULT_MAX_ROUNDS
    assert result.rounds_exhausted is False


# --------------------------------------------------------------------------
# adjacency
# --------------------------------------------------------------------------


def test_touching_features_form_one_group(shapefile):
    layer = read(shapefile, expansion.Budget.uniform(5.0))
    groups = expansion.clusters(layer.geometries)

    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]  # the touching pair, and the straggler


def test_nothing_outside_the_window_is_even_read(shapefile):
    """The cap is also what bounds the work; a statewide layer never lands whole."""
    layer = read(shapefile, expansion.Budget.uniform(5.0))

    assert "Unreachable SMR" not in layer.names


def test_a_point_is_never_counted_as_cut():
    from shapely.geometry import Point

    poly = BOX.as_polygon()
    assert expansion.is_cut(Point(-117.27, 32.84), poly) is False  # inside
    assert expansion.is_cut(Point(-117.00, 32.84), poly) is False  # outside


def test_grazing_the_boundary_is_not_being_cut():
    """A feature that shares an edge with the box but lies outside it."""
    from shapely.geometry import box as shp_box

    poly = BOX.as_polygon()
    outside = shp_box(BOX.east, 32.83, BOX.east + 0.01, 32.85)

    assert outside.intersects(poly)
    assert expansion.is_cut(outside, poly) is False


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def test_the_report_records_both_boxes_and_the_growth_per_side(shapefile):
    report = run(shapefile, 5.0).as_dict()

    assert report["applied"] is True
    assert report["moved"] is True
    assert report["box_before_wsen"] == list(BOX.as_tuple())
    assert report["box_after_wsen"][2] > report["box_before_wsen"][2]
    assert report["grew_km"]["east"] > 0
    assert report["grew_km"]["west"] == 0
    assert report["layers"] == ["mpa"]
    assert report["margin_km"] == expansion.DEFAULT_MARGIN_KM


def test_a_report_is_produced_even_when_nothing_happened(shapefile):
    """A seam that is silent when it declines is indistinguishable from an empty one."""
    elsewhere = BBox.parse("-119.60,34.30,-119.40,34.50")

    report = run(shapefile, 5.0, box=elsewhere).as_dict()

    assert report["applied"] is True
    assert report["moved"] is False
    assert report["captured"] == []
    assert report["box_before_wsen"] == report["box_after_wsen"]


def test_a_long_group_is_summarised_rather_than_listed():
    """A refused shoreline group runs to 1163 features; nobody reads 1163 names."""
    group = expansion.Group(
        layer="shoreline",
        indices=list(range(20)),
        names=[f"segment {i}" for i in range(20)],
        bounds=(-117.3, 32.8, -117.2, 32.9),
        width_km=9.3,
        height_km=11.1,
    )

    assert group.as_dict()["features"] == 20
    assert len(group.as_dict()["names"]) == expansion.NAMES_SHOWN
    assert "and 15 more" in group.label()


# --------------------------------------------------------------------------
# inputs the module refuses
# --------------------------------------------------------------------------


def test_names_that_do_not_match_the_geometry_are_refused():
    with pytest.raises(expansion.ExpansionError):
        expansion.Layer(key="mpa", geometries=[None, None], names=["only one"])


def test_a_layer_with_no_crs_is_refused_rather_than_guessed(tmp_path):
    shp = make_cluster_shapefile(tmp_path / "layer")
    (shp.parent / f"{shp.stem}.prj").unlink()

    with pytest.raises(expansion.ExpansionError, match="no coordinate reference system"):
        expansion.read_window(
            str(shp), expansion.window(BOX, expansion.Budget.uniform(5.0)), "mpa"
        )
