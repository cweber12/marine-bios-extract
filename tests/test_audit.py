"""The citation audit: what is verified, what is not, and what could not be checked.

Every assertion here runs against a *fixture* registry, never the real one. The
audit's job is to report the live registry accurately, not to be satisfied by
it - and a test that asserted the real registry was fully verified would put the
suite in direct conflict with the rule against committing red, for as long as
any layer remained unverified. The losing rule would be that one.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from biosextract import catalog, citation as citation_mod
from biosextract.citation import UNKNOWN
from biosextract.cli import main
from tests.fixtures import make_archive, make_bare_archive


@pytest.fixture
def registry():
    """Two datasets: one with a licence recorded, one with nothing."""
    return {
        "verified": replace(
            catalog.get("mpa"),
            key="verified",
            license="CC-BY 4.0 (Creative Commons Attribution) - attribution required",
        ),
        "unverified": replace(catalog.get("shoreline"), key="unverified", license=""),
    }


def cache_with(tmp_path, **archives):
    """A cache directory laid out the way fetch() lays it out."""
    root = tmp_path / "cache"
    for key, archive in archives.items():
        target = root / key
        target.mkdir(parents=True, exist_ok=True)
        (target / archive.name).write_bytes(archive.read_bytes())
    root.mkdir(parents=True, exist_ok=True)
    return root


def by_key(rows):
    return {r.key: r for r in rows}


def test_reports_one_of_each(registry, tmp_path):
    rows = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))

    assert rows["verified"].clear is True
    assert rows["unverified"].clear is False
    assert "no licence recorded" in rows["unverified"].problems[0]


def test_names_the_reason_rather_than_only_the_verdict(registry, tmp_path):
    rows = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))

    assert rows["unverified"].license == UNKNOWN
    assert any("licence" in p for p in rows["unverified"].problems)


def test_an_absent_archive_is_a_note_not_a_problem(registry, tmp_path):
    """The answer to "never downloaded" is a fetch, not a verification.

    Counting it as work outstanding would mean the audit could never pass on a
    machine with a cold cache, which is most machines.
    """
    rows = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))
    row = rows["verified"]

    assert row.clear is True
    assert row.notes and "no cached archive" in row.notes[0]
    assert not any("cached" in p for p in row.problems)


def test_reads_a_cached_archive_when_there_is_one(registry, tmp_path):
    archive = make_archive(tmp_path / "src", "ds582")
    cache = cache_with(tmp_path, verified=archive)

    row = by_key(citation_mod.audit(registry, cache))["verified"]

    assert row.archive == "ds582.zip"
    assert row.originator == "California Department of Fish and Wildlife"
    assert row.metadata_source.endswith("metadata.xml")
    assert row.clear is True


def test_an_archive_with_no_metadata_document_says_so(registry, tmp_path):
    """Three of the four real cached archives are like this: data, nothing else.

    Distinct from an archive shipping an empty document, which parses and yields
    nothing - here there is nothing to parse, and no dialect will ever change
    that, so the audit has to point at a person rather than at the bytes.
    """
    archive = make_bare_archive(tmp_path / "src", "ds3115")
    cache = cache_with(tmp_path, verified=archive)

    row = by_key(citation_mod.audit(registry, cache))["verified"]

    assert any("no metadata document" in n for n in row.notes)
    assert any("citation incomplete" in p for p in row.problems)
    assert "originator" in row.problems[0] and "publication date" in row.problems[0]


def test_an_empty_metadata_document_is_not_the_same_as_none(registry, tmp_path):
    """A document that says nothing was still read; the audit should not claim
    the archive carries none, because the next question differs: chase the
    publisher for a better document, or accept there was never one."""
    archive = make_archive(tmp_path / "src2", "ds3115", metadata=None)
    cache = cache_with(tmp_path, verified=archive)

    row = by_key(citation_mod.audit(registry, cache))["verified"]

    assert row.metadata_source, "a document was read, however unhelpful"
    assert not any("no metadata document" in n for n in row.notes)
    assert any("citation incomplete" in p for p in row.problems)


def test_the_audit_makes_no_request(registry, tmp_path, monkeypatch):
    def no_requests(*a, **kw):  # pragma: no cover - only runs on a regression
        raise AssertionError("the audit must not reach a publisher")

    monkeypatch.setattr(catalog, "_open", no_requests)
    citation_mod.audit(registry, tmp_path / "empty-cache")


def test_the_verdict_covers_wired_up_datasets_only(registry, tmp_path):
    """A gated source is not expected to be citable yet.

    An alarm that is always ringing for a reason nobody can act on is one
    nobody hears.
    """
    registry["gated"] = replace(
        catalog.get("cmecs-substrate"), key="gated", license=""
    )
    rows = citation_mod.audit(registry, tmp_path / "empty-cache")

    outstanding = [r.key for r in citation_mod.unverified(rows)]
    assert outstanding == ["unverified"], "the gated dataset must not be counted"
    assert not by_key(rows)["gated"].clear, "...but it is still reported"


def test_rows_are_ordered_so_two_runs_read_the_same(registry, tmp_path):
    rows = citation_mod.audit(registry, tmp_path / "empty-cache")
    assert [r.key for r in rows] == sorted(r.key for r in rows)


def test_a_row_serialises_for_the_manifest(registry, tmp_path):
    row = citation_mod.audit(registry, tmp_path / "empty-cache")[0]
    d = row.as_dict()
    assert set(("key", "license", "problems", "notes", "clear")) <= set(d)
    assert isinstance(d["problems"], list)


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


def argv(tmp_path, *extra, datasets=("benthic-substrate",)):
    return ["citations", "--cache-dir", str(tmp_path / "empty-cache"), *datasets, *extra]


def test_the_command_reports_without_check_and_succeeds(tmp_path, capsys):
    """Without --check it is a report, and a report that fails your shell is a
    nuisance."""
    code = main(argv(tmp_path, datasets=("shoreline",)))

    printed = capsys.readouterr().out
    assert code == 0
    assert "shoreline" in printed
    assert "TODO" in printed, "it still says what is outstanding"


def test_check_exits_non_zero_when_a_layer_is_unverified(tmp_path, capsys):
    code = main(argv(tmp_path, "--check", datasets=("shoreline",)))

    assert code == 1
    assert "Outstanding: shoreline" in capsys.readouterr().out


def test_check_exits_zero_when_nothing_is_outstanding(tmp_path, capsys):
    """benthic-substrate is the one layer verified so far; when the rest catch
    up, this is the shape of a passing gate."""
    code = main(argv(tmp_path, "--check", datasets=("benthic-substrate",)))

    assert code == 0
    assert "Outstanding" not in capsys.readouterr().out


def test_the_command_says_it_could_not_read_an_archive_it_does_not_have(
    tmp_path, capsys
):
    main(argv(tmp_path, datasets=("shoreline",)))
    assert "no cached archive" in capsys.readouterr().out


def test_the_command_never_reaches_a_publisher(tmp_path, monkeypatch):
    def no_requests(*a, **kw):  # pragma: no cover - only runs on a regression
        raise AssertionError("bios citations must not reach a publisher")

    monkeypatch.setattr(catalog, "_open", no_requests)
    assert main(argv(tmp_path, datasets=("shoreline", "mpa"))) == 0


def test_all_includes_the_datasets_a_default_run_would_skip(tmp_path, capsys):
    main(argv(tmp_path, "--all", datasets=()))
    printed = capsys.readouterr().out

    assert "state-waters" in printed, "unverified datasets are shown with --all"
    assert "cmecs-substrate" in printed, "so are gated ones"
    assert "[unverified]" in printed and "[manual]" in printed


def test_a_gated_dataset_is_shown_but_does_not_fail_the_gate(tmp_path, capsys):
    code = main(argv(tmp_path, "--all", "--check", datasets=()))
    printed = capsys.readouterr().out

    assert "cmecs-substrate" in printed
    outstanding = printed.split("Outstanding: ")[1].splitlines()[0]
    assert "cmecs-substrate" not in outstanding
    assert code == 1, "...the wired-up layers still fail it"
