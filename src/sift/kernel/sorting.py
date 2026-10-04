# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a list of named things is put in order, so that a person reading it recognises the order.

## Why not NOCASE

`ORDER BY name COLLATE NOCASE` folds ASCII and nothing else:

    ['Alesund', 'apple', 'Apple', 'iPhone', 'Zebra', 'Zoe', 'Alesund', 'eclair']
                                                            ^ with a ring      ^ with an acute

Every accented name sorts after Z, because its first byte is above 'z'. `Alesund` belongs with the
A's.

## Why a stored key and not a collation

SQLite will take an application-defined collation, and it would be less code. It is the wrong
answer, and the reason is not speed:

**An index built with a custom collation cannot be read by anything that does not have that
collation.** A plain `sqlite3` opening the file (a backup check, a recovery, somebody looking at
their own library) gets `no such collation sequence`. Sift's database is a file the person running
it owns, and a file that only Sift can read is a worse thing to hand somebody than a column.

The cost is real and is stated rather than hidden: every write of a name has to write its key, and a
write that forgets is silent: the row simply sorts wrong. `tests/gates/test_one_ordering.py`
refuses a statement that writes one without the other, which is what makes it safe rather than
careful.

## What the key is

Compatibility-decomposed, combining marks removed, case-folded, whitespace collapsed. That folds
`A-with-a-ring` to `a`, `e-with-an-acute` to `e`, a non-breaking space to a space, and the wide
Latin letters to ordinary ones, so a name files where somebody reading it would look.

It is deliberately NOT a full locale collation. Sift does not know what language a library is in,
and a key that guesses is worse than one whose rule can be stated in a sentence: this puts every
Latin-script name where its unaccented spelling would go, which is what somebody scanning a list
expects. Scripts that do not decompose to Latin keep their own order among
themselves, after the Latin ones, which is correct, since
nothing here can know how a reader of that script would order them.

**It is not a fold for MATCHING.** Two different names can share a key (`Renee` and the same name
with two accents) and that is right for ordering and wrong for identity. Nothing here is a
uniqueness rule; the `UNIQUE ... COLLATE NOCASE` constraints on those tables are untouched.
"""

from __future__ import annotations

import unicodedata

__all__ = ["sort_key"]


def sort_key(text: str) -> str:
    """The stored form a named row is ordered by.

    Empty in, empty out: a name is never empty in Sift, but a key is written beside whatever the
    caller has, and a function that raised here would turn a data problem into a failed write.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.casefold().split())
