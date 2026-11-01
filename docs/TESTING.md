# Pengujian

## Yang dapat dijalankan tanpa host Arch ARM

```bash
make check
make lint        # bila shellcheck tersedia
make dry-run
./scripts/run-qemu.sh --image contoh.img --dry-run
```

Cakupan (44 tes):

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
