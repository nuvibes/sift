# SPDX-License-Identifier: AGPL-3.0-or-later
"""Leftovers, counted before anything is removed.

A library accumulates things nothing points at any more. A folder removed from the library leaves
the rows for the files that were in it, because a file can sit in several folders and Sift cannot
tell "this drive is unplugged" from "I do not want these any more". A file scanned twice leaves the
pictures the first scan made. A job that failed on every attempt sits in the queue forever, because the
alternative is a queue that quietly forgets its failures.

None of that is a fault on its own. Each one is a deliberate refusal to delete something on a guess.
This module is the other half: somewhere to see what has built up and remove it on purpose.

Two rules hold for everything registered here, and they are the whole design:

- **Counted before it is offered.** A tidying says what it would remove and how much space it would
  free without removing anything. A control whose effect is only visible afterwards is one nobody
  can use carefully.
- **It never runs on its own.** No schedule, no threshold, no tidying at boot. Everything here is
  irreversible, and the thing standing between somebody and a mistake is that they pressed it.

Each area registers its own, because the leftovers of a feature are that feature's business and the
kernel has no way to know what a face picture is. What the kernel owns is the shape: a name, a
sentence, a count, and a way to run it.

A count is taken one of two ways. A tidying that counts rows answers when the screen asks. One
that has to read a whole directory off the disk is **costly** and answers from the last survey,
which a job takes when somebody asks for one and this module keeps, with the moment it was taken:
reading the cache directory of a large library takes seconds on a fast disk and much longer on a
share, and a screen that paid that on every visit would take half a minute to open.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, DerivativeKind
from sift.kernel.db import Connection, Database, register_schema_initializer
from sift.kernel.log import get_logger
from sift.kernel.seams import SettingsSeam

log = get_logger(__name__)

COMPONENT = "tidy"
VERSION = 1

#: The last survey of each costly tidying: what it counted and when. One row per name; a name
#: nothing registers any more is a row nothing reads.
_CREATE_SURVEYS = """
CREATE TABLE IF NOT EXISTS tidy_surveys (
  name        TEXT PRIMARY KEY,
  count       INTEGER NOT NULL,
  frees_bytes INTEGER,
  surveyed_at INTEGER NOT NULL
)
"""

_LOAD_SURVEY = "SELECT count, frees_bytes, surveyed_at FROM tidy_surveys WHERE name = ?"
_SAVE_SURVEY = """
INSERT INTO tidy_surveys (name, count, frees_bytes, surveyed_at) VALUES (?, ?, ?, ?)
ON CONFLICT(name) DO UPDATE SET
  count = excluded.count, frees_bytes = excluded.frees_bytes, surveyed_at = excluded.surveyed_at
"""


async def _initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_SURVEYS)


register_schema_initializer(COMPONENT, VERSION, _initialize, baseline=1)


@dataclass(frozen=True, slots=True)
class Leftovers:
    """What one tidying would remove, having looked.

    `frees_bytes` is None when the answer is not about disk: rows in a table take no space worth
    naming, and quoting a number for them would invite somebody to run it for the wrong reason.

    `count` is None for a costly tidying nothing has surveyed yet: not zero, which would read as
    nothing to do. `surveyed_at` is when a kept count was taken, and None for one taken now.
    """

    name: str
    title: str
    detail: str
    #: What one of the things counted is called, and more than one: the count on screen says its
    #: noun ("134 jobs"), and only the tidying knows what it counts.
    noun: str
    nouns: str
    count: int | None
    frees_bytes: int | None = None
    surveyed_at: int | None = None


class Tidying(Protocol):
    """One kind of leftover: how to count it, and how to remove it.

    `costly` says the count reads the disk, so the screen shows the last survey rather than
    taking one; `title` and `detail` are what the screen says about it either way.
    """

    @property
    def name(self) -> str: ...

    @property
    def costly(self) -> bool: ...

    @property
    def title(self) -> str: ...

    @property
    def detail(self) -> str: ...

    @property
    def noun(self) -> str: ...

    @property
    def nouns(self) -> str: ...

    async def survey(self) -> Leftovers: ...

    async def run(self) -> int: ...


@dataclass(frozen=True, slots=True)
class Resources:
    """What a tidying is built from. Deliberately only three things.

    A tidying that needs a store builds its own from these. Passing the assembled stores instead
    would mean the kernel holding a reference to every slice's objects, which is the coupling this
    registry exists to avoid.

    `preferences` is the third, and the kernel's own read seam rather than a slice's object: the
    descriptions a Smart Search model no longer in use left behind can only be told apart from the
    rest by which model IS in use, and that is a preference. None where the caller has no settings
    to hand (the background survey, which surveys only the costly tidyings) and a tidying that
    needs one says it cannot count rather than guessing.
    """

    database: Database
    settings: Settings
    preferences: SettingsSeam | None = None


Builder = Callable[[Resources], Tidying]


async def existing_asset_ids(resources: Resources, asset_ids: Sequence[str]) -> set[str]:
    """Which of these ids are still assets in the library.

    Here rather than in the tidying that needs it. A feature keeping its own table of asset ids has
    no way to be told when a file is deleted (a virtual table takes no foreign key, so nothing
    cascades) and it has to ask. But reaching for the content store from inside a feature is
    refused by a gate, and rightly: that store reads any asset with no permission check.

    A tidying is the one place that is genuinely a maintenance question rather than a viewer's, so
    the answer lives here, in the kernel, where the assets table belongs.
    """
    return await ContentStore(resources.database, settings=resources.settings).existing_ids(
        asset_ids
    )


async def existing_job_ids(resources: Resources, job_ids: Sequence[str]) -> set[str]:
    """Which of these ids are still rows in the queue.

    Here for the reason `existing_asset_ids` is: a feature that names its scratch after a job has
    no way to be told when the queue sweeps the row, and the jobs table is the kernel's. Asked in
    one statement, chunked by the same rule every id list in Sift is.
    """
    if not job_ids:
        return set()
    from sift.kernel.db import in_clause

    found: set[str] = set()
    for start in range(0, len(job_ids), 500):
        chunk = list(job_ids[start : start + 500])
        sql, params = in_clause("SELECT id FROM jobs WHERE id IN (?*)", chunk)
        rows = await resources.database.fetch_all(sql, tuple(params))
        found.update(str(row["id"]) for row in rows)
    return found


# Registered by the areas that own the leftovers, at import. Process-global, like the schema and
# handler registries: which tidyings exist is a property of the code that is running.
_BUILDERS: dict[str, Builder] = {}


def register_tidying(name: str, builder: Builder) -> None:
    """Claim a name. Registering the same one twice is a bug, not an override."""
    if name in _BUILDERS:
        raise ValueError(f"a tidying named {name!r} is already registered")
    _BUILDERS[name] = builder


def registered_tidyings() -> dict[str, Builder]:
    """The registry, copied. Nothing mutates it through here."""
    return dict(_BUILDERS)


def build_all(resources: Resources) -> list[Tidying]:
    """Every registered tidying, in the order they were registered.

    The order matters and is not alphabetical. Removing rows leaves the files they pointed at
    behind, so the row tidyings are registered before the file ones: run in order, the second
    picks up what the first stranded. Run out of order nothing breaks; it just takes two passes.
    """
    return [builder(resources) for builder in _BUILDERS.values()]


async def survey_all(resources: Resources) -> list[Leftovers]:
    """What every tidying would remove. Removes nothing, and reads no directory: a costly
    tidying answers with its last survey, or with no count when there has never been one."""
    return [await _current(resources, tidying) for tidying in build_all(resources)]


async def _current(resources: Resources, tidying: Tidying) -> Leftovers:
    if not tidying.costly:
        return await tidying.survey()
    row = await resources.database.fetch_one(_LOAD_SURVEY, (tidying.name,))
    return Leftovers(
        name=tidying.name,
        title=tidying.title,
        detail=tidying.detail,
        noun=tidying.noun,
        nouns=tidying.nouns,
        count=None if row is None else int(row["count"]),
        frees_bytes=None if row is None or row["frees_bytes"] is None else int(row["frees_bytes"]),
        surveyed_at=None if row is None else int(row["surveyed_at"]),
    )


async def remember_survey(resources: Resources, tidying: Tidying) -> Leftovers:
    """Take one costly tidying's survey now and keep it as the answer the screen reads."""
    found = await tidying.survey()
    now = int(time.time())
    # Maintenance draws the kept survey and reads it again on the jobs bell.
    async with telling(resources.database, EVERY_ADMIN, About.JOBS) as connection:
        await connection.execute(_SAVE_SURVEY, (tidying.name, found.count, found.frees_bytes, now))
    log.info("tidy.surveyed", name=tidying.name, count=found.count)
    return Leftovers(
        name=found.name,
        title=found.title,
        detail=found.detail,
        noun=found.noun,
        nouns=found.nouns,
        count=found.count,
        frees_bytes=found.frees_bytes,
        surveyed_at=now,
    )


async def survey_costly(resources: Resources) -> list[Leftovers]:
    """Every costly tidying's survey, taken now and kept. What the survey job does."""
    return [
        await remember_survey(resources, tidying)
        for tidying in build_all(resources)
        if tidying.costly
    ]


# --- helpers the file-based tidyings share ---------------------------------------------------


def walk_files(root: Path) -> list[Path]:
    """Every file under a directory, or nothing if it is not there.

    A cache directory that does not exist yet is an ordinary state on a new install, not an error
    to report to somebody looking at a maintenance screen.
    """
    if not root.is_dir():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def size_of(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            # A file that vanished between listing and measuring is one fewer thing to remove.
            continue
    return total


def remove_files(paths: list[Path]) -> int:
    """Unlink each, and say how many went. One that is already gone counts as done."""
    removed = 0
    for path in paths:
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            removed += 1
        except OSError as error:
            log.warning("tidy.unlink_failed", detail=str(error))
    return removed


async def in_thread_files(root: Path) -> list[Path]:
    """`walk_files` off the event loop.

    A cache directory holds a file per thumbnail, per preview and per sprite sheet of every file in
    the library, and listing it is one long synchronous walk. Sift is a single process: the API,
    the job feed and every video anybody is watching share one loop, so a walk on it stops the
    application for as long as it takes.
    """
    return await asyncio.to_thread(walk_files, root)


# --- what the kernel itself leaves behind -----------------------------------------------------


#: How long a record whose folder was removed is left alone before Maintenance offers it. The
#: promise at the remove is that adding the folder back brings everything with it; thirty days
#: is long enough for a drive that went away for a holiday, and the sentence on the card says it.
STRANDED_KEEP_DAYS = 30


class StrandedAssets:
    """Files Sift still has a record of, in no folder it can reach.

    Removing a folder from the library deletes the places its files sat, and deliberately does not
    delete the files themselves: the same bytes can sit in several folders, and a folder is removed
    far more often because a drive is unplugged than because somebody wants their tags and ratings
    thrown away. What is left when the last place goes is a record of a file that appears on no
    screen, can never be opened, and is still counted in every total.
    """

    name = "stranded-assets"
    costly = False
    title = "Files missing from every folder"
    noun = "file"
    nouns = "files"
    detail = (
        "Sift still holds a record of these, but every folder they were in has been "
        "removed from your library, so there's nothing left to open. Removing them takes "
        "their tags, ratings, people and favorites with them, permanently. If a drive is "
        "simply unplugged, plug it back in first \u2014 these will disappear on their own. A "
        f"folder removed in the last {STRANDED_KEEP_DAYS} days isn't counted here: adding it "
        "back brings everything with it, and this waits for that."
    )

    def __init__(self, resources: Resources) -> None:
        self._db = resources.database
        self._content = ContentStore(resources.database, resources.settings)

    @staticmethod
    def _stranded_before() -> int:
        """The moment before which a strand is old enough to be offered."""
        return int(time.time()) - STRANDED_KEEP_DAYS * 86_400

    async def survey(self) -> Leftovers:
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=len(await self._content.stranded_asset_ids(self._stranded_before())),
        )

    async def run(self) -> int:
        # Imported here rather than at the top: the access repository holds a content store, and
        # importing it up there would make this module part of a cycle for the sake of one call.
        from sift.kernel.access import ObjectType, Repository

        access = Repository(self._db, self._content)
        removed = 0
        for asset_id in await self._content.stranded_asset_ids(self._stranded_before()):
            if await self._content.remove_asset_if_unplaced(asset_id):
                # Grants name their object by id with no foreign key behind it, so nothing
                # cascades them. One left behind goes on applying to whatever is issued that id.
                await access.forget_object(ObjectType.ITEM, asset_id)
                removed += 1
        log.info("tidy.stranded_assets", removed=removed)
        return removed


class LeftoverDerivatives:
    """Thumbnails, previews and sprite sheets nothing points at.

    Every one of these was made from a file, and the row that named it went when the file did. The
    picture itself is not deleted at the same moment on purpose: unlinking during a delete would
    make removing a file depend on the cache being writable, and the cache is the disposable half.

    Safe by construction, and worth saying why: everything under the cache is rebuildable from the
    original, so the worst case of removing one wrongly is that it is made again the next time it
    is asked for.
    """

    name = "leftover-derivatives"
    #: Reads the whole cache directory: a file per picture of every file in the library.
    costly = True
    title = "Leftover thumbnails and previews"
    noun = "file"
    nouns = "files"
    detail = (
        "Pictures Sift made from files it no longer has. They aren't shown anywhere and "
        "nothing can reach them. Anything still in use is rebuilt from the original file "
        "the next time it's needed."
    )

    def __init__(self, resources: Resources) -> None:
        self._settings = resources.settings
        self._content = ContentStore(resources.database, resources.settings)

    async def _orphans(self) -> list[Path]:
        cache = self._settings.cache_dir
        known = await self._content.derivative_paths()
        files = await in_thread_files(cache)
        # The transcode cache is its own thing with its own lifetime and no rows in this table.
        # Sweeping it from here would delete segments out from under somebody watching a video.
        transcode = self._settings.transcode_cache_dir
        return [
            path
            for path in files
            # `as_posix`: the rows spell the path with slashes on every site (`derivative_relpath`),
            # where `str()` renders backslashes on Windows, so nothing on disk would match a row
            # and every derivative in use would be an orphan this DELETED.
            # A file in the cache's root is Sift's own (the kept probe), never a picture.
            if (
                not path.is_relative_to(transcode)
                and len(path.relative_to(cache).parts) > 1
                and path.relative_to(cache).as_posix() not in known
            )
        ]

    async def survey(self) -> Leftovers:
        orphans = await self._orphans()
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=len(orphans),
            frees_bytes=await asyncio.to_thread(size_of, orphans),
        )

    async def run(self) -> int:
        removed = await asyncio.to_thread(remove_files, await self._orphans())
        log.info("tidy.leftover_derivatives", removed=removed)
        return removed


_SETTLED_FAILURES = "SELECT COUNT(*) AS total FROM jobs WHERE state = 'failed'"
_DELETE_SETTLED_FAILURES = "DELETE FROM jobs WHERE state = 'failed'"


class SettledFailures:
    """Jobs that failed on every attempt and will not be retried.

    They are kept rather than cleared because a queue that forgets its failures is a queue that
    cannot answer why something never happened. That is right up until the reason has been read and
    dealt with, after which they are a growing list nobody looks at.

    Removing one loses the recorded reason it failed. It does not retry anything and it does not
    bring anything back: a file whose scan failed is scanned again by asking for a scan.
    """

    name = "settled-failures"
    costly = False
    title = "Jobs that won't be retried"
    noun = "job"
    nouns = "jobs"
    detail = (
        "Work that ran out of attempts and stopped being retried. Clearing these loses the "
        "reason each one gives for failing, and nothing else \u2014 it doesn't retry them."
    )

    def __init__(self, resources: Resources) -> None:
        self._db = resources.database

    async def survey(self) -> Leftovers:
        rows = await self._db.fetch_all(_SETTLED_FAILURES, ())
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=int(rows[0]["total"]) if rows else 0,
        )

    async def run(self) -> int:
        # The failed jobs are Activity's and their count is Maintenance's, and both read again on
        # the jobs bell.
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            cursor = await connection.execute(_DELETE_SETTLED_FAILURES)
            removed = cursor.rowcount
        log.info("tidy.settled_failures", removed=removed)
        return max(removed, 0)


#: The switch that makes repaired copies, named as a string: the kernel may not import the feature
#: that declares it (`performance.REPAIR_PLAYBACK_KEY`), and only needs to read the answer.
REPAIR_PLAYBACK_KEY = "performance.repair_playback"


class RepackagedCopies:
    """Whole second copies of videos, offered for deleting while the switch that makes them is off.

    The switch stops Sift making them and deletes none: a copy is cache, and a delete on Off
    takes the copies a browser needs to play a file's container as well as the repaired ones. So
    the space is given back HERE, on purpose, counted first like every tidying.

    Only while the switch is off, and that is what keeps the delete honest. With it on, a repaired
    copy is in use and deleting it would leave the file stuttering until the switch was turned off
    and on again; with it off, nothing is making them and they are exactly the leftovers this
    screen is for. A copy the player needs to play a file in the browser is made again the next
    time that file is played. No preferences to read is no count, never "all of them".
    """

    name = "repackaged-copies"
    costly = False
    title = "Repackaged copies of videos"
    noun = "copy"
    nouns = "copies"
    detail = (
        "Whole second copies of videos, kept so they skip smoothly or play in your browser. "
        "Offered here while Repair videos that stutter when skipping is off. Your own files are "
        "never changed, and a video that needs its copy to play gets it again when it's played."
    )

    def __init__(self, resources: Resources) -> None:
        self._db = resources.database
        self._content = ContentStore(resources.database, resources.settings)
        self._preferences = resources.preferences

    async def _offered(self) -> bool | None:
        """Whether the switch is off, so the copies are leftovers. None where it cannot be read."""
        if self._preferences is None:
            return None
        return not await self._preferences.get_app(REPAIR_PLAYBACK_KEY)

    async def survey(self) -> Leftovers:
        offered = await self._offered()
        count: int | None = None
        freed: int | None = None
        if offered is False:
            count, freed = 0, 0
        elif offered:
            count, freed = await self._content.derivative_totals(DerivativeKind.REMUX)
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=count,
            frees_bytes=freed,
        )

    async def run(self) -> int:
        if not await self._offered():
            return 0
        removed, freed = await self._content.drop_derivatives(DerivativeKind.REMUX)
        log.info("tidy.repackaged_copies", removed=removed, bytes=freed)
        return removed


# Registered in the order they should run: rows first, then the files those rows were holding on
# to. See `build_all`.
register_tidying(StrandedAssets.name, StrandedAssets)
register_tidying(SettledFailures.name, SettledFailures)
register_tidying(LeftoverDerivatives.name, LeftoverDerivatives)
register_tidying(RepackagedCopies.name, RepackagedCopies)
