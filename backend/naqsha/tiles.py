"""Dynamic XYZ tiles straight from the ODM Cloud-Optimised GeoTIFFs."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rio_tiler.colormap import cmap
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader

EMPTY_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f"
             b"\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
             b"\x00IEND\xaeB`\x82")


def bounds_wgs84(path: Path) -> list[float]:
    with rasterio.open(path) as ds:
        return list(transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21))


@lru_cache(maxsize=32)
def value_range(path: str, mtime: float) -> tuple[float, float]:
    with rasterio.open(path) as ds:
        f = max(1, max(ds.width, ds.height) // 1024)
        a = ds.read(1, out_shape=(ds.height // f, ds.width // f), masked=True)
        v = a.compressed()
        v = v[np.isfinite(v)]
        if v.size == 0:
            return 0.0, 1.0
        return float(np.percentile(v, 2)), float(np.percentile(v, 98))


def tile(path: Path, layer: str, z: int, x: int, y: int) -> bytes:
    try:
        with Reader(str(path)) as src:
            if layer in ("dsm", "dtm"):
                img = src.tile(x, y, z, tilesize=256, indexes=1)
                lo, hi = value_range(str(path), path.stat().st_mtime)
                img.rescale(in_range=((lo, hi),))
                return img.render(img_format="PNG", colormap=cmap.get("terrain"))
            img = src.tile(x, y, z, tilesize=256, indexes=(1, 2, 3))
            return img.render(img_format="PNG")
    except TileOutsideBounds:
        return EMPTY_PNG


def legend(path: Path) -> dict:
    lo, hi = value_range(str(path), path.stat().st_mtime)
    cm = cmap.get("terrain")
    return {"min": round(lo, 2), "max": round(hi, 2), "colormap": "terrain",
            "stops": [[i / 255, [int(c) for c in cm[i][:3]]] for i in list(range(0, 256, 32)) + [255]]}
