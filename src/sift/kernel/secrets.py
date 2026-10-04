# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sealing a secret under a key, and opening it again.

One authenticated-encryption implementation, and one place its tag and its nonce size are decided.
It sits in the foundation because two features need it and neither owns it: sign-in produces the
master key these run under, and the downloader is what has secrets worth sealing. A feature never
imports a feature, so the shape they share is here.

**Nothing here decides what the key is.** A key arrives already unwrapped, from the user who
unlocked it. That is deliberate: deriving a key is a password question and belongs with passwords,
and keeping the two apart is what stops a shortcut being taken later that seals something under
anything weaker.

AES-GCM nonces are 96 bits and a fresh random one is generated for every seal, so no nonce is ever
reused under a given key.
"""

from __future__ import annotations

import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
"""Bytes of nonce. Read by the envelope that wraps a master key as well, so the two agree."""

# The tag bound into every ciphertext sealed here. It is what stops one kind of ciphertext being
# accepted where another is expected: a wrapped key can never be opened as a sealed secret, or
# the reverse, even under the same key.
_SECRET_AAD = b"sift/secret/v1"


def seal_secret(key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    """Encrypt a secret under a key. Returns (ciphertext, nonce)."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    return AESGCM(key).encrypt(nonce, plaintext, _SECRET_AAD), nonce


def open_secret(key: bytes, ciphertext: bytes, nonce: bytes) -> bytes | None:
    """Decrypt a secret sealed with `seal_secret`. None if the key is wrong or the bytes altered."""
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _SECRET_AAD)
    except Exception:
        # The library raises on a bad key and on altered bytes alike. Either way the answer is the
        # same: this key does not open this secret, and saying which would say more than that.
        return None
