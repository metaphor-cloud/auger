"""Encrypt a stored token."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SealError(Exception):
    pass


def seal(key: bytes, plaintext: bytes, tries: int = 3) -> tuple[bytes, bytes]:
    """Return the nonce and the ciphertext. Retries a transient device failure."""
    last: Exception | None = None
    for _ in range(tries):
        nonce = os.urandom(12)
        try:
            return nonce, AESGCM(key).encrypt(nonce, plaintext, None)
        except Exception as error:  # a hardware backend can fail transiently
            last = error
    raise SealError(str(last))
