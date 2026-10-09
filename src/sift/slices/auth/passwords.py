# SPDX-License-Identifier: AGPL-3.0-or-later
"""What makes a password and a PIN acceptable: length, variety, and not in the breach list.

The breach list ships with Sift and is checked offline: a floor, not a promise."""

from __future__ import annotations

import functools
import secrets
from importlib import resources

from sift.slices.auth.tuning import (
    GENERATED_PASSWORD_LENGTH,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PIN_DIGITS,
)

# The ~100k most common breached passwords (SecLists, MIT), plain text so it stays greppable.
_BREACH_LIST = "common_passwords.txt"

# Any non-letter, non-digit, non-space counts as a symbol.
_SYMBOLS_EXCLUDED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


class PasswordPolicyError(ValueError):
    """A password or PIN that does not meet the policy. The message is safe to show a user."""


def _parse_breach_list(text: str) -> frozenset[str]:
    """The list lower-cased and deduplicated; empty raises, as it would pass every password."""
    words = frozenset(line.strip().lower() for line in text.splitlines() if line.strip())
    if not words:
        raise RuntimeError(
            f"the bundled breach list {_BREACH_LIST} is empty, so no password could be recognized "
            "as breached. This is a packaging error, not a configuration one."
        )
    return words


@functools.lru_cache(maxsize=1)
def _breached() -> frozenset[str]:
    """The bundled breach list, loaded once. Raises if it is missing or empty (see above)."""
    try:
        text = (resources.files("sift.slices.auth.data") / _BREACH_LIST).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            f"the bundled breach list {_BREACH_LIST} is missing, so a breached password could not "
            "be recognized. This is a packaging error, not a configuration one."
        ) from exc
    return _parse_breach_list(text)


#: Folded before the lookup: a substitution leaves the word recognizable to a cracker too.
_LEET = str.maketrans(
    {"4": "a", "@": "a", "3": "e", "1": "i", "!": "i", "0": "o", "5": "s", "$": "s", "7": "t"}
)


def _variants(password: str) -> tuple[str, ...]:
    """The forms worth looking up: as typed, trailing decoration off, and leet undone."""
    lowered = password.lower()
    trimmed = lowered.rstrip("!@#$%^&*()-_=+.,?/\\|~`'\"[]{}<>:; ")
    forms = {lowered, trimmed}
    for one in (lowered, trimmed):
        forms.add(one.translate(_LEET))
    # All-punctuation trims to empty, which must never be looked up.
    return tuple(form for form in forms if form)


def is_breached(password: str) -> bool:
    """Whether the password, or an obvious dressing-up of one, is in the bundled leak list."""
    known = _breached()
    return any(form in known for form in _variants(password))


def _character_classes(password: str) -> tuple[bool, bool, bool, bool]:
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(not c.isspace() and c not in _SYMBOLS_EXCLUDED for c in password)
    return has_upper, has_lower, has_digit, has_symbol


def validate_password(password: str) -> None:
    """Accept a password that meets the policy; raise PasswordPolicyError with a specific reason."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(f"That's longer than {PASSWORD_MAX_LENGTH} characters.")

    has_upper, has_lower, has_digit, has_symbol = _character_classes(password)
    missing = []
    if not has_upper:
        missing.append("an upper-case letter")
    if not has_lower:
        missing.append("a lower-case letter")
    if not has_digit:
        missing.append("a number")
    if not has_symbol:
        missing.append("a symbol")
    if missing:
        raise PasswordPolicyError("Add " + ", ".join(missing) + ".")

    if is_breached(password):
        raise PasswordPolicyError(
            "That password appears in a list of commonly-leaked passwords. Choose another."
        )


def validate_pin(pin: str) -> None:
    """A new PIN is exactly `PIN_DIGITS` digits; checked only where one is set."""
    if not pin.isdigit():
        raise PasswordPolicyError("A PIN is digits only.")
    if len(pin) != PIN_DIGITS:
        raise PasswordPolicyError(f"A PIN is {PIN_DIGITS} digits.")


#: Without l, I, 1, o, O and 0, since it is read off one screen and typed into another.
_GENERATED_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: Symbols any keyboard layout types and a shell needs no quoting for.
_GENERATED_SYMBOLS = "!@#$%^&*-_=+"


def generated_password() -> str:
    """A strong password composed to pass the policy, and still checked against it."""
    length = GENERATED_PASSWORD_LENGTH
    characters = [
        secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"),
        secrets.choice("abcdefghijkmnopqrstuvwxyz"),
        secrets.choice("23456789"),
        secrets.choice(_GENERATED_SYMBOLS),
    ]
    characters += [secrets.choice(_GENERATED_ALPHABET) for _ in range(length - len(characters))]
    # Not `random.shuffle`: a seeded shuffle would leak structure.
    secrets.SystemRandom().shuffle(characters)
    password = "".join(characters)
    validate_password(password)
    return password


def generated_username() -> str:
    """A readable name for an invented user; the caller retries until it is free."""
    number = secrets.randbelow(10_000)
    return f"guest-{number:04d}"
