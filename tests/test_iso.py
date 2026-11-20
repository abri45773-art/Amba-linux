#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Amba Linux bootable UEFI ISO generator."""
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("build_iso", str(ROOT / "scripts/build-iso.py"))
build_iso = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_iso)

build_efi_stub = build_iso.build_efi_stub
generate_iso = build_iso.generate_iso
get_version = build_iso.get_version
make_fat12_img = build_iso.make_fat12_img
import pycdlib


def run_cmd(args, cwd=ROOT):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True)


class EfiStubTests(unittest.TestCase):
    def test_aarch64_stub_header(self):
        data = build_efi_stub("aarch64")
        self.assertEqual(b"MZ", data[:2])
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        self.assertEqual(b"PE\x00\x00", data[pe_offset : pe_offset + 4])
        machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
        self.assertEqual(0xAA64, machine, "Machine type harus AARCH64 (0xaa64)")
        magic = struct.unpack_from("<H", data, pe_offset + 24)[0]
        self.assertEqual(0x20B, magic, "PE32+ magic harus 0x20b")
        subsystem = struct.unpack_from("<H", data, pe_offset + 24 + 68)[0]
        self.assertEqual(10, subsystem, "Subsystem harus EFI_APPLICATION (10)")

    def test_x86_64_stub_header(self):
        data = build_efi_stub("x86_64")
        self.assertEqual(b"MZ", data[:2])
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        self.assertEqual(b"PE\x00\x00", data[pe_offset : pe_offset + 4])
        machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
        self.assertEqual(0x8664, machine, "Machine type harus AMD64 (0x8664)")


class Fat12ImgTests(unittest.TestCase):
    def test_make_fat12_structure(self):
        files = {
            "TEST.TXT": b"Hello from FAT12",
            "BOOT.EFI": b"EFI payload stub",
        }
        img = make_fat12_img(files)
        self.assertEqual(2880 * 512, len(img), "Ukuran FAT12 image harus 1.44 MB")
        self.assertEqual(b"\x55\xaa", img[510:512], "Boot signature harus 0x55AA")
        self.assertEqual(b"MSDOS5.0", img[3:11])
        self.assertEqual(b"FAT12   ", img[54:62])


class IsoBuildTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "test.iso"
            res = run_cmd([sys.executable, "scripts/build-iso.py", "--dry-run", "--output", str(target)])
            self.assertEqual(0, res.returncode, res.stderr)
            self.assertIn("RENCANA BUILD ISO", res.stdout)
            self.assertFalse(target.exists())

    def test_iso_generation_and_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "amba-test.iso"
            info = generate_iso(target, force=True)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 1024 * 1024)
            self.assertEqual(info["size"], target.stat().st_size)
            self.assertEqual(
                info["sha256"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )

            # Inspect ISO contents with pycdlib
            iso = pycdlib.PyCdlib()
            iso.open(str(target))
            self.assertTrue(iso.has_rock_ridge())
            self.assertTrue(iso.has_joliet())
            self.assertIsNotNone(iso.eltorito_boot_catalog)

            # Check for critical files
            expected_files = [
                "/BOOT/EFI.IMG;1",
                "/BOOT/GRUB/GRUB.CFG;1",
                "/EFI/BOOT/BOOTAA64.EFI;1",
                "/EFI/BOOT/BOOTX64.EFI;1",
                "/LOADER/LOADER.CFG;1",
                "/LOADER/ENTRIES/AMBA.CFG;1",
                "/README.TXT;1",
                "/AMBA/VERSION.TXT;1",
                "/AMBA/RELEASE.JSO;1",
            ]
            for ef in expected_files:
                entry = iso.get_record(iso_path=ef)
                self.assertIsNotNone(entry, f"Berkas {ef} harus ada di dalam ISO")
                self.assertGreater(entry.data_length, 0)

            # The README must not claim this prototype is a complete system.
            readme = io.BytesIO()
            iso.get_file_from_iso_fp(readme, iso_path="/README.TXT;1")
            readme_text = readme.getvalue().decode("utf-8")
            self.assertIn("prototipe", readme_text)
            self.assertIn("Tidak memuat kernel", readme_text)
            self.assertNotIn("Desktop Distribution", readme_text)

            iso.close()

    def test_iso_is_reproducible_with_fixed_timestamp(self):
        """Two builds must match byte-for-byte; pycdlib's clock is pinned."""
        with tempfile.TemporaryDirectory() as tmp:
            first = generate_iso(Path(tmp) / "a.iso", force=True)
            second = generate_iso(Path(tmp) / "b.iso", force=True)
            self.assertEqual(first["sha256"], second["sha256"])
            raw = (Path(tmp) / "a.iso").read_bytes()
            self.assertEqual(b"2026010100000000\x00", raw[16 * 2048 + 813 : 16 * 2048 + 830])
            self.assertEqual(bytes([0x7E, 1, 1, 0, 0, 0, 0]), raw[29 * 2048 + 18 : 29 * 2048 + 25])

    def test_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "existing.iso"
            target.write_bytes(b"dummy")
            with self.assertRaises(FileExistsError):
                generate_iso(target, force=False)
            # With force=True it should succeed
            info = generate_iso(target, force=True)
            self.assertTrue(target.stat().st_size > 1000)


if __name__ == "__main__":
    unittest.main()
