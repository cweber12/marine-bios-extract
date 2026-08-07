"""The ``bios study`` command, end to end, with no network.

This is the seam that actually verifies the feature: the whole path from a study
on disk to analysis-ready files inside that same study. The existing
local-archive injection accepts any dataset key, including the automatically
fetchable ones, so a complete run - resolve, clip, write, manifest, attribution,
producer entry - is driven against a synthetic archive and a fixture studies
root without a single request.

Every assertion is on something a user could observe: the files that appear,
the numbers in the manifest, the text on the screen, and what the study says
about itself afterwards.
"""

from __future__ import annotations

import json

import pytest

from biosextract import studies, studyrun
from biosextract.cli import main
from tests.fixtures import make_archive, make_raster_archive
from tests.test_studies import REFERENCE_STATIONS, write_study


@pytest.fixture
def studies_root(tmp_path):
    """A studies directory holding the reference study and an older one."""
    root = tmp_path / "studies"
    write_study(
        root,
        "20260805T2352Z__older",
        created="2026-08-05T23:52:00Z",
        stations={"autoss": {"lon": -117.257, "lat": 32.867, "role": "primary"}},
    )
    write_study(
        root,
        "20260807T1913Z__session",
        created="2026-08-07T19:13:38Z",
        stations=REFERENCE_STATIONS,
        producers=[{"name": "station-data", "dir": "station-data", "status": "ok"}],
    )
    return root


@pytest.fixture
def mpa_archive(tmp_path):
    return make_archive(tmp_path / "archives")


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "repo-cache"


def study_argv(studies_root, cache_dir, archive, *extra, study="latest", pad="5"):
    return [
        "study",
        "--studies-root",
        str(studies_root),
        "--study",
        study,
        "--pad-km",
        pad,
        "--datasets",
        "mpa",
        "--local-archive",
        f"mpa={archive}",
        "--cache-dir",
        str(cache_dir),
        "--yes",
        *extra,
    ]


def out_dir(studies_root, study_id="20260807T1913Z__session"):
    return studies_root / study_id / "marine-bios"


def read_manifest(studies_root, study_id="20260807T1913Z__session"):
    return json.loads(
        (out_dir(studies_root, study_id) / "manifest.json").read_text(encoding="utf-8")
    )


def our_producer(studies_root, study_id="20260807T1913Z__session"):
    meta = json.loads(
        (studies_root / study_id / "study.json").read_text(encoding="utf-8")
    )
    return next(p for p in meta["producers"] if p["name"] == "marine-bios")


# --------------------------------------------------------------------------
# a full run
# --------------------------------------------------------------------------


def test_a_full_run_writes_the_expected_files(studies_root, cache_dir, mpa_archive):
    code = main(study_argv(studies_root, cache_dir, mpa_archive))

    assert code == 0
    written = sorted(p.name for p in out_dir(studies_root).iterdir())
    assert written == [
        "ATTRIBUTION.txt",
        "manifest.json",
        "mpa.csv",
        "mpa.geojson",
        "mpa.gpkg",
    ]


def test_output_lands_inside_the_study_not_in_the_repo(
    studies_root, cache_dir, mpa_archive
):
    main(study_argv(studies_root, cache_dir, mpa_archive))

    # Named to match the existing sibling producer convention, and inside the
    # study the box came from - not in an output/ directory in the checkout.
    assert out_dir(studies_root).parent.name == "20260807T1913Z__session"
    assert out_dir(studies_root).name == studies.PRODUCER == "marine-bios"


def test_downloads_are_cached_in_the_repository_not_the_study(
    studies_root, cache_dir, mpa_archive
):
    main(study_argv(studies_root, cache_dir, mpa_archive))

    # Seven studies of the same coastline must not cost seven copies of a
    # 151 MB archive.
    assert (cache_dir / "mpa").is_dir()
    assert not (studies_root / "20260807T1913Z__session" / ".cache").exists()


def test_the_manifest_pins_the_archive_and_records_the_box(
    studies_root, cache_dir, mpa_archive
):
    main(study_argv(studies_root, cache_dir, mpa_archive))
    doc = read_manifest(studies_root)

    source = doc["layers"][0]["source"]
    assert source["url"] == f"local:{mpa_archive}"
    assert len(source["sha256"]) == 64
    assert source["downloaded_bytes"] > 0
    assert "last_modified" in source and "bytes" in source

    box = doc["request"]["bbox"]
    assert box == doc["request"]["stages"]["box"]["box_wsen"]
    assert doc["request"]["padding_km"] == {
        "north_km": 5.0,
        "south_km": 5.0,
        "east_km": 5.0,
        "west_km": 5.0,
    }
    assert doc["request"]["study_id"] == "20260807T1913Z__session"


def test_the_box_in_the_manifest_is_the_box_the_geojson_declares(
    studies_root, cache_dir, mpa_archive
):
    main(study_argv(studies_root, cache_dir, mpa_archive))

    doc = read_manifest(studies_root)
    geojson = json.loads((out_dir(studies_root) / "mpa.geojson").read_text())
    assert geojson["clippedToBbox"] == doc["request"]["bbox"]


def test_publisher_use_constraints_are_printed_during_the_run(
    studies_root, cache_dir, mpa_archive, capsys
):
    main(study_argv(studies_root, cache_dir, mpa_archive))

    printed = capsys.readouterr().out
    assert "use constraint:" in printed
    assert "not intended for navigational use" in printed


def test_the_run_writes_no_escape_sequences(studies_root, cache_dir, mpa_archive, capsys):
    """A captured log has to stay readable; nothing here draws a screen."""
    main(study_argv(studies_root, cache_dir, mpa_archive))

    captured = capsys.readouterr()
    assert "\x1b" not in captured.out
    assert "\x1b" not in captured.err


# --------------------------------------------------------------------------
# stations and the box
# --------------------------------------------------------------------------


def test_unpositioned_stations_are_named_on_the_console(
    studies_root, cache_dir, mpa_archive, capsys
):
    main(study_argv(studies_root, cache_dir, mpa_archive))

    printed = capsys.readouterr().out
    assert "skipped yellow_buoy (subject): no lon/lat in study.json" in printed


def test_the_four_paddings_produce_four_independent_edges(
    studies_root, cache_dir, mpa_archive
):
    main(
        study_argv(
            studies_root,
            cache_dir,
            mpa_archive,
            "--pad-north-km",
            "1",
            "--pad-east-km",
            "20",
        )
    )
    stage = read_manifest(studies_root)["request"]["stages"]["box"]
    west, south, east, north = stage["box_wsen"]
    env_w, env_s, env_e, env_n = stage["envelope_wsen"]

    assert north - env_n < env_s - south  # 1 km north, 5 km south
    assert east - env_e > env_w - west  # 20 km east, 5 km west


def test_a_study_with_no_positioned_stations_fails_with_an_explanation(
    tmp_path, cache_dir, mpa_archive, capsys
):
    root = tmp_path / "studies"
    write_study(
        root,
        "20260807T0000Z__unplaced",
        stations={"yellow_buoy": {"lon": None, "lat": None, "role": "subject"}},
    )

    code = main(study_argv(root, cache_dir, mpa_archive))

    assert code == 2
    err = capsys.readouterr().err
    assert "no station in this study has a position" in err
    assert "Traceback" not in err


def test_a_missing_studies_directory_names_the_expected_path(
    tmp_path, cache_dir, mpa_archive, capsys
):
    absent = tmp_path / "there-is-no-studies-dir"

    code = main(study_argv(absent, cache_dir, mpa_archive))

    assert code == 2
    err = capsys.readouterr().err
    assert str(absent) in err
    assert "Traceback" not in err


# --------------------------------------------------------------------------
# resolving the study
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["latest", "20260807T1913Z__session", "session", "1913"]
)
def test_a_study_is_resolvable_four_ways(studies_root, cache_dir, mpa_archive, name):
    assert main(study_argv(studies_root, cache_dir, mpa_archive, study=name)) == 0
    assert out_dir(studies_root).is_dir()


def test_an_ambiguous_fragment_lists_the_candidates(
    tmp_path, cache_dir, mpa_archive, capsys
):
    root = tmp_path / "studies"
    write_study(root, "20260805T2352Z__yellow-buoy", created="2026-08-05T23:52:00Z")
    write_study(root, "20260807T1720Z__yellow-buoy", created="2026-08-07T17:20:00Z")

    code = main(study_argv(root, cache_dir, mpa_archive, study="yellow-buoy"))

    err = capsys.readouterr().err
    assert code == 2
    assert "matches 2 studies" in err
    # Not a silent pick of the newest.
    assert "20260805T2352Z__yellow-buoy" in err
    assert "20260807T1720Z__yellow-buoy" in err


# --------------------------------------------------------------------------
# the record left in the study
# --------------------------------------------------------------------------


def test_a_producer_entry_is_appended(studies_root, cache_dir, mpa_archive):
    main(study_argv(studies_root, cache_dir, mpa_archive))
    entry = our_producer(studies_root)

    assert entry["dir"] == "marine-bios"
    assert entry["tool_version"].startswith("marine-bios-extract ")
    assert entry["status"] == "ok"
    assert entry["created_utc"].endswith("Z")
    assert entry["bbox_wsen"] == read_manifest(studies_root)["request"]["bbox"]
    assert entry["pad_km"]["north_km"] == 5.0
    assert entry["datasets"] == ["mpa"]
    assert entry["products"] == {
        "mpa.geojson": "ok",
        "mpa.csv": "ok",
        "mpa.gpkg": "ok",
    }
    assert entry["stations_used"] == ["46254", "LJAC1", "LJPC1", "autoss"]
    assert entry["stations_skipped"] == [
        {"name": "yellow_buoy", "role": "subject", "reason": "no lon/lat in study.json"}
    ]


def test_nothing_else_in_the_study_metadata_is_modified(
    studies_root, cache_dir, mpa_archive
):
    meta_path = studies_root / "20260807T1913Z__session" / "study.json"
    before = json.loads(meta_path.read_text(encoding="utf-8"))
    original = meta_path.read_text(encoding="utf-8")

    main(study_argv(studies_root, cache_dir, mpa_archive))

    after = json.loads(meta_path.read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "producers"} == {
        k: v for k, v in before.items() if k != "producers"
    }
    assert list(after) == list(before)
    # The other tool's entry is untouched, and the file is otherwise byte
    # identical once ours is taken back out again.
    assert after["producers"][0] == before["producers"][0]
    restored = dict(after, producers=before["producers"])
    assert json.dumps(restored, indent=2) + "\n" == original


def test_a_second_run_replaces_our_entry_rather_than_stacking_them(
    studies_root, cache_dir, mpa_archive
):
    main(study_argv(studies_root, cache_dir, mpa_archive))
    main(study_argv(studies_root, cache_dir, mpa_archive, "--pad-km", "7"))

    meta = json.loads(
        (studies_root / "20260807T1913Z__session" / "study.json").read_text()
    )
    ours = [p for p in meta["producers"] if p["name"] == "marine-bios"]
    assert len(ours) == 1
    assert ours[0]["pad_km"]["north_km"] == 7.0


# --------------------------------------------------------------------------
# a layer with nothing in the box
# --------------------------------------------------------------------------


def test_a_dataset_that_clips_to_nothing_is_still_written(
    tmp_path, cache_dir, mpa_archive, capsys
):
    """"No marine protected areas within 5 km" is a result, not a missing file."""
    root = tmp_path / "studies"
    write_study(
        root,
        "20260807T0000Z__elsewhere",
        stations={"buoy": {"lon": -119.500, "lat": 34.400, "role": "subject"}},
    )

    code = main(study_argv(root, cache_dir, mpa_archive, study="elsewhere"))

    assert code == 0
    printed = capsys.readouterr().out
    assert "NOTHING IN THE BOX" in printed

    produced = root / "20260807T0000Z__elsewhere" / "marine-bios"
    assert (produced / "mpa.geojson").is_file()
    assert json.loads((produced / "mpa.geojson").read_text())["features"] == []

    doc = json.loads((produced / "manifest.json").read_text())
    assert doc["layers"][0]["clip"]["kept"] == 0
    assert any("0 features in the box" in w for w in doc["warnings"])


# --------------------------------------------------------------------------
# not writing
# --------------------------------------------------------------------------


def test_a_dry_run_shows_the_box_and_writes_nothing(
    studies_root, cache_dir, mpa_archive, capsys
):
    code = main(study_argv(studies_root, cache_dir, mpa_archive, "--dry-run"))

    assert code == 0
    printed = capsys.readouterr().out
    assert "Box:" in printed
    assert "Dry run" in printed
    assert not out_dir(studies_root).exists()
    meta = json.loads(
        (studies_root / "20260807T1913Z__session" / "study.json").read_text()
    )
    assert [p["name"] for p in meta["producers"]] == ["station-data"]


def test_without_a_terminal_it_fails_clearly_instead_of_prompting(
    studies_root, cache_dir, mpa_archive, capsys, monkeypatch
):
    """capsys already gives us non-tty streams; make that explicit and assert."""
    monkeypatch.setattr(studyrun, "interactive", lambda: False)
    argv = [a for a in study_argv(studies_root, cache_dir, mpa_archive) if a != "--yes"]

    code = main(argv)

    assert code == 2
    err = capsys.readouterr().err
    assert "no terminal to confirm it on" in err
    assert "--yes" in err
    assert not out_dir(studies_root).exists()


def test_both_streams_must_be_a_terminal(monkeypatch):
    """An interactive stdin with a redirected stdout must not count as a terminal.

    Drawing there sprays control codes into a file, and it is a real
    configuration: `bios study | tee run.log`.
    """

    class Fake:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    monkeypatch.setattr(studyrun.sys, "stdin", Fake(True))
    monkeypatch.setattr(studyrun.sys, "stdout", Fake(False))
    assert studyrun.interactive() is False

    monkeypatch.setattr(studyrun.sys, "stdout", Fake(True))
    assert studyrun.interactive() is True


def test_padding_is_required_rather_than_defaulted(
    studies_root, cache_dir, mpa_archive, capsys
):
    argv = [a for a in study_argv(studies_root, cache_dir, mpa_archive) if a != "5"]
    argv.remove("--pad-km")

    with pytest.raises(SystemExit) as exc:
        main(argv)

    assert "no padding given" in str(exc.value)


# --------------------------------------------------------------------------
# re-running over an existing directory
# --------------------------------------------------------------------------


def test_pre_existing_files_are_reported_and_left_alone(
    studies_root, cache_dir, mpa_archive, capsys
):
    produced = out_dir(studies_root)
    produced.mkdir(parents=True)
    (produced / "shoreline.geojson").write_text("{}", encoding="utf-8")

    main(study_argv(studies_root, cache_dir, mpa_archive))

    printed = capsys.readouterr().out
    assert "shoreline.geojson" in printed
    assert "--force" in printed
    assert (produced / "shoreline.geojson").exists()


def test_force_removes_them_after_listing_them(studies_root, cache_dir, mpa_archive, capsys):
    produced = out_dir(studies_root)
    produced.mkdir(parents=True)
    (produced / "shoreline.geojson").write_text("{}", encoding="utf-8")

    main(study_argv(studies_root, cache_dir, mpa_archive, "--force"))

    printed = capsys.readouterr().out
    assert "removing shoreline.geojson" in printed
    assert not (produced / "shoreline.geojson").exists()


# --------------------------------------------------------------------------
# rasters, and more than one layer at a time
# --------------------------------------------------------------------------


def test_a_raster_and_a_vector_share_one_box(studies_root, cache_dir, mpa_archive, tmp_path):
    kelp = make_raster_archive(tmp_path / "archives")

    code = main(
        [
            "study",
            "--studies-root",
            str(studies_root),
            "--study",
            "latest",
            "--pad-km",
            "5",
            "--datasets",
            "mpa",
            "kelp-persistence",
            "--local-archive",
            f"mpa={mpa_archive}",
            "--local-archive",
            f"kelp-persistence={kelp}",
            "--cache-dir",
            str(cache_dir),
            "--yes",
        ]
    )

    assert code == 0
    produced = out_dir(studies_root)
    assert (produced / "kelp-persistence.tif").is_file()
    assert (produced / "mpa.geojson").is_file()

    doc = read_manifest(studies_root)
    assert {layer["key"] for layer in doc["layers"]} == {"mpa", "kelp-persistence"}
    # One box for the whole run: every file describes the same rectangle.
    for layer in doc["layers"]:
        assert layer["citation"]["accessed"]
    entry = our_producer(studies_root)
    assert entry["products"]["kelp-persistence.tif"] == "ok"


# --------------------------------------------------------------------------
# the two extension points
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_seams():
    """Seams are module state; a test that registers must not leak into others."""
    box_before = list(studyrun.BOX_SEAM)
    plan_before = list(studyrun.PLAN_SEAM)
    yield
    studyrun.BOX_SEAM[:] = box_before
    studyrun.PLAN_SEAM[:] = plan_before


def test_no_stage_is_registered_by_default():
    assert studyrun.BOX_SEAM == []
    assert studyrun.PLAN_SEAM == []


def test_a_box_stage_can_move_the_box_and_is_reported(
    studies_root, cache_dir, mpa_archive
):
    """The seam cluster expansion will use: box derived -> box final."""
    from biosextract.bbox import BBox

    def widen(state):
        before = state.box
        state.box = BBox(
            before.west - 0.01, before.south, before.east + 0.01, before.north
        )
        return state, {"applied": True, "grew_km": 2.0}

    studyrun.register_box_stage("widen", widen)
    main(study_argv(studies_root, cache_dir, mpa_archive))

    doc = read_manifest(studies_root)
    derived = doc["request"]["stages"]["box"]["box_wsen"]
    final = doc["request"]["bbox"]
    assert final[0] < derived[0] and final[2] > derived[2]
    assert doc["request"]["stages"]["widen"] == {"applied": True, "grew_km": 2.0}
    # Everything downstream describes the box the stage settled on.
    geojson = json.loads((out_dir(studies_root) / "mpa.geojson").read_text())
    assert geojson["clippedToBbox"] == final
    assert our_producer(studies_root)["bbox_wsen"] == final


def test_a_plan_stage_can_drop_a_dataset_and_is_reported(
    studies_root, cache_dir, mpa_archive
):
    """The seam the re-run policy will use: plan -> execute."""

    def skip_everything(state):
        for entry in state.plan:
            entry["skipped"] = "already present for this box"
        return state, {"skipped": [e["key"] for e in state.plan]}

    studyrun.register_plan_stage("rerun-policy", skip_everything)
    code = main(study_argv(studies_root, cache_dir, mpa_archive))

    assert code == 1  # nothing was extracted
    assert not (out_dir(studies_root) / "mpa.geojson").exists()


def test_seam_reports_reach_the_study_metadata_but_built_in_ones_do_not(
    studies_root, cache_dir, mpa_archive
):
    def note(state):
        return state, {"applied": True, "note": "for the record"}

    studyrun.register_box_stage("expansion", note)
    main(study_argv(studies_root, cache_dir, mpa_archive))

    entry = our_producer(studies_root)
    assert entry["stages"] == {"expansion": {"applied": True, "note": "for the record"}}
    # The manifest keeps the full transcript; study.json keeps the decisions.
    assert "box" in read_manifest(studies_root)["request"]["stages"]


def test_registering_the_same_name_twice_replaces_it():
    studyrun.register_box_stage("x", lambda s: (s, {}))
    studyrun.register_box_stage("x", lambda s: (s, {"second": True}))

    assert len(studyrun.BOX_SEAM) == 1
    assert studyrun.BOX_SEAM[0][1](None)[1] == {"second": True}


def test_shapefile_sidecars_are_not_mistaken_for_leftovers(
    studies_root, cache_dir, mpa_archive, capsys
):
    """A shapefile is not a file, and --force must not dismantle one."""
    main(
        study_argv(
            studies_root, cache_dir, mpa_archive, "--force", "--formats", "shp"
        )
    )

    printed = capsys.readouterr().out
    produced = out_dir(studies_root)
    assert "removing" not in printed
    for ext in (".shp", ".shx", ".dbf", ".prj"):
        assert (produced / f"mpa{ext}").is_file(), ext


def test_a_run_that_extracts_nothing_leaves_no_empty_directory(
    studies_root, cache_dir, mpa_archive
):
    """An empty marine-bios/ would claim this toolkit produced something."""

    def skip_everything(state):
        for entry in state.plan:
            entry["skipped"] = "nothing to do"
        return state, {}

    studyrun.register_plan_stage("skip-all", skip_everything)

    assert main(study_argv(studies_root, cache_dir, mpa_archive)) == 1
    assert not out_dir(studies_root).exists()
