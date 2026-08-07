"""Reading the shared studies directory.

WHERE STUDIES LIVE
------------------
One level *above* this repository, in ``la-jolla-buoy/studies/``. Each study is
a snapshot of one site over one time window, created by ``station-data-extract``
and then written into, side by side, by several tools that each own one
subdirectory:

    la-jolla-buoy/
      studies/20260807T1913Z__session/
        study.json          <- shared, tool-agnostic. Written at creation.
        station-data/       <- the station-pull tool's namespace
        cudem/              <- cudem-extract's namespace
        marine-bios/        <- OURS. Nothing else writes here.
      marine-bios-extract/  <- this repo (code only)

WHAT THIS MODULE DOES
---------------------
Reads ``study.json`` and nothing else, apart from the one mutation described
below. The station list, the site, the time window and the study's own status
belong to the tool that created them.

The single write is :func:`record_producer`, which appends (or replaces) our
entry in the ``producers`` list. Every other key in the file is left exactly as
it was found, byte for byte.

Product planning is deliberately absent. ``cudem-extract``'s reader plans its
seven fixed products; ours vary with which datasets were selected, so that logic
would have to be wrong here to be shared.

WHY THIS IS A PRIVATE COPY
--------------------------
It is the third copy of a ``study.json`` reader in this family, and
``cudem-extract``'s ADR 0001 asks for the decision to be revisited when a third
appears. It was revisited; see ``docs/adr/0001-this-repo-reads-studies-itself.md``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

STUDIES_DIRNAME = "studies"
STUDY_META_NAME = "study.json"

#: Our namespace inside a study. Matches the ``station-data`` convention rather
#: than an underscored variant, so a study folder reads as one convention.
PRODUCER = "marine-bios"

STATUS_OK = "ok"
STATUS_INCOMPLETE = "incomplete"
STATUS_FAILED = "failed"


class StudyError(Exception):
    """A study cannot be found, named unambiguously, or used."""


def repo_root() -> Path:
    """This repository's root, derived from where this file sits."""
    return Path(__file__).resolve().parents[2]


def default_studies_root(root: Path | str | None = None) -> Path:
    """``<repo>/../studies`` - one level up, shared with the sibling tools."""
    base = Path(root) if root is not None else repo_root()
    return base.resolve().parent / STUDIES_DIRNAME


def default_cache_dir() -> Path:
    """The repository cache, not a per-study one.

    Seven studies of the same coastline should not cost seven copies of a
    151 MB archive, so the cache is anchored to the checkout rather than to
    whatever directory the command happened to be run from.
    """
    return repo_root() / ".cache"


# --------------------------------------------------------------------------
# stations
# --------------------------------------------------------------------------


class Station:
    """One observing location named in a study.

    A station may be listed without ever having had a position recorded -
    ``yellow_buoy`` is exactly that in every study written so far, despite being
    the subject. Absent coordinates are a fact about the study, not an error,
    but they are never silently dropped either: an unpositioned station is
    named on the console and recorded in the producer entry.
    """

    def __init__(self, name: str, lon=None, lat=None, role: str | None = None) -> None:
        self.name = name
        self.lon = None if lon is None else float(lon)
        self.lat = None if lat is None else float(lat)
        self.role = role or "unknown"

    @property
    def positioned(self) -> bool:
        return self.lon is not None and self.lat is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Station {self.name} {self.lon},{self.lat} {self.role}>"


# --------------------------------------------------------------------------
# studies
# --------------------------------------------------------------------------


class Study:
    """One study directory, as far as this toolkit needs to understand it.

    Construct via :func:`load_study`. A study whose metadata will not parse
    still becomes a ``Study``, with ``error`` set, so it can be listed and
    explained rather than vanishing without a reason.
    """

    def __init__(
        self,
        path: Path | str,
        study_id: str,
        label: str = "",
        created_utc: str = "",
        status: str = "",
        stations=(),
        error: str | None = None,
        our_status: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.study_id = study_id
        self.label = label or study_id
        self.created_utc = created_utc or ""
        self.status = status or ""
        self.stations = list(stations)
        self.error = error
        #: What ``study.json`` says about our last run here, if anything. Read
        #: at load time so a listing can mark rows without a second pass.
        self.our_status = our_status

    # ----------------------------------------------------------- locations

    @property
    def meta_path(self) -> Path:
        return self.path / STUDY_META_NAME

    @property
    def producer_dir(self) -> Path:
        return self.path / PRODUCER

    @property
    def has_products(self) -> bool:
        return self.producer_dir.is_dir()

    # ------------------------------------------------------------ geometry

    @property
    def positioned(self) -> list[Station]:
        return [st for st in self.stations if st.positioned]

    @property
    def skipped(self) -> list[Station]:
        """Stations that cannot contribute to the box."""
        return [st for st in self.stations if not st.positioned]

    @property
    def usable(self) -> bool:
        return self.error is None and bool(self.positioned)

    @property
    def unusable_reason(self) -> str | None:
        if self.error:
            return f"{STUDY_META_NAME} unreadable: {self.error}"
        if not self.stations:
            return f"no stations listed in {STUDY_META_NAME}"
        if not self.positioned:
            return "no station in this study has a position"
        return None

    def envelope(self) -> tuple[float, float, float, float] | None:
        """Tightest (west, south, east, north) around the positioned stations.

        Role is deliberately ignored. A station named in a study is a place the
        study cares about, so the extraction should cover it - and a role
        vocabulary that grows later can then never silently shrink the box.
        """
        pts = self.positioned
        if not pts:
            return None
        lons = [st.lon for st in pts]
        lats = [st.lat for st in pts]
        return (min(lons), min(lats), max(lons), max(lats))

    def skipped_records(self) -> list[dict]:
        """The unpositioned stations, in the shape the producer entry records."""
        return [
            {
                "name": st.name,
                "role": st.role,
                "reason": f"no lon/lat in {STUDY_META_NAME}",
            }
            for st in self.skipped
        ]

    # ----------------------------------------------------------- rendering

    @property
    def created_short(self) -> str:
        return self.created_utc[:16].replace("T", " ") if self.created_utc else "-"

    def station_summary(self) -> str:
        """'4/5 st', or '0/3 st' when nothing is placed."""
        return f"{len(self.positioned)}/{len(self.stations)} st"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Study {self.study_id}>"


# --------------------------------------------------------------------------
# finding and loading
# --------------------------------------------------------------------------


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_study(path: Path | str) -> Study:
    """Read one study. Never raises: an unreadable one comes back with ``error``.

    Cheap by construction - ``study.json`` only, never an output file.
    """
    path = Path(path)
    study_id = path.name or path.resolve().name
    meta_path = path / STUDY_META_NAME

    if not meta_path.is_file():
        return Study(path, study_id, error=f"no {STUDY_META_NAME}")

    try:
        meta = _read_json(meta_path)
    except (ValueError, OSError) as exc:
        return Study(path, study_id, error=str(exc))

    if not isinstance(meta, dict):
        return Study(path, study_id, error="not a JSON object")

    site = meta.get("site") or {}
    raw = site.get("stations") or {}
    stations: list[Station] = []
    if isinstance(raw, dict):
        for name, s in raw.items():
            s = s if isinstance(s, dict) else {}
            stations.append(Station(name, s.get("lon"), s.get("lat"), s.get("role")))

    ours = None
    for p in meta.get("producers") or []:
        if isinstance(p, dict) and p.get("name") == PRODUCER:
            ours = p.get("status") or STATUS_INCOMPLETE

    return Study(
        path=path,
        study_id=meta.get("study_id") or study_id,
        label=meta.get("label") or study_id,
        created_utc=meta.get("created_utc") or "",
        status=meta.get("status") or "",
        stations=stations,
        our_status=ours,
    )


def list_studies(root: Path | str | None = None) -> list[Study]:
    """Every study under ``root``, newest first.

    A directory that looks like a study but will not parse is *included*, with
    its error attached. A study that disappears from a listing with no
    explanation is the failure mode this repo family works hardest to avoid.
    """
    root = Path(root) if root is not None else default_studies_root()
    if not root.is_dir():
        return []

    out: list[Study] = []
    for name in sorted(os.listdir(root)):
        if name.startswith("."):
            continue
        d = root / name
        if not d.is_dir():
            continue
        # Anything holding a study.json, or already holding our output.
        if not (d / STUDY_META_NAME).is_file() and not (d / PRODUCER).is_dir():
            continue
        out.append(load_study(d))

    out.sort(key=lambda s: (s.created_utc or "", s.study_id), reverse=True)
    return out


def require_studies_root(root: Path | str | None = None) -> Path:
    """The studies root, or a clear error naming the path that was expected."""
    path = Path(root) if root is not None else default_studies_root()
    if not path.is_dir():
        raise StudyError(
            f"no studies directory at {path}\n"
            "Studies are created by station-data-extract and shared between the "
            "sibling toolkits. Point --studies-root at one if yours lives "
            "somewhere else."
        )
    return path


def resolve_study(arg, studies: list[Study]) -> Study:
    """Find the one study ``arg`` names, or explain why it cannot.

    Accepts a study id, a label, the literal ``latest``, or a unique fragment of
    either. An ambiguous fragment is an error listing the candidates rather than
    a silent pick of the newest: naming something almost uniquely should not
    quietly select for you.
    """
    if not studies:
        raise StudyError("no studies found")

    key = str(arg).strip()
    if key.lower() == "latest":
        return studies[0]

    for s in studies:  # exact id first, then exact label
        if s.study_id == key:
            return s
    exact = [s for s in studies if s.label == key]
    if len(exact) == 1:
        return exact[0]

    low = key.lower()
    hits = [s for s in studies if low in s.study_id.lower() or low in s.label.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise StudyError(
            f"no study matches {arg!r}. Available:\n"
            + "\n".join(f"  {s.study_id}" for s in studies)
        )
    raise StudyError(
        f"{arg!r} matches {len(hits)} studies; name one exactly:\n"
        + "\n".join(f"  {s.study_id}" for s in hits)
    )


# --------------------------------------------------------------------------
# leaving a record in the shared metadata
# --------------------------------------------------------------------------


def record_producer(study: Study, entry: dict) -> Path:
    """Add or replace our entry in ``study.json``'s ``producers`` list.

    This is the one mutation we make to a file we do not own, and it is confined
    to that list: the site, the stations, the time window and the study's own
    status are left exactly as the creating tool wrote them. The list exists
    precisely so a study can accumulate several tools' output, and a study
    holding marine layers that does not say so misdescribes itself.

    The document is re-serialised with the same two-space indent and trailing
    newline the creating tool uses, and ``json`` preserves key order, so every
    other line of the file survives unchanged.
    """
    meta_path = Path(study.meta_path)
    try:
        meta = _read_json(meta_path)
    except (ValueError, OSError) as exc:
        raise StudyError(f"cannot update {meta_path}: {exc}") from exc
    if not isinstance(meta, dict):
        raise StudyError(f"cannot update {meta_path}: not a JSON object")

    producers = meta.get("producers")
    if not isinstance(producers, list):
        producers = []
    producers = [
        p for p in producers if not (isinstance(p, dict) and p.get("name") == PRODUCER)
    ]
    producers.append(entry)
    meta["producers"] = producers

    tmp = meta_path.with_name(meta_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, meta_path)
    return meta_path


def producer_status(study: Study) -> str | None:
    """What ``study.json`` currently says about our last run here."""
    meta_path = Path(study.meta_path)
    if not meta_path.is_file():
        return None
    try:
        meta = _read_json(meta_path)
    except (ValueError, OSError):
        return None
    if not isinstance(meta, dict):
        return None
    for p in meta.get("producers") or []:
        if isinstance(p, dict) and p.get("name") == PRODUCER:
            return p.get("status")
    return None
