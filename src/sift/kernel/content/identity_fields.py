# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fields a person or an import writes on a file: its title, dates, links and music."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.content import songs
from sift.kernel.content.identity_store import StoreCore
from sift.kernel.db import Connection, Database, Row, in_clause, point_read
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, Reversal, record_event
from sift.kernel.urls import cleaned_for_record
from sift.kernel.vocabulary import (
    VIA_DOWNLOAD,
    VIA_FINGERPRINT,
    VIA_MUSIC_LOOKUP,
    Subject,
)

_SET_TITLE = "UPDATE assets SET title = ? WHERE id = ?"

_SET_DOWNLOAD_URL = "UPDATE assets SET download_url = ? WHERE id = ?"

#: `AND download_url IS NULL` is the whole rule, written ONCE: `schema.py`'s upgrade step reads it.
SEED_DOWNLOAD_URL = "UPDATE assets SET download_url = ? WHERE id = ? AND download_url IS NULL"

_SET_RELEASE_DATE = "UPDATE assets SET release_date = ? WHERE id = ?"

#: One statement per field: the probe writes every column it knows, so one field through it would
#: blank a dozen.
_SET_DETAILS = "UPDATE assets SET details = ? WHERE id = ?"

_SET_PRODUCTION_DATE = "UPDATE assets SET production_date = ? WHERE id = ?"

_SET_SITE_CODE = "UPDATE assets SET site_code = ? WHERE id = ?"

# THE MUSIC FIELD IS NOT WRITTEN HERE: the database keeps it equal to the name of the file's song
# (`song_files`), and every writer goes through `kernel/content/songs.py` (`test_songs` holds it).

#: Where a song's name on a file came from, when Sift put it there; a typed name has no word.
MUSIC_FROM_SITE = "site"

MUSIC_FROM_ACOUSTID = "acoustid"

MUSIC_SHARED = "shared"

MUSIC_SOURCES = (MUSIC_FROM_SITE, MUSIC_FROM_ACOUSTID, MUSIC_SHARED)

#: Which task the ledger says named the song; a shared name (absent) follows the fingerprint task.
_SONG_VIAS: Mapping[str, str] = {
    MUSIC_FROM_SITE: VIA_DOWNLOAD,
    MUSIC_FROM_ACOUSTID: VIA_MUSIC_LOOKUP,
}

cleaned_song = songs.cleaned_song

#: A file's links, replaced as a set: only rows no longer named go, so `created_at` survives.
_DELETE_ASSET_LINKS_NOT_IN = """
DELETE FROM asset_links
 WHERE asset_id = :asset_id
   AND url NOT IN (SELECT value FROM json_each(:urls))
"""

_ADD_ASSET_LINK = """
INSERT INTO asset_links (id, asset_id, url, label, created_at)
VALUES (?, ?, ?, NULL, ?)
ON CONFLICT(asset_id, url) DO NOTHING
"""

_ASSET_LINKS = "SELECT url FROM asset_links WHERE asset_id = ? ORDER BY id"


class Fields(StoreCore):
    """The fields written on a file."""

    async def set_title(self, asset_id: str, title: str | None) -> None:
        """Name a file other than its filename, the only writer of `title`; blank is NULL."""
        cleaned = (title or "").strip()
        await self._write_record(_SET_TITLE, (cleaned or None, asset_id))

    async def set_download_url(self, asset_id: str, url: str | None) -> None:
        """Write where a file came from, as a person corrected it; blank clears it. Cleaned HERE,
        the column's only writer, so a typed address and a seeded one are stored alike."""
        cleaned = cleaned_for_record(url or "")
        await self._write_record(_SET_DOWNLOAD_URL, (cleaned or None, asset_id))

    async def set_release_date(self, asset_id: str, date: str | None) -> None:
        """Write when what is in a file came out; blank clears it. Trusted to be a date: only a
        date control and a stash-box's date field reach here."""
        cleaned = (date or "").strip()
        await self._write_record(_SET_RELEASE_DATE, (cleaned or None, asset_id))

    async def set_details(self, asset_id: str, details: str | None) -> None:
        """What a release is about, in the words of whoever put it out. Blank clears it."""
        cleaned = (details or "").strip()
        await self._write_record(_SET_DETAILS, (cleaned or None, asset_id))

    async def set_production_date(self, asset_id: str, date: str | None) -> None:
        """When it was filmed; blank clears it. A date, as for `set_release_date`."""
        cleaned = (date or "").strip()
        await self._write_record(_SET_PRODUCTION_DATE, (cleaned or None, asset_id))

    async def set_site_code(self, asset_id: str, code: str | None) -> None:
        """The reference the Site that released it files it under. Blank clears it."""
        cleaned = (code or "").strip()
        await self._write_record(_SET_SITE_CODE, (cleaned or None, asset_id))

    async def set_music(
        self, asset_id: str, music: str | None, *, by_user: str | None = None
    ) -> songs.Chosen:
        """The song this file carries, as somebody typed it; blank takes it off. The name finds
        or makes the song (`songs.choose`), and what changed is answered for the caller to say."""
        async with self._db.write() as connection:
            chosen = await songs.choose(connection, asset_id, music, made=songs.by_user(by_user))
            if chosen.changed:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
            return chosen

    async def set_links(self, asset_id: str, urls: Sequence[str]) -> None:
        """Where a release can be found, replaced as a whole set and cleaned as `download_url` is.

        NOT `download_url`, never folded together: that is the one address SIFT fetched this copy
        from; these are a stash-box's several statements about the release.
        """
        cleaned: list[str] = []
        for one in urls:
            kept = cleaned_for_record(str(one or ""))
            if kept and kept not in cleaned:
                cleaned.append(kept)
        now = int(time.time())
        async with self._db.write() as connection:
            await connection.execute(
                _DELETE_ASSET_LINKS_NOT_IN, {"asset_id": asset_id, "urls": json.dumps(cleaned)}
            )
            for one in cleaned:
                await connection.execute(_ADD_ASSET_LINK, (new_id(), asset_id, one, now))
            announce(await who_may_see_a_file(connection), About.LIBRARY)

    async def links_of(self, asset_id: str) -> list[str]:
        """A file's links, oldest first."""
        rows = await self._db.fetch_all(_ASSET_LINKS, (asset_id,))
        return [str(row["url"]) for row in rows]

    async def seed_download_url(self, asset_id: str, url: str) -> None:
        """Record where a file was fetched from, only if nothing is there yet, so a re-dropped
        link never undoes a correction somebody typed."""
        cleaned = cleaned_for_record(url)
        if cleaned:
            await self._write_record(SEED_DOWNLOAD_URL, (cleaned, asset_id))

    async def seed_music(
        self,
        asset_id: str,
        music: str,
        *,
        page: Object | None = None,
        source: str = MUSIC_FROM_SITE,
        from_asset: Object | None = None,
        facts: Mapping[str, object] | None = None,
        receipt: Reversal | None = None,
    ) -> bool:
        """Record the song Sift found for this file, only where it carries none (a re-drop never
        undoes a choice), and whether it was written. See `seed_music_on`.

        The write and its History event are one transaction, the event only where a row changed.
        `source` decides the event's object: the Site's `page` (None if it has no row), nothing for
        `acoustid`, `from_asset` for `shared`. `facts` join the payload, the only place they
        survive; `receipt` makes the act undoable (a shared name's is).
        """
        async with self._db.write() as connection:
            written = await seed_music_on(
                connection,
                asset_id,
                music,
                page=page,
                source=source,
                from_asset=from_asset,
                facts=facts,
                receipt=receipt,
            )
            if written:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
            return written


async def seed_music_on(
    connection: Connection,
    asset_id: str,
    music: str,
    *,
    page: Object | None = None,
    source: str = MUSIC_FROM_SITE,
    from_asset: Object | None = None,
    facts: Mapping[str, object] | None = None,
    receipt: Reversal | None = None,
    recording_id: str | None = None,
    score: float | None = None,
) -> bool:
    """`ContentStore.seed_music` on the caller's connection, for a writer whose own rows (and
    refusal check, or an Undo could land between) share the transaction. The sources' rules are
    checked, not trusted: each becomes a line somebody reads.
    """
    if source not in MUSIC_SOURCES:
        raise ValueError(
            f"{source!r} is not a source of a song's name ({', '.join(MUSIC_SOURCES)})"
        )
    if (source == MUSIC_SHARED) != (from_asset is not None):
        raise ValueError("a shared song names the file it came from, and only a shared one does")
    if page is not None and source != MUSIC_FROM_SITE:
        raise ValueError("only a song named from a Site's page names the page")
    cleaned = cleaned_song(music)
    if not cleaned:
        return False
    song_id = await songs.name_song_on(
        connection,
        asset_id,
        cleaned,
        source=source,
        recording_id=recording_id,
        from_asset_id=from_asset.id if from_asset is not None else None,
        score=score,
    )
    if song_id is None:
        return False
    payload: dict[str, object] = {"song": cleaned, "song_id": song_id}
    if source != MUSIC_FROM_SITE:
        # No `source` reads as a Site's page, so a Site's name writes none.
        payload["source"] = source
    if from_asset is not None:
        payload["from"] = from_asset.id
    payload.update(facts or {})
    await record_event(
        connection,
        actor=Actor.sift(_SONG_VIAS.get(source, VIA_FINGERPRINT)),
        verb="song_named",
        subject=Subject(kind="asset", id=asset_id),
        object=page if source == MUSIC_FROM_SITE else from_asset,
        payload=json.dumps(payload),
        receipt=receipt,
    )
    return True


async def unseed_music_on(
    connection: Connection, asset_id: str, music: str, *, song_id: str | None = None
) -> bool:
    """Take a song Sift put on a file back off, only while it is still as Sift put it
    (`songs.take_back`); `song_id` is the song the act named, unchanged by a rename."""
    return await songs.take_back(connection, asset_id, music, song_id=song_id)


#: The newest standing (not undone) act that named a song on this file.
_SONG_NAMED_ON = (
    "SELECT d.id AS id, d.payload AS payload FROM workbench_decision_subjects s"
    " JOIN workbench_decisions d ON d.id = s.decision_id"
    " WHERE s.kind = 'asset' AND s.subject_id = ? AND d.verb = 'song_named'"
    " AND d.reversed_at IS NULL ORDER BY d.id DESC LIMIT 1"
)

#: Not one of `MUSIC_SOURCES`: nothing records a typed name.
MUSIC_TYPED = "typed"

_SONG_ON_FILE = point_read(
    "content.song_on_file",
    "SELECT f.song_id AS song_id, f.source AS source, f.from_asset_id AS from_asset_id,"
    " s.name AS name FROM song_files f JOIN songs s ON s.id = f.song_id WHERE f.asset_id = ?",
)


@dataclass(frozen=True, slots=True)
class MusicProvenance:
    """Where the song on a file came from: typed, a Site's page, AcoustID, or another file."""

    source: str
    from_asset_id: str | None = None
    #: The ledger row that named it: a shared name's act IS the receipt an Undo goes through.
    act_id: str | None = None
    #: The song whose page the Music field opens; None for a name with no song behind it.
    song_id: str | None = None


async def music_provenance(
    database: Database, asset_id: str, music: str | None
) -> MusicProvenance | None:
    """Where this file's CURRENT song came from, or None where it has none.

    Read off `song_files`, the ledger's act only for a shared name's receipt id; another name than
    the song's reads "typed". A name with no song behind it falls back to the ledger's act,
    believed only while the field still says what it wrote.
    """
    current = cleaned_song(music or "")
    if not current:
        return None
    carried = await database.fetch_one(_SONG_ON_FILE, (asset_id,))
    if carried is not None:
        song_id = str(carried["song_id"])
        if str(carried["name"]) != current or carried["source"] not in MUSIC_SOURCES:
            return MusicProvenance(source=MUSIC_TYPED, song_id=song_id)
        source = str(carried["source"])
        if source != MUSIC_SHARED:
            return MusicProvenance(source=source, song_id=song_id)
        act = await database.fetch_one(_SONG_NAMED_ON, (asset_id,))
        return MusicProvenance(
            source=source,
            from_asset_id=carried["from_asset_id"],
            act_id=None if act is None else str(act["id"]),
            song_id=song_id,
        )
    row = await database.fetch_one(_SONG_NAMED_ON, (asset_id,))
    held: Any = None
    if row is not None:
        with suppress(ValueError, TypeError):
            held = json.loads(str(row["payload"]))
    if not isinstance(held, dict) or held.get("song") != current:
        return MusicProvenance(source=MUSIC_TYPED)
    source = held.get("source") or MUSIC_FROM_SITE
    if source not in MUSIC_SOURCES:
        return MusicProvenance(source=MUSIC_TYPED)
    shared_from = held.get("from") if source == MUSIC_SHARED else None
    return MusicProvenance(
        source=str(source),
        from_asset_id=shared_from if isinstance(shared_from, str) else None,
        act_id=str(row["id"]) if row is not None else None,
    )


_SONGS_OF = (
    "SELECT id, music FROM assets WHERE id IN (?*) AND music IS NOT NULL AND TRIM(music) <> ''"
)

#: The two facts about the FILE that decide whether its sound is worth asking AcoustID about.
_SONG_AND_LENGTH = "SELECT music, duration_ms FROM assets WHERE id = ?"

_SONGS_AND_LENGTHS = "SELECT id, music, duration_ms FROM assets WHERE id IN (?*)"

_SONGS_AT_ONCE = 500


async def songs_of(database: Database, asset_ids: Sequence[str]) -> dict[str, str]:
    """The song each of these files carries, stripped. Unscoped (Sift asks, before writing a
    fact), and in the kernel because a feature may not read the files' own table."""
    if not asset_ids:
        return {}
    sql, params = in_clause(_SONGS_OF, list(asset_ids))
    rows = await database.fetch_all(sql, params)
    return {str(row["id"]): str(row["music"]).strip() for row in rows}


@dataclass(frozen=True, slots=True)
class SongAndLength:
    """What the file itself says about its sound: the song on it, if any, and its length."""

    music: str | None
    duration_ms: int | None


async def song_and_length(database: Database, asset_id: str) -> SongAndLength | None:
    """The song on this file and its length, or None where the file is not there. See `songs_of`
    for why this is asked of the kernel."""
    row = await database.fetch_one(_SONG_AND_LENGTH, (asset_id,))
    return None if row is None else _song_and_length(row)


async def songs_and_lengths(
    database: Database, asset_ids: Sequence[str]
) -> dict[str, SongAndLength]:
    """`song_and_length` for a page of files, by id; a file not there is absent."""
    wanted = list(dict.fromkeys(asset_ids))
    found: dict[str, SongAndLength] = {}
    for start in range(0, len(wanted), _SONGS_AT_ONCE):
        sql, params = in_clause(_SONGS_AND_LENGTHS, wanted[start : start + _SONGS_AT_ONCE])
        for row in await database.fetch_all(sql, params):
            found[str(row["id"])] = _song_and_length(row)
    return found


def _song_and_length(row: Row) -> SongAndLength:
    music = row["music"]
    duration = row["duration_ms"]
    return SongAndLength(
        music=None if music is None else str(music),
        duration_ms=None if duration is None else int(duration),
    )
