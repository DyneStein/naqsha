"""Command line: the whole pipeline with no clicks.

  python -m naqsha serve                       # the web app
  python -m naqsha process <video folder>      # videos -> finished map, unattended
        [--name NAME] [--quality preview|standard|max] [--out DIR] [--overlap 0.85] [--trees [--tree-min-height 4]]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path


def _wait(job_ids, db, label: str):
    from .words import friendly
    last = ""
    while True:
        jobs = [db.one("SELECT * FROM jobs WHERE id=?", (j,)) for j in job_ids]
        # pipeline jobs spawn a reconstruct job; follow the newest job of the project
        live = db.one("SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1", (jobs[0]["project_id"],))
        line = f"{label}: {friendly(live['stage'])} {round(100 * (live['progress'] or 0))}%"
        if line != last:
            print(line, flush=True)
            last = line
        if live["status"] in ("done", "failed", "cancelled") and \
                not db.one("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')",
                           (live["project_id"],)):
            return live
        time.sleep(3)


def process(args) -> int:
    from . import db, jobs, pipeline, treework  # noqa: F401  (registers handlers)
    from .odm import docker_available, ensure_docker
    import os
    db.init()
    jobs.OWNER = os.getpid()
    jobs.start_worker()
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"not a folder: {folder}", file=sys.stderr)
        return 2
    if not docker_available()[0] and not ensure_docker():
        print("Docker is not running and could not be started.", file=sys.stderr)
        return 2
    pid = db.insert("projects", name=args.name or folder.name, video_dir=str(folder),
                    created_at=time.time(), status="new")
    jid = jobs.submit("analyze", pid)
    end = _wait([jid], db, "1/2 reading videos")
    p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if p["status"] != "analyzed":
        print(f"analysis failed: {p['error']}", file=sys.stderr)
        return 1
    s = p["summary"]
    print(f"   {s['clips']} clips, {s['flights']} flights, {s['path_m'] / 1000:.2f} km flown, "
          f"max {s['max_dist_from_takeoff_m']:.0f} m from takeoff, {s['area_mappable_m2'] / 1e4:.1f} ha mappable")
    vids = pipeline.auto_videos(pid)
    if not vids:
        print("no clip has the camera pointing down enough to map", file=sys.stderr)
        return 1
    from .selection import SelectionParams
    params = {**SelectionParams().to_dict(), "videos": vids, "include_oblique": True,
              "forward_overlap": args.overlap}
    sid = db.insert("selections", project_id=pid, params=params, status="queued", created_at=time.time())
    jid = jobs.submit("pipeline", pid, sid, {"preset": args.quality, "auto_heal": True})
    end = _wait([jid], db, "2/2 building the map")
    run = db.one("SELECT * FROM runs WHERE project_id=? AND status='done' ORDER BY id DESC LIMIT 1", (pid,))
    if not run:
        print(f"map failed: {(end.get('error') or 'see the log')[:500]}", file=sys.stderr)
        return 1
    rdir = pipeline.run_dir_for(run)
    qa = run["qa"] or {}
    print(f"   quality: {qa.get('grade')}; {qa.get('registration', {}).get('registered')}/"
          f"{qa.get('registration', {}).get('images_in')} photos fitted; "
          f"{(qa.get('ortho') or {}).get('valid_area_m2', 0) / 1e4:.2f} ha mapped")
    out = Path(args.out).expanduser() if args.out else folder / "naqsha-output"
    out.mkdir(parents=True, exist_ok=True)
    pdir = pipeline.project_dir(pid)
    copies = {"map.tif": rdir / "odm_orthophoto" / "odm_orthophoto.tif", "elevation.tif": rdir / "odm_dem" / "dsm.tif",
              "ground.tif": rdir / "odm_dem" / "dtm.tif", "flight.kml": pdir / "flight.kml",
              "coverage.geojson": pdir / "coverage.geojson", "track.geojson": pdir / "track.geojson"}
    for name, src in copies.items():
        if src.exists():
            shutil.copy2(src, out / name)
    (out / "quality.json").write_text(json.dumps(qa, indent=1, default=str))
    if args.trees:
        if not qa.get("outputs", {}).get("dsm"):
            print("   trees skipped: this quality has no elevation model", file=sys.stderr)
        else:
            _wait([jobs.submit("trees", pid, run["id"], {"min_height": 1.0})], db, "3/3 counting trees")
            tdir = rdir / "trees"
            if (tdir / "summary.json").exists():
                ts = json.loads((tdir / "summary.json").read_text())
                mh = args.tree_min_height
                treework.save_settings(run["id"], {"min_height_m": mh})
                f = treework.filtered_files(run["id"], mh)
                n = len(f["trees"]["features"])
                print(f"   {n} trees{f' at least {mh:g} m tall' if mh else ''} ({ts['count']} woody plants detected; "
                      f"not yet checked by hand: mark sample squares in the app to measure accuracy)")
                for name in ("trees", "crowns", "density"):
                    (out / f"{name}.geojson").write_text(json.dumps(f[name]))
                shutil.copy2(tdir / "trees.csv", out / "trees_all.csv")
    print(f"done -> {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="naqsha")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    pr = sub.add_parser("process")
    pr.add_argument("folder")
    pr.add_argument("--name")
    pr.add_argument("--quality", choices=["preview", "standard", "max"], default="standard")
    pr.add_argument("--overlap", type=float, default=0.85)
    pr.add_argument("--out")
    pr.add_argument("--trees", action="store_true", help="also count the trees on the finished map")
    pr.add_argument("--tree-min-height", type=float, default=0.0, help="count only trees at least this tall, in metres")
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("naqsha.api:app", host=a.host, port=a.port)
        return 0
    return process(a)


if __name__ == "__main__":
    sys.exit(main())
