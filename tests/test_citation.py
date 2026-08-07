"""Citation, licence and use-constraint handling.

The governing rule is that a missing value must stay visibly missing. An
incomplete citation is an inconvenience; a confidently fabricated originator is
a misattribution that outlives the run.
"""

from __future__ import annotations

import json

import pytest

from biosextract import citation as citation_mod
from biosextract.citation import UNKNOWN, Citation, format_pubdate
from tests.fixtures import ISO_METADATA, TEST_BBOX, make_archive


def test_reads_fgdc_originator_and_date(archive):
    c = citation_mod.from_archive(archive, "mpa", "California Marine Protected Areas")
    assert c.originator == "California Department of Fish and Wildlife"
    assert c.publication_date == "2023, Mar. 8"
    assert c.complete is True
    assert c.metadata_source.endswith("metadata.xml")


def test_archive_title_wins_over_the_registry_label(archive):
    c = citation_mod.from_archive(archive, "mpa", "Some Label We Chose")
    assert c.title == "California Marine Protected Areas [ds582]"


def test_reads_use_constraints_from_the_archive(archive):
    c = citation_mod.from_archive(archive, "mpa", "MPAs")
    assert "not intended for navigational use" in c.use_constraints


def test_reads_iso19139_despite_namespaces(tmp_path):
    arch = make_archive(tmp_path, "ds3115", metadata=ISO_METADATA)
    c = citation_mod.from_archive(arch, "shoreline", "Shoreline Types")
    assert c.originator == "NOAA Office of Response and Restoration"
    assert "Not for navigation." in c.use_constraints


def test_missing_metadata_is_reported_not_invented(tmp_path):
    arch = make_archive(tmp_path, "ds582", metadata=None)
    c = citation_mod.from_archive(arch, "mpa", "MPAs", metadata_page="https://x/DS582.html")
    assert c.originator == UNKNOWN
    assert c.publication_date == UNKNOWN
    assert c.complete is False
    assert c.warnings, "an incomplete citation must carry a warning"
    assert "DS582.html" in c.warnings[0], "the warning should say where to look"


def test_registry_licence_is_used_when_metadata_is_silent(archive):
    c = citation_mod.from_archive(
        archive, "mpa", "MPAs", known_license="CC-BY - attribution required"
    )
    assert "CC-BY" in c.license


def test_unverified_licence_stays_unknown(archive):
    c = citation_mod.from_archive(archive, "mpa", "MPAs")
    assert c.license == UNKNOWN, "an unverified licence must not default to permissive"


@pytest.mark.parametrize(
    "raw,expected",
    [("20230308", "2023, Mar. 8"), ("202306", "2023, Jun."), ("2019", "2019"), ("", None)],
)
def test_publication_date_formats(raw, expected):
    assert format_pubdate(raw) == expected


def test_apa_and_mla_contain_all_five_required_elements(archive):
    c = citation_mod.from_archive(
        archive, "mpa", "MPAs", url="https://example/ds582.zip", accessed="2026-08-07"
    )
    for style in (c.apa(), c.mla()):
        assert "California Department of Fish and Wildlife" in style  # originator
        assert "2023" in style  # publication date
        assert "Marine Protected Areas" in style  # title
        assert "BIOS" in style  # where accessed from
        assert "2026-08-07" in style  # when accessed


def test_attribution_file_warns_about_clipped_attributes(tmp_path, archive):
    c = citation_mod.from_archive(archive, "mpa", "MPAs")
    path = citation_mod.write_attribution_file(
        [c], tmp_path / "ATTRIBUTION.txt", bbox_text=TEST_BBOX,
        version="0.1.0", generated="2026-08-07T00:00:00+00:00",
    )
    text = path.read_text()
    assert "orig_" in text, "readers must be told the orig_ fields are stale"
    assert "not legal advice" in text
    assert TEST_BBOX in text


def test_end_to_end_run_emits_attribution_and_citation_in_the_manifest(archive, tmp_path):
    from biosextract.cli import main

    out = tmp_path / "out"
    assert main(
        [
            "extract", f"--bbox={TEST_BBOX}", "--datasets", "mpa",
            "--local-archive", f"mpa={archive}",
            "--formats", "geojson,gpkg,kmz",
            "--out-dir", str(out), "--prefix", "lajolla",
            "--cache-dir", str(tmp_path / "cache"),
        ]
    ) == 0

    attribution = out / "lajolla_ATTRIBUTION.txt"
    assert attribution.exists()
    assert "California Department of Fish and Wildlife" in attribution.read_text()

    doc = json.loads((out / "lajolla_manifest.json").read_text())
    cite = doc["layers"][0]["citation"]
    assert cite["complete"] is True
    assert cite["originator"] == "California Department of Fish and Wildlife"
    assert "not intended for navigational use" in cite["use_constraints"]
    # The attribution file must itself be a recorded, hashed output.
    assert any(o["kind"] == "attribution" for o in doc["outputs"])


def test_provenance_is_embedded_in_the_geojson(archive, tmp_path):
    from biosextract.cli import main

    out = tmp_path / "out"
    main(
        [
            "extract", f"--bbox={TEST_BBOX}", "--datasets", "mpa",
            "--local-archive", f"mpa={archive}", "--formats", "geojson",
            "--out-dir", str(out), "--prefix", "lajolla",
            "--cache-dir", str(tmp_path / "cache"),
        ]
    )
    doc = json.loads((out / "lajolla_mpa.geojson").read_text())
    assert doc["type"] == "FeatureCollection"
    assert "California Department of Fish and Wildlife" in doc["attribution"]
    assert doc["useConstraints"]
    assert doc["clippedToBbox"] == [-117.30, 32.80, -117.24, 32.88]
    # Foreign members must not disturb the parts readers actually rely on.
    assert doc["features"] and doc["features"][0]["type"] == "Feature"


def test_network_command_describes_behaviour_without_touching_the_network(capsys):
    from biosextract.cli import main

    assert main(["network"]) == 0
    printed = capsys.readouterr().out
    assert "filelib.wildlife.ca.gov" in printed
    assert "User-Agent" in printed
    assert "not fall back to their REST service" in printed


def test_contact_env_var_is_appended_to_user_agent(monkeypatch):
    from biosextract import catalog

    monkeypatch.setenv("BIOS_CONTACT", "someone@example.org")
    assert "contact:someone@example.org" in catalog.user_agent()
    monkeypatch.delenv("BIOS_CONTACT")
    assert "contact:" not in catalog.user_agent()
