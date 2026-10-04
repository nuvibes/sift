# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who makes a row: the `Made` every creating write takes, and how a username is stored."""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.text import clean_token_text

# Who made a row is a fact about the act, so every creating write takes a `Made`: a pass reading a
# folder name and a person typing that name reach the same upsert and leave different rows. `kind`
# is what `created_by_kind` stores ('sift' or 'user'; 'box' comes from `mark_created_by_box`), and
# `via` is which pass, in the words a file's marks use. The word list is in `kernel/vocabulary.py`,
# so the ledger can check a pass name without importing this package.


@dataclass(frozen=True, slots=True)
class Made:
    """Who is making a row, handed to the write that makes it.

    One argument because the three move together: a pass has no user and a user has no pass.
    `user_id` may be None for 'user': a person did it and the row does not say which.
    """

    kind: str
    via: str | None = None
    user_id: str | None = None


#: A pass of Sift's own, named.
def by_sift(via: str) -> Made:
    """Sift made this, and this is the pass that did it."""
    return Made("sift", via)


def by_user(user_id: str | None = None) -> Made:
    """Somebody made this. The id where the write path was handed one, and NULL where it was not."""
    return Made("user", None, user_id)


#: 'sift' with no pass named: what an older row carries. Not a default: every write asks for
#: `made` by keyword, and this is for the caller that means "Sift, and which pass is not worth a
#: word" (`tests/gates/test_who_made_a_row_is_said.py` keeps it from becoming one).
MADE_UNSAID = Made("sift", None)

#: A person did it and the row does not say which: what tests seed rows with, and how an older row
#: reads.
MADE_BY_A_PERSON = Made("user", None, None)


# Stored without a leading marker so `@name` and `name` are one row. Only the leading run goes: a
# stray `@` inside an identifier is left alone rather than guessed at.
_USERNAME_MARKERS = "@ \t"


def _clean_username(name: str) -> str:
    """A username as it is stored, control characters removed before the emptiness check.

    A downloader's username is a path segment from a pasted URL, so it is cleaned rather than
    refused, and one that was only control characters is caught as empty.
    """
    cleaned = clean_token_text(name).strip().lstrip(_USERNAME_MARKERS)
    if not cleaned:
        raise ValueError("a username cannot be empty")
    return cleaned
