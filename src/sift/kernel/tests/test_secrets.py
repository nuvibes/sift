# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sealing a secret and opening it again, and every way that is supposed to fail: a version that
returned the plaintext regardless of key would pass a round-trip test, so the refusals are the
point."""

from __future__ import annotations

import pytest

from sift.kernel.secrets import NONCE_BYTES, open_secret, seal_secret

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
PLAINTEXT = b"a saved site login"


def test_a_sealed_secret_opens_again_under_the_same_key() -> None:
    ciphertext, nonce = seal_secret(KEY, PLAINTEXT)

    assert open_secret(KEY, ciphertext, nonce) == PLAINTEXT


def test_the_sealed_bytes_are_not_the_plaintext() -> None:
    """The ciphertext is not the plaintext: a seal that returned its input passes everything
    else."""
    ciphertext, _ = seal_secret(KEY, PLAINTEXT)

    assert PLAINTEXT not in ciphertext


def test_a_nonce_is_the_declared_size_and_never_reused() -> None:
    """No nonce repeats under one key, sampled: AES-GCM fails catastrophically on reuse."""
    seals = [seal_secret(KEY, PLAINTEXT) for _ in range(64)]

    assert all(len(nonce) == NONCE_BYTES for _, nonce in seals)
    assert len({nonce for _, nonce in seals}) == 64


def test_the_wrong_key_does_not_open_it() -> None:
    ciphertext, nonce = seal_secret(KEY, PLAINTEXT)

    assert open_secret(OTHER_KEY, ciphertext, nonce) is None


def test_altered_bytes_do_not_open_it() -> None:
    """Authenticated encryption: a changed ciphertext is refused rather than decrypted to rubbish."""
    ciphertext, nonce = seal_secret(KEY, PLAINTEXT)
    altered = bytes([ciphertext[0] ^ 0x01]) + ciphertext[1:]

    assert open_secret(KEY, altered, nonce) is None


def test_the_wrong_nonce_does_not_open_it() -> None:
    ciphertext, nonce = seal_secret(KEY, PLAINTEXT)
    wrong = bytes([nonce[0] ^ 0x01]) + nonce[1:]

    assert open_secret(KEY, ciphertext, wrong) is None


def test_a_refusal_is_a_return_and_never_an_exception() -> None:
    """Callers branch on None. A raise here would surface as a 500 on an ordinary wrong password."""
    assert open_secret(KEY, b"", b"") is None
    assert open_secret(b"too short", b"nonsense", b"nonsense") is None


@pytest.mark.parametrize("plaintext", [b"", b"\x00", bytes(range(256)) * 8])
def test_it_seals_whatever_it_is_given(plaintext: bytes) -> None:
    """Empty, a null byte, and something larger than one block. None of these is a special case."""
    ciphertext, nonce = seal_secret(KEY, plaintext)

    assert open_secret(KEY, ciphertext, nonce) == plaintext
