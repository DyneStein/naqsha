from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("NAQSHA_DATA", ROOT.parent / "naqsha-data")).resolve()
DB_PATH = DATA_DIR / "naqsha.sqlite"
FRONTEND_DIST = ROOT / "frontend" / "dist"

ODM_IMAGE = os.environ.get("NAQSHA_ODM_IMAGE", "opendronemap/odm:latest")
ODM_GPU_IMAGE = os.environ.get("NAQSHA_ODM_GPU_IMAGE", "opendronemap/odm:gpu")
DEM_AUTO = os.environ.get("NAQSHA_DEM_AUTO", "1") == "1"   # fetch Copernicus GLO-30 for terrain


def project_dir(pid: int) -> Path:
    return DATA_DIR / "projects" / f"p{pid:04d}"


DATA_DIR.mkdir(parents=True, exist_ok=True)
# keep macOS Spotlight from indexing gigabytes of frames and rasters (CPU + RAM)
(DATA_DIR / ".metadata_never_index").touch(exist_ok=True)
