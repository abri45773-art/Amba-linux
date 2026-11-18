#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Independent checker for the Amba companion APK.

Checks ZIP integrity/alignment, APK Signature Scheme v2 structure, the embedded
certificate/public-key relationship, the cryptographic signature (via openssl),
the Android chunked content digest, and binary manifest/resource metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axml import CHUNK_STRING_POOL, CHUNK_XML, CHUNK_XML_RESOURCE_MAP, CHUNK_XML_START_ELEMENT
from signing import ALGORITHMS, APK_SIG_BLOCK_MAGIC, CHUNK_SIZE, V2_BLOCK_ID


class VerifyError(AssertionError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)


def read_lp(buf: bytes, offset: int) -> tuple[bytes, int]:
    """Read one little-endian uint32 length prefix and its payload."""
    _require(0 <= offset <= len(buf) - 4, "panjang uint32 melewati batas buffer")
    (length,) = struct.unpack_from("<I", buf, offset)
    end = offset + 4 + length
    _require(end <= len(buf), f"payload {length} byte melewati batas buffer")
    return buf[offset + 4 : end], end


def read_records(sequence: bytes, label: str) -> list[bytes]:
    records = []
    offset = 0
    while offset < len(sequence):
        record, offset = read_lp(sequence, offset)
        _require(record, f"record kosong dalam {label}")
        records.append(record)
    _require(offset == len(sequence), f"panjang {label} tidak tepat")
    return records


def _eocd(apk: bytes) -> tuple[int, int, int]:
    eocd_start = apk.rfind(b"PK\x05\x06")
    _require(eocd_start >= 0 and eocd_start + 22 <= len(apk), "end of central directory tidak ditemukan")
    disk, central_disk, disk_entries, entries, central_size, central_offset, comment_size = struct.unpack_from(
        "<HHHHIIH", apk, eocd_start + 4
    )
    _require(disk == central_disk == 0, "APK multi-disk tidak didukung")
    _require(disk_entries == entries, "jumlah entri central directory tidak konsisten")
    _require(eocd_start + 22 + comment_size == len(apk), "EOCD bukan record terakhir")
    _require(central_offset + central_size == eocd_start, "central directory tidak berakhir di EOCD")
    return eocd_start, central_offset, central_size


def split_sections(apk: bytes, block_start: int, block_size: int) -> list[bytes]:
    """Return the three byte ranges protected by APK Signature Scheme v2."""
    eocd_start, central_offset, central_size = _eocd(apk)
    _require(central_offset == block_start + block_size, "offset central directory tidak cocok dengan blok tanda tangan")
    eocd = bytearray(apk[eocd_start:])
    # Content digest uses the pre-signing central-directory offset.
    eocd[16:20] = struct.pack("<I", block_start)
    return [apk[:block_start], apk[central_offset : central_offset + central_size], bytes(eocd)]


def content_digest(sections: list[bytes], algorithm: str) -> bytes:
    """Compute Android's digest over all 1 MiB chunks in section order."""
    hashes: list[bytes] = []
    for section in sections:
        for offset in range(0, len(section), CHUNK_SIZE):
            chunk = section[offset : offset + CHUNK_SIZE]
            framed = b"\xa5" + struct.pack("<I", len(chunk)) + chunk
            hashes.append(hashlib.new(algorithm, framed).digest())
    _require(bool(hashes), "tidak ada chunk APK untuk dihitung")
    framed = b"\x5a" + struct.pack("<I", len(hashes)) + b"".join(hashes)
    return hashlib.new(algorithm, framed).digest()


def parse_signing_block(apk: bytes) -> dict:
    _eocd_start, central_offset, _central_size = _eocd(apk)
    _require(apk[central_offset : central_offset + 4] == b"PK\x01\x02", "central directory tidak diawali PK\\x01\\x02")
    _require(central_offset >= 24, "central directory terlalu dekat dengan awal berkas")
    footer = apk[central_offset - 24 : central_offset]
    _require(footer[-16:] == APK_SIG_BLOCK_MAGIC, "magic APK Sig Block tidak ada tepat sebelum central directory")
    (size,) = struct.unpack_from("<Q", footer, 0)
    _require(size >= 24, "APK Sig Block terlalu kecil")
    block_start = central_offset - size - 8
    _require(block_start >= 0, "blok tanda tangan melewati awal file")
    (size_again,) = struct.unpack_from("<Q", apk, block_start)
    _require(size_again == size, "ukuran blok tanda tangan tidak konsisten")

    pairs: dict[int, bytes] = {}
    pos = block_start + 8
    pairs_end = central_offset - 24
    while pos < pairs_end:
        _require(pos + 12 <= pairs_end, "header pair tanda tangan terpotong")
        (length,) = struct.unpack_from("<Q", apk, pos)
        _require(length >= 4, "pair tanda tangan terlalu pendek")
        end = pos + 8 + length
        _require(end <= pairs_end, "pair tanda tangan melewati batas blok")
        (pair_id,) = struct.unpack_from("<I", apk, pos + 8)
        _require(pair_id not in pairs, f"ID pair tanda tangan duplikat: {pair_id:#x}")
        pairs[pair_id] = apk[pos + 12 : end]
        pos = end
    _require(pos == pairs_end, "panjang blok tanda tangan tidak cocok dengan isinya")
    return {"start": block_start, "size": size + 8, "pairs": pairs, "central_offset": central_offset}


def _parse_algorithm_records(sequence: bytes, label: str) -> dict[int, bytes]:
    parsed: dict[int, bytes] = {}
    for record in read_records(sequence, label):
        _require(len(record) >= 8, f"record {label} terlalu pendek")
        (algorithm_id,) = struct.unpack_from("<I", record, 0)
        value, end = read_lp(record, 4)
        _require(end == len(record), f"field berlebih dalam record {label}")
        _require(algorithm_id not in parsed, f"algoritma duplikat dalam {label}: {algorithm_id:#x}")
        parsed[algorithm_id] = value
    return parsed


def _openssl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True, text=False)


def verify_apk(path: Path) -> dict:
    apk = path.read_bytes()
    required = ["AndroidManifest.xml", "classes.dex", "resources.arsc"]
    try:
        with zipfile.ZipFile(io.BytesIO(apk)) as archive:
            _require(archive.testzip() is None, "entri ZIP rusak")
            names = archive.namelist()
            _require(len(names) == len(set(names)), "APK memuat nama entri ZIP duplikat")
            _require(names == required, f"daftar entri APK tidak sesuai: {names}")
            info = archive.getinfo("resources.arsc")
            _require(info.compress_type == zipfile.ZIP_STORED, "resources.arsc harus disimpan tanpa kompresi")
            _require(info.header_offset + 30 <= len(apk), "local header resources.arsc terpotong")
            name_size, extra_size = struct.unpack_from("<HH", apk, info.header_offset + 26)
            data_offset = info.header_offset + 30 + name_size + extra_size
            _require(data_offset % 4 == 0, "data resources.arsc harus sejajar 4 byte")
            manifest = archive.read("AndroidManifest.xml")
            dex = archive.read("classes.dex")
            arsc = archive.read("resources.arsc")
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise VerifyError(f"ZIP APK tidak valid: {error}") from error

    block = parse_signing_block(apk)
    _require(V2_BLOCK_ID in block["pairs"], "blok APK Signature Scheme v2 tidak ada")
    signers, end = read_lp(block["pairs"][V2_BLOCK_ID], 0)
    _require(end == len(block["pairs"][V2_BLOCK_ID]), "field berlebih setelah sequence signer")
    signer_records = read_records(signers, "signers")
    _require(len(signer_records) == 1, "build Amba harus memiliki tepat satu signer")
    signer = signer_records[0]

    signed_data, offset = read_lp(signer, 0)
    signatures_sequence, offset = read_lp(signer, offset)
    public_key, offset = read_lp(signer, offset)
    _require(offset == len(signer), "field signer v2 berlebih")
    signatures = _parse_algorithm_records(signatures_sequence, "signatures")

    digests_sequence, offset = read_lp(signed_data, 0)
    certificates_sequence, offset = read_lp(signed_data, offset)
    attributes_sequence, offset = read_lp(signed_data, offset)
    _require(offset == len(signed_data), "field signedData berlebih")
    _require(not attributes_sequence, "build Amba tidak memakai atribut tambahan v2")
    digests = _parse_algorithm_records(digests_sequence, "digests")
    certificates = read_records(certificates_sequence, "certificates")
    _require(len(certificates) == 1, "build Amba harus memiliki tepat satu sertifikat")
    certificate = certificates[0]

    known = {values[0]: (name, values[1]) for name, values in ALGORITHMS.items()}
    candidates = [algorithm_id for algorithm_id in signatures if algorithm_id in digests and algorithm_id in known]
    _require(len(candidates) == 1, "tidak ada tepat satu pasangan algoritma signature/digest yang didukung")
    algorithm_id = candidates[0]
    algorithm_name, digest_name = known[algorithm_id]

    # Derive the public key from the certificate and require it to match the
    # signer field before using it for cryptographic verification.
    with tempfile.TemporaryDirectory(prefix="amba-verify.") as tmp:
        job = Path(tmp)
        (job / "cert.der").write_bytes(certificate)
        (job / "sig").write_bytes(signatures[algorithm_id])
        (job / "signed_data").write_bytes(signed_data)
        result = _openssl(
            ["x509", "-inform", "DER", "-in", str(job / "cert.der"), "-pubkey", "-noout"]
        )
        _require(result.returncode == 0, f"sertifikat DER tidak dapat dibaca: {result.stderr.decode(errors='replace')[:120]}")
        (job / "pub.pem").write_bytes(result.stdout)
        result = _openssl(["pkey", "-pubin", "-in", str(job / "pub.pem"), "-outform", "DER"])
        _require(result.returncode == 0, "public key sertifikat tidak dapat dikonversi")
        _require(result.stdout == public_key, "public key signer tidak sama dengan public key sertifikat")
        result = _openssl(
            [
                "dgst",
                f"-{digest_name}",
                "-verify",
                str(job / "pub.pem"),
                "-signature",
                str(job / "sig"),
                str(job / "signed_data"),
            ]
        )
        detail = result.stderr.decode(errors="replace").strip()[:120]
        _require(result.returncode == 0, f"tanda tangan v2 tidak valid: {detail}")

    sections = split_sections(apk, block["start"], block["size"])
    expected = content_digest(sections, digest_name)
    _require(digests[algorithm_id] == expected, "digest konten v2 tidak cocok dengan isi APK")

    manifest_info = parse_manifest(manifest)
    resources = parse_resource_strings(arsc)
    return {
        "package": manifest_info["package"],
        "version_name": manifest_info["versionName"],
        "version_code": manifest_info["versionCode"],
        "activity": manifest_info["activity"],
        "min_sdk": manifest_info["min_sdk"],
        "target_sdk": manifest_info["target_sdk"],
        "signature_scheme": "v2",
        "algorithm": algorithm_name,
        "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
        "resources": resources,
        "dex_sha256": hashlib.sha256(dex).hexdigest(),
        "size": len(apk),
        "sha256": hashlib.sha256(apk).hexdigest(),
    }


# -- binary XML / resource table readers used by the assertions above --------
def _pool_strings(blob: bytes, offset: int) -> list[str]:
    (chunk_type, header_size, _) = struct.unpack_from("<HHI", blob, offset)
    _require(chunk_type == CHUNK_STRING_POOL, "chunk string pool tidak ditemukan")
    (count, _styles, flags, strings_start, _styles_start) = struct.unpack_from("<IIIII", blob, offset + 8)
    _require(flags & 0x100, "string pool harus memakai encoding UTF-8")
    strings = []
    for index in range(count):
        (string_offset,) = struct.unpack_from("<I", blob, offset + header_size + 4 * index)
        pos = offset + strings_start + string_offset
        lengths = []
        for _ in range(2):  # character count, then byte count
            byte = blob[pos]
            pos += 1
            if byte & 0x80:
                lengths.append(((byte & 0x7F) << 8) | blob[pos])
                pos += 1
            else:
                lengths.append(byte)
        strings.append(blob[pos : pos + lengths[1]].decode("utf-8"))
    return strings


def parse_manifest(blob: bytes) -> dict:
    (chunk_type, header_size, total) = struct.unpack_from("<HHI", blob, 0)
    _require(chunk_type == CHUNK_XML, "AndroidManifest.xml bukan binary XML")
    _require(header_size == 8, "header binary XML tidak 8 byte")
    _require(total == len(blob), "ukuran binary XML tidak cocok dengan isinya")

    strings = _pool_strings(blob, 8)
    (pool_size,) = struct.unpack_from("<I", blob, 8 + 4)
    pos = 8 + pool_size
    resource_map = []
    info: dict = {"elements": []}
    depth = 0
    while pos < len(blob):
        (kind, hsize, size) = struct.unpack_from("<HHI", blob, pos)
        _require(size >= 8, f"chunk XML tidak valid pada offset {pos}")
        if kind == CHUNK_XML_RESOURCE_MAP:
            resource_map = list(struct.unpack_from(f"<{(size - 8) // 4}I", blob, pos + 8))
        elif kind == CHUNK_XML_START_ELEMENT:
            name_index = struct.unpack_from("<I", blob, pos + 20)[0]
            (attr_start, attr_size, attr_count) = struct.unpack_from("<HHH", blob, pos + 24)
            attributes = {}
            for i in range(attr_count):
                # attributeStart is relative to ResXMLTree_attrExt, which begins
                # 16 bytes into the chunk (after header, line and comment).
                base = pos + 16 + attr_start + i * attr_size
                (_, attr_name, _raw) = struct.unpack_from("<III", blob, base)
                (data_type, data) = struct.unpack_from("<BI", blob, base + 15)
                key = strings[attr_name]
                if data_type == 0x03:
                    attributes[key] = strings[data]
                elif data_type == 0x12:
                    attributes[key] = "true" if data else "false"
                elif data_type == 0x01:
                    attributes[key] = f"@0x{data:08X}"
                else:
                    attributes[key] = data
            name = strings[name_index]
            info["elements"].append((depth, name, attributes))
            if name == "manifest":
                info["package"] = attributes.get("package")
                info["versionName"] = attributes.get("versionName")
                info["versionCode"] = attributes.get("versionCode")
            if name == "uses-sdk":
                info["min_sdk"] = attributes.get("minSdkVersion")
                info["target_sdk"] = attributes.get("targetSdkVersion")
            if name == "activity" and depth == 2:
                info["activity"] = attributes.get("name")
            depth += 1
        elif kind == 0x0103:  # end element
            depth -= 1
        pos += size
    _ = resource_map
    if "activity" not in info:
        raise VerifyError("manifest tanpa elemen <activity>")
    if "min_sdk" not in info:
        raise VerifyError("manifest tanpa elemen <uses-sdk>")
    return info


def parse_resource_strings(blob: bytes) -> dict[str, str]:
    (chunk_type, header_size, total) = struct.unpack_from("<HHI", blob, 0)
    _require(chunk_type == 0x0002, "resources.arsc bukan ResTable")
    _require(total == len(blob), "ukuran resources.arsc tidak cocok")
    value_pool_offset = header_size
    strings = _pool_strings(blob, value_pool_offset)
    (value_pool_size,) = struct.unpack_from("<I", blob, value_pool_offset + 4)
    package_offset = value_pool_offset + value_pool_size
    (pkg_type, pkg_header, _pkg_size) = struct.unpack_from("<HHI", blob, package_offset)
    _require(pkg_type == 0x0200, "chunk paket arsc tidak ditemukan")
    # ResTable_package: chunk header, id, 256-byte name, typeStrings,
    # lastPublicType, keyStrings, lastPublicKey.
    (types_start,) = struct.unpack_from("<I", blob, package_offset + 8 + 4 + 256)
    (key_start,) = struct.unpack_from("<I", blob, package_offset + 8 + 4 + 256 + 8)
    type_pool = package_offset + types_start
    key_pool = package_offset + key_start
    keys = _pool_strings(blob, key_pool)
    _require(_pool_strings(blob, type_pool) == ["string"], "tipe resource harus 'string'")

    (key_pool_size,) = struct.unpack_from("<I", blob, key_pool + 4)
    type_spec = key_pool + key_pool_size
    (spec_size,) = struct.unpack_from("<I", blob, type_spec + 4)
    type_chunk = type_spec + spec_size
    (kind, thsize, _tsize, _tid, _flags, _res, count, entries_start) = struct.unpack_from("<HHIBBHII", blob, type_chunk)
    _require(kind == 0x0201, "chunk tipe arsc tidak ditemukan")
    result = {}
    for index in range(count):
        (entry_offset,) = struct.unpack_from("<I", blob, type_chunk + thsize + 4 * index)
        base = type_chunk + entries_start + entry_offset
        (entry_size, _flags, key_index) = struct.unpack_from("<HHI", blob, base)
        # Res_value sits right after the entry header (size + flags + key).
        (data_type, data) = struct.unpack_from("<BI", blob, base + entry_size + 3)
        _require(data_type == 0x03, "nilai resource harus berupa string")
        result[keys[key_index]] = strings[data]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verifikasi APK Amba (v2)")
    parser.add_argument("apk", type=Path)
    parser.add_argument("--json", action="store_true", help="cetak metadata sebagai JSON")
    args = parser.parse_args(argv)
    try:
        info = verify_apk(args.apk)
    except VerifyError as error:
        print(f"GAGAL: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        print(f"OK: {args.apk.name}")
        for key, value in info.items():
            if key != "resources":
                print(f"  {key}: {value}")
        for key, value in info["resources"].items():
            preview = value if len(value) <= 60 else value[:57] + "..."
            print(f"  res.{key}: {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
