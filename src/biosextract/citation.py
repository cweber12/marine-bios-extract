"""Citation, licence and use-constraint extraction.

BIOS asks that a layer be cited with five elements: title, originator,
publication date, that it came from BIOS, and the date you accessed it. Only the
first and last of those are knowable from the download itself, so the rest is
read out of the metadata document that ships inside the archive.

Reading the metadata from the cached archive rather than from CDFW's website is
deliberate on two counts. It costs no extra HTTP request, and it describes the
exact bytes that produced the output rather than whatever the website says
today. The web metadata page is not a reliable substitute in any case:
``DS3151.html`` resolves while ``DS582.html`` returns 404.

Nothing here invents a value. When the originator or publication date cannot be
found, the citation says so and points at the metadata page, because a
confidently wrong attribution is worse than an obviously incomplete one.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

#: Value used when a field genuinely could not be determined. Chosen to be
#: conspicuous in a bibliography rather than to read as a real value.
UNKNOWN = "[unknown - see metadata]"

#: Where a citation field came from. The distinction matters to a reader
#: checking one: an archive value can be re-derived from the download they have,
#: a registry value can only be re-checked against the page a person read.
ARCHIVE = "archive"
REGISTRY = "registry"


def _localname(tag: str) -> str:
    """Strip any XML namespace. FGDC is unnamespaced, ISO 19139 is not."""
    return tag.rsplit("}", 1)[-1].lower()


def _text_of(elem) -> str:
    """Value of an element, whether it holds text directly or wraps it.

    FGDC puts the value straight in the element (``<origin>CDFW</origin>``).
    ISO 19139 wraps it (``<gmd:organisationName><gco:CharacterString>CDFW
    </gco:CharacterString></gmd:organisationName>``). Reading ``.text`` alone
    silently returns whitespace for every ISO document, so fall through to the
    subtree when the element itself is empty.
    """
    direct = (elem.text or "").strip()
    if direct:
        return direct
    return " ".join(t.strip() for t in elem.itertext() if t and t.strip())


def _first_text(root, *names: str) -> str | None:
    """First non-empty value of any descendant whose local name matches.

    Matching on local name means the same code reads FGDC and ISO 19139 without
    a namespace map that would go stale.
    """
    wanted = {n.lower() for n in names}
    for elem in root.iter():
        if _localname(elem.tag) in wanted:
            text = _text_of(elem)
            if text:
                return text
    return None


def _all_text(root, *names: str) -> list[str]:
    wanted = {n.lower() for n in names}
    out = []
    for elem in root.iter():
        if _localname(elem.tag) in wanted:
            text = _text_of(elem)
            if text and text not in out:
                out.append(text)
    return out


#: A tag, as opposed to a less-than sign in prose. Requires a name character
#: straight after the bracket, so "depth < 30 m" survives and "<DIV …>" does not.
_TAG_RE = re.compile(r"<\s*/?[a-zA-Z][^>]*>")


def _tidy(text: str | None, limit: int = 2000) -> str | None:
    """Collapse a metadata value into one readable line.

    Esri's ArcGIS metadata stores constraint text as an escaped HTML *fragment*:
    the licence statement arrives as ``<DIV STYLE="text-align:Left;"><SPAN>…``
    once the XML layer has decoded its entities. Printed as-is that is markup on
    a console rather than a sentence, and a use constraint nobody can read
    protects nobody - so tags come out here, where every dialect benefits, and
    any entity left behind by a doubly-escaped document is decoded after.
    """
    if not text:
        return None
    stripped = _TAG_RE.sub(" ", text) if _TAG_RE.search(text) else text
    collapsed = re.sub(r"\s+", " ", html.unescape(stripped)).strip()
    return collapsed[:limit] if collapsed else None


def format_pubdate(raw: str | None) -> str | None:
    """FGDC publication dates are usually YYYYMMDD, sometimes YYYY or YYYYMM."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    try:
        if len(digits) >= 8:
            return f"{digits[:4]}, {months[int(digits[4:6]) - 1]}. {int(digits[6:8])}"
        if len(digits) >= 6:
            return f"{digits[:4]}, {months[int(digits[4:6]) - 1]}."
        if len(digits) == 4:
            return digits
    except (ValueError, IndexError):
        pass
    return _tidy(raw, 40)


@dataclass
class Citation:
    """Everything needed to credit a layer properly."""

    key: str
    title: str
    originator: str = UNKNOWN
    publication_date: str = UNKNOWN
    repository: str = "Biogeographic Information and Observation System (BIOS)"
    publisher: str = "California Department of Fish and Wildlife"
    accessed: str = ""
    url: str = ""
    sha256: str = ""
    license: str = UNKNOWN
    use_constraints: str = ""
    access_constraints: str = ""
    metadata_source: str = ""  # which archive member supplied the values
    metadata_page: str = ""
    #: field name -> ARCHIVE or REGISTRY. A reader checking a citation needs to
    #: know whether a value came out of the bytes in hand or off a page someone
    #: read once, because only one of those can be re-derived from the download.
    field_sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return UNKNOWN not in (self.originator, self.publication_date)

    def apa(self) -> str:
        """BIOS's documented APA form: Originator. (Date). Title. Publisher. Repository."""
        return (
            f"{self.originator}. ({self.publication_date}). {self.title}. "
            f"{self.publisher}. {self.repository}. Retrieved {self.accessed}, from {self.url}"
        )

    def mla(self) -> str:
        return (
            f'{self.originator}. "{self.title}." {self.publication_date}. '
            f"{self.repository}. Accessed {self.accessed}."
        )

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "originator": self.originator,
            "publication_date": self.publication_date,
            "repository": self.repository,
            "publisher": self.publisher,
            "accessed": self.accessed,
            "url": self.url,
            "sha256": self.sha256,
            "license": self.license,
            "use_constraints": self.use_constraints,
            "access_constraints": self.access_constraints,
            "metadata_source": self.metadata_source,
            "metadata_page": self.metadata_page,
            "field_sources": dict(self.field_sources),
            "complete": self.complete,
            "apa": self.apa(),
            "mla": self.mla(),
            "warnings": self.warnings,
        }

    def text_block(self) -> str:
        """Human-readable stanza for the ATTRIBUTION file."""
        lines = [
            f"{self.title}",
            f"  Licence:     {self.license}",
            f"  Cite as:     {self.apa()}",
            f"  Source:      {self.url}",
            f"  Accessed:    {self.accessed}",
            f"  SHA-256:     {self.sha256 or UNKNOWN}",
        ]
        if self.use_constraints:
            lines.append(f"  Use limits:  {self.use_constraints}")
        if self.access_constraints:
            lines.append(f"  Access:      {self.access_constraints}")
        if self.metadata_page:
            lines.append(f"  Metadata:    {self.metadata_page}")
        for warning in self.warnings:
            lines.append(f"  NOTE:        {warning}")
        return "\n".join(lines)


def _originator(root) -> str | None:
    """Who to credit, in the order CDFW themselves prescribe.

    Their `Citing BIOS <https://wildlife.ca.gov/Data/BIOS/Citing-BIOS>`_ page is
    the authority: "The Originator will be listed in the Publication section of
    the metadata, if one exists. If there isn't an Originator, use the Primary
    Person listed in the Point of Contact section."

    So the tiers are asked in turn rather than all at once - a single lookup
    would let document order decide the answer, and in an Esri document the
    programme credit sits before the contact. ``idCredit`` is last because it
    names who funded and produced the work ("California Coastal and Seafloor
    Mapping Project, ...") rather than who is cited for it; it is a better
    answer than ``[unknown]``, and a worse one than either name above it.
    """
    return (
        _first_text(root, "origin", "organisationName", "citedResponsibleParty")
        or _first_text(root, "rpindname", "rporgname")
        or _first_text(root, "idcredit")
    )


def _parse_metadata_xml(blob: bytes) -> dict:
    """Pull citation fields out of an FGDC, ISO 19139 or Esri ArcGIS document.

    Three dialects, one set of lookups: matching is on local element names, so
    no namespace map has to be kept current. Esri's format is the one ArcGIS Pro
    exports by default and is what several BIOS layers now ship - ds3091 carries
    a complete citation in it, and was reported as needing hand-finishing purely
    because nothing here knew the element names.
    """
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return {}

    return {
        "originator": _tidy(_originator(root), 200),
        "pubdate": _first_text(root, "pubdate", "publicationDate", "date"),
        # Esri names the title `resTitle`, so ds3091's own title was never read
        # and the registry label stood in for it - silently, since a label is
        # not a missing value and nothing warned.
        "title": _tidy(_first_text(root, "title", "resTitle"), 300),
        "use_constraints": _tidy(
            " ".join(
                _all_text(
                    root, "useconst", "useLimitation", "otherConstraints", "useLimit"
                )
            )
        ),
        "access_constraints": _tidy(
            " ".join(_all_text(root, "accconst", "accessConstraints")), 500
        ),
        "abstract": _tidy(_first_text(root, "abstract"), 500),
    }


def from_archive(
    archive: Path,
    key: str,
    title: str,
    url: str = "",
    sha256: str = "",
    accessed: str | None = None,
    known_license: str = "",
    known_constraints: str = "",
    metadata_page: str = "",
    known_originator: str = "",
    known_pubdate: str = "",
) -> Citation:
    """Build a citation for a dataset from the metadata inside its archive.

    The ``known_*`` arguments are values a person verified out of band and
    recorded in the registry. Anything found in the archive metadata is
    preferred over them, since it travels with the bytes and stays true across a
    republication that a pin would silently outlive.

    They exist because most BIOS archives carry no metadata document at all -
    ds582, ds3115 and ds3158 ship data and nothing else - so for those layers a
    pin is not a shortcut past reading the bytes, it is the only thing there
    will ever be. Which source won is recorded per field, so the manifest can
    say so rather than leaving a reader to guess.
    """
    citation = Citation(
        key=key,
        title=title,
        accessed=accessed or date.today().isoformat(),
        url=url,
        sha256=sha256,
        license=known_license or UNKNOWN,
        use_constraints=known_constraints,
        metadata_page=metadata_page,
    )

    parsed: dict = {}
    member_used = ""
    try:
        with zipfile.ZipFile(archive) as zf:
            candidates = [
                n for n in zf.namelist()
                if n.lower().endswith((".xml", ".shp.xml"))
                and "__MACOSX" not in n
            ]
            # Prefer a document that actually carries a citation block.
            for name in sorted(candidates, key=lambda n: (len(n), n)):
                try:
                    found = _parse_metadata_xml(zf.read(name))
                except (KeyError, OSError):
                    continue
                if found.get("originator") or found.get("pubdate"):
                    parsed, member_used = found, name
                    break
                if found and not parsed:
                    parsed, member_used = found, name
    except (zipfile.BadZipFile, OSError) as exc:
        citation.warnings.append(f"could not read archive metadata: {exc}")

    if known_license:
        citation.field_sources["license"] = REGISTRY
    if known_constraints:
        citation.field_sources["use_constraints"] = REGISTRY

    if parsed:
        citation.metadata_source = member_used
        if parsed.get("originator"):
            citation.originator = parsed["originator"]
            citation.field_sources["originator"] = ARCHIVE
        if parsed.get("pubdate"):
            citation.publication_date = format_pubdate(parsed["pubdate"]) or UNKNOWN
            citation.field_sources["publication_date"] = ARCHIVE
        # The archive's own title is authoritative over our registry label.
        if parsed.get("title"):
            citation.title = parsed["title"]
            citation.field_sources["title"] = ARCHIVE
        if parsed.get("use_constraints"):
            citation.use_constraints = parsed["use_constraints"]
            citation.field_sources["use_constraints"] = ARCHIVE
        if parsed.get("access_constraints"):
            citation.access_constraints = parsed["access_constraints"]

    # Only now, and only into the gaps. An archive that named an originator has
    # already had the last word; a pin never overrides it.
    if citation.originator == UNKNOWN and known_originator:
        citation.originator = known_originator
        citation.field_sources["originator"] = REGISTRY
    if citation.publication_date == UNKNOWN and known_pubdate:
        citation.publication_date = format_pubdate(known_pubdate) or UNKNOWN
        citation.field_sources["publication_date"] = REGISTRY

    if not citation.complete:
        citation.warnings.append(
            "originator and/or publication date could not be read from the archive; "
            "complete the citation by hand before publishing"
            + (f" - see {metadata_page}" if metadata_page else "")
        )
    return citation


HEADER = """\
ATTRIBUTION AND CITATION
========================

These files were derived from third-party published datasets. Every source
below requires attribution when the derived data is published, presented or
redistributed. Copy the relevant citation into your work.

Extracted by : marine-bios-extract {version}
Extracted on : {generated}
Bounding box : {bbox}  (WEST,SOUTH,EAST,NORTH, WGS84)

IMPORTANT: geometries here have been clipped to that bounding box. Fields
prefixed `orig_` describe the ORIGINAL uncut feature and no longer describe the
geometry they sit beside. Use `area_m2` / `length_m` for anything computed from
what is actually in these files.

"""

FOOTER = """

----------------------------------------------------------------------
This file was generated automatically. It records what was downloaded and
what the publishers ask in return; it is not legal advice. Where a use
constraint matters to a decision, confirm it with the publisher directly.
"""


@dataclass
class AuditRow:
    """What is known about one dataset's citation, and what is not."""

    key: str
    title: str
    status: str
    license: str = UNKNOWN
    originator: str = UNKNOWN
    publication_date: str = UNKNOWN
    #: Where a person read the registry's values, and when.
    verified_from: str = ""
    verified_on: str = ""
    #: Cached archive inspected, if there was one to inspect.
    archive: str = ""
    #: Member the citation was read from, if the archive carried one.
    metadata_source: str = ""
    #: Things a person has to go and do. Empty means nothing is outstanding.
    problems: list[str] = field(default_factory=list)
    #: Limits on what this audit could check - not work, but not silence either.
    notes: list[str] = field(default_factory=list)

    @property
    def clear(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "status": self.status,
            "license": self.license,
            "originator": self.originator,
            "publication_date": self.publication_date,
            "archive": self.archive,
            "verified_from": self.verified_from,
            "verified_on": self.verified_on,
            "metadata_source": self.metadata_source,
            "problems": list(self.problems),
            "notes": list(self.notes),
            "clear": self.clear,
        }


def audit(datasets: dict, cache_dir: Path) -> list[AuditRow]:
    """Report what is verified about every dataset's citation, and what is not.

    Reads **only what is already on disk**. A licence audit that downloads half
    a gigabyte is one nobody runs, and for most BIOS layers the archive has
    nothing to say anyway: three of the four cached on 2026-08-08 ship no
    metadata document at all, so their licence exists only on a web page a
    person has to read.

    That asymmetry is why a row separates *problems* from *notes*. A problem is
    work outstanding - nobody has verified this licence. A note is a limit on
    what could be checked here, such as an archive that has never been
    downloaded; the answer to that is a fetch, not a verification, and treating
    it as a failure would mean the audit could never pass on a cold cache.

    ``datasets`` is passed in rather than read from the registry so a test can
    hand it a known mix, and so a caller can audit a subset.
    """
    from .fetch import cached_archive

    rows: list[AuditRow] = []
    for key in sorted(datasets):
        dataset = datasets[key]
        row = AuditRow(
            key=key,
            title=dataset.title,
            status=dataset.status,
            license=dataset.license or UNKNOWN,
            verified_from=dataset.verified_from,
            verified_on=dataset.verified_on,
        )

        archive = cached_archive(cache_dir, key)
        if archive is None:
            row.notes.append("no cached archive, so its metadata was not read")
            # The pins are still what a run would print, so report them. No
            # incomplete-citation problem is raised either way: without the
            # archive there is no way to know whether it would have supplied
            # the rest, and guessing in either direction would be a lie.
            row.originator = dataset.known_originator or UNKNOWN
            row.publication_date = format_pubdate(dataset.known_pubdate) or UNKNOWN
        else:
            row.archive = archive.name
            cite = from_archive(
                archive,
                key=key,
                title=dataset.title,
                known_license=dataset.license,
                known_constraints=dataset.use_constraints,
                known_originator=dataset.known_originator,
                known_pubdate=dataset.known_pubdate,
            )
            row.originator = cite.originator
            row.publication_date = cite.publication_date
            row.metadata_source = cite.metadata_source
            row.license = cite.license
            if not cite.metadata_source:
                row.notes.append(
                    "the archive carries no metadata document, so nothing about "
                    "it can be confirmed from the bytes"
                )
            if not cite.complete:
                missing = [
                    name
                    for name, value in (
                        ("originator", cite.originator),
                        ("publication date", cite.publication_date),
                    )
                    if value == UNKNOWN
                ]
                row.problems.append("citation incomplete: no " + " and no ".join(missing))

        if row.license == UNKNOWN:
            row.problems.append("no licence recorded, and none read from the archive")
        elif dataset.license and not dataset.verified_from:
            # A recorded licence nobody can trace is the failure mode this whole
            # exercise is about. It reads as settled, so nobody re-checks it,
            # and it is indistinguishable from a guess made years ago.
            row.problems.append(
                "licence recorded with no provenance: nothing says which page it "
                "was read from, so nobody can re-check it"
            )

        rows.append(row)
    return rows


def unverified(rows: list[AuditRow], status: str = "ready") -> list[AuditRow]:
    """Rows with outstanding work, among datasets of ``status``.

    The verdict covers wired-up datasets only. A gated or unverified source is
    not expected to be citable yet - counting it would make the audit fail for
    a reason nobody can act on, and an alarm that is always ringing is one
    nobody hears.
    """
    return [r for r in rows if r.status == status and not r.clear]


def write_attribution_file(
    citations: list[Citation], path: Path, bbox_text: str, version: str, generated: str
) -> Path:
    """Write the paste-ready attribution file that ships beside the outputs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = HEADER.format(version=version, generated=generated, bbox=bbox_text)
    body += ("\n" + "-" * 70 + "\n\n").join(c.text_block() for c in citations)
    body += FOOTER
    path.write_text(body, encoding="utf-8")
    return path
