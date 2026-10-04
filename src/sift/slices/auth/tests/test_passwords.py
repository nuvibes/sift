# SPDX-License-Identifier: AGPL-3.0-or-later
"""The password and PIN policy, one rule at a time."""

from __future__ import annotations

import pytest

from sift.slices.auth.passwords import (
    PasswordPolicyError,
    _parse_breach_list,
    is_breached,
    validate_password,
    validate_pin,
)

pytestmark = pytest.mark.unit

GOOD = "Corr3ct-Horse!staple9"


def test_a_strong_password_is_accepted() -> None:
    validate_password(GOOD)  # does not raise


def test_a_short_password_is_rejected() -> None:
    with pytest.raises(PasswordPolicyError, match="at least 10"):
        validate_password("Ab1!xyz")  # 7 characters, every class present


@pytest.mark.parametrize(
    ("password", "missing"),
    [
        ("corr3ct-horse!staple", "upper-case"),
        ("CORR3CT-HORSE!STAPLE", "lower-case"),
        ("Corranct-Horse!staple", "number"),
        ("Corr3ctHorse9staple1", "symbol"),
    ],
)
def test_each_missing_character_class_is_rejected(password: str, missing: str) -> None:
    with pytest.raises(PasswordPolicyError, match=missing):
        validate_password(password)


def test_a_breached_password_is_rejected_even_if_it_meets_the_shape() -> None:
    # "Password1!" is ten characters with every class present, and it leads the leak lists. The
    # complexity rules pass it; the breach check is what must reject it.
    upper, lower, digit, symbol = any(c.isupper() for c in "Password1!"), True, True, True
    assert upper and lower and digit and symbol
    with pytest.raises(PasswordPolicyError, match="commonly-leaked"):
        validate_password("Password1!")


def test_an_absurdly_long_password_is_rejected() -> None:
    with pytest.raises(PasswordPolicyError, match="longer than"):
        validate_password("A1!" + "a" * 2000)


def test_the_breach_check_is_case_insensitive() -> None:
    assert is_breached("password")
    assert is_breached("PASSWORD")
    assert not is_breached(GOOD)


def test_the_breach_list_loads_and_is_not_empty() -> None:
    # A missing list would pass every password, so it is proved present and populated.
    assert is_breached("123456")
    assert is_breached("qwerty")


def test_an_empty_or_blank_breach_list_fails_closed() -> None:
    # A present-but-empty file (a bad copy, a shadowed mount, a truncated write) must not turn the
    # breach check into one that accepts everything. It raises instead of returning an empty set.
    for text in ["", "   \n\n  \n"]:
        with pytest.raises(RuntimeError, match="empty"):
            _parse_breach_list(text)
    assert _parse_breach_list("123456\nqwerty\n") == frozenset({"123456", "qwerty"})


@pytest.mark.parametrize("pin", ["123456", "000000", "246810"])
def test_a_valid_pin_is_accepted(pin: str) -> None:
    validate_pin(pin)


# Four and five digits are refused where a PIN is set: one length everywhere a PIN is typed.
@pytest.mark.parametrize("pin", ["123", "1234", "12345", "1234567", "12a456", "abcdef", ""])
def test_an_invalid_pin_is_rejected(pin: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_pin(pin)


def test_a_missing_breach_list_is_shouted_about_rather_than_shrugged_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing breach list raises rather than becoming a check that passes everything."""
    import sift.slices.auth.passwords as module

    def _no_data(_package: str) -> object:
        raise FileNotFoundError("no such package")

    module._breached.cache_clear()
    monkeypatch.setattr("sift.slices.auth.passwords.resources.files", _no_data)
    try:
        with pytest.raises(RuntimeError, match="packaging error"):
            is_breached("123456")
    finally:
        # The result is cached for the process, so a poisoned cache would follow this test around.
        module._breached.cache_clear()


@pytest.mark.parametrize(
    "dressed",
    [
        "Password123!",  # a symbol on the end
        "Passw0rd123!",  # a digit for a letter, and a symbol
        "Iloveyou123!",
        "Monkey1234!",
        "Sunshine123!",
        "Qwerty12345!",
    ],
)
def test_a_leaked_password_in_fancy_dress_is_still_refused(dressed: str) -> None:
    """The list holds bare words, and a bare word is not what anybody types at a policy.

    Asked for a symbol, people put one on the end; asked for a digit, they swap a letter for the
    digit that looks like it. Accepting `Password123!` while refusing `password123` would make
    the check answer the letter of its own question and none of its purpose.
    """
    with pytest.raises(PasswordPolicyError, match="commonly-leaked"):
        validate_password(dressed)


@pytest.mark.parametrize(
    "good",
    [
        "Corr3ct-Horse!staple9",
        "An0ther-Secur3!keyword",
        "Quiet-Harbour-Lantern-4!",
    ],
)
def test_a_real_passphrase_is_not_caught_by_the_widening(good: str) -> None:
    """The other side of it, and the one that would be reported as Sift refusing everything.

    Undoing substitutions makes the check match more things, and matching more things is only
    useful while it is not matching good ones. These are the shape the policy is asking for.
    """
    validate_password(good)


def test_a_password_of_nothing_but_punctuation_does_not_match_an_empty_entry() -> None:
    """Trimming the trailing decoration off `!!!!!!!!!!` leaves nothing at all, and looking nothing
    up must not be how every password gets refused."""
    with pytest.raises(PasswordPolicyError) as raised:
        validate_password("!!!!!!!!!!")
    # Refused for what it actually lacks (letters and a digit), not as a leaked password.
    assert "commonly-leaked" not in str(raised.value)
