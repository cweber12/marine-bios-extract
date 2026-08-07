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

If the area you want is a **study** in the shared `..\studies` directory, you do
not have to type a rectangle at all — see below.

## Extracting for a study

`station-data-extract` and `cudem-extract` both work from `..\studies\<id>\`,
and so does this toolkit. `study` builds the box out of the study's own station
positions and writes into the study folder:

```powershell
.\run.ps1 study --study latest --pad-km 10 --datasets mpa shoreline --yes
```

That reads `study.json`, takes the tightest rectangle around every station that
has a position, pads it by 10 km on each side, and writes the layers to
`<study>\marine-bios\` with a manifest and an `ATTRIBUTION.txt`. It then appends
one entry to `study.json`'s `producers` list and touches nothing else in that
file.

Some details worth knowing:

- **The study can be named four ways** — its id, its label, any unique fragment
  of either, or `latest`. A fragment matching more than one study is an error
  listing them, never a silent pick of the newest.
- **Padding is required and has no default.** A margin is a statement about the
  study area, and this repo does not bake study areas into code. `--pad-km`
  sets every side; `--pad-north-km`, `--pad-south-km`, `--pad-east-km` and
  `--pad-west-km` override individual ones, so you can reach 20 km offshore
  without dragging the box 20 km inland.
- **Padding is kilometres, converted per axis** at the envelope's centre
  latitude. A degree of longitude at 32.87 N is about 16% shorter than a degree
  of latitude, so padding in degrees would quietly give you a narrower box than
  you asked for. Miles are not offered: the registry holds a *nautical* mile
  limit layer and an ambiguous "mile" in a marine repo is a defect waiting to
  happen.
- **Every station with a position shapes the box, regardless of role.** Stations
  without coordinates are excluded, named on the console, and recorded in the
  producer entry with the reason — in the reference study that is the subject
  buoy itself, which is exactly the fact a run must not bury.
- **The box grows to keep feature groups whole.** A rectangle from station
  positions plus a round number of kilometres lands wherever it lands, and in
  the reference study it falls through the middle of `South La Jolla SMR` and
  drops its touching partner `South La Jolla SMCA` entirely — half a management
  unit, which is worse than none of it because it looks complete. So the run
  finds the features the boundary cuts, walks adjacency out to the whole
  connected group, and moves the box to contain those groups plus a margin. See
  [Growing the box to whole feature groups](#growing-the-box-to-whole-feature-groups).
- **A layer with nothing in the box is still written**, with a recorded count of
  zero. "No saline wetlands within 10 km" is a result worth reading, not a file
  you never notice is missing.
- **Downloads are cached in this repository**, not per study, so seven studies
  of the same coastline do not cost seven copies of a 151 MB archive.
- **`--dry-run`** resolves and prints the box and the plan without writing
  anything; **`--yes`** skips the confirmation. Note that a dry run *does*
  download, because the box cannot be settled without reading the layers that
  might be cut — it says so, and `--no-expand` makes it free. Without a terminal
  and without `--yes` the command stops and says which flag it needed, rather
  than blocking on a prompt nobody can see. It writes no escape sequences, so a
  redirected log stays readable.
- **`--force`** removes files already in `marine-bios\` that this run does not
  write. Without it they are listed and left alone.

`extract` is unchanged and remains the way to work from a rectangle you name
yourself.

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

## Growing the box to whole feature groups

`study` runs one more rule before it downloads anything: it finds the features
the boundary cuts, walks adjacency out to the whole connected group, and moves
the box far enough to contain those groups plus a small margin. It repeats,
because growing the box reveals features the old one never touched, and it stops
when nothing moves or after eight rounds.

This is a property of the **box**, so the run still produces exactly one
rectangle and every layer — raster included — gets that one. It is not the same
thing as `--whole-features`, which is a property of the **clip** and lets
individual geometries extend past the box. That flag stays off unless you ask
for it.

**Each side may only grow by the padding you chose for that side.** No new
number is invented, and the cap scales with the intent you already expressed:
ask for 2 km inland and the rule will not spend more than 2 km inland. That cap
is what makes the rule safe on every layer instead of on a hand-maintained list
of safe ones. Measured against the real published archives:

| Layer | Features | Connected groups | Largest group |
|---|---|---|---|
| `mpa` | 155 | 100 | 5 features, 13.5 × 17.4 km |
| `shoreline` | 13 248 | 1 831 | 1 163 features, 109.6 × 178.5 km |
| `state-waters` | 8 | 8 | 1 feature, 649.6 × 1055.4 km |
| `benthic-substrate` | 333 | 1 | 333 features, 655.5 × 1055.9 km |

`mpa` is the case the rule is for: small, bounded groups. The other three are
not, and they fail two different ways. `shoreline` and `benthic-substrate`
chain — adjacency walks from one cut segment out to a group spanning a hundred
kilometres, or for `benthic-substrate` the whole state as a single group of 333
polygons. `state-waters` never chains at all, its eight features being mutually
disjoint, but each one is a single `MultiLineString` running the length of
California, so the cut feature is already bigger than any box. Either way the
group will not fit inside the budget, so it is **left cut and reported by name
and size** rather than dragging your box up the coast.

A feature left cut keeps the `orig_*`, `clipped` and `clip_fraction` handling
above, so no number in the output ever describes a polygon that no longer
exists.

- **`--no-expand`** turns the rule off and keeps the rectangle the padding
  produced, cuts and all.
- **`--expand-budget-km`** replaces the padding as the budget, one number for
  every side. Useful when you want a tight study box but are willing to reach
  further to keep a reserve whole.

What moved, by how much per side, what was captured and what was refused is
printed while the run happens, and recorded in both `manifest.json` and the
study's own `producers` entry — along with the box before expansion and the box
after, so the difference is never something you have to infer.

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

Every run writes a manifest — `<prefix>_manifest.json` from `extract`, plain
`manifest.json` from `study`: the box, the dataset list, the resolved URL and
sha256 of each source archive, its `Last-Modified` and `Content-Length`, the CRS
chain, how many features were selected and how many were cut, and the size and
hash of every file written. A `study` run additionally records the study, the
station envelope, the four padding values and the stations it had to skip, so
the box can be rebuilt from the study alone.

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
