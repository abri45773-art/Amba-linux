#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""APK Signature Scheme v2 signer built on the openssl CLI.

The implementation follows Android's v2 wire format: one digest over all 1 MiB
chunks in the three protected ZIP sections, length-prefixed digest/signature
records, and an APK Signing Block immediately before the central directory.
Only ``openssl`` is required; no JDK or Android SDK is used.
"""
from __future__ import annotations

import hashlib
import struct
import subprocess
import tempfile
from pathlib import Path

APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
V2_BLOCK_ID = 0x7109871A
CHUNK_SIZE = 1024 * 1024

# Name -> (APK signature algorithm ID, digest name, key type).
# https://source.android.com/docs/security/features/apksigning/v2
ALGORITHMS = {
    "rsa2048": (0x0103, "sha256", "RSA"),  # RSASSA-PKCS1-v1_5 with SHA-256
    "ec256": (0x0201, "sha256", "EC"),  # ECDSA with SHA-256
}


class SigningError(RuntimeError):
    pass


def _openssl(args: list[str], stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["openssl", *args], input=stdin, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise SigningError(f"openssl {args[0]} gagal: {message}")
    return result.stdout


def generate_key_pair(directory: Path, algorithm: str = "rsa2048") -> tuple[Path, Path]:
    """Create an unencrypted private key and self-signed debug certificate."""
    if algorithm not in ALGORITHMS:
        raise SigningError(f"algoritma tidak didukung: {algorithm}")
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / "debug.key"
    cert = directory / "debug.crt"
    if algorithm == "rsa2048":
        _openssl(["genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key)])
    else:
        _openssl(
            [
                "genpkey",
                "-algorithm",
                "EC",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-out",
                str(key),
            ]
        )
    _openssl(
        [
            "req",
            "-x509",
            "-new",
            "-key",
            str(key),
            "-days",
            "3650",
            "-subj",
            "/CN=Amba Linux Release/O=Amba Linux/OU=Debug Signing",
            "-out",
            str(cert),
        ]
    )
    return key, cert


def certificate_der(cert_path: Path) -> bytes:
    return _openssl(["x509", "-in", str(cert_path), "-outform", "DER"])


def public_key_der(key_path: Path) -> bytes:
    return _openssl(["pkey", "-in", str(key_path), "-pubout", "-outform", "DER"])


def _sign(key_path: Path, data: bytes, digest: str) -> bytes:
    with tempfile.NamedTemporaryFile() as handle:
        handle.write(data)
        handle.flush()
        return _openssl(["dgst", f"-{digest}", "-sign", str(key_path), handle.name])


def _length_prefixed(*chunks: bytes) -> bytes:
    return b"".join(struct.pack("<I", len(chunk)) + chunk for chunk in chunks)


def _digest(data: bytes, algorithm: str) -> bytes:
    return hashlib.new(algorithm, data).digest()


def content_digest(sections: list[bytes], algorithm: str = "sha256") -> bytes:
    """Compute Android's chunked content digest over all protected sections.

    Every non-empty section is split into 1 MiB chunks. Each chunk is hashed as
    ``0xa5 || uint32le(size) || data``. Those hashes, in section order, are then
    folded as ``H(0x5a || uint32le(chunk_count) || chunk_hashes...)``.
    """
    chunk_hashes: list[bytes] = []
    for section in sections:
        for offset in range(0, len(section), CHUNK_SIZE):
            chunk = section[offset : offset + CHUNK_SIZE]
            chunk_hashes.append(_digest(b"\xa5" + struct.pack("<I", len(chunk)) + chunk, algorithm))
    if not chunk_hashes:
        raise SigningError("APK tidak memiliki bagian yang dapat ditandatangani")
    return _digest(
        b"\x5a" + struct.pack("<I", len(chunk_hashes)) + b"".join(chunk_hashes),
        algorithm,
    )


def _content_digest_sections(apk: bytes) -> list[bytes]:
    """Split an unsigned ZIP into the three sections protected by v2."""
    if apk[:4] != b"PK\x03\x04":
        raise SigningError("APK tidak diawali local file header")
    eocd_index = apk.rfind(b"PK\x05\x06")
    if eocd_index < 0 or eocd_index + 22 > len(apk):
        raise SigningError("end of central directory tidak ditemukan")
    (comment_size,) = struct.unpack_from("<H", apk, eocd_index + 20)
    if eocd_index + 22 + comment_size != len(apk):
        raise SigningError("EOCD bukan record ZIP terakhir")
    (central_size, central_offset) = struct.unpack_from("<II", apk, eocd_index + 12)
    if central_offset + central_size != eocd_index:
        raise SigningError("central directory tidak berakhir tepat di EOCD")
    return [apk[:central_offset], apk[central_offset:eocd_index], apk[eocd_index:]]


def sign_v2(apk: bytes, key_path: Path, cert_path: Path, algorithm: str = "rsa2048") -> bytes:
    if algorithm not in ALGORITHMS:
        raise SigningError(f"algoritma tidak didukung: {algorithm}")
    alg_id, digest_name, _key_type = ALGORITHMS[algorithm]

    sections = _content_digest_sections(apk)
    digest = content_digest(sections, digest_name)
    cert = certificate_der(cert_path)

    # signedData = digests, certificates, additional attributes. Each field is
    # one length-prefixed sequence; every record inside a sequence is prefixed.
    digest_record = struct.pack("<I", alg_id) + _length_prefixed(digest)
    signed_data = _length_prefixed(
        _length_prefixed(digest_record),
        _length_prefixed(cert),
        b"",  # empty additional-attributes sequence
    )

    signature = _sign(key_path, signed_data, digest_name)
    signature_record = struct.pack("<I", alg_id) + _length_prefixed(signature)
    signer = _length_prefixed(
        signed_data,
        _length_prefixed(signature_record),
        public_key_der(key_path),
    )
    # v2 value = length-prefixed sequence of length-prefixed signers.
    v2_value = _length_prefixed(_length_prefixed(signer))

    block_id = struct.pack("<I", V2_BLOCK_ID)
    pair = struct.pack("<Q", len(block_id) + len(v2_value)) + block_id + v2_value
    # The repeated size excludes only the first uint64 size field.
    repeated_size = len(pair) + 8 + len(APK_SIG_BLOCK_MAGIC)
    block = (
        struct.pack("<Q", repeated_size)
        + pair
        + struct.pack("<Q", repeated_size)
        + APK_SIG_BLOCK_MAGIC
    )

    first, central, eocd_bytes = sections
    eocd = bytearray(eocd_bytes)
    (central_offset,) = struct.unpack_from("<I", eocd, 16)
    eocd[16:20] = struct.pack("<I", central_offset + len(block))
    return first + block + central + bytes(eocd)


def verify_structure(apk: bytes) -> dict:
    """Cheap build-time check; the independent verifier performs crypto checks."""
    eocd_index = apk.rfind(b"PK\x05\x06")
    if eocd_index < 0:
        raise SigningError("APK tanpa EOCD")
    central_offset = struct.unpack_from("<I", apk, eocd_index + 16)[0]
    if central_offset < 24:
        raise SigningError("offset central directory terlalu kecil")
    footer = apk[central_offset - 24 : central_offset]
    if footer[-16:] != APK_SIG_BLOCK_MAGIC:
        raise SigningError("magic APK Sig Block tidak ditemukan tepat sebelum central directory")
    (size,) = struct.unpack_from("<Q", footer, 0)
    block_start = central_offset - (size + 8)
    if block_start < 0:
        raise SigningError("APK Sig Block melewati awal file")
    (head,) = struct.unpack_from("<Q", apk, block_start)
    if head != size:
        raise SigningError("ukuran APK Sig Block tidak konsisten")
    return {"block_offset": block_start, "size": size + 8, "central_offset": central_offset}
