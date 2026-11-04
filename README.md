# Amba Linux

**Amba Linux** adalah proyek distro Linux berbasis **Arch Linux ARM** untuk **ARM64 (aarch64) UEFI**, dengan desktop **KDE Plasma**.

> Status: **fondasi awal / prototipe siap-build**, bukan rilis ISO yang sudah diuji boot.
> Repositori ini berisi konfigurasi, branding, skrip pembuat image, skrip uji QEMU, dan tes offline.
> Image belum dibangun di lingkungan kerja ini karena build memerlukan host Arch Linux ARM aarch64 dan akses repositori paket Arch Linux ARM.

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
  lib/common.sh              Validasi dan helper boot entry
tests/                       Tes statis/offline
docs/                        Panduan build, boot, pengujian, keamanan, changelog
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
make check       # syntax Bash + unit test offline (44 tes)
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
- Builder hanya membuat **file image loopback**, tidak mempartisi disk fisik.
- QEMU secara default memakai `-snapshot`; perubahan disk dibuang kecuali `--persistent`.

## Dokumentasi

- [Build image](docs/BUILD.md)
- [Boot UEFI dan kompatibilitas hardware](docs/BOOT-UEFI.md)
- [Pengujian](docs/TESTING.md)
- [Keamanan](docs/SECURITY.md)
- [Arsitektur](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Changelog](docs/CHANGELOG.md)
- [Pihak ketiga dan lisensi](THIRD_PARTY.md)

## Lisensi

Kode dan aset asli proyek ini berada di bawah **AGPL-3.0-or-later** seperti berkas [LICENSE](LICENSE). Paket Arch Linux ARM, kernel Linux, KDE, dan komponen pihak ketiga lainnya tetap mengikuti lisensi masing-masing.
