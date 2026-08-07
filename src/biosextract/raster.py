"""Bounding-box clipping of raster layers.

The output grid is anchored to the requested box, not to the extent of the
source data. Two runs over the same box therefore produce pixel-aligned rasters
that can be differenced directly, which is the whole point of asking for a box
rather than a dataset. Anchoring to the data extent instead would shift the grid
whenever the source was revised.

Nodata is preserved rather than filled. For a product like Kelp Persistence the
difference between "surveyed, no kelp" and "never surveyed" is the difference
between a real zero and no information, and collapsing them invalidates any
change claim built on top.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .bbox import BBox


class RasterError(RuntimeError):
    """Raised when a raster cannot be read or the box selects nothing."""


@dataclass
class RasterClip:
    """A clipped raster held in memory, plus how it was built."""

    data: np.ndarray
    transform: object
    crs: str
    nodata: float | None
    source_crs: str
    source_resolution: tuple[float, float]
    width: int
    height: int
    valid_pixels: int
    total_pixels: int

    def as_dict(self) -> dict:
        return {
            "output_crs": self.crs,
            "source_crs": self.source_crs,
            "source_resolution": list(self.source_resolution),
            "width": self.width,
            "height": self.height,
            "valid_pixels": int(self.valid_pixels),
            "total_pixels": int(self.total_pixels),
            "nodata": None if self.nodata is None else float(self.nodata),
        }


def clip(
    path: str,
    bbox: BBox,
    output_crs: str | None = None,
    resolution: float | None = None,
    verbose: bool = True,
) -> RasterClip:
    """Clip ``path`` to ``bbox``, reprojecting onto a box-anchored grid."""
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from rasterio.windows import from_bounds

    with rasterio.open(path) as src:
        if src.crs is None:
            raise RasterError(
                f"{path} declares no CRS, so its pixels cannot be placed on the "
                "earth. Refusing to guess one."
            )
        source_crs = src.crs.to_string()
        src_res = (abs(src.transform.a), abs(src.transform.e))

        try:
            left, bottom, right, top = bbox.to_crs_bounds(source_crs)
        except Exception as exc:
            raise RasterError(
                f"could not express the box in the raster CRS ({source_crs}): {exc}"
            ) from exc

        sb = src.bounds
        if left >= sb.right or right <= sb.left or bottom >= sb.top or top <= sb.bottom:
            raise RasterError(
                "the bounding box does not overlap %s.\nRaster covers %s in %s; "
                "the box projects to %s."
                % (
                    Path(path).name,
                    tuple(round(v, 1) for v in sb),
                    source_crs,
                    (round(left, 1), round(bottom, 1), round(right, 1), round(top, 1)),
                )
            )

        # Grow the read window outward to whole pixels so no partial edge pixel
        # is dropped before warping. `op=` is passed by keyword because rasterio
        # 1.4 made these arguments keyword-only.
        window = (
            from_bounds(left, bottom, right, top, src.transform)
            .round_offsets(op="floor")
            .round_lengths(op="ceil")
        )
        # Keep the read inside the file even when the box overhangs an edge.
        window = window.intersection(
            rasterio.windows.Window(0, 0, src.width, src.height)
        )
        if window.width < 1 or window.height < 1:
            raise RasterError(
                "the box selects less than one pixel of %s (source resolution "
                "%.3g x %.3g in %s)" % (Path(path).name, src_res[0], src_res[1], source_crs)
            )

        data = src.read(1, window=window)
        win_transform = src.window_transform(window)
        nodata = src.nodata
        dtype = data.dtype

    out_crs = output_crs or source_crs

    # Resolution defaults to the source's, expressed in the output CRS, so the
    # tool never invents detail that is not in the data.
    if resolution is None:
        if out_crs == source_crs:
            res = src_res[0]
        else:
            tr, _w, _h = calculate_default_transform(
                source_crs, out_crs, data.shape[1], data.shape[0],
                *rasterio.transform.array_bounds(data.shape[0], data.shape[1], win_transform),
            )
            res = abs(tr.a)
    else:
        res = float(resolution)

    # Anchor to the box, in the output CRS.
    o_left, o_bottom, o_right, o_top = bbox.to_crs_bounds(out_crs)
    width = max(1, int(math.ceil((o_right - o_left) / res)))
    height = max(1, int(math.ceil((o_top - o_bottom) / res)))
    from rasterio.transform import from_origin

    dst_transform = from_origin(o_left, o_top, res, res)

    fill = nodata if nodata is not None else 0
    dst = np.full((height, width), fill, dtype=dtype)

    from rasterio.warp import reproject as _reproject

    _reproject(
        source=data,
        destination=dst,
        src_transform=win_transform,
        src_crs=source_crs,
        dst_transform=dst_transform,
        dst_crs=out_crs,
        src_nodata=nodata,
        dst_nodata=nodata,
        # Nearest keeps class codes and year counts intact; averaging a
        # persistence count would produce values that mean nothing.
        resampling=Resampling.nearest,
    )

    if nodata is None:
        valid = int(dst.size)
    else:
        valid = int(np.count_nonzero(dst != nodata))

    if verbose:
        print(
            "    %d x %d px at %.3g m, %d valid (%.1f%%)"
            % (width, height, res, valid, 100.0 * valid / max(1, dst.size))
        )

    return RasterClip(
        data=dst,
        transform=dst_transform,
        crs=out_crs,
        nodata=None if nodata is None else float(nodata),
        source_crs=source_crs,
        source_resolution=src_res,
        width=width,
        height=height,
        valid_pixels=valid,
        total_pixels=int(dst.size),
    )


def write_geotiff(
    clip_result: RasterClip, path: Path, provenance: dict | None = None
) -> Path:
    """Write a tiled, compressed GeoTIFF.

    Provenance is written into the TIFF's own tag dictionary, including the
    standard ``TIFFTAG_COPYRIGHT`` slot, so ``gdalinfo`` on the file alone tells
    you where it came from and what the publisher asks in return.
    """
    import rasterio

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": clip_result.height,
        "width": clip_result.width,
        "count": 1,
        "dtype": clip_result.data.dtype,
        "crs": clip_result.crs,
        "transform": clip_result.transform,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    if clip_result.nodata is not None:
        profile["nodata"] = clip_result.nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(clip_result.data, 1)
        if provenance:
            tags = {
                "ATTRIBUTION": provenance.get("attribution", ""),
                "LICENSE": provenance.get("license", ""),
                "SOURCE": provenance.get("source", ""),
                "ACCESSED": provenance.get("accessed", ""),
                "USE_CONSTRAINTS": provenance.get("use_constraints", ""),
                "GENERATED_BY": provenance.get("generated_by", ""),
                "TIFFTAG_COPYRIGHT": provenance.get("attribution", ""),
                "TIFFTAG_IMAGEDESCRIPTION": provenance.get("title", ""),
            }
            dst.update_tags(**{k: str(v) for k, v in tags.items() if v})
    return path
