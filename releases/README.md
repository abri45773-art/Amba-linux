# Cermin berkas rilis

Direktori ini menyimpan salinan berkas rilis yang **tidak** dapat diunggah
sebagai aset GitHub Release dari lingkungan build (unggahan aset memakai
`uploads.github.com`). Berkas di sini adalah cermin yang ditautkan dari catatan
rilis dan dari [`docs/releases.html`](../docs/releases.html).

| Versi | Berkas | Ukuran | SHA-256 |
|---|---|---|---|
| 0.1.0 | `amba-linux-0.1.0.iso` | ± 1,6 MB | lihat `v0.1.0/SHA256SUMS` |

Catatan:

- Berkas di sini **bukan** bagian dari arsip source; `scripts/make-release.py`
  mengecualikan direktori `releases/`.
- ISO ini prototipe kerangka boot UEFI tanpa kernel, initramfs, rootfs, atau
  KDE Plasma (lihat [docs/RELEASE.md](../docs/RELEASE.md)).
- Setelah aset ISO berhasil diunggah ke GitHub Release (mis. `make publish` di
  mesin yang bisa menjangkau `uploads.github.com`), berkas cermin ini boleh
  dihapus dan tautan di `docs/release.json` dikembalikan ke URL aset rilis.
