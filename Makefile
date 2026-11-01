.PHONY: check test lint dry-run wallpaper image

check: test

test:
	@set -e; for script in scripts/*.sh scripts/lib/*.sh profile/rootfs/usr/lib/amba/*; do bash -n "$$script"; done
	python3 -m unittest discover -s tests -v

lint:
	shellcheck scripts/*.sh scripts/lib/*.sh profile/rootfs/usr/lib/amba/*

dry-run:
	./scripts/build-image.sh --dry-run

# Default: 3840x2160 into the profile. Override for other sizes, e.g.
#   make wallpaper WIDTH=1920 HEIGHT=1080 OUTPUT=out/amba-1080p.png
wallpaper:
	@args=""; \
	if [ -n "$(WIDTH)" ]; then args="$$args --width $(WIDTH)"; fi; \
	if [ -n "$(HEIGHT)" ]; then args="$$args --height $(HEIGHT)"; fi; \
	if [ -n "$(OUTPUT)" ]; then args="$$args --output $(OUTPUT)"; fi; \
	if [ -n "$(LEVEL)" ]; then args="$$args --level $(LEVEL)"; fi; \
	python3 scripts/render-wallpaper.py $$args

# Example: make image ROOTFS=downloads/rootfs.tar.gz SHA256=... HASH_FILE=/secure/amba.hash
image:
	./scripts/build-image.sh --rootfs "$(ROOTFS)" --rootfs-sha256 "$(SHA256)" --password-hash-file "$(HASH_FILE)" $(IMAGE_ARGS)
