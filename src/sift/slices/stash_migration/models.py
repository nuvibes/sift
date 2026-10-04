# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Stash routes take and answer with."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from sift.kernel.wire import Wire

#: The longest path a person can type. Longer is not a path anybody typed.
MAX_PATH = 1024

#: The longest name a library may be given here: the libraries feature's own limit checks it.
MAX_NAME = 64


class ReadStash(Wire):
    """Stash's database file, or the folder Stash keeps it in, by where it is on the device Sift
    runs on. A folder is what a browser can pick; the database is found in it by name."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH)


class StashUnattached(Wire):
    """How many People, Sites and Tags a Stash library made here that Stash attached to nothing
    are still here, by kind: the rows each wall lists under `created=stash_unattached`."""

    people: int = 0
    sites: int = 0
    tags: int = 0


class StashRan(Wire):
    """The run over the last read, once it finished: when, and the task's own sentence."""

    ended_at: int
    said: str


class StashRead(Wire):
    """What a Stash database holds, counted, and which folder here each of its folders is.

    `summary` is every count the screen shows before a run (`reader.Summary`). `mapping` is Stash's
    folder beside the folder in this library it matched, for each one that matched.
    """

    summary: dict[str, Any]
    mapping: dict[str, str]
    #: The Stash database this read came from, so a screen opened later names what it shows.
    source: str | None = None
    #: The run over this read, once one has finished. None before any has.
    ran: StashRan | None = None
    #: How many scenes and pictures wait for their files now. Falls as they arrive.
    waiting: int = 0
    #: Stash's blobs folder, offered or chosen, where Stash keeps pictures as files rather than in
    #: its database; None where every picture is in the database (nothing to choose).
    blobs: str | None = None
    #: The rows Stash attached to nothing, still here. Falls as they are deleted.
    unattached: StashUnattached = Field(default_factory=StashUnattached)


class StashRunStarted(Wire):
    """The task a run is, to follow on the screen."""

    job_id: str


class BringStash(Wire):
    """How to bring the last read in: whether with the pictures Stash kept on performers, studios
    and tags, and from which folder where Stash keeps them as files."""

    model_config = ConfigDict(extra="forbid")

    pictures: bool = False
    blobs: str | None = Field(default=None, max_length=MAX_PATH)


class NewStashLibrary(BringStash):
    """A library to make for the last read, by name. The folder it goes in is the server's."""

    name: str = Field(min_length=1, max_length=MAX_NAME)


class StashSwitch(Wire):
    """The new library is made and the switch to it is arranged: the screen waits for Sift to
    come back on it, as it does for any library it opens."""

    switching: bool
    library: str


class StashWaitingMarker(Wire):
    """A marker that waits with its video: its title (its first tag where it has none), where it
    starts and where it ends, in milliseconds; no end for a moment."""

    title: str
    start_ms: int
    end_ms: int | None


class StashWaitingRow(Wire):
    """One scene or picture that waits for its file, and what waits on it.

    `paths` are where Stash had its files, as Stash spelt them. `kind` is `scene` or `image`, in
    Stash's words; the screen calls them files and pictures.
    """

    id: str
    kind: str
    label: str
    paths: list[str]
    rating: int | None
    markers: list[StashWaitingMarker]
    people: list[str]
    sites: list[str]
    tags: list[str]


class StashWaitingPage(Wire):
    """One page of what waits, and how many wait in all."""

    total: int
    offset: int
    rows: list[StashWaitingRow]
