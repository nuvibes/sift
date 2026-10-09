# SPDX-License-Identifier: AGPL-3.0-or-later
"""What counts as text: one definition for writing and for search, so stored text is findable."""

from __future__ import annotations

import unicodedata

UNPRINTABLE = frozenset({*range(0x20), 0x7F})

#: A lone surrogate cannot be encoded as UTF-8, so binding one to SQLite raises.
SURROGATES = frozenset(range(0xD800, 0xE000))

INVISIBLE = frozenset({0x200B, 0x2060, 0xFEFF, 0x00AD})

# The zero-width joiners stay out of `INVISIBLE`: they shape Persian, Indic scripts and emoji.

QUOTE = '"'


def printable(text: str) -> str:
    """The same text in NFC, so both sides of search spell it alike, with non-text removed."""
    composed = unicodedata.normalize("NFC", text)
    return "".join(
        character
        for character in composed
        if ord(character) not in UNPRINTABLE
        and ord(character) not in SURROGATES
        and ord(character) not in INVISIBLE
    )


def clean_name(value: str, *, what: str) -> str:
    """A name as it should be stored, or a refusal naming the rule it broke."""
    if QUOTE in value:
        raise ValueError(f"{what} cannot contain a double quote \u2014 the search box uses it")
    cleaned = printable(value).strip()
    if not cleaned:
        raise ValueError(f"{what} cannot be blank")
    return cleaned


def clean_stored_text(value: str) -> str:
    """Control characters out of text that did not come from a person typing."""
    return printable(value)


def clean_token_text(value: str) -> str:
    """Remote text also written as a filter token, so the quote comes out too."""
    return printable(value).replace(QUOTE, "")


def stripped_or_none(value: object) -> str | None:
    """Any value as text without outer spaces, or None when nothing is left."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def non_empty_str(value: object) -> str | None:
    """The value when it is a non-empty str, otherwise None."""
    return value if isinstance(value, str) and value else None
