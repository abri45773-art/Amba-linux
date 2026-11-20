#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Create the release assets for Amba Linux from this checkout.

Outputs (all deterministic, all checksummed):

    dist/amba-android-<version>.apk              signed companion app
    dist/amba-linux-<version>.iso                prototype UEFI boot-framework ISO
    dist/amba-linux-<version>-source.zip         tracked files at this commit
    dist/amba-linux-<version>-source.tar.gz      same tree, Unix format
    dist/SHA256SUMS                              sha256sum -c compatible
    dist/release-manifest.json                   machine-readable asset list
    dist/release-notes.md                        text for the GitHub Release

The ISO is a boot-framework prototype built by scripts/build-iso.py: it carries
the UEFI boot chain, loader configuration and distro profile, but no kernel,
initramfs or root filesystem. Building it needs pycdlib (`pip install -r
requirements-dev.txt`); pass --skip-iso to omit it.

The disk image is never built here: it requires a native Arch Linux ARM
aarch64 host, so the script only records it as pending.

Examples:
    python3 scripts/make-release.py --dry-run             # print the plan
    python3 scripts/make-release.py --keystore ~/.amba-keys
    python3 scripts/make-release.py --skip-apk --skip-iso
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "profile/VERSION"
MANIFEST_FILE = ROOT / "docs/release.json"
APK_BUILDER = ROOT / "tools/apkbuild/build_apk.py"
APK_VERIFIER = ROOT / "tools/apkbuild/verify_apk.py"
ISO_BUILDER = ROOT / "scripts/build-iso.py"
DIST = ROOT / "dist"

#: Build outputs and local state that must never ship inside a source archive.
EXCLUDED_PATHS = {"dist", ".git", "out", "work", "downloads", "__pycache__", ".venv"}


@dataclass(frozen=True)
class SourceEntry:
    """A blob and its mode as stored in the release commit."""

    path: Path
    data: bytes
    mode: int
    symlink: bool = False


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, **kwargs)


def source_entries(commit: str) -> list[SourceEntry]:
    """Read source bytes from *commit*, never from a possibly dirty worktree."""
    result = subprocess.run(
        ["git", "ls-tree", "-rz", commit],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise SystemExit(f"git ls-tree gagal: {message}")

    entries: list[SourceEntry] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode_text, object_type, object_id = metadata.split(b" ", 2)
        path = Path(raw_path.decode("utf-8"))
        if path.parts[0] in EXCLUDED_PATHS:
            continue
        if object_type != b"blob":
            kind = object_type.decode(errors="replace")
            raise SystemExit(f"tipe git tidak didukung dalam arsip source: {path} ({kind})")
        blob = subprocess.run(
            ["git", "cat-file", "blob", object_id.decode("ascii")],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if blob.returncode != 0:
            message = blob.stderr.decode(errors="replace").strip()
            raise SystemExit(f"git cat-file gagal untuk {path}: {message}")
        git_mode = int(mode_text, 8)
        symlink = git_mode == 0o120000
        mode = 0o777 if symlink else (0o755 if git_mode & 0o111 else 0o644)
        entries.append(SourceEntry(path=path, data=blob.stdout, mode=mode, symlink=symlink))
    return sorted(entries, key=lambda item: item.path.as_posix())


def git_value(*args: str, default: str = "unknown") -> str:
    result = run(["git", *args])
    if result.returncode != 0:
        return default
    return result.stdout.strip() or default


def version() -> str:
    return VERSION_FILE.read_text().strip()


def file_record(path: Path, *, name: str | None = None) -> dict:
    data = path.read_bytes()
    return {
        "file": name or path.name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def load_release(manifest: Path | None = None) -> dict:
    return json.loads((manifest or MANIFEST_FILE).read_text())


def ensure_manifest_version(release: dict, manifest: Path = MANIFEST_FILE) -> None:
    expected = version()
    if release["release"]["version"] != expected:
        raise SystemExit(
            f"{manifest} menyebut versi {release['release']['version']}, "
            f"tetapi profile/VERSION berisi {expected}."
        )


def build_source_zip(target: Path, entries: list[SourceEntry], prefix: str, epoch: int) -> None:
    # ZIP timestamps are restricted to 1980..2107. UTC avoids host timezone
    # differences; clamping protects callers that pass an unusual --epoch.
    timestamp = tuple(_utc(max(315532800, min(epoch, 4354819198))))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in entries:
            info = zipfile.ZipInfo(f"{prefix}/{entry.path.as_posix()}", date_time=timestamp)
            file_type = 0o120000 if entry.symlink else 0o100000
            info.external_attr = ((file_type | entry.mode) & 0xFFFF) << 16
            info.create_system = 3  # Unix, so modes survive extraction
            archive.writestr(
                info,
                entry.data,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def _utc(epoch: int) -> tuple[int, int, int, int, int, int]:
    import time

    return time.gmtime(epoch)[:6]


def build_source_tarball(target: Path, entries: list[SourceEntry], prefix: str, epoch: int) -> None:
    """tar.gz with normalised metadata, matching the ZIP contents."""
    import gzip
    import io

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            info = tarfile.TarInfo(f"{prefix}/{entry.path.as_posix()}")
            info.mtime = epoch
            info.mode = entry.mode
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if entry.symlink:
                info.type = tarfile.SYMTYPE
                info.linkname = entry.data.decode("utf-8")
                info.size = 0
                archive.addfile(info)
            else:
                info.size = len(entry.data)
                archive.addfile(info, io.BytesIO(entry.data))
    with open(target, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch, compresslevel=9) as gz:
            gz.write(buffer.getvalue())


def build_apk(target: Path, keystore: Path, manifest: Path) -> dict:
    args = [
        sys.executable,
        str(APK_BUILDER),
        "--output",
        str(target),
        "--manifest",
        str(manifest),
        "--keystore",
        str(keystore),
    ]
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"build APK gagal:\n{result.stdout}\n{result.stderr}")
    check = subprocess.run(
        [sys.executable, str(APK_VERIFIER), str(target), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if check.returncode != 0:
        raise SystemExit(f"verifikasi APK gagal: {check.stderr.strip()}")
    info = json.loads(check.stdout)
    return info


def build_iso(target: Path, release_version: str) -> dict:
    """Build the prototype UEFI ISO with scripts/build-iso.py (needs pycdlib)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("amba_build_iso", ISO_BUILDER)
    if spec is None or spec.loader is None:
        raise SystemExit(f"tidak dapat memuat {ISO_BUILDER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.generate_iso(target, version=release_version, force=True)
    except ModuleNotFoundError as error:
        raise SystemExit(
            f"build ISO membutuhkan modul {error.name} "
            "(pip install -r requirements-dev.txt) atau jalankan dengan --skip-iso"
        ) from error


def release_notes(
    release: dict,
    records: list[dict],
    commit: str,
    epoch: int,
    has_apk: bool,
    has_iso: bool = False,
) -> str:
    timestamp = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    lines = [
        f"# Amba Linux {release['release']['version']} — {release['release']['status']}",
        "",
        release["release"]["notes"],
        "",
        f"- Tanggal rilis: {release['release']['date']}",
        f"- Commit: `{commit}`",
        f"- Lisensi: {release['project']['license']}",
        f"- Target: {release['project']['architecture']} / {release['project']['firmware']} / {release['project']['desktop']}",
        "",
        "## Aset",
        "",
        "| Berkas | Ukuran | SHA-256 |",
        "|---|---|---|",
    ]
    for record in records:
        size = f"{record['size'] / 1024:.0f} KB" if record["size"] >= 1024 else f"{record['size']} B"
        lines.append(f"| `{record['file']}` | {size} | `{record['sha256'][:16]}…` |")
    lines += [
        "",
        "Verifikasi unduhan:",
        "",
        "```bash",
        "sha256sum -c SHA256SUMS",
        "```",
        "",
        "## Catatan",
        "",
    ]
    if has_apk:
        lines += [
            "- APK ditandatangani kunci **debug** (`CN=Amba Linux Release`), bukan kunci Play Store.",
            "  Pasang dengan mengizinkan sumber tidak dikenal, atau bangun ulang dari source.",
        ]
    else:
        lines.append("- APK tidak disertakan pada build rilis ini.")
    if has_iso:
        lines += [
            "- **ISO prototipe.** `amba-linux-*-.iso` adalah kerangka boot UEFI: katalog El Torito,",
            "  stub `BOOTAA64.EFI`/`BOOTX64.EFI`, konfigurasi systemd-boot/GRUB, profil distro, dan",
            "  dokumentasi. ISO ini **belum** memuat kernel, initramfs, rootfs, atau KDE Plasma,",
            "  sehingga belum bisa boot ke desktop. Image sistem lengkap dibangun di host",
            "  Arch Linux ARM aarch64 dengan `scripts/build-image.sh`.",
        ]
    else:
        lines.append("- ISO tidak disertakan pada build rilis ini (`--skip-iso`).")
    lines += [
        "- Image disk `amba-linux-*-aarch64.img` belum tersedia: image harus dibangun di host",
        "  Arch Linux ARM aarch64 memakai `scripts/build-image.sh`, lihat `docs/BUILD.md`.",
        "- Tes offline tidak membuktikan image dapat boot; lihat `docs/TESTING.md`.",
        f"- Arsip source dibuat dari commit `{commit}` pada {timestamp}.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buat aset rilis Amba Linux")
    parser.add_argument("--output-dir", type=Path, default=DIST)
    parser.add_argument("--keystore", type=Path, default=None, help="direktori kunci APK (debug.key/debug.crt)")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_FILE, help="manifest rilis (default: docs/release.json)")
    parser.add_argument("--skip-apk", action="store_true")
    parser.add_argument("--skip-iso", action="store_true", help="lewati ISO prototipe (butuh pycdlib)")
    parser.add_argument("--skip-source", action="store_true")
    parser.add_argument("--epoch", type=int, default=None, help="timestamp SOURCE_DATE_EPOCH untuk reproduksibilitas")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="timpa aset yang sudah ada")
    args = parser.parse_args(argv)

    if args.skip_apk and args.skip_iso and args.skip_source:
        parser.error("minimal satu aset harus dibangun (APK, ISO, atau source)")

    release = load_release(args.manifest)
    ensure_manifest_version(release, args.manifest)
    version_ = release["release"]["version"]
    commit = git_value("rev-parse", "HEAD")
    short = git_value("rev-parse", "--short=12", "HEAD")
    epoch = args.epoch if args.epoch is not None else int(git_value("log", "-1", "--format=%ct", default="0"))
    entries = [] if args.skip_source else source_entries(commit)
    if entries:
        status = run(["git", "status", "--porcelain", "--untracked-files=all"])
        dirty = [line[3:] for line in status.stdout.splitlines() if len(line) > 3]
        dirty = [name for name in dirty if Path(name.split(" -> ")[-1]).parts[0] not in EXCLUDED_PATHS]
        if dirty:
            print(
                f"catatan: {len(dirty)} perubahan worktree tidak masuk arsip source "
                f"(mis. {dirty[0]}); arsip selalu dibaca dari commit {short}.",
                file=sys.stderr,
            )
    prefix = f"amba-linux-{version_}"

    out_dir = args.output_dir
    planned = []
    if not args.skip_apk:
        planned.append(out_dir / f"amba-android-{version_}.apk")
    if not args.skip_iso:
        planned.append(out_dir / f"{prefix}.iso")
    if not args.skip_source:
        planned.append(out_dir / f"{prefix}-source.zip")
        planned.append(out_dir / f"{prefix}-source.tar.gz")
    planned += [out_dir / "SHA256SUMS", out_dir / "release-manifest.json", out_dir / "release-notes.md"]

    if args.dry_run:
        print(f"RENCANA RILIS Amba Linux {version_} (tidak ada berkas ditulis)")
        print(f"  commit    : {short}")
        print(f"  epoch     : {epoch}")
        print(f"  keluaran  : {out_dir}")
        print(f"  berkas git: {len(entries)} (dibaca dari commit {short})")
        for path in planned:
            print(f"  - {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
        if not args.skip_apk:
            print("  APK       : dibangun dengan tools/apkbuild, lalu diverifikasi (v2)")
        if not args.skip_iso:
            print("  ISO       : kerangka boot UEFI prototipe (tanpa kernel/rootfs)")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    keystore = args.keystore or (out_dir / "keys")
    for path in planned:
        if path.exists() and not args.force:
            raise SystemExit(f"{path} sudah ada; pakai --force untuk menimpa.")

    records: list[dict] = []
    generated: dict[str, dict] = {}

    if not args.skip_apk:
        apk_path = out_dir / f"amba-android-{version_}.apk"
        apk_info = build_apk(apk_path, keystore, args.manifest)
        records.append(file_record(apk_path))
        generated["apk"] = apk_info

    if not args.skip_iso:
        iso_path = out_dir / f"{prefix}.iso"
        iso_info = build_iso(iso_path, version_)
        records.append(file_record(iso_path))
        generated["iso"] = {key: iso_info[key] for key in ("file", "size", "sha256", "volume_id", "version")}

    if not args.skip_source:
        zip_path = out_dir / f"{prefix}-source.zip"
        tar_path = out_dir / f"{prefix}-source.tar.gz"
        build_source_zip(zip_path, entries, prefix, epoch)
        build_source_tarball(tar_path, entries, prefix, epoch)
        records.append(file_record(zip_path))
        records.append(file_record(tar_path))

    sums = out_dir / "SHA256SUMS"
    sums.write_text("".join(f"{record['sha256']}  {record['file']}\n" for record in records))

    manifest = {
        "project": release["project"],
        "release": {**release["release"], "commit": commit, "commit_short": short, "epoch": epoch},
        "assets": records,
        "pending": [
            asset for asset in release["assets"] if asset.get("pending")
        ],
        "apk": generated.get("apk"),
        "iso": generated.get("iso"),
        "build_tool": "scripts/make-release.py",
    }
    (out_dir / "release-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (out_dir / "release-notes.md").write_text(
        release_notes(
            release,
            records,
            short,
            epoch,
            has_apk=not args.skip_apk,
            has_iso=not args.skip_iso,
        )
    )

    print(f"Aset rilis Amba Linux {version_} ditulis ke {out_dir}:")
    for record in records:
        print(f"  {record['file']}  {record['size']} byte  sha256 {record['sha256'][:16]}…")
    print(f"  SHA256SUMS, release-manifest.json, release-notes.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
