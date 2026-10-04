# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder rearranged on the disk is the same folder, and Sift goes on knowing what it knew.

Everything Sift records about a FILE survives the file being moved, because a file is recognised by
its bytes. A FOLDER recognised only by its path would lose everything recorded about it when it was
renamed, and a share, a restriction, a concealment and the rule saying whose files land in a
folder are all written on the folder rather than on what is inside it.

So these are the tests that matter here, and they are about what is kept rather than about what is
found: a renamed folder keeps its id, keeps its sharing, and costs no re-reading. Beside them are
the ones about not being sure, which all end the same way: an unrecognised folder is a new folder,
which loses what a folder remembered, and that is the mistake worth making. The other one hands
somebody a folder they were never shared.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sift.kernel.access import Effect, ObjectType, Repository
from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.library_write import forget_folder
from sift.slices.library_roots import jobs, moved_folders, sweeping, walking
from sift.slices.library_roots.service import LibraryService
from sift.testing.fixtures import Actors

from .conftest import RecordingReindexer, draw
from .test_jobs import Context


async def _scan(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


# --- what a folder is at all ---------------------------------------------------------------------


async def test_a_folder_with_nothing_in_it_is_a_folder(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """A folder somebody has just made exists in Sift, in one assertion.

    A folder that existed only as a side effect of taking a file out of it would be in no list,
    could not be downloaded into and could not be shared.
    """
    (root_path / "new-folder").mkdir()

    await _scan(context_for, root, settings, service, reindexer)

    assert await library_store.folder_at(root.id, "new-folder") is not None


async def test_a_folder_holding_only_files_sift_will_not_open_is_still_a_folder(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """A folder of documents is a folder. It is on the disk, so it is in the tree."""
    (root_path / "paperwork").mkdir()
    (root_path / "paperwork" / "notes.txt").write_text("nothing to index", encoding="utf-8")

    await _scan(context_for, root, settings, service, reindexer)

    assert await library_store.folder_at(root.id, "paperwork") is not None


# --- a folder that moved -------------------------------------------------------------------------


async def test_a_renamed_folder_is_the_same_folder(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """The id survives, which is what everything else in this file is really asserting."""
    draw(root_path / "holidays" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "holidays" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    before = await library_store.folder_at(root.id, "holidays")
    assert before is not None

    (root_path / "holidays").rename(root_path / "trips")
    await _scan(context_for, root, settings, service, reindexer)

    after = await library_store.folder_at(root.id, "trips")
    assert after is not None
    assert after.id == before.id
    assert after.name == "trips"
    assert await library_store.folder_at(root.id, "holidays") is None


async def test_a_renamed_folder_keeps_what_it_was_shared_with(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
    access: Repository,
    actors: Actors,
) -> None:
    """The reason the id has to survive, stated as the thing somebody would actually lose.

    A folder is what a share is written on. Recognised as new, the share would have been dropped by
    the removal of the old row, silently, while the folder itself is sitting there renamed.
    """
    draw(root_path / "holidays" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "holidays" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    folder = await library_store.folder_at(root.id, "holidays")
    assert folder is not None
    await access.grant(ObjectType.FOLDER, folder.id, actors.guest.id, Effect.SHARE)

    (root_path / "holidays").rename(root_path / "trips")
    await _scan(context_for, root, settings, service, reindexer)

    moved = await library_store.folder_at(root.id, "trips")
    assert moved is not None
    kept = await access.grants_on(ObjectType.FOLDER, moved.id)
    assert [one.subject_user_id for one in kept] == [actors.guest.id]


async def test_a_renamed_folder_does_not_re_read_its_files(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
    temp_db: Database,
) -> None:
    """The cost half, and it is the difference between instant and a full library pass.

    The location rows move rather than being replaced, so the take-in that follows finds every file
    exactly where it is recorded and reads none of them. A location that was replaced would have a
    new id and a new `first_seen_at`, and every file in the folder would have been hashed again.
    """
    draw(root_path / "holidays" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "holidays" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    before = {
        (row["id"], row["asset_id"])
        for row in await temp_db.fetch_all("SELECT id, asset_id FROM asset_locations")
    }

    (root_path / "holidays").rename(root_path / "trips")
    reindexer.told.clear()
    await _scan(context_for, root, settings, service, reindexer)

    rows = await temp_db.fetch_all("SELECT id, asset_id, rel_path, status FROM asset_locations")
    assert {(row["id"], row["asset_id"]) for row in rows} == before
    assert sorted(str(row["rel_path"]) for row in rows) == ["trips/a.png", "trips/b.png"]
    assert {str(row["status"]) for row in rows} == {"present"}
    # Nothing was taken in, so nothing was handed to the index. A folder that had been re-read
    # would have told it about every file in it.
    assert reindexer.told == []


async def test_a_folder_moved_deeper_takes_what_is_under_it(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """A move is recognised at the top and the whole subtree goes with it, in one rewrite."""
    draw(root_path / "shoot" / "inner" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "shoot" / "inner" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    inner = await library_store.folder_at(root.id, "shoot/inner")
    assert inner is not None

    (root_path / "archive").mkdir()
    (root_path / "shoot").rename(root_path / "archive" / "shoot")
    await _scan(context_for, root, settings, service, reindexer)

    moved = await library_store.folder_at(root.id, "archive/shoot/inner")
    assert moved is not None
    assert moved.id == inner.id
    # The name is the last part of where it now is, and a descendant keeps its own.
    assert moved.name == "inner"


async def test_an_empty_folder_renamed_beside_nothing_else_is_recognised(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """A folder shared before anything was put in it has nothing to compare, and is still the same
    folder, but only where the shape leaves no room to be wrong: one empty folder went, one empty
    folder came, in the same place."""
    (root_path / "pending").mkdir()
    await _scan(context_for, root, settings, service, reindexer)
    before = await library_store.folder_at(root.id, "pending")
    assert before is not None

    (root_path / "pending").rename(root_path / "ready")
    await _scan(context_for, root, settings, service, reindexer)

    after = await library_store.folder_at(root.id, "ready")
    assert after is not None
    assert after.id == before.id


# --- when it is not sure -------------------------------------------------------------------------


async def test_two_folders_that_swap_names_are_not_guessed_at(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """Two empty folders that both went and both came leave two candidates each, so neither is
    matched. They come back as new folders, which loses what they remembered: the mistake this is
    willing to make, because the other one gives somebody a folder they were never shared."""
    (root_path / "one").mkdir()
    (root_path / "two").mkdir()
    await _scan(context_for, root, settings, service, reindexer)
    first = await library_store.folder_at(root.id, "one")
    second = await library_store.folder_at(root.id, "two")
    assert first is not None and second is not None

    (root_path / "one").rename(root_path / "three")
    (root_path / "two").rename(root_path / "four")
    await _scan(context_for, root, settings, service, reindexer)

    third = await library_store.folder_at(root.id, "three")
    fourth = await library_store.folder_at(root.id, "four")
    assert third is not None and fourth is not None
    assert {third.id, fourth.id} & {first.id, second.id} == set()


async def test_a_folder_copied_rather_than_moved_is_not_the_original(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """The original is still there, so nothing vanished and there is nothing to recognise."""
    draw(root_path / "shoot" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "shoot" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    original = await library_store.folder_at(root.id, "shoot")
    assert original is not None

    copy = root_path / "copy"
    copy.mkdir()
    for name in ("a.png", "b.png"):
        (copy / name).write_bytes((root_path / "shoot" / name).read_bytes())
    await _scan(context_for, root, settings, service, reindexer)

    made = await library_store.folder_at(root.id, "copy")
    assert made is not None
    assert made.id != original.id
    assert await library_store.folder_at(root.id, "shoot") is not None


# --- a folder that is really gone ----------------------------------------------------------------


async def test_a_deleted_folder_loses_its_row_and_its_grants(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
    access: Repository,
    actors: Actors,
    temp_db: Database,
) -> None:
    """The grants go with the row, and they go FIRST.

    `acl_grants.object_id` carries no foreign key, so nothing cascades them. Left behind they name
    an id, and an id is not a promise never to be reused, so the next folder given that id would
    arrive already shared with somebody.
    """
    (root_path / "gone").mkdir()
    await _scan(context_for, root, settings, service, reindexer)
    folder = await library_store.folder_at(root.id, "gone")
    assert folder is not None
    await access.grant(ObjectType.FOLDER, folder.id, actors.guest.id, Effect.SHARE)

    (root_path / "gone").rmdir()
    await _scan(context_for, root, settings, service, reindexer)

    assert await library_store.folder_at(root.id, "gone") is None
    left = await temp_db.fetch_all(
        "SELECT id FROM acl_grants WHERE object_type = ? AND object_id = ?",
        (ObjectType.FOLDER.value, folder.id),
    )
    assert left == []


async def test_a_walk_that_could_not_look_removes_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one that would cost somebody their whole library.

    A drive that is not plugged in and a folder Sift is refused come back as a walk holding nothing,
    which reads as "every folder has gone" to anything that trusts it. This is the guard, and it
    is asserted rather than assumed because the failure is silent and total.
    """
    draw(root_path / "shoot" / "a.png", "testsrc2=size=64x48:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    before = await library_store.folder_at(root.id, "shoot")
    assert before is not None

    monkeypatch.setattr(
        jobs,
        "_walk_confined",
        lambda base, start: walking.Walk(files=(), directories=(), looked=False),
    )
    await _scan(context_for, root, settings, service, reindexer)

    after = await library_store.folder_at(root.id, "shoot")
    assert after is not None
    assert after.id == before.id


async def test_a_scan_of_one_folder_leaves_the_rest_of_the_library_alone(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """Handed the wrong scope this deletes a library's whole folder tree, so the scope is asserted.

    The watcher asks for exactly this (one folder, because a file landed in it), and every other
    folder in the library is one the walk deliberately did not look at.
    """
    draw(root_path / "one" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "two" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    other = await library_store.folder_at(root.id, "two")
    assert other is not None

    narrow = await library_store.folder_at(root.id, "one")
    assert narrow is not None
    context = await context_for(jobs.SCAN, {"root_id": root.id, "folder_id": narrow.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    still = await library_store.folder_at(root.id, "two")
    assert still is not None
    assert still.id == other.id


async def test_a_small_folder_is_not_mistaken_for_a_large_one_that_swallowed_it(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """Most of the old folder being here is not the same question as this being the old folder.

    Two pictures moved into a folder of twelve is not that folder arriving under a new name, and
    matching on the first question alone lets any small folder be swallowed by any large one it
    was copied into.
    """
    draw(root_path / "pair" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "pair" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    small = await library_store.folder_at(root.id, "pair")
    assert small is not None

    big = root_path / "everything"
    big.mkdir()
    for index in range(12):
        draw(big / f"{index}.png", f"testsrc2=size={72 + index * 8}x48:rate=1")
    for name in ("a.png", "b.png"):
        (root_path / "pair" / name).rename(big / name)
    (root_path / "pair").rmdir()
    await _scan(context_for, root, settings, service, reindexer)

    landed = await library_store.folder_at(root.id, "everything")
    assert landed is not None
    assert landed.id != small.id


async def test_a_moved_folder_hangs_off_the_row_it_is_really_inside(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """A folder's place is recorded twice (as a path and as a parent), and both have to move.

    Rewriting only the path leaves a folder listed under a heading it is not inside, which is a
    tree that draws wrongly and, worse, a tree whose permission inheritance walks the wrong way.
    """
    draw(root_path / "shoot" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "shoot" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)

    (root_path / "archive").mkdir()
    (root_path / "shoot").rename(root_path / "archive" / "shoot")
    await _scan(context_for, root, settings, service, reindexer)

    holder = await library_store.folder_at(root.id, "archive")
    moved = await library_store.folder_at(root.id, "archive/shoot")
    assert holder is not None and moved is not None
    assert moved.parent_id == holder.id


async def test_a_renamed_folder_takes_its_archives_with_it(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    tmp_path: Path,
    temp_db: Database,
) -> None:
    """A picture indexed out of an archive records the archive's path as well as its own.

    Both are paths inside the library and both go stale together. Left behind, every picture in a
    moved gallery points at a zip that is not there, and the next scan does not recognise the
    gallery it already has.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    where = root_path / "galleries" / "shoot.zip"
    where.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(where, "w", compression=zipfile.ZIP_DEFLATED) as writing:
        for index, name in enumerate(["one.png", "two.png"]):
            made = scratch / f"{index}-{name}"
            draw(made, f"testsrc2=size={64 + index * 8}x48:rate=1")
            writing.write(made, name)
            made.unlink()
    await _scan(context_for, root, settings, service, reindexer)

    (root_path / "galleries").rename(root_path / "shoots")
    await _scan(context_for, root, settings, service, reindexer)

    rows = await temp_db.fetch_all(
        "SELECT rel_path, archive_rel_path FROM asset_locations ORDER BY rel_path"
    )
    assert [str(row["archive_rel_path"]) for row in rows] == [
        "shoots/shoot.zip",
        "shoots/shoot.zip",
    ]
    assert all(str(row["rel_path"]).startswith("shoots/shoot.zip/") for row in rows)


async def test_two_folders_holding_the_same_thing_are_not_guessed_at(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """Two folders that could each be either of two others leave four candidates and no answer.

    The empty-folder rule has a uniqueness check of its own; this is the one on the rule that
    compares contents, and without it a library holding two identical folders would hand one
    folder's sharing to the other on a rename, with nothing on any screen to show for it.
    """
    for folder in ("left", "right"):
        for name, source in (("a.png", "size=64x48"), ("b.png", "size=48x64")):
            draw(root_path / folder / name, f"testsrc2={source}:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    before = {
        (await library_store.folder_at(root.id, "left")),
        (await library_store.folder_at(root.id, "right")),
    }
    assert None not in before

    (root_path / "left").rename(root_path / "one")
    (root_path / "right").rename(root_path / "two")
    await _scan(context_for, root, settings, service, reindexer)

    after = [
        await library_store.folder_at(root.id, "one"),
        await library_store.folder_at(root.id, "two"),
    ]
    assert all(one is not None for one in after)
    assert {one.id for one in after if one} & {one.id for one in before if one} == set()


async def test_a_deleted_folder_whose_files_are_deeper_is_not_matched_to_a_new_empty_one(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
    access: Repository,
    actors: Actors,
) -> None:
    """The case the empty rule has to refuse, and it is the one that would hand out a share.

    `shoot` holds no files of its own (they are in `shoot/inner`), so on the direct contents it
    looks as empty as a folder somebody just made. Deleted on the same pass that an unrelated empty
    folder appears, a rule that only looked at direct contents would call the two one folder and
    move `shoot` on top of it, sharing and all.
    """
    draw(root_path / "shoot" / "inner" / "a.png", "testsrc2=size=64x48:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    shoot = await library_store.folder_at(root.id, "shoot")
    assert shoot is not None
    await access.grant(ObjectType.FOLDER, shoot.id, actors.guest.id, Effect.SHARE)

    (root_path / "shoot" / "inner" / "a.png").unlink()
    (root_path / "shoot" / "inner").rmdir()
    (root_path / "shoot").rmdir()
    (root_path / "somewhere-else").mkdir()
    await _scan(context_for, root, settings, service, reindexer)

    landed = await library_store.folder_at(root.id, "somewhere-else")
    assert landed is not None
    assert landed.id != shoot.id
    assert await access.grants_on(ObjectType.FOLDER, landed.id) == []


# --- the three places this pass declines ---------------------------------------------------------
#
# Every one of them is a `continue`: recognising a folder leans towards NOT recognising it, so what
# these prove is that the pass steps over the case and carries on rather than guessing or failing.


def test_a_recorded_file_that_is_not_on_the_disk_at_all_counts_for_nothing() -> None:
    """The ordinary way a folder stops being recognisable: its files went somewhere else.

    Driven directly because a scan cannot easily be made to compare two sides that share no name,
    and this is the branch that decides "not the same folder", which is the safe direction and so
    the one nothing else would notice going wrong.
    """
    assert moved_folders._overlap({("gone.png", 10)}, {("here.png", 10)}) == 0
    assert moved_folders._overlap({("gone.png", 10), ("here.png", 10)}, {("here.png", 10)}) == 1


def test_a_file_of_the_same_name_and_a_different_size_is_not_the_same_file() -> None:
    """The name alone is not enough, and this is the branch that says so.

    Two folders holding a `cover.jpg` each is ordinary. Counting that as a match would let any two
    folders of stock filenames be recognised as one another, which is the mistake with no way back:
    it attaches one folder's sharing to a different folder.
    """
    assert moved_folders._overlap({("cover.jpg", 10)}, {("cover.jpg", 99)}) == 0


def test_a_file_whose_size_nobody_knows_is_matched_on_its_name() -> None:
    """A picture indexed out of an archive: the walk lists one `gallery.zip` and Sift recorded the
    fifty pictures inside it, so the two agree about the name and cannot agree about the bytes."""
    assert moved_folders._overlap({("gallery.zip", None)}, {("gallery.zip", 4096)}) == 1
    assert moved_folders._overlap({("gallery.zip", 4096)}, {("gallery.zip", None)}) == 1


async def test_a_folder_is_not_recognised_onto_a_path_something_else_already_holds(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """A path that already has a row is never offered as somewhere a folder might have moved TO.

    This is the first of the two guards against attaching one folder's history to another, and it is
    the cheap one: a folder that is still on the disk is not a folder that appeared, so it is never a
    candidate at all. What is left is what would have happened without any of this: the row already
    there keeps its path, and the one whose folder went is removed by the sweep.
    """
    draw(root_path / "holidays" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "trips" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    holidays = await library_store.folder_at(root.id, "holidays")
    trips = await library_store.folder_at(root.id, "trips")
    assert holidays is not None and trips is not None

    # `trips` leaves the disk and `holidays` takes its name, so what the walk finds under "trips" is
    # what Sift recorded under "holidays", while a row for "trips" is still sitting there.
    (root_path / "trips" / "b.png").unlink()
    (root_path / "trips").rmdir()
    (root_path / "holidays").rename(root_path / "trips")
    await _scan(context_for, root, settings, service, reindexer)

    after = await library_store.folder_at(root.id, "trips")
    assert after is not None
    assert after.id == trips.id, "the row already there keeps the path, rather than being replaced"


async def test_settling_steps_over_a_folder_that_has_no_row(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """A directory the walk went through that the library has no row for.

    A scan that indexed a library and then failed on the way out would be re-run, re-walking
    everything, for the sake of a grouping that is a convenience, so a missing row is stepped over
    the same way a listener that refuses to answer is.
    """
    settled: list[str] = []

    async def remember(folder_id: str, _name: str) -> None:
        settled.append(folder_id)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await sweeping._settle_folders(
        context, root_id=root.id, dirs={"never-walked"}, folder_settled=remember
    )

    assert settled == []


async def test_a_folder_is_not_recognised_onto_a_path_another_recognition_just_took(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    library_store: LibraryStore,
) -> None:
    """The second guard, and the one the first cannot cover.

    Recognising a folder into a place that does not exist yet has to make the chain above it first.
    So a folder can be offered a path that was free when the pairing was worked out and is taken by
    the time it gets there, taken by another folder recognised a moment earlier in the same pass.

    Refusing is the only safe answer: the two are not one folder, and going through with it would
    hand somebody the shares, restrictions and concealments of a folder they were never given.
    """
    draw(root_path / "alpha" / "a.png", "testsrc2=size=64x48:rate=1")
    draw(root_path / "beta" / "b.png", "testsrc2=size=48x64:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    alpha = await library_store.folder_at(root.id, "alpha")
    beta = await library_store.folder_at(root.id, "beta")
    assert alpha is not None and beta is not None

    # `beta` becomes `x`, and `alpha` moves inside it as `x/y`. Pairing sees two folders that
    # vanished and two that appeared, and matches each by what is in it. `alpha` is dealt with
    # first, and making `x/y` is what puts a row at `x`, which is where `beta` was going.
    (root_path / "alpha").rename(root_path / "held")
    (root_path / "beta").rename(root_path / "x")
    (root_path / "held").rename(root_path / "x" / "y")
    await _scan(context_for, root, settings, service, reindexer)

    inside = await library_store.folder_at(root.id, "x/y")
    assert inside is not None
    assert inside.id == alpha.id, "the one that got there first is recognised"

    outer = await library_store.folder_at(root.id, "x")
    assert outer is not None
    assert outer.id != beta.id, "and the one that arrived second is a new folder, not that one"


# --- forgetting one, which two features do and neither may own ---------------------------------


async def test_forgetting_a_folder_takes_the_grants_before_the_row(
    library_store: LibraryStore, access: Repository, actors: Actors, root: Root
) -> None:
    """The ORDER between the two stores is the whole of what this function is.

    `acl_grants.object_id` carries no foreign key, so nothing cascades a grant when a folder row
    goes. A grant left behind names an id, and an id is not a promise never to be reused, so the
    next folder given it inherits a share nobody granted. Every folder UNDER it is forgotten the
    same way, because the row cascades and the grants do not.
    """
    top = await library_store.upsert_folder(root.id, "clips")
    under = await library_store.upsert_folder(root.id, "clips/holiday")
    await access.grant(ObjectType.FOLDER, top.id, actors.guest.id, Effect.SHARE)
    await access.grant(ObjectType.FOLDER, under.id, actors.guest.id, Effect.SHARE)

    assert await forget_folder(library_store, access, top.id) is True

    assert await library_store.get_folder(top.id) is None
    assert await library_store.get_folder(under.id) is None, "the row under it cascaded"
    assert await access.grants_on(ObjectType.FOLDER, top.id) == []
    assert await access.grants_on(ObjectType.FOLDER, under.id) == [], (
        "a grant on a folder that has gone is a share waiting for whoever is given that id next"
    )


async def test_forgetting_a_folder_that_has_already_gone_is_not_a_failure(
    library_store: LibraryStore, access: Repository, root: Root
) -> None:
    """An ancestor took it by cascade, which is the ordinary case: the scan forgets a folder and
    everything under it, and the delete feature reaches the same rows from the other end. The
    outcome asked for is the outcome there is."""
    top = await library_store.upsert_folder(root.id, "clips")
    under = await library_store.upsert_folder(root.id, "clips/holiday")
    await forget_folder(library_store, access, top.id)

    assert await forget_folder(library_store, access, under.id) is False
