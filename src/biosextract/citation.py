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

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

#: Value used when a field genuinely could not be determined. Chosen to be
#: conspicuous in a bibliography rather than to read as a real value.
UNKNOWN = "[unknown - see metadata]"


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


def _tidy(text: str | None, limit: int = 2000) -> str | None:
    if not text:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
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


def _parse_metadata_xml(blob: bytes) -> dict:
    """Pull citation fields out of an FGDC or ISO 19139 document."""
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return {}

    return {
        "originator": _tidy(
            _first_text(root, "origin", "organisationName", "citedResponsibleParty"), 200
        ),
        "pubdate": _first_text(root, "pubdate", "publicationDate", "date"),
        "title": _tidy(_first_text(root, "title"), 300),
        "use_constraints": _tidy(
            " ".join(_all_text(root, "useconst", "useLimitation", "otherConstraints"))
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
) -> Citation:
    """Build a citation for a dataset from the metadata inside its archive.

    ``known_license`` and ``known_constraints`` are values verified out of band
    and recorded in the registry; anything found in the archive metadata is
    preferred over them, since it travels with the bytes.
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

    if parsed:
        citation.metadata_source = member_used
        if parsed.get("originator"):
            citation.originator = parsed["originator"]
        if parsed.get("pubdate"):
            citation.publication_date = format_pubdate(parsed["pubdate"]) or UNKNOWN
        # The archive's own title is authoritative over our registry label.
        if parsed.get("title"):
            citation.title = parsed["title"]
        if parsed.get("use_constraints"):
            citation.use_constraints = parsed["use_constraints"]
        if parsed.get("access_constraints"):
            citation.access_constraints = parsed["access_constraints"]

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
