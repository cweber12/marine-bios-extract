"""Finding the readable payload inside a downloaded archive.

Nothing is unpacked. GDAL's ``/vsizip/`` virtual filesystem reads members in
place, so a 150 MB statewide archive costs 150 MB on disk rather than 300 MB,
and there is no half-extracted directory to invalidate.

BIOS archives are not uniform. Depending on the dataset and its vintage a ZIP
may hold a plain shapefile, a file geodatabase, an Esri GRID raster stored as a
directory of ``.adf`` files, or a GeoTIFF - sometimes several, alongside PDFs
and metadata. Classification is therefore explicit and, when it is ambiguous,
reported rather than resolved by picking the first match.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: Members that are never data, however tempting their extension looks.
_IGNORE_RE = re.compile(
    r"(^|/)(__MACOSX/|\.)|\.(pdf|txt|xml|html?|docx?|lyr|avl|mxd|png|jpg)$", re.I
)

_VECTOR_EXT = (".shp", ".gpkg", ".geojson", ".json", ".kml", ".gml", ".tab")
_RASTER_EXT = (".tif", ".tiff", ".img", ".bil", ".vrt", ".jp2")


class ArchiveError(RuntimeError):
    """Raised when an archive holds nothing readable, or too many candidates."""


@dataclass(frozen=True)
class Payload:
    """One readable dataset inside an archive."""

    #: GDAL path, e.g. ``/vsizip//abs/path/ds582.zip/ds582/mpa.shp``
    vsi_path: str
    #: Member path inside the archive, for reporting.
    member: str
    #: "vector" or "raster"
    kind: str
    #: "shapefile", "filegdb", "geotiff", "esri-grid", ...
    fmt: str

    def __str__(self) -> str:
        return f"{self.member} ({self.fmt})"


def _vsi(archive: Path, member: str = "") -> str:
    """Build a /vsizip/ path. GDAL wants POSIX separators inside the archive."""
    base = "/vsizip/" + str(Path(archive).resolve()).replace("\\", "/")
    return posixpath.join(base, member) if member else base


def inspect(archive: Path) -> list[Payload]:
    """List every readable dataset inside ``archive``.

    Returns vectors and rasters together; callers filter by ``kind``. Order is
    stable (alphabetical by member) so a run is reproducible.
    """
    archive = Path(archive)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()

    payloads: list[Payload] = []
    seen_gdb: set[str] = set()

    for name in names:
        if _IGNORE_RE.search(name):
            continue
        lower = name.lower()

        # ---- File geodatabase: a directory, not a file ------------------
        # Members appear as "something.gdb/a00000001.gdbtable". Register the
        # container once.
        m = re.match(r"^(.*?\.gdb)/", name, flags=re.I)
        if m:
            gdb = m.group(1)
            if gdb not in seen_gdb:
                seen_gdb.add(gdb)
                payloads.append(
                    Payload(
                        vsi_path=_vsi(archive, gdb),
                        member=gdb,
                        kind="vector",
                        fmt="filegdb",
                    )
                )
            continue

        # ---- Esri GRID raster: also a directory -------------------------
        # Identified by the w001001.adf band file; the parent directory is what
        # GDAL opens.
        if lower.endswith("w001001.adf"):
            grid_dir = posixpath.dirname(name)
            payloads.append(
                Payload(
                    vsi_path=_vsi(archive, grid_dir),
                    member=grid_dir,
                    kind="raster",
                    fmt="esri-grid",
                )
            )
            continue

        if lower.endswith(_VECTOR_EXT):
            payloads.append(
                Payload(
                    vsi_path=_vsi(archive, name),
                    member=name,
                    kind="vector",
                    fmt="shapefile" if lower.endswith(".shp") else lower.rsplit(".", 1)[-1],
                )
            )
        elif lower.endswith(_RASTER_EXT):
            payloads.append(
                Payload(
                    vsi_path=_vsi(archive, name),
                    member=name,
                    kind="raster",
                    fmt="geotiff" if lower.endswith((".tif", ".tiff")) else lower.rsplit(".", 1)[-1],
                )
            )

    return sorted(payloads, key=lambda p: p.member.lower())


def select(archive: Path, kind: str, layer_hint: str | None = None) -> Payload:
    """Pick the single payload of ``kind`` to read, or explain why it cannot.

    An archive with two shapefiles and no hint is genuinely ambiguous, and
    quietly taking the first would be the kind of plausible-but-wrong choice
    this repo family is built to avoid. The error lists the candidates so the
    caller can name one.
    """
    payloads = [p for p in inspect(archive) if p.kind == kind]
    if not payloads:
        everything = inspect(archive)
        raise ArchiveError(
            "%s contains no %s data.%s"
            % (
                Path(archive).name,
                kind,
                (
                    " It does contain: " + ", ".join(str(p) for p in everything)
                    if everything
                    else " No readable GIS members were found at all."
                ),
            )
        )

    if layer_hint:
        matches = [p for p in payloads if layer_hint.lower() in p.member.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            payloads = matches

    if len(payloads) == 1:
        return payloads[0]

    raise ArchiveError(
        "%s contains %d %s datasets and no unambiguous choice:\n  %s\n"
        "Name one with --layer."
        % (
            Path(archive).name,
            len(payloads),
            kind,
            "\n  ".join(str(p) for p in payloads),
        )
    )
