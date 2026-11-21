# Pengujian

## Yang dapat dijalankan tanpa host Arch ARM

```bash
make check
make lint        # bila shellcheck tersedia
make dry-run
./scripts/run-qemu.sh --image contoh.img --dry-run
```

Cakupan:

- Syntax semua skrip Bash, termasuk skrip di `profile/rootfs/usr/lib/amba/`.
- Validasi username, hash password (termasuk hash asli dari `openssl passwd -6`),
  ukuran image, dan input berbahaya.
- Paket wajib ARM64/KDE, audio, dan paket performa ada dan tidak duplikat.
- Identitas `os-release`.
- JSON/SVG aset dapat diparse; metadata wallpaper punya `KPackageStructure`.
- Drop-in SDDM berada di direktori yang benar-benar dibaca SDDM
  (`/etc/sddm.conf.d`), tanpa autologin.
- `apply-branding` dijalankan sungguhan pada direktori staging: `ID=amba`,
  `VERSION_ID` muncul tepat sekali, idempoten, dan melaporkan template hilang.
- `update-systemd-boot` menyalin EFI binary ke ESP dan aman bila ESP tidak ada.
- Tuning sysctl/udev/zram/journald dapat diparse dan nilainya sesuai dokumentasi.
- Initramfs tidak memakai `autodetect` host build dan memuat modul virtio.
- Boot entry memakai path ESP yang benar dan `nowatchdog`.
- `files_have_same_size` menolak pasangan pflash yang tidak cocok.
- Render wallpaper berskala (`--width/--height`) deterministik dan menghasilkan
  PNG dengan dimensi IHDR yang diminta.
- QEMU default memakai snapshot, mesin ARM64, dan `virtio-rng-pci`.
- Builder menargetkan file image loopback, bukan disk fisik.
- Drop-in khusus hardening sysctl (`99-amba-security.conf`) eksis dengan nilai keamanan jaringan/kernel: redirect/source-route ditolak, `rp_filter=2`, `log_martians`, `ip_forward=0`, `dmesg_restrict`, `kptr_restrict`, `suid_dumpable=0`, `mmap_min_addr`, perlindungan hardlink/symlink/fifo.
- Builder memaksa `umask 077` dan `chmod 600` untuk image + checksum.
- Builder memakai `check_password_hash_file_permissions` dan menolak file hash yang dapat dibaca group/world.
- APK dibangun lalu diverifikasi ulang secara independen: struktur ZIP, aturan
  `resources.arsc` (stored + 4-byte aligned), blok tanda tangan v2, tanda tangan
  RSA/ECDSA lewat openssl, digest konten v2, isi manifest, dan tabel resource.
- Mengubah satu byte di `classes.dex` harus membuat verifikasi gagal.
- `scripts/make-release.py` menghasilkan APK + ISO + ZIP + TAR.GZ + `SHA256SUMS`
  yang konsisten, dan arsip source hanya memuat berkas terlacak git.
- ISO prototipe diperiksa dengan `pycdlib`: header PE32+ stub UEFI (machine
  AARCH64/AMD64, subsystem EFI), struktur FAT12 El Torito, Rock Ridge/Joliet,
  katalog boot, dan berkas wajib di dalam ISO. README di ISO harus menyatakan
  bahwa image ini prototipe tanpa kernel/rootfs.
- `docs/releases.html` dirender dari `docs/release.json` dan `make release-check`
  gagal bila halaman sudah basi.

Tes ini **tidak** melakukan instalasi paket, mount, chroot, atau boot.

Tes ini **tidak** melakukan instalasi paket, mount, chroot, atau boot.

## Checklist uji boot nyata

Jalankan pada QEMU ARM64 UEFI atau perangkat target:

1. Firmware menemukan `EFI/BOOT/BOOTAA64.EFI`.
2. Menu `systemd-boot` menampilkan Amba Linux.
3. Kernel dan initramfs termuat tanpa panic.
4. Root ext4 ditemukan berdasarkan UUID.
5. `systemctl default` mencapai `graphical.target`.
6. SDDM muncul dan user hasil build dapat login.
7. KDE Plasma, Konsole, Dolphin, dan pengaturan sistem terbuka.
8. NetworkManager memperoleh jaringan.
9. Audio PipeWire, suspend/resume, USB, dan GPU diuji sesuai perangkat.
10. `sudo` meminta password; akun `root`/`alarm` tidak dapat login password.
11. Reboot bersih dan filesystem tidak rusak.
12. `pacman -Syu` setelah rilis tetap mempertahankan branding melalui hook, dan
    `EFI/BOOT/BOOTAA64.EFI` diperbarui oleh hook `91-amba-systemd-boot`.
13. `swapon --show` menunjukkan `/dev/zram0`; `sysctl net.ipv4.tcp_congestion_control`
    melaporkan `bbr`; `systemctl status fstrim.timer` aktif.

Perintah diagnosis di sistem target:

```bash
cat /etc/os-release
uname -a
pacman -Q linux-aarch64 plasma-desktop sddm
bootctl --esp-path=/boot status
systemctl --failed
journalctl -b -p warning
lsblk -f
```

## Status saat ini

- Repositori: fondasi awal.
- Build image: belum dijalankan di lingkungan kerja Debian.
- Boot QEMU/hardware: belum diverifikasi.
- Jangan memasarkan image sebagai rilis stabil sebelum checklist nyata lulus pada perangkat yang dideklarasikan.
