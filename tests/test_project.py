#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static/offline tests. These do not claim to boot or build an image."""
import json
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def bash(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", code], cwd=ROOT, text=True, capture_output=True)


class ProfileTests(unittest.TestCase):
    def test_packages_are_arm64_desktop_profile(self):
        lines = (ROOT / "profile/packages.aarch64.txt").read_text().splitlines()
        packages = [line.split("#", 1)[0].strip() for line in lines]
        packages = [p for p in packages if p]
        self.assertEqual(len(packages), len(set(packages)))
        for required in ["linux-aarch64", "plasma-desktop", "sddm", "networkmanager", "mkinitcpio"]:
            self.assertIn(required, packages)
        self.assertNotIn("linux", packages)
        self.assertNotIn("linux-amba", packages)

    def test_release_identity(self):
        text = (ROOT / "profile/rootfs/usr/share/amba/os-release").read_text()
        self.assertIn('ID=amba', text)
        self.assertIn('ID_LIKE=arch', text)
        self.assertIn('PRETTY_NAME="Amba Linux"', text)

    def test_json_and_svg_assets_parse(self):
        for path in (ROOT / "profile/rootfs").rglob("*.json"):
            with self.subTest(path=path):
                json.loads(path.read_text())
        for path in (ROOT / "profile/rootfs").rglob("*.svg"):
            with self.subTest(path=path):
                ET.fromstring(path.read_text())

    def test_no_autologin_or_passwordless_sudo(self):
        sddm = (ROOT / "profile/rootfs/etc/sddm/conf.d/10-amba.conf").read_text()
        self.assertNotIn("Autologin", sddm)
        sudo = (ROOT / "profile/rootfs/etc/sudoers.d/10-wheel").read_text()
        self.assertIn("%wheel ALL=(ALL:ALL) ALL", sudo)
        self.assertNotIn("NOPASSWD", sudo)

    def test_initramfs_is_not_build_host_specific(self):
        config = (ROOT / "profile/rootfs/etc/mkinitcpio.conf").read_text()
        hooks = next(line for line in config.splitlines() if line.startswith("HOOKS="))
        self.assertNotIn("autodetect", hooks)
        self.assertIn("block", hooks)
        self.assertIn("filesystems", hooks)

    def test_networkmanager_uses_resolved(self):
        config = (ROOT / "profile/rootfs/etc/NetworkManager/conf.d/10-amba-dns.conf").read_text()
        self.assertIn("dns=systemd-resolved", config)
        builder = (ROOT / "scripts/build-image.sh").read_text()
        self.assertIn("disable systemd-networkd.service", builder)
        self.assertIn("enable NetworkManager.service", builder)


class HelperTests(unittest.TestCase):
    def test_username_validation(self):
        for name in ["amba", "budi_01", "a-b"]:
            self.assertEqual(0, bash(f'source scripts/lib/common.sh; valid_username "{name}"').returncode)
        for name in ["root", "alarm", "Bad User", "1abc", "", "a;reboot"]:
            self.assertNotEqual(0, bash(f'source scripts/lib/common.sh; valid_username "{name}"').returncode)

    def test_password_hash_validation_rejects_plaintext(self):
        valid = [
            "$6$" + "a" * 16 + "$" + "b" * 86,
            "$6$rounds=656000$" + "a" * 16 + "$" + "b" * 86,
            "$y$j9T$" + "a" * 22 + "$" + "b" * 43,
        ]
        invalid = ["password123", "", "$2b$abcdefghijklmnopqrst", "$6$short", "$6$" + "a" * 16 + "$" + "b" * 10]
        for value in valid + invalid:
            quoted = "'" + value.replace("'", "'\\''") + "'"
            result = bash(f"source scripts/lib/common.sh; valid_password_hash {quoted}")
            self.assertEqual(0 if value in valid else 1, result.returncode, value)

    def test_boot_entry(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        result = bash(f'source scripts/lib/common.sh; render_boot_entry "{uuid}"')
        self.assertEqual(0, result.returncode)
        self.assertIn("linux   /Image", result.stdout)
        self.assertIn("initrd  /initramfs-linux.img", result.stdout)
        self.assertIn(f"root=UUID={uuid} rw rootwait", result.stdout)
        self.assertNotIn("/boot/Image", result.stdout)


class ScriptTests(unittest.TestCase):
    def test_build_dry_run_needs_no_root(self):
        result = subprocess.run(["./scripts/build-image.sh", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("tidak ada perubahan dilakukan", result.stdout)
        self.assertIn("aarch64 / UEFI / KDE Plasma", result.stdout)

    def test_build_rejects_bad_size_before_privileged_work(self):
        result = subprocess.run(["./scripts/build-image.sh", "--size-gib", "1"], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("12..128", result.stderr)

    def test_build_rejects_reserved_user(self):
        result = subprocess.run(["./scripts/build-image.sh", "--user", "root"], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("dicadangkan", result.stderr)

    def test_qemu_dry_run_is_snapshot_and_arm64(self):
        result = subprocess.run(["./scripts/run-qemu.sh", "--image", "example.img", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("qemu-system-aarch64", result.stdout)
        self.assertIn("-snapshot", result.stdout)
        self.assertIn("virtio-blk-pci", result.stdout)
        self.assertNotIn("file=example.img,readonly", result.stdout)

    def test_qemu_persistent_flag(self):
        result = subprocess.run(["./scripts/run-qemu.sh", "--image", "example.img", "--persistent", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("-snapshot", result.stdout)

    def test_builder_has_no_physical_disk_target(self):
        text = (ROOT / "scripts/build-image.sh").read_text()
        self.assertIn('losetup --find --show --partscan "$IMAGE_WORK"', text)
        self.assertIn('sfdisk --quiet "$IMAGE_WORK"', text)
        self.assertNotIn("/dev/sd", text)
        self.assertNotIn("/dev/nvme", text)


if __name__ == "__main__":
    unittest.main()
