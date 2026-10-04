# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cryptographic pieces auth is built from: hashing, the key envelope, and session tokens.

None of it is invented here. Argon2id comes from argon2-cffi, the authenticated encryption from
cryptography, and the token handling from the standard library. What this module does is assemble
them into the two things auth needs and hold the shape of both in one place:

  * a password (or PIN) turned into a verifier that reveals nothing if the database is stolen, and
  * a random master key that protects saved site logins, itself locked with the user's password.

The second is the reason a stolen backup is a locked box beside a locked key. The master key
encrypts the logins; a key derived from the password encrypts the master key; the password is
kept nowhere. Change the password and only the wrapped key is rewritten: the logins are never
re-encrypted, which is the whole point of wrapping a key with a key rather than encrypting the
data with the password directly.

One rule runs through all of it: **the PIN never derives a key.** It is six digits, which
is to say it is guessable offline in no time, and wrapping the master key with it would hand the
site logins to anyone holding the file. The PIN unlocks a screen; the password unlocks the box.
"""

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

# Domain separators. The wrapping key and the login hash both come from the same password, so the
# only thing keeping them independent is that they are computed differently: a distinct salt
# each, and this tag mixed into the wrapping-key derivation so that even an impossible salt
# collision could not make one equal the other. The AAD tags bind each ciphertext to its purpose,
# so a wrapped key can never be accepted where a sealed secret is expected, or the reverse.
_KDF_DOMAIN = b"sift/master-key-wrapping-key/v1"
_WRAP_AAD = b"sift/master-key-wrap/v1"
_SECRET_AAD = b"sift/secret/v1"

# AES-GCM nonces are 96 bits. A fresh random nonce is generated for every wrap and every seal, so
# no nonce is ever reused under a given key.
_NONCE_BYTES = 12
_MASTER_KEY_BYTES = 32


# --- Argon2id parameters ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Argon2Params:
    """How costly a hash is. Higher is slower for everyone, attacker included."""

    time_cost: int
    memory_cost_kib: int
    parallelism: int

    def meets_floor(self) -> bool:
        """Whether these are at least as strong as the minimum this build will hash with.

        A weak set of parameters is not a smaller version of a strong one: it is a hash an
        attacker cracks. The floor is asserted rather than assumed.
        """
        return (
            self.time_cost >= ARGON2_MIN_TIME_COST
            and self.memory_cost_kib >= ARGON2_MIN_MEMORY_KIB
            and self.parallelism >= ARGON2_PARALLELISM
        )


def resolve_argon2_params(total_ram_bytes: int | None) -> Argon2Params:
    """Pick hashing parameters for this machine, never weaker than the floor.

    A box with more memory can afford a memory-harder hash, which is the kind an attacker's GPU
    struggles most to parallelize. A box that will not say how much memory it has (or has little)
    gets the floor, which is already strong. The result is clamped from both sides: below by the
    floor, above by a ceiling that keeps a single login from taking a visible second or a burst of
    them from exhausting memory.
    """
    memory = ARGON2_MIN_MEMORY_KIB
    if total_ram_bytes is not None:
        offered_kib = total_ram_bytes // ARGON2_RAM_FRACTION_DENOMINATOR // 1024
        memory = max(ARGON2_MIN_MEMORY_KIB, min(offered_kib, ARGON2_MAX_MEMORY_KIB))
    return Argon2Params(
        time_cost=ARGON2_MIN_TIME_COST,
        memory_cost_kib=memory,
        parallelism=ARGON2_PARALLELISM,
    )


# --- Hashing (passwords and PINs) ------------------------------------------------------------


class Hasher:
    """Argon2id hashing for passwords and PINs.

    Verification is constant-time within the primitive, and a mismatch is a returned False rather
    than a raised exception, so a caller cannot accidentally let one class of failure look
    different from another to something timing the response.
    """

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
        """A throwaway hash to verify against when no user matched.

        Answering "wrong password" and "no such user" must take the same time and return the
        same thing, or the difference tells an attacker which usernames exist. The no-user path
        verifies a submitted password against this instead of skipping the work.
        """
        return self._hasher.hash(secrets.token_urlsafe(16))

    def needs_rehash(self, stored_hash: str) -> bool:
        """Whether a stored hash was made with weaker parameters than this hasher now uses.

        A hash written at the floor (by the console reset tool, or on a box that has since been
        tuned to more memory) verifies faster than the tuned no-user dummy, and that gap is a
        user-existence oracle. A successful login is the moment to upgrade it, so a real hash and
        the dummy converge to the same cost.
        """
        return self._hasher.check_needs_rehash(stored_hash)


# --- The master-key envelope -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WrappedKey:
    """A master key locked with a password-derived key, and what it takes to unlock it.

    The salt and nonce are not secret (they are stored in the clear beside the ciphertext) and
    the ciphertext is useless without the password that reproduces the wrapping key.
    """

    ciphertext: bytes
    nonce: bytes
    salt: bytes


# The wrapping key is derived with FIXED parameters, not the hardware-tuned ones the login hash
# uses. This is what lets a backup restored on a different machine still open with the password:
# tuning the derivation to the machine that wrote it would make a key wrapped on a big box
# impossible to unwrap on a small one, and restore-anywhere is the entire point of wrapping with
# the password. The floor is strong and every machine can afford it, which is why the floor is
# exactly the right fixed value.
WRAPPING_KDF_PARAMS = Argon2Params(
    time_cost=ARGON2_MIN_TIME_COST,
    memory_cost_kib=ARGON2_MIN_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
)


def generate_master_key() -> bytes:
    """A fresh 256-bit key from the OS CSPRNG. This is what actually encrypts the site logins."""
    return secrets.token_bytes(_MASTER_KEY_BYTES)


def _wrapping_key(password: str, salt: bytes) -> bytes:
    """Derive the key that wraps the master key, from the password and a salt.

    Separate from the login hash by construction: its own salt, fixed parameters, and a domain tag
    mixed into the input. Same password, different derivation, so the login hash can never double
    as this key. Takes a password. It must never be handed a PIN: a PIN is guessable offline, and
    a key derived from one would undo the entire envelope.
    """
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
    """Recover the master key, or None if the password is wrong or the ciphertext was tampered.

    The password is verified as a side effect of the decryption succeeding: AES-GCM's tag fails to
    authenticate under the wrong wrapping key, and there is nothing to return.
    """
    wrapping_key = _wrapping_key(password, wrapped.salt)
    try:
        return AESGCM(wrapping_key).decrypt(wrapped.nonce, wrapped.ciphertext, _WRAP_AAD)
    except Exception:
        # cryptography raises InvalidTag on a bad key or altered ciphertext. Either way the answer
        # is the same: this password does not open this key.
        return None


def rewrap_master_key(
    wrapped: WrappedKey,
    old_password: str,
    new_password: str,
) -> WrappedKey | None:
    """Re-lock the same master key under a new password. None if the old password is wrong.

    Only the ~32-byte key is re-encrypted; whatever the master key protects is never touched. That
    is why a password change is cheap however large the library of saved logins has grown.
    """
    master_key = unwrap_master_key(wrapped, old_password)
    if master_key is None:
        return None
    return wrap_master_key(master_key, new_password)


# The layer the master key exists for (sealing a secret under it) is in `kernel.secrets`.
# Two features need it and neither owns it, and nothing about it is a password question.


# --- Session and CSRF tokens -----------------------------------------------------------------


def new_token() -> str:
    """An opaque, unguessable token from the OS CSPRNG. Used for sessions and for CSRF."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The value stored for a session, so the database never holds the token itself.

    A plain SHA-256 rather than Argon2: the token already carries 256 bits of entropy, so there is
    nothing to brute-force and no reason to pay a slow hash on every request. What this defends is
    a leaked database: the stored hash cannot be replayed as a cookie.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    """Constant-time comparison, so a near-miss is not distinguishable by how long it took.

    Compared as bytes: `compare_digest` on a `str` raises on any non-ASCII character, and one of
    these values is a request header an attacker controls. Encoding first turns a hostile header
    into a plain non-match rather than an unhandled error.
    """
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))


_CSRF_MESSAGE = b"sift/csrf/v1"


def derive_csrf_token(session_token: str) -> str:
    """A CSRF token bound to one session, derived from that session's token.

    The session token lives in an HttpOnly cookie a browser will not let a page read, and a
    SameSite cookie a browser will not send across origins. So a cross-site page cannot obtain the
    session token, cannot compute this from it, and cannot forge a state-changing request that
    carries the matching header. This is delivered in the login response for the client to echo
    back, and recomputed from the cookie on each request to compare against: nothing is stored,
    and it cannot be replayed on a different session because a different session has a different
    token.
    """
    return hmac.new(session_token.encode("utf-8"), _CSRF_MESSAGE, hashlib.sha256).hexdigest()
