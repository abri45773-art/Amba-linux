# Roadmap

## 0.1 — fondasi awal

- [x] Profil paket ARM64 KDE Plasma
- [x] Branding Amba dan hook pacman
- [x] Builder image GPT/UEFI native aarch64
- [x] Skrip QEMU ARM64 UEFI
- [x] Tes offline dan dokumentasi
- [ ] Build nyata di host Arch Linux ARM
- [ ] Uji boot QEMU dan perangkat target

## 0.2 — verifikasi hardware

- [ ] Tentukan laptop/server ARM64 referensi
- [ ] Catat firmware, ACPI/DTB, GPU, Wi-Fi, audio, suspend
- [ ] Tambahkan quirks hanya bila diperlukan
- [ ] Uji upgrade paket dan regenerasi initramfs

## 0.3 — distribusi

- [ ] Build terotomasi pada runner ARM64 tepercaya
- [ ] Manifest versi paket dan checksum rilis
- [ ] Installer dengan konfirmasi disk yang lebih aman
- [ ] Kebijakan pembaruan dan pengumuman keamanan

## Nanti bila diperlukan

- [ ] Paket kernel `linux-amba` terpisah
- [ ] Secure Boot dengan manajemen kunci yang jelas
- [ ] Live image dengan overlay sementara
- [ ] Cross-build x86_64 → aarch64 yang teruji
