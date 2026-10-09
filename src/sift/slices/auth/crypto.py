# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cryptographic pieces auth is built from: hashing, the key envelope, and session tokens.

The PIN never derives a key: six digits fall offline in no time. The password unlocks the box."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sift.slices.auth.tuning import (
    ARGON2_HASH_LENGTH,
    ARGON2_MAX_MEMORY_KIB,
    ARGON2_MIN_MEMORY_KIB,
    ARGON2_MIN_TIME_COST,
    ARGON2_PARALLELISM,
    ARGON2_RAM_FRACTION_DENOMINATOR,
    ARGON2_SALT_LENGTH,
    SESSION_TOKEN_BYTES,
)

# Domain separators: the wrapping key never equals the login hash, and each AAD binds a purpose.
_KDF_DOMAIN = b"sift/master-key-wrapping-key/v1"
_WRAP_AAD = b"sift/master-key-wrap/v1"
_SECRET_AAD = b"sift/secret/v1"

# A fresh random 96-bit nonce per wrap and seal, so none repeats under a key.
_NONCE_BYTES = 12
_MASTER_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class Argon2Params:
    """How costly a hash is. Higher is slower for everyone, attacker included."""

    time_cost: int
    memory_cost_kib: int
    parallelism: int

    def meets_floor(self) -> bool:
        """Whether these are at least the minimum this build will hash with."""
        return (
            self.time_cost >= ARGON2_MIN_TIME_COST
            and self.memory_cost_kib >= ARGON2_MIN_MEMORY_KIB
            and self.parallelism >= ARGON2_PARALLELISM
        )


def resolve_argon2_params(total_ram_bytes: int | None) -> Argon2Params:
    """Hashing parameters for this machine's memory, clamped between the floor and a ceiling."""
    memory = ARGON2_MIN_MEMORY_KIB
    if total_ram_bytes is not None:
        offered_kib = total_ram_bytes // ARGON2_RAM_FRACTION_DENOMINATOR // 1024
        memory = max(ARGON2_MIN_MEMORY_KIB, min(offered_kib, ARGON2_MAX_MEMORY_KIB))
    return Argon2Params(
        time_cost=ARGON2_MIN_TIME_COST,
        memory_cost_kib=memory,
        parallelism=ARGON2_PARALLELISM,
    )


class Hasher:
    """Argon2id hashing for passwords and PINs; every failure is the same returned False."""

    def __init__(self, params: Argon2Params) -> None:
        if not params.meets_floor():
            raise ValueError("argon2 parameters are below the floor this build will hash with")
        self._params = params
        self._hasher = PasswordHasher(
            time_cost=params.time_cost,
            memory_cost=params.memory_cost_kib,
            parallelism=params.parallelism,
            hash_len=ARGON2_HASH_LENGTH,
            salt_len=ARGON2_SALT_LENGTH,
            type=Type.ID,
        )

    @property
    def params(self) -> Argon2Params:
        return self._params

    def hash(self, secret: str) -> str:
        """A self-describing PHC hash string: algorithm, parameters and salt are inside it."""
        return self._hasher.hash(secret)

    def verify(self, stored_hash: str, secret: str) -> bool:
        """Whether the secret matches. False for any mismatch or malformed hash, never a raise."""
        try:
            return self._hasher.verify(stored_hash, secret)
        except (
            argon2_exceptions.VerifyMismatchError,
            argon2_exceptions.InvalidHashError,
            argon2_exceptions.VerificationError,
        ):
            return False

    def dummy_hash(self) -> str:
        """A throwaway hash for the no-user path, so it costs what a wrong password does."""
        return self._hasher.hash(secrets.token_urlsafe(16))

    def needs_rehash(self, stored_hash: str) -> bool:
        """Whether a stored hash is weaker than now: its faster verify would reveal the user."""
        return self._hasher.check_needs_rehash(stored_hash)


@dataclass(frozen=True, slots=True)
class WrappedKey:
    """A master key locked with a password-derived key; the salt and nonce are not secret."""

    ciphertext: bytes
    nonce: bytes
    salt: bytes


# Fixed, not tuned, so a backup restored on a smaller machine still opens.
WRAPPING_KDF_PARAMS = Argon2Params(
    time_cost=ARGON2_MIN_TIME_COST,
    memory_cost_kib=ARGON2_MIN_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
)


def generate_master_key() -> bytes:
    """A fresh 256-bit key from the OS CSPRNG. This is what actually encrypts the site logins."""
    return secrets.token_bytes(_MASTER_KEY_BYTES)


def _wrapping_key(password: str, salt: bytes) -> bytes:
    """Derive the master key's wrapping key, separate from the login hash. Never from a PIN."""
    return hash_secret_raw(
        secret=_KDF_DOMAIN + password.encode("utf-8"),
        salt=salt,
        time_cost=WRAPPING_KDF_PARAMS.time_cost,
        memory_cost=WRAPPING_KDF_PARAMS.memory_cost_kib,
        parallelism=WRAPPING_KDF_PARAMS.parallelism,
        hash_len=ARGON2_HASH_LENGTH,
        type=Type.ID,
    )


def wrap_master_key(master_key: bytes, password: str) -> WrappedKey:
    """Lock a master key with a key derived from the password. Fresh salt and nonce each time."""
    salt = secrets.token_bytes(ARGON2_SALT_LENGTH)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    wrapping_key = _wrapping_key(password, salt)
    ciphertext = AESGCM(wrapping_key).encrypt(nonce, master_key, _WRAP_AAD)
    return WrappedKey(ciphertext=ciphertext, nonce=nonce, salt=salt)


def unwrap_master_key(wrapped: WrappedKey, password: str) -> bytes | None:
    """Recover the master key, or None if the password is wrong or the ciphertext was tampered."""
    wrapping_key = _wrapping_key(password, wrapped.salt)
    try:
        return AESGCM(wrapping_key).decrypt(wrapped.nonce, wrapped.ciphertext, _WRAP_AAD)
    except Exception:
        return None


def rewrap_master_key(
    wrapped: WrappedKey,
    old_password: str,
    new_password: str,
) -> WrappedKey | None:
    """Re-lock the same master key under a new password; nothing it protects is re-encrypted."""
    master_key = unwrap_master_key(wrapped, old_password)
    if master_key is None:
        return None
    return wrap_master_key(master_key, new_password)


def new_token() -> str:
    """An opaque, unguessable token from the OS CSPRNG. Used for sessions and for CSRF."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The stored form of a session token: SHA-256, as the token already has 256 bits of entropy."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    """Constant-time comparison, as bytes so a hostile non-ASCII header is a plain non-match."""
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))


_CSRF_MESSAGE = b"sift/csrf/v1"


def derive_csrf_token(session_token: str) -> str:
    """A CSRF token bound to one session: an HMAC of its HttpOnly cookie, so nothing is stored."""
    return hmac.new(session_token.encode("utf-8"), _CSRF_MESSAGE, hashlib.sha256).hexdigest()
