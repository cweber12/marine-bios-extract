"""Bounding-box clipping of vector layers.

Two things here are load-bearing.

**The clip happens in the source CRS.** Most BIOS layers are published in
California Teale Albers. Reprojecting an entire statewide layer to WGS84 and
then clipping would be both slower and lossier than moving the box the other
way, so the box is transformed into the source CRS - densified along its edges,
because a four-corner rectangle in Albers has straight sides where the true
boundary is curved.

**Clipping invalidates precomputed attributes.** BIOS polygons ship fields like
``Acres`` and ``Shape_Area`` describing the whole original feature. Cut a
polygon at the edge of the box and those numbers still describe the uncut one.
Left alone that is a textbook plausible-wrong-number, so every declared geometry
field is renamed ``orig_*`` and replaced with a value recomputed from the
clipped geometry in a metric CRS. Each feature also carries ``clipped`` and
``clip_fraction`` so a partial feature can be excluded from any total.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from pyogrio import raw, read_info
from shapely import from_wkb, to_wkb

from .bbox import BBox


class VectorError(RuntimeError):
    """Raised when a layer cannot be read or clipped."""


class EmptyClipError(VectorError):
    """The bounding box selected no features from this layer."""


@dataclass
class ClipResult:
    """A clipped layer, ready to write."""

    geometry: np.ndarray  # WKB, in `crs`
    fields: list[str]
    field_data: list[np.ndarray]
    crs: str
    geometry_type: str

    source_features: int = 0
    selected: int = 0  # survived the bbox prefilter
    kept: int = 0  # survived the exact clip
    clipped_count: int = 0  # kept features that were actually cut
    source_crs: str = ""
    measure_crs: str = ""
    recomputed_fields: list[str] = field(default_factory=list)
    whole_features: bool = False

    def __len__(self) -> int:
        return self.kept

    def column(self, name: str) -> np.ndarray:
        return self.field_data[self.fields.index(name)]

    def as_dict(self) -> dict:
        return {
            "source_features": self.source_features,
            "selected_by_bbox": self.selected,
            "kept": self.kept,
            "clipped_at_boundary": self.clipped_count,
            "source_crs": self.source_crs,
            "output_crs": self.crs,
            "measure_crs": self.measure_crs,
            "recomputed_fields": self.recomputed_fields,
            "whole_features": self.whole_features,
        }


def _measure(geoms, source_crs: str, measure_crs: str):
    """Area (m^2) and length (m) of each geometry, computed in a metric CRS."""
    from pyproj import CRS, Transformer
    from shapely.ops import transform as shapely_transform

    src = CRS.from_user_input(source_crs)
    dst = CRS.from_user_input(measure_crs)
    if src.equals(dst):
        projected = list(geoms)
    else:
        tf = Transformer.from_crs(src, dst, always_xy=True)
        projected = [
            shapely_transform(lambda x, y, _t=tf: _t.transform(x, y), g)
            if g is not None and not g.is_empty
            else g
            for g in geoms
        ]
    areas = np.array(
        [0.0 if g is None or g.is_empty else float(g.area) for g in projected]
    )
    lengths = np.array(
        [0.0 if g is None or g.is_empty else float(g.length) for g in projected]
    )
    return areas, lengths


def _reproject(geoms, src_crs: str, dst_crs: str):
    from pyproj import CRS, Transformer
    from shapely.ops import transform as shapely_transform

    src = CRS.from_user_input(src_crs)
    dst = CRS.from_user_input(dst_crs)
    if src.equals(dst):
        return list(geoms)
    tf = Transformer.from_crs(src, dst, always_xy=True)
    return [
        shapely_transform(lambda x, y, _t=tf: _t.transform(x, y), g)
        if g is not None and not g.is_empty
        else g
        for g in geoms
    ]


def clip(
    path: str,
    bbox: BBox,
    layer: str | None = None,
    geometry_fields: tuple[str, ...] = (),
    whole_features: bool = False,
    output_crs: str | None = None,
    measure_crs: str | None = None,
    verbose: bool = True,
) -> ClipResult:
    """Read ``path``, keep what falls in ``bbox``, and return it ready to write.

    ``whole_features`` keeps every intersecting feature intact instead of
    cutting it at the boundary. The output then extends beyond the requested
    rectangle, which is sometimes what you want (a whole MPA rather than the
    corner of one) and is recorded in the manifest either way.
    """
    info = read_info(path, layer=layer)
    source_crs = info.get("crs")
    if not source_crs:
        raise VectorError(
            f"{path} declares no coordinate reference system, so its coordinates "
            "cannot be placed on the earth. Refusing to guess one."
        )
    source_features = int(info.get("features", 0) or 0)

    out_crs = output_crs or "EPSG:4326"
    meas_crs = measure_crs or bbox.utm_epsg

    # Prefilter with the driver's own spatial index, then clip exactly. The
    # envelope is deliberately the *widened* projection of the box, so nothing
    # near a curved edge is dropped before the exact test runs.
    try:
        env = bbox.to_crs_bounds(source_crs)
    except Exception as exc:  # pragma: no cover - pyproj failure modes vary
        raise VectorError(
            f"could not express the bounding box in the layer CRS ({source_crs}): {exc}"
        ) from exc

    meta, _fids, wkb, field_data = raw.read(path, layer=layer, bbox=env)
    fields = [str(f) for f in meta["fields"]]
    field_data = [np.asarray(col) for col in field_data]
    selected = len(wkb)

    if selected == 0:
        raise EmptyClipError(
            "the bounding box selected no features from %s.\n"
            "Layer covers %s in %s; the box projects to %s."
            % (
                path.rsplit("/", 1)[-1],
                info.get("total_bounds"),
                source_crs,
                tuple(round(v, 1) for v in env),
            )
        )

    geoms = list(from_wkb(wkb))
    clip_poly = bbox.polygon_in_crs(source_crs)

    orig_areas, orig_lengths = _measure(geoms, source_crs, meas_crs)

    if whole_features:
        # Still test properly: the bbox prefilter is an envelope test, so it can
        # admit a feature whose bounding box overlaps while the geometry does not.
        keep_mask = np.array([g is not None and g.intersects(clip_poly) for g in geoms])
        kept_geoms = [g for g, k in zip(geoms, keep_mask) if k]
        was_clipped = np.zeros(len(kept_geoms), dtype=bool)
    else:
        cut = [
            g.intersection(clip_poly) if g is not None else None for g in geoms
        ]
        keep_mask = np.array(
            [g is not None and not g.is_empty for g in cut]
        )
        kept_geoms = [g for g, k in zip(cut, keep_mask) if k]
        # A feature counts as clipped when the intersection actually removed
        # something, not merely because it touched the boundary.
        was_clipped = np.array(
            [
                not o.equals(c)
                for o, c, k in zip(geoms, cut, keep_mask)
                if k
            ],
            dtype=bool,
        )

    if not len(kept_geoms):
        raise EmptyClipError(
            f"every candidate feature from {path.rsplit('/', 1)[-1]} fell outside "
            "the box once tested exactly (the prefilter is an envelope test)."
        )

    field_data = [col[keep_mask] for col in field_data]
    kept_orig_area = orig_areas[keep_mask]
    kept_orig_len = orig_lengths[keep_mask]

    new_areas, new_lengths = _measure(kept_geoms, source_crs, meas_crs)

    # clip_fraction: how much of the original feature survives. Area for
    # polygons, length for lines, 1.0 for points - measured on whichever is
    # non-zero so it stays meaningful across geometry types.
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(
            kept_orig_area > 0,
            new_areas / np.where(kept_orig_area > 0, kept_orig_area, 1.0),
            np.where(
                kept_orig_len > 0,
                new_lengths / np.where(kept_orig_len > 0, kept_orig_len, 1.0),
                1.0,
            ),
        )
    frac = np.clip(np.nan_to_num(frac, nan=1.0), 0.0, 1.0)

    # Rename any attribute that describes the original geometry, so a stale
    # number can never be mistaken for a live one.
    recomputed: list[str] = []
    declared = {f.lower() for f in geometry_fields}
    for i, name in enumerate(list(fields)):
        if name.lower() in declared:
            fields[i] = "orig_" + name
            recomputed.append(name)

    fields += ["clipped", "clip_fraction", "area_m2", "length_m"]
    field_data += [
        was_clipped,
        frac.astype("float64"),
        new_areas.astype("float64"),
        new_lengths.astype("float64"),
    ]

    out_geoms = _reproject(kept_geoms, source_crs, out_crs)
    gtype = meta.get("geometry_type") or "Unknown"

    if verbose:
        note = " (whole features)" if whole_features else ""
        print(
            "    %d of %d features kept, %d cut at the boundary%s"
            % (len(out_geoms), selected, int(was_clipped.sum()), note)
        )

    return ClipResult(
        geometry=np.array([to_wkb(g) for g in out_geoms], dtype=object),
        fields=fields,
        field_data=field_data,
        crs=out_crs,
        geometry_type=gtype,
        source_features=source_features,
        selected=selected,
        kept=len(out_geoms),
        clipped_count=int(was_clipped.sum()),
        source_crs=str(source_crs),
        measure_crs=meas_crs,
        recomputed_fields=recomputed,
        whole_features=whole_features,
    )
