"""Post-run quality assessment.

Answers: did every image register, did any register in the wrong place
(typical of repetitive texture), is the model in one piece, and which flown
areas ended up as holes in the orthophoto.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np


def _rotation_matrix(rv):
    rv = np.asarray(rv, float)
    th = np.linalg.norm(rv)
    if th < 1e-12:
        return np.eye(3)
    k = rv / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K


def shot_residuals(recon_path: Path) -> tuple[list[dict], list[int]]:
    """Per-image distance between the SfM camera centre and its GPS prior.

    Both live in OpenSfM's topocentric frame. Returns (records, component sizes).
    """
    recs = json.loads(recon_path.read_text())
    out, sizes = [], []
    for ci, rec in enumerate(recs):
        shots = rec.get("shots", {})
        sizes.append(len(shots))
        for name, s in shots.items():
            R = _rotation_matrix(s["rotation"])
            c = -R.T @ np.asarray(s["translation"], float)
            g = s.get("gps_position") or (s.get("metadata") or {}).get("gps_position")
            if g is None:
                continue
            g = np.asarray(g, float)
            out.append({"image": name, "component": ci,
                        "dxy": round(float(np.hypot(*(c[:2] - g[:2]))), 2),
                        "dz": round(float(c[2] - g[2]), 2)})
    return out, sizes


def ortho_valid_polygon(ortho: Path, max_dim: int = 2000):
    """Valid-data footprint of the orthophoto, in its own CRS."""
    import rasterio
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely import unary_union

    with rasterio.open(ortho) as ds:
        scale = max(ds.width, ds.height) / max_dim
        h, w = int(ds.height / max(scale, 1)), int(ds.width / max(scale, 1))
        if ds.count >= 4:
            mask = ds.read(ds.count, out_shape=(h, w)) > 0
        else:
            mask = ds.read_masks(1, out_shape=(h, w)) > 0
        tr = ds.transform * ds.transform.scale(ds.width / w, ds.height / h)
        polys = [shape(g) for g, v in shapes(mask.astype(np.uint8), mask=mask, transform=tr) if v == 1]
        return unary_union(polys) if polys else None, ds.crs, ds.res


def assess(run_dir: Path, manifest: dict, planned_wgs84=None) -> dict:
    qa: dict = {"outputs": {}, "warnings": [], "flagged_images": [], "grade": "unknown"}
    outs = {
        "orthophoto": run_dir / "odm_orthophoto" / "odm_orthophoto.tif",
        "dsm": run_dir / "odm_dem" / "dsm.tif",
        "dtm": run_dir / "odm_dem" / "dtm.tif",
        "report": run_dir / "odm_report" / "report.pdf",
        "pointcloud": run_dir / "odm_georeferencing" / "odm_georeferenced_model.laz",
        "model": run_dir / "odm_texturing" / "odm_textured_model_geo.obj",
    }
    for k, p in outs.items():
        if p.exists():
            qa["outputs"][k] = str(p.relative_to(run_dir))
    if "report" not in qa["outputs"]:
        rep = sorted(run_dir.glob("submodels/submodel_*/odm_report/report.pdf"))
        if rep:
            qa["outputs"]["report"] = str(rep[0].relative_to(run_dir))

    n_in = len(manifest["images"])
    stats_files = [run_dir / "odm_report" / "stats.json"]
    if not stats_files[0].exists():
        stats_files = sorted(run_dir.glob("submodels/submodel_*/odm_report/stats.json"))
    parts = []
    for sp in stats_files:
        if not sp.exists():
            continue
        st = json.loads(sp.read_text())
        rs = st.get("reconstruction_statistics", {})
        ge = st.get("gps_errors") or {}
        parts.append({
            "shots": rs.get("reconstructed_shots_count") or 0, "initial": rs.get("initial_shots_count") or 0,
            "points": rs.get("reconstructed_points_count") or 0, "components": rs.get("components") or 0,
            "reproj": rs.get("reprojection_error_pixels"), "gps": ge.get("average_error"),
            "ce90": ge.get("ce90"), "le90": ge.get("le90"),
            "gsd": (st.get("odm_processing_statistics") or {}).get("average_gsd"),
        })
    if parts:
        def wavg(key):
            v = [(p[key], p["shots"]) for p in parts if p[key] is not None and p["shots"]]
            return sum(a * w for a, w in v) / sum(w for _, w in v) if v else None
        qa["sfm"] = {
            "reconstructed_shots": sum(p["shots"] for p in parts),
            "initial_shots": sum(p["initial"] for p in parts),
            "reconstructed_points": sum(p["points"] for p in parts),
            "components": sum(p["components"] for p in parts),
            "reprojection_error_px": wavg("reproj"),
            "gps_error_m": wavg("gps"), "gps_ce90_m": max((p["ce90"] for p in parts if p["ce90"]), default=None),
            "gps_le90_m": max((p["le90"] for p in parts if p["le90"]), default=None),
            "gsd_cm": wavg("gsd"),
            "parts": len(parts),
        }

    # ODM rewrites reconstruction.json into projected (UTM) coordinates but keeps
    # gps_position topocentric; the .topocentric.json copy has both in one frame.
    # Split-merge runs keep one reconstruction per submodel.
    def pick(d: Path):
        for n in ("reconstruction.topocentric.json", "reconstruction.json"):
            if (d / n).exists():
                return d / n
        return None
    root = pick(run_dir / "opensfm")
    recons = [root] if root else [p for d in sorted(run_dir.glob("submodels/submodel_*/opensfm"))
                                  if (p := pick(d))]
    if recons:
        res, sizes = [], []
        seen = set()
        for rp in recons:
            r_i, s_i = shot_residuals(rp)
            for r in r_i:
                if r["image"] not in seen:     # overlap images appear in two submodels
                    seen.add(r["image"])
                    res.append(r)
            sizes += s_i
        if len(recons) > 1:
            qa["split_submodels"] = len(recons)
        sigma = {im["name"]: im["xy_sigma"] for im in manifest["images"]}
        registered = {r["image"] for r in res}
        dropped = [im["name"] for im in manifest["images"] if im["name"] not in registered]
        flagged = []
        for r in res:
            lim = max(8.0, 4.0 * sigma.get(r["image"], 2.5))
            if r["dxy"] > lim or abs(r["dz"]) > max(10.0, lim):
                flagged.append({**r, "limit_m": round(lim, 1)})
        dxy = np.array([r["dxy"] for r in res]) if res else np.array([0.0])
        qa["registration"] = {
            "images_in": n_in, "registered": len(registered), "dropped": dropped[:500],
            "dropped_count": len(dropped), "components": sizes,
            # pieces that did not connect; split submodels are separate by design
            "pieces": max(1, len(sizes) - (len(recons) - 1)),
            "gps_residual_median_m": round(float(np.median(dxy)), 2),
            "gps_residual_p95_m": round(float(np.percentile(dxy, 95)), 2),
        }
        qa["flagged_images"] = flagged
        if len(sizes) > len(recons):
            qa["warnings"].append(
                f"model split into {len(sizes)} pieces ({', '.join(map(str, sizes))} images); pieces are "
                "placed by GPS only, so their seams can be offset by a few metres")
        if dropped:
            pct = 100 * len(dropped) / max(1, n_in)
            qa["warnings"].append(f"{len(dropped)} images ({pct:.0f}%) could not be registered (low texture, "
                                  "blur or too little overlap)")
        if flagged:
            qa["warnings"].append(
                f"{len(flagged)} images registered far from their GPS position; likely false matches on "
                "repetitive texture. They are candidates for exclusion")

    ortho = run_dir / outs["orthophoto"].relative_to(run_dir)
    if ortho.exists():
        try:
            valid, crs, res = ortho_valid_polygon(ortho)
            qa["ortho"] = {"crs": str(crs), "resolution_m": [round(r, 4) for r in res],
                           "valid_area_m2": round(valid.area, 1) if valid is not None else 0}
            if planned_wgs84 is not None and valid is not None:
                from pyproj import Transformer
                from shapely.ops import transform
                to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
                to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
                planned = transform(to_crs, planned_wgs84)
                holes = planned.difference(valid.buffer(0.5))
                parts = [g for g in getattr(holes, "geoms", [holes]) if g.area >= 25]
                hole_area = sum(g.area for g in parts)
                qa["holes"] = {
                    "planned_area_m2": round(planned.area, 1),
                    "hole_area_m2": round(hole_area, 1),
                    "hole_fraction": round(hole_area / planned.area, 3) if planned.area else 0,
                    "geojson": {"type": "FeatureCollection", "features": [
                        {"type": "Feature", "properties": {"area_m2": round(g.area, 1)},
                         "geometry": json.loads(json.dumps(transform(to_wgs, g).__geo_interface__))}
                        for g in sorted(parts, key=lambda g: -g.area)[:200]]},
                }
                if qa["holes"]["hole_fraction"] > 0.1:
                    qa["warnings"].append(
                        f"{100 * qa['holes']['hole_fraction']:.0f}% of the mappable flown area is missing from "
                        "the orthophoto (usually dense canopy, water or moving foliage)")
        except Exception as e:  # QA must never fail the run
            qa["warnings"].append(f"orthophoto check failed: {e}")

    reg = qa.get("registration", {})
    drop_frac = reg.get("dropped_count", 0) / max(1, n_in)
    flag_frac = len(qa["flagged_images"]) / max(1, n_in)
    comps = reg.get("pieces", 1)
    if "orthophoto" not in qa["outputs"]:
        qa["grade"] = "failed"
    elif drop_frac > 0.25 or flag_frac > 0.1 or comps > 3 or qa.get("holes", {}).get("hole_fraction", 0) > 0.5:
        qa["grade"] = "poor"
    elif drop_frac > 0.08 or flag_frac > 0.02 or comps > 1 or qa.get("holes", {}).get("hole_fraction", 0) > 0.15:
        qa["grade"] = "fair"
    else:
        qa["grade"] = "good"
    return qa


def heal_plan(qa: dict, opts: dict) -> dict | None:
    """Decide whether a second pass is worth it and with what changes."""
    reg = qa.get("registration", {})
    n = max(1, reg.get("images_in", 1))
    flagged = [f["image"] for f in qa.get("flagged_images", [])]
    changes: dict = {}
    reasons = []
    if flagged and len(flagged) <= 0.2 * n:
        reasons.append(f"exclude {len(flagged)} mis-registered images")
    else:
        flagged = []
    if reg.get("dropped_count", 0) > 0.15 * n or reg.get("pieces", len(reg.get("components", [1]))) > 1:
        changes["min-num-features"] = int(opts.get("min-num-features", 10000) * 1.5)
        changes["matcher-neighbors"] = int(opts.get("matcher-neighbors", 24)) + 12
        reasons.append("more features and wider matching to reconnect the model")
    if not reasons:
        return None
    return {"exclude": flagged, "overrides": changes, "reason": "; ".join(reasons)}
