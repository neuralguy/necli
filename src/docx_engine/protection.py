"""Хеш пароля documentProtection (итеративный SHA-512, ECMA-376)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import struct

DEFAULT_SPIN_COUNT = 100_000
# Defensive ceiling for untrusted documents. Normal Office values are far below
# this, while an attacker-controlled huge spin count can otherwise lock a worker.
MAX_SPIN_COUNT = 10_000_000


def _validated_spin(spin) -> int:
    if isinstance(spin, bool):
        raise ValueError("spinCount must be an integer")
    try:
        value = int(spin)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("spinCount must be an integer") from exc
    if value < 0 or value > MAX_SPIN_COUNT:
        raise ValueError(f"spinCount must be between 0 and {MAX_SPIN_COUNT}")
    return value


def _iterated_sha512(password: str, salt: bytes, spin: int) -> bytes:
    spin = _validated_spin(spin)
    h = hashlib.sha512(salt + str(password).encode("utf-16-le")).digest()
    for i in range(spin):
        h = hashlib.sha512(h + struct.pack("<I", i)).digest()
    return h


def hash_protection_password(password: str, spin_count: int = DEFAULT_SPIN_COUNT) -> dict:
    spin_count = _validated_spin(spin_count)
    salt = os.urandom(16)
    h = _iterated_sha512(password, salt, spin_count)
    return {
        "hash": base64.b64encode(h).decode(),
        "salt": base64.b64encode(salt).decode(),
        "spinCount": spin_count,
        "algorithmSid": 14,
    }


def verify_protection_password(password: str, protection: dict) -> bool:
    if not isinstance(protection, dict):
        return False
    expected = protection.get("hash")
    # Absence of a protection hash is not a successful password verification.
    # Callers that want to know whether protection is enabled should test that
    # separately instead of conflating "not protected" with "password valid".
    if not expected:
        return False
    try:
        if int(protection.get("algorithmSid", 14)) != 14:
            return False
        spin = _validated_spin(protection.get("spinCount", DEFAULT_SPIN_COUNT))
        salt_text = protection.get("salt") or ""
        salt = base64.b64decode(str(salt_text), validate=True) if salt_text else b""
        expected_bytes = base64.b64decode(str(expected), validate=True)
        h = _iterated_sha512(password, salt, spin)
        return hmac.compare_digest(h, expected_bytes)
    except (ValueError, TypeError, binascii.Error):
        return False
