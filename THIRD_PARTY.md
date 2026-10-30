# Pihak ketiga dan lisensi

Proyek ini tidak berafiliasi dengan Arch Linux atau Arch Linux ARM.

- **Arch Linux ARM**: rootfs, repositori paket, keyring, dan kernel `linux-aarch64`. Lisensi mengikuti masing-masing paket/hulu.
  - https://archlinuxarm.org/
  - https://github.com/archlinuxarm/PKGBUILDs
- **Linux kernel**: GPL-2.0-only; sumber dan konfigurasi paket Arch Linux ARM.
- **KDE Plasma / SDDM / aplikasi KDE**: lisensi masing-masing proyek.
- **systemd / systemd-boot**: lisensi proyek systemd.
- **Arch `arch-install-scripts`**: referensi alur chroot; tidak disalin ke repositori ini.
- **Archboot / archiso**: dipelajari sebagai referensi ekosistem; kode mereka tidak disalin ke proyek ini.

Kode skrip, konfigurasi asli, logo SVG, dan wallpaper Amba dalam repositori ini dilisensikan AGPL-3.0-or-later. Komponen pihak ketiga yang dipasang ke image tetap berada di bawah lisensinya sendiri; lihat metadata paket dan berkas `/usr/share/licenses/` pada sistem target.
