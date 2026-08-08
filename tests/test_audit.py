"""The citation audit: what is verified, what is not, and what could not be checked.

Every assertion here runs against the synthetic registry in `tests/registry.py`,
never the real one. Two separate reasons, and both have already bitten.

The audit's job is to report the live registry accurately, not to be satisfied
by it - a test that asserted the real registry was fully verified would put the
suite in direct conflict with the rule against committing red, for as long as
any layer remained unverified. The losing rule would be that one.

And a real layer's licence is somebody's outstanding work, not a fixture. Three
tests in this module used `mpa`, `shoreline` and `benthic-substrate` as stand-ins
for "the untraceable one", "the unverified one" and "the verified one", and each
went red the moment that work got done. The shapes they wanted are declared
once, under names no real registry has, and the layer behind them can now change
freely.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from biosextract import catalog, citation as citation_mod
from biosextract.citation import UNKNOWN
from biosextract.cli import main
from tests.fixtures import make_archive, make_bare_archive
from tests.registry import (
    DEMOTED,
    GATED,
    PINNED,
    UNTRACEABLE,
    UNVERIFIED,
    VERIFIED,
    VERIFIED_FROM,
    VERIFIED_ON,
    install_synthetic_registry,  # noqa: F401 - autouse; the CLI reads the global
    synthetic_registry,  # noqa: F401 - requested by the fixtures below
)


@pytest.fixture
def registry(synthetic_registry):  # noqa: F811 - the fixture, not the import
    """Two shapes: one licence a reader can trace, one absent.

    "Verified" means the licence *and* where someone read it. A licence with no
    provenance is a third state, and it has its own test below rather than
    hiding in this fixture.
    """
    return {key: synthetic_registry[key] for key in (VERIFIED, UNVERIFIED)}


def test_a_licence_with_no_provenance_is_neither_verified_nor_absent(
    registry, synthetic_registry, tmp_path  # noqa: F811
):
    """The state mpa is actually in: a claim that reads as settled.

    Nobody re-checks it, because nothing about it looks unfinished - which makes
    it indistinguishable from a guess someone made years ago. Asserted against a
    layer this test controls, so that finishing mpa's provenance for real is not
    a change that has to come here as well.
    """
    registry[UNTRACEABLE] = synthetic_registry[UNTRACEABLE]
    row = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))[UNTRACEABLE]

    assert row.license != UNKNOWN, "the licence is recorded..."
    assert not row.clear, "...and still outstanding"
    assert any("no provenance" in p for p in row.problems)


def cache_with(tmp_path, archives):
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
    """A traced licence raises no problem; an absent one does.

    Neither row is `clear` here, because a cold cache leaves the traced layer's
    originator unknown - that is `test_a_cold_cache_leaves_a_citation_undetermined`
    below. What this test is about is the problems.
    """
    rows = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))

    assert not rows[VERIFIED].problems
    assert rows[UNVERIFIED].problems
    assert "no licence recorded" in rows[UNVERIFIED].problems[0]


def test_names_the_reason_rather_than_only_the_verdict(registry, tmp_path):
    rows = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))

    assert rows[UNVERIFIED].license == UNKNOWN
    assert any("licence" in p for p in rows[UNVERIFIED].problems)


def test_an_absent_archive_is_a_note_not_a_problem(registry, tmp_path):
    """The answer to "never downloaded" is a fetch, not a verification.

    Counting it as work outstanding would mean the audit could never pass on a
    machine with a cold cache, which is most machines. That property is the one
    #17 bought and #25 had to preserve while fixing the verdict.
    """
    rows = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))
    row = rows[VERIFIED]

    assert not row.problems, "an absent archive is nobody's outstanding work"
    assert row.notes and "no cached archive" in row.notes[0]
    assert not any("cached" in p for p in row.problems)


def test_a_cold_cache_leaves_a_citation_undetermined_rather_than_clear(
    registry, tmp_path
):
    """#25: `ok` for a citation nobody could check is a false clean bill.

    The row printed an `[unknown]` originator and was reported as verified in
    the same block. Nothing is outstanding - there is no work to do here - but
    "could not be established" is its own state, not the absence of a problem.
    """
    row = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))[VERIFIED]

    assert row.verdict == citation_mod.UNDETERMINED
    assert row.clear is False, "it must not read as verified"
    assert not row.problems, "...and must not read as work outstanding either"
    assert row.originator == UNKNOWN
    assert any("could not be checked" in u for u in row.unchecked)


def test_pinned_facts_survive_a_cold_cache_without_going_undetermined(
    registry, synthetic_registry, tmp_path  # noqa: F811
):
    """The blast-radius guard, and the reason the third state is not simply
    "no cached archive".

    Three real layers had their originator and date pinned by #18, so the
    archive has nothing left to contribute and its absence costs nothing.
    Keying the new state on the missing archive alone would flip all three to
    undetermined - which is how a new verdict gets read as noise and ignored,
    taking the one row that meant something with it.
    """
    registry[PINNED] = synthetic_registry[PINNED]

    row = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))[PINNED]

    assert row.archive == "", "no archive was read..."
    assert row.verdict == citation_mod.CLEAR, "...and none was needed"
    assert not row.unchecked


def test_outstanding_work_outranks_a_question_nobody_could_answer(
    registry, tmp_path
):
    """A layer can be both, and then it is outstanding.

    Reporting a known-missing licence as merely unknowable would be the same
    conflation #25 is about, pointing the other way: it would move real work
    into the bucket the gate deliberately ignores.
    """
    row = by_key(citation_mod.audit(registry, tmp_path / "empty-cache"))[UNVERIFIED]

    assert row.unchecked, "the cold cache left a question open here too"
    assert row.problems, "but the absent licence is work regardless"
    assert row.verdict == citation_mod.OUTSTANDING


def test_reads_a_cached_archive_when_there_is_one(registry, tmp_path):
    archive = make_archive(tmp_path / "src", "ds9001")
    cache = cache_with(tmp_path, {VERIFIED: archive})

    row = by_key(citation_mod.audit(registry, cache))[VERIFIED]

    assert row.archive == "ds9001.zip"
    assert row.originator == "California Department of Fish and Wildlife"
    assert row.metadata_source.endswith("metadata.xml")
    assert row.clear is True


def test_an_archive_with_no_metadata_document_says_so(registry, tmp_path):
    """Three of the four real cached archives are like this: data, nothing else.

    Distinct from an archive shipping an empty document, which parses and yields
    nothing - here there is nothing to parse, and no dialect will ever change
    that, so the audit has to point at a person rather than at the bytes.
    """
    archive = make_bare_archive(tmp_path / "src", "ds9001")
    cache = cache_with(tmp_path, {VERIFIED: archive})

    row = by_key(citation_mod.audit(registry, cache))[VERIFIED]

    assert any("no metadata document" in n for n in row.notes)
    assert any("citation incomplete" in p for p in row.problems)
    assert "originator" in row.problems[0] and "publication date" in row.problems[0]


def test_a_pin_answers_an_archive_that_carries_nothing(
    registry, synthetic_registry, tmp_path  # noqa: F811
):
    """The shape most BIOS archives are in: data, and a fact a person read.

    Without the pin the row above is incomplete however carefully anyone reads
    the publisher's page, so this is the difference the pins were added to make.
    """
    registry[PINNED] = synthetic_registry[PINNED]
    cache = cache_with(tmp_path, {PINNED: make_bare_archive(tmp_path / "src", "ds9004")})

    row = by_key(citation_mod.audit(registry, cache))[PINNED]

    assert row.originator == "Somebody At The Publisher"
    assert row.publication_date == "2023, Mar. 8"
    assert not any("citation incomplete" in p for p in row.problems)
    assert row.clear is True


def test_an_empty_metadata_document_is_not_the_same_as_none(registry, tmp_path):
    """A document that says nothing was still read; the audit should not claim
    the archive carries none, because the next question differs: chase the
    publisher for a better document, or accept there was never one."""
    archive = make_archive(tmp_path / "src2", "ds9001", metadata=None)
    cache = cache_with(tmp_path, {VERIFIED: archive})

    row = by_key(citation_mod.audit(registry, cache))[VERIFIED]

    assert row.metadata_source, "a document was read, however unhelpful"
    assert not any("no metadata document" in n for n in row.notes)
    assert any("citation incomplete" in p for p in row.problems)


def test_the_audit_makes_no_request(registry, tmp_path, monkeypatch):
    def no_requests(*a, **kw):  # pragma: no cover - only runs on a regression
        raise AssertionError("the audit must not reach a publisher")

    monkeypatch.setattr(catalog, "_open", no_requests)
    citation_mod.audit(registry, tmp_path / "empty-cache")


def test_the_verdict_covers_wired_up_datasets_only(
    registry, synthetic_registry, tmp_path  # noqa: F811
):
    """A gated source is not expected to be citable yet.

    An alarm that is always ringing for a reason nobody can act on is one
    nobody hears.
    """
    registry[GATED] = replace(synthetic_registry[GATED], license="")
    rows = citation_mod.audit(registry, tmp_path / "empty-cache")

    outstanding = [r.key for r in citation_mod.unverified(rows)]
    assert outstanding == [UNVERIFIED], "the gated dataset must not be counted"
    assert not by_key(rows)[GATED].clear, "...but it is still reported"


def test_rows_are_ordered_so_two_runs_read_the_same(registry, tmp_path):
    rows = citation_mod.audit(registry, tmp_path / "empty-cache")
    assert [r.key for r in rows] == sorted(r.key for r in rows)


def test_the_two_selectors_do_not_overlap(registry, synthetic_registry, tmp_path):  # noqa: F811
    """Whatever a caller does with each pile, no row is ever in both.

    The summary line adds these counts up in front of a reader, so a row
    landing in both would report more layers than the registry holds.
    """
    registry[PINNED] = synthetic_registry[PINNED]
    rows = citation_mod.audit(registry, tmp_path / "empty-cache")

    outstanding = {r.key for r in citation_mod.unverified(rows)}
    unknown = {r.key for r in citation_mod.undetermined(rows)}

    assert outstanding == {UNVERIFIED}
    assert unknown == {VERIFIED}, "the traced licence with no pins and no archive"
    assert not outstanding & unknown


def test_an_undetermined_row_is_not_counted_as_work_outstanding(registry, tmp_path):
    """The recorded decision on #25, asserted where it can regress.

    Putting these in `unverified()` would fail `--check` on every cold cache,
    and the only way to turn it green would be downloading an archive - the
    exact cost #17 was built to avoid.
    """
    rows = citation_mod.audit(registry, tmp_path / "empty-cache")

    assert VERIFIED not in {r.key for r in citation_mod.unverified(rows)}


def test_a_row_serialises_for_the_manifest(registry, tmp_path):
    row = citation_mod.audit(registry, tmp_path / "empty-cache")[0]
    d = row.as_dict()
    assert set(("key", "license", "problems", "notes", "unchecked", "verdict", "clear")) <= set(d)
    assert isinstance(d["problems"], list)
    assert d["verdict"] in (citation_mod.CLEAR, citation_mod.UNDETERMINED, citation_mod.OUTSTANDING)


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------
# These run the real CLI, which reads `catalog.DATASETS` directly rather than
# taking a registry the way `audit()` does - it is a CLI, and that is the
# point. So the synthetic datasets are installed on the module by the autouse
# fixture, and named here like any other key.


def argv(tmp_path, *extra, datasets=(VERIFIED,)):
    return ["citations", "--cache-dir", str(tmp_path / "empty-cache"), *datasets, *extra]


def test_the_command_reports_without_check_and_succeeds(tmp_path, capsys):
    """Without --check it is a report, and a report that fails your shell is a
    nuisance."""
    code = main(argv(tmp_path, datasets=(UNVERIFIED,)))

    printed = capsys.readouterr().out
    assert code == 0
    assert UNVERIFIED in printed
    assert "TODO" in printed, "it still says what is outstanding"


def test_check_exits_non_zero_when_a_layer_is_unverified(tmp_path, capsys):
    code = main(argv(tmp_path, "--check", datasets=(UNVERIFIED,)))

    assert code == 1
    assert f"Outstanding: {UNVERIFIED}" in capsys.readouterr().out


def test_check_exits_zero_when_nothing_is_outstanding(tmp_path, capsys):
    """The shape of a passing gate, against a dataset this test controls.

    It used to name whichever real layer was verified at the time - first
    `benthic-substrate`, then `benthic-substrate` again once a provenance rule
    it did not yet meet arrived - and each time correct work turned it red. A
    gate test asserts what a clean row looks like, not that some particular real
    layer is clean today.
    """
    code = main(argv(tmp_path, "--check", datasets=(VERIFIED,)))

    assert code == 0
    assert "Outstanding" not in capsys.readouterr().out


def test_a_licence_nobody_can_trace_is_outstanding_work(tmp_path, capsys):
    """mpa's case: a CC-BY claim recorded before provenance was recorded.

    It reads as settled, so nobody re-checks it, which is indistinguishable from
    a guess made years ago.
    """
    code = main(argv(tmp_path, "--check", datasets=(UNTRACEABLE,)))
    printed = capsys.readouterr().out

    assert code == 1
    assert "no provenance" in printed
    assert f"Outstanding: {UNTRACEABLE}" in printed


def test_a_traced_licence_says_where_it_was_read(tmp_path, capsys):
    main(argv(tmp_path, datasets=(VERIFIED,)))
    printed = capsys.readouterr().out

    assert f"verified:   {VERIFIED_FROM} on {VERIFIED_ON}" in printed
    assert "no provenance" not in printed


def test_the_command_says_it_could_not_read_an_archive_it_does_not_have(
    tmp_path, capsys
):
    """Both places it is said, because either alone survives the other's loss.

    Asserting the bare phrase passed with the note suppressed and passed again
    with the `read from` line gutted - two ways for the command to go quiet
    about a cold cache, and one assertion that noticed neither.
    """
    main(argv(tmp_path, datasets=(VERIFIED,)))
    printed = capsys.readouterr().out

    assert "read from:  no cached archive" in printed
    assert "note:       no cached archive" in printed


def test_the_command_never_reaches_a_publisher(tmp_path, monkeypatch):
    def no_requests(*a, **kw):  # pragma: no cover - only runs on a regression
        raise AssertionError("bios citations must not reach a publisher")

    monkeypatch.setattr(catalog, "_open", no_requests)
    assert main(argv(tmp_path, datasets=(VERIFIED, UNVERIFIED))) == 0


def test_all_includes_the_datasets_a_default_run_would_skip(tmp_path, capsys):
    main(argv(tmp_path, "--all", datasets=()))
    printed = capsys.readouterr().out

    assert DEMOTED in printed, "unverified datasets are shown with --all"
    assert GATED in printed, "so are gated ones"
    assert "[unverified]" in printed and "[manual]" in printed


def test_a_gated_dataset_is_shown_but_does_not_fail_the_gate(tmp_path, capsys):
    code = main(argv(tmp_path, "--all", "--check", datasets=()))
    printed = capsys.readouterr().out

    assert GATED in printed
    outstanding = printed.split("Outstanding: ")[1].splitlines()[0]
    assert GATED not in outstanding
    assert code == 1, "...the wired-up layers still fail it"
    assert UNVERIFIED in outstanding, "and it is this one failing it, not a real layer"
