"""Runs INSIDE the ODM container: merge chunk outputs with ODM's own code.

Usage: python3 odm_merge_script.py /datasets/project
Expects /datasets/project/submodels/submodel_*/ (each a finished ODM project
run with --orthophoto-cutline and --dem-euclidean-map) and writes merged
products to the standard ODM locations under /datasets/project.
"""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, "/code")
from opendm import orthophoto, point_cloud  # noqa: E402
from opendm.cogeo import convert_to_cogeo  # noqa: E402
from opendm.dem.merge import euclidean_merge_dems  # noqa: E402

root = sys.argv[1]
subs = sorted(glob.glob(os.path.join(root, "submodels", "submodel_*")))
report = {"chunks": len(subs)}
threads = os.cpu_count() or 4

ORTHO_VARS = {"TILED": "YES", "COMPRESS": "DEFLATE", "PREDICTOR": "2", "BIGTIFF": "IF_SAFER",
              "BLOCKXSIZE": 512, "BLOCKYSIZE": 512, "NUM_THREADS": threads}
DEM_VARS = {"TILED": "YES", "COMPRESS": "DEFLATE", "PREDICTOR": "2", "BIGTIFF": "IF_SAFER",
            "BLOCKXSIZE": 512, "BLOCKYSIZE": 512, "NUM_THREADS": threads}

# 1. co-registration: shift each chunk so shared cameras coincide.
# Non-destructive: part files are never edited (they may be shared, via hard
# links, with another run); a shifted copy is merged instead. (A VRT would be
# lighter, but ODM's merge creates its output with the first input's driver.)
from osgeo import gdal  # noqa: E402

gdal.UseExceptions()

offsets_path = os.path.join(root, "chunk_offsets.json")
offs = json.load(open(offsets_path)).get("offsets_m", {}) if os.path.exists(offsets_path) and len(subs) > 1 else {}
SHIFTED = {}


def shifted(path, dx, dy, kind):
    if abs(dx) < 0.02 and abs(dy) < 0.02:
        return path
    base, _ = os.path.splitext(path)
    out = base + ".shifted.tif"
    if os.path.exists(out):
        os.remove(out)
    gdal.Translate(out, path, format="GTiff",
                   creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER",
                                    f"NUM_THREADS={threads}"])
    ds = gdal.Open(out, gdal.GA_Update)
    gt = list(ds.GetGeoTransform())
    gt[0] += dx
    gt[3] += dy
    ds.SetGeoTransform(gt)
    ds = None
    for stale in (base + ".shifted.euclideand.tif",):
        if os.path.exists(stale):
            os.remove(stale)
    return out


def part_file(sub, rel, kind):
    f = os.path.join(sub, rel)
    if not os.path.exists(f):
        return f
    dx, dy, _ = offs.get(os.path.basename(sub), [0.0, 0.0, 0.0])
    return shifted(f, dx, dy, kind)


report["shifted_chunks"] = sum(1 for v in offs.values() if abs(v[0]) >= 0.02 or abs(v[1]) >= 0.02)

# 2. orthophoto: blend along ODM's per-chunk cutlines
pairs = []
for s in subs:
    f = os.path.join(s, "odm_orthophoto", "odm_orthophoto_feathered.tif")
    c = os.path.join(s, "odm_orthophoto", "odm_orthophoto_cut.tif")
    if os.path.exists(f) and os.path.exists(c):
        pairs.append([part_file(s, "odm_orthophoto/odm_orthophoto_feathered.tif", "ortho"),
                      part_file(s, "odm_orthophoto/odm_orthophoto_cut.tif", "ortho")])
os.makedirs(os.path.join(root, "odm_orthophoto"), exist_ok=True)
out_ortho = os.path.join(root, "odm_orthophoto", "odm_orthophoto.tif")
if os.path.exists(out_ortho):
    os.remove(out_ortho)
if len(pairs) > 1:
    orthophoto.merge(pairs, out_ortho, ORTHO_VARS)
elif len(subs) == 1:
    # one part: the plain orthophoto (the feathered copy fades its own edges)
    single = os.path.join(subs[0], "odm_orthophoto", "odm_orthophoto.tif")
    if os.path.exists(single):
        subprocess.run(["cp", single, out_ortho], check=True)
if os.path.exists(out_ortho):
    convert_to_cogeo(out_ortho, max_workers=threads)
report["orthophoto_inputs"] = len(pairs)

# 3. DEMs: distance-weighted merge (ODM's euclidean merge)
os.makedirs(os.path.join(root, "odm_dem"), exist_ok=True)
for name in ("dsm.tif", "dtm.tif"):
    dems = [part_file(s, os.path.join("odm_dem", name), "dem") for s in subs
            if os.path.exists(os.path.join(s, "odm_dem", name))]
    if not dems:
        continue
    out = os.path.join(root, "odm_dem", name)
    if os.path.exists(out):
        os.remove(out)
    if len(dems) == 1:
        subprocess.run(["cp", dems[0], out], check=True)
    else:
        euclidean_merge_dems(dems, out, DEM_VARS)
    if os.path.exists(out):
        convert_to_cogeo(out, max_workers=threads)
    report[name] = len(dems)

# 4. point cloud (chunks are not shifted here; the rasters are the product)
clouds = [p for p in (os.path.join(s, "odm_georeferencing", "odm_georeferenced_model.laz") for s in subs)
          if os.path.exists(p)]
if clouds:
    os.makedirs(os.path.join(root, "odm_georeferencing"), exist_ok=True)
    out = os.path.join(root, "odm_georeferencing", "odm_georeferenced_model.laz")
    try:
        if len(clouds) == 1:
            subprocess.run(["cp", clouds[0], out], check=True)
        else:
            point_cloud.merge(clouds, out, rerun=True)
        report["pointcloud"] = len(clouds)
    except Exception as e:  # point cloud is a bonus, never fail the map for it
        report["pointcloud_error"] = str(e)

with open(os.path.join(root, "merge_report.json"), "w") as f:
    json.dump(report, f)
print(json.dumps(report))
