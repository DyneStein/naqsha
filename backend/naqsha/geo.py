"""Projection helpers and camera-ray geometry.

All metric work happens in the local UTM zone (metres, x=east, y=north).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from pyproj import CRS, Transformer


def utm_crs(lat: float, lon: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


class LocalProjection:
    def __init__(self, lat0: float, lon0: float):
        self.crs = utm_crs(lat0, lon0)
        self._fwd = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        self._inv = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    @property
    def epsg(self) -> int:
        return int(self.crs.to_epsg())

    def to_xy(self, lat, lon):
        x, y = self._fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return x, y

    def to_ll(self, x, y):
        lon, lat = self._inv.transform(np.asarray(x, float), np.asarray(y, float))
        return lat, lon


def haversine(lat1, lon1, lat2, lon2):
    r = 6371008.8
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


@dataclass
class Intrinsics:
    width: int
    height: int
    focal_px: float
    cx: float
    cy: float

    @classmethod
    def from_f35(cls, width: int, height: int, f35: float) -> "Intrinsics":
        # 35 mm equivalent focal length is defined against the 36 mm frame
        # width, which maps to the long image side.
        f = f35 / 36.0 * max(width, height)
        return cls(width, height, f, width / 2.0, height / 2.0)

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.width / 2 / self.focal_px))

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.height / 2 / self.focal_px))


def camera_axes(heading_deg: float, pitch_deg: float, roll_deg: float = 0.0):
    """Camera right/down/forward unit vectors in ENU.

    heading: clockwise from true north. pitch: 0 = horizon, -90 = nadir.
    roll: positive rolls the image clockwise as seen by the camera.
    """
    h, p, r = math.radians(heading_deg), math.radians(pitch_deg), math.radians(roll_deg)
    fwd = np.array([math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p)])
    right = np.array([math.cos(h), -math.sin(h), 0.0])
    down = -np.cross(right, fwd)
    if r:
        c, s = math.cos(r), math.sin(r)
        right, down = c * right - s * down, s * right + c * down
    return right, down, fwd


def ground_footprint(x: float, y: float, height_above_ground: float, heading: float,
                     pitch: float, roll: float, K: Intrinsics, max_range: float,
                     samples_per_edge: int = 4) -> list[tuple[float, float]] | None:
    """Project the image border onto a flat ground plane below the camera.

    Rays that miss the ground, or hit it beyond max_range (horizontal), are
    clipped at max_range so oblique frames give a bounded, honest footprint.
    Returns polygon vertices in projected metres, or None if no ray hits.
    """
    if height_above_ground <= 0.5:
        return None
    right, down, fwd = camera_axes(heading, pitch, roll)
    w, h = K.width, K.height
    border = []
    n = samples_per_edge
    for i in range(n):
        border.append((w * i / n, 0))
    for i in range(n):
        border.append((w, h * i / n))
    for i in range(n):
        border.append((w - w * i / n, h))
    for i in range(n):
        border.append((0, h - h * i / n))
    pts = []
    any_hit = False
    for u, v in border:
        ray = fwd + (u - K.cx) / K.focal_px * right + (v - K.cy) / K.focal_px * down
        ray = ray / np.linalg.norm(ray)
        horiz = math.hypot(ray[0], ray[1])
        if ray[2] < -1e-6:
            t = height_above_ground / -ray[2]
            dx, dy = ray[0] * t, ray[1] * t
            dist = math.hypot(dx, dy)
            if dist <= max_range:
                any_hit = True
                pts.append((x + dx, y + dy))
                continue
        if horiz < 1e-9:
            continue
        pts.append((x + ray[0] / horiz * max_range, y + ray[1] / horiz * max_range))
    if not any_hit or len(pts) < 3:
        return None
    return pts
