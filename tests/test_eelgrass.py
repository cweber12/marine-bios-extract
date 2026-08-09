"""Eelgrass (ds1503), end to end.

The case this layer adds is ``area_acres``. Every other precomputed area in the
registry is called ``Acres``, so until now a rename-and-recompute driven by the
registry declaration and one driven by a hard-coded field name would have looked
identical from the outside. They are not, and this is where the difference shows.

Registry *content* - whether the licence is verified, whether the citation is
finished - is asserted in `test_audit` against the synthetic registry, per #24.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from biosextract import catalog
from biosextract.cli import main
from tests.fixtures import TEST_BBOX, make_gdb_archive

KEY = "eelgrass"
STEM = "eelgrass"

#: ds1503's real schema, read from the archive on 2026-08-08.
FIELDS = ["fsource", "Year", "Location", "area_acres", "Shape_Length", "Shape_Area"]

#: The publisher's acreage, describing the uncut bed.
ACRES = [10.0, 20.0, 30.0]


def values():
    return [
        np.array(["A survey", "Another survey", "A third"], dtype=object),
        np.array([2016, 2019, 2021], dtype=np.int32),
        np.array(["Mission Bay", "La Jolla", "Elsewhere"], dtype=object),
        np.array(ACRES, dtype=np.float32),
        np.array([1000.0, 2000.0, 3000.0]),
        np.array([40000.0, 50000.0, 60000.0]),
    ]


@pytest.fixture
def archive(tmp_path):
    return make_gdb_archive(tmp_path, "ds1503", fields=FIELDS, values=values())


def test_the_key_is_registered_and_runs_by_default():
    assert KEY in catalog.DATASETS
    assert KEY in catalog.resolve_keys(None)


def test_area_acres_is_recomputed_like_any_other_declared_area(archive, tmp_path):
    """The declaration drives it, not the field name.

    `area_acres` is spelled nothing like `Acres`, so a code path keyed to the
    familiar name would leave the publisher's uncut acreage sitting on a clipped
    polygon under a name that reads as current.
    """
    out = tmp_path / "out"
    code = main(
        [
            "extract",
            f"--bbox={TEST_BBOX}",
            "--datasets", KEY,
            "--local-archive", f"{KEY}={archive}",
            "--formats", "geojson",
            "--out-dir", str(out),
            "--cache-dir", str(tmp_path / "cache"),
        ]
    )
    assert code == 0

    doc = json.loads((out / f"extract_{STEM}.geojson").read_text())
    props = doc["features"][0]["properties"]

    assert props["orig_area_acres"] in ACRES, "the publisher's value, named stale"
    assert "area_acres" not in props, "the bare name must not read as current"
    assert props["area_m2"] > 0, "and a live measurement replaces it"


def test_the_survey_attributes_survive_untouched(archive, tmp_path):
    """A polygon is evidence of eelgrass when its survey ran.

    Year and fsource are what make that readable, so they must come through as
    themselves - they are not geometry, and nothing should rename them.
    """
    out = tmp_path / "out"
    main(
        [
            "extract",
            f"--bbox={TEST_BBOX}",
            "--datasets", KEY,
            "--local-archive", f"{KEY}={archive}",
            "--formats", "geojson",
            "--out-dir", str(out),
            "--cache-dir", str(tmp_path / "cache"),
        ]
    )
    doc = json.loads((out / f"extract_{STEM}.geojson").read_text())
    props = doc["features"][0]["properties"]

    assert props["Year"] in (2016, 2019, 2021)
    assert props["fsource"]
    assert "orig_Year" not in props


def test_a_cut_bed_reports_the_fraction_that_survived(archive, tmp_path):
    out = tmp_path / "out"
    main(
        [
            "extract",
            f"--bbox={TEST_BBOX}",
            "--datasets", KEY,
            "--local-archive", f"{KEY}={archive}",
            "--formats", "geojson",
            "--out-dir", str(out),
            "--cache-dir", str(tmp_path / "cache"),
        ]
    )
    doc = json.loads((out / f"extract_{STEM}.geojson").read_text())

    cut = [f for f in doc["features"] if f["properties"]["clipped"]]
    assert cut, "the fixture straddles the boundary"
    for feature in cut:
        assert 0 < feature["properties"]["clip_fraction"] < 1
