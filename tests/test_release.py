#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline tests for the release pipeline: APK build, page, notes, archives.

Nothing here needs a phone, the Android SDK, or network access. The APK is
built with tools/apkbuild and then verified independently (structure, v2
signature, content digest, manifest, resource table).
"""
import hashlib
import html.parser
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/apkbuild"
sys.path.insert(0, str(TOOLS))

import verify_apk  # noqa: E402  (import path set up above)
from dex import Asm, DexFile  # noqa: E402

VERSION = (ROOT / "profile/VERSION").read_text().strip()
RELEASE = json.loads((ROOT / "docs/release.json").read_text())


def run(args, cwd=ROOT):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True)


class DexFormatTests(unittest.TestCase):
    """The hand-written DEX writer must stay spec-compliant."""

    def test_uleb128_round_trip(self):
        from dex import uleb128

        cases = {0: b"\x00", 1: b"\x01", 127: b"\x7f", 128: b"\x80\x01", 16384: b"\x80\x80\x01"}
        for value, expected in cases.items():
            self.assertEqual(expected, uleb128(value))
        with self.assertRaises(ValueError):
            uleb128(-1)

    def test_mutf8_encodes_nul_and_bmp(self):
        from dex import encode_mutf8

        self.assertEqual(b"\xc0\x80", encode_mutf8("\x00"))
        self.assertEqual("é".encode(), encode_mutf8("é"))  # 2-byte form
        self.assertEqual(b"\xe2\x82\xac", encode_mutf8("€"))  # 3-byte form
        with self.assertRaises(ValueError):
            encode_mutf8("\U0001F600")  # outside the BMP is not valid DEX 035

    def test_header_layout_and_checksums(self):
        dex = DexFile()
        cls = dex.add_class("Lcom/amba/Probe;", "Ljava/lang/Object;", 0x0001)
        init = Asm(registers=1, params=1)
        init.ins(0x70, "35c", regs=[0], index=dex.method_ref("Ljava/lang/Object;", "<init>", (), "V"))
        init.ins(0x0E, "10x")
        dex.add_method(cls, "<init>", init, (), "V", 0x10001)
        data = dex.build()

        self.assertEqual(b"dex\n035\x00", data[:8])
        (file_size,) = struct.unpack_from("<I", data, 32)
        (header_size,) = struct.unpack_from("<I", data, 36)
        self.assertEqual(len(data), file_size)
        self.assertEqual(0x70, header_size)
        self.assertEqual(hashlib.sha1(data[32:]).digest(), data[12:32])
        import zlib

        self.assertEqual(zlib.adler32(data[12:]) & 0xFFFFFFFF, struct.unpack_from("<I", data, 8)[0])

    def test_unknown_format_is_rejected(self):
        from dex import Asm

        with self.assertRaises(ValueError):
            Asm(1, 0).ins(0x00, "99z", reg=0)

    def test_break_label_resolution(self):
        asm = Asm(registers=1, params=0)
        asm.label("start").ins(0x1A, "21c", a=0, index=0)
        asm.ins(0x0E, "10x")
        self.assertEqual(6, len(asm.assemble()))  # 2 + 2 + 2 bytes


class ApkSignatureFormatTests(unittest.TestCase):
    def test_android_chunked_digest_known_vector(self):
        """Fixed vector catches the former, invalid per-section double hash."""
        sections = [b"a", b"b" * (1024 * 1024 + 3), b"c"]
        self.assertEqual(
            "14ff1ae3f026d28ed7672458f080fe4d58511e0ab5a992748e86afa171bccce8",
            verify_apk.content_digest(sections, "sha256").hex(),
        )


class ApkBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="amba-apk."))
        cls.keystore = cls.tmp / "keys"
        cls.apk = cls.tmp / "amba.apk"
        args = [
            sys.executable,
            str(TOOLS / "build_apk.py"),
            "--output",
            str(cls.apk),
            "--keystore",
            str(cls.keystore),
            "--info",
            str(cls.tmp / "info.json"),
        ]
        cls.build = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
        cls.info = json.loads((cls.tmp / "info.json").read_text()) if (cls.tmp / "info.json").is_file() else {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_build_succeeds(self):
        self.assertEqual(0, self.build.returncode, self.build.stderr)
        self.assertTrue(self.apk.is_file())
        self.assertGreater(self.apk.stat().st_size, 1000)
        self.assertLess(self.apk.stat().st_size, 200 * 1024, "APK seharusnya tetap kecil")

    def test_zip_layout_rules(self):
        apk = self.apk.read_bytes()
        with zipfile.ZipFile(self.apk) as archive:
            self.assertEqual(
                ["AndroidManifest.xml", "classes.dex", "resources.arsc"],
                archive.namelist(),
            )
            info = archive.getinfo("resources.arsc")
            self.assertEqual(zipfile.ZIP_STORED, info.compress_type)
            name_size, extra_size = struct.unpack_from("<HH", apk, info.header_offset + 26)
            data_offset = info.header_offset + 30 + name_size + extra_size
            self.assertEqual(0, data_offset % 4, "data resources.arsc harus 4-byte aligned")
            self.assertIsNone(archive.testzip())

    def test_independent_verification_passes(self):
        info = verify_apk.verify_apk(self.apk)
        self.assertEqual("com.amba.companion", info["package"])
        self.assertEqual(VERSION, info["version_name"])
        self.assertEqual(".MainActivity", info["activity"])
        self.assertEqual(23, info["min_sdk"])
        self.assertEqual(34, info["target_sdk"])
        self.assertEqual("v2", info["signature_scheme"])
        self.assertEqual("Amba Linux", info["resources"]["app_name"])
        self.assertEqual(len(info["certificate_sha256"]), 64)

    def test_cli_verifier_prints_json(self):
        result = run([sys.executable, str(TOOLS / "verify_apk.py"), str(self.apk), "--json"])
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(self.apk.stat().st_size, payload["size"])
        self.assertEqual(hashlib.sha256(self.apk.read_bytes()).hexdigest(), payload["sha256"])

    def test_tampering_is_detected(self):
        """Changing one byte of classes.dex must break the v2 content digest."""
        data = bytearray(self.apk.read_bytes())
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            dex_info = archive.getinfo("classes.dex")
        offset = dex_info.header_offset + len("classes.dex") + 26 + len(dex_info.filename)
        offset += dex_info.compress_size // 2
        data[offset] ^= 0xFF
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.apk"
            broken.write_bytes(bytes(data))
            with self.assertRaises(verify_apk.VerifyError):
                verify_apk.verify_apk(broken)

    def test_resigning_changes_only_the_signature_neighbourhood(self):
        first = self.apk.read_bytes()
        block = verify_apk.parse_signing_block(first)
        sections = verify_apk.split_sections(first, block["start"], block["size"])
        self.assertEqual(len(first), sum(len(section) for section in sections) + block["size"])


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_matches_version_file(self):
        self.assertEqual(VERSION, RELEASE["release"]["version"])

    def test_manifest_describes_every_release_asset(self):
        kinds = {asset["kind"] for asset in RELEASE["assets"]}
        for expected in {"apk", "iso", "zip", "tarball", "image", "sha256"}:
            self.assertIn(expected, kinds)
        names = [asset["file"] for asset in RELEASE["assets"]]
        self.assertEqual(len(names), len(set(names)))
        for asset in RELEASE["assets"]:
            if asset["kind"] != "sha256":
                self.assertIn("{version}", asset["file"])
            self.assertTrue(asset["description"].strip())
            self.assertTrue(asset["requirements"].strip())

    def test_repo_slug_matches_git_remote(self):
        remote = run(["git", "remote", "get-url", "origin"]).stdout.strip()
        if not remote:
            self.skipTest("tidak ada remote origin")
        self.assertIn(RELEASE["project"]["repo"], remote)


class ReleaseScriptTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "dist"
            result = run([sys.executable, "scripts/make-release.py", "--dry-run", "--output-dir", str(target)])
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("tidak ada berkas ditulis", result.stdout)
            self.assertIn(f"amba-android-{VERSION}.apk", result.stdout)
            self.assertIn(f"amba-linux-{VERSION}.iso", result.stdout)
            self.assertFalse(target.exists())

    def test_full_release_build_is_self_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "dist"
            result = run(
                [
                    sys.executable,
                    "scripts/make-release.py",
                    "--output-dir",
                    str(target),
                    "--keystore",
                    str(Path(tmp) / "keys"),
                ]
            )
            self.assertEqual(0, result.returncode, result.stderr)

            sums = (target / "SHA256SUMS").read_text().splitlines()
            self.assertEqual(4, len(sums), sums)
            for line in sums:
                digest, name = line.split("  ")
                self.assertEqual(digest, hashlib.sha256((target / name).read_bytes()).hexdigest())

            manifest = json.loads((target / "release-manifest.json").read_text())
            self.assertEqual(VERSION, manifest["release"]["version"])
            self.assertEqual(4, len(manifest["assets"]))
            self.assertTrue(manifest["release"]["commit"])
            self.assertEqual(RELEASE["project"]["repo"], manifest["project"]["repo"])

            # The APK in the release must pass the independent verifier.
            apk_name = f"amba-android-{VERSION}.apk"
            info = verify_apk.verify_apk(target / apk_name)
            self.assertEqual(manifest["apk"]["sha256"], info["sha256"])

            # The ISO is a real ISO 9660 image with an El Torito UEFI catalog.
            iso_path = target / f"amba-linux-{VERSION}.iso"
            self.assertGreater(iso_path.stat().st_size, 1_000_000)
            self.assertEqual(manifest["iso"]["sha256"], hashlib.sha256(iso_path.read_bytes()).hexdigest())
            import pycdlib

            iso = pycdlib.PyCdlib()
            iso.open(str(iso_path))
            try:
                self.assertIsNotNone(iso.eltorito_boot_catalog)
                for iso_file in ("/EFI/BOOT/BOOTAA64.EFI;1", "/AMBA/VERSION.TXT;1", "/AMBA/RELEASE.JSO;1"):
                    self.assertIsNotNone(iso.get_record(iso_path=iso_file), f"{iso_file} harus ada di ISO")
            finally:
                iso.close()

            # Source archives carry the same file list as the git checkout.
            zip_path = target / f"amba-linux-{VERSION}-source.zip"
            tar_path = target / f"amba-linux-{VERSION}-source.tar.gz"
            prefix = f"amba-linux-{VERSION}"
            tracked = {
                line for line in run(["git", "ls-tree", "-r", "--name-only", "HEAD"]).stdout.splitlines() if line
            }
            with zipfile.ZipFile(zip_path) as archive:
                zip_names = {name.split("/", 1)[1] for name in archive.namelist()}
                archived_readme = archive.read(f"{prefix}/README.md")
                zip_script_mode = archive.getinfo(f"{prefix}/scripts/build-image.sh").external_attr >> 16
            self.assertTrue(zip_names, "arsip source kosong")
            self.assertEqual(tracked, zip_names)
            self.assertNotIn("dist", {name.split("/")[0] for name in zip_names})
            self.assertIn("profile/VERSION", zip_names)
            committed_readme = subprocess.run(
                ["git", "show", "HEAD:README.md"], cwd=ROOT, capture_output=True, check=True
            ).stdout
            self.assertEqual(committed_readme, archived_readme)
            self.assertTrue(zip_script_mode & 0o111, "bit executable hilang dari ZIP")
            with tarfile.open(tar_path) as archive:
                tar_names = {name.split("/", 1)[1] for name in archive.getnames()}
                tar_script_mode = archive.getmember(f"{prefix}/scripts/build-image.sh").mode
            self.assertEqual(zip_names, tar_names)
            self.assertTrue(tar_script_mode & 0o111, "bit executable hilang dari TAR.GZ")

            notes = (target / "release-notes.md").read_text()
            self.assertIn(f"Amba Linux {VERSION}", notes)
            for line in sums:
                self.assertIn(line.split("  ")[1], notes)

    def test_manifest_version_mismatch_is_rejected(self):
        """A release.json that disagrees with profile/VERSION must fail loudly."""
        with tempfile.TemporaryDirectory() as tmp:
            source = json.loads((ROOT / "docs/release.json").read_text())
            source["release"]["version"] = "9.9.9"
            broken = Path(tmp) / "release.json"
            broken.write_text(json.dumps(source))
            code = (
                "import sys,runpy;"
                f"sys.argv=['make-release.py','--dry-run','--manifest','{broken}'];"
                f"runpy.run_path('{ROOT}/scripts/make-release.py', run_name='__main__')"
            )
            result = run([sys.executable, "-c", code])
            self.assertNotEqual(0, result.returncode)
            self.assertIn("profile/VERSION", result.stderr)


class ReleasePageTests(unittest.TestCase):
    def render(self, *args):
        return run([sys.executable, "scripts/render-release-page.py", *args])

    def test_page_matches_manifest_without_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "releases.html"
            result = self.render("--manifest", str(Path(tmp) / "missing.json"), "--output", str(out))
            self.assertEqual(0, result.returncode, result.stderr)
            page = out.read_text()
            self.assertIn(f"Rilis Amba Linux {VERSION}", page)
            self.assertIn("tanpa data build", result.stdout)
            self.assertIn("belum tersedia", page)
            for asset in RELEASE["assets"]:
                self.assertIn(asset["file"].replace("{version}", VERSION), page)

    def test_page_embeds_checksums_from_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build = run(
                [
                    sys.executable,
                    "scripts/make-release.py",
                    "--output-dir",
                    str(dist),
                    "--keystore",
                    str(Path(tmp) / "keys"),
                    "--skip-source",
                ]
            )
            self.assertEqual(0, build.returncode, build.stderr)
            out = Path(tmp) / "releases.html"
            result = self.render("--manifest", str(dist / "release-manifest.json"), "--output", str(out))
            self.assertEqual(0, result.returncode, result.stderr)
            page = out.read_text()
            info = verify_apk.verify_apk(dist / f"amba-android-{VERSION}.apk")
            self.assertIn(info["sha256"], page)
            self.assertIn(info["certificate_sha256"], page)
            self.assertIn("minSdk", page.replace("min_sdk", "minSdk"))
            self.assertIn("with checksum", result.stdout.replace("dengan checksum", "with checksum"))

    def test_check_mode_detects_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "releases.html"
            out.write_text("<html>basi</html>\n")
            result = self.render("--output", str(out), "--check")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("tidak sesuai", result.stderr)


class CommittedPageTests(unittest.TestCase):
    """docs/releases.html is generated; it must never drift from the manifest."""

    def test_committed_page_is_current(self):
        page = (ROOT / "docs/releases.html")
        self.assertTrue(page.is_file(), "docs/releases.html belum dibuat")
        text = page.read_text()
        self.assertIn(f"Rilis Amba Linux {VERSION}", text)
        for asset in RELEASE["assets"]:
            self.assertIn(asset["file"].replace("{version}", VERSION), text)

        class Collector(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
                self.hrefs = []

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                self.hrefs.extend(value for key, value in attrs if key == "href" and value)

        collector = Collector()
        collector.feed(text)
        for tag in ("html", "head", "body", "h1", "h2", "article"):
            self.assertIn(tag, collector.tags)
        self.assertIn(f"releases/download/v{VERSION}", " ".join(collector.hrefs))
        self.assertNotIn("localhost", text)
        self.assertNotIn("http://", text, "halaman harus memakai https")

    def test_linux_and_macos_section_mentions_verification(self):
        text = (ROOT / "docs/releases.html").read_text()
        self.assertIn("sha256sum", text)
        self.assertIn("SHA256SUMS", text)


class PublishScriptTests(unittest.TestCase):
    def test_help_and_option_validation(self):
        result = run(["bash", "scripts/publish-release.sh", "--help"])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--upload-only", result.stdout)

        result = run(["bash", "scripts/publish-release.sh", "--tag", "not a tag", "--yes"])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Tag tidak valid", result.stderr)

    def test_missing_dist_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(
                ["bash", "scripts/publish-release.sh", "--dist", str(Path(tmp) / "nope"), "--yes"]
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("Direktori aset tidak ada", result.stderr)

    def test_script_only_targets_the_declared_repo(self):
        text = (ROOT / "scripts/publish-release.sh").read_text()
        self.assertIn("create_args=(release create", text)
        self.assertIn("upload_args=(release upload", text)
        self.assertIn("--clobber", text)
        self.assertIn("sha256sum -c", text)
        self.assertIn("EXPECTED_REPO", text)
        self.assertIn("--prerelease", text)


if __name__ == "__main__":
    unittest.main()
