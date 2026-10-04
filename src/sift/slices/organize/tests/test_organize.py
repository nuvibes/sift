# SPDX-License-Identifier: AGPL-3.0-or-later
"""Renaming and moving a file: what happens on disk, and what the index says afterwards.

Every test here asserts both halves. A rename that moved the bytes and left the index behind, or
updated the index and left the bytes, is the failure this feature exists to avoid, and either one
of those passes a test that only looks at the other.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.content import ContentStore
from sift.kernel.ledger import Actor
from sift.slices.organize import service as organize_service
from sift.slices.organize.service import (
    NotAllowed,
    NotFound,
    Organizer,
    OrganizeRefused,
    check_filename,
)

from .conftest import Library

pytestmark = pytest.mark.anyio


async def test_a_rename_moves_the_file_and_the_index_follows_it(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The claim this whole feature makes, asserted on both sides at once."""
    added = await add_file(managed, "clip.mp4")

    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    assert not (managed.path / "clip.mp4").exists()
    assert (managed.path / "holiday.mp4").exists()
    assert done.filename == "holiday.mp4"

    locations = await content_store.locations(added.asset.id)
    assert [location.rel_path for location in locations] == ["holiday.mp4"]
    # The path Sift would open. If this disagreed with the disk, every screen would be pointing at
    # a file that is not there while the file itself was perfectly fine.
    assert await content_store.path_of(locations[0]) == managed.path / "holiday.mp4"


async def test_a_name_typed_without_an_extension_keeps_the_files_own(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The rename box opens on the whole name, so a name typed over it has no extension. Stored as
    typed, the file lost the ending its type is read from and the folder walk passed it by. It
    keeps its own; an ending that was typed is taken as typed."""
    added = await add_file(managed, "clip.mp4")

    done = await organizer.rename(added.asset.id, new_name="holiday", actor=admin)

    assert done.filename == "holiday.mp4"
    assert (managed.path / "holiday.mp4").exists()
    assert not (managed.path / "holiday").exists()
    locations = await content_store.locations(added.asset.id)
    assert [location.rel_path for location in locations] == ["holiday.mp4"]

    typed = await organizer.rename(added.asset.id, new_name="holiday.mkv", actor=admin)
    assert typed.filename == "holiday.mkv"


async def test_a_renamed_file_is_still_the_same_file(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Identity is the content, so nothing recorded about the file is lost by moving it.

    The asset id and the location id both survive: a rename is the same row at a different address,
    not a delete and a re-import. Anything hanging off either id (a rating, a tag, a person)
    comes with it without this feature knowing those things exist.
    """
    added = await add_file(managed, "clip.mp4")

    await organizer.rename(added.asset.id, new_name="renamed.mp4", actor=admin)

    after = await content_store.get(added.asset.id)
    assert after is not None
    assert after.identity == added.asset.identity
    locations = await content_store.locations(added.asset.id)
    assert [location.id for location in locations] == [added.location.id]


async def test_a_move_puts_the_file_in_another_folder_and_repoints_the_index(
    organizer: Organizer,
    content_store: ContentStore,
    library_store: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    added = await add_file(managed, "clip.mp4")
    (managed.path / "sorted").mkdir()
    destination = await library_store.upsert_folder(managed.root.id, "sorted")

    done = await organizer.move(added.asset.id, folder_id=destination.id, actor=admin)

    assert not (managed.path / "clip.mp4").exists()
    assert (managed.path / "sorted" / "clip.mp4").exists()
    assert done.folder_id == destination.id
    locations = await content_store.locations(added.asset.id)
    assert [location.rel_path for location in locations] == ["sorted/clip.mp4"]


async def test_a_move_into_the_folder_it_is_already_in_is_refused(
    organizer: Organizer, library_store: Any, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """Not carried out and not silently ignored: it is a request that means nothing."""
    added = await add_file(managed, "clip.mp4")
    here = await library_store.root_folder(managed.root.id)

    with pytest.raises(OrganizeRefused, match="already in that folder"):
        await organizer.move(added.asset.id, folder_id=here.id, actor=admin)

    assert (managed.path / "clip.mp4").exists()


async def test_a_move_into_another_library_folder_on_the_same_disk_works(
    organizer: Organizer,
    library_store: Any,
    content_store: Any,
    managed: Library,
    second_managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Several library folders under one drive is the ordinary arrangement, and a move there is a
    rename like any other. Two roots CAN be two disks, so what is asked is whether they are."""
    added = await add_file(managed, "clip.mp4")
    elsewhere = await library_store.root_folder(second_managed.root.id)

    moved = await organizer.move(added.asset.id, folder_id=elsewhere.id, actor=admin)

    assert (second_managed.path / "clip.mp4").is_file()
    assert not (managed.path / "clip.mp4").exists()
    # The row follows the file, into the other root. Left naming the old one, the next sweep of
    # that root would go looking for the file and mark it missing.
    location = next(
        each
        for each in await content_store.locations(added.asset.id)
        if each.id == moved.location_id
    )
    assert (location.root_id, location.rel_path) == (second_managed.root.id, "clip.mp4")


async def test_a_move_to_a_folder_on_a_different_disk_is_refused(
    organizer: Organizer,
    library_store: Any,
    managed: Library,
    second_managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Across two real disks a move is a copy of every byte and a delete. Refused, not half-done."""
    added = await add_file(managed, "clip.mp4")
    elsewhere = await library_store.root_folder(second_managed.root.id)

    # A test cannot conjure a second filesystem, so the question is answered rather than staged.
    monkeypatch.setattr(organize_service, "same_filesystem", lambda one, other: False)

    with pytest.raises(OrganizeRefused, match="different disk"):
        await organizer.move(added.asset.id, folder_id=elsewhere.id, actor=admin)

    assert (managed.path / "clip.mp4").is_file(), "the file moved despite the refusal"


async def test_a_move_to_a_folder_in_another_library_is_refused(
    organizer: Organizer,
    library_store: Any,
    managed: Library,
    read_only: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """A folder Sift was never given read-write is refused, whichever library folder it is in.

    Two roots CAN be two disks, and that refusal is its own test. This one is the other reason a
    move into another library folder can be turned down: the target has to have been marked
    managed, exactly as the source does, because the
    file is arriving there.
    """
    added = await add_file(managed, "clip.mp4")
    elsewhere = await library_store.root_folder(read_only.root.id)

    with pytest.raises(OrganizeRefused, match="read-only"):
        await organizer.move(added.asset.id, folder_id=elsewhere.id, actor=admin)

    assert (managed.path / "clip.mp4").exists()


async def test_a_move_naming_no_folder_at_all_is_told_so(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(OrganizeRefused, match="Say which folder"):
        await organizer.move(added.asset.id, folder_id="", actor=admin)


async def test_a_move_to_a_folder_that_is_not_there_is_a_not_found(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(NotFound):
        await organizer.move(added.asset.id, folder_id="01HX0000000000000000000777", actor=admin)


# --- the read-only refusal ------------------------------------------------------------------


async def test_renaming_in_a_read_only_library_is_refused_at_the_seam(
    organizer: Organizer, read_only: Library, add_file: Any, admin: Viewer
) -> None:
    """A sentence somebody can act on, not an error from the filesystem.

    The file is still there afterwards and still called what it was called, which is the half that
    would be untrue if the refusal happened at the syscall after some of the work was done.
    """
    added = await add_file(read_only, "clip.mp4")

    with pytest.raises(OrganizeRefused, match="read-only"):
        await organizer.rename(added.asset.id, new_name="renamed.mp4", actor=admin)

    assert (read_only.path / "clip.mp4").exists()
    assert not (read_only.path / "renamed.mp4").exists()


async def test_moving_in_a_read_only_library_is_refused_at_the_seam(
    organizer: Organizer, library_store: Any, read_only: Library, add_file: Any, admin: Viewer
) -> None:
    added = await add_file(read_only, "clip.mp4")
    (read_only.path / "sorted").mkdir()
    destination = await library_store.upsert_folder(read_only.root.id, "sorted")

    with pytest.raises(OrganizeRefused, match="read-only"):
        await organizer.move(added.asset.id, folder_id=destination.id, actor=admin)

    assert (read_only.path / "clip.mp4").exists()


async def test_a_file_whose_library_was_removed_is_no_longer_findable(
    organizer: Organizer, library_store: Any, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """Removing a library takes its locations with it, so the file cannot be named any more.

    It reaches the "no such file" answer through the cascade
    (the location rows go with the root), not through a missing-root check further in.
    """
    added = await add_file(managed, "clip.mp4")
    await library_store.delete_root(managed.root.id, actor=Actor.sift("folder"))

    with pytest.raises(NotFound):
        await organizer.rename(added.asset.id, new_name="renamed.mp4", actor=admin)


# --- the collision --------------------------------------------------------------------------


async def test_renaming_onto_a_name_that_is_taken_refuses_and_destroys_nothing(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The reason a rename is as dangerous as a delete, and the reason this is not `os.replace`.

    Both files still exist afterwards with their own contents, and the index still describes both.
    A bare rename would have left one file where there were two and nothing anywhere saying so.
    """
    one = await add_file(managed, "clip.mp4")
    two = await add_file(managed, "other.jpg", source="accepted.jpg")
    occupied = (managed.path / "other.jpg").read_bytes()

    with pytest.raises(OrganizeRefused, match="already something called"):
        await organizer.rename(one.asset.id, new_name="other.jpg", actor=admin)

    assert (managed.path / "clip.mp4").exists()
    assert (managed.path / "other.jpg").read_bytes() == occupied
    assert await content_store.get(two.asset.id) is not None


async def test_moving_onto_a_taken_name_in_the_destination_is_refused(
    organizer: Organizer, library_store: Any, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """And the file in the way is one Sift has never indexed, which is the sharper case.

    The index cannot warn about a file it does not know about, so a check against the database
    would wave this through and the move would destroy somebody's file. The refusal has to come
    from the filesystem being asked to claim the name, not from a query.
    """
    added = await add_file(managed, "clip.mp4")
    (managed.path / "sorted").mkdir()
    (managed.path / "sorted" / "clip.mp4").write_bytes(b"a file Sift has never seen")
    destination = await library_store.upsert_folder(managed.root.id, "sorted")
    occupied = (managed.path / "sorted" / "clip.mp4").read_bytes()

    with pytest.raises(OrganizeRefused, match="already something called"):
        await organizer.move(added.asset.id, folder_id=destination.id, actor=admin)

    assert (managed.path / "sorted" / "clip.mp4").read_bytes() == occupied


# --- confinement ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../escape.mp4",
        "..\\escape.mp4",
        "sub/escape.mp4",
        "/etc/passwd",
        "C:clip.mp4",
        "..",
        ".",
        "",
        "   ",
    ],
)
async def test_a_name_that_is_really_a_path_is_refused(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer, name: str
) -> None:
    """Every shape of "this is not a name", refused before anything is touched.

    Both separators, on every platform: which one is "the" separator is a property of the machine
    parsing the string, and the string came from a text box.
    """
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(OrganizeRefused):
        await organizer.rename(added.asset.id, new_name=name, actor=admin)

    assert (managed.path / "clip.mp4").exists()


async def test_a_name_the_index_could_not_store_never_reaches_the_disk(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The disk and the index must not be able to disagree, and this is the way they nearly could.

    A drive-qualified name is an ordinary filename on Linux (a colon is a legal character), so a
    check that only looks for separators lets it through. The column that stores the path does not:
    joined on Windows a drive replaces the root instead of extending it, so it is refused there.

    Two checks that disagree is the whole problem. If the name is accepted here and refused when the
    row is written, the file has already moved and the index still describes where it was,
    which is what every screen would then be pointing at.
    """
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(OrganizeRefused):
        await organizer.rename(added.asset.id, new_name="C:clip.mp4", actor=admin)

    # The file did not move, and nothing else was created beside it.
    #
    # Asserted as a LISTING, not as a path join. `Path(library) / "C:clip.mp4"` on Windows is
    # `library\clip.mp4` (a drive-relative name keeps the base drive), so the obvious second
    # assertion re-checks the first one's path and can never fail: `assert not ... .exists()`
    # against it reads as a real check and is one only on Linux.
    assert sorted(one.name for one in managed.path.iterdir()) == ["clip.mp4"]
    locations = await content_store.locations(added.asset.id)
    assert [location.rel_path for location in locations] == ["clip.mp4"]
    assert await content_store.path_of(locations[0]) == managed.path / "clip.mp4"


async def test_a_name_that_is_too_long_is_refused(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(OrganizeRefused, match="too long"):
        await organizer.rename(added.asset.id, new_name="a" * 5000, actor=admin)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Creating a symbolic link needs a privilege an ordinary Windows account does not hold "
        "(WinError 1314). The same escape by the mechanism a Windows user actually has (a "
        "junction, which needs no privilege) is proved by the test below."
    ),
)
async def test_a_symlink_out_of_the_library_does_not_become_a_destination(
    organizer: Organizer,
    library_store: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    tmp_path: Path,
) -> None:
    """A `..` is not the only way out of a folder, and this is the one a string check cannot see.

    The destination folder is a symlink pointing somewhere else on the machine. Lexically the path
    is a perfectly ordinary one inside the root; resolved, it is not inside the root at all.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (managed.path / "escape").symlink_to(outside)
    added = await add_file(managed, "clip.mp4")
    destination = await library_store.upsert_folder(managed.root.id, "escape")

    with pytest.raises(OrganizeRefused):
        await organizer.move(added.asset.id, folder_id=destination.id, actor=admin)

    assert not (outside / "clip.mp4").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows reparse point")
async def test_a_junction_out_of_the_library_does_not_become_a_destination(
    organizer: Organizer,
    library_store: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    tmp_path: Path,
) -> None:
    """The same escape, by the mechanism a Windows user actually has.

    `mklink /J` needs no privilege, so anybody can drop one inside a library, and unlike a symbolic
    link it reports `is_symlink() == False`, so a guard that notices links never fires. The refusal
    has to come from resolving the path and finding it outside the root, which is the half worth
    proving on the platform Sift ships on.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(managed.path / "escape"), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    added = await add_file(managed, "clip.mp4")
    destination = await library_store.upsert_folder(managed.root.id, "escape")

    with pytest.raises(OrganizeRefused):
        await organizer.move(added.asset.id, folder_id=destination.id, actor=admin)

    assert not (outside / "clip.mp4").exists()
    assert (managed.path / "clip.mp4").exists()


def test_the_name_check_refuses_a_null_byte() -> None:
    """Called directly, because a null byte cannot survive the round trip through a request."""
    with pytest.raises(OrganizeRefused, match="line breaks"):
        check_filename("clip\x00.mp4")

    with pytest.raises(OrganizeRefused, match="line breaks"):
        check_filename("clip\n.mp4")


def test_the_name_check_refuses_a_drive_qualified_name_in_its_own_words() -> None:
    """Asserted at this layer as well as through the operation.

    Two guards refuse this: the name check here, and the check on the stored path further in. The
    operation is safe if either holds, which is the point of having both, and it also means a
    test that only goes through the operation cannot tell whether this one still works.
    """
    with pytest.raises(OrganizeRefused, match="not a file name Sift can store"):
        check_filename("C:clip.mp4")


def test_the_name_check_trims_and_keeps_the_rest() -> None:
    assert check_filename("  holiday.mp4  ") == "holiday.mp4"


# --- who may do it --------------------------------------------------------------------------


async def test_a_guest_cannot_rename_what_they_can_see(
    organizer: Organizer,
    access: Repository,
    managed: Library,
    add_file: Any,
    guest: Viewer,
) -> None:
    """Refused for being a guest, and the file is untouched afterwards."""
    added = await add_file(managed, "clip.mp4")
    await access.grant(ObjectType.ROOT, managed.root.id, guest.id, Effect.SHARE)

    with pytest.raises(NotAllowed):
        await organizer.rename(added.asset.id, new_name="renamed.mp4", actor=guest)

    assert (managed.path / "clip.mp4").exists()


async def test_a_guest_renaming_what_they_cannot_see_is_told_it_is_not_there(
    organizer: Organizer, managed: Library, add_file: Any, guest: Viewer
) -> None:
    """Not "admins only", which would confirm the file exists.

    The order of the two checks is the whole of this: visibility is settled before permission, so
    somebody who cannot see a file learns nothing by asking to rename it.
    """
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(NotFound):
        await organizer.rename(added.asset.id, new_name="renamed.mp4", actor=guest)


async def test_a_guest_cannot_move_what_they_can_see(
    organizer: Organizer,
    access: Repository,
    library_store: Any,
    managed: Library,
    add_file: Any,
    guest: Viewer,
) -> None:
    added = await add_file(managed, "clip.mp4")
    (managed.path / "sorted").mkdir()
    destination = await library_store.upsert_folder(managed.root.id, "sorted")
    await access.grant(ObjectType.ROOT, managed.root.id, guest.id, Effect.SHARE)

    with pytest.raises(NotAllowed):
        await organizer.move(added.asset.id, folder_id=destination.id, actor=guest)

    assert (managed.path / "clip.mp4").exists()


async def test_an_asset_that_does_not_exist_is_a_not_found(
    organizer: Organizer, admin: Viewer
) -> None:
    with pytest.raises(NotFound):
        await organizer.rename("01HX0000000000000000000999", new_name="renamed.mp4", actor=admin)


# --- more than one copy ---------------------------------------------------------------------


async def test_a_file_in_two_folders_will_not_be_renamed_by_guesswork(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The same bytes in two places. Which copy is meant is not something to assume."""
    added = await add_file(managed, "clip.mp4")
    await add_file(managed, "copy/clip.mp4")

    with pytest.raises(OrganizeRefused, match="more than one folder"):
        await organizer.rename(added.asset.id, new_name="renamed.mp4", actor=admin)

    assert len(await content_store.locations(added.asset.id)) == 2


async def test_naming_the_copy_renames_that_one_and_leaves_the_other(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    added = await add_file(managed, "clip.mp4")
    await add_file(managed, "copy/clip.mp4")

    await organizer.rename(
        added.asset.id, new_name="renamed.mp4", actor=admin, location_id=added.location.id
    )

    assert (managed.path / "renamed.mp4").exists()
    assert (managed.path / "copy" / "clip.mp4").exists()
    paths = sorted(location.rel_path for location in await content_store.locations(added.asset.id))
    assert paths == ["copy/clip.mp4", "renamed.mp4"]


async def test_naming_a_copy_that_is_not_this_asset_is_a_not_found(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(NotFound):
        await organizer.rename(
            added.asset.id,
            new_name="renamed.mp4",
            actor=admin,
            location_id="01HX0000000000000000000888",
        )
