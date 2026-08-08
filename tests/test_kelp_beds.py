"""Administrative Kelp Beds (ds3135), end to end.

The layer's own field names are the subject here. ``Shape_Length`` and
``Shape_Area`` describe the *uncut* bed, so a clip that leaves them in place
hands somebody a bed area that is right for a polygon they do not have. The
registry declares them and the clip renames and recomputes them; this asserts
that the declaration is what drives it, using a fixture carrying ds3135's real
schema rather than the default ``Sub``/``Acres`` one.

Assertions about *registry content* - what the licence says, whether a citation
is finished - belong in `test_audit` against the synthetic registry, per #24.
What is asserted here is the contract that the key exists and extracts.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from biosextract import catalog
from biosextract.cli import main
from tests.fixtures import TEST_BBOX, make_gdb_archive

KEY = "admin-kelp-beds"
#: Output filenames sanitise the key; the hyphens do not survive.
STEM = "admin_kelp_beds"

#: ds3135's real schema, read from the archive on 2026-08-08.
FIELDS = ["KelpBed", "Status", "Lessee", "Shape_Length", "Shape_Area"]


def values():
    return [
        np.array([118, 119, 120], dtype=np.int32),
        np.array(["open", "leased", "closed"], dtype=object),
        np.array(["", "A Kelp Company", ""], dtype=object),
        # The publisher's numbers, describing the uncut bed.
        np.array([1000.0, 2000.0, 3000.0]),
        np.array([50000.0, 60000.0, 70000.0]),
    ]


@pytest.fixture
def archive(tmp_path):
    return make_gdb_archive(tmp_path, "ds3135", fields=FIELDS, values=values())


def test_the_key_is_registered_and_runs_by_default():
    """A contract, not an assertion about content: the key exists and is wired.

    `resolve_keys(None)` is what a bare `bios extract` runs, so a layer missing
    from it is one nobody gets without knowing to ask.
    """
    assert KEY in catalog.DATASETS
    assert KEY in catalog.resolve_keys(None)


def test_extracts_and_recomputes_the_publishers_geometry_fields(archive, tmp_path):
    out = tmp_path / "out"
    code = main(
        [
            "extract",
            f"--bbox={TEST_BBOX}",
            "--datasets", KEY,
            "--local-archive", f"{KEY}={archive}",
            "--formats", "geojson",
            "--out-dir", str(out),
            "--prefix", "lajolla",
            "--cache-dir", str(tmp_path / "cache"),
        ]
    )
    assert code == 0

    doc = json.loads((out / f"lajolla_{STEM}.geojson").read_text())
    props = doc["features"][0]["properties"]

    # The stale values survive under a name that says they are stale...
    assert props["orig_Shape_Area"] in (50000.0, 60000.0, 70000.0)
    assert props["orig_Shape_Length"] in (1000.0, 2000.0, 3000.0)
    assert "Shape_Area" not in props, "the bare name must not read as current"
    # ...and the live ones arrive under names that carry their unit, so nobody
    # has to know which projection the publisher measured in.
    assert props["area_m2"] > 0
    assert props["length_m"] > 0
    assert props["area_m2"] != props["orig_Shape_Area"]
    assert "clip_fraction" in props

    # The layer's own attributes are untouched - only geometry fields are.
    assert props["Status"] in ("open", "leased", "closed")
    assert "orig_Status" not in props


def test_a_clipped_bed_reports_the_fraction_that_survived(archive, tmp_path):
    """A bed cut by the box is the case the recompute exists for.

    Without it the acreage of a sliver reads as the acreage of the whole bed,
    which is the plausible-wrong-number this repo family exists to prevent.
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

    cut = [f for f in doc["features"] if f["properties"]["clipped"]]
    assert cut, "the fixture straddles the boundary, so one feature is cut"
    for feature in cut:
        assert 0 < feature["properties"]["clip_fraction"] < 1
