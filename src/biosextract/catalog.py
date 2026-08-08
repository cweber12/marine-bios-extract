"""Dataset registry and download-URL resolution.

Every layer this toolkit knows about is declared here, and every one of them is
resolved against the publisher before it is fetched. Resolution is deliberately
not string formatting: the BIOS file library is browsable, so the resolver lists
the directory, confirms the archive is actually present, and reads its size and
modification date from an HTTP HEAD. Guessing a URL and getting a 404 tells you
nothing about *why*; listing the directory and reporting its contents tells you
whether the dataset moved, was renamed, or never existed.

The bucket directory can be derived (``ds3151`` lives in ``3100_3199``) but the
derivation is treated as a hypothesis to be confirmed, never as an answer.

Provider notes
--------------
``bios``  CDFW's own file library. Fully automatable, stable, browsable.
``pmep``  Pacific Marine and Estuarine Fish Habitat Partnership. Its bulk
          geodatabase sits behind a ShareFile link that requires an email
          registration, so it cannot be fetched unattended. Supply the archive
          yourself with ``--local-archive`` or accept the REST fallback, which
          is explicitly recorded as non-reproducible in the manifest.
``usgs``  National Map staged products (watershed boundaries, quad cells).
``fema``  National Flood Hazard Layer, published per county.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

#: Identifies the tool honestly rather than impersonating a browser, and points
#: at the programme whose data is being used so an administrator seeing it in a
#: log can tell what it is. Set BIOS_CONTACT to append your own address.
USER_AGENT = "marine-bios-extract/0.1 (+https://wildlife.ca.gov/Data/BIOS)"


def user_agent() -> str:
    """User-Agent, with an operator contact appended when one is configured."""
    import os

    contact = (os.environ.get("BIOS_CONTACT") or "").strip()
    return f"{USER_AGENT} contact:{contact}" if contact else USER_AGENT


#: The tool's complete network behaviour, stated so it can be audited without
#: reading the code. Printed by `bios network`.
#:
#: This is not a crawler. It follows no links, keeps no URL frontier and
#: discovers nothing: it reads one directory index to confirm a filename you
#: already named, then downloads that one file. Checked 2026-08-06,
#: filelib.wildlife.ca.gov/robots.txt disallows /cgi-bin/, /scripts/, /private/
#: and /admin/ plus several file extensions; neither directory paths nor .zip
#: files are restricted, so this access is permitted.
NETWORK_PROFILE = """\
Requests made per dataset, on a cold run:

  1. GET   the bucket directory index, to confirm the archive exists
  2. HEAD  the archive, to read its size and Last-Modified
  3. GET   the archive itself

On any later run these drop to zero: the archive is cached under .cache/ and
reused. Re-extracting a different bounding box costs no network at all.

Extracting all seven automatic BIOS datasets is therefore about 21 requests,
once. Nothing is fetched that you did not name, no links are followed, and no
URLs are discovered. Metadata for citations is read out of the cached archive
rather than fetched separately.

`bios study` makes the same three requests per dataset and no more, but it makes
step 3 earlier than you might expect: it grows the box out to whole feature
groups before it plans, and it cannot know what the boundary cuts without
reading the vector layers. So a `--dry-run` downloads the vector archives it
would have used, and says so. `--no-expand` returns a dry run to step 1 and 2
only.

The User-Agent identifies this tool and links to the BIOS programme. Set the
BIOS_CONTACT environment variable to append your email address, which is the
courteous thing to do if you run this often.

Gated sources are not worked around. PMEP publishes its geodatabase behind an
email registration form; this tool asks you to complete that once and pass the
file with --local-archive rather than automating past it, and deliberately does
not fall back to their REST service to avoid the form.
"""

#: Root of CDFW's public BIOS dataset library.
BIOS_ROOT = "https://filelib.wildlife.ca.gov/Public/BDB/GIS/BIOS/Public_Datasets"

#: Human-readable metadata page for a BIOS dataset. Present for most datasets
#: but not all - DS3151 resolves, DS582 returns 404 - so it is enrichment only
#: and never the resolver.
BIOS_METADATA = "https://filelib.wildlife.ca.gov/Public/BDB/GIS/BIOS/metadata/DS{n}.html"


class CatalogError(RuntimeError):
    """Raised when a dataset cannot be resolved to a concrete download."""


class ManualDownloadRequired(CatalogError):
    """The publisher gates this archive behind a form, so it cannot be fetched.

    Carries the page a human should visit and the flag to pass afterwards.
    """

    def __init__(self, message: str, landing_url: str) -> None:
        super().__init__(message)
        self.landing_url = landing_url


@dataclass(frozen=True)
class Dataset:
    """One publishable layer."""

    key: str
    title: str
    provider: str
    kind: str  # "vector" or "raster"
    dataset_id: str | None = None  # BIOS "ds582", where applicable
    #: Layer name inside a multi-layer archive. None means "the only layer".
    layer: str | None = None
    #: Attribute fields that describe the *original* geometry and therefore stop
    #: being true the moment a feature is clipped. Recomputed on output.
    geometry_fields: tuple[str, ...] = ()
    notes: str = ""
    #: Licence verified out of band and recorded here. Anything found in the
    #: archive's own metadata document wins over this at runtime, because that
    #: travels with the bytes. Empty means "not verified" and will surface as
    #: unknown rather than being guessed.
    license: str = ""
    #: Publisher's stated limits on use. Printed during extraction when set,
    #: because a constraint nobody reads protects nobody.
    use_constraints: str = ""
    #: "ready"      wired up and fetchable unattended
    #: "manual"     published behind a form; needs --local-archive
    #: "unverified" declared, but not confirmed against the publisher - either
    #:              its download URL is unknown, or its archive holds a choice
    #:              nobody has made. Resolving one raises rather than guessing.
    #: Only "ready" datasets are included when no --datasets list is given, so
    #: a batch run can never half-succeed on a source that needs a human.
    status: str = "ready"
    #: Why a dataset is not "ready", in the words a user should read when they
    #: ask for it by name. A status with no reason attached is re-litigated
    #: every time someone meets it, because the decision lived in a transcript.
    status_reason: str = ""
    landing_url: str = ""


@dataclass
class ResolvedSource:
    """A confirmed, fetchable archive plus everything needed to pin it."""

    dataset: Dataset
    url: str
    bytes: int | None = None
    last_modified: str | None = None
    etag: str | None = None
    resolved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def as_dict(self) -> dict:
        return {
            "key": self.dataset.key,
            "title": self.dataset.title,
            "provider": self.dataset.provider,
            "kind": self.dataset.kind,
            "dataset_id": self.dataset.dataset_id,
            "url": self.url,
            "bytes": self.bytes,
            "last_modified": self.last_modified,
            "etag": self.etag,
            "resolved_at": self.resolved_at,
        }


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
# Keys are stable CLI names. Titles match what the CDFW Marine Viewer legend
# prints, so a layer seen in the viewer can be found here by eye.

DATASETS: dict[str, Dataset] = {
    # ---- CDFW BIOS, vector ------------------------------------------------
    "mpa": Dataset(
        key="mpa",
        title="California Marine Protected Areas",
        provider="bios",
        kind="vector",
        dataset_id="ds582",
        geometry_fields=("Acres", "Hectares", "Shape_Area", "Shape_Leng", "AREA_SQMI"),
        license="CC-BY (Creative Commons Attribution) - attribution required",
        use_constraints=(
            "This dataset is not intended for navigational use or defining legal "
            "boundaries. The authoritative boundaries are those in California Code "
            "of Regulations Title 14 section 632."
        ),
        notes="SMR, SMCA, SMRMA, SMP and special closures.",
    ),
    "mpa-coords": Dataset(
        key="mpa-coords",
        title="Marine Protected Areas Coordinates",
        provider="bios",
        kind="vector",
        dataset_id="ds3207",
        notes="Boundary corner points as published in regulation.",
    ),
    "state-waters": Dataset(
        key="state-waters",
        title="Three Nautical Mile State Maritime Limit",
        provider="bios",
        kind="vector",
        dataset_id="ds3158",
        geometry_fields=("Shape_Leng", "LENGTH"),
        status="unverified",
        status_reason=(
            "ds3158.zip holds this limit twice and nobody has chosen which is\n"
            "meant:\n"
            "    ds3158.gdb      the 3 nm line, 8 MultiLineString features\n"
            "    ds3158_alt.gdb  all state water as one polygon, 5888 sq mi\n"
            "Both open, so this is an unmade decision rather than a bug. It has\n"
            "stayed unmade because a jurisdictional boundary carries almost\n"
            "nothing for a buoy study: the polygon clipped to a study box is a\n"
            "near-solid fill, and whether the line lands in the box at all\n"
            "depends entirely on the west padding. Decided 2026-08-07, issue #12.\n"
            "Wiring it up is a slice that starts by choosing line or polygon on\n"
            "purpose."
        ),
        landing_url="https://filelib.wildlife.ca.gov/Public/BDB/GIS/BIOS/metadata/DS3158.html",
        notes="Jurisdictional, not ecological. See status_reason before wiring up.",
    ),
    "shoreline": Dataset(
        key="shoreline",
        title="Shoreline Types",
        provider="bios",
        kind="vector",
        dataset_id="ds3115",
        geometry_fields=("Shape_Leng", "LENGTH", "Miles", "Kilometers"),
        notes="Beaches, coastal marsh, hardened shores, rocky shores, tidal flats.",
    ),
    "saline-wetlands": Dataset(
        key="saline-wetlands",
        title="Saline Wetlands - ACE",
        provider="bios",
        kind="vector",
        dataset_id="ds2864",
        geometry_fields=("Acres", "Hectares", "Shape_Area"),
    ),
    "benthic-substrate": Dataset(
        key="benthic-substrate",
        title="Predicted Nearshore Benthic Substrates of California",
        provider="bios",
        kind="vector",
        dataset_id="ds3091",
        geometry_fields=("Acres", "Hectares", "Shape_Area", "Shape_Leng"),
        notes="Hard/soft prediction.",
    ),
    # ---- CDFW BIOS, raster ------------------------------------------------
    "kelp-persistence": Dataset(
        key="kelp-persistence",
        title="Kelp Persistence [ds3151]",
        provider="bios",
        kind="raster",
        dataset_id="ds3151",
        notes=(
            "5 m grid, count of years kelp canopy was observed across the "
            "2002-2016 survey series. Subject overlaps kelp-density-extract, "
            "but the source is unrelated: that toolkit reads Landsat biomass "
            "from EDI, this one reads CDFW's rasterised aerial survey product."
        ),
    ),
    # ---- PMEP (external) --------------------------------------------------
    "cmecs-substrate": Dataset(
        key="cmecs-substrate",
        title="West Coast Nearshore CMECS Substrate Habitat",
        provider="pmep",
        kind="vector",
        layer="Substrate",
        geometry_fields=("Shape_Area", "Shape_Leng", "Hectares", "Acres"),
        status="manual",
        notes=(
            "Version 2.1, June 2025. Zipped file geodatabase, download gated "
            "behind an email registration form."
        ),
        landing_url="https://www.pacificfishhabitat.org/data/nearshore-cmecs-substrate-habitat/",
    ),
    "cmecs-quality": Dataset(
        key="cmecs-quality",
        title="West Coast Nearshore CMECS Substrate Data Quality",
        provider="pmep",
        kind="vector",
        layer="DataQuality",
        status="manual",
        notes="Companion layer shipped inside the same geodatabase as cmecs-substrate.",
        landing_url="https://www.pacificfishhabitat.org/data/nearshore-cmecs-substrate-habitat/",
    ),
    # ---- USGS / FEMA reference layers ------------------------------------
    "watersheds": Dataset(
        key="watersheds",
        title="WBD Watersheds (HUC4 through HUC12)",
        provider="usgs",
        kind="vector",
        status="unverified",
        notes="National Map staged Watershed Boundary Dataset, region 18 covers California.",
        landing_url="https://prd-tnm.s3.amazonaws.com/index.html?prefix=StagedProducts/Hydrography/WBD/",
    ),
    "quads-24k": Dataset(
        key="quads-24k",
        title="USGS 24K Quads",
        provider="usgs",
        kind="vector",
        status="unverified",
        landing_url="https://www.usgs.gov/programs/national-geospatial-program/national-map",
    ),
    "stream-gages": Dataset(
        key="stream-gages",
        title="California Stream Gages",
        provider="usgs",
        kind="vector",
        status="unverified",
        landing_url="https://waterdata.usgs.gov/nwis",
    ),
    "flood-hazard": Dataset(
        key="flood-hazard",
        title="Flood Hazard Areas (FEMA NFHL)",
        provider="fema",
        kind="vector",
        status="unverified",
        notes="Published per county; San Diego County is FIPS 06073.",
        landing_url="https://msc.fema.gov/portal/advanceSearch",
    ),
}


def get(key: str) -> Dataset:
    """Look up a dataset by CLI key, listing the alternatives when it is wrong."""
    try:
        return DATASETS[key]
    except KeyError:
        raise CatalogError(
            "unknown dataset %r. Known datasets:\n  %s"
            % (key, "\n  ".join(sorted(DATASETS)))
        ) from None


def resolve_keys(spec: str | None) -> list[str]:
    """Turn a --datasets value into a validated list of keys.

    ``None`` or ``"all"`` means every dataset whose download is wired up;
    unverified ones must be asked for by name so they fail visibly rather than
    breaking an otherwise good run.
    """
    if spec is None or spec.strip().lower() == "all":
        return [k for k, d in DATASETS.items() if d.status == "ready"]
    keys = [p.strip() for p in re.split(r"[,\s]+", spec.strip()) if p.strip()]
    if not keys:
        raise CatalogError("--datasets was given but empty")
    for k in keys:
        get(k)  # raises with the full list if unknown
    return keys


# --------------------------------------------------------------------------
# BIOS resolution
# --------------------------------------------------------------------------


def bios_bucket(dataset_id: str) -> str:
    """Directory a BIOS dataset lives in: ds3151 -> '3100_3199'.

    This is a hypothesis. ``resolve_bios`` confirms it by listing the directory
    before returning a URL.
    """
    m = re.fullmatch(r"ds(\d+)", dataset_id.strip().lower())
    if not m:
        raise CatalogError(
            f"{dataset_id!r} is not a BIOS dataset id; expected the form 'ds3151'"
        )
    n = int(m.group(1))
    lo = (n // 100) * 100
    return f"{lo}_{lo + 99}"


def _open(url: str, method: str = "GET", timeout: int = 60):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": user_agent()})
    return urllib.request.urlopen(req, timeout=timeout)


def list_bios_directory(bucket: str, timeout: int = 60) -> dict[str, str]:
    """Map ``ds1234.zip`` -> absolute URL for everything in a bucket directory.

    The library serves a plain HTML index. Rather than depend on its exact
    flavour, every ``href`` ending in ``.zip`` is taken; size and date come from
    an HTTP HEAD later, which is authoritative regardless of how the index is
    rendered.
    """
    url = f"{BIOS_ROOT}/{bucket}/"
    try:
        with _open(url, timeout=timeout) as resp:
            if resp.status != 200:
                raise CatalogError(f"listing {url} returned HTTP {resp.status}")
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise CatalogError(
            f"could not list {url}: HTTP {exc.code}. The BIOS library layout may "
            "have changed; browse it in a browser to check."
        ) from exc
    except urllib.error.URLError as exc:
        raise CatalogError(f"could not reach {url}: {exc.reason}") from exc

    found: dict[str, str] = {}
    for href in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.I):
        name = href.rsplit("/", 1)[-1]
        if name.lower().endswith(".zip"):
            found[name.lower()] = url + name
    return found


def resolve_bios(dataset: Dataset, timeout: int = 60) -> ResolvedSource:
    """Confirm a BIOS archive exists and read its size and modification date."""
    if not dataset.dataset_id:
        raise CatalogError(f"{dataset.key} has no BIOS dataset id")

    bucket = bios_bucket(dataset.dataset_id)
    listing = list_bios_directory(bucket, timeout=timeout)
    want = f"{dataset.dataset_id.lower()}.zip"

    if want not in listing:
        nearby = sorted(listing)[:20]
        raise CatalogError(
            "%s is not in %s/%s/.\n"
            "The bucket contains %d archives%s\n"
            "Check the dataset id, or browse %s/%s/ directly."
            % (
                want,
                BIOS_ROOT,
                bucket,
                len(listing),
                (", starting: " + ", ".join(nearby)) if nearby else ".",
                BIOS_ROOT,
                bucket,
            )
        )

    url = listing[want]
    size: int | None = None
    last_modified = None
    etag = None
    try:
        with _open(url, method="HEAD", timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            size = int(length) if length and length.isdigit() else None
            last_modified = resp.headers.get("Last-Modified")
            etag = resp.headers.get("ETag")
    except (urllib.error.HTTPError, urllib.error.URLError):
        # The listing already proved existence; a HEAD failure costs us the pin
        # metadata but should not block the download.
        pass

    return ResolvedSource(
        dataset=dataset,
        url=url,
        bytes=size,
        last_modified=last_modified,
        etag=etag,
    )


def resolve(dataset: Dataset, timeout: int = 60) -> ResolvedSource:
    """Resolve any registered dataset to a concrete, confirmed download."""
    # Status is checked before provider, because "unverified" is a statement
    # about the dataset and not about whether a URL can be found. A BIOS
    # archive that resolves perfectly well and then holds an unmade choice
    # would otherwise fail three stages later, on a symptom rather than the
    # reason, and with nothing on screen about the decision behind it.
    if dataset.status == "unverified":
        raise CatalogError(
            "%s (%s) is declared but not wired up (status=unverified).\n%s%s"
            % (
                dataset.key,
                dataset.title,
                dataset.status_reason or
                "Its download has not been confirmed against the publisher.",
                f"\nStart from {dataset.landing_url}" if dataset.landing_url else "",
            )
        )

    if dataset.provider == "bios":
        return resolve_bios(dataset, timeout=timeout)

    if dataset.provider == "pmep":
        raise ManualDownloadRequired(
            "%s (%s) is published as a zipped file geodatabase behind an email "
            "registration form, so it cannot be downloaded unattended.\n"
            "Download it once from:\n    %s\n"
            "then pass the archive with:\n"
            "    --local-archive %s=<path to the .zip>\n"
            "It will be cached and clipped exactly like an automatic source."
            % (dataset.key, dataset.title, dataset.landing_url, dataset.key),
            landing_url=dataset.landing_url,
        )

    raise CatalogError(
        "%s (provider %r) is declared but its download is not wired up yet "
        "(status=%s). Start from %s"
        % (dataset.key, dataset.provider, dataset.status, dataset.landing_url or "the publisher")
    )


def metadata_url(dataset: Dataset) -> str | None:
    """CDFW metadata page for a BIOS dataset, if the id looks like one."""
    if dataset.provider != "bios" or not dataset.dataset_id:
        return None
    m = re.fullmatch(r"ds(\d+)", dataset.dataset_id.strip().lower())
    return BIOS_METADATA.format(n=m.group(1)) if m else None
