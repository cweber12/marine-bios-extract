# marine-bios-extract

Give it a bounding rectangle; get back the CDFW marine GIS layers for exactly
that area, as GeoJSON, CSV, GeoPackage, KMZ, shapefile or GeoTIFF, with a
manifest recording precisely which published archive produced them.

It is the sibling of `kelp-density-extract`, `hf-radar-extract` and
`cudem-extract`, and follows the same conventions: nothing about a study area is
baked into the code, bounding boxes are always `WEST,SOUTH,EAST,NORTH`, and
every run writes a provenance manifest.

```powershell
.\run.ps1 list
.\run.ps1 extract --bbox '-117.30,32.80,-117.24,32.88' --datasets mpa shoreline
```

## Why it downloads whole files instead of querying ArcGIS

The CDFW Marine Viewer is an Esri web app, and it is tempting to pull its layers
with an ArcGIS REST bounding-box query. This toolkit deliberately does not.

REST query endpoints cap the number of records they return and signal the cut
with `exceededTransferLimit`. Page that imperfectly and you receive a plausible
subset — the right *shape* of answer, quietly missing features, with nothing to
indicate it. That is the failure mode this repo family exists to prevent. The
services are also unpinnable (there is no versioned endpoint to cite six months
later), and CDFW's map servers returned HTTP 500 repeatedly while this was being
built.

So instead: resolve the published archive, download it once, cache it, and clip
locally. The clip is exact, the result is reproducible, and once an archive is
cached the tool keeps working when the publisher does not.

## Installing

**Use `venv`, not conda.**

```powershell
cd C:\Projects\la-jolla-buoy\marine-bios-extract
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
.\run.ps1 list
```

The `[test]` extra adds pytest, which `run-tests.ps1` needs. Plain
`pip install -e .` gives you a working tool but no test runner.

This stack used to be a conda-only affair, because rasterio, pyogrio, pyproj and
shapely all wrap C libraries — GDAL, PROJ and GEOS. That is no longer true: as
of 2026-08-06 every dependency ships a prebuilt `win_amd64` wheel for CPython
3.13, so `pip` installs binaries and compiles nothing.

Three reasons to stay on venv here specifically:

- `run.ps1` and `run-tests.ps1` look for `.venv\Scripts\python.exe`. A conda
  environment lives elsewhere and both launchers will tell you no environment
  exists.
- The sibling repos (`kelp-density-extract`, `hf-radar-extract`) already use
  `.venv`. One convention per family.
- Mixing conda-forge GDAL with pip wheels is the classic way to get a DLL
  conflict on Windows. Staying pure-pip avoids it: rasterio and pyogrio each
  bundle their own GDAL and never consult a system copy.

Expect roughly 80 MB of wheels and 300–400 MB installed — most of it those two
bundled GDAL copies. That is the price of not needing conda.

If you would rather use conda anyway, install the same packages from conda-forge
(`conda install -c conda-forge python=3.13 numpy pandas pyproj rasterio pyogrio
shapely pytest`) and skip the launchers — call `python -m biosextract ...`
directly with `src` on `PYTHONPATH`. Do not install a conda GDAL alongside pip
wheels of rasterio or pyogrio.

### Dependencies

| Package | Why it is here |
|---|---|
| `pyogrio` | reads Shapefile and File Geodatabase, with a read-time bbox filter. The one dependency this repo adds over the kelp toolkit, and unavoidable: rasterio exposes GDAL's raster API only, not OGR |
| `shapely` | the exact geometric clip, and area/length recomputation |
| `rasterio` | raster windowed reads, reprojection and GeoTIFF writing |
| `pyproj` | CRS handling and the densified box reprojection |
| `numpy`, `pandas` | array and column handling |
| `pytest` | optional `[test]` extra |

There is no HTTP dependency. Downloads use `urllib.request` from the standard
library, matching `kelp-density-extract`. Citation metadata is parsed with the
standard library's `xml.etree`.

Both launchers check the environment and tell you what is missing rather than
failing with an import traceback three calls deep.

## What it can fetch

`.\run.ps1 list` prints this, grouped by whether it can run unattended.

| Key | Kind | BIOS id | Layer |
|---|---|---|---|
| `mpa` | vector | ds582 | California Marine Protected Areas |
| `mpa-coords` | vector | ds3207 | MPA boundary coordinates |
| `state-waters` | vector | ds3158 | Three nautical mile state maritime limit |
| `shoreline` | vector | ds3115 | Shoreline types |
| `saline-wetlands` | vector | ds2864 | Saline wetlands (ACE) |
| `benthic-substrate` | vector | ds3091 | Predicted nearshore benthic substrates |
| `kelp-persistence` | raster | ds3151 | Kelp persistence, 5 m grid |

`cmecs-substrate` and `cmecs-quality` are PMEP layers whose bulk geodatabase is
behind an email registration form. Download it once, then:

```powershell
.\run.ps1 extract --bbox '-117.30,32.80,-117.24,32.88' `
    --datasets cmecs-substrate --local-archive cmecs-substrate=C:\downloads\pmep.zip
```

`watersheds`, `quads-24k`, `stream-gages` and `flood-hazard` are declared but
not yet wired up. Asking for one tells you where its data lives rather than
guessing a URL and 404ing.

## Clipping, and the attribute trap

By default geometries are **cut at the box**. `--whole-features` keeps every
intersecting feature intact instead, which is what you want when you care about
an MPA as a unit rather than about the area inside your rectangle.

Either way, be aware of what clipping does to attributes. BIOS polygons ship
precomputed fields like `Acres` and `Shape_Area` that describe the *whole
original feature*. Cut a polygon in half and those numbers do not change — they
silently stop being true. So every such field is renamed `orig_*`, and each
feature gains:

| Column | Meaning |
|---|---|
| `clipped` | whether this feature was actually cut |
| `clip_fraction` | how much of the original survives, 0–1 |
| `area_m2` | recomputed area of what is here, in the box's UTM zone |
| `length_m` | recomputed perimeter or line length |

To total the area of protected water in your box, sum `area_m2`. Summing
`orig_Acres` answers a different question — the size of every MPA that happens
to touch your box — and the column name is there to stop you doing it by
accident.

## Output formats

`--formats` takes any combination of:

- **`geojson`** — portable, WGS84 only, attribute types are loose
- **`csv`** — attributes plus WKT and a representative point guaranteed to fall
  inside its own polygon (unlike a centroid)
- **`gpkg`** — GeoPackage; preserves full field names and types, best for archiving
- **`kmz`** — Google Earth viewing only, never analysis
- **`shp`** — shapefile, for older tooling; truncates field names to ten
  characters, so `clip_fraction` becomes `clip_fract`

Rasters are always written as tiled, deflate-compressed GeoTIFF.

## Attribution and citation

These are third-party published datasets, and several require attribution. Every
run writes `<prefix>_ATTRIBUTION.txt` next to the outputs: a paste-ready
citation per layer in the format BIOS asks for, with the licence, the publisher's
use constraints, the access date and the sha256 of the archive it came from.

Citation metadata is read from the FGDC or ISO 19139 document inside each
downloaded archive. That costs no extra request and, more importantly, describes
the exact bytes that produced your output rather than whatever the website says
today.

**Nothing is invented.** When the originator or publication date is not in the
archive, the citation says `[unknown - see metadata]`, the run prints which
layers need finishing by hand, and the manifest records `"complete": false`. An
incomplete citation is an inconvenience; a fabricated one is a misattribution
that outlives the run.

Provenance also travels inside the files themselves, so credit survives being
copied out of the folder:

| Format | Where provenance lands |
|---|---|
| GeoJSON | foreign members on the FeatureCollection (`attribution`, `license`, `useConstraints`, `clippedToBbox`) |
| GeoPackage | the GeoPackage metadata tables |
| GeoTIFF | TIFF tags, including `TIFFTAG_COPYRIGHT` — visible to `gdalinfo` |
| KMZ | the Document description, shown in Google Earth |
| CSV, shapefile | nowhere — neither format has a slot, so the ATTRIBUTION file is it |

### Use constraints are printed, not buried

When a publisher states a limit, the run prints it as the layer is extracted.
The one most likely to matter here is on `ds582`:

> This dataset is not intended for navigational use or defining legal boundaries.

The authoritative marine protected area boundaries are those in California Code
of Regulations Title 14 §632. This layer is fine for analysis, siting context and
mapping; it is not the thing to rely on for whether an activity is legally inside
an MPA. That is a statement of what the publisher's metadata says, not legal
advice — where it matters to a decision, confirm with CDFW Marine Region.

## What this tool does on the network

Run `.\run.ps1 network` and it will tell you, in full. Summarised:

**It is not a crawler.** It follows no links, keeps no URL frontier, and
discovers nothing. Per dataset, a cold run makes three requests: one `GET` on the
bucket directory index to confirm the archive exists, one `HEAD` to read its size
and `Last-Modified`, and one `GET` to download it. Every later run makes zero,
because the archive is cached. All seven automatic datasets is roughly 21
requests, once.

It contacts exactly one host, `filelib.wildlife.ca.gov`. As checked on
2026-08-06, that host's `robots.txt` disallows `/cgi-bin/`, `/scripts/`,
`/private/` and `/admin/` plus several file extensions; directory paths and
`.zip` files are not restricted, so this access is permitted. The `User-Agent`
identifies the tool honestly rather than impersonating a browser — set
`BIOS_CONTACT` to append your email, which is worth doing if you run it often.

**Gated sources are not worked around.** PMEP publishes its geodatabase behind an
email registration form, presumably so PSMFC can report usage to its funders. The
tool asks you to complete that once and hand over the file, and deliberately does
not fall back to their open REST service as a way around the form.

## Reproducibility

Every run writes `<prefix>_manifest.json`: the box, the dataset list, the
resolved URL and sha256 of each source archive, its `Last-Modified`, the CRS
chain, how many features were selected and how many were cut, and the size and
hash of every file written.

BIOS archives carry no version number in their URL, so that hash *is* the
version. When a cached archive no longer matches what the publisher is serving,
the tool says so and points at `--refresh` rather than quietly using either one.

## Configuration

Copy `config.example.toml`, edit it, pass it with `--config`. Command-line
arguments always override the file. Nothing in the example is a default in the
code.

## Testing

```powershell
.\run-tests.ps1            # offline; synthetic fixtures in EPSG:3310
.\run-tests.ps1 -Network   # also resolves every dataset against CDFW
```

The default run deliberately never touches the network, so a red suite always
means a real defect rather than an upstream outage. The fixtures are built in
California Teale Albers rather than WGS84 because that is what BIOS actually
publishes, and a suite written in degrees would never exercise reprojection.

## A note on kelp

`kelp-persistence` (ds3151) counts the years in which CDFW's aerial surveys saw
canopy at each 5 m cell. That is a different measurement from
`kelp-density-extract`, which reads Landsat-derived biomass from EDI. Same
subject, unrelated sources and units — use both, but do not treat one as a check
on the other.
