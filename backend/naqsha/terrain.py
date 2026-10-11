"""Ground elevation model.

Uses the Copernicus GLO-30 DEM (public, 30 m) when reachable, read as a
windowed COG request and cached locally per project. Falls back to a flat
ground at the takeoff elevation, which is what the drone's own height
reference assumes anyway.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

COP_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/"
           "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
           "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")


class Terrain:
    def __init__(self, path: Path | None):
        self.path = path
        self._ds = None
        self._arr = None
        if path and path.exists():
            import rasterio
            self._ds = rasterio.open(path)
            self._arr = self._ds.read(1, masked=True).filled(np.nan).astype(float)

    @property
    def available(self) -> bool:
        return self._arr is not None

    def sample(self, lat, lon) -> np.ndarray:
        lat = np.atleast_1d(np.asarray(lat, float))
        lon = np.atleast_1d(np.asarray(lon, float))
        out = np.full(lat.shape, np.nan)
        if not self.available:
            return out
        inv = ~self._ds.transform
        cols, rows = inv * (lon, lat)
        # bilinear
        r0 = np.floor(rows - 0.5).astype(int)
        c0 = np.floor(cols - 0.5).astype(int)
        fr = rows - 0.5 - r0
        fc = cols - 0.5 - c0
        H, W = self._arr.shape
        ok = (r0 >= 0) & (c0 >= 0) & (r0 + 1 < H) & (c0 + 1 < W) & ~np.isnan(lat)
        if ok.any():
            a = self._arr
            r, c, y, x = r0[ok], c0[ok], fr[ok], fc[ok]
            out[ok] = (a[r, c] * (1 - x) * (1 - y) + a[r, c + 1] * x * (1 - y)
                       + a[r + 1, c] * (1 - x) * y + a[r + 1, c + 1] * x * y)
        return out


def fetch_copernicus(bbox: tuple[float, float, float, float], dest: Path, margin_deg: float = 0.01) -> Path | None:
    """Fetch a DEM crop covering bbox=(min_lat, min_lon, max_lat, max_lon)."""
    import rasterio
    from rasterio.merge import merge

    min_lat, min_lon, max_lat, max_lon = bbox
    min_lat -= margin_deg; min_lon -= margin_deg; max_lat += margin_deg; max_lon += margin_deg
    tiles = []
    for la in range(math.floor(min_lat), math.floor(max_lat) + 1):
        for lo in range(math.floor(min_lon), math.floor(max_lon) + 1):
            tiles.append(COP_URL.format(ns="N" if la >= 0 else "S", lat=abs(la),
                                        ew="E" if lo >= 0 else "W", lon=abs(lo)))
    datasets = []
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT="20"):
            for url in tiles:
                datasets.append(rasterio.open("/vsicurl/" + url))
            arr, transform = merge(datasets, bounds=(min_lon, min_lat, max_lon, max_lat))
            profile = datasets[0].profile.copy()
    except Exception as e:  # offline, or tile missing (sea)
        log.warning("DEM fetch failed: %s", e)
        return None
    finally:
        for d in datasets:
            d.close()
    profile.update(driver="GTiff", height=arr.shape[1], width=arr.shape[2], transform=transform,
                   compress="deflate", tiled=False, blockxsize=None, blockysize=None)
    profile.pop("blockxsize"); profile.pop("blockysize")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dest, "w", **profile) as dst:
        dst.write(arr)
    return dest
