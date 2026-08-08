"""Command line interface.

Nothing about a study area is a default in this file. Every value comes from the
command line or a ``--config`` TOML file, and the command line always wins.

Commands
--------
``list``     what this toolkit knows how to fetch, and whether it is wired up
``resolve``  confirm the published archives without downloading them
``extract``  the real work: fetch, clip, write, record
``study``    the same work, for a study in the shared studies directory: the
             box comes from the study's stations and the output lands inside it
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import (
    __version__,
    catalog,
    citation as citation_mod,
    fetch as fetch_mod,
    manifest as manifest_mod,
    studyrun as studyrun_mod,
)
from .archive import ArchiveError, select as select_payload
from .bbox import BBox, BBoxError
from .outputs import VECTOR_WRITERS


def _load_config(path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        raise SystemExit("reading a config file needs Python 3.11 or newer")
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"config file not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"could not parse {path}: {exc}")


def _resolve_bbox(args, cfg: dict) -> BBox:
    """Command line first, then config, then a clear error. Never a default."""
    area = cfg.get("area", {})
    if args.bbox:
        return BBox.parse(args.bbox)
    if args.center:
        lat_s, lon_s = [p.strip() for p in args.center.split(",")]
        radius = args.radius_km or area.get("radius_km")
        if not radius:
            raise SystemExit("--center needs --radius-km (or radius_km in the config)")
        return BBox.from_center(float(lat_s), float(lon_s), float(radius))
    if area.get("bbox"):
        return BBox.parse(area["bbox"])
    if area.get("center"):
        lat_s, lon_s = [p.strip() for p in str(area["center"]).split(",")]
        if not area.get("radius_km"):
            raise SystemExit("config [area].center needs radius_km alongside it")
        return BBox.from_center(float(lat_s), float(lon_s), float(area["radius_km"]))
    raise SystemExit(
        "no study area given. Pass --bbox WEST,SOUTH,EAST,NORTH (both longitudes "
        "negative in California), or --center LAT,LON with --radius-km, or point "
        "--config at a file that sets one."
    )


def _parse_formats(spec: str | None, default: str = "geojson,csv,gpkg") -> list[str]:
    formats = [f.strip().lower() for f in (spec or default).split(",") if f.strip()]
    unknown = [f for f in formats if f not in VECTOR_WRITERS]
    if unknown:
        raise SystemExit(
            f"unknown output format(s) {unknown}. Known: {', '.join(VECTOR_WRITERS)}"
        )
    return formats


def _parse_local_archives(values: list[str] | None) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for item in values or []:
        if "=" not in item:
            raise SystemExit(
                f"--local-archive expects KEY=PATH, got {item!r} "
                "(e.g. --local-archive cmecs-substrate=C:\\downloads\\pmep.zip)"
            )
        key, _, path = item.partition("=")
        catalog.get(key.strip())
        out[key.strip()] = Path(path.strip())
    return out


def cmd_list(args) -> int:
    groups = [
        ("ready", "Fetchable automatically (these run when --datasets is omitted)"),
        ("manual", "Need a one-time manual download, then --local-archive KEY=PATH"),
        ("unverified", "Declared but not wired up yet; asking by name explains why"),
    ]
    for status, heading in groups:
        members = [d for d in catalog.DATASETS.values() if d.status == status]
        if not members:
            continue
        print(f"{heading}:\n")
        for d in members:
            print(
                f"  {d.key:20} {d.kind:7} {d.dataset_id or '-':8} "
                f"{d.title}" + (f"  [{d.provider}]" if status != "ready" else "")
            )
        print()
    print("Run 'resolve <key>' to confirm a published archive without downloading it.")
    return 0


def cmd_network(args) -> int:
    """Print exactly what this tool does over the network, and to whom."""
    print("marine-bios-extract network behaviour")
    print("=" * 38)
    print()
    print(catalog.NETWORK_PROFILE)
    print(f"User-Agent currently sent:\n  {catalog.user_agent()}")
    print()
    print("Hosts contacted:")
    hosts = sorted({catalog.BIOS_ROOT.split("/")[2]})
    for host in hosts:
        print(f"  {host}   (CDFW public file library)")
    print("  nothing else. Gated publishers are never contacted automatically.")
    return 0


def cmd_resolve(args) -> int:
    keys = catalog.resolve_keys(",".join(args.datasets) if args.datasets else None)
    failures = 0
    for key in keys:
        dataset = catalog.get(key)
        try:
            src = catalog.resolve(dataset, timeout=args.timeout)
        except catalog.ManualDownloadRequired as exc:
            print(f"\n{key}: manual download required\n{exc}")
            failures += 1
            continue
        except catalog.CatalogError as exc:
            print(f"\n{key}: FAILED\n  {exc}")
            failures += 1
            continue
        size = f"{src.bytes / 1e6:.1f} MB" if src.bytes else "size unknown"
        print(f"{key:20} {size:>12}  {src.last_modified or 'date unknown'}")
        print(f"{'':20} {src.url}")
    return 1 if failures and not args.keep_going else 0


def cmd_extract(args) -> int:
    cfg = _load_config(args.config) if args.config else {}
    extract_cfg = cfg.get("extract", {})
    out_cfg = cfg.get("output", {})

    bbox = _resolve_bbox(args, cfg)
    keys = catalog.resolve_keys(
        ",".join(args.datasets) if args.datasets else extract_cfg.get("datasets")
    )
    local = _parse_local_archives(args.local_archive)

    formats = _parse_formats(args.formats or out_cfg.get("formats"))

    out_dir = Path(args.out_dir or out_cfg.get("dir") or "output")
    prefix = args.prefix or out_cfg.get("prefix") or "extract"
    cache_dir = Path(args.cache_dir or cfg.get("network", {}).get("cache_dir") or ".cache")
    whole = args.whole_features or bool(extract_cfg.get("whole_features", False))
    output_crs = args.output_crs or extract_cfg.get("crs")
    max_bytes = int(
        (args.max_download_mb or extract_cfg.get("max_download_mb") or 512) * 1024 * 1024
    )

    print(f"Study area: {bbox}")
    if bbox.spans_utm_zones > 1:
        print(
            f"  note: the box spans {bbox.spans_utm_zones} UTM zones; areas are "
            f"measured in {bbox.utm_epsg} and stretch toward the edges."
        )
    print(f"Datasets:   {', '.join(keys)}")
    print(f"Clip mode:  {'whole intersecting features' if whole else 'cut at the box'}")
    print()

    layers: list[dict] = []
    outputs: list[dict] = []
    warnings: list[str] = []
    citations: list[citation_mod.Citation] = []
    run_date = date.today().isoformat()
    failures = 0

    for key in keys:
        dataset = catalog.get(key)
        print(f"{key} - {dataset.title}")
        try:
            if key in local:
                src = catalog.ResolvedSource(
                    dataset=dataset, url=f"local:{local[key]}", bytes=None
                )
                archive = fetch_mod.adopt_local(src, local[key], cache_dir, verbose=True)
            else:
                src = catalog.resolve(dataset, timeout=args.timeout)
                archive = fetch_mod.fetch(
                    src,
                    cache_dir,
                    refresh=args.refresh,
                    max_bytes=max_bytes,
                    verbose=True,
                )

            payload = select_payload(archive.path, dataset.kind, dataset.layer or args.layer)
            print(f"    reading {payload}", flush=True)
            if dataset.read_note:
                print(f"    {dataset.read_note}", flush=True)

            # Citation metadata comes out of the cached archive: no extra
            # request, and it describes the exact bytes in hand.
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
            citations.append(cite)
            if cite.use_constraints:
                print(f"    use constraint: {cite.use_constraints}")
            for note in cite.warnings:
                warnings.append(f"{key}: {note}")
                print(f"    note: {note}")

            provenance = {
                "title": cite.title,
                "attribution": cite.apa(),
                "license": cite.license,
                "source": cite.url,
                "accessed": cite.accessed,
                "use_constraints": cite.use_constraints,
                "generated_by": (
                    f"marine-bios-extract {__version__}; geometries clipped to "
                    f"{bbox.as_tuple()} - orig_* fields describe the uncut feature"
                ),
                "bbox": list(bbox.as_tuple()),
            }

            entry = {
                "key": key,
                "title": dataset.title,
                "source": archive.as_dict(),
                "payload": payload.member,
                "format": payload.fmt,
                "citation": cite.as_dict(),
            }

            if dataset.kind == "vector":
                from . import vector as vector_mod

                result = vector_mod.clip(
                    payload.vsi_path,
                    bbox,
                    layer=None,
                    geometry_fields=dataset.geometry_fields,
                    whole_features=whole,
                    output_crs=output_crs or "EPSG:4326",
                    verbose=True,
                )
                entry["clip"] = result.as_dict()
                for fmt in formats:
                    writer, ext = VECTOR_WRITERS[fmt]
                    path = out_dir / f"{prefix}_{key.replace('-', '_')}{ext}"
                    try:
                        writer(result, path, provenance=provenance)
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"{key}: could not write {fmt}: {exc}")
                        print(f"    warning: {fmt} failed: {exc}")
                        continue
                    outputs.append(
                        manifest_mod.describe_output(path, fmt, f"{dataset.title} clipped to the box")
                    )
                    print(f"    wrote {path.name}")
            else:
                from . import raster as raster_mod

                result = raster_mod.clip(
                    payload.vsi_path,
                    bbox,
                    output_crs=output_crs,
                    resolution=args.resolution or extract_cfg.get("resolution"),
                    verbose=True,
                )
                entry["clip"] = result.as_dict()
                path = out_dir / f"{prefix}_{key.replace('-', '_')}.tif"
                raster_mod.write_geotiff(result, path, provenance=provenance)
                outputs.append(
                    manifest_mod.describe_output(path, "geotiff", f"{dataset.title} clipped to the box")
                )
                print(f"    wrote {path.name}")

            layers.append(entry)

        except catalog.ManualDownloadRequired as exc:
            failures += 1
            warnings.append(f"{key}: manual download required")
            print(f"    skipped - {exc}\n")
            continue
        except (
            catalog.CatalogError,
            fetch_mod.FetchError,
            ArchiveError,
            BBoxError,
        ) as exc:
            failures += 1
            warnings.append(f"{key}: {exc}")
            print(f"    FAILED: {exc}\n")
            if not args.keep_going:
                break
            continue
        except Exception as exc:  # noqa: BLE001
            failures += 1
            warnings.append(f"{key}: {type(exc).__name__}: {exc}")
            print(f"    FAILED: {type(exc).__name__}: {exc}\n")
            if not args.keep_going:
                break
            continue
        print()

    if not layers:
        print("Nothing was extracted.", file=sys.stderr)
        return 1

    document = manifest_mod.build(
        bbox,
        {
            "datasets": keys,
            "whole_features": whole,
            "output_crs": output_crs or "EPSG:4326",
            "formats": formats,
            "measure_crs": bbox.utm_epsg,
            "cache_dir": str(cache_dir),
            "accessed": run_date,
        },
        layers,
        outputs,
        warnings,
    )

    # The attribution file is written before the manifest is finalised so that
    # it, too, is hashed and recorded as an output.
    attribution_path = citation_mod.write_attribution_file(
        citations,
        out_dir / f"{prefix}_ATTRIBUTION.txt",
        bbox_text=str(bbox),
        version=__version__,
        generated=document["generated_at"],
    )
    outputs.append(
        manifest_mod.describe_output(
            attribution_path, "attribution", "required citations and use constraints"
        )
    )
    document["outputs"] = outputs
    manifest_path = manifest_mod.write(document, out_dir / f"{prefix}_manifest.json")

    print(f"Attribution: {attribution_path}")
    print(f"Manifest:    {manifest_path}")
    incomplete = [c.key for c in citations if not c.complete]
    if incomplete:
        print(
            "\nCitations needing a hand: %s\n"
            "  Their originator or publication date was not in the archive metadata. "
            "Complete them before publishing." % ", ".join(incomplete)
        )
    licensed = [c for c in citations if "CC-BY" in (c.license or "")]
    if licensed:
        print(
            "\n%d layer(s) are CC-BY: attribution is required if you publish, present "
            "or redistribute anything derived from them. The text is in %s."
            % (len(licensed), attribution_path.name)
        )
    print(
        f"\n{len(layers)} layer(s) extracted, {len(outputs)} file(s) written, {failures} failed."
    )
    return 1 if failures and not args.keep_going else 0


def _padding(args) -> "studyrun_mod.Padding":
    """Four margins in kilometres, from --pad-km and the four per-side flags.

    There is no default. A margin is a statement about the study area, and this
    repo does not bake study areas into code - a run that did not say how much
    coastline it wanted should say so, not silently pick a number that then
    turns up in a manifest looking deliberate.
    """
    sides = ("north", "south", "east", "west")
    given = {s: getattr(args, f"pad_{s}_km") for s in sides}
    if args.pad_km is None and all(v is None for v in given.values()):
        raise SystemExit(
            "no padding given. The box is the tightest rectangle around the "
            "study's positioned stations, which is routinely a few hundred "
            "metres across, so a margin is required:\n"
            "    --pad-km 10                       10 km on every side\n"
            "    --pad-km 10 --pad-west-km 2       10 km, except 2 km inland\n"
            "    --pad-north-km 5 --pad-south-km 5 --pad-east-km 20 --pad-west-km 1"
        )
    base = args.pad_km
    resolved = {}
    for side in sides:
        value = given[side] if given[side] is not None else base
        if value is None:
            raise SystemExit(
                f"no padding for the {side} side. Give --pad-{side}-km, or "
                "--pad-km to set every side that has no value of its own."
            )
        resolved[f"{side}_km"] = float(value)
    return studyrun_mod.Padding(**resolved)


def cmd_study(args) -> int:
    from . import expansion as expansion_mod, studies as studies_mod

    # Registered here rather than at import, so importing the package does not
    # quietly change what a run does.
    expansion_mod.register()

    request = studyrun_mod.Request(
        studies_root=(
            Path(args.studies_root)
            if args.studies_root
            else studies_mod.default_studies_root()
        ),
        study=args.study,
        datasets=catalog.resolve_keys(
            ",".join(args.datasets) if args.datasets else None
        ),
        padding=_padding(args),
        formats=_parse_formats(args.formats),
        local_archives=_parse_local_archives(args.local_archive),
        cache_dir=Path(args.cache_dir) if args.cache_dir else studies_mod.default_cache_dir(),
        output_crs=args.output_crs,
        resolution=args.resolution,
        whole_features=args.whole_features,
        expand=not args.no_expand,
        expand_budget_km=args.expand_budget_km,
        refresh=args.refresh,
        force=args.force,
        dry_run=args.dry_run,
        assume_yes=args.yes,
        keep_going=args.keep_going,
        timeout=args.timeout,
        max_bytes=int((args.max_download_mb or 512) * 1024 * 1024),
    )
    try:
        return studyrun_mod.run(request)
    except (studies_mod.StudyError, studyrun_mod.StudyRunError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bios",
        description="Extract CDFW BIOS and companion marine GIS layers for a bounding box.",
        epilog=(
            "Bounding boxes are WEST,SOUTH,EAST,NORTH in decimal degrees. In "
            "California both longitudes are negative. Quote the value in "
            "PowerShell, which otherwise splits it into four arguments."
        ),
    )
    p.add_argument("--version", action="version", version=f"marine-bios-extract {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    net = sub.add_parser("network", help="describe every request this tool makes")
    net.set_defaults(func=cmd_network)

    lst = sub.add_parser("list", help="show the dataset registry")
    lst.set_defaults(func=cmd_list)

    res = sub.add_parser("resolve", help="confirm published archives without downloading")
    res.add_argument("datasets", nargs="*", help="dataset keys; omit for all wired-up ones")
    res.add_argument("--timeout", type=int, default=60)
    res.add_argument("--keep-going", action="store_true")
    res.set_defaults(func=cmd_resolve)

    ext = sub.add_parser("extract", help="fetch, clip and write")
    ext.add_argument("--bbox", help="WEST,SOUTH,EAST,NORTH in degrees")
    ext.add_argument("--center", help="LAT,LON to build a box around")
    ext.add_argument("--radius-km", type=float, help="half-width for --center")
    ext.add_argument("--datasets", nargs="*", help="dataset keys; omit for all wired-up ones")
    ext.add_argument("--config", type=Path, help="TOML config; the command line still wins")
    ext.add_argument("--formats", help="geojson,csv,gpkg,kmz,shp (default geojson,csv,gpkg)")
    ext.add_argument("--out-dir", type=Path)
    ext.add_argument("--prefix")
    ext.add_argument("--cache-dir", type=Path)
    ext.add_argument("--output-crs", help="default EPSG:4326")
    ext.add_argument("--resolution", type=float, help="raster output cell size")
    ext.add_argument(
        "--whole-features",
        action="store_true",
        help="keep intersecting features intact instead of cutting them at the box",
    )
    ext.add_argument("--layer", help="name a layer inside a multi-layer archive")
    ext.add_argument(
        "--local-archive",
        action="append",
        metavar="KEY=PATH",
        help="supply a manually downloaded archive for a gated dataset",
    )
    ext.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    ext.add_argument("--max-download-mb", type=float, default=None)
    ext.add_argument("--timeout", type=int, default=600)
    ext.add_argument("--keep-going", action="store_true", help="continue after a dataset fails")
    ext.set_defaults(func=cmd_extract)

    std = sub.add_parser(
        "study",
        help="extract for a study in ../studies, writing into the study folder",
        description=(
            "Build the box from a study's stations, extract the selected layers "
            "into <study>/marine-bios/, and record the run in the study's own "
            "metadata. Every answer is a flag, so the command runs unattended."
        ),
        epilog=(
            "Example:\n"
            "  bios study --study latest --pad-km 10 --datasets mpa shoreline --yes\n"
            "\n"
            "The study can be named by id, by label, by any unique fragment of "
            "either, or as 'latest'. A fragment that matches more than one study "
            "is an error listing them, never a silent pick of the newest."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    std.add_argument("--studies-root", type=Path, help="default ../studies")
    std.add_argument(
        "--study", required=True, help="study id, label, unique fragment, or 'latest'"
    )
    std.add_argument("--datasets", nargs="*", help="dataset keys; omit for all wired-up ones")
    std.add_argument("--pad-km", type=float, help="margin for every side, in km")
    std.add_argument("--pad-north-km", type=float, help="overrides --pad-km on this side")
    std.add_argument("--pad-south-km", type=float, help="overrides --pad-km on this side")
    std.add_argument("--pad-east-km", type=float, help="overrides --pad-km on this side")
    std.add_argument("--pad-west-km", type=float, help="overrides --pad-km on this side")
    std.add_argument("--formats", help="geojson,csv,gpkg,kmz,shp (default geojson,csv,gpkg)")
    std.add_argument("--cache-dir", type=Path, help="default the repository .cache/")
    std.add_argument("--output-crs", help="default EPSG:4326")
    std.add_argument("--resolution", type=float, help="raster output cell size")
    std.add_argument(
        "--whole-features",
        action="store_true",
        help="keep intersecting features intact instead of cutting them at the box",
    )
    std.add_argument(
        "--no-expand",
        action="store_true",
        help=(
            "do not grow the box to whole feature groups; keep the rectangle the "
            "padding produced, even where it cuts a reserve in half"
        ),
    )
    std.add_argument(
        "--expand-budget-km",
        type=float,
        help=(
            "how far each side may grow to capture a whole feature group "
            "(default: the padding chosen for that side)"
        ),
    )
    std.add_argument(
        "--local-archive",
        action="append",
        metavar="KEY=PATH",
        help="supply a manually downloaded archive for a gated dataset",
    )
    std.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    std.add_argument(
        "--force",
        action="store_true",
        help="remove files already in the study's marine-bios directory that this run does not write",
    )
    std.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and show the box and the plan, then stop without writing",
    )
    std.add_argument(
        "-y", "--yes", action="store_true", help="do not ask before writing"
    )
    std.add_argument("--max-download-mb", type=float, default=None)
    std.add_argument("--timeout", type=int, default=600)
    std.add_argument("--keep-going", action="store_true", help="continue after a dataset fails")
    std.set_defaults(func=cmd_study)
    return p


#: Options whose value legitimately begins with "-". A western longitude does,
#: and argparse treats any such token as another flag: "--bbox -117.3,32.8,..."
#: fails with "expected one argument" no matter how the shell quotes it, because
#: quoting is stripped before argparse ever sees it. Rewriting the pair to
#: "--bbox=-117.3,..." is the documented workaround; doing it here means nobody
#: has to know that.
_VALUE_FLAGS = frozenset(
    {
        "--bbox",
        "--center",
        "--radius-km",
        "--resolution",
        "--max-download-mb",
        "--pad-km",
        "--pad-north-km",
        "--pad-south-km",
        "--pad-east-km",
        "--pad-west-km",
    }
)


def normalize_argv(argv: list[str]) -> list[str]:
    """Join value-taking flags to a following value that starts with '-'."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if (
            token in _VALUE_FLAGS
            and i + 1 < len(argv)
            and argv[i + 1].startswith("-")
        ):
            out.append(f"{token}={argv[i + 1]}")
            i += 2
            continue
        out.append(token)
        i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    argv = normalize_argv(list(sys.argv[1:] if argv is None else argv))
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BBoxError as exc:
        print(f"Bounding box error: {exc}", file=sys.stderr)
        return 2
    except catalog.CatalogError as exc:
        print(f"Catalog error: {exc}", file=sys.stderr)
        return 2
    except studyrun_mod.StudyRunError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
