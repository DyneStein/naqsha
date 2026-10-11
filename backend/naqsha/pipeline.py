"""Job handlers: analyse a project, select frames, reconstruct with ODM."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import fields as dc_fields
from pathlib import Path

import numpy as np
from shapely import from_geojson, to_geojson, unary_union

from . import chunking, db, memory, odm, odm_compat, qa as qa_mod
from .analysis import CleanTrack, clean_clip, clip_stats, harmonise_altitude, minimum_enclosing_circle
from .camera import CameraStore
from .config import DATA_DIR, DEM_AUTO, project_dir
from .coverage import area_m2, coverage, kml, to_wgs84, track_geojson
from .geo import LocalProjection
from .geotag import frame_time, geotag
from .jobs import JobContext, handler
from .selection import (SelectionParams, check_links, densify_requests, quality_scan, select_clip,
                        thin_to_cap)
from .telemetry import dji_djmd, dji_srt
from .telemetry.model import Telemetry
from .terrain import Terrain, fetch_copernicus
from .video import VIDEO_EXTS, export_frames, probe

CAMERAS = CameraStore(DATA_DIR / "cameras.json")


# ----------------------------------------------------------------- persistence

def save_track(tr: CleanTrack, path: Path) -> None:
    arrays, meta = {}, {}
    for f in dc_fields(CleanTrack):
        v = getattr(tr, f.name)
        if isinstance(v, np.ndarray):
            arrays[f.name] = v
        else:
            meta[f.name] = v
    np.savez_compressed(path, __meta=np.array(json.dumps(meta)), **arrays)


def load_track(path: Path) -> CleanTrack:
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["__meta"]))
    kw = {k: z[k] for k in z.files if k != "__meta"}
    kw.update(meta)
    return CleanTrack(**kw)


def load_project_tracks(pid: int) -> tuple[dict[int, CleanTrack], dict]:
    pdir = project_dir(pid)
    tracks = {}
    for v in db.all_("SELECT * FROM videos WHERE project_id=? AND status='ok'", (pid,)):
        p = pdir / "tracks" / f"v{v['id']}.npz"
        if p.exists():
            tracks[v["id"]] = load_track(p)
    analysis = json.loads((pdir / "analysis.json").read_text())
    return tracks, analysis


# ----------------------------------------------------------------- analyse

def scan_videos(video_dir: Path) -> list[Path]:
    vids = [p for p in sorted(video_dir.rglob("*")) if p.suffix.lower() in VIDEO_EXTS
            and not p.name.startswith(".") and p.suffix.lower() != ".lrf"]
    return vids


def _harmonise_layouts(tels: dict, layouts: dict, videos: list[dict], pdir: Path) -> None:
    """One camera model writes one layout. Clips that had to infer theirs (too
    little motion to be sure) are re-read with the best layout their model has."""
    by_model: dict[str, list[int]] = {}
    for vid, (info, tel) in tels.items():
        by_model.setdefault(tel.camera_model, []).append(vid)
    names = {v["id"]: v for v in videos}
    for model, vids in by_model.items():
        known = [layouts[v] for v in vids if v in layouts and layouts[v][0] != "inferred"]
        if known:
            best = known[0][1]
        else:
            inferred = [(v, layouts[v][1]) for v in vids if v in layouts]
            if not inferred:
                continue
            # the layout with the most roles, from the clip with the most motion
            best = max(inferred, key=lambda kv: (len(kv[1]), len(tels[kv[0]][1])))[1]
        for v in vids:
            if v in layouts and layouts[v][1] != best:
                info, _ = tels[v]
                tel, lname, lay = dji_djmd.read(Path(names[v]["path"]), info.telemetry_stream, info.fps,
                                                info.frames, layout=best)
                tel.notes.append("telemetry layout taken from another clip of the same camera")
                tel.save(pdir / "telemetry" / f"v{v}.npz")
                tels[v] = (info, tel)
                layouts[v] = (lname, lay)


@handler("analyze")
def analyze(ctx: JobContext) -> None:
    pid = ctx.job["project_id"]
    proj = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    pdir = project_dir(pid)
    (pdir / "tracks").mkdir(parents=True, exist_ok=True)
    (pdir / "telemetry").mkdir(exist_ok=True)
    db.update("projects", pid, status="analyzing", error=None)
    try:
        _analyze(ctx, pid, Path(proj["video_dir"]), pdir)
    except Exception as e:
        db.update("projects", pid, status="failed", error=str(e))
        raise


def _analyze(ctx: JobContext, pid: int, video_dir: Path, pdir: Path) -> None:
    if not video_dir.is_dir():
        raise FileNotFoundError(f"video folder not found: {video_dir}")
    # register any new videos
    known = {v["path"] for v in db.all_("SELECT path FROM videos WHERE project_id=?", (pid,))}
    for p in scan_videos(video_dir):
        if str(p) not in known:
            db.insert("videos", project_id=pid, path=str(p), name=p.name, status="new")
    videos = db.all_("SELECT * FROM videos WHERE project_id=? ORDER BY name", (pid,))
    if not videos:
        raise ValueError("no video files found in the folder")

    tels: dict[int, tuple] = {}
    layouts: dict[int, tuple[str, dict]] = {}
    for k, v in enumerate(videos):
        ctx.progress(0.6 * k / len(videos), "reading telemetry", v["name"])
        path = Path(v["path"])
        try:
            info = probe(path)
            cache = pdir / "telemetry" / f"v{v['id']}.npz"
            if cache.exists():
                tel = Telemetry.load(cache)
            elif info.telemetry_stream is not None:
                tel, lname, lay = dji_djmd.read(path, info.telemetry_stream, info.fps, info.frames)
                layouts[v["id"]] = (lname, lay)
            elif (srt := dji_srt.find_sidecar(path)) is not None:
                tel = dji_srt.read(srt, info.fps, info.frames, (info.encoder or "DJI unknown"))
            else:
                db.update("videos", v["id"], info=info.to_dict(), status="no-telemetry",
                          error="no embedded telemetry track and no .SRT sidecar")
                continue
            tel.save(cache)
            db.update("videos", v["id"], info=info.to_dict(), telemetry_source=tel.source, status="ok",
                      error=None)
            tels[v["id"]] = (info, tel)
        except Exception as e:
            db.update("videos", v["id"], status="error", error=str(e)[:2000])
    if not tels:
        raise ValueError("none of the videos carry usable telemetry")
    _harmonise_layouts(tels, layouts, videos, pdir)

    all_lat = np.concatenate([t.data["lat"] for _, t in tels.values()])
    all_lon = np.concatenate([t.data["lon"] for _, t in tels.values()])
    if np.isnan(all_lat).all():
        raise ValueError("telemetry present but contains no GPS fix")
    lat0, lon0 = float(np.nanmedian(all_lat)), float(np.nanmedian(all_lon))
    proj = LocalProjection(lat0, lon0)

    ctx.progress(0.62, "cleaning GPS")
    tracks = [clean_clip(vid, tel, proj) for vid, (info, tel) in tels.items()]

    ctx.progress(0.7, "terrain model")
    dem_path = pdir / "dem.tif"
    bbox = (float(np.nanmin(all_lat)), float(np.nanmin(all_lon)), float(np.nanmax(all_lat)), float(np.nanmax(all_lon)))
    dem_note = None
    if not dem_path.exists() and DEM_AUTO:
        if fetch_copernicus(bbox, dem_path) is None:
            dem_note = "terrain model unavailable (offline?); heights assume flat ground at takeoff level"
    terrain = Terrain(dem_path if dem_path.exists() else None)

    flights, warnings = harmonise_altitude(tracks, {vid: t.data["alt"] for vid, (_, t) in tels.items()}, terrain)
    if dem_note:
        warnings.append(dem_note)
    for tr in tracks:
        save_track(tr, pdir / "tracks" / f"v{tr.clip_id}.npz")

    ctx.progress(0.8, "coverage")
    intr = {}
    for vid, (info, tel) in tels.items():
        intr[vid], src = CAMERAS.intrinsics(tel.camera_model, info.width, info.height)
    cov = coverage(tracks, intr)
    cov_wgs = {k: to_wgs84(g, proj) for k, g in cov.items() if g is not None}
    names = {v["id"]: v["name"] for v in videos}
    tfc = track_geojson(tracks, names)

    homes = [f["home"] for f in flights if f["home"]]
    for tr in tracks:
        home = flights[tr.flight_key]["home"] if tr.flight_key >= 0 else None
        db.update("videos", tr.clip_id, stats=clip_stats(tr, home))

    xs = np.concatenate([tr.x[~np.isnan(tr.x)][::15] for tr in tracks])
    ys = np.concatenate([tr.y[~np.isnan(tr.y)][::15] for tr in tracks])
    cx, cy, r = minimum_enclosing_circle(xs, ys)
    clat, clon = proj.to_ll(cx, cy)
    max_from_home = 0.0
    for tr in tracks:
        home = flights[tr.flight_key]["home"] if tr.flight_key >= 0 else None
        ok = ~np.isnan(tr.x)
        if home and ok.any():
            max_from_home = max(max_from_home, float(np.max(np.hypot(tr.x[ok] - home["x"], tr.y[ok] - home["y"]))))
    hull = cov["all"].convex_hull if cov.get("all") is not None else None
    lats = np.concatenate([tr.lat for tr in tracks]); lons = np.concatenate([tr.lon for tr in tracks])
    summary = {
        "clips": len(tracks),
        "flights": len(flights),
        "duration_s": round(sum(float(tr.t[-1]) for tr in tracks), 1),
        "path_m": round(sum(float(np.hypot(np.diff(tr.x[~np.isnan(tr.x)][::30]), np.diff(tr.y[~np.isnan(tr.y)][::30])).sum()) for tr in tracks), 1),
        "max_dist_from_takeoff_m": round(max_from_home, 1),
        "enclosing_circle": {"lat": float(clat), "lon": float(clon), "radius_m": round(r, 1)},
        "bbox": {"min_lat": float(np.nanmin(lats)), "min_lon": float(np.nanmin(lons)),
                 "max_lat": float(np.nanmax(lats)), "max_lon": float(np.nanmax(lons))},
        "area_seen_m2": round(area_m2(cov.get("all")), 0),
        "area_mappable_m2": round(area_m2(cov.get("mappable")), 0),
        "area_nadir_m2": round(area_m2(cov.get("nadir")), 0),
        "hull_area_m2": round(hull.area, 0) if hull is not None else 0,
        "max_hag_m": round(max(float(np.max(tr.hag)) for tr in tracks), 1),
        "terrain_model": "Copernicus GLO-30" if terrain.available else "flat (takeoff level)",
        "camera_models": sorted({tel.camera_model for _, tel in tels.values()}),
        "warnings": warnings,
    }
    analysis = {"crs_epsg": proj.epsg, "lat0": lat0, "lon0": lon0, "flights": flights, "summary": summary,
                "intrinsics": {str(k): {"focal_px": v.focal_px, "w": v.width, "h": v.height,
                                        "hfov": round(v.hfov_deg, 2)} for k, v in intr.items()}}
    (pdir / "analysis.json").write_text(json.dumps(analysis, indent=1, default=float))
    (pdir / "track.geojson").write_text(json.dumps(tfc))
    cov_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": k, "area_m2": round(cov[k].area, 1)},
         "geometry": json.loads(to_geojson(g))} for k, g in cov_wgs.items()]}
    (pdir / "coverage.geojson").write_text(json.dumps(cov_fc))
    (pdir / "flight.kml").write_text(kml(tfc, cov_wgs, homes, f"Project {pid}"))
    db.update("projects", pid, status="analyzed", summary=summary, error=None)
    ctx.progress(1.0, "done", force=True)


# ----------------------------------------------------------------- select

@handler("select")
def select(ctx: JobContext) -> None:
    sid = ctx.job["ref_id"]
    sel = db.one("SELECT * FROM selections WHERE id=?", (sid,))
    db.update("selections", sid, status="running", error=None)
    try:
        _select(ctx, sel)
    except Exception as e:
        db.update("selections", sid, status="failed" if not isinstance(e, KeyboardInterrupt) else "cancelled",
                  error=str(e)[:2000])
        raise


def _select(ctx: JobContext, sel: dict) -> None:
    pid, sid = sel["project_id"], sel["id"]
    params = SelectionParams.from_dict(sel["params"])
    pdir = project_dir(pid)
    sdir = pdir / "selections" / f"s{sid}"
    img_dir = sdir / "images"
    thumb_dir = sdir / "thumbs"
    img_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(exist_ok=True)
    tracks, analysis = load_project_tracks(pid)
    videos = {v["id"]: v for v in db.all_("SELECT * FROM videos WHERE project_id=? AND status='ok'", (pid,))}
    only = set(sel["params"].get("videos") or auto_videos(pid))
    if not only:
        raise ValueError("none of the clips points the camera down enough (30 deg or more) to make a map")
    if only:
        tracks = {k: v for k, v in tracks.items() if k in only}
    intr = {vid: CAMERAS.intrinsics(Telemetry.load(pdir / "telemetry" / f"v{vid}.npz").camera_model,
                                    videos[vid]["info"]["width"], videos[vid]["info"]["height"])[0]
            for vid in tracks}

    # 1. quality scan (cached per video): the slow pass, one decode per clip
    q_by: dict[int, dict] = {}
    vids = sorted(tracks)
    for k, vid in enumerate(vids):
        v = videos[vid]
        cb = ctx.sub(0.45 * k / len(vids), 0.45 * (k + 1) / len(vids), f"scanning {v['name']}")
        q_by[vid] = quality_scan(Path(v["path"]), params.scan_step, pdir / "quality" / f"v{vid}_s{params.scan_step}.npz",
                                 progress=cb, total_frames=v["info"]["frames"])

    # 2. selection
    ctx.progress(0.46, "selecting frames")
    selections = [select_clip(tracks[vid], intr[vid], q_by[vid], params) for vid in vids]
    notes = thin_to_cap(selections, params.max_frames)
    wanted = {s.clip_id: list(s.frames) for s in selections}

    # 3. export + link check + densify
    exported: dict[int, dict[int, Path]] = {vid: {} for vid in vids}

    def export(req: dict[int, list[int]], lo: float, hi: float, label: str):
        todo = [(vid, [f for f in fr if f not in exported[vid]]) for vid, fr in req.items()]
        todo = [(v, f) for v, f in todo if f]
        n = sum(len(f) for _, f in todo) or 1
        done = 0
        for vid, frames in todo:
            ctx.progress(lo + (hi - lo) * done / n, label, f"{videos[vid]['name']}: {len(frames)} frames")
            span = (hi - lo) * len(frames) / n
            base = lo + (hi - lo) * done / n
            out = export_frames(Path(videos[vid]["path"]), frames, img_dir / f"v{vid}",
                                progress=lambda f, b=base, s=span: ctx.progress(b + s * f))
            for f, p in zip(sorted(set(frames)), out):
                exported[vid][f] = p
            done += len(frames)

    export(wanted, 0.47, 0.75, "exporting frames")
    links: list[dict] = []
    link_cache: dict = {}
    if params.link_check:
        for rnd in range(params.densify_rounds + 1):
            images = [{"clip": vid, "frame": f, "path": str(p)} for vid in vids for f, p in exported[vid].items()]
            lo = 0.75 + 0.15 * rnd / (params.densify_rounds + 1)
            links = check_links(images, tracks, intr, params, known=link_cache,
                                progress=ctx.sub(lo, lo + 0.1 / (params.densify_rounds + 1), "checking image links"))
            if rnd == params.densify_rounds:
                break
            req = densify_requests(links, q_by, tracks, {vid: set(exported[vid]) for vid in vids})
            if not req:
                break
            notes.append(f"round {rnd + 1}: added {sum(len(v) for v in req.values())} frames at weak or "
                         "inconsistent links")
            export(req, lo + 0.1 / (params.densify_rounds + 1), lo + 0.15 / (params.densify_rounds + 1),
                   "densifying weak links")

    # 4. geotag + manifest
    ctx.progress(0.92, "geotagging")
    from PIL import Image
    images = []
    flight_sigma_z = 0.5
    total = sum(len(e) for e in exported.values()) or 1
    k = 0
    for vid in vids:
        tr = tracks[vid]
        info = videos[vid]["info"]
        tel_model = Telemetry.load(pdir / "telemetry" / f"v{vid}.npz").camera_model
        f35 = CAMERAS.nominal_f35(tel_model)
        for f, path in sorted(exported[vid].items()):
            name = f"v{vid:03d}_f{f:07d}.jpg"
            final = img_dir / name
            if path != final:
                path.replace(final)
            meta = {
                "camera_model": tel_model, "time_utc": frame_time(info.get("creation_time"), tr.t[f]),
                "lat": float(tr.lat[f]), "lon": float(tr.lon[f]), "abs_alt": float(tr.abs_alt[f]),
                "rel_h": float(tr.rel_h[f]), "g_pitch": float(tr.g_pitch[f]), "g_roll": float(tr.g_roll[f]),
                "heading": float(tr.heading[f]), "vn": float(tr.vn[f]), "ve": float(tr.ve[f]),
                "vd": float(tr.vd[f]),
                "xy_sigma": float(tr.xy_sigma[f]) if tr.xy_sigma is not None and not math.isnan(tr.xy_sigma[f]) else 3.0,
                "z_sigma": flight_sigma_z, "f35": f35, "width": info["width"], "height": info["height"],
                "clip": vid, "frame": f,
            }
            geotag(final, meta)
            with Image.open(final) as im:
                im.thumbnail((360, 360))
                im.save(thumb_dir / name, quality=80)
            images.append({"name": name, "clip": vid, "frame": int(f), "t": round(float(tr.t[f]), 3),
                           "lat": meta["lat"], "lon": meta["lon"], "abs_alt": round(meta["abs_alt"], 2),
                           "hag": round(float(tr.hag[f]), 1), "pitch": meta["g_pitch"], "roll": meta["g_roll"],
                           "heading": round(meta["heading"], 1), "xy_sigma": round(meta["xy_sigma"], 2)})
            k += 1
            if k % 10 == 0:
                ctx.progress(0.92 + 0.07 * k / total, "geotagging")
    for vid in vids:
        d = img_dir / f"v{vid}"
        if d.exists():
            shutil.rmtree(d)

    link_status = {}
    for ln in links:
        link_status[ln["status"]] = link_status.get(ln["status"], 0) + 1
    # planned mappable coverage of the selected frames, for hole detection later
    from .coverage import footprints as fp_iter
    from .geo import LocalProjection as LP
    proj = LP(analysis["lat0"], analysis["lon0"])
    # ground the map is expected to cover: footprints of the chosen frames only;
    # angled frames count only their near ground (the far part toward the
    # horizon is never orthorectified, and would show up as false "gaps")
    polys = []
    for vid in vids:
        mine = sorted(im["frame"] for im in images if im["clip"] == vid)
        for _, kind, poly in fp_iter(tracks[vid], intr[vid], frames=mine, max_range_factor=2.0):
            if kind != "high-oblique":
                polys.append(poly)
    planned = to_wgs84(unary_union(polys), proj) if polys else None
    manifest = {
        "selection_id": sid, "params": params.to_dict(), "images": images, "links": links,
        "notes": notes, "planned_wgs84": json.loads(to_geojson(planned)) if planned is not None else None,
        "per_clip": [{"clip": s.clip_id, "name": videos[s.clip_id]["name"], "scanned": s.scanned,
                      "eligible": s.eligible, "kept": len(exported[s.clip_id]), "rejected": s.rejected}
                     for s in selections],
        "fps": {str(vid): tracks[vid].fps for vid in vids},
        "has_ypr": True,
    }
    (sdir / "manifest.json").write_text(json.dumps(manifest, default=float))
    summary = {"images": len(images), "links": link_status, "notes": notes,
               "per_clip": manifest["per_clip"],
               "disk_mb": round(sum(p.stat().st_size for p in img_dir.glob("*.jpg")) / 2**20, 1)}
    db.update("selections", sid, status="done", n_images=len(images), summary=summary)
    ctx.progress(1.0, "done", force=True)


def auto_videos(pid: int, min_pitch: float = -30.0, min_hag: float = 8.0) -> list[int]:
    """Clips worth mapping: GPS present, camera at least 30 deg down, flown high enough."""
    out = []
    for v in db.all_("SELECT * FROM videos WHERE project_id=? AND status='ok'", (pid,)):
        st = v.get("stats") or {}
        if (st.get("gimbal_pitch_median", 0) <= min_pitch and st.get("hag_median", 0) >= min_hag
                and (st.get("nadir_fraction", 0) + st.get("oblique_fraction", 0)) >= 0.3):
            out.append(v["id"])
    return out


def planned_coverage(pid: int, images: list[dict]):
    """Ground the map should cover (WGS84): chosen frames' footprints, near ground only."""
    from .coverage import footprints as fp_iter
    tracks, analysis = load_project_tracks(pid)
    videos = {v["id"]: v for v in db.all_("SELECT * FROM videos WHERE project_id=?", (pid,))}
    proj = LocalProjection(analysis["lat0"], analysis["lon0"])
    polys = []
    for vid in sorted({im["clip"] for im in images}):
        if vid not in tracks:
            continue
        model = Telemetry.load(project_dir(pid) / "telemetry" / f"v{vid}.npz").camera_model
        K = CAMERAS.intrinsics(model, videos[vid]["info"]["width"], videos[vid]["info"]["height"])[0]
        mine = sorted(im["frame"] for im in images if im["clip"] == vid)
        for _, kind, poly in fp_iter(tracks[vid], K, frames=mine, max_range_factor=2.0):
            if kind != "high-oblique":
                polys.append(poly)
    return to_wgs84(unary_union(polys), proj) if polys else None


def recheck_run(rid: int) -> dict:
    """Re-run the quality check of a finished map (no reprocessing)."""
    run = db.one("SELECT * FROM runs WHERE id=?", (rid,))
    manifest = selection_manifest(run["selection_id"])
    excluded = set(run.get("excluded") or [])
    images = [im for im in manifest["images"] if im["name"] not in excluded]
    planned = planned_coverage(run["project_id"], images)
    result = qa_mod.assess(run_dir_for(run), {"images": images}, planned)
    old = run.get("qa") or {}
    for k in ("chunks", "registration_between_parts", "camera_calibrated"):
        if k in old:
            result[k] = old[k]
    db.update("runs", rid, qa=result)
    return result


def selection_manifest(sid: int) -> dict:
    sel = db.one("SELECT * FROM selections WHERE id=?", (sid,))
    p = project_dir(sel["project_id"]) / "selections" / f"s{sid}" / "manifest.json"
    return json.loads(p.read_text())


# ----------------------------------------------------------------- reconstruct

def run_dir_for(run: dict) -> Path:
    return project_dir(run["project_id"]) / "runs" / f"r{run['id']}"


@handler("reconstruct")
def reconstruct(ctx: JobContext) -> None:
    rid = ctx.job["ref_id"]
    run = db.one("SELECT * FROM runs WHERE id=?", (rid,))
    db.update("runs", rid, status="running", error=None)
    try:
        _reconstruct(ctx, run)
    except Exception as e:
        db.update("runs", rid, status="failed", error=str(e)[:3000], finished_at=time.time())
        raise


def _reconstruct(ctx: JobContext, run: dict) -> None:
    rid, sid, pid = run["id"], run["selection_id"], run["project_id"]
    manifest = selection_manifest(sid)
    params = ctx.job["params"] or {}
    excluded = set(run.get("excluded") or [])
    images = [im for im in manifest["images"] if im["name"] not in excluded]
    if len(images) < 5:
        raise ValueError("fewer than 5 images to reconstruct")

    ok, msg = odm.docker_available()
    if not ok:
        raise RuntimeError(msg)
    use_gpu = odm.gpu_available() and params.get("gpu", True)
    image_name = odm.ODM_GPU_IMAGE if use_gpu else odm.ODM_IMAGE

    rdir = run_dir_for(run)
    subs_dir = rdir / "submodels"
    if params.get("resume") and subs_dir.exists():
        # keep finished parts (marked .naqsha_done); drop half-done ones and old merged output
        for d in subs_dir.glob("submodel_*"):
            if not (d / ".naqsha_done").exists():
                odm.remove_tree(d)
        for d in ("odm_orthophoto", "odm_dem", "odm_georeferencing", "odm_report"):
            odm.remove_tree(rdir / d)
    else:
        odm.remove_tree(rdir)
    subs_dir.mkdir(parents=True, exist_ok=True)
    if run.get("parent_run_id"):
        # an auto-repair run: borrow the parent's finished parts (hard links, no
        # extra disk); only parts whose photo set changed are rebuilt
        parent = db.one("SELECT * FROM runs WHERE id=?", (run["parent_run_id"],))
        if parent:
            for d in (run_dir_for(parent) / "submodels").glob("submodel_*"):
                if (d / ".naqsha_done").exists() and not (subs_dir / f"p{d.name}").exists():
                    shutil.copytree(d, subs_dir / f"p{d.name}", copy_function=_link_or_copy,
                                    ignore=shutil.ignore_patterns("*.shifted.*"))
    finished = {}
    for d in list(subs_dir.glob("submodel_*")) + list(subs_dir.glob("psubmodel_*")):
        try:
            finished[tuple(json.loads((d / ".naqsha_done").read_text())["images"])] = d
        except (OSError, ValueError, KeyError):
            pass
    src = project_dir(pid) / "selections" / f"s{sid}" / "images"

    videos = {v["id"]: v for v in db.all_("SELECT * FROM videos WHERE project_id=?", (pid,))}
    first = videos[images[0]["clip"]]
    width, height = first["info"]["width"], first["info"]["height"]
    megapixels = width * height / 1e6
    tel_model = Telemetry.load(project_dir(pid) / "telemetry" / f"v{first['id']}.npz").camera_model
    cameras = CAMERAS.opensfm_cameras(tel_model, width, height) if params.get("use_calibration", True) else None
    fps = float(np.median([float(v) for v in manifest["fps"].values()]))
    sigma = float(np.median([im["xy_sigma"] for im in images]))

    # orientation priors: verified patched ODM, or solve around its bug
    ctx.progress(0.003, "checking ODM", "orientation handling")
    probe_path = DATA_DIR / "odm_probe.json"
    probe_cache = json.loads(probe_path.read_text()) if probe_path.exists() else {}
    omode = odm_compat.orientation_mode(image_name, probe_cache, DATA_DIR / "odm_patch")
    probe_path.write_text(json.dumps(probe_cache))
    geo_txt, geo_stats = odm_compat.geo_file(images, buggy=omode["mode"] == "inverse")
    geo_lines = {ln.split()[0]: ln for ln in geo_txt.strip().split("\n")[1:]}
    all_priors = geo_stats["orientation_priors"] == len(images)
    geo_stats["mode"] = omode["mode"]

    base_opts = odm.build_options(
        run["preset"], has_ypr=all_priors,
        include_oblique=manifest["params"].get("include_oblique", False), fps=fps, gps_sigma=sigma,
        cameras=cameras, overrides=(run["options"] or {}).get("overrides"))
    base_opts["orthophoto-cutline"] = True       # per-chunk seams for ODM's blend-merge
    if base_opts.get("dsm") or base_opts.get("dtm"):
        base_opts["dem-euclidean-map"] = True

    # memory plan: chunks that fit what Docker can give ODM
    # GPU or not, ODM's RAM is the limit (GPU features/matching need little
    # system RAM, but dense reconstruction and texturing do)
    budget = memory.docker_budget_gb()
    cap = int(params.get("max_chunk_images") or 0) or memory.max_images(run["preset"], budget, megapixels)
    proj = LocalProjection(float(np.mean([im["lat"] for im in images])), float(np.mean([im["lon"] for im in images])))
    ex, ey = proj.to_xy([im["lat"] for im in images], [im["lon"] for im in images])
    xy = np.column_stack([ex, ey])
    overlap_m = max(15.0, 1.2 * float(np.median([im["hag"] for im in images])))
    plan = chunking.plan(xy, cap, overlap_m)
    # each queue item: (images, fallback level). Level 0 = best settings; higher
    # levels trade a little accuracy for robustness when ODM gives up on a part.
    queue = [([images[i] for i in c["members"]], 0) for c in plan]
    cores = {id(q): [images[i]["name"] for i in c["core"]] for q, c in zip(queue, plan)}
    name_idx = {im["name"]: i for i, im in enumerate(images)}

    def replan_if_bigger(current_cap: int) -> int:
        """After a part finishes the memory model knows more; merge the parts still
        waiting into fewer, bigger ones if they now fit (fewer seams, less overlap work)."""
        new_cap = int(params.get("max_chunk_images") or 0) or memory.max_images(run["preset"], budget, megapixels)
        waiting = [q for q in queue if q[1] == 0 and id(q) in cores]
        if new_cap < current_cap * 1.3 or len(waiting) < 2:
            return current_cap
        todo = sorted({name_idx[n] for q in waiting for n in cores[id(q)]})
        new_plan = chunking.plan(xy, new_cap, overlap_m, core_idx=np.array(todo))
        if len(new_plan) >= len(waiting):
            return current_cap
        for q in waiting:
            queue.remove(q)
        for c in new_plan:
            item = ([images[i] for i in c["members"]], 0)
            cores[id(item)] = [images[i]["name"] for i in c["core"]]
            queue.append(item)
        info["replanned"] = info.get("replanned", []) + [
            {"after_part": done_chunks, "cap": new_cap, "parts_before": len(waiting), "parts_after": len(new_plan)}]
        return new_cap

    info = {"overrides": (run["options"] or {}).get("overrides"), "resolved": base_opts, "gpu": use_gpu,
            "images": len(images), "priors": geo_stats, "memory_budget_gb": round(budget, 2),
            "chunk_cap": cap, "chunks": []}
    db.update("runs", rid, options=info)

    def link(ims, dest: Path):
        (dest / "images").mkdir(parents=True, exist_ok=True)
        for im in ims:
            try:
                os.link(src / im["name"], dest / "images" / im["name"])
            except OSError:
                shutil.copy2(src / im["name"], dest / "images" / im["name"])
        (dest / "geo.txt").write_text("EPSG:4326\n" + "\n".join(geo_lines[im["name"]] for im in ims) + "\n")

    # run chunks; a chunk that runs out of memory is split in two and retried
    done_chunks = 0
    crashes = 0
    cams_prior = cameras
    k = max([int(d.name.split("_")[1]) + 1 for d in subs_dir.glob("submodel_*")] or [0])
    used: set[str] = set()
    while queue:
        ims, level = queue.pop(0)
        key = tuple(sorted(im["name"] for im in ims))
        if key in finished:
            # resumed or repair run: this exact part already finished
            src_dir = finished[key]
            if src_dir.name.startswith("p"):          # borrowed from the parent run
                dst = subs_dir / f"submodel_{k:04d}"
                k += 1
                src_dir.rename(dst)
                finished[key] = src_dir = dst
            used.add(src_dir.name)
            done_chunks += 1
            info["chunks"].append({"name": src_dir.name, "images": len(ims), "result": "ok (reused)"})
            cj = src_dir / "cameras.json"
            if cj.exists():
                cams_prior = json.loads(cj.read_text())
            continue
        name = f"submodel_{k:04d}"
        k += 1
        cdir = subs_dir / name
        link(ims, cdir)
        opts = dict(base_opts)
        opts.update(FALLBACKS[level][1])
        opts["geo"] = f"/datasets/{name}/geo.txt"
        if cams_prior:
            opts["cameras"] = cams_prior
        total = done_chunks + 1 + len(queue)
        lo, hi = 0.02 + 0.85 * done_chunks / total, 0.02 + 0.85 * (done_chunks + 1) / total
        label = f"part {done_chunks + 1} of {total}" if total > 1 else ""
        runner = odm.OdmRun(subs_dir, name, opts, use_gpu, mounts=omode["mounts"], name=f"naqsha-r{rid}",
                            log_path=rdir / "odm.log")
        ctx.on_cancel[:] = [runner.kill]
        ctx.progress(lo, f"{label}: starting" if label else "starting ODM", f"{len(ims)} images, {run['preset']}")
        rc = runner.run(lambda f, st, lo=lo, hi=hi, label=label: ctx.progress(
            lo + (hi - lo) * f, f"{label}: {st}" if label else st))
        ctx.check_cancel()
        peak = runner.monitor.peak_gb if runner.monitor else 0.0
        tail = (rdir / "odm.log").read_text(errors="replace")[-30000:]
        crashed = rc != 0 and odm.docker_crashed(tail, rc)
        oom = rc != 0 and (odm.is_oom(tail, rc) or crashed)
        if crashed:
            crashes += 1
            ctx.progress(lo, "Docker stopped; restarting it", "the computer ran low on memory")
            if crashes > 3 or not odm.ensure_docker():
                raise RuntimeError("Docker stopped repeatedly while processing. Close other apps or lower "
                                   "Docker's memory share so the computer keeps enough for itself.")
        # out of memory: what we know is that it needed more than the budget
        memory.record(run["preset"], len(ims), megapixels, budget if oom else peak, oom)
        rec = {"name": name, "images": len(ims), "peak_gb": round(peak, 2), "returncode": rc,
               "stage_peaks_gb": {s_: round(v, 2) for s_, v in (runner.monitor.stage_peaks if runner.monitor else {}).items()}}
        if rc != 0:
            odm.remove_tree(cdir)
            if oom and len(ims) >= 2 * memory.MIN_CHUNK:
                sub_xy = np.array([xy[images.index(im)] for im in ims])
                halves = chunking.plan(sub_xy, max(memory.MIN_CHUNK, int(len(ims) * 0.6)), overlap_m)
                queue[:0] = [([ims[i] for i in c["members"]], level) for c in halves]
                why = "Docker stopped (host memory)" if crashed else f"out of memory at {peak:.1f} GB"
                rec["result"] = f"{why}; split into {len(halves)} smaller parts"
                info["chunks"].append(rec)
                db.update("runs", rid, options=info)
                continue
            if not oom and level + 1 < len(FALLBACKS):
                queue.insert(0, (ims, level + 1))
                rec["result"] = f"failed ({odm.diagnose_failure(tail, rc)[:120]}); retrying with {FALLBACKS[level + 1][0]}"
                info["chunks"].append(rec)
                db.update("runs", rid, options=info)
                continue
            raise RuntimeError(odm.diagnose_failure(tail, rc))
        used.add(name)
        if not params.get("keep_intermediates"):
            rec["freed_gb"] = round(prune_part(cdir) / 2**30, 2)
        rec["result"] = "ok" if level == 0 else f"ok ({FALLBACKS[level][0]})"
        rec["settings"] = FALLBACKS[level][0]
        (cdir / ".naqsha_done").write_text(json.dumps({"images": list(key), "level": level}))
        info["chunks"].append(rec)
        db.update("runs", rid, options=info)
        done_chunks += 1
        # later chunks start from the lens this chunk just calibrated
        cj = cdir / "cameras.json"
        if cj.exists():
            cams_prior = json.loads(cj.read_text())
        cap = replan_if_bigger(cap)

    # only this map's parts may reach the merge: drop leftovers (parts of an
    # earlier plan, or borrowed parts whose photos changed)
    for d in list(subs_dir.glob("submodel_*")) + list(subs_dir.glob("psubmodel_*")):
        if d.name not in used:
            odm.remove_tree(d)

    # register chunks to each other through the cameras they share
    ctx.progress(0.88, "joining parts", f"{done_chunks} part(s)")
    offsets_info = register_chunks(subs_dir)
    info["registration"] = offsets_info
    db.update("runs", rid, options=info)
    script = Path(__file__).with_name("odm_merge_script.py")
    (rdir / "chunk_offsets.json").write_text(json.dumps(offsets_info))
    r = odm.run_in_container(image_name, rdir, script, ["/datasets"], name=f"naqsha-r{rid}-merge")
    with open(rdir / "odm.log", "a") as log:
        log.write("\n===== merge =====\n" + r.stdout + r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"merging the parts failed: {r.stderr.strip()[-600:]}")
    first_report = subs_dir / "submodel_0000" / "odm_report" / "report.pdf"
    if first_report.exists() and done_chunks == 1:
        (rdir / "odm_report").mkdir(exist_ok=True)
        shutil.copy2(first_report, rdir / "odm_report" / "report.pdf")

    ctx.progress(0.93, "quality check")
    planned = from_geojson(json.dumps(manifest["planned_wgs84"])) if manifest.get("planned_wgs84") else None
    result = qa_mod.assess(rdir, {"images": images}, planned)
    result["chunks"] = info["chunks"]
    result["registration_between_parts"] = offsets_info
    cams_p = subs_dir / "submodel_0000" / "cameras.json"
    if cams_p.exists() and result["grade"] in ("good", "fair"):
        CAMERAS.save_calibration(tel_model, width, height, json.loads(cams_p.read_text()), rid)
        result["camera_calibrated"] = True
    db.update("runs", rid, status="done", qa=result, finished_at=time.time())

    # auto-heal: one extra pass when the QA finds fixable problems
    if params.get("auto_heal", True) and not run.get("parent_run_id"):
        plan_h = qa_mod.heal_plan(result, base_opts)
        if plan_h:
            child = db.insert("runs", project_id=pid, selection_id=sid, parent_run_id=rid, preset=run["preset"],
                              options={"overrides": {**((run["options"] or {}).get("overrides") or {}),
                                                     **plan_h["overrides"]}},
                              excluded=sorted(excluded | set(plan_h["exclude"])), status="queued",
                              created_at=time.time(), note=f"auto-repair of map {rid}: {plan_h['reason']}")
            from .jobs import submit
            submit("reconstruct", pid, child, {**params, "auto_heal": False})
            db.update("runs", rid, note=f"auto-repair queued as map {child}: {plan_h['reason']}")
    ctx.progress(1.0, "done", force=True)


# Retry ladder for a part ODM could not finish (not memory: that splits instead).
FALLBACKS = [
    ("best settings", {}),
    ("incremental SfM", {"sfm-algorithm": "incremental"}),
    ("incremental SfM, no rolling-shutter correction", {"sfm-algorithm": "incremental", "rolling-shutter": False}),
]


KEEP_IN_PART = {
    # merge
    "odm_orthophoto/odm_orthophoto.tif", "odm_orthophoto/odm_orthophoto_feathered.tif",
    "odm_orthophoto/odm_orthophoto_cut.tif", "odm_dem/dsm.tif", "odm_dem/dtm.tif",
    "odm_georeferencing/odm_georeferenced_model.laz", "odm_georeferencing/odm_georeferenced_model.bounds.gpkg",
    "odm_georeferencing/coords.txt",
    # quality check, alignment, calibration, resume
    "odm_report/shots.geojson", "odm_report/stats.json", "odm_report/report.pdf",
    "opensfm/reconstruction.json", "opensfm/reconstruction.topocentric.json", "cameras.json",
    ".naqsha_done", "geo.txt",
}


def prune_part(cdir: Path) -> int:
    """Delete a finished part's intermediates (features, undistorted images,
    depth maps, meshes): ~3 GB each, and 17 parts would not fit on a laptop
    disk. Keeps what merging, QA and resuming need. Returns bytes freed."""
    freed = 0
    for f in sorted(cdir.rglob("*"), reverse=True):
        rel = f.relative_to(cdir).as_posix()
        if rel.startswith("images/") or rel == "images" or rel in KEEP_IN_PART:
            continue
        if any(k.startswith(rel + "/") for k in KEEP_IN_PART):
            continue                      # a directory on the way to something we keep
        try:
            if f.is_symlink() or f.is_file():
                freed += f.stat().st_size
                f.unlink()
            elif f.is_dir() and not any(f.iterdir()):
                f.rmdir()
        except PermissionError:
            # Linux: files written by the container are root-owned
            subprocess.run(["docker", "run", "--rm", "--entrypoint", "rm", "-v", f"{f.parent.resolve()}:/x",
                            odm.ODM_IMAGE, "-rf", f"/x/{f.name}"], capture_output=True)
    return freed


def _link_or_copy(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def register_chunks(subs_dir: Path) -> dict:
    """Translation per chunk that makes shared cameras coincide (mean kept at zero)."""
    subs = sorted(p for p in subs_dir.glob("submodel_*") if (p / "odm_report" / "shots.geojson").exists())
    pos = []
    for p in subs:
        fc = json.loads((p / "odm_report" / "shots.geojson").read_text())
        pos.append({f["properties"]["filename"]: np.array(f["properties"]["translation"], float)
                    for f in fc["features"]})
    pairs = []
    shared_counts = {}
    for i in range(len(subs)):
        for j in range(i + 1, len(subs)):
            common = set(pos[i]) & set(pos[j])
            if len(common) >= 3:
                shared_counts[f"{i}-{j}"] = len(common)
                for n in common:
                    pairs.append((i, j, pos[j][n] - pos[i][n]))
    off, rms = chunking.solve_offsets(pairs, len(subs))
    # raw disagreement before correction, for the report
    raw = [float(np.hypot(*d[:2])) for _, _, d in pairs]
    return {
        "parts": [p.name for p in subs],
        "offsets_m": {p.name: [round(float(v), 3) for v in off[k]] for k, p in enumerate(subs)},
        "shared_cameras": shared_counts,
        "before_median_m": round(float(np.median(raw)), 3) if raw else 0.0,
        "after_median_m": round(rms, 3),
    }


@handler("pipeline")
def pipeline(ctx: JobContext) -> None:
    """Selection followed by reconstruction, as one job."""
    params = ctx.job["params"]
    sid = ctx.job["ref_id"]
    sel = db.one("SELECT * FROM selections WHERE id=?", (sid,))
    db.update("selections", sid, status="running")
    try:
        _select(ctx, sel)
    except Exception as e:
        db.update("selections", sid, status="failed", error=str(e)[:2000])
        raise
    rid = db.insert("runs", project_id=sel["project_id"], selection_id=sid, preset=params.get("preset", "standard"),
                    options={"overrides": params.get("overrides")}, status="queued", created_at=time.time())
    from .jobs import submit
    submit("reconstruct", sel["project_id"], rid, params)
