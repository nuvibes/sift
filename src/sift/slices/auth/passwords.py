# SPDX-License-Identifier: AGPL-3.0-or-later
"""What makes a password and a PIN acceptable.

The sign-in password is the whole front door: there is no email and no third-party login behind
it, so its strength is the strength of the lock. Three things are checked, and all three must
pass:

  * length: at least ten characters,
  * variety: an upper-case letter, a lower-case letter, a digit and a symbol, and
  * not obviously breached: absent from a bundled list of the passwords that turn up first in
    every leak.

The breach list ships in the image and is checked offline. Sift runs on machines that may have no
outbound internet, so there is no call to a third-party "have I been pwned" service; the trade-off
is that the list is a snapshot rather than the whole internet's, and it is a floor, not a promise.

A strength meter belongs in the browser as live guidance while someone types. It is not enforced
here (guidance that blocks is just a worse version of these rules), and it is the frontend's to
add.
"""

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

# The ~100k most common passwords from a public breach corpus (SecLists, MIT), lower-cased,
# ASCII-filtered and deduplicated. Plain text rather than compressed so the source stays greppable;
# it is checked offline, so nothing here ever leaves the machine.
_BREACH_LIST = "common_passwords.txt"

# A small set of ASCII symbols is not enough: "a symbol" means anything that is not a letter,
# a digit or whitespace, so accented punctuation and the like all count. Whitespace is excluded
# because a trailing space is a variety class nobody meant to add.
_SYMBOLS_EXCLUDED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


class PasswordPolicyError(ValueError):
    """A password or PIN that does not meet the policy. The message is safe to show a user."""


def _parse_breach_list(text: str) -> frozenset[str]:
    """Lower-case, strip and deduplicate the list, and refuse an empty result.

    An empty breach list is not a smaller check: it is a check that passes everything, and the one
    failure mode this must not have is to wave a leaked password through because the data was not
    there. A missing file, and equally a file that is present but empty or blank (a bad copy into the
    image, a mount that shadowed it, a truncated write), both raise rather than silently accept every
    password. Kept separate from the file read so this refusal is directly testable.
    """
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


#: The substitutions people reach for when a policy demands a symbol or a digit. Folded before the
#: list is consulted, because the list holds the plain words and the whole point of a substitution
#: is that it leaves the word recognizable, to a person, and to anybody cracking a hash.
_LEET = str.maketrans(
    {"4": "a", "@": "a", "3": "e", "1": "i", "!": "i", "0": "o", "5": "s", "$": "s", "7": "t"}
)


def _variants(password: str) -> tuple[str, ...]:
    """The forms of this password worth looking up.

    The list holds bare words. `Password123!` is not in it and `password123` is, so without this
    the exclamation mark alone would get a leaked password past the check, and adding one
    symbol to a common word is the single most predictable thing a person does when a policy asks
    for a symbol.

    So three passes, each cheap: as typed, with the trailing decoration taken off, and with the
    obvious letter-for-symbol substitutions undone. Widening this way costs one set lookup each and
    catches the passwords the list was assembled to catch.
    """
    lowered = password.lower()
    trimmed = lowered.rstrip("!@#$%^&*()-_=+.,?/\\|~`'\"[]{}<>:; ")
    forms = {lowered, trimmed}
    for one in (lowered, trimmed):
        forms.add(one.translate(_LEET))
    # An empty string is what a password of nothing but punctuation trims to. It is not a password
    # anybody typed and it must not be looked up: an empty entry in the list would refuse
    # everything.
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
    """Accept a password that meets the policy; raise PasswordPolicyError with a reason otherwise.

    The reasons are specific on purpose: "add a symbol" is help, "invalid password" is a riddle.
    None of them reveal anything about any other user, so specificity costs nothing here.
    """
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
    """A new PIN is digits only, exactly `PIN_DIGITS` of them. It guards a screen, not the key.

    Checked where a PIN is SET and nowhere else: a shorter PIN set under an older rule still
    verifies, for the reason `PIN_DIGITS` gives.
    """
    if not pin.isdigit():
        raise PasswordPolicyError("A PIN is digits only.")
    if len(pin) != PIN_DIGITS:
        raise PasswordPolicyError(f"A PIN is {PIN_DIGITS} digits.")


# --- inventing credentials ---------------------------------------------------------------------
#
# Everything below draws from `secrets`, which reads the operating system's random source. A
# seeded generator is the one thing that must never appear here: a password reproducible from a
# known seed is not a password, and the mistake is invisible in review because the output looks the
# same either way.

_GENERATED_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""Characters a generated password is built from.

Missing on purpose: l, I, 1, o, O, 0. This password is read off one screen and typed into another,
often out loud, and a character somebody has to squint at is a support request. The alphabet is 57
characters, so each one carries about 5.8 bits.
"""

_GENERATED_SYMBOLS = "!@#$%^&*-_=+"
"""The symbol the policy asks for, from a set that survives being typed on any keyboard layout and
does not need quoting when somebody pastes it into a shell."""


def generated_password() -> str:
    """A strong password, built to pass the policy above rather than hoping it does.

    Composed rather than sampled-and-retried: the four classes the policy requires are placed
    deliberately and the remainder is filled from the alphabet, then the whole thing is shuffled so
    the classes are not always in the same positions. Sampling until a random string happened to
    satisfy every rule would work, and would loop for as long as chance decided.

    Long enough that the breach list is irrelevant to it (nothing this shape is in any leak), but
    it is still checked by the same `validate_password` every typed password goes through, on the
    principle that a generator exempt from the policy is a generator that quietly drifts out of it.
    """
    length = GENERATED_PASSWORD_LENGTH
    characters = [
        secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"),
        secrets.choice("abcdefghijkmnopqrstuvwxyz"),
        secrets.choice("23456789"),
        secrets.choice(_GENERATED_SYMBOLS),
    ]
    characters += [secrets.choice(_GENERATED_ALPHABET) for _ in range(length - len(characters))]
    # `SystemRandom` rather than `random.shuffle`, so the ordering comes from the same source the
    # characters did. Shuffling a securely-chosen set with a seeded generator would leak structure.
    secrets.SystemRandom().shuffle(characters)
    password = "".join(characters)
    validate_password(password)
    return password


def generated_username() -> str:
    """A name for an invented user: a word and four digits.

    Readable on purpose. It is spoken and typed by two different people, so it is short and has no
    ambiguous characters, and it is the name that will sit in a sharing dialog afterwards, where
    a random string tells nobody anything. Uniqueness is the caller's: it retries against the table
    rather than trusting four digits to be free.
    """
    number = secrets.randbelow(10_000)
    return f"guest-{number:04d}"
