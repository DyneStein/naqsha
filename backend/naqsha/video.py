"""ffprobe/ffmpeg wrappers: probing, low-res analysis decode, full-res frame export."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import numpy as np

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".lrf"}


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    frames: int
    duration: float
    codec: str
    pix_fmt: str
    creation_time: str | None
    encoder: str | None
    telemetry_stream: int | None   # index of DJI djmd data track, if any

    def to_dict(self) -> dict:
        return asdict(self)


def _rate(s: str) -> float:
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    return float(s)


def probe(path: Path) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, check=True, text=True,
    ).stdout
    j = json.loads(out)
    video = next(
        (s for s in j["streams"]
         if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    if video is None:
        raise ValueError(f"{path.name}: no video stream")
    tel = None
    for s in j["streams"]:
        tag = (s.get("codec_tag_string") or "").lower()
        handler = (s.get("tags", {}).get("handler_name") or "").lower()
        if s.get("codec_type") == "data" and (tag == "djmd" or handler == "cam meta"):
            tel = int(s["index"])
            break
    fps = _rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0")
    duration = float(video.get("duration") or j["format"].get("duration") or 0)
    frames = int(video.get("nb_frames") or round(duration * fps))
    fmt_tags = j["format"].get("tags", {})
    return VideoInfo(
        path=str(path), width=int(video["width"]), height=int(video["height"]), fps=fps,
        frames=frames, duration=duration, codec=video.get("codec_name", "?"),
        pix_fmt=video.get("pix_fmt", "?"),
        creation_time=fmt_tags.get("creation_time") or video.get("tags", {}).get("creation_time"),
        encoder=fmt_tags.get("encoder"), telemetry_stream=tel,
    )


@lru_cache(maxsize=1)
def hwaccel() -> list[str]:
    """Hardware decode flags for this machine (decode is the slow part for 4K HEVC)."""
    if platform.system() == "Darwin":
        return ["-hwaccel", "videotoolbox"]
    if shutil.which("nvidia-smi"):
        return ["-hwaccel", "cuda"]
    return []


def iter_lowres_gray(path: Path, step: int, width: int = 960) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (frame_index, gray uint8 image) for every `step`-th frame.

    Every frame must still be decoded (inter-frame codec), so this is a single
    sequential pass with the frame subsampling done inside ffmpeg.
    """
    info = probe(path)
    height = int(round(info.height * width / info.width / 2) * 2)
    cmd = [
        "ffmpeg", "-v", "error", *hwaccel(), "-i", str(path), "-map", "0:v:0",
        "-vf", f"select='not(mod(n\\,{step}))',scale={width}:{height}:flags=area,format=gray",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-",
    ]
    size = width * height
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=size * 4)
    try:
        k = 0
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield k * step, np.frombuffer(buf, np.uint8).reshape(height, width)
            k += 1
    finally:
        proc.stdout.close()
        rc = proc.wait()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if rc not in (0, None) and k == 0:
            raise RuntimeError(f"ffmpeg decode failed for {path.name}: {err[-500:]}")


def export_frames(path: Path, frame_indices: list[int], out_dir: Path, quality: int = 2,
                  chunk: int = 50, progress=None) -> list[Path]:
    """Export exact frames at full resolution as high-quality JPEGs.

    Returns paths in the same order as the sorted unique indices. The select
    filter emits frames in stream order, so the k-th output file is the k-th
    requested index; the count is verified so a silent mismatch cannot mislabel
    frames (which would attach the wrong GPS to an image). Chunks stay well
    under ffmpeg's expression nesting limit (~100 terms).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted(set(int(i) for i in frame_indices))
    fps = probe(path).fps
    results: list[Path] = []
    for c in range(0, len(wanted), chunk):
        part = wanted[c : c + chunk]
        tmp = out_dir / f".tmp_{c}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        # Seek near the chunk start so each chunk does not decode from frame 0.
        # With -copyts the filter clock stays on the original timeline, so the
        # frame number is recovered as round(t * fps) (constant frame rate).
        seek = max(0.0, part[0] / fps - 2.0)
        expr = "+".join(f"eq(round(t*{fps:.6f})\\,{i})" for i in part)
        cmd = ["ffmpeg", "-v", "error", *hwaccel()]
        if seek > 0:
            cmd += ["-ss", f"{seek:.3f}"]
        cmd += ["-i", str(path), "-map", "0:v:0", "-copyts",
                "-vf", f"select='{expr}'", "-fps_mode", "passthrough",
                "-q:v", str(quality), "-pix_fmt", "yuvj420p", str(tmp / "%06d.jpg")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"ffmpeg failed exporting {path.name}: {r.stderr.strip()[-800:]}")
        got = sorted(tmp.glob("*.jpg"))
        if len(got) != len(part):
            shutil.rmtree(tmp)
            raise RuntimeError(
                f"{path.name}: asked ffmpeg for {len(part)} frames, got {len(got)}; "
                "refusing to guess the frame-to-GPS mapping"
            )
        for idx, src in zip(part, got):
            dst = out_dir / f"f{idx:07d}.jpg"
            src.replace(dst)
            results.append(dst)
        tmp.rmdir()
        if progress:
            progress(len(results) / len(wanted))
    return results
