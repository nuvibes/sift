# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing this feature's tables; no file is opened here."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sift.kernel import chromaprint
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, telling, who_may_see_a_file
from sift.kernel.content import Lack
from sift.kernel.content.identity import songs_of
from sift.kernel.content.identity_fields import songs_and_lengths
from sift.kernel.db import Connection, Database
from sift.kernel.log import get_logger
from sift.kernel.sql_splice import splice
from sift.slices.music.matching import CANDIDATE_KEYS, CANDIDATE_LIMIT, KEY_SCHEME, Match
from sift.slices.music.schema import WAITING_KIND, WAITING_SCOPE

log = get_logger(__name__)

#: Waiting in `music_waiting`, or fingerprinted but not paired under the scheme in force.
LACKS_AUDIO_FINGERPRINT = (
    "(EXISTS (SELECT 1 FROM music_waiting w WHERE w.asset_id = a.id)"
    " OR EXISTS (SELECT 1 FROM audio_fingerprints f WHERE f.asset_id = a.id"
    " AND (f.indexed_scheme IS NOT ? OR f.algorithm IS NOT ?)))"
)

#: With the vault shut; no row is zero.
_WAITING_FOR = (
    "SELECT permitted - concealed AS files FROM viewer_entity_counts"
    " WHERE user_id = ? AND kind = ? AND object_id = ?"
)

_ANY_WAITING = "SELECT 1 AS ok FROM music_waiting LIMIT 1"

#: The count's own term asked of a page of ids, so the two cannot describe different sets.
_LACKING_AMONG = splice(
    "SELECT a.id FROM (SELECT value AS id FROM json_each(?)) a WHERE {{LACKS}}",
    LACKS=LACKS_AUDIO_FINGERPRINT,
)

_KEEP = """
INSERT INTO audio_fingerprints
  (asset_id, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id) DO UPDATE SET algorithm = excluded.algorithm,
  tool = excluded.tool,
  duration_ms = excluded.duration_ms,
  offset_ms = excluded.offset_ms,
  fingerprint = excluded.fingerprint,
  computed_at = excluded.computed_at,
  indexed_scheme = NULL
"""

_KEEP_PENDING = """
INSERT INTO audio_fingerprints_pending
  (identity, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(identity) DO UPDATE SET algorithm = excluded.algorithm,
  tool = excluded.tool,
  duration_ms = excluded.duration_ms,
  offset_ms = excluded.offset_ms,
  fingerprint = excluded.fingerprint,
  computed_at = excluded.computed_at
"""

# One transaction, so a crash cannot leave both rows; a stale-algorithm row is not copied.
_CLAIM = """
INSERT INTO audio_fingerprints
  (asset_id, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)
SELECT ?, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at
  FROM audio_fingerprints_pending WHERE identity = ? AND algorithm = ?
ON CONFLICT(asset_id) DO NOTHING
"""

_DROP_CLAIMED = "DELETE FROM audio_fingerprints_pending WHERE identity = ?"

# Past the cutoff, or under a replaced algorithm the claim refuses.
_DROP_STALE = "DELETE FROM audio_fingerprints_pending WHERE computed_at < ? OR algorithm IS NOT ?"

#: An older algorithm is still lacking, or the Build would hand the file out every run.
_HAS_ROW = "SELECT 1 AS ok FROM audio_fingerprints WHERE asset_id = ? AND algorithm = ?"

# Lists of values travel as one bound JSON array read with `json_each`.

#: Whether this file's pairing is done under the scheme in force.
_INDEXED = "SELECT 1 AS ok FROM audio_fingerprints WHERE asset_id = ? AND indexed_scheme = ?"

_DROP_KEYS = "DELETE FROM audio_fingerprint_keys WHERE asset_id = ?"

_ADD_KEYS = "INSERT INTO audio_fingerprint_keys (key, asset_id) SELECT value, ? FROM json_each(?)"

#: The length-relative floor is `matching.is_candidate`; grouped first to read each row once.
_CANDIDATES = """
SELECT s.asset_id, s.shared, f.duration_ms
  FROM (SELECT k.asset_id, COUNT(*) AS shared
          FROM audio_fingerprint_keys k
         WHERE k.key IN (SELECT value FROM json_each(?)) AND k.asset_id <> ?
         GROUP BY k.asset_id
        HAVING COUNT(*) >= ?) s
  JOIN audio_fingerprints f ON f.asset_id = s.asset_id
 ORDER BY s.shared DESC, s.asset_id
 LIMIT ?
"""

_FINGERPRINT_OF = """
SELECT algorithm, tool, duration_ms, offset_ms, fingerprint
  FROM audio_fingerprints WHERE asset_id = ?
"""

# Two statements so each side uses its own index.
_DROP_PAIRS_A = "DELETE FROM music_pairs WHERE a_id = ?"
_DROP_PAIRS_B = "DELETE FROM music_pairs WHERE b_id = ?"

_ADD_PAIR = """
INSERT INTO music_pairs (a_id, b_id, ber, offset_s, windows, matching, computed_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(a_id, b_id) DO UPDATE SET ber = excluded.ber,
  offset_s = excluded.offset_s,
  windows = excluded.windows,
  matching = excluded.matching,
  computed_at = excluded.computed_at
"""

_STAMP_INDEXED = "UPDATE audio_fingerprints SET indexed_scheme = ? WHERE asset_id = ?"

_PAIRS_OF = """
SELECT a_id, b_id, ber, offset_s, windows, matching, computed_at FROM music_pairs WHERE a_id = ?
UNION ALL
SELECT a_id, b_id, ber, offset_s, windows, matching, computed_at FROM music_pairs WHERE b_id = ?
 ORDER BY ber, a_id, b_id
"""

#: Past this many, the closest are the ones worth drawing.
GROUP_LIMIT = 500

#: One hop, closest first, a chain scoring its weakest link; a hidden file never bridges a hop.
_GROUP = """
WITH direct(id, ber) AS (
  SELECT p.b_id, p.ber FROM music_pairs p WHERE p.a_id = :asset
  UNION ALL
  SELECT p.a_id, p.ber FROM music_pairs p WHERE p.b_id = :asset
),
bridge(id, ber) AS (
  SELECT d.id, d.ber FROM direct d
   WHERE :user IS NULL OR EXISTS (SELECT 1 FROM viewer_assets va
                                   WHERE va.user_id = :user AND va.asset_id = d.id
                                     AND va.concealed = 0)
),
reached(id, ber) AS (
  SELECT id, ber FROM direct
  UNION ALL
  SELECT p.b_id, MAX(b.ber, p.ber) FROM bridge b JOIN music_pairs p ON p.a_id = b.id
  UNION ALL
  SELECT p.a_id, MAX(b.ber, p.ber) FROM bridge b JOIN music_pairs p ON p.b_id = b.id
)
SELECT r.id AS asset_id, MIN(r.ber) AS ber
  FROM reached r
 WHERE r.id <> :asset
   AND (:user IS NULL OR EXISTS (SELECT 1 FROM viewer_assets va
                                  WHERE va.user_id = :user AND va.asset_id = r.id
                                    AND (va.concealed = 0 OR :reveal = 1)))
 GROUP BY r.id
 ORDER BY MIN(r.ber), r.id
 LIMIT :limit
"""


@dataclass(frozen=True, slots=True)
class Kept:
    """One fingerprint as it is stored."""

    algorithm: int
    tool: str
    duration_ms: int
    offset_ms: int
    fingerprint: bytes
    computed_at: int


@dataclass(frozen=True, slots=True)
class Pair:
    """Two files verified to share a song, smaller id first; build with `of` to keep the sign."""

    a_id: str
    b_id: str
    ber: float
    offset_s: float
    windows: int
    matching: int
    computed_at: int

    @classmethod
    def of(cls, first: str, second: str, match: Match, computed_at: int) -> Pair:
        """A pair from a comparison read from `first` to `second`, in the table's order."""
        if first == second:
            raise ValueError("a file is not paired with itself")
        forward = first < second
        return cls(
            a_id=first if forward else second,
            b_id=second if forward else first,
            ber=match.ber,
            offset_s=match.offset_s if forward else -match.offset_s or 0.0,
            windows=match.windows,
            matching=match.matching,
            computed_at=computed_at,
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    """A file sharing keys with the one being paired, and how much sound its fingerprint covers."""

    asset_id: str
    shared: int
    duration_ms: int


class MusicStore:
    """This feature's tables, and nothing else."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def lack(self) -> Lack:
        """Files with audio and no fingerprint, as a Generate term; an empty row is an answer."""
        return Lack(LACKS_AUDIO_FINGERPRINT, params=(KEY_SCHEME, chromaprint.ALGORITHM))

    async def waiting_for(self, user_id: str) -> int:
        """How many files this user can see still want a fingerprint, from the per-user count."""
        row = await self._db.fetch_one(_WAITING_FOR, (user_id, WAITING_KIND, WAITING_SCOPE))
        return 0 if row is None else max(0, int(row["files"]))

    async def any_waiting(self) -> bool:
        """Whether any file still wants a fingerprint, which decides whether the card is drawn."""
        return await self._db.fetch_one(_ANY_WAITING) is not None

    async def lacking_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files still owe a fingerprint or its pairing, by the count's own term."""
        if not asset_ids:
            return set()
        # Bound from the term itself, so a parameter cannot fall short.
        params = (json.dumps(list(asset_ids)), *self.lack().params)
        rows = await self._db.fetch_all(_LACKING_AMONG, params)
        return {str(row["id"]) for row in rows}

    async def has(self, asset_id: str) -> bool:
        """Whether this file has been answered for already, under the algorithm in force."""
        found = await self._db.fetch_one(_HAS_ROW, (asset_id, chromaprint.ALGORITHM))
        return found is not None

    async def keep(self, asset_id: str, kept: Kept) -> None:
        """Write one file's fingerprint, replacing any, and clear its stamp so it pairs again."""
        await self._db.execute(
            _KEEP,
            (
                asset_id,
                kept.algorithm,
                kept.tool,
                kept.duration_ms,
                kept.offset_ms,
                kept.fingerprint,
                kept.computed_at,
            ),
        )

    async def keep_pending(self, identity: str, kept: Kept) -> None:
        """Write a fingerprint taken at staging, under the identity of the bytes it came from."""
        await self._db.execute(
            _KEEP_PENDING,
            (
                identity,
                kept.algorithm,
                kept.tool,
                kept.duration_ms,
                kept.offset_ms,
                kept.fingerprint,
                kept.computed_at,
            ),
        )

    async def claim(self, asset_id: str, identity: str) -> bool:
        """Move a staged fingerprint onto this file; False when none waits under this algorithm."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_CLAIM, (asset_id, identity, chromaprint.ALGORITHM))
            claimed = int(cursor.rowcount) > 0
            await connection.execute(_DROP_CLAIMED, (identity,))
        return claimed

    async def forget_pending_before(self, cutoff: int) -> int:
        """Drop staged fingerprints past `cutoff` or under an old algorithm; returns how many."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_DROP_STALE, (cutoff, chromaprint.ALGORITHM))
            gone = int(cursor.rowcount)
        if gone:
            log.info("music.pending_swept", rows=gone)
        return gone

    async def indexed(self, asset_id: str) -> bool:
        """Whether this file's fingerprint has been paired under the scheme in force."""
        return await self._db.fetch_one(_INDEXED, (asset_id, KEY_SCHEME)) is not None

    async def index_keys(self, asset_id: str, keys: Iterable[int]) -> None:
        """Replace this file's keys before it searches, so two arriving together find each other."""
        async with self._db.write() as connection:
            await connection.execute(_DROP_KEYS, (asset_id,))
            await connection.execute(_ADD_KEYS, (asset_id, json.dumps(sorted(keys))))

    async def candidates(
        self, asset_id: str, keys: Iterable[int], *, limit: int = CANDIDATE_LIMIT
    ) -> list[Candidate]:
        """Files sharing at least `CANDIDATE_KEYS` of these keys, most first, at most `limit`."""
        wanted = sorted(keys)
        if not wanted:
            return []
        rows = await self._db.fetch_all(
            _CANDIDATES, (json.dumps(wanted), asset_id, CANDIDATE_KEYS, limit)
        )
        return [
            Candidate(
                asset_id=str(row["asset_id"]),
                shared=int(row["shared"]),
                duration_ms=int(row["duration_ms"]),
            )
            for row in rows
        ]

    async def fingerprint_of(self, asset_id: str) -> chromaprint.Fingerprint | None:
        """The fingerprint kept for this file, as values. None where there is no row."""
        row = await self._db.fetch_one(_FINGERPRINT_OF, (asset_id,))
        if row is None:
            return None
        return chromaprint.Fingerprint(
            algorithm=int(row["algorithm"]),
            tool=str(row["tool"]),
            duration_ms=int(row["duration_ms"]),
            offset_ms=int(row["offset_ms"]),
            values=chromaprint.parse(bytes(row["fingerprint"])),
        )

    async def write_pairs(self, asset_id: str, pairs: Sequence[Pair]) -> None:
        """Replace every pair this file is in, in one transaction, rung on `About.SAME_MUSIC`."""
        for one in pairs:
            if asset_id not in (one.a_id, one.b_id):
                raise ValueError("a file's pairs are the pairs it is in")
        async with self._db.write() as connection:
            before = connection.total_changes
            await connection.execute(_DROP_PAIRS_A, (asset_id,))
            await connection.execute(_DROP_PAIRS_B, (asset_id,))
            for one in pairs:
                await connection.execute(
                    _ADD_PAIR,
                    (
                        one.a_id,
                        one.b_id,
                        one.ber,
                        one.offset_s,
                        one.windows,
                        one.matching,
                        one.computed_at,
                    ),
                )
            if connection.total_changes != before:
                announce(await who_may_see_a_file(connection), About.SAME_MUSIC)

    async def settle(self, asset_id: str) -> None:
        """Mark this file's pairing done; the last write of the run."""
        await self._db.execute(_STAMP_INDEXED, (KEY_SCHEME, asset_id))

    async def pairs_of(self, asset_id: str) -> list[Pair]:
        """Every pair this file is in, closest first."""
        rows = await self._db.fetch_all(_PAIRS_OF, (asset_id, asset_id))
        return [
            Pair(
                a_id=str(row["a_id"]),
                b_id=str(row["b_id"]),
                ber=float(row["ber"]),
                offset_s=float(row["offset_s"]),
                windows=int(row["windows"]),
                matching=int(row["matching"]),
                computed_at=int(row["computed_at"]),
            )
            for row in rows
        ]

    async def music_group(self, asset_id: str) -> list[str]:
        """The file's group as Sift's own writes see it, unscoped; never an answer to a person."""
        return await self._group(asset_id, user_id=None, reveal=False)

    async def same_music_of(
        self, user_id: str, asset_id: str, *, reveal: bool = False
    ) -> list[str]:
        """The files sharing a song with this one that this user may see, one hop, closest first."""
        return await self._group(asset_id, user_id=user_id, reveal=reveal)

    async def _group(self, asset_id: str, *, user_id: str | None, reveal: bool) -> list[str]:
        rows = await self._db.fetch_all(
            _GROUP,
            {"asset": asset_id, "user": user_id, "reveal": int(reveal), "limit": GROUP_LIMIT},
        )
        return [str(row["asset_id"]) for row in rows]


# Song names: the refusals, AcoustID's answers and its key; the song itself is the catalog's.

#: Read inside the writer's transaction, so an Undo cannot be overwritten.
_REFUSED = "SELECT 1 AS refused FROM music_name_refusals WHERE asset_id = ? AND song = ?"

_REFUSE = "INSERT OR IGNORE INTO music_name_refusals (asset_id, song, refused_at) VALUES (?, ?, ?)"

#: A song somebody put on a file by hand: any "no" to that song on that file is taken back.
_UNREFUSE = "DELETE FROM music_name_refusals WHERE asset_id = ? AND song = ?"

#: Owed a lookup: a long enough fingerprint, no song, no refusal and no kept answer.
_STILL_OWED = """
LENGTH(f.fingerprint) > 0
   AND NOT EXISTS (SELECT 1 FROM music_name_refusals r WHERE r.asset_id = f.asset_id)
   AND NOT EXISTS (SELECT 1 FROM music_lookups l WHERE l.asset_id = f.asset_id
                    AND l.status IN ('named', 'nothing'))
"""

#: Length is decided beside it (`NameStore._wanting`).
_PAGE_OF_FILES = """
SELECT f.asset_id AS asset_id, f.duration_ms AS duration_ms
  FROM audio_fingerprints f
 WHERE f.asset_id IN (SELECT value FROM json_each(:assets))
   AND {{RULE}}
"""

_WANTS_LOOKUP = splice(_PAGE_OF_FILES, RULE=_STILL_OWED)

#: The same of the library, a page at a time in id order, for the catch-up.
_OWED_LOOKUPS = splice(
    """
SELECT f.asset_id
  FROM audio_fingerprints f
 WHERE f.asset_id > :after
   AND f.duration_ms >= :shortest_ms
   AND {{OWED}}
 ORDER BY f.asset_id
 LIMIT :limit
""",
    OWED=_STILL_OWED,
)

#: Ask again: last answer "not known" before `:before`; the old answer stays until replaced.
_ASKED_AND_NOT_KNOWN = """
LENGTH(f.fingerprint) > 0
   AND NOT EXISTS (SELECT 1 FROM music_name_refusals r WHERE r.asset_id = f.asset_id)
   AND EXISTS (SELECT 1 FROM music_lookups l WHERE l.asset_id = f.asset_id
                AND l.status = 'nothing' AND l.looked_up_at < :before)
"""

_WANTS_AGAIN = splice(_PAGE_OF_FILES, RULE=_ASKED_AND_NOT_KNOWN)

_NOT_KNOWN_PAGE = splice(
    """
SELECT f.asset_id
  FROM audio_fingerprints f
 WHERE f.asset_id > :after
   AND f.duration_ms >= :shortest_ms
   AND {{AGAIN}}
 ORDER BY f.asset_id
 LIMIT :limit
""",
    AGAIN=_ASKED_AND_NOT_KNOWN,
)

#: One lookup's outcome, replacing an earlier refusal or failure for the same file.
_KEEP_LOOKUP = """
INSERT INTO music_lookups
  (asset_id, looked_up_at, lengths_sent, status, recording_id, title, artists, score)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id) DO UPDATE SET looked_up_at = excluded.looked_up_at,
  lengths_sent = excluded.lengths_sent, status = excluded.status,
  recording_id = excluded.recording_id, title = excluded.title, artists = excluded.artists,
  score = excluded.score
"""

_LOOKUP_OF = (
    "SELECT status, lengths_sent, recording_id, title, artists, score FROM music_lookups"
    " WHERE asset_id = ?"
)

#: Later than any answer.
_ANY_AGE = 2**62

#: One row or none.
_KEY_ID = "SELECT secret_id FROM music_lookup_key WHERE id = 1"
_SET_KEY_ID = """
INSERT INTO music_lookup_key (id, secret_id, set_at) VALUES (1, ?, ?)
ON CONFLICT(id) DO UPDATE SET secret_id = excluded.secret_id, set_at = excluded.set_at
"""
_DROP_KEY_ID = "DELETE FROM music_lookup_key WHERE id = 1"


@dataclass(frozen=True, slots=True)
class LookupKept:
    """What one lookup sent and what came of it. See `music_lookups`."""

    status: str
    lengths_sent: tuple[int, ...]
    recording_id: str | None = None
    title: str | None = None
    artists: str | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class OwedPage:
    """One page of the files a lookup is owed for (`NameStore.owed_lookups`)."""

    files: tuple[str, ...]
    #: The last id read, where the next page starts; None at the end.
    last: str | None


class NameStore:
    """The names Sift wrote, the names taken back, and what AcoustID was asked. No file is opened."""

    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def database(self) -> Database:
        return self._db

    async def songs_of(self, asset_ids: Sequence[str]) -> dict[str, str]:
        """The song each of these files carries, for those that carry one."""
        return await songs_of(self._db, asset_ids)

    @staticmethod
    async def refused_on(connection: Connection, asset_id: str, song: str) -> bool:
        """Whether somebody took this song off this file, read under the caller's transaction."""
        rows = list(await connection.execute_fetchall(_REFUSED, (asset_id, song)))
        return bool(rows)

    @staticmethod
    async def refuse_on(connection: Connection, asset_id: str, song: str) -> None:
        """Keep this song off this file for good."""
        await connection.execute(_REFUSE, (asset_id, song, int(time.time())))

    @staticmethod
    async def unrefuse_on(connection: Connection, asset_id: str, song: str) -> None:
        """Take back a "no" to this song on this file: somebody put it there by hand."""
        await connection.execute(_UNREFUSE, (asset_id, song))

    async def wants_lookup(self, asset_id: str, *, shortest_ms: int) -> bool:
        """Whether AcoustID should be asked about this file. See `_WANTS_LOOKUP`."""
        return asset_id in await self.wanting_lookup([asset_id], shortest_ms=shortest_ms)

    async def wants_asking_again(self, asset_id: str, *, shortest_ms: int, before: int) -> bool:
        """Whether AcoustID may be asked again about this file (see `_ASKED_AND_NOT_KNOWN`)."""
        found = await self.wanting_asking_again([asset_id], shortest_ms=shortest_ms, before=before)
        return asset_id in found

    async def wanting_lookup(self, asset_ids: Sequence[str], *, shortest_ms: int) -> set[str]:
        """Which of these files `wants_lookup`, asked of the page in one go."""
        return await self._wanting(_WANTS_LOOKUP, asset_ids, shortest_ms=shortest_ms)

    async def wanting_asking_again(
        self, asset_ids: Sequence[str], *, shortest_ms: int, before: int
    ) -> set[str]:
        """Which of these files `wants_asking_again`, asked of the page in one go."""
        return await self._wanting(
            _WANTS_AGAIN, asset_ids, shortest_ms=shortest_ms, before=before or _ANY_AGE
        )

    async def _wanting(
        self, statement: str, asset_ids: Sequence[str], *, shortest_ms: int, **bound: object
    ) -> set[str]:
        # The file's own length where it has one, else the fingerprint's.
        lengths = await self._songless_lengths(asset_ids)
        if not lengths:
            return set()
        rows = await self._db.fetch_all(statement, {"assets": json.dumps(list(lengths)), **bound})
        wanted: set[str] = set()
        for row in rows:
            asset_id = str(row["asset_id"])
            own = lengths[asset_id]
            length = own if own is not None else row["duration_ms"]
            if length is not None and int(length) >= shortest_ms:
                wanted.add(asset_id)
        return wanted

    async def _songless_lengths(self, asset_ids: Sequence[str]) -> dict[str, int | None]:
        """Each of these files that carries no song, with its own length (None where it has none)."""
        own = await songs_and_lengths(self._db, asset_ids)
        return {
            asset_id: found.duration_ms
            for asset_id in dict.fromkeys(asset_ids)
            if (found := own.get(asset_id)) is not None and not (found.music or "").strip()
        }

    async def not_known_page(
        self, *, after: str, limit: int, shortest_ms: int, before: int
    ) -> OwedPage:
        """Files AcoustID did not know, asked before `before`, songless, a page after `after`."""
        rows = await self._db.fetch_all(
            _NOT_KNOWN_PAGE,
            {"after": after, "limit": limit, "shortest_ms": shortest_ms, "before": before},
        )
        return await self._songless_page([str(row["asset_id"]) for row in rows])

    async def _songless_page(self, ids: list[str]) -> OwedPage:
        """A page less the songful files, with the last id read as the next start."""
        if not ids:
            return OwedPage(files=(), last=None)
        carrying = await songs_of(self._db, ids)
        return OwedPage(files=tuple(one for one in ids if not carrying.get(one)), last=ids[-1])

    async def owed_lookups(self, *, after: str, limit: int, shortest_ms: int) -> OwedPage:
        """One page of files still owed a lookup after `after`; a short page is not the end."""
        rows = await self._db.fetch_all(
            _OWED_LOOKUPS, {"after": after, "limit": limit, "shortest_ms": shortest_ms}
        )
        return await self._songless_page([str(row["asset_id"]) for row in rows])

    async def keep_lookup(self, asset_id: str, kept: LookupKept) -> None:
        """Record one lookup on `About.JOBS`; a named song rings the library bell elsewhere."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(_KEEP_LOOKUP, _lookup_row(asset_id, kept))

    @staticmethod
    async def keep_lookup_on(connection: Connection, asset_id: str, kept: LookupKept) -> None:
        """`keep_lookup` on the caller's connection, beside the name it produced."""
        await connection.execute(_KEEP_LOOKUP, _lookup_row(asset_id, kept))

    async def lookup_of(self, asset_id: str) -> LookupKept | None:
        """What the last lookup of this file sent and what came of it, or None."""
        row = await self._db.fetch_one(_LOOKUP_OF, (asset_id,))
        if row is None:
            return None
        return LookupKept(
            status=str(row["status"]),
            lengths_sent=tuple(int(one) for one in json.loads(str(row["lengths_sent"]))),
            recording_id=row["recording_id"],
            title=row["title"],
            artists=row["artists"],
            score=None if row["score"] is None else float(row["score"]),
        )

    async def key_id(self) -> str | None:
        """The id of the sealed AcoustID key, or None where none is set. Never the key."""
        row = await self._db.fetch_one(_KEY_ID)
        return None if row is None else str(row["secret_id"])

    async def set_key_id(self, secret_id: str) -> None:
        """Point at a newly sealed key, telling every admin's settings screen."""
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_SET_KEY_ID, (secret_id, int(time.time())))

    async def drop_key_id(self) -> None:
        """Forget which key was set; the caller forgets the sealed value."""
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_DROP_KEY_ID)


def _lookup_row(asset_id: str, kept: LookupKept) -> tuple[object, ...]:
    return (
        asset_id,
        int(time.time()),
        json.dumps(list(kept.lengths_sent)),
        kept.status,
        kept.recording_id,
        kept.title,
        kept.artists,
        kept.score,
    )
