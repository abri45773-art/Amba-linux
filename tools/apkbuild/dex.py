#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Minimal DEX writer used to build the Amba Android companion app.

The release pipeline must not need Gradle, the Android SDK, or a network
download, so the DEX file is assembled here from a small instruction DSL that
covers exactly what the app uses. Anything outside that subset raises instead
of silently producing a broken file. Output is deterministic, so an unchanged
app builds to the same bytes.

Format reference: https://source.android.com/docs/core/runtime/dex-format
"""
from __future__ import annotations

import hashlib
import struct
import zlib

#: Instruction formats handled by :class:`Asm`; values are sizes in 16-bit code units.
FORMAT_SIZE = {
    "10x": 1, "11n": 1, "11x": 1, "12x": 1,
    "21c": 2, "21h": 2, "21s": 2, "21t": 2, "22c": 2, "23x": 2, "31i": 3, "35c": 3,
}

ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_STATIC = 0x0008
ACC_CONSTRUCTOR = 0x10000

TYPE_HEADER_ITEM = 0x0000
TYPE_STRING_ID_ITEM = 0x0001
TYPE_TYPE_ID_ITEM = 0x0002
TYPE_PROTO_ID_ITEM = 0x0003
TYPE_FIELD_ID_ITEM = 0x0004
TYPE_METHOD_ID_ITEM = 0x0005
TYPE_CLASS_DEF_ITEM = 0x0006
TYPE_MAP_LIST = 0x1000
TYPE_TYPE_LIST = 0x1001
TYPE_CLASS_DATA_ITEM = 0x2000
TYPE_CODE_ITEM = 0x2001
TYPE_STRING_DATA_ITEM = 0x2002


def uleb128(value: int) -> bytes:
    if value < 0:
        raise ValueError("uleb128 tidak menerima nilai negatif")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_mutf8(text: str) -> bytes:
    """DEX strings are MUTF-8: NUL is two bytes and there are no 4-byte forms."""
    out = bytearray()
    for char in text:
        code = ord(char)
        if code == 0:
            out += b"\xc0\x80"
        elif code < 0x80:
            out.append(code)
        elif code < 0x800:
            out += bytes((0xC0 | (code >> 6), 0x80 | (code & 0x3F)))
        elif code < 0x10000:
            out += bytes((0xE0 | (code >> 12), 0x80 | ((code >> 6) & 0x3F), 0x80 | (code & 0x3F)))
        else:
            raise ValueError("DEX 035 tidak mendukung karakter di luar BMP")
    return bytes(out)


class Ref:
    """Symbolic pool reference; resolved to a final index when the file is built.

    Pool indices shift while a class is still being written (every new string
    changes the sorted order), so instructions must never capture a raw index.
    """

    __slots__ = ("kind", "args")

    def __init__(self, kind: str, *args) -> None:
        self.kind = kind
        self.args = args

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Ref({self.kind!r}, {self.args!r})"


class Asm:
    """Assembler for one method body, with symbolic branch labels."""

    def __init__(self, registers: int, params: int) -> None:
        if not 0 <= registers <= 65535 or not 0 <= params <= registers:
            raise ValueError("jumlah register/parameter tidak masuk akal")
        self.registers = registers
        self.params = params
        self.items: list[dict] = []
        self.outs = 0

    def label(self, name: str) -> "Asm":
        self.items.append({"kind": "label", "name": name})
        return self

    def ins(self, op: int, fmt: str, **ops) -> "Asm":
        if fmt not in FORMAT_SIZE:
            raise ValueError(f"format tidak didukung: {fmt}")
        self.items.append({"kind": "ins", "op": op, "fmt": fmt, "ops": ops})
        if fmt == "35c":
            self.outs = max(self.outs, len(ops["regs"]))
        return self

    def _layout(self) -> tuple[list[int], dict[str, int]]:
        offsets: list[int] = []
        labels: dict[str, int] = {}
        cursor = 0
        for item in self.items:
            if item["kind"] == "label":
                if item["name"] in labels:
                    raise ValueError(f"label ganda: {item['name']}")
                labels[item["name"]] = cursor
                offsets.append(cursor)
            else:
                offsets.append(cursor)
                cursor += FORMAT_SIZE[item["fmt"]]
        return offsets, labels

    def assemble(self) -> bytes:
        offsets, labels = self._layout()
        out = bytearray()
        for item, offset in zip(self.items, offsets):
            if item["kind"] == "label":
                continue
            fmt, op, ops = item["fmt"], item["op"], item["ops"]
            if fmt == "10x":
                out += struct.pack("<H", op)
            elif fmt == "11n":
                value = ops["value"]
                if not 0 <= value <= 15:
                    raise ValueError("const/4 hanya menerima 0..15")
                out += struct.pack("<H", op | (value << 12) | (ops["reg"] << 8))
            elif fmt == "11x":
                out += struct.pack("<H", op | (ops["reg"] << 8))
            elif fmt == "12x":
                out += struct.pack("<H", op | (ops["a"] << 8) | (ops["b"] << 12))
            elif fmt == "21h":
                out += struct.pack("<Hh", op | (ops["a"] << 8), ops["value"])
            elif fmt == "31i":
                value = ops["value"]
                if not -0x80000000 <= value <= 0xFFFFFFFF:
                    raise ValueError("konstanta 32-bit di luar jangkauan")
                if value > 0x7FFFFFFF:  # e.g. an opaque colour literal
                    value -= 0x100000000
                out += struct.pack("<Hi", op | (ops["a"] << 8), value)
            elif fmt == "21s":
                out += struct.pack("<Hh", op | (ops["a"] << 8), ops["value"])
            elif fmt == "23x":
                out += struct.pack("<HBB", op | (ops["a"] << 8), ops["b"], ops["c"])
            elif fmt in ("21c", "22c"):
                out += struct.pack("<HH", op | (ops["a"] << 8), ops["index"])
            elif fmt == "21t":
                delta = labels[ops["target"]] - offset
                if not -32768 <= delta <= 32767:
                    raise ValueError("lompatan di luar jangkauan format 21t")
                out += struct.pack("<Hh", op | (ops["reg"] << 8), delta)
            elif fmt == "35c":
                regs = list(ops["regs"])
                if not 1 <= len(regs) <= 5:
                    raise ValueError("invoke membutuhkan 1..5 register")
                arr = regs + [0] * (5 - len(regs))
                out += struct.pack(
                    "<HHH",
                    op | (arr[4] << 8) | (len(regs) << 12),
                    ops["index"],
                    arr[0] | (arr[1] << 4) | (arr[2] << 8) | (arr[3] << 12),
                )
            else:  # pragma: no cover - guarded by ins()
                raise ValueError(f"format tidak didukung: {fmt}")
        return bytes(out)


class DexFile:
    """Builds one classes.dex from registered classes."""

    def __init__(self) -> None:
        self.strings: set[str] = set()
        self.types: set[str] = set()
        self.protos: set[tuple[str, tuple[str, ...]]] = set()
        self.fields: set[tuple[str, str, str]] = set()
        self.methods: set[tuple[str, str, str, tuple[str, ...]]] = set()
        self.classes: list[dict] = []

    # -- pools ------------------------------------------------------------
    def _use(self, value: str) -> None:
        self.strings.add(value)
        self.types.add(value)

    def string(self, value: str) -> int:
        self.strings.add(value)
        return sorted(self.strings).index(value)

    def type(self, descriptor: str) -> int:
        self._use(descriptor)
        return sorted(self.types).index(descriptor)

    def proto(self, ret: str, params: tuple[str, ...]) -> int:
        self.protos.add((ret, params))
        self._use(ret)
        for param in params:
            self._use(param)
        return sorted(self.protos, key=self._proto_key).index((ret, params))  # same order as build()

    def field(self, owner: str, name: str, type_: str) -> int:
        self.fields.add((owner, name, type_))
        self.strings.add(name)
        self._use(owner)
        self._use(type_)
        return sorted(self.fields, key=self._field_key).index((owner, name, type_))

    def method(self, owner: str, name: str, params: tuple[str, ...], ret: str) -> int:
        self.methods.add((owner, name, ret, params))
        self.strings.add(name)
        self._use(owner)
        self.proto(ret, params)
        return sorted(self.methods, key=self._method_key).index((owner, name, ret, params))

    # -- symbolic references used inside method bodies ---------------------
    def string_ref(self, value: str) -> Ref:
        return Ref("string", value)

    def type_ref(self, descriptor: str) -> Ref:
        return Ref("type", descriptor)

    def field_ref(self, owner: str, name: str, type_: str) -> Ref:
        return Ref("field", owner, name, type_)

    def method_ref(self, owner: str, name: str, params: tuple[str, ...], ret: str) -> Ref:
        return Ref("method", owner, name, params, ret)

    def _resolve(self, ref: Ref) -> int:
        if ref.kind == "string":
            return self.string(ref.args[0])
        if ref.kind == "type":
            return self.type(ref.args[0])
        if ref.kind == "field":
            return self.field(*ref.args)
        if ref.kind == "method":
            owner, name, params, ret = ref.args
            return self.method(owner, name, params, ret)
        raise ValueError(f"jenis referensi tidak dikenal: {ref.kind}")

    def _instructions_with_refs(self) -> list[dict]:
        """Every instruction dict that still carries a symbolic reference."""
        out = []
        for cls in self.classes:
            for method in cls["methods"]:
                for item in method["asm"].items:
                    if item["kind"] == "ins" and isinstance(item["ops"].get("index"), Ref):
                        out.append((item, item["ops"]["index"]))
        return out

    # -- classes ----------------------------------------------------------
    def add_class(self, name: str, superclass: str, access: int) -> dict:
        cls = {"name": name, "superclass": superclass, "access": access, "fields": [], "methods": []}
        self.classes.append(cls)
        self._use(name)
        self._use(superclass)
        return cls

    def add_field(self, cls: dict, name: str, type_: str, access: int) -> None:
        self.field(cls["name"], name, type_)
        cls["fields"].append({"name": name, "type": type_, "access": access})

    def add_method(self, cls: dict, name: str, asm: Asm, params: tuple[str, ...], ret: str, access: int) -> None:
        self.method(cls["name"], name, params, ret)
        cls["methods"].append({"name": name, "ret": ret, "params": params, "access": access, "asm": asm})

    # -- sorting keys -----------------------------------------------------
    def _tindex(self) -> dict[str, int]:
        return {value: i for i, value in enumerate(sorted(self.types))}

    def _sindex(self) -> dict[str, int]:
        return {value: i for i, value in enumerate(sorted(self.strings))}

    def _proto_key(self, proto):
        ret, params = proto
        ti = self._tindex()
        return (ti[ret], [ti[p] for p in params])

    def _field_key(self, field):
        owner, name, type_ = field
        ti, si = self._tindex(), self._sindex()
        return (ti[owner], si[name], ti[type_])

    def _method_key(self, method):
        owner, name, ret, params = method
        ti, si = self._tindex(), self._sindex()
        pi = {p: i for i, p in enumerate(sorted(self.protos, key=self._proto_key))}
        return (ti[owner], si[name], pi[(ret, params)])

    # -- serialisation ----------------------------------------------------
    def build(self) -> bytes:
        # Symbolic references still have to be registered before any id table
        # is sorted: pass 1 registers every referenced string/type/method/field,
        # pass 2 stores the final index once the pools have stopped growing.
        refs = self._instructions_with_refs()
        for _, ref in refs:
            self._resolve(ref)
        for item, ref in refs:
            item["ops"]["index"] = self._resolve(ref)

        strings = sorted(self.strings)
        types = sorted(self.types)
        if not strings or not types:
            raise ValueError("DEX harus memiliki minimal satu string dan satu tipe")
        sindex = {value: i for i, value in enumerate(strings)}
        tindex = {value: i for i, value in enumerate(types)}
        # The id tables are sorted by their own indices, so the sort key must be
        # evaluated against the fully sorted index maps, not the growing pools.
        def proto_sort(proto):
            ret, params = proto
            return (tindex[ret], [tindex[p] for p in params])

        def field_sort(field):
            owner, name, type_ = field
            return (tindex[owner], sindex[name], tindex[type_])

        protos = sorted(self.protos, key=proto_sort)
        pindex = {value: i for i, value in enumerate(protos)}

        def method_sort(method):
            owner, name, ret, params = method
            return (tindex[owner], sindex[name], pindex[(ret, params)])

        _ = field_sort, method_sort
        fields = sorted(self.fields, key=field_sort)
        fkey = {value: i for i, value in enumerate(fields)}
        methods = sorted(self.methods, key=method_sort)
        mkey = {value: i for i, value in enumerate(methods)}

        # The id tables sit between the header and the data section, so the
        # data section start is known before anything is serialised; every
        # offset stored in the file is absolute.
        header_size = 0x70
        string_ids_off = header_size
        type_ids_off = string_ids_off + 4 * len(strings)
        proto_ids_off = type_ids_off + 4 * len(types)
        field_ids_off = proto_ids_off + 12 * len(protos)
        method_ids_off = field_ids_off + 8 * len(fields)
        class_defs_off = method_ids_off + 8 * len(methods)
        data_off = class_defs_off + 32 * len(self.classes)

        data = bytearray()
        sections: list[tuple[int, int, int, int]] = []  # (offset, kind, count, item_size)

        # 1. string_data_item
        string_offsets = {}
        first_string = data_off
        for value in strings:
            string_offsets[value] = data_off + len(data)
            data += uleb128(len(value)) + encode_mutf8(value) + b"\x00"
        sections.append((first_string, TYPE_STRING_DATA_ITEM, len(strings), -1))

        def align() -> None:
            while len(data) % 4:
                data.append(0)

        # 2. type_list (prototype parameters), () shares offset 0
        type_lists: dict[tuple[str, ...], int] = {(): 0}
        first_type_list = None
        for _, params in protos:
            if not params or params in type_lists:
                continue
            align()
            if first_type_list is None:
                first_type_list = data_off + len(data)
            type_lists[params] = data_off + len(data)
            data += struct.pack("<I", len(params))
            for param in params:
                data += struct.pack("<H", tindex[param])
            if len(params) % 2:  # uint + 2-byte params: pad to 4-byte alignment
                data += struct.pack("<H", 0)
        if first_type_list is not None:
            sections.append((first_type_list, TYPE_TYPE_LIST, len(type_lists) - 1, 4))

        # 3. code_item per method
        first_code = None
        code_count = 0
        class_data_collected = []
        for cls in self.classes:
            code_offsets: dict[tuple, int] = {}
            for method in cls["methods"]:
                key = (cls["name"], method["name"], method["ret"], method["params"])
                asm: Asm = method["asm"]
                insns = asm.assemble()
                if len(insns) % 2:
                    raise AssertionError("panjang insns harus genap")
                align()
                if first_code is None:
                    first_code = data_off + len(data)
                code_offsets[key] = data_off + len(data)
                data += struct.pack(
                    "<HHHHII",
                    asm.registers,
                    asm.params,
                    asm.outs,
                    0,  # tries_size
                    0,  # debug_info_off
                    len(insns) // 2,
                )
                data += insns
                code_count += 1
            class_data_collected.append(code_offsets)
        if first_code is not None:
            sections.append((first_code, TYPE_CODE_ITEM, code_count, 16))

        # 4. class_data_item per class
        first_class_data = None
        class_data_offsets: list[int] = []
        for cls, code_offsets in zip(self.classes, class_data_collected):
            align()
            if first_class_data is None:
                first_class_data = data_off + len(data)
            class_data_offsets.append(data_off + len(data))

            static_fields = [f for f in cls["fields"] if f["access"] & ACC_STATIC]
            instance_fields = [f for f in cls["fields"] if not f["access"] & ACC_STATIC]
            direct = [m for m in cls["methods"] if m["name"] == "<init>" or m["access"] & (ACC_STATIC | ACC_PRIVATE)]
            virtual = [m for m in cls["methods"] if m not in direct]

            def encode_fields(entries) -> bytes:
                # the entry count for each list is already written above
                blob = bytearray()
                prev = 0
                for entry in sorted(entries, key=lambda e: fkey[(cls["name"], e["name"], e["type"])]):
                    idx = fkey[(cls["name"], entry["name"], entry["type"])]
                    blob += uleb128(idx - prev) + uleb128(entry["access"])
                    prev = idx
                return bytes(blob)

            def encode_methods(entries) -> bytes:
                blob = bytearray()
                prev = 0
                for entry in sorted(entries, key=lambda e: mkey[(cls["name"], e["name"], e["ret"], e["params"])]):
                    key = (cls["name"], entry["name"], entry["ret"], entry["params"])
                    idx = mkey[key]
                    blob += uleb128(idx - prev) + uleb128(entry["access"]) + uleb128(code_offsets[key])
                    prev = idx
                return bytes(blob)

            data += uleb128(len(static_fields))
            data += uleb128(len(instance_fields))
            data += uleb128(len(direct))
            data += uleb128(len(virtual))
            data += encode_fields(static_fields)
            data += encode_fields(instance_fields)
            data += encode_methods(direct)
            data += encode_methods(virtual)
        if first_class_data is not None:
            sections.append((first_class_data, TYPE_CLASS_DATA_ITEM, len(self.classes), -1))

        # 5. map_list occupies the last word-aligned block of the data section
        while len(data) % 4:
            data.append(0)
        data_size = len(data)
        map_off = data_off + data_size

        map_items = [
            (0, TYPE_HEADER_ITEM, 1, 0x70),
            (string_ids_off, TYPE_STRING_ID_ITEM, len(strings), 4),
            (type_ids_off, TYPE_TYPE_ID_ITEM, len(types), 4),
        ]
        if protos:
            map_items.append((proto_ids_off, TYPE_PROTO_ID_ITEM, len(protos), 12))
        if fields:
            map_items.append((field_ids_off, TYPE_FIELD_ID_ITEM, len(fields), 8))
        if methods:
            map_items.append((method_ids_off, TYPE_METHOD_ID_ITEM, len(methods), 8))
        map_items.append((class_defs_off, TYPE_CLASS_DEF_ITEM, len(self.classes), 32))
        for offset, kind, count, size in sections:
            map_items.append((offset, kind, count, size))
        map_items.append((map_off, TYPE_MAP_LIST, 1, -1))
        map_items.sort(key=lambda item: item[0])

        data += struct.pack("<I", len(map_items))
        for offset, kind, count, size in map_items:
            unused = 0 if size < 0 else size
            data += struct.pack("<HHII", kind, unused, count, offset)

        file_size = data_off + len(data)
        out = bytearray(b"dex\n035\x00")
        out += b"\x00" * 4  # checksum (filled below)
        out += b"\x00" * 20  # signature (filled below)
        # Header (0x70 bytes): magic/checksum/signature, then the size+offset
        # pairs in the fixed order string, type, proto, field, method, class.
        out += struct.pack("<IIIIII", file_size, header_size, 0x12345678, 0, 0, map_off)
        out += struct.pack("<IIII", len(strings), string_ids_off, len(types), type_ids_off)
        out += struct.pack("<III", len(protos), proto_ids_off if protos else 0, len(fields))
        out += struct.pack(
            "<IIII",
            field_ids_off if fields else 0,
            len(methods),
            method_ids_off if methods else 0,
            len(self.classes),
        )
        out += struct.pack("<II", class_defs_off if self.classes else 0, data_size)
        out += struct.pack("<I", data_off)
        if len(out) != header_size:  # pragma: no cover - layout regression guard
            raise AssertionError("header DEX tidak 0x70 byte")

        for value in strings:
            out += struct.pack("<I", string_offsets[value])
        for value in types:
            out += struct.pack("<I", sindex[value])
        for ret, params in protos:
            out += struct.pack("<III", sindex[ret], tindex[ret], type_lists[params])
        for owner, name, type_ in fields:
            out += struct.pack("<HHI", tindex[owner], tindex[type_], sindex[name])
        for owner, name, ret, params in methods:
            out += struct.pack("<HHI", tindex[owner], pindex[(ret, params)], sindex[name])
        for cls, class_data_off in zip(self.classes, class_data_offsets):
            out += struct.pack(
                "<IIIIIIII",
                tindex[cls["name"]],
                cls["access"],
                tindex[cls["superclass"]],
                0,  # interfaces_off
                0,  # source_file_idx
                0,  # annotations_off
                0,  # class_data_off (filled just below)
                0,  # static_values_off
            )
            out[-8:-4] = struct.pack("<I", class_data_off)

        out += data
        # Signature covers everything after the signature field; the checksum
        # then covers the signature too, so it must be written second.
        out[12:32] = hashlib.sha1(bytes(out[32:])).digest()
        out[8:12] = struct.pack("<I", zlib.adler32(bytes(out[12:])) & 0xFFFFFFFF)
        if len(out) != file_size:  # pragma: no cover - layout regression guard
            raise AssertionError(f"ukuran DEX salah: {len(out)} != {file_size}")
        return bytes(out)
