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
