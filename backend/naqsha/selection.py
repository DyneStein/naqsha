"""Frame selection: which video frames become photogrammetry images.

Pipeline per clip:
  1. quality scan  - one low-res decode pass: sharpness and brightness for every
                     `step`-th frame (cached).
  2. eligibility   - gimbal angle, height, flight phase, turn rate, GPS health,
                     blur and exposure filters.
  3. overlap pick  - walk the track; when footprint overlap with the last kept
                     frame drops to the target, keep the sharpest candidate in
                     the window just before that point.
  4. link check    - (after export) feature-match each consecutive pair. Weak
                     links get an intermediate frame; pairs whose image motion
                     disagrees with the GPS/IMU motion are flagged, which is
                     the typical signature of repetitive texture (tree canopy,
                     grass, tiled roofs) producing false matches.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from shapely import Polygon, make_valid

from .analysis import CleanTrack
from .geo import Intrinsics, ground_footprint
from .video import iter_lowres_gray


@dataclass
class SelectionParams:
    include_oblique: bool = False      # also use frames with gimbal between -60 and -30 deg
    nadir_pitch: float = -60.0
    oblique_pitch: float = -30.0
    min_hag: float = 8.0               # m above ground
    forward_overlap: float = 0.85      # target overlap between consecutive kept frames (canopy needs 85-90%)
    overlap_tolerance: float = 0.08    # window of acceptable overlap when picking sharpest
    max_heading_change: float = 15.0   # deg; force a new frame after this much rotation
    max_yaw_rate: float = 25.0         # deg/s; rotating faster smears and skews (rolling shutter)
    min_relative_sharpness: float = 0.55   # vs rolling median of the clip
    brightness_range: tuple = (25.0, 235.0)
    scan_step: int = 3                 # analyse every 3rd frame (20 Hz at 60 fps)
    max_frames: int = 0                # 0 = unlimited
    link_check: bool = True
    min_inliers: int = 60
    densify_rounds: int = 2
    motion_tolerance: float = 0.35     # fraction of expected shift allowed as disagreement

    @classmethod
    def from_dict(cls, d: dict | None) -> "SelectionParams":
        p = cls()
        for k, v in (d or {}).items():
            if hasattr(p, k):
                setattr(p, k, tuple(v) if k == "brightness_range" else v)
        return p

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ quality scan

def laplacian_sharpness(gray: np.ndarray) -> float:
    # centre 80% only: borders are softer on every lens and carry vignetting
    h, w = gray.shape
    g = gray[h // 10: h - h // 10, w // 10: w - w // 10]
    return float(cv2.Laplacian(g, cv2.CV_32F, ksize=3).var())


def quality_scan(video: Path, step: int, cache: Path,
                 progress: Callable[[float], None] | None = None, total_frames: int = 0) -> dict:
    if cache.exists():
        z = np.load(cache)
        if int(z["step"]) == step:
            return {k: z[k] for k in z.files}
    idx, sharp, bright = [], [], []
    for k, (i, gray) in enumerate(iter_lowres_gray(video, step)):
        idx.append(i)
        sharp.append(laplacian_sharpness(gray))
        bright.append(float(gray.mean()))
        if progress and total_frames and k % 50 == 0:
            progress(min(1.0, i / total_frames))
    res = {"step": np.array(step), "frame": np.array(idx), "sharp": np.array(sharp), "bright": np.array(bright)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **res)
    return res


def rolling_median(a: np.ndarray, half: int) -> np.ndarray:
    out = np.empty_like(a, dtype=float)
    for i in range(len(a)):
        out[i] = np.median(a[max(0, i - half): i + half + 1])
    return out


# ------------------------------------------------------------------ selection

@dataclass
class Candidate:
    clip_id: int
    frame: int
    sharp_rel: float
    poly: Polygon | None
    reason: str = ""


@dataclass
class ClipSelection:
    clip_id: int
    frames: list[int] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    eligible: int = 0
    scanned: int = 0


def _footprint(tr: CleanTrack, i: int, K: Intrinsics, range_factor: float = 3.0):
    pts = ground_footprint(tr.x[i], tr.y[i], tr.hag[i], tr.heading[i], tr.g_pitch[i], tr.g_roll[i],
                           K, min(120.0, range_factor * tr.hag[i]))
    if pts is None:
        return None
    p = make_valid(Polygon(pts))
    return p if p.area > 1 else None


def overlap(a, b) -> float:
    if a is None or b is None:
        return 0.0
    inter = a.intersection(b).area
    return inter / min(a.area, b.area)


def select_clip(tr: CleanTrack, K: Intrinsics, q: dict, p: SelectionParams) -> ClipSelection:
    res = ClipSelection(tr.clip_id)
    frames = q["frame"]
    sharp = q["sharp"]
    bright = q["bright"]
    res.scanned = len(frames)
    if len(frames) == 0:
        return res
    # sharpness relative to the local scene: texture varies (road vs canopy),
    # so absolute Laplacian variance is meaningless across a clip
    half = max(5, int(3.0 * tr.fps / p.scan_step))
    rel = sharp / np.maximum(rolling_median(sharp, half), 1e-6)

    max_pitch = p.oblique_pitch if p.include_oblique else p.nadir_pitch
    reasons = {}

    def reject(r):
        reasons[r] = reasons.get(r, 0) + 1

    ok_mask = []
    for k, i in enumerate(frames):
        if i >= len(tr.t):
            ok_mask.append(False); reject("beyond telemetry"); continue
        if np.isnan(tr.x[i]) or not tr.gps_ok[i]:
            ok_mask.append(False); reject("no GPS"); continue
        if tr.g_pitch[i] > max_pitch:
            ok_mask.append(False); reject("camera not pointing down enough"); continue
        if tr.hag[i] < p.min_hag:
            ok_mask.append(False); reject("too low (takeoff/landing)"); continue
        if tr.yaw_rate[i] > p.max_yaw_rate:
            ok_mask.append(False); reject("rotating fast"); continue
        if rel[k] < p.min_relative_sharpness:
            ok_mask.append(False); reject("motion blur"); continue
        if not (p.brightness_range[0] <= bright[k] <= p.brightness_range[1]):
            ok_mask.append(False); reject("bad exposure"); continue
        ok_mask.append(True)
    ok_mask = np.array(ok_mask)
    res.eligible = int(ok_mask.sum())
    res.rejected = reasons

    # contiguous eligible runs: a gap longer than 1 s starts a new strip
    elig = np.flatnonzero(ok_mask)
    if len(elig) == 0:
        return res
    runs, cur = [], [elig[0]]
    for a, b in zip(elig, elig[1:]):
        if (frames[b] - frames[a]) / tr.fps > 1.0:
            runs.append(cur); cur = [b]
        else:
            cur.append(b)
    runs.append(cur)

    polys: dict[int, Polygon | None] = {}

    def poly(k):
        if k not in polys:
            polys[k] = _footprint(tr, int(frames[k]), K)
        return polys[k]

    chosen: list[int] = []
    for run in runs:
        # first frame: sharpest within the first 0.5 s
        head = [k for k in run if frames[k] - frames[run[0]] <= 0.5 * tr.fps]
        last = max(head, key=lambda k: rel[k])
        chosen.append(last)
        window: list[int] = []   # candidates in the overlap band [target, target + tol)
        for k in run:
            if frames[k] <= frames[last]:
                continue
            ov = overlap(poly(last), poly(k))
            dh = abs(((tr.heading[frames[k]] - tr.heading[frames[last]]) + 180) % 360 - 180)
            if dh >= p.max_heading_change:
                # rotated: only frames from the last ~0.3 s share the new heading
                recent = [j for j in window + [k] if frames[k] - frames[j] <= 0.3 * tr.fps]
                pick = max(recent, key=lambda j: rel[j])
            elif ov < p.forward_overlap:
                # among candidates still meeting the overlap target, take the
                # furthest one that is about as sharp as the sharpest
                pool = window or [k]
                best = max(rel[j] for j in pool)
                pick = [j for j in pool if rel[j] >= 0.92 * best][-1]
            else:
                if ov < p.forward_overlap + p.overlap_tolerance:
                    window.append(k)
                continue
            chosen.append(pick)
            last = pick
            window = []
        # close the strip with its sharpest tail frame if the end adds coverage
        if window:
            tail = max(window, key=lambda j: rel[j])
            if overlap(poly(last), poly(tail)) < 0.95:
                chosen.append(tail)
    res.frames = sorted(set(int(frames[k]) for k in chosen))
    return res


def thin_to_cap(selections: list[ClipSelection], cap: int) -> list[str]:
    total = sum(len(s.frames) for s in selections)
    if cap <= 0 or total <= cap:
        return []
    keep_ratio = cap / total
    for s in selections:
        n = max(2, int(round(len(s.frames) * keep_ratio)))
        if n < len(s.frames):
            idx = np.linspace(0, len(s.frames) - 1, n).round().astype(int)
            s.frames = [s.frames[i] for i in sorted(set(idx))]
    return [f"frame cap {cap} reached: thinned {total} -> {sum(len(s.frames) for s in selections)} "
            "frames uniformly; overlap is now below target"]


# ------------------------------------------------------------------ link check

_sift = None


def _features(path: Path, width: int = 1600):
    global _sift
    if _sift is None:
        _sift = cv2.SIFT_create(nfeatures=4000)
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    scale = width / img.shape[1]
    img = cv2.resize(img, (width, int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    img = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(img)
    kp, des = _sift.detectAndCompute(img, None)
    return np.float32([k.pt for k in kp]), des, scale, img.shape


def match_pair(fa, fb, ratio: float = 0.75):
    pa, da, _, _ = fa
    pb, db, _, _ = fb
    if da is None or db is None or len(da) < 8 or len(db) < 8:
        return 0, None, None
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 64})
    m = flann.knnMatch(da, db, k=2)
    good = [a for a, b in (x for x in m if len(x) == 2) if a.distance < ratio * b.distance]
    if len(good) < 8:
        return len(good), None, None
    A = pa[[g.queryIdx for g in good]]
    B = pb[[g.trainIdx for g in good]]
    F, mask = cv2.findFundamentalMat(A, B, cv2.USAC_MAGSAC, 1.5, 0.999, 5000)
    if F is None or mask is None:
        return 0, None, None
    inl = mask.ravel().astype(bool)
    H, _ = cv2.findHomography(A[inl], B[inl], cv2.RANSAC, 4.0) if inl.sum() >= 8 else (None, None)
    return int(inl.sum()), H, (A[inl], B[inl])


def expected_shift_px(tr: CleanTrack, ia: int, ib: int, K: Intrinsics, scale: float):
    """Image-centre shift predicted from telemetry for a nadir pair, in check-image px.

    For a nadir camera, ground under the image centre of A appears in B
    displaced opposite to the camera motion, scaled by focal/height.
    """
    if tr.g_pitch[ia] > -75 or tr.g_pitch[ib] > -75:
        return None
    dx, dy = tr.x[ib] - tr.x[ia], tr.y[ib] - tr.y[ia]
    h = math.radians(tr.heading[ib])
    right = dx * math.cos(h) - dy * math.sin(h)
    fwd = dx * math.sin(h) + dy * math.cos(h)
    hag = max(tr.hag[ia], 1.0)
    f = K.focal_px * scale
    # image right = camera right, image down = -forward for a nadir camera
    du = -right * f / hag
    dv = fwd * f / hag
    # rotation between the two frames moves the centre too; small, ignored
    return du, dv


def check_links(images: list[dict], tracks: dict[int, CleanTrack], intr: dict[int, Intrinsics],
                p: SelectionParams, progress: Callable[[float], None] | None = None,
                known: dict | None = None) -> list[dict]:
    """Match consecutive kept frames within each clip. Returns one record per link.

    `known` caches match results by (clip, a, b) across densify rounds: only
    pairs that involve a newly added frame are matched again.
    """
    known = {} if known is None else known
    links = []
    by_clip: dict[int, list[dict]] = {}
    for im in images:
        by_clip.setdefault(im["clip"], []).append(im)
    total = sum(max(0, len(v) - 1) for v in by_clip.values()) or 1
    done = 0
    cache: dict[str, tuple] = {}

    def feats(im):
        key = im["path"]
        if key not in cache:
            cache[key] = _features(Path(im["path"]))
            if len(cache) > 6:
                cache.pop(next(iter(cache)))
        return cache[key]

    for clip, ims in by_clip.items():
        ims.sort(key=lambda d: d["frame"])
        tr = tracks[clip]
        recs = []
        for a, b in zip(ims, ims[1:]):
            key = (clip, a["frame"], b["frame"])
            if key in known:
                recs.append(dict(known[key]))
                done += 1
                continue
            fa, fb = feats(a), feats(b)
            inl, H, _ = match_pair(fa, fb)
            rec = {"clip": clip, "a": a["frame"], "b": b["frame"], "inliers": inl,
                   "status": "ok" if inl >= p.min_inliers else "weak"}
            exp = expected_shift_px(tr, a["frame"], b["frame"], intr[clip], fa[2])
            if H is not None and exp is not None and (b["frame"] - a["frame"]) / tr.fps < 5:
                h, w = fa[3]
                cb = cv2.perspectiveTransform(np.float32([[[w / 2, h / 2]]]), H)[0, 0]
                rec["expected_px"] = [round(float(exp[0]), 1), round(float(exp[1]), 1)]
                rec["observed_px"] = [round(float(cb[0] - w / 2), 1), round(float(cb[1] - h / 2), 1)]
            known[key] = {k_: v for k_, v in rec.items() if k_ not in ("motion_scale", "motion_error_px")}
            recs.append(rec)
            done += 1
            if progress:
                progress(done / total)
        # The telemetry prediction scales with focal/height-above-surface, and the
        # surface (canopy, terrain above the DEM) is unknown, so calibrate the
        # scale from strongly-matched pairs, then flag pairs that disagree with it.
        ratios = [math.hypot(*r["observed_px"]) / math.hypot(*r["expected_px"])
                  for r in recs if "expected_px" in r and r["inliers"] >= 4 * p.min_inliers
                  and math.hypot(*r["expected_px"]) > 3]
        scale = float(np.median(ratios)) if len(ratios) >= 3 else 1.0
        for r in recs:
            r["motion_scale"] = round(scale, 3)
            if "expected_px" not in r:
                continue
            ex, ey = (scale * v for v in r["expected_px"])
            ox, oy = r["observed_px"]
            mag = math.hypot(ex, ey)
            err = math.hypot(ox - ex, oy - ey)
            r["motion_error_px"] = round(err, 1)
            if mag > 15 and err > max(25.0, p.motion_tolerance * mag):
                r["status"] = "inconsistent"
        links.extend(recs)
    return links


def densify_requests(links: list[dict], q_by_clip: dict[int, dict], tracks: dict[int, CleanTrack],
                     already: dict[int, set]) -> dict[int, list[int]]:
    """For each weak or inconsistent link, pick the sharpest scanned frame near its midpoint."""
    req: dict[int, list[int]] = {}
    for ln in links:
        if ln["status"] == "ok":
            continue
        clip = ln["clip"]
        q = q_by_clip[clip]
        fr = q["frame"]
        a, b = ln["a"], ln["b"]
        if b - a < 3 * int(q["step"]):
            continue  # nothing in between to add
        mid = (a + b) / 2
        span = (b - a) / 4
        cand = [k for k in range(len(fr)) if abs(fr[k] - mid) <= span and fr[k] not in already[clip]]
        if not cand:
            continue
        best = int(fr[max(cand, key=lambda k: q["sharp"][k])])
        req.setdefault(clip, []).append(best)
    return req
