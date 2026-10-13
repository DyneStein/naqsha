"""HTTP API + static frontend."""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, jobs, odm, pipeline, tiles, treework
from .config import DATA_DIR, FRONTEND_DIST, ODM_GPU_IMAGE, ODM_IMAGE, project_dir
from .odm import PRESETS
from .selection import SelectionParams

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Naqsha")


@app.on_event("startup")
def _startup() -> None:
    db.init()
    # ODM containers outlive a crashed/stopped server; their jobs were just
    # marked failed, so stop them rather than let them hold CPU and RAM
    import subprocess
    r = subprocess.run(["docker", "ps", "-q", "--filter", "name=naqsha-r"], capture_output=True, text=True)
    for cid in r.stdout.split():
        subprocess.run(["docker", "kill", cid], capture_output=True)
    jobs.start_worker()


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


# ---------------------------------------------------------------- system

@app.get("/api/system")
def system():
    ok, msg = odm.docker_available()
    gpu = odm.gpu_available()
    image = ODM_GPU_IMAGE if gpu else ODM_IMAGE
    du = shutil.disk_usage(DATA_DIR)
    return {
        "docker": {"ok": ok, "detail": msg},
        "odm_image": {"name": image, "present": ok and odm.image_present(image)},
        "gpu": gpu,
        "docker_mem_gb": round(odm.docker_mem_gb(), 1) if ok else None,
        "disk_free_gb": round(du.free / 2**30, 1),
        "data_dir": str(DATA_DIR),
        "presets": PRESETS,
        "selection_defaults": SelectionParams().to_dict(),
    }


@jobs.handler("pull_odm")
def _pull(ctx: jobs.JobContext):
    import subprocess
    image = ODM_GPU_IMAGE if odm.gpu_available() else ODM_IMAGE
    ctx.progress(0.05, f"pulling {image}")
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)


@app.post("/api/system/pull-odm")
def pull_odm():
    return {"job_id": jobs.submit("pull_odm", None)}


@app.get("/api/fs")
def fs_list(path: str = Query(default=str(Path.home()))):
    p = Path(path).expanduser()
    if not p.is_dir():
        raise HTTPException(400, "not a directory")
    entries = []
    for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
        if c.name.startswith("."):
            continue
        if c.is_dir():
            entries.append({"name": c.name, "dir": True})
        elif c.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv", ".avi"}:
            entries.append({"name": c.name, "dir": False, "size": c.stat().st_size})
    return {"path": str(p), "parent": str(p.parent), "entries": entries}


# ---------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: str
    video_dir: str


@app.get("/api/projects")
def projects():
    return db.all_("SELECT * FROM projects ORDER BY id DESC")


@app.post("/api/projects")
def create_project(p: ProjectIn):
    d = Path(p.video_dir).expanduser()
    if not d.is_dir():
        raise HTTPException(400, f"folder not found: {d}")
    pid = db.insert("projects", name=p.name.strip() or d.name, video_dir=str(d.resolve()),
                    created_at=time.time(), status="new")
    jid = jobs.submit("analyze", pid)
    return {"id": pid, "job_id": jid}


@app.get("/api/projects/{pid}")
def project(pid: int):
    p = db.one("SELECT * FROM projects WHERE id=?", (pid,)) or _404("project")
    p["videos"] = db.all_("SELECT * FROM videos WHERE project_id=? ORDER BY name", (pid,))
    ap = project_dir(pid) / "analysis.json"
    if ap.exists():
        a = json.loads(ap.read_text())
        p["flights"] = a.get("flights")
        p["intrinsics"] = a.get("intrinsics")
    return p


@app.post("/api/projects/{pid}/analyze")
def reanalyze(pid: int):
    db.one("SELECT id FROM projects WHERE id=?", (pid,)) or _404("project")
    pdir = project_dir(pid)
    for sub in ("telemetry", "tracks"):
        if (pdir / sub).exists():
            shutil.rmtree(pdir / sub)
    return {"job_id": jobs.submit("analyze", pid)}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    busy = db.one("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,))
    if busy:
        raise HTTPException(409, "project has running jobs; cancel them first")
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    odm.remove_tree(project_dir(pid))
    return {"ok": True}


GEO_FILES = {"track": "track.geojson", "coverage": "coverage.geojson", "kml": "flight.kml"}


@app.get("/api/projects/{pid}/geo/{kind}")
def project_geo(pid: int, kind: str):
    if kind not in GEO_FILES:
        _404("layer")
    p = project_dir(pid) / GEO_FILES[kind]
    if not p.exists():
        _404("layer")
    if kind == "kml":
        return FileResponse(p, media_type="application/vnd.google-earth.kml+xml", filename=f"project{pid}_flight.kml")
    return FileResponse(p, media_type="application/geo+json")


# ---------------------------------------------------------------- selections

class SelectionIn(BaseModel):
    params: dict = {}
    reconstruct: dict | None = None     # if set, run ODM right after (one-click pipeline)


@app.get("/api/projects/{pid}/selections")
def selections(pid: int):
    return db.all_("SELECT * FROM selections WHERE project_id=? ORDER BY id DESC", (pid,))


@app.post("/api/projects/{pid}/selections")
def create_selection(pid: int, body: SelectionIn):
    p = db.one("SELECT * FROM projects WHERE id=?", (pid,)) or _404("project")
    if p["status"] != "analyzed":
        raise HTTPException(409, "project is not analysed yet")
    params = {**SelectionParams().to_dict(), **body.params}
    sid = db.insert("selections", project_id=pid, params=params, status="queued", created_at=time.time())
    if body.reconstruct is not None:
        jid = jobs.submit("pipeline", pid, sid, body.reconstruct)
    else:
        jid = jobs.submit("select", pid, sid)
    return {"id": sid, "job_id": jid}


@app.get("/api/selections/{sid}")
def selection(sid: int):
    s = db.one("SELECT * FROM selections WHERE id=?", (sid,)) or _404("selection")
    if s["status"] == "done":
        m = pipeline.selection_manifest(sid)
        s["images"] = m["images"]
        s["links"] = m["links"]
        s["planned_wgs84"] = m.get("planned_wgs84")
    return s


@app.get("/api/selections/{sid}/thumb/{name}")
def thumb(sid: int, name: str, full: bool = False):
    s = db.one("SELECT * FROM selections WHERE id=?", (sid,)) or _404("selection")
    base = project_dir(s["project_id"]) / "selections" / f"s{sid}" / ("images" if full else "thumbs")
    p = (base / name).resolve()
    if base.resolve() not in p.parents or not p.exists():
        _404("image")
    return FileResponse(p, media_type="image/jpeg")


@app.delete("/api/selections/{sid}")
def delete_selection(sid: int):
    s = db.one("SELECT * FROM selections WHERE id=?", (sid,)) or _404("selection")
    if s["status"] in ("queued", "running"):
        raise HTTPException(409, "selection is still being built")
    db.execute("DELETE FROM selections WHERE id=?", (sid,))
    odm.remove_tree(project_dir(s["project_id"]) / "selections" / f"s{sid}")
    for r in db.all_("SELECT * FROM runs WHERE selection_id=?", (sid,)):
        odm.remove_tree(pipeline.run_dir_for(r))
    return {"ok": True}


# ---------------------------------------------------------------- runs

class RunIn(BaseModel):
    preset: str = "standard"
    overrides: dict | None = None
    auto_heal: bool = True
    use_calibration: bool = True
    exclude: list[str] = []
    max_chunk_images: int = 0       # 0 = decided by the memory model


@app.post("/api/selections/{sid}/runs")
def create_run(sid: int, body: RunIn):
    s = db.one("SELECT * FROM selections WHERE id=?", (sid,)) or _404("selection")
    if s["status"] != "done":
        raise HTTPException(409, "selection is not ready")
    if body.preset not in PRESETS:
        raise HTTPException(400, f"unknown preset {body.preset}")
    rid = db.insert("runs", project_id=s["project_id"], selection_id=sid, preset=body.preset,
                    options={"overrides": body.overrides}, excluded=body.exclude, status="queued",
                    created_at=time.time())
    jid = jobs.submit("reconstruct", s["project_id"], rid,
                      {"auto_heal": body.auto_heal, "use_calibration": body.use_calibration,
                       "max_chunk_images": body.max_chunk_images})
    return {"id": rid, "job_id": jid}


@app.post("/api/runs/{rid}/retry")
def retry_run(rid: int):
    """Re-run a failed map, keeping every part that already finished."""
    r = _run(rid)
    if r["status"] in ("queued", "running"):
        raise HTTPException(409, "map is still being built")
    db.update("runs", rid, status="queued", error=None, qa=None)
    # same chunk size as before, so finished parts match and are reused
    cap = (r.get("options") or {}).get("chunk_cap") or 0
    jid = jobs.submit("reconstruct", r["project_id"], rid,
                      {"auto_heal": True, "resume": True, "max_chunk_images": cap})
    return {"job_id": jid}


@app.post("/api/runs/{rid}/recheck")
def recheck(rid: int):
    r = _run(rid)
    if r["status"] != "done":
        raise HTTPException(409, "only finished maps can be re-checked")
    return pipeline.recheck_run(rid)


@app.get("/api/projects/{pid}/runs")
def runs(pid: int):
    return db.all_("SELECT * FROM runs WHERE project_id=? ORDER BY id DESC", (pid,))


def _run(rid: int) -> dict:
    return db.one("SELECT * FROM runs WHERE id=?", (rid,)) or _404("run")


@app.get("/api/runs/{rid}")
def run(rid: int):
    r = _run(rid)
    rdir = pipeline.run_dir_for(r)
    layers = {}
    for name, rel in (("orthophoto", "odm_orthophoto/odm_orthophoto.tif"), ("dsm", "odm_dem/dsm.tif"),
                      ("dtm", "odm_dem/dtm.tif"), ("sharp", "trees/sharp.tif")):
        p = rdir / rel
        if p.exists():
            layers[name] = {"bounds": tiles.bounds_wgs84(p)}
            if name not in ("orthophoto", "sharp"):
                layers[name]["legend"] = tiles.legend(p)
    r["layers"] = layers
    return r


@app.get("/api/runs/{rid}/log")
def run_log(rid: int, tail: int = 400):
    p = pipeline.run_dir_for(_run(rid)) / "odm.log"
    if not p.exists():
        return Response("", media_type="text/plain")
    lines = p.read_text(errors="replace").splitlines()
    return Response("\n".join(lines[-tail:]), media_type="text/plain")


DOWNLOADS = {
    "orthophoto": ("odm_orthophoto/odm_orthophoto.tif", "image/tiff"),
    "dsm": ("odm_dem/dsm.tif", "image/tiff"),
    "dtm": ("odm_dem/dtm.tif", "image/tiff"),
    "report": ("odm_report/report.pdf", "application/pdf"),
    "pointcloud": ("odm_georeferencing/odm_georeferenced_model.laz", "application/octet-stream"),
    "log": ("odm.log", "text/plain"),
}


@app.get("/api/runs/{rid}/download/{kind}")
def download(rid: int, kind: str):
    if kind not in DOWNLOADS:
        _404("file")
    rel, mt = DOWNLOADS[kind]
    rdir = pipeline.run_dir_for(_run(rid))
    p = rdir / rel
    if not p.exists() and kind == "report":
        # chunked runs: ODM writes one report per part; serve the first
        p = next(iter(sorted(rdir.glob("submodels/submodel_*/odm_report/report.pdf"))), p)
    if not p.exists():
        _404("file")
    return FileResponse(p, media_type=mt, filename=f"run{rid}_{p.name}")


@app.get("/api/runs/{rid}/tiles/{layer}/{z}/{x}/{y}.png")
def run_tile(rid: int, layer: str, z: int, x: int, y: int):
    rel = {"orthophoto": "odm_orthophoto/odm_orthophoto.tif", "dsm": "odm_dem/dsm.tif",
           "dtm": "odm_dem/dtm.tif", "sharp": "trees/sharp.tif"}.get(layer)
    if rel is None:
        _404("layer")
    p = pipeline.run_dir_for(_run(rid)) / rel
    if not p.exists():
        _404("layer")
    return Response(tiles.tile(p, layer, z, x, y), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.delete("/api/runs/{rid}")
def delete_run(rid: int):
    r = _run(rid)
    if r["status"] in ("queued", "running"):
        raise HTTPException(409, "run is active; cancel it first")
    db.execute("DELETE FROM runs WHERE id=?", (rid,))
    odm.remove_tree(pipeline.run_dir_for(r))
    return {"ok": True}


# ---------------------------------------------------------------- trees

class TreeRunIn(BaseModel):
    min_height: float = 1.0
    pretrained: bool = False


class PlotIn(BaseModel):
    id: int | None = None
    role: str | None = None          # "test" grades the count, "teach" teaches the model
    center: list[float] | None = None
    size_m: float | None = None
    marks: list[list[float]] = []
    done: bool = False
    session: dict | None = None      # {seconds, adds, removes} of this marking session, for the experiment log


class RandomPlotsIn(BaseModel):
    n: int = 5
    size_m: float = 20.0
    role: str = "test"
    seed: int | None = None


class AskIn(BaseModel):
    n: int = 8
    size_m: float = 12.0


class TeachIn(BaseModel):
    use: str = "all"                 # all | random | active


class StartIn(BaseModel):
    project: int | None = None       # None: plain pretrained DeepForest
    version: str | None = None


class ModelIn(BaseModel):
    version: str | None = None       # None goes back to the pretrained model


def _tree_busy(rid: int) -> bool:
    return bool(db.one("SELECT id FROM jobs WHERE ref_id=? AND kind IN ('trees','trees_train','sharp') AND status IN ('queued','running')", (rid,)))


@app.post("/api/runs/{rid}/trees/sharp")
def trees_sharp(rid: int):
    """Rebuild the map image from the raw frames, for counting small plants."""
    r = _run(rid)
    if r["status"] != "done":
        raise HTTPException(409, "the map is not finished yet")
    if _tree_busy(rid):
        raise HTTPException(409, "trees are already being counted on this map")
    return {"job": jobs.submit("sharp", r["project_id"], rid, {})}


@app.get("/api/runs/{rid}/trees")
def trees_status(rid: int):
    _run(rid)
    return treework.status(rid)


@app.post("/api/runs/{rid}/trees")
def trees_count(rid: int, body: TreeRunIn):
    r = _run(rid)
    if r["status"] != "done":
        raise HTTPException(409, "the map is not finished yet")
    if _tree_busy(rid):
        raise HTTPException(409, "trees are already being counted on this map")
    return {"job": jobs.submit("trees", r["project_id"], rid, body.model_dump())}


@app.post("/api/runs/{rid}/trees/train")
def trees_train(rid: int, body: TeachIn | None = None):
    r = _run(rid)
    use = (body or TeachIn()).use
    if use not in ("all", "random", "active"):
        raise HTTPException(422, "use must be all, random or active")
    if _tree_busy(rid):
        raise HTTPException(409, "trees are already being counted on this map")
    return {"job": jobs.submit("trees_train", r["project_id"], rid, {"use": use})}


@app.post("/api/runs/{rid}/trees/ask")
def trees_ask(rid: int, body: AskIn):
    _run(rid)
    if not 1 <= body.n <= 30 or not 6 <= body.size_m <= 30:
        raise HTTPException(422, "1 to 30 patches of 6 to 30 m")
    if _tree_busy(rid):
        raise HTTPException(409, "wait for the current count to finish; questions come from its results")
    try:
        treework.ask_regions(rid, body.n, body.size_m)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(409, str(e))
    return treework.status(rid)


@app.get("/api/runs/{rid}/trees/compare")
def trees_compare(rid: int):
    _run(rid)
    return treework.compare(rid)


@app.post("/api/runs/{rid}/trees/plots")
def trees_plot_save(rid: int, body: PlotIn):
    _run(rid)
    if body.id is None and (body.center is None or body.role not in ("test", "teach")):
        raise HTTPException(422, "a new square needs a centre and a role (test or teach)")
    rec = treework.upsert_plot(rid, body.model_dump())
    return {"plot": {k: rec.get(k) for k in treework.PUBLIC}, **treework.status(rid)}


@app.post("/api/runs/{rid}/trees/plots/random")
def trees_plots_random(rid: int, body: RandomPlotsIn):
    _run(rid)
    if body.role not in ("test", "teach") or not 1 <= body.n <= 20 or not 10 <= body.size_m <= 60:
        raise HTTPException(422, "role must be test or teach, 1 to 20 squares, 10 to 60 m")
    try:
        treework.place_random(rid, body.n, body.size_m, body.role, body.seed)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(409, str(e))
    return treework.status(rid)


@app.post("/api/runs/{rid}/trees/start")
def trees_start(rid: int, body: StartIn):
    """Where this project's teaching starts: pretrained DeepForest, or a model taught on another project."""
    r = _run(rid)
    if body.project is None:
        treework.set_base_model(r["project_id"], None)
    else:
        if body.project == r["project_id"]:
            raise HTTPException(422, "pick a model from another project; this project's own versions are in the Model list")
        if body.version not in {m["version"] for m in treework.list_models(body.project)}:
            _404("model version")
        treework.set_base_model(r["project_id"], {"project": body.project, "version": body.version})
    return treework.status(rid)


@app.post("/api/runs/{rid}/trees/model")
def trees_model(rid: int, body: ModelIn):
    r = _run(rid)
    if body.version and body.version != "base" and body.version not in {m["version"] for m in treework.list_models(r["project_id"])}:
        _404("model version")
    treework.set_current_model(r["project_id"], body.version)
    return treework.status(rid)


@app.delete("/api/runs/{rid}/trees/plots/{plot_id}")
def trees_plot_delete(rid: int, plot_id: int):
    _run(rid)
    treework.delete_plot(rid, plot_id)
    return treework.status(rid)


TREE_FILES = {"trees": ("trees.geojson", "application/geo+json"), "crowns": ("crowns.geojson", "application/geo+json"),
              "density": ("density.geojson", "application/geo+json"), "csv": ("trees.csv", "text/csv"),
              "kml": ("trees.kml", "application/vnd.google-earth.kml+xml"), "chm": ("chm.tif", "image/tiff"), "sharp": ("sharp.tif", "image/tiff"),
              "log": ("experiment.jsonl", "application/x-ndjson")}


class TreeSettingsIn(BaseModel):
    min_height_m: float | None = None
    imagery: str | None = None        # "map" (stitched orthophoto) or "sharp" (rebuilt from the raw frames)


@app.post("/api/runs/{rid}/trees/settings")
def trees_settings(rid: int, body: TreeSettingsIn):
    _run(rid)
    if body.min_height_m is not None and not 0 <= body.min_height_m <= 40:
        raise HTTPException(422, "height must be 0 to 40 m")
    if body.imagery not in (None, "map", "sharp"):
        raise HTTPException(422, "imagery must be map or sharp")
    treework.save_settings(rid, body.model_dump())
    return treework.status(rid)


@app.get("/api/runs/{rid}/trees/file/{kind}")
def trees_file(rid: int, kind: str, download: bool = False, min_h: float = 0.0):
    if kind not in TREE_FILES:
        _404("file")
    name, mt = TREE_FILES[kind]
    p = pipeline.run_dir_for(_run(rid)) / "trees" / name
    if not p.exists():
        _404("file")
    fname = f"map{rid}_{name}" if not min_h else f"map{rid}_{p.stem}_min{min_h:g}m{p.suffix}"
    disp = {"Content-Disposition": f'attachment; filename="{fname}"'} if download else {}
    if min_h > 0 and kind in ("trees", "crowns", "density", "csv", "kml"):
        f = treework.filtered_files(rid, min_h)
        if kind in ("trees", "crowns", "density"):
            return Response(json.dumps(f[kind]), media_type=mt, headers={"Cache-Control": "no-cache", **disp})
        feats = f["trees"]["features"]
        if kind == "csv":
            rows = ["id,lat,lon,height_m,crown_m,source,score"]
            for ft in feats:
                q = ft["properties"]; lon, lat = ft["geometry"]["coordinates"]
                rows.append(",".join(str(v) if v is not None else "" for v in (q["id"], lat, lon, q["height_m"], q["crown_m"], q["source"], q["score"])))
            return Response("\n".join(rows) + "\n", media_type=mt, headers=disp)
        kml = ['<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
               f'<name>Trees at least {min_h:g} m tall</name>']
        for ft in feats:
            q = ft["properties"]; lon, lat = ft["geometry"]["coordinates"]
            kml.append(f"<Placemark><name>{q['id']}</name><description>height {q['height_m']} m, crown {q['crown_m']} m"
                       f"</description><Point><coordinates>{lon},{lat}</coordinates></Point></Placemark>")
        kml.append("</Document></kml>")
        return Response("".join(kml), media_type=mt, headers=disp)
    return FileResponse(p, media_type=mt, filename=fname if download else None, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- jobs

@app.get("/api/jobs")
def job_list(project_id: int | None = None, limit: int = 50):
    if project_id is None:
        return db.all_("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
    return db.all_("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit))


@app.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: int):
    jobs.cancel(jid)
    return {"ok": True}


# ---------------------------------------------------------------- frontend

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (FRONTEND_DIST / path).resolve()
        if path and f.is_file() and FRONTEND_DIST.resolve() in f.parents:
            return FileResponse(f)
        # index.html names the hashed bundles; never cache it, or rebuilds go unseen
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
else:
    @app.get("/", include_in_schema=False)
    def no_ui():
        return JSONResponse({"detail": "frontend not built; run `npm run build` in frontend/"})
