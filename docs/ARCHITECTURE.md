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
