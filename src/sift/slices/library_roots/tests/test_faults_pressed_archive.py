# SPDX-License-Identifier: AGPL-3.0-or-later
"""A pressed walk's probes all go at the press's urgency: an archive's pictures and a file asked
again as never read, as well as its plain files."""

from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.content import Root
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext
from sift.kernel.jobs.tuning import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
from sift.kernel.tests.content_helpers import FIXTURES
from sift.slices.library_roots import jobs, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer
from sift.slices.library_roots.tests.test_archive_scan import gallery

Context = Callable[..., Awaitable[JobContext]]


async def _probes(database: Database) -> list[tuple[str, int]]:
    rows = await database.fetch_all(
        "SELECT payload, priority FROM jobs WHERE type = ? ORDER BY id", (taking_in.PROBE,)
    )
    return [(json.loads(row["payload"])["asset_id"], int(row["priority"])) for row in rows]


async def test_a_pressed_walk_asks_its_archive_pictures_probes_at_the_press_urgency(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
) -> None:
    gallery(root_path / "set" / "shoot.zip", ["01.png", "02.png"], tmp_path)
    (root_path / "loose").mkdir()
    shutil.copy(FIXTURES / "accepted.jpg", root_path / "loose" / "accepted.jpg")
    context = await context_for(jobs.SCAN, {"root_id": root.id}, priority=WAITED_ON_PRIORITY)

    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    probes = await _probes(temp_db)
    assert len(probes) == 3, "two pictures out of the archive and the loose one"
    assert {priority for _, priority in probes} == {WAITED_ON_PRIORITY}


async def test_a_file_never_read_is_asked_again_at_the_press_urgency(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
) -> None:
    (root_path / "loose").mkdir()
    shutil.copy(FIXTURES / "accepted.jpg", root_path / "loose" / "accepted.jpg")
    first = await context_for(jobs.SCAN, {"root_id": root.id}, priority=DEFAULT_PRIORITY)
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    # Its probe lost before it ran: the file is taken in, unchanged, and never read.
    await temp_db.execute("DELETE FROM jobs WHERE type = ?", (taking_in.PROBE,))

    again = await context_for(jobs.SCAN, {"root_id": root.id}, priority=WAITED_ON_PRIORITY)
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    probes = await _probes(temp_db)
    assert len(probes) == 1
    assert probes[0][1] == WAITED_ON_PRIORITY
