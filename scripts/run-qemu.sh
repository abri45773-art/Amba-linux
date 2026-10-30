#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Boot an Amba Linux ARM64 image in the QEMU 'virt' machine.
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

IMAGE=""
FIRMWARE=""
VARS=""
MEMORY=4096
CPUS=4
HEADLESS=0
PERSISTENT=0
ACCEL=tcg
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: ./scripts/run-qemu.sh --image FILE [options]

Options:
  --image FILE       Raw Amba Linux aarch64 image (required)
  --firmware FILE    UEFI code, e.g. QEMU_EFI.fd / AAVMF_CODE.fd
  --vars FILE        Writable-template UEFI variables file
  --memory MiB       RAM, 2048..32768 (default: 4096)
  --cpus N           CPUs, 1..16 (default: 4)
  --accel tcg|kvm    Default: tcg. KVM requires an aarch64 host and /dev/kvm.
  --headless         No graphical window; serial console on the terminal
  --persistent       Write changes to the image (default: discard disk writes)
  --dry-run          Print the QEMU command without starting it
  -h, --help         Show this help

If firmware paths are omitted, common Arch Linux and Debian paths are checked.
UEFI variable state is copied to a temporary file for each run. Disk writes are
discarded unless --persistent is supplied. Stop QEMU with Ctrl-A then X in
headless mode, or close the graphical window.
EOF
}
need_value() { [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "Opsi $1 membutuhkan nilai."; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) need_value "$@"; IMAGE=$2; shift 2 ;;
    --firmware) need_value "$@"; FIRMWARE=$2; shift 2 ;;
    --vars) need_value "$@"; VARS=$2; shift 2 ;;
    --memory) need_value "$@"; MEMORY=$2; shift 2 ;;
    --cpus) need_value "$@"; CPUS=$2; shift 2 ;;
    --accel) need_value "$@"; ACCEL=$2; shift 2 ;;
    --headless) HEADLESS=1; shift ;;
    --persistent) PERSISTENT=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Opsi tidak dikenal: $1" ;;
  esac
done

[[ -n "$IMAGE" ]] || { usage >&2; die "--image wajib."; }
if [[ ! "$MEMORY" =~ ^[0-9]+$ ]] || (( MEMORY < 2048 || MEMORY > 32768 )); then
  die "--memory harus 2048..32768 MiB."
fi
if [[ ! "$CPUS" =~ ^[0-9]+$ ]] || (( CPUS < 1 || CPUS > 16 )); then
  die "--cpus harus 1..16."
fi
[[ "$ACCEL" == tcg || "$ACCEL" == kvm ]] || die "--accel harus tcg atau kvm."

find_first() {
  local candidate
  for candidate in "$@"; do
    if [[ -f "$candidate" ]]; then printf '%s\n' "$candidate"; return 0; fi
  done
  return 1
}
if [[ -z "$FIRMWARE" ]]; then
  FIRMWARE=$(find_first \
    /usr/share/edk2/aarch64/QEMU_EFI.fd \
    /usr/share/edk2-arm-vars/QEMU_EFI.fd \
    /usr/share/AAVMF/AAVMF_CODE.fd \
    /usr/share/qemu-efi-aarch64/QEMU_EFI.fd) || true
fi
if [[ -z "$VARS" ]]; then
  VARS=$(find_first \
    /usr/share/edk2/aarch64/vars-template-pflash.raw \
    /usr/share/edk2-arm-vars/vars-template-pflash.raw \
    /usr/share/AAVMF/AAVMF_VARS.fd \
    /usr/share/qemu-efi-aarch64/QEMU_VARS.fd) || true
fi

CMD=(qemu-system-aarch64 -machine virt -smp "$CPUS" -m "$MEMORY"
  -drive "if=pflash,format=raw,file=${FIRMWARE:-FIRMWARE_BELUM_DITENTUKAN},readonly=on"
  -drive "if=pflash,format=raw,file=${VARS:-VARS_BELUM_DITENTUKAN}"
  -drive "if=none,format=raw,file=${IMAGE},id=amba-disk"
  -device "virtio-blk-pci,drive=amba-disk"
  -device virtio-gpu-pci
  -device qemu-xhci -device usb-kbd -device usb-tablet
  -netdev "user,id=amba-net" -device "virtio-net-pci,netdev=amba-net"
  -serial mon:stdio)
if [[ "$ACCEL" == kvm ]]; then CMD+=(-accel kvm -cpu host); else CMD+=(-accel tcg -cpu cortex-a76); fi
if (( HEADLESS )); then CMD+=(-display none); else CMD+=(-display gtk); fi
if (( ! PERSISTENT )); then CMD+=(-snapshot); fi

if (( DRY_RUN )); then
  printf '%q ' "${CMD[@]}"; printf '\n'
  printf 'Catatan: file VARS sementara akan dibuat saat menjalankan QEMU sungguhan.\n'
  exit 0
fi

require_tools qemu-system-aarch64 mktemp cp
[[ -f "$IMAGE" ]] || die "Image tidak ditemukan: $IMAGE"
[[ -n "$FIRMWARE" && -f "$FIRMWARE" ]] || die "Firmware UEFI ARM64 tidak ditemukan. Pasang edk2-aarch64 (Arch) atau qemu-efi-aarch64 (Debian), lalu isi --firmware."
[[ -n "$VARS" && -f "$VARS" ]] || die "Template UEFI VARS tidak ditemukan. Isi --vars."
if [[ "$ACCEL" == kvm ]]; then
  [[ $(uname -m) == aarch64 && -w /dev/kvm ]] || die "KVM memerlukan host aarch64 dan akses tulis /dev/kvm."
fi
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/amba-qemu.XXXXXX")
cleanup() { rm -rf -- "$TMP_DIR"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cp -- "$VARS" "$TMP_DIR/VARS.fd"
# Replace only the placeholder argument, preserving all other quoting.
for i in "${!CMD[@]}"; do
  if [[ "${CMD[$i]}" == "if=pflash,format=raw,file=$VARS" ]]; then
    CMD[i]="if=pflash,format=raw,file=$TMP_DIR/VARS.fd"
  fi
done
log "Menjalankan QEMU. Disk $([[ $PERSISTENT -eq 1 ]] && echo PERSISTEN || echo snapshot/sementara)."
"${CMD[@]}"
