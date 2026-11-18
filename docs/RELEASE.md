# Rilis: APK Android dan source ZIP

Halaman unduhan publik ada di **[releases.html](releases.html)** (dibuat otomatis,
jangan disunting manual). Dokumen ini menjelaskan isi tiap aset, cara membuatnya,
dan cara mengunggahnya ke GitHub Releases.

## Aset rilis

| Berkas | Dibuat oleh | Isi |
|---|---|---|
| `amba-android-<versi>.apk` | `tools/apkbuild/` | Aplikasi pendamping Android (~5 KB), ditandatangani APK Signature Scheme v2 |
| `amba-linux-<versi>-source.zip` | `scripts/make-release.py` | Semua berkas terlacak git pada commit rilis |
| `amba-linux-<versi>-source.tar.gz` | `scripts/make-release.py` | Isi sama, format tar.gz |
| `SHA256SUMS` | `scripts/make-release.py` | Satu baris `sha256sum` per aset |
| `release-manifest.json` | `scripts/make-release.py` | Versi, commit, ukuran, dan checksum tiap aset |
| `release-notes.md` | `scripts/make-release.py` | Catatan siap tempel untuk GitHub Release |
| `amba-linux-<versi>-aarch64.img` | `scripts/build-image.sh` (host Arch ARM) | Image disk, **belum** ada di pipeline ini |

Sumber tunggal data rilis adalah [`docs/release.json`](release.json); versi harus
sama dengan `profile/VERSION`. Halaman HTML dan catatan rilis dirender dari sana,
sehingga versi, nama berkas, dan tautan tidak mungkin berbeda antar tempat.

## Membuat aset

```bash
make check                     # tes offline, termasuk build + verifikasi APK
make release                   # dist/ lengkap; APK ditandatangani kunci di dist/keys
make release KEYSTORE=~/.amba-keys   # pakai kunci tetap agar checksum stabil
make release FORCE=1           # timpa aset yang sudah ada
make page                      # render halaman kanonis tanpa checksum build
make page-preview              # dist/releases.html dengan checksum build lokal
make release-check             # gagal bila halaman/manifest sudah basi
```

Hasil di `dist/` (diabaikan git):

```text
dist/amba-android-0.1.0.apk
dist/amba-linux-0.1.0-source.zip
dist/amba-linux-0.1.0-source.tar.gz
dist/SHA256SUMS
dist/release-manifest.json
dist/release-notes.md
```

Urutan langkah di `scripts/make-release.py`:

1. Versi di `docs/release.json` dicocokkan dengan `profile/VERSION`.
2. APK dibangun (`tools/apkbuild/build_apk.py`) lalu **diverifikasi**
   (`tools/apkbuild/verify_apk.py`): struktur ZIP, aturan `resources.arsc`,
   blok tanda tangan v2, tanda tangan RSA/ECDSA via openssl, dan digest konten v2.
3. Arsip source membaca blob langsung dari commit git (bukan worktree) dengan
   metadata waktu commit (`SOURCE_DATE_EPOCH`), sehingga perubahan yang belum
   di-commit tidak dapat menyusup dan dua build commit yang sama identik.
4. `SHA256SUMS`, `release-manifest.json`, dan `release-notes.md` ditulis.

Berkas yang belum di-commit tidak masuk arsip source; skrip memberi peringatan
bila ada berkas seperti itu.

## Mengunggah ke GitHub Releases

```bash
./scripts/publish-release.sh --yes            # tag v<versi>, rilis publik
./scripts/publish-release.sh --draft          # simpan sebagai draft dulu
./scripts/publish-release.sh --prerelease     # tandai versi prototipe/prarilis
./scripts/publish-release.sh --upload-only    # ganti aset rilis yang ada
```

Skrip hanya mengunggah aset yang tercantum dan lolos verifikasi
`SHA256SUMS`, menolak tag/commit/repositori yang tidak cocok dengan manifest, dan
tidak melakukan apa pun tanpa `--yes` atau konfirmasi interaktif. Gunakan
`make page-preview` untuk membuat `dist/releases.html` dengan checksum build;
halaman kanonis di `docs/` tetap independen dari kunci debug lokal.

## Aplikasi Android (APK)

APK kecil ini **tidak** memuat image Linux dan tidak memasang Amba Linux. Fungsinya
menampilkan identitas rilis, catatan, dan daftar aset — berguna sebagai kartu rilis
offline di perangkat Android.

- Package `com.amba.companion`, satu activity `MainActivity` tanpa izin jaringan.
- UI dibangun dari kode (tanpa layout XML) memakai palet Amba (`#0f172a` latar,
  `#2dd4bf` aksen), teks dari `docs/release.json`.
- Dibangun tanpa Gradle/Android SDK: DEX ditulis langsung oleh `tools/apkbuild/dex.py`,
  manifest dan `resources.arsc` oleh modul lain di direktori yang sama.
- Ditandatangani kunci **debug** (`CN=Amba Linux Release`). Ini cukup untuk
  sideload dan untuk verifikasi checksum, tetapi bukan identitas Play Store dan
  tidak boleh dipakai untuk rilis yang dipercaya publik.

Alur build APK:

```bash
python3 tools/apkbuild/build_apk.py --output dist/amba-android-0.1.0.apk \
    --keystore ~/.amba-keys
python3 tools/apkbuild/verify_apk.py dist/amba-android-0.1.0.apk
```

Saat `build_apk.py` dipanggil langsung tanpa `--keystore`, kunci sementara dibuat
di direktori temporer. `make release` berbeda: kunci persisten default disimpan di
`dist/keys/`, sehingga checksum stabil selama direktori itu dipertahankan.

### Kunci penandatangan

`make release` menaruh kunci di `dist/keys/` (diabaikan git dan tidak ikut arsip
source). Untuk rilis yang harus stabil, simpan kunci di luar repositori:

```bash
mkdir -p ~/.amba-keys && chmod 700 ~/.amba-keys
make release KEYSTORE=~/.amba-keys
```

Kunci yang bocor memungkinkan pihak lain membuat APK dengan identitas yang sama.
Karena ini kunci debug dan APK tidak meminta izin sensitif, risikonya terbatas,
tetapi tetap jangan commit `debug.key`.

## Image disk

Image `*.img` tidak dibuat oleh pipeline ini: `scripts/build-image.sh` hanya jalan
di host Arch Linux ARM aarch64 dengan akses root (lihat [BUILD.md](BUILD.md)).
Setelah image ada, unggah dengan:

```bash
cp out/amba-linux-0.1.0-aarch64.img dist/
cd dist && sha256sum amba-linux-0.1.0-aarch64.img >> SHA256SUMS
./../scripts/publish-release.sh --upload-only --yes
```

Tanpa image nyata, halaman rilis menandai kartu image sebagai
**belum tersedia** dan tidak memasang tautan unduhan.

## Verifikasi

```bash
cd dist
sha256sum -c SHA256SUMS          # semua berkas
python3 ../tools/apkbuild/verify_apk.py amba-android-0.1.0.apk
```

`verify_apk.py` keluar dengan status bukan-nol bila ada satu byte pun berubah:
tanda tangan v2 menutup seluruh isi APK, dan digest konten dihitung ulang dari
berkas yang diverifikasi.
