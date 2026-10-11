"""Split a frame set into overlapping spatial chunks that fit in memory.

Recursive bisection along the principal axis of camera positions keeps chunks
compact (short seams, strong internal geometry). Each chunk's core is then
grown by every frame within `overlap_m` of it so neighbouring reconstructions
share images: the shared cameras are later used to register the chunks to each
other, and the shared ground gives room for seamline blending.
"""
from __future__ import annotations

import numpy as np
from shapely import MultiPoint


def _bisect(idx: np.ndarray, xy: np.ndarray, max_core: int) -> list[np.ndarray]:
    if len(idx) <= max_core:
        return [idx]
    pts = xy[idx]
    c = pts - pts.mean(0)
    # principal axis
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    proj = c @ vt[0]
    order = np.argsort(proj)
    k = int(np.ceil(len(idx) / max_core))
    # split so both halves are multiples of the target size where possible
    cut = len(idx) * (k // 2) // k
    cut = min(max(cut, 1), len(idx) - 1)
    return _bisect(idx[order[:cut]], xy, max_core) + _bisect(idx[order[cut:]], xy, max_core)


def plan(xy: np.ndarray, max_images: int, overlap_m: float, min_overlap_m: float = 8.0,
         core_idx: np.ndarray | None = None) -> list[dict]:
    """Return [{"core": [i...], "members": [i...]}] covering all frames.

    members never exceed max_images; overlap shrinks if needed to fit. With
    core_idx, only those frames are partitioned (re-planning the unfinished
    part of a job) while overlap members may come from any frame, so new parts
    still share photos with parts that already finished.
    """
    n = len(xy)
    todo = np.arange(n) if core_idx is None else np.asarray(core_idx)
    if len(todo) <= max_images and core_idx is None:
        return [{"core": list(range(n)), "members": list(range(n)), "overlap_m": 0.0}]
    max_core = max(8, int(max_images * 0.7))
    cores = _bisect(todo, xy, max_core)
    chunks = []
    for core in cores:
        hull = MultiPoint(xy[core]).convex_hull
        ov = overlap_m
        while True:
            zone = hull.buffer(ov)
            d = np.array([zone.distance(_pt(p)) for p in xy]) if ov > 0 else None
            members = sorted(set(core.tolist()) | set(np.flatnonzero(d <= 0).tolist())) if d is not None \
                else sorted(core.tolist())
            if len(members) <= max_images or ov <= min_overlap_m:
                break
            ov *= 0.7
        if len(members) > max_images:
            # keep the nearest extras only
            extra = [i for i in members if i not in set(core.tolist())]
            dist = [hull.distance(_pt(xy[i])) for i in extra]
            keep = [extra[j] for j in np.argsort(dist)[: max(0, max_images - len(core))]]
            members = sorted(set(core.tolist()) | set(keep))
        chunks.append({"core": sorted(core.tolist()), "members": members, "overlap_m": round(ov, 1)})
    return chunks


def _pt(p):
    from shapely import Point
    return Point(float(p[0]), float(p[1]))


def solve_offsets(pairs: list[tuple[int, int, np.ndarray]], n_chunks: int) -> tuple[np.ndarray, float]:
    """Robust least-squares translation per chunk from shared-camera differences.

    pairs: (i, j, d) with d = position_in_j - position_in_i for one shared
    camera. Corrections t satisfy t_j - t_i = -d, with the mean correction fixed
    at zero so the georeference stays the average of all chunks. Cameras at a
    chunk's edge are weakly constrained and can disagree by metres, so pairs
    whose residual exceeds 3x the robust spread are dropped and the solve is
    repeated. Returns (offsets[n,3], median horizontal residual after, metres).
    """
    if not pairs:
        return np.zeros((n_chunks, 3)), 0.0
    rows, rhs = [], []
    for i, j, d in pairs:
        r = np.zeros(n_chunks)
        r[j] += 1
        r[i] -= 1
        rows.append(r)
        rhs.append(-np.asarray(d, float))
    R, B = np.array(rows), np.array(rhs)
    keep = np.ones(len(R), bool)
    for _ in range(5):
        A = np.vstack([R[keep], np.ones(n_chunks) / n_chunks])
        Bk = np.vstack([B[keep], np.zeros(3)])
        off, *_ = np.linalg.lstsq(A, Bk, rcond=None)
        res = np.linalg.norm((R @ off - B)[:, :2], axis=1)
        med = np.median(res[keep])
        mad = np.median(np.abs(res[keep] - med)) * 1.4826 + 1e-6
        new_keep = res <= med + 3 * mad + 0.05
        if (new_keep == keep).all():
            break
        keep = new_keep
    return off, float(np.median(res[keep]))
