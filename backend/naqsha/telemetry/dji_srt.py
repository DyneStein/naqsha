"""Decoder for DJI .SRT telemetry sidecars (older DJI models).

DJI writes one subtitle block per frame (or per few frames). Field names vary by
model, so each value is matched by a tolerant regex.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .model import Telemetry

_BLOCK = re.compile(r"\n\s*\n")
_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->")
_NUM = r"(-?\d+(?:\.\d+)?)"
_PATTERNS = {
    "lat": [rf"\[latitude\s*:\s*{_NUM}\]", rf"GPS\s*\(\s*{_NUM}\s*,\s*{_NUM}"],
    "lon": [rf"\[long?itude\s*:\s*{_NUM}\]"],
    "rel_h": [rf"rel_alt\s*:\s*{_NUM}", rf"\bH\s*{_NUM}\s*m"],
    "alt": [rf"abs_alt\s*:\s*{_NUM}", rf"\[altitude\s*:\s*{_NUM}\]"],
    "g_pitch": [rf"gb_pitch\s*:\s*{_NUM}"],
    "g_yaw": [rf"gb_yaw\s*:\s*{_NUM}"],
    "yaw": [rf"\[yaw\s*:\s*{_NUM}", rf"drone_yaw\s*:\s*{_NUM}"],
    "wb": [rf"\[ct\s*:\s*{_NUM}\]"],
}


def _find(text: str, key: str):
    for pat in _PATTERNS[key]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def find_sidecar(video: Path) -> Path | None:
    for ext in (".SRT", ".srt"):
        p = video.with_suffix(ext)
        if p.exists():
            return p
    return None


def read(srt: Path, fps: float, n_frames: int, camera_model: str = "DJI unknown") -> Telemetry:
    text = srt.read_text(errors="replace").replace("\r\n", "\n")
    samples = []
    for block in _BLOCK.split(text):
        m = _TIME.search(block)
        if not m:
            continue
        h, mi, s, ms = (int(x) for x in m.groups())
        t = h * 3600 + mi * 60 + s + ms / (1000 if len(m.group(4)) == 3 else 10 ** len(m.group(4)))
        row = {k: _find(block, k) for k in _PATTERNS}
        # Mavic-style "GPS(lon, lat, alt)" puts lon first
        g = re.search(rf"GPS\s*\(\s*{_NUM}\s*,\s*{_NUM}", block)
        if g and row["lon"] is None:
            row["lon"], row["lat"] = float(g.group(1)), float(g.group(2))
        samples.append((t, row))
    if not samples:
        raise ValueError(f"no telemetry blocks parsed from {srt.name}")

    tel = Telemetry.empty(n_frames, "dji-srt", fps, camera_model)
    d = tel.data
    st = np.array([s[0] for s in samples])
    for key in ("lat", "lon", "rel_h", "alt", "g_pitch", "g_yaw", "yaw", "wb"):
        vals = np.array([np.nan if s[1][key] is None else s[1][key] for s in samples])
        ok = ~np.isnan(vals)
        if ok.sum() == 0:
            continue
        # sample-and-hold, same as djmd, so the shared cleaning code applies
        idx = np.clip(np.searchsorted(st[ok], d["t"], side="right") - 1, 0, ok.sum() - 1)
        d[key] = vals[ok][idx]
    d["lat"][(d["lat"] == 0) & (d["lon"] == 0)] = np.nan
    if np.isnan(d["g_pitch"]).all():
        d["g_pitch"][:] = -90.0
        tel.notes.append("SRT has no gimbal pitch; assumed nadir (-90)")
    if np.isnan(d["yaw"]).all():
        tel.notes.append("SRT has no heading; heading will be derived from GPS course")
    first = np.r_[True, np.diff(np.nan_to_num(d["lat"])) != 0]
    d["gps_new"] = first.astype(float)
    for k in ("vn", "ve", "vd", "pitch", "roll", "g_roll"):
        d[k] = np.nan_to_num(d[k])
    return tel
