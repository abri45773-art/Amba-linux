#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writer for Android binary XML (AXML) with no SDK dependency.

Only what a launcher activity manifest needs is implemented: namespace
declarations, elements with attributes holding string, integer, boolean or
resource-reference values, and one UTF-8 string pool. String indices are
registered in a first pass so they are stable while chunks are rendered.

Format reference: https://justanapplication.wordpress.com/category/android/android-binary-xml/
"""
from __future__ import annotations

import struct

CHUNK_STRING_POOL = 0x0001
CHUNK_XML = 0x0003
CHUNK_XML_START_NAMESPACE = 0x0100
CHUNK_XML_END_NAMESPACE = 0x0101
CHUNK_XML_START_ELEMENT = 0x0102
CHUNK_XML_END_ELEMENT = 0x0103
CHUNK_XML_RESOURCE_MAP = 0x0180

ANDROID_NS = "http://schemas.android.com/apk/res/android"

TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_BOOLEAN = 0x12

NO_ENTRY = 0xFFFFFFFF
UTF8_FLAG = 0x00000100


def _utf8_length_header(length: int) -> bytes:
    """Length prefix used by UTF-8 pools: one byte, or two when 0x80 or more."""
    if length < 0x80:
        return bytes((length,))
    return bytes((0x80 | (length >> 8), length & 0xFF))


class StringPool:
    """UTF-8 string pool chunk."""

    def __init__(self) -> None:
        self._strings: list[str] = []
        self._index: dict[str, int] = {}

    def add(self, value: str) -> int:
        if value not in self._index:
            self._index[value] = len(self._strings)
            self._strings.append(value)
        return self._index[value]

    def __len__(self) -> int:
        return len(self._strings)

    def encode(self) -> bytes:
        data = bytearray()
        offsets: list[int] = []
        for value in self._strings:
            encoded = value.encode("utf-8")
            offsets.append(len(data))
            data += _utf8_length_header(len(value)) + _utf8_length_header(len(encoded))
            data += encoded + b"\x00"
        while len(data) % 4:
            data.append(0)

        # ResStringPool_header is 28 bytes from the chunk start: 8 bytes of
        # chunk header, then count, styleCount, flags, stringsStart, stylesStart.
        header_size = 0x1C
        strings_start = header_size + 4 * len(self._strings)
        header = struct.pack(
            "<HHIIIIII",
            CHUNK_STRING_POOL,
            header_size,
            header_size + 4 * len(self._strings) + len(data),
            len(self._strings),
            0,  # styleCount
            UTF8_FLAG,
            strings_start,
            0,  # stylesStart
        )
        offsets = b"".join(struct.pack("<I", offset) for offset in offsets)
        return header + offsets + bytes(data)


class RawString:
    """Marks a plain string attribute value (as opposed to a resource id)."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class Reference:
    """A resource reference used as an attribute value, e.g. @string/app_name."""

    __slots__ = ("res_id", "raw")

    def __init__(self, res_id: int, raw: int | None = None) -> None:
        self.res_id = res_id
        self.raw = raw


class Element:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: list[tuple[str | None, str, object]] = []
        self.children: list = []

    def attr(self, key: str, value: object) -> "Element":
        """Add an attribute; ``android__name`` maps to the android namespace."""
        namespace = None
        name = key
        if "__" in key:
            prefix, name = key.split("__", 1)
            if prefix != "android":
                raise ValueError(f"namespace tidak dikenal: {prefix}")
            namespace = ANDROID_NS
        if not isinstance(value, (RawString, Reference, int, bool)):
            raise TypeError(f"nilai atribut tidak didukung: {value!r}")
        self.attributes.append((namespace, name, value))
        return self

    def child(self, name: str, **attributes: object) -> "Element":
        element = Element(name)
        for key, value in attributes.items():
            element.attr(key, value)
        self.children.append(element)
        return element


class Document:
    def __init__(
        self,
        namespaces: tuple[tuple[str, str], ...] = (("android", ANDROID_NS),),
        resource_map: dict[int, int] | None = None,
    ) -> None:
        self.root = Element("__root__")
        self.pool = StringPool()
        self.namespaces = list(namespaces)
        # Binary XML keeps one resource-map entry per attribute *name* in pool
        # order, so tag names must be added before their attributes.
        self.resource_map = resource_map or {}
        self.attribute_ids: dict[str, int] = {}

    def element(self, name: str, **attributes: object) -> Element:
        return self.root.child(name, **attributes)

    def build(self) -> bytes:
        # Pass 1 keeps every string index stable for the rendering pass.
        for prefix, uri in self.namespaces:
            self.pool.add(prefix)
            self.pool.add(uri)
        self._register(self.root)

        chunks = bytearray()
        chunks += self._resource_map()
        for prefix, uri in self.namespaces:
            chunks += self._namespace_chunk(CHUNK_XML_START_NAMESPACE, prefix, uri)
        chunks += self._render(self.root)
        for prefix, uri in reversed(self.namespaces):
            chunks += self._namespace_chunk(CHUNK_XML_END_NAMESPACE, prefix, uri)

        body = self.pool.encode() + bytes(chunks)
        return struct.pack("<HHI", CHUNK_XML, 8, 8 + len(body)) + body

    def _resource_map(self) -> bytes:
        """Map attribute-name string indices to framework attr ids in order."""
        entries = []
        for index, value in enumerate(self.pool._strings):  # noqa: SLF001 - same module
            res_id = self.resource_map.get(value)
            if res_id is not None:
                entries.append((index, res_id))
        if not entries:
            return b""
        body = b"".join(struct.pack("<I", res_id) for _, res_id in entries)
        return struct.pack("<HHI", CHUNK_XML_RESOURCE_MAP, 8, 8 + len(body)) + body

    def _namespace_chunk(self, kind: int, prefix: str, uri: str) -> bytes:
        return struct.pack(
            "<HHIIIII",
            kind,
            16,
            24,
            1,
            NO_ENTRY,
            self.pool.add(prefix),
            self.pool.add(uri),
        )

    def _register(self, node) -> None:
        for child in node.children:
            self.pool.add(child.name)
            for namespace, name, value in child.attributes:
                self.pool.add(name)
                if namespace:
                    self.pool.add(namespace)
                if isinstance(value, RawString):
                    self.pool.add(value.text)
            self._register(child)

    def _render(self, node) -> bytes:
        out = bytearray()
        for child in node.children:
            attrs = bytearray()
            for namespace, name, value in child.attributes:
                if isinstance(value, RawString):
                    raw_index = self.pool.add(value.text)
                    data_type, data = TYPE_STRING, raw_index
                elif isinstance(value, Reference):
                    raw_index = value.raw if value.raw is not None else NO_ENTRY
                    data_type, data = TYPE_REFERENCE, value.res_id
                elif isinstance(value, bool):
                    raw_index = NO_ENTRY
                    data_type, data = TYPE_INT_BOOLEAN, 0xFFFFFFFF if value else 0
                else:
                    raw_index = NO_ENTRY
                    data_type, data = TYPE_INT_DEC, value & 0xFFFFFFFF
                # 20 bytes: ns, name, raw value, then a Res_value (size, res0,
                # dataType, data). aapt uses this width for every attribute.
                attrs += struct.pack(
                    "<IIIH",
                    self.pool.add(namespace) if namespace else NO_ENTRY,
                    self.pool.add(name),
                    raw_index,
                    8,
                )
                attrs += struct.pack("<BBI", 0, data_type, data)

            body = struct.pack("<II", NO_ENTRY, self.pool.add(child.name))
            body += struct.pack(
                "<HHHHHH",
                20,  # attributeStart, relative to this struct
                20,  # attributeSize: every attribute is 20 bytes wide
                len(child.attributes),
                0,  # idIndex
                0,  # classIndex
                0,  # styleIndex
            )
            body += attrs
            out += struct.pack("<HHIII", CHUNK_XML_START_ELEMENT, 16, 16 + len(body), 1, NO_ENTRY)
            out += body
            out += self._render(child)
            out += struct.pack(
                "<HHIIIII",
                CHUNK_XML_END_ELEMENT,
                16,
                24,
                1,
                NO_ENTRY,
                NO_ENTRY,
                self.pool.add(child.name),
            )
        return bytes(out)


def build_manifest(root: Element) -> bytes:
    """Serialise ``root`` (usually <manifest>) with the android namespace bound."""
    document = Document()
    document.root.children.append(root)
    return document.build()
