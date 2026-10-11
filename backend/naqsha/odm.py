"""OpenDroneMap runner: option presets, docker invocation, progress parsing."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .config import ODM_GPU_IMAGE, ODM_IMAGE

# Relative cost of each ODM stage, used to turn stage changes into a % bar.
STAGES = [
    ("dataset", 2), ("split", 1), ("merge", 1), ("opensfm", 40), ("openmvs", 30),
    ("odm_filterpoints", 2), ("odm_meshing", 6), ("mvs_texturing", 6),
    ("odm_georeferencing", 3), ("odm_dem", 4), ("odm_orthophoto", 4),
    ("odm_report", 1), ("odm_postprocess", 1),
]
OPENSFM_STEPS = ["extract_metadata", "detect_features", "match_features", "create_tracks",
                 "reconstruct", "bundle", "export_geocoords", "undistort", "export_visualsfm"]

PRESETS: dict[str, dict] = {
    # Quick look: minutes, sparse model only. Good for checking coverage and alignment.
    "preview": {
        "feature-quality": "medium", "min-num-features": 8000, "pc-quality": "low",
        "fast-orthophoto": True, "orthophoto-resolution": 5, "skip-3dmodel": True,
    },
    # Default. Dense reconstruction, DSM and orthophoto near native resolution.
    "standard": {
        "feature-quality": "high", "min-num-features": 12000, "pc-quality": "medium",
        "dsm": True, "dtm": True, "orthophoto-resolution": 2.5, "dem-resolution": 5, "skip-3dmodel": True,
        "mesh-octree-depth": 12,
    },
    # GPU box: everything turned up. Expect hours and 32+ GB RAM for ~1000 images.
    "max": {
        "feature-quality": "ultra", "min-num-features": 16000, "pc-quality": "high",
        "dsm": True, "dtm": True, "orthophoto-resolution": 1.5, "dem-resolution": 3,
        "mesh-octree-depth": 13, "mesh-size": 400000, "skip-3dmodel": False,
    },
}

COMMON = {
    "feature-type": "dspsift",
    "matcher-type": "flann",
    # video frames are dense along the path: match more GPS neighbours than a
    # photo grid needs, so strips connect across turns and parallel passes
    "matcher-neighbors": 24,
    "camera-lens": "brown",
    "auto-boundary": True,
    "cog": True,
    "build-overviews": True,
    "pc-filter": 2.5,
    "rolling-shutter": True,
}


_GPU = None


def gpu_available() -> bool:
    """True only if an NVIDIA GPU exists AND Docker can hand it to containers
    (nvidia-container-toolkit); a driver without the toolkit would fail every run."""
    global _GPU
    if _GPU is not None:
        return _GPU
    _GPU = False
    if shutil.which("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True)
        if r.returncode == 0 and "GPU" in r.stdout:
            info = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"],
                                  capture_output=True, text=True).stdout
            cdi = subprocess.run(["docker", "info", "--format", "{{json .CDISpecDirs}}"],
                                 capture_output=True, text=True).stdout
            _GPU = "nvidia" in info or "nvidia" in cdi.lower() or "cdi" in cdi.lower()
    return _GPU


def docker_available() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "docker not installed"
    r = subprocess.run(["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True)
    if r.returncode != 0:
        return False, "docker daemon not running (start Docker Desktop)"
    try:
        info = json.loads(r.stdout)
        return True, f"{info.get('NCPU')} CPUs, {round(info.get('MemTotal', 0) / 2**30, 1)} GB for containers"
    except ValueError:
        return True, "running"


def docker_mem_gb() -> float:
    r = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True)
    try:
        return int(r.stdout.strip()) / 2**30
    except ValueError:
        return 0.0


def image_present(image: str) -> bool:
    r = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    return r.returncode == 0


def build_options(preset: str, *, has_ypr: bool, include_oblique: bool,
                  fps: float, gps_sigma: float, cameras: dict | None, overrides: dict | None) -> dict:
    opts = dict(COMMON)
    opts.update(PRESETS[preset])
    # orientation priors from the gimbal/heading make triangulation-based SfM
    # both faster and much harder to fool with repetitive texture
    opts["sfm-algorithm"] = "triangulation" if has_ypr else "incremental"
    # rolling-shutter readout must fit inside one frame period at this fps
    opts["rolling-shutter-readout"] = max(5, int(0.8 * 1000.0 / max(fps, 1)))
    opts["gps-accuracy"] = round(max(1.0, gps_sigma), 1)
    if include_oblique:
        opts["sky-removal"] = True
    if cameras:
        opts["cameras"] = cameras
    opts["max-concurrency"] = os.cpu_count() or 4
    for k, v in (overrides or {}).items():
        if v is None:
            opts.pop(k, None)
        else:
            opts[k] = v
    return opts


def to_args(opts: dict, container_dir: str) -> list[str]:
    args: list[str] = []
    for k, v in opts.items():
        if v is False:
            continue
        if k == "cameras" and isinstance(v, dict):
            args += ["--cameras", f"{container_dir}/cameras_prior.json"]
            continue
        if v is True:
            args.append(f"--{k}")
        else:
            args += [f"--{k}", str(v)]
    return args


class OdmRun:
    """One ODM project run: <mount_dir>/<project>/images -> products in the same folder."""

    def __init__(self, mount_dir: Path, project: str, opts: dict, use_gpu: bool,
                 mounts: list[str] | None = None, name: str | None = None, log_path: Path | None = None):
        self.mount_dir = mount_dir
        self.project = project
        self.opts = opts
        self.use_gpu = use_gpu
        self.mounts = mounts or []
        self.name = name or f"naqsha-{project}"
        self.log_path = log_path or (mount_dir / project / "odm.log")
        self.proc: subprocess.Popen | None = None
        self.monitor = None

    @property
    def image(self) -> str:
        return ODM_GPU_IMAGE if self.use_gpu else ODM_IMAGE

    def command(self) -> list[str]:
        cmd = ["docker", "run", "--rm", "--name", self.name, "-v", f"{self.mount_dir.resolve()}:/datasets",
               *self.mounts]
        if self.use_gpu:
            cmd += ["--gpus", "all"]
        cmd += [self.image, "--project-path", "/datasets", self.project]
        return cmd + to_args(self.opts, f"/datasets/{self.project}")

    def run(self, on_progress: Callable[[float, str], None]) -> int:
        from .memory import PeakMonitor
        pdir = self.mount_dir / self.project
        if isinstance(self.opts.get("cameras"), dict):
            (pdir / "cameras_prior.json").write_text(json.dumps(self.opts["cameras"]))
        stage_re = re.compile(r"Running (\w+) stage")
        sfm_re = re.compile(r'bin/opensfm"?\s+(\w+)')
        weights = dict(STAGES)
        total = sum(weights.values())
        done_before, acc = {}, 0
        for nm, w in STAGES:
            done_before[nm] = acc
            acc += w
        current = "dataset"
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        with open(self.log_path, "a") as log:
            log.write(f"\n===== {self.project} =====\n$ " + " ".join(self.command()) + "\n")
            log.flush()
            self.proc = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, bufsize=1)
            self.monitor = PeakMonitor(self.name).start()
            assert self.proc.stdout is not None
            try:
                for line in self.proc.stdout:
                    log.write(line)
                    log.flush()
                    m = stage_re.search(line)
                    if m and m.group(1) in weights:
                        current = m.group(1)
                        self.monitor.stage = current
                        on_progress(done_before[current] / total, current)
                        continue
                    if current in ("opensfm", "split"):
                        m = sfm_re.search(line)
                        if m and m.group(1) in OPENSFM_STEPS:
                            k = OPENSFM_STEPS.index(m.group(1))
                            frac = (done_before["opensfm"] + weights["opensfm"] * k / len(OPENSFM_STEPS)) / total
                            on_progress(frac, f"opensfm {m.group(1)}")
                return self.proc.wait()
            finally:
                self.monitor.stop()

    def kill(self) -> None:
        subprocess.run(["docker", "kill", self.name], capture_output=True)


def run_in_container(image: str, mount_dir: Path, script: Path, args: list[str], mounts: list[str] | None = None,
                     name: str = "naqsha-merge") -> subprocess.CompletedProcess:
    """Run one of our python scripts inside the ODM image (GDAL, rasterio, ODM libs)."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    cmd = ["docker", "run", "--rm", "--name", name, "-v", f"{mount_dir.resolve()}:/datasets",
           "-v", f"{script.resolve()}:/naqsha_script.py:ro", *(mounts or []),
           "--entrypoint", "python3", image, "/naqsha_script.py", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def is_oom(log_text: str, returncode: int) -> bool:
    t = log_text[-20000:]
    return returncode == 137 or "ran out of memory" in t or "MemoryError" in t or "std::bad_alloc" in t \
        or "Child returned 137" in t


def diagnose_failure(log_text: str, returncode: int) -> str:
    t = log_text[-20000:]
    if is_oom(t, returncode):
        return ("ODM ran out of memory even after splitting the job down to the smallest chunk size. "
                "Give Docker more memory (Docker Desktop > Settings > Resources) or use a lower quality.")
    if "create_tracks" in t and "IndexError" in t and "splitmerge" in t:
        return ("ODM's split-merge mode crashed while building tracks (an ODM bug). Rerun with "
                "split disabled (override split=null) or on a machine with more memory.")
    if "No space left on device" in t:
        return "Disk full during processing. Free space or move the data folder to a bigger disk."
    if "Not enough supported images" in t or "not enough images" in t.lower():
        return "Too few usable images reached ODM."
    if "The program could not process this dataset" in t or "Reconstruction could not be generated" in t:
        return ("Structure-from-motion failed to connect the images. Try higher overlap, more "
                "features, or include oblique frames.")
    return f"ODM exited with code {returncode}; see the log for details."


def remove_tree(path: Path) -> None:
    """Delete a directory that may contain root-owned files written by the container."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        return
    except PermissionError:
        pass
    image = ODM_GPU_IMAGE if gpu_available() and image_present(ODM_GPU_IMAGE) else ODM_IMAGE
    subprocess.run(["docker", "run", "--rm", "--entrypoint", "rm", "-v", f"{path.parent.resolve()}:/x",
                    image, "-rf", f"/x/{path.name}"], capture_output=True)


def docker_crashed(log_text: str, returncode: int) -> bool:
    """The Docker engine itself died (not ODM): usually host memory pressure."""
    t = log_text[-5000:]
    return returncode == 125 or "failed to connect to the docker API" in t or \
        "error waiting for container" in t or "Cannot connect to the Docker daemon" in t


def ensure_docker(timeout_s: int = 240) -> bool:
    """Make sure the Docker engine is up; on macOS restart Docker Desktop if needed."""
    import platform
    import time as _t
    if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        return True
    if platform.system() == "Darwin":
        subprocess.run(["open", "-a", "Docker"], capture_output=True)
    deadline = _t.time() + timeout_s
    while _t.time() < deadline:
        if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
            return True
        _t.sleep(4)
    return False
