# SPDX-License-Identifier: AGPL-3.0-or-later
"""Song writes, and the reads those writes check themselves against.

The Photo Sets slice's three rules, for the same reasons (see `slices/photo_sets/service.py`):
nothing here decides who may do anything (the route resolves the song through the access layer
first), every write records what it did in the ledger, and a write that changes what a screen
draws says so.

**A file's song is the kernel's.** Which song a file carries is a row of `song_files`, written
only through the kernel's song door (`kernel/content/songs.py`), so a file put on a song or taken
off it here goes through that door like Sift's own writers do; and the file's Music field, which
the record, the search index and the `music:` filter read, follows the song by the database's own
triggers. So a rename here renames every file's field, a merge moves every file's field to the
survivor's name, and a delete empties them: the reply says which files those were, for the route
to tell the search index.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sift.kernel.access import ObjectType, Viewer
from sift.kernel.access.stamps import bump_stamps_for_object
from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.changes import About, announce, telling, who_may_see_a_file
from sift.kernel.content import songs
from sift.kernel.content.entity_state import opinion_before
from sift.kernel.content.user_state import OpinionKind, record_opinion
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import ChosenCover, chosen_from_row, cover_change
from sift.kernel.db import Database, in_clause
from sift.kernel.ledger import ACTOR_USER, Actor, Object, record_event
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part

_ONE = "SELECT id, name, recording_id, notes FROM songs WHERE id = ?"
_RENAME = "UPDATE songs SET name = ?, name_sort = ? WHERE id = ? RETURNING id"
_SET_NOTES = "UPDATE songs SET notes = ? WHERE id = ? RETURNING id"
_CHOSEN_COVER = (
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM songs WHERE id = ?"
)
#: One statement writes every cover pointer. See `photo_sets.service._SET_COVER`.
_SET_COVER = (
    "UPDATE songs SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    " cover_cleared_at = ?, cover_by_default = NULL WHERE id = ? RETURNING name"
)
_DELETE = "DELETE FROM songs WHERE id = ?"
#: The files carrying one song, which a rename, a merge and a delete change the field of.
_FILES_OF = "SELECT asset_id FROM song_files WHERE song_id = ? ORDER BY asset_id"
_FILES_OF_MANY = "SELECT asset_id FROM song_files WHERE song_id IN (?*) ORDER BY asset_id"
_NAMES_OF = "SELECT id, name, recording_id, notes FROM songs WHERE id IN (?*)"
#: A merge: every file of the songs going moves to the one kept, and the field follows (the
#: `song_music_moved` trigger in the kernel's door).
_MOVE_FILES = "UPDATE song_files SET song_id = ? WHERE song_id IN (?*)"
#: The survivor takes what it has not got: the recording that says which piece of music it is, and
#: the note somebody wrote. Its own name and cover stay, which is what keeping it means.
_TAKE_RECORDING = "UPDATE songs SET recording_id = ? WHERE id = ? AND recording_id IS NULL"
_LET_GO_OF_RECORDING = "UPDATE songs SET recording_id = NULL WHERE id = ?"
_TAKE_NOTES = "UPDATE songs SET notes = ? WHERE id = ? AND (notes IS NULL OR notes = '')"
_DELETE_MANY = "DELETE FROM songs WHERE id IN (?*)"

_SET_FAVORITE = """
INSERT INTO song_user_state (song_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(song_id, user_id) DO UPDATE SET
  favorite = excluded.favorite, updated_at = excluded.updated_at
RETURNING favorite, rating
"""
_SET_VAULT = """
INSERT INTO song_user_state (song_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(song_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_SET_RATING = """
INSERT INTO song_user_state (song_id, user_id, rating, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(song_id, user_id) DO UPDATE SET
  rating = excluded.rating, updated_at = excluded.updated_at
RETURNING favorite, rating
"""


@dataclass(frozen=True, slots=True)
class Song:
    """A song as the table holds it, unscoped. What a screen is shown is `SongView`."""

    id: str
    name: str
    recording_id: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class Changed:
    """What a write changed: whether it landed, and the files whose Music field moved with it,
    which the route hands to the search index (a song's name is in each file's words)."""

    done: bool
    files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Credited:
    """What setting a song's artists left: whether the song was there, and its credits after."""

    done: bool
    artists: tuple[songs.Credit, ...] = ()


@dataclass(frozen=True, slots=True)
class Merged:
    """What a merge of songs moved, or would move. See `models.SongsMerged`."""

    into_name: str
    from_names: tuple[str, ...]
    files: tuple[str, ...]


def _opinion_of(row) -> tuple[bool, int | None]:  # type: ignore[no-untyped-def]
    return bool(row["favorite"]), None if row["rating"] is None else int(row["rating"])


class SongService:
    """Song writes. The kernel's door writes a file's song; this writes the song's own row."""

    def __init__(self, database: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = database
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def get(self, song_id: str) -> Song | None:
        """One song's row, unscoped. The route has resolved it against the viewer first."""
        row = await self._db.fetch_one(_ONE, (song_id,))
        if row is None:
            return None
        return Song(
            id=str(row["id"]),
            name=str(row["name"]),
            recording_id=row["recording_id"],
            notes=row["notes"],
        )

    async def create(self, name: str, *, by_user: str) -> Song:
        """The song a name means, made by this user where no song is called that.

        A song's identity is its recording where AcoustID said one and its name where nothing did
        (`kernel/content/songs.py`), so making one by a name a song already carries answers THAT
        song rather than a second of the same name: the two would be one piece of music twice, and
        the Music page would hold a merge waiting to happen.
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            song_id = await songs.song_called(connection, name, made=songs.by_user(by_user))
        if song_id is None:  # pragma: no cover (the model refuses a blank name)
            raise ValueError("a song's name cannot be blank")
        made = await self.get(song_id)
        if made is None:  # pragma: no cover (written a moment ago)
            raise ValueError("the song was not written")
        return made

    async def files_of(self, song_id: str) -> list[str]:
        """The files carrying one song, unscoped: for the search index, never for a screen."""
        return [str(row["asset_id"]) for row in await self._db.fetch_all(_FILES_OF, (song_id,))]

    async def rename(self, song_id: str, name: str, *, actor: Actor) -> Changed:
        """Rename the song, and with it the Music field of every file carrying it (the kernel's
        trigger). The name it had goes into the event, because the column is overwritten in place.
        """
        was = await self.get(song_id)
        if was is None:
            return Changed(done=False)
        cleaned = songs.cleaned_song(name)
        if cleaned == was.name:
            return Changed(done=True)
        files = await self.files_of(song_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(_RENAME, (cleaned, sort_key(cleaned), song_id))
            )
            if not rows:  # pragma: no cover (resolved a moment ago)
                return Changed(done=False)
            await record_event(
                connection,
                actor=actor,
                verb="renamed",
                subject=Subject(kind="song", id=song_id, name=cleaned),
                payload=json.dumps({"before": was.name}),
            )
            if files:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return Changed(done=True, files=tuple(files))

    async def set_notes(self, song_id: str, notes: str | None, *, actor: Actor) -> bool:
        """Write the note somebody typed on it. False when there is no such song."""
        was = await self.get(song_id)
        if was is None:
            return False
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(_SET_NOTES, (notes, song_id)))
            if rows:  # pragma: no branch (resolved a moment ago)
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="song", id=song_id, name=was.name),
                    payload=json.dumps({"field": "notes"}),
                )
        return bool(rows)

    async def chosen_cover(self, song_id: str) -> ChosenCover:
        """What this song is drawn as: an uploaded picture, a file and a moment of it, or nothing
        (the music glyph)."""
        row = await self._db.fetch_one(_CHOSEN_COVER, (song_id,))
        if row is None:  # pragma: no cover (the route resolved the song a line ago)
            return ChosenCover()
        return chosen_from_row(row)

    async def set_cover(
        self,
        song_id: str,
        asset_id: str | None,
        at_ms: int | None = None,
        upload_id: str | None = None,
        *,
        actor: Actor,
        frame: CoverFrame | None = None,
    ) -> bool:
        """Choose the picture the song is drawn as. See `PhotoSetService.set_cover`."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            was = list(await connection.execute_fetchall(_CHOSEN_COVER, (song_id,)))
            change = cover_change(
                chosen_from_row(was[0]) if was else ChosenCover(),
                asset_id=asset_id,
                at_ms=at_ms,
                upload_id=upload_id,
                frame=frame,
                box=None,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_COVER,
                    (asset_id, at_ms, upload_id, change.frame, change.cleared_at, song_id),
                )
            )
            if rows:
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="song", id=song_id, name=str(rows[0]["name"])),
                    object=change.object,
                    payload=change.payload,
                )
        return bool(rows)

    async def delete(self, song_id: str, *, actor: Actor) -> Changed:
        """Delete the song. Its files stay, keep their music fingerprints and lose the name: the
        cascade takes each file's row off it, and the kernel's trigger empties each Music field.
        """
        was = await self.get(song_id)
        if was is None:
            return Changed(done=False)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            # Every file taken off it by hand, so no pass names the song on them again.
            files = await songs.let_go_of(connection, song_id)
            cursor = await connection.execute(_DELETE, (song_id,))
            if not cursor.rowcount:  # pragma: no cover (resolved a moment ago)
                return Changed(done=False)
            # Its credits went with it (the cascade); an artist nothing else credits goes too.
            await songs.forget_uncredited_artists(connection)
            await record_event(
                connection,
                actor=actor,
                verb="deleted",
                subject=Subject(kind="song", id=song_id, name=was.name),
                payload=json.dumps({"files": len(files)}),
            )
            if files:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return Changed(done=True, files=tuple(files))

    async def add(self, song_id: str, asset_ids: Sequence[str], *, actor: Actor) -> list[str]:
        """Put files on the song by hand, in the order given. The files that moved.

        A file on another song moves to this one, as choosing this song in its Music field would
        move it: a file carries one song. A file already on it is left as it was and not counted.
        """
        was = await self.get(song_id)
        if was is None:
            return []
        landed: list[str] = []
        async with self._db.write() as connection:
            for asset_id in asset_ids:
                if await songs.put_on_by_hand(connection, asset_id, song_id):
                    landed.append(asset_id)
                    await record_event(
                        connection,
                        actor=actor,
                        verb="linked",
                        subject=Subject(kind="asset", id=asset_id),
                        object=Object(kind="song", id=song_id, name=was.name),
                    )
            if landed:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return landed

    async def remove(self, song_id: str, asset_ids: Sequence[str], *, actor: Actor) -> list[str]:
        """Take files off the song. The files that were on it."""
        was = await self.get(song_id)
        if was is None:
            return []
        taken: list[str] = []
        async with self._db.write() as connection:
            for asset_id in asset_ids:
                if await songs.take_off(connection, asset_id, song_id):
                    taken.append(asset_id)
                    await record_event(
                        connection,
                        actor=actor,
                        verb="unlinked",
                        subject=Subject(kind="asset", id=asset_id),
                        object=Object(kind="song", id=song_id, name=was.name),
                    )
            if taken:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return taken

    async def _merging(self, into: str, going: Sequence[str]) -> tuple[Song, list[Song]] | None:
        """The survivor and the songs going, read before anything moves. None where any is gone."""
        sql, values = in_clause(_NAMES_OF, [into, *going])
        rows = {str(row["id"]): row for row in await self._db.fetch_all(sql, values)}
        if into not in rows or any(one not in rows for one in going):
            return None

        def song(one: str) -> Song:
            row = rows[one]
            return Song(
                id=one,
                name=str(row["name"]),
                recording_id=row["recording_id"],
                notes=row["notes"],
            )

        return song(into), [song(one) for one in going]

    async def weigh_merge(self, into: str, going: Sequence[str]) -> Merged | None:
        """What folding these songs into one would move. Nothing is written."""
        found = await self._merging(into, going)
        if found is None:
            return None
        kept, gone = found
        sql, values = in_clause(_FILES_OF_MANY, [one.id for one in gone])
        files = tuple(str(row["asset_id"]) for row in await self._db.fetch_all(sql, values))
        return Merged(into_name=kept.name, from_names=tuple(one.name for one in gone), files=files)

    async def merge(self, into: str, going: Sequence[str], *, actor: Actor) -> Merged | None:
        """Fold several songs into one, in one transaction. None where any of them is gone.

        Every file of the songs going moves to the one kept, and its Music field with it (the
        kernel's trigger). The survivor keeps its name and its cover, and takes a recording and a
        note it has none of from the first song going that has one: which piece of music it is,
        and what somebody wrote about it, are facts the merge must not lose. One `merged` event per
        song going, the survivor as the object, the way a person's merge is recorded. It cannot be
        taken back, which is why the screen weighs it first.
        """
        weighed = await self.weigh_merge(into, going)
        found = await self._merging(into, going)
        if weighed is None or found is None:
            return None
        kept, gone = found
        recording = kept.recording_id or next(
            (one.recording_id for one in gone if one.recording_id), None
        )
        notes = kept.notes or next((one.notes for one in gone if one.notes), None)
        ids = [one.id for one in gone]
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            sql, values = in_clause(_MOVE_FILES, ids)
            await connection.execute(sql, [into, *values])
            if recording and not kept.recording_id:
                # One recording is one song (a unique index): the song going lets go of it first.
                for one in gone:
                    if one.recording_id == recording:
                        await connection.execute(_LET_GO_OF_RECORDING, (one.id,))
                await connection.execute(_TAKE_RECORDING, (recording, into))
            if notes and not kept.notes:
                await connection.execute(_TAKE_NOTES, (notes, into))
            # And the artists, where it credits none: the first song going that credits any.
            if not await songs.credits_of(connection, into):
                for one in gone:
                    credited = await songs.credits_of(connection, one.id)
                    if credited:
                        await songs.credit(
                            connection,
                            into,
                            [artist.name for artist in credited],
                            made=songs.UNSAID,
                            source=None,
                        )
                        break
            for one in gone:
                await record_event(
                    connection,
                    actor=actor,
                    verb="merged",
                    subject=Subject(kind="song", id=one.id, name=one.name),
                    object=Object(kind="song", id=into, name=kept.name),
                )
            sql, values = in_clause(_DELETE_MANY, ids)
            await connection.execute(sql, values)
            await songs.forget_uncredited_artists(connection)
            if weighed.files:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return weighed

    async def set_favorite(
        self, viewer: Viewer, song_id: str, *, favorite: bool
    ) -> tuple[bool, int | None]:
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            before = await opinion_before(
                connection,
                subject_kind="song",
                subject_id=song_id,
                user_id=viewer.id,
                kind=OpinionKind.FAVORITE,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_FAVORITE, (song_id, viewer.id, int(favorite), now)
                )
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="song",
                subject_id=song_id,
                kind=OpinionKind.FAVORITE,
                before=before,
                after=int(favorite),
                at=now,
            )
        return _opinion_of(rows[0])

    async def set_vault(self, viewer: Viewer, song_id: str, *, vault: bool) -> None:
        """Hide the song from this user, or stop hiding it. See `PhotoSetService.set_vault`:
        hiding a song conceals the FILES that carry it as well as its row (the verdict's own arm),
        so the revocation stamp moves in the same transaction."""
        now = self._now()
        async with self._db.write() as connection:
            before = await opinion_before(
                connection,
                subject_kind="song",
                subject_id=song_id,
                user_id=viewer.id,
                kind=OpinionKind.HIDE,
            )
            await connection.execute(
                _SET_VAULT, (song_id, viewer.id, int(vault), now if vault else None, now)
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="song",
                subject_id=song_id,
                kind=OpinionKind.HIDE,
                before=before,
                after=int(vault),
                at=now,
            )
            moved = await bump_stamps_for_object(connection, ObjectType.SONG, song_id)
            announce(moved, About.LIBRARY)

    async def set_artists(self, song_id: str, names: Sequence[str], *, actor: Actor) -> Credited:
        """Set the song's artists, in this order, by hand: a name finds its artist or makes one,
        an artist left credited by nothing goes. One History line on the song, with the names
        before and after, where anything changed. The song's NAME is left as it is."""
        was = await self.get(song_id)
        if was is None:
            return Credited(done=False)
        made = songs.by_user(actor.id) if actor.kind == ACTOR_USER else songs.UNSAID
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            before, after = await songs.credit(
                connection, song_id, list(names), made=made, source=None
            )
            if [one.name for one in before] != [one.name for one in after]:
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="song", id=song_id, name=was.name),
                    payload=json.dumps(
                        {
                            "field": "artists",
                            "before": [one.name for one in before],
                            "after": [one.name for one in after],
                        }
                    ),
                )
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return Credited(done=True, artists=tuple(after))

    async def rename_artist(self, artist_id: str, name: str, *, actor: Actor) -> bool:
        """Rename an artist on every song that credits it (`songs.rename_artist`), one History line
        on each of those songs. False where there is no such artist."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            renamed = await songs.rename_artist(connection, artist_id, name)
            if renamed is None:
                return False
            after = songs.cleaned_song(name)
            for song_id in renamed.songs:
                row = list(await connection.execute_fetchall(_ONE, (song_id,)))
                if not row:  # pragma: no cover (read inside the write that holds the credit)
                    continue
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="song", id=song_id, name=str(row[0]["name"])),
                    payload=json.dumps(
                        {"field": "artists", "before": [renamed.before], "after": [after]}
                    ),
                )
            if renamed.songs:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return True

    async def set_rating(
        self, viewer: Viewer, song_id: str, *, rating: int | None
    ) -> tuple[bool, int | None]:
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            before = await opinion_before(
                connection,
                subject_kind="song",
                subject_id=song_id,
                user_id=viewer.id,
                kind=OpinionKind.RATING,
            )
            rows = list(
                await connection.execute_fetchall(_SET_RATING, (song_id, viewer.id, rating, now))
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="song",
                subject_id=song_id,
                kind=OpinionKind.RATING,
                before=before,
                after=rating,
                at=now,
            )
        return _opinion_of(rows[0])


#: The one song service. Held as a part so nothing imports this slice to reach it.
SERVICE: Part[SongService] = Part("songs")
