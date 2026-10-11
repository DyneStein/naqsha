"""Minimal schema-less protobuf wire-format decoder.

DJI embeds per-frame telemetry in the MP4 as a protobuf stream ("djmd" data
track). DJI does not publish the .proto, so we decode the wire format directly
and address fields by number path. Proto3 omits zero-valued fields, so a
missing field means 0, not "unknown".
"""
from __future__ import annotations

import struct

Message = list  # list[tuple[int, object]]


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return result, i
        if shift > 70:
            raise ValueError("varint too long")


def decode(buf: bytes, depth: int = 0) -> Message:
    """Decode a protobuf message into [(field_number, value), ...].

    Length-delimited fields are decoded recursively when they parse cleanly as a
    message, otherwise kept as bytes.
    """
    out: Message = []
    i = 0
    n = len(buf)
    while i < n:
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError("field 0")
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            if i + 8 > n:
                raise ValueError("truncated fixed64")
            value = struct.unpack_from("<d", buf, i)[0]
            i += 8
        elif wire == 5:
            if i + 4 > n:
                raise ValueError("truncated fixed32")
            value = struct.unpack_from("<f", buf, i)[0]
            i += 4
        elif wire == 2:
            length, i = _varint(buf, i)
            if i + length > n:
                raise ValueError("truncated bytes")
            raw = buf[i : i + length]
            i += length
            value = raw
            if raw and depth < 8:
                try:
                    sub = decode(raw, depth + 1)
                    if sub:
                        value = sub
                except ValueError:
                    value = raw
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.append((field, value))
    return out


def get(msg: Message, *path: int, default=0):
    """Fetch the first value at a field-number path, or default when absent."""
    cur = msg
    for f in path:
        if not isinstance(cur, list):
            return default
        for k, v in cur:
            if k == f:
                cur = v
                break
        else:
            return default
    return cur


def signed64(v: int) -> int:
    """Reinterpret an unsigned varint as two's-complement int64."""
    return v - (1 << 64) if v >= (1 << 63) else v
