"""Payload discovery inside downloaded archives."""

from __future__ import annotations

import zipfile

import pytest

from biosextract import archive as archive_mod


def test_finds_the_shapefile_and_ignores_metadata(archive):
    payloads = archive_mod.inspect(archive)
    members = [p.member for p in payloads]
    assert any(m.endswith(".shp") for m in members)
    assert not any(m.endswith((".xml", ".txt", ".dbf", ".shx", ".prj")) for m in members)


def test_select_returns_the_only_vector(archive):
    payload = archive_mod.select(archive, "vector")
    assert payload.fmt == "shapefile"
    assert payload.vsi_path.startswith("/vsizip/")
    assert payload.vsi_path.endswith(".shp")


def test_select_raster_from_a_vector_only_archive_explains_itself(archive):
    with pytest.raises(archive_mod.ArchiveError) as exc:
        archive_mod.select(archive, "raster")
    assert "no raster data" in str(exc.value)
    assert ".shp" in str(exc.value), "it should say what the archive does contain"


def test_ambiguity_is_reported_not_guessed(tmp_path):
    """Two shapefiles and no hint must fail loudly rather than pick one."""
    from tests.fixtures import make_shapefile

    stage = tmp_path / "multi"
    make_shapefile(stage, "substrate")
    make_shapefile(stage, "quality")
    zip_path = tmp_path / "multi.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in sorted(stage.iterdir()):
            zf.write(f, f"multi/{f.name}")

    with pytest.raises(archive_mod.ArchiveError) as exc:
        archive_mod.select(zip_path, "vector")
    assert "--layer" in str(exc.value)

    # ...and the hint resolves it.
    chosen = archive_mod.select(zip_path, "vector", layer_hint="quality")
    assert "quality" in chosen.member


def test_unreadable_member_does_not_create_an_ambiguity(tmp_path, capsys):
    """ds3091.zip in miniature: two .gdb members, one of which no driver opens.

    Classified on filename that is two datasets and a refusal. Opened, there is
    exactly one, and the layer must resolve with no hint at all.
    """
    from tests.fixtures import make_two_gdb_archive

    zip_path = make_two_gdb_archive(tmp_path)

    payload = archive_mod.select(zip_path, "vector")
    assert payload.member == "v1_final/ds3091_vector.gdb"
    assert payload.fmt == "filegdb"

    out = capsys.readouterr().out
    assert "v1_final/ds3091.gdb" in out, "a skipped member must be named, not dropped"
    assert "skipping" in out


def test_inspect_still_lists_the_member_that_will_not_open(tmp_path):
    """The listing is a listing. Only `select` judges what opens."""
    from tests.fixtures import make_two_gdb_archive

    members = [p.member for p in archive_mod.inspect(make_two_gdb_archive(tmp_path))]
    assert "v1_final/ds3091.gdb" in members
    assert "v1_final/ds3091_vector.gdb" in members


def test_probe_names_the_drivers_reason(tmp_path):
    from tests.fixtures import make_two_gdb_archive

    payloads = archive_mod.inspect(make_two_gdb_archive(tmp_path))
    broken = next(p for p in payloads if p.member == "v1_final/ds3091.gdb")
    good = next(p for p in payloads if p.member.endswith("_vector.gdb"))

    assert archive_mod.opens(good) is None
    reason = archive_mod.opens(broken)
    assert reason and "Error" in reason, f"expected a driver reason, got {reason!r}"


def test_an_archive_whose_candidates_all_fail_says_so(tmp_path):
    """Not 'no vector data' - the members are there, they just do not open."""
    zip_path = tmp_path / "broken.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for gdb in ("one.gdb", "two.gdb"):
            zf.writestr(f"{gdb}/a00000001.gdbtable", b"not a geodatabase")

    with pytest.raises(archive_mod.ArchiveError) as exc:
        archive_mod.select(zip_path, "vector", verbose=False)
    message = str(exc.value)
    assert "not one of them opens" in message
    assert "one.gdb" in message and "two.gdb" in message


def test_file_geodatabase_registered_once(tmp_path):
    zip_path = tmp_path / "gdb.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for member in ("a00000001.gdbtable", "a00000002.gdbtable", "gdb"):
            zf.writestr(f"PMEP.gdb/{member}", b"x" * 16)
    payloads = archive_mod.inspect(zip_path)
    gdbs = [p for p in payloads if p.fmt == "filegdb"]
    assert len(gdbs) == 1, "a geodatabase is one dataset, not one per member file"
    assert gdbs[0].member == "PMEP.gdb"


def test_esri_grid_resolves_to_its_directory(tmp_path):
    zip_path = tmp_path / "grid.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ds3151/kelp/w001001.adf", b"x" * 16)
        zf.writestr("ds3151/kelp/hdr.adf", b"x" * 16)
    payloads = archive_mod.inspect(zip_path)
    grids = [p for p in payloads if p.fmt == "esri-grid"]
    assert len(grids) == 1
    assert grids[0].member == "ds3151/kelp"
    assert grids[0].kind == "raster"


def test_macosx_junk_ignored(tmp_path):
    zip_path = tmp_path / "junk.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("__MACOSX/._thing.shp", b"junk")
        zf.writestr("notes.pdf", b"junk")
    assert archive_mod.inspect(zip_path) == []
