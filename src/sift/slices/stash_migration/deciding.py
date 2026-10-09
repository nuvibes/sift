# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Stash read decides, and the plain reads and shapes the run is made of."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sift.kernel.jobs import JobContext, TaskRun
from sift.kernel.paths import PathEscape, confine
from sift.kernel.vocabulary import VIA_STASH_LIBRARY, VIA_STASH_UNATTACHED
from sift.slices.stash_migration import reader
from sift.slices.stash_migration.reader import (
    BoxId,
    Entity,
    Gallery,
    Group,
    Item,
    Marker,
    SavedFilter,
    StashFile,
    Summary,
)
from sift.slices.stash_migration.waiting import Waiting

#: What Stash calls its database when nothing says otherwise, and the file beside it that can say
#: otherwise (its `database:` line). A browser picks Stash's FOLDER, since a page can pick only
#: folders; the database is found in it by these two names, never by listing what the folder holds.
STASH_DATABASE = "stash-go.sqlite"
STASH_CONFIG = "config.yml"
_DATABASE_LINE = re.compile(r"^database:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


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


@dataclass
class Known:
    """Which row here each of Stash's performers, studios and tags became, with what was read."""

    person: dict[int, tuple[str, Entity]] = field(default_factory=dict)
    site: dict[int, tuple[str, Entity]] = field(default_factory=dict)
    tag: dict[int, tuple[str, Entity]] = field(default_factory=dict)


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
