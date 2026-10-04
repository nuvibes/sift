# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cryptographic primitives, on their own.

These do not touch a database or a request. What they prove is that the pieces the rest of auth is
assembled from behave: a hash verifies and only the right secret verifies against it, a key wrapped
with a password opens with that password and no other, and the token handling is what it claims.
"""

from __future__ import annotations

import pytest

from sift.kernel.secrets import open_secret, seal_secret
from sift.slices.auth import crypto
from sift.slices.auth.crypto import (
    Argon2Params,
    Hasher,
    derive_csrf_token,
    generate_master_key,
    hash_token,
    new_token,
    resolve_argon2_params,
    rewrap_master_key,
    tokens_equal,
    unwrap_master_key,
    wrap_master_key,
)
from sift.slices.auth.tuning import (
    ARGON2_MAX_MEMORY_KIB,
    ARGON2_MIN_MEMORY_KIB,
    ARGON2_MIN_TIME_COST,
)

pytestmark = pytest.mark.unit

PASSWORD = "Corr3ct-Horse!staple9"
OTHER = "An0ther-Secur3!keyword"


# --- Argon2 parameters -----------------------------------------------------------------------


def test_the_floor_is_met_even_with_no_memory_information() -> None:
    params = resolve_argon2_params(None)
    assert params.meets_floor()
    assert params.memory_cost_kib == ARGON2_MIN_MEMORY_KIB
    assert params.time_cost >= ARGON2_MIN_TIME_COST


def test_more_memory_raises_the_cost_up_to_the_ceiling() -> None:
    generous = resolve_argon2_params(64 * 1024**3)  # 64 GB
    assert generous.memory_cost_kib == ARGON2_MAX_MEMORY_KIB
    modest = resolve_argon2_params(2 * 1024**3)  # 2 GB -> below what the fraction would raise
    assert modest.memory_cost_kib == ARGON2_MIN_MEMORY_KIB


def test_a_hasher_refuses_parameters_below_the_floor() -> None:
    with pytest.raises(ValueError, match="floor"):
        Hasher(Argon2Params(time_cost=1, memory_cost_kib=8, parallelism=1))


# --- Hashing ---------------------------------------------------------------------------------


def test_a_password_verifies_against_its_own_hash(hasher: Hasher) -> None:
    stored = hasher.hash(PASSWORD)
    assert hasher.verify(stored, PASSWORD)


def test_a_wrong_password_does_not_verify(hasher: Hasher) -> None:
    stored = hasher.hash(PASSWORD)
    assert not hasher.verify(stored, OTHER)


def test_verify_returns_false_rather_than_raising_on_a_malformed_hash(hasher: Hasher) -> None:
    assert hasher.verify("not-a-hash", PASSWORD) is False


def test_the_same_password_hashes_differently_each_time(hasher: Hasher) -> None:
    # A per-hash random salt is inside the PHC string; two hashes of one password must differ, or
    # the salt is not doing its job.
    assert hasher.hash(PASSWORD) != hasher.hash(PASSWORD)


def test_the_dummy_hash_is_a_real_hash_that_no_real_password_opens(hasher: Hasher) -> None:
    dummy = hasher.dummy_hash()
    assert not hasher.verify(dummy, PASSWORD)


# --- The master-key envelope -----------------------------------------------------------------


def test_a_master_key_opens_with_the_password_that_wrapped_it() -> None:
    master_key = generate_master_key()
    wrapped = wrap_master_key(master_key, PASSWORD)
    assert unwrap_master_key(wrapped, PASSWORD) == master_key


def test_a_master_key_does_not_open_with_the_wrong_password() -> None:
    wrapped = wrap_master_key(generate_master_key(), PASSWORD)
    assert unwrap_master_key(wrapped, OTHER) is None


def test_a_tampered_wrapped_key_does_not_open() -> None:
    """Each of the three parts, flipped rather than overwritten.

    Overwriting the first byte with a constant is not tampering when the byte is already that
    constant: once in 256 wraps the "corrupt" key is the original and opens correctly, which
    is a security test failing for a reason that has nothing to do with the code. Inverting the
    byte always changes it.
    """
    wrapped = wrap_master_key(generate_master_key(), PASSWORD)

    def flipped(part: bytes) -> bytes:
        return bytes([part[0] ^ 0xFF]) + part[1:]

    for corrupt in (
        crypto.WrappedKey(flipped(wrapped.ciphertext), wrapped.nonce, wrapped.salt),
        crypto.WrappedKey(wrapped.ciphertext, flipped(wrapped.nonce), wrapped.salt),
        crypto.WrappedKey(wrapped.ciphertext, wrapped.nonce, flipped(wrapped.salt)),
    ):
        assert unwrap_master_key(corrupt, PASSWORD) is None


def test_rewrapping_keeps_the_same_master_key() -> None:
    master_key = generate_master_key()
    wrapped = wrap_master_key(master_key, PASSWORD)
    rewrapped = rewrap_master_key(wrapped, PASSWORD, OTHER)
    assert rewrapped is not None
    assert unwrap_master_key(rewrapped, OTHER) == master_key
    # The old password no longer opens the re-wrapped key.
    assert unwrap_master_key(rewrapped, PASSWORD) is None


def test_rewrapping_with_the_wrong_old_password_fails() -> None:
    wrapped = wrap_master_key(generate_master_key(), PASSWORD)
    assert rewrap_master_key(wrapped, OTHER, "N3w-passw0rd!here") is None


def test_wrapping_uses_a_fresh_salt_and_nonce_each_time() -> None:
    master_key = generate_master_key()
    first = wrap_master_key(master_key, PASSWORD)
    second = wrap_master_key(master_key, PASSWORD)
    assert first.salt != second.salt
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


# --- The inner layer: sealing a secret with the master key -----------------------------------


def test_a_sealed_secret_opens_with_the_master_key() -> None:
    master_key = generate_master_key()
    ciphertext, nonce = seal_secret(master_key, b"session=abc123; path=/")
    assert open_secret(master_key, ciphertext, nonce) == b"session=abc123; path=/"


def test_a_sealed_secret_does_not_open_with_a_different_key() -> None:
    ciphertext, nonce = seal_secret(generate_master_key(), b"cookie")
    assert open_secret(generate_master_key(), ciphertext, nonce) is None


# --- Tokens ----------------------------------------------------------------------------------


def test_tokens_are_unguessable_and_distinct() -> None:
    assert new_token() != new_token()
    assert len(new_token()) >= 32


def test_a_token_hash_is_stable_and_does_not_reveal_the_token() -> None:
    token = new_token()
    assert hash_token(token) == hash_token(token)
    assert token not in hash_token(token)


def test_constant_time_compare_agrees_with_equality() -> None:
    assert tokens_equal("abc", "abc")
    assert not tokens_equal("abc", "abd")


def test_the_csrf_token_is_bound_to_the_session_token() -> None:
    token = new_token()
    assert derive_csrf_token(token) == derive_csrf_token(token)
    assert derive_csrf_token(token) != derive_csrf_token(new_token())
    # It is not the session token itself, so leaking it does not leak the session.
    assert derive_csrf_token(token) != token


def test_a_hasher_says_what_parameters_it_was_built_with() -> None:
    """Read rather than assumed, by the tuner that writes them down and by the boot check that
    compares what is configured against what is in use. A hasher whose parameters cannot be read
    back is one nobody can prove is running at the strength it was set to."""
    params = resolve_argon2_params(None)

    assert Hasher(params).params == params
