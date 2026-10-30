#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later

log() { printf '\033[1;36m[amba]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[amba] kesalahan:\033[0m %s\n' "$*" >&2; exit 1; }

valid_username() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] && [[ "$1" != root && "$1" != alarm ]]
}

valid_sha256() {
  [[ "$1" =~ ^[0-9a-fA-F]{64}$ ]]
}

# Parse an unsigned decimal integer without Bash's octal interpretation.
# Leading zeros are accepted ("016" -> 16); oversized inputs are rejected
# before they can overflow shell arithmetic.
parse_uint() {
  local value="$1"
  [[ "$value" =~ ^[0-9]{1,9}$ ]] || return 1
  printf '%s\n' "$((10#$value))"
}

valid_password_hash() {
  # SHA-512 crypt ($6$) or yescrypt ($y$). Plaintext and truncated hashes are rejected.
  local value="$1"
  if [[ "$value" =~ ^\$6\$(rounds=[0-9]+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}$ ]]; then
    return 0
  fi
  if [[ "$value" =~ ^\$y\$[./A-Za-z0-9]+\$[./A-Za-z0-9]{1,64}\$[./A-Za-z0-9]{1,128}$ ]]; then
    return 0
  fi
  return 1
}

require_tools() {
  local tool missing=()
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  ((${#missing[@]} == 0)) || die "Perintah belum tersedia: ${missing[*]}"
}

# Keep this in one place so tests and the builder exercise the same boot entry.
render_boot_entry() {
  local root_uuid="$1"
  [[ "$root_uuid" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || return 1
  printf '%s\n' \
    'title   Amba Linux (ARM64)' \
    'linux   /Image' \
    'initrd  /initramfs-linux.img' \
    "options root=UUID=${root_uuid} rw rootwait console=tty0 console=ttyAMA0,115200"
}
