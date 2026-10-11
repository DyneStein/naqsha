"""Camera intrinsics: first guess from known models, refined by calibration.

The video FOV of a drone can differ from its photo FOV (sensor crop, EIS). We
start from the 35 mm equivalent focal length and, after the first successful
reconstruction, store the self-calibrated focal length so later footprint
estimates and runs use the measured value.
"""
from __future__ import annotations

import json
from pathlib import Path

from .geo import Intrinsics

# 35 mm-equivalent focal length for video modes (16:9, full width readout).
KNOWN_F35 = {
    "dji fc9313": 24.0,   # Mini 5 Pro
    "dji fc8482": 24.0,   # Mini 4 Pro
    "dji fc3582": 24.0,   # Mini 3 Pro
    "dji fc3682": 24.0,   # Mini 3
    "dji fc4382": 24.0,   # Air 3 wide
    "dji l2d-20c": 24.0,  # Mavic 3 Pro Hasselblad
}
DEFAULT_F35 = 24.0


class CameraStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text()) if path.exists() else {}

    def key(self, model: str, w: int, h: int) -> str:
        return f"{model.lower()}|{w}x{h}"

    def intrinsics(self, model: str, w: int, h: int) -> tuple[Intrinsics, str]:
        k = self.key(model, w, h)
        cal = self.data.get(k)
        if cal and cal.get("focal_ratio"):
            f = cal["focal_ratio"] * max(w, h)
            return Intrinsics(w, h, f, w / 2, h / 2), "calibrated"
        f35 = KNOWN_F35.get(model.lower(), DEFAULT_F35)
        return Intrinsics.from_f35(w, h, f35), "nominal" if model.lower() in KNOWN_F35 else "assumed"

    def f35(self, model: str, w: int, h: int) -> float:
        K, _ = self.intrinsics(model, w, h)
        return K.focal_px / max(w, h) * 36.0

    @staticmethod
    def nominal_f35(model: str) -> float:
        """Focal written to EXIF. Kept nominal on purpose: OpenSfM derives the
        camera id from it, and a stored calibration (--cameras) is matched by
        that id, so it must not change once a calibration exists."""
        return KNOWN_F35.get(model.lower(), DEFAULT_F35)

    def save_calibration(self, model: str, w: int, h: int, cameras_json: dict, run_id: int) -> None:
        """Store the OpenSfM camera (focal, distortion) for this model/resolution."""
        for name, cam in cameras_json.items():
            if int(cam.get("width", 0)) == w and int(cam.get("height", 0)) == h:
                self.data[self.key(model, w, h)] = {
                    "focal_ratio": cam.get("focal_x", cam.get("focal")),
                    "opensfm_camera": {name: cam},
                    "from_run": run_id,
                }
                self.path.write_text(json.dumps(self.data, indent=2))
                return

    def opensfm_cameras(self, model: str, w: int, h: int) -> dict | None:
        cal = self.data.get(self.key(model, w, h))
        return cal.get("opensfm_camera") if cal else None
