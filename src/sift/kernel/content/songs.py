# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's song: the one door every writer of it goes through.

A song is a row of `songs` (the note over the table in `kernel/access/schema.py` says what one is)
and a file carries at most one: its row of `song_files`. Five things put a song on a file: a
download reading a Site's page, AcoustID's answer to the file's music fingerprint, another file
with the same music, a swap from another install, and a person's own hand (the file's Music
field, or the song's own page).
Every one of them comes through here.

## The one home, and the field that follows it

`song_files` is where a file's song is. `assets.music`, the file's Music field, is read by the
record, the search index, the `music:` filter, a swap and a Stash import, and it is KEPT EQUAL to
the song's name by the triggers below rather than written by anybody: a file put on a song takes
its name, a song renamed renames every file on it, a song merged into another moves its files'
names with them, and a file taken off its song has the field emptied. So nothing that reads the
field needs to know songs exist, and no writer can leave the two saying different things.
`test_songs` holds that no statement outside this module writes the field.

`music_names`, where the music feature recorded where Sift's name for a file came from, is moved
here by catalog step 81 and dropped by the music feature's own step after it: its facts (the
source, the file a name came from, the recording, the score) are the membership's and the song's
own columns. `music_lookups` stays: it is what AcoustID was asked and what it said, whether or not
any file took the name.

## Which song a name means

A recording is one song: an answer naming a recording finds the song that carries it. A name with
no recording finds the song of that name, the one with no recording first, so a name read off a
Site's page and the same name typed later are one song; and an answer whose recording no song
carries yet takes a song of the same name that has none, which is the page's name and AcoustID's
being the same piece of music. Two recordings with one name stay two songs, and two songs a person
knows are one are merged on the Music page.

## Who made a song

A song made here says who made it in the five columns every row in the catalog carries: Sift and
the task (the download for a Site's page, the lookup for AcoustID), or the user who typed it. A
song made from the same music as another file is that file's song already, so no song is made that
way unless the file it came from carries a name and no song, which only an older library can.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sift.kernel.db import Connection, register_schema_invariant
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import table_exists
from sift.kernel.sorting import sort_key
from sift.kernel.sql_splice import splice
from sift.kernel.text import clean_stored_text
from sift.kernel.vocabulary import VIA_DOWNLOAD, VIA_MUSIC_LOOKUP, VIA_SWAP, VIA_UPDATE, Subject

log = get_logger(__name__)

#: How a song came to be on a file, as `song_files.source` holds it. A person's own hand is NULL,
#: the word every membership table in the catalog uses for that.
SOURCE_ACOUSTID = "acoustid"
SOURCE_SITE = "site"
SOURCE_SHARED = "shared"
#: The song a file arrived with from another install: the sender's song, landed as this library
#: knows it (the recording's song, else the name's, else one made by the swap).
SOURCE_SWAP = "swap"
SOURCES = (SOURCE_ACOUSTID, SOURCE_SITE, SOURCE_SHARED, SOURCE_SWAP)


def cleaned_song(music: str) -> str:
    """A song's name as a file's record keeps it: control characters out, runs of space folded.

    NOT the address cleaner (`cleaned_for_record`, a URL normaliser), which would store
    "Night Drive? - Band" as "Night Drive" (the rest read as a query string) and
    "Song #1 - X" as "Song " (a fragment).
    """
    return " ".join(clean_stored_text(music).split())


@dataclass(frozen=True, slots=True)
class Maker:
    """Who makes a song where none carries the name yet: the catalog's `created_by_*` columns."""

    kind: str | None
    via: str | None = None
    user_id: str | None = None


#: A song named from a Site's page is made by the download that read it.
BY_DOWNLOAD = Maker("sift", VIA_DOWNLOAD)
#: A song AcoustID named is made by the lookup.
BY_LOOKUP = Maker("sift", VIA_MUSIC_LOOKUP)
#: A song a file arrived with from another install is made by the swap.
BY_SWAP = Maker("sift", VIA_SWAP)
#: The row does not say: a name carried from another file that had no song of its own.
UNSAID = Maker(None)


def by_user(user_id: str | None) -> Maker:
    """A song somebody typed, made by them; by nobody the row can name where no user is known."""
    return Maker("user", None, user_id) if user_id else UNSAID


#: Which maker each of Sift's three sources makes a song as.
_MADE_BY_SOURCE = {
    SOURCE_ACOUSTID: BY_LOOKUP,
    SOURCE_SITE: BY_DOWNLOAD,
    SOURCE_SHARED: UNSAID,
    SOURCE_SWAP: BY_SWAP,
}


# --- what a person's hand means to the passes that name songs on their own ---------------------
#
# A song somebody took off a file by hand (off the song's page, out of the file's Music field, or
# by deleting the song) is a "no" the passes that name songs by themselves must keep: the next time
# the file's same-music group settles, or AcoustID is asked about it, the song must not come
# straight back. And a song somebody puts on a file by hand is a "yes" that takes such a "no" back.
#
# The passes and their record of refusals belong to the music feature (`music_name_refusals`,
# which its spread and its lookup already honour), and the kernel may not import a feature. So
# the feature registers what each act means to it, here, at import (`slices/music/names.py`), and
# the door calls every registered hand on the caller's own connection, inside the same write as
# the act. With nothing registered (a library without the music feature) the act is only itself.

#: What one hand is told: the caller's connection, the file, and the song's NAME, which is what a
#: refusal is kept by (a pass offers a name, never a song's id).
Hand = Callable[[Connection, str, str], Awaitable[None]]

_TAKEN_OFF: dict[str, Hand] = {}
_PUT_ON_BY_HAND: dict[str, Hand] = {}


def on_hand(name: str, *, taken_off: Hand, put_on: Hand) -> None:
    """A feature says what a song taken off a file by hand, and one put on by hand, means to it.
    Keyed by `name`, so registering again (a test building the application twice) replaces."""
    _TAKEN_OFF[name] = taken_off
    _PUT_ON_BY_HAND[name] = put_on


_NAME_OF_SONG = "SELECT name FROM songs WHERE id = ?"
_FILES_OF_SONG = "SELECT asset_id FROM song_files WHERE song_id = ? ORDER BY asset_id"


async def _tell(
    hands: dict[str, Hand], connection: Connection, asset_id: str, song_id: str
) -> None:
    if not hands:
        return
    name = await _one(connection, _NAME_OF_SONG, (song_id,))
    if name is None:  # pragma: no cover (read inside the write that holds the row)
        return
    for hand in hands.values():
        await hand(connection, asset_id, str(name))


async def let_go_of(connection: Connection, song_id: str) -> list[str]:
    """A song about to be deleted: every file on it is taken off by hand, each hand told so. The
    files it held, for the caller to name to the search index. Call it in the delete's own write,
    before the row goes (the cascade cannot say the song's name afterwards)."""
    rows = list(await connection.execute_fetchall(_FILES_OF_SONG, (song_id,)))
    files = [str(row["asset_id"]) for row in rows]
    for asset_id in files:
        await _tell(_TAKEN_OFF, connection, asset_id, song_id)
    return files


# --- the statements -------------------------------------------------------------------------------

_SONG_OF_RECORDING = "SELECT id FROM songs WHERE recording_id = ?"
#: The song a name means: one with no recording first, then the oldest.
_SONG_CALLED = (
    "SELECT id, recording_id FROM songs WHERE name = ? COLLATE NOCASE"
    " ORDER BY recording_id IS NOT NULL, id LIMIT 1"
)
_GIVE_RECORDING = "UPDATE songs SET recording_id = ? WHERE id = ? AND recording_id IS NULL"
_MAKE_SONG = """
INSERT INTO songs
  (id, name, name_sort, recording_id, created_at, created_by_kind, created_by_via,
   created_by_user_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""
_SONG_OF_FILE = "SELECT song_id FROM song_files WHERE asset_id = ?"
#: The Music field of a file: a file whose field holds anything and no song is one an older
#: library left that way, and a writer that only fills a gap leaves it as it is.
_FIELD_OF_FILE = "SELECT music FROM assets WHERE id = ?"
#: `added_at` is named and bound, never defaulted: see `_ADDED_AT_COLUMNS` in the schema. The file
#: a name came from is kept only while it is there: one deleted between the spread's read and this
#: write leaves the name and no pointer, as its deletion would have a moment later.
_PUT_ON = """
INSERT INTO song_files (asset_id, song_id, source, from_asset_id, score, added_at)
VALUES (?, ?, ?, (SELECT id FROM assets WHERE id = ?), ?, ?)
ON CONFLICT(asset_id) DO NOTHING
"""
#: A file moved to another song by hand: a person's hand is where its song came from.
_MOVE_BY_HAND = """
UPDATE song_files SET song_id = ?, source = NULL, from_asset_id = NULL, score = NULL, added_at = ?
 WHERE asset_id = ?
"""
_TAKE_OFF = "DELETE FROM song_files WHERE asset_id = ? RETURNING song_id"
#: Sift's act taken back: only while the file still carries the song by Sift's hand (a song
#: somebody chose since is theirs) and it is the song the act put there: the one the act names by
#: id, or for an act from before songs had ids, the one still called what it wrote.
_TAKE_BACK = (
    "DELETE FROM song_files WHERE asset_id = :asset AND source IS NOT NULL"
    " AND (song_id = :song OR (:song IS NULL"
    " AND song_id IN (SELECT id FROM songs WHERE name = :name))) RETURNING song_id"
)
#: A Music field holding a name with no song under it, emptied by a person clearing it. The one
#: write of the field outside the triggers, and it is here, in the field's one door.
_EMPTY_THE_FIELD = "UPDATE assets SET music = NULL WHERE id = ? AND music IS NOT NULL"


async def _one(connection: Connection, sql: str, params: tuple[object, ...]) -> object | None:
    rows = list(await connection.execute_fetchall(sql, params))
    return rows[0][0] if rows else None


def _now() -> int:
    return int(time.time())


async def song_called(
    connection: Connection, name: str, *, made: Maker, recording_id: str | None = None
) -> str | None:
    """The id of the song a name (and a recording, where one is known) means, made where none
    does. None for a name that cleans to nothing. See the module's note on which song a name means.
    """
    cleaned = cleaned_song(name)
    if not cleaned:
        return None
    if recording_id:
        found = await _one(connection, _SONG_OF_RECORDING, (recording_id,))
        if found is not None:
            return str(found)
    rows = list(await connection.execute_fetchall(_SONG_CALLED, (cleaned,)))
    if rows:
        song_id, carried = str(rows[0]["id"]), rows[0]["recording_id"]
        if not recording_id:
            return song_id
        if carried is None:
            await connection.execute(_GIVE_RECORDING, (recording_id, song_id))
            return song_id
    song_id = new_id()
    await connection.execute(
        _MAKE_SONG,
        (
            song_id,
            cleaned,
            sort_key(cleaned),
            recording_id or None,
            _now(),
            made.kind,
            made.via,
            made.user_id,
        ),
    )
    return song_id


async def song_of_recording(connection: Connection, recording_id: str) -> str | None:
    """The song that is this AcoustID recording, or None: one recording is one song."""
    found = await _one(connection, _SONG_OF_RECORDING, (recording_id,))
    return None if found is None else str(found)


async def song_of_file(connection: Connection, asset_id: str) -> str | None:
    """The song a file carries, or None."""
    found = await _one(connection, _SONG_OF_FILE, (asset_id,))
    return None if found is None else str(found)


async def name_song_on(
    connection: Connection,
    asset_id: str,
    name: str,
    *,
    source: str,
    recording_id: str | None = None,
    from_asset_id: str | None = None,
    score: float | None = None,
) -> str | None:
    """One of Sift's three writers puts a song on a file that carries none. The song where it
    landed, None where the file already has a song or a name of its own.

    Fills only a gap, for the reason the download address's seed does: the same link dropped again
    months later still finishes, and a finish that replaced a song somebody chose would silently
    undo their choice. A name carried from another file joins THAT file's song,
    whatever it is called by then.
    """
    if source not in SOURCES:
        raise ValueError(f"{source!r} is not a source of a song ({', '.join(SOURCES)})")
    # ONE TEST, the field: a file that carries a song has its song's name there (the triggers
    # keep it so), and a field holding anything else at all (even spaces, which an older library
    # can) is left as it is, as the field's own seed always left it. Only an empty field is a gap.
    if await _one(connection, _FIELD_OF_FILE, (asset_id,)) is not None:
        return None
    song_id = None
    if source == SOURCE_SHARED and from_asset_id is not None:
        song_id = await song_of_file(connection, from_asset_id)
    if song_id is None:
        song_id = await song_called(
            connection, name, recording_id=recording_id, made=_MADE_BY_SOURCE[source]
        )
    if song_id is None:
        return None
    cursor = await connection.execute(
        _PUT_ON, (asset_id, song_id, source, from_asset_id, score, _now())
    )
    return song_id if cursor.rowcount else None


async def take_back(
    connection: Connection, asset_id: str, name: str, *, song_id: str | None = None
) -> bool:
    """Take a song one of Sift's writers put on a file back off it, only while the file still
    carries it that way. `song_id` is the song the act put there, where it says; renamed since, it
    is still that song. Whether it went. See `_TAKE_BACK`."""
    rows = list(
        await connection.execute_fetchall(
            _TAKE_BACK, {"asset": asset_id, "song": song_id, "name": cleaned_song(name)}
        )
    )
    return bool(rows)


@dataclass(frozen=True, slots=True)
class Chosen:
    """What a person's choice of a file's song changed: the song before and the song after."""

    before: str | None
    after: str | None

    @property
    def changed(self) -> bool:
        return self.before != self.after


async def choose(connection: Connection, asset_id: str, name: str | None, *, made: Maker) -> Chosen:
    """A person sets a file's song by its name, or takes it off with nothing. What it changed.

    The song the name means is found or made (`song_called`); the file moves onto it by hand, so
    the membership says no source. A file already on that song is left as it is: choosing what is
    there changed nothing.
    """
    before = await song_of_file(connection, asset_id)
    cleaned = cleaned_song(name or "")
    if not cleaned:
        if before is not None:
            await _tell(_TAKEN_OFF, connection, asset_id, before)
            await connection.execute_fetchall(_TAKE_OFF, (asset_id,))
        else:
            await connection.execute(_EMPTY_THE_FIELD, (asset_id,))
        return Chosen(before=before, after=None)
    after = await song_called(connection, cleaned, made=made)
    if after is None or after == before:
        return Chosen(before=before, after=before)
    if before is None:
        await connection.execute(_PUT_ON, (asset_id, after, None, None, None, _now()))
    else:
        await _tell(_TAKEN_OFF, connection, asset_id, before)
        await connection.execute(_MOVE_BY_HAND, (after, _now(), asset_id))
    await _tell(_PUT_ON_BY_HAND, connection, asset_id, after)
    return Chosen(before=before, after=after)


async def put_on_by_hand(connection: Connection, asset_id: str, song_id: str) -> bool:
    """A person puts a file on a song from the song's page. Whether it moved: a file already on
    that song stays as it was, and a file on another song moves, as a choice in its Music field
    would move it."""
    before = await song_of_file(connection, asset_id)
    if before == song_id:
        return False
    if before is None:
        await connection.execute(_PUT_ON, (asset_id, song_id, None, None, None, _now()))
    else:
        await _tell(_TAKEN_OFF, connection, asset_id, before)
        await connection.execute(_MOVE_BY_HAND, (song_id, _now(), asset_id))
    await _tell(_PUT_ON_BY_HAND, connection, asset_id, song_id)
    return True


async def take_off(connection: Connection, asset_id: str, song_id: str) -> bool:
    """A person takes a file off a song, and every hand is told. Whether it was on it."""
    if await song_of_file(connection, asset_id) != song_id:
        return False
    await _tell(_TAKEN_OFF, connection, asset_id, song_id)
    rows = list(
        await connection.execute_fetchall(
            "DELETE FROM song_files WHERE asset_id = ? AND song_id = ? RETURNING asset_id",
            (asset_id, song_id),
        )
    )
    return bool(rows)


# --- a song's artists --------------------------------------------------------------------------
#
# The artists a song credits, in order, as rows of their own (the note over `songs` in the schema).
# Two writers, and both come through here: AcoustID's answer, which fills a song's credits only
# while it has none (a person's own list is never rewritten by a later answer, for the reason a
# file's song is never replaced by one), and a person's own hand, which sets the whole list. An
# artist credited by nothing any more is deleted in the same write, so the artists are exactly
# the names some song credits.

#: How a credit came to be on a song, as `song_artists.source` holds it. A person's hand is NULL.
CREDIT_FROM_ACOUSTID = "acoustid"
#: The artists a song arrived with from another install.
CREDIT_FROM_SWAP = "swap"
CREDIT_SOURCES = (CREDIT_FROM_ACOUSTID, CREDIT_FROM_SWAP)

#: How AcoustID's answer joins the artists of one recording (`acoustid.answer_of`), which is what
#: `music_lookups.artists` keeps. A name holding this text is split by it: the kept answer cannot
#: say otherwise, and the song's page corrects it by hand.
ARTISTS_JOINED_BY = ", "

_ARTIST_CALLED = "SELECT id FROM artists WHERE name = ? COLLATE NOCASE"
_MAKE_ARTIST = """
INSERT INTO artists
  (id, name, name_sort, created_at, created_by_kind, created_by_via, created_by_user_id)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""
_CREDITS_OF = """
SELECT a.id AS id, a.name AS name, sa.source AS source
  FROM song_artists sa
  JOIN artists a ON a.id = sa.artist_id
 WHERE sa.song_id = ?
 ORDER BY sa.position, a.id
"""
_DROP_CREDITS = "DELETE FROM song_artists WHERE song_id = ? RETURNING artist_id"
_CREDIT = """
INSERT INTO song_artists (song_id, artist_id, position, source, added_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(song_id, artist_id) DO NOTHING
"""
#: An artist no song credits any more. Asked of the artists a write just took a credit from.
_FORGET_UNCREDITED = (
    "DELETE FROM artists WHERE id = ?"
    " AND NOT EXISTS (SELECT 1 FROM song_artists sa WHERE sa.artist_id = artists.id)"
)
_RENAME_ARTIST = "UPDATE artists SET name = ?, name_sort = ? WHERE id = ?"
_NAME_OF_ARTIST = "SELECT name FROM artists WHERE id = ?"
_SONGS_OF_ARTIST = "SELECT song_id FROM song_artists WHERE artist_id = ? ORDER BY song_id"
#: An artist folded into another of the same name: every credit moves, unless the song already
#: credits the one kept, where the credit going is simply dropped (the second statement).
_MOVE_CREDITS = (
    "UPDATE song_artists SET artist_id = :into WHERE artist_id = :going"
    " AND NOT EXISTS (SELECT 1 FROM song_artists k"
    " WHERE k.song_id = song_artists.song_id AND k.artist_id = :into)"
)
_DROP_ARTIST = "DELETE FROM artists WHERE id = ?"


@dataclass(frozen=True, slots=True)
class Credit:
    """One artist a song credits: the artist's id and name."""

    id: str
    name: str


def split_artists(credit: str | None) -> list[str]:
    """The artists one kept answer names, in its order, each cleaned, a name said twice once."""
    found: list[str] = []
    seen: set[str] = set()
    for one in (credit or "").split(ARTISTS_JOINED_BY):
        cleaned = cleaned_song(one)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            found.append(cleaned)
    return found


async def artist_called(connection: Connection, name: str, *, made: Maker) -> str | None:
    """The id of the artist of this name (case aside), made where there is none. None for a name
    that cleans to nothing."""
    cleaned = cleaned_song(name)
    if not cleaned:
        return None
    found = await _one(connection, _ARTIST_CALLED, (cleaned,))
    if found is not None:
        return str(found)
    artist_id = new_id()
    await connection.execute(
        _MAKE_ARTIST,
        (artist_id, cleaned, sort_key(cleaned), _now(), made.kind, made.via, made.user_id),
    )
    return artist_id


async def credits_of(connection: Connection, song_id: str) -> list[Credit]:
    """The artists one song credits, in order."""
    rows = await connection.execute_fetchall(_CREDITS_OF, (song_id,))
    return [Credit(id=str(row["id"]), name=str(row["name"])) for row in rows]


async def _forget_uncredited(connection: Connection, artist_ids: list[str]) -> None:
    for artist_id in dict.fromkeys(artist_ids):
        await connection.execute(_FORGET_UNCREDITED, (artist_id,))


#: Every artist no song credits: what a song deleted or merged away leaves behind (the cascade
#: takes its credits and cannot say whose they were). The artists are a few hundred rows.
_FORGET_EVERY_UNCREDITED = (
    "DELETE FROM artists"
    " WHERE NOT EXISTS (SELECT 1 FROM song_artists sa WHERE sa.artist_id = artists.id)"
)


async def forget_uncredited_artists(connection: Connection) -> None:
    """Delete every artist no song credits. Called in the write that deleted or merged songs."""
    await connection.execute(_FORGET_EVERY_UNCREDITED)


async def credit(
    connection: Connection, song_id: str, names: list[str], *, made: Maker, source: str | None
) -> tuple[list[Credit], list[Credit]]:
    """Set a song's credits to these names, in this order: the credits before and after.

    Each name finds its artist or makes one (`artist_called`); a name given twice is credited
    once, at its first place. An artist the old list credited and the new one does not, and that
    no other song credits, goes. `source` is how the credits came (`CREDIT_FROM_ACOUSTID`, or None
    for a person's own hand).
    """
    before = await credits_of(connection, song_id)
    dropped = [str(row[0]) for row in await connection.execute_fetchall(_DROP_CREDITS, (song_id,))]
    position = 0
    now = _now()
    for name in names:
        artist_id = await artist_called(connection, name, made=made)
        if artist_id is None:
            continue
        cursor = await connection.execute(_CREDIT, (song_id, artist_id, position, source, now))
        position += int(bool(cursor.rowcount))
    await _forget_uncredited(connection, dropped)
    return before, await credits_of(connection, song_id)


async def credit_where_none(
    connection: Connection, song_id: str, names: list[str], *, source: str
) -> bool:
    """One of Sift's writers credits these artists, in this order, on a song that credits none
    yet. Whether any were written: a song with credits already (a person's list, or an earlier
    writer's) keeps them, as a file's song is never replaced by a later answer, and so does a song
    the writer names no artist for. `source` is one of `CREDIT_SOURCES`, and makes the artists it
    has to make as the lookup or the swap."""
    if source not in CREDIT_SOURCES:
        raise ValueError(f"{source!r} is not a source of a credit ({', '.join(CREDIT_SOURCES)})")
    if not names or await credits_of(connection, song_id):
        return False
    made = BY_LOOKUP if source == CREDIT_FROM_ACOUSTID else BY_SWAP
    _before, after = await credit(connection, song_id, names, made=made, source=source)
    return bool(after)


async def credit_from_answer(connection: Connection, song_id: str, artists: str | None) -> bool:
    """AcoustID's artists, as a kept answer joins them (`split_artists`), on a song that credits
    none yet. See `credit_where_none`."""
    return await credit_where_none(
        connection, song_id, split_artists(artists), source=CREDIT_FROM_ACOUSTID
    )


_ARTIST_COUNT = "SELECT COUNT(*) AS n FROM artists"


async def _artist_count(connection: Connection) -> int:
    rows = list(await connection.execute_fetchall(_ARTIST_COUNT))
    return int(rows[0]["n"])


async def credit_kept_answers(
    connection: Connection, answers: list[tuple[str, str | None]]
) -> dict[str, int]:
    """The music feature's one-time step: the artists of every answer AcoustID already gave, as
    `(recording, artists joined)`, credited on the song that is that recording where it credits
    nobody yet. Answers the counts (songs credited, artists made), which it logs; one History line
    for the library where anything was credited, never one per song. Safe twice: a song credited
    already is left as it is, so a second run credits nothing and says nothing.
    """
    made_before = await _artist_count(connection)
    credited: list[str] = []
    for recording_id, artists in answers:
        song_id = await song_of_recording(connection, recording_id)
        if song_id is not None and await credit_from_answer(connection, song_id, artists):
            credited.append(song_id)
    made = await _artist_count(connection) - made_before
    counts = {"songs": len(credited), "artists": made}
    if credited and await table_exists(connection, "workbench_decisions"):
        first = credited[0]
        await record_event(
            connection,
            actor=Actor.sift(VIA_UPDATE),
            verb="added",
            subject=Subject(
                kind="song", id=first, name=str(await _one(connection, _NAME_OF_SONG, (first,)))
            ),
            count=len(credited),
            payload=json.dumps({"credited": len(credited), "artists": made}),
        )
    log.info("songs.artists_credited", **counts)
    return counts


@dataclass(frozen=True, slots=True)
class ArtistRenamed:
    """What renaming an artist changed: its name before, the artist that holds the name now (itself,
    or the artist of that name it was folded into), and every song crediting it."""

    before: str
    into: str
    songs: tuple[str, ...]


async def rename_artist(connection: Connection, artist_id: str, name: str) -> ArtistRenamed | None:
    """Rename an artist everywhere it is credited. A name another artist already has (case aside)
    folds this one into that one: two artists of one name would be one artist twice. None where
    there is no such artist or the name cleans to nothing; the songs empty where nothing changed.
    """
    cleaned = cleaned_song(name)
    was = await _one(connection, _NAME_OF_ARTIST, (artist_id,))
    if was is None or not cleaned:
        return None
    songs_of = [
        str(row[0]) for row in await connection.execute_fetchall(_SONGS_OF_ARTIST, (artist_id,))
    ]
    if str(was) == cleaned:
        return ArtistRenamed(before=str(was), into=artist_id, songs=())
    other = await _one(connection, _ARTIST_CALLED, (cleaned,))
    if other is None or str(other) == artist_id:
        await connection.execute(_RENAME_ARTIST, (cleaned, sort_key(cleaned), artist_id))
        return ArtistRenamed(before=str(was), into=artist_id, songs=tuple(songs_of))
    into = str(other)
    await connection.execute(_MOVE_CREDITS, {"into": into, "going": artist_id})
    await connection.execute(_DROP_ARTIST, (artist_id,))
    return ArtistRenamed(before=str(was), into=into, songs=tuple(songs_of))


# --- the triggers that keep the Music field equal to the song ----------------------------------

#: Every trigger here is named with this, so `keep_true` can find its own and only its own.
_PREFIX = "song_music_"

#: The name the file's new song is called, for the two triggers a membership fires.
_NAME_OF_NEW = "(SELECT s.name FROM songs s WHERE s.id = NEW.song_id)"

_TRIGGER = "CREATE TRIGGER IF NOT EXISTS {{NAME}} AFTER {{EVENT}}{{WHEN}} BEGIN {{BODY}}; END"
_DROP_TRIGGER = "DROP TRIGGER IF EXISTS {{NAME}}"

#: A file takes its new song's name, where its field does not say it already.
_TAKES_THE_NAME = splice(
    "UPDATE assets SET music = {{NAME}} WHERE id = NEW.asset_id AND music IS NOT {{NAME}}",
    NAME=_NAME_OF_NEW,
)


def _trigger(name: str, event: str, body: str, when: str = "") -> tuple[str, str]:
    return _PREFIX + name, splice(
        _TRIGGER,
        NAME=_PREFIX + name,
        EVENT=event,
        WHEN=(" WHEN " + when) if when else "",
        BODY=body,
    )


TRIGGERS: dict[str, str] = dict(
    (
        # A file put on a song takes its name.
        _trigger("filed", "INSERT ON song_files", _TAKES_THE_NAME),
        # A file moved to another song (by hand, or by a merge) takes the other one's name.
        _trigger(
            "moved",
            "UPDATE OF song_id ON song_files",
            _TAKES_THE_NAME,
            when="OLD.song_id IS NOT NEW.song_id",
        ),
        # A file taken off its song, or its song deleted (the cascade), has the field emptied.
        # Under a file's own deletion the row is already gone and this matches nothing.
        _trigger(
            "unfiled",
            "DELETE ON song_files",
            "UPDATE assets SET music = NULL WHERE id = OLD.asset_id AND music IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM song_files f WHERE f.asset_id = OLD.asset_id)",
        ),
        # A song renamed renames every file on it.
        _trigger(
            "renamed",
            "UPDATE OF name ON songs",
            "UPDATE assets SET music = NEW.name"
            " WHERE id IN (SELECT f.asset_id FROM song_files f WHERE f.song_id = NEW.id)"
            " AND music IS NOT NEW.name",
            when="OLD.name IS NOT NEW.name",
        ),
    )
)

_TRIGGERS_PRESENT = (
    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND substr(name, 1, ?) = ?"
)


def _normal(ddl: str) -> str:
    """A trigger's text without the IF NOT EXISTS the engine drops, spacing folded."""
    return " ".join(ddl.replace("IF NOT EXISTS ", "").split())


async def start(connection: Connection) -> None:
    """The triggers, for a catalog made fresh or brought to version 81."""
    for ddl in TRIGGERS.values():
        await connection.execute(ddl)


async def keep_true(connection: Connection) -> None:
    """Every boot: the triggers say what this build says. Cheap when nothing is wrong (one read of
    the schema). Guarded on the tables, because the schema tests bring a catalog up alone."""
    for table in ("songs", "song_files", "assets"):
        if not await table_exists(connection, table):
            return
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT, (len(_PREFIX), _PREFIX))
    present = {str(row[0]): _normal(str(row[1])) for row in rows}
    wanted = {name: _normal(ddl) for name, ddl in TRIGGERS.items()}
    repaired = sorted(name for name in wanted if present.get(name) != wanted[name])
    gone = sorted(name for name in present if name not in wanted)
    if not repaired and not gone:
        return
    log.warning("songs.triggers_repaired", repaired=repaired, dropped=gone)
    for name in (*gone, *repaired):
        await connection.execute(splice(_DROP_TRIGGER, NAME=name))
    for name in repaired:
        await connection.execute(TRIGGERS[name])


register_schema_invariant("catalog_song_music", keep_true)


# --- catalog version 81: every song a file already carries, moved onto a row -------------------

#: Every file whose Music field holds a name, with where Sift's name came from where the music
#: feature recorded it. Read once by the step.
_NAMED_FILES = """
SELECT a.id AS asset_id, a.music AS music, n.song AS noted, n.source AS source,
       n.from_asset_id AS from_asset_id, n.recording_id AS recording_id, n.score AS score,
       n.named_at AS named_at
  FROM assets a
  LEFT JOIN music_names n ON n.asset_id = a.id
 WHERE a.music IS NOT NULL AND TRIM(a.music) <> ''
 ORDER BY a.id
"""
#: The same where the music feature never recorded anything (no `music_names` table).
_NAMED_FILES_ALONE = """
SELECT a.id AS asset_id, a.music AS music, NULL AS noted, NULL AS source,
       NULL AS from_asset_id, NULL AS recording_id, NULL AS score, NULL AS named_at
  FROM assets a
 WHERE a.music IS NOT NULL AND TRIM(a.music) <> ''
 ORDER BY a.id
"""
_ANY_SONG = "SELECT 1 FROM songs LIMIT 1"
_FILE_IS_THERE = "SELECT 1 FROM assets WHERE id = ?"


@dataclass
class _Group:
    """The files the step puts on one song, and what decides the song's row."""

    recording_id: str | None
    names: Counter[str]
    files: list[tuple[str, str | None, str | None, float | None, int | None, str]]
    sources: set[str]

    def name(self) -> str:
        """The spelling most of its files carry; the first such for a tie."""
        return self.names.most_common(1)[0][0]

    def maker(self) -> Maker:
        if self.recording_id is not None or SOURCE_ACOUSTID in self.sources:
            return BY_LOOKUP
        if SOURCE_SITE in self.sources:
            return BY_DOWNLOAD
        return UNSAID

    def made_at(self) -> int:
        moments = [member[4] for member in self.files if member[4] is not None]
        return min(moments) if moments else _now()


async def move_named_songs(connection: Connection) -> dict[str, int]:
    """Catalog version 81: every song a file carries, moved onto a row of `songs`.

    Every file with a name in its Music field is put on a song: grouped by the AcoustID recording
    where the music feature recorded one (and only while the field still says what it recorded,
    as `identity.music_provenance` reads it: a name typed over Sift's is the typer's), otherwise by
    the name, case and spacing folded, and a name group joins the recording's song of the same
    name. Where the feature recorded the name's source, the membership keeps it, with the file it
    came from and the score; every other name was typed or arrived before anything recorded it, and
    reads as a person's hand. Each file's field is then the song's name, which the triggers
    write: a file whose field was spelled differently from most of its song's files is respelled.

    One History line for the library, never one per file. Idempotent: a library that has a song
    already has been through this. Answers the counts, which it logs.
    """
    counts = {"songs": 0, "files": 0, "respelled": 0}
    if list(await connection.execute_fetchall(_ANY_SONG)):
        log.info("songs.moved", **counts)
        return counts
    noted = await table_exists(connection, "music_names")
    rows = list(await connection.execute_fetchall(_NAMED_FILES if noted else _NAMED_FILES_ALONE))
    by_recording: dict[str, _Group] = {}
    by_name: dict[str, _Group] = {}
    for row in rows:
        name = cleaned_song(str(row["music"]))
        if not name:
            continue
        held = row["noted"] is not None and cleaned_song(str(row["noted"])) == name
        source = str(row["source"]) if held and row["source"] in SOURCES else None
        recording = str(row["recording_id"]) if held and row["recording_id"] else None
        member = (
            str(row["asset_id"]),
            source,
            str(row["from_asset_id"]) if held and row["from_asset_id"] else None,
            float(row["score"]) if held and row["score"] is not None else None,
            int(row["named_at"]) if held and row["named_at"] is not None else None,
            str(row["music"]),
        )
        if recording is not None:
            group = by_recording.setdefault(recording, _Group(recording, Counter(), [], set()))
        else:
            group = by_name.setdefault(name.casefold(), _Group(None, Counter(), [], set()))
        group.names[name] += 1
        group.files.append(member)
        if source is not None:
            group.sources.add(source)
    # A name group joins the recording's song that is called the same, as a later name would.
    recording_called = {group.name().casefold(): group for group in by_recording.values()}
    groups = list(by_recording.values())
    for folded, group in by_name.items():
        joined = recording_called.get(folded)
        if joined is None:
            groups.append(group)
            continue
        joined.names.update(group.names)
        joined.files.extend(group.files)
        joined.sources |= group.sources
    first: tuple[str, str] | None = None
    for group in groups:
        song_id = new_id()
        made = group.maker()
        name = group.name()
        await connection.execute(
            _MAKE_SONG,
            (
                song_id,
                name,
                sort_key(name),
                group.recording_id,
                group.made_at(),
                made.kind,
                made.via,
                made.user_id,
            ),
        )
        first = first or (song_id, name)
        counts["songs"] += 1
        for asset_id, source, from_asset_id, score, at, field in group.files:
            # A file the name came from that has since gone leaves the name, and no pointer.
            if from_asset_id is not None and not list(
                await connection.execute_fetchall(_FILE_IS_THERE, (from_asset_id,))
            ):
                from_asset_id = None
            await connection.execute(_PUT_ON, (asset_id, song_id, source, from_asset_id, score, at))
            counts["files"] += 1
            # The trigger writes the song's name into the field; a field spelled otherwise moved.
            counts["respelled"] += int(field != name)
    if first is not None and await table_exists(connection, "workbench_decisions"):
        await record_event(
            connection,
            actor=Actor.sift(VIA_UPDATE),
            verb="added",
            subject=Subject(kind="song", id=first[0], name=first[1]),
            count=counts["files"],
            payload=json.dumps({"songs": counts["songs"], "files": counts["files"]}),
        )
    log.info("songs.moved", **counts)
    return counts
