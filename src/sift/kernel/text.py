# SPDX-License-Identifier: AGPL-3.0-or-later
"""What counts as text, in the one place both the write side and the read side read it from.

FTS5 reads a match expression as a C string, so a NUL ends it early whatever the quoting, and the
search side removes such characters from a query. Text stored with one would then be unreachable:
listed, attached, and never found. So the same definition cleans what is written, and the two sides
cannot disagree: text that is storable is findable.
"""

from __future__ import annotations

import unicodedata

#: Everything below a space, plus DEL: a name never means a NUL, a bell or an escape; a paste
#: carries one by accident.
UNPRINTABLE = frozenset({*range(0x20), 0x7F})

#: The surrogate range: halves of a UTF-16 pairing. A lone one cannot be encoded as UTF-8, so
#: binding one to SQLite raises before the query runs.
SURROGATES = frozenset(range(0xD800, 0xE000))

#: Characters that take no space and say nothing, arriving by paste: the name looks like what
#: somebody types and does not match it. Zero-width space, word joiner, byte-order mark, soft
#: hyphen.
INVISIBLE = frozenset({0x200B, 0x2060, 0xFEFF, 0x00AD})

# Not in `INVISIBLE`: the zero-width non-joiner and joiner (U+200C, U+200D) decide which letters
# join in Persian and several Indic scripts, and hold emoji sequences together, so removing one
# changes a name. A name spelled with one is harder to search for, which beats a name made wrong.

#: The double quote, and only it: the query language spends it to keep a spaced name one token and
#: has no escape, so a stored name cannot carry one. The apostrophe stays: `O'Brien` is a name, not
#: punctuation to the parser.
QUOTE = '"'


def printable(text: str) -> str:
    """The same text, in one canonical spelling, with everything that is not text removed.

    Never raises, may return empty. NFC matters as much as the removal: an accented letter can be
    spelled two ways that look identical and compare unequal, and both sides of the search read
    this, so both spell it the same.
    """
    composed = unicodedata.normalize("NFC", text)
    return "".join(
        character
        for character in composed
        if ord(character) not in UNPRINTABLE
        and ord(character) not in SURROGATES
        and ord(character) not in INVISIBLE
    )


def clean_name(value: str, *, what: str) -> str:
    """A name as it should be stored, or a refusal saying which rule it broke.

    `what` is the noun phrase the message opens with ("a tag's name"), so a form with four fields
    says which one. Control characters are removed silently: nobody types one, and refusing a name
    over an invisible character helps nobody. A double quote is refused out loud: it was typed on
    purpose, and deleting it would hand back a different name. Blank is checked last, so a name of
    nothing but control characters earns the blank message.
    """
    if QUOTE in value:
        raise ValueError(f"{what} cannot contain a double quote \u2014 the search box uses it")
    cleaned = printable(value).strip()
    if not cleaned:
        raise ValueError(f"{what} cannot be blank")
    return cleaned


def clean_stored_text(value: str) -> str:
    """Control characters out of text that arrived from somewhere other than a person typing.

    A filename off a downloader, a folder name, a site name from a pasted URL: there is nobody to
    show a refusal to, so this only removes, and an empty result is the caller's to fall back from.
    Quotes stay: a file really can be called `say "hi".mp4`. Text written as a filter token takes
    `clean_token_text` instead.
    """
    return printable(value)


def clean_token_text(value: str) -> str:
    """Remote text that is also written as a filter token: a username, a site name.

    Cleaned rather than refused, like `clean_stored_text`, but the quote comes out too: one is
    named in a query as `sites:"example"`, and the grammar has no escape for a quote inside it.
    """
    return printable(value).replace(QUOTE, "")


def stripped_or_none(value: object) -> str | None:
    """Any value as text without its outer spaces, or None when nothing is left to write.

    For a value read off the wire or out of a record, where a number is still a name and a
    string of spaces is nothing at all.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def non_empty_str(value: object) -> str | None:
    """The value itself when it is a non-empty str, and None for anything else.

    For a field of a stored record, where a number or a list under a key that holds a string is a
    shape this build does not recognise, and is read as absent rather than turned into words.
    """
    return value if isinstance(value, str) and value else None
