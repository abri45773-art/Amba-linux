#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writer for a minimal Android resource table (resources.arsc).

The companion app only needs a ``string`` type with one default
configuration, so this emits exactly that: a global value pool, one package
with its type and key pools, a type spec, and one type chunk.

Resource ids follow the usual layout 0xPPTTEEEE: PP is the package id (0x7F),
TT the 1-based type id, EEEE the entry index.
"""
from __future__ import annotations

import struct

from axml import StringPool

CHUNK_RES_TABLE = 0x0002
CHUNK_RES_TABLE_PACKAGE = 0x0200
CHUNK_RES_TABLE_TYPE = 0x0201
CHUNK_RES_TABLE_TYPE_SPEC = 0x0202

TYPE_STRING = 0x03

PACKAGE_ID = 0x7F
TYPE_STRING_ID = 0x01

# ResTable_config with every field zeroed is the default configuration.
DEFAULT_CONFIG = struct.pack("<II", 0x38, 0) + b"\x00" * (0x38 - 8)


def resource_id(type_id: int, entry_index: int) -> int:
    return (PACKAGE_ID << 24) | (type_id << 16) | entry_index


class ResourceTable:
    def __init__(self, package_name: str = "com.amba.companion") -> None:
        self.package_name = package_name
        self.values: list[str] = []
        self.keys: list[str] = []

    def add_string(self, key: str, value: str) -> int:
        """Register a string resource and return its resource id."""
        if key in self.keys:
            raise ValueError(f"kunci resource ganda: {key}")
        self.keys.append(key)
        self.values.append(value)
        return resource_id(TYPE_STRING_ID, len(self.keys) - 1)

    def build(self) -> bytes:
        if not self.values:
            raise ValueError("tabel resource tanpa entri tidak valid")
        value_pool = StringPool()
        key_pool = StringPool()
        type_pool = StringPool()
        type_pool.add("string")
        for value in self.values:
            value_pool.add(value)
        for key in self.keys:
            key_pool.add(key)

        body = value_pool.encode() + self._package(key_pool, type_pool, value_pool)
        # ResTable_header: chunk header + packageCount
        return struct.pack("<HHII", CHUNK_RES_TABLE, 12, 12 + len(body), 1) + body

    def _package(self, key_pool: StringPool, type_pool: StringPool, value_pool: StringPool) -> bytes:
        header_size = 0x011C  # chunk header + id + 128 UTF-16 chars + 4 offsets
        type_pool_bytes = type_pool.encode()
        key_pool_bytes = key_pool.encode()
        type_spec = self._type_spec()
        type_chunk = self._type(key_pool, value_pool)

        types_start = header_size
        key_start = types_start + len(type_pool_bytes)
        size = header_size + len(type_pool_bytes) + len(key_pool_bytes) + len(type_spec) + len(type_chunk)

        header = struct.pack("<HHI", CHUNK_RES_TABLE_PACKAGE, header_size, size)
        header += struct.pack("<I", PACKAGE_ID)
        header += self.package_name.encode("utf-16-le")[:254].ljust(256, b"\x00")
        header += struct.pack("<IIII", types_start, 1, key_start, len(self.keys))
        if len(header) != header_size:  # pragma: no cover - layout regression guard
            raise AssertionError("header paket arsc tidak 0x11C byte")
        return header + type_pool_bytes + key_pool_bytes + type_spec + type_chunk

    def _type_spec(self) -> bytes:
        # ResTable_typeSpec: header + id + res0 + res1 + entryCount, then flags.
        count = len(self.keys)
        header_size = 16
        flags = b"\x00\x00\x00\x00" * count
        return (
            struct.pack("<HHIBBH", CHUNK_RES_TABLE_TYPE_SPEC, header_size, header_size + len(flags), TYPE_STRING_ID, 0, 0)
            + struct.pack("<I", count)
            + flags
        )

    def _type(self, key_pool: StringPool, value_pool: StringPool) -> bytes:
        # ResTable_type: header + id + flags + reserved + entryCount +
        # entriesStart, then the configuration struct.
        count = len(self.keys)
        header_size = 0x14 + len(DEFAULT_CONFIG)  # 0x4C
        entries_start = header_size + 4 * count

        entries = bytearray()
        offsets: list[int] = []
        for index, key in enumerate(self.keys):
            while (entries_start + len(entries)) % 4:
                entries.append(0)
            offsets.append(len(entries))
            # ResTable_entry: size, flags, key index; then Res_value.
            entries += struct.pack("<HHI", 8, 0, key_pool.add(key))
            entries += struct.pack("<HBBI", 8, 0, TYPE_STRING, value_pool.add(self.values[index]))
        while len(entries) % 4:
            entries.append(0)

        header = struct.pack(
            "<HHIBBH",
            CHUNK_RES_TABLE_TYPE,
            header_size,
            entries_start + len(entries),
            TYPE_STRING_ID,
            0,  # flags
            0,  # reserved
        )
        header += struct.pack("<II", count, entries_start)
        header += DEFAULT_CONFIG
        if len(header) != header_size:  # pragma: no cover - layout regression guard
            raise AssertionError("header tipe arsc tidak 0x4C byte")
        table = b"".join(struct.pack("<I", offset) for offset in offsets)
        return header + table + bytes(entries)
