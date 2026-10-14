"""Tree counting on a finished map.

  candidates  - DeepForest (Weinstein et al. 2019, RetinaNet trained on hand-labelled
                airborne crowns) on the orthophoto resampled to 10 cm, the scale it was
                trained at; treetops from the canopy height model (canopy.py)
  methods     - three ways to turn candidates into trees: image only, image checked
                against height, height only. Every rule that is not certain is a method
                choice, never a silent filter
  plots       - squares where a person marked every tree by hand. They pick the method
                and score threshold with the best F1, and give precision and recall
  estimate    - detected x precision / recall, with Wilson intervals, so the count is
                reported with its measured error instead of as a bare number
  fine-tune   - the marked squares can also retrain DeepForest on this site's trees

Nothing here is guessed: every number traces to the image, the height model or a hand
count.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import cv2
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from . import canopy

DETECT_RES = 0.10          # m per pixel; DeepForest's training scale
SITE_RES = 0.05            # taught models: saplings ~1 m across become ~20 px, inside the detector's smallest anchor
SHARP_RES = 0.025          # taught on the sharp map (sharp.py): plants 0.4 m across are still ~16 px
MEASURABLE_H = 1.0         # below this a detected crown's height is reported as not measurable:
                           # under closed canopy the ground is hidden and the height model reads ~0
PATCH = 400                # px per model window
OVERLAP = 100              # px shared by neighbouring windows
MIN_SCORE = 0.08           # keep low-confidence boxes; the threshold is chosen later
GREEN_MIN = 0.30           # share of a crown that must look like vegetation
THRESHOLDS = [round(x, 2) for x in np.arange(0.10, 0.61, 0.05)]
METHODS = {
    "image": "Image model only",
    "image+height": "Image model checked against tree height",
    "height": "Treetops from the height model only",
}


# ------------------------------------------------------------------ helpers

def _veg_fraction(rgb: np.ndarray) -> float:
    """Share of pixels whose colour reads as vegetation (green chromatic coordinate,
    excess-green), ignoring empty and very dark pixels."""
    a = rgb.reshape(-1, 3).astype(np.float32)
    s = a.sum(1)
    ok = s > 60
    if not ok.any():
        return 0.0
    r, g, b = (a[ok] / s[ok, None]).T
    veg = (g > 0.355) | ((2 * g - r - b) > 0.04)
    return float(veg.mean())


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.3) -> np.ndarray:
    import torch
    from torchvision.ops import nms
    if not len(boxes):
        return np.zeros(0, int)
    return nms(torch.as_tensor(boxes, dtype=torch.float32), torch.as_tensor(scores, dtype=torch.float32), iou).numpy()


def treetops(chm: np.ndarray, res: float, min_h: float = 2.0) -> np.ndarray:
    """Local maxima of the smoothed canopy height model with a window that grows with
    tree height (taller trees have wider crowns). Returns (row, col, height) rows."""
    c = np.nan_to_num(chm, nan=0.0).astype(np.float32)
    s = cv2.GaussianBlur(c, (0, 0), max(0.5, 0.35 / res))
    tops = np.zeros(s.shape, bool)
    for r_m, lo, hi in [(1.0, 0, 6), (1.5, 6, 10), (2.0, 10, 15), (2.5, 15, 99)]:
        k = max(1, int(round(r_m / res)))
        mx = cv2.dilate(s, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
        tops |= (s >= mx - 1e-4) & (s >= lo) & (s < hi)
    tops &= s >= min_h
    rr, cc = np.nonzero(tops)
    # smoothing finds the tops; the height itself comes from the unsmoothed model nearby
    k = max(1, int(round(0.6 / res)))
    raw_max = cv2.dilate(c, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    return np.column_stack([rr, cc, raw_max[rr, cc]]) if len(rr) else np.zeros((0, 3))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


# ------------------------------------------------------------------ detection

def model_res(model_dir: Path | None) -> float:
    if model_dir is not None and (model_dir / "model.json").exists():
        return float(json.loads((model_dir / "model.json").read_text()).get("res", DETECT_RES))
    return DETECT_RES


def load_model(model_dir: Path | None = None):
    from deepforest import main
    m = main.deepforest()
    if model_dir is not None and (model_dir / "model.pt").exists():
        import torch
        m.load_model()
        m.model.load_state_dict(torch.load(model_dir / "model.pt", map_location="cpu"))
    else:
        m.load_model()   # weecology/deepforest-tree, cached after the first download
    m.model.eval()
    return m


def detect_image(ortho: Path, model, bounds=None, progress=lambda f, m=None: None, res: float = DETECT_RES) -> list[dict]:
    """Run the crown detector over every part of the orthophoto that has data.
    Returns boxes in map coordinates (metres, the orthophoto's CRS)."""
    with rasterio.open(ortho) as ds:
        b = bounds or ds.bounds
        step_m = (PATCH - OVERLAP) * res
        size_m = PATCH * res
        xs = np.arange(b[0], b[2], step_m)
        ys = np.arange(b[3], b[1], -step_m)
        cells = [(x, y) for y in ys for x in xs]
        alpha_band = ds.count if ds.count >= 4 else None
        out = []
        for i, (x0, y1) in enumerate(cells):
            progress(i / len(cells), f"window {i + 1} of {len(cells)}")
            win = from_bounds(x0, y1 - size_m, x0 + size_m, y1, ds.transform)
            if alpha_band:
                a = ds.read(alpha_band, window=win, out_shape=(PATCH // 4, PATCH // 4), boundless=True, fill_value=0)
                if (a > 0).mean() < 0.05:
                    continue
            rgb = ds.read([1, 2, 3], window=win, out_shape=(3, PATCH, PATCH), boundless=True, fill_value=0,
                          resampling=Resampling.average)
            img = np.ascontiguousarray(np.moveaxis(rgb, 0, -1))
            df = model.predict_image(image=img.astype("float32"))
            if df is None or not len(df):
                continue
            mask = None
            if alpha_band:
                mask = ds.read(alpha_band, window=win, out_shape=(PATCH, PATCH), boundless=True, fill_value=0) > 0
            for r in df.itertuples():
                x_a, y_a, x_b, y_b = float(r.xmin), float(r.ymin), float(r.xmax), float(r.ymax)
                # drop boxes cut by the window edge; the neighbouring window sees them whole
                edge = OVERLAP / 2
                cx, cy = (x_a + x_b) / 2, (y_a + y_b) / 2
                if (cx < edge and x0 > b[0]) or (cy < edge and y1 < b[3]) or \
                   (cx > PATCH - edge and x0 + step_m < b[2]) or (cy > PATCH - edge and y1 - step_m > b[1]):
                    continue
                if mask is not None and not mask[int(min(cy, PATCH - 1)), int(min(cx, PATCH - 1))]:
                    continue
                crop = img[int(y_a):int(y_b) + 1, int(x_a):int(x_b) + 1]
                out.append({
                    "x0": x0 + x_a * res, "x1": x0 + x_b * res,
                    "y0": y1 - y_b * res, "y1": y1 - y_a * res,
                    "score": round(float(r.score), 3), "veg": round(_veg_fraction(crop), 2),
                })
    if out:
        bx = np.array([[d["x0"], d["y0"], d["x1"], d["y1"]] for d in out])
        keep = _nms(bx, np.array([d["score"] for d in out]))
        out = [out[i] for i in sorted(keep)]
    return out


# ------------------------------------------------------------------ methods

def _chm_sampler(chm_path: Path):
    ds = rasterio.open(chm_path)
    a = ds.read(1).astype(np.float32)
    a[a < -1] = np.nan
    inv = ~ds.transform

    def box_stats(x0, y0, x1, y1):
        c0, r0 = inv * (x0, y1)
        c1, r1 = inv * (x1, y0)
        r0, r1 = max(0, int(r0)), min(a.shape[0], int(math.ceil(r1)))
        c0, c1 = max(0, int(c0)), min(a.shape[1], int(math.ceil(c1)))
        v = a[r0:r1, c0:c1]
        v = v[np.isfinite(v)]
        return float(np.percentile(v, 95)) if v.size else None
    return a, ds.transform, box_stats, ds


def build_candidates(ortho: Path, chm_path: Path, boxes: list[dict], min_h: float = 2.0) -> dict:
    """Attach heights to image boxes and find treetops; everything a method needs."""
    chm, tf, box_h, ds = _chm_sampler(chm_path)
    res = float(tf.a)
    tops = treetops(chm, res, min_h=min_h)
    top_xy = np.array([tf * (c + 0.5, r + 0.5) for r, c, _ in tops]) if len(tops) else np.zeros((0, 2))
    top_h = tops[:, 2] if len(tops) else np.zeros(0)
    # vegetation check for treetops: sample the orthophoto around each top
    top_veg = np.zeros(len(top_xy))
    with rasterio.open(ortho) as od:
        for i, (x, y) in enumerate(top_xy):
            w = from_bounds(x - 0.6, y - 0.6, x + 0.6, y + 0.6, od.transform)
            rgb = od.read([1, 2, 3], window=w, out_shape=(3, 12, 12), boundless=True, fill_value=0)
            top_veg[i] = _veg_fraction(np.moveaxis(rgb, 0, -1))
    for d in boxes:
        d["h"] = None if (h := box_h(d["x0"], d["y0"], d["x1"], d["y1"])) is None else round(h, 1)
        inside = np.nonzero((top_xy[:, 0] >= d["x0"]) & (top_xy[:, 0] <= d["x1"]) &
                            (top_xy[:, 1] >= d["y0"]) & (top_xy[:, 1] <= d["y1"]))[0] if len(top_xy) else []
        d["tops"] = [int(i) for i in inside]
    ds.close()
    return {"boxes": boxes,
            "tops": [{"x": round(float(x), 2), "y": round(float(y), 2), "h": round(float(h), 1), "veg": round(float(v), 2)}
                     for (x, y), h, v in zip(top_xy, top_h, top_veg)],
            "min_h": min_h}


def apply_method(cand: dict, method: str, threshold: float) -> list[dict]:
    """Turn candidates into a list of trees {x, y, h, crown, source}."""
    boxes, tops, min_h = cand["boxes"], cand["tops"], cand["min_h"]
    trees: list[dict] = []
    if method == "height":
        for t in tops:
            if t["veg"] >= GREEN_MIN:
                trees.append({"x": t["x"], "y": t["y"], "h": t["h"], "crown": None, "source": "height"})
        return trees
    used_tops: set[int] = set()
    for d in boxes:
        if d["score"] < threshold or d["veg"] < GREEN_MIN:
            continue
        crown = round(((d["x1"] - d["x0"]) + (d["y1"] - d["y0"])) / 2, 1)
        if method == "image+height":
            if d["h"] is not None and d["h"] < min_h:
                continue                     # shrub or grass
            tall = [i for i in d["tops"] if tops[i]["h"] >= min_h]
            used_tops.update(d["tops"])
            if len(tall) >= 2:
                pts = np.array([[tops[i]["x"], tops[i]["y"]] for i in tall])
                gap = min(np.linalg.norm(pts[a] - pts[b]) for a in range(len(pts)) for b in range(a + 1, len(pts)))
                if gap >= 2.5:               # two separate crowns inside one box
                    for i in tall:
                        trees.append({"x": tops[i]["x"], "y": tops[i]["y"], "h": tops[i]["h"],
                                      "crown": round(crown / math.sqrt(len(tall)), 1), "source": "image split by height",
                                      "score": d["score"]})
                    continue
        trees.append({"x": round((d["x0"] + d["x1"]) / 2, 2), "y": round((d["y0"] + d["y1"]) / 2, 2), "h": d["h"],
                      "crown": crown, "source": "image", "score": d["score"],
                      "box": [round(d["x0"], 2), round(d["y0"], 2), round(d["x1"], 2), round(d["y1"], 2)]})
    if method == "image+height":
        for i, t in enumerate(tops):
            if i not in used_tops and t["h"] >= min_h + 1 and t["veg"] >= GREEN_MIN:
                trees.append({"x": t["x"], "y": t["y"], "h": t["h"], "crown": None, "source": "height (missed by image)"})
    return trees


# ------------------------------------------------------------------ accuracy from hand-marked squares

def _inside(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    if not len(pts):
        return np.zeros(0, bool)
    from matplotlib.path import Path as MplPath
    return MplPath(poly).contains_points(pts)


def match(pred: np.ndarray, truth: np.ndarray, radius) -> int:
    """Greedy one-to-one matching of detections to hand marks, closest pairs first.
    `radius` is one distance or one per detection (big crowns get more room)."""
    if not len(pred) or not len(truth):
        return 0
    d = np.linalg.norm(pred[:, None, :] - truth[None, :, :], axis=2)
    r = np.broadcast_to(np.asarray(radius, float).reshape(-1, 1) if np.ndim(radius) else radius, d.shape)
    pairs = np.argwhere(d <= r)
    order = np.argsort(d[pairs[:, 0], pairs[:, 1]])
    used_p, used_t, tp = set(), set(), 0
    for k in order:
        i, j = pairs[k]
        if i in used_p or j in used_t:
            continue
        used_p.add(i); used_t.add(j); tp += 1
    return tp


def match_radius(t: dict) -> float:
    """A mark matches a detected tree when it lies within its crown: half the crown
    width, at least 2 m (marks and crown centres never coincide exactly), at most 4 m."""
    c = t.get("crown")
    return float(min(4.0, max(2.0, c / 2))) if c else 2.5


def score_plots(trees: list[dict], plots: list[dict], radius: float | None = None) -> dict:
    """Pool true/false positives over every hand-marked square."""
    pts = np.array([[t["x"], t["y"]] for t in trees]) if trees else np.zeros((0, 2))
    rad = np.array([match_radius(t) for t in trees]) if trees else np.zeros(0)
    tp = fp = fn = 0
    per = []
    for p in plots:
        poly = np.array(p["poly_m"])
        sel = _inside(poly, pts)
        inside = pts[sel]
        truth = np.array(p["marks_m"]) if p["marks_m"] else np.zeros((0, 2))
        k = match(inside, truth, radius if radius is not None else rad[sel])
        per.append({"id": p["id"], "found": int(len(inside)), "marked": int(len(truth)), "matched": k})
        tp += k; fp += len(inside) - k; fn += len(truth) - k
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec, "f1": f1, "per_plot": per}


def design_estimate(plots: list[dict], area_m2: float | None, cells: np.ndarray | None = None) -> dict | None:
    """Trees on the whole map from randomly placed squares alone, no model involved:
    mean density in the squares x mapped area, with a Student-t 95% interval.

    With `cells` (centres of the mapped ground on a grid), each square is weighted by
    the share of the map closer to it than to any other square. Random squares can
    bunch up in one part of a map by chance (or be added there later); unweighted, that
    part would count for more than its area."""
    if not area_m2 or len(plots) < 2:
        return None
    from scipy import stats
    dens = np.array([len(p["marks_m"]) / (p["size_m"] ** 2) for p in plots])
    n = len(dens)
    w = np.full(n, 1 / n)
    if cells is not None and len(cells):
        from scipy.spatial import cKDTree
        ctr = np.array([np.mean(np.array(p["poly_m"]), 0) for p in plots])
        _, near = cKDTree(ctr).query(cells)
        w = np.bincount(near, minlength=n) / len(cells)
    m = float((w * dens).sum())
    # variance of a weighted mean of independent squares; equals s^2/n when weights are equal
    s2 = float((w * (dens - m) ** 2).sum()) / max(1e-9, 1 - float((w ** 2).sum()))
    se = math.sqrt(float((w ** 2).sum()) * s2)
    tq = stats.t.ppf(0.975, n - 1)
    return {"count": round(m * area_m2), "low": max(0, round((m - tq * se) * area_m2)), "high": round((m + tq * se) * area_m2),
            "squares": n, "per_ha": round(m * 1e4, 1), "weighted": cells is not None}


def estimate(n_detected: int, sc: dict) -> dict:
    """Total corrected for measured error: true trees ~ detected x precision / recall."""
    tp, fp, fn = sc["tp"], sc["fp"], sc["fn"]
    if tp == 0:
        return {"count": None, "low": None, "high": None}
    p_lo, p_hi = wilson(tp, tp + fp)
    r_lo, r_hi = wilson(tp, tp + fn)
    mid = n_detected * sc["precision"] / sc["recall"]
    return {"count": round(mid), "low": round(n_detected * p_lo / max(r_hi, 1e-6)),
            "high": round(n_detected * p_hi / max(r_lo, 1e-6))}


def choose(cand: dict, plots: list[dict]) -> dict:
    """Pick the method and threshold with the best F1 on the marked squares."""
    best = None
    table = []
    for method in METHODS:
        for thr in (THRESHOLDS if method != "height" else [0.0]):
            trees = apply_method(cand, method, thr)
            sc = score_plots(trees, plots)
            table.append({"method": method, "threshold": thr, "f1": round(sc["f1"], 3),
                          "precision": round(sc["precision"], 3), "recall": round(sc["recall"], 3), "n": len(trees)})
            # ties keep the earlier (simpler) method and lower cut-off
            if best is None or sc["f1"] > best[0]["f1"] + 1e-9:
                best = (sc, method, thr, trees)
    sc, method, thr, trees = best
    return {"method": method, "threshold": thr, "score": sc, "trees": trees, "table": table}


# ------------------------------------------------------------------ outputs

def _to_wgs(crs) -> Transformer:
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def density_grid(trees: list[dict], cell: float = 20.0) -> list[dict]:
    if not trees:
        return []
    pts = np.array([[t["x"], t["y"]] for t in trees])
    gx = np.floor(pts[:, 0] / cell).astype(int)
    gy = np.floor(pts[:, 1] / cell).astype(int)
    out = {}
    for a, b in zip(gx, gy):
        out[(a, b)] = out.get((a, b), 0) + 1
    return [{"x0": a * cell, "y0": b * cell, "n": n, "per_ha": round(n * 10000 / (cell * cell))} for (a, b), n in out.items()]


def write_outputs(out_dir: Path, crs, trees: list[dict], summary: dict) -> None:
    tf = _to_wgs(crs)
    feats, crowns = [], []
    for i, t in enumerate(trees, 1):
        lon, lat = tf.transform(t["x"], t["y"])
        h = t.get("h")
        props = {"id": i, "height_m": h if h is not None and h >= MEASURABLE_H else None, "crown_m": t.get("crown"), "source": t["source"],
                 "score": t.get("score")}
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]}})
        if t.get("crown"):
            r = t["crown"] / 2
            ring = [tf.transform(t["x"] + r * math.cos(a), t["y"] + r * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 17)]
            crowns.append({"type": "Feature", "properties": props,
                           "geometry": {"type": "Polygon", "coordinates": [[[round(x, 7), round(y, 7)] for x, y in ring]]}})
    dens = []
    for c in density_grid(trees):
        ring = [tf.transform(c["x0"] + dx, c["y0"] + dy) for dx, dy in [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]]
        dens.append({"type": "Feature", "properties": {"trees": c["n"], "per_ha": c["per_ha"]},
                     "geometry": {"type": "Polygon", "coordinates": [[[round(x, 7), round(y, 7)] for x, y in ring]]}})
    fc = lambda f: {"type": "FeatureCollection", "features": f}  # noqa: E731
    (out_dir / "trees.geojson").write_text(json.dumps(fc(feats)))
    (out_dir / "crowns.geojson").write_text(json.dumps(fc(crowns)))
    (out_dir / "density.geojson").write_text(json.dumps(fc(dens)))
    with open(out_dir / "trees.csv", "w") as f:
        f.write("id,lat,lon,height_m,crown_m,source,score\n")
        for ft in feats:
            p = ft["properties"]; lon, lat = ft["geometry"]["coordinates"]
            f.write(f"{p['id']},{lat},{lon},{'' if p['height_m'] is None else p['height_m']},"
                    f"{'' if p['crown_m'] is None else p['crown_m']},{p['source']},{'' if p['score'] is None else p['score']}\n")
    kml = ['<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Trees</name>']
    for ft in feats:
        p = ft["properties"]; lon, lat = ft["geometry"]["coordinates"]
        kml.append(f"<Placemark><name>{p['id']}</name><description>height {p['height_m']} m, crown {p['crown_m']} m, "
                   f"{p['source']}</description><Point><coordinates>{lon},{lat}</coordinates></Point></Placemark>")
    kml.append("</Document></kml>")
    (out_dir / "trees.kml").write_text("".join(kml))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))


HEIGHT_CLASSES = [("5 m and taller", 5.0, None), ("3 to 5 m", 3.0, 5.0), ("1 to 3 m: young trees and bushes", MEASURABLE_H, 3.0)]


def height_classes(trees: list[dict]) -> list[dict]:
    """Counts by measured height, so 'how many trees' can be read under any sensible
    definition (big trees only, or everything woody) without recounting."""
    out = []
    for name, lo, hi in HEIGHT_CLASSES:
        n = sum(1 for t in trees if t.get("h") is not None and t["h"] >= lo and (hi is None or t["h"] < hi))
        out.append({"name": name, "min_m": lo, "count": n})
    out.append({"name": "height not measurable", "min_m": None,
                "count": sum(1 for t in trees if t.get("h") is None or t["h"] < MEASURABLE_H)})
    return out


def height_hist(trees: list[dict], step: float = 0.5, top: float = 40.0) -> dict:
    """Trees per 0.5 m of height, so the app can count 'at least X m' instantly."""
    edges = np.arange(MEASURABLE_H, top + step, step)
    hs = np.array([t["h"] for t in trees if t.get("h") is not None and t["h"] >= MEASURABLE_H])
    counts = np.histogram(np.clip(hs, MEASURABLE_H, top), bins=np.append(edges, np.inf))[0] if len(hs) else np.zeros(len(edges), int)
    return {"from_m": MEASURABLE_H, "step_m": step, "counts": [int(c) for c in counts],
            "unmeasured": int(sum(1 for t in trees if t.get("h") is None or t["h"] < MEASURABLE_H))}


def filter_features(fc: dict, min_h: float) -> dict:
    """Trees (or crowns) at least min_h tall. Trees whose height could not be measured
    are kept only when no height limit is asked for."""
    if min_h <= 0:
        return fc
    return {"type": "FeatureCollection", "features": [f for f in fc["features"]
            if f["properties"].get("height_m") is not None and f["properties"]["height_m"] >= min_h]}


def summarise(trees: list[dict], area_m2: float | None, chosen: dict | None, method: str, threshold: float,
              extra: dict) -> dict:
    hs = [t["h"] for t in trees if t.get("h") is not None and t["h"] >= MEASURABLE_H]
    s = {"count": len(trees), "method": method, "method_name": METHODS[method], "threshold": threshold,
         "height_median_m": round(float(np.median(hs)), 1) if hs else None,
         "height_p90_m": round(float(np.percentile(hs, 90)), 1) if hs else None,
         "per_ha": round(len(trees) * 10000 / area_m2, 1) if area_m2 else None,
         "height_measured": len(hs), "by_source": {}, "checked": chosen is not None, "updated": time.time(),
         "height_classes": height_classes(trees), "height_hist": height_hist(trees), "area_m2": area_m2, **extra}
    for t in trees:
        s["by_source"][t["source"]] = s["by_source"].get(t["source"], 0) + 1
    if chosen:
        sc = chosen["score"]
        s["accuracy"] = {k: (round(sc[k], 3) if isinstance(sc[k], float) else sc[k])
                         for k in ("tp", "fp", "fn", "precision", "recall", "f1", "per_plot")}
        s["estimate"] = estimate(len(trees), sc)
        s["method_table"] = chosen["table"]
    return s


# ------------------------------------------------------------------ teaching the model this site

def training_boxes(plot: dict, cand: dict) -> list[tuple[float, float, float, float]]:
    """Each hand mark becomes a crown box. The detector's own box is reused when it
    holds this mark alone; otherwise (saplings, or several trees in one box) the box
    is sized from the spacing to the nearest other mark: neighbouring crowns cannot be
    much wider than the gap between their centres."""
    boxes = cand["boxes"]
    marks = np.array(plot["marks_m"]) if plot["marks_m"] else np.zeros((0, 2))
    sizes = [((b["x1"] - b["x0"]) + (b["y1"] - b["y0"])) / 2 for b in boxes if b["score"] >= 0.2]
    typical = float(np.median(sizes)) if sizes else 4.0
    out = []
    for i, (mx, my) in enumerate(marks):
        if len(marks) > 1:
            d = np.hypot(marks[:, 0] - mx, marks[:, 1] - my)
            d[i] = np.inf
            nn = float(d.min())
        else:
            nn = np.inf
        own = None
        for b in boxes:
            if b["x0"] <= mx <= b["x1"] and b["y0"] <= my <= b["y1"]:
                inside = ((marks[:, 0] >= b["x0"]) & (marks[:, 0] <= b["x1"]) & (marks[:, 1] >= b["y0"]) & (marks[:, 1] <= b["y1"])).sum()
                if inside == 1:
                    own = b
                    break
        if own is not None:
            out.append((own["x0"], own["y0"], own["x1"], own["y1"]))
        else:
            side = float(np.clip(0.9 * nn, 0.8, typical)) if np.isfinite(nn) else typical
            r = side / 2
            out.append((mx - r, my - r, mx + r, my + r))
    return out


def fine_tune(ortho: Path, cand: dict, plots: list[dict], model_dir: Path, epochs: int = 25, res: float = SITE_RES,
              progress=lambda f, m=None: None, init_dir: Path | None = None) -> dict:
    """Retrain DeepForest on the hand-marked squares (every tree in a square must be marked).
    Starts from the pretrained weights, or from init_dir when the site begins from a model
    taught elsewhere."""
    import pandas as pd
    import torch
    from deepforest import main
    from PIL import Image

    work = model_dir / "train"
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    with rasterio.open(ortho) as ds:
        for p in plots:
            xs = [x for x, _ in p["poly_m"]]; ys = [y for _, y in p["poly_m"]]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
            rgb = ds.read([1, 2, 3], window=from_bounds(x0, y0, x1, y1, ds.transform), out_shape=(3, h, w),
                          boundless=True, fill_value=0, resampling=Resampling.average)
            name = f"plot_{p['id']}.png"
            Image.fromarray(np.moveaxis(rgb, 0, -1)).save(work / name)
            if not p["marks_m"]:     # no trees here: an empty frame (all-zero box) teaches what is not a tree
                rows.append({"image_path": name, "xmin": 0, "ymin": 0, "xmax": 0, "ymax": 0, "label": "Tree"})
            for bx0, by0, bx1, by1 in training_boxes(p, cand):
                c0 = max(0, (bx0 - x0) / res); c1 = min(w - 1, (bx1 - x0) / res)
                r0 = max(0, (y1 - by1) / res); r1 = min(h - 1, (y1 - by0) / res)
                if c1 - c0 >= 3 and r1 - r0 >= 3:
                    rows.append({"image_path": name, "xmin": int(c0), "ymin": int(r0), "xmax": int(c1), "ymax": int(r1), "label": "Tree"})
    if not any(r["xmax"] for r in rows):
        raise ValueError("no marked trees to learn from")
    pd.DataFrame(rows).to_csv(work / "labels.csv", index=False)
    progress(0.1, f"{len(rows)} marked crowns in {len(plots)} squares")
    import pytorch_lightning as pl

    class Report(pl.Callback):
        def on_train_epoch_end(self, trainer, module):
            progress(0.1 + 0.85 * (trainer.current_epoch + 1) / epochs, f"learning, round {trainer.current_epoch + 1} of {epochs}")

    def attempt(accelerator: str):
        m = main.deepforest(config_args={
            "train": {"csv_file": str(work / "labels.csv"), "root_dir": str(work), "epochs": epochs, "lr": 0.0002,
                      "augmentations": [{"HorizontalFlip": {"p": 0.5}}, {"VerticalFlip": {"p": 0.5}}, {"RandomBrightnessContrast": {"p": 0.3}}, {"HueSaturationValue": {"p": 0.3}}]},
            "log_root": str(work / "logs"), "workers": 0, "batch_size": 2, "accelerator": accelerator, "devices": "auto" if accelerator == "auto" else 1})
        m.load_model()
        if init_dir is not None and (init_dir / "model.pt").exists():
            m.model.load_state_dict(torch.load(init_dir / "model.pt", map_location="cpu"))
        m.create_trainer(callbacks=[Report()], enable_checkpointing=False, enable_progress_bar=False)
        m.model.train()        # load_model leaves it in inference mode
        m.trainer.fit(m)
        return m

    try:
        m = attempt("auto")
    except RuntimeError as e:
        # The Mac graphics chip (MPS) sometimes fails inside torchvision's box matching
        # ("IntArrayRef contains an int that cannot be represented as a SymInt"), most often
        # on squares with many marks. The processor is slower but does not have that fault.
        if not torch.backends.mps.is_available():
            raise
        progress(0.1, f"graphics chip failed ({str(e).splitlines()[0][:80]}), teaching again on the processor")
        m = attempt("cpu")
    m.model.eval()
    torch.save(m.model.state_dict(), model_dir / "model.pt")
    info = {"crowns": sum(1 for r in rows if r["xmax"]), "squares": [p["id"] for p in plots], "epochs": epochs, "res": res, "trained_at": time.time()}
    (model_dir / "model.json").write_text(json.dumps(info))
    return info


# ------------------------------------------------------------------ active learning

def uncertainty_points(cand: dict, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    """Where is the model unsure? Returns (xy, weight) per candidate.

    - a crown box whose confidence sits near the cut-off could go either way
    - image and height disagree: a confident box on something low, or a tall
      treetop with no box on it (two independent sources; query-by-committee)
    Boxes far above or below the cut-off, agreeing with height, weigh almost nothing."""
    xy, w = [], []
    tops, min_h = cand["tops"], cand["min_h"]
    covered = set()
    for b in cand["boxes"]:
        if b["veg"] < GREEN_MIN:
            continue
        s = b["score"]
        u = math.exp(-((s - threshold) / 0.08) ** 2)                 # near the decision
        if s >= threshold and b["h"] is not None and MEASURABLE_H <= b["h"] < min_h:
            u = max(u, 0.6)                                          # confident box, but low
        if s >= threshold and len([i for i in b["tops"] if tops[i]["h"] >= min_h]) >= 2:
            u = max(u, 0.5)                                          # one box, two treetops
        covered.update(b["tops"])
        if u > 0.05:
            xy.append(((b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2)); w.append(u)
    for i, t in enumerate(tops):
        if i not in covered and t["h"] >= min_h + 1 and t["veg"] >= GREEN_MIN:
            xy.append((t["x"], t["y"])); w.append(0.7)                # tall, green, no box: missed?
    return (np.array(xy) if xy else np.zeros((0, 2))), np.array(w)


def pick_regions(xy: np.ndarray, w: np.ndarray, allowed, k: int, size_m: float, spacing_m: float = 25.0) -> list[dict]:
    """Greedy: the patch with the most uncertainty, then the next best at least
    `spacing_m` away, and so on, so one confusing corner does not take every question.
    `allowed(x, y)` says whether a patch centred there lies on mapped, unused ground.
    Uncertainty is binned on a grid a third of the patch size and summed over each
    patch window in one pass."""
    if not len(xy):
        return []
    step = size_m / 3
    x0, y0 = xy[:, 0].min() - size_m, xy[:, 1].min() - size_m
    nx = int((xy[:, 0].max() - x0 + size_m) / step) + 1
    ny = int((xy[:, 1].max() - y0 + size_m) / step) + 1
    grid = np.zeros((ny, nx), np.float32)
    cnt = np.zeros((ny, nx), np.float32)
    ix = ((xy[:, 0] - x0) / step).astype(int)
    iy = ((xy[:, 1] - y0) / step).astype(int)
    np.add.at(grid, (iy, ix), w.astype(np.float32))
    np.add.at(cnt, (iy, ix), 1)
    win = 3                                   # 3 x 3 cells = one patch
    u = cv2.boxFilter(grid, -1, (win, win), normalize=False, borderType=cv2.BORDER_CONSTANT)
    n = cv2.boxFilter(cnt, -1, (win, win), normalize=False, borderType=cv2.BORDER_CONSTANT)
    order = np.argsort(u, axis=None)[::-1]
    out: list[dict] = []
    for flat in order:
        if len(out) >= k or u.flat[flat] <= 0:
            break
        r, c = divmod(int(flat), nx)
        cx, cy = x0 + (c + 0.5) * step, y0 + (r + 0.5) * step
        if any(math.hypot(cx - o["x"], cy - o["y"]) < spacing_m for o in out):
            continue
        if not allowed(cx, cy):
            continue
        out.append({"x": float(cx), "y": float(cy), "uncertainty": round(float(u[r, c]), 2), "candidates": int(n[r, c])})
    return out
