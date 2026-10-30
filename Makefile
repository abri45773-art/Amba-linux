.PHONY: check test lint dry-run wallpaper image

check: test
test:
	@set -e; for script in scripts/*.sh scripts/lib/*.sh profile/rootfs/usr/lib/amba/apply-branding; do bash -n "$$script"; done
	python3 -m unittest discover -s tests -v

lint:
	shellcheck scripts/*.sh scripts/lib/*.sh profile/rootfs/usr/lib/amba/apply-branding

dry-run:
	./scripts/build-image.sh --dry-run

wallpaper:
	python3 scripts/render-wallpaper.py

# Example: make image ROOTFS=downloads/rootfs.tar.gz SHA256=... HASH_FILE=/secure/amba.hash
image:
	./scripts/build-image.sh --rootfs "$(ROOTFS)" --rootfs-sha256 "$(SHA256)" --password-hash-file "$(HASH_FILE)" $(IMAGE_ARGS)
