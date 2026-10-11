import json
import math
import os
import struct
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("NAQSHA_DATA", str(Path(__file__).parent / ".tmpdata"))

from naqsha.analysis import CleanTrack, fuse_position
from naqsha.geo import Intrinsics, camera_axes, ground_footprint
from naqsha.odm import build_options, to_args
from naqsha.qa import _rotation_matrix, heal_plan, shot_residuals
from naqsha.selection import SelectionParams, select_clip
from naqsha.telemetry.protobuf import decode, get, signed64

VIDEOS = Path(__file__).resolve().parents[3] / "1 October 2026 Shakarparia scan"


# ------------------------------------------------------------- protobuf

def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def test_protobuf_nested_double_and_negative_varint():
    inner = b"\x11" + struct.pack("<d", 33.6874) + b"\x19" + struct.pack("<d", 73.0724)
    msg = b"\x0a" + _varint(len(inner)) + inner + b"\x10" + _varint((1 << 64) - 289)
    m = decode(msg)
    assert get(m, 1, 2) == pytest.approx(33.6874)
    assert get(m, 1, 3) == pytest.approx(73.0724)
    assert signed64(get(m, 2)) == -289
    assert get(m, 9, default=0) == 0  # proto3: absent means zero


# ------------------------------------------------------------- geometry

def test_camera_axes_conventions():
    r, d, f = camera_axes(0, 0)
    assert np.allclose(f, [0, 1, 0]) and np.allclose(r, [1, 0, 0]) and np.allclose(d, [0, 0, -1])
    r, d, f = camera_axes(90, -90)          # nadir, heading east
    assert np.allclose(f, [0, 0, -1], atol=1e-9)
    assert np.allclose(d, [-1, 0, 0], atol=1e-9)   # image down points back (west) => image top is east


def test_nadir_footprint_size_and_orientation():
    K = Intrinsics.from_f35(3840, 2160, 24)
    pts = np.array(ground_footprint(0, 0, 35, 0, -90, 0, K, 1000))
    w = pts[:, 0].max() - pts[:, 0].min()
    h = pts[:, 1].max() - pts[:, 1].min()
    assert w == pytest.approx(2 * 35 * math.tan(math.radians(K.hfov_deg / 2)), rel=1e-3)
    assert h == pytest.approx(2 * 35 * math.tan(math.radians(K.vfov_deg / 2)), rel=1e-3)
    # image top-left (first border sample) lies north-west for heading 0
    assert pts[0, 0] < 0 and pts[0, 1] > 0


def test_oblique_footprint_is_clipped():
    K = Intrinsics.from_f35(3840, 2160, 24)
    pts = np.array(ground_footprint(0, 0, 30, 0, -10, 0, K, 120))
    assert np.hypot(pts[:, 0], pts[:, 1]).max() <= 120 + 1e-6


# ------------------------------------------------------------- GPS fusion

def test_fusion_absorbs_step_jump():
    fps = 60.0
    t = np.arange(0, 120, 1 / fps)
    ve = np.full_like(t, 3.0); vn = np.zeros_like(t)
    true_x = 3.0 * t
    fix_t = t[::6]
    fx = true_x[::6] + np.random.default_rng(0).normal(0, 0.8, len(fix_t))
    fx[fix_t > 60] += 20.0                      # persistent GNSS jump
    fy = np.zeros_like(fx)
    x, y, resid, jumps = fuse_position(t, fix_t, fx, fy, vn, ve)
    assert len(jumps) == 1 and jumps[0][1] == pytest.approx(20, abs=3)
    # local geometry comes from velocity: the jump is spread over ~10 s, so no
    # frame-to-frame step deviates from v/fps by more than ~20 m / 10 s
    steps = np.diff(x)
    assert np.abs(steps - 3.0 / fps).max() < 0.08
    assert np.median(np.abs(steps - 3.0 / fps)) < 0.005
    # far from the jump, position tracks the GPS anchor
    assert abs(x[t < 30][-1] - true_x[t < 30][-1]) < 2.0


def test_jump_widens_position_uncertainty():
    from naqsha.analysis import clean_clip
    from naqsha.geo import LocalProjection
    from naqsha.telemetry.model import Telemetry
    fps = 60.0
    n = int(120 * fps)
    tel = Telemetry.empty(n, "test", fps, "DJI FC9313")
    d = tel.data
    t = d["t"]
    proj = LocalProjection(33.68, 73.07)
    x0, y0 = proj.to_xy(33.68, 73.07)
    xs = x0 + 3.0 * t
    xs = np.where(t > 60, xs + 20, xs)
    lat, lon = proj.to_ll(xs, np.full(n, y0))
    step = np.arange(n) // 6 * 6           # 10 Hz sample-and-hold, like DJI
    d["lat"], d["lon"] = np.asarray(lat)[step], np.asarray(lon)[step]
    d["gps_new"] = (np.arange(n) % 6 == 0).astype(float)
    d["ve"][:] = 3.0; d["vn"][:] = 0; d["vd"][:] = 0
    for k in ("alt", "rel_h", "pitch", "roll", "yaw", "g_pitch", "g_roll", "g_yaw"):
        d[k][:] = 0
    tr = clean_clip(1, tel, proj)
    assert len(tr.gps_jumps) == 1
    assert tr.xy_sigma[int(61 * fps)] > 8          # right after the jump
    assert tr.xy_sigma[int(10 * fps)] == pytest.approx(2.5)   # far away


# ------------------------------------------------------------- selection

def _straight_track(n_s=60, fps=60.0, speed=4.0, hag=35.0):
    t = np.arange(0, n_s, 1 / fps)
    n = len(t)
    z = np.zeros(n)
    return CleanTrack(
        clip_id=1, fps=fps, t=t, lat=z + 33.6, lon=z + 73.0, x=z.copy(), y=speed * t, rel_h=z + hag,
        abs_alt=z + 500 + hag, hag=z + hag, heading=z.copy(), g_pitch=z - 90, g_roll=z.copy(),
        speed=z + speed, vn=z + speed, ve=z.copy(), vd=z.copy(), yaw_rate=z.copy(), wb=z.copy(),
        phase=z.astype(int) + 4, gps_ok=np.ones(n, bool), xy_sigma=z + 2.5)


def test_selection_spacing_matches_overlap_target():
    tr = _straight_track()
    K = Intrinsics.from_f35(3840, 2160, 24)
    frames = np.arange(0, len(tr.t), 3)
    q = {"frame": frames, "sharp": np.ones(len(frames)) * 100, "bright": np.full(len(frames), 120.0)}
    p = SelectionParams(forward_overlap=0.8)
    sel = select_clip(tr, K, q, p)
    along = 2 * 35 * math.tan(math.radians(K.vfov_deg / 2))   # flying "up" the image
    expected_gap_s = (1 - 0.8) * along / 4.0
    gaps = np.diff(sel.frames) / tr.fps
    assert np.median(gaps) == pytest.approx(expected_gap_s, rel=0.15)
    # no near-duplicates
    assert gaps.min() > 0.5 * expected_gap_s


def test_selection_rejects_blur_and_low_frames():
    tr = _straight_track()
    tr.hag[:600] = 3.0                                  # first 10 s: still climbing
    K = Intrinsics.from_f35(3840, 2160, 24)
    frames = np.arange(0, len(tr.t), 3)
    sharp = np.ones(len(frames)) * 100
    sharp[400:420] = 20                                 # a burst of motion blur
    q = {"frame": frames, "sharp": sharp, "bright": np.full(len(frames), 120.0)}
    sel = select_clip(tr, K, q, SelectionParams())
    assert min(sel.frames) >= 600
    assert not any(frames[400] <= f <= frames[419] for f in sel.frames)
    assert sel.rejected.get("motion blur", 0) >= 15


# ------------------------------------------------------------- geotag

def test_geotag_roundtrip(tmp_path):
    from datetime import datetime, timezone
    import piexif
    from PIL import Image
    from naqsha.geotag import geotag
    p = tmp_path / "f.jpg"
    Image.new("RGB", (64, 36), (90, 120, 60)).save(p, quality=90)
    meta = dict(camera_model="DJI FC9313", time_utc=datetime(2026, 10, 1, 9, 51, 1, 250000, tzinfo=timezone.utc),
                lat=33.6874123, lon=73.0724321, abs_alt=576.4, rel_h=34.0, g_pitch=-89.9, g_roll=0.0,
                heading=312.5, vn=2.9, ve=-3.1, vd=0.0, xy_sigma=3.0, z_sigma=0.5, f35=24.0,
                width=64, height=36, clip=4, frame=1234)
    geotag(p, meta)
    ex = piexif.load(str(p))
    lat = sum(n / d / 60 ** i for i, (n, d) in enumerate(ex["GPS"][piexif.GPSIFD.GPSLatitude]))
    lon = sum(n / d / 60 ** i for i, (n, d) in enumerate(ex["GPS"][piexif.GPSIFD.GPSLongitude]))
    assert lat == pytest.approx(33.6874123, abs=1e-7) and lon == pytest.approx(73.0724321, abs=1e-7)
    assert ex["0th"][piexif.ImageIFD.Make] == b"DJI" and ex["0th"][piexif.ImageIFD.Model] == b"FC9313"
    raw = p.read_bytes()
    assert b'drone-dji:FlightYawDegree="-47.5000"' in raw          # 312.5 -> signed
    assert b'drone-dji:GimbalPitchDegree="-89.9000"' in raw
    assert b'Camera:GPSXYAccuracy="1.500"' in raw                   # ODM doubles it back to 3 m
    Image.open(p).verify()


# ------------------------------------------------------------- QA

def test_shot_residuals(tmp_path):
    R = _rotation_matrix([0.1, -0.2, 0.3])
    c = np.array([10.0, 5.0, 30.0])
    tvec = (-R @ c).tolist()
    recon = [{"shots": {
        "a.jpg": {"rotation": [0.1, -0.2, 0.3], "translation": tvec, "gps_position": [10.0, 5.0, 30.0]},
        "b.jpg": {"rotation": [0.1, -0.2, 0.3], "translation": tvec, "gps_position": [22.0, 5.0, 30.0]},
    }}]
    f = tmp_path / "reconstruction.json"
    f.write_text(json.dumps(recon))
    res, sizes = shot_residuals(f)
    d = {r["image"]: r["dxy"] for r in res}
    assert sizes == [2] and d["a.jpg"] == pytest.approx(0, abs=1e-6) and d["b.jpg"] == pytest.approx(12)


def test_heal_plan():
    qa = {"registration": {"images_in": 100, "dropped_count": 30, "components": [60, 10]},
          "flagged_images": [{"image": "x.jpg"}, {"image": "y.jpg"}]}
    plan = heal_plan(qa, {"min-num-features": 12000, "matcher-neighbors": 24})
    assert plan["exclude"] == ["x.jpg", "y.jpg"]
    assert plan["overrides"]["min-num-features"] == 18000
    assert heal_plan({"registration": {"images_in": 100, "dropped_count": 0, "components": [100]},
                      "flagged_images": []}, {}) is None


# ------------------------------------------------------------- ODM options

def test_odm_options():
    o = build_options("standard", has_ypr=True, include_oblique=True, fps=59.94, gps_sigma=2.5,
                      cameras=None, overrides={"pc-quality": "high"})
    assert o["sfm-algorithm"] == "triangulation"
    assert o["rolling-shutter-readout"] < 1000 / 59.94            # must fit in one frame period
    assert o["sky-removal"] is True and o["pc-quality"] == "high"
    assert "split" not in o                                       # we chunk ourselves
    args = to_args(o, "/datasets/project")
    assert "--rolling-shutter" in args and "--fast-orthophoto" not in args


def test_chunk_plan_and_offsets():
    from naqsha.chunking import plan, solve_offsets
    xy = np.concatenate([np.column_stack([np.linspace(0, 600, 200), np.full(200, k * 25.0)]) for k in range(3)])
    chunks = plan(xy, 140, 30)
    assert sorted(i for c in chunks for i in c["core"]) == list(range(600))   # every frame owned once
    assert all(len(c["members"]) <= 140 for c in chunks)
    assert all(len(c["members"]) > len(c["core"]) for c in chunks)          # neighbours overlap
    off, rms = solve_offsets([(0, 1, np.array([1.0, 0, 0]))] * 5, 2)
    assert off[1, 0] - off[0, 0] == pytest.approx(-1.0) and abs(off[:, 0].sum()) < 1e-9 and rms < 1e-9


def test_patched_mode_reaches_every_orientation():
    from naqsha.odm_compat import solve_odm_inputs
    rng = np.random.default_rng(1)
    for _ in range(200):
        *_, res = solve_odm_inputs(rng.uniform(0, 360), rng.uniform(-90, -20), rng.uniform(-10, 10),
                                   33.688, 73.07, 576, buggy=False)
        assert res < 1e-3


# ------------------------------------------------------------- real footage (skipped elsewhere)

@pytest.mark.skipif(not VIDEOS.exists(), reason="sample footage not present")
def test_real_djmd_clip():
    from naqsha.telemetry import dji_djmd
    from naqsha.video import probe
    v = sorted(VIDEOS.glob("*.MP4"))[0]
    info = probe(v)
    tel, lname, _ = dji_djmd.read(v, info.telemetry_stream, info.fps, info.frames)
    assert lname == "dji-mini5pro"
    assert len(tel) == info.frames
    assert tel.camera_model == "DJI FC9313"
    assert 33.68 < np.nanmedian(tel.data["lat"]) < 33.69 and 73.07 < np.nanmedian(tel.data["lon"]) < 73.08
    assert -35 < np.median(tel.data["g_pitch"]) < -25


# ------------------------------------------------------------- ODM orientation workaround

def test_odm_opk_replica_matches_observed_odm():
    from naqsha.odm_compat import odm_opk
    # value read back from ODM 3.6.2's own exif output for a real frame
    o, p, k = odm_opk(-31.4, 0.1, 0.0, 33.68803677416666, 73.07052313777777, 576.4411)
    assert (round(o, 2), round(p, 2), round(k, 2)) == (-0.05, -0.01, -165.53)


@pytest.mark.parametrize("heading,pitch", [(328.6, -89.9), (10, -90), (200, -55), (123, -70)])
def test_corrected_priors_give_true_camera(heading, pitch):
    from naqsha.odm_compat import odm_opk, rotation_from_opk, solve_odm_inputs
    y, p, r, res = solve_odm_inputs(heading, pitch, 0.0, 33.688, 73.07, 576)
    assert res < 1e-3
    R = rotation_from_opk(*odm_opk(y, p, r, 33.688, 73.07, 576))
    up, axis = R.T @ np.array([0, -1, 0]), R.T @ np.array([0, 0, 1])
    assert math.degrees(math.atan2(up[0], up[1])) % 360 == pytest.approx(heading % 360, abs=1e-2)
    assert math.degrees(math.asin(-axis[2])) == pytest.approx(-pitch, abs=1e-2)


def test_geo_file_drops_unrepresentable_orientation():
    from naqsha.odm_compat import geo_file
    ims = [dict(name="a.jpg", heading=40, pitch=-90, lat=33.688, lon=73.07, abs_alt=576, xy_sigma=2.5),
           dict(name="b.jpg", heading=95, pitch=-35, lat=33.688, lon=73.07, abs_alt=576, xy_sigma=9.0)]
    txt, st = geo_file(ims, buggy=True)
    lines = txt.strip().split("\n")
    assert lines[0] == "EPSG:4326" and st["orientation_priors"] == 1
    assert "nan nan nan 9.00" in lines[2] and "nan" not in lines[1]


@pytest.mark.skipif(not VIDEOS.exists(), reason="sample footage not present")
def test_schema_inference_rediscovers_known_layout():
    from naqsha.telemetry.dji_djmd import MINI5PRO, extract_stream, split_frames
    from naqsha.telemetry.schema import infer, to_layout
    from naqsha.video import probe
    v = sorted(VIDEOS.glob("*.MP4"))[3]           # clip with movement and climbs
    info = probe(v)
    _, frames = split_frames(extract_stream(v, info.telemetry_stream))
    lay = to_layout(infer(frames, info.fps))
    for role in ("lat", "lon", "vn", "ve", "yaw", "rel_h", "g_pitch"):
        assert lay[role][0] == MINI5PRO[role][0], role
        assert lay[role][1] == pytest.approx(MINI5PRO[role][1]), role


def test_memory_model_learns_baseline(tmp_path, monkeypatch):
    from naqsha import memory
    monkeypatch.setattr(memory, "MODEL_PATH", tmp_path / "m.json")
    assert memory.max_images("standard", 4.0, 8.3) >= memory.MIN_CHUNK
    memory.record("preview", 296, 8.29, 2.67, False)
    memory.record("preview", 101, 8.29, 2.82, False)
    base, slope = memory.model("preview", 8.29)
    assert 2.4 < base < 2.9 and slope == pytest.approx(memory.MIN_SLOPE)
    assert memory.max_images("preview", 3.93, 8.29) > 200
    memory.record("preview", 300, 8.29, 3.93, True)       # ran out of memory at 300 with a 3.93 GB budget
    assert memory.max_images("preview", 3.93, 8.29) < 300


def test_replan_only_unfinished_frames_but_overlaps_finished():
    from naqsha.chunking import plan
    xy = np.column_stack([np.linspace(0, 900, 300), np.zeros(300)])
    todo = np.arange(100, 300)                       # first 100 frames already done
    chunks = plan(xy, 150, 30, core_idx=todo)
    cores = sorted(i for c in chunks for i in c["core"])
    assert cores == list(range(100, 300))           # every unfinished frame owned once
    assert any(m < 100 for c in chunks for m in c["members"])   # still shares photos with done part


def test_altitude_without_absolute_altitude_stays_finite():
    from naqsha.analysis import harmonise_altitude
    from naqsha.terrain import Terrain
    a, b = _straight_track(), _straight_track()
    b.clip_id = 2
    flights, warnings = harmonise_altitude([a, b], {1: np.full(len(a.t), np.nan), 2: np.full(len(b.t), np.nan)},
                                           Terrain(None))
    assert len(flights) == 1
    assert np.isfinite(a.abs_alt).all() and np.isfinite(b.abs_alt).all()


def test_dsm_legend_is_json(tmp_path):
    import json as _json
    import rasterio
    from rasterio.transform import from_origin
    from naqsha.tiles import legend
    p = tmp_path / "dsm.tif"
    with rasterio.open(p, "w", driver="GTiff", width=50, height=50, count=1, dtype="float32",
                       crs="EPSG:32643", transform=from_origin(321000, 3729000, 0.05, 0.05), nodata=-9999) as ds:
        ds.write(np.linspace(540, 600, 2500, dtype="float32").reshape(1, 50, 50))
    lg = legend(p)
    _json.dumps(lg)                                  # must serialise (was numpy uint8)
    assert 540 <= lg["min"] < lg["max"] <= 600


# ------------------------------------------------------------- trees

def test_treetops_finds_separate_crowns_and_skips_low_bumps():
    from naqsha.trees import treetops
    res = 0.2
    yy, xx = np.mgrid[0:200, 0:200] * res
    chm = np.zeros((200, 200), np.float32)
    for cx, cy, h in [(10, 10, 12), (20, 10, 9), (30, 30, 15)]:          # three trees, metres
        chm = np.maximum(chm, h * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.5 ** 2)))
    chm = np.maximum(chm, 1.2 * np.exp(-((xx - 5) ** 2 + (yy - 30) ** 2) / 2))   # a 1.2 m shrub
    tops = treetops(chm, res, min_h=2.0)
    assert len(tops) == 3
    assert sorted(np.round(tops[:, 2]).astype(int)) == [9, 12, 15]


def test_matching_is_one_to_one_within_radius():
    from naqsha.trees import match
    pred = np.array([[0, 0], [0.5, 0], [10, 10]], float)
    truth = np.array([[0.2, 0], [9, 9]], float)
    assert match(pred, truth, 2.0) == 2          # (0,0)<->(0.2,0) and (10,10)<->(9,9); the duplicate is a false positive
    assert match(pred, truth, 1.0) == 1


def test_estimate_corrects_for_measured_precision_and_recall():
    from naqsha.trees import estimate
    sc = {"tp": 80, "fp": 20, "fn": 20, "precision": 0.8, "recall": 0.8}
    e = estimate(1000, sc)
    assert e["count"] == 1000 and e["low"] < 1000 < e["high"]
    sc = {"tp": 90, "fp": 10, "fn": 60, "precision": 0.9, "recall": 0.6}   # misses many: true count is higher
    assert estimate(1000, sc)["count"] == 1500


def test_method_choice_prefers_what_matches_the_hand_count():
    from naqsha.trees import apply_method, choose, score_plots
    # four real trees; the image model also fires on a road (low vegetation) and misses one tree
    boxes = [{"x0": x - 1, "x1": x + 1, "y0": y - 1, "y1": y + 1, "score": 0.6, "veg": 0.9, "h": 8.0, "tops": [i]}
             for i, (x, y) in enumerate([(5, 5), (15, 5), (5, 15)])]
    boxes.append({"x0": 20, "x1": 22, "y0": 20, "y1": 22, "score": 0.7, "veg": 0.05, "h": 0.1, "tops": []})
    tops = [{"x": x, "y": y, "h": 8.0, "veg": 0.9} for x, y in [(5, 5), (15, 5), (5, 15), (15, 15)]]
    cand = {"boxes": boxes, "tops": tops, "min_h": 2.0}
    plot = {"id": 1, "poly_m": [[0, 0], [25, 0], [25, 25], [0, 25]],
            "marks_m": [[5, 5], [15, 5], [5, 15], [15, 15]]}
    assert len(apply_method(cand, "image", 0.2)) == 3                  # road box rejected as non-vegetation
    assert score_plots(apply_method(cand, "image+height", 0.2), [plot])["f1"] == 1.0
    best = choose(cand, [plot])
    assert best["score"]["f1"] == 1.0 and best["method"] in ("image+height", "height")


def test_active_learning_asks_about_unsure_spots_and_spreads_them():
    from naqsha.trees import pick_regions, uncertainty_points
    sure = [{"x0": x, "x1": x + 3, "y0": 0, "y1": 3, "score": 0.9, "veg": 0.9, "h": 9.0, "tops": []} for x in range(0, 30, 5)]
    unsure = [{"x0": 200 + x, "x1": 203 + x, "y0": 0, "y1": 3, "score": 0.21, "veg": 0.9, "h": 6.0, "tops": []} for x in range(0, 30, 5)]
    far = [{"x0": 400, "x1": 403, "y0": 0, "y1": 3, "score": 0.19, "veg": 0.9, "h": 6.0, "tops": []}]
    cand = {"boxes": sure + unsure + far, "tops": [{"x": 100, "y": 1, "h": 12.0, "veg": 0.9}], "min_h": 2.0}
    xy, w = uncertainty_points(cand, 0.2)
    assert len(xy) == len(unsure) + len(far) + 1          # confident crowns carry no weight; the missed tall top does
    regions = pick_regions(xy, w, lambda x, y: True, k=3, size_m=12)
    assert regions[0]["x"] > 190 and regions[0]["x"] < 240  # densest doubt first
    import math
    assert all(math.hypot(a["x"] - b["x"], a["y"] - b["y"]) >= 25
               for i, a in enumerate(regions) for b in regions[i + 1:])  # spread out


def test_hand_count_estimate_and_crown_aware_matching():
    from naqsha.trees import design_estimate, score_plots
    plots = [{"size_m": 20, "marks_m": [[0, 0]] * n} for n in (4, 6)]
    e = design_estimate(plots, 10000)                      # 1 ha at 5 trees per 400 m2
    assert e["count"] == 125 and e["low"] < 125 < e["high"]
    big = [{"x": 0, "y": 0, "crown": 7.0}]                # a 7 m crown; the mark sits 3 m from its centre
    sq = [{"id": 1, "poly_m": [[-10, -10], [10, -10], [10, 10], [-10, 10]], "marks_m": [[3, 0]]}]
    assert score_plots(big, sq)["tp"] == 1
    assert score_plots([{"x": 0, "y": 0, "crown": 2.0}], sq)["tp"] == 0
