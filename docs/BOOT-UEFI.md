# Boot UEFI ARM64

## Layout image

```text
GPT
├── AMBA_ESP   512 MiB  FAT32  /boot
│   ├── EFI/BOOT/BOOTAA64.EFI
│   ├── EFI/systemd/systemd-bootaa64.efi
│   ├── loader/loader.conf
│   ├── loader/entries/amba.conf
│   ├── Image
│   └── initramfs-linux.img
└── AMBA_ROOT  sisa     ext4   /
```

Boot entry memakai:

```text
linux   /Image
initrd  /initramfs-linux.img
options root=UUID=<uuid-root> rw rootwait console=tty0 console=ttyAMA0,115200
```

Path kernel/initrd relatif terhadap ESP karena ESP dipasang di `/boot`. Kernel `linux-aarch64` Arch Linux ARM menyediakan EFI stub. DTB/ACPI harus disediakan oleh firmware UEFI; image generik tidak menanam satu DTB board tertentu.

## QEMU

Pasang firmware:

```bash
# Arch Linux host
sudo pacman -S --needed qemu-system-aarch64 edk2-aarch64

# Debian/Ubuntu host
sudo apt install qemu-system-arm qemu-efi-aarch64
```

Jalankan:

```bash
./scripts/run-qemu.sh --image out/amba-linux-0.1.0-aarch64.img
```

Secara default skrip memakai TCG dan `-snapshot`. Untuk host ARM64 dengan KVM:

```bash
./scripts/run-qemu.sh \
  --image out/amba-linux-0.1.0-aarch64.img \
  --accel kvm --persistent
```

`--persistent` menulis perubahan ke image. Buat salinan image sebelum pengujian merusak.

Mode serial:

```bash
./scripts/run-qemu.sh --image out/amba-linux-0.1.0-aarch64.img --headless
```

Keluar dari QEMU serial: `Ctrl-A`, lalu `X`.

Jika firmware tidak terdeteksi otomatis, berikan path eksplisit:

```bash
./scripts/run-qemu.sh \
  --image out/amba-linux-0.1.0-aarch64.img \
  --firmware /path/QEMU_EFI.fd \
  --vars /path/vars-template-pflash.raw
```

## Menulis ke USB/media nyata

**Peringatan:** perintah berikut menghapus seluruh isi disk tujuan. Salah memilih device dapat menghancurkan data. Proyek ini belum menyediakan installer aman dengan konfirmasi partisi otomatis.

1. Cadangkan data.
2. Identifikasi disk removable dengan `lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS`.
3. Pastikan semua partisi disk tujuan tidak sedang di-mount.
4. Tulis image ke **seluruh disk**, bukan ke partisi seperti `/dev/sdX1`.

Contoh manual, ganti `/dev/TARGET` hanya setelah verifikasi:

```bash
sudo umount /dev/TARGET?* 2>/dev/null || true
sudo dd if=out/amba-linux-0.1.0-aarch64.img of=/dev/TARGET \
  bs=16M conv=fsync status=progress
```

Jangan menjalankan contoh tersebut pada disk sistem. Setelah menulis, partisi root dapat diperluas dengan tool partisi/filesystem yang sesuai; lakukan dari lingkungan lain, bukan dari root yang sedang dipakai.

## Batasan hardware

- Target awal adalah **UEFI ARM64 generik**, bukan semua perangkat ARM.
- Laptop/server ARM tertentu memerlukan firmware yang menyediakan ACPI/DTB sesuai, dukungan storage, GPU, input, dan jaringan oleh kernel.
- Secure Boot belum didukung; firmware harus mengizinkan boot unsigned atau dikonfigurasi sesuai kebutuhan.
- Raspberry Pi dan board U-Boot memerlukan jalur boot/firmware berbeda dan bukan target image ini.
- Jika perangkat tidak menampilkan menu boot ARM64, periksa firmware dan dokumentasi vendor sebelum mengubah image.
