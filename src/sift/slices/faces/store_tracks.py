# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the faces a pass kept, and writing who each one is: the answers, the questions
Sift asks, and the counts the People pages draw."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.db import Connection, in_clause, point_read
from sift.kernel.ledger import Actor, record_event
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import (
    Subject,
)
from sift.slices.faces import recognize
from sift.slices.faces.models import (
    AskedBy,
    Attribution,
    Vector,
)
from sift.slices.faces.schema import FACE_BAND_KIND, FACE_BAND_SEPARATOR
from sift.slices.faces.store_pictures import (
    PicturesStore,
)
from sift.slices.faces.store_records import (
    _PILES_PER_READ,
    SOURCE_STASH_BOX,
    Asked,
    Ruling,
    Standing,
    StoredFace,
    StoredTrack,
    _asker,
    _face,
    _Moment,
    _track,
    clearest,
    now_ms,
)

#: THE FACES A STASH-BOX'S ANSWER IS A CLAIM ABOUT, not yet asked (`Store.box_questions`):
#: - **One face, one person**: two faces or two names in a file make choosing a guess.
#: - **Nothing to compare her with**: with a reference this model described, the arithmetic has
#:   judged the face already, and one it did not put to her is a disagreement, never a question.
#: - **Not answered**: refused as her, or set aside with its group.
#: Narrowed on the RECOGNIZER, a partition shared with the Disagreements read. `{{MORE}}` narrows
#: it to some files. Bound as (recognizer, recognizer, [the files], how many).
_BOX_QUESTIONS = (
    "SELECT t.id AS track_id, t.asset_id AS asset_id, ap.person_id AS person_id "
    "FROM asset_people AS ap "
    "JOIN face_scans AS s ON s.asset_id = ap.asset_id AND s.recognizer = ? "
    "JOIN face_tracks AS t ON t.asset_id = ap.asset_id "
    "WHERE ap.source = 'stash_box' AND t.person_id IS NULL "
    "  AND (SELECT COUNT(*) FROM face_tracks AS o WHERE o.asset_id = ap.asset_id) = 1 "
    "  AND (SELECT COUNT(*) FROM asset_people AS b "
    "        WHERE b.asset_id = ap.asset_id AND b.source = 'stash_box') = 1 "
    "  AND NOT EXISTS (SELECT 1 FROM face_references AS r "
    "                   WHERE r.person_id = ap.person_id AND r.recognizer = ? "
    "                     AND (r.origin != 'seed' OR r.retired_at IS NULL)) "
    "  AND NOT EXISTS (SELECT 1 FROM face_rejections AS x "
    "                   WHERE x.track_id = t.id AND x.person_id = ap.person_id) "
    "  AND NOT EXISTS (SELECT 1 FROM face_piles AS p "
    "                   WHERE p.id = t.pile_id AND p.status = 'ignored') "
    "{{MORE}}ORDER BY t.id LIMIT ?"
)


if f"ap.source = '{SOURCE_STASH_BOX}'" not in _BOX_QUESTIONS:  # pragma: no cover (an edit)
    raise RuntimeError("the stash-box questions and the word the box writes disagree")


_BOX_QUESTIONS_ALL = splice(_BOX_QUESTIONS, MORE="")


#: The same, for some files, whose ids `in_clause` binds between the recognizer and the bound.
_BOX_QUESTIONS_IN = splice(_BOX_QUESTIONS, MORE="  AND ap.asset_id IN (?*) ")


#: One user's face counts, by person and how each face was named. See `Store.face_bands`.
_FACE_BANDS = (
    "SELECT object_id, permitted, concealed FROM viewer_entity_counts"
    " WHERE user_id = ? AND kind = ?"
)


#: The newest few faces of each of several people, in their own page's order: one short range
#: of `ix_face_tracks_person_recent` per person (`attributed_at DESC` puts a NULL last, so the
#: index serves the order). `?` twice for the optional narrowing.
_ATTRIBUTED_HEADS = """
SELECT t.* FROM json_each(?) p
  JOIN face_tracks t ON t.id IN (
       SELECT x.id FROM face_tracks x
        WHERE x.person_id = p.value AND (? IS NULL OR x.attribution = ?)
        ORDER BY x.attributed_at DESC, x.id DESC LIMIT ?)
 ORDER BY t.attributed_at DESC, t.id DESC
"""


#: Several people's faces as one list, newest decision first.
_ATTRIBUTED_PAGE = """
SELECT * FROM face_tracks
 WHERE person_id IN (?*) AND (? IS NULL OR attribution = ?)
 ORDER BY attributed_at DESC, id DESC
 LIMIT ? OFFSET ?
"""


#: One person's matched faces with a confidence, surest first.
_SUREST_MATCHED = """
SELECT * FROM face_tracks
 WHERE person_id = ? AND attribution = 'matched' AND confidence IS NOT NULL
 ORDER BY confidence DESC, id
 LIMIT ? OFFSET ?
"""


#: Several people's matched faces, the surest `?` of each, in one statement for a page of cards.
_SUREST_MATCHED_HEADS = """
SELECT t.* FROM json_each(?) p
  JOIN face_tracks t ON t.id IN (
       SELECT x.id FROM face_tracks x
        WHERE x.person_id = p.value AND x.attribution = 'matched' AND x.confidence IS NOT NULL
        ORDER BY x.confidence DESC, x.id LIMIT ?)
 ORDER BY t.person_id, t.confidence DESC, t.id
"""


# The two reads behind every face picture served, declared point reads so they run on the event
# loop where the machine allows it: a wall of groups asks for hundreds.
_TRACK = point_read("faces.track", "SELECT * FROM face_tracks WHERE id = ?")


_FACES_OF = point_read(
    "faces.faces_of_track",
    "SELECT * FROM face_detections WHERE track_id = ? ORDER BY timestamp_ms",
)


class TracksStore(PicturesStore):
    """Reading the faces a pass kept, and writing who each one is."""

    # --- tracks and their faces ------------------------------------------------------------

    async def tracks_of(self, asset_id: str) -> list[StoredTrack]:
        rows = await self._db.fetch_all(
            "SELECT * FROM face_tracks WHERE asset_id = ? ORDER BY started_ms", (asset_id,)
        )
        return [_track(row) for row in rows]

    async def track(self, track_id: str) -> StoredTrack | None:
        row = await self._db.fetch_one(_TRACK, (track_id,))
        return _track(row) if row is not None else None

    async def tracks(self, track_ids: Sequence[str]) -> dict[str, StoredTrack]:
        """Several appearances by id, in one read. Absent means no such row."""
        found: dict[str, StoredTrack] = {}
        wanted = list(dict.fromkeys(track_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            query, bound = in_clause(
                "SELECT * FROM face_tracks WHERE id IN (?*)", wanted[start : start + MAX_PAGE_SIZE]
            )
            for row in await self._db.fetch_all(query, bound):
                found[str(row["id"])] = _track(row)
        return found

    async def faces_of(self, track_id: str) -> list[StoredFace]:
        rows = await self._db.fetch_all(_FACES_OF, (track_id,))
        return [_face(row) for row in rows]

    async def standing_of(self, asset_id: str) -> list[Standing]:
        """Every appearance this file has now, with its standing and clearest description, read
        by a rescan before it replaces them. One with no faces left is left out."""
        rows = await self._db.fetch_all(
            "SELECT id, person_id, attribution, asked_by FROM face_tracks WHERE asset_id = ?",
            (asset_id,),
        )
        faces = await self.faces_of_many([str(row["id"]) for row in rows])
        out: list[Standing] = []
        for row in rows:
            found = faces.get(str(row["id"]))
            if not found:
                continue
            out.append(
                Standing(
                    track_id=str(row["id"]),
                    person_id=None if row["person_id"] is None else str(row["person_id"]),
                    attribution=(
                        None if row["attribution"] is None else Attribution(row["attribution"])
                    ),
                    asked_by=None if row["asked_by"] is None else AskedBy(row["asked_by"]),
                    vector=clearest(found).vector,
                )
            )
        return out

    async def write_successors(self, asset_id: str, pairs: Sequence[tuple[str, str]]) -> None:
        """Write down that each `(old, new)` appearance is one face, found again by a rescan.

        Every row that pointed at `old` is pointed at `new` in the same step, so the answer to "what
        is this face called now" is always one row away however many rescans there have been.
        """
        if not pairs:
            return
        stamp = now_ms()
        async with self._db.write() as connection:
            for old, new in pairs:
                await connection.execute(
                    "UPDATE face_successors SET successor_id = ? WHERE successor_id = ?", (new, old)
                )
                await connection.execute(
                    "INSERT INTO face_successors (track_id, successor_id, asset_id, created_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(track_id) DO UPDATE SET "
                    "successor_id = excluded.successor_id",
                    (old, new, asset_id, stamp),
                )

    async def live_ids(self, track_ids: Sequence[str]) -> dict[str, str]:
        """Each appearance by the id it goes by now: its own, or the one a rescan found it as
        (`schema._CREATE_SUCCESSORS`), which every Undo reads its receipt's faces through."""
        wanted = list(dict.fromkeys(track_ids))
        out = {one: one for one in wanted}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            query, bound = in_clause(
                "SELECT track_id, successor_id FROM face_successors WHERE track_id IN (?*)",
                wanted[start : start + MAX_PAGE_SIZE],
            )
            for row in await self._db.fetch_all(query, bound):
                out[str(row["track_id"])] = str(row["successor_id"])
        return out

    async def picture_moments(self, track_ids: Sequence[str]) -> dict[str, int]:
        """When each appearance's picture was taken, in milliseconds into its file: the moment of
        its CLEAREST face, so a pressed picture plays from the frame it shows. Only the columns
        choosing needs, never the descriptions. One with no faces left is absent.
        """
        found: dict[str, list[_Moment]] = {}
        wanted = list(dict.fromkeys(track_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            query, bound = in_clause(
                "SELECT id, track_id, quality, timestamp_ms FROM face_detections "
                "WHERE track_id IN (?*)",
                wanted[start : start + MAX_PAGE_SIZE],
            )
            for row in await self._db.fetch_all(query, bound):
                found.setdefault(str(row["track_id"]), []).append(
                    _Moment(
                        id=str(row["id"]),
                        quality=float(row["quality"]),
                        timestamp_ms=int(row["timestamp_ms"]),
                    )
                )
        return {track: clearest(faces).timestamp_ms for track, faces in found.items()}

    async def turned_of(self, track_ids: Sequence[str], line: float) -> set[str]:
        """Which of these appearances are turned past `line`: their clearest face is, chosen as
        `picture_moments` chooses it, so the face judged is the face the screen shows. For the one
        screen that says so, a file's own faces; the whole-library question is `turned_away`."""
        found: dict[str, list[_Moment]] = {}
        wanted = list(dict.fromkeys(track_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            query, bound = in_clause(
                "SELECT id, track_id, quality, timestamp_ms, frontality FROM face_detections "
                "WHERE track_id IN (?*)",
                wanted[start : start + MAX_PAGE_SIZE],
            )
            for row in await self._db.fetch_all(query, bound):
                found.setdefault(str(row["track_id"]), []).append(
                    _Moment(
                        id=str(row["id"]),
                        quality=float(row["quality"]),
                        timestamp_ms=int(row["timestamp_ms"]),
                        frontality=None if row["frontality"] is None else float(row["frontality"]),
                    )
                )
        turned: set[str] = set()
        for track, faces in found.items():
            angle = clearest(faces).frontality
            if angle is not None and angle < line:
                turned.add(track)
        return turned

    async def faces_of_many(self, track_ids: Sequence[str]) -> dict[str, list[StoredFace]]:
        """The faces of several appearances, in one read. An appearance with none is absent.

        Grouped here rather than in SQL, since the caller picks the clearest face by a rule that
        lives in Python.
        """
        found: dict[str, list[StoredFace]] = {}
        wanted = list(dict.fromkeys(track_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            query, bound = in_clause(
                "SELECT * FROM face_detections WHERE track_id IN (?*) ORDER BY track_id, "
                "timestamp_ms",
                wanted[start : start + MAX_PAGE_SIZE],
            )
            for row in await self._db.fetch_all(query, bound):
                found.setdefault(str(row["track_id"]), []).append(_face(row))
        return found

    async def best_face_per_track(self) -> list[tuple[str, Vector]]:
        """One description per appearance, its best, for matching the whole library at once,
        ordered so a re-match is reproducible."""
        rows = await self._db.fetch_all(
            "SELECT track_id, embedding FROM face_detections "
            "WHERE id IN (SELECT id FROM face_detections AS inner_faces "
            "WHERE inner_faces.track_id = face_detections.track_id "
            "ORDER BY quality DESC, id LIMIT 1) ORDER BY track_id",
            (),
        )
        return [(str(row["track_id"]), recognize.unpack(bytes(row["embedding"]))) for row in rows]

    async def attribute(
        self,
        track_id: str,
        person_id: str | None,
        *,
        confidence: float | None,
        attribution: Attribution | None,
        asked_by: AskedBy | None = None,
    ) -> None:
        """Attach a person to an appearance, or take one off, which clears the confidence too.

        `asked_by` is who asks: `MATCH` for the arithmetic, `GROUP` for a group being named. A
        writer that PUTS a question back (an Undo) passes nothing and the face keeps its word,
        which taking the person off does not clear (`schema._ADD_ASKED_BY`).
        """
        await self._db.execute(
            "UPDATE face_tracks SET person_id = ?, confidence = ?, attribution = ?, "
            "asked_by = COALESCE(?, asked_by), attributed_at = ? WHERE id = ?",
            (
                person_id,
                confidence if person_id else None,
                attribution.value if attribution and person_id else None,
                _asker(asked_by, person_id, attribution),
                # Cleared with the rest when the person comes off, so "recognized lately" cannot
                # list an appearance nobody is attached to any more.
                now_ms() if person_id else None,
                track_id,
            ),
        )

    async def forget_attribution_without_a_person(self) -> int:
        """Clear the leftovers of an attribution whose person has been deleted. Returns how many.

        `face_tracks.person_id` is ON DELETE SET NULL, which leaves a confidence and a state
        describing a decision about nobody; nothing else clears them.
        """
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "UPDATE face_tracks SET attribution = NULL, confidence = NULL, "
                    "attributed_at = NULL "
                    "WHERE person_id IS NULL AND attribution IS NOT NULL RETURNING id",
                    (),
                )
            )
        return len(rows)

    async def face_bands(self, user_id: str, *, revealed: bool) -> dict[str, dict[str, int]]:
        """Every person with a face this user may see, and how many by how each was named.

        Person id to band to count, the band an `Attribution` word or empty: one range of one
        user's rows of the stored counts (`FACE_BAND_KIND`). `revealed` says whether concealed
        files come back for this viewer, so a count is of the faces the screen will draw.
        """
        rows = await self._db.fetch_all(_FACE_BANDS, (user_id, FACE_BAND_KIND))
        bands: dict[str, dict[str, int]] = {}
        for row in rows:
            shown = int(row["permitted"]) - (0 if revealed else int(row["concealed"]))
            if shown <= 0:
                continue
            person_id, _sep, band = str(row["object_id"]).partition(FACE_BAND_SEPARATOR)
            bands.setdefault(person_id, {})[band] = shown
        return bands

    async def attributed_heads(
        self, person_ids: Sequence[str], *, attribution: Attribution | None, each: int
    ) -> list[StoredTrack]:
        """The newest `each` faces of each of these people, narrowed to one kind when asked, in
        the order their own page reads (`attributed_to`): one statement for a page of cards."""
        if not person_ids or each <= 0:
            return []
        wanted = None if attribution is None else attribution.value
        rows = await self._db.fetch_all(
            _ATTRIBUTED_HEADS, (json.dumps(list(person_ids)), wanted, wanted, each)
        )
        return [_track(row) for row in rows]

    async def attributed_page(
        self,
        person_ids: Sequence[str],
        *,
        attribution: Attribution | None,
        limit: int,
        offset: int,
    ) -> list[StoredTrack]:
        """One page of these people's faces together, newest decision first.

        For a card whose first faces were not enough (every one of them on a file this viewer
        may not see) and for the nameless card, which is several people's faces as one.
        """
        if not person_ids:
            return []
        sql, params = in_clause(_ATTRIBUTED_PAGE, list(person_ids))
        wanted = None if attribution is None else attribution.value
        rows = await self._db.fetch_all(sql, [*params, wanted, wanted, limit, offset])
        return [_track(row) for row in rows]

    async def surest_matched(self, person_id: str, *, limit: int, offset: int) -> list[StoredTrack]:
        """One person's matched faces, the surest first. A page, for the card's best percentage:
        the first of these on a file the viewer may see is the answer."""
        rows = await self._db.fetch_all(_SUREST_MATCHED, (person_id, limit, offset))
        return [_track(row) for row in rows]

    async def surest_matched_heads(
        self, person_ids: Sequence[str], *, each: int
    ) -> dict[str, list[StoredTrack]]:
        """`surest_matched`'s first page for each of these people, by person: one statement for a
        page of cards. A person with no matched face is absent."""
        if not person_ids or each <= 0:
            return {}
        rows = await self._db.fetch_all(
            _SUREST_MATCHED_HEADS, (json.dumps(list(dict.fromkeys(person_ids))), each)
        )
        found: dict[str, list[StoredTrack]] = {}
        for row in rows:
            track = _track(row)
            found.setdefault(str(track.person_id), []).append(track)
        return found

    async def attributed_to(self, person_id: str, *, most: int) -> list[StoredTrack]:
        """ONE person's attributed appearances, most recent decision first.

        Its own statement, narrowed in SQL on the person (the partial index on `person_id`), so a
        busy library cannot crowd her out. Bounded by `PERSON_FACES_AT_MOST`, since visibility is
        settled over the whole set before a page is taken.
        """
        rows = await self._db.fetch_all(
            "SELECT * FROM face_tracks WHERE person_id = ? "
            "ORDER BY attributed_at IS NULL, attributed_at DESC, id DESC LIMIT ?",
            (person_id, most),
        )
        return [_track(row) for row in rows]

    async def proposed(self, *, most: int) -> list[StoredTrack]:
        """Every appearance Sift has PROPOSED somebody for, surest first.

        Between the two lines, plus the faces offered off a named group, which carry no confidence
        and sort last. **Surest first is the answer**: how close the arithmetic came is the only
        rank for "which should I look at". Narrowed in SQL on the attribution and bounded by
        `SUGGESTIONS_AT_MOST`, since visibility is settled over the whole set.
        """
        rows = await self._db.fetch_all(
            "SELECT * FROM face_tracks WHERE person_id IS NOT NULL AND attribution = 'suggested' "
            "ORDER BY confidence IS NULL, confidence DESC, id DESC LIMIT ?",
            (most,),
        )
        return [_track(row) for row in rows]

    async def take_back_a_cover(self, person_id: str, track_id: str, *, actor: Actor) -> bool:
        """Take off the cover this face gave the person, where it is still that face. True if so.

        The undo of a face that filled the gap, for a receipt that says one did, and **only while
        the cover is still exactly that face**: every other cover writer clears `cover_track_id`,
        so a chosen picture is a newer decision and stays. No clear mark is written, so the rule
        for an entity with no cover (`kernel/access/default_covers.py`) takes over. Written into
        the ledger in the same transaction (`sentences.COVER_WITHOUT_OBJECT`).
        """
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "UPDATE people SET cover_asset_id = NULL, cover_track_id = NULL, "
                    "cover_at_ms = NULL, cover_frame = NULL, cover_by_default = NULL "
                    "WHERE id = ? AND cover_track_id = ? AND cover_upload_id IS NULL "
                    "RETURNING id, name",
                    (person_id, track_id),
                )
            )
            if rows:
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="person", id=person_id, name=str(rows[0]["name"])),
                    payload=json.dumps({"cover": "none"}),
                )
                # The person's picture is drawn on every screen that lists them.
                announce(EVERY_ADMIN, About.LIBRARY)
        return bool(rows)

    async def unattributed(self, recognizer: str) -> list[tuple[str, str, Vector]]:
        """Every appearance nobody has been attached to, with its file and best description: what
        clustering piles up and a new person is matched against.

        **Only the appearances described by `recognizer`**: two models' numbers are the same
        length and mean nothing to each other (see `measured_by_others`). The file comes back so a
        re-match knows which files to recount without asking per face.
        """
        # One row per appearance, chosen inside the database so the blobs not chosen never leave
        # it: the highest quality, the lowest id on a tie, so two runs over untouched data agree.
        rows = await self._db.sweep_all(
            "SELECT t.id AS track_id, t.asset_id AS asset_id, d.embedding AS embedding "
            "FROM face_tracks AS t "
            "JOIN face_scans AS s ON s.asset_id = t.asset_id AND s.recognizer = ? "
            "JOIN face_detections AS d ON d.id = ("
            "  SELECT best.id FROM face_detections AS best "
            "   WHERE best.track_id = t.id "
            "   ORDER BY best.quality DESC, best.id LIMIT 1"
            ") "
            "WHERE t.person_id IS NULL ORDER BY t.id",
            (recognizer,),
            what="facial fingerprints",
        )
        return [
            (
                str(row["track_id"]),
                str(row["asset_id"]),
                recognize.unpack(bytes(row["embedding"])),
            )
            for row in rows
        ]

    async def asked(self, recognizer: str) -> list[Asked]:
        """Every question standing, with the person proposed, its confidence, who asked and the
        face's clearest description: what a re-match judges again beside `unattributed`, chosen
        exactly as that read chooses."""
        rows = await self._db.sweep_all(
            "SELECT t.id AS track_id, t.asset_id AS asset_id, t.person_id AS person_id, "
            "t.confidence AS confidence, d.embedding AS embedding, "
            # An older question put back is read by the rule `_REMEMBER_WHO_ASKED` stamped.
            "COALESCE(t.asked_by, CASE WHEN t.confidence IS NULL THEN 'group' ELSE 'match' END) "
            "AS asked_by "
            "FROM face_tracks AS t "
            "JOIN face_scans AS s ON s.asset_id = t.asset_id AND s.recognizer = ? "
            "JOIN face_detections AS d ON d.id = ("
            "  SELECT best.id FROM face_detections AS best "
            "   WHERE best.track_id = t.id "
            "   ORDER BY best.quality DESC, best.id LIMIT 1"
            ") "
            "WHERE t.person_id IS NOT NULL AND t.attribution = ? ORDER BY t.id",
            (recognizer, Attribution.SUGGESTED.value),
            what="face questions",
        )
        return [
            Asked(
                track_id=str(row["track_id"]),
                asset_id=str(row["asset_id"]),
                person_id=str(row["person_id"]),
                confidence=None if row["confidence"] is None else float(row["confidence"]),
                vector=recognize.unpack(bytes(row["embedding"])),
                asked_by=AskedBy(str(row["asked_by"])),
            )
            for row in rows
        ]

    async def restate(self, rulings: Sequence[Ruling]) -> set[str]:
        """Write a pass's rulings in ONE transaction, each only if its face is still where the pass
        read it. Returns the faces whose rows were written.

        A ruling that keeps the person and the state is a re-score and moves only the confidence;
        every other is `attribute`'s write. Both write who asked where the ruling says (`_asker`).
        """
        if not rulings:
            return set()
        async with self._db.write() as connection:
            return await self.restate_on(connection, rulings)

    async def restate_on(self, connection: Connection, rulings: Sequence[Ruling]) -> set[str]:
        """`restate` inside the caller's write."""
        landed: set[str] = set()
        stamp = now_ms()
        for ruling in rulings:
            was = ruling.was.value if ruling.was is not None else None
            guard = (ruling.track_id, ruling.was_person, was, was)
            if ruling.person_id == ruling.was_person and ruling.attribution == ruling.was:
                rows = await connection.execute_fetchall(
                    "UPDATE face_tracks SET confidence = ?, asked_by = COALESCE(?, asked_by) "
                    "WHERE id = ? AND person_id IS ? AND (? IS NULL OR attribution = ?) "
                    "RETURNING id",
                    (
                        ruling.confidence,
                        _asker(ruling.asked_by, ruling.person_id, ruling.attribution),
                        *guard,
                    ),
                )
            else:
                held = ruling.person_id is not None
                rows = await connection.execute_fetchall(
                    "UPDATE face_tracks SET person_id = ?, confidence = ?, attribution = ?, "
                    "asked_by = COALESCE(?, asked_by), "
                    "attributed_at = ? "
                    "WHERE id = ? AND person_id IS ? AND (? IS NULL OR attribution = ?) "
                    "RETURNING id",
                    (
                        ruling.person_id,
                        ruling.confidence if held else None,
                        ruling.attribution.value if held and ruling.attribution else None,
                        _asker(ruling.asked_by, ruling.person_id, ruling.attribution),
                        stamp if held else None,
                        *guard,
                    ),
                )
            landed.update(str(row["id"]) for row in rows)
        return landed

    async def box_questions(
        self, recognizer: str, *, most: int, asset_ids: Sequence[str] | None = None
    ) -> list[tuple[str, str, str]]:
        """The faces a stash-box put somebody on the file of, not yet asked: `(face, file, who)`.

        Every such face in the library, or only in `asset_ids`. See `_BOX_QUESTIONS` for what
        makes one; bounded, like every list read here.
        """
        if asset_ids is None:
            rows = await self._db.fetch_all(_BOX_QUESTIONS_ALL, (recognizer, recognizer, most))
        else:
            wanted = list(dict.fromkeys(asset_ids))
            if not wanted:
                return []
            sql, params = in_clause(_BOX_QUESTIONS_IN, wanted)
            rows = await self._db.fetch_all(sql, (recognizer, recognizer, *params, most))
        return [(str(row["track_id"]), str(row["asset_id"]), str(row["person_id"])) for row in rows]

    async def best_vectors(self, track_ids: Sequence[str]) -> dict[str, Vector]:
        """The clearest description of each of these appearances, chosen as `unattributed`
        chooses, so a re-match scores what the pass scored. Absent where there is none."""
        wanted = list(dict.fromkeys(track_ids))
        found: dict[str, Vector] = {}
        for start in range(0, len(wanted), _PILES_PER_READ):
            sql, params = in_clause(
                "SELECT t.id AS track_id, d.embedding AS embedding FROM face_tracks AS t "
                "JOIN face_detections AS d ON d.id = ("
                "  SELECT best.id FROM face_detections AS best "
                "   WHERE best.track_id = t.id "
                "   ORDER BY best.quality DESC, best.id LIMIT 1"
                ") WHERE t.id IN (?*)",
                wanted[start : start + _PILES_PER_READ],
            )
            for row in await self._db.fetch_all(sql, params):
                found[str(row["track_id"])] = recognize.unpack(bytes(row["embedding"]))
        return found
