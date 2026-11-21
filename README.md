# Amba Linux

**Amba Linux** adalah proyek distro Linux berbasis **Arch Linux ARM** untuk **ARM64 (aarch64) UEFI**, dengan desktop **KDE Plasma**.

> Status: **fondasi awal / prototipe siap-build**.
> Repositori ini berisi konfigurasi, branding, skrip pembuat image, skrip uji QEMU, dan tes offline.
> Rilis GitHub menyediakan **ISO prototipe kerangka boot UEFI**, APK pendamping, dan source code.
> ISO tersebut belum memuat kernel, initramfs, rootfs, atau KDE Plasma sehingga belum bisa boot ke desktop;
> image sistem lengkap memerlukan host Arch Linux ARM aarch64 dan akses repositori paket Arch Linux ARM.

## Pilihan teknis

| Komponen | Pilihan |
|---|---|
| Basis | Arch Linux ARM generic aarch64 rootfs |
| Arsitektur | ARM64 / aarch64 |
| Firmware | UEFI ARM64; target uji QEMU `virt` |
| Kernel | `linux-aarch64` standar Arch Linux ARM |
| Desktop | KDE Plasma + SDDM |
| Bootloader | `systemd-boot`, fallback `EFI/BOOT/BOOTAA64.EFI` |
| Filesystem | GPT: ESP FAT32 512 MiB + root ext4 |
| Image | Raw disk image persisten, bukan live ISO |
| Bahasa/locale awal | `id_ID.UTF-8`, zona waktu `Asia/Jakarta` |
| Swap | zram (swap terkompresi di RAM), bukan partisi swap |
| Optimasi bawaan | `noatime` + `/tmp` tmpfs, zram, BBR+fq, scheduler I/O per device, batas journald, `fstrim`, Baloo nonaktif default |

**Catatan penting:** Arch Linux resmi hanya menargetkan x86_64. Untuk ARM64, proyek ini memakai **Arch Linux ARM**, proyek komunitas terpisah. UEFI ARM64 juga tidak menjamin semua laptop/server ARM dapat boot; dukungan bergantung pada firmware, ACPI/Device Tree, kernel, dan driver perangkat.

## Isi proyek

```text
profile/
  packages.aarch64.txt       Daftar paket desktop ARM64
  rootfs/                    Overlay konfigurasi, branding, tema, wallpaper
    etc/sddm.conf.d/         Drop-in SDDM (path yang benar-benar dibaca SDDM)
    etc/sysctl.d/            Tuning memori, jaringan, inotify
    etc/systemd/             zram-generator, batas journald
    etc/udev/rules.d/        Pemilihan I/O scheduler per jenis device
    usr/lib/amba/            Skrip branding dan pembaru systemd-boot
scripts/
  build-image.sh             Pembuat image disk di host Arch Linux ARM aarch64
  run-qemu.sh                Menjalankan image di QEMU ARM64 UEFI
  render-wallpaper.py        Render wallpaper PNG tanpa dependensi eksternal
  build-iso.py               Bangun ISO prototipe kerangka boot UEFI (butuh pycdlib)
  make-release.py            Buat APK + ISO + arsip source + checksum + catatan rilis
  render-release-page.py     Render docs/releases.html dari docs/release.json
  publish-release.sh         Unggah aset sebagai GitHub Release
  lib/common.sh              Validasi dan helper boot entry
tools/apkbuild/              Pembuat APK tanpa Gradle/SDK (DEX, AXML, arsc, tanda tangan v2)
tests/                       Tes statis/offline
docs/                        Panduan build, boot, pengujian, rilis, keamanan, changelog
```

## Mulai cepat

Di **host Arch Linux ARM aarch64**:

```bash
sudo pacman -Syu --needed arch-install-scripts dosfstools e2fsprogs libarchive util-linux openssl

mkdir -p downloads
curl -fL --retry 3 -o downloads/ArchLinuxARM-aarch64-latest.tar.gz \
  https://os.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz

# Dapatkan SHA-256 arsip dari sumber tepercaya, lalu masukkan saat build.
sha256sum downloads/ArchLinuxARM-aarch64-latest.tar.gz

# Buat hash password secara interaktif; jangan taruh password plaintext di riwayat shell.
umask 077
openssl passwd -6 > ~/amba-password.hash

sudo ./scripts/build-image.sh \
  --rootfs downloads/ArchLinuxARM-aarch64-latest.tar.gz \
  --rootfs-sha256 <64-DIGIT-SHA256> \
  --password-hash-file ~/amba-password.hash \
  --user amba
```

Hasil yang diharapkan:

```text
out/amba-linux-0.1.0-aarch64.img
out/amba-linux-0.1.0-aarch64.img.sha256
```

Uji di QEMU sebelum menulis ke media nyata:

```bash
./scripts/run-qemu.sh --image out/amba-linux-0.1.0-aarch64.img
```

Untuk host x86_64, QEMU memakai emulasi TCG sehingga lambat. Untuk pengujian yang lebih realistis, gunakan host ARM64 dengan KVM atau perangkat ARM64 UEFI yang kompatibel.

## Pemeriksaan tanpa build

```bash
make check       # syntax Bash + unit test offline (83 tes)
make lint        # memerlukan shellcheck
make dry-run     # menampilkan rencana build tanpa perubahan
make wallpaper   # render ulang wallpaper 4K (bisa WIDTH=/HEIGHT=/OUTPUT=)
```

Tes offline **tidak** membuktikan image dapat boot, KDE dapat masuk, Wi-Fi/GPU bekerja, atau perangkat tertentu kompatibel. Lihat [docs/TESTING.md](docs/TESTING.md).

## Optimasi bawaan

- Root ext4 memakai `noatime,errors=remount-ro`.
- **zram** (`zram-generator`, zstd) sebagai swap terkompresi, cocok untuk board
  ARM ber-RAM kecil dengan storage lambat.
- Jaringan memakai `fq` + **BBR**; batas inotify dinaikkan untuk Plasma/IDE.
- udev memilih `none` untuk NVMe/virtio-blk dan `mq-deadline` untuk eMMC/SATA.
- Initramfs zstd (`-T0`) dan hanya locale yang dipakai yang di-generate.
- Journald dibatasi 200 MB dengan kompresi; `fstrim.timer` aktif.
- `systemd-boot` timeout 3 detik, kernel cmdline memakai `nowatchdog`.

Rincian dan alasan tiap pilihan ada di [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
dan [docs/CHANGELOG.md](docs/CHANGELOG.md).

## Keamanan bawaan

- Tidak ada password bawaan yang ditanamkan.
- Build nyata wajib menerima file hash password SHA-crypt atau yescrypt.
- Akun `root` dikunci; user bawaan `alarm` dikunci dan kedaluwarsa.
- SDDM tidak memakai autologin.
- Anggota `wheel` memakai `sudo` dengan password.
- sysctl hardening: `dmesg_restrict`, `kptr_restrict`, penerimaan redirect/source-route dinonaktifkan, `rp_filter=2` dan `log_martians` pada jalur jaringan, `ip_forward=0`, `suid_dumpable=0`, `mmap_min_addr` di-set ke 64 KiB, dan perlindungan hardlink/symlink/fifo.
- Image output dan checksum hanya dapat dibaca pemilik (`chmod 600`); builder juga memaksa `umask 077`.
- Builder menolak file hash password yang dapat dibaca group/world (izin harus privat, misalnya `0600`).
- Builder hanya membuat **file image loopback**, tidak mempartisi disk fisik.
- QEMU secara default memakai `-snapshot`; perubahan disk dibuang kecuali `--persistent`.

## Rilis: ISO prototipe, APK Android, dan source ZIP

Halaman unduhan ada di **[docs/releases.html](docs/releases.html)**, dibuat otomatis
dari [`docs/release.json`](docs/release.json) supaya versi, nama berkas, dan checksum
selalu konsisten.

```bash
python3 -m pip install -r requirements-dev.txt   # pycdlib untuk ISO
make release        # dist/: APK, ISO, source .zip/.tar.gz, SHA256SUMS, catatan rilis
make iso            # hanya ISO: dist/amba-linux-<versi>.iso
make page           # render ulang docs/releases.html dari manifest kanonis
make page-preview   # pratinjau lokal dengan ukuran/checksum persis
make publish        # unggah dist/ sebagai GitHub Release (butuh gh auth)
```

| Aset | Isi |
|---|---|
| `amba-linux-<versi>.iso` | **ISO prototipe**: kerangka boot UEFI (El Torito, stub `BOOTAA64.EFI`, konfigurasi systemd-boot/GRUB), profil distro, skrip, dan dokumentasi |
| `amba-android-<versi>.apk` | Aplikasi pendamping Android ~5 KB (tanpa Gradle/SDK), tanda tangan v2 |
| `amba-linux-<versi>-source.zip` | Seluruh berkas terlacak git pada commit rilis |
| `amba-linux-<versi>-source.tar.gz` | Isi sama, format tar.gz |
| `SHA256SUMS` | Checksum semua aset di atas |

ISO bukan sistem lengkap: tidak ada kernel, initramfs, root filesystem, atau KDE
Plasma di dalamnya, jadi boot akan berhenti dan belum ada desktop. ISO ini untuk
memeriksa struktur boot dan profil distro sebelum build nyata. Image disk `*.img`
tetap harus dibangun di host Arch Linux ARM aarch64. Rincian alur, kunci
penandatangan, dan cara verifikasi ada di [docs/RELEASE.md](docs/RELEASE.md).

ISO juga dicerminkan di [`releases/v<versi>/`](releases/README.md) di dalam
repositori agar tautan unduhan di halaman rilis selalu hidup; cermin itu tidak
ikut arsip source dan boleh dihapus setelah aset ISO berhasil diunggah ke
GitHub Release.

## Dokumentasi

- [Build image](docs/BUILD.md)
- [Boot UEFI dan kompatibilitas hardware](docs/BOOT-UEFI.md)
- [Pengujian](docs/TESTING.md)
- [Keamanan](docs/SECURITY.md)
- [Arsitektur](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Rilis: ISO, APK, dan source ZIP](docs/RELEASE.md)
- [Halaman unduhan](docs/releases.html)
- [Changelog](docs/CHANGELOG.md)
- [Pihak ketiga dan lisensi](THIRD_PARTY.md)

## Lisensi

Kode dan aset asli proyek ini berada di bawah **AGPL-3.0-or-later** seperti berkas [LICENSE](LICENSE). Paket Arch Linux ARM, kernel Linux, KDE, dan komponen pihak ketiga lainnya tetap mengikuti lisensi masing-masing.
