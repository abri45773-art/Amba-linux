# Arsitektur

Amba Linux awal adalah **profil distro + pembuat image**, bukan fork kernel dan bukan installer grafis.

```text
Arch Linux ARM aarch64 rootfs
        │
        ▼
pacman -Syu + packages.aarch64.txt
        │
        ▼
overlay profile/rootfs
  ├── os-release Amba + pacman hook
  ├── mkinitcpio generik
  ├── SDDM / NetworkManager / Bluetooth
  ├── sudoers wheel dengan password
  └── KDE look-and-feel + wallpaper
        │
        ▼
user build-time + root/alarm terkunci
        │
        ▼
GPT image: ESP FAT32 + root ext4
  └── systemd-boot ARM64 + linux-aarch64 + initramfs
```

## Mengapa bukan archiso?

`archiso` adalah tool profil live/installation media Arch. Jalur ARM64 memiliki kebutuhan firmware, rootfs, dan boot yang berbeda. Untuk target awal ARM64 UEFI, proyek ini memakai rootfs Arch Linux ARM dan image disk persisten agar alurnya eksplisit. Ini bukan live ISO.

## Mengapa kernel standar?

Nama distro tidak harus sama dengan nama kernel. `linux-aarch64` memberikan konfigurasi multi-platform dan pembaruan keamanan dari Arch Linux ARM. Kernel kustom `linux-amba` dapat ditambahkan nanti sebagai paket terpisah setelah ada kebutuhan patch, konfigurasi, dan siklus pembaruan yang jelas.

## Identitas rilis

`filesystem` Arch dapat memperbarui `/usr/lib/os-release`. Hook pacman `90-amba-branding.hook` memanggil `/usr/lib/amba/apply-branding` setelah install/upgrade path tersebut agar identitas Amba diterapkan kembali.

## Aset

Logo SVG dan wallpaper dibuat untuk proyek ini. Wallpaper dirender oleh `scripts/render-wallpaper.py` memakai Python standard library sehingga tidak memerlukan ImageMagick atau layanan eksternal.

## Optimasi runtime

Optimasi disimpan sebagai drop-in kecil di overlay supaya mudah ditinjau dan
dicabut tanpa menyentuh paket Arch Linux ARM.

| Berkas | Alasan |
|---|---|
| `etc/fstab` (dibuat builder) | `noatime` menghapus penulisan metadata per pembacaan; `errors=remount-ro` membuat fs korup berhenti menulis, bukan lanjut diam-diam |
| `etc/systemd/zram-generator.conf` | Swap terkompresi di RAM (zstd); board ARM sering ber-RAM kecil dengan eMMC/SD lambat |
| `etc/sysctl.d/99-amba-memory.conf` | `swappiness=180` + `page-cluster=0` cocok untuk zram; `max_map_count` untuk browser/Plasma |
| `etc/sysctl.d/99-amba-network.conf` | `fq` + BBR menurunkan latensi tanpa tuning manual |
| `etc/sysctl.d/99-amba-desktop.conf` | Batas inotify default terlalu kecil untuk Plasma indexing dan IDE |
| `etc/udev/rules.d/60-amba-io-scheduler.rules` | `none` untuk NVMe/virtio-blk, `mq-deadline` untuk eMMC/SATA |
| `etc/systemd/journald.conf.d/10-amba.conf` | Journal dibatasi agar image 16 GiB tidak penuh oleh log |
| `etc/mkinitcpio.conf` | zstd `-T0` lebih cepat dibuat dan didekompresi; modul virtio eksplisit |

Yang **tidak** diubah secara default: mitigasi CPU, Secure Boot, dan governor
CPU. Mitigasi dimatikan hanya bila pengguna mengukur sendiri keuntungannya;
governor diserahkan ke `power-profiles-daemon` agar profil daya perangkat nyata
tidak dipaksa.

## Bug yang pernah ada

Riwayat perbaikan (termasuk drop-in SDDM yang salah direktori, `/etc/hosts` yang
tidak ditulis, dan bootloader yang tidak ikut diperbarui setelah upgrade
`systemd`) ada di [CHANGELOG.md](CHANGELOG.md).
