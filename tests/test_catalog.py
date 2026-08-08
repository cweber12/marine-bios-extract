"""Registry and URL resolution.

The point of these tests is that the toolkit never invents a download URL. The
bucket name is derived, but derivation is a hypothesis that ``resolve_bios``
confirms against a real directory listing.
"""

from __future__ import annotations

import json
import re

import pytest

from biosextract import catalog, citation
from tests import registry as synthetic


def assert_well_formed(key, d):
    """The shape every registry entry must have, real or synthetic."""
    assert d.key == key, "registry key and dataset.key must match"
    assert d.kind in ("vector", "raster")
    assert d.status in ("ready", "manual", "unverified")
    assert d.provider in ("bios", "pmep", "usgs", "fema")
    if d.provider == "bios":
        assert d.dataset_id, f"{key} is a BIOS dataset but has no ds id"
    if d.status != "ready":
        assert d.landing_url, f"{key} is not automatic, so it must say where to go"


def test_every_registered_dataset_is_self_consistent():
    for key, d in catalog.DATASETS.items():
        assert_well_formed(key, d)


def test_the_synthetic_registry_collides_with_no_real_key():
    """`tests/registry.py` owns the content assertions; it must own its keys too.

    A synthetic key that shadowed a real one would let a test that still
    reaches for real content pass quietly against a substitute, instead of
    dying with the KeyError that says so. That is the same class of bug as the
    five in-place fixes this registry replaces, in a form nobody would notice.
    """
    collisions = set(synthetic.SYNTHETIC) & set(catalog.DATASETS)
    assert not collisions, f"synthetic keys must not exist for real: {collisions}"


def test_the_synthetic_registry_is_as_well_formed_as_the_real_one():
    """It is installed into `catalog.DATASETS`, so it meets the same rules.

    A fixture the production code would reject is a fixture that proves the
    wrong thing about the production code.
    """
    for key, d in synthetic.SYNTHETIC.items():
        assert_well_formed(key, d)
        if d.known_originator or d.known_pubdate:
            assert d.verified_from and d.verified_on, (
                f"{key} pins a citation fact but does not say where it was read"
            )


def test_a_hand_verified_pin_must_say_where_it_came_from():
    """A pin without provenance is folklore in a citation format.

    Nobody can re-check it, so it hardens by age rather than by evidence. The
    guard covers the citation pins because those are what this rule was written
    with; `license` is deliberately not covered *yet*, because `mpa` carries one
    recorded before any of this existed and nobody now knows which page it came
    from. Extending the guard to licences today would make this suite red until
    a human resolves that (#20), and §1.7 forbids committing red - so the audit
    reports it instead, which is visible without being a lie.
    """
    for key, d in catalog.DATASETS.items():
        if d.known_originator or d.known_pubdate:
            assert d.verified_from, (
                f"{key} pins a citation fact but does not say where it was read"
            )
            assert d.verified_on, f"{key} pins a citation fact but not when it was read"


def test_the_real_registry_audits_without_crashing(tmp_path):
    """The one test that runs the audit over the live registry.

    Every other assertion about verified, unverified or untraceable state moved
    to `tests/registry.py` in #24, and this is what would otherwise have been
    lost with them: the audit meeting real entries, with their real mix of
    absent licences, pinned dates and statuses nobody has resolved. That is the
    coverage that made the five breakages informative in the first place.

    What it asserts is that the audit *runs* and reports every dataset, and
    that a row survives the trip into a manifest. Not that any layer is clean -
    that is somebody's outstanding work, and a test demanding it would pit the
    suite against the rule about never committing red.
    """
    rows = citation.audit(catalog.DATASETS, tmp_path / "empty-cache")

    assert {r.key for r in rows} == set(catalog.DATASETS), "every dataset is reported"
    for row in rows:
        assert row.license, "a row always names a licence, even if it is UNKNOWN"
        assert row.status in ("ready", "manual", "unverified")
        json.dumps(row.as_dict())  # it has to reach a manifest

    # The verdict is computed over the real mix too; what it *says* is a matter
    # for whoever is finishing the licences, not for this test.
    citation.unverified(rows)


@pytest.mark.parametrize(
    "dataset_id,bucket",
    [
        ("ds582", "500_599"),
        ("ds3151", "3100_3199"),
        ("ds3115", "3100_3199"),
        ("ds2864", "2800_2899"),
        ("ds3091", "3000_3099"),
        ("ds3207", "3200_3299"),
        ("ds1", "0_99"),
    ],
)
def test_bucket_derivation(dataset_id, bucket):
    assert catalog.bios_bucket(dataset_id) == bucket


def test_bucket_rejects_nonsense():
    with pytest.raises(catalog.CatalogError):
        catalog.bios_bucket("kelp")


def test_unknown_key_lists_the_alternatives():
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.get("does-not-exist")
    assert "mpa" in str(exc.value), "the error should show what is available"


def test_resolve_keys_defaults_to_ready_only():
    keys = catalog.resolve_keys(None)
    assert keys, "there should be at least one automatic dataset"
    for k in keys:
        assert catalog.DATASETS[k].status == "ready"
    # A gated source must never be swept into a batch run implicitly.
    assert "cmecs-substrate" not in keys


def test_resolve_keys_accepts_explicit_list():
    assert catalog.resolve_keys("mpa, shoreline") == ["mpa", "shoreline"]


def test_resolve_keys_rejects_unknown():
    with pytest.raises(catalog.CatalogError):
        catalog.resolve_keys("mpa,nope")


def test_gated_provider_explains_itself_rather_than_guessing():
    with pytest.raises(catalog.ManualDownloadRequired) as exc:
        catalog.resolve(catalog.get("cmecs-substrate"))
    message = str(exc.value)
    assert "--local-archive" in message
    assert exc.value.landing_url.startswith("http")


def test_unverified_provider_refuses_to_invent_a_url():
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.resolve(catalog.get("flood-hazard"))
    assert "not wired up" in str(exc.value)


def test_every_unverified_dataset_records_why():
    """A status with no reason gets re-argued by whoever meets it next."""
    for key, d in catalog.DATASETS.items():
        if d.status != "unverified":
            continue
        with pytest.raises(catalog.CatalogError) as exc:
            catalog.resolve(d)
        assert d.landing_url in str(exc.value), key


def test_state_waters_says_which_choice_was_not_made(monkeypatch):
    """It is downloadable; what is unverified is which of two products to read.

    Resolving must therefore refuse *before* the network, not resolve happily
    and fail later on an ambiguous archive with nothing on screen about why.
    """
    def no_requests(*a, **kw):  # pragma: no cover - only runs on a regression
        raise AssertionError("an unverified dataset must not reach the publisher")

    monkeypatch.setattr(catalog, "_open", no_requests)

    dataset = catalog.get("state-waters")
    assert dataset.status == "unverified"

    with pytest.raises(catalog.CatalogError) as exc:
        catalog.resolve(dataset)
    message = str(exc.value)
    assert "ds3158.gdb" in message and "ds3158_alt.gdb" in message
    assert "line" in message and "polygon" in message


def test_state_waters_is_out_of_a_default_run():
    assert "state-waters" not in catalog.resolve_keys(None)
    assert "state-waters" not in catalog.resolve_keys("all")
    # ...but can still be named, which is what makes the refusal reachable.
    assert catalog.resolve_keys("state-waters") == ["state-waters"]


def test_a_recorded_licence_names_a_licence():
    """§4: a licence is verified out of band or it stays unknown.

    An empty string is the registry's way of saying "not verified", and that
    surfaces as [unknown - see metadata]. What must never appear is a value that
    reads like a licence without being one.
    """
    for key, d in catalog.DATASETS.items():
        if not d.license:
            continue
        assert any(
            token in d.license for token in ("CC-BY", "CC0", "Public Domain")
        ), f"{key} records a licence that names nothing checkable: {d.license!r}"


def test_a_read_note_says_what_a_slow_layer_will_cost():
    """The note is a measured fact about ds3091, not decoration.

    It lived in test_study_command, where it was the fifth test asserting on a
    real layer's registry text from a module about behaviour - and where any
    rewording of the note would have turned correct work red. The registry is
    this module's subject, so it belongs here; and it now asks the note to name
    a duration rather than to contain one particular word, because "takes a
    minute or two" and "takes 60 to 120 seconds" are the same fact.
    """
    noted = {k: d for k, d in catalog.DATASETS.items() if d.read_note}

    assert "benthic-substrate" in noted, "ds3091 is the layer measured as slow"
    for key, d in noted.items():
        assert d.status == "ready", (
            f"{key} cannot be fetched unattended, so a note about its read is decoration"
        )
        assert re.search(r"minute|second|hour", d.read_note), (
            f"{key}'s note does not say what the wait is: {d.read_note!r}"
        )


def test_benthic_substrate_carries_its_verified_licence():
    d = catalog.get("benthic-substrate")
    assert "CC-BY 4.0" in d.license
    assert "creativecommons.org/licenses/by/4.0" in d.use_constraints
    assert "disclaims liability" in d.use_constraints


def test_metadata_url_only_for_bios():
    assert catalog.metadata_url(catalog.get("mpa")).endswith("DS582.html")
    assert catalog.metadata_url(catalog.get("cmecs-substrate")) is None


def test_listing_parser_is_tolerant_of_index_flavour(monkeypatch):
    """The library's HTML index format is not a contract; hrefs are."""
    iis = (
        '<pre><a href="/Public/BDB/GIS/BIOS/Public_Datasets/">[To Parent]</a><br>'
        '3/8/2023 9:01 AM 490298 <a href="ds582.zip">ds582.zip</a><br>'
        '5/27/2026 1:00 PM 470019 <a href="ds598.zip">ds598.zip</a><br>'
        '<a href="readme.txt">readme.txt</a></pre>'
    )
    apache = (
        '<html><body><h1>Index</h1><table>'
        '<tr><td><a href="ds582.zip">ds582.zip</a></td><td>2023-03-08</td></tr>'
        "</table></body></html>"
    )
    for html in (iis, apache):
        class _Resp:
            status = 200

            def read(self):
                return html.encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(catalog, "_open", lambda *a, **k: _Resp())
        found = catalog.list_bios_directory("500_599")
        assert "ds582.zip" in found
        assert found["ds582.zip"].endswith("/500_599/ds582.zip")
        assert "readme.txt" not in found


def test_missing_archive_reports_the_directory_contents(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "list_bios_directory",
        lambda bucket, timeout=60: {"ds500.zip": "u/ds500.zip"},
    )
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.resolve_bios(catalog.get("mpa"))
    text = str(exc.value)
    assert "ds582.zip is not in" in text
    assert "ds500.zip" in text, "the error must show what the bucket does hold"


@pytest.mark.network
def test_live_resolution_of_every_bios_dataset():
    """The real acceptance check. Needs network; run with -m network."""
    for key, d in catalog.DATASETS.items():
        if d.provider != "bios" or d.status != "ready":
            continue
        src = catalog.resolve(d)
        assert src.url.endswith(f"{d.dataset_id}.zip")
        assert src.bytes is None or src.bytes > 1024
