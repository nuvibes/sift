# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the host offers: the files it chose, the facts about them that may travel, and their titles.

## The files are the host's own scoped read of what was chosen

A host chooses people, Sites, tags, collections, photo sets and single files, and at most one saved
filter. Each is turned into files by the SAME leaf the search grammar filters by (`people`, `sites`,
`tags`, `collections`, `photo_sets`, `assets` in `kernel/access/constraints.py`), and a saved filter
by the search feature's own parse of the query it keeps, then every one of them is read through
`Repository.visible_assets`, paged to the end. Nothing here decides who may see a file: the read
that draws the walls decides, so a swap cannot offer a file its host could not open on screen.

The read is made as the host with Hidden OPEN (`swap_reader`), whatever the session that asked had
unlocked: Hidden governs what a screen shows, never what a swap sends. Two of the kernel's rules on
top, applied inside that same read, and one of the swap's own:

- **Never a kept-local file.** "Kept local" means nothing about the file leaves this device, and
  that rule (the file's own switch, or a Site, person or tag above it kept local) is
  `constraints.KEPT_LOCAL_HERE`. It is read through the `enrichment` leaf, whose `local` value is
  that expression word for word (`constraints.py`, the `enrichment` predicate), so there is no
  second copy of it here.
- **Never a file this device cannot strip.** A format with no strip (`transfer.NEVER_SENT_MIMES`,
  empty today: HEIC and AVIF have theirs) is left out here rather than failed in the middle of a
  transfer, so the guest is never offered a file that cannot come.
- **Never a file marked "Do not swap".** The file's own mark, or a Site, person or tag above it
  marked, is `constraints.KEPT_FROM_SWAPS_HERE`, read through the `kept_from_swaps` leaf. A person
  marked is never named either: every file carrying them is left out, and a person with no file in
  the offer is not offered (below).

So a person or a file somebody hid goes in a swap unless "Do not swap" (or Kept local) keeps it.

The chosen things are a UNION: a person and a Site offer the person's files and the Site's files,
not only the files that are both. A saved filter is one more member of that union. The union is
read as two statements rather than one (the entities in one, the saved filter in its own)
because a saved filter carries more than a tree (its words, its folder scope) and folding one into
the other would be a filter nobody wrote.

## Facial fingerprints, with no files

The one kind that is not turned into files. A person chosen for their facial fingerprints is
offered with their names and their fingerprints and nothing else: the people the host's Sift can
recognize, for a guest that wants to recognize them too. They pass the same doors a person does:
the scoped read of people with Hidden open, and never a person marked "Do not swap" or "Do not
enrich" (nothing about them leaves this device). A person with no fingerprints to send (the faces
feature off, or nobody confirmed a face of theirs) is not offered, for the same reason a person
with no files is not: nothing of them would arrive.

The fingerprints go as numbers. When the guest's hello named a different face model, the face
pictures they were read from go beside them (`MAX_OFFER_PICTURES` bounds how many), because
numbers from one model mean nothing to the other and the picture is what lets the guest make its
own.

## What travels, and what is made up

Per file: the hashes that let the guest tell whether it already has it, its kind and shape, the
Site and Username it was filed under (never an address), which offered people are on it, and a
title Sift makes up, "Ava Example, clip 14 of 38": the file's first offered person and its place
in that person's offered files. A file under no offered person is titled by its Site
("Northlight Media, clip 3 of 9"), and one with neither is "Clip 3 of 9". The title is what the
offer screen lists a file as; the file's own name on disk travels beside it, the leaf alone, and
is the name it lands under on the other side. For each person: the
name, the aliases, the stash-box ids when the host allows them, and the facial fingerprints a file
of them would carry. For each Site: the name. The field list is closed in `models.py`.

A file's song travels with it: its name, the artists kept apart from the name where the host keeps
them, and the AcoustID recording it is where one said, so the guest can put the file on the song it
already has (`ingest`). Read through the same scoped door the Music page draws a song by
(`Repository.visible_song`), as the host with Hidden open: a song that read would not show is
not sent. Only a guest whose hello said it takes songs is sent them (`Offer.as_sent`).

A person the host chose who has no file in the offer (everything of theirs kept local or kept
from swaps) is not offered at all: nothing of them would arrive, so their name has no reason to go.

## One call for the host

`offer_for` is the whole of it for the session: the files, the people's facts, the offer. The three
steps stay separate functions because the first two read and the third is pure, and the titles
and the closed field list are what the tests hold.
"""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sift.kernel.access import AnyOf, AssetFilter, Not, Repository, Viewer, Where
from sift.kernel.access.catalog import attribution_of_files, refused_here
from sift.kernel.content.identity import Asset
from sift.kernel.db import Database
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.seams import SavedFilterSeam
from sift.slices.swap import store
from sift.slices.swap.models import (
    MAX_FACES_PER_PERSON,
    MAX_NAMES_PER_PERSON,
    MAX_OFFER_PICTURES,
    MAX_PICTURE_TEXT,
    MAX_SONG_NAME,
    Chosen,
    FileKind,
    Offer,
    OfferedFace,
    OfferedFaces,
    OfferedFile,
    OfferedPerson,
    OfferedSite,
    OfferedSong,
)
from sift.slices.swap.transfer import NEVER_SENT_MIMES

#: Which search leaf turns each kind of chosen thing into files. The leaves are the grammar's own,
#: so "this person's files" in a swap is exactly what the person's Files tab draws.
LEAF_OF_KIND: Mapping[str, str] = {
    "person": "people",
    "site": "sites",
    "tag": "tags",
    "collection": "collections",
    "photo_set": "photo_sets",
    # A song: the files that carry it.
    "song": "songs",
    # One file, picked on a wall in swap mode: the file itself, through the same walls.
    "asset": "assets",
}

#: The kind that offers a person's facial fingerprints and no file. See the module docstring.
FINGERPRINTS_ONLY = "facial_fingerprints"

#: Every file that is NOT kept local, by the kernel's one rule. See the module docstring.
NOT_KEPT_LOCAL = Not(Where("enrichment", ("local",)))

#: Every file that is NOT marked "Do not swap", by itself or by anything it is filed under.
NOT_KEPT_FROM_SWAPS = Not(Where("kept_from_swaps"))

#: The order the offer is read in, and so the order a person's files are numbered in: the oldest
#: first, so "clip 1" is the first one the host had. A seekable order, so each page is one seek.
OFFER_ORDER = "oldest"

#: What one file is called in a title, by kind.
_WORD: Mapping[str, str] = {"video": "clip", "image": "picture", "gif": "GIF"}

#: The kinds of file a swap moves, from the word `assets.media_type` stores.
_KINDS: Mapping[str, FileKind] = {"video": "video", "image": "image", "gif": "gif"}


class FaceDescriptions(Protocol):
    """The facial fingerprints a file of them would carry for each of these people, or None where
    the faces feature is off. Handed in for the same reason the saved filters are: the faces
    feature owns them.

    Keyed by person id; a person with none is absent. Each value is `faces_of`'s shape, built by
    whoever implements this from the references an export reads. `peer_model` is the face model the
    guest said it uses, or None where it said none: when it names a model other than this one's,
    each fingerprint carries the face picture it was read from.
    """

    async def descriptions(
        self, person_ids: Sequence[str], *, peer_model: str | None = None
    ) -> Mapping[str, OfferedFaces] | None: ...


@dataclass(frozen=True, slots=True)
class Offered:
    """One file the host will offer, with what the offer says about it besides its own row."""

    asset: Asset
    #: The offered people on this file, in the offer's own order of people.
    people: tuple[str, ...] = ()
    site: str | None = None
    username: str | None = None
    #: What the file is called on disk now, else the name it arrived under: the leaf alone.
    name: str | None = None
    #: The file's song, as the scoped read of songs shows it to the host with Hidden open.
    song: OfferedSong | None = None


@dataclass(frozen=True, slots=True)
class OfferedName:
    """An offered person as the selection knows them: the id and the name the host sees."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Selection:
    """What was chosen, as files: every file in the offer, and the people it will name."""

    files: tuple[Offered, ...] = ()
    people: tuple[OfferedName, ...] = ()
    #: The people chosen for their facial fingerprints alone, who are not already offered through
    #: a file: the host's own order, each once.
    fingerprints: tuple[OfferedName, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonFacts:
    """Everything the offer may say about one person."""

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    boxes: tuple[str, ...] = ()
    faces: OfferedFaces | None = None


async def files_of(
    access: Repository,
    database: Database,
    search: SavedFilterSeam,
    viewer: Viewer,
    chosen: Sequence[Chosen],
) -> Selection:
    """The files `chosen` stands for, as this viewer reads them for a swap (`swap_reader`),
    kept-local files left out, oldest first. See the module docstring for every rule applied."""
    reader = await swap_reader(access, viewer)
    if reader is None:
        # Nobody to read as (the user is gone or disabled), and so nothing to offer.
        return Selection()
    seen = await _offered_assets(access, search, reader, chosen)
    assets = sorted(seen.values(), key=lambda asset: (asset.added_at, asset.id))

    chosen_people = list(dict.fromkeys(one.id for one in chosen if one.kind == "person"))
    visible = await access.visible_people(reader, chosen_people) if chosen_people else {}
    attributed = await attribution_of_files(database, [asset.id for asset in assets])
    on_disk = await _names_on_disk(access, reader, [asset.id for asset in assets])
    songs = await _songs_of(access, database, reader, [asset.id for asset in assets])

    order = [person_id for person_id in chosen_people if person_id in visible]
    files: list[Offered] = []
    named: set[str] = set()
    for asset in assets:
        carried = attributed.get(asset.id, [])
        on = {one.id for one in carried if one.kind == "person"}
        people = tuple(person_id for person_id in order if person_id in on)
        named.update(people)
        filings = [one for one in carried if one.kind == "username"]
        filed = next((one for one in filings if one.site), filings[0] if filings else None)
        files.append(
            Offered(
                asset=asset,
                people=people,
                site=filed.site if filed is not None else None,
                username=(filed.name or None) if filed is not None else None,
                name=on_disk.get(asset.id) or asset.original_filename or None,
                song=songs.get(asset.id),
            )
        )
    offered = tuple(
        OfferedName(id=person_id, name=visible[person_id].name)
        for person_id in order
        if person_id in named
    )
    return Selection(
        files=tuple(files),
        people=offered,
        fingerprints=await _fingerprints_of(
            access, database, reader, chosen, {one.id for one in offered}
        ),
    )


async def swap_reader(access: Repository, viewer: Viewer) -> Viewer | None:
    """The host as every swap read sees the library: read from the database, with Hidden open.
    None for a user gone or disabled."""
    return await access.load_viewer(viewer.id, show_hidden=True)


async def _offered_assets(
    access: Repository, search: SavedFilterSeam, reader: Viewer, chosen: Sequence[Chosen]
) -> dict[str, Asset]:
    """Every file `chosen` stands for, each once, read as `reader` (`swap_reader`) with every rule
    of the module docstring applied: the one read the offer and its weight both make."""
    seen: dict[str, Asset] = {}
    for narrowing in await chosen_filters(search, reader, chosen):
        refusing = narrowing.also(NOT_KEPT_LOCAL).also(NOT_KEPT_FROM_SWAPS)
        async for asset in _every(access, reader, refusing):
            if asset.mime in NEVER_SENT_MIMES:
                continue
            seen.setdefault(asset.id, asset)
    return seen


async def chosen_filters(
    search: SavedFilterSeam, reader: Viewer, chosen: Sequence[Chosen]
) -> list[AssetFilter]:
    """What `chosen` stands for, as filters over the files, before any refusal: the entities as one
    union, and each saved filter as its own. The offer narrows each by the refusals; what a swap
    leaves out (`weight.left_out`) reads the same filters for the files the refusals take away."""
    groups: dict[str, list[str]] = {}
    saved: list[str] = []
    for one in chosen:
        if one.kind == "filter":
            saved.append(one.id)
        elif one.kind != FINGERPRINTS_ONLY:
            groups.setdefault(LEAF_OF_KIND[one.kind], []).append(one.id)

    filters: list[AssetFilter] = []
    if groups:
        filters.append(
            AssetFilter(where=AnyOf(tuple(Where(key, tuple(ids)) for key, ids in groups.items())))
        )
    for saved_id in saved:
        found = await search.saved_filter(reader, saved_id)
        if found is not None:
            filters.append(found)
    return filters


async def weigh(
    access: Repository, search: SavedFilterSeam, viewer: Viewer, chosen: Sequence[Chosen]
) -> tuple[int, int]:
    """How many files the picks would offer and what they add up to, in bytes: the same scoped
    read as the offer itself (`_offered_assets`), so what the sender is told it will send is what
    the offer will hold."""
    reader = await swap_reader(access, viewer)
    if reader is None:
        return 0, 0
    seen = await _offered_assets(access, search, reader, chosen)
    return len(seen), sum(max(0, asset.size_bytes or 0) for asset in seen.values())


async def _fingerprints_of(
    access: Repository,
    database: Database,
    opened: Viewer,
    chosen: Sequence[Chosen],
    offered: set[str],
) -> tuple[OfferedName, ...]:
    """The people chosen for their facial fingerprints, as the offer may name them: seen through
    the scoped read with Hidden open, never one kept from swaps or kept local, and never one
    already offered through a file (they carry their fingerprints there)."""
    wanted = list(
        dict.fromkeys(one.id for one in chosen if one.kind == FINGERPRINTS_ONLY and one.id)
    )
    wanted = [person_id for person_id in wanted if person_id not in offered]
    if not wanted:
        return ()
    visible = await access.visible_people(opened, wanted)
    named: list[OfferedName] = []
    for person_id in wanted:
        seen = visible.get(person_id)
        if seen is None:
            continue
        if await refused_here(database, "swap", "person", person_id) or await refused_here(
            database, "enrich", "person", person_id
        ):
            continue
        named.append(OfferedName(id=person_id, name=seen.name))
    return tuple(named)


async def _every(access: Repository, viewer: Viewer, narrowed: AssetFilter) -> AsyncIterator[Asset]:
    """Every file one filter reaches, a wall's page at a time, by seeking past the last one."""
    after: str | None = None
    while True:
        page = await access.visible_assets(
            viewer,
            limit=MAX_PAGE_SIZE,
            asset_filter=narrowed,
            sort=OFFER_ORDER,
            after=after,
        )
        for item in page.items:
            yield item.asset
        if len(page.items) < MAX_PAGE_SIZE:
            return
        after = page.items[-1].asset.id


async def people_of(
    database: Database,
    selection: Selection,
    *,
    faces: FaceDescriptions | None = None,
    peer_model: str | None = None,
) -> list[PersonFacts]:
    """The facts the offer may carry about each offered person, in the selection's order: the
    people offered through files, then the people offered for their facial fingerprints alone,
    each of those only when they have fingerprints to send."""
    everyone = (*selection.people, *selection.fingerprints)
    ids = [one.id for one in everyone]
    if not ids:
        return []
    aliases = await store.aliases_of_people(database, ids)
    boxes = await store.box_ids_of_people(database, ids)
    described = (
        await faces.descriptions(ids, peer_model=peer_model) if faces is not None else None
    ) or {}
    alone = {one.id for one in selection.fingerprints}
    return [
        PersonFacts(
            id=one.id,
            name=one.name,
            aliases=tuple(aliases.get(one.id, ())),
            boxes=tuple(boxes.get(one.id, ())),
            faces=described.get(one.id),
        )
        for one in everyone
        if one.id not in alone or _has_faces(described.get(one.id))
    ]


def _has_faces(faces: OfferedFaces | None) -> bool:
    return faces is not None and len(faces.faces) > 0


def faces_of(
    recognizer: str,
    dimension: int,
    references: Sequence[tuple[str, float, Sequence[float]]],
    *,
    pictures: Mapping[str, bytes] | None = None,
) -> OfferedFaces:
    """A person's references as the offer carries them: the best `MAX_FACES_PER_PERSON` by quality,
    each description in the pack's own byte form (little-endian four-byte floats), in base64.

    `pictures`, keyed by digest, are the face squares to send beside them: given only when the
    guest uses another face model. A picture that would not fit the offer's field is left out."""
    usable = [one for one in references if len(one[2]) == dimension]
    best = sorted(usable, key=lambda one: (-one[1], one[0]))[:MAX_FACES_PER_PERSON]
    return OfferedFaces(
        recognizer=recognizer,
        dimension=dimension,
        faces=[
            OfferedFace(
                digest=digest,
                quality=round(quality, 4),
                vector=base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii"),
                picture=_picture_text((pictures or {}).get(digest)),
            )
            for digest, quality, vector in best
        ],
        confirmed=len(references),
    )


def _picture_text(picture: bytes | None) -> str | None:
    """A face picture in the offer's form, or None when there is none or it would not fit."""
    if not picture:
        return None
    text = base64.b64encode(picture).decode("ascii")
    return text if len(text) <= MAX_PICTURE_TEXT else None


def _within_budget(people: Sequence[PersonFacts]) -> list[OfferedFaces | None]:
    """Each person's fingerprints with the face pictures past `MAX_OFFER_PICTURES` left off, the
    offer's order deciding who keeps theirs. Numbers alone are never dropped."""
    spent = 0
    kept: list[OfferedFaces | None] = []
    for one in people:
        if one.faces is None:
            kept.append(None)
            continue
        faces: list[OfferedFace] = []
        for face in one.faces.faces:
            cost = len(face.picture or "")
            if cost and spent + cost > MAX_OFFER_PICTURES:
                face = face.model_copy(update={"picture": None})
                cost = 0
            spent += cost
            faces.append(face)
        kept.append(one.faces.model_copy(update={"faces": faces}))
    return kept


#: Which song each of these files is on: the membership alone, the song itself read through the
#: scoped door after it.
_SONGS_OF_FILES = (
    "SELECT asset_id, song_id FROM song_files WHERE asset_id IN (SELECT value FROM json_each(?))"
)


async def _songs_of(
    access: Repository, database: Database, viewer: Viewer, ids: Sequence[str]
) -> dict[str, OfferedSong]:
    """Each offered file's song as the offer carries it, keyed by file. A file on no song, or on
    one the scoped read does not show this viewer whole (a locked tile names nothing), is absent. The artists are the ones the song
    credits, in order; the name says them too."""
    on: dict[str, str] = {}
    for start in range(0, len(ids), _NAMES_PER_READ):
        rows = await database.fetch_all(
            _SONGS_OF_FILES, (json.dumps(list(ids[start : start + _NAMES_PER_READ])),)
        )
        on.update({str(row["asset_id"]): str(row["song_id"]) for row in rows})
    said: dict[str, OfferedSong | None] = {}
    for song_id in dict.fromkeys(on.values()):
        seen = await access.visible_song(viewer, song_id)
        name = seen.name.strip()[:MAX_SONG_NAME] if seen is not None and not seen.locked else ""
        said[song_id] = (
            OfferedSong(
                name=name,
                artists=[one[:MAX_SONG_NAME] for _id, one in seen.artists][:MAX_NAMES_PER_PERSON]
                if seen
                else [],
                recording=(seen.recording_id or None) if seen else None,
            )
            if name
            else None
        )
    return {asset_id: song for asset_id, song_id in on.items() if (song := said[song_id])}


#: How many files one read of their names on disk asks about: a page, as every list read is.
_NAMES_PER_READ = MAX_PAGE_SIZE


async def _names_on_disk(access: Repository, viewer: Viewer, ids: Sequence[str]) -> dict[str, str]:
    """What each offered file is called on disk now (the file page's own reading), a page at a time."""
    names: dict[str, str] = {}
    for start in range(0, len(ids), _NAMES_PER_READ):
        names.update(await access.names_on_disk(viewer, ids[start : start + _NAMES_PER_READ]))
    return names


def _leaf(name: str | None) -> str | None:
    """The last segment of a name, held to the wire's ceiling: a folder never travels with it."""
    if not name:
        return None
    leaf = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return leaf[:255] or None


def build(
    selection: Selection, people: Sequence[PersonFacts], *, share_boxes: bool = True
) -> Offer:
    """The offer, from the selection and the people's facts. Pure: no reads, no clock.

    `share_boxes` is the host's "Share stash-box ids" tick. Off, no stash-box id travels.
    """
    index = {one.id: position for position, one in enumerate(people)}

    # Each person's offered files, in the offer's order: what "clip 14 of 38" counts in.
    under: dict[str, list[str]] = {}
    # The files under no offered person, by the Site they were filed under (or none).
    loose: dict[str | None, list[str]] = {}
    for one in selection.files:
        placed = [person_id for person_id in one.people if person_id in index]
        for person_id in placed:
            under.setdefault(person_id, []).append(one.asset.id)
        if not placed:
            loose.setdefault(one.site, []).append(one.asset.id)
    places = {
        (group_key, asset_id): number
        for group_key, group in (
            *((("person", key), ids) for key, ids in under.items()),
            *((("site", key), ids) for key, ids in loose.items()),
        )
        for number, asset_id in enumerate(group, start=1)
    }

    files: list[OfferedFile] = []
    sites: dict[str, None] = {}
    for one in selection.files:
        asset = one.asset
        placed = [person_id for person_id in one.people if person_id in index]
        if placed:
            head: str | None = people[index[placed[0]]].name
            key: tuple[str, str | None] = ("person", placed[0])
            count = len(under[placed[0]])
        else:
            head = one.site
            key = ("site", one.site)
            count = len(loose[one.site])
        if one.site:
            sites.setdefault(one.site, None)
        files.append(
            OfferedFile(
                key=asset.id,
                oshash=asset.oshash,
                size=max(0, asset.size_bytes or 0),
                identity=asset.identity,
                video_phash=asset.video_phash if asset.media_type == "video" else None,
                phash=asset.phash if asset.media_type == "image" else None,
                fingerprint_version=asset.fingerprint_version,
                duration_ms=asset.duration_ms,
                kind=_kind(asset.media_type),
                width=asset.width,
                height=asset.height,
                title=title(head, asset.media_type, places[(key, asset.id)], count),
                site=one.site,
                username=one.username,
                people=[index[person_id] for person_id in placed],
                name=_leaf(one.name),
                song=one.song,
            )
        )

    return Offer(
        files=files,
        people=[
            OfferedPerson(
                name=one.name,
                aliases=list(one.aliases),
                boxes=list(one.boxes) if share_boxes else [],
                faces=faces,
            )
            for one, faces in zip(people, _within_budget(people), strict=True)
        ],
        sites=[OfferedSite(name=name) for name in sites],
    )


def title(head: str | None, media_type: str, place: int, count: int) -> str:
    """A made-up name: "{head}, clip {n} of {count}", or "Clip {n} of {count}" with no head."""
    word = _WORD[media_type]
    if head is None:
        return f"{word[:1].upper()}{word[1:]} {place} of {count}"
    return f"{head}, {word} {place} of {count}"


def _kind(media_type: str) -> FileKind:
    # Every kind a file can be: the content schema's CHECK holds `media_type` to these three.
    return _KINDS[media_type]


async def offer_for(
    access: Repository,
    database: Database,
    search: SavedFilterSeam,
    viewer: Viewer,
    chosen: Sequence[Chosen],
    *,
    share_boxes: bool = True,
    faces: FaceDescriptions | None = None,
    peer_model: str | None = None,
) -> Offer:
    """The host's offer for what it chose: `files_of`, then `people_of`, then `build`.

    `peer_model` is the face model the guest's hello named (see `FaceDescriptions`)."""
    selection = await files_of(access, database, search, viewer, chosen)
    people = await people_of(database, selection, faces=faces, peer_model=peer_model)
    return build(selection, people, share_boxes=share_boxes)
