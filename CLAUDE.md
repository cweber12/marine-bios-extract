# marine-bios-extract — working agreement

**Repo:** `C:\Projects\la-jolla-buoy\marine-bios-extract`
**Purpose:** given a bounding rectangle, produce analysis-ready files for CDFW
BIOS marine layers and their companion datasets.
**Audience:** a CLI coding agent working directly in this repo.

This file follows the same shape as `kelp-density-extract\CLAUDE.md`, with one
deliberate exception: §1 is the workflow from `station-data-extract\CLAUDE.md`,
because a plan that lives only in a transcript dies with the session. Where any
of the three differ, the difference is deliberate and explained.

---

## 1. Workflow — confirm, plan, PRD, branch, slice, PR

Follow this for any task beyond a one-line fix. It is not optional, and the
steps do not collapse into a single reply.

### 1.1 Confirm understanding before doing anything

Say back what you think is being asked, in your own words, including anything
ambiguous and how you intend to read it. Keep it proportional — a line or two
for a small ask. If two readings would lead to materially different work, ask;
do not pick one silently.

Say what you think is *out* of scope. Most misunderstandings in this family have
been about scope, not intent.

Offer suggestions when warranted — a better approach, a hidden cost, a
correctness risk — *before* implementing. Say nothing if you have nothing;
manufactured suggestions train the owner to skim past the ones that matter.

### 1.2 Produce a plan in logical slices

A slice is a change that:

- does one thing you can name in a short sentence,
- leaves the repo working and `.\run-tests.ps1` green,
- can be committed on its own and understood from its commit message alone.

Rename, refactor, bugfix and new feature are **separate slices**, even when they
touch the same file. If a slice cannot be described without the word "and", it
is probably two slices.

State the slices in order, numbered, with the dependencies between them and an
acceptance check for each. Estimate nothing; just make the order defensible.

### 1.3 Confirm the plan

Present the slice list and wait for agreement before implementing. Explicit
authorization in the prompt ("go ahead", "do it") is agreement for the scope
described, and only that scope. If the plan changes mid-flight — and it will,
because verification surfaces real bugs — say so and re-confirm rather than
quietly expanding scope.

### 1.4 Open a PRD

Once the slice list is agreed, write it up as a PRD and publish it to the issue
tracker (`cweber12/marine-bios-extract`) with the `ready-for-agent` label. Do
this *before* starting slice 1.

The conversation that produced the plan is not durable. The issue is, and it is
the only thing another agent — or you in a fresh session — can pick the work up
from. A plan that exists solely in a transcript has to be rebuilt from scratch
every time someone returns to it, and it gets rebuilt slightly differently each
time.

The PRD states the problem and the solution *from the user's point of view*, the
user stories, the implementation decisions, the **test seams**, and what is out
of scope. Say what was considered and rejected, and why — the rejected options
are most of what makes the accepted one defensible, and they are the first thing
someone re-litigates otherwise.

Agree the seams before publishing; they decide whether the feature can be
verified at all. Prefer existing seams to new ones — here they are the dataset
registry, the resolver, the clip functions and the writers, each reachable
without the network. Something that can only be exercised by a live download is
not a seam: see the `network` marker excluded by default in `run-tests.ps1`.

### 1.5 Split the PRD into issues, when that earns its keep

Break the PRD into issues — one per **vertical** slice, each cutting a complete
path through resolve → download → clip → write, rather than one layer across the
whole feature. A slice that delivers only a registry entry, or only a writer,
cannot be demonstrated and cannot be verified except by the slice that finally
uses it.

Publish in dependency order so each issue can name a real blocker. Label them
`ready-for-agent` and point each at the PRD as its parent. **Never close or edit
the PRD issue itself** — it is the record of what was decided, not a checklist.

Mark each issue AFK if it can be implemented and merged without a human, or HITL
if it needs a decision or a look at the artifact. Prefer AFK. A slice whose whole
purpose is that a number or a map reads correctly *to a person* is HITL, because
no gate can assert it.

**Skip this step when it does not earn its keep.** One slice is one issue is
overhead, and a PRD small enough to finish on one branch is better worked from
the PRD. The test is whether two agents could pick up two of the issues without
colliding. If not, splitting bought nothing.

### 1.6 Branch per issue

Work every issue on its own branch, cut from an up-to-date `main`. Never commit
directly to `main`, except where §1.10 allows it.

- Name the branch after the issue: `issue-<number>-<short-slug>`. Work with no
  issue behind it — see §1.10 — goes on `docs/<short-slug>`.
- One issue per branch. Work that "was right there" belongs to a different
  branch and a different issue, even when it is two lines.
- Do not start an issue whose blocker has not merged. The blocker's code is the
  ground the slices stand on, and rebasing half-finished work onto a moved
  blocker is how a verified slice quietly stops being verified.
- **Told to start one anyway? Say so, then stack.** Cut the branch from the
  *blocker's branch*, not from `main`, and open the PR against the blocker's
  branch so the diff is your slice alone. State in the PR body that it is
  stacked, on what, and that the blocker merges first. Cutting from `main`
  instead produces a PR carrying the blocker's commits as well — unreviewable,
  and it merges the blocker twice.

### 1.7 Implement one slice, verify it, commit it

**Commit after every slice.** Not at the end of the task, not once per session —
after each slice. A clean working tree between slices is the point: it means any
slice can be reverted or bisected on its own. The commit history *is* the change
record.

Before committing a slice:

- run the gates in §2 — `.\run-tests.ps1`, plus a smoke run of the CLI if you
  touched it. Never commit red,
- confirm the working tree contains only that slice's changes,
- write a message with a conventional prefix (§2) that says what changed and
  *why*, not just what.

Then move to the next slice. Do not batch commits.

### 1.8 Push, open a PR, and wait

When every slice in the issue is committed and its gates pass, push the branch
and open a pull request. Then stop.

The PR body states:

- `Closes #<issue>`, so the tracker closes itself on merge,
- what changed and why, at the level of the slices,
- **the actual output of the gates you ran** — not "tests pass". A claim is not
  evidence. If the change touches the network path, show `.\run-tests.ps1
  -Network` as well; the default run excludes those tests, so a green default
  says nothing about the publisher still being reachable,
- anything you did not do, and why.

Then **wait for confirmation to merge.** Do not merge your own PR unprompted, do
not approve it, and do not bypass hooks or checks to make it mergeable. If a
hook fails, the hook is the message.

On confirmation:

- merge with a **merge or rebase commit, never a squash**. Every slice is meant
  to be revertible and bisectable on its own, and squashing a branch into a
  single commit destroys the exact property §1.7 exists to create,
- delete the remote branch, then the local branch,
- return to `main` and pull, so the next issue starts from the merged state.

If changes are requested instead, keep working on the same branch — new slices,
new commits, same rules. Do not rewrite history that has already been pushed.

**Merging a stack: retarget the upper PR before merging the lower one.**
Deleting a branch on merge closes any PR still pointing at it — GitHub does not
reliably retarget — and a closed PR can be neither reopened nor retargeted while
its base branch is missing. Recovering means pushing the deleted ref back,
reopening, retargeting and only then merging. Doing it in the right order costs
nothing — it is the same commands, with the retarget moved to the front:

```powershell
gh pr edit <upper> --base main
gh pr merge <lower> --merge --delete-branch
# merge main into the upper branch, re-run the gates, then:
gh pr merge <upper> --merge --delete-branch
```

**Re-run `.\run-tests.ps1` on the upper branch merged with the new `main`
before merging it.** Its green run was against the tree before the blocker
landed, and that is not the tree it is about to join. Quote the re-run, not the
original, as the gate output for the merge.

### 1.9 Report honestly

If a slice is blocked, say so and finish the others. If verification fails, show
the output. If you found a bug in your own earlier work, say that plainly —
catching it is worth more than looking tidy. A dataset that resolved but clipped
to nothing is a finding to report, not a quiet zero.

### 1.10 Documentation changes are routed by what they change

Size is not the test; whether a rule moves is.

| Change | Route |
|---|---|
| Typo, formatting, a dead link, clearer wording for a rule that already exists | Commit to `main` with a `docs:` prefix. No branch, no PR, no PRD |
| Adding, removing or altering a rule — anything in §1, §4 or §5, or a new fact in §3 | Branch `docs/<slug>` and a PR. A PRD only if it spans more than one slice |
| `README.md`, docstrings or `--help` text shipped alongside code | Part of that slice's commit, on that slice's branch. Not a separate route |

A rule change earns a PR because the PR is the only place a person reads the new
rule *before* it starts binding every future session, and the PR body is where
*why* it changed survives — a `docs:` subject line will not carry it. A typo
carries none of that cost, and routing typos through branches teaches everyone
to skim the PR that mattered.

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
