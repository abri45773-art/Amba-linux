# Pengujian

## Yang dapat dijalankan tanpa host Arch ARM

```bash
make check
make lint        # bila shellcheck tersedia
make dry-run
./scripts/run-qemu.sh --image contoh.img --dry-run
```

Cakupan:

- Syntax semua skrip Bash.
- Validasi username, hash password, ukuran image, dan input berbahaya.
- Paket wajib ARM64/KDE ada dan tidak duplikat.
- Identitas `os-release`.
- JSON/SVG aset dapat diparse.
- Tidak ada autologin SDDM atau sudo tanpa password.
- Initramfs tidak memakai `autodetect` host build.
- Boot entry memakai path ESP yang benar.
- QEMU default memakai snapshot dan mesin ARM64.
- Builder menargetkan file image loopback, bukan disk fisik.

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
12. `pacman -Syu` setelah rilis tetap mempertahankan branding melalui hook.

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
