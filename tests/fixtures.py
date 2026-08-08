"""Synthetic archives that look like what CDFW actually publishes.

Building fixtures in California Teale Albers rather than WGS84 is deliberate:
most BIOS layers are published in EPSG:3310, and a test suite written entirely
in degrees would never exercise the reprojection that real data forces.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import numpy as np
from pyogrio import raw
from pyproj import CRS, Transformer
from shapely import to_wkb
from shapely.geometry import box
from shapely.ops import transform as shapely_transform

ALBERS = "EPSG:3310"

_TF = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(3310), always_xy=True)


def to_albers(geom):
    return shapely_transform(lambda x, y: _TF.transform(x, y), geom)


#: A box wholly inside the La Jolla test area, one straddling its NE corner,
#: and one far away that must never be selected.
INSIDE = box(-117.29, 32.81, -117.27, 32.83)
STRADDLE = box(-117.26, 32.85, -117.20, 32.90)
OUTSIDE = box(-117.10, 32.60, -117.05, 32.65)

TEST_BBOX = "-117.30,32.80,-117.24,32.88"


def make_shapefile(directory: Path, name: str = "mpa") -> Path:
    """Three polygons with an ``Acres`` field that clipping will invalidate."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    geoms = [to_albers(g) for g in (INSIDE, STRADDLE, OUTSIDE)]
    raw.write(
        str(directory / f"{name}.shp"),
        np.array([to_wkb(g) for g in geoms], dtype=object),
        [
            np.array(["Matlahuayl SMR", "Scripps SMCA", "Far Away SMR"], dtype=object),
            np.array([100.0, 200.0, 300.0]),
            np.array(["SMR", "SMCA", "SMR"], dtype=object),
        ],
        fields=["NAME", "Acres", "Type"],
        geometry_type="Polygon",
        crs=ALBERS,
        driver="ESRI Shapefile",
    )
    return directory / f"{name}.shp"


#: A pair of reserves sharing an edge, standing in for South La Jolla SMR and
#: South La Jolla SMCA. ``PAIR_CUT`` straddles the east edge of ``TEST_BBOX``;
#: ``PAIR_PARTNER`` touches it and lies wholly outside the box, so a run that
#: does not expand loses it entirely.
PAIR_CUT = box(-117.25, 32.83, -117.23, 32.85)
PAIR_PARTNER = box(-117.23, 32.83, -117.21, 32.85)

#: Disjoint from both, and positioned so the box only reaches it *after* it has
#: grown to capture the pair - which is what makes a second round necessary.
SECOND_ROUND = box(-117.206, 32.860, -117.200, 32.870)

#: Far enough east that no budget in these tests can reach it.
UNREACHABLE = box(-117.00, 32.83, -116.98, 32.85)


def make_cluster_shapefile(directory: Path, name: str = "mpa") -> Path:
    """Four polygons: a touching pair, a second-round straggler, and a far one."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    geoms = [
        to_albers(g) for g in (PAIR_CUT, PAIR_PARTNER, SECOND_ROUND, UNREACHABLE)
    ]
    raw.write(
        str(directory / f"{name}.shp"),
        np.array([to_wkb(g) for g in geoms], dtype=object),
        [
            np.array(
                [
                    "South La Jolla SMR",
                    "South La Jolla SMCA",
                    "Second Round SMR",
                    "Unreachable SMR",
                ],
                dtype=object,
            ),
            np.array([100.0, 200.0, 300.0, 400.0]),
        ],
        fields=["NAME", "Acres"],
        geometry_type="Polygon",
        crs=ALBERS,
        driver="ESRI Shapefile",
    )
    return directory / f"{name}.shp"


def make_cluster_archive(tmp_path: Path, dataset_id: str = "ds582") -> Path:
    """:func:`make_cluster_shapefile`, zipped like a BIOS download."""
    stage = Path(tmp_path) / f"{dataset_id}_cluster"
    make_cluster_shapefile(stage)
    archive = Path(tmp_path) / f"{dataset_id}-cluster.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(stage)):
            zf.write(stage / fname, f"{dataset_id}/{fname}")
        zf.writestr(f"{dataset_id}/metadata.xml", FGDC_METADATA)
    return archive


#: An FGDC metadata document of the shape BIOS actually ships, trimmed to the
#: elements the citation builder reads.
FGDC_METADATA = """<?xml version="1.0"?>
<metadata>
  <idinfo>
    <citation>
      <citeinfo>
        <origin>California Department of Fish and Wildlife</origin>
        <pubdate>20230308</pubdate>
        <title>California Marine Protected Areas [ds582]</title>
        <onlink>https://filelib.wildlife.ca.gov/Public/BDB/GIS/BIOS/</onlink>
      </citeinfo>
    </citation>
    <descript>
      <abstract>Boundaries of California's marine protected areas.</abstract>
    </descript>
    <accconst>None</accconst>
    <useconst>This dataset is not intended for navigational use or defining
      legal boundaries.</useconst>
  </idinfo>
</metadata>
"""

#: ISO 19139 uses namespaces and different element names for the same facts.
ISO_METADATA = """<?xml version="1.0"?>
<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd"
                 xmlns:gco="http://www.isotc211.org/2005/gco">
  <gmd:identificationInfo><gmd:MD_DataIdentification>
    <gmd:citation><gmd:CI_Citation>
      <gmd:title><gco:CharacterString>Shoreline Types</gco:CharacterString></gmd:title>
      <gmd:date><gmd:CI_Date>
        <gmd:date><gco:Date>2019-06-01</gco:Date></gmd:date>
      </gmd:CI_Date></gmd:date>
      <gmd:citedResponsibleParty><gmd:CI_ResponsibleParty>
        <gmd:organisationName>
          <gco:CharacterString>NOAA Office of Response and Restoration</gco:CharacterString>
        </gmd:organisationName>
      </gmd:CI_ResponsibleParty></gmd:citedResponsibleParty>
    </gmd:CI_Citation></gmd:citation>
    <gmd:resourceConstraints><gmd:MD_LegalConstraints>
      <gmd:useLimitation>
        <gco:CharacterString>Not for navigation.</gco:CharacterString>
      </gmd:useLimitation>
    </gmd:MD_LegalConstraints></gmd:resourceConstraints>
  </gmd:MD_DataIdentification></gmd:identificationInfo>
</gmd:MD_Metadata>
"""


def make_archive(
    tmp_path: Path, dataset_id: str = "ds582", metadata: str | None = FGDC_METADATA
) -> Path:
    """A ZIP shaped like a BIOS download: data under a directory, plus junk.

    ``metadata=None`` produces an archive with no usable citation metadata,
    which is the case the citation builder has to report rather than paper over.
    """
    stage = Path(tmp_path) / dataset_id
    make_shapefile(stage)
    archive = Path(tmp_path) / f"{dataset_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(stage)):
            zf.write(stage / fname, f"{dataset_id}/{fname}")
        zf.writestr(f"{dataset_id}/metadata.xml", metadata if metadata else "<metadata/>")
        zf.writestr(f"{dataset_id}/readme.txt", "not data")
    return archive


def make_geodatabase(directory: Path, layer: str = "ds391") -> Path:
    """A real file geodatabase holding the same three polygons as the shapefile.

    Written with GDAL's OpenFileGDB driver, which is also what reads the BIOS
    archives that ship a ``.gdb``, so the fixture exercises the same path a real
    download does.
    """
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    geoms = [to_albers(g) for g in (INSIDE, STRADDLE, OUTSIDE)]
    raw.write(
        str(directory),
        np.array([to_wkb(g) for g in geoms], dtype=object),
        [
            np.array(["Hard", "Extent", "Hard"], dtype=object),
            np.array([100.0, 200.0, 300.0]),
        ],
        fields=["Sub", "Acres"],
        geometry_type="Polygon",
        crs=ALBERS,
        driver="OpenFileGDB",
        layer=layer,
    )
    return directory


def make_two_gdb_archive(
    tmp_path: Path, dataset_id: str = "ds3091", metadata: str | None = FGDC_METADATA
) -> Path:
    """``ds3091.zip`` in miniature: two ``.gdb`` members, only one of which opens.

    The real archive ships ``v1_final/ds3091.gdb`` - which no driver recognises -
    beside ``v1_final/ds3091_vector.gdb``, which holds the layer. Classified on
    filename alone that reads as two datasets and an ambiguity; opened, it is one
    dataset. The broken member here is a directory of plausible-looking
    ``.gdbtable`` junk, exactly what the name-based classifier accepts.

    Note the shorter name is the *unreadable* one, so a hint of ``ds3091`` must
    be able to reach a member whose name is a prefix of its neighbour's.
    """
    stage = Path(tmp_path) / f"{dataset_id}_two_gdb"
    good = make_geodatabase(stage / "v1_final" / f"{dataset_id}_vector.gdb")

    archive = Path(tmp_path) / f"{dataset_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(good)):
            zf.write(good / fname, f"v1_final/{dataset_id}_vector.gdb/{fname}")
        # The member that will not open: right shape, wrong bytes.
        for fname in ("a00000001.gdbtable", "a00000001.gdbtablx", "gdb"):
            zf.writestr(f"v1_final/{dataset_id}.gdb/{fname}", b"not a geodatabase")
        if metadata:
            zf.writestr(f"{dataset_id}/metadata.xml", metadata)
    return archive


def make_ambiguous_archive(tmp_path: Path, dataset_id: str = "ds3158") -> Path:
    """Two shapefiles that both open: a genuine ambiguity, and one to refuse.

    ``ds3158.zip`` is the real example - a line product and a polygon product
    side by side, both readable - where the tool has no basis for choosing and
    guessing would produce a plausible wrong answer.
    """
    stage = Path(tmp_path) / f"{dataset_id}_ambiguous"
    make_shapefile(stage, "line_version")
    make_shapefile(stage, "poly_version")
    archive = Path(tmp_path) / f"{dataset_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(stage)):
            zf.write(stage / fname, f"{dataset_id}/{fname}")
        zf.writestr(f"{dataset_id}/metadata.xml", FGDC_METADATA)
    return archive


def make_raster_archive(tmp_path: Path, dataset_id: str = "ds3151") -> Path:
    """A GeoTIFF in Albers, zipped, standing in for Kelp Persistence."""
    import rasterio
    from rasterio.transform import from_origin

    stage = Path(tmp_path) / f"{dataset_id}_r"
    stage.mkdir(parents=True, exist_ok=True)
    tif = stage / "kelp.tif"

    minx, miny, maxx, maxy = to_albers(box(-117.35, 32.75, -117.15, 32.95)).bounds
    res = 50.0
    width = int((maxx - minx) / res)
    height = int((maxy - miny) / res)
    data = np.zeros((height, width), dtype="int16")
    data[height // 4 : height // 2, width // 4 : width // 2] = 7  # a kelp patch
    with rasterio.open(
        tif, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="int16", crs=ALBERS, transform=from_origin(minx, maxy, res, res),
        nodata=-1,
    ) as dst:
        dst.write(data, 1)

    archive = Path(tmp_path) / f"{dataset_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tif, f"{dataset_id}/kelp.tif")
    return archive
