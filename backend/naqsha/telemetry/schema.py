"""Find telemetry fields in an unknown DJI protobuf layout by how they behave.

DJI uses a different .proto per model (dvtm_Mini4_Pro, dvtm_Air3, dvtm_wm265e,
...), and field numbers move between them. Rather than hard-coding each one,
every numeric leaf is treated as a candidate and roles are assigned by
behaviour, each check using the GPS track as ground truth:

  lat/lon      paired doubles in coordinate range under one parent
  velocity     floats whose horizontal magnitude follows GPS speed
  heading      the angle that follows the GPS course while moving
  rel. height  the value whose change follows the vertical velocity
  gimbal pitch the angle outside the aircraft-attitude group, most negative

Every assignment carries a score so callers can tell a solid inference from a
guess.
"""
from __future__ import annotations

import math

import numpy as np

from .protobuf import signed64

Path = tuple


def flatten(msg, prefix: Path = ()) -> dict:
    out = {}
    seen = set()
    for f, v in msg:
        if f in seen:
            continue            # first occurrence only, like get()
        seen.add(f)
        p = prefix + (f,)
        if isinstance(v, list):
            out.update(flatten(v, p))
        elif isinstance(v, (int, float)):
            out[p] = signed64(v) if isinstance(v, int) else v
    return out


def _arrays(frames: list, max_samples: int = 600):
    step = max(1, len(frames) // max_samples)
    idx = list(range(0, len(frames), step))
    flats = [flatten(frames[i]) for i in idx]
    paths = set()
    for f in flats:
        paths.update(f)
    is_float = {}
    arrs = {}
    for p in paths:
        vals = [f.get(p, 0) for f in flats]      # proto3: missing == 0
        is_float[p] = any(isinstance(f.get(p), float) for f in flats)
        arrs[p] = np.array(vals, float)
    return np.array(idx), arrs, is_float


def _angle_scale(a: np.ndarray, is_float: bool):
    m = np.abs(a).max() if len(a) else 0
    if is_float and m <= 360.5:
        return 1.0
    if not is_float and m <= 3600.5:
        return 0.1
    return None


def infer(frames: list, fps: float) -> dict:
    idx, A, isf = _arrays(frames)
    t = idx / fps
    out: dict = {"paths": {}, "scores": {}}

    # --- lat/lon
    best = None
    for p in A:
        if not isf[p] or len(p) < 1:
            continue
        sib = p[:-1] + (p[-1] + 1,)
        if sib not in A or not isf[sib]:
            continue
        la, lo = A[p], A[sib]
        ok = (la != 0) & (np.abs(la) <= 90) & (np.abs(lo) <= 180) & (lo != 0)
        if ok.mean() < 0.5:
            continue
        spread = max(np.ptp(la[ok]), np.ptp(lo[ok]))
        if spread > 1.0:            # a flight does not cross a degree
            continue
        score = ok.mean()
        if best is None or score > best[0]:
            best = (score, p, sib)
    if best is None:
        return out
    out["paths"]["lat"], out["paths"]["lon"] = best[1], best[2]
    out["scores"]["latlon"] = round(best[0], 3)
    la, lo = A[best[1]], A[best[2]]
    ok = (la != 0) & (lo != 0)
    y = (la - np.median(la[ok])) * 111320.0
    x = (lo - np.median(lo[ok])) * 111320.0 * math.cos(math.radians(np.median(la[ok])))
    dt = np.gradient(t)
    gvn, gve = np.gradient(y) / dt, np.gradient(x) / dt
    gspeed = np.hypot(gvn, gve)
    moving = ok & (gspeed > 2.0) & (gspeed < 30)
    course = np.degrees(np.arctan2(gve, gvn))

    # --- velocity: siblings (a, b) with hypot following GPS speed
    vbest = None
    if moving.sum() > 10:
        for p in A:
            if not isf[p] or p in (best[1], best[2]):
                continue
            q = p[:-1] + (p[-1] + 1,)
            if q not in A or not isf[q]:
                continue
            a, b = A[p], A[q]
            if np.abs(a).max() > 40 or np.abs(b).max() > 40:
                continue
            err = np.median(np.abs(np.hypot(a, b)[moving] - gspeed[moving]))
            corr_n = np.corrcoef(a[moving], gvn[moving])[0, 1] if np.std(a[moving]) > 0 else 0
            corr_e = np.corrcoef(b[moving], gve[moving])[0, 1] if np.std(b[moving]) > 0 else 0
            score = (corr_n + corr_e) / 2 - err / 5
            if vbest is None or score > vbest[0]:
                vbest = (score, p, q)
    if vbest and vbest[0] > 0.6:
        out["paths"]["vn"], out["paths"]["ve"] = vbest[1], vbest[2]
        vd = vbest[2][:-1] + (vbest[2][-1] + 1,)
        if vd in A:
            out["paths"]["vd"] = vd
        out["scores"]["velocity"] = round(float(vbest[0]), 3)

    # --- heading: angle following the GPS course
    hbest = None
    if moving.sum() > 10:
        for p in A:
            s = _angle_scale(A[p], isf[p])
            if s is None or p in out["paths"].values():
                continue
            a = A[p] * s
            d = np.abs(((a[moving] - course[moving]) + 180) % 360 - 180)
            med = float(np.median(d))
            if hbest is None or med < hbest[0]:
                hbest = (med, p, s)
    if hbest and hbest[0] < 20:
        out["paths"]["yaw"] = hbest[1]
        out["scales"] = {"yaw": hbest[2]}
        out["scores"]["heading_err_deg"] = round(hbest[0], 2)
        attitude_parent = hbest[1][:-1]
    else:
        attitude_parent = None

    # --- relative height: follows -integral of vertical velocity, or is the
    # smooth non-negative distance closest to (altitude - takeoff)
    rbest = None
    vd_arr = A.get(out["paths"].get("vd")) if out["paths"].get("vd") else None
    climbing = np.abs(vd_arr) > 0.3 if vd_arr is not None else None
    for p in A:
        if p in out["paths"].values() or np.ptp(A[p]) <= 0:
            continue
        a = A[p]
        if climbing is not None and climbing.sum() >= 10:
            raw_rate = np.gradient(a) / dt
            c = np.corrcoef(raw_rate[climbing], -vd_arr[climbing])[0, 1]
            if not np.isfinite(c) or c < 0.5:
                continue
            # scale = regression slope of (-vd) on the raw rate; snap to a power of ten
            k = float(np.sum(raw_rate[climbing] * -vd_arr[climbing]) / np.sum(raw_rate[climbing] ** 2))
            if k <= 0:
                continue
            scale = 10.0 ** round(math.log10(k))
            fit = abs(math.log10(k / scale))      # 0 when the rate matches exactly
            # absolute altitude follows climb rate just as well; height above
            # takeoff is the smaller of the two, so prefer lower values slightly
            score = c - fit - 0.001 * float(np.median(a * scale)) / 100
        else:
            # no vertical motion to calibrate against: values must look like metres
            scale = 1.0 if a.max() <= 1000 else 0.001
            h = a * scale
            if h.max() > 1000 or h.min() < -50 or not isf.get(p, False) and scale == 1.0 and h.max() > 500:
                continue
            score = 0.35 if (h.min() >= -2 and np.ptp(h) >= 2) else 0
        if rbest is None or score > rbest[0]:
            rbest = (score, p, scale)
    if rbest and rbest[0] > 0.3:
        out["paths"]["rel_h"] = rbest[1]
        out.setdefault("scales", {})["rel_h"] = rbest[2]
        out["scores"]["rel_h"] = round(float(rbest[0]), 3)

    # --- gimbal pitch: an angle outside the attitude group, most negative
    gbest = None
    for p in A:
        if p in out["paths"].values() or (attitude_parent is not None and p[:-1] == attitude_parent):
            continue
        s = _angle_scale(A[p], isf[p])
        if s is None:
            continue
        a = A[p] * s
        if a.min() < -91 or a.max() > 35 or np.ptp(a) == 0 and a[0] == 0:
            continue
        # gimbal pitch moves in steps and sits between -90 and ~0
        med = float(np.median(a))
        if med > 5:
            continue
        cand = (med, p, s)
        if gbest is None or cand[0] < gbest[0]:
            gbest = cand
    if gbest:
        out["paths"]["g_pitch"] = gbest[1]
        out.setdefault("scales", {})["g_pitch"] = gbest[2]
        out["scores"]["g_pitch_median"] = round(gbest[0], 1)
    return out


def to_layout(result: dict) -> dict:
    """Inference result -> layout {role: (path, scale)} usable by the decoder."""
    paths, scales = result.get("paths", {}), result.get("scales", {})
    lay = {}
    for role, path in paths.items():
        lay[role] = (tuple(path), scales.get(role, 1.0))
    return lay


def confidence(result: dict) -> float:
    sc = result.get("scores", {})
    c = 0.0
    c += 1.0 if sc.get("latlon", 0) > 0.5 else 0
    c += 1.0 if sc.get("velocity", 0) > 0.6 else 0
    c += 1.0 if sc.get("heading_err_deg", 99) < 10 else 0
    c += 1.0 if sc.get("rel_h", 0) > 0.6 else 0
    return c
