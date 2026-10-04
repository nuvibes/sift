# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a rename or a move back.

Undo is the reason every operation is written down before it is done. A rename leaves nothing on
disk that remembers what the file was called, so without the record there is no undo that
could exist, and the record is only worth having if it survives the things that happen to a
library afterwards.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.slices.organize.service import NotAllowed, NotFound, Organizer, OrganizeRefused
from sift.testing.fixtures import FakeClock

from .conftest import Library

pytestmark = pytest.mark.anyio


async def test_undo_puts_a_renamed_file_back(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    await organizer.undo(done.move_id, actor=admin)

    assert (managed.path / "clip.mp4").exists()
    assert not (managed.path / "holiday.mp4").exists()
    locations = await content_store.locations(added.asset.id)
    assert [location.rel_path for location in locations] == ["clip.mp4"]


async def test_undo_puts_a_moved_file_back_in_its_folder(
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

    await organizer.undo(done.move_id, actor=admin)

    assert (managed.path / "clip.mp4").exists()
    assert not (managed.path / "sorted" / "clip.mp4").exists()
    locations = await content_store.locations(added.asset.id)
    assert locations[0].rel_path == "clip.mp4"
    assert locations[0].folder_id == added.location.folder_id


async def test_undoing_twice_is_refused(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """An entry that has been taken back is still a record of something that happened."""
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)
    await organizer.undo(done.move_id, actor=admin)

    with pytest.raises(OrganizeRefused, match="already been undone"):
        await organizer.undo(done.move_id, actor=admin)


async def test_undo_will_not_overwrite_something_that_took_the_old_name(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """The one that matters. An undo that forced its way back would delete a file, silently.

    Somebody renames a file, then puts something else at the old name, then presses undo. Forcing
    the file home would destroy what is there: a deletion carried out by the button whose entire
    job is to prevent one.
    """
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)
    (managed.path / "clip.mp4").write_bytes(b"something else entirely")

    with pytest.raises(OrganizeRefused, match="already something called"):
        await organizer.undo(done.move_id, actor=admin)

    assert (managed.path / "clip.mp4").read_bytes() == b"something else entirely"
    assert (managed.path / "holiday.mp4").exists()


async def test_a_refused_undo_can_still_be_undone_later(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """The entry is marked only once the file is really back.

    Marking it first would leave a record claiming to have been undone by an operation that then
    refused, and no way to try again once the obstruction was cleared.
    """
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)
    (managed.path / "clip.mp4").write_bytes(b"in the way")

    with pytest.raises(OrganizeRefused):
        await organizer.undo(done.move_id, actor=admin)

    (managed.path / "clip.mp4").unlink()
    await organizer.undo(done.move_id, actor=admin)

    assert (managed.path / "clip.mp4").exists()


async def test_undo_is_refused_in_a_library_that_is_no_longer_writable(
    organizer: Organizer,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A library writable when the rename happened and read-only by the time undo is pressed."""
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)

    with pytest.raises(OrganizeRefused, match="not allowed to write"):
        await organizer.undo(done.move_id, actor=admin)

    assert (managed.path / "holiday.mp4").exists()
    assert not (managed.path / "clip.mp4").exists(), "and nothing was put back"


async def test_undoing_an_older_move_out_of_order_is_refused(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The one that quietly loses a name if it is allowed.

    Renamed twice: a -> b -> c. Undoing the FIRST move would send the file to `a`, and undoing the
    second would then send it to `b` (a name it only ever held in passing), with both records
    marked undone and nothing left to take back. The name it started with becomes unreachable while
    every step reports success. So an undo has to be the reverse of the last thing that happened.
    """
    added = await add_file(managed, "a.mp4")
    first = await organizer.rename(added.asset.id, new_name="b.mp4", actor=admin)
    clock_free = await organizer.rename(added.asset.id, new_name="c.mp4", actor=admin)

    with pytest.raises(OrganizeRefused, match="moved since"):
        await organizer.undo(first.move_id, actor=admin)

    # Untouched, and the newer move is still there to be taken back properly.
    assert (managed.path / "c.mp4").exists()
    assert await organizer.last_move(added.asset.id) == clock_free.move_id

    # In the right order it all comes back, including the name it started with.
    await organizer.undo(clock_free.move_id, actor=admin)
    await organizer.undo(first.move_id, actor=admin)

    assert (managed.path / "a.mp4").exists()
    assert not (managed.path / "b.mp4").exists()
    assert not (managed.path / "c.mp4").exists()
    location = await content_store.location(added.location.id)
    assert location is not None
    assert location.rel_path == "a.mp4"


async def test_renaming_a_file_to_the_name_it_already_has_says_so(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """Its own answer, not a collision with itself reported as a name being taken."""
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(OrganizeRefused, match="already called that"):
        await organizer.rename(added.asset.id, new_name="clip.mp4", actor=admin)

    assert (managed.path / "clip.mp4").exists()


async def test_a_guest_cannot_undo(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer, guest: Viewer
) -> None:
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    with pytest.raises(NotAllowed):
        await organizer.undo(done.move_id, actor=guest)

    assert (managed.path / "holiday.mp4").exists()


async def test_undoing_something_that_was_never_recorded_is_a_not_found(
    organizer: Organizer, admin: Viewer
) -> None:
    with pytest.raises(NotFound):
        await organizer.undo("01HX0000000000000000000555", actor=admin)


async def test_undoing_a_move_of_a_file_that_has_since_gone_is_refused(
    organizer: Organizer,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The record outlives the file, deliberately, so this is an ordinary refusal not a crash."""
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)
    await content_store.remove_location(added.location.id)

    with pytest.raises(OrganizeRefused, match="no longer in your library"):
        await organizer.undo(done.move_id, actor=admin)


async def test_the_record_outlives_the_account_that_made_it(
    organizer: Organizer,
    temp_db: Database,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Removing a user must not take their history, or hold the user hostage to it.

    A plain reference would refuse to delete the user at all; a cascade would delete the record.
    Both are wrong, and this asserts the third thing: the entry stays, the attribution goes.
    """
    added = await add_file(managed, "clip.mp4")
    done = await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    await temp_db.execute("DELETE FROM users WHERE id = ?", (admin.id,))

    row = await temp_db.fetch_one("SELECT * FROM file_moves WHERE id = ?", (done.move_id,))
    assert row is not None
    assert row["moved_by"] is None


async def test_the_latest_move_is_what_undo_would_take_back(
    organizer: Organizer, clock: FakeClock, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """Recency decides, and the clock moves between the two so it is recency being tested.

    With both moves recorded at the same instant the ordering falls to the tiebreak on the id, and
    the claim in the name would hold whether or not the time was consulted at all.
    """
    added = await add_file(managed, "clip.mp4")
    first = await organizer.rename(added.asset.id, new_name="one.mp4", actor=admin)
    clock.advance(60)
    second = await organizer.rename(added.asset.id, new_name="two.mp4", actor=admin)

    assert await organizer.last_move(added.asset.id) == second.move_id

    await organizer.undo(second.move_id, actor=admin)
    assert await organizer.last_move(added.asset.id) == first.move_id


async def test_an_asset_that_has_never_moved_has_nothing_to_undo(
    organizer: Organizer, managed: Library, add_file: Any
) -> None:
    added = await add_file(managed, "clip.mp4")
    assert await organizer.last_move(added.asset.id) is None
