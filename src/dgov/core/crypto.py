"""Lightweight reversible secret encryption for agent endpoint credentials.

Uses only the standard library: a PBKDF2-derived keystream XOR the plaintext,
plus an HMAC tag so tampering is detectable.  Replace with a KMS envelope in
production; the point here is that no credential is ever stored in clear text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_PREFIX = "dgov1$"


def _materials(secret: str, salt: bytes) -> tuple[bytes, bytes]:
    key = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 120_000, dklen=64)
    return key[:32], key[32:]


def _keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(out[:length])


def encrypt_secret(plaintext: str | None, secret: str) -> str:
    if not plaintext:
        return ""
    salt = secrets.token_bytes(16)
    stream_key, mac_key = _materials(secret, salt)
    raw = plaintext.encode("utf-8")
    cipher = bytes(a ^ b for a, b in zip(raw, _keystream(stream_key, len(raw)), strict=False))
    tag = hmac.new(mac_key, salt + cipher, hashlib.sha256).digest()[:16]
    return _PREFIX + base64.urlsafe_b64encode(salt + tag + cipher).decode("ascii")


def decrypt_secret(encoded: str | None, secret: str) -> str:
    if not encoded or not encoded.startswith(_PREFIX):
        return ""
    try:
        blob = base64.urlsafe_b64decode(encoded[len(_PREFIX) :].encode("ascii"))
    except Exception:
        return ""
    if len(blob) < 32:
        return ""
    salt, tag, cipher = blob[:16], blob[16:32], blob[32:]
    stream_key, mac_key = _materials(secret, salt)
    if not hmac.compare_digest(tag, hmac.new(mac_key, salt + cipher, hashlib.sha256).digest()[:16]):
        return ""
    raw = bytes(a ^ b for a, b in zip(cipher, _keystream(stream_key, len(cipher)), strict=False))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
