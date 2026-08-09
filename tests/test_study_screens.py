"""Where the screens plug into ``bios study``.

The screens themselves are tested in ``test_picker.py`` against scripted
keystrokes. What is tested here is the wiring: which flag skips which screen,
what happens with no terminal, and that an answer given on screen reaches the
run in the same shape a flag would have.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from biosextract import catalog, cli, picker, studies, studyrun
from tests.test_studies import REFERENCE_STATIONS, write_study


@pytest.fixture
def studies_root(tmp_path):
    root = tmp_path / "studies"
    write_study(root, "20260805T2352Z__older", created="2026-08-05T23:52:00Z",
                stations={"autoss": {"lon": -117.257, "lat": 32.867, "role": "primary"}})
    write_study(root, "20260807T1913Z__session", created="2026-08-07T19:13:38Z",
                stations=REFERENCE_STATIONS)
    return root


def _args(study=None, datasets=None, timeout=60):
    return Namespace(study=study, datasets=datasets, timeout=timeout)


@pytest.fixture
def no_screens(monkeypatch):
    """Every screen becomes an error, so a test can assert none was drawn."""

    def refuse(*a, **kw):
        raise AssertionError("a screen was drawn when a flag had already answered it")

    monkeypatch.setattr(picker, "pick_study", refuse)
    monkeypatch.setattr(picker, "pick_datasets", refuse)


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(picker, "can_pick", lambda *a, **kw: True)


@pytest.fixture
def no_terminal(monkeypatch):
    monkeypatch.setattr(picker, "can_pick", lambda *a, **kw: False)


# --------------------------------------------------------------------------
# a flag skips its screen
# --------------------------------------------------------------------------


def test_both_flags_draw_nothing(studies_root, tmp_path, terminal, no_screens):
    got = cli._ask(_args(study="latest", datasets=["mpa"]), studies_root, tmp_path, {})
    assert got == ("latest", ["mpa"], {})


def test_datasets_with_no_keys_still_means_every_wired_up_layer(
    studies_root, tmp_path, terminal, no_screens
):
    """`--datasets` with nothing after it is an answer, not a missing answer."""
    study, keys, _ = cli._ask(_args(study="latest", datasets=[]), studies_root, tmp_path, {})
    assert keys == catalog.resolve_keys(None)


def test_only_the_missing_answer_is_asked_for(studies_root, tmp_path, terminal, monkeypatch):
    monkeypatch.setattr(
        picker, "pick_study", lambda *a, **kw: pytest.fail("--study was supplied")
    )
    monkeypatch.setattr(picker, "pick_datasets", lambda *a, **kw: picker.Choice.picked(["mpa"]))
    monkeypatch.setattr(picker, "report_sizes", lambda *a, **kw: {})
    study, keys, _ = cli._ask(_args(study="latest"), studies_root, tmp_path, {})
    assert (study, keys) == ("latest", ["mpa"])


# --------------------------------------------------------------------------
# no terminal: nothing is drawn, and #3's invocations keep working
# --------------------------------------------------------------------------


def test_without_a_terminal_a_missing_study_names_the_flag(
    studies_root, tmp_path, no_terminal, no_screens
):
    with pytest.raises(SystemExit, match="--study"):
        cli._ask(_args(datasets=["mpa"]), studies_root, tmp_path, {})


def test_without_a_terminal_missing_datasets_keep_the_headless_default(
    studies_root, tmp_path, no_terminal, no_screens
):
    study, keys, preresolved = cli._ask(_args(study="latest"), studies_root, tmp_path, {})
    assert (study, keys, preresolved) == ("latest", catalog.resolve_keys(None), {})


# --------------------------------------------------------------------------
# the screens, in sequence
# --------------------------------------------------------------------------


class _Source:
    bytes = 1_000_000
    last_modified = "Thu, 30 May 2024 00:00:00 GMT"


@pytest.fixture
def screens(monkeypatch):
    """Scripted screen outcomes, plus a record of what was drawn."""

    drawn: list[str] = []
    resolves: list[str] = []

    def install(study_choices, dataset_choices):
        study_it, dataset_it = iter(study_choices), iter(dataset_choices)

        def pick_study(found, root="", **kw):
            drawn.append("study")
            return next(study_it)

        def pick_datasets(registry, local_archives=(), **kw):
            drawn.append("datasets")
            return next(dataset_it)

        monkeypatch.setattr(picker, "pick_study", pick_study)
        monkeypatch.setattr(picker, "pick_datasets", pick_datasets)
        monkeypatch.setattr(
            catalog, "resolve", lambda d, timeout=60: resolves.append(d.key) or _Source()
        )

    install.drawn = drawn
    install.resolves = resolves
    return install


def _study(label):
    return studies.Study(path=label, study_id=f"2026__{label}", label=label)


def test_a_study_chosen_on_screen_reaches_the_run_as_its_id(
    studies_root, tmp_path, terminal, screens
):
    screens([picker.Choice.picked(_study("session"))], [picker.Choice.picked(["mpa"])])
    study, keys, preresolved = cli._ask(_args(), studies_root, tmp_path, {})
    assert study == "2026__session"
    assert keys == ["mpa"]
    assert set(preresolved) == {"mpa"}, "the sizes it resolved are handed to the run"


def test_escape_on_the_dataset_screen_steps_back_into_the_study_screen(
    studies_root, tmp_path, terminal, screens
):
    screens(
        [picker.Choice.picked(_study("first")), picker.Choice.picked(_study("second"))],
        [picker.Choice.back(), picker.Choice.picked(["mpa"])],
    )
    study, _, _ = cli._ask(_args(), studies_root, tmp_path, {})
    assert screens.drawn == ["study", "datasets", "study", "datasets"]
    assert study == "2026__second"


def test_the_sizes_are_resolved_once_and_handed_on(
    studies_root, tmp_path, terminal, screens
):
    screens([picker.Choice.picked(_study("a"))], [picker.Choice.picked(["mpa"])])
    _, _, preresolved = cli._ask(_args(), studies_root, tmp_path, {})
    assert screens.resolves == ["mpa"], "one listing and one HEAD per dataset"
    assert set(preresolved) == {"mpa"}


def test_a_layer_supplied_from_disk_is_never_resolved_for_its_size(
    studies_root, tmp_path, terminal, screens
):
    screens([picker.Choice.picked(_study("a"))], [picker.Choice.picked(["mpa"])])
    _, _, preresolved = cli._ask(
        _args(), studies_root, tmp_path, {"mpa": tmp_path / "mpa.zip"}
    )
    assert screens.resolves == []
    assert preresolved == {}


def test_abandoning_a_screen_abandons_the_run(studies_root, tmp_path, terminal, screens):
    screens([picker.Choice.abandoned()], [])
    assert cli._ask(_args(), studies_root, tmp_path, {}) is None


def test_escape_on_the_first_screen_exits(studies_root, tmp_path, terminal, screens):
    screens([picker.Choice.back()], [])
    assert cli._ask(_args(), studies_root, tmp_path, {}) is None


def test_a_studies_directory_that_is_not_there_is_an_error_not_a_screen(
    tmp_path, terminal, no_screens
):
    with pytest.raises(studies.StudyError, match="no studies directory"):
        cli._ask(_args(), tmp_path / "absent", tmp_path, {})


# --------------------------------------------------------------------------
# what the run does with an answer the picker already resolved
# --------------------------------------------------------------------------


def test_the_run_does_not_resolve_what_the_picker_resolved(monkeypatch, tmp_path):
    request = studyrun.Request(
        studies_root=tmp_path,
        study="latest",
        datasets=["mpa", "shoreline"],
        padding=studyrun.Padding(1, 1, 1, 1),
        formats=["geojson"],
        preresolved={"mpa": _Source()},
    )
    monkeypatch.setattr(
        catalog, "resolve", lambda d, timeout=60: _Source() if d.key == "shoreline"
        else pytest.fail("mpa was resolved twice")
    )

    state, report = studyrun.stage_resolve_sources(studyrun.RunState(request=request))
    assert report["reused"] == ["mpa"]
    assert sorted(state.sources) == ["mpa", "shoreline"]


def test_a_preresolved_dataset_that_was_not_selected_is_ignored(tmp_path, monkeypatch):
    """Choosing fewer layers on a second pass must not smuggle one back in."""
    request = studyrun.Request(
        studies_root=tmp_path,
        study="latest",
        datasets=["mpa"],
        padding=studyrun.Padding(1, 1, 1, 1),
        formats=["geojson"],
        preresolved={"mpa": _Source(), "shoreline": _Source()},
    )
    monkeypatch.setattr(
        catalog, "resolve", lambda d, timeout=60: pytest.fail("nothing left to resolve")
    )
    state, report = studyrun.stage_resolve_sources(studyrun.RunState(request=request))
    assert sorted(state.sources) == ["mpa"]
    assert report["resolved"] == ["mpa"]
