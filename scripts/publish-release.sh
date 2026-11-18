#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Publish assets produced by scripts/make-release.py as a GitHub Release.
# Nothing is published without --yes or an interactive confirmation.
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

VERSION=$(<"$REPO_DIR/profile/VERSION")
DIST="$REPO_DIR/dist"
TAG="v${VERSION}"
TITLE=""
NOTES=""
TARGET=""
CONFIRM=0
UPLOAD_ONLY=0
DRAFT=0
PRERELEASE=0

usage() {
  cat <<'EOF'
Publikasikan rilis Amba Linux ke GitHub Releases.

Usage:
  ./scripts/publish-release.sh [options]

Options:
  --tag TAG          Tag rilis (default: v<VERSION>, mis. v0.1.0)
  --title TEXT       Judul rilis (default: "Amba Linux <VERSION>")
  --notes FILE       Berkas catatan (default: dist/release-notes.md)
  --dist DIR         Direktori aset (default: dist)
  --target REF       Commit/branch yang diberi tag (default: commit di manifest)
  --draft            Buat sebagai draft
  --prerelease       Tandai sebagai prarilis
  --upload-only      Unggah ulang aset ke rilis yang sudah ada
  --yes              Jangan minta konfirmasi
  -h, --help         Tampilkan bantuan

Hanya berkas yang tercantum dan lolos verifikasi SHA256SUMS yang diunggah,
ditambah SHA256SUMS itu sendiri. Menjalankan --upload-only memakai --clobber.
EOF
}

need_value() {
  [[ $# -ge 2 && -n ${2:-} ]] || die "Opsi $1 memerlukan nilai."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) need_value "$@"; TAG=$2; shift 2 ;;
    --title) need_value "$@"; TITLE=$2; shift 2 ;;
    --notes) need_value "$@"; NOTES=$2; shift 2 ;;
    --dist) need_value "$@"; DIST=$2; shift 2 ;;
    --target) need_value "$@"; TARGET=$2; shift 2 ;;
    --draft) DRAFT=1; shift ;;
    --prerelease) PRERELEASE=1; shift ;;
    --upload-only) UPLOAD_ONLY=1; shift ;;
    --yes) CONFIRM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Opsi tidak dikenal: $1" ;;
  esac
done

[[ -n "$TITLE" ]] || TITLE="Amba Linux $VERSION"
[[ -n "$NOTES" ]] || NOTES="$DIST/release-notes.md"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || die "Tag tidak valid: $TAG"

require_tools gh python3 sha256sum

[[ -d "$DIST" ]] || die "Direktori aset tidak ada: $DIST (jalankan scripts/make-release.py lebih dulu)"
[[ -f "$NOTES" ]] || die "Catatan rilis tidak ditemukan: $NOTES"
[[ -f "$DIST/SHA256SUMS" ]] || die "SHA256SUMS tidak ada; jalankan scripts/make-release.py"
[[ -f "$DIST/release-manifest.json" ]] || die "release-manifest.json tidak ada; build rilis belum lengkap"

# Read trusted metadata from the locally generated manifest without requiring jq.
mapfile -t META < <(python3 - "$DIST/release-manifest.json" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(manifest["release"]["version"])
print(manifest["release"]["commit"])
print(manifest["project"]["repo"])
PY
)
((${#META[@]} == 3)) || die "release-manifest.json tidak lengkap"
MANIFEST_VERSION=${META[0]}
MANIFEST_COMMIT=${META[1]}
EXPECTED_REPO=${META[2]}
[[ "$MANIFEST_VERSION" == "$VERSION" ]] || die "Versi manifest $MANIFEST_VERSION tidak sama dengan profile/VERSION $VERSION"
[[ "$TAG" == "v$MANIFEST_VERSION" ]] || die "Tag $TAG tidak sama dengan versi manifest v$MANIFEST_VERSION"
[[ -n "$TARGET" ]] || TARGET=$MANIFEST_COMMIT

# Verify every declared checksum before contacting GitHub. Restrict asset names
# to a flat directory so a crafted checksum file cannot upload arbitrary paths.
(cd "$DIST" && sha256sum -c --strict --quiet SHA256SUMS) || die "SHA256SUMS tidak cocok dengan berkas di $DIST"
mapfile -t ASSETS < <(sed -nE 's/^[0-9a-fA-F]{64}  (.+)$/\1/p' "$DIST/SHA256SUMS")
((${#ASSETS[@]} > 0)) || die "SHA256SUMS tidak mencantumkan aset"
for asset in "${ASSETS[@]}"; do
  [[ "$asset" != */* && "$asset" != .* ]] || die "Nama aset tidak aman di SHA256SUMS: $asset"
  [[ -f "$DIST/$asset" ]] || die "Aset tidak ditemukan: $DIST/$asset"
done
UPLOADS=("${ASSETS[@]}" "SHA256SUMS")

ACTUAL_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
[[ "$ACTUAL_REPO" == "$EXPECTED_REPO" ]] || die "Repositori aktif $ACTUAL_REPO, tetapi manifest meminta $EXPECTED_REPO"
gh api "repos/$EXPECTED_REPO/commits/$TARGET" --silent >/dev/null || die "Target $TARGET belum tersedia di GitHub"

log "Repositori : $ACTUAL_REPO"
log "Tag/target : $TAG -> $TARGET"
log "Aset       : ${UPLOADS[*]}"

if (( ! CONFIRM )); then
  printf 'Lanjut publikasikan rilis %s? [y/N] ' "$TAG" >&2
  read -r answer
  [[ "$answer" == [yY]* ]] || die "Dibatalkan."
fi

if gh release view "$TAG" >/dev/null 2>&1; then
  (( UPLOAD_ONLY )) || die "Rilis $TAG sudah ada. Pakai --upload-only untuk mengganti aset."
  log "Rilis $TAG sudah ada; mengunggah ulang aset"
  upload_args=(release upload "$TAG" --clobber)
  for asset in "${UPLOADS[@]}"; do
    upload_args+=("$DIST/$asset")
  done
  gh "${upload_args[@]}"
else
  (( ! UPLOAD_ONLY )) || die "Rilis $TAG belum ada; hapus --upload-only untuk membuatnya."
  log "Membuat rilis $TAG"
  create_args=(release create "$TAG" --title "$TITLE" --notes-file "$NOTES" --target "$TARGET")
  (( DRAFT )) && create_args+=(--draft)
  (( PRERELEASE )) && create_args+=(--prerelease)
  for asset in "${UPLOADS[@]}"; do
    create_args+=("$DIST/$asset")
  done
  gh "${create_args[@]}"
fi

log "Selesai: $(gh release view "$TAG" --json url --jq .url)"
log "Halaman lokal: docs/releases.html (render kanonis dengan make page)"
