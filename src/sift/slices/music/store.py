# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing what this feature records. No file is opened here.

Every statement is over this feature's own tables, and one over the per-user counts the
visibility component keeps for it. What it does not ask is which files the user asking may see:
the stored count already is that answer (`waiting_for`), and the Generate count takes this
feature's condition into the kernel's own statement (`lack` below). The one read that answers a
viewer (which files share a song with this one) asks the stored verdict (`viewer_assets`) the
way every read of a file does, and the route hands its answer to the ordinary read besides.
"""

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

#: Lacking an audio fingerprint, as one term of the Generate count.
#:
#: `a` is the asset row, which the kernel's count supplies. The question (read, with a sound
#: track, and no fingerprint row) is `music_waiting`, which the database keeps by the one rule
#: declared in `schema.py` (`WAITING`) and written out by the kernel (`kernel/access/waiting.py`):
#: this term reads the answer rather than stating the rule a second time.
#: "Has audio" is `acodec IS NOT NULL` there, so a photograph, a GIF and a silent video are all
#: excluded before anything is decoded.
#:
#: OR A FINGERPRINT NOT YET PAIRED (v3): a row whose keys have not been cut under the scheme in
#: force (`matching.KEY_SCHEME`, the first parameter), or one read by an older algorithm than the
#: one in force (`chromaprint.ALGORITHM`, the second). The work this product owes a file is
#: its fingerprint AND the pairing that reads it, and every fingerprint kept before the pairing
#: existed has the first and not the second. Without this half nothing would ever ask for them: the
#: Build asks this term which files to hand the task, and the task's own "has one, not indexed"
#: branch does the rest without opening the file. A pressed run is still the only thing that starts
#: it. See `slices/music/service.py`.
LACKS_AUDIO_FINGERPRINT = (
    "(EXISTS (SELECT 1 FROM music_waiting w WHERE w.asset_id = a.id)"
    " OR EXISTS (SELECT 1 FROM audio_fingerprints f WHERE f.asset_id = a.id"
    " AND (f.indexed_scheme IS NOT ? OR f.algorithm IS NOT ?)))"
)

#: How many waiting files one user may see with their vault shut, off the count the visibility
#: component keeps. No row is nought: a count that reaches nothing is taken away.
_WAITING_FOR = (
    "SELECT permitted - concealed AS files FROM viewer_entity_counts"
    " WHERE user_id = ? AND kind = ? AND object_id = ?"
)

#: Whether any file in the library is waiting at all.
_ANY_WAITING = "SELECT 1 AS ok FROM music_waiting LIMIT 1"

#: Which of these files lack the work: the Build's count term (`LACKS_AUDIO_FINGERPRINT`) asked of a
#: page of ids rather than of the library, spliced from the one constant so the page and the count
#: cannot describe two sets. `a.id` is each id of the page, bound as one JSON list.
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

# The claim, in one transaction: copy the waiting row onto the file and take the waiting row away.
# Two transactions would leave a window in which a crash keeps both, and the next claim would then
# write a fingerprint the file already has: harmless, and still two answers to one question
# sitting in the database at the same time. A waiting row read under an algorithm no longer in force
# is not copied: the file is then read afresh, and the row goes with the claim either way.
_CLAIM = """
INSERT INTO audio_fingerprints
  (asset_id, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)
SELECT ?, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at
  FROM audio_fingerprints_pending WHERE identity = ? AND algorithm = ?
ON CONFLICT(asset_id) DO NOTHING
"""

_DROP_CLAIMED = "DELETE FROM audio_fingerprints_pending WHERE identity = ?"

# A row nothing will ever claim: one that waited past the cutoff for a file that never arrived, or
# one read under an algorithm since replaced, which the claim refuses.
_DROP_STALE = "DELETE FROM audio_fingerprints_pending WHERE computed_at < ? OR algorithm IS NOT ?"

#: A row read under the algorithm in force. One read by an older algorithm is the count's lack too
#: (`LACKS_AUDIO_FINGERPRINT`), so it must not count as answered here, or the Build would hand the
#: file out on every run and the task would return immediately without reading it again.
_HAS_ROW = "SELECT 1 AS ok FROM audio_fingerprints WHERE asset_id = ? AND algorithm = ?"

# --- which files share a song: the keys and the pairs (v3) ------------------------------------
#
# See `slices/music/matching.py` for the rule and `schema.py` for the tables. Every list of values
# travels as ONE bound JSON array read with `json_each`, never as text in the statement.

#: Whether this file's pairing is done under the scheme in force.
_INDEXED = "SELECT 1 AS ok FROM audio_fingerprints WHERE asset_id = ? AND indexed_scheme = ?"

_DROP_KEYS = "DELETE FROM audio_fingerprint_keys WHERE asset_id = ?"

_ADD_KEYS = "INSERT INTO audio_fingerprint_keys (key, asset_id) SELECT value, ? FROM json_each(?)"

#: The files sharing at least `CANDIDATE_KEYS` distinct keys with a set of them, and how much sound
#: each one's fingerprint covers: the second half of the floor is length-relative and is decided
#: by `matching.is_candidate`, so the rule is stated once. Grouped first and joined after, so the
#: fingerprint row is read once per candidate rather than once per shared key.
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

# A file's pairs are replaced whole: two statements rather than `a_id = ? OR b_id = ?`, so each
# side is read off its own index.
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

#: The most files one group answers with. A song somebody set a few hundred clips to is a real
#: library, and one hop from each of those is a few hundred more; past this many the closest are
#: the ones worth drawing, and the statement stays a page rather than a walk of the library.
GROUP_LIMIT = 500

#: A file's group: the files paired with it, and the files paired with THOSE (one hop, never
#: further, never the file itself), ordered closest first. A file reached two ways keeps its
#: closer one, and a file reached through another scores the weaker of the two links, because a
#: chain is only as sure as its least sure pair.
#:
#: ONE statement for both callers. `:user` NULL is the group as Sift's own acts see it (the song
#: name spreading through it). A user makes it answer to the stored verdict twice over: a file is
#: in the answer only where this user may see it: concealed ones only where `:reveal` says this
#: viewer is shown one at all (`reveals_existence`, the flag every count of files binds), and a
#: file is a BRIDGE to the next hop only where this user may see it and it is not concealed from
#: them, whatever `:reveal` says, because a chain through a hidden file would describe it out of
#: visible ones. The query language's `same_music:` leaf (`kernel/access/constraints.py`) walks
#: the same hop by the same bridge rule.
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
    """Two files verified to share a song, as `music_pairs` holds them: the smaller id first.

    `offset_s` is where the song sits in `b_id` minus where it sits in `a_id`. Built by `of`,
    which is what keeps the order and the sign of the offset agreeing.
    """

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
        """Files with audio and no fingerprint, as one term of the Generate count.

        No product name on it: a file this pass cannot read is written down as an EMPTY row rather
        than as a verdict, so there is nothing to exclude: the empty row has already taken it out
        of this term. See `slices/music/schema.py`.
        """
        return Lack(LACKS_AUDIO_FINGERPRINT, params=(KEY_SCHEME, chromaprint.ALGORITHM))

    async def waiting_for(self, user_id: str) -> int:
        """How many files this user can see still want a fingerprint.

        One row, off the per-user count the visibility component keeps over `music_waiting`,
        rather than every file with audio checked against the user's visible set on every draw of
        the board. The vault's files are left out whatever the viewer's mode: this is a number about
        work, and a locked tile is not a file anybody can look at.
        """
        row = await self._db.fetch_one(_WAITING_FOR, (user_id, WAITING_KIND, WAITING_SCOPE))
        return 0 if row is None else max(0, int(row["files"]))

    async def any_waiting(self) -> bool:
        """Whether any file in the library still wants a fingerprint: the library's own fact,
        with no user, which is what decides whether the card is drawn at all."""
        return await self._db.fetch_one(_ANY_WAITING) is not None

    async def lacking_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files still owe a fingerprint or its pairing: `lack` asked of a page.

        It asks the count's own term, not merely whether a paired row exists, which would hand a
        task to every silent file and every file whose copies are missing, which the count leaves
        out, so a run would read more files handed out than it counted.
        """
        if not asset_ids:
            return set()
        # Bound from the count's own term, so the two cannot disagree about what `?` means. Written
        # out by hand, the binding could fall a value short of the term, and every Run now of the
        # music task would fail before it handed out a single file.
        params = (json.dumps(list(asset_ids)), *self.lack().params)
        rows = await self._db.fetch_all(_LACKING_AMONG, params)
        return {str(row["id"]) for row in rows}

    async def has(self, asset_id: str) -> bool:
        """Whether this file has been answered for already, under the algorithm in force."""
        found = await self._db.fetch_one(_HAS_ROW, (asset_id, chromaprint.ALGORITHM))
        return found is not None

    async def keep(self, asset_id: str, kept: Kept) -> None:
        """Write one file's fingerprint, replacing whatever was there.

        A replaced fingerprint is no longer indexed: its keys and pairs were cut from the old
        values, so the stamp goes with them and the next run pairs it again.
        """
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
        """Move a waiting fingerprint onto this file. Whether there was one to move, read under
        the algorithm in force."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_CLAIM, (asset_id, identity, chromaprint.ALGORITHM))
            claimed = int(cursor.rowcount) > 0
            await connection.execute(_DROP_CLAIMED, (identity,))
        return claimed

    async def forget_pending_before(self, cutoff: int) -> int:
        """Drop fingerprints that waited for a file that never arrived, and any read under an
        algorithm no longer in force. How many went."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_DROP_STALE, (cutoff, chromaprint.ALGORITHM))
            gone = int(cursor.rowcount)
        if gone:
            log.info("music.pending_swept", rows=gone)
        return gone

    # --- which files share a song: the keys and the pairs (v3) --------------------------------

    async def indexed(self, asset_id: str) -> bool:
        """Whether this file's fingerprint has been paired under the scheme in force."""
        return await self._db.fetch_one(_INDEXED, (asset_id, KEY_SCHEME)) is not None

    async def index_keys(self, asset_id: str, keys: Iterable[int]) -> None:
        """Replace this file's keys, in one transaction.

        Not the stamp: that is written with the pairs (`settle`), so a failure between the two
        leaves the file unindexed and the next run pairs it again. The keys are written first and
        on their own because the ORDER is what finds every pair once: a file writes its keys before
        it searches, so of two files arriving together whichever searches second finds the other.
        """
        async with self._db.write() as connection:
            await connection.execute(_DROP_KEYS, (asset_id,))
            await connection.execute(_ADD_KEYS, (asset_id, json.dumps(sorted(keys))))

    async def candidates(
        self, asset_id: str, keys: Iterable[int], *, limit: int = CANDIDATE_LIMIT
    ) -> list[Candidate]:
        """The other files sharing at least `CANDIDATE_KEYS` of these keys, most shared first, and
        no more than `limit` of them (`matching.CANDIDATE_LIMIT`)."""
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
        """Replace every pair this file is in with these, in one transaction.

        Whole rather than added to: the pairs were all verified just now against every candidate
        the keys found, so a pair of this file that is not among them no longer holds: its
        fingerprint was read again, or the rule moved. Told on its own bell (`About.SAME_MUSIC`).
        """
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
        """Say this file's pairing is done, under the scheme in force. The last write of the run."""
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
        """The files sharing a song with this one, one hop, as Sift's own acts see them: every
        file, whoever may see it. For a write Sift makes (a song name spreading through the group),
        never for an answer to a person: that is `same_music_of`."""
        return await self._group(asset_id, user_id=None, reveal=False)

    async def same_music_of(
        self, user_id: str, asset_id: str, *, reveal: bool = False
    ) -> list[str]:
        """The files sharing a song with this one that this user may see, one hop, closest first.

        Scoped by the stored verdict (the file reached and the file it was reached through),
        with a concealed file only where `reveal` says this viewer is shown one at all. Never the
        file itself. Whether the user may see THIS file is the caller's question, asked before
        this one (see the route).
        """
        return await self._group(asset_id, user_id=user_id, reveal=reveal)

    async def _group(self, asset_id: str, *, user_id: str | None, reveal: bool) -> list[str]:
        rows = await self._db.fetch_all(
            _GROUP,
            {"asset": asset_id, "user": user_id, "reveal": int(reveal), "limit": GROUP_LIMIT},
        )
        return [str(row["asset_id"]) for row in rows]


# --- SONG-NAMES: the names Sift wrote, the names taken back, what AcoustID was asked ---------------
#
# Two tables of `schema.py` and one more (`music_lookup_key`), read and written by
# `slices/music/names.py` and `slices/music/lookup.py`. The song itself is a row of the catalog's
# `songs`, and a file's is its row of `song_files`, written only through the kernel's song door
# (`kernel/content/songs.py`, reached by `seed_music_on`); that row says where it came from, which
# is what `music_names` said before step 4 of this feature's schema retired it.

#: Whether somebody took this song off this file. Read under the writer's own transaction, so an
#: Undo that lands between a check and a write cannot be overwritten by it.
_REFUSED = "SELECT 1 AS refused FROM music_name_refusals WHERE asset_id = ? AND song = ?"

_REFUSE = "INSERT OR IGNORE INTO music_name_refusals (asset_id, song, refused_at) VALUES (?, ?, ?)"

#: A song somebody put on a file by hand: any "no" to that song on that file is taken back.
_UNREFUSE = "DELETE FROM music_name_refusals WHERE asset_id = ? AND song = ?"

#: Whether a file is one AcoustID should be asked about: a fingerprint with something in it, long
#: enough for a song, no song on the file, nobody having taken a song off it, and no answer kept. A
#: lookup that ended in a refusal or a failure is asked again; one that named a song or found
#: nothing is not.
#:
#: The file's own song and length are asked of the kernel first (`songs_and_lengths`), because a
#: feature does not read the files' table: `:length_ms` is the file's length where it has one, and
#: this statement is asked only of a file that carries no song.
#:
#: Written once and spliced into both statements below, so the one file's question and the
#: library's list cannot come to disagree about what a lookup is still owed for.
_STILL_OWED = """
LENGTH(f.fingerprint) > 0
   AND NOT EXISTS (SELECT 1 FROM music_name_refusals r WHERE r.asset_id = f.asset_id)
   AND NOT EXISTS (SELECT 1 FROM music_lookups l WHERE l.asset_id = f.asset_id
                    AND l.status IN ('named', 'nothing'))
"""

#: A page of files asked one of the two questions below, in one statement. Whether each is long
#: enough for a song is decided beside it (`NameStore._wanting`), with the file's own length first.
_PAGE_OF_FILES = """
SELECT f.asset_id AS asset_id, f.duration_ms AS duration_ms
  FROM audio_fingerprints f
 WHERE f.asset_id IN (SELECT value FROM json_each(:assets))
   AND {{RULE}}
"""

_WANTS_LOOKUP = splice(_PAGE_OF_FILES, RULE=_STILL_OWED)

#: The same question asked of the library, a page at a time in id order: what the catch-up walks
#: and what its count is made of. The length here is the sound the fingerprint covers, because the
#: file's own length is the kernel's to read; each file is asked again one at a time, with its own
#: length and song (`LookupStarter._worth_asking`), before anything is queued for it.
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

#: Whether a file is one AcoustID may be ASKED AGAIN about: a fingerprint with something in it,
#: nobody having taken a song off it, and its last answer that AcoustID did not know it, given
#: before `:before` (a moment; nought for any age). The file's own song and length are asked of the
#: kernel first, as `_WANTS_LOOKUP`'s are. The earlier answer is kept until the new one replaces
#: it (`_KEEP_LOOKUP`), so a file asked again and still not known reads the same until then.
_ASKED_AND_NOT_KNOWN = """
LENGTH(f.fingerprint) > 0
   AND NOT EXISTS (SELECT 1 FROM music_name_refusals r WHERE r.asset_id = f.asset_id)
   AND EXISTS (SELECT 1 FROM music_lookups l WHERE l.asset_id = f.asset_id
                AND l.status = 'nothing' AND l.looked_up_at < :before)
"""

_WANTS_AGAIN = splice(_PAGE_OF_FILES, RULE=_ASKED_AND_NOT_KNOWN)

#: The same asked of the library a page at a time in id order, as `_OWED_LOOKUPS` is.
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

#: A moment later than any answer: "asked before this" is any answer at all.
_ANY_AGE = 2**62

#: The sealed AcoustID key's id. One row or none: an install has one application key.
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
    #: The last id the page READ, song or no song: where the next page starts. None at the end.
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
        """Whether AcoustID may be asked again about this file: its last answer was that it did not
        know it, given before `before` (nought: any age), and it still has no song. See
        `_ASKED_AND_NOT_KNOWN`."""
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
        # Long enough by the file's own length where it has one, else by what the fingerprint covers.
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
        """One page of the files AcoustID did not know, last asked before `before`, that still
        have no song, in id order after `after`. See `owed_lookups` for how the walk pages."""
        rows = await self._db.fetch_all(
            _NOT_KNOWN_PAGE,
            {"after": after, "limit": limit, "shortest_ms": shortest_ms, "before": before},
        )
        return await self._songless_page([str(row["asset_id"]) for row in rows])

    async def _songless_page(self, ids: list[str]) -> OwedPage:
        """A page read in id order, less the files that carry a song (asked of the kernel), with
        the last id READ as where the next page starts."""
        if not ids:
            return OwedPage(files=(), last=None)
        carrying = await songs_of(self._db, ids)
        return OwedPage(files=tuple(one for one in ids if not carrying.get(one)), last=ids[-1])

    async def owed_lookups(self, *, after: str, limit: int, shortest_ms: int) -> OwedPage:
        """One page of the files a lookup is still owed for, in id order after `after`.

        The files that carry a song already are left out, asked of the kernel (`songs_of`), so a
        page can hold fewer than `limit` files while more follow: the next page starts after the
        page's `last`, which is the last id READ, and the walk ends when that is None.
        """
        rows = await self._db.fetch_all(
            _OWED_LOOKUPS, {"after": after, "limit": limit, "shortest_ms": shortest_ms}
        )
        return await self._songless_page([str(row["asset_id"]) for row in rows])

    async def keep_lookup(self, asset_id: str, kept: LookupKept) -> None:
        """Write down what one lookup sent and what came of it, and tell every admin.

        Told on the work's own bell (`About.JOBS`), which the Music pane's counts and the lookup
        task's row on Tasks follow: an answer of nothing, a refusal and a failure change those
        counts and nothing any wall of files draws, so they ring no library bell. A song named
        on the file is told on the library's bell as well, by its writer (`LookupTask._keep`).
        """
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
        """Point at a newly sealed key. Every admin's settings screen is told: it says "Key set"."""
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_SET_KEY_ID, (secret_id, int(time.time())))

    async def drop_key_id(self) -> None:
        """Forget which key was set. The sealed value itself is forgotten by the caller."""
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
