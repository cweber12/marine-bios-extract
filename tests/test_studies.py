"""Reading the shared studies directory.

The assertions are all about what a user of the toolkit would observe: which
studies appear in a listing, which stations shape the box, which study a name
resolves to, and what a run leaves behind in ``study.json``. Nothing here
asserts on how the reader got there.
"""

from __future__ import annotations

import json

import pytest

from biosextract import studies


def write_study(
    root,
    study_id,
    label=None,
    created="2026-08-07T19:13:38Z",
    stations=None,
    producers=None,
    extra=None,
):
    """Build one study directory shaped like what station-data-extract writes."""
    d = root / study_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema_version": 1,
        "study_id": study_id,
        "label": label if label is not None else study_id.split("__")[-1],
        "created_utc": created,
        "created_by": "station-data-extract 2026-08-05-a",
        "site": {"name": "La Jolla, CA", "stations": stations or {}},
        "time_window_utc": {"start": "2026-06-23T19:13:38Z", "end": created},
        "producers": producers if producers is not None else [],
        "status": "ok",
        "notes": "",
    }
    meta.update(extra or {})
    (d / studies.STUDY_META_NAME).write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return d


#: The reference study: four positioned stations and one subject buoy that has
#: never had a position recorded.
REFERENCE_STATIONS = {
    "yellow_buoy": {"lon": None, "lat": None, "role": "subject"},
    "autoss": {"lon": -117.257, "lat": 32.867, "role": "primary_reference"},
    "LJAC1": {"lon": -117.258, "lat": 32.867, "role": "cross_check"},
    "46254": {"lon": -117.267, "lat": 32.868, "role": "surface_endpoint"},
    "LJPC1": {"lon": -117.257, "lat": 32.866, "role": "context_only"},
}


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def test_a_well_formed_study_parses(tmp_path):
    d = write_study(tmp_path, "20260807T1913Z__session", stations=REFERENCE_STATIONS)

    study = studies.load_study(d)

    assert study.error is None
    assert study.study_id == "20260807T1913Z__session"
    assert study.label == "session"
    assert len(study.stations) == 5
    assert study.usable


def test_an_unparseable_study_still_loads_with_its_error(tmp_path):
    d = tmp_path / "20260807T0000Z__broken"
    d.mkdir()
    (d / studies.STUDY_META_NAME).write_text("{not json", encoding="utf-8")

    study = studies.load_study(d)

    assert study.error is not None
    assert not study.usable
    assert "unreadable" in study.unusable_reason


def test_an_unparseable_study_is_still_listed(tmp_path):
    write_study(tmp_path, "20260807T1913Z__good", stations=REFERENCE_STATIONS)
    bad = tmp_path / "20260807T2000Z__broken"
    bad.mkdir()
    (bad / studies.STUDY_META_NAME).write_text("{not json", encoding="utf-8")

    listed = studies.list_studies(tmp_path)

    # A study that vanishes from a listing with no explanation is worse than one
    # that appears carrying its error. It sorts last, having no readable
    # creation time to sort by, but it is there.
    assert sorted(s.study_id for s in listed) == [
        "20260807T1913Z__good",
        "20260807T2000Z__broken",
    ]
    broken = next(s for s in listed if s.study_id == "20260807T2000Z__broken")
    assert broken.error is not None


def test_studies_are_listed_newest_first(tmp_path):
    write_study(tmp_path, "a__one", created="2026-08-01T00:00:00Z")
    write_study(tmp_path, "b__two", created="2026-08-05T00:00:00Z")
    write_study(tmp_path, "c__three", created="2026-08-03T00:00:00Z")

    assert [s.study_id for s in studies.list_studies(tmp_path)] == [
        "b__two",
        "c__three",
        "a__one",
    ]


def test_a_directory_that_is_not_a_study_is_ignored(tmp_path):
    write_study(tmp_path, "real__study")
    (tmp_path / "notes").mkdir()
    (tmp_path / "loose.txt").write_text("hello", encoding="utf-8")

    assert [s.study_id for s in studies.list_studies(tmp_path)] == ["real__study"]


def test_a_missing_studies_root_lists_nothing_and_requires_loudly(tmp_path):
    absent = tmp_path / "nowhere"

    assert studies.list_studies(absent) == []
    with pytest.raises(studies.StudyError) as exc:
        studies.require_studies_root(absent)
    assert str(absent) in str(exc.value)


# --------------------------------------------------------------------------
# stations and the envelope
# --------------------------------------------------------------------------


def test_positioned_and_unpositioned_stations_are_separated(tmp_path):
    d = write_study(tmp_path, "s", stations=REFERENCE_STATIONS)

    study = studies.load_study(d)

    assert sorted(st.name for st in study.positioned) == [
        "46254",
        "LJAC1",
        "LJPC1",
        "autoss",
    ]
    assert [st.name for st in study.skipped] == ["yellow_buoy"]
    assert study.station_summary() == "4/5 st"


def test_a_skipped_station_records_its_role_and_a_reason(tmp_path):
    study = studies.load_study(write_study(tmp_path, "s", stations=REFERENCE_STATIONS))

    assert study.skipped_records() == [
        {
            "name": "yellow_buoy",
            "role": "subject",
            "reason": "no lon/lat in study.json",
        }
    ]


def test_the_envelope_ignores_role(tmp_path):
    """A role vocabulary that grows later must never silently shrink the box."""
    stations = {
        "core": {"lon": -117.26, "lat": 32.86, "role": "primary_reference"},
        "fringe": {"lon": -117.30, "lat": 32.90, "role": "some_role_invented_later"},
        "no_role_at_all": {"lon": -117.20, "lat": 32.80},
    }
    study = studies.load_study(write_study(tmp_path, "s", stations=stations))

    assert study.envelope() == (-117.30, 32.80, -117.20, 32.90)


def test_a_study_with_no_positioned_stations_says_so(tmp_path):
    stations = {"yellow_buoy": {"lon": None, "lat": None, "role": "subject"}}
    study = studies.load_study(write_study(tmp_path, "s", stations=stations))

    assert study.envelope() is None
    assert not study.usable
    assert study.unusable_reason == "no station in this study has a position"


def test_a_study_with_no_stations_at_all_says_so(tmp_path):
    study = studies.load_study(write_study(tmp_path, "s", stations={}))

    assert study.envelope() is None
    assert study.unusable_reason == "no stations listed in study.json"


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


@pytest.fixture
def three_studies(tmp_path):
    write_study(
        tmp_path, "20260805T2352Z__yellow-buoy", created="2026-08-05T23:52:00Z"
    )
    write_study(
        tmp_path, "20260807T1720Z__yellow-buoy", created="2026-08-07T17:20:00Z"
    )
    write_study(tmp_path, "20260807T1913Z__session", created="2026-08-07T19:13:00Z")
    return studies.list_studies(tmp_path)


def test_resolves_by_id(three_studies):
    got = studies.resolve_study("20260805T2352Z__yellow-buoy", three_studies)
    assert got.study_id == "20260805T2352Z__yellow-buoy"


def test_resolves_by_label(three_studies):
    assert studies.resolve_study("session", three_studies).label == "session"


def test_resolves_by_unique_fragment(three_studies):
    assert (
        studies.resolve_study("1720", three_studies).study_id
        == "20260807T1720Z__yellow-buoy"
    )


def test_resolves_latest(three_studies):
    assert studies.resolve_study("latest", three_studies).study_id == (
        "20260807T1913Z__session"
    )


def test_an_ambiguous_fragment_lists_the_candidates_rather_than_guessing(three_studies):
    with pytest.raises(studies.StudyError) as exc:
        studies.resolve_study("yellow-buoy", three_studies)

    message = str(exc.value)
    assert "matches 2 studies" in message
    assert "20260805T2352Z__yellow-buoy" in message
    assert "20260807T1720Z__yellow-buoy" in message


def test_an_unmatched_name_lists_what_is_available(three_studies):
    with pytest.raises(studies.StudyError) as exc:
        studies.resolve_study("no-such-study", three_studies)

    assert "no study matches" in str(exc.value)
    assert "20260807T1913Z__session" in str(exc.value)


# --------------------------------------------------------------------------
# the one write
# --------------------------------------------------------------------------


def test_appending_a_producer_entry_leaves_every_other_key_untouched(tmp_path):
    d = write_study(
        tmp_path,
        "s",
        stations=REFERENCE_STATIONS,
        producers=[{"name": "station-data", "dir": "station-data", "status": "ok"}],
    )
    before = json.loads((d / "study.json").read_text(encoding="utf-8"))
    study = studies.load_study(d)

    studies.record_producer(study, {"name": studies.PRODUCER, "status": "ok"})

    after = json.loads((d / "study.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "producers"} == {
        k: v for k, v in before.items() if k != "producers"
    }
    assert list(after) == list(before)  # key order too
    assert after["producers"][0] == before["producers"][0]
    assert after["producers"][-1] == {"name": studies.PRODUCER, "status": "ok"}


def test_the_file_is_byte_identical_apart_from_our_entry(tmp_path):
    d = write_study(tmp_path, "s", stations=REFERENCE_STATIONS)
    original = (d / "study.json").read_text(encoding="utf-8")

    studies.record_producer(studies.load_study(d), {"name": studies.PRODUCER})

    # Put the producers list back to what it was and the bytes must match:
    # nothing else about the document moved, not even its whitespace.
    restored = json.loads((d / "study.json").read_text(encoding="utf-8"))
    restored["producers"] = []
    assert json.dumps(restored, indent=2) + "\n" == original


def test_a_second_run_replaces_our_entry_rather_than_stacking_them(tmp_path):
    d = write_study(tmp_path, "s", stations=REFERENCE_STATIONS)
    study = studies.load_study(d)

    studies.record_producer(study, {"name": studies.PRODUCER, "status": "incomplete"})
    studies.record_producer(study, {"name": studies.PRODUCER, "status": "ok"})

    after = json.loads((d / "study.json").read_text(encoding="utf-8"))
    ours = [p for p in after["producers"] if p["name"] == studies.PRODUCER]
    assert ours == [{"name": studies.PRODUCER, "status": "ok"}]


def test_our_previous_status_is_read_back(tmp_path):
    d = write_study(tmp_path, "s", stations=REFERENCE_STATIONS)
    studies.record_producer(studies.load_study(d), {"name": studies.PRODUCER, "status": "ok"})

    assert studies.load_study(d).our_status == "ok"
    assert studies.producer_status(studies.load_study(d)) == "ok"


# --------------------------------------------------------------------------
# the canary
# --------------------------------------------------------------------------


def test_real_studies_still_parse():
    """Parse the actual sibling studies directory whenever it is present.

    Following the prior art in cudem-extract: a change in what
    station-data-extract writes should fail this suite rather than surface
    months later as a box in the wrong place. Skips on a clean clone, so it is
    a guard rather than a dependency.
    """
    root = studies.default_studies_root()
    if not root.is_dir():
        pytest.skip(f"no shared studies directory at {root}")

    found = studies.list_studies(root)
    if not found:
        pytest.skip(f"{root} exists but holds no studies")

    for study in found:
        assert study.error is None, f"{study.study_id}: {study.error}"
        assert study.study_id
        for st in study.stations:
            assert st.role
            if st.positioned:
                assert -180.0 <= st.lon <= 180.0
                assert -90.0 <= st.lat <= 90.0

    usable = [s for s in found if s.usable]
    assert usable, "no study in the shared directory has a positioned station"
    for study in usable:
        west, south, east, north = study.envelope()
        assert west <= east and south <= north
