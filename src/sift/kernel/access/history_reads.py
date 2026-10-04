# SPDX-License-Identifier: AGPL-3.0-or-later
"""The small readers every History thread shares: which tables this database holds, a nullable
column as text or as a count, and a millisecond stamp as seconds.
"""

from __future__ import annotations

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"


#: The face tables AND THE SEMANTIC INDEX keep MILLISECONDS while every other decision in the
#: library is in seconds. The division happens here, at the one read that puts the two beside each
#: other, and this constant is what stops it being three unexplained `// 1000`s.
#:
#: The sixteen millisecond columns are every `face_*` stamp, `cover_pictures.created_at` and
#: `semantic_indexed.indexed_at`; every read of one on a History thread goes through `_seconds`, or
#: the line is dated some fifty thousand years out.
_MS_PER_SECOND = 1000


def _seconds(value: object) -> int | None:
    """A millisecond stamp (the face tables, the semantic index) as seconds; None where none."""
    return None if value is None else int(str(value)) // _MS_PER_SECOND


def _count_or_none(value: object) -> int | None:
    """A nullable count column as an int, keeping NULL as None: "not known", never zero."""
    return None if value is None else int(str(value))


def _text_or_none(value: object) -> str | None:
    """A nullable text column as a str, keeping NULL as None."""
    return None if value is None else str(value)
