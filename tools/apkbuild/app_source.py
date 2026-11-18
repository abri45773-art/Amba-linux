#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Amba companion app, written directly in Dalvik bytecode.

The UI is built in code instead of inflating a binary layout, which keeps the
APK a few kilobytes and the build free of aapt2. Opcodes used here are the
ones :mod:`dex` implements; see FORMAT_SIZE there for the supported formats.
"""
from __future__ import annotations

import struct

from dex import ACC_PUBLIC, Asm, DexFile

# Android framework classes used by the activity.
ACTIVITY = "Lcom/amba/companion/MainActivity;"
BASE_ACTIVITY = "Landroid/app/Activity;"
VIEW = "Landroid/view/View;"
TEXT_VIEW = "Landroid/widget/TextView;"
SCROLL_VIEW = "Landroid/widget/ScrollView;"
LINEAR_LAYOUT = "Landroid/widget/LinearLayout;"
RESOURCES = "Landroid/content/res/Resources;"
METRICS = "Landroid/util/DisplayMetrics;"
CHAR_SEQUENCE = "Ljava/lang/CharSequence;"
BUNDLE = "Landroid/os/Bundle;"

# Opcodes (Dalvik). Keeping them named makes the assembly readable.
NOP = 0x00
MOVE_RESULT_OBJECT = 0x0C
RETURN_VOID = 0x0E
RETURN_OBJECT = 0x11
CONST_4 = 0x12
CONST_16 = 0x13
CONST = 0x14
CONST_HIGH16 = 0x15  # const/high16 with a 21h literal
CONST_STRING = 0x1A
NEW_INSTANCE = 0x22
IGET = 0x52
INVOKE_VIRTUAL = 0x6E
INVOKE_SUPER = 0x6F
INVOKE_DIRECT = 0x70
INVOKE_STATIC = 0x71
DIV_INT = 0x93
MUL_INT = 0x92

# Colours from the Amba palette (0xAARRGGBB as a signed 32-bit int).
COLOR_BG = 0xFF0F172A
COLOR_CARD = 0xFF13233B
COLOR_TEXT = 0xFFE2E8F0
COLOR_MUTED = 0xFF94A3B8
COLOR_ACCENT = 0xFF2DD4BF

# View ids are not inflated from XML, but keeping them unique and stable makes
# it possible to point UI tests at the same numbers later.
ID_CONTENT = 0x7F0A0001
ID_TITLE = 0x7F0A0002
ID_META = 0x7F0A0003
ID_BODY = 0x7F0A0004
ID_FOOTER = 0x7F0A0005


class _Builder:
    """Thin wrappers so the assembly below reads like source code."""

    def __init__(self, dex: DexFile, asm: Asm) -> None:
        self.dex = dex
        self.asm = asm

    def label(self, name: str) -> "_Builder":
        self.asm.label(name)
        return self

    # The method reference must be registered before the instruction is
    # appended: the very next registration changes sorted pool indices, and
    # the assembler reads {"index": ...} when the instruction is created.
    def _invoke_with(self, op, owner, name, params, ret, regs) -> "_Builder":
        ref = self.dex.method_ref(owner, name, params, ret)
        self.asm.ins(op, "35c", regs=regs, index=ref)
        return self

    def invoke(self, owner, name, params, ret, regs) -> "_Builder":
        return self._invoke_with(INVOKE_VIRTUAL, owner, name, params, ret, regs)

    def invoke_super(self, owner, name, params, ret, regs) -> "_Builder":
        return self._invoke_with(INVOKE_SUPER, owner, name, params, ret, regs)

    def invoke_direct(self, owner, name, params, ret, regs) -> "_Builder":
        return self._invoke_with(INVOKE_DIRECT, owner, name, params, ret, regs)

    def invoke_static(self, owner, name, params, ret, regs) -> "_Builder":
        return self._invoke_with(INVOKE_STATIC, owner, name, params, ret, regs)

    def new(self, reg: int, type_: str) -> "_Builder":
        ref = self.dex.type_ref(type_)
        self.asm.ins(NEW_INSTANCE, "21c", a=reg, index=ref)
        return self

    def result(self, reg: int) -> "_Builder":
        self.asm.ins(MOVE_RESULT_OBJECT, "11x", reg=reg)
        return self

    def text(self, reg: int, value: str) -> "_Builder":
        ref = self.dex.string_ref(value)
        self.asm.ins(CONST_STRING, "21c", a=reg, index=ref)
        return self

    def int_(self, reg: int, value: int) -> "_Builder":
        if 0 <= value <= 7:  # const/4 holds a signed 4-bit literal
            self.asm.ins(CONST_4, "11n", reg=reg, value=value)
        elif -32768 <= value <= 32767:
            self.asm.ins(CONST_16, "21s", a=reg, value=value)
        elif value > 0:
            self.asm.ins(CONST, "31i", a=reg, value=value)
        else:  # a full 32-bit constant, e.g. an opaque ARGB colour
            self.asm.ins(CONST, "31i", a=reg, value=value)
        return self

    def float_sp(self, reg: int, value: float) -> "_Builder":
        """Load a float constant (const/high16 when the mantissa allows it)."""
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
        if bits & 0xFFFF:
            # const vAA, #+BBBB0000 only encodes values with 16 zero low bits.
            raise ValueError(f"float {value} tidak dapat dimuat dengan const/high16")
        self.asm.ins(CONST_HIGH16, "21h", a=reg, value=bits >> 16)
        return self

    def returns(self) -> "_Builder":
        self.asm.ins(RETURN_VOID, "10x")
        return self

    def return_object(self, reg: int) -> "_Builder":
        self.asm.ins(RETURN_OBJECT, "11x", reg=reg)
        return self


def _add_text_view(builder: _Builder, layout_reg: int, text: str, size: int, color: int, regs: tuple[int, int, int]) -> None:
    """Create a TextView, style it, and append it to the layout."""
    view_reg, str_reg, float_reg = regs
    builder.new(view_reg, TEXT_VIEW)
    builder.invoke_direct(TEXT_VIEW, "<init>", ("Landroid/content/Context;",), "V", [view_reg, 0])
    builder.text(str_reg, text)
    builder.invoke(TEXT_VIEW, "setText", (CHAR_SEQUENCE,), "V", [view_reg, str_reg])
    builder.invoke(TEXT_VIEW, "setTextSize", ("I", "F"), "V", [view_reg, 2, float_reg])
    builder.int_(str_reg, COLOR_TEXT if color == COLOR_TEXT else color)
    builder.invoke(TEXT_VIEW, "setTextColor", ("I",), "V", [view_reg, str_reg])
    builder.invoke(LINEAR_LAYOUT, "addView", (VIEW,), "V", [layout_reg, view_reg])


def build_activity(
    dex: DexFile,
    *,
    title: str,
    meta: str,
    body: str,
    footer: str,
    small_size: float = 13.0,
    title_size: float = 26.0,
) -> None:
    """Emit MainActivity with a programmatic scrollable screen."""
    cls = dex.add_class(ACTIVITY, BASE_ACTIVITY, ACC_PUBLIC)

    # --- onCreate(Bundle) ------------------------------------------------
    onCreate = _Builder(dex, Asm(registers=3, params=2))
    onCreate.invoke_super(BASE_ACTIVITY, "onCreate", (BUNDLE,), "V", [0, 1])
    onCreate.invoke(ACTIVITY, "buildScreen", (), VIEW, [0])
    onCreate.result(1)
    onCreate.invoke(BASE_ACTIVITY, "setContentView", (VIEW,), "V", [0, 1])
    onCreate.returns()
    dex.add_method(cls, "onCreate", onCreate.asm, (BUNDLE,), "V", ACC_PUBLIC)

    # --- private View buildScreen() --------------------------------------
    # v0 ScrollView, v1 LinearLayout, v2 TextView, v3 metrics, v4 densityDpi,
    # v5..v8 scratch (padding, strings, float constant).
    screen = _Builder(dex, Asm(registers=11, params=1))
    screen.new(0, SCROLL_VIEW)
    screen.invoke_direct(SCROLL_VIEW, "<init>", ("Landroid/content/Context;",), "V", [0, 0])
    screen.new(1, LINEAR_LAYOUT)
    screen.invoke_direct(LINEAR_LAYOUT, "<init>", ("Landroid/content/Context;",), "V", [1, 0])
    screen.int_(2, 1)  # LinearLayout.VERTICAL
    screen.invoke(LINEAR_LAYOUT, "setOrientation", ("I",), "V", [1, 2])

    # padding = densityDpi / 10 ~= 16dp, without touching floats.
    screen.invoke(BASE_ACTIVITY, "getResources", (), RESOURCES, [0])
    screen.result(3)
    screen.invoke(RESOURCES, "getDisplayMetrics", (), METRICS, [3])
    screen.result(3)
    density_ref = dex.field_ref(METRICS, "densityDpi", "I")
    screen.asm.ins(IGET, "22c", a=4, b=3, index=density_ref)
    screen.int_(5, 10)
    screen.asm.ins(DIV_INT, "23x", a=4, b=4, c=5)
    screen.invoke(LINEAR_LAYOUT, "setPadding", ("I", "I", "I", "I"), "V", [1, 4, 4, 4, 4])

    # Title
    screen.new(2, TEXT_VIEW)
    screen.invoke_direct(TEXT_VIEW, "<init>", ("Landroid/content/Context;",), "V", [2, 0])
    screen.text(5, title)
    screen.invoke(TEXT_VIEW, "setText", (CHAR_SEQUENCE,), "V", [2, 5])
    screen.float_sp(6, title_size)
    screen.int_(7, 2)  # TypedValue.COMPLEX_UNIT_SP
    screen.invoke(TEXT_VIEW, "setTextSize", ("I", "F"), "V", [2, 7, 6])
    screen.int_(5, COLOR_ACCENT)
    screen.invoke(TEXT_VIEW, "setTextColor", ("I",), "V", [2, 5])
    screen.invoke(LINEAR_LAYOUT, "addView", (VIEW,), "V", [1, 2])

    # Meta line (version, status)
    screen.new(2, TEXT_VIEW)
    screen.invoke_direct(TEXT_VIEW, "<init>", ("Landroid/content/Context;",), "V", [2, 0])
    screen.text(5, meta)
    screen.invoke(TEXT_VIEW, "setText", (CHAR_SEQUENCE,), "V", [2, 5])
    screen.float_sp(6, small_size)
    screen.int_(7, 2)
    screen.invoke(TEXT_VIEW, "setTextSize", ("I", "F"), "V", [2, 7, 6])
    screen.int_(5, COLOR_MUTED)
    screen.invoke(TEXT_VIEW, "setTextColor", ("I",), "V", [2, 5])
    screen.invoke(LINEAR_LAYOUT, "addView", (VIEW,), "V", [1, 2])

    # Body: the release manifest rendered as text.
    screen.new(2, TEXT_VIEW)
    screen.invoke_direct(TEXT_VIEW, "<init>", ("Landroid/content/Context;",), "V", [2, 0])
    screen.text(5, body)
    screen.invoke(TEXT_VIEW, "setText", (CHAR_SEQUENCE,), "V", [2, 5])
    screen.float_sp(6, 15.0)
    screen.int_(7, 2)
    screen.invoke(TEXT_VIEW, "setTextSize", ("I", "F"), "V", [2, 7, 6])
    screen.int_(5, COLOR_TEXT)
    screen.invoke(TEXT_VIEW, "setTextColor", ("I",), "V", [2, 5])
    screen.invoke(LINEAR_LAYOUT, "addView", (VIEW,), "V", [1, 2])

    # Footer hint
    screen.new(2, TEXT_VIEW)
    screen.invoke_direct(TEXT_VIEW, "<init>", ("Landroid/content/Context;",), "V", [2, 0])
    screen.text(5, footer)
    screen.invoke(TEXT_VIEW, "setText", (CHAR_SEQUENCE,), "V", [2, 5])
    screen.float_sp(6, small_size)
    screen.int_(7, 2)
    screen.invoke(TEXT_VIEW, "setTextSize", ("I", "F"), "V", [2, 7, 6])
    screen.int_(5, COLOR_MUTED)
    screen.invoke(TEXT_VIEW, "setTextColor", ("I",), "V", [2, 5])
    screen.invoke(LINEAR_LAYOUT, "addView", (VIEW,), "V", [1, 2])

    screen.new(2, TEXT_VIEW)  # spacer keeps the text off the bottom edge
    screen.invoke_direct(TEXT_VIEW, "<init>", ("Landroid/content/Context;",), "V", [2, 0])
    screen.invoke(LINEAR_LAYOUT, "addView", (VIEW,), "V", [1, 2])

    # background + scroll wiring
    screen.int_(5, COLOR_BG)
    screen.invoke(SCROLL_VIEW, "setBackgroundColor", ("I",), "V", [0, 5])
    screen.invoke(SCROLL_VIEW, "addView", (VIEW,), "V", [0, 1])
    screen.return_object(0)
    dex.add_method(cls, "buildScreen", screen.asm, (), VIEW, 0x0002)  # private

    _ = (ID_CONTENT, ID_TITLE, ID_META, ID_BODY, ID_FOOTER)  # kept for UI tests
