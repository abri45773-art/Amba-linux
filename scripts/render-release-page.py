#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render docs/releases.html from docs/release.json.

The page is generated, never hand-edited: the release manifest is the single
source of truth for versions, asset names and notes. Pass a
release-manifest.json produced by scripts/make-release.py to render a separate
build preview with exact checksums and sizes.

    python3 scripts/render-release-page.py
    python3 scripts/render-release-page.py --check
    python3 scripts/render-release-page.py --manifest dist/release-manifest.json \
        --output dist/releases.html
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_JSON = ROOT / "docs/release.json"
OUTPUT = ROOT / "docs/releases.html"

KIND_LABEL = {
    "apk": "Android",
    "zip": "Arsip",
    "tarball": "Arsip",
    "iso": "ISO",
    "image": "Disk image",
    "sha256": "Verifikasi",
}

KIND_ICON = {
    "apk": "🤖",
    "zip": "🗜️",
    "tarball": "🗜️",
    "iso": "💿",
    "image": "💾",
    "sha256": "🔐",
}


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def download_url(repo: str, version: str, filename: str, template: str | None = None) -> str:
    """Release asset URL, or a template such as a repo mirror of the same file."""
    if template:
        return template.format(repo=repo, version=version, filename=filename)
    return f"https://github.com/{repo}/releases/download/v{version}/{filename}"


def collect_assets(release: dict, built: dict | None) -> list[dict]:
    """Merge the declared assets with any real sizes/checksums from a build."""
    known = {record["file"]: record for record in (built or {}).get("assets", [])}
    version = release["release"]["version"]
    repo = release["project"]["repo"]
    rows = []
    for asset in release["assets"]:
        filename = asset["file"].replace("{version}", version)
        record = known.pop(filename, None)
        pending = asset.get("pending", False) and record is None
        rows.append(
            {
                **asset,
                "filename": filename,
                "url": download_url(repo, version, filename, asset.get("download_url")),
                "record": record,
                "pending": pending,
            }
        )
    for filename, record in known.items():  # files the manifest did not declare
        rows.append(
            {
                "id": filename,
                "kind": "zip",
                "label": filename,
                "filename": filename,
                "description": "Berkas tambahan dari build lokal.",
                "requirements": "-",
                "size_hint": human_size(record["size"]),
                "usage": "Gunakan sesuai catatan rilis yang menyertai berkas ini.",
                "url": download_url(repo, version, filename),
                "record": record,
                "pending": False,
            }
        )
    return rows


def asset_card(asset: dict) -> str:
    kind = asset["kind"]
    pending = asset["pending"]
    record = asset["record"]
    size = human_size(record["size"]) if record else asset["size_hint"]
    checksum = record["sha256"] if record else None
    badge = (
        '<span class="badge badge-pending">belum tersedia</span>'
        if pending
        else '<span class="badge badge-ready">siap</span>'
    )
    if pending:
        action = '<span class="button button-disabled" aria-disabled="true">Belum tersedia</span>'
    else:
        action = f'<a class="button" href="{html.escape(asset["url"])}" rel="noopener">Unduh</a>'
    checksum_row = f'<div class="checksum"><code>{html.escape(checksum)}</code></div>' if checksum else ""
    pending_steps = ""
    if pending:
        pending_steps = (
            '<li><strong>Bangun di host Arch Linux ARM aarch64</strong>'
            "<code>sudo ./scripts/build-image.sh --rootfs … --rootfs-sha256 … --password-hash-file …</code></li>"
            "<li><strong>Unggah ke rilis</strong>"
            "<code>./scripts/publish-release.sh --upload-only --yes</code> lalu render ulang halaman ini</li>"
        )
    steps = f"""
        <ol class="mini-steps">
          <li><strong>Unduh</strong><span>Klik tombol di bawah; berkas tersimpan di folder Download perangkat.</span></li>
          <li><strong>Verifikasi</strong><span>Bandingkan SHA-256 dengan <code>SHA256SUMS</code> sebelum dipakai.</span></li>
          <li><strong>Pakai</strong><span>{html.escape(asset["usage"])}</span></li>
          {pending_steps}
        </ol>"""
    return f"""      <article class="card card-{html.escape(kind)}" id="{html.escape(asset["id"])}">
        <header>
          <span class="icon" aria-hidden="true">{KIND_ICON.get(kind, "📦")}</span>
          <div>
            <h3>{html.escape(asset["label"])}</h3>
            <p class="filename"><code>{html.escape(asset["filename"])}</code></p>
          </div>
          {badge}
        </header>
        <p class="description">{html.escape(asset["description"])}</p>
        <dl>
          <div><dt>Ukuran</dt><dd>{html.escape(size)}</dd></div>
          <div><dt>Kebutuhan</dt><dd>{html.escape(asset["requirements"])}</dd></div>
          <div><dt>Jenis</dt><dd>{KIND_LABEL.get(kind, kind)}</dd></div>
        </dl>
        {checksum_row}
        {steps}
        <footer>{action}</footer>
      </article>"""


def render(release: dict, built: dict | None) -> str:
    project = release["project"]
    rel = release["release"]
    version = rel["version"]
    assets = collect_assets(release, built)
    ready = [asset for asset in assets if not asset["pending"]]
    pending = [asset for asset in assets if asset["pending"]]
    repo_url = f"https://github.com/{project['repo']}"
    branch = project.get("branch", "main")
    apk = built.get("apk") if built else None

    cards = "\n".join(asset_card(asset) for asset in assets)
    pending_list = (
        "<ul>"
        + "".join(f"<li><code>{html.escape(asset['filename'])}</code> — {html.escape(asset['description'])}</li>" for asset in pending)
        + "</ul>"
        if pending
        else "<p>Tidak ada aset yang tertunda.</p>"
    )

    checksum_block = ""
    if built:
        rows = "\n".join(
            f"{record['sha256']}  {record['file']}" for record in sorted(built["assets"], key=lambda r: r["file"])
        )
        checksum_block = f"""      <pre class="codeblock"><code>{html.escape(rows)}</code></pre>"""
    else:
        checksum_block = """      <p class="muted">Checksum muncul di sini setelah <code>scripts/make-release.py</code> dijalankan,
        atau unduh <code>SHA256SUMS</code> langsung dari halaman rilis GitHub.</p>"""

    apk_details = ""
    if apk:
        apk_details = f"""      <dl class="facts">
        <div><dt>Package</dt><dd><code>{html.escape(apk["package"])}</code></dd></div>
        <div><dt>Versi aplikasi</dt><dd>{html.escape(apk["version_name"])} ({apk["version_code"]})</dd></div>
        <div><dt>minSdk / targetSdk</dt><dd>{apk["min_sdk"]} / {apk["target_sdk"]}</dd></div>
        <div><dt>Activity</dt><dd><code>{html.escape(apk["activity"])}</code></dd></div>
        <div><dt>Tanda tangan</dt><dd>{html.escape(apk["signature_scheme"].upper())} · {html.escape(apk["algorithm"])}</dd></div>
        <div><dt>SHA-256 sertifikat</dt><dd><code>{html.escape(apk["certificate_sha256"])}</code></dd></div>
      </dl>"""

    built_note = (
        f"<p class=\"muted\">Halaman ini dirender dari build lokal: {len(ready)} aset dengan checksum terverifikasi.</p>"
        if built
        else "<p class=\"muted\">Halaman ini dirender tanpa hasil build; ukuran memakai perkiraan dari manifest.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rilis {html.escape(project["name"])} {html.escape(version)} — Unduh ISO, APK &amp; Source</title>
<meta name="description" content="{html.escape(project["tagline"])} — unduh ISO prototipe UEFI, APK pendamping, source code ZIP/TAR.GZ, dan checksum rilis {html.escape(version)}.">
<meta name="color-scheme" content="dark">
<link rel="icon" href="../profile/rootfs/usr/share/icons/hicolor/scalable/apps/amba.svg" type="image/svg+xml">
<style>
  :root {{
    --bg: #0f172a;
    --panel: #16233a;
    --panel-2: #1b2b45;
    --line: #27405c;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #2dd4bf;
    --accent-dark: #0f766e;
    --warn: #fbbf24;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "Inter", "Noto Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
    background:
      radial-gradient(1100px 620px at 12% -10%, rgba(45, 212, 191, .18), transparent 60%),
      radial-gradient(900px 520px at 92% 0%, rgba(15, 118, 110, .25), transparent 55%),
      var(--bg);
    color: var(--text);
    line-height: 1.6;
  }}
  a {{ color: var(--accent); }}
  .wrap {{ max-width: 1040px; margin: 0 auto; padding: 40px 22px 72px; }}
  header.hero {{ display: flex; gap: 20px; align-items: center; margin-bottom: 8px; }}
  header.hero img {{ width: 76px; height: 76px; flex: none; }}
  h1 {{ font-size: clamp(1.9rem, 4vw, 2.7rem); margin: 0; letter-spacing: -.02em; }}
  .tagline {{ color: var(--muted); margin: 6px 0 0; }}
  .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 22px 0 30px; }}
  .pill {{
    border: 1px solid var(--line); border-radius: 999px; padding: 5px 13px;
    font-size: .82rem; color: var(--muted); background: rgba(22, 35, 58, .6);
  }}
  .pill strong {{ color: var(--text); font-weight: 600; }}
  section {{ margin-bottom: 44px; }}
  h2 {{ font-size: 1.22rem; margin: 0 0 4px; }}
  .section-note {{ color: var(--muted); margin: 0 0 18px; font-size: .95rem; }}
  .cards {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 310px), 1fr)); }}
  .mini-steps {{ margin: 0; padding-left: 18px; color: var(--muted); font-size: .86rem; display: grid; gap: 6px; }}
  .mini-steps li strong {{ color: var(--text); display: block; font-size: .88rem; }}
  .mini-steps code {{ background: rgba(15, 23, 42, .7); padding: 1px 5px; border-radius: 5px; }}
  .card {{
    background: linear-gradient(180deg, var(--panel), var(--panel-2));
    border: 1px solid var(--line); border-radius: 16px; padding: 20px;
    display: flex; flex-direction: column; gap: 14px;
  }}
  .card header {{ display: flex; align-items: start; gap: 12px; }}
  .card header div {{ flex: 1; min-width: 0; }}
  .icon {{ font-size: 1.5rem; line-height: 1; }}
  .card h3 {{ font-size: 1.02rem; margin: 0; }}
  .filename {{ margin: 2px 0 0; }}
  code {{
    font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .84em;
  }}
  .filename code {{ color: var(--muted); word-break: break-all; }}
  .badge {{ font-size: .7rem; border-radius: 999px; padding: 3px 9px; white-space: nowrap; }}
  .badge-ready {{ background: rgba(45, 212, 191, .16); color: var(--accent); border: 1px solid rgba(45, 212, 191, .4); }}
  .badge-pending {{ background: rgba(251, 191, 36, .12); color: var(--warn); border: 1px solid rgba(251, 191, 36, .35); }}
  .description {{ color: var(--muted); margin: 0; font-size: .92rem; }}
  dl {{ margin: 0; display: grid; gap: 6px; font-size: .88rem; }}
  dl div {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px dashed rgba(148, 163, 184, .16); padding-bottom: 5px; }}
  dt {{ color: var(--muted); }}
  dd {{ margin: 0; text-align: right; }}
  .checksum {{ background: rgba(15, 23, 42, .75); border: 1px solid var(--line); border-radius: 10px; padding: 9px 11px; overflow-x: auto; }}
  .checksum code {{ color: var(--accent); font-size: .74rem; word-break: break-all; }}
  .card footer {{ margin-top: auto; }}
  .button {{
    display: inline-block; text-align: center; width: 100%;
    background: linear-gradient(135deg, var(--accent), var(--accent-dark));
    color: #04231f; font-weight: 700; text-decoration: none;
    padding: 11px 16px; border-radius: 11px;
  }}
  .button:hover {{ filter: brightness(1.08); }}
  .button-disabled {{ background: rgba(148, 163, 184, .16); color: var(--muted); cursor: not-allowed; }}
  .steps {{ counter-reset: step; list-style: none; padding: 0; margin: 0; display: grid; gap: 14px; }}
  .steps li {{ position: relative; padding-left: 44px; color: var(--muted); }}
  .steps li strong {{ color: var(--text); display: block; }}
  .steps li::before {{
    counter-increment: step; content: counter(step);
    position: absolute; left: 0; top: 0; width: 28px; height: 28px; border-radius: 50%;
    display: grid; place-items: center; font-size: .82rem; font-weight: 700;
    color: var(--accent); background: rgba(45, 212, 191, .14); border: 1px solid rgba(45, 212, 191, .4);
  }}
  .codeblock {{
    background: rgba(15, 23, 42, .8); border: 1px solid var(--line); border-radius: 12px;
    padding: 15px 17px; overflow-x: auto; margin: 0;
  }}
  .codeblock code {{ font-size: .8rem; color: #cbd5e1; }}
  .facts {{ grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); display: grid; gap: 8px 24px; }}
  .note {{
    border-left: 3px solid var(--warn); background: rgba(251, 191, 36, .07);
    padding: 13px 16px; border-radius: 0 10px 10px 0; color: var(--muted); font-size: .92rem;
  }}
  .muted {{ color: var(--muted); font-size: .92rem; }}
  footer.page {{ border-top: 1px solid var(--line); margin-top: 20px; padding-top: 20px; color: var(--muted); font-size: .88rem; }}
  footer.page a {{ margin-right: 14px; }}
  @media (prefers-reduced-motion: no-preference) {{
    .card {{ transition: transform .15s ease, border-color .15s ease; }}
    .card:hover {{ transform: translateY(-2px); border-color: rgba(45, 212, 191, .45); }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <img src="../profile/rootfs/usr/share/icons/hicolor/scalable/apps/amba.svg" alt="Logo Amba Linux" width="76" height="76">
    <div>
      <h1>Rilis {html.escape(project["name"])} {html.escape(version)}</h1>
      <p class="tagline">{html.escape(project["tagline"])}</p>
    </div>
  </header>

  <div class="pill-row">
    <span class="pill">Status <strong>{html.escape(rel["status"])}</strong></span>
    <span class="pill">Tanggal <strong>{html.escape(rel["date"])}</strong></span>
    <span class="pill">Arsitektur <strong>{html.escape(project["architecture"])}</strong></span>
    <span class="pill">Firmware <strong>{html.escape(project["firmware"])}</strong></span>
    <span class="pill">Desktop <strong>{html.escape(project["desktop"])}</strong></span>
    <span class="pill">Lisensi <strong>{html.escape(project["license"])}</strong></span>
  </div>

  <section aria-labelledby="catatan">
    <h2 id="catatan">Catatan rilis</h2>
    <p class="section-note">{html.escape(rel["notes"])}</p>
    {built_note}
  </section>

  <section aria-labelledby="unduhan">
    <h2 id="unduhan">Unduhan</h2>
    <p class="section-note">{len(ready)} dari {len(assets)} aset sudah tersedia untuk versi ini.</p>
    <div class="cards">
{cards}
    </div>
  </section>

  <section aria-labelledby="pasang-apk">
    <h2 id="pasang-apk">Memasang APK di Android</h2>
    <ol class="steps">
      <li><strong>Unduh <code>amba-android-{html.escape(version)}.apk</code></strong>
        Berkas ada di kartu “Android (APK)” di atas, atau di halaman rilis GitHub.</li>
      <li><strong>Izinkan sumber tidak dikenal</strong>
        Android akan menampilkan dialog; aktifkan izin hanya untuk aplikasi unduhan/browser yang dipakai.</li>
      <li><strong>Verifikasi checksum (opsional, disarankan)</strong>
        <code>sha256sum amba-android-{html.escape(version)}.apk</code> di Linux,
        <code>shasum -a 256 amba-android-{html.escape(version)}.apk</code> di macOS, atau
        <code>certutil -hashfile amba-android-{html.escape(version)}.apk SHA256</code> di Windows.</li>
      <li><strong>Pasang dan buka</strong>
        Aplikasi menampilkan status rilis, daftar aset, dan catatan; tidak meminta izin jaringan.</li>
    </ol>
    <div class="note">
      APK ditandatangani kunci debug (<code>CN=Amba Linux Release</code>) agar bisa dipasang tanpa
      Play Store. Play Protect dapat menampilkan peringatan untuk aplikasi di luar toko resmi.
    </div>
  </section>

  <section aria-labelledby="apk-detail">
    <h2 id="apk-detail">Detail APK</h2>
    <p class="section-note">Metadata yang dibaca langsung dari APK hasil build.</p>
    {apk_details or '<p class="muted">Detail APK muncul setelah build dijalankan.</p>'}
  </section>

  <section aria-labelledby="checksum">
    <h2 id="checksum">Checksum SHA-256</h2>
    <p class="section-note">Bandingkan dengan <code>SHA256SUMS</code> dari halaman rilis.</p>
{checksum_block}
  </section>

  <section aria-labelledby="belum">
    <h2 id="belum">Yang belum tersedia</h2>
    <p class="section-note">Aset ini tidak dibuat oleh pipeline rilis karena membutuhkan host atau perangkat khusus.</p>
    {pending_list}
    <div class="note">
      Image disk ARM64 dibangun di host Arch Linux ARM aarch64 dengan
      <code>scripts/build-image.sh</code> (lihat <code>docs/BUILD.md</code>), lalu diunggah sebagai
      <code>amba-linux-{html.escape(version)}-aarch64.img</code>. Tanpa build nyata, halaman ini tidak
      akan menautkannya.
    </div>
  </section>

  <section aria-labelledby="bangun">
    <h2 id="bangun">Bangun ulang dari source</h2>
    <p class="section-note">Seluruh aset dapat dibuat ulang dari arsip source yang sama.</p>
    <pre class="codeblock"><code>tar xzf amba-linux-{html.escape(version)}-source.tar.gz
cd amba-linux-{html.escape(version)}
make check                      # tes offline
python3 scripts/make-release.py --dry-run
python3 scripts/make-release.py # APK + ISO + ZIP + TAR.GZ + SHA256SUMS di dist/</code></pre>
  </section>

  <footer class="page">
    <p>
      <a href="{html.escape(repo_url)}">Repositori</a>
      <a href="{html.escape(repo_url)}/releases">Semua rilis</a>
      <a href="{html.escape(repo_url)}/blob/{html.escape(branch)}/docs/BUILD.md">Panduan build</a>
      <a href="{html.escape(repo_url)}/blob/{html.escape(branch)}/docs/SECURITY.md">Keamanan</a>
      <a href="{html.escape(repo_url)}/issues">Laporkan masalah</a>
    </p>
    <p>Halaman ini dibuat otomatis oleh <code>scripts/render-release-page.py</code> dari
      <code>docs/release.json</code> — jangan disunting manual.
      Kode Amba Linux: {html.escape(project["license"])}. Proyek ini tidak berafiliasi dengan Arch Linux
      atau Arch Linux ARM.</p>
  </footer>
</div>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render halaman rilis HTML")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="release-manifest.json hasil build untuk pratinjau dengan checksum",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="gagal bila halaman belum sesuai")
    args = parser.parse_args(argv)

    release = load(RELEASE_JSON)
    built = load(args.manifest) if args.manifest is not None and args.manifest.is_file() else None
    page = "\n".join(line.rstrip() for line in render(release, built).splitlines()) + "\n"

    if args.check:
        current = args.output.read_text() if args.output.is_file() else ""
        if current != page:
            print(f"{args.output} tidak sesuai; jalankan scripts/render-release-page.py", file=sys.stderr)
            return 1
        print(f"{args.output} sudah sesuai.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page)
    state = "dengan checksum build lokal" if built else "tanpa data build"
    print(f"Halaman rilis ditulis ke {args.output} ({state}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
