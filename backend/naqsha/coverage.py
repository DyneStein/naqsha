"""Ground coverage: per-frame footprints, unions, and map exports."""
from __future__ import annotations

import json
import math
from xml.sax.saxutils import escape

import numpy as np
from shapely import Polygon, make_valid, unary_union
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform

from .analysis import NADIR_PITCH, OBLIQUE_PITCH, CleanTrack
from .geo import Intrinsics, LocalProjection, ground_footprint


def footprints(tr: CleanTrack, K: Intrinsics, every_s: float = 0.25, min_hag: float = 3.0,
               max_range_factor: float = 5.0, max_range_cap: float = 150.0, frames=None):
    """Footprint polygons for frames sampled every `every_s` seconds (or `frames`).

    Yields (frame_index, kind, Polygon) where kind is nadir|oblique|high-oblique.
    """
    step = max(1, int(round(every_s * tr.fps)))
    idx = frames if frames is not None else range(0, len(tr.t), step)
    for i in idx:
        if np.isnan(tr.x[i]) or tr.hag[i] < min_hag:
            continue
        p = tr.g_pitch[i]
        rng = min(max_range_cap, max_range_factor * tr.hag[i])
        pts = ground_footprint(tr.x[i], tr.y[i], tr.hag[i], tr.heading[i], p, tr.g_roll[i], K, rng)
        if pts is None:
            continue
        poly = make_valid(Polygon(pts))
        if poly.is_empty or poly.area < 1:
            continue
        kind = "nadir" if p <= NADIR_PITCH else ("oblique" if p <= OBLIQUE_PITCH else "high-oblique")
        yield i, kind, poly


def coverage(tracks: list[CleanTrack], intr: dict[int, Intrinsics]):
    """Union footprints per kind. Returns dict kind -> geometry (projected)."""
    by_kind: dict[str, list] = {"nadir": [], "oblique": [], "high-oblique": []}
    for tr in tracks:
        for _, kind, poly in footprints(tr, intr[tr.clip_id]):
            by_kind[kind].append(poly)
    out = {}
    for k, polys in by_kind.items():
        out[k] = unary_union(polys).buffer(0) if polys else None
    usable = [g for k, g in out.items() if g is not None and k != "high-oblique"]
    out["mappable"] = unary_union(usable) if usable else None
    alls = [g for g in out.values() if g is not None]
    out["all"] = unary_union(alls) if alls else None
    return out


def to_wgs84(geom, proj: LocalProjection):
    def f(x, y, z=None):
        la, lo = proj.to_ll(x, y)
        return lo, la
    return shp_transform(f, geom)


def feature(geom, props: dict) -> dict:
    return {"type": "Feature", "geometry": mapping(geom), "properties": props}


def track_geojson(tracks: list[CleanTrack], names: dict[int, str], step_s: float = 0.5) -> dict:
    feats = []
    for tr in tracks:
        step = max(1, int(round(step_s * tr.fps)))
        idx = [i for i in range(0, len(tr.t), step) if not np.isnan(tr.lat[i])]
        if len(idx) < 2:
            continue
        coords = [[round(float(tr.lon[i]), 7), round(float(tr.lat[i]), 7), round(float(tr.abs_alt[i]), 1)]
                  for i in idx]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"clip_id": tr.clip_id, "name": names.get(tr.clip_id, str(tr.clip_id)),
                           "flight": tr.flight_key + 1,
                           "t": [round(float(tr.t[i]), 2) for i in idx],
                           "pitch": [round(float(tr.g_pitch[i]), 1) for i in idx],
                           "hag": [round(float(tr.hag[i]), 1) for i in idx]},
        })
    return {"type": "FeatureCollection", "features": feats}


def kml(track_fc: dict, coverage_wgs: dict, homes: list[dict], title: str) -> str:
    def coords(seq):
        return " ".join(f"{c[0]},{c[1]},{c[2] if len(c) > 2 else 0}" for c in seq)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        f"<name>{escape(title)}</name>",
        '<Style id="trk"><LineStyle><color>ff00a5ff</color><width>3</width></LineStyle></Style>',
        '<Style id="cov"><LineStyle><color>ff00ff00</color><width>1</width></LineStyle>'
        '<PolyStyle><color>4400ff00</color></PolyStyle></Style>',
    ]
    for f in track_fc["features"]:
        parts.append(f"<Placemark><name>{escape(f['properties']['name'])}</name><styleUrl>#trk</styleUrl>"
                     f"<LineString><altitudeMode>absolute</altitudeMode><coordinates>"
                     f"{coords(f['geometry']['coordinates'])}</coordinates></LineString></Placemark>")
    for kind in ("mappable",):
        g = coverage_wgs.get(kind)
        if g is None:
            continue
        polys = list(g.geoms) if hasattr(g, "geoms") else [g]
        for p in polys:
            if p.geom_type != "Polygon":
                continue
            inner = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{coords(r.coords)}</coordinates>"
                            f"</LinearRing></innerBoundaryIs>" for r in p.interiors)
            parts.append(f"<Placemark><name>coverage ({kind})</name><styleUrl>#cov</styleUrl><Polygon>"
                         f"<outerBoundaryIs><LinearRing><coordinates>{coords(p.exterior.coords)}</coordinates>"
                         f"</LinearRing></outerBoundaryIs>{inner}</Polygon></Placemark>")
    for i, h in enumerate(homes):
        parts.append(f"<Placemark><name>Takeoff {i + 1}</name><Point><coordinates>{h['lon']},{h['lat']},0"
                     f"</coordinates></Point></Placemark>")
    parts.append("</Document></kml>")
    return "\n".join(parts)


def area_m2(geom) -> float:
    return float(geom.area) if geom is not None else 0.0


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False, default=lambda o: None
                      if isinstance(o, float) and math.isnan(o) else o)
