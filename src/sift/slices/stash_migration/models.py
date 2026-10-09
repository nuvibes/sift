# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Stash routes take and answer with."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from sift.kernel.wire import Wire

MAX_PATH = 1024

MAX_NAME = 64


class ReadStash(Wire):
    """Stash's database file, or its folder on this device, where it is found by name."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH)


class StashUnattached(Wire):
    """How many People, Sites and Tags from Stash attached to nothing are still here, by kind."""

    people: int = 0
    sites: int = 0
    tags: int = 0


class StashRan(Wire):
    """The run over the last read, once it finished: when, and the task's own sentence."""

    ended_at: int
    said: str


class StashRead(Wire):
    """What a Stash database holds, counted, and which folder here each of its folders matched."""

    summary: dict[str, Any]
    mapping: dict[str, str]
    source: str | None = None
    ran: StashRan | None = None
    waiting: int = 0
    #: Where Stash keeps pictures as files; None when all are in the database.
    blobs: str | None = None
    unattached: StashUnattached = Field(default_factory=StashUnattached)


class StashRunStarted(Wire):
    """The task a run is, to follow on the screen."""

    job_id: str


class BringStash(Wire):
    """Bring the last read in, with or without Stash's pictures and from which folder."""

    model_config = ConfigDict(extra="forbid")

    pictures: bool = False
    blobs: str | None = Field(default=None, max_length=MAX_PATH)


class NewStashLibrary(BringStash):
    """A library to create for the last read, by name."""

    name: str = Field(min_length=1, max_length=MAX_NAME)


class StashSwitch(Wire):
    """The new library is created and the switch to it arranged."""

    switching: bool
    library: str


class StashWaitingMarker(Wire):
    """A marker waiting with its video: title, start and end in milliseconds."""

    title: str
    start_ms: int
    end_ms: int | None


class StashWaitingRow(Wire):
    """One scene or picture waiting for its file, with Stash's own paths and kind."""

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
