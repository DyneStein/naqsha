"""Per-frame telemetry container shared by all decoders."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# One row per video frame. Angles in degrees, distances in metres, speeds m/s.
FIELDS = (
    "t",            # seconds since video start (frame_index / fps)
    "lat", "lon",   # WGS84, NaN where no fix
    "alt",          # altitude as reported by the drone (unreliable absolute)
    "rel_h",        # barometric height above takeoff
    "vn", "ve", "vd",
    "yaw", "pitch", "roll",          # aircraft attitude, yaw true-north clockwise
    "g_pitch", "g_roll", "g_yaw",    # gimbal; g_pitch -90 = nadir
    "wb",           # white balance (K), 0 when unknown
    "gps_new",      # 1 where a fresh GPS sample arrived at this frame
)


@dataclass
class Telemetry:
    source: str                      # "dji-djmd" | "dji-srt"
    fps: float
    camera_model: str
    data: dict[str, np.ndarray]
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.data["t"])

    def save(self, path: Path) -> None:
        np.savez_compressed(
            path,
            __meta=np.array([self.source, str(self.fps), self.camera_model, "\n".join(self.notes)]),
            **self.data,
        )

    @classmethod
    def load(cls, path: Path) -> "Telemetry":
        z = np.load(path, allow_pickle=False)
        source, fps, cam, notes = [str(x) for x in z["__meta"]]
        data = {k: z[k] for k in z.files if k != "__meta"}
        return cls(source, float(fps), cam, data, [n for n in notes.split("\n") if n])

    @classmethod
    def empty(cls, n: int, source: str, fps: float, camera_model: str) -> "Telemetry":
        data = {k: np.full(n, np.nan) for k in FIELDS}
        data["t"] = np.arange(n) / fps
        data["gps_new"] = np.zeros(n)
        data["wb"] = np.zeros(n)
        return cls(source, fps, camera_model, data)
