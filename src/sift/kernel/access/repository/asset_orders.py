# SPDX-License-Identifier: AGPL-3.0-or-later
"""The orders a wall of files is sorted by: each sort's ORDER BY tail, the shuffle, and the
seek that continues a page after a row."""

from __future__ import annotations

from sift.kernel.when import day_start, today

#: The width the shuffle mixes in: every product stays inside SQLite's signed 64-bit integers,
#: where an overflow would turn the key into a float. Any odd multiplier is a bijection mod 2^32.
_WORD = 0xFFFFFFFF
_MIX_IN = 0x2C1B3C6D
_MIX_OUT = 0x045D9F3B


def _shuffle_key(row: str) -> str:
    """The shuffle's sort key for one rowid expression, as SQL: a multiply, an xorshift, a multiply.

    The xorshift is what stops some seeds walking the library in the order it was added. SQLite has
    no XOR, so `x ^ y` is `(x | y) - (x & y)`. Any row expression, so `_SEEK_AFTER` keys the
    anchor with the same text.
    """
    spread = f"((({row} + :shuffle_offset) & {_WORD}) * {_MIX_IN}) & {_WORD}"
    mixed = f"(({spread}) | (({spread}) >> 16)) - (({spread}) & (({spread}) >> 16))"
    return f"((({mixed}) * {_MIX_OUT} + :shuffle_stride) & {_WORD})"


# Every tail ends on `a.id`, a total order, so two files that tie cannot swap between pages.
# `NULLS LAST` keeps a file with no duration or size out of the front of "longest".
_ORDER_TAILS: dict[str, str] = {
    "newest": "          a.added_at DESC, a.id DESC",
    # When THIS user last opened it.
    "viewed": (
        "          (SELECT s.last_viewed_at FROM asset_user_state s"
        " WHERE s.asset_id = a.id AND s.user_id = :viewer) DESC NULLS LAST, a.id DESC"
    ),
    "oldest": "          a.added_at ASC, a.id ASC",
    # When its record was last edited (`kernel.access.edited`); pages by offset.
    "edited": (
        "          (SELECT e.edited_at FROM asset_edits e WHERE e.asset_id = a.id)"
        " DESC NULLS LAST, a.added_at DESC, a.id DESC"
    ),
    # Not `COLLATE NOCASE`, which folds ASCII only; see `kernel.sorting`.
    "name_az": (
        "          COALESCE(a.filename_sort, a.original_filename) ASC NULLS LAST, a.id ASC"
    ),
    "name_za": (
        "          COALESCE(a.filename_sort, a.original_filename) DESC NULLS LAST, a.id DESC"
    ),
    "longest": "          a.duration_ms DESC NULLS LAST, a.id DESC",
    "shortest": "          a.duration_ms ASC NULLS LAST, a.id ASC",
    "largest": "          a.size_bytes DESC NULLS LAST, a.id DESC",
    "smallest": "          a.size_bytes ASC NULLS LAST, a.id ASC",
    # How many times THIS user opened it; a file never opened has no row, so NULLS LAST.
    "most_viewed": (
        "          (SELECT s.view_count FROM asset_user_state s"
        " WHERE s.asset_id = a.id AND s.user_id = :viewer) DESC NULLS LAST, a.id DESC"
    ),
    # Times THIS user pressed the O mark; `NULLIF` puts a zero behind with the never touched.
    "o_count": (
        "          NULLIF((SELECT s.o_count FROM asset_user_state s"
        " WHERE s.asset_id = a.id AND s.user_id = :viewer), 0) DESC NULLS LAST, a.id DESC"
    ),
    # THIS user's heart and stars, off the `mine` join.
    "favorite": "          COALESCE(mine.favorite, 0) DESC, a.added_at DESC, a.id DESC",
    "rating": "          mine.rating DESC NULLS LAST, a.added_at DESC, a.id DESC",
    # When THIS user hearted it, off their `opinions`, by the opinion's id: a ULID never goes down,
    # where a clock steps backwards. A heart with no history sorts behind.
    "favorited": (
        "          (SELECT MAX(o.id) FROM opinions o"
        " WHERE o.subject_kind = 'asset' AND o.subject_id = a.id AND o.user_id = :viewer"
        " AND o.kind = 'favorite' AND o.after = 1) DESC NULLS LAST, a.id DESC"
    ),
    "favorited_oldest": (
        "          (SELECT MAX(o.id) FROM opinions o"
        " WHERE o.subject_kind = 'asset' AND o.subject_id = a.id AND o.user_id = :viewer"
        " AND o.kind = 'favorite' AND o.after = 1) ASC NULLS LAST, a.id ASC"
    ),
    # A shuffle that holds still while paged: a function of the row and the seed, not `random()`.
    "random": "          " + _shuffle_key("a.rowid") + ", a.id",
    # Closest match first; no text scores NULL and falls through to newest.
    "relevance": "          rl.score ASC NULLS LAST, a.added_at DESC, a.id DESC",
    # Nearest by meaning, apart from bm25 `relevance`. What the TEXT matched leads, or a literal
    # match the model did not name sorts behind every guess. Nothing ranked falls through to newest.
    "similarity": (
        "          rl.score ASC NULLS LAST, sm.score ASC NULLS LAST, a.added_at DESC, a.id DESC"
    ),
}

#: How many shuffles there are; the route bounds a seed by it and the client mints below it.
SHUFFLE_MODULUS = 2147483647

#: Spreads a seed over 64 bits before it is split in two. Odd, so two seeds never share a shuffle.
_SEED_SPREAD = 0x9E3779B97F4A7C15


def shuffle_of(seed: int | None) -> tuple[int, int]:
    """The two halves of a seed the shuffle key binds: `:shuffle_offset` and `:shuffle_stride`.

    Any integer is folded into the space, so the key's arithmetic cannot overflow. No seed means
    today, from the machine's local midnight, so a wall holds still for a visit.
    """
    chosen = day_start(today()) if seed is None else seed
    spread = (chosen % SHUFFLE_MODULUS) * _SEED_SPREAD & 0xFFFFFFFFFFFFFFFF
    return spread & _WORD, spread >> 32


#: How to continue a page from the row before it, for the sorts an index answers: one seek from
#: that row's key rather than a probe per row an offset passes over. Each is the tail's key compared
#: as a row value with the tail's NULLS LAST. Orders that read outside the row page by offset.
_SEEK_AFTER: dict[str, str] = {
    "newest": "(a.added_at, a.id) < (:after_key, :after_id)",
    "oldest": "(a.added_at, a.id) > (:after_key, :after_id)",
    "name_az": (
        "(:after_key IS NULL AND COALESCE(a.filename_sort, a.original_filename) IS NULL"
        " AND a.id > :after_id)"
        " OR (:after_key IS NOT NULL AND ("
        "COALESCE(a.filename_sort, a.original_filename) IS NULL"
        " OR (COALESCE(a.filename_sort, a.original_filename), a.id) > (:after_key, :after_id)))"
    ),
    "name_za": (
        "(:after_key IS NULL AND COALESCE(a.filename_sort, a.original_filename) IS NULL"
        " AND a.id < :after_id)"
        " OR (:after_key IS NOT NULL AND ("
        "COALESCE(a.filename_sort, a.original_filename) IS NULL"
        " OR (COALESCE(a.filename_sort, a.original_filename), a.id) < (:after_key, :after_id)))"
    ),
    "longest": (
        "(:after_key IS NULL AND a.duration_ms IS NULL AND a.id < :after_id)"
        " OR (:after_key IS NOT NULL AND (a.duration_ms IS NULL"
        " OR (a.duration_ms, a.id) < (:after_key, :after_id)))"
    ),
    "shortest": (
        "(:after_key IS NULL AND a.duration_ms IS NULL AND a.id > :after_id)"
        " OR (:after_key IS NOT NULL AND (a.duration_ms IS NULL"
        " OR (a.duration_ms, a.id) > (:after_key, :after_id)))"
    ),
    "largest": (
        "(:after_key IS NULL AND a.size_bytes IS NULL AND a.id < :after_id)"
        " OR (:after_key IS NOT NULL AND (a.size_bytes IS NULL"
        " OR (a.size_bytes, a.id) < (:after_key, :after_id)))"
    ),
    "smallest": (
        "(:after_key IS NULL AND a.size_bytes IS NULL AND a.id > :after_id)"
        " OR (:after_key IS NOT NULL AND (a.size_bytes IS NULL"
        " OR (a.size_bytes, a.id) > (:after_key, :after_id)))"
    ),
    # Here so a continued page follows THAT row whatever was added since; no index answers it.
    "random": (f"({_shuffle_key('a.rowid')}, a.id) > ({_shuffle_key(':after_key')}, :after_id)"),
}

#: The column each seekable sort's key is read from on the row a page continues after.
_SEEK_KEY_OF: dict[str, str] = {
    "newest": "added_at",
    "oldest": "added_at",
    "name_az": "COALESCE(filename_sort, original_filename)",
    "name_za": "COALESCE(filename_sort, original_filename)",
    "longest": "duration_ms",
    "shortest": "duration_ms",
    "largest": "size_bytes",
    "smallest": "size_bytes",
    "random": "rowid",
}

#: The sorts a page may be continued under; the client sends `after` only for these.
SEEKABLE_SORTS: frozenset[str] = frozenset(_SEEK_AFTER)

if set(_SEEK_AFTER) != set(
    _SEEK_KEY_OF
):  # pragma: no cover (an edit to one table and not the other)
    raise RuntimeError("the seek predicates and the seek keys name different sorts")


_SEEK_OPEN = "\n AND ("
_SEEK_CLOSE = ")"


_SEEK_ANCHOR = "SELECT <<KEY>> AS key FROM assets WHERE id = ?"

_SEEK_ANCHORS: dict[str, str] = {
    sort: _SEEK_ANCHOR.replace("<<KEY>>", key) for sort, key in _SEEK_KEY_OF.items()
}


def seek_anchor(sort: str) -> str:
    """The statement that reads a row's key under `sort`, binding the row's id."""
    return _SEEK_ANCHORS[sort]


#: What a request gets when it names no sort or one not offered; the added_at index serves it.
DEFAULT_SORT = "newest"

#: Closest match first, named once because the search route defaults to it and the grid does not.
RELEVANCE = "relevance"

#: Nearest first by what a model made of the words.
SIMILARITY = "similarity"

#: Every sort a caller may name; the router refuses anything else with a 422.
SORT_KEYS: frozenset[str] = frozenset(_ORDER_TAILS)
