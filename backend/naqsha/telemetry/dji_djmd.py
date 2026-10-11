"""Decoder for DJI's embedded "djmd" protobuf telemetry track.

Field map, verified on DJI Mini 5 Pro (FC9313) footage against the GPS track:

  frame message = top-level field 3, exactly one per video frame
    3.1.2            capture timestamp, microseconds (monotonic)
    3.2.24.1         white balance, kelvin
    3.3.2.{1,2,3}    velocity north / east / down, m/s
    3.3.3.{1,2,3}    aircraft pitch / roll / yaw, decidegrees; yaw is true-north
                     heading (matches GPS course to <1 deg in forward flight)
    3.3.4.1.{2,3}    latitude / longitude (double), updated at ~10 Hz
    3.3.4.2          altitude, mm (GPS takeoff altitude + barometric height)
    3.3.5.1          barometric height above takeoff, mm
    3.4.3.{1,2}      gimbal pitch / roll, decidegrees (-900 = nadir)
    3.4.3.3          gimbal yaw, decidegrees, with an arbitrary per-flight
                     offset; NOT north-referenced, so camera heading is taken
                     from the aircraft yaw instead

Missing fields mean zero (proto3). Lat/lon of exactly 0 means no fix.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .model import Telemetry
from .protobuf import decode, get, signed64


def extract_stream(video: Path, stream_index: int) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-map", f"0:{stream_index}",
         "-c", "copy", "-f", "data", "-"],
        capture_output=True, check=True,
    )
    return proc.stdout


def _model_name(top: list, first_frame: list | None) -> str:
    # the camera name sits in the stream header (2.2.1.4) and in each frame (2.1.4)
    for msg, path in ((top, (2, 2, 1, 4)), (first_frame, (2, 1, 4))):
        if msg is None:
            continue
        cam = get(msg, *path, default=b"")
        if isinstance(cam, bytes) and cam:
            return cam.decode(errors="replace").strip()
    return "DJI unknown"


# role -> (field path inside the per-frame message, scale to SI units)
MINI5PRO = {
    "lat": ((3, 4, 1, 2), 1.0), "lon": ((3, 4, 1, 3), 1.0), "alt": ((3, 4, 2), 0.001),
    "rel_h": ((3, 5, 1), 0.001), "vn": ((3, 2, 1), 1.0), "ve": ((3, 2, 2), 1.0), "vd": ((3, 2, 3), 1.0),
    "pitch": ((3, 3, 1), 0.1), "roll": ((3, 3, 2), 0.1), "yaw": ((3, 3, 3), 0.1),
    "g_pitch": ((4, 3, 1), 0.1), "g_roll": ((4, 3, 2), 0.1), "g_yaw": ((4, 3, 3), 0.1),
    "wb": ((2, 24, 1), 1.0),
}
KNOWN_LAYOUTS = {"dji-mini5pro": MINI5PRO}


def split_frames(raw: bytes) -> tuple[list, list]:
    top = decode(raw)
    frames = [v for f, v in top if f == 3 and isinstance(v, list)]
    if not frames:
        raise ValueError("djmd stream contains no frame records")
    return top, frames


def apply_layout(frames: list, layout: dict, fps: float, camera_model: str, source: str) -> Telemetry:
    n = len(frames)
    tel = Telemetry.empty(n, source, fps, camera_model)
    d = tel.data
    for role in ("alt", "rel_h", "vn", "ve", "vd", "pitch", "roll", "yaw", "g_pitch", "g_roll", "g_yaw", "wb"):
        d[role] = np.zeros(n) if role in layout or role in ("vn", "ve", "vd", "pitch", "roll", "g_roll", "wb") \
            else np.full(n, np.nan)
    lat = np.full(n, np.nan)
    lon = np.full(n, np.nan)
    lp, ls = layout["lat"]
    op, os_ = layout["lon"]
    for i, fr in enumerate(frames):
        la, lo = get(fr, *lp), get(fr, *op)
        if isinstance(la, float) and isinstance(lo, float) and (la != 0.0 or lo != 0.0):
            lat[i], lon[i] = la * ls, lo * os_
        for role, (path, scale) in layout.items():
            if role in ("lat", "lon"):
                continue
            v = get(fr, *path)
            if isinstance(v, int):
                v = signed64(v)
            if isinstance(v, (int, float)):
                d[role][i] = float(v) * scale
    for k in ("yaw", "g_yaw"):
        if not np.isnan(d[k]).all():
            d[k] = d[k] % 360.0
    if "g_pitch" not in layout:
        d["g_pitch"][:] = -90.0
        tel.notes.append("gimbal pitch not found in telemetry; assumed straight down")
    if "yaw" not in layout:
        d["yaw"][:] = np.nan
    d["lat"], d["lon"] = lat, lon
    new = np.zeros(n)
    prev = None
    for i in range(n):
        if np.isnan(lat[i]):
            continue
        cur = (lat[i], lon[i])
        if cur != prev:
            new[i] = 1
            prev = cur
    d["gps_new"] = new
    return tel


def heading_check(tel: Telemetry) -> float | None:
    """Median |yaw - GPS course| while moving, degrees (None if it never moves)."""
    d = tel.data
    ok = ~np.isnan(d["lat"])
    if ok.sum() < 20 or np.isnan(d["yaw"]).all():
        return None
    idx = np.flatnonzero(ok)[:: max(1, int(tel.fps / 2))]
    y = (d["lat"][idx] - d["lat"][idx].mean()) * 111320
    x = (d["lon"][idx] - d["lon"][idx].mean()) * 111320 * np.cos(np.radians(d["lat"][idx].mean()))
    t = d["t"][idx]
    vn, ve = np.gradient(y, t), np.gradient(x, t)
    mv = np.hypot(vn, ve) > 2.5
    if mv.sum() < 8:
        return None
    course = np.degrees(np.arctan2(ve, vn))
    return float(np.median(np.abs(((d["yaw"][idx][mv] - course[mv]) + 180) % 360 - 180)))


def validate(tel: Telemetry) -> bool:
    d = tel.data
    lat_ok = (~np.isnan(d["lat"])).mean() > 0.5 and np.nanmax(np.abs(d["lat"])) <= 90
    h = heading_check(tel)
    return bool(lat_ok and (h is None or h < 20))


def parse(raw: bytes, fps: float, expected_frames: int | None = None,
          layout: dict | None = None) -> tuple[Telemetry, str, dict]:
    """Decode with a given layout, else the first known layout that validates,
    else a layout inferred from the data itself.

    Returns (telemetry, layout name, layout)."""
    from .schema import infer, to_layout
    top, frames = split_frames(raw)
    model = _model_name(top, frames[0])
    candidates = [("given", layout)] if layout else list(KNOWN_LAYOUTS.items())
    tel = None
    name = None
    used = None
    for nm, lay in candidates:
        t = apply_layout(frames, lay, fps, model, "dji-djmd")
        if validate(t):
            tel, name, used = t, nm, lay
            break
    if tel is None:
        inferred = to_layout(infer(frames, fps))
        if "lat" not in inferred:
            raise ValueError("could not find GPS fields in this DJI telemetry layout")
        tel = apply_layout(frames, inferred, fps, model, "dji-djmd")
        name, used = "inferred", inferred
        tel.notes.append("telemetry layout not recognised; fields were identified from the data itself")
    n = len(frames)
    if expected_frames is not None and expected_frames != n:
        tel.notes.append(
            f"telemetry has {n} records but video has {expected_frames} frames; "
            "records are mapped by index and the shorter length is used")
    valid = ~np.isnan(tel.data["lat"])
    if valid.sum() == 0:
        tel.notes.append("no GPS fix anywhere in this clip")
    elif valid.mean() < 0.95:
        tel.notes.append(f"GPS missing on {100 * (1 - valid.mean()):.1f}% of frames")
    tel.notes = [n for n in tel.notes]
    return tel, name, used


def read(video: Path, stream_index: int, fps: float, expected_frames: int | None,
         layout: dict | None = None) -> tuple[Telemetry, str, dict]:
    return parse(extract_stream(video, stream_index), fps, expected_frames, layout)
