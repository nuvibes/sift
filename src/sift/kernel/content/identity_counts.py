# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many files each pass still has to do, and the pages it reads them from."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from time import monotonic
from typing import Any, TypeVar, cast

from sift.kernel.changes import About, mark_of
from sift.kernel.content.backlog import StoredCounts, Term, Totals
from sift.kernel.content.identity_models import (
    _EVERY_READ_FILE,
    RECIPE_VERSIONS,
    DerivativeKind,
    Lack,
    Lacking,
    LibraryPage,
    PageKey,
    VerdictProduct,
    Within,
    _made_for_sql,
)
from sift.kernel.content.identity_store import StoreCore
from sift.kernel.content.perceptual import FINGERPRINT_VERSION
from sift.kernel.content.presence import HAS_A_PRESENT_COPY
from sift.kernel.db import in_clause
from sift.kernel.ingress import Kind
from sift.kernel.sql_splice import splice

_T = TypeVar("_T")

#: What can move a count of the library: a pass or a decision writing (`LIBRARY`), a file arriving
#: or leaving (`ARRIVALS`), a switch or a folder's rule (`SETTINGS`), and any job's progress (`JOBS`).
#: A count is held until one of them is announced, as the workbench holds its piles.
MOVED_BY = frozenset({About.LIBRARY, About.ARRIVALS, About.SETTINGS, About.JOBS})

#: The longest a count is held all the same: what bounds a change nobody announced.
HELD_AT_MOST_SECONDS = 300.0

#: How many counts are held; a few per product and folder choice.
_HELD_KEPT = 256

#: A file in no refusing folder, or also in one that does not refuse: `ImportPolicy._on`'s EITHER
#: rule as a condition. The refusing roots bind twice, one JSON array each time.
_OUTSIDE_REFUSING_ROOTS = (
    "(NOT EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id"
    " AND l.root_id IN (SELECT value FROM json_each(?)))"
    " OR EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id"
    " AND l.root_id NOT IN (SELECT value FROM json_each(?))))"
)


def wanted_outside(refusing: Sequence[Sequence[str]]) -> Within | None:
    """The files not shut out by folders refusing a piece of work, one set of roots per KEY the
    work depends on. None, not an always-true condition, when no folder refuses."""
    sets = [json.dumps(sorted(set(roots))) for roots in refusing if roots]
    if not sets:
        return None
    return Within(
        condition=" AND ".join([_OUTSIDE_REFUSING_ROOTS] * len(sets)),
        params=tuple(value for one in sets for value in (one, one)),
    )


#: A file with a copy present in one of the named folders, the ids bound once as a JSON array.
_IN_ROOTS = (
    "EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id AND l.status = 'present'"
    " AND l.root_id IN (SELECT value FROM json_each(?)))"
)


def _in_roots(statement: str, roots: Sequence[str] | None) -> str:
    """`<<IN_ROOTS>>` filled with `_IN_ROOTS` for some folders, or nothing for the whole library;
    no other text goes in, and `_roots_bound` is its value."""
    return statement.replace("<<IN_ROOTS>>", "" if roots is None else f" AND {_IN_ROOTS}")


def _roots_bound(roots: Sequence[str] | None) -> tuple[Any, ...]:
    """What binds at the place `_in_roots` filled: the folders as one array, or nothing."""
    return () if roots is None else (json.dumps(sorted(set(roots))),)


_AMONG = " AND a.id IN (SELECT value FROM json_each(?))"


def _term_params(lacks: Sequence[Lack]) -> list[Any]:
    """Every term's values in `_lacking_statement`'s `?` order: condition, `within`, product."""
    params: list[Any] = []
    for one in lacks:
        params.extend(one.params)
        if one.within is not None:
            params.extend(one.within.params)
        if one.product is not None:
            params.append(one.product)
    return params


# Files never read: the scan's own "still needs probing" condition, so the number and the button
# agree. A file the probe gave up on was read, and is left out of both the count and the page.
_COUNT_UNREAD = """
SELECT COUNT(*) AS total FROM assets a
 WHERE a.probed_at IS NULL
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""

#: Whether any file is so, for a start: a seek of the unread rows' own index.
_ANY_UNREAD = """
SELECT EXISTS (SELECT 1 FROM assets a
                WHERE a.probed_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                                   WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0))
       AS found
"""

#: For the pass at start that hands out the probes a cut-off scan never did.
_UNREAD_PAGE = splice(
    """
SELECT a.id FROM assets a
 WHERE a.probed_at IS NULL
   AND {{PRESENT}}
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
 ORDER BY a.added_at, a.id
 LIMIT ? OFFSET ?
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

#: Browse's files: a removed folder's keep their rows and no place.
_HAS_A_PLACE = "EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id)"

_COUNT_ASSETS = splice("SELECT COUNT(*) AS total FROM assets a WHERE {{PLACE}}", PLACE=_HAS_A_PLACE)

_COUNT_PLACED = "SELECT COUNT(DISTINCT asset_id) AS total FROM asset_locations"

#: Which read files want each product, by its key, where not every one does.
_MADE_FOR_READ: Mapping[str, str] = {
    VerdictProduct.PREVIEWS.value: _made_for_sql(DerivativeKind.PREVIEW),
    VerdictProduct.SPRITES.value: _made_for_sql(DerivativeKind.SPRITE),
    "music": "(a.acodec IS NOT NULL)",
}

#: Which kinds of unread file want each product, by its key; a key not named, every kind. Only the
#: kind is known before the read, so an unread video wants a strip and a sound fingerprint.
UNREAD_KINDS: Mapping[str, frozenset[str]] = {
    VerdictProduct.PREVIEWS.value: frozenset({Kind.GIF.value, Kind.VIDEO.value}),
    VerdictProduct.SPRITES.value: frozenset({Kind.GIF.value, Kind.VIDEO.value}),
    "music": frozenset({Kind.VIDEO.value}),
}


def _of_kinds(kinds: frozenset[str]) -> str:
    """An `UNREAD_KINDS` entry as a condition; only those constants go in."""
    return "(" + " OR ".join(f"a.media_type = '{one}'" for one in sorted(kinds)) + ")"


#: Not yet read and not given up on: it is coming. Binds the read's verdict product.
_COMING = """(a.probed_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM file_verdicts pv
                       WHERE pv.asset_id = a.id AND pv.product = ? AND pv.transient = 0))"""

# THE ONE RULE for what a product still has to do, both ends of every bar after the read: a file with
# a present copy, not given up on, made for it, read or coming. Binds the read's verdict, then its.
_WANTING = """{{PRESENT}}
   AND ((a.probed_at IS NOT NULL AND {{MADE_FOR}}) OR ({{COMING}} AND {{MADE_FOR_UNREAD}}))
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""

_COUNT_WANTING = (
    """
SELECT COUNT(*) AS total FROM assets a
 WHERE """
    + _WANTING
)

_COUNT_COMING = """
SELECT COUNT(*) AS total FROM assets a
 WHERE {{PRESENT}}
   AND {{COMING}} AND {{MADE_FOR_UNREAD}}
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""


def _bar_statement(template: str, product: str | None) -> str:
    """One end of a bar for this product, or for work every file wants (None)."""
    made_for = _MADE_FOR_READ.get(product or "", _EVERY_READ_FILE)
    kinds = UNREAD_KINDS.get(product or "")
    unread = _EVERY_READ_FILE if kinds is None else _of_kinds(kinds)
    fragments = {"PRESENT": HAS_A_PRESENT_COPY, "COMING": _COMING, "MADE_FOR_UNREAD": unread}
    if "{{MADE_FOR}}" in template:
        fragments["MADE_FOR"] = made_for
    return splice(template, **fragments)


#: By the products a file's kind narrows, and None for the rest.
_NARROWED = (*sorted(set(_MADE_FOR_READ) | set(UNREAD_KINDS)), None)

_COUNT_WANTING_OF = {key: _bar_statement(_COUNT_WANTING, key) for key in _NARROWED}

_WANTING_OF = {key: _bar_statement(_WANTING, key) for key in _NARROWED}

_COUNT_COMING_OF = {key: _bar_statement(_COUNT_COMING, key) for key in _NARROWED}

#: The facts a naming template can use. The username is the one filed first (a download's own).
_NAMING_FACTS = """
SELECT a.id AS id, a.title AS title, a.release_date AS release_date, a.added_at AS added_at,
       a.site_code AS site_code, u.name AS creator, s.name AS site
  FROM assets a
  LEFT JOIN usernames u ON u.id = (
        SELECT au.username_id
          FROM asset_usernames au
         WHERE au.asset_id = a.id
         ORDER BY au.decided_at IS NULL, au.decided_at, au.username_id
         LIMIT 1)
  LEFT JOIN sites s ON s.id = u.site_id
 WHERE a.id IN (?*)
"""

_NAMING_FACTS_AT_ONCE = 500

#: A page of the library in intake order: only read files with a present copy (no pass can work
#: on a file whose every copy is missing; the count draws the same line). BY KEY, NOT BY OFFSET:
#: a file read behind an offset would shift the rest and one would never be handed out.
_ASSET_IDS_PAGE = splice(
    """
SELECT a.id, a.added_at FROM assets a
 WHERE a.probed_at IS NOT NULL
   AND {{PRESENT}}
   AND (a.added_at, a.id) > (?, ?)<<IN_ROOTS>>
 ORDER BY a.added_at, a.id
 LIMIT ?
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

_BEFORE_EVERY_FILE = (-(2**63), "")

#: The files lacking a fingerprint, as a `Lack`; a test holds it in step with the per-file list.
LACKS_FINGERPRINT = """(
       (a.media_type = 'image' AND a.phash IS NULL)
    OR (a.media_type = 'gif'   AND (a.phash IS NULL OR a.videohash IS NULL))
    OR (a.media_type NOT IN ('image', 'gif')
        AND (a.phash IS NULL OR a.videohash IS NULL
             OR a.oshash IS NULL OR a.video_phash IS NULL))
   -- OR WHAT IT HAS IS AN OLDER GENERATION'S. NULL here is not "old", it is "none taken yet",
   -- and the lines above already say what a file of each kind is missing; this adds the one case
   -- they cannot see: every hash present, taken by a version of the arithmetic that no longer
   -- agrees with the one in use. Without it the first improvement to any of the four can only be
   -- applied by decoding every file in the library again.
    OR a.fingerprint_version < ?
  )"""

#: `_LACKING_DERIVATIVE`'s test as a `Lack`, held in step by a test counting both ways.
LACKS_DERIVATIVE = (
    "NOT EXISTS (SELECT 1 FROM derivatives d"
    " WHERE d.asset_id = a.id AND d.kind = ? AND d.recipe_version >= ?)"
)

_LACKS_DERIVATIVE_OF = {
    kind: splice(
        "({{MADE_FOR}} AND {{LACKS}})", MADE_FOR=_made_for_sql(kind), LACKS=LACKS_DERIVATIVE
    )
    for kind in DerivativeKind
}


def lacks_fingerprint() -> Lack:
    """The files waiting for a fingerprint, as a term naming its product (so a file given up on
    is not waiting) and binding the generation in use."""
    return Lack(
        LACKS_FINGERPRINT,
        params=(FINGERPRINT_VERSION,),
        product=VerdictProduct.FINGERPRINTS.value,
    )


def lacks_derivative(kinds: Sequence[DerivativeKind]) -> Lack | None:
    """The files with none of these kinds at the recipe in use, as one term; None for no kinds,
    since an always-false term would still be walked."""
    if not kinds:
        return None
    bound: list[Any] = []
    for kind in kinds:
        bound += [kind.value, RECIPE_VERSIONS[kind]]
    return Lack(_any_of([_LACKS_DERIVATIVE_OF[kind] for kind in kinds]), tuple(bound))


def _any_of(conditions: Sequence[str]) -> str:
    return "(" + " OR ".join(conditions) + ")"


#: One pass deciding every term for every read file with a present copy (the line
#: `_ASSET_IDS_PAGE` draws, so count and walk are one set): a count per term, and the union.
_COUNT_LACKING = splice(
    """
SELECT <<EACH>>, COALESCE(SUM(<<ANY>>), 0) AS files
FROM (SELECT <<TERMS>> FROM assets a
      WHERE a.probed_at IS NOT NULL
        AND {{PRESENT}}<<IN_ROOTS>>)
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

_COUNT_LACKING_BY_KIND = splice(
    """
SELECT kind, <<EACH>>, COALESCE(SUM(<<ANY>>), 0) AS files
FROM (SELECT a.media_type AS kind, <<TERMS>> FROM assets a
      WHERE a.probed_at IS NOT NULL
        AND {{PRESENT}}<<IN_ROOTS>><<AMONG>>)
GROUP BY kind
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

_KINDS_OF = """
SELECT id, media_type FROM assets WHERE id IN (SELECT value FROM json_each(?))
"""

#: The same count over what one user can see, concealed rows left out as the wall reads it.
_COUNT_LACKING_VISIBLE = splice(
    """
SELECT <<EACH>>, COALESCE(SUM(<<ANY>>), 0) AS files
FROM (SELECT <<TERMS>> FROM assets a
      JOIN viewer_assets va ON va.asset_id = a.id AND va.user_id = ? AND va.concealed = 0
      WHERE a.probed_at IS NOT NULL
        AND {{PRESENT}}<<IN_ROOTS>>)
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

#: The same over the files the vault or a hide holds back from one user: a range of the partial
#: index on concealed rows, as long as what is concealed.
_COUNT_LACKING_CONCEALED = splice(
    """
SELECT <<EACH>>, COALESCE(SUM(<<ANY>>), 0) AS files
FROM (SELECT <<TERMS>> FROM viewer_assets va
      JOIN assets a ON a.id = va.asset_id
      WHERE va.user_id = ? AND va.concealed = 1
        AND a.probed_at IS NOT NULL
        AND {{PRESENT}}<<IN_ROOTS>>)
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

#: A file the feature said it cannot make this for is not lacking it; a transient verdict (a share
#: away) does not count, as the next scan clears it.
_NOT_VERDICTED = (
    "NOT EXISTS (SELECT 1 FROM file_verdicts v"
    " WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)"
)


def _within_term(condition: str, params: tuple[Any, ...], within: Within | None) -> Term:
    """A kept term over the files `within` wants, as `_within_statement` filters a count."""
    if within is None:
        return Term(condition, params)
    return Term(f"{condition} AND ({within.condition})", (*params, *within.params))


def _within_statement(statement: str, within: Within) -> str:
    """A count filtered to the files `within` wants; only its constant condition goes in as text."""
    joiner = " AND " if " WHERE " in statement else " WHERE "
    return f"{statement}{joiner}({within.condition})"


def _term(one: Lack) -> str:
    """One term's condition: its own, the files that want it, and not given up on."""
    parts = [f"({one.condition})"]
    if one.within is not None:
        parts.append(f"({one.within.condition})")
    if one.product is not None:
        parts.append(_NOT_VERDICTED)
    return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"


#: The files a lacking count is over, as a kept term's head: read, with a copy present.
_READ_AND_PRESENT = splice(
    "a.probed_at IS NOT NULL AND {{PRESENT}}<<IN_ROOTS>>", PRESENT=HAS_A_PRESENT_COPY
)


def _kept_terms(
    lacks: Sequence[Lack], ticked: Sequence[bool], roots: Sequence[str] | None
) -> list[Term]:
    """`_lacking_statement`'s counts as kept terms: one per term, then the union of the ticked
    ones when any is."""
    head, bound = _in_roots(_READ_AND_PRESENT, roots), _roots_bound(roots)
    terms = [Term(f"{head} AND {_term(one)}", (*bound, *_term_params([one]))) for one in lacks]
    chosen = [one for one, on in zip(lacks, ticked, strict=True) if on]
    if len(chosen) == 1:
        terms.append(terms[list(ticked).index(True)])
    elif chosen:
        either = " OR ".join(_term(one) for one in chosen)
        terms.append(Term(f"{head} AND ({either})", (*bound, *_term_params(chosen))))
    return terms


def _lacking_of(totals: Sequence[Totals], count: int, kind: str | None = None) -> Lacking:
    """The kept totals of `_kept_terms` as a `Lacking`, of one kind or of all."""

    def total(one: Totals) -> int:
        return sum(one.values()) if kind is None else one.get(kind, 0)

    files = total(totals[count]) if len(totals) > count else 0
    return Lacking(each=tuple(total(one) for one in totals[:count]), files=files)


def _lacking_statement(
    lacks: Sequence[Lack],
    ticked: Sequence[bool],
    *,
    statement: str = _COUNT_LACKING,
    roots: Sequence[str] | None = None,
    among: Sequence[str] | None = None,
) -> str:
    """`_COUNT_LACKING` (or a variant) with these terms, numbered by position. Only each term's
    constant condition goes in as text; every value binds, `roots`' after every term's.
    """

    terms = ", ".join(f"{_term(one)} AS t{n}" for n, one in enumerate(lacks))
    each = ", ".join(f"COALESCE(SUM(t{n}), 0) AS n{n}" for n in range(len(lacks)))
    any_ticked = " OR ".join(f"t{n}" for n, on in enumerate(ticked) if on) or "0"
    return (
        _in_roots(statement, roots)
        .replace("<<AMONG>>", "" if among is None else _AMONG)
        .replace("<<TERMS>>", terms)
        .replace("<<EACH>>", each)
        .replace("<<ANY>>", any_ticked)
    )


class Counts(StoreCore):
    """How much each pass has left, and the pages it reads."""

    @property
    def _kept(self) -> StoredCounts:
        """The counts kept as rows (`backlog`), made on first use."""
        kept: StoredCounts | None = getattr(self, "_kept_counts", None)
        if kept is None:
            kept = self._kept_counts = StoredCounts(self._db, self._clock)
        return kept

    async def _held(self, key: tuple[object, ...], count: Callable[[], Awaitable[_T]]) -> _T:
        """The count `key` names, taken again only once the library has moved since it was taken:
        a screen opened on a library at rest reads the answer, not every file again."""
        mark = mark_of(MOVED_BY)
        if mark is None:
            return await count()
        held = self._held_counts.get(key)
        if held is not None and held[0] == mark and monotonic() - held[1] < HELD_AT_MOST_SECONDS:
            return cast(_T, held[2])
        taken_at = monotonic()
        value = await count()
        # Moved while counting: the answer may already be old, so it is not held.
        if mark_of(MOVED_BY) == mark:
            if len(self._held_counts) >= _HELD_KEPT:
                self._held_counts.clear()
            self._held_counts[key] = (mark, taken_at, value)
        return value

    async def asset_count(self, within: Within | None = None) -> int:
        """How many files the library holds (of those `within` wants): the denominator for a
        feature that may not query the asset table itself."""
        statement = _COUNT_PLACED if within is None else _COUNT_ASSETS
        term = _within_term(_HAS_A_PLACE, (), within)

        async def count() -> int:
            kept = await self._kept.totals([term])
            if kept is not None:
                return sum(kept[0].values())
            return await self._count_within(statement, within)

        return await self._held(("asset_count", within), count)

    async def wanting_count(self, product: str, within: Within | None = None) -> int:
        """How many files want this product, read or coming, done or not: its bar's whole."""
        statement = _COUNT_WANTING_OF.get(product, _COUNT_WANTING_OF[None])
        params = (VerdictProduct.PROBE.value, product)
        term = _within_term(_WANTING_OF.get(product, _WANTING_OF[None]), params, within)

        async def count() -> int:
            kept = await self._kept.totals([term])
            if kept is not None:
                return sum(kept[0].values())
            return await self._count_bound(statement, params, within)

        return await self._held(("wanting", product, within), count)

    async def coming_count(self, product: str, within: Within | None = None) -> int:
        """How many unread files want this product: what `count_lacking` cannot see."""
        statement = _COUNT_COMING_OF.get(product, _COUNT_COMING_OF[None])
        params = (VerdictProduct.PROBE.value, product)
        return await self._held(
            ("coming", product, within), lambda: self._count_bound(statement, params, within)
        )

    async def _count_bound(
        self, statement: str, params: tuple[Any, ...], within: Within | None
    ) -> int:
        """A count with values of its own, filtered to the files `within` wants."""
        if within is not None:
            statement, params = _within_statement(statement, within), (*params, *within.params)
        (row,) = await self._db.fetch_all(statement, params)
        return int(row["total"])

    async def _count_within(self, statement: str, within: Within | None) -> int:
        """A count filtered to the files `within` wants, as the lacking count is."""
        if within is None:
            (row,) = await self._db.fetch_all(statement, ())
        else:
            (row,) = await self._db.fetch_all(_within_statement(statement, within), within.params)
        return int(row["total"])

    async def naming_facts(self, asset_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        """What a naming template can say about each of these files, by id; unknown ids absent."""
        rows: dict[str, dict[str, object]] = {}
        ids = list(asset_ids)
        for start in range(0, len(ids), _NAMING_FACTS_AT_ONCE):
            sql, params = in_clause(_NAMING_FACTS, ids[start : start + _NAMING_FACTS_AT_ONCE])
            for row in await self._db.fetch_all(sql, params):
                rows[str(row["id"])] = dict(row)
        return rows

    async def asset_ids_page(
        self, *, after: PageKey | None = None, limit: int, roots: Sequence[str] | None = None
    ) -> LibraryPage:
        """A page of read files, oldest first, after the page before's `last`, over the folders
        `roots` names (None: the whole library)."""
        added_at, asset_id = after if after is not None else _BEFORE_EVERY_FILE
        rows = await self._db.fetch_all(
            _in_roots(_ASSET_IDS_PAGE, roots), (added_at, asset_id, *_roots_bound(roots), limit)
        )
        ids = [str(row["id"]) for row in rows]
        last = (int(rows[-1]["added_at"]), ids[-1]) if rows else after
        return LibraryPage(ids=ids, last=last)

    async def unread_page(self, *, offset: int, limit: int) -> list[str]:
        """A page of the files never read that have a copy there to read, oldest first."""
        rows = await self._db.fetch_all(_UNREAD_PAGE, (VerdictProduct.PROBE.value, limit, offset))
        return [row["id"] for row in rows]

    async def count_lacking(
        self,
        lacks: Sequence[Lack],
        *,
        ticked: Sequence[bool] | None = None,
        roots: Sequence[str] | None = None,
    ) -> Lacking:
        """How many read files each term is true of, and the union over the ticked terms (all,
        when not given), in ONE statement whatever the number of terms; `roots` as for the walk."""
        if not lacks:
            return Lacking(each=(), files=0)
        flags = [True] * len(lacks) if ticked is None else list(ticked)
        if len(flags) != len(lacks):
            raise ValueError("count_lacking needs one tick per term")
        params = [*_term_params(lacks), *_roots_bound(roots)]
        statement = _lacking_statement(lacks, flags, roots=roots)
        terms = _kept_terms(lacks, flags, roots)

        async def count() -> Lacking:
            kept = await self._kept.totals(terms)
            if kept is not None:
                return _lacking_of(kept, len(lacks))
            (row,) = await self._db.fetch_all(statement, params)
            return Lacking(
                each=tuple(int(row[f"n{n}"]) for n in range(len(lacks))),
                files=int(row["files"]),
            )

        return await self._held((statement, *params), count)

    async def count_lacking_by_kind(
        self,
        lacks: Sequence[Lack],
        *,
        ticked: Sequence[bool] | None = None,
        roots: Sequence[str] | None = None,
        among: Sequence[str] | None = None,
    ) -> dict[str, Lacking]:
        """`count_lacking` split by media kind, for an estimate that prices each kind apart;
        `among` counts only those files."""
        if not lacks:
            return {}
        flags = [True] * len(lacks) if ticked is None else list(ticked)
        if len(flags) != len(lacks):
            raise ValueError("count_lacking needs one tick per term")
        statement = _lacking_statement(
            lacks, flags, statement=_COUNT_LACKING_BY_KIND, roots=roots, among=among
        )
        params = [*_term_params(lacks), *_roots_bound(roots), *_roots_bound(among)]
        terms = _kept_terms(lacks, flags, roots)

        async def count() -> dict[str, Lacking]:
            # A few chosen files are read as they are; the library's split is kept.
            kept = None if among is not None else await self._kept.totals(terms)
            if kept is not None:
                kinds = sorted({kind for one in kept for kind in one})
                return {kind: _lacking_of(kept, len(lacks), kind) for kind in kinds}
            rows = await self._db.fetch_all(statement, params)
            return {
                str(row["kind"]): Lacking(
                    each=tuple(int(row[f"n{n}"]) for n in range(len(lacks))),
                    files=int(row["files"]),
                )
                for row in rows
            }

        return dict(await self._held((statement, *params), count))

    async def kinds_of(self, asset_ids: Sequence[str]) -> dict[str, str]:
        """The media kind of each of these files, by id. An id not in the library is absent."""
        if not asset_ids:
            return {}
        rows = await self._db.fetch_all(_KINDS_OF, (json.dumps(sorted(set(asset_ids))),))
        return {str(row["id"]): str(row["media_type"]) for row in rows}

    async def count_lacking_visible(
        self, user_id: str, lacks: Sequence[Lack], *, admin: bool = False
    ) -> Lacking:
        """`count_lacking` over the files one user can see, the stored visibility joined in.

        An admin may see every file with a place (`visibility._VERDICT_ROWS`), so theirs is the
        library's kept count less what is concealed from them, never a walk of the library."""
        if not lacks:
            return Lacking(each=(), files=0)
        params = _term_params(lacks)
        params.append(user_id)
        flags = [True] * len(lacks)
        if admin:
            every = await self.count_lacking(lacks)
            statement = _lacking_statement(lacks, flags, statement=_COUNT_LACKING_CONCEALED)
            (held,) = await self._db.fetch_all(statement, params)
            return Lacking(
                each=tuple(n - int(held[f"n{i}"]) for i, n in enumerate(every.each)),
                files=every.files - int(held["files"]),
            )
        statement = _lacking_statement(lacks, flags, statement=_COUNT_LACKING_VISIBLE)
        (row,) = await self._db.fetch_all(statement, params)
        return Lacking(
            each=tuple(int(row[f"n{n}"]) for n in range(len(lacks))),
            files=int(row["files"]),
        )

    async def unread_count(self) -> int:
        """How many files have never been read: a stalled read leaves them looking imported, and
        this number is the only way to notice. A scan re-reads exactly these."""

        async def count() -> int:
            (row,) = await self._db.fetch_all(_COUNT_UNREAD, (VerdictProduct.PROBE.value,))
            return int(row["total"])

        return await self._held(("unread",), count)

    async def any_unread(self) -> bool:
        """Whether `unread_count` is above nought, without counting."""
        (row,) = await self._db.fetch_all(_ANY_UNREAD, (VerdictProduct.PROBE.value,))
        return bool(row["found"])
