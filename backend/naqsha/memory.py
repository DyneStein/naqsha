"""Memory budgeting so any 8 GB machine can process any dataset.

ODM's peak RAM grows roughly linearly with images per reconstruction (its own
guidance: ~4 GB for 40 images, 16 GB for 250). We keep a small learned model
per quality preset: every run records (images, megapixels, peak RAM), and the
per-image slope used for planning is the worst one observed. Big jobs are then
cut into chunks that fit, and a chunk that still runs out of memory is split
and retried automatically, which also teaches the model.
"""
from __future__ import annotations

import json
import re

import numpy as np
import subprocess
import threading
import time

from .config import DATA_DIR

MODEL_PATH = DATA_DIR / "memory_model.json"
BASE_GB = 0.9              # floor for the fixed part of ODM's memory
# fixed part per preset before any measurement: preview measured 2.6 GB at both
# 101 and 296 images, so most of ODM's peak does not depend on image count
PRIOR_BASE = {"preview": 2.4, "standard": 2.4, "max": 2.8}
SAFETY = 1.25              # headroom over the worst observed slope
# priors (GB per 8.3 MP image), from our first runs: Standard reached texturing with
# 296 images in 3.8 GB. Replaced by measurements as soon as runs complete.
PRIOR_PER_IMAGE = {"preview": 0.004, "standard": 0.012, "max": 0.035}
MIN_CHUNK = 24             # below this a reconstruction is not worth splitting further


def _load() -> dict:
    try:
        return json.loads(MODEL_PATH.read_text())
    except (OSError, ValueError):
        return {"samples": []}


def record(preset: str, n_images: int, megapixels: float, peak_gb: float, oom: bool) -> None:
    m = _load()
    m["samples"].append({"preset": preset, "n": n_images, "mp": round(megapixels, 2),
                         "peak_gb": round(peak_gb, 3), "oom": oom, "t": time.time()})
    m["samples"] = m["samples"][-200:]
    MODEL_PATH.write_text(json.dumps(m, indent=1))


MIN_SLOPE = 0.002          # GB/image floor: memory never truly stops growing with images


def model(preset: str, megapixels: float) -> tuple[float, float]:
    """Peak RAM model for a preset: peak = base + slope * images.

    Fitted as an upper envelope over every successful run (so the plan covers
    the worst run seen), then pushed up by any run that ran out of memory.
    Measured: preview used 2.8 GB for 101 images and 2.7 GB for 296, so the
    fixed part dominates and a per-image-only model would chunk far too small.
    """
    ok, oom = [], []
    for s in _load()["samples"]:
        if s["preset"] != preset or s["n"] <= 0:
            continue
        n_eff = s["n"] * megapixels / max(s["mp"], 0.1)
        (oom if s["oom"] else ok).append((n_eff, s["peak_gb"]))
    prior_slope = PRIOR_PER_IMAGE[preset] * megapixels / 8.3
    if len(ok) >= 2 and len({round(n) for n, _ in ok}) >= 2:
        ns, ps = np.array([n for n, _ in ok]), np.array([p for _, p in ok])
        slope = max(MIN_SLOPE, float(np.polyfit(ns, ps, 1)[0]))
        base = float(np.max(ps - slope * ns))
    elif ok:
        n, p = max(ok)
        base = min(PRIOR_BASE[preset], p)
        slope = max(MIN_SLOPE, min(prior_slope, (p - base) / n))
        base = p - slope * n
    else:
        slope, base = prior_slope, PRIOR_BASE[preset]
    for n, p in oom:
        # this many images did not fit in the p GB budget: the line must pass above it
        if base + slope * n < p:
            slope = max(slope, (p - base) / n)
    return base, slope


def per_image_gb(preset: str, megapixels: float) -> float:
    return model(preset, megapixels)[1] * SAFETY


def max_images(preset: str, budget_gb: float, megapixels: float) -> int:
    base, slope = model(preset, megapixels)
    room = budget_gb - base * 1.05
    if room <= 0:
        return MIN_CHUNK
    return max(MIN_CHUNK, int(room / (slope * SAFETY)))


def docker_budget_gb() -> float:
    """RAM ODM may use: what Docker has, minus room for the OS inside the VM."""
    r = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True)
    try:
        total = int(r.stdout.strip()) / 2**30
    except ValueError:
        return 3.0
    budget = total * 0.88 - 0.3
    # on a laptop the Docker VM shares RAM with the OS and the app: never plan
    # to use more than the host can give while staying responsive
    host = host_ram_gb()
    if host:
        budget = min(budget, host - 3.0)
    return max(1.5, budget)


def host_ram_gb() -> float:
    import os
    import platform
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout
            return int(out.strip()) / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError):
        return 0.0


_MEM_RE = re.compile(r"([\d.]+)\s*([KMG]i?B)")
_UNIT = {"KiB": 2**-20, "KB": 1e3 / 2**30, "MiB": 2**-10, "MB": 1e6 / 2**30, "GiB": 1.0, "GB": 1e9 / 2**30}


class PeakMonitor:
    """Samples a container's memory every few seconds; tracks overall and per-stage peaks."""

    def __init__(self, container: str, interval: float = 2.0):
        self.container = container
        self.interval = interval
        self.peak_gb = 0.0
        self.stage = "start"
        self.stage_peaks: dict[str, float] = {}
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=10)

    def _sample(self) -> float | None:
        """Memory ODM really needs: anonymous + kernel + shared (cgroup v2).

        `docker stats` also counts file cache, which the kernel drops under
        pressure; reading 100 photos alone showed 4 GB that way. Planning on it
        would make chunks needlessly small.
        """
        r = subprocess.run(["docker", "exec", self.container, "cat", "/sys/fs/cgroup/memory.stat"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            vals = {}
            for line in r.stdout.splitlines():
                k, _, v = line.partition(" ")
                if k in ("anon", "kernel", "shmem"):
                    vals[k] = int(v)
            if "anon" in vals:
                return sum(vals.values()) / 2**30
        r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", self.container],
                           capture_output=True, text=True)
        m = _MEM_RE.search(r.stdout or "")
        return float(m.group(1)) * _UNIT.get(m.group(2), 1.0) if m else None

    def _run(self):
        while not self._stop.is_set():
            gb = self._sample()
            if gb is not None:
                self.peak_gb = max(self.peak_gb, gb)
                self.stage_peaks[self.stage] = max(self.stage_peaks.get(self.stage, 0.0), gb)
            self._stop.wait(self.interval)
