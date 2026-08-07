"""Grow a box outward to whole feature groups, capped by the padding already chosen.

WHY THIS EXISTS
---------------
A rectangle drawn from station positions plus a round number of kilometres lands
wherever it lands. In the reference La Jolla study that boundary falls through
the middle of ``South La Jolla SMR`` and leaves its touching partner
``South La Jolla SMCA`` out of the extract entirely. Half of a management unit
is worse than none of it, because it looks complete.

So: find the features the boundary cuts, walk adjacency out to the whole
connected group, and move the box far enough to contain those groups plus a
small margin.

THE CAP, AND WHY IT IS THE PADDING
----------------------------------
"Capture the whole group" with no limit is not safe. Measured against the real
published archives on 2026-08-07:

===================  ========  ========  =========================================
layer                features  clusters  largest connected group
===================  ========  ========  =========================================
``mpa``                   155       100  5 features, 13.5 x 17.4 km
``shoreline``          13 248      1831  1163 features, 109.6 x 178.5 km
``state-waters``            8         8  1 feature, 649.6 x 1055.4 km
``benthic-substrate``     333         1  333 features, 655.5 x 1055.9 km
===================  ========  ========  =========================================

``mpa`` is the case the rule is for: small, bounded groups. The other three are
not, and they fail in two different ways. ``shoreline`` and
``benthic-substrate`` chain: adjacency walks from one cut segment out to a group
spanning a hundred kilometres or, for ``benthic-substrate``, the whole state as
a single connected group of 333 polygons. ``state-waters`` never chains at all -
its eight features are mutually disjoint - but each one is a single
MultiLineString running the length of California, so the *cut feature itself* is
already bigger than any box. Same outcome, different mechanism, and both are
handled by the same cap rather than by knowing which layer is which.

**The budget for each side is the padding already chosen for that side.** No new
number is invented, the cap scales with the intent already expressed, and it
makes the rule safe by construction. A group that will not fit inside the box
the budget allows is left cut and reported by name and size.

A per-dataset ``expandable`` flag in the registry was rejected: it is a
hand-verified fact that goes silently wrong the day a publisher changes
topology, and the table above would have had to be re-verified for six datasets
before anything could ship. Expanding to the cut feature only, without the
adjacency walk, was also rejected - that is what ``--whole-features`` already
does, and it would leave the touching partner of a cut reserve half-in, which is
the exact failure this module exists to remove.

WHAT IS PURE HERE
-----------------
:func:`expand` takes a box, some geometry and a budget, and returns a box and a
report. No network, no filesystem, no run state. :func:`read_window` is the one
impure helper, and it reads an archive already on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bbox import BBox

#: How far past a captured group the box is pushed. A group sitting exactly on
#: the boundary reads as cut to anyone looking at the map, and is one
#: reprojection rounding error away from being cut in fact. Small enough to be
#: noise beside any padding worth typing; paid for out of the budget like every
#: other kilometre of growth.
DEFAULT_MARGIN_KM = 0.5

#: Expansion reveals features the previous box did not touch, so it repeats.
#: It converges quickly - every round is capped by the same fixed maximum box,
#: so there is a ceiling it cannot pass - but "converges quickly" is not
#: "terminates", and an unbounded loop over real data is not something to find
#: out about in production. Hitting the bound is reported, never silent.
DEFAULT_MAX_ROUNDS = 8

#: Attribute names worth putting in a report, most specific first. A layer with
#: none of them is reported by feature number, which is still enough to find the
#: thing in the output.
NAME_FIELDS = ("NAME", "FULLNAME", "SHORTNAME", "LABEL", "Name", "LIMIT", "TYPE")

#: Names listed per group before the report starts counting instead. A refused
#: ``shoreline`` group runs to 1163 features and nobody reads 1163 names.
NAMES_SHOWN = 5


class ExpansionError(RuntimeError):
    """A layer could not be read for expansion."""


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    """The furthest each side may travel, in kilometres."""

    north_km: float
    south_km: float
    east_km: float
    west_km: float

    @classmethod
    def uniform(cls, km: float) -> "Budget":
        return cls(north_km=km, south_km=km, east_km=km, west_km=km)

    @property
    def exhausted(self) -> bool:
        """True when no side may move at all, so there is nothing to try."""
        return not any(
            (self.north_km, self.south_km, self.east_km, self.west_km)
        )

    def as_dict(self) -> dict:
        return {
            "north_km": self.north_km,
            "south_km": self.south_km,
            "east_km": self.east_km,
            "west_km": self.west_km,
        }


@dataclass
class Layer:
    """One vector layer's geometry, in EPSG:4326, already limited to the window.

    ``names`` runs parallel to ``geometries`` and exists only so a report can
    say *South La Jolla SMCA* rather than *feature 91*.
    """

    key: str
    geometries: list
    names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.names:
            self.names = [f"feature {i}" for i in range(len(self.geometries))]
        if len(self.names) != len(self.geometries):
            raise ExpansionError(
                f"{self.key}: {len(self.names)} names for "
                f"{len(self.geometries)} geometries"
            )


# --------------------------------------------------------------------------
# adjacency
# --------------------------------------------------------------------------


def clusters(geometries: list) -> list[list[int]]:
    """Group indices into connected components under "touches or intersects".

    Adjacency is plain ``intersects`` with no snapping tolerance. Polygons
    digitised from a shared boundary carry the same vertices, so the real
    ``mpa`` archive resolves ``South La Jolla SMR`` and ``South La Jolla SMCA``
    into one group without help. A tolerance would be the wrong kind of help
    anyway: it would chain features that are merely near each other, and the
    whole value of the cap above rests on groups being what the publisher
    actually joined.
    """
    from shapely import STRtree

    n = len(geometries)
    if n == 0:
        return []

    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    tree = STRtree(geometries)
    left, right = tree.query(geometries, predicate="intersects")
    for a, b in zip(left, right):
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def is_cut(geom, poly) -> bool:
    """True when ``poly`` keeps part of ``geom`` and discards part of it.

    Grazing the boundary is not being cut. A polygon that shares an edge with
    the box but lies wholly outside it intersects the box; nothing of it
    survives the clip, and treating that as a cut would have the box chase
    features it never had.
    """
    if geom is None or geom.is_empty:
        return False
    if poly.covers(geom):
        return False
    inside = geom.intersection(poly)
    if inside.is_empty:
        return False
    if geom.area > 0:
        return inside.area > 0
    if geom.length > 0:
        return inside.length > 0
    return False  # a point is either in or out, never cut


# --------------------------------------------------------------------------
# the rule
# --------------------------------------------------------------------------


@dataclass
class Group:
    """One connected group of features, and what it would cost to capture."""

    layer: str
    indices: list[int]
    names: list[str]
    bounds: tuple[float, float, float, float]  # w, s, e, n in degrees
    width_km: float
    height_km: float

    def label(self) -> str:
        shown = self.names[:NAMES_SHOWN]
        text = ", ".join(shown)
        if len(self.names) > NAMES_SHOWN:
            text += f" and {len(self.names) - NAMES_SHOWN} more"
        return text

    def as_dict(self) -> dict:
        return {
            "layer": self.layer,
            "features": len(self.indices),
            "names": self.names[:NAMES_SHOWN],
            "size_km": [round(self.width_km, 2), round(self.height_km, 2)],
        }


@dataclass
class Expansion:
    """What the rule decided, and the box it settled on."""

    box: BBox
    before: BBox
    budget: Budget
    margin_km: float
    rounds: int = 0
    rounds_exhausted: bool = False
    captured: list[Group] = field(default_factory=list)
    refused: list[Group] = field(default_factory=list)
    still_cut: dict[str, int] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        return self.box.as_tuple() != self.before.as_tuple()

    def as_dict(self) -> dict:
        grew = self.before.growth_km(self.box)
        return {
            "applied": True,
            "moved": self.moved,
            "layers": list(self.layers),
            "box_before_wsen": list(self.before.as_tuple()),
            "box_after_wsen": list(self.box.as_tuple()),
            "budget_km": self.budget.as_dict(),
            "margin_km": self.margin_km,
            "grew_km": {side: round(km, 3) for side, km in grew.items()},
            "rounds": self.rounds,
            "rounds_exhausted": self.rounds_exhausted,
            "captured": [g.as_dict() for g in self.captured],
            "refused": [
                dict(g.as_dict(), reason="larger than the budget for at least one side")
                for g in self.refused
            ],
            "still_cut": dict(self.still_cut),
        }


def _group_of(layer: Layer, indices: list[int], box: BBox) -> Group:
    xs: list[float] = []
    ys: list[float] = []
    for i in indices:
        w, s, e, n = layer.geometries[i].bounds
        xs += [w, e]
        ys += [s, n]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    km_lon, km_lat = box.km_per_degree
    return Group(
        layer=layer.key,
        indices=list(indices),
        names=[layer.names[i] for i in indices],
        bounds=bounds,
        width_km=(bounds[2] - bounds[0]) * km_lon,
        height_km=(bounds[3] - bounds[1]) * km_lat,
    )


def expand(
    box: BBox,
    layers: list[Layer],
    budget: Budget,
    margin_km: float = DEFAULT_MARGIN_KM,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> Expansion:
    """Move ``box`` outward to contain whole feature groups, within ``budget``.

    ``layers`` must already be limited to the window returned by
    :func:`window`, which is the furthest the box can ever reach. That is not
    an optimisation, it is what makes the answer correct: a group is only
    accepted when it fits inside that window with its margin, and a group that
    fits cannot have a neighbour outside the window, because such a neighbour
    would have to touch a member that crosses the window boundary.
    """
    result = Expansion(box=box, before=box, budget=budget, margin_km=margin_km)
    result.layers = [layer.key for layer in layers]
    if not layers or budget.exhausted:
        result.still_cut = _count_cut(box, layers)
        return result

    limit = window(box, budget)
    grouped = [(layer, clusters(layer.geometries)) for layer in layers]

    settled: set[tuple[str, int]] = set()  # groups already decided, by first index
    current = box

    for round_no in range(1, max_rounds + 1):
        result.rounds = round_no
        poly = current.as_polygon()
        want = list(current.as_tuple())
        moved = False

        for layer, groups in grouped:
            for indices in groups:
                token = (layer.key, indices[0])
                if token in settled:
                    continue
                if not any(is_cut(layer.geometries[i], poly) for i in indices):
                    continue

                group = _group_of(layer, indices, current)
                target = _with_margin(group.bounds, current, margin_km)
                if not _fits(target, limit):
                    result.refused.append(group)
                    settled.add(token)
                    continue

                settled.add(token)
                result.captured.append(group)
                want = [
                    min(want[0], target[0]),
                    min(want[1], target[1]),
                    max(want[2], target[2]),
                    max(want[3], target[3]),
                ]
                moved = True

        if not moved:
            break
        current = BBox(*want)
        if round_no == max_rounds:
            # One more pass would be needed to know there is nothing left. Say
            # so rather than presenting a box as settled when it is not.
            result.rounds_exhausted = _anything_still_cut(current, grouped, settled)

    result.box = current
    result.still_cut = _count_cut(current, layers)
    return result


def window(box: BBox, budget: Budget) -> BBox:
    """The furthest the box can reach: every side pushed out by its budget.

    Reading layers to this rectangle and no further is what bounds the work. A
    statewide layer never lands in memory whole, and a group that runs past this
    edge is one the budget could not have captured anyway.
    """
    return box.grown(
        north_km=budget.north_km,
        south_km=budget.south_km,
        east_km=budget.east_km,
        west_km=budget.west_km,
    )


def _with_margin(
    bounds: tuple[float, float, float, float], box: BBox, margin_km: float
) -> tuple[float, float, float, float]:
    km_lon, km_lat = box.km_per_degree
    return (
        bounds[0] - margin_km / km_lon,
        bounds[1] - margin_km / km_lat,
        bounds[2] + margin_km / km_lon,
        bounds[3] + margin_km / km_lat,
    )


def _fits(target: tuple[float, float, float, float], limit: BBox) -> bool:
    return (
        target[0] >= limit.west
        and target[1] >= limit.south
        and target[2] <= limit.east
        and target[3] <= limit.north
    )


def _anything_still_cut(box: BBox, grouped, settled) -> bool:
    poly = box.as_polygon()
    for layer, groups in grouped:
        for indices in groups:
            if (layer.key, indices[0]) in settled:
                continue
            if any(is_cut(layer.geometries[i], poly) for i in indices):
                return True
    return False


def _count_cut(box: BBox, layers: list[Layer]) -> dict[str, int]:
    poly = box.as_polygon()
    return {
        layer.key: sum(1 for g in layer.geometries if is_cut(g, poly))
        for layer in layers
    }


# --------------------------------------------------------------------------
# reading a layer, which is the one thing here that touches a disk
# --------------------------------------------------------------------------


def read_window(path: str, limit: BBox, key: str, layer: str | None = None) -> Layer:
    """Read the features of one archived layer that fall in ``limit``, in 4326.

    The read is bbox-filtered by the driver in the layer's own CRS, then
    reprojected, so a statewide archive costs the handful of features near the
    study and not 151 MB of memory.
    """
    import numpy as np
    from pyogrio import raw, read_info
    from pyproj import CRS, Transformer
    from shapely import from_wkb
    from shapely.ops import transform as shapely_transform

    try:
        info = read_info(path, layer=layer)
    except Exception as exc:  # noqa: BLE001 - driver failure modes vary
        raise ExpansionError(f"{key}: could not read {path}: {exc}") from exc

    source_crs = info.get("crs")
    if not source_crs:
        raise ExpansionError(
            f"{key} declares no coordinate reference system, so its features "
            "cannot be placed on the earth. Refusing to guess one."
        )

    env = limit.to_crs_bounds(source_crs)
    meta, _fids, wkb, field_data = raw.read(path, layer=layer, bbox=env)
    geoms = list(from_wkb(wkb))

    src = CRS.from_user_input(source_crs)
    dst = CRS.from_epsg(4326)
    if not src.equals(dst):
        tf = Transformer.from_crs(src, dst, always_xy=True)
        geoms = [
            shapely_transform(lambda x, y, _t=tf: _t.transform(x, y), g)
            if g is not None and not g.is_empty
            else g
            for g in geoms
        ]

    fields = [str(f) for f in meta["fields"]]
    names = _names(fields, field_data, len(geoms))
    keep = [i for i, g in enumerate(geoms) if g is not None and not g.is_empty]
    return Layer(
        key=key,
        geometries=[geoms[i] for i in keep],
        names=[names[i] for i in keep],
    )


def _names(fields: list[str], field_data, count: int) -> list[str]:
    lower = {f.lower(): i for i, f in enumerate(fields)}
    for candidate in NAME_FIELDS:
        i = lower.get(candidate.lower())
        if i is not None:
            return [str(v) for v in field_data[i]]
    return [f"feature {i}" for i in range(count)]


# --------------------------------------------------------------------------
# the run stage
# --------------------------------------------------------------------------
#
# Everything above is a box, some geometry and a budget. What follows is the
# plug: it registers at BOX_SEAM, gathers the geometry from the run, and hands
# the answer back. The command body is not touched.


def stage(state) -> tuple[object, dict]:
    """Move :attr:`RunState.box` out to whole feature groups. A BOX_SEAM stage.

    Only vector layers drive this. A raster has no features to be half of, and
    it clips to whatever rectangle the vector layers settle on - expansion is a
    property of the *box*, so the run still produces exactly one rectangle and
    every selected layer gets it.

    Nothing here may sink a run. A layer that cannot be read is a warning and a
    named entry in the report; the box then settles on the layers that could be.
    """
    from . import catalog, studyrun
    from .archive import select as select_payload

    request = state.request
    if not request.expand:
        return state, {
            "applied": False,
            "reason": "disabled by --no-expand",
        }

    budget = _budget_for(request)
    keys = [
        key
        for key in request.datasets
        if key not in state.source_errors and catalog.get(key).kind == "vector"
    ]
    if not keys:
        return state, {
            "applied": False,
            "reason": "no vector layer in this run; a raster has no feature groups",
            "budget_km": budget.as_dict(),
        }
    if budget.exhausted:
        return state, {
            "applied": False,
            "reason": "the padding is zero on every side, so there is no budget to spend",
            "budget_km": budget.as_dict(),
        }

    limit = window(state.box, budget)
    print(
        "\nExpansion: reading %d vector layer(s) to find groups the box cuts, "
        "up to\n           N %g km, S %g km, E %g km, W %g km - the padding "
        "already chosen."
        % (
            len(keys),
            budget.north_km,
            budget.south_km,
            budget.east_km,
            budget.west_km,
        )
    )

    layers: list[Layer] = []
    unread: dict[str, str] = {}
    for key in keys:
        dataset = catalog.get(key)
        try:
            archive = studyrun.acquire(state, dataset, verbose=True)
            payload = select_payload(archive.path, dataset.kind, dataset.layer)
            layers.append(read_window(payload.vsi_path, limit, key, layer=None))
        except Exception as exc:  # noqa: BLE001 - a layer we cannot read is not fatal
            unread[key] = f"{type(exc).__name__}: {exc}"
            state.warnings.append(f"{key}: not consulted for expansion: {exc}")
            print(f"           {key}: not consulted - {str(exc).splitlines()[0]}")

    result = expand(state.box, layers, budget)
    state.box = result.box
    _announce(result, unread)

    report = result.as_dict()
    if unread:
        report["unread"] = unread
    return state, report


def _budget_for(request) -> Budget:
    """The per-side budget: the padding, unless a flag says otherwise."""
    if request.expand_budget_km is not None:
        return Budget.uniform(float(request.expand_budget_km))
    padding = request.padding
    return Budget(
        north_km=padding.north_km,
        south_km=padding.south_km,
        east_km=padding.east_km,
        west_km=padding.west_km,
    )


def _announce(result: Expansion, unread: dict[str, str]) -> None:
    """Say what moved and what did not. A refusal left in a file protects nobody."""
    if result.moved:
        grew = result.before.growth_km(result.box)
        moved_sides = ", ".join(
            f"{km:.1f} km {side}" for side, km in grew.items() if km > 0.01
        )
        print(f"           box grew {moved_sides}")
        print(f"           now {result.box}")
    else:
        print("           the box was left where it was")

    for group in result.captured:
        print(
            "           captured %s: %s (%d feature(s), %.1f x %.1f km)"
            % (
                group.layer,
                group.label(),
                len(group.indices),
                group.width_km,
                group.height_km,
            )
        )
    for group in result.refused:
        print(
            "           REFUSED %s: %s - %d feature(s) spanning %.1f x %.1f km, "
            "larger than the budget allows"
            % (
                group.layer,
                group.label(),
                len(group.indices),
                group.width_km,
                group.height_km,
            )
        )
    still = {k: v for k, v in result.still_cut.items() if v}
    if still:
        print(
            "           still cut at the boundary: %s. Their orig_* fields and "
            "clip_fraction\n           describe the cut, as always."
            % ", ".join(f"{k} {v}" for k, v in sorted(still.items()))
        )
    if result.rounds_exhausted:
        print(
            "           stopped after %d rounds with features still cut; this box "
            "is not\n           settled. Raise the budget or pass --no-expand."
            % result.rounds
        )
    for key, reason in unread.items():
        print(f"           {key} did not take part: {reason.splitlines()[0]}")


def register() -> None:
    """Plug :func:`stage` into the run. Idempotent."""
    from . import studyrun

    studyrun.register_box_stage("expansion", stage)
