"""The ``bios study`` run: one box, one study folder, named stages.

A study already knows where it is. This module turns that into analysis-ready
files inside the study itself: it reads the study's stations, builds the tightest
rectangle around the positioned ones, pads it by four independent distances,
resolves and fetches the selected archives, clips them to that one rectangle,
writes them into ``<study>/marine-bios/`` with a manifest and an attribution
file, and appends an entry to the study's ``producers`` list.

STAGES AND SEAMS
----------------
The run is assembled from named stages rather than written as one function,
because three later features have to plug into the middle of it without
rewriting it::

    resolve the study
    derive the box from the stations and the padding
    --- BOX_SEAM ------------------- the expansion rule inserts itself here
    plan what this run will do
    --- PLAN_SEAM ------------------ the re-run policy inserts itself here
    execute: fetch, clip, write
    record: manifest, attribution, producer entry

A seam is a list of stages. A stage takes the :class:`RunState` and returns
``(state, report)``: the state it wants the rest of the run to see, and a
structured report of what it did, which is filed under its name in
``state.reports`` and ends up in the manifest. A later slice adds a module and
registers it at a seam with :func:`register_box_stage` or
:func:`register_plan_stage`; it does not edit the body of :func:`run`.

Two rules make that work. A stage must return the state (it may return the same
object, mutated), and a stage that did nothing must still return a report saying
so - a seam that is silent when it declines is indistinguishable from a seam
nobody registered at.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No terminal interaction. Every answer arrives as a flag, and where one is
missing the run says which flag is missing rather than asking. The picker is a
later slice; this module must still work when it exists, which is why it is
built around a request object rather than around prompts.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import (
    __version__,
    catalog,
    citation as citation_mod,
    fetch as fetch_mod,
    manifest as manifest_mod,
    studies,
)
from .archive import ArchiveError, select as select_payload
from .bbox import BBox, BBoxError
from .outputs import VECTOR_WRITERS

#: Files a run always writes beside the layers.
MANIFEST_NAME = "manifest.json"
ATTRIBUTION_NAME = "ATTRIBUTION.txt"

TOOL_VERSION = f"marine-bios-extract {__version__}"


class StudyRunError(RuntimeError):
    """The run cannot proceed, with a message a person can act on."""


def interactive() -> bool:
    """True only when *both* streams are a terminal.

    Both, not either. An interactive stdin with a redirected stdout is a real
    configuration - ``bios study | tee run.log`` - and anything drawn there
    sprays control codes into a file nobody can read afterwards. This command
    writes no escape sequences at all, so the check exists to decide whether it
    is allowed to *ask* anything.
    """
    try:
        return bool(
            sys.stdin is not None
            and sys.stdout is not None
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        )
    except (AttributeError, ValueError):  # closed or replaced streams
        return False


# --------------------------------------------------------------------------
# what a run was asked to do
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Padding:
    """Four independent margins in kilometres, one per side of the box."""

    north_km: float
    south_km: float
    east_km: float
    west_km: float

    def as_dict(self) -> dict:
        return {
            "north_km": self.north_km,
            "south_km": self.south_km,
            "east_km": self.east_km,
            "west_km": self.west_km,
        }

    def budget(self, side: str) -> float:
        """The growth budget for one side, for a stage registered at BOX_SEAM.

        The expansion rule's cap is the padding already chosen for that side: no
        new number is invented, and the cap scales with the intent the caller
        already expressed.
        """
        return float(getattr(self, f"{side}_km"))


@dataclass(frozen=True)
class Request:
    """Every answer a run needs, and nothing about how it was obtained.

    Constructed from flags today and from the picker in a later slice. Keeping
    it a plain value means the run is drivable from a test with no terminal.
    """

    studies_root: Path
    study: str
    datasets: list[str]
    padding: Padding
    formats: list[str]
    local_archives: dict[str, Path] = field(default_factory=dict)
    cache_dir: Path = field(default_factory=studies.default_cache_dir)
    output_crs: str | None = None
    resolution: float | None = None
    whole_features: bool = False
    refresh: bool = False
    force: bool = False
    dry_run: bool = False
    assume_yes: bool = False
    keep_going: bool = False
    timeout: int = 600
    max_bytes: int = fetch_mod.DEFAULT_MAX_BYTES

    def as_dict(self) -> dict:
        return {
            "studies_root": str(self.studies_root),
            "study": self.study,
            "datasets": list(self.datasets),
            "padding_km": self.padding.as_dict(),
            "formats": list(self.formats),
            "local_archives": {k: str(v) for k, v in self.local_archives.items()},
            "cache_dir": str(self.cache_dir),
            "whole_features": self.whole_features,
            "output_crs": self.output_crs or "EPSG:4326",
        }


@dataclass
class RunState:
    """Everything the stages read and write, threaded through the run."""

    request: Request
    study: studies.Study | None = None
    envelope: tuple[float, float, float, float] | None = None
    #: The box as the padding alone produced it, kept so a stage at BOX_SEAM can
    #: be seen to have moved it.
    derived_box: BBox | None = None
    #: The box every output actually describes.
    box: BBox | None = None
    #: One entry per dataset, in run order. See :func:`stage_plan`.
    plan: list[dict] = field(default_factory=list)
    #: Structured report per stage, keyed by stage name. Goes into the manifest.
    reports: dict[str, dict] = field(default_factory=dict)
    #: The subset of those contributed by stages registered at a seam. Kept
    #: apart because these go into the study's own metadata as well, and
    #: study.json should carry what a later slice decided, not a transcript of
    #: the built-in stages that the manifest already holds.
    seam_reports: dict[str, dict] = field(default_factory=dict)

    layers: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    citations: list = field(default_factory=list)
    failures: int = 0

    @property
    def out_dir(self) -> Path:
        """``<study>/marine-bios``. Our namespace; nothing else writes here."""
        if self.study is None:  # pragma: no cover - stage order guarantees this
            raise StudyRunError("no study resolved yet")
        return self.study.producer_dir


#: A stage: takes the run state, returns it plus a structured report.
Stage = Callable[[RunState], "tuple[RunState, dict]"]

#: **Seam 1**, between "box derived from stations and padding" and "box final".
#: A stage here may move :attr:`RunState.box`; ``derived_box`` is left alone so
#: the difference stays visible. Cluster expansion registers here.
BOX_SEAM: list[tuple[str, Stage]] = []

#: **Seam 2**, between "plan what this run will do" and "execute it". A stage
#: here may drop entries from :attr:`RunState.plan`, mark them as skipped, or
#: refuse the run outright by raising :class:`StudyRunError`. The re-run policy
#: registers here.
PLAN_SEAM: list[tuple[str, Stage]] = []


def register_box_stage(name: str, stage: Stage) -> None:
    """Register a stage at :data:`BOX_SEAM`. Idempotent by name."""
    _register(BOX_SEAM, name, stage)


def register_plan_stage(name: str, stage: Stage) -> None:
    """Register a stage at :data:`PLAN_SEAM`. Idempotent by name."""
    _register(PLAN_SEAM, name, stage)


def _register(seam: list, name: str, stage: Stage) -> None:
    for i, (existing, _) in enumerate(seam):
        if existing == name:
            seam[i] = (name, stage)
            return
    seam.append((name, stage))


def apply_seam(seam: list[tuple[str, Stage]], state: RunState) -> RunState:
    """Run every stage registered at a seam, in registration order."""
    for name, stage in seam:
        state, report = stage(state)
        report = report if report is not None else {"applied": False}
        state.reports[name] = report
        state.seam_reports[name] = report
    return state


def apply_stage(name: str, stage: Stage, state: RunState) -> RunState:
    """Run one built-in stage and file its report under ``name``."""
    state, report = stage(state)
    state.reports[name] = report
    return state


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------


def stage_resolve_study(state: RunState) -> tuple[RunState, dict]:
    """Find the study named, and refuse one that cannot yield a box."""
    root = studies.require_studies_root(state.request.studies_root)
    found = studies.list_studies(root)
    if not found:
        raise StudyRunError(
            f"no studies under {root}\n"
            "Studies are created by station-data-extract."
        )
    study = studies.resolve_study(state.request.study, found)

    if not study.usable:
        raise StudyRunError(
            f"{study.study_id} cannot be used: {study.unusable_reason}\n"
            "A box is derived from the stations that have a position, so a study "
            "with none of them has nothing to extract for."
        )

    state.study = study
    print(f"Study:      {study.study_id}  ({study.label})")
    print(f"            {study.path}")
    return state, {
        "studies_root": str(root),
        "study_id": study.study_id,
        "label": study.label,
        "created_utc": study.created_utc,
        "candidates": len(found),
    }


def stage_derive_box(state: RunState) -> tuple[RunState, dict]:
    """Envelope of the positioned stations, padded four ways."""
    study = state.study
    assert study is not None  # stage order
    envelope = study.envelope()
    padding = state.request.padding

    try:
        box = BBox.from_envelope(
            envelope,
            north_km=padding.north_km,
            south_km=padding.south_km,
            east_km=padding.east_km,
            west_km=padding.west_km,
        )
    except BBoxError as exc:
        raise StudyRunError(f"could not build a box for {study.study_id}: {exc}") from exc

    state.envelope = envelope
    state.derived_box = box
    state.box = box

    used = sorted(st.name for st in study.positioned)
    print(f"Stations:   {len(used)} positioned - {', '.join(used)}")
    for st in study.skipped:
        # Named, never buried. For the reference study this is the subject buoy
        # itself, which is exactly the fact a run must not swallow.
        print(f"            skipped {st.name} ({st.role}): no lon/lat in study.json")
    print(
        "Padding:    N %g km, S %g km, E %g km, W %g km"
        % (padding.north_km, padding.south_km, padding.east_km, padding.west_km)
    )
    print(f"Box:        {box}")
    if box.spans_utm_zones > 1:
        print(
            f"            note: the box spans {box.spans_utm_zones} UTM zones; areas "
            f"are measured in {box.utm_epsg} and stretch toward the edges."
        )
    return state, {
        "envelope_wsen": list(envelope),
        "padding_km": padding.as_dict(),
        "box_wsen": list(box.as_tuple()),
        "stations_used": used,
        "stations_skipped": study.skipped_records(),
    }


def stage_plan(state: RunState) -> tuple[RunState, dict]:
    """Resolve each dataset and say what this run will produce.

    Resolution is a directory listing and a HEAD - no payload - so the plan can
    name the real URL and size of everything that is about to be downloaded.
    A dataset supplied with ``--local-archive`` is not resolved at all, which is
    what makes a complete run possible with no network.
    """
    request = state.request
    out_dir = state.out_dir
    entries: list[dict] = []

    print(f"\nOutput:     {out_dir}")
    print(f"Datasets:   {', '.join(request.datasets)}")
    print(f"Formats:    {', '.join(request.formats)}")
    print(f"Cache:      {request.cache_dir}")
    print()

    for key in request.datasets:
        dataset = catalog.get(key)
        entry: dict = {
            "key": key,
            "title": dataset.title,
            "kind": dataset.kind,
            "files": [f.name for f in planned_files(out_dir, dataset, request.formats)],
            "source": None,
            "error": None,
        }
        if key in request.local_archives:
            entry["source"] = {"url": f"local:{request.local_archives[key]}"}
            print(f"  {key:20} local archive {request.local_archives[key]}")
        else:
            try:
                src = catalog.resolve(dataset, timeout=request.timeout)
            except catalog.CatalogError as exc:
                entry["error"] = str(exc)
                state.failures += 1
                state.warnings.append(f"{key}: {exc}")
                print(f"  {key:20} UNAVAILABLE - {str(exc).splitlines()[0]}")
                if not request.keep_going:
                    raise StudyRunError(
                        f"{key} could not be resolved:\n{exc}\n"
                        "Pass --keep-going to extract the rest anyway."
                    ) from exc
            else:
                entry["resolved"] = src
                entry["source"] = src.as_dict()
                size = f"{src.bytes / 1e6:.1f} MB" if src.bytes else "size unknown"
                print(f"  {key:20} {size:>12}  {src.last_modified or 'date unknown'}")
        entries.append(entry)

    state.plan = entries
    return state, {
        "out_dir": str(out_dir),
        "datasets": [e["key"] for e in entries],
        "files": [f for e in entries for f in e["files"]],
        "unavailable": [e["key"] for e in entries if e["error"]],
    }


def planned_files(out_dir: Path, dataset: catalog.Dataset, formats: list[str]) -> list[Path]:
    """The files one dataset will produce.

    Named by dataset key with no prefix: the directory already provides the
    namespace, so ``marine-bios/mpa.geojson`` says everything ``extract_mpa``
    said and reads better beside ``station-data/`` and ``cudem/``.
    """
    if dataset.kind == "raster":
        return [out_dir / f"{dataset.key}.tif"]
    return [out_dir / f"{dataset.key}{VECTOR_WRITERS[f][1]}" for f in formats]


def confirm(state: RunState) -> bool:
    """Ask before writing, unless told not to, and never hang waiting.

    Without a terminal there is nobody to answer, so the run stops with the flag
    that would have let it through rather than blocking on a prompt no CI job
    can see.
    """
    if state.request.assume_yes or state.request.dry_run:
        return True
    if not interactive():
        raise StudyRunError(
            "this run would write into "
            f"{state.out_dir} and there is no terminal to confirm it on.\n"
            "Pass --yes to accept the plan above, or --dry-run to stop here."
        )
    answer = input("Write these files? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def stage_execute(state: RunState) -> tuple[RunState, dict]:
    """Fetch, clip and write every dataset in the plan, to the one box."""
    request = state.request
    box = state.box
    out_dir = state.out_dir
    assert box is not None  # stage order
    out_dir.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()
    written: list[Path] = []

    for entry in state.plan:
        key = entry["key"]
        if entry.get("error") or entry.get("skipped"):
            continue
        dataset = catalog.get(key)
        print(f"\n{key} - {dataset.title}")
        try:
            archive = _acquire(state, dataset, entry)
            payload = select_payload(archive.path, dataset.kind, dataset.layer)
            print(f"    reading {payload}")

            cite = citation_mod.from_archive(
                archive.path,
                key=key,
                title=dataset.title,
                url=archive.source.url,
                sha256=archive.sha256,
                accessed=run_date,
                known_license=dataset.license,
                known_constraints=dataset.use_constraints,
                metadata_page=catalog.metadata_url(dataset) or "",
            )
            state.citations.append(cite)
            if cite.use_constraints:
                # Printed while the run happens, because a constraint left in a
                # file nobody opens protects nobody.
                print(f"    use constraint: {cite.use_constraints}")
            for note in cite.warnings:
                state.warnings.append(f"{key}: {note}")
                print(f"    note: {note}")

            provenance = _provenance(cite, box)
            layer = {
                "key": key,
                "title": dataset.title,
                "source": archive.as_dict(),
                "payload": payload.member,
                "format": payload.fmt,
                "citation": cite.as_dict(),
            }

            if dataset.kind == "vector":
                paths, clip_info = _write_vector(state, dataset, payload, provenance)
            else:
                paths, clip_info = _write_raster(state, dataset, payload, provenance)
            layer["clip"] = clip_info
            entry["written"] = [p.name for p in paths]
            written.extend(paths)
            state.layers.append(layer)

        except catalog.ManualDownloadRequired as exc:
            state.failures += 1
            state.warnings.append(f"{key}: manual download required")
            entry["error"] = "manual download required"
            print(f"    skipped - {exc}")
        except (
            catalog.CatalogError,
            fetch_mod.FetchError,
            ArchiveError,
            BBoxError,
        ) as exc:
            state.failures += 1
            state.warnings.append(f"{key}: {exc}")
            entry["error"] = str(exc)
            print(f"    FAILED: {exc}")
            if not request.keep_going:
                break
        except Exception as exc:  # noqa: BLE001
            state.failures += 1
            state.warnings.append(f"{key}: {type(exc).__name__}: {exc}")
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"    FAILED: {type(exc).__name__}: {exc}")
            if not request.keep_going:
                break

    stale = _stale_files(out_dir, written)
    if stale:
        print()
        if request.force:
            for path in stale:
                print(f"  removing {path.name} - not written by this run (--force)")
                path.unlink()
        else:
            print(
                "  %d file(s) in %s were not written by this run and may describe a "
                "different box:" % (len(stale), out_dir.name)
            )
            for path in stale:
                print(f"    {path.name}")
            print("  pass --force to remove them.")
            state.warnings.append(
                "%d pre-existing file(s) left in place: %s"
                % (len(stale), ", ".join(p.name for p in stale))
            )

    return state, {
        "written": [p.name for p in written],
        "stale": [p.name for p in stale],
        "stale_removed": bool(stale) and request.force,
        "failures": state.failures,
    }


def _acquire(state: RunState, dataset: catalog.Dataset, entry: dict):
    """The archive for one dataset: adopted from disk, or fetched and cached."""
    request = state.request
    key = dataset.key
    if key in request.local_archives:
        src = catalog.ResolvedSource(
            dataset=dataset, url=f"local:{request.local_archives[key]}", bytes=None
        )
        return fetch_mod.adopt_local(
            src, request.local_archives[key], request.cache_dir, verbose=True
        )
    src = entry.get("resolved") or catalog.resolve(dataset, timeout=request.timeout)
    return fetch_mod.fetch(
        src,
        request.cache_dir,
        refresh=request.refresh,
        max_bytes=request.max_bytes,
        timeout=request.timeout,
        verbose=True,
    )


def _provenance(cite, box: BBox) -> dict:
    return {
        "title": cite.title,
        "attribution": cite.apa(),
        "license": cite.license,
        "source": cite.url,
        "accessed": cite.accessed,
        "use_constraints": cite.use_constraints,
        "generated_by": (
            f"{TOOL_VERSION}; geometries clipped to {box.as_tuple()} - "
            "orig_* fields describe the uncut feature"
        ),
        "bbox": list(box.as_tuple()),
    }


def _write_vector(state: RunState, dataset, payload, provenance):
    from . import vector as vector_mod

    request = state.request
    box = state.box
    result = vector_mod.clip(
        payload.vsi_path,
        box,
        layer=None,
        geometry_fields=dataset.geometry_fields,
        whole_features=request.whole_features,
        output_crs=request.output_crs or "EPSG:4326",
        # A layer with nothing in the box is an answer worth writing down.
        allow_empty=True,
        verbose=True,
    )
    if result.kept == 0:
        print(
            f"    NOTHING IN THE BOX: {dataset.key} has no features here. Writing "
            "the layer anyway, with a recorded count of zero."
        )
        state.warnings.append(f"{dataset.key}: 0 features in the box")

    paths: list[Path] = []
    for fmt in request.formats:
        writer, ext = VECTOR_WRITERS[fmt]
        path = state.out_dir / f"{dataset.key}{ext}"
        kwargs = {}
        if fmt == "gpkg":
            # A GeoPackage layer name is a SQLite table name; keep the hyphen
            # out of it while the filename still says the dataset key.
            kwargs["layer"] = dataset.key.replace("-", "_")
        try:
            writer(result, path, provenance=provenance, **kwargs)
        except Exception as exc:  # noqa: BLE001
            state.warnings.append(f"{dataset.key}: could not write {fmt}: {exc}")
            print(f"    warning: {fmt} failed: {exc}")
            continue
        state.outputs.append(
            manifest_mod.describe_output(path, fmt, f"{dataset.title} clipped to the box")
        )
        paths.append(path)
        print(f"    wrote {path.name}")
    return paths, result.as_dict()


def _write_raster(state: RunState, dataset, payload, provenance):
    from . import raster as raster_mod

    request = state.request
    result = raster_mod.clip(
        payload.vsi_path,
        state.box,
        output_crs=request.output_crs,
        resolution=request.resolution,
        verbose=True,
    )
    path = state.out_dir / f"{dataset.key}.tif"
    raster_mod.write_geotiff(result, path, provenance=provenance)
    state.outputs.append(
        manifest_mod.describe_output(path, "geotiff", f"{dataset.title} clipped to the box")
    )
    print(f"    wrote {path.name}")
    return [path], result.as_dict()


def _stale_files(out_dir: Path, written: list[Path]) -> list[Path]:
    """Files already in our directory that this run did not produce."""
    if not out_dir.is_dir():
        return []
    keep = {p.name for p in written} | {MANIFEST_NAME, ATTRIBUTION_NAME}
    return sorted(
        (p for p in out_dir.iterdir() if p.is_file() and p.name not in keep),
        key=lambda p: p.name,
    )


def stage_record(state: RunState) -> tuple[RunState, dict]:
    """Manifest, attribution file, and our entry in the study's metadata."""
    request = state.request
    study = state.study
    box = state.box
    assert study is not None and box is not None  # stage order

    document = manifest_mod.build(
        box,
        {
            **request.as_dict(),
            "study_id": study.study_id,
            "study_path": str(study.path),
            "envelope_wsen": list(state.envelope or ()),
            "measure_crs": box.utm_epsg,
            "accessed": date.today().isoformat(),
            "stages": state.reports,
        },
        state.layers,
        state.outputs,
        state.warnings,
    )

    # Written before the manifest is finalised so it, too, is hashed as an
    # output of the run.
    attribution_path = citation_mod.write_attribution_file(
        state.citations,
        state.out_dir / ATTRIBUTION_NAME,
        bbox_text=str(box),
        version=__version__,
        generated=document["generated_at"],
    )
    state.outputs.append(
        manifest_mod.describe_output(
            attribution_path, "attribution", "required citations and use constraints"
        )
    )
    document["outputs"] = state.outputs
    manifest_path = manifest_mod.write(document, state.out_dir / MANIFEST_NAME)

    entry = producer_entry(state)
    meta_path = studies.record_producer(study, entry)

    print(f"\nAttribution: {attribution_path}")
    print(f"Manifest:    {manifest_path}")
    print(f"Recorded in: {meta_path}")
    return state, {
        "manifest": manifest_path.name,
        "attribution": attribution_path.name,
        "producer_status": entry["status"],
    }


def producer_entry(state: RunState) -> dict:
    """Our record in the shared ``study.json``.

    Everything needed to rebuild the box from the study alone: the rectangle,
    the four padding values, which datasets were extracted, which files came
    out, and which stations shaped the box - including the ones that could not,
    with the reason.
    """
    study = state.study
    box = state.box
    assert study is not None and box is not None  # stage order

    products: dict[str, str] = {}
    for entry in state.plan:
        for name in entry["files"]:
            if entry.get("error"):
                products[name] = f"failed: {entry['error'].splitlines()[0]}"
            elif name in (entry.get("written") or []):
                products[name] = "ok"
            else:
                products[name] = "missing"

    return {
        "name": studies.PRODUCER,
        "dir": studies.PRODUCER,
        "tool_version": TOOL_VERSION,
        "status": studies.STATUS_OK if not state.failures else studies.STATUS_INCOMPLETE,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bbox_wsen": list(box.as_tuple()),
        "pad_km": state.request.padding.as_dict(),
        "datasets": [e["key"] for e in state.plan],
        "products": products,
        "stations_used": sorted(st.name for st in study.positioned),
        "stations_skipped": study.skipped_records(),
        # Only what a stage registered at a seam decided - the expansion that
        # moved the box, the re-run policy that removed a file. The built-in
        # stages' reports are in the manifest, which is where a transcript of a
        # run belongs.
        **({"stages": state.seam_reports} if state.seam_reports else {}),
    }


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


def run(request: Request) -> int:
    """Drive the whole thing. Returns a process exit code."""
    state = RunState(request=request)

    state = apply_stage("study", stage_resolve_study, state)
    state = apply_stage("box", stage_derive_box, state)
    state = apply_seam(BOX_SEAM, state)
    state = apply_stage("plan", stage_plan, state)
    state = apply_seam(PLAN_SEAM, state)

    if request.dry_run:
        print("\nDry run: nothing was downloaded, written or recorded.")
        return 0

    if not confirm(state):
        print("Cancelled; nothing was written.")
        return 1

    state = apply_stage("execute", stage_execute, state)

    if not state.layers:
        print("\nNothing was extracted.", file=sys.stderr)
        return 1

    state = apply_stage("record", stage_record, state)
    _summarise(state)
    return 1 if state.failures and not request.keep_going else 0


def _summarise(state: RunState) -> None:
    incomplete = [c.key for c in state.citations if not c.complete]
    if incomplete:
        print(
            "\nCitations needing a hand: %s\n"
            "  Their originator or publication date was not in the archive metadata. "
            "Complete them before publishing." % ", ".join(incomplete)
        )
    licensed = [c for c in state.citations if "CC-BY" in (c.license or "")]
    if licensed:
        print(
            "\n%d layer(s) are CC-BY: attribution is required if you publish, present "
            "or redistribute anything derived from them. The text is in %s."
            % (len(licensed), ATTRIBUTION_NAME)
        )
    empty = [
        layer["key"] for layer in state.layers if layer.get("clip", {}).get("kept") == 0
    ]
    if empty:
        print(
            "\n%d layer(s) had nothing in the box and were written empty: %s"
            % (len(empty), ", ".join(empty))
        )
    print(
        "\n%d layer(s) extracted, %d file(s) written, %d failed."
        % (len(state.layers), len(state.outputs), state.failures)
    )
