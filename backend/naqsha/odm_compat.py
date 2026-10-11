"""Work around ODM/OpenSfM's yaw/pitch/roll -> OPK conversion.

ODM (3.6.x) and OpenSfM build the "local" frame for OPK angles from ECEF axes:
north is correct but "down" is the ECEF -Z axis, which is only local down at
the pole. The result is a heading error of about (90 deg + longitude), 163 deg
in Islamabad, on every orientation prior. Incremental SfM barely uses those
priors, but triangulation SfM depends on them and collapses (a few hundred
points instead of tens of thousands).

Fix, applied per run: compute the true OPK in OpenSfM's convention
(R_cam = F (Rx(w) Ry(p) Rz(k))^T in topocentric ENU, F = diag(1,-1,-1)),
then solve for the yaw/pitch/roll that make ODM's own conversion produce it,
and hand those to ODM through a geo.txt file. Whether the installed ODM image
has the bug is probed by running its code, so a fixed ODM is handled too.
"""
from __future__ import annotations

import json
import math
import subprocess

import numpy as np

from .geo import camera_axes

_A = 6378137.0
_E2 = 6.69437999014e-3
F = np.diag([1.0, -1.0, -1.0])


def _ecef(lat, lon, alt):
    la, lo = math.radians(lat), math.radians(lon)
    n = _A / math.sqrt(1 - _E2 * math.sin(la) ** 2)
    return np.array([(n + alt) * math.cos(la) * math.cos(lo), (n + alt) * math.cos(la) * math.sin(lo),
                     (n * (1 - _E2) + alt) * math.sin(la)])


def odm_opk(yaw, pitch, roll, lat, lon, alt, fixed: bool = False):
    """Exact replica of ODM_Photo.compute_opk / opensfm exif.extract_opk.

    fixed=True replicates the patched version (north taken in local ENU).
    """
    y, p, r = math.radians(yaw), math.radians(pitch), math.radians(roll)
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    cnb = np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                    [-sp, cp * sr, cp * cr]])
    cbb = np.array([[0, 1, 0], [1, 0, 0], [0, 0, -1]])
    if fixed:
        xnp = np.array([0.0, 1.0, 0.0])
    else:
        d = 1e-7
        xnp = _ecef(lat + d, lon, alt) - _ecef(lat - d, lon, alt)
        xnp /= np.linalg.norm(xnp)
    znp = np.array([0.0, 0.0, -1.0])
    ynp = np.cross(znp, xnp)
    cen = np.array([xnp, ynp, znp]).T
    ceb = cen @ cnb @ cbb
    return np.array([math.degrees(math.atan2(-ceb[1][2], ceb[2][2])), math.degrees(math.asin(ceb[0][2])),
                     math.degrees(math.atan2(-ceb[0][1], ceb[0][0]))])


def rotation_from_opk(o, p, k):
    o, p, k = map(math.radians, (o, p, k))
    co, so, cp, sp, ck, sk = math.cos(o), math.sin(o), math.cos(p), math.sin(p), math.cos(k), math.sin(k)
    rx = np.array([[1, 0, 0], [0, co, -so], [0, so, co]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[ck, -sk, 0], [sk, ck, 0], [0, 0, 1]])
    return F @ (rx @ ry @ rz).T


def true_opk(heading, gimbal_pitch, gimbal_roll=0.0):
    """OPK (degrees) of our camera model in OpenSfM's convention."""
    right, down, fwd = camera_axes(heading, gimbal_pitch, gimbal_roll)
    r_cam = np.array([right, down, fwd])          # world (ENU) -> OpenCV camera
    m = (F @ r_cam).T                             # = Rx(w) Ry(p) Rz(k)
    return np.array([math.degrees(math.atan2(-m[1][2], m[2][2])),
                     math.degrees(math.asin(max(-1.0, min(1.0, m[0][2])))),
                     math.degrees(math.atan2(-m[0][1], m[0][0]))])


def rotation_error_deg(ra, rb) -> float:
    c = (np.trace(ra @ rb.T) - 1) / 2
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def solve_odm_inputs(heading, gimbal_pitch, gimbal_roll, lat, lon, alt, buggy=True):
    """yaw/pitch/roll (ODM convention, pitch 0 = nadir) giving the true rotation.

    buggy=True solves against ODM's broken conversion, False against the fixed
    one (patched or upstream-fixed ODM). Returns (yaw, pitch, roll, residual_deg).
    """
    target = true_opk(heading, gimbal_pitch, gimbal_roll)
    r_target = rotation_from_opk(*target)
    fixed = not buggy

    def f_opk(x):
        return odm_opk(*x, lat, lon, alt, fixed=fixed)

    def resid(x):
        return rotation_error_deg(rotation_from_opk(*f_opk(x)), r_target)

    if fixed and abs(gimbal_roll) < 1e-6:
        # the fixed conversion is exactly DJI's convention when there is no roll
        x = np.array([heading, 90.0 + gimbal_pitch, 0.0])
        if resid(x) < 1e-4:
            return float(_wrap(heading)), 90.0 + gimbal_pitch, 0.0, resid(x)

    best = None
    base_yaw = _wrap(90.0 + lon - target[2]) if buggy else _wrap(heading)
    for dy in (0.0, 180.0):
        for dp in (0.0, -10.0, 10.0):
            x = np.array([_wrap(base_yaw + dy), 90.0 + gimbal_pitch + dp, gimbal_roll])
            for _ in range(60):
                f = _wrap(f_opk(x) - target)
                if np.abs(f).max() < 1e-7:
                    break
                J = np.zeros((3, 3))
                f0 = f_opk(x)
                for j in range(3):
                    dx = np.zeros(3); dx[j] = 1e-5
                    J[:, j] = _wrap(f_opk(x + dx) - f0) / 1e-5
                step = np.linalg.lstsq(J, f, rcond=None)[0]
                n = np.abs(step).max()
                if n > 5.0:                       # damped: no wild jumps across singularities
                    step *= 5.0 / n
                x = x - step
                x[0] = _wrap(x[0])
            r = resid(x)
            if best is None or r < best[1]:
                best = (x.copy(), r)
            if r < 1e-4:
                break
        if best[1] < 1e-4:
            break
    x, res = best
    return float(_wrap(x[0])), float(x[1]), float(x[2]), res


PROBE = """
import sys, json
sys.path.insert(0, '/code')
from opendm.photo import ODM_Photo
p = object.__new__(ODM_Photo)
for k, v in dict(yaw=-31.4, pitch=0.1, roll=0.0, latitude=33.688, longitude=73.0705, altitude=576.0,
                 omega=None, phi=None, kappa=None).items():
    setattr(p, k, v)
p.compute_opk()
print(json.dumps([p.omega, p.phi, p.kappa]))
"""
PATCH_MARK = "# naqsha: north in local ENU"


def _image_id(image: str) -> str:
    return subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", image],
                          capture_output=True, text=True).stdout.strip()


def _probe(image: str, mounts: list[str]) -> np.ndarray:
    cmd = ["docker", "run", "--rm", *mounts, "--entrypoint", "python3", image, "-c", PROBE]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    try:
        return np.array(json.loads(out.stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        raise RuntimeError(f"could not probe ODM orientation handling: {out.stderr[-500:]}")


def _make_patch(image: str, dest) -> bool:
    src = subprocess.run(["docker", "run", "--rm", "--entrypoint", "cat", image, "/code/opendm/photo.py"],
                         capture_output=True, text=True).stdout
    lines = src.split("\n")
    out, done, in_opk = [], 0, False
    for ln in lines:
        out.append(ln)
        if ln.strip().startswith("def compute_opk"):
            in_opk = True
        elif in_opk and ln.strip().startswith("def "):
            in_opk = False
        if in_opk and ln.strip() == "xnp /= m":
            indent = ln[: len(ln) - len(ln.lstrip())]
            out.append(f"{indent}xnp = np.array([0.0, 1.0, 0.0])  {PATCH_MARK}")
            done += 1
    if done != 1:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out))
    return True


def orientation_mode(image: str, cache: dict, patch_dir) -> dict:
    """Decide how to pass orientation priors to this ODM image.

    Returns {"mode": "native"|"patched"|"inverse", "mounts": [...]}; probed once
    per image id. "patched" mounts a corrected photo.py (verified by running
    it); "inverse" keeps stock ODM and solves around its bug.
    """
    iid = _image_id(image)
    key = iid.replace("sha256:", "")[:16] or "unknown"
    patch_file = patch_dir / key / "photo.py"
    if iid in cache and isinstance(cache[iid], dict):
        info = cache[iid]
        if info["mode"] != "patched" or patch_file.exists():
            return {**info, "mounts": _mounts(info["mode"], patch_file)}
    fixed_ref = odm_opk(-31.4, 0.1, 0.0, 33.688, 73.0705, 576.0, fixed=True)
    buggy_ref = odm_opk(-31.4, 0.1, 0.0, 33.688, 73.0705, 576.0, fixed=False)
    got = _probe(image, [])
    if np.abs(_wrap(got - fixed_ref)).max() < 1e-3:
        mode = "native"
    elif np.abs(_wrap(got - buggy_ref)).max() < 1e-3:
        mode = "inverse"
        if _make_patch(image, patch_file):
            got_p = _probe(image, _mounts("patched", patch_file))
            if np.abs(_wrap(got_p - fixed_ref)).max() < 1e-3:
                mode = "patched"
    else:
        raise RuntimeError(f"unrecognised ODM orientation convention (got {got.tolist()}); "
                           "refusing to pass orientation priors")
    cache[iid] = {"mode": mode}
    return {"mode": mode, "mounts": _mounts(mode, patch_file)}


def _mounts(mode: str, patch_file) -> list[str]:
    if mode != "patched":
        return []
    return ["-v", f"{patch_file.resolve()}:/code/opendm/photo.py:ro"]


def geo_file(images: list[dict], buggy: bool, max_residual: float = 0.5) -> tuple[str, dict]:
    """geo.txt contents for ODM --geo plus stats.

    Orientation that ODM cannot represent (strong tilts under the bug) is
    written as nan so ODM ignores it instead of using a wrong prior. Accuracy
    columns are passed as-is (ODM does not double geo.txt values).
    """
    lines = ["EPSG:4326"]
    reachable = 0
    for im in images:
        yaw, pitch, roll, res = solve_odm_inputs(im["heading"], im["pitch"], im.get("roll", 0.0),
                                                 im["lat"], im["lon"], im["abs_alt"], buggy)
        if res <= max_residual:
            reachable += 1
            ypr = f"{yaw:.5f} {pitch:.5f} {roll:.5f}"
        else:
            ypr = "nan nan nan"
        lines.append(f"{im['name']} {im['lon']:.9f} {im['lat']:.9f} {im['abs_alt']:.3f} {ypr} "
                     f"{im['xy_sigma']:.2f} {im.get('z_sigma', 1.0):.2f}")
    return "\n".join(lines) + "\n", {"images": len(images), "orientation_priors": reachable,
                                      "odm_opk_bug": buggy}
