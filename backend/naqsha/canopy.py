"""Bare ground and canopy height from the surface model.

The stitched DSM is the top of everything (ground, trees, roofs). For tree work we
need height above ground, so the ground has to be separated out:

  1. minimum surface - the DSM resampled to a coarse grid keeping the lowest value
                       per cell, so gaps between leaves reach down towards the ground
  2. SMRF            - Pingel et al. 2013, the same filter ODM and PDAL use on point
                       clouds: open the surface with growing windows and mark cells
                       that rise faster than the terrain slope allows as objects
  3. ground surface  - the remaining cells, interpolated across the objects
  4. canopy height   - DSM minus ground, on a fine grid for treetops and crowns

If ODM already produced a DTM (--dtm) that is used instead of steps 1 to 3.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.fill import fillnodata
from rasterio.transform import from_origin
from rasterio.warp import reproject


@dataclass
class Grid:
    transform: object
    crs: object
    width: int
    height: int

    @property
    def res(self) -> float:
        return float(self.transform.a)


def grid_like(path: Path, res: float) -> Grid:
    with rasterio.open(path) as ds:
        b = ds.bounds
        w = int(math.ceil((b.right - b.left) / res))
        h = int(math.ceil((b.top - b.bottom) / res))
        return Grid(from_origin(b.left, b.top, res, res), ds.crs, w, h)


def resample(path: Path, grid: Grid, how: Resampling, band: int = 1) -> np.ndarray:
    """Read one band onto a grid; nodata becomes NaN. GDAL warps in blocks, so a
    multi-gigabyte DSM never has to fit in memory at full resolution."""
    out = np.full((grid.height, grid.width), np.nan, np.float32)
    with rasterio.open(path) as ds:
        reproject(rasterio.band(ds, band), out, dst_transform=grid.transform, dst_crs=grid.crs,
                  src_nodata=ds.nodata, dst_nodata=np.nan, resampling=how, num_threads=2)
    return out


def _fill(a: np.ndarray, max_dist: int = 400) -> np.ndarray:
    valid = np.isfinite(a)
    if valid.all() or not valid.any():
        return a
    out = np.where(valid, a, 0).astype(np.float32)
    out = fillnodata(out, mask=valid.astype(np.uint8), max_search_distance=max_dist, smoothing_iterations=0)
    # fillnodata leaves cells beyond its reach at the placeholder; mark them unknown again
    far = cv2.distanceTransform((~valid).astype(np.uint8), cv2.DIST_L2, 5) > max_dist
    out[far] = np.nan
    return out


def _disk(r: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def smrf_ground(zmin: np.ndarray, cell: float, slope: float = 0.25, max_window_m: float = 24.0,
                threshold: float = 0.5, scalar: float = 1.25) -> tuple[np.ndarray, np.ndarray]:
    """Simple Morphological Filter on a minimum surface.

    Returns (ground elevation, is_object mask). `slope` is rise over run the ground
    may have; Shakarparian is hilly, hence 0.25 rather than flat-terrain 0.15.
    """
    valid = np.isfinite(zmin)
    z = _fill(zmin)
    reach = np.isfinite(z)
    obj = np.zeros(z.shape, bool)
    last = np.where(reach, z, np.nanmedian(z)).astype(np.float32)   # morphology cannot carry NaN
    rmax = max(1, int(round(max_window_m / 2 / cell)))
    radii = sorted({max(1, int(round(r))) for r in np.linspace(1, rmax, 14)})
    prev_r = 0
    for r in radii:
        opened = cv2.morphologyEx(last, cv2.MORPH_OPEN, _disk(r))
        thresh = slope * (r - prev_r if prev_r else r) * cell
        obj |= (last - opened) > max(thresh, 0.05)
        last, prev_r = opened, r
    obj |= ~valid
    ground = np.where(obj, np.nan, z)
    ground = _fill(ground, max_dist=int(4 * rmax))
    # final classification: anything clearly above the interpolated surface is an object,
    # the threshold grows with local slope (SMRF's elevation scalar)
    gy, gx = np.gradient(np.where(np.isfinite(ground), ground, np.nanmedian(ground)), cell)
    s = np.hypot(gx, gy)
    obj = (z - ground) > (threshold + scalar * np.nan_to_num(s))
    ground2 = _fill(np.where(obj | ~valid, np.nan, z), max_dist=int(4 * rmax))
    known = np.isfinite(ground2)
    ground2 = cv2.GaussianBlur(np.where(known, ground2, np.nanmedian(ground2)).astype(np.float32), (0, 0), 1.5)
    ground2[~known] = np.nan
    return ground2, obj


def write(path: Path, a: np.ndarray, grid: Grid, nodata=-9999.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.where(np.isfinite(a), a, nodata).astype(np.float32)
    with rasterio.open(path, "w", driver="GTiff", width=grid.width, height=grid.height, count=1, dtype="float32",
                       crs=grid.crs, transform=grid.transform, nodata=nodata, compress="deflate", tiled=True,
                       predictor=3) as ds:
        ds.write(arr, 1)


def canopy_height(dsm: Path, out_dir: Path, dtm: Path | None = None, res: float = 0.2,
                  ground_cell: float = 0.5, log=lambda *_: None) -> dict:
    """Write ground.tif and chm.tif (height above ground, metres) into out_dir."""
    fine = grid_like(dsm, res)
    log(f"surface grid {fine.width}x{fine.height} at {res} m")
    surface = resample(dsm, fine, Resampling.max)       # keep treetops, not their average
    if dtm is not None and dtm.exists():
        ground = resample(dtm, fine, Resampling.bilinear)
        ground = _fill(ground)
        source = "ODM DTM"
    else:
        coarse = grid_like(dsm, ground_cell)
        zmin = resample(dsm, coarse, Resampling.min)
        log(f"ground filter on {coarse.width}x{coarse.height} cells")
        g, obj = smrf_ground(zmin, ground_cell)
        write(out_dir / "ground.tif", g, coarse)
        ground = resample(out_dir / "ground.tif", fine, Resampling.bilinear)
        source = f"SMRF on DSM ({ground_cell} m cells, {100 * obj.mean():.0f}% objects)"
    chm = surface - ground
    chm[~np.isfinite(surface)] = np.nan
    chm = np.clip(chm, 0, 60)
    write(out_dir / "chm.tif", chm, fine)
    v = chm[np.isfinite(chm)]
    return {"ground_source": source, "res_m": res, "width": fine.width, "height": fine.height,
            "chm_p50": round(float(np.percentile(v, 50)), 2) if v.size else None,
            "chm_p95": round(float(np.percentile(v, 95)), 2) if v.size else None}
