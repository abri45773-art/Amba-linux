.PHONY: check test lint dry-run wallpaper image iso release release-check page page-preview publish verify-apk clean-release

check: test

test:
	@set -e; for script in scripts/*.sh scripts/lib/*.sh profile/rootfs/usr/lib/amba/*; do bash -n "$$script"; done
	python3 -m unittest discover -s tests -v

lint:
	shellcheck scripts/*.sh scripts/lib/*.sh profile/rootfs/usr/lib/amba/*
	python3 -m py_compile scripts/make-release.py scripts/render-release-page.py scripts/build-iso.py tools/apkbuild/*.py

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

# Build bootable UEFI ISO: make iso [OUTPUT=dist/amba-linux-<ver>.iso] [FORCE=1]
iso:
	python3 scripts/build-iso.py $(if $(OUTPUT),--output "$(OUTPUT)",) $(if $(FORCE),--force,)

# Release assets: dist/amba-android-<ver>.apk, dist/amba-linux-<ver>.iso,
# dist/*-source.{zip,tar.gz}, dist/SHA256SUMS, dist/release-notes.md.
# The APK key defaults to dist/keys; the ISO needs pycdlib
# (pip install -r requirements-dev.txt).
#   make release KEYSTORE=~/.amba-keys FORCE=1
#   make release SKIP_ISO=1              # tanpa pycdlib
release:
	python3 scripts/make-release.py $(if $(KEYSTORE),--keystore "$(KEYSTORE)",) $(if $(SKIP_ISO),--skip-iso,) $(if $(FORCE),--force,)

# Re-render the canonical, checksum-independent page committed to the repo.
page:
	python3 scripts/render-release-page.py

# Render a local page containing exact checksums without modifying docs/.
page-preview:
	python3 scripts/render-release-page.py --manifest dist/release-manifest.json --output dist/releases.html

# Fail if docs/releases.html or the release pipeline drifted from its sources.
release-check:
	python3 scripts/render-release-page.py --check
	python3 -m unittest tests.test_release -v

# Verify one generated APK: make verify-apk APK=dist/amba-android-0.1.0.apk
verify-apk:
	@test -n "$(APK)" || { echo "Gunakan: make verify-apk APK=path/to/app.apk" >&2; exit 2; }
	python3 tools/apkbuild/verify_apk.py "$(APK)"

# Undo generated release assets (keeps dist/keys).
clean-release:
	rm -rf dist/amba-android-*.apk dist/*-source.zip dist/*-source.tar.gz dist/*.iso dist/*.iso.sha256 \
	       dist/SHA256SUMS dist/release-manifest.json dist/release-notes.md dist/releases.html

# Publish dist/ as a GitHub Release (needs gh auth). Asks before acting.
publish:
	./scripts/publish-release.sh
