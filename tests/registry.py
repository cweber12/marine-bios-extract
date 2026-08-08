"""One synthetic registry, for tests that assert on a dataset's *content*.

`catalog.DATASETS` is production data that happens to be reachable from a test.
Leaning on it works right up until someone verifies a layer, and then a correct
change turns a test red: five instances of that across #21, #22 and #23, each
fixed in place, which is why the sixth is fixed here instead. The rule the
fixes converged on is:

    A test may **name** a real dataset key. It must never **assert on that
    dataset's content**.

Naming one is a genuine contract - key `mpa` exists and is `ready` - and the
~30 `--datasets mpa --local-archive mpa=<fixture>` usages depend on nothing
else. Asserting that `benthic-substrate` is verified, or that `mpa` says
"not intended for navigational use", is a different thing: it is a fixture
borrowed from live data, and it changes whenever a person finishes some real
work. Those assertions come here.

The datasets below are named by *shape* rather than by subject, because the
shape is what the audit and citation code actually distinguishes. Between them
they cover every branch of :func:`biosextract.citation.audit`.

Two properties are deliberate and load-bearing:

**The keys exist in no real registry.** A test that still reaches for real
content after being converted dies with ``KeyError`` rather than quietly
reading whichever live layer is behind today. `test_catalog` asserts the
collision-freedom, because a synthetic key that shadowed a real one would make
that failure silent - the same class of bug this file exists to remove.

**Installation is additive.** :func:`install_synthetic_registry` puts these
beside the real entries rather than replacing them, so the label-only usages in
the same module keep working and `--all` still sees the real registry. Freezing
the whole registry would hollow out `test_catalog`, whose subject *is* the real
one.

Beside `tests/fixtures.py`, and for the same reason: a fixture the tests
control, in one place, instead of six ad-hoc patches.
"""

from __future__ import annotations

import pytest

from biosextract import catalog
from biosextract.catalog import Dataset

#: A licence a reader can trace, with provenance: audits clear.
VERIFIED = "layer-verified"
#: No licence at all: audits with a problem naming the licence.
UNVERIFIED = "layer-unverified"
#: A licence recorded with nowhere to re-check it - `mpa`'s real state, which
#: is exactly why it must not be *borrowed* from `mpa`.
UNTRACEABLE = "layer-untraceable"
#: Citation facts pinned in the registry, for archives carrying no metadata.
PINNED = "layer-pinned"
#: Published behind a form: reported, but never counted against the verdict.
GATED = "layer-gated"
#: Declared but not wired up.
DEMOTED = "layer-demoted"

#: Text no real registry entry carries, so an assertion on it can only be
#: satisfied by this file.
LICENSE = "CC-BY 4.0 (Creative Commons Attribution) - attribution required"
CONSTRAINTS = (
    "Synthetic use constraint: this layer is a test fixture and describes "
    "nothing that exists."
)
VERIFIED_FROM = "https://example.invalid/metadata/DS9001.html"
VERIFIED_ON = "2026-08-08"
LANDING = "https://example.invalid/how-to-get-this-one"

SYNTHETIC: dict[str, Dataset] = {
    VERIFIED: Dataset(
        key=VERIFIED,
        title="A Layer Somebody Verified",
        provider="bios",
        kind="vector",
        dataset_id="ds9001",
        license=LICENSE,
        use_constraints=CONSTRAINTS,
        verified_from=VERIFIED_FROM,
        verified_on=VERIFIED_ON,
    ),
    UNVERIFIED: Dataset(
        key=UNVERIFIED,
        title="A Layer Nobody Has Checked",
        provider="bios",
        kind="vector",
        dataset_id="ds9002",
    ),
    UNTRACEABLE: Dataset(
        key=UNTRACEABLE,
        title="A Layer With A Licence Nobody Can Trace",
        provider="bios",
        kind="vector",
        dataset_id="ds9003",
        license=LICENSE,
        use_constraints=CONSTRAINTS,
    ),
    PINNED: Dataset(
        key=PINNED,
        title="A Layer Whose Archive Says Nothing",
        provider="bios",
        kind="vector",
        dataset_id="ds9004",
        license=LICENSE,
        known_originator="Somebody At The Publisher",
        known_pubdate="2023-03-08",
        verified_from=VERIFIED_FROM,
        verified_on=VERIFIED_ON,
    ),
    GATED: Dataset(
        key=GATED,
        title="A Layer Behind A Registration Form",
        provider="pmep",
        kind="vector",
        status="manual",
        landing_url=LANDING,
    ),
    DEMOTED: Dataset(
        key=DEMOTED,
        title="A Layer Declared But Not Wired Up",
        provider="bios",
        kind="vector",
        dataset_id="ds9006",
        status="unverified",
        status_reason="Its archive holds a choice nobody has made.",
        landing_url=LANDING,
    ),
}


@pytest.fixture
def synthetic_registry() -> dict[str, Dataset]:
    """The synthetic datasets as a mapping, to hand straight to ``audit()``.

    A fresh dict each time, so a test that adds a shape of its own - a variant
    it wants to name in one place - cannot leak it into the next test. The
    ``Dataset`` values are frozen, so sharing them costs nothing.
    """
    return dict(SYNTHETIC)


@pytest.fixture(autouse=True)
def install_synthetic_registry(monkeypatch):
    """Put the synthetic datasets in ``catalog.DATASETS`` for a CLI run to find.

    ``citation.audit()`` takes a registry parameter, but ``cmd_citations`` and
    the study run read the global directly - by design, they are a CLI - so a
    value handed to ``audit()`` never reaches them. The swap has to be on the
    module, which is what the three in-place fixes each did individually.

    Autouse, and imported into a module rather than living in ``conftest``:
    every test in a converted module gets it, and no test anywhere else does.
    ``monkeypatch`` unwinds it per test, so nothing leaks across modules.
    """
    for key, dataset in SYNTHETIC.items():
        monkeypatch.setitem(catalog.DATASETS, key, dataset)
