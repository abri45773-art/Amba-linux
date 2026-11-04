# Changelog

Semua perubahan penting proyek ini dicatat di sini. Format mengikuti
[Keep a Changelog](https://keepachangelog.com/) dan versi mengikuti
`profile/VERSION`.

## 0.1.0 — perbaikan bug & optimasi (belum dirilis)

### Bug yang diperbaiki

1. **Konfigurasi SDDM tidak pernah terbaca.** Drop-in dipindahkan dari
   `etc/sddm/conf.d/` ke `etc/sddm.conf.d/`. SDDM hanya membaca
   `/usr/lib/sddm/sddm.conf.d`, `/etc/sddm.conf.d`, dan `/etc/sddm.conf`
   (`man 5 sddm.conf`), sehingga nama tema, cursor theme, dan `HideUsers=alarm`
   sebelumnya diabaikan tanpa peringatan.
2. **Hostname tidak ter-resolve lokal.** Builder kini menulis `/etc/hosts` berisi
   `127.0.1.1 <hostname>.localdomain <hostname>`. Tanpa entri ini `sudo` dan
   aplikasi Plasma menunggu lookup DNS dan gagal saat perangkat offline.
3. **`os-release` tanpa versi.** `/usr/lib/amba/apply-branding` sekarang
   menyuntikkan `VERSION_ID` dan `VERSION` dari `/usr/share/amba/VERSION`, dan
   idempoten sehingga aman dipanggil ulang oleh hook pacman.
4. **`apply-branding` gagal tanpa pesan jelas** bila template hilang; sekarang
   melaporkan path yang bermasalah ke stderr.
5. **Bootloader tidak ikut diperbarui.** Ditambahkan
   `etc/pacman.d/hooks/91-amba-systemd-boot.hook` dan
   `/usr/lib/amba/update-systemd-boot` agar `BOOTAA64.EFI` di ESP selalu sama
   dengan `systemd` yang terpasang.
6. **Satu unit systemd opsional bisa menggagalkan seluruh build.** Unit sekarang
   diaktifkan lewat `enable_units`/`unit_present`, dan langkah opsional memakai
   `chroot_soft` yang memberi peringatan alih-alih menghentikan build.
7. **Keyring ALARM yang basi.** Builder menyegarkan `archlinuxarm-keyring`
   sebelum upgrade penuh; kegagalan langkah ini tidak fatal karena verifikasi
   tanda tangan tetap wajib pada `pacman -Syu`.
8. **Hash password ber-CRLF ditolak.** `\r` dari file yang dibuat di Windows
   dibuang sebelum validasi.
9. **`run-qemu.sh` tidak memeriksa pasangan pflash.** Firmware dan template VARS
   yang ukurannya berbeda membuat QEMU gagal dengan pesan tidak terkait;
   sekarang diperiksa lebih awal memakai `files_have_same_size`.
10. **Metadata wallpaper tidak dikenali Plasma 6.** `metadata.json` kini memuat
    `"KPackageStructure": "Wallpaper/Images"` dan sudah diuji agar `Id` sama
    dengan nama direktori.
11. **Grup primer bersama.** `useradd` memakai `--user-group` sehingga akun
    desktop mendapat grup privat, bukan grup `users` bersama.
12. **`/var/log/journal` tidak pernah ada** meski `Storage=persistent`; direktori
    sekarang dibuat builder dengan pemilik `root:systemd-journal`.

### Optimasi

1. Root ext4 memakai `noatime,errors=remount-ro` (tidak ada penulisan metadata
   per pembacaan di eMMC/NVMe/SD).
2. **zram** sebagai swap terkompresi di RAM (`zram-generator`, zstd, prioritas
   100) — penting untuk board ARM ber-RAM 2–8 GiB dengan storage lambat.
3. `sysctl`: `vm.swappiness=180`, `page-cluster=0`, `vfs_cache_pressure=50`,
   rasio dirty lebih rendah, `vm.max_map_count=1048576`.
4. Jaringan: `fq` + **BBR**, `tcp_fastopen=3`, `tcp_mtu_probing=1`, modul dimuat
   lewat `modules-load.d` sebelum `systemd-sysctl`.
5. Batas inotify dinaikkan (Plasma indexing, IDE) agar tidak "too many open files".
6. Aturan udev memilih `none` untuk NVMe/virtio-blk, `mq-deadline` untuk
   eMMC/SATA, plus read-ahead lebih besar.
7. Journald dibatasi (200 MB sistem, 32 MB per file) dan dikompresi.
8. Initramfs: zstd (`-T0`) menggantikan gzip, ditambah modul
   `virtio_pci/blk/scsi/net/gpu` eksplisit.
9. `fstrim.timer`, `systemd-oomd`, dan `power-profiles-daemon` diaktifkan.
10. systemd-boot: `timeout 3` (dari 5) dan `nowatchdog` pada kernel cmdline.
11. `ParallelDownloads = 5` pada `pacman.conf` target (build dan update lebih cepat).
12. Profil paket dilengkapi: `pipewire-alsa`, `alsa-ucm-conf`, `alsa-utils`,
    `polkit-kde-agent`, `zram-generator`, `power-profiles-daemon`,
    `noto-fonts-emoji`, `xdg-utils`.
13. `scripts/render-wallpaper.py` ditulis ulang: **1,6 s** untuk 4K
    (sebelumnya 15,8 s hanya untuk loop render), mendukung `--width/--height/
    --output/--level`. Pixel output identik dalam toleransi ±2/255.
14. QEMU: `virtio-rng-pci` mengurangi boot yang tertahan menunggu entropi.
15. Tes offline bertambah dari 22 menjadi 44, termasuk eksekusi
    `apply-branding`, `update-systemd-boot`, dan render wallpaper berskala.
16. **`/tmp` sebagai tmpfs** (`size=50%`, `noatime`) di fstab yang dibuat
    builder — file sementara tidak lagi menulis ke eMMC/SD; halaman melebihi
    batas dapat di-swap ke zram (prioritas 100).
17. **Interval writeback diperpanjang** (`vm.dirty_expire_centisecs=3000`,
    `vm.dirty_writeback_centisecs=1500`) sehingga flush digabung menjadi lebih
    sedikit dan besar, mengurangi wear flash dan jitter I/O.
18. **sysctl desktop/jaringan baru**: `kernel.sched_autogroup_enabled=1` (job
    latar belakang tidak mengganggu jendela interaktif), `fs.file-max=2097152`,
    `kernel.nmi_watchdog=0`, `tcp_tw_reuse=1`, `somaxconn`/`tcp_max_syn_backlog`
    4096.
19. **Baloo nonaktif default** (`/etc/xdg/baloofilerc`) — indexer tidak lagi
    membebani CPU dan eMMC saat login di board ARM lemah; dapat diaktifkan
    kembali via System Settings atau `~/.config/baloofilerc`.
20. **systemd fail-fast**: `DefaultTimeoutStartSec=20s`, `DefaultTimeoutStopSec
    =10s`, `DefaultDeviceTimeoutSec=10s` agar satu unit macet tidak menahan
    boot selama 90 detik.
