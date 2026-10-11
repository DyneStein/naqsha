"""Tree counting jobs and the hand-marked sample squares, per finished map (run).

Files live in <run>/trees/: chm.tif, ground.tif, candidates.json, plots.json,
summary.json and the exports. A model taught on this site is shared by every map
of the project, in <project>/tree_model/.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import rasterio
from pyproj import Transformer

from . import canopy, db, trees
from .config import project_dir
from .jobs import JobContext, handler

DEFAULT_THRESHOLD = 0.2
MIN_MARKS = 5            # a square needs at least this many marked trees to count


def run_files(rid: int) -> tuple[dict, Path, Path]:
    run = db.one("SELECT * FROM runs WHERE id=?", (rid,))
    if not run:
        raise KeyError(f"no map #{rid}")
    rdir = project_dir(run["project_id"]) / "runs" / f"r{rid}"
    return run, rdir, rdir / "trees"


def model_dir(project_id: int) -> Path:
    return project_dir(project_id) / "tree_model"


def _ortho(run: dict, rdir: Path) -> Path:
    rel = (run.get("qa") or {}).get("outputs", {}).get("orthophoto")
    if not rel:
        raise ValueError("this map has no orthophoto")
    return rdir / rel


def _crs(path: Path):
    with rasterio.open(path) as ds:
        return ds.crs


# ------------------------------------------------------------------ experiment log

def log_event(tdir: Path, event: str, **data) -> None:
    """Append-only record of every labelling session, teaching run and evaluation,
    so approaches can be compared afterwards by labelling effort."""
    tdir.mkdir(parents=True, exist_ok=True)
    with open(tdir / "experiment.jsonl", "a") as f:
        f.write(json.dumps({"t": round(time.time(), 1), "event": event, **data}) + "\n")


# ------------------------------------------------------------------ model versions

def base_model(project_id: int) -> dict | None:
    """Where this site's teaching starts: None for plain pretrained DeepForest (the default
    for every new project), or a model version taught on another project."""
    f = model_dir(project_id) / "base.json"
    b = json.loads(f.read_text()) if f.exists() else None
    if not b:
        return None
    d = model_dir(b["project"]) / "versions" / b["version"]
    if not (d / "model.pt").exists():
        return None
    p = db.one("SELECT name FROM projects WHERE id=?", (b["project"],))
    meta = json.loads((d / "model.json").read_text())
    return {**meta, "version": f"base-p{b['project']}-{b['version']}", "dir": d, "project": b["project"],
            "project_name": p["name"] if p else f"project {b['project']}", "source_version": b["version"]}


def set_base_model(project_id: int, source: dict | None) -> None:
    """source: {project, version} or None (plain pretrained). Counting switches to the new start."""
    md = model_dir(project_id)
    md.mkdir(parents=True, exist_ok=True)
    (md / "base.json").write_text(json.dumps(source))
    set_current_model(project_id, "base" if source else None)


def current_model(project_id: int) -> dict | None:
    """The model counts use: a version taught here, the starting model from another
    site ('base'), or None for pretrained DeepForest."""
    md = model_dir(project_id)
    cur = md / "current.json"
    if not cur.exists():
        return None
    v = json.loads(cur.read_text()).get("version")
    if v == "base":
        return base_model(project_id)
    if not v or not (md / "versions" / v / "model.pt").exists():
        return None
    return {"version": v, "dir": md / "versions" / v, **json.loads((md / "versions" / v / "model.json").read_text())}


def set_current_model(project_id: int, version: str | None) -> None:
    md = model_dir(project_id)
    md.mkdir(parents=True, exist_ok=True)
    (md / "current.json").write_text(json.dumps({"version": version}))


def all_site_models() -> list[dict]:
    """Every taught model on this computer, for 'start from another site'."""
    out = []
    for p in db.all_("SELECT id, name FROM projects ORDER BY id"):
        for m in list_models(p["id"]):
            out.append({"project": p["id"], "project_name": p["name"], **m})
    return out


def list_models(project_id: int) -> list[dict]:
    vd = model_dir(project_id) / "versions"
    out = []
    for d in sorted(vd.glob("v*"), key=lambda p: int(p.name[1:])) if vd.exists() else []:
        if (d / "model.json").exists():
            out.append({"version": d.name, **json.loads((d / "model.json").read_text())})
    return out


# ------------------------------------------------------------------ squares

def load_plots(tdir: Path) -> list[dict]:
    p = tdir / "plots.json"
    plots = json.loads(p.read_text()) if p.exists() else []
    for q in plots:                       # squares from before roles existed were checks
        q.setdefault("role", "test")
        q.setdefault("origin", "manual")
    return plots


def save_plots(tdir: Path, plots: list[dict]) -> None:
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "plots.json").write_text(json.dumps(plots, indent=1))


def _usable(p: dict) -> bool:
    # a finished square with no trees is a real answer: for a test it is a count of
    # zero, for teaching it shows the model ground that holds no tree
    return bool(p.get("done"))


PUBLIC = ("id", "role", "origin", "stratum", "round", "uncertainty", "size_m", "center", "poly", "marks", "done", "used_for_training")


def upsert_plot(rid: int, plot: dict) -> dict:
    """plot: {id?, role, center: [lon, lat], size_m, marks: [[lon, lat], ...], done, session?}.
    Squares are axis-aligned in the map's metric grid, so 'every tree inside' is unambiguous."""
    run, rdir, tdir = run_files(rid)
    crs = _crs(_ortho(run, rdir))
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    back = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    plots = load_plots(tdir)
    old = next((p for p in plots if p["id"] == plot.get("id")), None) if plot.get("id") else None
    center = plot.get("center") or (old or {}).get("center")
    cx, cy = fwd.transform(*center)
    h = float(plot.get("size_m") or (old or {}).get("size_m", 30)) / 2
    poly_m = [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]
    marks = plot.get("marks", (old or {}).get("marks", []))
    rec = {**(old or {}), "id": (old or {}).get("id") or (max([p["id"] for p in plots], default=0) + 1),
           "role": plot.get("role") or (old or {}).get("role", "teach"),
           "origin": (old or {}).get("origin", plot.get("origin", "manual")),
           "size_m": 2 * h, "center": center, "poly_m": poly_m, "poly": [list(back.transform(x, y)) for x, y in poly_m],
           "marks": marks, "marks_m": [list(fwd.transform(lon, lat)) for lon, lat in marks],
           "done": bool(plot.get("done", False)), "updated": time.time()}
    rec.setdefault("used_for_training", False)
    rec.setdefault("created", time.time())
    sess = plot.get("session")
    if sess:
        rec.setdefault("sessions", []).append(sess)
        log_event(tdir, "square_marked", square=rec["id"], role=rec["role"], origin=rec["origin"],
                  stratum=rec.get("stratum"), trees=len(marks), done=rec["done"], **sess)
    if old:
        plots[plots.index(old)] = rec
    else:
        plots.append(rec)
    save_plots(tdir, plots)
    # an unfinished square changes no result; only recount when a finished one is involved
    if (tdir / "candidates.json").exists() and (rec["done"] or (old or {}).get("done")):
        finalize(rid)
    return rec


def delete_plot(rid: int, plot_id: int) -> None:
    _, _, tdir = run_files(rid)
    plots = load_plots(tdir)
    gone = next((p for p in plots if p["id"] == plot_id), None)
    save_plots(tdir, [p for p in plots if p["id"] != plot_id])
    log_event(tdir, "square_deleted", square=plot_id)
    if (tdir / "candidates.json").exists() and gone and gone.get("done"):
        finalize(rid)


def _free_ground(run: dict, rdir: Path, tdir: Path, size_m: float, cell: float = 1.0):
    """Grid of where a square of size_m can sit: wholly on mapped ground and clear of
    every existing square. Returns (ok mask, bounds, cell, crs)."""
    import cv2
    import numpy as np
    from rasterio.enums import Resampling
    with rasterio.open(_ortho(run, rdir)) as ds:
        b = ds.bounds
        w, h = int((b.right - b.left) / cell), int((b.top - b.bottom) / cell)
        if ds.count < 4:
            raise ValueError("the map image has no transparency band to tell mapped from empty ground")
        alpha = ds.read(ds.count, out_shape=(h, w), resampling=Resampling.nearest) > 0
        crs = ds.crs
    k = int(size_m / cell)
    ok = cv2.erode(alpha.astype(np.uint8), np.ones((k + 2, k + 2), np.uint8)) > 0
    for p in load_plots(tdir):
        xs = [x for x, _ in p["poly_m"]]; ys = [y for _, y in p["poly_m"]]
        half = k // 2 + 1
        c0, c1 = int((min(xs) - b.left) / cell) - half, int((max(xs) - b.left) / cell) + half
        r0, r1 = int((b.top - max(ys)) / cell) - half, int((b.top - min(ys)) / cell) + half
        ok[max(0, r0):max(0, r1), max(0, c0):max(0, c1)] = False
    return ok, b, cell, crs


def ask_regions(rid: int, k: int, size_m: float = 12.0) -> list[dict]:
    """Active learning: place k small teaching patches where the current model is least
    sure (crown confidence near the cut-off, image and height disagreeing), spread out."""
    run, rdir, tdir = run_files(rid)
    cand = json.loads((tdir / "candidates.json").read_text())
    meta = json.loads((tdir / "detect.json").read_text()) if (tdir / "detect.json").exists() else {}
    summ = json.loads((tdir / "summary.json").read_text()) if (tdir / "summary.json").exists() else {}
    thr = summ.get("threshold", DEFAULT_THRESHOLD) if summ.get("method") != "height" else DEFAULT_THRESHOLD
    ok, b, cell, crs = _free_ground(run, rdir, tdir, size_m)

    def allowed(x, y):
        c, r = int((x - b.left) / cell), int((b.top - y) / cell)
        return 0 <= r < ok.shape[0] and 0 <= c < ok.shape[1] and bool(ok[r, c])
    xy, w = trees.uncertainty_points(cand, thr)
    regions = trees.pick_regions(xy, w, allowed, k, size_m)
    if not regions:
        raise ValueError("no uncertain area left that is free of existing squares")
    rnd = 1 + max([p.get("round", 0) for p in load_plots(tdir) if p.get("origin") == "active"], default=0)
    back = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    placed = []
    for g in regions:
        rec = upsert_plot(rid, {"role": "teach", "center": list(back.transform(g["x"], g["y"])), "size_m": size_m,
                                "marks": [], "done": False, "origin": "active"})
        placed.append((rec["id"], g))
    allp = load_plots(tdir)
    by_id = dict(placed)
    for q in allp:
        if q["id"] in by_id:
            q.update({"round": rnd, "uncertainty": by_id[q["id"]]["uncertainty"], "asked_by": meta.get("model_version", "pretrained")})
    save_plots(tdir, allp)
    log_event(tdir, "al_queries", round=rnd, model_version=meta.get("model_version", "pretrained"), threshold=thr,
              size_m=size_m, squares=[i for i, _ in placed], uncertainty=[g["uncertainty"] for _, g in placed])
    return [r for r, _ in placed]


def place_random(rid: int, n: int, size_m: float, role: str, seed: int | None = None) -> list[dict]:
    """Drop n squares at random inside the mapped area, spread evenly over sparse,
    medium and dense tree cover (strata from the detector's own crown density), never
    overlapping an existing square. Random placement keeps the accuracy check honest:
    people choose easy, clear spots."""
    import cv2
    import numpy as np
    run, rdir, tdir = run_files(rid)
    cand = json.loads((tdir / "candidates.json").read_text())
    seed = int(seed if seed is not None else time.time()) % (2 ** 31)
    rng = np.random.default_rng(seed)
    ok, b, cell, crs = _free_ground(run, rdir, tdir, size_m)
    h, w = ok.shape
    k = int(size_m / cell)
    dens = np.zeros((h, w), np.float32)
    for bx in cand["boxes"]:
        if bx["score"] >= DEFAULT_THRESHOLD and bx["veg"] >= trees.GREEN_MIN:
            c, r = int(((bx["x0"] + bx["x1"]) / 2 - b.left) / cell), int((b.top - (bx["y0"] + bx["y1"]) / 2) / cell)
            if 0 <= r < h and 0 <= c < w:
                dens[r, c] += 1
    dens = cv2.boxFilter(dens, -1, (k, k), normalize=False)
    rr, cc = np.nonzero(ok)
    if not len(rr):
        raise ValueError("no room left for another square of this size")
    vals = dens[rr, cc]
    edges = np.quantile(vals, [1 / 3, 2 / 3])
    strata = np.digitize(vals, edges)            # 0 sparse, 1 medium, 2 dense
    names = ["sparse", "medium", "dense"]
    back = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    placed: list[dict] = []
    taken = np.zeros((h, w), bool)
    for i in range(n):
        s_i = i % 3
        pool = np.nonzero((strata == s_i) & ~taken[rr, cc])[0]
        if not len(pool):
            pool = np.nonzero(~taken[rr, cc])[0]
            if not len(pool):
                break
        j = rng.choice(pool)
        r, c = rr[j], cc[j]
        taken[max(0, r - k):r + k, max(0, c - k):c + k] = True
        x, y = b.left + (c + 0.5) * cell, b.top - (r + 0.5) * cell
        rec = upsert_plot(rid, {"role": role, "center": list(back.transform(x, y)), "size_m": size_m,
                                "marks": [], "done": False, "origin": "random"})
        allp = load_plots(tdir)
        for q in allp:
            if q["id"] == rec["id"]:
                q["stratum"] = names[int(strata[j])]
                q["seed"] = seed
        save_plots(tdir, allp)
        placed.append(rec)
    log_event(tdir, "squares_placed", role=role, n=len(placed), size_m=size_m, seed=seed,
              squares=[p["id"] for p in placed])
    return placed


# ------------------------------------------------------------------ results

def finalize(rid: int) -> dict:
    """Pick the method, score it on the test squares, write the trees and the summary.
    Cheap: no model runs here, so it is redone whenever a square changes.

    Test squares only ever grade. The method and cut-off are chosen on teaching
    squares when there are any (choosing on the squares that grade it would flatter
    the result); with none yet, they are chosen on the test squares and flagged."""
    run, rdir, tdir = run_files(rid)
    cand = json.loads((tdir / "candidates.json").read_text())
    meta = json.loads((tdir / "detect.json").read_text()) if (tdir / "detect.json").exists() else {}
    plots = [p for p in load_plots(tdir) if _usable(p)]
    test = [p for p in plots if p["role"] == "test"]
    teach = [p for p in plots if p["role"] == "teach"]
    # a taught model is tuned on the squares it was taught from (they follow the same idea of a tree)
    taught_ids = set(meta.get("trained_on") or [])
    pick_on = [p for p in teach if p["id"] in taught_ids] or teach or test
    chosen = trees.choose(cand, pick_on) if pick_on else None
    if chosen and chosen["score"]["tp"] == 0:
        chosen = None       # no square lines up with any found tree: nothing to choose by
    selected_on = ("teaching squares" if teach else "test squares") if chosen else None
    if chosen:
        method, thr = chosen["method"], chosen["threshold"]
    else:
        method, thr = "image", DEFAULT_THRESHOLD
    result = trees.apply_method(cand, method, thr)
    graded = trees.score_plots(result, test) if test else None
    no_match = bool(graded) and graded["tp"] == 0 and graded["fn"] > 0
    area = ((run.get("qa") or {}).get("ortho") or {}).get("valid_area_m2")
    extra = {"model": meta.get("model", "DeepForest (pretrained)"), "model_version": meta.get("model_version"),
             "ground": meta.get("ground"),
             "candidates": {"image_boxes": len(cand["boxes"]), "treetops": len(cand["tops"])},
             "squares_test": len(test), "squares_teach": len(teach), "selected_on": selected_on,
             "detect_seconds": meta.get("seconds"), "no_match": no_match}
    graded_ok = graded if graded and not no_match else None
    extra["squares_estimate"] = trees.design_estimate([p for p in plots if p.get("origin") == "random"], area)
    summary = trees.summarise(result, area, {"score": graded_ok, "table": chosen["table"] if chosen else []} if graded_ok else None,
                              method, thr, extra)
    trees.write_outputs(tdir, _crs(_ortho(run, rdir)), result, summary)
    _log_eval(tdir, cand, test, meta, method, thr, selected_on, summary)
    return summary


def _log_eval(tdir: Path, cand: dict, test: list[dict], meta: dict, method: str, thr: float, selected_on, summary) -> None:
    """One evaluation record per (model, test set) state, with every method and cut-off
    scored on the test squares, so later analysis is not tied to today's choices."""
    if not test:
        return
    sig = json.dumps([meta.get("model_version"), meta.get("t"), sorted((p["id"], len(p["marks_m"])) for p in test)])
    sf = tdir / "eval_sig.txt"
    if sf.exists() and sf.read_text() == sig:
        return
    sf.write_text(sig)
    table = []
    for m in trees.METHODS:
        for t in (trees.THRESHOLDS if m != "height" else [0.0]):
            sc = trees.score_plots(trees.apply_method(cand, m, t), test)
            table.append({"method": m, "threshold": t, "tp": sc["tp"], "fp": sc["fp"], "fn": sc["fn"],
                          "f1": round(sc["f1"], 4), "n_total": len(trees.apply_method(cand, m, t))})
    log_event(tdir, "evaluation", model_version=meta.get("model_version"), test_squares=[p["id"] for p in test],
              test_trees=sum(len(p["marks_m"]) for p in test), chosen={"method": method, "threshold": thr, "on": selected_on},
              estimate=summary.get("estimate"), count=summary["count"], table=table)


def compare(rid: int) -> dict:
    """Every model version's candidates scored on the current test squares, with the
    labelling effort that produced it. Computed fresh, so rule changes apply to all."""
    run, rdir, tdir = run_files(rid)
    test = [p for p in load_plots(tdir) if _usable(p) and p["role"] == "test"]
    models = {m["version"]: m for m in list_models(run["project_id"])}
    rows = []
    for f in sorted(tdir.glob("candidates_*.json"), key=lambda f: (f.stem != "candidates_pretrained", f.stat().st_mtime)):
        v = f.stem.split("_", 1)[1]
        cand = json.loads(f.read_text())
        best = None
        per_method = {}
        for m in trees.METHODS:
            for t in (trees.THRESHOLDS if m != "height" else [0.0]):
                tr = trees.apply_method(cand, m, t)
                sc = trees.score_plots(tr, test)
                r = {"method": m, "threshold": t, "f1": round(sc["f1"], 3), "precision": round(sc["precision"], 3),
                     "recall": round(sc["recall"], 3), "count": len(tr)}
                if m not in per_method or r["f1"] > per_method[m]["f1"]:
                    per_method[m] = r
                if best is None or r["f1"] > best["f1"]:
                    best = r
        meta = models.get(v, {})
        rows.append({"version": v, "approach": meta.get("approach", "pretrained" if v == "pretrained" else "?"),
                     "trees_labelled": meta.get("trees_labelled", 0), "label_seconds": meta.get("label_seconds", 0),
                     "squares": len(meta.get("squares", [])), "best": best, "per_method": per_method})
    return {"test_squares": len(test), "test_trees": sum(len(p["marks_m"]) for p in test), "rows": rows}


def load_settings(tdir: Path) -> dict:
    f = tdir / "settings.json"
    return {"min_height_m": 0.0, **(json.loads(f.read_text()) if f.exists() else {})}


def save_settings(rid: int, settings: dict) -> dict:
    _, _, tdir = run_files(rid)
    cur = load_settings(tdir)
    cur.update({k: v for k, v in settings.items() if k in ("min_height_m",)})
    (tdir / "settings.json").write_text(json.dumps(cur))
    log_event(tdir, "settings", **cur)
    return cur


def filtered_files(rid: int, min_h: float) -> dict:
    """trees / crowns / density for trees at least min_h tall, rebuilt from the saved
    results (density is recounted on the filtered trees)."""
    run, rdir, tdir = run_files(rid)
    trees_fc = trees.filter_features(json.loads((tdir / "trees.geojson").read_text()), min_h)
    crowns_fc = trees.filter_features(json.loads((tdir / "crowns.geojson").read_text()), min_h)
    crs = _crs(_ortho(run, rdir))
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    back = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    pts = [{"x": x, "y": y} for x, y in (fwd.transform(*f["geometry"]["coordinates"]) for f in trees_fc["features"])]
    dens = []
    for c in trees.density_grid(pts):
        ring = [back.transform(c["x0"] + dx, c["y0"] + dy) for dx, dy in [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]]
        dens.append({"type": "Feature", "properties": {"trees": c["n"], "per_ha": c["per_ha"]},
                     "geometry": {"type": "Polygon", "coordinates": [[[round(x, 7), round(y, 7)] for x, y in ring]]}})
    return {"trees": trees_fc, "crowns": crowns_fc, "density": {"type": "FeatureCollection", "features": dens}}


def status(rid: int) -> dict:
    run, rdir, tdir = run_files(rid)
    s = tdir / "summary.json"
    plots = load_plots(tdir)
    job = db.one("SELECT id, kind, status, progress, stage FROM jobs WHERE ref_id=? AND kind IN ('trees','trees_train') "
                 "ORDER BY id DESC LIMIT 1", (rid,))
    cur = current_model(run["project_id"])
    return {"summary": json.loads(s.read_text()) if s.exists() else None,
            "plots": [{k: p.get(k) for k in PUBLIC} for p in plots],
            "job": job, "site_model": {k: v for k, v in cur.items() if k != "dir"} if cur else None,
            "settings": load_settings(tdir),
            "base_model": {k: v for k, v in (base_model(run["project_id"]) or {}).items() if k != "dir"} or None,
            "other_models": [m for m in all_site_models() if m["project"] != run["project_id"]],
            "models": list_models(run["project_id"])}


# ------------------------------------------------------------------ jobs

def _count(rid: int, params: dict, report) -> None:
    """report(fraction, stage=None, message=None)"""
    run, rdir, tdir = run_files(rid)
    tdir.mkdir(parents=True, exist_ok=True)
    ortho = _ortho(run, rdir)
    outs = (run.get("qa") or {}).get("outputs", {})
    if not outs.get("dsm"):
        raise ValueError("this map has no elevation model; build it with Standard or Maximum quality")
    t0 = time.time()
    if not (tdir / "chm.tif").exists() or params.get("min_height") is not None:
        report(0.02, "Measuring tree heights", "separating bare ground from trees and buildings")
        dtm = rdir / outs["dtm"] if outs.get("dtm") else None
        ground_src = canopy.canopy_height(rdir / outs["dsm"], tdir, dtm=dtm)["ground_source"]
    else:
        ground_src = json.loads((tdir / "detect.json").read_text()).get("ground") if (tdir / "detect.json").exists() else None
    report(0.1, "Finding trees in the map image", "loading the tree model")
    cur = None if params.get("pretrained") else current_model(run["project_id"])
    model = trees.load_model(cur["dir"] if cur else None)
    res = trees.model_res(cur["dir"] if cur else None)
    boxes = trees.detect_image(ortho, model, res=res,
                               progress=lambda f, m=None: report(0.12 + 0.78 * f, f"Finding trees in the map image ({res * 100:.0f} cm)", m))
    report(0.92, "Checking crowns against tree height")
    cand = trees.build_candidates(ortho, tdir / "chm.tif", boxes, min_h=float(params.get("min_height") or 1.0))
    version = cur["version"] if cur else "pretrained"
    (tdir / "candidates.json").write_text(json.dumps(cand))
    (tdir / f"candidates_{version}.json").write_text(json.dumps(cand))      # kept for later comparison
    (tdir / "detect.json").write_text(json.dumps({
        "model": f"DeepForest, taught on this site ({version})" if cur else "DeepForest (pretrained)",
        "model_version": version, "trained_on": cur["squares"] if cur else [], "ground": ground_src,
        "seconds": round(time.time() - t0), "t": round(time.time())}))
    log_event(tdir, "count", model_version=version, boxes=len(boxes), seconds=round(time.time() - t0))
    report(0.97, "Counting")
    finalize(rid)


@handler("trees")
def count_job(ctx: JobContext) -> None:
    _count(ctx.job["ref_id"], ctx.job["params"] or {}, lambda f, s=None, m=None: ctx.progress(f, s, m))
    ctx.progress(1.0, "Done", force=True)


@handler("trees_train")
def train_job(ctx: JobContext) -> None:
    """Teach a new model version from the pretrained weights on every finished teaching
    square, make it current, and count again. Test squares are never used here."""
    rid = ctx.job["ref_id"]
    params = ctx.job["params"] or {}
    run, rdir, tdir = run_files(rid)
    # Teaching is cumulative: every finished teaching square and active-learning patch on
    # this site, so both approaches feed one model. ("random"/"active" alone remain for
    # experiments that compare the approaches.)
    use = params.get("use", "all")
    plots = [p for p in load_plots(tdir) if _usable(p) and p["role"] == "teach"
             and (use == "all" or (use == "active") == (p.get("origin") == "active"))]
    if not plots:
        raise ValueError("finish at least one teaching square first (test squares are never used for teaching)")
    cand = json.loads((tdir / "candidates.json").read_text())
    vd = model_dir(run["project_id"]) / "versions"
    n = 1 + max([int(d.name[1:]) for d in vd.glob("v*")], default=0) if vd.exists() else 1
    vdir = vd / f"v{n}"
    vdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    base = base_model(run["project_id"])
    info = trees.fine_tune(_ortho(run, rdir), cand, plots, vdir, init_dir=base["dir"] if base else None,
                           res=float(base.get("res", trees.SITE_RES)) if base else trees.SITE_RES,
                           progress=lambda f, m=None: ctx.progress(0.6 * f, "Teaching the model your trees", m))
    kinds = sorted({"active learning" if p.get("origin") == "active" else "teaching squares" for p in plots})
    label = " + ".join(kinds) if use == "all" else {"random": "random squares", "active": "active learning"}[use]
    if base:
        label += f", started from {base['project_name']} {base['source_version']}"
    secs = sum(sum(x.get("seconds", 0) for x in p.get("sessions", [])) for p in plots)
    info.update({"approach": label, "map": rid, "base": base["version"] if base else None, "seconds": round(time.time() - t0), "label_seconds": secs,
                 "trees_labelled": sum(len(p["marks_m"]) for p in plots)})
    (vdir / "model.json").write_text(json.dumps(info))
    set_current_model(run["project_id"], vdir.name)
    allp = load_plots(tdir)
    for p in allp:
        if p["id"] in {q["id"] for q in plots}:
            p["used_for_training"] = True
    save_plots(tdir, allp)
    log_event(tdir, "teach", model_version=vdir.name, squares=[p["id"] for p in plots], crowns=info["crowns"],
              epochs=info["epochs"], seconds=info["seconds"], approach=info["approach"])
    _count(rid, {}, lambda f, s=None, m=None: ctx.progress(0.6 + 0.4 * f, s, m))   # count again with the taught model
    ctx.progress(1.0, "Done", force=True)
