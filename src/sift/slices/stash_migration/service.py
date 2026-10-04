# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bringing a Stash library into this one.

## The two steps

READ: somebody names Stash's database file (inside a folder Sift has been given). Sift copies it
into its own data folder through SQLite's backup (Stash may be running, and a file copy of a live
database is a torn one), opens the copy so nothing can be written, and says what is in it: every
count, the folders Stash scans and which of this library's folders each one matches.

RUN: a task over that copy. First every scene and image is matched to a file here: by where it is
(Stash's folder put in place of the matching folder here), then by its OSHash, then by its video
fingerprint (`Finder`). Which folder here each of Stash's folders is gets asked again whenever the
read is shown or run, because a folder added after the read holds files the read could not see; the
run's note says when that answer differs from the read's. Then the People, Sites and Tags those
files carry come across (Tags first with their parents, then Sites with theirs, then People), and
then the files themselves. What a matched file is given is what a stash-box answer gives one,
written by the same writers (`kernel.enrichment.Enricher`) with every field on Merge: it fills what
is empty and adds to lists, and never overwrites what this library already says. Ratings and the O
and view counts land on the person who pressed Run, because they are that person's, and only where
they have said nothing themselves: a rating fills an empty one, a count is raised and never
lowered. A gallery whose pictures are here becomes a Photo Set through the one door every Photo Set
is made by, once, and a later run or a picture that arrives later adds to that set.

Then what belongs to other features, each through that feature's own writer (`ports.StashDoors`):
the heart and the stars Stash kept on a performer, a studio or a tag; the stash-box ids it kept,
linked where a stash-box here has the same address; its scene markers as Loops on the matched
videos (a marker with no end becomes a twenty-second Loop tagged as a Stash marker, since a moment
is not a stretch); its saved filters over scenes and images, as saved searches where every part
of one can be said in Sift's words (`saved_filters`); its groups, as Collections of the scenes that
are here, in the group's order; and, where the person asked for them, the pictures it kept on
performers, studios and tags, as their covers where they have none, through the cover door that
re-encodes every outside picture. What could not come across is in the report.

A scene's resume point and the time it was watched in all come across with its counts, for whoever
pressed Run and only where they have said nothing here; no date comes with any of them.

## Attached to nothing

A person, a Site or a tag Stash attached to no scene and no image comes across whole (its heart,
its stars, its stash-box ids, its picture) and the row made for it is marked as such
(`VIA_STASH_UNATTACHED`), which the People, Sites and Tags walls filter by
(`created=stash_unattached`), so reviewing them, and deleting those not wanted, is one list each.

A picture inside a zip is found where it is: Sift reads a zip in place, so a picture in one has a
place of its own (the zip's path with the picture's name under it), which is Stash's path for it.

## Nothing lands without its file

A scene or an image whose file this library does not hold is not imported, and neither is a
person, a Site or a tag that only such scenes and images carry. What the run would have written for
each is kept (`waiting`, and `schema` for the tables) with what Stash knew of its file: its paths,
its OSHash, its video fingerprint, its size and its length. The report lists every one by name and
by Stash's path, with what waits on it.

When a later scan brings a file in and its fingerprints are read, the pass that runs after them
(`land`, the `STASH_ARRIVED` task) asks ONE question: which waiting rows does a file here now carry.
It is answered by the content store from the waiting rows' own keys (`ContentStore.carriers_of`),
through the same matcher the run uses, and every row it finds is applied through the same writers
the run uses, its People, Sites and Tags made from their kept records at that moment if they are
still absent. Then the row is gone. With nothing waiting, the pass reads one row and stops.

A person, a Site or a tag Stash attaches to no scene and no image at all has no file to wait for,
so it is a record on its own and comes across, counted apart in the report so it can be reviewed.

People, Sites and Tags for a scene that is not here do not come across ahead of it: a file
somebody chose not to keep may never arrive, and everything made for it would be clutter nobody
asked for. They wait with their files.

## What it never does

It never makes a file, and running it again adds nothing twice: every write it makes is a fill or
a find, and what waits is replaced by what the newest run found rather than added to.

## Into a new library

Asked for by name, the run goes into a library made beside this one instead: the folders this read
matched become its folders, the copy and the plan go with it, and the task is queued there before it
is opened. The task then waits for that library's first scan to finish, because until then it holds
no files to match.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.catalog import mark_made_via
from sift.kernel.access.tag_tree import file_under_if_unfiled
from sift.kernel.config import Settings
from sift.kernel.content import AssetUserState, ContentStore, LibraryStore, UserStateStore
from sift.kernel.db import Database
from sift.kernel.enrichment import Enricher, Naming
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    Family,
    JobCanceled,
    JobContext,
    JobFailedPermanently,
    JobHeld,
    JobQueue,
    JobState,
    TaskRun,
    get_schedule,
    registered_families,
)
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine
from sift.kernel.records import Subject
from sift.kernel.seams import PhotoSetSeam
from sift.kernel.vocabulary import VIA_STASH_LIBRARY, VIA_STASH_UNATTACHED
from sift.kernel.whole_file import write_json_whole
from sift.kernel.wiring import Part
from sift.slices.stash_migration import reader
from sift.slices.stash_migration.ports import LinkOn, OpinionOn, StashDoors
from sift.slices.stash_migration.reader import (
    BoxId,
    Entity,
    Gallery,
    Group,
    Item,
    Marker,
    NotAStashDatabase,
    SavedFilter,
    StashFile,
    Summary,
)
from sift.slices.stash_migration.said import note_of_landing, note_of_run
from sift.slices.stash_migration.saved_filters import translate
from sift.slices.stash_migration.tally import Tally, moved_since_read
from sift.slices.stash_migration.waiting import (
    KeptGallery,
    KeptGroup,
    Package,
    Waiting,
    WaitingFile,
    WaitingStore,
)

log = get_logger(__name__)

#: The task that brings a Stash library in.
STASH_IMPORT = "stash_import"

#: The pass that applies what waits, once files have arrived and been fingerprinted.
STASH_ARRIVED = "stash_arrived"

#: Where the copy and what was read about it live, in this library's data folder.
FOLDER = "stash"
COPY_NAME = "stash.sqlite"
PLAN_NAME = "plan.json"
REPORT_NAME = "report.json"

#: What Stash calls its database when nothing says otherwise, and the file beside it that can say
#: otherwise (its `database:` line). A browser picks Stash's FOLDER, since a page can pick only
#: folders; the database is found in it by these two names, never by listing what the folder holds.
STASH_DATABASE = "stash-go.sqlite"
STASH_CONFIG = "config.yml"
_DATABASE_LINE = re.compile(r"^database:[ \t]*(.+?)[ \t]*$", re.MULTILINE)

#: How many scenes or images between two reports of progress and two looks for a Cancel.
BATCH = 500

#: How long a Loop made from a Stash marker with no end runs. A moment has no length of its own,
#: and a Loop must have one; twenty seconds is long enough to see what the moment was.
MOMENT_MS = 20_000

#: The tag every Loop made from a moment carries, so the made-up length can be found and trimmed.
MOMENT_TAG = "From a Stash marker"

#: How long the task waits between two looks at a new library's first scan.
SCAN_WAIT_SECONDS = 30.0

#: The Tasks screen's Scan task: the whole-library walk a new library's first scan is.
SCAN_TASK = "scan"

#: What a plan's source is called when a writer asks which box answered: Stash is not a box, and
#: the actor on every write is the person who pressed Run, so this names the source for the plan's
#: own bookkeeping and nothing reads it as a box.
SOURCE = "stash"

#: The three kinds of named thing, by the word the waiting rows and the doors use for each.
_KINDS: dict[str, Subject] = {"person": Subject.PERSON, "site": Subject.SITE, "tag": Subject.TAG}

#: How deep a chain of parents is followed when a waiting Site or tag is made. Stash refuses a loop
#: and so does Sift; this only stops a damaged copy from recursing without end.
_DEEPEST = 32


class StashRefused(Exception):
    """Refused, with a sentence a person can act on, and the status a route answers it with."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _map_path(path: str, mapping: Mapping[str, str]) -> Path | None:
    """Where a Stash path is on this machine, by the longest Stash folder in the mapping."""
    stash = reader.stash_path(path)
    for prefix in sorted(mapping, key=len, reverse=True):
        try:
            rest = stash.relative_to(reader.stash_path(prefix))
        except ValueError:
            continue
        return Path(mapping[prefix]).joinpath(*rest.parts)
    return None


def _place(roots: Sequence[tuple[str, Path]], where: Path) -> tuple[str, str] | None:
    """Which library folder a path is in and where under it, or None when it is in none."""
    for root_id, root in roots:
        try:
            rest = where.relative_to(root)
        except ValueError:
            continue
        return root_id, rest.as_posix()
    return None


@dataclass
class Finder:
    """Which of this library's files a Stash file is.

    ONE matcher with two feeds: the run builds it from every place and fingerprint in the library
    (`StashMigration._finder`), and the pass that lands what waits builds it from only the files
    that carry a waiting row's keys (`StashMigration._arrived`). The order it trusts them in is
    written here once, so the two cannot come to disagree about which file a Stash file is."""

    roots: list[tuple[str, Path]]
    places: dict[tuple[str, str], str]
    by_oshash: dict[str, str]
    by_phash: dict[str, str]

    def place_of(self, where: Path) -> str | None:
        found = _place(self.roots, where)
        return None if found is None else self.places.get((found[0], found[1].casefold()))

    def find(self, one: StashFile, mapping: Mapping[str, str]) -> tuple[str, str] | None:
        """The asset a Stash file is, and how it was found; None when this library lacks it."""
        return self.find_here(one, _map_path(one.path, mapping))

    def find_here(self, one: StashFile, here: Path | None) -> tuple[str, str] | None:
        """`find`, with where the file is on this device already worked out."""
        if here is not None:
            found = self.place_of(here)
            if found is not None:
                return found, "place"
        if one.oshash and one.oshash.lower() in self.by_oshash:
            return self.by_oshash[one.oshash.lower()], "oshash"
        if one.phash and one.phash.lower() in self.by_phash:
            return self.by_phash[one.phash.lower()], "fingerprint"
        return None


@dataclass
class Stashed:
    """Everything a run reads from the copy, read once before anything is written."""

    tags: list[Entity]
    studios: list[Entity]
    performers: list[Entity]
    scenes: list[Item]
    images: list[Item]
    markers: list[Marker]
    galleries: list[Gallery]
    box_ids: dict[str, list[BoxId]]
    several_parents: list[str]
    filters: list[SavedFilter]
    tag_names: dict[int, str]
    person_names: dict[int, str]
    site_names: dict[int, str]
    groups: list[Group] = field(default_factory=list)


def _read_stash(connection: Any) -> Stashed:
    return Stashed(
        tags=list(reader.tags(connection)),
        studios=list(reader.studios(connection)),
        performers=list(reader.performers(connection)),
        scenes=list(reader.scenes(connection)),
        images=list(reader.images(connection)),
        markers=list(reader.markers(connection)),
        galleries=reader.galleries(connection),
        box_ids={
            kind: reader.box_ids(connection, kind)
            for kind in ("performer", "studio", "tag", "scene")
        },
        several_parents=reader.tags_with_several_parents(connection),
        filters=list(reader.saved_filters(connection)),
        tag_names=reader.names_of(connection, "tags"),
        person_names=reader.names_of(connection, "performers"),
        site_names=reader.names_of(connection, "studios"),
        groups=reader.groups(connection),
    )


def names_in(
    fields: Mapping[str, object], markers: Iterable[Mapping[str, Any]] = ()
) -> set[tuple[str, str]]:
    """The People, Sites and Tags one scene or image carries, as (kind, name): its own fields and
    the tags of its markers."""
    found: set[tuple[str, str]] = set()
    people = fields.get("people")
    if isinstance(people, list):
        found.update(("person", str(one)) for one in people)
    if fields.get("site"):
        found.add(("site", str(fields["site"])))
    tags = fields.get("tags")
    if isinstance(tags, list):
        found.update(("tag", str(one)) for one in tags)
    for marker in markers:
        found.update(("tag", str(one)) for one in marker.get("tags", ()))
    return found


def _via(key: tuple[str, str], decided: Decided) -> str:
    """The word a row made for this person, Site or tag is marked with: attached to nothing in
    Stash, or imported with its files."""
    return VIA_STASH_UNATTACHED if key in decided.without_files else VIA_STASH_LIBRARY


@dataclass(frozen=True)
class Decided:
    """Which People, Sites and Tags come across now, by kind and name, and which of those Stash
    attaches to nothing at all."""

    come: set[tuple[str, str]]
    without_files: set[tuple[str, str]]


def decide(stashed: Stashed, landed: set[tuple[str, int]]) -> Decided:
    """Nothing lands without its file.

    A person, a Site or a tag comes across when a scene or an image that carries it lands, or
    when nothing in Stash carries it at all (a record of its own, with no file to wait for). A
    Site's parent and a tag's one parent come with it: they are part of its record. Everything
    else waits with the scenes and images that carry it."""
    marked: dict[int, list[Mapping[str, Any]]] = {}
    for one in stashed.markers:
        marked.setdefault(one.scene_id, []).append({"tags": list(one.tags)})
    carried: set[tuple[str, str]] = set()
    come: set[tuple[str, str]] = set()
    for item in [*stashed.scenes, *stashed.images]:
        named = names_in(item.fields, marked.get(item.stash_id, []) if item.kind == "scene" else ())
        carried |= named
        if (item.kind, item.stash_id) in landed:
            come |= named
    every = {
        *(("tag", one.name) for one in stashed.tags),
        *(("site", one.name) for one in stashed.studios),
        *(("person", one.name) for one in stashed.performers),
    }
    # What a carried row brings with it is carried too: a Site's or a tag's parent (a network, a
    # category) and the tags a person or a Site wears. Only what nothing reaches that way is a
    # record on its own, attached to nothing.
    worn = _worn_tags(stashed)
    alone = every - _with_what_they_carry(carried, stashed, worn)
    come |= alone
    return Decided(come=_with_what_they_carry(come, stashed, worn), without_files=alone)


def _worn_tags(stashed: Stashed) -> dict[tuple[str, str], list[str]]:
    """The tags each performer and studio wears, by kind and name."""
    worn: dict[tuple[str, str], list[str]] = {}
    for kind, entities in (("person", stashed.performers), ("site", stashed.studios)):
        for one in entities:
            tags = one.fields.get("tags")
            if isinstance(tags, list) and tags:
                worn[(kind, one.name)] = [str(tag) for tag in tags]
    return worn


def _with_what_they_carry(
    chosen: set[tuple[str, str]], stashed: Stashed, worn: Mapping[tuple[str, str], list[str]]
) -> set[tuple[str, str]]:
    """These People, Sites and Tags with what is part of their records: a Site's and a tag's one
    parent, and the tags a person or a Site wears, followed until nothing new is added."""
    parents = {("site", one.name): one.parent for one in stashed.studios if one.parent} | {
        ("tag", one.name): one.parent for one in stashed.tags if one.parent
    }
    found = set(chosen)
    pending = list(found)
    while pending:
        kind, name = pending.pop()
        parent = parents.get((kind, name))
        joined = [(kind, parent)] if parent is not None else []
        joined += [("tag", tag) for tag in worn.get((kind, name), [])]
        for one in joined:
            if one not in found:
                found.add(one)
                pending.append(one)
    return found


def _entity_record(one: Entity, box_ids: Sequence[BoxId]) -> dict[str, Any]:
    """What a waiting person, Site or tag is kept as: its record, the opinion on it, its parent,
    the stash-box ids Stash kept for it and its picture's checksum."""
    return {
        "stash_id": one.stash_id,
        "fields": dict(one.fields),
        "favorite": one.favorite,
        "rating": one.rating,
        "parent": one.parent,
        "box_ids": [[box.endpoint, box.remote_id] for box in box_ids],
        "picture": one.picture,
    }


def _entity_from(name: str, record: Mapping[str, Any]) -> Entity:
    return Entity(
        stash_id=int(record.get("stash_id") or 0),
        name=name,
        fields=dict(record.get("fields") or {}),
        favorite=bool(record.get("favorite")),
        rating=record.get("rating"),
        parent=record.get("parent"),
        picture=record.get("picture"),
    )


def said_waiting(one: Waiting) -> dict[str, Any]:
    """One waiting scene or picture as the screen and the report list it: what it is called, the
    paths Stash had it at, and what waits on it (its markers by title and time, its People, Sites
    and Tags by name, its rating)."""
    fields = one.package.get("fields") or {}
    people = fields.get("people") or []
    tags = fields.get("tags") or []
    return {
        "id": one.id,
        "kind": one.kind,
        "label": one.label,
        "paths": [stash_file.path for stash_file in one.files],
        "rating": one.package.get("rating"),
        "markers": [
            {
                "title": marker.get("title") or (marker.get("tags") or [""])[0],
                "start_ms": round(float(marker.get("start") or 0) * 1000),
                "end_ms": None if marker.get("end") is None else round(float(marker["end"]) * 1000),
            }
            for marker in one.package.get("markers") or []
        ],
        "people": [str(name) for name in people],
        "sites": [str(fields["site"])] if fields.get("site") else [],
        "tags": [str(name) for name in tags],
    }


def _database_in(folder: Path) -> Path | None:
    """Stash's database inside `folder`, by name: the one its `config.yml` names, that name beside
    the config when the path it gives is from another machine or a container, or `stash-go.sqlite`.
    Only a file inside the folder is answered: a database named elsewhere is chosen by its file."""
    candidates: list[Path] = []
    config = folder / STASH_CONFIG
    if config.is_file():
        named = _DATABASE_LINE.search(config.read_text(encoding="utf-8", errors="replace"))
        if named is not None:
            said = named.group(1).strip("'\"")
            candidates += [folder / said, folder / PurePosixPath(said.replace("\\", "/")).name]
    candidates.append(folder / STASH_DATABASE)
    for candidate in candidates:
        try:
            inside = confine(folder, candidate)
        except PathEscape:
            continue
        if inside.is_file():
            return inside
    return None


class StashMigration:
    """Reading a Stash database and bringing it into this library. One per application."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        library: LibraryStore,
        enricher: Enricher,
        naming: Naming,
        user_state: UserStateStore,
        photo_sets: PhotoSetSeam | None = None,
        *,
        content: ContentStore,
        doors: StashDoors | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._content = content
        self.doors = doors
        self._db = database
        self._settings = settings
        self._library = library
        self._enricher = enricher
        self._naming = naming
        self._user_state = user_state
        self._photo_sets = photo_sets
        self._clock = clock
        self._waiting = WaitingStore(database)

    @property
    def folder(self) -> Path:
        return self._settings.data_dir / FOLDER

    # --- read ---------------------------------------------------------------------------------

    async def _confined(self, chosen: str, what: str = "Stash's blobs folder") -> Path:
        """The path named, proven to be inside a folder Sift has been given. The picker's rule.
        `what` names what was asked for in the sentence a refusal says."""
        places = [Path(grant.abs_path) for grant in await self._library.grants()]

        def inside() -> Path | None:
            for place in places:
                try:
                    return confine(place, Path(chosen.strip()))
                except PathEscape:
                    continue
            return None

        found = await asyncio.to_thread(inside)
        if found is None:
            raise StashRefused(
                "Sift can only read a file in a folder it has been given. Add the folder that "
                f"holds {what} in `Settings > Folders` first."
            )
        return found

    async def _chosen(self, chosen: str) -> Path:
        """The file named, proven to be inside a folder Sift has been given. The picker's rule.

        A FOLDER named is Stash's folder (what a browser can pick): the database is the one its
        `config.yml` names, or `stash-go.sqlite`, looked for inside that folder by name."""
        found = await self._confined(chosen, "Stash's database")
        if await asyncio.to_thread(found.is_dir):
            inside = await asyncio.to_thread(_database_in, found)
            if inside is None:
                raise StashRefused(
                    "There's no Stash database in that folder. Choose the folder that holds "
                    f"{STASH_DATABASE}, or the one its {STASH_CONFIG} is in."
                )
            return inside
        if not await asyncio.to_thread(found.is_file):
            raise StashRefused("There's no file there. Check the name and try again.")
        return found

    async def read(self, chosen: str) -> dict[str, Any]:
        """Copy Stash's database in, read it, and say what it holds and what matches here."""
        source = await self._chosen(chosen)
        copy = self.folder / COPY_NAME
        try:
            await asyncio.to_thread(reader.copy_in, source, copy)
            summary = await asyncio.to_thread(_summary_of, copy)
        except NotAStashDatabase as refused:
            await asyncio.to_thread(copy.unlink, True)
            raise StashRefused(str(refused)) from refused
        mapping = await self._proposed(summary.folders)
        # `source` names the Stash database this read came from, which is what tells one Stash's
        # gallery 3 from another's (`schema`, `stash_galleries`).
        # Stash keeps its blobs folder beside its database unless told otherwise, so that is the
        # folder offered where it keeps pictures as files.
        plan = {
            "read_at": int(self._clock()),
            "mapping": mapping,
            "source": str(source),
            "blobs_proposed": str(source.parent / "blobs"),
        }
        await asyncio.to_thread(write_json_whole, self.folder / PLAN_NAME, plan)
        log.info("stash.read", version=summary.version, scenes=summary.scenes)
        return {
            "summary": asdict(summary),
            "mapping": mapping,
            "source": plan["source"],
            "waiting": await self._waiting.count(),
            "blobs": plan["blobs_proposed"] if summary.pictures_in_a_folder else None,
            "unattached": await self.unattached(),
        }

    async def _proposed(self, folders: Sequence[reader.TopFolder]) -> dict[str, str]:
        """Which folder here each of Stash's top folders is: the library folder called what the
        Stash folder is called, by the name it was given here or by its folder's own name. A
        folder with no match here is left out; its files are then found by their fingerprints, or
        wait for them."""
        roots = await self._library.roots()
        proposed: dict[str, str] = {}
        for one in folders:
            name = reader.stash_path(one.path).name.casefold()
            for root in roots:
                if name in (root.name.casefold(), Path(root.abs_path).name.casefold()):
                    proposed[one.path] = root.abs_path
                    break
        return proposed

    async def _matched_now(
        self, folders: Sequence[reader.TopFolder], kept: Mapping[str, str]
    ) -> dict[str, str]:
        """Which folder here each of Stash's top folders is now: the read's own answer where its
        folder is still one of this library's, and the name rule (`_proposed`) for the rest.

        Nothing lands without its file, so what counts is what this library holds when the
        answer is used, not what it held at the read."""
        roots = {root.abs_path for root in await self._library.roots()}
        now = {stash: here for stash, here in kept.items() if here in roots}
        for stash, here in (await self._proposed(folders)).items():
            now.setdefault(stash, here)
        return now

    async def last_read(self, queue: JobQueue | None = None) -> dict[str, Any] | None:
        """What the last read found, while its copy is still here; None when nothing was read.

        With the queue, it also says the run over that read, from the queue's record of the task.
        `waiting` is how many scenes and pictures wait for their files now, which falls as they
        arrive. `mapping` is the folder match as it stands now (`_matched_now`).
        """
        copy = self.folder / COPY_NAME
        if not await asyncio.to_thread(copy.is_file):
            return None
        try:
            summary = await asyncio.to_thread(_summary_of, copy)
            plan = json.loads(
                await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
            )
        except (NotAStashDatabase, OSError, ValueError):
            return None
        mapping = await self._matched_now(summary.folders, dict(plan.get("mapping") or {}))
        ran = None
        if queue is not None:
            done = await queue.last_finished_runs([STASH_IMPORT], ended_in=(JobState.DONE,))
            ran = ran_after(done.get(STASH_IMPORT), int(plan.get("read_at") or 0))
        blobs = plan.get("blobs") or plan.get("blobs_proposed")
        return {
            "summary": asdict(summary),
            "mapping": mapping,
            "source": plan.get("source"),
            "ran": ran,
            "waiting": await self._waiting.count(),
            "blobs": blobs if summary.pictures_in_a_folder else None,
            "unattached": await self.unattached(),
        }

    async def waiting_page(self, offset: int, limit: int) -> tuple[int, list[dict[str, Any]]]:
        """How many scenes and pictures wait for their files, and one page of them as listed."""
        total, rows = await self._waiting.page(offset, limit)
        return total, [said_waiting(one) for one in rows]

    # --- run ----------------------------------------------------------------------------------

    async def ask_to_run(
        self, actor: Viewer, queue: JobQueue, *, pictures: bool = False, blobs: str | None = None
    ) -> str:
        """Queue the run over the last read, refused where there is nothing read or one is going.

        `pictures` is the choice to bring the pictures of People, Sites and Tags, and `blobs` the
        folder Stash keeps them in when it keeps them as files (`choose`)."""
        if await self.last_read() is None:
            raise StashRefused("Read a Stash database first.")
        for state in (JobState.QUEUED, JobState.RUNNING):
            waiting = await queue.list(job_type=STASH_IMPORT, state=state, limit=1)
            if waiting.total:
                raise StashRefused("A Stash library is already being imported.")
        await self.choose(pictures=pictures, blobs=blobs)
        return await queue.enqueue(STASH_IMPORT, {}, requested_by=actor.id)

    async def choose(self, *, pictures: bool, blobs: str | None) -> None:
        """Keep the choice about pictures with the read, where the run and the pass that lands
        what waits both find it. A blobs folder is held to the folders Sift has been given, the
        rule the database file itself is held to, and refused where it is not a folder."""
        folder: str | None = None
        if pictures and blobs and blobs.strip():
            found = await self._confined(blobs)
            if not await asyncio.to_thread(found.is_dir):
                raise StashRefused("There's no folder there. Check the blobs folder and try again.")
            folder = str(found)
        plan_path = self.folder / PLAN_NAME
        plan = json.loads(await asyncio.to_thread(plan_path.read_text, encoding="utf-8"))
        plan["pictures"] = bool(pictures)
        plan["blobs"] = folder
        await asyncio.to_thread(write_json_whole, plan_path, plan)

    async def unattached(self) -> dict[str, int]:
        """How many People, Sites and Tags a Stash library made here that Stash attached to nothing
        are still here: the rows the walls list under `created=stash_unattached`. Falls as they
        are deleted, so the line that links to them says what is left to look at."""
        counts: dict[str, int] = {}
        for kind, statement in _UNATTACHED.items():
            row = await self._db.fetch_one(statement, (VIA_STASH_UNATTACHED,))
            counts[kind] = int(row["n"]) if row is not None else 0
        return counts

    async def run(self, context: JobContext) -> None:
        """The task. See the module's own description for the order and the rules."""
        user_id = context.job.requested_by
        if not user_id:
            raise JobFailedPermanently("Sift doesn't know who asked for this, so it has stopped.")
        read = await self.last_read()
        if read is None:
            raise JobFailedPermanently("The Stash copy has gone. Read the database again.")
        await self._wait_for_first_scan(context, user_id)
        plan = json.loads(
            await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
        )
        mapping: dict[str, str] = dict(read["mapping"])
        source = str(plan.get("source") or "")
        read_at = int(plan.get("read_at") or 0)
        actor = Actor.user(user_id)
        tally = Tally()
        moved_since_read(tally, dict(plan.get("mapping") or {}), mapping)
        if self.doors is None:
            tally.not_done = [
                "favorites",
                "stash-box ids",
                "markers",
                "saved filters",
                "groups",
                "pictures",
            ]
        connection = await asyncio.to_thread(reader.open_copy, self.folder / COPY_NAME)
        try:
            stashed = await asyncio.to_thread(_read_stash, connection)
        finally:
            await asyncio.to_thread(connection.close)
        matched = await self._match(stashed, tally, mapping)
        await context.report_progress(0.05)
        decided = decide(stashed, set(matched))
        known = await self._catalog(context, stashed, tally, actor, decided)
        await self._opinions(tally, user_id, known)
        await self._links(context, stashed, tally, known)
        if plan.get("pictures"):
            await self._pictures(tally, known, _blobs_of(plan), actor)
        scenes = await self._items(context, stashed, tally, actor, user_id, matched)
        await self._file_links(context, stashed, tally, scenes)
        await self._galleries(stashed, tally, matched, source, actor)
        await self._groups(stashed, tally, scenes, source, actor, user_id)
        await self._marks(stashed, tally, user_id, scenes, known)
        await self._searches(stashed, tally, user_id)
        await self._keep_waiting(stashed, tally, matched, mapping, known, read_at, user_id, source)
        await asyncio.to_thread(write_json_whole, self.folder / REPORT_NAME, asdict(tally))
        await context.set_progress(1.0)
        await context.set_note(note_of_run(tally))
        log.info(
            "stash.imported",
            scenes_matched=tally.scenes_matched,
            images_matched=tally.images_matched,
            waiting=tally.waiting_scenes + tally.waiting_images,
            folders_matched_since_read=tally.folders_matched_since_read,
            folders_rematched_since_read=tally.folders_rematched_since_read,
            folders_unmatched_since_read=tally.folders_unmatched_since_read,
        )

    async def _match(
        self, stashed: Stashed, tally: Tally, mapping: Mapping[str, str]
    ) -> dict[tuple[str, int], str]:
        """Which file here each scene and image is, before anything is written: what comes across
        depends on it. Counts how each was found."""
        finder = await self._finder()
        matched: dict[tuple[str, int], str] = {}
        for one in [*stashed.scenes, *stashed.images]:
            if one.kind == "scene":
                tally.scenes += 1
            else:
                tally.images += 1
            found: tuple[str, str] | None = None
            # A file inside a zip is looked for like any other: Sift reads a zip where it is, and
            # a picture in one is kept at the zip's path with its own name under it, as Stash's is.
            for stash_file in one.files:
                found = finder.find(stash_file, mapping)
                if found is not None:
                    if stash_file.in_zip:
                        tally.zip_pictures_matched += 1
                    break
            if found is None:
                if one.files and all(stash_file.in_zip for stash_file in one.files):
                    tally.images_in_zips += 1
                continue
            asset_id, how = found
            matched[(one.kind, one.stash_id)] = asset_id
            if one.kind == "scene":
                tally.scenes_matched += 1
            else:
                tally.images_matched += 1
            if how == "place":
                tally.matched_by_place += 1
            elif how == "oshash":
                tally.matched_by_oshash += 1
            else:
                tally.matched_by_fingerprint += 1
        return matched

    async def _catalog(
        self,
        context: JobContext,
        stashed: Stashed,
        tally: Tally,
        actor: Actor,
        decided: Decided,
    ) -> Known:
        """Tags with their parents, Sites with theirs, and People: only those that come across now
        (`decide`). Answers which row here each one of Stash's became, for the parts of the run
        that follow."""
        known = Known()
        ids: dict[str, str] = {}
        for one in stashed.tags:
            if ("tag", one.name) not in decided.come:
                continue
            local = await self._made(Subject.TAG, one.name, via=_via(("tag", one.name), decided))
            if local is None:
                continue
            ids[one.name] = local
            known.tag[one.stash_id] = (local, one)
            await self._merge(Subject.TAG, local, one, actor)
            tally.tags += 1
            tally.tags_without_files += 1 if ("tag", one.name) in decided.without_files else 0
        for one in stashed.tags:
            if one.parent and one.name in ids and one.parent in ids:
                async with self._db.write() as writing:
                    if await file_under_if_unfiled(
                        writing, ids[one.name], ids[one.parent], actor=actor
                    ):
                        tally.tags_filed_under_a_parent += 1
        tally.tags_with_several_parents = list(stashed.several_parents)
        await context.report_progress(0.08)
        for one in stashed.studios:
            if ("site", one.name) not in decided.come:
                continue
            local = await self._made(Subject.SITE, one.name, via=_via(("site", one.name), decided))
            if local is None:
                continue
            known.site[one.stash_id] = (local, one)
            await self._merge(Subject.SITE, local, one, actor)
            tally.sites += 1
            tally.sites_without_files += 1 if ("site", one.name) in decided.without_files else 0
        await context.report_progress(0.1)
        for one in stashed.performers:
            if ("person", one.name) not in decided.come:
                continue
            local = await self._made(
                Subject.PERSON, one.name, via=_via(("person", one.name), decided)
            )
            if local is None:
                continue
            known.person[one.stash_id] = (local, one)
            await self._merge(Subject.PERSON, local, one, actor)
            tally.people += 1
            if ("person", one.name) in decided.without_files:
                tally.people_without_files += 1
            if context.stopping() == "cancel":
                raise JobCanceled
        await context.report_progress(0.15)
        return known

    async def _merge(self, subject: Subject, local: str, one: Entity | Item, actor: Actor) -> None:
        """Offer one row's fields to the writer that owns it, every field on Merge.

        The rows the write invents (a scene's People, its Site, its Tags) are named as made from
        this Stash library, as the rows `_made` makes are.
        """
        decided = await self._enricher.plan_for(
            subject=subject, local_id=local, source_id=SOURCE, offered=one.fields, strategies={}
        )
        if decided is None or not decided.writes:
            return
        invented = await self._enricher.missing_for(decided)
        await self._enricher.apply(decided, creating=True, actor=actor)
        for missing in invented:
            made = await self._named(missing.kind, missing.name, creating=False)
            if made is not None:
                await mark_made_via(self._db, missing.kind, made, VIA_STASH_LIBRARY)

    async def _made(
        self, subject: Subject, name: str, *, via: str = VIA_STASH_LIBRARY
    ) -> str | None:
        """The row this name means here, made when it means none, and then said to be made from
        this Stash library rather than from a stash-box's answer (the lookups' own word).

        `via` is `VIA_STASH_UNATTACHED` for a row Stash attached to nothing: the mark the People,
        Sites and Tags walls filter by (`created=stash_unattached`), so the review of those is one
        list. Only a row made here wears it; one this library already had is left as it was."""
        found = await self._named(subject.value, name, creating=False)
        if found is not None:
            return found
        made = await self._named(subject.value, name, creating=True)
        if made is not None:
            await mark_made_via(self._db, subject.value, made, via)
        return made

    async def _named(self, kind: str, name: str, *, creating: bool) -> str | None:
        """One of the three lookups, by the subject's own word."""
        if kind == Subject.PERSON.value:
            return await self._naming.person_named(name, creating=creating)
        if kind == Subject.SITE.value:
            return await self._naming.site_named(name, creating=creating)
        if kind == Subject.TAG.value:
            return await self._naming.tag_named(name, creating=creating)
        return None

    async def _finder(self) -> Finder:
        """Every place and fingerprint this library holds, read once, through the kernel."""
        roots = [(root.id, Path(root.abs_path)) for root in await self._library.roots()]
        places = {
            (one.root_id, one.rel_path.casefold()): one.asset_id
            for one in await self._content.every_location()
        }
        by_oshash: dict[str, str] = {}
        by_phash: dict[str, str] = {}
        for asset_id, oshash, phash in await self._content.every_fingerprint():
            if oshash:
                by_oshash.setdefault(str(oshash).lower(), asset_id)
            if phash:
                by_phash.setdefault(str(phash).lower(), asset_id)
        return Finder(roots, places, by_oshash, by_phash)

    async def _items(
        self,
        context: JobContext,
        stashed: Stashed,
        tally: Tally,
        actor: Actor,
        user_id: str,
        matched: Mapping[tuple[str, int], str],
    ) -> dict[int, str]:
        """Every matched scene, then every matched image: written, and rated for whoever asked.
        Answers which file here each matched scene is, for the markers."""
        everything = [*stashed.scenes, *stashed.images]
        total = max(1, len(everything))
        scenes: dict[int, str] = {}
        for done, one in enumerate(everything, start=1):
            asset_id = matched.get((one.kind, one.stash_id))
            if asset_id is not None:
                await self._write_item(one, asset_id, actor, user_id, tally)
                if one.kind == "scene":
                    scenes[one.stash_id] = asset_id
            if done % BATCH == 0:
                if context.stopping() == "cancel":
                    raise JobCanceled
                await context.report_progress(0.15 + 0.7 * done / total)
        return scenes

    async def _write_item(
        self, one: Item, asset_id: str, actor: Actor, user_id: str | None, tally: Tally
    ) -> None:
        """One scene's or image's record onto its file, and the person's own opinion of it, where
        they have not given one here: the run and the pass that lands what waited both write
        through this, so a file that arrives late is given exactly what a file found at once is."""
        await self._merge(Subject.ASSET, asset_id, one, actor)
        if user_id is None or (
            one.rating is None
            and not one.o_count
            and not one.views
            and one.resume_ms is None
            and not one.played_ms
        ):
            return
        held = await self._user_state.state_of(asset_id, user_id)
        if one.rating is not None and held.rating is None:
            await self._user_state.set_rating(asset_id, user_id, one.rating)
            tally.ratings += 1
        if one.o_count > held.o_count or one.views > held.view_count:
            await self._user_state.carry_counts(
                asset_id, user_id, o_count=one.o_count, views=one.views
            )
            tally.o_counts += 1 if one.o_count > held.o_count else 0
            tally.viewed += 1 if one.views > held.view_count else 0
        await self._carry_watching(one, asset_id, user_id, held, tally)

    async def _carry_watching(
        self, one: Item, asset_id: str, user_id: str, held: AssetUserState, tally: Tally
    ) -> None:
        """Where Stash would pick a scene up and how long it was watched, for this person, where
        they have said nothing here: a resume point fills an empty one, the time watched is raised
        to Stash's and never lowered, so a second run adds nothing. No date comes with either.

        One write through the store's own sitting-without-a-view (`record_watch_time`), which
        replaces the resume point: it is handed the one already here when there is one, so a place
        this person reached in Sift is never moved by Stash's."""
        more_ms = max(0, one.played_ms - held.watched_ms)
        resume = held.resume_ms if held.resume_ms is not None else one.resume_ms
        if more_ms == 0 and resume == held.resume_ms:
            return
        await self._user_state.record_watch_time(
            asset_id, user_id, watch_ms=more_ms, resume_ms=resume
        )
        tally.resume_points += 1 if resume != held.resume_ms else 0
        tally.time_watched += 1 if more_ms else 0

    async def _galleries(
        self,
        stashed: Stashed,
        tally: Tally,
        matched: Mapping[tuple[str, int], str],
        source: str,
        actor: Actor,
    ) -> None:
        """Each gallery whose pictures are here, as a Photo Set through the one door that makes one,
        or added to the set an earlier run made for it."""
        for gallery in stashed.galleries:
            here = [
                asset_id
                for image_id in gallery.images
                if (asset_id := matched.get(("image", image_id))) is not None
            ]
            if here:
                await self._grow_gallery(source, gallery.stash_id, gallery.name, here, actor, tally)

    async def _grow_gallery(
        self,
        source: str,
        gallery_id: int,
        name: str,
        asset_ids: Sequence[str],
        actor: Actor,
        tally: Tally,
    ) -> None:
        """Pictures of one gallery that are here, into the Photo Set it became: made once (or found,
        where a zip or a folder here made one of them), then added to, so a second run never makes
        a second set. A set somebody deleted is not made again: the id kept for it says it is gone."""
        kept = await self._waiting.gallery(source, gallery_id) or KeptGallery(name, None)
        new = [one for one in dict.fromkeys(asset_ids) if one not in kept.pictures]
        if not new:
            return
        pictures = (*kept.pictures, *new)
        photo_set_id = kept.photo_set_id
        if photo_set_id is not None:
            if self.doors is not None:
                await self.doors.add_to_photo_set(photo_set_id, new, actor=actor)
        elif self._photo_sets is not None:
            photo_set_id = await self._photo_sets.holding(list(pictures))
            if photo_set_id is None:
                photo_set_id = await self._photo_sets.make(
                    list(pictures), name=kept.name, origin=VIA_STASH_LIBRARY
                )
                tally.photo_sets += photo_set_id is not None
        await self._waiting.keep_gallery(
            source, gallery_id, KeptGallery(kept.name, photo_set_id, pictures)
        )

    async def _groups(
        self,
        stashed: Stashed,
        tally: Tally,
        scenes: Mapping[int, str],
        source: str,
        actor: Actor,
        user_id: str | None,
    ) -> None:
        """Each group whose scenes are here, as a Collection of them in the group's own order, or
        added to the Collection an earlier run made for it."""
        for group in stashed.groups:
            here = [asset_id for scene_id in group.scenes if (asset_id := scenes.get(scene_id))]
            if here:
                await self._grow_group(
                    source, group.stash_id, group.name, here, actor, user_id, tally
                )

    async def _grow_group(
        self,
        source: str,
        group_id: int,
        name: str,
        asset_ids: Sequence[str],
        actor: Actor,
        user_id: str | None,
        tally: Tally,
    ) -> None:
        """Scenes of one group that are here, into the Collection it became, at its end.

        Made once, by the Collections feature's own writer, owned by whoever pressed Run (a
        Collection is somebody's); added to from then on, so a second run or a scene arriving
        later never makes a second one. A Collection somebody deleted is not made again. A scene
        that arrives late goes at the end, since the group's order is Stash's and the Collection
        may have been rearranged here since."""
        if self.doors is None:
            return
        kept = await self._waiting.group(source, group_id) or KeptGroup(name, None)
        new = [one for one in dict.fromkeys(asset_ids) if one not in kept.scenes]
        if not new:
            return
        collection_id = kept.collection_id
        if collection_id is None and not kept.scenes:
            if user_id is None:
                return
            collection_id = await self.doors.make_collection(kept.name, user_id, actor=actor)
            if collection_id is not None:
                tally.collections += 1
        gone = (
            collection_id is not None
            and await self.doors.add_to_collection(collection_id, new, actor=actor) is None
        )
        if gone:
            collection_id = None
        await self._waiting.keep_group(
            source, group_id, KeptGroup(kept.name, collection_id, (*kept.scenes, *new))
        )

    async def _pictures(self, tally: Tally, known: Known, blobs: Path | None, actor: Actor) -> None:
        """The pictures Stash kept on the People, Sites and Tags that came across, as their covers
        where they have none, through the cover door (`StashDoors.picture`), which re-encodes every
        picture that is not a file of the library. Read from this run's copy, or from Stash's blobs
        folder where Stash keeps them as files."""
        if self.doors is None:
            return
        kinds: tuple[tuple[OpinionOn, dict[int, tuple[str, Entity]]], ...] = (
            ("person", known.person),
            ("site", known.site),
            ("tag", known.tag),
        )
        wanted = [
            (kind, local, one.picture)
            for kind, rows in kinds
            for local, one in rows.values()
            if one.picture
        ]
        if not wanted:
            return
        connection = await asyncio.to_thread(reader.open_copy, self.folder / COPY_NAME)
        try:
            for kind, local, checksum in wanted:
                await self._picture(connection, kind, local, str(checksum), blobs, actor, tally)
        finally:
            await asyncio.to_thread(connection.close)

    async def _picture(
        self,
        connection: Any,
        kind: OpinionOn,
        local: str,
        checksum: str,
        blobs: Path | None,
        actor: Actor,
        tally: Tally,
    ) -> None:
        if self.doors is None:
            return
        blob = await asyncio.to_thread(reader.picture_of, connection, checksum, blobs)
        if blob is None:
            tally.pictures_not_found += 1
        elif await self.doors.picture(kind, local, blob, actor=actor):
            tally.pictures += 1

    # --- what belongs to other features --------------------------------------------------------

    async def _opinions(self, tally: Tally, user_id: str, known: Known) -> None:
        """The heart and the stars Stash kept on a performer, a studio or a tag, for whoever asked.

        Theirs, like a file's rating: Stash kept one opinion per library and the person bringing it
        in is the one who held it. Only what they have not said here is filled (`StashDoors`)."""
        kinds: tuple[tuple[OpinionOn, dict[int, tuple[str, Entity]]], ...] = (
            ("person", known.person),
            ("site", known.site),
            ("tag", known.tag),
        )
        for kind, rows in kinds:
            for local, one in rows.values():
                await self._opinion(kind, local, one, user_id, tally)

    async def _opinion(
        self, kind: OpinionOn, local: str, one: Entity, user_id: str | None, tally: Tally
    ) -> None:
        if self.doors is None or user_id is None or (not one.favorite and one.rating is None):
            return
        if await self.doors.opinion(kind, local, user_id, favorite=one.favorite, rating=one.rating):
            tally.favorites += 1 if one.favorite else 0
            tally.entity_ratings += 1 if one.rating is not None else 0

    async def _links(
        self, context: JobContext, stashed: Stashed, tally: Tally, known: Known
    ) -> None:
        """The stash-box ids Stash kept on its performers, studios and tags, linked here.

        Through the stash-box feature's own link, so a link made here is fetched and kept as one a
        person makes, and the enrichment never asks again who that row is. A scene's ids are the
        files' (`_file_links`)."""
        tally.scene_box_ids = len(stashed.box_ids["scene"])
        if self.doors is None:
            return
        key = await _master_key(context)
        kinds: tuple[tuple[LinkOn, str, dict[int, tuple[str, Entity]]], ...] = (
            ("person", "performer", known.person),
            ("site", "studio", known.site),
            ("tag", "tag", known.tag),
        )
        for kind, stash_kind, rows in kinds:
            for one in stashed.box_ids[stash_kind]:
                local = rows.get(one.owner)
                if local is None:
                    continue
                await self._link(kind, local[0], one.endpoint, one.remote_id, key, tally)
                if context.stopping() == "cancel":
                    raise JobCanceled

    async def _file_links(
        self, context: JobContext, stashed: Stashed, tally: Tally, scenes: Mapping[int, str]
    ) -> None:
        """The stash-box ids Stash kept on the scenes matched here, each its file's answer from that
        box, fetched by the id and applied as an exact match is (`StashDoors.link_file`)."""
        if self.doors is None:
            return
        key = await _master_key(context)
        for one in stashed.box_ids["scene"]:
            asset_id = scenes.get(one.owner)
            if asset_id is not None:
                await self._link_file(asset_id, one.endpoint, one.remote_id, key, tally)
                if context.stopping() == "cancel":
                    raise JobCanceled

    async def _link_file(
        self, asset_id: str, endpoint: str, remote_id: str, key: bytes | None, tally: Tally
    ) -> None:
        if self.doors is None:
            return
        went = (await self.doors.link_file(asset_id, endpoint, remote_id, key)).value
        tally.file_links[went] = tally.file_links.get(went, 0) + 1

    async def _link(
        self,
        kind: LinkOn,
        local: str,
        endpoint: str,
        remote_id: str,
        key: bytes | None,
        tally: Tally,
    ) -> None:
        if self.doors is None:
            return
        outcome = await self.doors.link(kind, local, endpoint, remote_id, key)
        tally.links[outcome.value] = tally.links.get(outcome.value, 0) + 1

    async def _marks(
        self,
        stashed: Stashed,
        tally: Tally,
        user_id: str,
        scenes: Mapping[int, str],
        known: Known,
    ) -> None:
        """Each scene marker as a Loop on the video it was made on, where that video is here. A
        marker on a video that is not here waits with it (`_keep_waiting`)."""
        tag_ids = {one.name: local for local, one in known.tag.values()}
        for one in stashed.markers:
            asset_id = scenes.get(one.scene_id)
            if asset_id is None:
                tally.marks_not_here += 1
            elif self.doors is not None:
                await self._mark(asset_id, one, tag_ids, user_id, tally)

    async def _mark(
        self,
        asset_id: str,
        one: Marker,
        tag_ids: Mapping[str, str],
        user_id: str | None,
        tally: Tally,
    ) -> None:
        """One marker as a Loop, through the Loops feature's own writer. Refused where the stretch is
        already marked, which is what makes a second run add none."""
        if self.doors is None:
            return
        start_ms = max(0, round(one.start_seconds * 1000))
        words = [tag_ids[name] for name in one.tags if name in tag_ids]
        if one.end_seconds is None:
            end_ms = start_ms + MOMENT_MS
            moment_tag = await self._made(Subject.TAG, MOMENT_TAG)
            if moment_tag is not None:
                words.append(moment_tag)
        else:
            end_ms = round(one.end_seconds * 1000)
        made = await self.doors.mark(
            asset_id,
            start_ms,
            end_ms,
            name=one.title or (one.tags[0] if one.tags else None),
            tag_ids=words,
            created_by=user_id or "",
        )
        if not made:
            tally.marks_refused += 1
            return
        tally.marks += 1
        tally.marks_from_moments += 1 if one.end_seconds is None else 0

    async def _searches(self, stashed: Stashed, tally: Tally, user_id: str) -> None:
        """Stash's saved filters over scenes and images, kept as saved searches over files."""
        for one in stashed.filters:
            said = translate(
                one,
                tag=stashed.tag_names.get,
                person=stashed.person_names.get,
                site=stashed.site_names.get,
            )
            if said.why:
                tally.filters_not_brought.append(f"{said.name} ({said.why})")
            elif self.doors is not None and await self.doors.keep_search(
                user_id, said.name, said.query
            ):
                tally.saved_searches += 1
            elif self.doors is not None:
                tally.filters_not_brought.append(f"{said.name} (a saved search by that name)")

    # --- what waits for its file ---------------------------------------------------------------

    async def _keep_waiting(
        self,
        stashed: Stashed,
        tally: Tally,
        matched: Mapping[tuple[str, int], str],
        mapping: Mapping[str, str],
        known: Known,
        read_at: int,
        user_id: str,
        source: str,
    ) -> None:
        """Every scene and image whose file is not here, kept in the run's own shape with what
        Stash knew of its file, and the records of the People, Sites and Tags only they carry.

        Replaces what an earlier run kept, whole: a scene that has arrived since is matched now
        and no longer waits, and one still missing is kept once, never twice."""
        markers, box_ids = _by_scene(stashed)
        in_gallery: dict[int, list[list[Any]]] = {}
        for gallery in stashed.galleries:
            for image_id in gallery.images:
                in_gallery.setdefault(image_id, []).append([gallery.stash_id, gallery.name])
        in_group: dict[int, list[list[Any]]] = {}
        for group in stashed.groups:
            for scene_id in group.scenes:
                in_group.setdefault(scene_id, []).append([group.stash_id, group.name])
        came = (
            {("tag", one.name) for _local, one in known.tag.values()}
            | {("site", one.name) for _local, one in known.site.values()}
            | {("person", one.name) for _local, one in known.person.values()}
        )
        rows: list[Waiting] = []
        wanted: set[tuple[str, str]] = set()
        for one in [*stashed.scenes, *stashed.images]:
            if (one.kind, one.stash_id) in matched or not one.files:
                continue
            package: Package = {
                "fields": dict(one.fields),
                "rating": one.rating,
                "o_count": one.o_count,
                "views": one.views,
                "markers": markers.get(one.stash_id, []) if one.kind == "scene" else [],
                "galleries": in_gallery.get(one.stash_id, []) if one.kind == "image" else [],
                "groups": in_group.get(one.stash_id, []) if one.kind == "scene" else [],
                "box_ids": box_ids.get(one.stash_id, []) if one.kind == "scene" else [],
                "resume_ms": one.resume_ms,
                "played_ms": one.played_ms,
                "source": source,
            }
            wanted |= names_in(one.fields, package["markers"]) - came
            files = tuple(
                WaitingFile(
                    path=stash_file.path,
                    here=None
                    if (here := _map_path(stash_file.path, mapping)) is None
                    else str(here),
                    oshash=stash_file.oshash.lower() if stash_file.oshash else None,
                    phash=stash_file.phash,
                    size_bytes=stash_file.size_bytes,
                    duration_ms=stash_file.duration_ms,
                )
                for stash_file in one.files
            )
            title = one.fields.get("title")
            rows.append(
                Waiting(
                    id=new_id(),
                    kind=one.kind,
                    stash_id=one.stash_id,
                    read_at=read_at,
                    user_id=user_id,
                    label=str(title) if title else reader.stash_path(one.files[0].path).name,
                    package=package,
                    files=files,
                )
            )
        # A waiting person's tags and a waiting Site's or tag's parent wait with it, as their own
        # records, unless they came across already.
        wanted = _with_what_they_carry(wanted, stashed, _worn_tags(stashed)) - came
        records = _records_of(stashed, wanted)
        await self._waiting.replace(rows, records)
        tally.marks_waiting = sum(len(one.package["markers"]) for one in rows)
        tally.waiting_scenes = sum(1 for one in rows if one.kind == "scene")
        tally.waiting_images = len(rows) - tally.waiting_scenes
        tally.people_waiting = sum(1 for kind, _name in records if kind == "person")
        tally.sites_waiting = sum(1 for kind, _name in records if kind == "site")
        tally.tags_waiting = sum(1 for kind, _name in records if kind == "tag")
        tally.waiting = [said_waiting(one) for one in rows]

    async def land(self, context: JobContext) -> None:
        """The pass: apply what waits to the files that have arrived for it, then forget it.

        Asked for after a batch of new files is fingerprinted, beside the duplicate sweep and the
        stash-box sweep, and after a scan settles, because a picture is found by where it is and a
        scan is what brings a new place. Every write goes through the run's own writers
        (`_write_item`, `_mark`, `_grow_gallery`, `_ensure`), as Sift's own act from this Stash
        library. With nothing waiting it reads one row and ends."""
        if not await self._waiting.anything():
            return
        arrived = await self._arrived()
        await context.set_units(len(arrived))
        if not arrived:
            return
        actor = Actor.sift(VIA_STASH_LIBRARY)
        key = await _master_key(context) if self.doors is not None else None
        pictures = await self._pictures_chosen()
        tally = Tally()
        for done, (row, asset_id) in enumerate(arrived, start=1):
            await self._land_one(row, asset_id, actor, key, tally, pictures=pictures)
            await self._waiting.landed(row.id)
            if context.stopping() == "cancel":
                raise JobCanceled
            await context.report_progress(done / len(arrived))
        await context.set_note(note_of_landing(len(arrived), tally))
        log.info("stash.landed", files=len(arrived), marks=tally.marks)

    async def arrived_count(self) -> int:
        """How many waiting scenes and pictures a file here now carries, and so how much the pass
        has ahead of it. The pass's own question, counted."""
        if not await self._waiting.anything():
            return 0
        return len(await self._arrived_ids())

    async def _arrived_ids(self) -> dict[str, str]:
        """Which waiting rows a file here now carries, by row: the one question the pass asks,
        answered by one read of the files that carry the waiting rows' keys, through the matcher
        the run uses."""
        keys = await self._waiting.keys()
        if not keys:
            return {}
        roots = [(root.id, Path(root.abs_path)) for root in await self._library.roots()]
        heres: dict[str, tuple[str, str] | None] = {}
        for _waiting_id, one in keys:
            if one.here is not None and one.here not in heres:
                heres[one.here] = _place(roots, Path(one.here))
        carriers = await self._content.carriers_of(
            oshashes=sorted({one.oshash for _id, one in keys if one.oshash}),
            phashes=sorted({one.phash for _id, one in keys if one.phash}),
            places=sorted({place for place in heres.values() if place is not None}),
        )
        finder = Finder(roots, {}, {}, {})
        for carrier in carriers:
            if carrier.kind == "place" and carrier.root_id and carrier.rel_path is not None:
                finder.places.setdefault(
                    (carrier.root_id, carrier.rel_path.casefold()), carrier.asset_id
                )
            elif carrier.kind == "oshash" and carrier.key:
                finder.by_oshash.setdefault(carrier.key, carrier.asset_id)
            elif carrier.kind == "phash" and carrier.key:
                finder.by_phash.setdefault(carrier.key, carrier.asset_id)
        found: dict[str, str] = {}
        for waiting_id, one in keys:
            if waiting_id in found:
                continue
            hit = finder.find_here(
                StashFile(one.path, oshash=one.oshash, phash=one.phash),
                None if one.here is None else Path(one.here),
            )
            if hit is not None:
                found[waiting_id] = hit[0]
        return found

    async def _arrived(self) -> list[tuple[Waiting, str]]:
        found = await self._arrived_ids()
        return [(row, found[row.id]) for row in await self._waiting.rows(list(found))]

    async def _pictures_chosen(self) -> Pictures | None:
        """Whether the run that left rows waiting was asked to bring pictures, and from where: the
        choice is the read's (`choose`), kept in its plan; None when it was not asked for."""
        try:
            plan = json.loads(
                await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        return Pictures(_blobs_of(plan)) if plan.get("pictures") else None

    async def _land_one(
        self,
        row: Waiting,
        asset_id: str,
        actor: Actor,
        key: bytes | None,
        tally: Tally,
        *,
        pictures: Pictures | None = None,
    ) -> None:
        """One waiting scene or picture onto the file that arrived for it."""
        package = row.package
        user_id = row.user_id
        if user_id is not None and not await self._still_a_user(user_id):
            user_id = None
        markers = package.get("markers") or []
        for kind, name in sorted(names_in(package.get("fields") or {}, markers)):
            await self._ensure(kind, name, actor, user_id, key, tally, pictures=pictures)
        item = Item(
            stash_id=row.stash_id,
            kind=row.kind,
            files=(),
            fields=dict(package.get("fields") or {}),
            rating=package.get("rating"),
            o_count=int(package.get("o_count") or 0),
            views=int(package.get("views") or 0),
            resume_ms=package.get("resume_ms"),
            played_ms=int(package.get("played_ms") or 0),
        )
        await self._write_item(item, asset_id, actor, user_id, tally)
        for endpoint, remote_id in package.get("box_ids") or []:
            await self._link_file(asset_id, str(endpoint), str(remote_id), key, tally)
        for marker in markers:
            one = Marker(
                scene_id=row.stash_id,
                title=str(marker.get("title") or ""),
                start_seconds=float(marker.get("start") or 0),
                end_seconds=None if marker.get("end") is None else float(marker["end"]),
                tags=tuple(str(name) for name in marker.get("tags") or ()),
            )
            tag_ids = {
                name: local
                for name in one.tags
                if (local := await self._named("tag", name, creating=False)) is not None
            }
            await self._mark(asset_id, one, tag_ids, user_id, tally)
        for gallery_id, name in package.get("galleries") or []:
            await self._grow_gallery(
                str(package.get("source") or ""),
                int(gallery_id),
                str(name),
                [asset_id],
                actor,
                tally,
            )
        for group_id, name in package.get("groups") or []:
            await self._grow_group(
                str(package.get("source") or ""),
                int(group_id),
                str(name),
                [asset_id],
                actor,
                user_id,
                tally,
            )

    async def _ensure(
        self,
        kind: str,
        name: str,
        actor: Actor,
        user_id: str | None,
        key: bytes | None,
        tally: Tally,
        depth: int = 0,
        *,
        pictures: Pictures | None = None,
    ) -> str | None:
        """A person, Site or tag a landing file carries, made from its kept record where it waited
        and is still absent, with the heart, the stars and the stash-box ids Stash kept on it.

        One that did not wait (it came across with the run, or was never Stash's) is left to the
        file's own write, which finds it or makes it. A kept record is applied once and then
        forgotten, so the next file carrying the same person finds her made."""
        record = await self._waiting.entity(kind, name)
        subject = _KINDS[kind]
        if record is None:
            return await self._named(kind, name, creating=False)
        entity = _entity_from(name, record)
        if entity.parent and depth < _DEEPEST and kind in ("site", "tag"):
            parent = await self._ensure(
                kind, entity.parent, actor, user_id, key, tally, depth + 1, pictures=pictures
            )
        else:
            parent = None
        # The tags a person or a Site wears come first, from their own kept records, so the merge
        # below finds them made rather than inventing each from its name alone.
        worn = entity.fields.get("tags")
        if kind != "tag" and isinstance(worn, list) and depth < _DEEPEST:
            for tag in worn:
                await self._ensure("tag", str(tag), actor, user_id, key, tally, depth + 1)
        local = await self._made(subject, name)
        if local is None:
            return None
        await self._merge(subject, local, entity, actor)
        if kind == "tag" and parent is not None:
            async with self._db.write() as writing:
                if await file_under_if_unfiled(writing, local, parent, actor=actor):
                    tally.tags_filed_under_a_parent += 1
        opinion_on: OpinionOn = (
            "person" if kind == "person" else "site" if kind == "site" else "tag"
        )
        await self._opinion(opinion_on, local, entity, user_id, tally)
        for endpoint, remote_id in record.get("box_ids") or []:
            await self._link(opinion_on, local, str(endpoint), str(remote_id), key, tally)
        if pictures is not None and entity.picture:
            await self._picture_from_copy(opinion_on, local, entity.picture, pictures, actor, tally)
        await self._waiting.entity_landed(kind, name)
        tally.people += 1 if kind == "person" else 0
        tally.sites += 1 if kind == "site" else 0
        tally.tags += 1 if kind == "tag" else 0
        return local

    async def _picture_from_copy(
        self,
        kind: OpinionOn,
        local: str,
        checksum: str,
        pictures: Pictures,
        actor: Actor,
        tally: Tally,
    ) -> None:
        """One picture for a person, Site or tag that landed after the run, read from the copy the
        read left (a checksum names the same picture in any copy, so a newer read is as good)."""
        copy = self.folder / COPY_NAME
        if not await asyncio.to_thread(copy.is_file):
            tally.pictures_not_found += 1
            return
        try:
            connection = await asyncio.to_thread(reader.open_copy, copy)
        except NotAStashDatabase:
            tally.pictures_not_found += 1
            return
        try:
            await self._picture(connection, kind, local, checksum, pictures.blobs, actor, tally)
        finally:
            await asyncio.to_thread(connection.close)

    async def _still_a_user(self, user_id: str) -> bool:
        row = await self._db.fetch_one(_USER_EXISTS, (user_id,))
        return row is not None

    # --- a new library ---------------------------------------------------------------------------

    async def _wait_for_first_scan(self, context: JobContext, user_id: str) -> None:
        """In a library made for this run, hold the run until its first scan has finished.

        The task is queued in the new library before it is ever opened, so it can be claimed before
        that library holds a single file. Held rather than failed: the wait passes on its own, and a
        hold hands back its attempt. The scan is asked for once, here, since nothing else knows the
        folders are new; it is the Tasks screen's own Scan task."""
        plan_path = self.folder / PLAN_NAME
        plan = json.loads(await asyncio.to_thread(plan_path.read_text, encoding="utf-8"))
        if not plan.get("after_first_scan"):
            return
        scanning = [kind for kind, family in registered_families().items() if family is Family.SCAN]
        for kind in scanning:
            for state in (JobState.QUEUED, JobState.RUNNING):
                if (await context.queue.list(job_type=kind, state=state, limit=1)).total:
                    raise JobHeld(WAITING_FOR_SCAN, retry_in=SCAN_WAIT_SECONDS)
        if not plan.get("scan_asked"):
            task = get_schedule(SCAN_TASK)
            if task is not None and task.job_type is not None:
                await context.queue.enqueue(
                    task.job_type, dict(task.payload), dedupe=True, requested_by=user_id
                )
            plan["scan_asked"] = True
            await asyncio.to_thread(write_json_whole, plan_path, plan)
            raise JobHeld(WAITING_FOR_SCAN, retry_in=SCAN_WAIT_SECONDS)
        plan["after_first_scan"] = False
        await asyncio.to_thread(write_json_whole, plan_path, plan)

    async def bring_into_new(
        self, name: str, actor: Viewer, *, pictures: bool = False, blobs: str | None = None
    ) -> str:
        """Make a library beside this one for the last read, and switch to it. Answers its id.

        Refused before anything is made: nothing read, no folder matched (a new library would have
        nothing to scan), or nothing here that makes libraries. The choice about pictures is kept
        with the read before the read goes with the new library (`choose`)."""
        read = await self.last_read()
        if read is None:
            raise StashRefused("Read a Stash database first.")
        mapping: dict[str, str] = dict(read["mapping"])
        if not mapping:
            raise StashRefused(
                "None of Stash's folders is a folder of this library, so a new library would "
                "have no files. Add the folders first, or bring it into this library."
            )
        if self.doors is None:
            raise StashRefused("Sift can't make a library from here.")
        await self.choose(pictures=pictures, blobs=blobs)
        roots = {Path(root.abs_path): root.name for root in await self._library.roots()}
        grants = [Path(grant.abs_path) for grant in await self._library.grants()]
        wanted = [(Path(path), roots.get(Path(path), Path(path).name)) for path in mapping.values()]

        async def seed(database: Path, data_dir: Path) -> None:
            await self._seed(database, data_dir, wanted, grants, actor.id)

        return await self.doors.new_library(name, actor, seed)

    async def _seed(
        self,
        database: Path,
        data_dir: Path,
        roots: Sequence[tuple[Path, str]],
        grants: Sequence[Path],
        user_id: str,
    ) -> None:
        """Give a library just made the matched folders, this read, and the queued run.

        The grants that hold those folders go with them, so its folder picker sees what this one's
        did. The report of any earlier run stays behind: it is about this library."""
        target = data_dir / FOLDER
        plan = json.loads(
            await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
        )
        plan["after_first_scan"] = True
        plan.pop("scan_asked", None)
        await asyncio.to_thread(reader.copy_in, self.folder / COPY_NAME, target / COPY_NAME)
        await asyncio.to_thread(write_json_whole, target / PLAN_NAME, plan)
        fresh = Database(database, readers=1)
        await fresh.connect()
        try:
            store = LibraryStore(fresh, self._settings)
            for grant in grants:
                if any(_inside(root, grant) for root, _name in roots):
                    await store.grant(grant)
            for path, root_name in roots:
                await store.create_root(name=root_name, abs_path=path)
            await JobQueue(fresh).enqueue(
                STASH_IMPORT, {}, requested_by=user_id, require_handler=False
            )
        finally:
            await fresh.close()
        log.info("stash.new_library_seeded", folders=len(roots))


@dataclass
class Known:
    """Which row here each of Stash's performers, studios and tags became, with what was read."""

    person: dict[int, tuple[str, Entity]] = field(default_factory=dict)
    site: dict[int, tuple[str, Entity]] = field(default_factory=dict)
    tag: dict[int, tuple[str, Entity]] = field(default_factory=dict)


#: What the task says while a new library's first scan runs.
WAITING_FOR_SCAN = "Waiting for this library's first scan to finish."

#: Whether a waiting row's User is still a User. A User's table is not a permission table.
_USER_EXISTS = "SELECT 1 FROM users WHERE id = ?"

#: How many rows of each kind wear the mark of a Stash row attached to nothing. Counts, for the
#: line on the pane; the walls list them (`created=stash_unattached`).
_UNATTACHED = {
    "people": "SELECT COUNT(*) AS n FROM people WHERE created_by_via = ?",
    "sites": "SELECT COUNT(*) AS n FROM sites WHERE created_by_via = ?",
    "tags": "SELECT COUNT(*) AS n FROM tags WHERE created_by_via = ?",
}


def _by_scene(
    stashed: Stashed,
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, list[list[str]]]]:
    """Each scene's markers, and its stash-box ids as `[endpoint, id]`, as a waiting row keeps them."""
    markers: dict[int, list[dict[str, Any]]] = {}
    for marker in stashed.markers:
        markers.setdefault(marker.scene_id, []).append(
            {
                "title": marker.title,
                "start": marker.start_seconds,
                "end": marker.end_seconds,
                "tags": list(marker.tags),
            }
        )
    box_ids: dict[int, list[list[str]]] = {}
    for box in stashed.box_ids["scene"]:
        box_ids.setdefault(box.owner, []).append([box.endpoint, box.remote_id])
    return markers, box_ids


def _records_of(stashed: Stashed, wanted: set[tuple[str, str]]) -> dict[tuple[str, str], Any]:
    """The kept record of every waiting person, Site and tag, with the parents of each that also
    wait, keyed by kind and name."""
    rows = {
        "person": (stashed.performers, "performer"),
        "site": (stashed.studios, "studio"),
        "tag": (stashed.tags, "tag"),
    }
    by_name = {
        (kind, one.name): (one, stash_kind)
        for kind, (entities, stash_kind) in rows.items()
        for one in entities
    }
    box_ids: dict[tuple[str, int], list[BoxId]] = {}
    for stash_kind in ("performer", "studio", "tag"):
        for box in stashed.box_ids[stash_kind]:
            box_ids.setdefault((stash_kind, box.owner), []).append(box)
    records: dict[tuple[str, str], Any] = {}
    for kind, name in sorted(wanted):
        found = by_name.get((kind, name))
        if found is None:
            continue
        one, stash_kind = found
        records[(kind, name)] = _entity_record(one, box_ids.get((stash_kind, one.stash_id), []))
    return records


def _blobs_of(plan: Mapping[str, Any]) -> Path | None:
    """Stash's blobs folder as the choice named it, or None where it keeps its pictures in its
    database (or nobody named one)."""
    blobs = plan.get("blobs")
    return Path(str(blobs)) if blobs else None


@dataclass(frozen=True)
class Pictures:
    """The pictures were asked for: read from the copy, or from this blobs folder where Stash keeps
    them as files."""

    blobs: Path | None = None


async def _master_key(context: JobContext) -> bytes | None:
    try:
        return await context.master_key()
    except RuntimeError:
        return None


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
    except ValueError:
        return False
    return True


def _summary_of(copy: Path) -> Summary:
    connection = reader.open_copy(copy)
    try:
        return reader.summarize(connection)
    finally:
        connection.close()


def ran_after(run: TaskRun | None, read_at: int) -> dict[str, Any] | None:
    """The run over the last read, when one finished its work: when, and the task's own sentence.

    From the queue's record, the one every page that starts a long run reads, so a reload says what
    the run said. A run that ended before the read belongs to an earlier read and says nothing about
    this one. Without this the screen would offer to import again a library that had come across.
    """
    if run is None or run.finished_at is None or run.finished_at < read_at:
        return None
    return {"ended_at": run.finished_at, "said": run.note or ""}


def register_stash_handlers(migration: StashMigration) -> None:
    """Claim the task and the pass. Called once, at boot, beside the other handlers."""
    from sift.kernel.jobs import register_handler

    async def handle(context: JobContext) -> None:
        await migration.run(context)

    async def land(context: JobContext) -> None:
        await migration.land(context)

    register_handler(STASH_IMPORT, handle, name="Importing a Stash library", alone=True)
    # One at a time: the settle that asks for it collapses a batch's requests onto one waiting
    # row. Beside a run it is harmless rather than prevented: every write either makes is a fill
    # or a find, so a row both apply is applied once in effect. A row the run keeps again after
    # the pass has landed it is landed, to no effect, by the next pass.
    register_handler(
        STASH_ARRIVED, land, name="Importing what Stash kept for arrived files", alone=True
    )


#: The Stash migration.
SERVICE: Part[StashMigration] = Part("stash_migration")
