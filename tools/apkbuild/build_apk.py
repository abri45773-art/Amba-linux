#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the Amba companion APK without Gradle, the SDK, or network access.

Pipeline: resources.arsc -> binary AndroidManifest.xml -> classes.dex ->
deterministic ZIP -> APK Signature Scheme v2 signature (openssl backend).

Usage:
    python3 tools/apkbuild/build_apk.py --output out/amba-android-0.1.0.apk
    python3 tools/apkbuild/build_apk.py --output app.apk --keystore DIR

Without ``--keystore`` a throwaway debug key pair is generated with openssl in
a temporary directory, so the checksum of the APK is only reproducible when
the same key is passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import axml
import signing
import zipwriter
from app_source import build_activity
from arsc import ResourceTable
from dex import DexFile

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs/release.json"

PACKAGE = "com.amba.companion"
MIN_SDK = 23
TARGET_SDK = 34
VERSION_CODE = 1


def load_release(path: Path) -> dict:
    data = json.loads(path.read_text())
    for key in ("project", "release", "assets"):
        if key not in data:
            raise SystemExit(f"{path}: kunci '{key}' tidak ada")
    return data


def version_label(release: dict) -> str:
    rel = release["release"]
    return f"{rel['version']} ({rel['status']})"


def asset_lines(release: dict) -> list[str]:
    lines = []
    for asset in release["assets"]:
        mark = "(belum tersedia)" if asset.get("pending") else ""
        lines.append(f"- {asset['label']} {mark}".rstrip())
        lines.append(f"  {asset['file']}  ~ {asset['size_hint']}")
    return lines


def build_resources(release: dict) -> tuple[bytes, dict[str, int]]:
    table = ResourceTable(PACKAGE)
    ids = {
        "app_name": table.add_string("app_name", "Amba Linux"),
        "app_version": table.add_string("app_version", version_label(release)),
        "app_summary": table.add_string(
            "app_summary", "Pendamping rilis Amba Linux untuk perangkat ARM64 UEFI"
        ),
        "release_notes": table.add_string("release_notes", release["release"]["notes"]),
    }
    return table.build(), ids


def build_manifest(res_id_app_name: int, version_name: str) -> bytes:
    manifest = axml.Element("manifest")
    manifest.attr("package", axml.RawString(PACKAGE))
    manifest.attr("android__versionCode", VERSION_CODE)
    manifest.attr("android__versionName", axml.RawString(version_name))
    manifest.attr("android__compileSdkVersion", TARGET_SDK)
    manifest.attr("android__compileSdkVersionCodename", axml.RawString("14"))

    uses_sdk = manifest.child("uses-sdk")
    uses_sdk.attr("android__minSdkVersion", MIN_SDK)
    uses_sdk.attr("android__targetSdkVersion", TARGET_SDK)

    application = manifest.child("application")
    application.attr("android__label", axml.Reference(res_id_app_name))
    application.attr("android__allowBackup", False)
    application.attr("android__hasCode", True)
    application.attr("android__theme", axml.RawString("@android:style/Theme.Material.NoActionBar"))
    application.attr("android__extractNativeLibs", False)

    activity = application.child("activity")
    activity.attr("android__name", axml.RawString(".MainActivity"))
    activity.attr("android__exported", True)
    activity.attr("android__label", axml.Reference(res_id_app_name))

    intent = activity.child("intent-filter")
    action = intent.child("action")
    action.attr("android__name", axml.RawString("android.intent.action.MAIN"))
    category = intent.child("category")
    category.attr("android__name", axml.RawString("android.intent.category.LAUNCHER"))

    return axml.build_manifest(manifest)


def build_dex(release: dict) -> bytes:
    project = release["project"]
    dex = DexFile()
    build_activity(
        dex,
        title=f"{project['name']} {version_label(release)}",
        meta=f"{project['architecture']} / {project['firmware']} / {project['desktop']}",
        body="\n".join(
            [
                release["release"]["notes"],
                "",
                "Aset rilis:",
                *asset_lines(release),
            ]
        ),
        footer="Amba Linux - AGPL-3.0-or-later. Aplikasi ini hanya menampilkan "
        "informasi rilis; image disk tetap harus dibangun di host Arch Linux ARM.",
    )
    return dex.build()


def build_apk(release: dict, keystore: Path | None, algorithm: str = "rsa2048") -> tuple[bytes, dict]:
    version_name = release["release"]["version"]
    arsc, ids = build_resources(release)
    manifest = build_manifest(ids["app_name"], version_name)
    dex_bytes = build_dex(release)

    writer = zipwriter.ZipWriter()
    writer.add("AndroidManifest.xml", manifest)
    writer.add("classes.dex", dex_bytes)
    writer.add("resources.arsc", arsc)
    unsigned = writer.build()

    if keystore is None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="amba-keys.") as tmp:
            key, cert = signing.generate_key_pair(Path(tmp), algorithm)
            signed = signing.sign_v2(unsigned, key, cert, algorithm)
    else:
        key = keystore / "debug.key"
        cert = keystore / "debug.crt"
        if not key.exists() or not cert.exists():
            signing.generate_key_pair(keystore, algorithm)
        signed = signing.sign_v2(unsigned, key, cert, algorithm)

    info = {
        "package": PACKAGE,
        "version_name": version_name,
        "version_code": VERSION_CODE,
        "min_sdk": MIN_SDK,
        "target_sdk": TARGET_SDK,
        "signature": "APK Signature Scheme v2",
        "algorithm": algorithm,
        "dex_size": len(dex_bytes),
        "arsc_size": len(arsc),
        "manifest_size": len(manifest),
        "unsigned_size": len(unsigned),
        "size": len(signed),
        "sha256": hashlib.sha256(signed).hexdigest(),
    }
    return signed, info


def verify(apk: bytes) -> None:
    """Fail the build if the archive or its signature block is malformed."""
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(apk)) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise SystemExit(f"APK rusak pada entri {bad}")
        names = archive.namelist()
        for required in ("AndroidManifest.xml", "classes.dex", "resources.arsc"):
            if required not in names:
                raise SystemExit(f"entri wajib hilang: {required}")
        info = archive.getinfo("resources.arsc")
        if info.compress_type != zipfile.ZIP_STORED:
            raise SystemExit("resources.arsc harus disimpan tanpa kompresi")
    signing.verify_structure(apk)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Amba companion APK")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--keystore", type=Path, default=None)
    parser.add_argument("--algorithm", choices=sorted(signing.ALGORITHMS), default="rsa2048")
    parser.add_argument("--info", type=Path, default=None, help="tulis metadata build sebagai JSON")
    args = parser.parse_args(argv)

    release = load_release(args.manifest)
    apk, info = build_apk(release, args.keystore, args.algorithm)
    verify(apk)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(apk)
    if args.info:
        args.info.parent.mkdir(parents=True, exist_ok=True)
        args.info.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    print(f"APK  : {args.output} ({info['size']} byte)")
    print(f"SHA-256: {info['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
