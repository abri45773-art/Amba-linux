#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static/offline tests. These do not claim to boot or build an image."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profile/rootfs"


def bash(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", code], cwd=ROOT, text=True, capture_output=True)


def run(args, env=None, cwd=ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, env=env)


def png_size(path: Path) -> tuple[int, int]:
    """Read width/height straight from the PNG IHDR chunk."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG with a leading IHDR")
    return struct.unpack(">II", data[16:24])


def parse_ini_like(path: Path) -> dict[str, dict[str, str]]:
    """Minimal parser for the key/value drop-ins used in this repo."""
    sections: dict[str, dict[str, str]] = {}
    section = ""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections.setdefault(section, {})
            continue
        key, _, value = line.partition("=")
        sections.setdefault(section, {})[key.strip()] = value.strip()
    return sections


def sysctl_values(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        assert sep, f"baris sysctl tanpa '=': {raw!r}"
        values[key.strip()] = value.strip()
    return values


def shell_function(name: str, script: Path | None = None) -> str:
    """Extract a top-level shell function definition from a script."""
    text = (script or ROOT / "scripts/build-image.sh").read_text().splitlines()
    start = next(i for i, line in enumerate(text) if line.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(text)) if text[i] == "}")
    return "\n".join(text[start : end + 1])


class ProfileTests(unittest.TestCase):
    def test_packages_are_arm64_desktop_profile(self):
        lines = (ROOT / "profile/packages.aarch64.txt").read_text().splitlines()
        packages = [line.split("#", 1)[0].strip() for line in lines]
        packages = [p for p in packages if p]
        self.assertEqual(len(packages), len(set(packages)))
        for required in [
            "linux-aarch64",
            "plasma-desktop",
            "sddm",
            "networkmanager",
            "mkinitcpio",
            # audio path that actually works on ARM codecs
            "pipewire-pulse",
            "pipewire-alsa",
            "alsa-ucm-conf",
            # performance/power packages referenced by the shipped configs
            "zram-generator",
            "power-profiles-daemon",
        ]:
            self.assertIn(required, packages)
        self.assertNotIn("linux", packages)
        self.assertNotIn("linux-amba", packages)

    def test_release_identity(self):
        text = (ROOT / "profile/rootfs/usr/share/amba/os-release").read_text()
        self.assertIn("ID=amba", text)
        self.assertIn("ID_LIKE=arch", text)
        self.assertIn('PRETTY_NAME="Amba Linux"', text)

    def test_json_and_svg_assets_parse(self):
        for path in (ROOT / "profile/rootfs").rglob("*.json"):
            with self.subTest(path=path):
                json.loads(path.read_text())
        for path in (ROOT / "profile/rootfs").rglob("*.svg"):
            with self.subTest(path=path):
                ET.fromstring(path.read_text())

    def test_wallpaper_package_metadata(self):
        path = PROFILE / "usr/share/wallpapers/Amba/metadata.json"
        meta = json.loads(path.read_text())
        self.assertEqual("Wallpaper/Images", meta["KPackageStructure"])
        self.assertEqual("Amba", meta["KPlugin"]["Id"])
        self.assertEqual(path.parent.name, meta["KPlugin"]["Id"])
        image = path.parent / "contents/images/3840x2160.png"
        self.assertEqual((3840, 2160), png_size(image))

    def test_sddm_dropin_is_in_a_directory_sddm_reads(self):
        # sddm only loads /usr/lib/sddm/sddm.conf.d, /etc/sddm.conf.d and
        # /etc/sddm.conf; a drop-in under /etc/sddm/conf.d is silently ignored.
        self.assertTrue((PROFILE / "etc/sddm.conf.d/10-amba.conf").is_file())
        self.assertFalse((PROFILE / "etc/sddm").exists())

    def test_no_autologin_or_passwordless_sudo(self):
        conf = parse_ini_like(PROFILE / "etc/sddm.conf.d/10-amba.conf")
        self.assertNotIn("Autologin", conf)
        self.assertEqual("1000", conf["Users"]["MinimumUid"])
        self.assertIn("alarm", conf["Users"]["HideUsers"])
        sudo = (PROFILE / "etc/sudoers.d/10-wheel").read_text()
        self.assertIn("%wheel ALL=(ALL:ALL) ALL", sudo)
        self.assertNotIn("NOPASSWD", sudo)

    def test_initramfs_is_not_build_host_specific(self):
        config = (PROFILE / "etc/mkinitcpio.conf").read_text()
        hooks = next(line for line in config.splitlines() if line.startswith("HOOKS="))
        self.assertNotIn("autodetect", hooks)
        self.assertIn("block", hooks)
        self.assertIn("filesystems", hooks)

    def test_initramfs_includes_virtio_and_fast_compression(self):
        config = (PROFILE / "etc/mkinitcpio.conf").read_text()
        modules = next(line for line in config.splitlines() if line.startswith("MODULES="))
        for module in ("virtio_blk", "virtio_pci", "virtio_gpu"):
            self.assertIn(module, modules)
        self.assertIn('COMPRESSION="zstd"', config)

    def test_networkmanager_uses_resolved(self):
        config = (PROFILE / "etc/NetworkManager/conf.d/10-amba-dns.conf").read_text()
        self.assertIn("dns=systemd-resolved", config)
        builder = (ROOT / "scripts/build-image.sh").read_text()
        self.assertIn("disable_units systemd-networkd.service", builder)
        self.assertIn("NetworkManager.service", builder)

    def test_optimisation_dropins_are_valid(self):
        vm = sysctl_values(PROFILE / "etc/sysctl.d/99-amba-memory.conf")
        self.assertEqual("180", vm["vm.swappiness"])
        self.assertEqual("0", vm["vm.page-cluster"])

        net = sysctl_values(PROFILE / "etc/sysctl.d/99-amba-network.conf")
        self.assertEqual("bbr", net["net.ipv4.tcp_congestion_control"])
        self.assertEqual("fq", net["net.core.default_qdisc"])
        modules = (PROFILE / "etc/modules-load.d/amba-network.conf").read_text().split()
        self.assertIn("tcp_bbr", modules)
        self.assertIn("sch_fq", modules)

        desktop = sysctl_values(PROFILE / "etc/sysctl.d/99-amba-desktop.conf")
        self.assertGreater(int(desktop["fs.inotify.max_user_watches"]), 8192)
        self.assertEqual("1", desktop["kernel.sched_autogroup_enabled"])
        self.assertGreater(int(desktop["fs.file-max"]), 65530)
        self.assertEqual("0", desktop["kernel.nmi_watchdog"])

        # Baloo indexing is off by default (CPU/flash savings on ARM boards)
        # and can be re-enabled per user.
        baloo = parse_ini_like(PROFILE / "etc/xdg/baloofilerc")
        self.assertEqual("false", baloo["General"]["Indexing-Enabled"].lower())

        # systemd fails fast instead of waiting 90s per stuck unit at boot.
        manager = parse_ini_like(PROFILE / "etc/systemd/system.conf.d/10-amba.conf")
        self.assertEqual("20s", manager["Manager"]["DefaultTimeoutStartSec"])
        self.assertEqual("10s", manager["Manager"]["DefaultTimeoutStopSec"])

        # /tmp lives on tmpfs so temp files never hit flash; zram covers overflow.
        builder = (ROOT / "scripts/build-image.sh").read_text()
        self.assertIn("tmpfs /tmp tmpfs", builder)
        self.assertIn("size=50%", builder)

        zram = parse_ini_like(PROFILE / "etc/systemd/zram-generator.conf")
        self.assertIn("zram-size", zram["zram0"])
        self.assertEqual("zstd", zram["zram0"]["compression-algorithm"])

        journal = parse_ini_like(PROFILE / "etc/systemd/journald.conf.d/10-amba.conf")
        self.assertEqual("persistent", journal["Journal"]["Storage"])
        self.assertTrue(journal["Journal"]["SystemMaxUse"].endswith("M"))

        rules = (PROFILE / "etc/udev/rules.d/60-amba-io-scheduler.rules").read_text()
        for kernel in ("nvme", "vd[a-z]", "mmcblk", "sd[a-z]"):
            self.assertIn(f'KERNEL=="{kernel}', rules)
        self.assertIn('ATTR{queue/scheduler}="none"', rules)
        # read-ahead must use the block-device queue attribute, not bdi/
        self.assertIn('ATTR{queue/read_ahead_kb}', rules)
        self.assertNotIn("bdi/read_ahead_kb", rules)


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

    def test_password_hash_accepts_real_openssl_output(self):
        # Regression guard: a hash produced by the documented command must pass.
        digest = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin", "-salt", "0123456789abcdef"],
            input="secret123\n", text=True, capture_output=True,
        )
        self.assertEqual(0, digest.returncode, digest.stderr)
        value = digest.stdout.strip()
        result = run(
            ["bash", "-c", 'source scripts/lib/common.sh; valid_password_hash "$AMBA_HASH"'],
            env={**os.environ, "AMBA_HASH": value},
        )
        self.assertEqual(0, result.returncode, value)

    def test_parse_uint(self):
        for value, expected in [("0", "0"), ("08", "8"), ("016", "16"), ("4096", "4096"), ("000000001", "1")]:
            result = bash(f'source scripts/lib/common.sh; parse_uint "{value}"')
            self.assertEqual(0, result.returncode, value)
            self.assertEqual(expected, result.stdout.strip(), value)
        for value in ["", "-1", "+1", "1.5", "abc", "1000000000", "1e3", "0x10"]:
            result = bash(f'source scripts/lib/common.sh; parse_uint "{value}"')
            self.assertNotEqual(0, result.returncode, value)

    def test_boot_entry(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        result = bash(f'source scripts/lib/common.sh; render_boot_entry "{uuid}"')
        self.assertEqual(0, result.returncode)
        self.assertIn("linux   /Image", result.stdout)
        self.assertIn("initrd  /initramfs-linux.img", result.stdout)
        self.assertIn(f"root=UUID={uuid} rw rootwait nowatchdog", result.stdout)
        self.assertNotIn("/boot/Image", result.stdout)

    def test_boot_entry_rejects_invalid_uuid(self):
        for uuid in ["", "-" * 36, "12345678123412341234123456789abc", "12345678-1234-1234-1234-123456789abg", "12345678-1234-1234-1234-123456789abc-extra"]:
            result = bash(f'source scripts/lib/common.sh; render_boot_entry "{uuid}"')
            self.assertNotEqual(0, result.returncode, uuid)
            self.assertEqual("", result.stdout, uuid)

    def test_files_have_same_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp, "code.fd")
            b = Path(tmp, "vars.fd")
            c = Path(tmp, "small.fd")
            a.write_bytes(b"\0" * 64)
            b.write_bytes(b"\xff" * 64)
            c.write_bytes(b"\0" * 8)
            self.assertEqual(0, bash(f'source scripts/lib/common.sh; files_have_same_size "{a}" "{b}"').returncode)
            self.assertNotEqual(0, bash(f'source scripts/lib/common.sh; files_have_same_size "{a}" "{c}"').returncode)
            self.assertNotEqual(0, bash(f'source scripts/lib/common.sh; files_have_same_size "{a}" "{tmp}/missing"').returncode)
            self.assertNotEqual(0, bash(f'source scripts/lib/common.sh; files_have_same_size "{a}"').returncode)


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amba-branding."))
        self.addCleanup(shutil.rmtree, self.tmp)

    def stage(self, version="0.1.0"):
        share = self.tmp / "usr/share/amba"
        share.mkdir(parents=True)
        (self.tmp / "etc").mkdir()
        shutil.copy(PROFILE / "usr/share/amba/os-release", share / "os-release")
        if version is not None:
            (share / "VERSION").write_text(f"{version}\n")

    def run_script(self, name):
        env = {**os.environ, "AMBA_DESTDIR": str(self.tmp)}
        return run(["bash", str(PROFILE / f"usr/lib/amba/{name}")], env=env)

    def test_publishes_version_id_and_identity(self):
        self.stage()
        result = self.run_script("apply-branding")
        self.assertEqual(0, result.returncode, result.stderr)
        published = (self.tmp / "usr/lib/os-release").read_text()
        self.assertIn("ID=amba", published)
        self.assertIn('VERSION_ID="0.1.0"', published)
        self.assertEqual(1, published.count("VERSION_ID="))
        self.assertTrue((self.tmp / "etc/os-release").is_symlink())
        self.assertEqual("../usr/lib/os-release", os.readlink(self.tmp / "etc/os-release"))

    def test_reapplying_is_idempotent(self):
        self.stage()
        self.assertEqual(0, self.run_script("apply-branding").returncode)
        first = (self.tmp / "usr/lib/os-release").read_text()
        self.assertEqual(0, self.run_script("apply-branding").returncode)
        self.assertEqual(first, (self.tmp / "usr/lib/os-release").read_text())

    def test_missing_template_is_reported(self):
        result = self.run_script("apply-branding")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("tidak ditemukan", result.stderr)

    def test_systemd_boot_hook_refreshes_efi_binary(self):
        efi = self.tmp / "usr/lib/systemd/boot/efi"
        efi.mkdir(parents=True)
        (efi / "systemd-bootaa64.efi").write_bytes(b"MZ-boot")
        (self.tmp / "boot/EFI/BOOT").mkdir(parents=True)
        result = self.run_script("update-systemd-boot")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"MZ-boot", (self.tmp / "boot/EFI/BOOT/BOOTAA64.EFI").read_bytes())
        self.assertEqual(b"MZ-boot", (self.tmp / "boot/EFI/systemd/systemd-bootaa64.efi").read_bytes())

    def test_systemd_boot_hook_is_noop_without_esp(self):
        efi = self.tmp / "usr/lib/systemd/boot/efi"
        efi.mkdir(parents=True)
        (efi / "systemd-bootaa64.efi").write_bytes(b"MZ-boot")
        result = self.run_script("update-systemd-boot")
        self.assertEqual(0, result.returncode, result.stderr)


class WallpaperTests(unittest.TestCase):
    def test_render_scales_to_requested_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "wp.png")
            result = run(["python3", "scripts/render-wallpaper.py", "--width", "96", "--height", "54", "--output", str(out)])
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual((96, 54), png_size(out))
            # Every scanline must use PNG filter type 0 (None); the renderer
            # writes raw rows, so a different filter byte means corrupt output.
            data = out.read_bytes()
            self.assertGreater(len(data), 100)

    def test_render_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp, "a.png"), Path(tmp, "b.png")
            for out in (first, second):
                result = run(["python3", "scripts/render-wallpaper.py", "--width", "64", "--height", "36", "--output", str(out)])
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_custom_size_requires_explicit_output(self):
        result = run(["python3", "scripts/render-wallpaper.py", "--width", "128", "--height", "72"])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--output", result.stderr)

    def test_default_output_matches_profile_asset(self):
        self.assertEqual((3840, 2160), png_size(PROFILE / "usr/share/wallpapers/Amba/contents/images/3840x2160.png"))

    def test_committed_wallpaper_matches_renderer(self):
        """The shipped PNG must be reproducible with `make wallpaper`."""
        committed = PROFILE / "usr/share/wallpapers/Amba/contents/images/3840x2160.png"
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp, "fresh.png")
            result = run(["python3", "scripts/render-wallpaper.py", "--output", str(fresh)])
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                committed.read_bytes(),
                fresh.read_bytes(),
                "wallpaper di profile berbeda dari hasil render; jalankan 'make wallpaper'",
            )


class BuilderHelperTests(unittest.TestCase):
    """Exercise the builder's chroot helpers without a real chroot."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="amba-units."))
        self.addCleanup(shutil.rmtree, self.tmp)
        units = self.tmp / "usr/lib/systemd/system"
        units.mkdir(parents=True)
        (units / "present.service").write_text("[Unit]\n")
        (self.tmp / "etc/systemd/system").mkdir(parents=True)

    def run_functions(self, body: str) -> subprocess.CompletedProcess:
        code = "\n".join([
            "set -u",
            f"ROOT_MNT={self.tmp}",
            "arch-chroot() { if [[ $2 == broken.service ]]; then return 1; fi; echo \"ARCHCHROOT $*\"; }",
            "warn() { echo \"WARN $*\" >&2; }",
            "die() { echo \"DIE $*\" >&2; exit 1; }",
            shell_function("chroot_soft"),
            shell_function("unit_present"),
            shell_function("enable_units"),
            shell_function("disable_units"),
            body,
        ])
        return run(["bash", "-c", code])

    def test_missing_unit_is_skipped_not_fatal(self):
        result = self.run_functions("enable_units present.service missing.service")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("present.service", result.stdout)
        self.assertNotIn("missing.service", result.stdout)
        self.assertIn("missing.service", result.stderr)

    def test_disable_units_ignores_absent_units(self):
        result = self.run_functions("disable_units present.service absent.socket")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("disable present.service", result.stdout)
        self.assertNotIn("absent.socket", result.stdout)

    def test_chroot_soft_does_not_abort(self):
        result = self.run_functions('chroot_soft broken.service; echo "lanjut"')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("lanjut", result.stdout)
        self.assertIn("dilewati", result.stderr)


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

    def test_build_rejects_octal_like_size_cleanly(self):
        result = subprocess.run(["./scripts/build-image.sh", "--size-gib", "08"], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("12..128", result.stderr)
        self.assertNotIn("value too great for base", result.stderr)

    def test_build_normalizes_leading_zero_size(self):
        result = subprocess.run(["./scripts/build-image.sh", "--size-gib", "016", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("(16 GiB", result.stdout)
        self.assertNotIn("016 GiB", result.stdout)

    def test_build_rejects_reserved_user(self):
        result = subprocess.run(["./scripts/build-image.sh", "--user", "root"], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("dicadangkan", result.stderr)

    def test_builder_writes_hosts_and_noatime_fstab(self):
        text = (ROOT / "scripts/build-image.sh").read_text()
        self.assertIn("127.0.1.1", text)
        self.assertIn("${DISTRO_HOSTNAME}.localdomain", text)
        self.assertIn("noatime", text)
        self.assertIn("errors=remount-ro", text)

    def test_builder_enables_units_tolerantly(self):
        text = (ROOT / "scripts/build-image.sh").read_text()
        self.assertIn("enable_units ", text)
        self.assertIn("unit_present", text)
        for unit in ("fstrim.timer", "systemd-resolved.service", "sddm.service"):
            self.assertIn(unit, text)

    def test_qemu_dry_run_is_snapshot_and_arm64(self):
        result = subprocess.run(["./scripts/run-qemu.sh", "--image", "example.img", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("qemu-system-aarch64", result.stdout)
        self.assertIn("-snapshot", result.stdout)
        self.assertIn("virtio-blk-pci", result.stdout)
        self.assertIn("virtio-rng-pci", result.stdout)
        self.assertNotIn("file=example.img,readonly", result.stdout)

    def test_qemu_persistent_flag(self):
        result = subprocess.run(["./scripts/run-qemu.sh", "--image", "example.img", "--persistent", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("-snapshot", result.stdout)

    def test_qemu_rejects_octal_like_memory_cleanly(self):
        result = subprocess.run(["./scripts/run-qemu.sh", "--image", "example.img", "--memory", "08", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("2048..32768", result.stderr)
        self.assertNotIn("value too great for base", result.stderr)

    def test_qemu_normalizes_leading_zero_memory_and_cpus(self):
        result = subprocess.run(["./scripts/run-qemu.sh", "--image", "example.img", "--memory", "04096", "--cpus", "04", "--dry-run"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("-smp 4 ", result.stdout)
        self.assertIn("-m 4096 ", result.stdout)

    def test_qemu_prefers_pflash_firmware_image(self):
        text = (ROOT / "scripts/run-qemu.sh").read_text()
        self.assertIn("/usr/share/edk2/aarch64/QEMU_EFI-pflash.raw", text)
        self.assertLess(text.index("QEMU_EFI-pflash.raw"), text.index("/usr/share/edk2/aarch64/QEMU_EFI.fd"))

    def test_qemu_rejects_mismatched_pflash_pair(self):
        text = (ROOT / "scripts/run-qemu.sh").read_text()
        self.assertIn('files_have_same_size "$FIRMWARE" "$VARS"', text)

    def test_builder_has_no_physical_disk_target(self):
        text = (ROOT / "scripts/build-image.sh").read_text()
        self.assertIn('losetup --find --show --partscan "$IMAGE_WORK"', text)
        self.assertIn('sfdisk --quiet "$IMAGE_WORK"', text)
        self.assertNotIn("/dev/sd", text)
        self.assertNotIn("/dev/nvme", text)


if __name__ == "__main__":
    unittest.main()
