# Build Amba Linux ARM64

## Syarat

- Host **aarch64 native** yang menjalankan Arch Linux ARM atau turunan dengan `arch-install-scripts`.
- Akses root untuk loop device, mount, dan chroot.
- Internet ke `os.archlinuxarm.org` dan mirror Arch Linux ARM.
- Ruang kosong memadai; image default 16 GiB dan proses sementara membutuhkan ruang tambahan.
- Paket host:

```bash
sudo pacman -Syu --needed arch-install-scripts dosfstools e2fsprogs libarchive util-linux openssl
```

Builder awal **tidak** mendukung cross-build dari Debian/x86_64. Jalur itu memerlukan pacman host yang dikonfigurasi untuk arsitektur target, qemu-user/binfmt, dan penanganan chroot yang berbeda. Menambahkannya nanti lebih aman daripada mencampur paket host x86_64 dan target aarch64.

## 1. Ambil rootfs resmi

```bash
mkdir -p downloads
curl -fL --retry 3 -o downloads/ArchLinuxARM-aarch64-latest.tar.gz \
  https://os.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz
```

Gunakan rootfs **generic aarch64**, bukan rootfs Raspberry Pi atau board tertentu.

Dapatkan nilai SHA-256 dari sumber tepercaya untuk arsip yang tepat. Builder menolak build tanpa `--rootfs-sha256` dan berhenti bila hash tidak cocok. Hash yang dihitung dari file yang sudah terlanjur diubah tidak membuktikan file itu aman; verifikasi tetap harus berasal dari sumber/kanal tepercaya.

## 2. Buat hash password

Jangan masukkan password plaintext sebagai argumen command line.

```bash
umask 077
openssl passwd -6 > ~/amba-password.hash
```

Perintah tersebut meminta password secara interaktif dan menulis hash SHA-crypt. Builder juga menerima yescrypt (`$y$`). Simpan file hash dengan izin ketat, misalnya `chmod 600 ~/amba-password.hash`.

## 3. Jalankan build

```bash
sudo ./scripts/build-image.sh \
  --rootfs downloads/ArchLinuxARM-aarch64-latest.tar.gz \
  --rootfs-sha256 <64-DIGIT-SHA256> \
  --password-hash-file ~/amba-password.hash \
  --user amba \
  --hostname amba \
  --timezone Asia/Jakarta \
  --locale id_ID.UTF-8 \
  --keymap us \
  --size-gib 16
```

Opsi lain:

```text
--output FILE       Nama image keluaran; file yang sudah ada tidak ditimpa
--work-dir DIR      Direktori induk sementara; di repositori, /tmp, atau /var/tmp
--dry-run           Tampilkan rencana tanpa root/perubahan
```

Alur builder:

1. Validasi input, host aarch64, dan SHA-256 rootfs.
2. Buat file image sparse, partisi GPT, ESP FAT32 512 MiB, root ext4.
3. Mount image melalui loop device, ekstrak rootfs, tulis `/etc/fstab`
   (`noatime,errors=remount-ro`) dan aktifkan `ParallelDownloads`.
4. Inisialisasi keyring Arch Linux ARM, **segarkan `archlinuxarm-keyring`**
   (tidak fatal), lalu `pacman -Syu --needed` untuk paket profil.
5. Terapkan overlay Amba: os-release + `VERSION_ID`, hook branding, drop-in
   SDDM, hook systemd-boot, sudoers, mkinitcpio, tuning sysctl/udev/journald,
   tema, dan wallpaper.
6. Tulis `/etc/hostname` dan `/etc/hosts` (termasuk `127.0.1.1`) agar hostname
   ter-resolve lokal.
7. Buat user desktop dengan grup privat, kunci `root` dan `alarm`, aktifkan
   layanan (`NetworkManager`, `sddm`, `systemd-resolved`, `fstrim.timer`,
   `systemd-oomd`, `power-profiles-daemon`). Unit yang tidak tersedia hanya
   memunculkan peringatan, bukan menggagalkan build.
8. Bangun initramfs zstd, pasang `systemd-bootaa64.efi` ke fallback UEFI, tulis
   boot entry.
9. Bersihkan cache, lepas mount/loop, pindahkan image ke `out/`, buat checksum.

Jika gagal, mount/loop dibersihkan oleh trap dan direktori kerja disimpan untuk diagnosis. Jangan menghapus direktori kerja sebelum memastikan tidak ada mount aktif:

```bash
findmnt -R work/
```

## 4. Hasil

```text
out/amba-linux-0.1.0-aarch64.img
out/amba-linux-0.1.0-aarch64.img.sha256
```

Image adalah sistem persisten bergaya installed-system. Ini **bukan** live ISO dengan squashfs/overlay dan bukan installer grafis. Untuk memasang ke media nyata, lihat [BOOT-UEFI.md](BOOT-UEFI.md).

## Kustomisasi

- Paket: `profile/packages.aarch64.txt`
- Identitas rilis: `profile/rootfs/usr/share/amba/os-release`
  (`VERSION_ID` diambil otomatis dari `profile/VERSION` oleh `apply-branding`)
- Tema KDE: `profile/rootfs/usr/share/plasma/look-and-feel/org.amba.linux.desktop/`
- Wallpaper sumber: `scripts/render-wallpaper.py`
- Initramfs: `profile/rootfs/etc/mkinitcpio.conf`
- Tuning runtime: `profile/rootfs/etc/sysctl.d/`,
  `profile/rootfs/etc/udev/rules.d/60-amba-io-scheduler.rules`,
  `profile/rootfs/etc/systemd/zram-generator.conf`,
  `profile/rootfs/etc/systemd/journald.conf.d/`

Setelah mengubah wallpaper:

```bash
make wallpaper            # 3840x2160, ~2 detik
make wallpaper WIDTH=1920 HEIGHT=1080 OUTPUT=out/amba-1080p.png
make check
```

Setelah mengubah tuning, jalankan `make check`: tes offline memastikan setiap
drop-in tetap bisa diparse dan nilainya sesuai dokumentasi.
