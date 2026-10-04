# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rows of what a Stash run could not import yet. See `schema` for the tables and why.

Everything here reads and writes this slice's own five tables and nothing else: which files here
carry a waiting row's fingerprints is the content store's question (`ContentStore.carriers_of`),
asked by the service with the keys read here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, announce_now
from sift.kernel.db import Database
from sift.kernel.sorting import sort_key

#: One scene or image as the run would have written it, in the run's own shape: `fields` in
#: Sift's field keys, `rating` out of ten, the two counts, its markers (for a scene) and the Stash
#: galleries it is in (for a picture), with the Stash database those galleries belong to.
Package = dict[str, Any]


@dataclass(frozen=True)
class WaitingFile:
    """One file Stash kept for a waiting row, as Stash knew it."""

    path: str
    #: Where that path is on this device by the read's folders, or None where no folder matched.
    here: str | None = None
    oshash: str | None = None
    phash: str | None = None
    size_bytes: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class Waiting:
    """One scene or image that waits for its file."""

    id: str
    kind: str
    stash_id: int
    read_at: int
    user_id: str | None
    label: str
    package: Package
    files: tuple[WaitingFile, ...] = ()


@dataclass(frozen=True)
class KeptGallery:
    """What a Stash gallery became here: its Photo Set, once it had one, and its pictures here."""

    name: str
    photo_set_id: str | None
    pictures: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class KeptGroup:
    """What a Stash group became here: its Collection, once it had one, and its scenes here."""

    name: str
    collection_id: str | None
    scenes: tuple[str, ...] = field(default_factory=tuple)


_FORGET_ALL = ("DELETE FROM stash_waiting", "DELETE FROM stash_waiting_entities")

_INSERT_WAITING = (
    "INSERT INTO stash_waiting (id, kind, stash_id, read_at, user_id, label, label_key, package)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_INSERT_FILE = (
    "INSERT INTO stash_waiting_files (waiting_id, position, path, here, oshash, phash,"
    " size_bytes, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

#: The table keeps Sift's own words for what waits (a file, a picture): a CHECK constraint is the
#: set of words Sift chose, never a stash-box's or Stash's, and the reader's words (scene, image)
#: stay on the reader's side of this store. Files sort before pictures, as they did.
_OUR_WORD = {"scene": "file", "image": "picture"}
_THEIR_WORD = {our: theirs for theirs, our in _OUR_WORD.items()}

_INSERT_ENTITY = "INSERT INTO stash_waiting_entities (kind, name, record) VALUES (?, ?, ?)"

_COUNT = "SELECT COUNT(*) AS n FROM stash_waiting"

_ANY = "SELECT EXISTS (SELECT 1 FROM stash_waiting) AS any_waiting"

#: Every waiting row's file identities, which is what the pass looks for among the files here.
_KEYS = (
    "SELECT waiting_id, path, here, oshash, phash, size_bytes, duration_ms"
    " FROM stash_waiting_files ORDER BY waiting_id, position"
)

_ROWS_BY_ID = "SELECT * FROM stash_waiting WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id"

_FILES_OF = (
    "SELECT * FROM stash_waiting_files WHERE waiting_id IN (SELECT value FROM json_each(?))"
    " ORDER BY waiting_id, position"
)

#: A page of what waits, in the order a person reads a list of things: scenes before pictures,
#: then by name, the id breaking a tie so a page never shifts under a reader.
_PAGE = "SELECT * FROM stash_waiting ORDER BY kind, label_key, id LIMIT ? OFFSET ?"

_ENTITY = "SELECT record FROM stash_waiting_entities WHERE kind = ? AND name = ?"

_FORGET_ENTITY = "DELETE FROM stash_waiting_entities WHERE kind = ? AND name = ?"

_FORGET = "DELETE FROM stash_waiting WHERE id = ?"

_GALLERY = (
    "SELECT name, photo_set_id, pictures FROM stash_galleries WHERE source = ? AND stash_id = ?"
)

_KEEP_GALLERY = """
INSERT INTO stash_galleries (source, stash_id, name, photo_set_id, pictures)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (source, stash_id) DO UPDATE SET
    name = excluded.name,
    photo_set_id = excluded.photo_set_id,
    pictures = excluded.pictures
"""

_GROUP = "SELECT name, collection_id, scenes FROM stash_groups WHERE source = ? AND stash_id = ?"

_KEEP_GROUP = """
INSERT INTO stash_groups (source, stash_id, name, collection_id, scenes)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (source, stash_id) DO UPDATE SET
    name = excluded.name,
    collection_id = excluded.collection_id,
    scenes = excluded.scenes
"""


def _waiting_from(row: Any, files: Sequence[WaitingFile]) -> Waiting:
    return Waiting(
        id=str(row["id"]),
        kind=_THEIR_WORD[str(row["kind"])],
        stash_id=int(row["stash_id"]),
        read_at=int(row["read_at"]),
        user_id=row["user_id"],
        label=str(row["label"]),
        package=json.loads(str(row["package"])),
        files=tuple(files),
    )


def _file_from(row: Any) -> WaitingFile:
    return WaitingFile(
        path=str(row["path"]),
        here=row["here"],
        oshash=row["oshash"],
        phash=row["phash"],
        size_bytes=row["size_bytes"],
        duration_ms=row["duration_ms"],
    )


class WaitingStore:
    """The five tables, read and written. One per application, held by the service."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def replace(
        self, rows: Sequence[Waiting], entities: Mapping[tuple[str, str], Mapping[str, Any]]
    ) -> None:
        """What waits, as the newest run found it: every earlier row replaced, in one transaction,
        so a second run leaves as many rows as the first and never twice as many."""
        waiting = [
            (
                one.id,
                _OUR_WORD[one.kind],
                one.stash_id,
                one.read_at,
                one.user_id,
                one.label,
                sort_key(one.label),
                json.dumps(one.package, sort_keys=True),
            )
            for one in rows
        ]
        files = [
            (
                one.id,
                position,
                stash_file.path,
                stash_file.here,
                stash_file.oshash,
                stash_file.phash,
                stash_file.size_bytes,
                stash_file.duration_ms,
            )
            for one in rows
            for position, stash_file in enumerate(one.files)
        ]
        records = [
            (kind, name, json.dumps(record, sort_keys=True))
            for (kind, name), record in entities.items()
        ]
        # Built before the writer is taken and written as three batches, so a library of tens of
        # thousands of waiting scenes holds the one writer for three statements, not for a
        # statement per row.
        async with self._db.write() as connection:
            for statement in _FORGET_ALL:
                await connection.execute(statement)
            await connection.executemany(_INSERT_WAITING, waiting)
            await connection.executemany(_INSERT_FILE, files)
            await connection.executemany(_INSERT_ENTITY, records)
            # Settings > From Stash lists what waits, and an open pane re-reads on the settings bell.
            announce(EVERY_ADMIN, About.SETTINGS)

    async def count(self) -> int:
        row = await self._db.fetch_one(_COUNT)
        return int(row["n"]) if row is not None else 0

    async def anything(self) -> bool:
        """Whether anything waits at all: the one read the pass makes when nothing does."""
        row = await self._db.fetch_one(_ANY)
        return bool(row is not None and row["any_waiting"])

    async def keys(self) -> list[tuple[str, WaitingFile]]:
        """Every waiting row's files, by the row they belong to. Through the lane: a Stash library
        can leave tens of thousands waiting, and this is read whole by a pass nobody waits on."""
        rows = await self._db.sweep_all(_KEYS, what="stash_waiting_keys")
        return [(str(row["waiting_id"]), _file_from(row)) for row in rows]

    async def rows(self, waiting_ids: Sequence[str]) -> list[Waiting]:
        """These rows whole, with their files; an id no longer waiting is left out."""
        if not waiting_ids:
            return []
        wanted = json.dumps(list(dict.fromkeys(waiting_ids)))
        files: dict[str, list[WaitingFile]] = {}
        for row in await self._db.fetch_all(_FILES_OF, (wanted,)):
            files.setdefault(str(row["waiting_id"]), []).append(_file_from(row))
        return [
            _waiting_from(row, files.get(str(row["id"]), []))
            for row in await self._db.fetch_all(_ROWS_BY_ID, (wanted,))
        ]

    async def page(self, offset: int, limit: int) -> tuple[int, list[Waiting]]:
        """How many wait, and one page of them with their files."""
        total = await self.count()
        found = await self._db.fetch_all(_PAGE, (limit, offset))
        ids = [str(row["id"]) for row in found]
        whole = {one.id: one for one in await self.rows(ids)}
        return total, [whole[one] for one in ids if one in whole]

    async def entity(self, kind: str, name: str) -> dict[str, Any] | None:
        """The waiting record of one person, Site or tag, or None when it does not wait."""
        row = await self._db.fetch_one(_ENTITY, (kind, name))
        return None if row is None else dict(json.loads(str(row["record"])))

    async def entity_landed(self, kind: str, name: str) -> None:
        """That record came across, so it waits no longer. An open Settings > From Stash re-reads."""
        await self._db.execute(_FORGET_ENTITY, (kind, name))
        announce_now(EVERY_ADMIN, About.SETTINGS)

    async def landed(self, waiting_id: str) -> None:
        """That row came across, so it waits no longer. Its files go with it. An open Settings >
        From Stash re-reads."""
        await self._db.execute(_FORGET, (waiting_id,))
        announce_now(EVERY_ADMIN, About.SETTINGS)

    async def gallery(self, source: str, stash_id: int) -> KeptGallery | None:
        row = await self._db.fetch_one(_GALLERY, (source, stash_id))
        if row is None:
            return None
        return KeptGallery(
            name=str(row["name"]),
            photo_set_id=row["photo_set_id"],
            pictures=tuple(json.loads(str(row["pictures"]))),
        )

    async def keep_gallery(self, source: str, stash_id: int, kept: KeptGallery) -> None:
        await self._db.execute(
            _KEEP_GALLERY,
            (source, stash_id, kept.name, kept.photo_set_id, json.dumps(list(kept.pictures))),
        )

    async def group(self, source: str, stash_id: int) -> KeptGroup | None:
        row = await self._db.fetch_one(_GROUP, (source, stash_id))
        if row is None:
            return None
        return KeptGroup(
            name=str(row["name"]),
            collection_id=row["collection_id"],
            scenes=tuple(json.loads(str(row["scenes"]))),
        )

    async def keep_group(self, source: str, stash_id: int, kept: KeptGroup) -> None:
        await self._db.execute(
            _KEEP_GROUP,
            (source, stash_id, kept.name, kept.collection_id, json.dumps(list(kept.scenes))),
        )
