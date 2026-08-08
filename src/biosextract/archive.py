"""Finding the readable payload inside a downloaded archive.

Nothing is unpacked. GDAL's ``/vsizip/`` virtual filesystem reads members in
place, so a 150 MB statewide archive costs 150 MB on disk rather than 300 MB,
and there is no half-extracted directory to invalidate.

BIOS archives are not uniform. Depending on the dataset and its vintage a ZIP
may hold a plain shapefile, a file geodatabase, an Esri GRID raster stored as a
directory of ``.adf`` files, or a GeoTIFF - sometimes several, alongside PDFs
and metadata. Classification is therefore explicit and, when it is ambiguous,
reported rather than resolved by picking the first match.

Classification by filename is a *claim*, not a fact, and a ``.gdb`` says nothing
about what is inside it. ``ds3091.zip`` ships two: ``ds3091_vector.gdb`` holds
the polygons, while ``ds3091.gdb`` holds the same product as a 10 m statewide
raster. Both open - but only one of them opens *as a vector*, which is what a
vector selection is asking. Counting the raster as a second vector candidate
made the archive look ambiguous and put a real layer out of reach entirely.

So when more than one candidate survives, each is asked to open **as the kind
being selected** before it is allowed to create an ambiguity, and one that
cannot is named with the driver's reason rather than dropped in silence.
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


#: How to resolve an ambiguity from `bios extract`, which has the flag.
EXTRACT_ADVICE = "Name one with --layer."


def study_advice(key: str) -> str:
    """How to resolve an ambiguity from a command that has no ``--layer``.

    ``bios study`` takes its layers from the registry and answers every question
    with a flag it declares; telling its user to pass one it does not have is
    advice they cannot follow. Point them at the command that does have it, and
    at the registry pin that would settle it for every future run.
    """
    return (
        "`bios study` has no --layer flag. Choose the member with\n"
        f"    bios extract --datasets {key} --layer <member>\n"
        f"or pin it as `layer=` on the {key} entry in catalog.py, so every run agrees."
    )


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


def _basename(member: str) -> str:
    return member.rstrip("/").rsplit("/", 1)[-1]


def _stem(member: str) -> str:
    base = _basename(member)
    return base.rsplit(".", 1)[0] if "." in base else base


def match(payloads: list[Payload], hint: str) -> list[Payload]:
    """Members ``hint`` names, most specific reading first.

    A plain substring test cannot select a member whose name is a prefix of its
    neighbour's: ``ds3091`` sits inside ``ds3091_vector`` too, so the shorter
    member was unreachable however it was spelled while the error promised
    otherwise. Exactness is therefore tried before containment - full member
    path, then filename, then filename without its extension - and the first
    reading that matches anything wins outright.
    """
    hint = hint.strip().lower()
    for rule in (
        lambda p: p.member.lower() == hint,
        lambda p: _basename(p.member.lower()) == hint,
        lambda p: _stem(p.member.lower()) == hint,
        lambda p: hint in p.member.lower(),
    ):
        found = [p for p in payloads if rule(p)]
        if found:
            return found
    return []


def opens(payload: Payload) -> str | None:
    """``None`` if ``payload`` opens *as its own kind*, else why it does not.

    Only the container is opened - layer names for a vector, band count for a
    raster - so nothing is read and the probe costs a header, not a scan. A
    dataset that opens with no layers or no bands counts as unreadable too: it
    is a directory the classifier recognised, not data anyone can use.

    "As its own kind" is the whole point rather than a detail. A file
    geodatabase can hold vectors or rasters and the filename does not say
    which; ``ds3091.gdb`` opens perfectly as a 10 m raster and not at all as a
    vector. For a vector selection it is not a broken member, it is not a
    member at all, and the reason reported says so.
    """
    try:
        if payload.kind == "vector":
            from pyogrio import list_layers

            if len(list_layers(payload.vsi_path)) == 0:
                return "opens, but declares no layers"
        else:
            import rasterio

            with rasterio.open(payload.vsi_path) as src:
                if src.count == 0:
                    return "opens, but has no raster bands"
    except Exception as exc:  # noqa: BLE001 - driver failures are many and varied
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        return f"{type(exc).__name__}: {first}" if first else type(exc).__name__
    return None


def readable(
    payloads: list[Payload], verbose: bool = True
) -> tuple[list[Payload], dict[str, str]]:
    """Split candidates into those that open as their kind and those that do not.

    Returns the survivors and a ``member -> reason`` map of what was rejected.
    Callers report the map; nothing is discarded quietly, because a member that
    silently vanishes looks exactly like one that was never in the archive.
    """
    good: list[Payload] = []
    rejected: dict[str, str] = {}
    for p in payloads:
        reason = opens(p)
        if reason is None:
            good.append(p)
        else:
            rejected[p.member] = f"not readable as {p.kind}: {reason}"
            if verbose:
                print(f"    skipping {p.member}: not readable as {p.kind}: {reason}")
    return good, rejected


def select(
    archive: Path,
    kind: str,
    layer_hint: str | None = None,
    verbose: bool = True,
    advice: str = EXTRACT_ADVICE,
) -> Payload:
    """Pick the single payload of ``kind`` to read, or explain why it cannot.

    An archive with two shapefiles and no hint is genuinely ambiguous, and
    quietly taking the first would be the kind of plausible-but-wrong choice
    this repo family is built to avoid. The error lists the candidates so the
    caller can name one.

    Ambiguity is only ever declared over members that actually open. The probe
    runs solely when a choice has to be made, so the ordinary one-payload
    archive still costs nothing but a listing.

    ``advice`` is the last line of a refusal and belongs to the *calling
    command*: only `bios extract` has a ``--layer`` flag, and a refusal printed
    by `bios study` that tells its user to pass one is an instruction they
    cannot carry out. See :func:`study_advice`.
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
        matches = match(payloads, layer_hint)
        if len(matches) == 1:
            chosen = matches[0]
            # An explicit choice is honoured, but not into a member the driver
            # will refuse three stages later with no mention of the hint.
            reason = opens(chosen)
            if reason is None:
                return chosen
            others = [p for p in payloads if p is not chosen and opens(p) is None]
            raise ArchiveError(
                "%s: %r names %s, which does not open: %s%s"
                % (
                    Path(archive).name,
                    layer_hint,
                    chosen.member,
                    reason,
                    (
                        "\nMembers that do open:\n  "
                        + "\n  ".join(str(p) for p in others)
                        if others
                        else ""
                    ),
                )
            )
        if len(matches) > 1:
            payloads = matches
        elif verbose:
            print(
                f"    note: layer hint {layer_hint!r} matched no member of "
                f"{Path(archive).name}"
            )

    if len(payloads) == 1:
        return payloads[0]

    # More than one candidate by name. Before calling that a conflict, make each
    # one prove it opens as this kind: ds3091.zip's two .gdb directories are the
    # vector product and the same product as a raster, not two vector layers.
    survivors, rejected = readable(payloads, verbose=verbose)

    if len(survivors) == 1:
        return survivors[0]

    if not survivors:
        raise ArchiveError(
            "%s contains %d member(s) named like %s data, and not one of them "
            "opens as %s:\n  %s"
            % (
                Path(archive).name,
                len(payloads),
                kind,
                kind,
                "\n  ".join(f"{member} - {reason}" for member, reason in rejected.items()),
            )
        )

    raise ArchiveError(
        "%s contains %d readable %s datasets and no unambiguous choice:\n  %s\n%s%s"
        % (
            Path(archive).name,
            len(survivors),
            kind,
            "\n  ".join(str(p) for p in survivors),
            (
                "(skipped: " + "; ".join(f"{m} - {r}" for m, r in rejected.items()) + ")\n"
                if rejected
                else ""
            ),
            advice,
        )
    )
