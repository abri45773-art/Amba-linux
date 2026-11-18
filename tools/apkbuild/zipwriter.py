#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic ZIP writer with the alignment rules Android expects.

``resources.arsc`` must be stored uncompressed and start on a 4-byte
boundary, and the Android runtime reads uncompressed entries straight from
mapped memory, so their data offsets get aligned too.
"""
from __future__ import annotations

import struct
import time
import zlib

STORE = 0
DEFLATE = 8

#: Entry names that Android requires to be stored uncompressed.
UNCOMPRESSED_NAMES = {"resources.arsc"}


class ZipWriter:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(
        self,
        name: str,
        data: bytes,
        compress: bool | None = None,
        align: int = 4,
        timestamp: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0),
    ) -> None:
        if compress is None:
            compress = name not in UNCOMPRESSED_NAMES
        if name in UNCOMPRESSED_NAMES:
            compress = False
        self.entries.append(
            {
                "name": name,
                "data": data,
                "compress": compress,
                "align": align if not compress else 0,
                "timestamp": timestamp,
            }
        )

    @staticmethod
    def _dos_time(value: tuple[int, int, int, int, int, int]) -> tuple[int, int]:
        year, month, day, hour, minute, second = value
        if year < 1980:
            year = 1980
        dos_time = (hour << 11) | (minute << 5) | (second // 2)
        dos_date = ((year - 1980) << 9) | (month << 5) | day
        return dos_time, dos_date

    def build(self) -> bytes:
        out = bytearray()
        central = bytearray()
        for entry in self.entries:
            name = entry["name"].encode("utf-8")
            payload = entry["data"]
            if entry["compress"]:
                compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
                stored = compressor.compress(payload) + compressor.flush()
                method = DEFLATE
            else:
                stored = payload
                method = STORE

            offset = len(out)
            if entry["align"] and offset % entry["align"]:
                out += b"\x00" * (entry["align"] - offset % entry["align"])
                offset = len(out)

            crc = zlib.crc32(payload) & 0xFFFFFFFF
            dos_time, dos_date = self._dos_time(entry["timestamp"])
            flags = 0x0800  # UTF-8 names
            out += struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                20,  # version needed
                flags,
                method,
                dos_time,
                dos_date,
                crc,
                len(stored),
                len(payload),
                len(name),
                0,  # extra length
            )
            out += name
            out += stored

            central += struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                20,  # version made by
                20,  # version needed
                flags,
                method,
                dos_time,
                dos_date,
                crc,
                len(stored),
                len(payload),
                len(name),
                0,  # extra
                0,  # comment
                0,  # disk number
                0,  # internal attrs
                0o644 << 16,  # external attrs
                offset,
            )
            central += name

        central_offset = len(out)
        out += central
        out += struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            len(self.entries),
            len(self.entries),
            len(central),
            central_offset,
            0,
        )
        return bytes(out)


def timestamp_from_epoch(epoch: int) -> tuple[int, int, int, int, int, int]:
    return time.gmtime(epoch)[:6]
