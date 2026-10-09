# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the running application's parts are kept, and the only place they are read from.

A part's name and type are declared once; only this module touches `app.state`; a gate holds it."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import FastAPI, Request
from starlette.requests import HTTPConnection

from sift.kernel import where
from sift.kernel.access import Repository, Viewer
from sift.kernel.changes import ChangeBus
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    EntityStateStore,
    LibraryStore,
    UserStateStore,
)
from sift.kernel.covers import CoverPictures, SubjectCovers
from sift.kernel.db import Database, SqliteCapabilities
from sift.kernel.diagnostics import (
    LoopBacklogWatch,
    LoopWatchdog,
    ReadPoolWatch,
    SlowestWork,
    ThreadPoolWatch,
    WidestReads,
)
from sift.kernel.enrichment import Enricher, Naming
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobQueue, WorkAhead, WorkerPool
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.lanes import StorageLanes
from sift.kernel.media import Accelerator
from sift.kernel.seams import (
    BoxPicturesSeam,
    DisagreementSeam,
    FaceEvidenceSeam,
    FilterEngine,
    ForgetGoneSeam,
    PhotoSetSeam,
    RecognitionSeam,
    ReindexSeam,
    SemanticSeam,
    SettingsSeam,
    StillSeam,
    UrlImporter,
)
from sift.kernel.workbench import Recorder, Workbench


class Part[T]:
    """One part: the name it is stored under and the type it holds; parts compare by name."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Part({self.name!r})"


def provide[T](app: FastAPI, part: Part[T], value: T) -> None:
    """Publish a part. Called from the composition root and nowhere else."""
    setattr(app.state, part.name, value)


def part_of[T](connection: HTTPConnection, part: Part[T]) -> T:
    """The part, typed as declared: the one place the untyped bag becomes typed; never None."""
    try:
        return getattr(connection.app.state, part.name)  # type: ignore[no-any-return]
    except AttributeError:
        raise RuntimeError(f"{part.name!r} was never built; start-up did not publish it") from None


def part_of_app[T](app: FastAPI, part: Part[T]) -> T:
    """The part, read from the application itself: start-up and test helpers."""
    try:
        return getattr(app.state, part.name)  # type: ignore[no-any-return]
    except AttributeError:
        raise RuntimeError(f"{part.name!r} was never built; start-up did not publish it") from None


def part_or_none[T](connection: HTTPConnection, part: Part[T]) -> T | None:
    """The part, or None where its absence is an answer rather than a fault."""
    found: T | None = getattr(connection.app.state, part.name, None)
    return found


def part_of_app_or_none[T](app: FastAPI, part: Part[T]) -> T | None:
    """The part from the application, or None while it is not built yet."""
    found: T | None = getattr(app.state, part.name, None)
    return found


def hold[T](connection: HTTPConnection, part: Part[T], value: T) -> None:
    """Publish a part from a running request, for state built on first use, like the self-test."""
    setattr(connection.app.state, part.name, value)


# The kernel's own parts; a feature declares its own.

ACCESS: Part[Repository] = Part("access")
"""The permission resolver. The only sanctioned way to read an asset or a folder."""

DATABASE: Part[Database] = Part("database")
CONTENT: Part[ContentStore] = Part("content")
LIBRARY: Part[LibraryStore] = Part("library")
USER_STATE: Part[UserStateStore] = Part("user_state")
#: The pin on named things, apart from the file opinions above.
ENTITY_STATE: Part[EntityStateStore] = Part("entity_state")
QUEUE: Part[JobQueue] = Part("queue")
#: Work not yet made into jobs; not "backlog", which names the loop's ready queue below.
WORK_AHEAD: Part[WorkAhead] = Part("work_ahead")
POOL: Part[WorkerPool] = Part("pool")
#: What each run of the long passes cost, and the time-left estimate read from it.
LEDGER: Part[Ledger] = Part("ledger")
WORKBENCH: Part[Workbench] = Part("workbench")
ENRICHER: Part[Enricher] = Part("enricher")
#: Turning a name into a row of this library, or finding the row it already is.
NAMING: Part[Naming] = Part("naming")
RECORDER: Part[Recorder] = Part("recorder")
"""Somewhere to write down what a decision did. The workbench's own store, seen as the one call a
feature needs, so a slice can leave a receipt without importing the shell that draws them."""
"""What a stash-box's answer may write, and who writes it. Every area registers a writer for the
subject it owns; the rules themselves live in `kernel/enrichment`."""
SETTINGS: Part[Settings] = Part("settings")
HARDWARE: Part[HardwareReport] = Part("hardware")

ACCELERATOR: Part[Accelerator] = Part("accelerator")
"""What the graphics card is worth asking for, and whether it still is.

Beside the hardware report rather than folded into it, because they answer different kinds of
question. The report is a description of the machine, taken once and true for as long as the process
lives. This one is a running judgement about whether the description is holding up, and it changes
while Sift is running, which is exactly why the two must not be the same object.

One per process. Two features read it (the preview builder and the player) and there is one
card, so a fault either of them meets is a fault the other stops paying for."""
SQLITE: Part[SqliteCapabilities] = Part("sqlite")
WATCHDOG: Part[LoopWatchdog] = Part("watchdog")

CHANGES: Part[ChangeBus] = Part("changes")
"""Who is connected, and what each of them is waiting to be told.

One per running application, and read only by the live slice: the route that holds a connection
open, and the plain read beside it that says where the stream of announcements stands. Every write
that changes what somebody may see announces through the module beside this one rather than through
the part, because the announcing happens inside the write, several layers below anything that holds
the application."""

URL_IMPORTER: Part[UrlImporter] = Part("downloads")
"""The downloader, seen only as "hand it a link". Absent when the feature is not installed, which
is why its readers use `part_or_none`; the download feature names this same part when it publishes
itself, so the name is written once and read from here."""
THREADS: Part[ThreadPoolWatch] = Part("threads")
#: Files read per storage and the waits for a place: a struggling share shows nowhere else.
LANES: Part[StorageLanes] = Part("lanes")

#: How long a read waits for a database connection.
READS: Part[ReadPoolWatch] = Part("reads")

#: How long the loop's ready queue takes to drain: buried in prompt work, not held.
BACKLOG: Part[LoopBacklogWatch] = Part("backlog")

#: What costs the time by kind: a small thing done thousands of times queues for nothing.
SLOWEST: Part[SlowestWork] = Part("slowest")
WIDEST: Part[WidestReads] = Part("widest")

ON_SETTINGS_CHANGED: Part[Callable[[set[str]], Coroutine[Any, Any, None]]] = Part(
    "on_settings_changed"
)
"""What to do when a preference is saved. Assembled at start-up, because the things that react to a
change belong to features that do not know about each other."""

ON_FOLDER_ADDED: Part[Callable[[str, str | None, bool], Coroutine[Any, Any, bool]]] = Part(
    "on_folder_added"
)
"""What to do when a library folder has been added: the folder's id, who added it, and whether its
first scan was asked for. Answers True when the reaction took that scan over and will queue it
itself, after work that has to come first (the benchmark of a device never measured). Assembled at
start-up for the reason `ON_SETTINGS_CHANGED` is."""


# Parts a feature builds and others read, typed by the kernel's interface so no slice names another.

SETTINGS_HUB: Part[SettingsSeam] = Part("settings_hub")
#: The one filter engine, shared so no two readers disagree about a query.
FILTER_ENGINE: Part[FilterEngine] = Part("filter_engine")
#: Giving a subject a fetched picture as its cover. See `SubjectCovers`.
SUBJECT_COVERS: Part[SubjectCovers] = Part("subject_covers")
"""Reading a preference. Seven features do; none of them owns the store."""

COVER_PICTURES: Part[CoverPictures] = Part("cover_pictures")
"""The store for uploaded cover pictures, one dependency for the twelve cover routes."""

REINDEXER: Part[ReindexSeam] = Part("reindexer")
STILLS: Part[StillSeam] = Part("stills")
"""Telling the search index that text it holds has changed."""

FORGET_GONE: Part[ForgetGoneSeam] = Part("forget_gone")
"""A deleted thing told to the search feature, which drops a saved filter left naming nothing."""

RECOGNITION: Part[RecognitionSeam] = Part("recognition")
"""Handing a new person the faces already waiting under their name."""

BOX_PICTURES: Part[BoxPicturesSeam] = Part("box_pictures")
"""A stash-box's pictures of somebody linked to one, for starter references."""

SEMANTIC_SEARCH: Part[SemanticSeam] = Part("semantic_search")
"""What some words mean, for a search box that must not know how."""

FACE_EVIDENCE: Part[FaceEvidenceSeam] = Part("face_evidence")
"""What the faces in a folder say, for a feature that reads folder names."""

DISAGREEMENTS: Part[DisagreementSeam] = Part("disagreements")
"""How many fields a stash-box disagrees with about one record, for the tab strip's mark."""

PHOTO_SET_MAKER: Part[PhotoSetSeam] = Part("photo_set_maker")
"""Making a Photo Set out of pictures, for a feature that proposes groupings and owns none."""


# The shared dependencies, so a route asks for the type it wants.


def access(request: Request) -> Repository:
    return part_of(request, ACCESS)


def database(request: Request) -> Database:
    return part_of(request, DATABASE)


def content(request: Request) -> ContentStore:
    return part_of(request, CONTENT)


def library(request: Request) -> LibraryStore:
    return part_of(request, LIBRARY)


def user_state(request: Request) -> UserStateStore:
    return part_of(request, USER_STATE)


def entity_state(request: Request) -> EntityStateStore:
    return part_of(request, ENTITY_STATE)


def queue(request: Request) -> JobQueue:
    return part_of(request, QUEUE)


def work_ahead(request: Request) -> WorkAhead:
    return part_of(request, WORK_AHEAD)


def ledger(request: Request) -> Ledger:
    return part_of(request, LEDGER)


def settings(request: Request) -> Settings:
    return part_of(request, SETTINGS)


def hardware(request: Request) -> HardwareReport:
    return part_of(request, HARDWARE)


def workbench(request: Request) -> Workbench:
    return part_of(request, WORKBENCH)


def enricher(request: Request) -> Enricher:
    return part_of(request, ENRICHER)


def recorder(request: Request) -> Recorder:
    return part_of(request, RECORDER)


def settings_hub(request: Request) -> SettingsSeam:
    return part_of(request, SETTINGS_HUB)


def filter_engine(request: Request) -> FilterEngine:
    """The one filter engine. See `FilterEngine`."""
    return part_of(request, FILTER_ENGINE)


def cover_pictures(request: Request) -> CoverPictures:
    """The uploaded-cover store. See `kernel.covers.CoverPictures`."""
    return part_of(request, COVER_PICTURES)


def reindexer(request: Request) -> ReindexSeam:
    return part_of(request, REINDEXER)


def forget_gone(request: Request) -> ForgetGoneSeam:
    return part_of(request, FORGET_GONE)


def stills(request: Request) -> StillSeam:
    """What renders a still of one moment. See `StillSeam`."""
    return part_of(request, STILLS)


def subject_covers(request: Request) -> SubjectCovers:
    """A fetched picture as a subject's cover. See `kernel.covers.SubjectCovers`."""
    return part_of(request, SUBJECT_COVERS)


async def whereabouts(
    request: Request, viewer: Viewer, *, root_id: str | None = None
) -> where.Whereabouts:
    """Where files sit, as `viewer` may be told it (`kernel.where`), in one library or in all."""
    return await where.whereabouts(
        viewer,
        access=access(request),
        library=library(request),
        settings=settings_hub(request),
        root_id=root_id,
    )
