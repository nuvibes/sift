# SPDX-License-Identifier: AGPL-3.0-or-later
"""The filter language's words: every field a filter can name, and the shapes a parsed query is
made of."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NamedTuple

from sift.kernel.access import (
    FolderDepth,
)
from sift.kernel.access.constraints import Filing


class Field(StrEnum):
    """The tokens: the short name you TYPE. `FILTERS` below holds the one you READ."""

    PEOPLE = "people"
    SITES = "sites"
    TAGS = "tags"
    COLLECTIONS = "collections"
    #: A set of pictures that arrived together: one shoot, one gallery, one folder.
    PHOTO_SETS = "photo_sets"
    #: The song a file carries. Plural like the family; `song:` is its other spelling.
    SONGS = "songs"
    #: Video, image or GIF, called `media:` as the screen calls it Media.
    MEDIA = "media"
    #: mp4, mkv, webm. What the file IS, as against what kind of thing it holds.
    FILETYPE = "filetype"
    #: Videos with a marked moment in them. Presence only (see `PRESENCE_FIELDS`).
    LOOPS = "loops"
    RATING = "rating"
    O_COUNT = "o_count"
    FAV = "fav"
    SHARING = "sharing"
    IN = "in"
    ADDED = "added"
    DURATION = "duration"
    #: How big the picture is, by its shorter side: `4k`, `1080p`, `720p+`, or a plain number.
    RESOLUTION = "resolution"
    #: How much room it takes, in the units people write: `500mb+`, `2gb-`.
    SIZE = "size"
    VCODEC = "vcodec"
    #: The audio codec, and `none` for a file with no sound at all.
    ACODEC = "acodec"
    #: The name on disk, matched anywhere inside it.
    FILENAME = "filename"
    #: The name somebody gave a file, which is not the name it has on disk.
    TITLE = "title"
    #: The track a file is set to, matched anywhere inside it.
    MUSIC = "music"
    #: Whether this user has opened it. `-viewed` is everything nobody here has watched.
    VIEWED = "viewed"
    #: Which way up the picture is: `landscape`, `portrait` or `square`.
    ORIENTATION = "orientation"
    #: How a file came to be enriched without a person doing it: `stash`, `faces` or `folder`.
    ENRICHED = "enriched"
    #: Where a file stands with enrichment from outside: `local`, `never`, `today`, `week`, and on.
    ENRICHMENT = "enrichment"
    #: Who made the file: `compress`, `edit`, `swap`, `download` or `library` (`FILE_MADE_BY`).
    CREATED = "created"
    #: What the people on a file are like, read off each person's record: `hair:blonde`.
    GENDER = "gender"
    HAIR = "hair"
    EYES = "eyes"
    ETHNICITY = "ethnicity"
    #: Where a person is from, as the ISO code the stash-boxes send.
    NATIONALITY = "nationality"
    BREASTS = "breasts"
    #: Whether somebody in it MAKES the edits, as against appearing in them. `pmv:yes`, `pmv:no`.
    PMV = "pmv"
    #: Ten-centimetre bands (`height:160-169`), since no two people share an exact height.
    HEIGHT = "height"
    #: An age in whole years from a person's birth date and today: `age:27`, `age:25-29`.
    AGE = "age"
    #: The YEAR the thing in the file was published: `released:2021`.
    RELEASED = "released"
    #: The Site a Site belongs to, by ID: everything published under one network.
    NETWORK = "network"
    #: The files that share a song with one file: its same-music group, one hop, never itself.
    SAME_MUSIC = "same_music"
    #: The files similar to one file, by ID (`FilterCompiler._lookalikes`).
    LIKE = "like"
    #: The files one product gave up on: `left_out:thumbnails`, `left_out:faces`.
    LEFT_OUT = "left_out"
    #: One person's folder-filed files with a face still unnamed, by the person's ID.
    UNNAMED_FACE = "unnamed_face"


#: Every OTHER spelling that means one of the tokens above: every label a filter has on the list,
#: and every retired token, kept for good because a saved search or a shared link still carries it.
ALIASES: dict[str, Field] = {
    # THE OLD SPELLING OF THE SITES FIELD, and it is kept for good.
    "platforms": Field.SITES,
    "folder": Field.IN,
    "file_type": Field.FILETYPE,
    "favorites": Field.FAV,
    "sharing_status": Field.SHARING,
    "file_size": Field.SIZE,
    "video_codec": Field.VCODEC,
    "audio_codec": Field.ACODEC,
    "file_name": Field.FILENAME,
    "name": Field.TITLE,
    "enriched_by": Field.ENRICHED,
    "created_by": Field.CREATED,
    # The renamed column's label.
    "asked_a_stash-box": Field.ENRICHMENT,
    "hair_colour": Field.HAIR,
    "eye_colour": Field.EYES,
    "breast_type": Field.BREASTS,
    "pmv_creator": Field.PMV,
    # The person record's spelling of the same six, which the People wall's facets are keyed by.
    "hair_color": Field.HAIR,
    "eye_color": Field.EYES,
    "country": Field.NATIONALITY,
    "height_cm": Field.HEIGHT,
    "release_date": Field.RELEASED,
    # The view-status filter's label.
    "view_status": Field.VIEWED,
    # The label of `like:`, which is the name the strip and the file menu give the same answer.
    "similar_to_this": Field.LIKE,
    # The one-thing spelling of `songs:`, since a file carries one song.
    "song": Field.SONGS,
    "face_still_unnamed": Field.UNNAMED_FACE,
    # Retired tokens.
    "type": Field.MEDIA,
}


#: The fields naming something in the catalog, looked up and scoped before they mean anything.
ENTITY_FIELDS = frozenset(
    {
        Field.PEOPLE,
        Field.SITES,
        Field.TAGS,
        Field.COLLECTIONS,
        Field.PHOTO_SETS,
        Field.SONGS,
        Field.IN,
    }
)


_DIMENSION: dict[Field, str] = {
    Field.TAGS: "tags",
    Field.PEOPLE: "people",
    Field.SITES: "sites",
    Field.COLLECTIONS: "collections",
    Field.PHOTO_SETS: "photo_sets",
    Field.SONGS: "songs",
}


#: The fields `-tags`, `tags:none` and `tags:any` ask about; not `in:`, since every file is somewhere.
PRESENCE_FIELDS = frozenset(
    {
        Field.TAGS,
        Field.PEOPLE,
        Field.SITES,
        Field.COLLECTIONS,
        Field.PHOTO_SETS,
        Field.SONGS,
        Field.LOOPS,
        Field.RATING,
        Field.VIEWED,
    }
)


_PRESENCE: dict[Field, str] = {
    Field.TAGS: "has_tags",
    Field.PEOPLE: "has_people",
    Field.SITES: "has_sites",
    Field.COLLECTIONS: "has_collections",
    Field.PHOTO_SETS: "has_photo_sets",
    Field.SONGS: "has_songs",
    Field.LOOPS: "has_loops",
    Field.RATING: "rated",
    # "Touched at all", not "has a view stamped on it".
    Field.VIEWED: "opened",
}


#: What `fav:` accepts, either way round. Anything else is a value that means no asset.
_TRUE = frozenset({"yes", "true", "1", "y", "on"})


_FALSE = frozenset({"no", "false", "0", "n", "off"})


#: `Nd` / `Nh` / `Nw`: how long ago.
_AGO_UNITS = {"h": 3_600, "d": 86_400, "w": 604_800}


#: `Ns` / `Nm` / `Nh`: how long something runs. Milliseconds, to match the column.
_LENGTH_UNITS = {"s": 1_000, "m": 60_000, "h": 3_600_000}


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_NUMBER = re.compile(r"^\d+$")


_AGO = re.compile(r"^(\d+)([hdw])$")


_LENGTH = re.compile(r"^(\d+)([smh])$")


#: The most filters one query may carry, and the longest a value may be.
MAX_TERMS = 32


MAX_VALUE = 200


#: How deeply groups may nest.
MAX_DEPTH = 8


#: The largest number a value may carry.
MAX_NUMBER = 9_999_999_999


_FIELD_ORDER = {found: position for position, found in enumerate(Field)}


class Unreadable(ValueError):
    """A value that cannot describe an asset. Never escapes this module."""


class Problem(NamedTuple):
    """A filter whose value nothing could act on, and the reason, for the answer to carry."""

    field: str
    value: str
    reason: str


class Op(StrEnum):
    """How a group's parts combine."""

    ALL = "all"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class Term:
    """One `field:value`, exactly as it was written. The unit both front-ends produce."""

    field: Field
    value: str


@dataclass(frozen=True, slots=True)
class Presence:
    """Whether a dimension is there at all: `-tags`, `tags:none`, `tags:any`."""

    field: Field
    present: bool


@dataclass(frozen=True, slots=True)
class Group:
    """Parts that combine. Built through `group` below, never constructed bare."""

    op: Op
    parts: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class Negated:
    """The opposite of what is inside it."""

    part: Node


Node = Term | Presence | Group | Negated


def group(op: Op, parts: tuple[Node, ...]) -> Node:
    """A group of parts, flattened and put in a fixed order."""
    flattened: list[Node] = []
    for part in parts:
        if isinstance(part, Group) and part.op is op:
            flattened.extend(part.parts)
        else:
            flattened.append(part)
    if len(flattened) == 1:
        return flattened[0]
    return Group(op, tuple(sorted(flattened, key=_ordering)))


def _ordering(node: Node) -> tuple[object, ...]:
    """A total order over nodes, so a group's parts have one spelling."""
    if isinstance(node, Term):
        return (0, _FIELD_ORDER[node.field], node.value, 0)
    if isinstance(node, Presence):
        return (1, _FIELD_ORDER[node.field], int(node.present))
    # A refused value sits beside the value it refuses, so its chip does not move when it turns.
    if isinstance(node, Negated) and isinstance(node.part, Term):
        return (0, _FIELD_ORDER[node.part.field], node.part.value, 1)
    if isinstance(node, Negated):
        return (2, _ordering(node.part))
    return (3, node.op.value, tuple(_ordering(part) for part in node.parts))


#: A query nothing satisfies: a choice with no options.
IMPOSSIBLE = Group(Op.ANY, ())


EVERYTHING = Group(Op.ALL, ())


@dataclass(frozen=True, slots=True)
class Query:
    """A parsed query: the free text, and the tree of filters."""

    text: str | None = None
    where: Node = field(default=EVERYTHING)

    #: How far below a named folder `in:` reaches.
    folder_depth: FolderDepth = FolderDepth.SUBTREE

    #: The History lines this request is filtered to:
    filings: tuple[Filing, ...] = ()

    def leaves(self) -> tuple[Term | Presence, ...]:
        """Every filter in the tree, whatever it is nested inside."""
        return tuple(_leaves(self.where))


def _leaves(node: Node) -> list[Term | Presence]:
    if isinstance(node, Term | Presence):
        return [node]
    if isinstance(node, Negated):
        return _leaves(node.part)
    return [leaf for part in node.parts for leaf in _leaves(part)]
