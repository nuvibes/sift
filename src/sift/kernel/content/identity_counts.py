# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many files each pass still has to do, and the pages it reads them from."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

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
from sift.kernel.sql_splice import splice

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

_COUNT_ASSETS = "SELECT COUNT(*) AS total FROM assets a"

_COUNT_WITH_AUDIO = "SELECT COUNT(*) AS total FROM assets a WHERE a.acodec IS NOT NULL"

#: `_MADE_FOR` before a file is read: its kind is known, its running time is not, so an unread
#: video counts as wanting a strip.
_MADE_FOR_UNREAD: Mapping[DerivativeKind, str] = {
    DerivativeKind.PREVIEW: "(a.media_type <> 'image')",
    DerivativeKind.SPRITE: "(a.media_type <> 'image')",
}

#: Not yet read and not given up on: it is coming. Binds the read's verdict product.
_COMING = """(a.probed_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM file_verdicts pv
                       WHERE pv.asset_id = a.id AND pv.product = ? AND pv.transient = 0))"""

# Both ends of a product's bar on Activity range over ONE set, the set `_COUNT_LACKING` reads:
# files with a present copy, not given up on, that the work is made for (read ones by `_MADE_FOR`,
# unread ones by `_MADE_FOR_UNREAD`, and those lack it). Binds the read's verdict product, then
# this product's.
_COUNT_WANTING = """
SELECT COUNT(*) AS total FROM assets a
 WHERE {{PRESENT}}
   AND ((a.probed_at IS NOT NULL AND {{MADE_FOR}}) OR ({{COMING}} AND {{MADE_FOR_UNREAD}}))
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""

_COUNT_COMING = """
SELECT COUNT(*) AS total FROM assets a
 WHERE {{PRESENT}}
   AND {{COMING}} AND {{MADE_FOR_UNREAD}}
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""


def _bar_statement(template: str, kind: DerivativeKind | None) -> str:
    """One end of a bar for this kind, or for work every file wants (None: the fingerprints)."""
    made_for = _EVERY_READ_FILE if kind is None else _made_for_sql(kind)
    unread = _EVERY_READ_FILE if kind is None else _MADE_FOR_UNREAD.get(kind, _EVERY_READ_FILE)
    fragments = {"PRESENT": HAS_A_PRESENT_COPY, "COMING": _COMING, "MADE_FOR_UNREAD": unread}
    if "{{MADE_FOR}}" in template:
        fragments["MADE_FOR"] = made_for
    return splice(template, **fragments)


_COUNT_WANTING_OF = {kind: _bar_statement(_COUNT_WANTING, kind) for kind in (*DerivativeKind, None)}

_COUNT_COMING_OF = {kind: _bar_statement(_COUNT_COMING, kind) for kind in (*DerivativeKind, None)}

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
        AND {{PRESENT}}<<IN_ROOTS>>)
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

#: A file the feature said it cannot make this for is not lacking it; a transient verdict (a share
#: away) does not count, as the next scan clears it.
_NOT_VERDICTED = (
    "NOT EXISTS (SELECT 1 FROM file_verdicts v"
    " WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)"
)


def _within_statement(statement: str, within: Within) -> str:
    """A count filtered to the files `within` wants; only its constant condition goes in as text."""
    joiner = " AND " if " WHERE " in statement else " WHERE "
    return f"{statement}{joiner}({within.condition})"


def _lacking_statement(
    lacks: Sequence[Lack],
    ticked: Sequence[bool],
    *,
    statement: str = _COUNT_LACKING,
    roots: Sequence[str] | None = None,
) -> str:
    """`_COUNT_LACKING` (or a variant) with these terms, numbered by position. Only each term's
    constant condition goes in as text; every value binds, `roots`' after every term's.
    """

    def term(one: Lack) -> str:
        parts = [f"({one.condition})"]
        if one.within is not None:
            parts.append(f"({one.within.condition})")
        if one.product is not None:
            parts.append(_NOT_VERDICTED)
        return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"

    terms = ", ".join(f"{term(one)} AS t{n}" for n, one in enumerate(lacks))
    each = ", ".join(f"COALESCE(SUM(t{n}), 0) AS n{n}" for n in range(len(lacks)))
    any_ticked = " OR ".join(f"t{n}" for n, on in enumerate(ticked) if on) or "0"
    return (
        _in_roots(statement, roots)
        .replace("<<TERMS>>", terms)
        .replace("<<EACH>>", each)
        .replace("<<ANY>>", any_ticked)
    )


class Counts(StoreCore):
    """How much each pass has left, and the pages it reads."""

    async def asset_count(self, within: Within | None = None) -> int:
        """How many files the library holds (of those `within` wants): the denominator for a
        feature that may not query the asset table itself."""
        return await self._count_within(_COUNT_ASSETS, within)

    async def with_audio_count(self, within: Within | None = None) -> int:
        """How many files carry a sound track: the music fingerprint's denominator."""
        return await self._count_within(_COUNT_WITH_AUDIO, within)

    async def wanting_count(
        self, kind: DerivativeKind | None, verdict: str, within: Within | None = None
    ) -> int:
        """How many files this work is for, done or not: its bar's denominator (`_COUNT_WANTING`).
        `kind` None is work every file wants."""
        return await self._count_bound(
            _COUNT_WANTING_OF[kind], (VerdictProduct.PROBE.value, verdict), within
        )

    async def coming_count(
        self, kind: DerivativeKind | None, verdict: str, within: Within | None = None
    ) -> int:
        """How many unread files will want this work: lacking it, outside the lacking count."""
        return await self._count_bound(
            _COUNT_COMING_OF[kind], (VerdictProduct.PROBE.value, verdict), within
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
        (row,) = await self._db.fetch_all(_lacking_statement(lacks, flags, roots=roots), params)
        return Lacking(
            each=tuple(int(row[f"n{n}"]) for n in range(len(lacks))),
            files=int(row["files"]),
        )

    async def count_lacking_by_kind(
        self,
        lacks: Sequence[Lack],
        *,
        ticked: Sequence[bool] | None = None,
        roots: Sequence[str] | None = None,
    ) -> dict[str, Lacking]:
        """`count_lacking` split by media kind, for an estimate that prices each kind apart."""
        if not lacks:
            return {}
        flags = [True] * len(lacks) if ticked is None else list(ticked)
        if len(flags) != len(lacks):
            raise ValueError("count_lacking needs one tick per term")
        statement = _lacking_statement(lacks, flags, statement=_COUNT_LACKING_BY_KIND, roots=roots)
        params = [*_term_params(lacks), *_roots_bound(roots)]
        rows = await self._db.fetch_all(statement, params)
        return {
            str(row["kind"]): Lacking(
                each=tuple(int(row[f"n{n}"]) for n in range(len(lacks))),
                files=int(row["files"]),
            )
            for row in rows
        }

    async def kinds_of(self, asset_ids: Sequence[str]) -> dict[str, str]:
        """The media kind of each of these files, by id. An id not in the library is absent."""
        if not asset_ids:
            return {}
        rows = await self._db.fetch_all(_KINDS_OF, (json.dumps(sorted(set(asset_ids))),))
        return {str(row["id"]): str(row["media_type"]) for row in rows}

    async def count_lacking_visible(self, user_id: str, lacks: Sequence[Lack]) -> Lacking:
        """`count_lacking` over the files one user can see, the stored visibility joined in."""
        if not lacks:
            return Lacking(each=(), files=0)
        params = _term_params(lacks)
        params.append(user_id)
        statement = _lacking_statement(lacks, [True] * len(lacks), statement=_COUNT_LACKING_VISIBLE)
        (row,) = await self._db.fetch_all(statement, params)
        return Lacking(
            each=tuple(int(row[f"n{n}"]) for n in range(len(lacks))),
            files=int(row["files"]),
        )

    async def unread_count(self) -> int:
        """How many files have never been read: a stalled read leaves them looking imported, and
        this number is the only way to notice. A scan re-reads exactly these."""
        (row,) = await self._db.fetch_all(_COUNT_UNREAD, (VerdictProduct.PROBE.value,))
        return int(row["total"])
