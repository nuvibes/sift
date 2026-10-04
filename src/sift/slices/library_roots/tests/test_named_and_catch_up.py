# SPDX-License-Identifier: AGPL-3.0-or-later
"""A scan of named files, the catch-up after Sift was closed, and what each leaves out."""

from __future__ import annotations

import errno
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LibraryStore, Root
from sift.kernel.content.library import FolderRow
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    JobQueue,
)
from sift.kernel.ledger import Actor
from sift.slices.library_roots import catch_up, jobs, sweeping, walking
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import (
    VIDEO_SECONDS,
    RecordingReindexer,
    draw,
)
from sift.slices.library_roots.tests.test_jobs import (
    Context,
    assets_in,
    finished,
    scans_asked_for,
)

# --- a scan of NAMED files ------------------------------------------------------------------------
#
# A change notification carries the path of the file that changed, so the scan it asks for does not
# have to rediscover it. What these are about is the two halves narrowing TOGETHER: a pass that
# looked at three files must take those three in and must conclude nothing whatever about the rest
# of the library, because it never examined it.


async def test_a_named_scan_takes_in_the_named_file_and_looks_at_nothing_else(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """Two files on the disk, one named. One asset.

    This is the whole saving, stated as behaviour rather than as a timing: the pass does not walk,
    so a library of half a million files costs one `stat` when one file is dropped into it.
    """
    draw(root_path / "named.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "other.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id, "paths": ["named.mp4"]})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 1, "the file that was not named was never looked at"


async def test_a_named_scan_does_not_mark_the_rest_of_the_library_missing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    content_store: ContentStore,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    """A named scan concludes about what it NAMED, and about nothing else.

    The other file is removed from the disk on purpose, and that is what makes this test bite. With
    both files present, `_still_there` reaches the right answer whether or not the sweep narrows
    (it looks for the file, finds it, and leaves it alone), so the filtering could be deleted and
    nothing would fail. That guard is the correctness one and this one is not redundant with it:
    what `only` decides is whether a pass may conclude anything at all about a row it never
    examined.

    So: one file arrives, another disappears, and the scan is told only about the arrival. The
    disappearance is real and will be found (by the reconciliation at start, or by a folder scan),
    but NOT by this pass, which never looked. An unfiltered sweep says "gone" about a file it
    was not asked about, which is a conclusion drawn from an absence of evidence.
    """
    draw(root_path / "old.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    before = [one.rel_path async for one in library_store.iter_locations_in_root(root.id)]
    assert before == ["old.mp4"], "the ordinary scan must have taken it in"

    (root_path / "old.mp4").unlink()
    draw(root_path / "new.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    named = await context_for(jobs.SCAN, {"root_id": root.id, "paths": ["new.mp4"]})
    await jobs.scan(named, settings=settings, service=service, reindexer=reindexer)

    still_here = {one.rel_path async for one in library_store.iter_locations_in_root(root.id)}
    assert still_here == {"old.mp4", "new.mp4"}, (
        "the pass was told about one file and must have said nothing about the other"
    )


async def test_a_named_scan_still_notices_the_named_file_has_gone(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    """Filtered is not the same as switched off.

    Within what it looked at, a named scan reaches the same conclusions an ordinary one does. A file
    that was named and is not there is marked missing, exactly as a walk would have.
    """
    draw(root_path / "here.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)

    (root_path / "here.mp4").unlink()
    named = await context_for(jobs.SCAN, {"root_id": root.id, "paths": ["here.mp4"]})
    await jobs.scan(named, settings=settings, service=service, reindexer=reindexer)

    present = [one.rel_path async for one in library_store.iter_locations_in_root(root.id)]
    assert present == [], "the named file was looked for, and it is not there"


async def test_a_named_scan_makes_the_folder_row_a_new_file_needs(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """A named scan does not reconcile the folder tree (that is the whole root's pass), so a
    file in a folder no pass has seen yet has no row to sit under until the take-in makes one."""
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    (root_path / "fresh").mkdir()
    draw(root_path / "fresh" / "new.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    named = await context_for(jobs.SCAN, {"root_id": root.id, "paths": ["fresh/new.mp4"]})
    await jobs.scan(named, settings=settings, service=service, reindexer=reindexer)

    folder = await library_store.folder_at(root.id, "fresh")
    assert folder is not None, "the folder row was made on the way in"
    present = [one.rel_path async for one in library_store.iter_locations_in_root(root.id)]
    assert "fresh/new.mp4" in present


async def test_the_catch_up_leaves_a_root_that_is_not_there_exactly_as_it_is(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drive not plugged in yet, a share still asleep at start: the pass at start asks the root
    once and stops. Every folder under it would otherwise read as moved, and every one of them
    be walked for nothing."""
    draw(root_path / "clips" / "kept.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await finished(first)
    folders_before = sorted(one.rel_path for one in await library_store.folders_in_root(root.id))
    scans_before = await scans_asked_for(job_queue)

    def off(path: Path) -> OSError | None:
        return OSError(53, "The network path was not found")

    monkeypatch.setattr(jobs, "_root_answer", off)
    catching_up = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(catching_up, service=service)

    assert await scans_asked_for(job_queue) == scans_before, "nothing was walked or named"
    folders_after = sorted(one.rel_path for one in await library_store.folders_in_root(root.id))
    assert folders_after == folders_before


def test_the_root_is_answered_by_one_stat_and_only_a_folder_counts(tmp_path: Path) -> None:
    """A root that is not there and a root whose path has become a file are both refusals, each
    carrying the error that says which."""
    assert walking._root_answer(tmp_path) is None
    missing = walking._root_answer(tmp_path / "unplugged")
    assert isinstance(missing, OSError) and not isinstance(missing, NotADirectoryError)
    (tmp_path / "a-file").write_bytes(b"x")
    became_a_file = walking._root_answer(tmp_path / "a-file")
    assert isinstance(became_a_file, NotADirectoryError)
    assert became_a_file.errno == errno.ENOTDIR


@pytest.mark.parametrize(
    "named",
    [
        pytest.param("/etc/passwd", id="absolute"),
        pytest.param("../outside.mp4", id="parent"),
        pytest.param("clips/../../outside.mp4", id="climbing"),
    ],
)
async def test_a_named_scan_refuses_a_path_that_is_not_a_library_path(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    named: str,
) -> None:
    """Refused, and refused TWICE, which is the point of asserting it this way.

    The queue itself will not accept a payload holding an absolute path or a `..` segment, so the
    first refusal happens before a job exists at all. `_named_paths` then refuses the same thing
    again when the payload is read, through `check_rel_path`, so a payload built by anything that
    did not go through the queue is refused too. Either one alone would pass this test; the reason
    both exist is that neither is allowed to be the only one.
    """
    with pytest.raises(ValueError):
        context = await context_for(jobs.SCAN, {"root_id": root.id, "paths": [named]})
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_the_scan_refuses_a_bad_path_even_when_the_queue_never_saw_it(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The second of the two gates, on its own.

    The test above cannot tell which refusal fired. This builds a valid payload, gets a context for
    it, and then puts a traversal into it directly, so the queue's check is bypassed and the only
    thing left is the one in `_named_paths`. Without that gate this passes silently and a payload
    from anywhere but the queue reaches a filesystem call.
    """
    context = await context_for(jobs.SCAN, {"root_id": root.id, "paths": ["fine.mp4"]})
    context.payload["paths"] = ["../../outside.mp4"]

    with pytest.raises(ValueError):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_a_named_scan_refuses_more_paths_than_the_cap(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """Refused whole rather than truncated.

    A silently shortened list is a scan that reports success having looked at some of what it was
    asked about, which is the worst of both: the watcher's own cap is what keeps this from ever
    being reached, and this is what makes that a rule rather than a habit.
    """
    too_many = [f"{index}.mp4" for index in range(jobs.MOST_NAMED_PATHS + 1)]
    context = await context_for(jobs.SCAN, {"root_id": root.id, "paths": too_many})
    with pytest.raises(ValueError):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_a_named_scan_of_nothing_does_not_widen_into_a_walk(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """An empty list means "nothing worth opening turned up", not "look at everything".

    The two are one keystroke apart in the code that reads the payload and a whole library apart in
    what they do: widened, a burst that happened to contain no media would walk the entire root.
    """
    draw(root_path / "untouched.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id, "paths": []})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 0


# --- catching up with what happened while Sift was not running -------------------------------------
#
# A watcher reports CHANGES, and a change is only a change relative to the moment it started. These
# are about the pass that finds what was already there, and about it costing the number of folders
# rather than the number of files, which is the whole reason it can run at every start.


async def test_a_file_that_arrived_while_sift_was_off_is_found_and_named(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """A file that arrived while Sift was closed is found at the next start.

    A scan records what each folder's directory looked like. Something arrives with Sift closed.
    The pass at start stats the folders, sees one whose timestamp moved, lists just that one, and
    asks for the file BY NAME, so finding it costs one listing rather than a walk.
    """
    draw(root_path / "clips" / "old.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await finished(first)

    draw(root_path / "clips" / "arrived.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    catching_up = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(catching_up, service=service)

    asked = [payload for payload in await scans_asked_for(job_queue) if "paths" in payload]
    assert asked, "the pass must have asked for the file that arrived"
    named: set[str] = set()
    for payload in asked:
        carried = payload["paths"]
        assert isinstance(carried, list)
        named.update(carried)
    assert named == {"clips/arrived.mp4"}, (
        "only the difference is named: the file that did not move is never mentioned again"
    )


async def test_a_catch_up_leaves_a_library_to_a_walk_of_it_already_coming(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """Adding a folder asks for its walk and for this pass at once. Both would take every file in,
    each file twice and some read twice. A walk of the whole library already waiting takes in
    everything this pass would name, so it names nothing."""
    draw(root_path / "clips" / "old.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await finished(first)
    draw(root_path / "clips" / "arrived.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    # A walk of one folder is not the whole library, and leaves the pass its work.
    await job_queue.enqueue(jobs.SCAN, {"root_id": root.id, "folder_id": "somewhere-else"})
    await job_queue.enqueue(jobs.SCAN, {"root_id": root.id})
    before = await scans_asked_for(job_queue)

    catching_up = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(catching_up, service=service)

    assert await scans_asked_for(job_queue) == before, "the walk coming takes the new file in"


async def test_a_library_where_nothing_moved_asks_for_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """The ordinary case at every start, and the one that has to cost nothing.

    Every folder is stat'd and not one is listed, because not one of them moved. This is what makes
    the pass affordable on a library of hundreds of thousands of files: it is a question about
    folders, and the answer is usually no.
    """
    draw(root_path / "clips" / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "two.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await finished(first)
    before = len(await scans_asked_for(job_queue))

    catching_up = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(catching_up, service=service)

    assert len(await scans_asked_for(job_queue)) == before, (
        "nothing moved, so nothing was asked for"
    )


async def test_a_file_removed_while_sift_was_off_is_named_too(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """A removal is a difference like an arrival, and it goes the same way.

    `look_at` leaves out a path that is not there and the sweep, narrowed to the named paths, is
    what marks it missing. So the same one mechanism carries both directions.
    """
    draw(root_path / "clips" / "goes.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "clips" / "stays.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await finished(first)

    (root_path / "clips" / "goes.mp4").unlink()

    catching_up = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(catching_up, service=service)

    named: set[str] = set()
    for payload in await scans_asked_for(job_queue):
        carried = payload.get("paths")
        if isinstance(carried, list):
            named.update(carried)
    assert named == {"clips/goes.mp4"}


async def test_a_new_folder_is_walked_rather_than_named(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """A directory Sift has no row for cannot be answered by naming files.

    It is genuinely new work and it needs a walk, bounded by that new subtree rather than by the
    library. Naming its files would leave the folder itself unrecorded.
    """
    draw(root_path / "clips" / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await finished(first)

    draw(root_path / "clips" / "deeper" / "new.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    catching_up = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(catching_up, service=service)

    asked = await scans_asked_for(job_queue)
    assert any("paths" not in payload for payload in asked), (
        "a folder Sift has never seen is walked, not named"
    )


async def test_the_catch_up_writes_down_what_it_saw_so_it_does_not_do_it_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """The catch-up pass writes down what it saw.

    Every folder is recorded as NULL by the migration, so the FIRST pass looks at all of them:
    that is the design, once. If the pass wrote nothing down, every folder would still be NULL at
    the next start and it would look at all of them again, and again, for ever. The saving is
    entirely in the second pass.

    A folder holding something Sift does not index counts too. Its timestamp moved and it was
    listed and found to hold no differences: it has been examined, and re-listing it at every
    start is exactly the cost this exists to avoid.
    """
    draw(root_path / "clips" / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    (root_path / "clips" / "notes.txt").write_text("not media", encoding="utf-8")
    scanned = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(scanned, settings=settings, service=service, reindexer=reindexer)
    await finished(scanned)

    # Something Sift will not index arrives, so the folder's timestamp moves and it is examined.
    (root_path / "clips" / "more.txt").write_text("still not media", encoding="utf-8")

    first = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(first, service=service)
    folders = await library_store.folders_in_root(root.id)
    looked_at, _ = catch_up._folders_that_moved(root_path, folders)
    assert looked_at == [], (
        "a folder this pass examined must be written down, or the next start examines it again"
    )

    before = len(await scans_asked_for(job_queue))
    second = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(second, service=service)
    assert len(await scans_asked_for(job_queue)) == before, "and the second pass finds nothing"


async def test_an_unchanged_folder_is_ruled_out_by_its_timestamp_and_never_listed(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    """The claim the whole catch-up rests on, asserted directly rather than through its results.

    Asked through the jobs it enqueues, this cannot be tested: a pass that listed EVERY folder and
    found no differences enqueues exactly what a pass that listed none does. The outcome is the same
    and the cost is the whole point: on a library of thousands of folders across a network share,
    listing all of them at every start is the thing being avoided.

    So `_folders_that_moved` is asked directly. It is the one function that decides what gets read,
    and what it returns IS the cost.
    """
    draw(root_path / "clips" / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "other" / "two.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    scanned = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(scanned, settings=settings, service=service, reindexer=reindexer)

    folders = await library_store.folders_in_root(root.id)
    assert folders, "the scan must have made folder rows"
    assert all(folder.seen_mtime is not None for folder in folders), (
        "a scan records what each directory looked like, or there is nothing to compare against"
    )

    moved, walk = catch_up._folders_that_moved(root_path, folders)
    assert moved == [], "nothing moved, so not one folder is opened"
    assert walk == set()

    # And now one of them really changes.
    draw(root_path / "clips" / "three.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    folders = await library_store.folders_in_root(root.id)
    moved, walk = catch_up._folders_that_moved(root_path, folders)
    assert [folder.rel_path for folder, _ in moved] == ["clips"], (
        "exactly the folder that changed, and no other"
    )


# --- what a named pass and a catch-up leave out -------------------------------------------------
#
# All pure functions over a directory, so they are driven directly. Each branch below is a way a
# real library says no (a path that will not resolve, a link, a file that went between the
# notification and the stat), and every one of them is a `continue` whose absence would be a scan
# that either fails or takes in something it was never meant to touch.


def _folder(rel_path: str, *, parent_id: str | None = "01HX0000000000000000000001") -> FolderRow:
    return FolderRow(
        id=new_id(),
        root_id="01HX0000000000000000000000",
        parent_id=parent_id,
        rel_path=rel_path,
        name=rel_path.rsplit("/", 1)[-1],
        seen_mtime=None,
    )


def test_a_named_path_that_does_not_resolve_inside_the_root_is_left_out(tmp_path: Path) -> None:
    """The walk refuses the same thing at its starting directory. A path that arrived from a
    notification gets no laxer a reading than one Sift found itself."""
    (tmp_path / "outside.mp4").write_bytes(b"x")
    root = tmp_path / "library"
    root.mkdir()

    looked = walking.look_at(root, ["../outside.mp4"])

    assert looked.files == ()
    assert looked.looked is True, "it positively looked, and found nothing it may open"


def test_a_named_path_that_is_not_worth_opening_is_left_out(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not media", encoding="utf-8")
    (tmp_path / "clip.mp4").write_bytes(b"x")

    looked = walking.look_at(tmp_path, ["notes.txt", "clip.mp4"])

    assert [one.rel_path for one in looked.files] == ["clip.mp4"]


def test_a_named_path_that_has_gone_since_the_notification_is_left_out(tmp_path: Path) -> None:
    """A notification describes a moment that has already passed. The sweep is what decides a file
    is really absent, by looking for it. This pass simply has nothing to say about it."""
    looked = walking.look_at(tmp_path, ["never-there.mp4"])

    assert looked.files == ()


def test_a_scan_payload_that_is_not_a_list_of_paths_is_refused(temp_db: Database) -> None:
    """Refused as a whole rather than filtered. A payload this cannot read is one whose author
    believed something about the scan that is not true."""
    with pytest.raises(ValueError):
        jobs._named_paths(_a_context(temp_db, {"paths": "clip.mp4"}))
    with pytest.raises(ValueError):
        jobs._named_paths(_a_context(temp_db, {"paths": ["clip.mp4", 7]}))


def _a_context(database: Database, payload: dict[str, Any]) -> Any:
    """Just enough of a job context for the payload reader, which reads nothing else."""

    class _Context:
        def __init__(self) -> None:
            self.payload = payload

    return _Context()


def test_a_folder_whose_row_will_not_resolve_is_stepped_over(tmp_path: Path) -> None:
    """Not walked, not stat'd, and above all not read through: the same confinement every write
    goes through, applied to a row before it reaches the filesystem."""
    moved, walk = catch_up._folders_that_moved(tmp_path, [_folder("../outside")])

    assert moved == []
    assert walk == set()


def test_a_folder_that_cannot_be_looked_at_puts_its_PARENT_on_the_walking_list(
    tmp_path: Path,
) -> None:
    """Gone and unreadable are indistinguishable from here, and only the parent's own listing can
    tell a folder that was REMOVED from one that was RENAMED. So the parent is walked."""
    folder = _folder("clips", parent_id="01HX000000000000000000000P")

    moved, walk = catch_up._folders_that_moved(tmp_path, [folder])

    assert moved == []
    assert walk == {"01HX000000000000000000000P"}


def test_a_root_folder_that_cannot_be_looked_at_walks_nothing(tmp_path: Path) -> None:
    """It has no parent to walk, and there is nothing above a root to compare a listing against."""
    moved, walk = catch_up._folders_that_moved(tmp_path / "gone", [_folder("", parent_id=None)])

    assert (moved, walk) == ([], set())


def test_a_folder_that_cannot_be_listed_reports_no_differences(tmp_path: Path) -> None:
    """Rather than reporting that everything in it has gone, which is what an empty listing would
    say if it were believed."""
    assert catch_up._differences(tmp_path, _folder("not-there"), set(), set()) == (set(), False)


def test_only_media_and_unknown_SUBFOLDERS_count_as_a_difference(tmp_path: Path) -> None:
    """A directory Sift has no row for is `structural`: it needs walking rather than naming,
    because a new subtree has files in it and a renamed one has to be recognised. Everything else
    in the folder is either a file worth opening or nothing this cares about."""
    inside = tmp_path / "clips"
    (inside / "known").mkdir(parents=True)
    (inside / "fresh").mkdir()
    (inside / "clip.mp4").write_bytes(b"x")
    (inside / "notes.txt").write_text("not media", encoding="utf-8")

    differences, structural = catch_up._differences(
        tmp_path, _folder("clips"), set(), {"clips/known"}
    )

    assert differences == {"clips/clip.mp4"}, (
        "the text file is not media, and folders are not files"
    )
    assert structural is True, "`fresh` is a folder Sift has no row for"

    settled, unchanged = catch_up._differences(
        tmp_path, _folder("clips"), {("clip.mp4", 1)}, {"clips/known", "clips/fresh"}
    )
    assert settled == set(), "a file already recorded at that size is not mentioned again"
    assert unchanged is False


async def test_a_catch_up_for_a_library_that_has_gone_says_so_rather_than_walking_nothing(
    context_for: Context, root: Root, library_store: LibraryStore, service: LibraryService
) -> None:
    """A root removed between the pass being queued and running is not an empty library.

    Treated as one, the catch-up would conclude that every folder in it had gone, and the whole
    reason this pass exists is to decide that from a listing rather than from an absence.
    """
    await library_store.delete_root(root.id, actor=Actor.sift("folder"))

    context = await context_for(jobs.RECONCILE, {"root_id": root.id})
    with pytest.raises(walking.RootIsGone):
        await jobs.reconcile(context, service=service)


async def test_a_folder_already_getting_a_walk_is_not_also_named(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
    job_queue: JobQueue,
) -> None:
    """A folder that gained BOTH a file and a subfolder while Sift was closed.

    The subfolder makes it structural, so its whole subtree is walked, and a walk covers the new
    file as well. Naming it too would be the same file taken in twice, once by each job.
    """
    draw(root_path / "clips" / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    scanned = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(scanned, settings=settings, service=service, reindexer=reindexer)
    await finished(scanned)

    draw(root_path / "clips" / "two.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    (root_path / "clips" / "fresh").mkdir()

    context = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(context, service=service)

    asked = await scans_asked_for(job_queue)
    walked = [one for one in asked if "folder_id" in one and "paths" not in one]
    named = [one for one in asked if "paths" in one]
    assert walked, "the new subfolder makes this a walk"
    assert named == [], "and a walk of the same folder covers the file that arrived beside it"


class _AnEntryThatWillNotSay:
    """A directory entry that answers every question with a refusal.

    `os.scandir` hands back live handles, and what this stands in for is the moment between the
    listing and the question: a file removed, a permission changed, a share that dropped. Windows
    has no way to produce any of that on demand, so the entry itself is the fixture.
    """

    name = "gone.mp4"

    def is_junction(self) -> bool:
        return False

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return False

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return True

    def stat(self, *, follow_symlinks: bool = True) -> object:
        raise OSError("it went")


class _AnEntryThatIsNeither:
    """Neither a directory nor a file: a pipe, a socket, a device. Left alone rather than opened."""

    name = "pipe.mp4"

    def is_junction(self) -> bool:
        return False

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return False

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return False


def test_an_entry_that_stops_answering_between_the_listing_and_the_question_is_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "clips").mkdir()
    monkeypatch.setattr(
        catch_up.os,  # type: ignore[attr-defined]
        "scandir",
        lambda _where: [_AnEntryThatWillNotSay(), _AnEntryThatIsNeither()],
    )

    differences, structural = catch_up._differences(tmp_path, _folder("clips"), set(), set())

    assert differences == set(), "neither of them is a file this pass can say anything about"
    assert structural is False


def test_a_named_path_that_is_not_a_plain_file_is_left_out(tmp_path: Path) -> None:
    """A directory carrying a media extension, and a junction to one.

    Both are things a notification can name and neither is a file to take in. The junction matters
    on the platform Sift ships on: it needs no privilege to make, so it is what somebody's library
    really contains where a symbolic link would be what it contains anywhere else.
    """
    (tmp_path / "clips.mp4").mkdir()
    (tmp_path / "real").mkdir()
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(tmp_path / "link.mp4"), str(tmp_path / "real")],
            check=True,
            capture_output=True,
        )
    else:
        (tmp_path / "link.mp4").symlink_to(tmp_path / "real", target_is_directory=True)

    looked = walking.look_at(tmp_path, ["clips.mp4", "link.mp4"])

    assert looked.files == ()


def test_a_junction_inside_a_folder_is_not_a_difference(tmp_path: Path) -> None:
    """It is not a file, and following it would put a whole tree from somewhere else into a folder
    Sift is comparing against its own rows."""
    inside = tmp_path / "clips"
    inside.mkdir()
    (tmp_path / "elsewhere").mkdir()
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(inside / "link"), str(tmp_path / "elsewhere")],
            check=True,
            capture_output=True,
        )
    else:
        (inside / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    differences, structural = catch_up._differences(tmp_path, _folder("clips"), set(), set())

    assert differences == set()
    assert structural is False, "a junction is not a subfolder Sift has never seen"


class _APathThatGoesMidLook:
    """A path that is a file right up until it is asked how big it is.

    The window is real and it is small: `look_at` checks the path is a plain file and then stats it,
    and a file removed between those two calls raises rather than answering. It cannot be produced
    on demand (the whole point is that it is a race), so the path itself stands in for it.
    """

    suffix = ".mp4"

    def is_junction(self) -> bool:
        return False

    def is_file(self) -> bool:
        return True

    def stat(self) -> object:
        raise OSError("it went between the check and the question")


def test_a_named_path_that_goes_while_it_is_being_looked_at_is_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not an error. The sweep below is what decides a file is really absent, by looking for it."""
    monkeypatch.setattr(walking, "confine", lambda _root, _candidate: _APathThatGoesMidLook())

    looked = walking.look_at(tmp_path, ["clip.mp4"])

    assert looked.files == ()
    assert looked.looked is True


async def test_a_walked_directory_with_no_folder_row_records_nothing(
    context_for: Context, root: Root, root_path: Path
) -> None:
    """A directory the walk saw and the tree has no row for.

    Ordinary rather than exceptional: the rows are made as the files in them are taken in, so a walk
    that reaches an empty directory before anything has been indexed there passes over one. What
    matters is that it is stepped over: there is nothing to record a timestamp against, and
    inventing a row here would put a folder into the tree by the back door.
    """
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    walk = walking.Walk(files=(), directories=("nowhere",), looked=True, mtimes={"nowhere": 12.0})

    await sweeping._record_what_was_seen(context, root_id=root.id, under="", walk=walk)

    assert await context.library.folder_at(root.id, "nowhere") is None


async def test_one_press_of_scan_is_one_job_with_a_folder_of_it_each(
    context_for: Context,
    root: Root,
    library_store: LibraryStore,
    job_queue: JobQueue,
    tmp_path: Path,
) -> None:
    """The fan-out is the server's, and this is what it buys.

    A loop in the client (one request per library folder) would make a job per folder with
    nothing joining them, a row per folder in the scan history stamped the same second, and no
    total anywhere.

    One job, and the folders hang off it, which is the shape `probe` and its pictures already
    have, so cancelling the parent cancels the tree and the Jobs screen draws it as one family.

    They are still separate jobs, deliberately: folders are independent and the pool is wide, so
    they walk alongside each other. One job walking them in turn would take the sum of them on one
    worker while the rest of the pool had nothing to do.
    """
    second = tmp_path / "second"
    second.mkdir()
    await library_store.create_root(name="Photos", abs_path=second)

    context = await context_for(jobs.LIBRARY_SCAN, {})
    await jobs.scan_everything(context)

    page = await job_queue.list(limit=100, parent_id=context.job.id)
    assert [job.type for job in page.jobs] == [jobs.SCAN, jobs.SCAN]
    assert {job.payload["root_id"] for job in page.jobs} == {
        root.id,
        *(one.id for one in await library_store.roots() if one.id != root.id),
    }
    # And no flag on an ordinary pass, or every scan in the library changes identity (see
    # `test_an_ordinary_scan_leaves_the_probe_payload_as_it_was` for what that costs).
    assert all("scan_only" not in job.payload for job in page.jobs)


async def test_a_press_of_scan_only_asks_every_folder_for_the_reading_alone(
    context_for: Context,
    root: Root,
    job_queue: JobQueue,
) -> None:
    """The flag travels to every part, or the pass is scan-only for some folders and not others."""
    _ = root
    context = await context_for(jobs.LIBRARY_SCAN, {"scan_only": True})
    await jobs.scan_everything(context)

    page = await job_queue.list(limit=100, parent_id=context.job.id)
    assert [job.payload.get("scan_only") for job in page.jobs] == [True]


async def test_a_folder_already_being_walked_is_not_walked_twice(
    context_for: Context,
    root: Root,
    job_queue: JobQueue,
) -> None:
    """Asked with `is_live`, and the note says what was skipped.

    Not `enqueue(dedupe=True)`, which is the other tool and answers a different question: dedupe
    collapses onto a WAITING row and hands back its id, so the folder would count as a part of this
    pass while actually belonging to whichever pass queued it, and its run would be filed under
    that pass in the history. Asking first is this caller's question, and a RUNNING scan answers it
    as well as a waiting one does.
    """
    await job_queue.enqueue(jobs.SCAN, {"root_id": root.id})

    context = await context_for(jobs.LIBRARY_SCAN, {})
    await jobs.scan_everything(context)

    assert (await job_queue.list(limit=100, parent_id=context.job.id)).total == 0
    settled = await job_queue.get(context.job.id)
    assert settled is not None
    assert settled.note == "0 of 1 folders"


async def test_a_library_with_no_folders_says_so_rather_than_finishing_silently(
    context_for: Context,
    job_queue: JobQueue,
) -> None:
    """A job that returns instantly and says nothing reads as broken. See `JobContext.set_note`."""
    context = await context_for(jobs.LIBRARY_SCAN, {})
    await jobs.scan_everything(context)

    settled = await job_queue.get(context.job.id)
    assert settled is not None
    assert settled.note == "no library folders to scan"
