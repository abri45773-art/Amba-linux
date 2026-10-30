#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Build a persistent Amba Linux ARM64 UEFI disk image on a native
# Arch Linux ARM aarch64 host. This script never writes to a physical disk.
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

VERSION=$(<"$REPO_DIR/profile/VERSION")
ROOTFS=""
ROOTFS_SHA256=""
USERNAME="amba"
PASSWORD_HASH_FILE=""
DISTRO_HOSTNAME="amba"
TIMEZONE="Asia/Jakarta"
LOCALE="id_ID.UTF-8"
KEYMAP="us"
SIZE_GIB=16
OUTPUT="$REPO_DIR/out/amba-linux-${VERSION}-aarch64.img"
WORK_BASE="$REPO_DIR/work"
DRY_RUN=0
LOOP_DEV=""
ROOT_MNT=""
WORK_DIR=""

usage() {
  cat <<'EOF'
Amba Linux ARM64 image builder

Usage:
  sudo ./scripts/build-image.sh --rootfs FILE --rootfs-sha256 HEX \
      --password-hash-file FILE [options]

Required for a real build:
  --rootfs FILE             Official ArchLinuxARM-aarch64-latest.tar.gz
  --rootfs-sha256 HEX       Expected SHA-256 obtained from a trusted channel
  --password-hash-file FILE File containing one SHA-crypt/yescrypt hash

Options:
  --output FILE             Output raw image (default: out/amba-linux-VERSION-aarch64.img)
  --work-dir DIR            Parent for temporary mount directory (default: work)
  --size-gib N              Image size in GiB, 12..128 (default: 16)
  --user NAME               Desktop user, not root/alarm (default: amba)
  --hostname NAME           Hostname (default: amba)
  --timezone ZONE           Zoneinfo name (default: Asia/Jakarta)
  --locale LOCALE           id_ID.UTF-8 or en_US.UTF-8 (default: id_ID.UTF-8)
  --keymap NAME             Console keymap (default: us)
  --dry-run                 Print the plan; do not require root or modify anything
  -h, --help                Show this help

The host must be Arch Linux ARM on aarch64. Cross-building from Debian/x86_64
is intentionally not implemented. Output is a persistent installed-style disk
image, not a live ISO. Existing output files are never overwritten.
EOF
}

need_value() {
  [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "Opsi $1 membutuhkan nilai."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rootfs) need_value "$@"; ROOTFS=$2; shift 2 ;;
    --rootfs-sha256) need_value "$@"; ROOTFS_SHA256=$2; shift 2 ;;
    --password-hash-file) need_value "$@"; PASSWORD_HASH_FILE=$2; shift 2 ;;
    --output) need_value "$@"; OUTPUT=$2; shift 2 ;;
    --work-dir) need_value "$@"; WORK_BASE=$2; shift 2 ;;
    --size-gib) need_value "$@"; SIZE_GIB=$2; shift 2 ;;
    --user) need_value "$@"; USERNAME=$2; shift 2 ;;
    --hostname) need_value "$@"; DISTRO_HOSTNAME=$2; shift 2 ;;
    --timezone) need_value "$@"; TIMEZONE=$2; shift 2 ;;
    --locale) need_value "$@"; LOCALE=$2; shift 2 ;;
    --keymap) need_value "$@"; KEYMAP=$2; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Opsi tidak dikenal: $1" ;;
  esac
done

valid_username "$USERNAME" || die "Nama user tidak valid atau dicadangkan: $USERNAME"
[[ "$DISTRO_HOSTNAME" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]] || die "Hostname tidak valid."
if ! SIZE_GIB=$(parse_uint "$SIZE_GIB") || (( SIZE_GIB < 12 || SIZE_GIB > 128 )); then
  die "--size-gib harus 12..128."
fi
[[ "$LOCALE" == "id_ID.UTF-8" || "$LOCALE" == "en_US.UTF-8" ]] || die "Locale awal harus id_ID.UTF-8 atau en_US.UTF-8."
[[ "$KEYMAP" =~ ^[a-zA-Z0-9_-]+$ ]] || die "Keymap tidak valid."
[[ "$TIMEZONE" =~ ^[A-Za-z0-9_+/-]+$ && "$TIMEZONE" != *..* && "$TIMEZONE" != /* ]] || die "Timezone tidak valid."

if (( DRY_RUN )); then
  cat <<EOF
RENCANA BUILD (tidak ada perubahan dilakukan)
  Target       : aarch64 / UEFI / KDE Plasma
  Basis        : Arch Linux ARM generic aarch64 rootfs
  Kernel       : linux-aarch64 dari repositori Arch Linux ARM
  Image        : $OUTPUT (${SIZE_GIB} GiB, GPT, ESP 512 MiB + ext4)
  Rootfs       : ${ROOTFS:-BELUM DITENTUKAN}
  User         : $USERNAME (password hash wajib saat build nyata)
  Locale       : $LOCALE, $TIMEZONE, keymap=$KEYMAP
  Boot         : systemd-boot, EFI/BOOT/BOOTAA64.EFI, tanpa Secure Boot
  Keamanan     : root dan user bawaan alarm dikunci; sudo memakai password
EOF
  exit 0
fi

[[ $EUID -eq 0 ]] || die "Build nyata harus dijalankan sebagai root (sudo)."
[[ $(uname -m) == "aarch64" ]] || die "Builder awal hanya mendukung host aarch64 native. Gunakan mesin/VM Arch Linux ARM."
[[ -f /etc/arch-release ]] || die "Host harus Arch Linux ARM (atau turunannya dengan arch-install-scripts)."
require_tools arch-chroot bsdtar sfdisk losetup mkfs.fat mkfs.ext4 blkid udevadm truncate sha256sum install findmnt

[[ -n "$ROOTFS" && -f "$ROOTFS" ]] || die "Rootfs tidak ditemukan. Isi --rootfs."
if [[ -z "$ROOTFS_SHA256" ]] || ! valid_sha256 "$ROOTFS_SHA256"; then
  die "--rootfs-sha256 wajib berupa 64 digit heksadesimal."
fi
[[ -n "$PASSWORD_HASH_FILE" && -f "$PASSWORD_HASH_FILE" ]] || die "File password hash tidak ditemukan."
PASSWORD_HASH=$(head -n 1 -- "$PASSWORD_HASH_FILE")
valid_password_hash "$PASSWORD_HASH" || die "Hash harus SHA-crypt (\$6\$) atau yescrypt (\$y\$), bukan password plaintext."

ROOTFS=$(realpath -- "$ROOTFS")
OUTPUT=$(realpath -m -- "$OUTPUT")
mkdir -p -- "$(dirname -- "$OUTPUT")" "$WORK_BASE"
WORK_BASE=$(realpath -- "$WORK_BASE")
[[ ! -e "$OUTPUT" ]] || die "Output sudah ada; pilih nama baru: $OUTPUT"
case "$WORK_BASE/" in "$REPO_DIR/"*|/tmp/*|/var/tmp/*) ;; *) die "--work-dir harus di dalam repositori, /tmp, atau /var/tmp." ;; esac

log "Memverifikasi SHA-256 rootfs..."
printf '%s  %s\n' "$ROOTFS_SHA256" "$ROOTFS" | sha256sum --check --status || die "SHA-256 rootfs tidak cocok. Build dihentikan."

WORK_DIR=$(mktemp -d "$WORK_BASE/amba-build.XXXXXX")
ROOT_MNT="$WORK_DIR/root"
IMAGE_WORK="$WORK_DIR/image.raw"
mkdir -p "$ROOT_MNT"

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ -n "$ROOT_MNT" ]] && findmnt -Rrn -- "$ROOT_MNT" >/dev/null 2>&1; then
    log "Membersihkan mount di $ROOT_MNT"
    umount -R -- "$ROOT_MNT"
  fi
  if [[ -n "$LOOP_DEV" ]]; then
    losetup -d -- "$LOOP_DEV" >/dev/null 2>&1
  fi
  if (( rc != 0 )); then
    log "Build gagal. Direktori kerja disimpan untuk diagnosis: $WORK_DIR"
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

log "Membuat image GPT ${SIZE_GIB} GiB..."
truncate -s "${SIZE_GIB}G" "$IMAGE_WORK"
sfdisk --quiet "$IMAGE_WORK" <<'EOF'
label: gpt
unit: sectors
first-lba: 2048
start=2048, size=1048576, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="AMBA_ESP"
start=1050624, type=B921B045-1DF0-41C3-AF44-4C6F280D3FAE, name="AMBA_ROOT"
EOF
LOOP_DEV=$(losetup --find --show --partscan "$IMAGE_WORK")
udevadm settle
[[ -b "${LOOP_DEV}p1" && -b "${LOOP_DEV}p2" ]] || die "Partisi loop tidak muncul: ${LOOP_DEV}p1/p2."
mkfs.fat -F 32 -n AMBA_ESP "${LOOP_DEV}p1" >/dev/null
mkfs.ext4 -q -F -L AMBA_ROOT "${LOOP_DEV}p2"
ROOT_UUID=$(blkid -s UUID -o value "${LOOP_DEV}p2")
ESP_UUID=$(blkid -s UUID -o value "${LOOP_DEV}p1")
mount "${LOOP_DEV}p2" "$ROOT_MNT"
mkdir -p "$ROOT_MNT/boot"
mount "${LOOP_DEV}p1" "$ROOT_MNT/boot"

log "Mengekstrak rootfs Arch Linux ARM..."
bsdtar -xpf "$ROOTFS" --numeric-owner -C "$ROOT_MNT"
[[ -x "$ROOT_MNT/usr/bin/pacman" ]] || die "Arsip tidak berisi rootfs Arch yang diharapkan."

cat > "$ROOT_MNT/etc/fstab" <<EOF
UUID=$ROOT_UUID / ext4 rw,relatime 0 1
UUID=$ESP_UUID /boot vfat rw,relatime,fmask=0077,dmask=0077 0 2
EOF

log "Memperbarui basis dan memasang KDE Plasma (butuh internet)..."
arch-chroot "$ROOT_MNT" /usr/bin/pacman-key --init
arch-chroot "$ROOT_MNT" /usr/bin/pacman-key --populate archlinuxarm
mapfile -t PACKAGES < <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$REPO_DIR/profile/packages.aarch64.txt")
arch-chroot "$ROOT_MNT" /usr/bin/pacman -Syu --needed --noconfirm "${PACKAGES[@]}"

log "Menerapkan branding dan konfigurasi Amba..."
cp -a "$REPO_DIR/profile/rootfs/." "$ROOT_MNT/"
chmod 0440 "$ROOT_MNT/etc/sudoers.d/10-wheel"
install -Dm644 "$REPO_DIR/profile/packages.aarch64.txt" "$ROOT_MNT/usr/share/amba/packages.aarch64.txt"
install -Dm644 "$REPO_DIR/profile/VERSION" "$ROOT_MNT/usr/share/amba/VERSION"
arch-chroot "$ROOT_MNT" /usr/lib/amba/apply-branding

printf '%s\n' "$DISTRO_HOSTNAME" > "$ROOT_MNT/etc/hostname"
printf 'LANG=%s\n' "$LOCALE" > "$ROOT_MNT/etc/locale.conf"
printf 'KEYMAP=%s\n' "$KEYMAP" > "$ROOT_MNT/etc/vconsole.conf"
cat > "$ROOT_MNT/etc/locale.gen" <<EOF
en_US.UTF-8 UTF-8
id_ID.UTF-8 UTF-8
EOF
[[ -f "$ROOT_MNT/usr/share/zoneinfo/$TIMEZONE" ]] || die "Timezone tidak tersedia di target: $TIMEZONE"
arch-chroot "$ROOT_MNT" /usr/bin/ln -snf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
arch-chroot "$ROOT_MNT" /usr/bin/locale-gen
arch-chroot "$ROOT_MNT" /usr/bin/systemctl set-default graphical.target
arch-chroot "$ROOT_MNT" /usr/bin/systemctl disable systemd-networkd.service systemd-networkd.socket
arch-chroot "$ROOT_MNT" /usr/bin/systemctl enable NetworkManager.service sddm.service systemd-resolved.service systemd-timesyncd.service bluetooth.service

log "Membuat user dan mengunci akun bawaan..."
arch-chroot "$ROOT_MNT" /usr/bin/useradd --create-home --groups wheel --shell /bin/bash "$USERNAME"
printf '%s:%s\n' "$USERNAME" "$PASSWORD_HASH" | arch-chroot "$ROOT_MNT" /usr/bin/chpasswd -e
arch-chroot "$ROOT_MNT" /usr/bin/passwd -l root
if arch-chroot "$ROOT_MNT" /usr/bin/getent passwd alarm >/dev/null; then
  arch-chroot "$ROOT_MNT" /usr/bin/usermod --lock --expiredate 1 alarm
fi
rm -f -- "$ROOT_MNT/etc/sudoers.d/alarm"
arch-chroot "$ROOT_MNT" /usr/bin/visudo -cf /etc/sudoers

# Unique identity on first boot, not a machine-id shared by every downloaded image.
: > "$ROOT_MNT/etc/machine-id"
rm -f "$ROOT_MNT/var/lib/dbus/machine-id"

log "Membangun initramfs dan bootloader ARM64 UEFI..."
arch-chroot "$ROOT_MNT" /usr/bin/mkinitcpio -P
# Install the removable-media fallback directly. This does not require EFI
# NVRAM on the build host and avoids bootctl probing the host firmware.
install -d "$ROOT_MNT/boot/loader/entries" "$ROOT_MNT/boot/EFI/BOOT" "$ROOT_MNT/boot/EFI/systemd"
install -m644 "$ROOT_MNT/usr/lib/systemd/boot/efi/systemd-bootaa64.efi" "$ROOT_MNT/boot/EFI/BOOT/BOOTAA64.EFI"
install -m644 "$ROOT_MNT/usr/lib/systemd/boot/efi/systemd-bootaa64.efi" "$ROOT_MNT/boot/EFI/systemd/systemd-bootaa64.efi"
cat > "$ROOT_MNT/boot/loader/loader.conf" <<'EOF'
default amba.conf
timeout 5
console-mode keep
editor no
EOF
render_boot_entry "$ROOT_UUID" > "$ROOT_MNT/boot/loader/entries/amba.conf"

for artifact in /boot/Image /boot/initramfs-linux.img /boot/EFI/BOOT/BOOTAA64.EFI; do
  [[ -s "$ROOT_MNT$artifact" ]] || die "Artefak boot tidak ada/kosong: $artifact"
done
arch-chroot "$ROOT_MNT" /usr/bin/pacman -Q linux-aarch64 plasma-desktop sddm > "$ROOT_MNT/usr/share/amba/build-packages.txt"

log "Membersihkan cache dan menyinkronkan filesystem..."
arch-chroot "$ROOT_MNT" /usr/bin/pacman -Scc --noconfirm
rm -f "$ROOT_MNT/var/lib/pacman/sync/"*.db
ln -snf /run/systemd/resolve/stub-resolv.conf "$ROOT_MNT/etc/resolv.conf"
sync
ROOT_MNT_CANON=$(realpath -- "$ROOT_MNT")
umount -R -- "$ROOT_MNT_CANON"
losetup -d -- "$LOOP_DEV"
LOOP_DEV=""

mv -- "$IMAGE_WORK" "$OUTPUT"
( cd -- "$(dirname -- "$OUTPUT")" && sha256sum -- "$(basename -- "$OUTPUT")" > "$(basename -- "$OUTPUT").sha256" )
log "Selesai membuat image: $OUTPUT"
log "Uji boot dengan scripts/run-qemu.sh sebelum menulis ke media/perangkat nyata."
log "Image belum di-boot oleh builder ini; kompatibilitas hardware harus diuji terpisah."
