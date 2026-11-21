# Keamanan

## Keputusan awal

- Tidak ada password bawaan.
- Builder wajib menerima hash password, bukan plaintext.
- `root` dikunci; user bawaan `alarm` dikunci dan kedaluwarsa.
- Tidak ada autologin SDDM.
- `wheel` memerlukan password untuk sudo.
- OpenSSH server tidak dipasang oleh profil awal.
- sysctl hardening drop-in diaktifkan: redirect/source-route ditolak,
  `rp_filter=2`, `log_martians=1`, `ip_forward=0`, IPv6
  `accept_redirects=0`, `kernel.dmesg_restrict=1`,
  `kernel.kptr_restrict=1`, `fs.suid_dumpable=0`, perlindungan
  hardlink/symlink/fifo diaktifkan, `vm.mmap_min_addr=65536`.
- Image output dan checksum privat (`chmod 600`, `umask 077`): image berisi
  hash password dan tidak boleh bocor ke pengguna lain di mesin build.
- Builder menolak file hash password yang dapat dibaca group/world.
- Secure Boot belum diimplementasikan.
- Builder hanya memanipulasi file image melalui loop device.
- Output yang sudah ada tidak ditimpa.
- QEMU default membuang perubahan disk (`-snapshot`).
- Swap memakai zram di RAM: halaman swap tidak pernah ditulis ke storage.
- Journal dibatasi (200 MB sistem) agar log tidak memenuhi image 16 GiB.
- Hook `91-amba-systemd-boot` menjaga binary bootloader di ESP tetap sama
  dengan `systemd` yang terpasang.

## Batas kepercayaan

- SHA-256 rootfs mendeteksi perubahan file, tetapi bukan tanda tangan kriptografis. Dapatkan hash dari sumber tepercaya.
- Paket setelah bootstrap diverifikasi oleh keyring/pacman Arch Linux ARM.
- Jangan menonaktifkan verifikasi tanda tangan pacman.
- Setelan sysctl hardening bukan pengganti firewall. Tidak ada firewall
  aktif secara default (OpenSSH tidak dipasang, tetapi jaringan lokal tetap
  terbuka normal). Untuk jaringan tidak tepercaya, tambahkan aturan nftables
  default-inbound-deny sebelum deployment.
- `rp_filter=2` (loose) dipilih agar VPN/multi-interface desktop tetap
  berfungsi; gunakan `rp_filter=1` (strict) hanya bila perangkat pasti satu
  interface saja.
- Baca `dmesg` membutuhkan root setelah `kernel.dmesg_restrict=1`; diagnostik
  kernel lewat `journalctl -k` tetap tersedia bagi yang memiliki hak.
- Jangan menjalankan build dari rootfs atau overlay yang tidak dipercaya; proses chroot/package installation berjalan sebagai root.
- `nowatchdog` pada kernel cmdline mematikan lockup/NMI watchdog; ini
  menghemat tick CPU tetapi menghilangkan deteksi soft-lockup otomatis.
  Hapus opsi tersebut dari `render_boot_entry` bila deteksi itu dibutuhkan.
- Setelan `vm.swappiness` tinggi hanya aman karena swap berupa zram; jangan
  menambahkan partisi swap disk tanpa menurunkannya kembali.
- File hash password tetap sensitif. Jangan commit `*.hash`, password, token, atau kunci privat.
- Image berisi hash password user dan data sistem; perlakukan sebagai data pribadi.

## Melaporkan masalah

Buka issue di repositori proyek dengan informasi perangkat, versi image, dan log yang sudah disensor. Jangan mengirim password, hash password, kunci privat, atau data pribadi.
