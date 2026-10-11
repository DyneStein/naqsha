"""Write EXIF GPS + DJI-style XMP into extracted frames so ODM gets full priors.

ODM reads: EXIF GPS (position), EXIF FocalLengthIn35mmFilm (initial focal),
XMP drone-dji:FlightYawDegree / GimbalPitchDegree / GimbalRollDegree
(orientation priors, enables --sfm-algorithm triangulation),
drone-dji:Flight{X,Y,Z}Speed (rolling-shutter correction) and
Camera:GPSXYAccuracy / GPSZAccuracy (per-image prior weight; ODM doubles it).
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import piexif

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


def _rational(x: float, den: int = 10000):
    f = Fraction(x).limit_denominator(den)
    return (f.numerator, f.denominator)


def _dms(deg: float):
    deg = abs(deg)
    d = int(deg)
    m_full = (deg - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    return ((d, 1), (m, 1), (int(round(s * 1_000_000)), 1_000_000))


def build_xmp(meta: dict) -> bytes:
    def f(v):
        return f"{v:+.4f}"
    attrs = {
        "drone-dji:AbsoluteAltitude": f(meta["abs_alt"]),
        "drone-dji:RelativeAltitude": f(meta["rel_h"]),
        "drone-dji:GimbalPitchDegree": f(meta["g_pitch"]),
        "drone-dji:GimbalRollDegree": f(meta["g_roll"]),
        "drone-dji:GimbalYawDegree": f(_signed(meta["heading"])),
        "drone-dji:FlightYawDegree": f(_signed(meta["heading"])),
        "drone-dji:FlightPitchDegree": f(meta.get("pitch", 0.0)),
        "drone-dji:FlightRollDegree": f(meta.get("roll", 0.0)),
        "drone-dji:FlightXSpeed": f(meta["vn"]),
        "drone-dji:FlightYSpeed": f(meta["ve"]),
        "drone-dji:FlightZSpeed": f(meta["vd"]),
        # ODM multiplies these by 2 ("over-estimation"), so write half
        "Camera:GPSXYAccuracy": f"{meta['xy_sigma'] / 2:.3f}",
        "Camera:GPSZAccuracy": f"{meta['z_sigma'] / 2:.3f}",
        "naqsha:Clip": str(meta["clip"]),
        "naqsha:Frame": str(meta["frame"]),
    }
    body = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    xml = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" '
        'xmlns:Camera="http://pix4d.com/camera/1.0/" xmlns:naqsha="https://local/naqsha/1.0/" '
        f'{body}/></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )
    return xml.encode("utf-8")


def _signed(h: float) -> float:
    return ((h + 180.0) % 360.0) - 180.0


def insert_xmp(jpeg: bytes, xmp: bytes) -> bytes:
    """Insert an APP1 XMP segment right after SOI (and any APP0/APP1 EXIF)."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    payload = XMP_HEADER + xmp
    if len(payload) + 2 > 65535:
        raise ValueError("XMP too large")
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    # walk past APP0/APP1 segments
    i = 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF and jpeg[i + 1] in (0xE0, 0xE1):
        ln = struct.unpack(">H", jpeg[i + 2 : i + 4])[0]
        i += 2 + ln
    return jpeg[:i] + seg + jpeg[i:]


def geotag(path: Path, meta: dict) -> None:
    make, _, model = meta["camera_model"].partition(" ")
    if not model:
        make, model = "DJI", meta["camera_model"]
    ts: datetime = meta["time_utc"]
    zeroth = {
        piexif.ImageIFD.Make: make.encode(),
        piexif.ImageIFD.Model: model.encode(),
        piexif.ImageIFD.Software: b"naqsha",
        piexif.ImageIFD.DateTime: ts.strftime("%Y:%m:%d %H:%M:%S").encode(),
    }
    exif = {
        piexif.ExifIFD.DateTimeOriginal: ts.strftime("%Y:%m:%d %H:%M:%S").encode(),
        piexif.ExifIFD.SubSecTimeOriginal: f"{ts.microsecond // 1000:03d}".encode(),
        piexif.ExifIFD.SubSecTime: f"{ts.microsecond // 1000:03d}".encode(),   # the one ODM reads
        piexif.ExifIFD.OffsetTimeOriginal: b"+00:00",
        piexif.ExifIFD.FocalLengthIn35mmFilm: int(round(meta["f35"])),
        piexif.ExifIFD.FocalLength: _rational(meta["f35"] / 2.7, 1000),
        piexif.ExifIFD.PixelXDimension: meta["width"],
        piexif.ExifIFD.PixelYDimension: meta["height"],
    }
    gps = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: b"N" if meta["lat"] >= 0 else b"S",
        piexif.GPSIFD.GPSLatitude: _dms(meta["lat"]),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if meta["lon"] >= 0 else b"W",
        piexif.GPSIFD.GPSLongitude: _dms(meta["lon"]),
        piexif.GPSIFD.GPSAltitudeRef: 0 if meta["abs_alt"] >= 0 else 1,
        piexif.GPSIFD.GPSAltitude: _rational(abs(meta["abs_alt"]), 1000),
        piexif.GPSIFD.GPSHPositioningError: _rational(meta["xy_sigma"], 100),
    }
    blob = piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps, "1st": {}, "thumbnail": None})
    data = path.read_bytes()
    out_tmp = path.with_suffix(".tmp.jpg")
    piexif.insert(blob, data, str(out_tmp))
    tagged = insert_xmp(out_tmp.read_bytes(), build_xmp(meta))
    out_tmp.unlink()
    path.write_bytes(tagged)


def frame_time(creation_time: str | None, t: float) -> datetime:
    if creation_time:
        base = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
    else:
        base = datetime(2000, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(seconds=float(t))
