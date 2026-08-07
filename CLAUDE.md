# marine-bios-extract — working agreement

**Repo:** `C:\Projects\la-jolla-buoy\marine-bios-extract`
**Purpose:** given a bounding rectangle, produce analysis-ready files for CDFW
BIOS marine layers and their companion datasets.
**Audience:** a CLI coding agent working directly in this repo.

This file follows the same shape as `kelp-density-extract\CLAUDE.md`. Where the
two differ, the difference is deliberate and explained.

---

## 1. Workflow — follow this for every prompt

Identical to the sibling repos, and not optional.

1. **Read back your understanding.** Proportional to the ask. Say explicitly
   what you are *not* going to do when scope is ambiguous.
2. **Offer suggestions when warranted** — a better approach, a hidden cost, a
   correctness risk — *before* implementing. Say nothing if you have nothing.
3. **Wait for confirmation.** Exception: explicit authorization in the prompt
   ("go ahead", "do it") is confirmation for the scope described.
4. **Plan and slice.** Vertical slices, each standing on its own, each with a
   stated acceptance check. Number them and present the list first.
5. **Implement one slice at a time, commit after each.** Run the suite before
   every commit; never commit red. The commit history *is* the change record.

---

## 2. Standing optimizations

| Practice | Why |
|---|---|
| Run `.\run-tests.ps1` before every commit | A red commit poisons the history |
| Smoke-run the CLI after touching it | Tests pass on code that fails to start |
| Batch independent tool calls into one message | Free latency win |
| Write a provenance manifest on every run | Source URL, hash, bbox, git SHA |
| Cache raw downloads under `.cache/` | Re-runs cost nothing and survive outages |
| Fail loudly on ambiguity | See §4 — silent wrong answers are the failure mode |
| Conventional commit prefixes (`feat:`, `fix:`, `test:`, `chore:`, `docs:`) | Scannable history |

---

## 3. Environment facts

Established 2026-08-06. Re-verify before contradicting.

| Fact | Detail |
|---|---|
| Python | `.venv\Scripts\python.exe`, CPython 3.13.x |
| Vector stack | `pyogrio` + `shapely`. **No geopandas** — not needed without a DataFrame layer |
| Raster stack | `rasterio` (its bundled GDAL), `pyproj` |
| **No `requests`** | HTTP is `urllib.request`, matching kelp-density-extract |
| **No GDAL CLI** | `ogr2ogr`, `gdal_translate` etc. are absent; go through the libraries |
| Archives are read in place | GDAL `/vsizip/`; nothing is unpacked to disk |

`pyogrio` is the one dependency this repo adds over the kelp toolkit, and it is
unavoidable: `rasterio` exposes GDAL's **raster** API only, not OGR, so there is
otherwise no way to read a shapefile or a file geodatabase. It also reads
`OpenFileGDB`, which PMEP and some BIOS archives require, and supports a
read-time bbox filter so a statewide layer never lands in memory whole.

---

## 4. Project conventions

**Nothing about a study area is hardcoded.** No baked-in bounding box, dataset
list, CRS, or output directory. Everything comes from the CLI or a config file,
and the CLI wins. `hf-radar-extract` is the reference implementation.

**Bounding box order is always `WEST,SOUTH,EAST,NORTH`** in decimal degrees.
Both are negative in California. Matches `cudem-extract` and
`kelp-density-extract`; do not introduce a second convention in the family.

**Derive the projected CRS from the bbox.** Areas and lengths are measured in
the UTM zone of the box centroid. Do not hardcode a zone.

**Download the published archive and clip locally.** Do *not* make ArcGIS REST
bbox queries the primary path. Those services cap returned records and flag the
cut with `exceededTransferLimit`; an imperfectly paged query returns a plausible
subset rather than an error. They are also unpinnable, and CDFW's map servers
returned HTTP 500 during development. A cached archive plus a local clip is
reproducible, verifiable, and survives the publisher being down.

**Resolve URLs, never construct them.** The BIOS bucket directory can be derived
(`ds3151` → `3100_3199`), but that is a hypothesis. `catalog.resolve_bios`
lists the directory and confirms the archive exists, then reads size and
`Last-Modified` from a HEAD. When a dataset is missing the error reports what
the bucket *does* contain. Note that `metadata/DS3151.html` resolves while
`DS582.html` 404s — metadata pages are enrichment, never the resolver.

**Clipping invalidates precomputed attributes.** BIOS polygons carry `Acres`,
`Shape_Area` and similar fields describing the *uncut* feature. Every declared
geometry field is renamed `orig_*` and replaced with a value recomputed from the
clipped geometry; each feature also carries `clipped` and `clip_fraction`. A
stale acreage surviving a clip is exactly the plausible-wrong-number this repo
family exists to prevent — never remove this handling to simplify a diff.

**Never fabricate attribution.** Citation metadata is read from the FGDC or ISO
document inside the cached archive, never guessed and never defaulted. A field
that cannot be found becomes `[unknown - see metadata]`, the run says which
layers need finishing by hand, and the manifest records `complete: false`. In
particular an unverified licence must stay `UNKNOWN` rather than defaulting to
anything permissive. Registry `license` and `use_constraints` values are facts
verified out of band; anything found in the archive metadata wins over them,
because that travels with the bytes.

**Publisher use constraints are printed at extraction time**, not left in a
file nobody opens, and they are embedded in every output format that has a slot
for them. `ds582` says it is "not intended for navigational use or defining
legal boundaries" — the authoritative source is CCR Title 14 §632. Do not
remove that surfacing to tidy up console output.

**Do not work around a gated publisher.** PMEP's registration form stays a
human step. Never add an automatic fallback that reaches the same data by
another route, and never automate the form. `NETWORK_PROFILE` in `catalog.py`
is the tool's public statement of its own behaviour; if you change what requests
are made, change that text in the same commit.

**Validate every download** before trusting it: HTTP status, ZIP magic
(`PK\x03\x04`), plausible size, `Content-Length` agreement, and that the archive
opens with readable members. A bad cached file is deleted rather than served.
The sibling repo once committed a 155-byte authorization error saved as data;
the equivalent here is an HTML error page written to `ds582.zip`.

**Anchor raster output grids to the bbox, not the data extent**, so runs with
different sources or dates align and can be differenced.

**Distinguish "no data" from "zero".** For Kelp Persistence, "surveyed, no kelp"
and "never surveyed" are different facts. Nodata is preserved, never filled, and
resampling is nearest-neighbour so class codes and year counts stay intact.

**Only `status="ready"` datasets run by default.** A gated or unverified source
must be named explicitly, so a batch run can never half-succeed silently.

---

## 5. Never commit

Enforced by `.gitignore`, stated here so it is a rule and not just config:

- `.venv/`
- source data (`.cache/`, `*.zip`, `data/`) — large, re-downloadable, licensed upstream
- generated output (`output/`, `*.tif`, `*.gpkg`, `*.geojson`, `*.csv`, `*.kmz`, shapefile parts)

Outputs must be reproducible from a command. If they are not, that is the bug.

---

## 6. Source data

**CDFW BIOS**, `https://filelib.wildlife.ca.gov/Public/BDB/GIS/BIOS/Public_Datasets/{bucket}/ds{N}.zip`.
The library is browsable, which is what makes resolution possible. Datasets
carry no version in the URL, so the pin is resolved URL + `Last-Modified` +
`Content-Length` + sha256, all recorded in the manifest. A changed hash on a
cached file is reported rather than absorbed.

Wired up: `mpa` (ds582), `mpa-coords` (ds3207), `state-waters` (ds3158),
`shoreline` (ds3115), `saline-wetlands` (ds2864), `benthic-substrate` (ds3091),
`kelp-persistence` (ds3151, 5 m raster).

**PMEP** (`cmecs-substrate`, `cmecs-quality`) is an *external* layer in the
Marine Viewer, published by PSMFC, not CDFW. Its bulk geodatabase sits behind a
ShareFile link requiring email registration, so it cannot be fetched unattended.
Status is `manual`: download once, then pass `--local-archive cmecs-substrate=<path>`.
A REST service exists at `gis.psmfc.org` but is deliberately not used as the
primary path, for the reasons in §4. Verified 2026-08-06.

**USGS and FEMA layers** (`watersheds`, `quads-24k`, `stream-gages`,
`flood-hazard`) are declared but `unverified` — their download endpoints have
not been confirmed against the publisher. Resolving one raises with a pointer to
the landing page rather than guessing a URL. Wiring each one up is a slice of
its own: confirm the real staged-product path, add a fixture, then flip the
status to `ready`.

**Overlap with kelp-density-extract is intentional but not duplicative.**
`kelp-persistence` (ds3151) is CDFW's rasterised aerial survey product, counting
years in which canopy was observed. The kelp toolkit reads Landsat-derived
*biomass* from EDI. Different sources, different units, different questions —
keep them in their own repos.
