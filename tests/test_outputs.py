"""Writers, and the end-to-end CLI run."""

from __future__ import annotations

import json

import pytest

from biosextract import outputs, vector
from biosextract.archive import select
from biosextract.bbox import BBox
from tests.fixtures import TEST_BBOX


@pytest.fixture
def clipped(archive):
    payload = select(archive, "vector")
    return vector.clip(
        payload.vsi_path, BBox.parse(TEST_BBOX), geometry_fields=("Acres",), verbose=False
    )


def test_geojson_is_valid_and_carries_the_flags(clipped, tmp_path):
    path = outputs.write_geojson(clipped, tmp_path / "mpa.geojson")
    doc = json.loads(path.read_text())
    assert doc["type"] == "FeatureCollection"
    assert len(doc["features"]) == clipped.kept
    props = doc["features"][0]["properties"]
    for key in ("clipped", "clip_fraction", "area_m2", "orig_Acres"):
        assert key in props
    assert isinstance(props["clipped"], bool), "numpy bools must serialise as JSON bools"


def test_geojson_refuses_a_non_wgs84_result(clipped, tmp_path):
    clipped.crs = "EPSG:3310"
    with pytest.raises(ValueError, match="WGS84"):
        outputs.write_geojson(clipped, tmp_path / "bad.geojson")


def test_csv_has_a_representative_point_inside_the_polygon(clipped, tmp_path):
    import csv as csv_mod

    from shapely import from_wkb
    from shapely.geometry import Point

    path = outputs.write_csv(clipped, tmp_path / "mpa.csv")
    rows = list(csv_mod.DictReader(path.open()))
    assert len(rows) == clipped.kept
    for row, wkb in zip(rows, clipped.geometry):
        pt = Point(float(row["rep_x"]), float(row["rep_y"]))
        assert from_wkb(wkb).intersects(pt), "rep point must lie on its own feature"


def test_gpkg_round_trips_full_field_names(clipped, tmp_path):
    from pyogrio import read_info

    path = outputs.write_gpkg(clipped, tmp_path / "mpa.gpkg")
    fields = list(read_info(str(path))["fields"])
    assert "clip_fraction" in fields, "GeoPackage must not truncate field names"
    assert "orig_Acres" in fields


def test_shapefile_writes_despite_truncation(clipped, tmp_path):
    from pyogrio import read_info

    path = outputs.write_shapefile(clipped, tmp_path / "mpa.shp")
    info = read_info(str(path))
    assert info["features"] == clipped.kept
    # Documented lossiness: 10-character field names.
    assert any(f.startswith("clip_fra") for f in info["fields"])


def test_kmz_is_a_zip_containing_kml(clipped, tmp_path):
    import zipfile

    path = outputs.write_kmz(clipped, tmp_path / "mpa.kmz")
    with zipfile.ZipFile(path) as zf:
        assert "doc.kml" in zf.namelist()
        kml = zf.read("doc.kml").decode()
    assert kml.startswith("<?xml")
    assert kml.count("<Placemark>") == clipped.kept
    assert "Matlahuayl SMR" in kml


def test_cli_end_to_end_writes_every_format_and_a_manifest(archive, tmp_path):
    from biosextract.cli import main

    out = tmp_path / "out"
    code = main(
        [
            "extract",
            f"--bbox={TEST_BBOX}",
            "--datasets", "mpa",
            "--local-archive", f"mpa={archive}",
            "--formats", "geojson,csv,gpkg,kmz,shp",
            "--out-dir", str(out),
            "--prefix", "lajolla",
            "--cache-dir", str(tmp_path / "cache"),
        ]
    )
    assert code == 0
    for ext in (".geojson", ".csv", ".gpkg", ".kmz", ".shp"):
        assert (out / f"lajolla_mpa{ext}").exists()

    doc = json.loads((out / "lajolla_manifest.json").read_text())
    assert doc["tool"]["name"] == "marine-bios-extract"
    assert doc["request"]["bbox"] == [-117.30, 32.80, -117.24, 32.88]
    layer = doc["layers"][0]
    assert layer["clip"]["kept"] == 2
    assert layer["clip"]["clipped_at_boundary"] == 1
    assert layer["source"]["sha256"]
    # Every declared output must exist and match its recorded hash.
    from biosextract.manifest import sha256_file

    for entry in doc["outputs"]:
        path = out / entry["path"]
        assert path.exists()
        assert sha256_file(path) == entry["sha256"]


def test_cli_normalizes_a_negative_longitude_argument():
    """`--bbox -117.3,...` must work; argparse alone reads it as a flag."""
    from biosextract.cli import normalize_argv

    assert normalize_argv(["extract", "--bbox", "-117.3,32.8,-117.2,32.9"]) == [
        "extract",
        "--bbox=-117.3,32.8,-117.2,32.9",
    ]
    # A normal value is left alone.
    assert normalize_argv(["extract", "--prefix", "lajolla"]) == [
        "extract",
        "--prefix",
        "lajolla",
    ]


def test_cli_rejects_an_unknown_format(archive, tmp_path):
    from biosextract.cli import main

    with pytest.raises(SystemExit):
        main(
            [
                "extract",
                f"--bbox={TEST_BBOX}",
                "--datasets", "mpa",
                "--local-archive", f"mpa={archive}",
                "--formats", "geotiff",
                "--out-dir", str(tmp_path / "out"),
            ]
        )
