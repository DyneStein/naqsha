"""Flight analysis: telemetry cleaning, altitude harmonisation, flight phases, stats.

Input is the raw per-frame telemetry of every clip in a project. Output is a
cleaned per-frame track (positions in projected metres plus WGS84) and a
summary that answers "where exactly did the drone fly".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .geo import LocalProjection, haversine
from .telemetry.model import Telemetry
from .terrain import Terrain

MAX_SPEED = 25.0            # m/s, above any small DJI's top speed
GPS_FUSION_WINDOW = 20.0    # s, window of GPS averaged into the absolute anchor
GPS_JUMP_M = 2.5            # m, GPS step not explained by velocity => jump event
GPS_SIGMA_FLOOR = 2.5       # m, consumer GNSS absolute accuracy floor
GPS_JUMP_FADE_S = 30.0      # s, how long a GNSS jump keeps nearby positions suspect
NADIR_PITCH = -60.0         # gimbal pitch at or below which a frame is mapping-grade
OBLIQUE_PITCH = -30.0       # below this an oblique frame can still contribute (with sky masking)


@dataclass
class CleanTrack:
    clip_id: int
    fps: float
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    x: np.ndarray
    y: np.ndarray
    rel_h: np.ndarray
    abs_alt: np.ndarray         # harmonised: ground_ref + rel_h
    hag: np.ndarray             # height above ground (DEM if available, else rel_h)
    heading: np.ndarray         # camera heading, deg clockwise from north
    g_pitch: np.ndarray
    g_roll: np.ndarray
    speed: np.ndarray
    vn: np.ndarray
    ve: np.ndarray
    vd: np.ndarray
    yaw_rate: np.ndarray        # deg/s
    wb: np.ndarray
    phase: np.ndarray           # int codes, see PHASES
    gps_ok: np.ndarray          # bool, position is backed by real fixes
    xy_sigma: np.ndarray | None = None   # per-frame horizontal position uncertainty, m
    gps_lag_s: float = 0.0
    gps_jumps: list = field(default_factory=list)
    flight_key: int = -1
    notes: list[str] = field(default_factory=list)


PHASES = {0: "ground", 1: "climb/descent", 2: "hover", 3: "transit", 4: "mapping"}


def _wrap(a):
    return (np.asarray(a) + 180.0) % 360.0 - 180.0


def _rolling(values: np.ndarray, t: np.ndarray, half_window: float, fn) -> np.ndarray:
    """Apply fn over a centred time window to irregular samples."""
    out = np.empty(len(values))
    lo = np.searchsorted(t, t - half_window, side="left")
    hi = np.searchsorted(t, t + half_window, side="right")
    for i in range(len(values)):
        out[i] = fn(values[lo[i]:hi[i]])
    return out


def dead_reckon(t: np.ndarray, vn: np.ndarray, ve: np.ndarray):
    dt = np.diff(t)
    e = np.concatenate([[0.0], np.cumsum(0.5 * (ve[1:] + ve[:-1]) * dt)])
    n = np.concatenate([[0.0], np.cumsum(0.5 * (vn[1:] + vn[:-1]) * dt)])
    return e, n


def estimate_gps_lag(ts, xs, ys, t, vn, ve) -> float:
    """Latency of GPS fixes relative to the fused velocity, by grid search.

    Applied only when it clearly beats zero lag, to avoid fitting noise.
    """
    if len(ts) < 20:
        return 0.0
    tm = 0.5 * (ts[1:] + ts[:-1])
    dt = np.diff(ts)
    gv_e = np.diff(xs) / np.maximum(dt, 1e-3)
    gv_n = np.diff(ys) / np.maximum(dt, 1e-3)
    moving = (np.hypot(gv_e, gv_n) > 2.0) & (np.hypot(gv_e, gv_n) < MAX_SPEED)
    if moving.sum() < 20:
        return 0.0

    def err(lag):
        pe = np.interp(tm[moving] - lag, t, ve)
        pn = np.interp(tm[moving] - lag, t, vn)
        return float(np.median(np.hypot(pe - gv_e[moving], pn - gv_n[moving])))

    lags = np.arange(-0.5, 0.51, 0.02)
    errs = [err(l) for l in lags]
    best = float(lags[int(np.argmin(errs))])
    if min(errs) > 0.9 * err(0.0) or abs(best) >= 0.49:
        return 0.0
    return round(best, 2)


def fuse_position(t, fix_t, fix_x, fix_y, vn, ve, window_s: float = GPS_FUSION_WINDOW):
    """Velocity dead-reckoning anchored to GPS with a robust long-window offset.

    DJI's reported position contains persistent step jumps (GNSS
    re-convergence, observed up to ~20 m) while its velocity is locally precise
    to centimetres. Position = dead-reckoned path + rolling-median(GPS - DR),
    so local geometry comes from velocity and the absolute anchor from many
    seconds of GPS. Returns x, y, per-fix residuals and jump events.
    """
    dre, drn = dead_reckon(t, vn, ve)
    ex = fix_x - np.interp(fix_t, t, dre)
    ny = fix_y - np.interp(fix_t, t, drn)
    off_x = _rolling(ex, fix_t, window_s / 2, np.median)
    off_y = _rolling(ny, fix_t, window_s / 2, np.median)
    # spread median switches (GNSS jumps) over ~10 s instead of a hard step
    off_x = _rolling(off_x, fix_t, 5.0, np.mean)
    off_y = _rolling(off_y, fix_t, 5.0, np.mean)
    x = dre + np.interp(t, fix_t, off_x)
    y = drn + np.interp(t, fix_t, off_y)
    resid = np.hypot(fix_x - np.interp(fix_t, t, x), fix_y - np.interp(fix_t, t, y))
    step = np.hypot(np.diff(ex), np.diff(ny))
    # GPS that disagrees with velocity by more than its own noise floor is a jump
    noise = float(np.median(step)) + 1.4826 * float(np.median(np.abs(step - np.median(step))))
    thr = max(GPS_JUMP_M, 6.0 * noise)
    jumps = [(float(fix_t[i + 1]), float(step[i])) for i in np.flatnonzero(step > thr)]
    return x, y, resid, jumps


def clean_clip(clip_id: int, tel: Telemetry, proj: LocalProjection) -> CleanTrack:
    d = tel.data
    n = len(tel)
    t = d["t"].astype(float)
    notes = list(tel.notes)
    lat, lon = d["lat"], d["lon"]
    have = ~np.isnan(lat)
    fix_idx = np.flatnonzero((d["gps_new"] > 0) & have)

    x = np.full(n, np.nan); y = np.full(n, np.nan)
    gps_ok = np.zeros(n, bool)
    vn = np.nan_to_num(d["vn"]); ve = np.nan_to_num(d["ve"]); vd = np.nan_to_num(d["vd"])
    lag = 0.0
    sigma = np.full(n, np.nan)
    jumps: list = []
    have_vel = np.abs(vn).sum() + np.abs(ve).sum() > 0
    if len(fix_idx) >= 2:
        fx, fy = proj.to_xy(lat[fix_idx], lon[fix_idx])
        fx, fy = np.asarray(fx, float), np.asarray(fy, float)
        ft = t[fix_idx]
        if have_vel:
            lag = estimate_gps_lag(ft, fx, fy, t, vn, ve)
            ft = ft - lag
            x, y, resid, jumps = fuse_position(t, ft, fx, fy, vn, ve)
            # local disagreement between GPS and the fused path sets the sigma
            p75 = _rolling(resid, ft, 10.0, lambda a: np.percentile(a, 75))
            sigma = np.maximum(GPS_SIGMA_FLOOR, 1.5 * np.interp(t, ft, p75))
            # after a jump nobody knows which side was right: widen the prior
            # on both sides, proportional to the jump, fading over 30 s
            for tj, mag in jumps:
                sigma = np.maximum(sigma, 0.5 * mag * np.clip(1 - np.abs(t - tj) / GPS_JUMP_FADE_S, 0, 1))
            if jumps:
                notes.append(f"{len(jumps)} GPS jump{'s' if len(jumps) > 1 else ''} (max {max(j[1] for j in jumps):.1f} m) "
                             "smoothed using velocity; nearby frames get wider position uncertainty")
        else:
            x = np.interp(t, ft, fx)
            y = np.interp(t, ft, fy)
            sigma = np.full(n, 2 * GPS_SIGMA_FLOOR)
            notes.append("no velocity in telemetry; GPS interpolated without fusion")
        gps_ok = (t >= ft[0] - 0.5) & (t <= ft[-1] + 0.5)
        gap = np.interp(t, ft[:-1], np.diff(ft), left=99, right=99)
        gps_ok &= gap < 2.0
        if not have_vel:
            vn = np.gradient(y, t); ve = np.gradient(x, t)
    elif len(fix_idx) == 1:
        x[:], y[:] = proj.to_xy(lat[fix_idx[0]], lon[fix_idx[0]])
        notes.append("only one GPS fix in clip")
    else:
        notes.append("no GPS; clip cannot be georeferenced")
    la, lo = proj.to_ll(x, y)
    la = np.where(np.isnan(x), np.nan, la); lo = np.where(np.isnan(x), np.nan, lo)

    heading = d["yaw"].copy()
    if np.isnan(heading).all():
        course = np.degrees(np.arctan2(ve, vn)) % 360
        heading = course
        notes.append("heading taken from GPS course (no attitude in telemetry)")
    heading = np.nan_to_num(heading) % 360
    unwrapped = np.degrees(np.unwrap(np.radians(heading)))
    yaw_rate = np.abs(np.gradient(unwrapped, t)) if n > 1 else np.zeros(n)

    speed = np.hypot(vn, ve)
    rel_h = np.nan_to_num(d["rel_h"])
    return CleanTrack(
        clip_id=clip_id, fps=tel.fps, t=t, lat=la, lon=lo, x=x, y=y, rel_h=rel_h,
        abs_alt=np.full(n, np.nan), hag=rel_h.copy(), heading=heading,
        g_pitch=np.nan_to_num(d["g_pitch"], nan=-90.0), g_roll=np.nan_to_num(d["g_roll"]),
        speed=speed, vn=vn, ve=ve, vd=vd, yaw_rate=yaw_rate, wb=np.nan_to_num(d["wb"]),
        phase=np.zeros(n, int), gps_ok=gps_ok, xy_sigma=sigma, gps_lag_s=lag, gps_jumps=jumps,
        notes=notes,
    )


def _phases(tr: CleanTrack) -> np.ndarray:
    ph = np.full(len(tr.t), 3)
    ph[(tr.speed < 0.6) & (np.abs(tr.vd) < 0.4)] = 2
    ph[(np.abs(tr.vd) > 0.8) & (tr.speed < 1.5)] = 1
    ph[(tr.rel_h < 1.5) & (tr.speed < 0.6)] = 0
    ph[(ph == 3) & (tr.g_pitch <= NADIR_PITCH)] = 4
    return ph


def harmonise_altitude(tracks: list[CleanTrack], raw_alt: dict[int, np.ndarray],
                       terrain: Terrain) -> tuple[list[dict], list[str]]:
    """Rebuild absolute altitude as ground_reference + barometric height.

    The aircraft's absolute altitude is GPS-referenced at each takeoff and can
    differ by tens of metres between batteries from the same spot. Barometric
    height above takeoff is consistent to decimetres. Each flight (one battery)
    is identified by its constant (alt - rel_h) offset; its ground reference is
    the DEM elevation at its takeoff point, or a shared value if no DEM.
    """
    warnings: list[str] = []
    # group clips into flights by the GPS-at-takeoff offset and time adjacency
    groups: list[dict] = []
    for tr in tracks:
        off = raw_alt[tr.clip_id] - tr.rel_h
        off = off[~np.isnan(off)]
        o = float(np.median(off)) if len(off) else math.nan
        for g in groups:
            # no absolute altitude in this layout: all such clips share one group
            same_unknown = math.isnan(o) and math.isnan(g["offset"])
            if same_unknown or (not math.isnan(o) and abs(g["offset"] - o) < 2.0):
                g["clips"].append(tr)
                break
        else:
            groups.append({"offset": o, "clips": [tr]})

    homes = []
    for gi, g in enumerate(groups):
        best = None
        for tr in g["clips"]:
            ok = ~np.isnan(tr.x)
            if not ok.any():
                continue
            i = np.flatnonzero(ok)[np.argmin(tr.rel_h[ok])]
            if best is None or tr.rel_h[i] < best[0]:
                best = (tr.rel_h[i], tr.lat[i], tr.lon[i], tr.x[i], tr.y[i])
        g["home"] = None
        if best and best[0] < 3.0:
            g["home"] = {"lat": float(best[1]), "lon": float(best[2]), "x": float(best[3]),
                         "y": float(best[4]), "observed": True}
            homes.append(g["home"])
    for g in groups:
        if g["home"] is None:
            if homes:
                # nearest observed takeoff to this flight's first position
                tr = g["clips"][0]
                ok = np.flatnonzero(~np.isnan(tr.x))
                if len(ok):
                    x0, y0 = tr.x[ok[0]], tr.y[ok[0]]
                    h = min(homes, key=lambda h: (h["x"] - x0) ** 2 + (h["y"] - y0) ** 2)
                else:
                    h = homes[0]
                g["home"] = dict(h, observed=False)
                warnings.append(
                    f"takeoff of flight {groups.index(g) + 1} not in footage; assumed same launch "
                    f"site as an observed takeoff")
            else:
                # no takeoff anywhere: assume it was near the lowest point flown
                best = None
                for tr in g["clips"]:
                    ok = np.flatnonzero(~np.isnan(tr.x))
                    if len(ok):
                        i = ok[np.argmin(tr.rel_h[ok])]
                        if best is None or tr.rel_h[i] < best[0]:
                            best = (tr.rel_h[i], tr.lat[i], tr.lon[i], tr.x[i], tr.y[i])
                if best:
                    g["home"] = {"lat": float(best[1]), "lon": float(best[2]), "x": float(best[3]),
                                 "y": float(best[4]), "observed": False}
                warnings.append(f"takeoff of flight {groups.index(g) + 1} not in footage and no other "
                                "takeoff known; assumed near its lowest recorded point")

    known = [g["offset"] for g in groups if not math.isnan(g["offset"])]
    # without any absolute altitude, heights stay relative to takeoff (ground = 0)
    shared_ref = float(np.median(known)) if known else 0.0
    for gi, g in enumerate(groups):
        ref = math.nan
        if g["home"] and terrain.available:
            ref = float(terrain.sample(g["home"]["lat"], g["home"]["lon"])[0])
        if math.isnan(ref):
            ref = shared_ref
        g["ground_ref"] = ref
        for tr in g["clips"]:
            tr.flight_key = gi
            tr.abs_alt = ref + tr.rel_h
            if terrain.available:
                ground = terrain.sample(tr.lat, tr.lon)
                hag = tr.abs_alt - ground
                tr.hag = np.where(np.isnan(hag), tr.rel_h, hag)
            else:
                tr.hag = tr.rel_h.copy()
            tr.phase = _phases(tr)
    spread = (max(known) - min(known)) if len(known) > 1 else 0
    if spread > 5:
        warnings.append(
            f"drone-reported absolute altitude differs by {spread:.0f} m between flights "
            f"(GPS vertical error); rebuilt from barometric height + "
            f"{'DEM' if terrain.available else 'shared'} ground reference")
    flights = [{"flight": i + 1, "clips": [tr.clip_id for tr in g["clips"]],
                "gps_takeoff_alt": round(g["offset"], 1), "ground_ref": round(g["ground_ref"], 1),
                "home": g["home"]} for i, g in enumerate(groups)]
    return flights, warnings


def path_length(x, y, step: int = 30) -> float:
    ok = ~np.isnan(x)
    xs, ys = x[ok][::step], y[ok][::step]
    if len(xs) < 2:
        return 0.0
    return float(np.hypot(np.diff(xs), np.diff(ys)).sum())


def clip_stats(tr: CleanTrack, home: dict | None) -> dict:
    ok = ~np.isnan(tr.x)
    dur = float(tr.t[-1] - tr.t[0]) if len(tr.t) > 1 else 0.0
    st = {
        "duration_s": round(dur, 1),
        "path_m": round(path_length(tr.x, tr.y), 1),
        "gps_coverage": round(float(tr.gps_ok.mean()), 3),
        "gps_lag_s": tr.gps_lag_s,
        "gps_jumps": len(tr.gps_jumps),
        "gps_max_jump_m": round(max((j[1] for j in tr.gps_jumps), default=0.0), 1),
        "xy_sigma_median_m": round(float(np.nanmedian(tr.xy_sigma)), 1) if tr.xy_sigma is not None
        and not np.isnan(tr.xy_sigma).all() else None,
        "max_speed": round(float(np.percentile(tr.speed, 99)), 1) if len(tr.speed) else 0,
        "rel_h_min": round(float(tr.rel_h.min()), 1),
        "rel_h_max": round(float(tr.rel_h.max()), 1),
        "hag_median": round(float(np.median(tr.hag)), 1),
        "gimbal_pitch_median": round(float(np.median(tr.g_pitch)), 1),
        "nadir_fraction": round(float((tr.g_pitch <= NADIR_PITCH).mean()), 3),
        "oblique_fraction": round(float(((tr.g_pitch > NADIR_PITCH) & (tr.g_pitch <= OBLIQUE_PITCH)).mean()), 3),
        "phase_seconds": {PHASES[k]: round(float((tr.phase == k).sum() / tr.fps), 1) for k in PHASES},
        "wb_range": [int(tr.wb.min()), int(tr.wb.max())] if tr.wb.any() else None,
        "flight": tr.flight_key + 1,
        "notes": tr.notes,
    }
    if home and ok.any():
        st["max_dist_from_home_m"] = round(float(np.max(np.hypot(tr.x[ok] - home["x"], tr.y[ok] - home["y"]))), 1)
    return st


def minimum_enclosing_circle(xs, ys):
    from shapely import MultiPoint, minimum_bounding_circle
    pts = MultiPoint(np.column_stack([xs, ys]))
    circ = minimum_bounding_circle(pts)
    c = circ.centroid
    r = max(math.hypot(px - c.x, py - c.y) for px, py in zip(xs, ys)) if len(xs) else 0
    return c.x, c.y, r


__all__ = ["CleanTrack", "clean_clip", "harmonise_altitude", "clip_stats", "path_length",
           "minimum_enclosing_circle", "haversine", "PHASES", "NADIR_PITCH", "OBLIQUE_PITCH"]
