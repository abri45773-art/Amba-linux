# Keamanan

## Keputusan awal

- Tidak ada password bawaan.
- Builder wajib menerima hash password, bukan plaintext.
- `root` dikunci; user bawaan `alarm` dikunci dan kedaluwarsa.
- Tidak ada autologin SDDM.
- `wheel` memerlukan password untuk sudo.
- OpenSSH server tidak dipasang oleh profil awal.
- Secure Boot belum diimplementasikan.
- Builder hanya memanipulasi file image melalui loop device.
- Output yang sudah ada tidak ditimpa.
- QEMU default membuang perubahan disk (`-snapshot`).

## Batas kepercayaan

- SHA-256 rootfs mendeteksi perubahan file, tetapi bukan tanda tangan kriptografis. Dapatkan hash dari sumber tepercaya.
- Paket setelah bootstrap diverifikasi oleh keyring/pacman Arch Linux ARM.
- Jangan menonaktifkan verifikasi tanda tangan pacman.
- Jangan menjalankan build dari rootfs atau overlay yang tidak dipercaya; proses chroot/package installation berjalan sebagai root.
- File hash password tetap sensitif. Jangan commit `*.hash`, password, token, atau kunci privat.
- Image berisi hash password user dan data sistem; perlakukan sebagai data pribadi.

## Melaporkan masalah

Buka issue di repositori proyek dengan informasi perangkat, versi image, dan log yang sudah disensor. Jangan mengirim password, hash password, kunci privat, atau data pribadi.
