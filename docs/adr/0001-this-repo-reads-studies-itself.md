# This repository reads the studies directory itself

Status: accepted, 2026-08-07.

`src/biosextract/studies.py` is a private reader for the shared `study.json`
format. It is the **third** copy of such a reader in the `la-jolla-buoy` family,
after `station-data-extract/…/study.py` and `cudem-extract/bathy/studies.py`.

`cudem-extract`'s ADR 0001 closes with:

> If a third extractor arrives and a third reader appears, revisit this — three
> copies is a different trade than two.

This ADR is that revisit. **The copy stands.** The contract shared between the
tools remains the on-disk layout and the `study.json` format, not the code that
parses it.

## Why the shared library still loses

Extracting a `studylib` was, again, the only real alternative, and the three
reasons against it have all got stronger rather than weaker:

- **It must install into three unlike environments.** `station-data-extract`
  runs in a venv with pandas, `cudem-extract` in a conda GDAL environment,
  and this repo in a venv whose whole point is a short dependency list —
  `pyogrio`, `shapely`, `rasterio`, `pyproj`, and deliberately not `requests`.
  A shared package would have to be pinned, published or path-hacked into all
  three, and a `sys.path` hack to read a JSON file is worse than the file.
- **Landing it would mean editing two repositories that currently pass their
  gates.** A feature in this repo would become a change to three. Nobody
  reviewing a marine-layer extraction wants to re-verify a bathymetry gate.
- **The tool that defines the format would not use it.** The reader that would
  be the natural basis for a shared library — `station-data-extract`'s, which
  *writes* the format — imports pandas and its own ingest package at module
  scope. A shared library that the format's own author does not consume is not
  a shared library; it is a fourth copy with a nicer name.

The count going from two to three does not move any of those. What three copies
would change is the cost of drift, and that is addressed directly rather than
structurally.

## What makes three copies safe

They read *different subsets*, and only one of them writes:

| Repo | Reads | Writes |
|---|---|---|
| `station-data-extract` | everything | creates `study.json`, owns the format |
| `cudem-extract` | stations, envelope, its own producer entry | `cudem/`, one entry in `producers` |
| this repo | stations, envelope, its own producer entry | `marine-bios/`, one entry in `producers` |

No reader touches another's producer directory, and no reader but the creating
tool modifies any key of `study.json` other than appending to `producers`.

This copy is deliberately **smaller** than `cudem-extract`'s, not a
transliteration of it. The product-planning logic is not ported: that reader
plans a fixed set of seven products, while ours vary with which datasets were
selected, so shared code would have had to be wrong in one of the two places.

Drift is guarded the way `cudem-extract` guards it, with a canary:
`test_real_studies_still_parse` in `tests/test_studies.py` parses the actual
`../studies` directory whenever it is present and skips on a clean clone. A
change in what the creating tool writes therefore fails this suite, rather than
surfacing months later as a box in the wrong place.

## Consequences

- A change to the `study.json` format needs a change in three repositories. The
  canary tests make that a red suite rather than a silent misread, which is the
  property that actually matters.
- The next reader is the **fourth**. That is a genuinely different trade again:
  by then the format would have four consumers and one writer, and a published,
  dependency-free `studylib` — installable into each environment on its own
  schedule rather than in one flag-day edit — becomes the cheaper option. Revisit
  it then, and treat this ADR as evidence that the two-copy reasoning was
  re-examined rather than inherited.
