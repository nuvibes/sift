# SPDX-License-Identifier: AGPL-3.0-or-later
"""Leftovers, counted before anything is removed, and never removed on their own.

Each area registers its own; a costly tidying reads the disk, so it answers from its last survey."""

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

#: The last survey of each costly tidying: what it counted and when.
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
    """What one tidying would remove; `frees_bytes` None for rows, `count` None before a survey."""

    name: str
    title: str
    detail: str
    #: What one counted thing is called, and more than one ("134 jobs").
    noun: str
    nouns: str
    count: int | None
    frees_bytes: int | None = None
    surveyed_at: int | None = None


class Tidying(Protocol):
    """One kind of leftover: how to count it, and how to remove it; `costly` reads the disk."""

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
    """What a tidying is built from: the database, settings and the preference seam, if any."""

    database: Database
    settings: Settings
    preferences: SettingsSeam | None = None


Builder = Callable[[Resources], Tidying]


async def existing_asset_ids(resources: Resources, asset_ids: Sequence[str]) -> set[str]:
    """Which of these ids are still assets, as no feature may read the content store."""
    return await ContentStore(resources.database, settings=resources.settings).existing_ids(
        asset_ids
    )


async def existing_job_ids(resources: Resources, job_ids: Sequence[str]) -> set[str]:
    """Which of these ids are still rows in the queue, asked in chunked statements."""
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


# Registered at import by the areas that own the leftovers.
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
    """Every registered tidying in registration order: rows first, then the files they stranded."""
    return [builder(resources) for builder in _BUILDERS.values()]


async def survey_all(resources: Resources) -> list[Leftovers]:
    """What every tidying would remove, reading no directory: a costly one gives its last survey."""
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
    """Every costly tidying's survey, taken now and kept: what the survey job does."""
    return [
        await remember_survey(resources, tidying)
        for tidying in build_all(resources)
        if tidying.costly
    ]


def walk_files(root: Path) -> list[Path]:
    """Every file under a directory, or nothing where it is not there yet."""
    if not root.is_dir():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def size_of(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            # A file that vanished between listing and measuring is one fewer to remove.
            continue
    return total


def remove_files(paths: list[Path]) -> int:
    """Unlink each, and say how many went; one already gone counts as done."""
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
    """`walk_files` off the event loop, as a large cache walk would stop the application."""
    return await asyncio.to_thread(walk_files, root)


#: How long a stranded record is kept before Maintenance offers it, so re-adding brings it back.
STRANDED_KEEP_DAYS = 30


class StrandedAssets:
    """Files Sift still has a record of in no folder it can reach, kept until cleared."""

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
        # Imported here, as the access repository holds a content store and would close a cycle.
        from sift.kernel.access import ObjectType, Repository

        access = Repository(self._db, self._content)
        removed = 0
        for asset_id in await self._content.stranded_asset_ids(self._stranded_before()):
            if await self._content.remove_asset_if_unplaced(asset_id):
                # Grants carry no foreign key; one left behind would apply to a reused id.
                await access.forget_object(ObjectType.ITEM, asset_id)
                removed += 1
        log.info("tidy.stranded_assets", removed=removed)
        return removed


class LeftoverDerivatives:
    """Thumbnails, previews and sprite sheets nothing points at; all rebuildable from originals."""

    name = "leftover-derivatives"
    #: Reads the whole cache directory.
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
        # The transcode cache has its own lifetime and no rows here; never swept from this.
        transcode = self._settings.transcode_cache_dir
        return [
            path
            for path in files
            # `as_posix`, as rows use slashes everywhere; a file in the cache's root is Sift's own.
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
    """Jobs that failed on every attempt; clearing them loses only the recorded reasons."""

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
        # Activity and Maintenance both read again on the jobs bell.
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            cursor = await connection.execute(_DELETE_SETTLED_FAILURES)
            removed = cursor.rowcount
        log.info("tidy.settled_failures", removed=removed)
        return max(removed, 0)


#: The repair switch, named as a string, as the kernel may not import its feature.
REPAIR_PLAYBACK_KEY = "performance.repair_playback"


class RepackagedCopies:
    """Whole second copies of videos, offered only while the switch that makes them is off."""

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
        """Whether the switch is off, so the copies are leftovers; None where it cannot be read."""
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


# Registered in run order: rows first, then the files they held (see `build_all`).
register_tidying(StrandedAssets.name, StrandedAssets)
register_tidying(SettledFailures.name, SettledFailures)
register_tidying(LeftoverDerivatives.name, LeftoverDerivatives)
register_tidying(RepackagedCopies.name, RepackagedCopies)
