# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the running application's parts are kept, and the only place they are read from.

Sift builds about fifty working parts at start-up (the database, the job queue, the permission
resolver, one service per feature) and every route needs a handful of them. They live on the
application object, which is a bag with no type: reading one gives back "something", and the reader
has to tell the type checker what it expects to find. Written out per route that would be dozens
of separate promises, most of every type exemption in the server, and none of them checked
against what start-up actually puts there. A part renamed on one side and not the other is not a
build failure; it is a live error in front of somebody.

A part is declared once, next to the type it holds, and read through `part_of`. The name is written
in exactly one place, so a rename is a build error rather than a surprise, and the type comes back
already known. One exemption remains, in `part_of` below, which is the boundary between an untyped
bag and everything above it.

**This module is the only thing that may touch `app.state`,** and a gate says so. The composition
root publishes through `provide`; everything else reads through `part_of` or one of the shared
dependencies at the bottom.

Parts belonging to a feature are declared by that feature, beside the class they hold. The kernel
does not name features, and this module is no exception.
"""

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
    InterfaceStateSeam,
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
    """One thing the application is built from: the name it is stored under, and what it is.

    Declared beside the class it holds, so the name and the type are decided together and neither
    can drift from the other. Comparing two parts is comparing their names, because a part is the
    name: there is never a second one under the same name.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Part({self.name!r})"


def provide[T](app: FastAPI, part: Part[T], value: T) -> None:
    """Publish a part. Called from the composition root and nowhere else."""
    setattr(app.state, part.name, value)


def part_of[T](connection: HTTPConnection, part: Part[T]) -> T:
    """The part, typed as it was declared.

    The one exemption in the server that is load-bearing rather than incidental: what comes off the
    application object is genuinely untyped, and this is where that stops being true. Everything
    above it is checked.

    Takes the connection rather than the request, because the job feed is a socket and needs the
    same four parts every route does. A socket is not a request and has no response, but both are
    connections and both arrive holding the application.

    A part that was never published raises here, at the first request that wants it, rather than
    coming back as None and failing further in with nothing saying why.
    """
    try:
        return getattr(connection.app.state, part.name)  # type: ignore[no-any-return]
    except AttributeError:
        raise RuntimeError(f"{part.name!r} was never built; start-up did not publish it") from None


def part_of_app[T](app: FastAPI, part: Part[T]) -> T:
    """The part, read from the application rather than from a connection.

    For the few places holding the application itself instead of a request: start-up, and the test
    helpers that seed a row before anything is called.
    """
    try:
        return getattr(app.state, part.name)  # type: ignore[no-any-return]
    except AttributeError:
        raise RuntimeError(f"{part.name!r} was never built; start-up did not publish it") from None


def part_or_none[T](connection: HTTPConnection, part: Part[T]) -> T | None:
    """The part, or None where its absence is an answer rather than a fault.

    For the handful of routes that stay useful without one: the job dashboard names what each job
    is doing and needs the database only to say which file, so a route built without one shows a
    smaller answer instead of a broken page. Anything that genuinely cannot work without its part
    uses `part_of` and gets told which one was missing.
    """
    found: T | None = getattr(connection.app.state, part.name, None)
    return found


def part_of_app_or_none[T](app: FastAPI, part: Part[T]) -> T | None:
    """The part, read from the application, where not being built yet is an answer.

    What `part_or_none` is for a request, this is for the application itself. Start-up builds the
    features in dependency order, so a builder that closes over something built after it has to read
    it when it is used rather than when it is wired, and until then the honest answer is that
    there is none.
    """
    found: T | None = getattr(app.state, part.name, None)
    return found


def hold[T](connection: HTTPConnection, part: Part[T], value: T) -> None:
    """Publish a part from a running request, for the one thing that is built on first use.

    Separate from `provide` and deliberately narrow. Almost everything is built once at start-up,
    where the order is decided and visible; this is for state that belongs to the application rather
    than to a module: the self-test, which must go away with the application so that a second one
    does not inherit the first one's answer.
    """
    setattr(connection.app.state, part.name, value)


# --- The kernel's own parts ---------------------------------------------------------------------
#
# Everything a feature may depend on without depending on another feature. A feature's own parts
# are declared by that feature.

ACCESS: Part[Repository] = Part("access")
"""The permission resolver. The only sanctioned way to read an asset or a folder."""

DATABASE: Part[Database] = Part("database")
CONTENT: Part[ContentStore] = Part("content")
LIBRARY: Part[LibraryStore] = Part("library")
USER_STATE: Part[UserStateStore] = Part("user_state")
#: The pin on a person, a Site, a collection, a tag or a photo set. Its own part rather
#: than a method on the one above, because that one is about a FILE and this is about a named
#: thing, and five slices reach for this where three reach for that.
ENTITY_STATE: Part[EntityStateStore] = Part("entity_state")
QUEUE: Part[JobQueue] = Part("queue")
#: How much work each kind has that has not been made into a job yet. Registered into by the
#: composition root, because the counters belong to the features that own the records they read.
#:
#: NOT "backlog": that name is taken twenty lines down for the event loop's ready queue, and the
#: `Part` name is a key on `app.state`, so the two would have silently overwritten each other.
WORK_AHEAD: Part[WorkAhead] = Part("work_ahead")
POOL: Part[WorkerPool] = Part("pool")
#: What each run of the long passes cost, and the estimate of time left that reads from it.
LEDGER: Part[Ledger] = Part("ledger")
WORKBENCH: Part[Workbench] = Part("workbench")
ENRICHER: Part[Enricher] = Part("enricher")
#: Turning a name into a row of this library, or finding the row it already is. Built at
#: composition because it spans people, tags and sites; read by the stash-box confirm.
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
#: How many files are being read from each storage, and how long reads have waited for a place.
#: The reading for the one resource the others are blind to: a network share that has stopped
#: coping reads as a healthy loop, a healthy pool and healthy readers, with every job slow.
LANES: Part[StorageLanes] = Part("lanes")

#: How long a read waits for a database connection. The third finite thing every request
#: needs, and one that fills without anything else saying so.
READS: Part[ReadPoolWatch] = Part("reads")

#: How long the loop's ready queue takes to drain. The three above measure waiting for a resource;
#: this measures the loop being buried in work that each returns promptly, which reads as perfectly
#: healthy on all three while nothing on screen loads.
BACKLOG: Part[LoopBacklogWatch] = Part("backlog")

#: What is costing the time, by kind of work. The other half of every watch here: a request that
#: queues for nothing and simply does a small thing several thousand times is slow, and appears on
#: none of the four measurements above.
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


# --- Parts a feature builds and several features read --------------------------------------------
#
# Declared here rather than by the feature that builds them, and typed by the interface rather than
# by the class. That is what keeps a reader from importing the feature behind it: the shape is the
# kernel's, the implementation is wired in at start-up, and no slice ends up naming another. The
# feature that owns one holds a second, concrete view of it (same name, its own type) because
# the feature it belongs to uses more of the object than the interface describes.

SETTINGS_HUB: Part[SettingsSeam] = Part("settings_hub")
#: The same hub, as the keeper of each User's arrangement of the interface: read and written for that
#: User only. Its own part because a reader of preferences has no business writing a User's state,
#: and a feature that keeps a hint there needs both calls. See `InterfaceStateSeam`.
INTERFACE_STATE: Part[InterfaceStateSeam] = Part("interface_state")
#: The one filter engine: the search feature's compiler, published at boot. The grid, the search
#: endpoints and a collection's Files tab all hold this same instance, so none of them can come to
#: disagree with another about what a query means. See `FilterEngine`.
FILTER_ENGINE: Part[FilterEngine] = Part("filter_engine")
#: Giving a person, a site or a tag a picture Sift fetched, as their cover. Built at composition
#: from each slice's own cover verbs; asked by the stash-box enrichment. See `SubjectCovers`.
SUBJECT_COVERS: Part[SubjectCovers] = Part("subject_covers")
"""Reading a preference. Seven features do; none of them owns the store."""

COVER_PICTURES: Part[CoverPictures] = Part("cover_pictures")
"""The store for uploaded cover pictures. One dependency for the twelve cover routes
(six that receive one and six that serve one) rather than a database and a settings object
at each of them."""

REINDEXER: Part[ReindexSeam] = Part("reindexer")
STILLS: Part[StillSeam] = Part("stills")
"""Telling the search index that text it holds has changed."""

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


# --- The shared dependencies ---------------------------------------------------------------------
#
# The same three-line accessor would otherwise be written out per route, for the resolver, the
# queue and the filter engine alike. They are here once, so a route asks for the type it wants
# and gets it.


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


def stills(request: Request) -> StillSeam:
    """What renders a still of one moment. See `StillSeam`."""
    return part_of(request, STILLS)


def subject_covers(request: Request) -> SubjectCovers:
    """A fetched picture as a subject's cover. See `kernel.covers.SubjectCovers`."""
    return part_of(request, SUBJECT_COVERS)


async def whereabouts(
    request: Request, viewer: Viewer, *, root_id: str | None = None
) -> where.Whereabouts:
    """Where files sit, as `viewer` may be told it (`kernel.where`), in one library or in all.

    The three parts it reads are the ones every screen naming a file's place needs, so a route asks
    here rather than assembling them.
    """
    return await where.whereabouts(
        viewer,
        access=access(request),
        library=library(request),
        settings=settings_hub(request),
        root_id=root_id,
    )
