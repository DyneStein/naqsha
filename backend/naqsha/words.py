"""Plain-language stage names for the command line (mirrors the web UI)."""
import re

_STAGES = [
    (r"scanning", "checking video quality"), (r"selecting", "choosing photos"),
    (r"exporting", "extracting photos"), (r"image links", "checking overlaps"),
    (r"densifying", "adding photos at weak spots"), (r"geotagging", "writing GPS into photos"),
    (r"telemetry", "reading GPS"), (r"cleaning GPS", "cleaning GPS"), (r"terrain", "terrain model"),
    (r"coverage", "coverage"), (r"detect_features", "finding features"), (r"match_features", "matching photos"),
    (r"create_tracks", "linking matches"), (r"reconstruct|bundle", "solving camera positions"),
    (r"openmvs", "dense 3D surface"), (r"meshing", "surface mesh"), (r"texturing", "painting photos onto surface"),
    (r"odm_dem", "elevation map"), (r"odm_orthophoto", "top-down map"), (r"joining", "joining parts"),
    (r"quality", "quality check"),
]


def friendly(stage) -> str:
    if not stage:
        return "waiting"
    m = re.match(r"^part (\d+) of (\d+):\s*(.*)$", stage, re.I)
    core = m.group(3) if m else stage
    text = next((t for p, t in _STAGES if re.search(p, core, re.I)), core)
    return f"part {m.group(1)}/{m.group(2)} {text}" if m else text
