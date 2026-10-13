"""Sharp map: the orthophoto rebuilt straight from the raw drone frames.

The stitched orthophoto drapes the frames over a surface model. Over leafy ground that
surface is noisy, and the texture smears into streaks wherever it is wrong. Small
plants vanish in the streaks, for the model and for the person marking squares.

Here every ground pixel is instead taken from the single frame that saw it most
straight-down, projected onto the bare-ground surface with that frame's solved camera
pose (OpenSfM, the same poses the orthophoto used). Low plants sit on that surface, so
they come out sharp and in the right place. Tall crowns lean away from the frame centre
(parallax), so wherever the canopy height model says tall trees stand, the orthophoto's
own pixels are kept. Blurred frames (the drone turning) are used only when nothing
sharper saw that spot.

Output: <run>/trees/sharp.tif on the orthophoto's grid, RGB + alpha.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.windows import Window

TILE = 1024              # px per output block
TALL_M = 5.0             # above this the orthophoto's pixels are kept (crowns lean in single frames)
REACH_M = 45.0           # frames whose centre is further than this from a block are not tried
BLUR_PENALTY = 0.35      # a blurred frame counts as this much further from straight-down


class Shot:
    __slots__ = ("name", "img", "R", "rvec", "t", "C", "K", "dist", "W", "H", "origin", "sharpness")


def _camera(cam: dict):
    W, H = cam["width"], cam["height"]
    S = max(W, H)
    K = np.array([[cam["focal_x"] * S, 0, cam["c_x"] * S + W / 2 - 0.5],
                  [0, cam.get("focal_y", cam["focal_x"]) * S, cam["c_y"] * S + H / 2 - 0.5], [0, 0, 1]], float)
    d = np.array([cam.get(k, 0.0) for k in ("k1", "k2", "p1", "p2", "k3")], float)
    return K, d, W, H


def _parts(rdir: Path) -> list[tuple[Path, np.ndarray]]:
    """(reconstruction folder, shift into the merged map) for every part of the map."""
    offs = {}
    f = rdir / "chunk_offsets.json"
    if f.exists():
        offs = json.loads(f.read_text()).get("offsets_m", {})
    subs = sorted((rdir / "submodels").glob("submodel_*")) if (rdir / "submodels").exists() else []
    parts = [(s, np.array(offs.get(s.name, [0, 0, 0]), float)) for s in subs if not offs or s.name in offs]
    return parts or [(rdir, np.zeros(3))]


def load_shots(rdir: Path) -> list[Shot]:
    """Every frame with a solved pose, in the merged map's coordinates (UTM metres)."""
    out: dict[str, Shot] = {}
    for part, off in _parts(rdir):
        rec_f, coords = part / "opensfm" / "reconstruction.json", part / "odm_georeferencing" / "coords.txt"
        if not rec_f.exists() or not coords.exists():
            continue
        e, n = map(float, coords.read_text().splitlines()[1].split()[:2])
        origin = np.array([e, n, 0.0]) + off
        for r in json.loads(rec_f.read_text()):
            cams = {k: _camera(v) for k, v in r["cameras"].items()}
            for name, s in r["shots"].items():
                img = part / "images" / name
                if name in out or not img.exists():
                    continue
                o = Shot()
                o.name, o.img = name, img
                o.rvec = np.array(s["rotation"], float)
                o.R, _ = cv2.Rodrigues(o.rvec)
                o.t = np.array(s["translation"], float)
                o.origin = origin
                o.C = -o.R.T @ o.t + origin
                o.K, o.dist, o.W, o.H = cams[s["camera"]]
                o.sharpness = None
                out[name] = o
    return list(out.values())


def _sharpness(shots: list[Shot]) -> None:
    for s in shots:
        g = cv2.imread(str(s.img), cv2.IMREAD_REDUCED_GRAYSCALE_4)
        s.sharpness = float(cv2.Laplacian(g, cv2.CV_32F).var()) if g is not None else 0.0
    med = float(np.median([s.sharpness for s in shots])) or 1.0
    for s in shots:
        s.sharpness = min(1.0, s.sharpness / med)


def _project(s: Shot, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    local = pts - s.origin
    depth = (local @ s.R.T + s.t)[:, 2]
    px, _ = cv2.projectPoints(local.reshape(-1, 1, 3), s.rvec, s.t, s.K, s.dist)
    return px.reshape(-1, 2).astype(np.float32), depth


def _sample(img: np.ndarray, P: np.ndarray) -> np.ndarray:
    """cv2.remap with an arbitrary number of points (remap maps must stay under 32k a side)."""
    k, w = len(P), 1024
    pad = (-k) % w
    Q = np.vstack([P, np.zeros((pad, 2), np.float32)])
    v = cv2.remap(img, Q[:, 0].reshape(-1, w), Q[:, 1].reshape(-1, w), cv2.INTER_LINEAR)
    return v.reshape(-1, img.shape[2])[:k]


def _read_on(path: Path, ref, window: Window, how=Resampling.bilinear) -> np.ndarray:
    """Band 1 of another raster, resampled onto a window of the reference grid."""
    from rasterio.warp import reproject
    out = np.full((int(window.height), int(window.width)), np.nan, np.float32)
    with rasterio.open(path) as ds:
        reproject(rasterio.band(ds, 1), out, dst_transform=ref.window_transform(window), dst_crs=ref.crs,
                  src_nodata=ds.nodata, dst_nodata=np.nan, resampling=how)
    return out


def build(rdir: Path, ortho: Path, ground: Path, chm: Path | None, out: Path, progress=lambda f, m=None: None) -> dict:
    shots = load_shots(rdir)
    if not shots:
        raise ValueError("this map has no camera poses or frames left to rebuild from")
    progress(0.01, f"checking {len(shots)} frames for blur")
    _sharpness(shots)
    centres = np.array([s.C[:2] for s in shots])
    t0 = time.time()
    tmp = out.with_suffix(".part.tif")
    with rasterio.open(ortho) as src:
        prof = dict(driver="GTiff", width=src.width, height=src.height, count=4, dtype="uint8", crs=src.crs,
                    transform=src.transform, tiled=True, blockxsize=512, blockysize=512, compress="deflate",
                    predictor=2, BIGTIFF="IF_SAFER", photometric="RGB")
        res = src.res[0]
        blocks = [(r, c) for r in range(0, src.height, TILE) for c in range(0, src.width, TILE)]
        used = 0
        with rasterio.open(tmp, "w", **prof) as dst:
            dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
            for i, (r, c) in enumerate(blocks):
                progress(0.02 + 0.9 * i / len(blocks), f"block {i + 1} of {len(blocks)}")
                w = Window(c, r, min(TILE, src.width - c), min(TILE, src.height - r))
                alpha = src.read(4, window=w)
                if not alpha.any():
                    continue
                x0, y1 = src.transform * (c, r)
                hw, hh = w.width * res / 2, w.height * res / 2
                near = [shots[j] for j in np.nonzero(np.hypot(centres[:, 0] - (x0 + hw), centres[:, 1] - (y1 - hh)) < REACH_M)[0]]
                rgb = np.moveaxis(src.read([1, 2, 3], window=w), 0, -1).copy()
                out_a = alpha.copy()
                if near:
                    z = _read_on(ground, src, w)
                    if np.isnan(z).all():
                        continue
                    z[np.isnan(z)] = np.nanmedian(z)
                    cols, rows = np.meshgrid(np.arange(w.width) + 0.5, np.arange(w.height) + 0.5)
                    pts = np.stack([x0 + cols.ravel() * res, y1 - rows.ravel() * res, z.ravel()], 1)
                    best = np.full(len(pts), np.inf, np.float32)
                    pick = np.full(len(pts), -1, np.int32)
                    maps = []
                    for k, s in enumerate(near):
                        P, depth = _project(s, pts)
                        ok = (depth > 0) & (P[:, 0] >= 2) & (P[:, 0] < s.W - 2) & (P[:, 1] >= 2) & (P[:, 1] < s.H - 2)
                        cost = np.hypot(P[:, 0] - s.W / 2, P[:, 1] - s.H / 2) / s.W + BLUR_PENALTY * (1 - s.sharpness)
                        cost[~ok] = np.inf
                        better = cost < best
                        best[better] = cost[better]
                        pick[better] = k
                        maps.append(P)
                    flat = rgb.reshape(-1, 3)
                    tall = np.zeros(len(pts), bool)
                    if chm is not None and chm.exists():
                        h = _read_on(chm, src, w, Resampling.max)
                        h = cv2.dilate(np.nan_to_num(h).astype(np.float32), np.ones((41, 41), np.uint8))   # ~1 m around tall crowns
                        tall = (h >= TALL_M).ravel()
                    for k in np.unique(pick[pick >= 0]):
                        m = (pick == k) & ~tall
                        if m.any():
                            img = cv2.imread(str(near[k].img))
                            flat[m] = _sample(img, maps[k][m])[:, ::-1]
                    used += int(((pick >= 0) & ~tall).sum())
                    rgb = flat.reshape(rgb.shape)
                dst.write(np.concatenate([np.moveaxis(rgb, -1, 0), out_a[None]], 0), window=w)
        progress(0.94, "building zoom levels")
        with rasterio.open(tmp, "r+") as dst:
            dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.average)
    tmp.replace(out)
    info = {"frames": len(shots), "seconds": round(time.time() - t0), "from_frames_m2": round(used * res * res),
            "tall_m": TALL_M, "built_at": round(time.time())}
    (out.parent / "sharp.json").write_text(json.dumps(info))
    return info
