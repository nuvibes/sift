# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making, renaming and moving a folder, and telling Sift a library folder has moved.

Each is a write into somebody's library through the one door that decides whether Sift may change
files there, so most tests are about refusals. A rearranged folder keeps its id, and every write
announces itself to the other browsers.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sift.kernel import changes
from sift.kernel.access import Effect, ObjectType, Repository
from sift.kernel.changes import About, ChangeBus
from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore, Root
from sift.kernel.ledger import Actor
from sift.kernel.library_write import LibraryWriteRefused, create_directory, move_directory
from sift.slices.library_roots import jobs
from sift.slices.library_roots.service import LibraryService
from sift.testing.fixtures import Actors

from .conftest import RecordingReindexer, draw
from .test_jobs import Context


@pytest.fixture
async def managed_root(library_store: LibraryStore, tmp_path: Path) -> Root:
    """A library Sift was handed read-write, which is what any of this needs."""
    directory = tmp_path / "managed"
    directory.mkdir()
    return await library_store.create_root(name="Managed", abs_path=directory)


async def _scan(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


# --- making one ----------------------------------------------------------------------------------


async def test_a_folder_is_made_on_the_disk_and_in_the_tree(
    managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """Both, in that order, and the row is written straight away.

    A folder somebody made in order to download into it is no use to them at the next walk of their
    library, which is when a row would otherwise appear.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None

    made = await service.create_folder(parent_id=top.id, name="Holidays")

    assert (Path(managed_root.abs_path) / "Holidays").is_dir()
    assert made.rel_path == "Holidays"
    assert await library_store.folder_at(managed_root.id, "Holidays") is not None


async def test_a_folder_is_refused_in_a_library_the_filesystem_will_not_let_sift_write_in(
    root: Root,
    service: LibraryService,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filesystem's answer is the whole guard, asked before anything is made."""
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)
    top = await library_store.root_folder(root.id)
    assert top is not None

    with pytest.raises(LibraryWriteRefused):
        await service.create_folder(parent_id=top.id, name="Holidays")

    assert not (Path(root.abs_path) / "Holidays").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "sub/folder",
        "sub\\folder",
        "..",
        "   ",
        "CON",
        "trailing.",
    ],
)
async def test_a_name_that_is_not_a_name_is_refused(
    name: str, managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """A text box is what stands in front of the rest of the disk.

    A separator makes it a path rather than a name and `..` walks out of the folder the permission
    was resolved against. The last two are Windows: a reserved name cannot be created at all, and a
    trailing dot is silently dropped, so what gets made is not what was asked for, and the row
    Sift writes then names something that is not there.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None

    with pytest.raises(LibraryWriteRefused):
        await service.create_folder(parent_id=top.id, name=name)


async def test_a_name_already_taken_is_refused_rather_than_handed_back(
    managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """Making a folder that is already there is not the operation somebody asked for, and answering
    as though it worked is how two people end up believing they each made it."""
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    await service.create_folder(parent_id=top.id, name="Holidays")

    # The sentence, not the operating system's "File exists".
    with pytest.raises(LibraryWriteRefused, match="already something called"):
        await service.create_folder(parent_id=top.id, name="Holidays")


# --- rearranging one -----------------------------------------------------------------------------


async def test_renaming_a_folder_keeps_it_the_same_folder(
    managed_root: Root,
    service: LibraryService,
    library_store: LibraryStore,
    access: Repository,
    actors: Actors,
) -> None:
    """The point of doing it here rather than in a file manager: nothing has to be recognised."""
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    made = await service.create_folder(parent_id=top.id, name="Holidays")
    await access.grant(ObjectType.FOLDER, made.id, actors.guest.id, Effect.SHARE)

    moved = await service.move_folder(
        folder_id=made.id, parent_id=None, name="Trips", actor=Actor.sift("folder")
    )

    assert moved.id == made.id
    assert moved.rel_path == "Trips"
    assert (Path(managed_root.abs_path) / "Trips").is_dir()
    assert not (Path(managed_root.abs_path) / "Holidays").exists()
    kept = await access.grants_on(ObjectType.FOLDER, moved.id)
    assert [one.subject_user_id for one in kept] == [actors.guest.id]


async def test_moving_a_folder_takes_its_files_and_its_children(
    managed_root: Root,
    service: LibraryService,
    library_store: LibraryStore,
    context_for: Context,
    settings: Settings,
    reindexer: RecordingReindexer,
) -> None:
    """One act on the disk and one rewrite of the rows, for everything underneath."""
    draw(Path(managed_root.abs_path) / "shoot" / "inner" / "a.png", "testsrc2=size=64x48:rate=1")
    await _scan(context_for, managed_root, settings, service, reindexer)
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    holder = await service.create_folder(parent_id=top.id, name="Archive")
    shoot = await library_store.folder_at(managed_root.id, "shoot")
    assert shoot is not None

    await service.move_folder(
        folder_id=shoot.id, parent_id=holder.id, name=None, actor=Actor.sift("folder")
    )

    inner = await library_store.folder_at(managed_root.id, "Archive/shoot/inner")
    assert inner is not None
    assert (Path(managed_root.abs_path) / "Archive" / "shoot" / "inner" / "a.png").is_file()
    located = await library_store.locations_in_folder(inner.id)
    assert [one.rel_path for one in located] == ["Archive/shoot/inner/a.png"]


async def test_a_folder_cannot_be_moved_inside_itself(
    managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """The filesystem either refuses this confusingly or, on some of them, makes a tree with no
    bottom. Refused here, in a sentence."""
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    outer = await service.create_folder(parent_id=top.id, name="Outer")
    inner = await service.create_folder(parent_id=outer.id, name="Inner")

    with pytest.raises(LibraryWriteRefused, match="inside itself"):
        await service.move_folder(
            folder_id=outer.id, parent_id=inner.id, name=None, actor=Actor.sift("folder")
        )


async def test_a_folder_cannot_be_moved_into_another_library(
    managed_root: Root,
    root: Root,
    service: LibraryService,
    library_store: LibraryStore,
) -> None:
    """Two libraries can have different permissions, so a folder crossing between them is a folder
    whose files change what may be seen of them. That is a different operation and not this one."""
    top = await library_store.root_folder(managed_root.id)
    other = await library_store.root_folder(root.id)
    assert top is not None and other is not None
    made = await service.create_folder(parent_id=top.id, name="Holidays")

    with pytest.raises(LibraryWriteRefused):
        await service.move_folder(
            folder_id=made.id, parent_id=other.id, name=None, actor=Actor.sift("folder")
        )


async def test_the_library_folder_itself_is_not_moved_this_way(
    managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """It is not a folder inside a library, it IS the library, and moving it is the operation with
    every check adding one has."""
    top = await library_store.root_folder(managed_root.id)
    assert top is not None

    with pytest.raises(LibraryWriteRefused, match="library folder itself"):
        await service.move_folder(
            folder_id=top.id, parent_id=None, name="Something", actor=Actor.sift("folder")
        )


# --- a library folder that moved -----------------------------------------------------------------


async def test_a_library_told_where_it_moved_keeps_everything_under_it(
    managed_root: Root,
    service: LibraryService,
    library_store: LibraryStore,
    context_for: Context,
    settings: Settings,
    reindexer: RecordingReindexer,
    access: Repository,
    actors: Actors,
    tmp_path: Path,
) -> None:
    """The one case a walk cannot recognise, because Sift is not looking anywhere near the new name.

    Nothing under it is re-read: every file is recorded relative to its library folder, so one
    stored path changes and the whole library is correct again. Removing it and adding it back
    recovers the files by their digests and drops every grant on every folder, deliberately.
    """
    draw(Path(managed_root.abs_path) / "shoot" / "a.png", "testsrc2=size=64x48:rate=1")
    await _scan(context_for, managed_root, settings, service, reindexer)
    shoot = await library_store.folder_at(managed_root.id, "shoot")
    assert shoot is not None
    await access.grant(ObjectType.FOLDER, shoot.id, actors.guest.id, Effect.SHARE)

    moved_to = tmp_path / "somewhere-else"
    Path(managed_root.abs_path).rename(moved_to)
    repointed = await library_store.repoint_root(managed_root.id, moved_to)

    assert repointed is not None
    assert repointed.abs_path == str(moved_to)
    assert repointed.name == "somewhere-else"
    still = await library_store.folder_at(managed_root.id, "shoot")
    assert still is not None
    assert still.id == shoot.id
    kept = await access.grants_on(ObjectType.FOLDER, still.id)
    assert [one.subject_user_id for one in kept] == [actors.guest.id]


async def test_a_library_cannot_be_moved_on_top_of_another_one(
    managed_root: Root, root: Root, library_store: LibraryStore
) -> None:
    """Every refusal adding a library has, again. Otherwise this is a second way in that skips
    them all, and two libraries holding one folder have two answers to who may see it."""
    from sift.kernel.content import RootOverlap

    with pytest.raises(RootOverlap):
        await library_store.repoint_root(managed_root.id, Path(root.abs_path))


async def test_a_library_can_be_told_it_moved_even_while_its_old_path_is_gone(
    managed_root: Root, library_store: LibraryStore, tmp_path: Path
) -> None:
    """The overlap check skips this library's own row, and that is the case it is for.

    A library being told where it now is will almost always still be recorded at a path that has
    stopped existing. Compared against itself, the operation would be impossible exactly when it is
    needed.
    """
    moved_to = tmp_path / "elsewhere"
    Path(managed_root.abs_path).rename(moved_to)

    repointed = await library_store.repoint_root(managed_root.id, moved_to)

    assert repointed is not None
    assert repointed.abs_path == str(moved_to)


async def test_rearranging_is_refused_in_a_library_the_filesystem_will_not_let_sift_write_in(
    root: Root,
    service: LibraryService,
    library_store: LibraryStore,
    context_for: Context,
    settings: Settings,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving a folder asks the filesystem the same question making one does, per folder."""
    draw(Path(root.abs_path) / "shoot" / "a.png", "testsrc2=size=64x48:rate=1")
    await _scan(context_for, root, settings, service, reindexer)
    shoot = await library_store.folder_at(root.id, "shoot")
    assert shoot is not None
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)

    with pytest.raises(LibraryWriteRefused, match="not allowed to write"):
        await service.move_folder(
            folder_id=shoot.id, parent_id=None, name="Trips", actor=Actor.sift("folder")
        )

    assert (Path(root.abs_path) / "shoot").is_dir()


async def test_a_library_told_it_moved_to_where_it_already_is_is_not_refused(
    managed_root: Root, library_store: LibraryStore
) -> None:
    """Somebody pressing "it moved" and picking the folder it is already in has said something true.

    The overlap rule compares this library against every other one; compared against itself it would
    always match, and the answer would be a sentence saying the folder is already part of the
    library somebody is looking at.
    """
    repointed = await library_store.repoint_root(managed_root.id, Path(managed_root.abs_path))

    assert repointed is not None
    assert repointed.abs_path == managed_root.abs_path


@pytest.mark.parametrize("name", ["sub/folder", "..", "CON", "trailing.", "   "])
async def test_a_rename_to_something_that_is_not_a_name_is_refused_as_a_sentence(
    name: str, managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """The same rule making one applies to renaming one, and it has to arrive the same way.

    The check is the kernel's and raises the kernel's own refusal; left unconverted it would reach
    the route as something it does not catch, so a name with a slash in it would come back as a 500
    and the dialog somebody typed it in would have nothing to draw. What they must get is the
    sentence.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    made = await service.create_folder(parent_id=top.id, name="Holidays")

    with pytest.raises(LibraryWriteRefused):
        await service.move_folder(
            folder_id=made.id, parent_id=None, name=name, actor=Actor.sift("folder")
        )

    assert (Path(managed_root.abs_path) / "Holidays").is_dir()


# --- and the windows that did not do it -----------------------------------------------------------


@pytest.fixture
def told() -> Iterator[ChangeBus]:
    """A bus of this test's own, so what a write announced can be read back.

    Cleared afterwards rather than left. The listener is one per process, set by the application at
    start-up, and a bus left behind here would go on collecting what every later test announced.
    """
    bus = ChangeBus()
    changes.listens(bus)
    yield bus
    changes.listens(None)


#: Any id at all. What these writes reach is every admin, as a reach settled at the moment of
#: sending, so what has to exist for one to arrive is an open connection rather than a named user.
ANOTHER_WINDOW = "01HX00000000000000000WIN1"


async def test_making_a_folder_tells_a_window_that_did_not_make_it(
    managed_root: Root, service: LibraryService, library_store: LibraryStore, told: ChangeBus
) -> None:
    """The browser that did the writing re-reads by itself. Every other one is told, or it is wrong.

    Without this the second browser shows the list from before the folder existed until something
    else happens to make it read again, and there is no symptom: the write is correct, the row is
    right, and the screen is simply old.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    waiting = told.subscribe(ANOTHER_WINDOW)

    await service.create_folder(parent_id=top.id, name="Holidays")

    assert waiting.take(as_admin=True).about == (About.LIBRARY,)


async def test_rearranging_a_folder_tells_them_too(
    managed_root: Root, service: LibraryService, library_store: LibraryStore, told: ChangeBus
) -> None:
    """Subscribed after the folder is made, so what is read back is the move and nothing else."""
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    made = await service.create_folder(parent_id=top.id, name="Holidays")
    waiting = told.subscribe(ANOTHER_WINDOW)

    await service.move_folder(
        folder_id=made.id, parent_id=None, name="Trips", actor=Actor.sift("folder")
    )

    # The folder tree on the library bell, and the files under it on the arrivals bell.
    assert set(waiting.take(as_admin=True).about) == {About.ARRIVALS, About.LIBRARY}


async def test_adding_a_library_tells_them_too(
    service: LibraryService, tmp_path: Path, told: ChangeBus
) -> None:
    """The largest of the three: a library appearing is a row on every admin's library screen."""
    directory = tmp_path / "another"
    directory.mkdir()
    waiting = told.subscribe(ANOTHER_WINDOW)

    await service.add_root(abs_path=directory)

    assert waiting.take(as_admin=True).about == (About.LIBRARY,)


async def test_telling_sift_a_library_moved_tells_them_too(
    managed_root: Root, service: LibraryService, tmp_path: Path, told: ChangeBus
) -> None:
    """Every admin's library list shows where a folder is. Re-pointed without a word, every screen
    but the one that did it would go on showing the old place, and a root that does not exist
    moves nothing and says nothing."""
    moved = tmp_path / "moved"
    Path(managed_root.abs_path).rename(moved)
    waiting = told.subscribe(ANOTHER_WINDOW)

    assert await service.repoint_root(GONE, moved) is None
    assert waiting.take(as_admin=True).about == ()

    root = await service.repoint_root(managed_root.id, moved)

    assert root is not None and Path(root.abs_path) == moved.resolve()
    assert waiting.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_window_that_is_not_an_admins_is_told_none_of_it(
    managed_root: Root, service: LibraryService, library_store: LibraryStore, told: ChangeBus
) -> None:
    """These are admin-only decisions about somebody's library, and the reach has to match.

    The role is applied when the message is sent rather than when it is published, so this is the
    same subscription as the test above reading itself back as what it is. A guest told to re-read
    a screen it may not open would be a request that can only be refused.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    waiting = told.subscribe(ANOTHER_WINDOW)

    await service.create_folder(parent_id=top.id, name="Holidays")

    assert waiting.take(as_admin=False).about == ()


# --- the refusals, and the two that are not refusals at all ---------------------------------------
#
# Each refusal is a sentence somebody reads, never the operating system's own words.

GONE = "01HX000000000000000000GONE"


async def test_making_a_folder_under_one_that_has_gone_is_refused(
    managed_root: Root, service: LibraryService
) -> None:
    """Well-formed and not there. Somebody else removed it while this screen was open."""
    with pytest.raises(LibraryWriteRefused, match="isn't there any more"):
        await service.create_folder(parent_id=GONE, name="Holidays")


async def test_rearranging_a_folder_that_has_gone_is_refused(
    managed_root: Root, service: LibraryService
) -> None:
    with pytest.raises(LibraryWriteRefused, match="isn't there any more"):
        await service.move_folder(
            folder_id=GONE, parent_id=None, name="Anything", actor=Actor.sift("folder")
        )


async def test_renaming_a_folder_to_the_name_it_already_has_does_nothing_and_is_not_an_error(
    managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """Somebody opening the rename box and pressing Rename without typing.

    Refusing would be a sentence about a mistake nobody made, and going through with it would ask
    the filesystem to move a folder on top of itself. The row comes back unchanged.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    made = await service.create_folder(parent_id=top.id, name="Holidays")

    same = await service.move_folder(
        folder_id=made.id, parent_id=None, name="Holidays", actor=Actor.sift("folder")
    )

    assert same.id == made.id
    assert same.rel_path == "Holidays"
    assert (Path(managed_root.abs_path) / "Holidays").is_dir()


async def test_a_folder_moved_on_the_disk_but_not_in_the_rows_says_so(
    managed_root: Root,
    service: LibraryService,
    library_store: LibraryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half-done case, which is the one worth a sentence of its own.

    The disk is changed first, so a failure to record it leaves a folder that really has moved and a
    library that still says otherwise, which a rescan fixes and nothing else will. Saying "could
    not move it" there would be false, and saying nothing would leave somebody looking at a tree
    that is wrong with no reason to doubt it.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    made = await service.create_folder(parent_id=top.id, name="Holidays")

    async def records_nothing(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(library_store, "move_folder", records_nothing)

    with pytest.raises(LibraryWriteRefused, match="Rescan this library"):
        await service.move_folder(
            folder_id=made.id, parent_id=None, name="Trips", actor=Actor.sift("folder")
        )


async def test_removing_a_folder_that_is_already_gone_is_not_a_failure(
    managed_root: Root, service: LibraryService
) -> None:
    """An ancestor was removed first and took this one with it by cascade. The outcome asked for is
    the outcome there is, so the answer says nothing happened rather than that something failed."""
    assert await service.remove_folder(root_id=managed_root.id, rel_path="never/existed") is False


async def test_removing_a_folder_forgets_what_was_granted_on_everything_inside_it(
    managed_root: Root, service: LibraryService, library_store: LibraryStore
) -> None:
    """A grant names an id, and an id is not a promise never to be reused.

    Left behind, a grant on a folder inside the one being removed is a permission waiting for
    whatever gets that id next, so the tree is walked and each one dropped, rather than relying on
    a foreign key that does not reach these rows.
    """
    top = await library_store.root_folder(managed_root.id)
    assert top is not None
    outer = await service.create_folder(parent_id=top.id, name="Holidays")
    inner = await service.create_folder(parent_id=outer.id, name="2024")

    assert await service.remove_folder(root_id=managed_root.id, rel_path="Holidays") is True
    assert await library_store.get_folder(inner.id) is None
    assert await library_store.get_folder(outer.id) is None


# --- the write door's own refusals ----------------------------------------------------------------
#
#
# Driven directly: the service settles a same-name rename before the filesystem is asked.


async def test_moving_a_folder_to_where_it_already_is_asks_the_filesystem_for_nothing(
    managed_root: Root,
) -> None:
    """Renaming a directory onto itself is not something to hand to a filesystem: some refuse it,
    and the ones that do not have no work to do either."""
    here = Path(managed_root.abs_path) / "Holidays"
    here.mkdir()

    await move_directory(here, here)

    assert here.is_dir()


async def test_moving_a_folder_onto_something_already_there_is_refused_by_name(
    managed_root: Root,
) -> None:
    """The check is for the SENTENCE. `rename` refuses rather than overwrites, so nothing is lost
    either way, but its refusal does not say what is already sitting there."""
    base = Path(managed_root.abs_path)
    (base / "Holidays").mkdir()
    (base / "Trips").mkdir()

    with pytest.raises(LibraryWriteRefused, match='already something called "Trips"'):
        await move_directory(base / "Holidays", base / "Trips")


async def test_a_folder_the_filesystem_will_not_make_is_reported_in_words(
    managed_root: Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk, a name the filesystem will not take, a folder that went read-only in between.
    Whatever it was, the person asking gets the reason rather than a traceback."""

    def refuses(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "mkdir", refuses)

    with pytest.raises(LibraryWriteRefused, match="No space left on device"):
        await create_directory(Path(managed_root.abs_path), "Holidays")


async def test_a_folder_the_filesystem_will_not_move_is_reported_in_words(
    managed_root: Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same for the other verb, and it is the likelier of the two: a folder somebody has open
    in another window cannot be renamed on Windows at all."""
    base = Path(managed_root.abs_path)
    (base / "Holidays").mkdir()

    def refuses(*_args: object, **_kwargs: object) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "rename", refuses)

    with pytest.raises(LibraryWriteRefused, match="Permission denied"):
        await move_directory(base / "Holidays", base / "Trips")
