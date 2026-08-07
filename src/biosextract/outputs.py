"""Writers for the clipped layers.

All four vector formats are written from the same ``ClipResult`` so they cannot
disagree with one another. Two carry caveats worth knowing:

*Shapefile* truncates field names to ten characters and has no boolean type, so
``clip_fraction`` becomes ``clip_frac`` and ``clipped`` becomes an integer. It is
offered for compatibility with older tooling; GeoPackage is the format that
round-trips BIOS attributes intact.

*KMZ* is for viewing, not analysis. Google Earth wants WGS84, so the KML is
always written in EPSG:4326 regardless of the output CRS chosen for the other
files.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from shapely import from_wkb

from .vector import ClipResult


def _json_safe(value):
    """Convert numpy scalars to JSON-native types, leaving NaN as null."""
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if np.isnan(f) else f
    if isinstance(value, (np.str_, str)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _records(result: ClipResult):
    for i in range(result.kept):
        yield {name: _json_safe(col[i]) for name, col in zip(result.fields, result.field_data)}


def write_geojson(result: ClipResult, path: Path, provenance: dict | None = None) -> Path:
    """RFC 7946 GeoJSON. Always WGS84, because the spec says so.

    Provenance is written as foreign members on the FeatureCollection. RFC 7946
    permits members it does not define, and every reader ignores what it does
    not understand, so the file carries its own attribution without becoming
    non-standard.
    """
    from shapely.geometry import mapping

    if result.crs not in ("EPSG:4326", "OGC:CRS84"):
        from pyproj import CRS

        if not CRS.from_user_input(result.crs).equals(CRS.from_epsg(4326)):
            raise ValueError(
                "GeoJSON must be WGS84 but this result is in %s. Write it in "
                "another format, or extract with --output-crs EPSG:4326." % result.crs
            )

    features = []
    for geom_wkb, props in zip(result.geometry, _records(result)):
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(from_wkb(geom_wkb)),
                "properties": props,
            }
        )
    document: dict = {"type": "FeatureCollection"}
    if provenance:
        document.update(
            {
                "attribution": provenance.get("attribution", ""),
                "license": provenance.get("license", ""),
                "source": provenance.get("source", ""),
                "accessed": provenance.get("accessed", ""),
                "generatedBy": provenance.get("generated_by", ""),
                "useConstraints": provenance.get("use_constraints", ""),
                "clippedToBbox": provenance.get("bbox", []),
            }
        )
    document["features"] = features

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_csv(result: ClipResult, path: Path, provenance: dict | None = None) -> Path:
    """Attributes plus a WKT geometry column and a representative point.

    The representative point is guaranteed to lie inside its polygon, unlike a
    centroid, so it is safe to plot a CSV row as a marker.

    ``provenance`` is accepted for a uniform writer signature but not written: a
    CSV has nowhere to put it without breaking the parsers that read it. The
    ATTRIBUTION file beside the outputs carries it instead.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    geoms = [from_wkb(g) for g in result.geometry]
    header = list(result.fields) + ["rep_x", "rep_y", "geometry_wkt"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for rec, geom in zip(_records(result), geoms):
            try:
                pt = geom.representative_point()
                rec["rep_x"], rec["rep_y"] = pt.x, pt.y
            except Exception:
                rec["rep_x"] = rec["rep_y"] = None
            rec["geometry_wkt"] = geom.wkt
            writer.writerow(rec)
    return path


def _write_ogr(result: ClipResult, path: Path, driver: str, **kwargs) -> Path:
    from pyogrio import raw

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    raw.write(
        str(path),
        result.geometry,
        [np.asarray(c) for c in result.field_data],
        fields=list(result.fields),
        geometry_type=result.geometry_type,
        crs=result.crs,
        driver=driver,
        **kwargs,
    )
    return path


def write_gpkg(
    result: ClipResult, path: Path, provenance: dict | None = None, layer: str | None = None
) -> Path:
    """GeoPackage - the format that preserves types, long names and metadata.

    Provenance goes into the GeoPackage's own metadata tables, so the credit
    travels with the file rather than living only in a sidecar someone forgets
    to copy.
    """
    kwargs: dict = {"layer": layer or Path(path).stem}
    if provenance:
        tags = {
            "attribution": provenance.get("attribution", ""),
            "license": provenance.get("license", ""),
            "source": provenance.get("source", ""),
            "accessed": provenance.get("accessed", ""),
            "generated_by": provenance.get("generated_by", ""),
            "use_constraints": provenance.get("use_constraints", ""),
        }
        tags = {k: str(v) for k, v in tags.items() if v}
        kwargs["metadata"] = tags
    try:
        return _write_ogr(result, path, "GPKG", **kwargs)
    except Exception:
        # Metadata support varies with the GDAL build; the data matters more
        # than the tags, so fall back rather than losing the layer.
        kwargs.pop("metadata", None)
        return _write_ogr(result, path, "GPKG", **kwargs)


def write_shapefile(result: ClipResult, path: Path, provenance: dict | None = None) -> Path:
    """Shapefile, with its field-name truncation accepted knowingly.

    The format has no metadata slot at all, so provenance is ignored here and
    lives in the ATTRIBUTION file instead.
    """
    return _write_ogr(result, path, "ESRI Shapefile")


def write_kmz(
    result: ClipResult,
    path: Path,
    provenance: dict | None = None,
    name: str = "",
    label_field: str | None = None,
) -> Path:
    """A KMZ for Google Earth. Viewing aid, not an analysis product."""
    from pyproj import CRS, Transformer
    from shapely.geometry import mapping
    from shapely.ops import transform as shapely_transform

    src = CRS.from_user_input(result.crs)
    dst = CRS.from_epsg(4326)
    geoms = [from_wkb(g) for g in result.geometry]
    if not src.equals(dst):
        tf = Transformer.from_crs(src, dst, always_xy=True)
        geoms = [shapely_transform(lambda x, y, _t=tf: _t.transform(x, y), g) for g in geoms]

    def coords(ring):
        return " ".join(f"{x:.8f},{y:.8f},0" for x, y in ring)

    def geom_kml(geom):
        gj = mapping(geom)
        t = gj["type"]
        if t == "Point":
            x, y = gj["coordinates"][:2]
            return f"<Point><coordinates>{x:.8f},{y:.8f},0</coordinates></Point>"
        if t in ("LineString", "LinearRing"):
            return f"<LineString><coordinates>{coords(gj['coordinates'])}</coordinates></LineString>"
        if t == "Polygon":
            rings = gj["coordinates"]
            inner = "".join(
                f"<innerBoundaryIs><LinearRing><coordinates>{coords(r)}</coordinates>"
                f"</LinearRing></innerBoundaryIs>"
                for r in rings[1:]
            )
            return (
                "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
                f"{coords(rings[0])}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon>"
            )
        if t.startswith("Multi") or t == "GeometryCollection":
            parts = "".join(geom_kml(g) for g in geom.geoms)
            return f"<MultiGeometry>{parts}</MultiGeometry>"
        return ""

    placemarks = []
    for geom, props in zip(geoms, _records(result)):
        if label_field and label_field in props:
            label = str(props[label_field])
        else:
            label = next(
                (str(v) for k, v in props.items() if isinstance(v, str) and v), ""
            )
        rows = "".join(
            f"<tr><td>{escape(str(k))}</td><td>{escape('' if v is None else str(v))}</td></tr>"
            for k, v in props.items()
        )
        placemarks.append(
            "<Placemark><name>%s</name><description><![CDATA[<table>%s</table>]]>"
            "</description>%s</Placemark>" % (escape(label), rows, geom_kml(geom))
        )

    credit = ""
    if provenance:
        parts = [
            provenance.get("attribution", ""),
            f"Licence: {provenance['license']}" if provenance.get("license") else "",
            f"Source: {provenance['source']}" if provenance.get("source") else "",
            f"Accessed: {provenance['accessed']}" if provenance.get("accessed") else "",
            provenance.get("use_constraints", ""),
            provenance.get("generated_by", ""),
        ]
        credit = (
            "<description><![CDATA["
            + "<br/>".join(escape(str(p)) for p in parts if p)
            + "]]></description>"
        )

    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{escape(name or Path(path).stem)}</name>"
        f"{credit}"
        '<Style id="s"><LineStyle><color>ff2288ff</color><width>2</width></LineStyle>'
        "<PolyStyle><color>5a2288ff</color></PolyStyle></Style>"
        + "".join(placemarks)
        + "</Document></kml>"
    )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml)
    return path


#: CLI name -> (writer, extension). Used to validate --formats and to drive the run.
VECTOR_WRITERS = {
    "geojson": (write_geojson, ".geojson"),
    "csv": (write_csv, ".csv"),
    "gpkg": (write_gpkg, ".gpkg"),
    "kmz": (write_kmz, ".kmz"),
    "shp": (write_shapefile, ".shp"),
}
