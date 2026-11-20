#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the prototype UEFI ISO image for Amba Linux.

Creates an ISO 9660 image with an El Torito UEFI boot catalog, Rock Ridge and
Joliet extensions. The image is a **boot-framework prototype**, not a complete
operating system: it ships the UEFI boot chain and the distro profile, but it
does not contain a Linux kernel, an initramfs, a root filesystem or KDE Plasma.
A full system ISO can only be produced on a native Arch Linux ARM aarch64 host
(see docs/BUILD.md).

Contents:
  - UEFI bootloaders: /EFI/BOOT/BOOTAA64.EFI (ARM64) and /EFI/BOOT/BOOTX64.EFI (x86_64)
  - El Torito EFI boot image: /boot/efi.img (FAT12 filesystem)
  - Bootloader configurations: /loader/loader.conf, /loader/entries/amba.conf, /boot/grub/grub.cfg
  - Distro profile: /amba/profile/, /amba/VERSION, /amba/hostname, /amba/packages.aarch64.txt
  - Release manifest: /amba/release.json
  - Helper scripts: /amba/scripts/
  - Documentation: /docs/
  - Android companion APK: /amba/amba-android-<version>.apk (if available)
  - Prototype notice: /README.txt

Usage:
    python3 scripts/build-iso.py
    python3 scripts/build-iso.py --dry-run
    python3 scripts/build-iso.py --output dist/amba-linux-0.1.0.iso --force
"""
from __future__ import annotations

import argparse
import hashlib
import io
import struct
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "profile/VERSION"
#: Fixed timestamp used for every ISO structure (2026-01-01T00:00:00Z).
ISO_EPOCH = 1767225600
DIST_DIR = ROOT / "dist"
OUT_DIR = ROOT / "out"


def get_version() -> str:
    if VERSION_FILE.is_file():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.1.0"


def build_efi_stub(arch: str = "aarch64") -> bytes:
    """Generate a minimal valid PE32+ EFI application stub."""
    machine = 0xAA64 if arch == "aarch64" else 0x8664
    # Assembly: returns EFI_SUCCESS (0)
    # AARCH64: mov x0, #0; ret
    # x86_64:  xor eax, eax; ret
    code = b"\x00\x00\x80\xd2\xc0\x03\x5f\xd6" if arch == "aarch64" else b"\x31\xc0\xc3"
    code = code.ljust(512, b"\x00")

    dos_header = bytearray(128)
    dos_header[0:2] = b"MZ"
    struct.pack_into("<I", dos_header, 0x3C, 0x80)

    pe_sig = b"PE\x00\x00"
    coff = struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, 0xF0, 0x0022)

    # Standard fields (24 bytes for PE32+)
    opt_standard = struct.pack("<HBBIIIII", 0x20B, 1, 0, len(code), 0, 0, 0x1000, 0x1000)
    # Windows-specific fields (88 bytes for PE32+)
    opt_windows = struct.pack(
        "<QIIHHHHHHIIIIHHQQQQII",
        0x00400000,  # ImageBase
        0x1000,      # SectionAlignment
        0x200,       # FileAlignment
        0, 0,        # OS Major, Minor
        0, 0,        # Image Major, Minor
        0, 0,        # Subsystem Major, Minor
        0,           # Win32VersionValue
        0x2000,      # SizeOfImage
        0x400,       # SizeOfHeaders
        0,           # CheckSum
        10,          # Subsystem = EFI Application (10)
        0,           # DllCharacteristics
        0, 0, 0, 0,  # Stack/Heap reserve/commit
        0, 16,       # LoaderFlags, NumberOfRvaAndSizes
    )
    opt_data_dirs = b"\x00" * (16 * 8)
    opt_header = opt_standard + opt_windows + opt_data_dirs

    sec_name = b".text\x00\x00\x00"
    sec_header = struct.pack(
        "<8sIIIIIIHHI",
        sec_name,
        len(code),
        0x1000,
        512,
        0x400,
        0, 0, 0, 0,
        0x60000020,  # IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ
    )

    headers = dos_header + pe_sig + coff + opt_header + sec_header
    headers = headers.ljust(0x400, b"\x00")
    return bytes(headers + code)


def make_fat12_img(files: dict[str, bytes]) -> bytes:
    """Create a minimal 1.44 MB FAT12 disk image containing *files* for El Torito UEFI boot."""
    sec_size = 512
    tot_sec = 2880
    rsvd_sec = 1
    num_fats = 2
    root_ent = 224
    fat_sec = 9
    root_sec = (root_ent * 32 + sec_size - 1) // sec_size
    data_start = rsvd_sec + num_fats * fat_sec + root_sec

    img = bytearray(tot_sec * sec_size)

    # BPB
    img[0:3] = b"\xeb\x3c\x90"
    img[3:11] = b"MSDOS5.0"
    struct.pack_into(
        "<HBHBHHBHHHII",
        img,
        11,
        sec_size, 1, rsvd_sec, num_fats, root_ent,
        tot_sec, 0xF0, fat_sec, 18, 2, 0, 0,
    )
    img[36:38] = b"\x00\x00"
    img[38] = 0x29
    img[39:43] = b"\x12\x34\x56\x78"
    img[43:54] = b"AMBA_EFI   "
    img[54:62] = b"FAT12   "
    img[510:512] = b"\x55\xaa"

    fat = bytearray(fat_sec * sec_size)
    fat[0:3] = b"\xf0\xff\xff"

    def set_fat12(cluster_num: int, val: int) -> None:
        offset = (cluster_num * 3) // 2
        if cluster_num % 2 == 0:
            fat[offset] = (fat[offset] & 0x00) | (val & 0xFF)
            fat[offset + 1] = (fat[offset + 1] & 0xF0) | ((val >> 8) & 0x0F)
        else:
            fat[offset] = (fat[offset] & 0x0F) | ((val << 4) & 0xF0)
            fat[offset + 1] = (fat[offset + 1] & 0x00) | ((val >> 4) & 0xFF)

    cluster = 2
    root_dir_offset = (rsvd_sec + num_fats * fat_sec) * sec_size
    ent_idx = 0

    for name, data in files.items():
        parts = name.upper().split(".")
        base = parts[0].ljust(8)[:8].encode("ascii", errors="replace")
        ext = (parts[1] if len(parts) > 1 else "").ljust(3)[:3].encode("ascii", errors="replace")

        ent_offset = root_dir_offset + ent_idx * 32
        ent_idx += 1
        img[ent_offset : ent_offset + 8] = base
        img[ent_offset + 8 : ent_offset + 11] = ext
        img[ent_offset + 11] = 0x20  # archive
        struct.pack_into("<HHHI", img, ent_offset + 22, 0, 0, cluster, len(data))

        needed_clusters = (len(data) + sec_size - 1) // sec_size
        for i in range(needed_clusters):
            curr_cl = cluster + i
            is_last = i == needed_clusters - 1
            next_cl = 0xFFF if is_last else curr_cl + 1
            set_fat12(curr_cl, next_cl)
            start_b = (data_start + curr_cl - 2) * sec_size
            chunk = data[i * sec_size : (i + 1) * sec_size]
            img[start_b : start_b + len(chunk)] = chunk

        cluster += needed_clusters

    img[512 : 512 + len(fat)] = fat
    img[512 + len(fat) : 512 + 2 * len(fat)] = fat
    return bytes(img)


class _FixedClock:
    """Stand-in for the ``time`` module that always reports *epoch*."""

    def __init__(self, epoch: int) -> None:
        self.epoch = epoch

    def time(self) -> float:
        return float(self.epoch)

    def __getattr__(self, name: str):
        import time

        return getattr(time, name)


@contextmanager
def fixed_timestamp(epoch: int):
    """Pin pycdlib's clock so identical inputs give byte-identical media.

    pycdlib stamps volume descriptors, directory records and Rock Ridge TF
    entries with ``time.time()``. Swapping that module attribute for the
    duration of the build makes the ISO reproducible without touching global
    state outside pycdlib.
    """
    import pycdlib.headervd
    import pycdlib.pycdlib

    modules = (pycdlib.headervd, pycdlib.pycdlib)
    clock = _FixedClock(epoch)
    original = [(module, module.time) for module in modules]
    try:
        for module in modules:
            module.time = clock
        yield
    finally:
        for module, value in original:
            module.time = value


def generate_iso(
    output_path: Path,
    version: str | None = None,
    force: bool = False,
    epoch: int = ISO_EPOCH,
) -> dict:
    """Build the prototype ISO and write it to *output_path*."""
    with fixed_timestamp(epoch):
        return _build_iso(output_path, version=version, force=force)


def _build_iso(
    output_path: Path,
    version: str | None = None,
    force: bool = False,
) -> dict:
    import pycdlib

    ver = version or get_version()
    vol_id = f"AMBA_{ver.replace('.', '')}"[:32]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        raise FileExistsError(f"Berkas {output_path} sudah ada; gunakan --force untuk menimpa.")

    # 1. Prepare EFI stubs
    efi_arm64 = build_efi_stub("aarch64")
    efi_x64 = build_efi_stub("x86_64")

    # 2. Prepare FAT12 El Torito boot image
    fat_img = make_fat12_img({
        "BOOTAA64.EFI": efi_arm64,
        "BOOTX64.EFI": efi_x64,
    })

    # 3. Bootloader configurations
    loader_conf = b"default  amba.conf\ntimeout  3\nconsole-mode max\n"
    amba_entry = (
        f"title   Amba Linux {ver} (ARM64 UEFI)\n"
        "linux   /boot/Image\n"
        "initrd  /boot/initramfs-linux.img\n"
        f"options root=LABEL={vol_id} rw quiet console=tty0 console=ttyAMA0,115200\n"
    ).encode("utf-8")

    grub_cfg = (
        'set default="0"\n'
        "set timeout=5\n\n"
        f'menuentry "Amba Linux {ver} (ARM64 UEFI Live)" {{\n'
        f"    linux /boot/Image root=LABEL={vol_id} rw quiet console=tty0 console=ttyAMA0,115200\n"
        "    initrd /boot/initramfs-linux.img\n"
        "}\n\n"
        f'menuentry "Amba Linux {ver} (Recovery / Safe Mode)" {{\n'
        f"    linux /boot/Image root=LABEL={vol_id} rw single console=tty0 console=ttyAMA0,115200\n"
        "    initrd /boot/initramfs-linux.img\n"
        "}\n"
    ).encode("utf-8")

    # 4. User documentation & README inside ISO
    readme_text = (
        f"==========================================================\n"
        f"Amba Linux {ver} — ISO prototipe kerangka boot UEFI (ARM64)\n"
        f"==========================================================\n\n"
        f"Versi     : {ver}\n"
        f"Arsitektur: aarch64 (ARM64)\n"
        f"Firmware  : UEFI 64-bit\n"
        f"Desktop   : KDE Plasma (target; belum ada di ISO ini)\n\n"
        f"PERINGATAN PENTING\n"
        f"------------------\n"
        f"ISO ini adalah KERANGKA BOOT (prototipe), bukan sistem operasi\n"
        f"lengkap dan belum bisa di-boot ke desktop:\n"
        f"- Tidak memuat kernel Linux (/boot/Image) maupun initramfs.\n"
        f"- Tidak memuat root filesystem atau KDE Plasma.\n"
        f"- BOOTAA64.EFI adalah stub PE32+ minimal yang langsung keluar,\n"
        f"  sehingga firmware akan melanjutkan urutan boot berikutnya.\n"
        f"- Entri menu systemd-boot/GRUB menunjuk /boot/Image yang belum ada.\n\n"
        f"Image sistem lengkap harus dibangun di host Arch Linux ARM aarch64\n"
        f"dengan scripts/build-image.sh (lihat docs/BUILD.md). ISO ini berguna\n"
        f"untuk memeriksa struktur boot, katalog El Torito, konfigurasi loader,\n"
        f"dan profil distro sebelum build nyata dilakukan.\n\n"
        f"ISI ISO\n"
        f"-------\n"
        f"- /EFI/BOOT/BOOTAA64.EFI, /EFI/BOOT/BOOTX64.EFI  stub bootloader UEFI\n"
        f"- /boot/efi.img         citra FAT12 untuk El Torito (platform EFI)\n"
        f"- /loader/, /boot/grub/ konfigurasi systemd-boot dan GRUB\n"
        f"- /amba/                VERSION, hostname, profil paket, release.json,\n"
        f"                        dan skrip build/QEMU\n"
        f"- /docs/                dokumentasi proyek (build, boot, rilis, uji)\n\n"
        f"PEMERIKSAAN CEPAT\n"
        f"-----------------\n"
        f"1. Verifikasi checksum dari halaman rilis GitHub:\n"
        f"   sha256sum -c SHA256SUMS        # SHA256SUMS diunduh terpisah\n"
        f"2. Periksa struktur ISO tanpa menulis ke media:\n"
        f"   python3 -c \"import pycdlib; i=pycdlib.PyCdlib(); i.open('amba-linux-{ver}.iso'); "
        f"print(i.list_children(iso_path='/'))\"\n"
        f"3. Untuk build sistem lengkap, ikuti docs/BUILD.md di host Arch Linux\n"
        f"   ARM aarch64, lalu uji dengan scripts/run-qemu.sh.\n"
    ).encode("utf-8")


    # 5. Initialize ISO with Joliet and Rock Ridge. Everything below runs under
    # a fixed clock so repeated builds are byte-identical.
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident=vol_id)

    # Directories
    dirs = [
        ("/BOOT", "boot", "/boot"),
        ("/BOOT/GRUB", "grub", "/boot/grub"),
        ("/EFI", "EFI", "/EFI"),
        ("/EFI/BOOT", "BOOT", "/EFI/BOOT"),
        ("/LOADER", "loader", "/loader"),
        ("/LOADER/ENTRIES", "entries", "/loader/entries"),
        ("/AMBA", "amba", "/amba"),
        ("/AMBA/PROFILE", "profile", "/amba/profile"),
        ("/AMBA/SCRIPTS", "scripts", "/amba/scripts"),
        ("/DOCS", "docs", "/docs"),
    ]
    for iso_dir, rr_dir, joliet_dir in dirs:
        iso.add_directory(iso_dir, rr_name=rr_dir, joliet_path=joliet_dir)

    # El Torito boot image
    iso.add_fp(
        io.BytesIO(fat_img),
        len(fat_img),
        "/BOOT/EFI.IMG;1",
        rr_name="efi.img",
        joliet_path="/boot/efi.img",
    )
    iso.add_eltorito("/BOOT/EFI.IMG;1", efi=True, platform_id=0xEF)

    # Bootloaders
    iso.add_fp(
        io.BytesIO(efi_arm64),
        len(efi_arm64),
        "/EFI/BOOT/BOOTAA64.EFI;1",
        rr_name="bootaa64.efi",
        joliet_path="/EFI/BOOT/BOOTAA64.EFI",
    )
    iso.add_fp(
        io.BytesIO(efi_x64),
        len(efi_x64),
        "/EFI/BOOT/BOOTX64.EFI;1",
        rr_name="bootx64.efi",
        joliet_path="/EFI/BOOT/BOOTX64.EFI",
    )

    # Config files
    iso.add_fp(
        io.BytesIO(loader_conf),
        len(loader_conf),
        "/LOADER/LOADER.CFG;1",
        rr_name="loader.conf",
        joliet_path="/loader/loader.conf",
    )
    iso.add_fp(
        io.BytesIO(amba_entry),
        len(amba_entry),
        "/LOADER/ENTRIES/AMBA.CFG;1",
        rr_name="amba.conf",
        joliet_path="/loader/entries/amba.conf",
    )
    iso.add_fp(
        io.BytesIO(grub_cfg),
        len(grub_cfg),
        "/BOOT/GRUB/GRUB.CFG;1",
        rr_name="grub.cfg",
        joliet_path="/boot/grub/grub.cfg",
    )

    # Root README.txt
    iso.add_fp(
        io.BytesIO(readme_text),
        len(readme_text),
        "/README.TXT;1",
        rr_name="README.txt",
        joliet_path="/README.txt",
    )

    # Amba Distro Metadata
    version_bytes = ver.encode("utf-8") + b"\n"
    iso.add_fp(
        io.BytesIO(version_bytes),
        len(version_bytes),
        "/AMBA/VERSION.TXT;1",
        rr_name="VERSION",
        joliet_path="/amba/VERSION",
    )

    hostname_file = ROOT / "profile/hostname"
    if hostname_file.is_file():
        h_bytes = hostname_file.read_bytes()
        iso.add_fp(
            io.BytesIO(h_bytes),
            len(h_bytes),
            "/AMBA/HOSTNAME.TXT;1",
            rr_name="hostname",
            joliet_path="/amba/hostname",
        )

    pkg_file = ROOT / "profile/packages.aarch64.txt"
    if pkg_file.is_file():
        p_bytes = pkg_file.read_bytes()
        iso.add_fp(
            io.BytesIO(p_bytes),
            len(p_bytes),
            "/AMBA/PROFILE/PACKAGES.TXT;1",
            rr_name="packages.aarch64.txt",
            joliet_path="/amba/profile/packages.aarch64.txt",
        )

    release_manifest = ROOT / "docs/release.json"
    if release_manifest.is_file():
        r_bytes = release_manifest.read_bytes()
        iso.add_fp(
            io.BytesIO(r_bytes),
            len(r_bytes),
            "/AMBA/RELEASE.JSO;1",
            rr_name="release.json",
            joliet_path="/amba/release.json",
        )

    # Companion Android APK if present in dist/
    apk_candidates = list((ROOT / "dist").glob("amba-android-*.apk"))
    if apk_candidates:
        apk_bytes = apk_candidates[0].read_bytes()
        iso.add_fp(
            io.BytesIO(apk_bytes),
            len(apk_bytes),
            "/AMBA/AMBA_APP.APK;1",
            rr_name=apk_candidates[0].name,
            joliet_path=f"/amba/{apk_candidates[0].name}",
        )

    # Key documentation files
    import re

    for doc in sorted((ROOT / "docs").glob("*.md")):
        doc_data = doc.read_bytes()
        clean_stem = re.sub(r"[^A-Z0-9_]", "_", doc.stem.upper())[:8]
        iso.add_fp(
            io.BytesIO(doc_data),
            len(doc_data),
            f"/DOCS/{clean_stem}.MD;1",
            rr_name=doc.name,
            joliet_path=f"/docs/{doc.name}",
        )

    # Scripts
    for script_name in ("build-image.sh", "run-qemu.sh"):
        script_file = ROOT / "scripts" / script_name
        if script_file.is_file():
            s_bytes = script_file.read_bytes()
            clean_stem = re.sub(r"[^A-Z0-9_]", "_", script_file.stem.upper())[:8]
            iso.add_fp(
                io.BytesIO(s_bytes),
                len(s_bytes),
                f"/AMBA/SCRIPTS/{clean_stem}.SH;1",
                rr_name=script_name,
                joliet_path=f"/amba/scripts/{script_name}",
            )

    # Write output ISO
    iso.write(str(output_path))
    iso.close()

    raw_data = output_path.read_bytes()
    sha256 = hashlib.sha256(raw_data).hexdigest()
    size = len(raw_data)

    return {
        "file": output_path.name,
        "path": str(output_path),
        "size": size,
        "sha256": sha256,
        "volume_id": vol_id,
        "version": ver,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bangun image ISO bootable UEFI untuk Amba Linux")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DIST_DIR / f"amba-linux-{get_version()}.iso",
        help="jalur berkas keluaran ISO (default: dist/amba-linux-<version>.iso)",
    )
    parser.add_argument("--version", type=str, default=None, help="override versi (default dari profile/VERSION)")
    parser.add_argument("--dry-run", action="store_true", help="tampilkan rencana build tanpa menulis berkas")
    parser.add_argument("--force", "-f", action="store_true", help="timpa berkas keluaran jika sudah ada")
    args = parser.parse_args(argv)

    ver = args.version or get_version()
    vol_id = f"AMBA_{ver.replace('.', '')}"[:32]

    if args.dry_run:
        print(f"RENCANA BUILD ISO Amba Linux {ver}:")
        print(f"  Volume ID   : {vol_id}")
        print(f"  Keluaran    : {args.output}")
        print("  Struktur    :")
        print("    - /EFI/BOOT/BOOTAA64.EFI (UEFI ARM64 stub)")
        print("    - /EFI/BOOT/BOOTX64.EFI  (UEFI x86_64 stub)")
        print("    - /boot/efi.img          (FAT12 El Torito UEFI bootable image)")
        print("    - /loader/loader.conf, /loader/entries/amba.conf")
        print("    - /boot/grub/grub.cfg")
        print("    - /amba/profile/, /amba/release.json, /amba/scripts/")
        print("    - /docs/ & /README.txt")
        print("  Catatan     : prototipe; tanpa kernel, initramfs, rootfs, atau desktop")
        print("  Status      : Berhasil (dry-run, tidak ada berkas ditulis)")
        return 0

    print(f"Membangun ISO bootable UEFI Amba Linux {ver}...")
    info = generate_iso(args.output, version=ver, force=args.force)

    # Also place a copy in out/ if output was in dist/
    if args.output.parent == DIST_DIR:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_copy = OUT_DIR / args.output.name
        out_copy.write_bytes(args.output.read_bytes())

    # Write a dedicated checksum file
    chk_file = args.output.with_suffix(".iso.sha256")
    chk_file.write_text(f"{info['sha256']}  {info['file']}\n", encoding="utf-8")

    size_mb = info["size"] / (1024 * 1024)
    print(f"ISO berhasil dibuat:")
    print(f"  Berkas   : {info['path']}")
    print(f"  Ukuran   : {info['size']} byte ({size_mb:.2f} MB)")
    print(f"  SHA-256  : {info['sha256']}")
    print(f"  Volume ID: {info['volume_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
