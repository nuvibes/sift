# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two tiers, and the refusals in front of the destructive one. A surviving file is checked by
its bytes on disk."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

# The event tables belong to the workbench slice, registered here.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.content import ContentStore, DerivativeKind
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import MOST_SUBJECTS
from sift.kernel.reach import VAULT_LOCKED
from sift.slices.delete.service import (
    Deleter,
    DeleteRefused,
    NotAllowed,
    NotFound,
    VaultLocked,
    _remove_directory,
)
from sift.testing.fixtures import hide

from .conftest import Library, RecordingPlaybackCache

pytestmark = pytest.mark.integration


# --- tier one: remove from Sift -----------------------------------------------------------


async def test_removing_from_sift_leaves_the_file_exactly_as_it_was(
    deleter: Deleter,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The safe tier, and the claim the confirm dialog makes on its behalf.

    The bytes are compared, not merely the file's existence: "your file stays on disk" is a promise
    about the file, and a test that only checked the path still resolved would pass against an
    implementation that truncated it.
    """
    added = await add_file(managed, "clip.mp4")
    path = managed.path / "clip.mp4"
    before = path.read_bytes()

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    assert path.exists()
    assert path.read_bytes() == before
    assert await content_store.get(added.asset.id) is None


async def test_a_file_removed_from_sift_comes_back_as_the_same_asset(
    deleter: Deleter,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """ "You can add it back by re-scanning", with its identity, not as something new.

    The digest is what makes that true, so this re-imports the untouched file exactly as a scan
    would and checks the asset that comes back carries the same one.
    """
    added = await add_file(managed, "clip.mp4")
    digest = added.asset.identity

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    rescanned = await add_file(managed, "clip.mp4")

    assert rescanned.asset.identity == digest
    assert await content_store.resolve_by_identity(digest) is not None


async def test_a_guest_cannot_forget_even_what_they_can_see(
    deleter: Deleter,
    content_store: ContentStore,
    access: Repository,
    managed: Library,
    add_file: Any,
    guest: Viewer,
) -> None:
    """Forgetting leaves the bytes but drops the shared index entry for everyone and cascades away
    the tags, people and ratings on it: that is curation, so it is an admin's. Shared with the
    guest deliberately, so the refusal is about the operation and not about the asset being
    invisible: the file and the record both survive."""
    added = await add_file(managed, "clip.mp4")
    await access.grant(ObjectType.ROOT, managed.root.id, guest.id, Effect.SHARE)

    with pytest.raises(NotAllowed):
        await deleter.remove(added.asset.id, mode="sift", actor=guest)

    assert (managed.path / "clip.mp4").exists()
    assert await content_store.get(added.asset.id) is not None


# --- who may do what ------------------------------------------------------------------------


async def test_a_guest_cannot_delete_from_disk_what_they_can_see(
    deleter: Deleter,
    access: Repository,
    managed: Library,
    add_file: Any,
    guest: Viewer,
) -> None:
    """Refused for being a guest, and the file is still there afterwards.

    Shared with them deliberately: the refusal has to be about the operation rather than about
    visibility, or this would pass against code that simply could not find the asset.
    """
    added = await add_file(managed, "clip.mp4")
    await access.grant(ObjectType.ROOT, managed.root.id, guest.id, Effect.SHARE)

    with pytest.raises(NotAllowed):
        await deleter.remove(added.asset.id, mode="disk", actor=guest)

    assert (managed.path / "clip.mp4").exists()


async def test_a_guest_deleting_what_they_cannot_see_is_told_it_is_not_there(
    deleter: Deleter, managed: Library, add_file: Any, guest: Viewer
) -> None:
    """No existence oracle.

    A guest who is refused with "admins only" has learned the file exists. Both modes answer the
    same way for an asset they cannot see, and it is the same answer as for an id that was never
    real, so asking cannot tell them apart.
    """
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(NotFound):
        await deleter.remove(added.asset.id, mode="disk", actor=guest)
    with pytest.raises(NotFound):
        await deleter.remove(added.asset.id, mode="sift", actor=guest)

    assert (managed.path / "clip.mp4").exists()


async def test_an_asset_that_never_existed_is_not_there_either(
    deleter: Deleter, admin: Viewer
) -> None:
    """The same refusal an admin gets for an id nobody ever minted."""
    with pytest.raises(NotFound):
        await deleter.remove("01HX0000000000000000000404", mode="disk", actor=admin)


async def test_naming_a_location_that_is_not_this_assets_is_not_there(
    deleter: Deleter, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """A location id belonging to something else does not widen what a caller can reach.

    Different bytes for the two files, which matters: the same bytes twice would be one asset with
    two locations, and naming either of them would be perfectly legitimate.
    """
    mine = await add_file(managed, "mine.mp4")
    other = await add_file(managed, "other.mkv", source="accepted.mkv")

    with pytest.raises(NotFound):
        await deleter.remove(mine.asset.id, mode="disk", actor=admin, location_id=other.location.id)

    assert (managed.path / "other.mkv").exists()


# --- the read-only refusal ------------------------------------------------------------------


async def test_disk_delete_is_refused_on_a_root_nobody_opted_in_for(
    deleter: Deleter,
    content_store: ContentStore,
    read_only: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The refusal this slice is built around, and it happens here rather than at the syscall.

    The filesystem is asked before the unlink, and its no comes back as a sentence that says what a
    person can go and check (who owns the folder, whether Sift may write there), rather than as
    an OSError from halfway through. An unlink that would have failed is not attempted.
    """
    added = await add_file(read_only, "clip.mp4")

    with pytest.raises(DeleteRefused, match="not allowed to write"):
        await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert (read_only.path / "clip.mp4").exists()
    assert await content_store.get(added.asset.id) is not None


async def test_the_safe_tier_still_works_on_a_read_only_root(
    deleter: Deleter,
    content_store: ContentStore,
    read_only: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Remove-from-Sift is always available, on every root. It writes nothing."""
    added = await add_file(read_only, "clip.mp4")

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    assert (read_only.path / "clip.mp4").exists()
    assert await content_store.get(added.asset.id) is None


async def test_a_managed_root_that_has_since_become_unwritable_is_refused(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filesystem is asked at the moment of the delete.

    A root remounted read-only since it was added has to be refused, and refused in a sentence
    rather than by the unlink failing halfway through.
    """
    added = await add_file(managed, "clip.mp4")

    # Patched where the check reads it, which is the kernel module that owns the question, not
    # in this slice, which never asks the filesystem anything itself.
    import sift.kernel.content.library as library_module

    monkeypatch.setattr(library_module, "mount_is_readonly", lambda path: True)

    with pytest.raises(DeleteRefused, match="read-only"):
        await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert (managed.path / "clip.mp4").exists()


async def test_a_location_pointing_at_a_root_that_is_gone_is_refused(
    deleter: Deleter, managed: Library, add_file: Any
) -> None:
    """A location pointing at a root that is gone is refused with a sentence, against the check
    itself: the foreign key prevents the state, but restored backups may not."""
    added = await add_file(managed, "clip.mp4")
    location = replace(added.location, root_id="01HX0000000000000000000404")

    with pytest.raises(DeleteRefused, match="no longer part of your library"):
        await deleter._writable_path(location)


# --- multi-location ---------------------------------------------------------------------------


async def test_losing_one_of_two_locations_leaves_the_asset_and_its_copy(
    deleter: Deleter,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The subtle one, and where a naive implementation destroys somebody's metadata.

    An asset with two copies that loses one still exists (it is elsewhere), so the row that
    everything anyone recorded about it hangs off must survive, and so must the other file.
    """
    first = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")
    assert len(await content_store.locations(first.asset.id)) == 2

    await deleter.remove(first.asset.id, mode="disk", actor=admin, location_id=first.location.id)

    assert await content_store.get(first.asset.id) is not None
    assert [place.rel_path for place in await content_store.locations(first.asset.id)] == [
        "two.mp4"
    ]
    assert not (managed.path / "one.mp4").exists()
    assert (managed.path / "two.mp4").exists()


async def test_losing_the_last_location_is_what_ends_the_asset(
    deleter: Deleter,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """And the file it named is gone from the disk."""
    added = await add_file(managed, "only.mp4")

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert await content_store.get(added.asset.id) is None
    assert not (managed.path / "only.mp4").exists()


async def test_deleting_an_asset_takes_every_copy_when_no_location_is_named(
    deleter: Deleter, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """Naming no location means the asset, which is all of it."""
    added = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert not (managed.path / "one.mp4").exists()
    assert not (managed.path / "two.mp4").exists()


async def test_nothing_is_deleted_when_one_of_several_folders_refuses(
    deleter: Deleter,
    content_store: ContentStore,
    managed: Library,
    read_only: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """All or nothing, and the reason is that half a delete cannot be described to anybody.

    The same bytes in two libraries, one of which Sift may change and one of which it may not.
    Checking each folder as it goes would remove the first file and then refuse, leaving somebody
    with one copy gone, a refusal on screen, and no way to tell which happened.
    """
    added = await add_file(managed, "clip.mp4")
    await add_file(read_only, "clip.mp4")

    with pytest.raises(DeleteRefused):
        await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert (managed.path / "clip.mp4").exists()
    assert (read_only.path / "clip.mp4").exists()
    assert await content_store.get(added.asset.id) is not None


# --- what "permanent" means -------------------------------------------------------------------


async def test_a_delete_from_disk_leaves_nothing_anywhere_to_recover_from(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    tmp_path: Path,
) -> None:
    """A delete from disk leaves the file nowhere: the whole temporary tree is swept."""
    added = await add_file(managed, "clip.mp4")
    body = (managed.path / "clip.mp4").read_bytes()

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    survivors = [
        path for path in tmp_path.rglob("*") if path.is_file() and path.read_bytes() == body
    ]
    assert survivors == [], "a delete from disk left a copy of the file somewhere"


async def test_a_file_already_gone_from_the_disk_does_not_stop_the_delete(
    deleter: Deleter,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Somebody removed it in a file manager between the scan and the delete.

    Raising here would leave the index entry pointing at nothing, with no way to clear it: the
    delete that would remove the row is the one that keeps failing. The file is gone, which is what
    was asked for.
    """
    added = await add_file(managed, "clip.mp4")
    (managed.path / "clip.mp4").unlink()

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert await content_store.get(added.asset.id) is None


# --- the grants an ended asset takes with it ---------------------------------------------------


async def test_forgetting_an_asset_takes_its_grants_with_it(
    deleter: Deleter,
    access: Repository,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    guest: Viewer,
) -> None:
    """Forgetting an asset takes its grants: grants carry no foreign key."""
    added = await add_file(managed, "clip.mp4")
    await access.grant(ObjectType.ITEM, added.asset.id, guest.id, Effect.SHARE)

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    assert await access.grants_of(guest.id) == []


async def test_deleting_from_disk_takes_the_grants_too(
    deleter: Deleter,
    access: Repository,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    guest: Viewer,
) -> None:
    """The other tier, and it is not the same code path.

    Removing the bytes ends the asset exactly as forgetting it does, so it has to clear up after
    itself exactly as forgetting does. Two tiers is two chances to leave the grant behind, which is
    why they finish in one shared place rather than in two matching pairs of lines.
    """
    added = await add_file(managed, "clip.mp4")
    await access.grant(ObjectType.ITEM, added.asset.id, guest.id, Effect.RESTRICT)

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert await access.grants_of(guest.id) == []


async def test_losing_one_of_two_locations_keeps_the_grants(
    deleter: Deleter,
    access: Repository,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    guest: Viewer,
) -> None:
    """The asset survived, so its access rules survive with it.

    This is the half of the rule that a cleanup written as "clear the grants whenever a delete
    runs" gets wrong, and gets wrong in the dangerous direction: it would quietly cancel a restrict
    (a promise that something is never shown to somebody) because a redundant copy of the file
    was tidied away.
    """
    first = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")
    await access.grant(ObjectType.ITEM, first.asset.id, guest.id, Effect.RESTRICT)

    await deleter.remove(first.asset.id, mode="sift", actor=admin, location_id=first.location.id)

    assert await content_store.get(first.asset.id) is not None
    remaining = await access.grants_of(guest.id)
    assert [(grant.object_type, grant.effect) for grant in remaining] == [
        (ObjectType.ITEM, Effect.RESTRICT)
    ]


async def test_deleting_from_disk_takes_the_pictures_made_from_it_too(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    content_store: ContentStore,
) -> None:
    """Deleting from disk takes the cached pictures made from it."""
    added = await add_file(managed, "clip.mp4")

    thumb = content_store.derivative_path(added.asset.id, DerivativeKind.THUMB, extension="jpg")
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"a picture of it")
    await content_store.add_derivative(added.asset.id, DerivativeKind.THUMB, extension="jpg")
    assert thumb.exists()

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert not (managed.path / "clip.mp4").exists()
    assert not thumb.exists(), "the picture made from a deleted file is still in the cache"


async def test_a_cache_that_cannot_be_written_does_not_fail_the_delete(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    content_store: ContentStore,
) -> None:
    """The cache is the disposable half, and a delete must never come to depend on it.

    A picture that cannot be unlinked is left where it is and found later by the leftover sweep.
    The file the person asked about is gone either way, which is the half that cannot be redone.
    """
    added = await add_file(managed, "clip.mp4")
    await content_store.add_derivative(added.asset.id, DerivativeKind.THUMB, extension="jpg")
    # No file was ever written at that path, which is the same situation as one that cannot be
    # removed: the unlink has nothing to work on.

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert not (managed.path / "clip.mp4").exists()


async def test_the_transcoded_pieces_of_a_deleted_file_go_with_it(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    playback_cache: RecordingPlaybackCache,
) -> None:
    """The same argument as the pictures, applied to the other cache made from a file.

    A video a browser cannot play natively is transcoded a few seconds at a time and those pieces
    are kept. Nothing in the database points at them, so a deleted file's pieces are never asked for
    again: they would sit there until the cap happened to evict them, which depends on how much
    somebody watches afterwards rather than on anything about the file that was deleted.
    """
    added = await add_file(managed, "clip.mp4")

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert playback_cache.discarded == [added.asset.id]


async def test_forgetting_a_file_drops_its_pieces_as_well_as_deleting_one(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    playback_cache: RecordingPlaybackCache,
) -> None:
    """Not gated on which mode was asked for, and that is deliberate rather than sloppy.

    A file forgotten from Sift keeps its bytes, so it can be scanned again, but the digest gives
    it a NEW row with a new id, and every piece in the cache is keyed by the old one. They are
    unreachable either way, so there is nothing to keep them for.
    """
    added = await add_file(managed, "clip.mp4")

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    assert playback_cache.discarded == [added.asset.id]
    assert (managed.path / "clip.mp4").exists(), "forgetting must not touch the file"


async def test_a_file_that_only_lost_one_of_its_places_keeps_its_pieces(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    playback_cache: RecordingPlaybackCache,
) -> None:
    """The asset survived, so somebody may still be watching it.

    The same half of the rule the grants test guards, and it fails in the same direction if this is
    written as "clear the cache whenever a delete runs": tidying a redundant copy would throw away
    the transcode of a video that is playing right now.
    """
    first = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")

    await deleter.remove(first.asset.id, mode="sift", actor=admin, location_id=first.location.id)

    assert playback_cache.discarded == []


# --- three refusals nothing else reaches --------------------------------------------------------


def test_a_directory_that_has_already_gone_counts_as_removed(tmp_path: Path) -> None:
    """The outcome asked for is the outcome there is.

    Two passes over the same subtree, or somebody clearing up alongside Sift, both end here, and
    reporting a folder as left behind when it is not there would send a person looking for it.
    """
    assert _remove_directory(tmp_path / "never-was") is True
    holds_something = tmp_path / "full"
    holds_something.mkdir()
    (holds_something / "notes.txt").write_text("not Sift's", encoding="utf-8")
    assert _remove_directory(holds_something) is False, "a folder that still holds something stays"


async def test_a_folder_the_two_stores_disagree_about_is_not_there(
    deleter: Deleter, managed: Library, admin: Viewer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floor under a folder that passed the permission read and has no row behind it.

    They are two stores and they are read one after the other, so a folder removed between the two
    lands here. Told it is NOT THERE rather than anything else: a person who cannot be shown a
    folder and a folder that has gone have to answer the same, or the difference names it.
    """
    root_folder = await deleter._library.root_folder(managed.root.id)
    assert root_folder is not None
    inside = await deleter._library.upsert_folder(managed.root.id, "clips")

    async def gone(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(deleter._library, "get_folder", gone)

    with pytest.raises(NotFound):
        await deleter.remove_folder(inside.id, actor=admin)


async def test_a_folder_holding_something_the_actor_cannot_see_deletes_nothing(
    deleter: Deleter,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused before the first file goes, and refused as a whole.

    A locked vault takes files off the screen. Deleting a folder while one is hidden would take away
    something the person was never shown, so this stops, and says which question to answer first,
    rather than deleting what it could see and leaving what it could not.
    """
    folder = await deleter._library.upsert_folder(managed.root.id, "clips")
    added = await add_file(managed, "clips/one.mp4", folder_id=folder.id)

    async def cannot_see(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(deleter._access, "open_asset", cannot_see)
    assert await deleter._library.locations_in_folder(folder.id), (
        "the file has to be recorded IN that folder, or there is nothing for the vault to hide"
    )

    with pytest.raises(DeleteRefused):
        await deleter.remove_folder(folder.id, actor=admin)

    assert (managed.path / "clips" / "one.mp4").exists(), "nothing may go while something is hidden"
    assert added.asset.id


# --- the vault, which is the one refusal that does not wear the undifferentiated face -----------


async def test_a_file_concealed_by_the_asker_own_vault_says_locked_rather_than_gone(
    deleter: Deleter,
    managed: Library,
    add_file: Callable[[Library, str], Any],
    admin: Viewer,
    temp_db: Any,
) -> None:
    """ "There is no such file" told to somebody looking at the padlock of the file they are trying
    to delete is a lie, and the more alarming one.

    THE USER HAS TO BE ONE THAT COULD OTHERWISE SEE THE FILE. `is_concealed` puts the same
    question with the vault shut and then open, and only a file that APPEARS when it opens was
    concealed: a user who cannot see it either way was refused, and a refusal is not
    concealment. That is what keeps the 423 from confirming a stranger's id names something real.
    """
    ingested = await add_file(managed, "clip.mp4")
    await hide(temp_db, "asset", ingested.asset.id, admin.id)

    refused = await deleter._unreachable(admin, ingested.asset.id)

    assert isinstance(refused, VaultLocked)


async def test_a_file_that_is_simply_not_there_stays_an_undifferentiated_absence(
    deleter: Deleter, admin: Viewer
) -> None:
    """Without this the one above would pass against a service that answered locked to everything."""
    refused = await deleter._unreachable(admin, "01M0NOSUCHASSETIDATALL0000")

    assert isinstance(refused, NotFound)


def test_the_vault_is_the_status_a_refusal_carries_and_the_others_keep_their_own() -> None:
    """The router's half. 423 rather than 404, and only for this one: the three beneath it are
    what stop this being a mapper that answers locked to everything."""
    from fastapi import status

    from sift.kernel.reach import ConcealedByVault
    from sift.slices.delete.router import _refusal

    # `ConcealedByVault` comes from the kernel rather than from this service, so it is not a
    # `DeleteRefused` by type: the route catches both and this mapper is what tells them apart.
    assert _refusal(cast(Any, ConcealedByVault("shut"))).status_code == status.HTTP_423_LOCKED
    assert _refusal(NotFound("no such file")).status_code == status.HTTP_404_NOT_FOUND
    assert _refusal(NotAllowed("not yours")).status_code == status.HTTP_403_FORBIDDEN


# --- a selection, as one operation ---------------------------------------------------------------


async def test_a_guest_is_refused_a_whole_selection_before_any_file_is_read(
    deleter: Deleter, managed: Library, add_file: Any, guest: Viewer
) -> None:
    """The refusal is about the caller and names no file, so it is raised rather than counted:
    counted, a guest's selection would come back as honoured with nothing removed."""
    added = await add_file(managed, "clip.mp4")

    with pytest.raises(NotAllowed):
        await deleter.remove_many([added.asset.id], mode="sift", actor=guest)

    assert (managed.path / "clip.mp4").exists()


async def test_a_folder_that_refuses_a_disk_delete_is_skipped_and_the_selection_says_why(
    deleter: Deleter,
    content_store: ContentStore,
    read_only: Library,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """One file that cannot go does not leave the rest where they were. Its folder's refusal is
    the reason the selection reports, and the file it refused for is exactly as it was."""
    kept = await add_file(read_only, "kept.mp4")
    gone = await add_file(managed, "gone.webm", source="accepted.webm")

    done = await deleter.remove_many([kept.asset.id, gone.asset.id], mode="disk", actor=admin)

    assert (done.removed, done.skipped, done.vault_locked) == (1, 1, False)
    assert done.reason is not None and "not allowed to write" in done.reason
    assert (read_only.path / "kept.mp4").exists()
    assert not (managed.path / "gone.webm").exists()
    assert await content_store.get(kept.asset.id) is not None


async def test_the_vault_keeps_its_sentence_over_a_folders_refusal(
    deleter: Deleter,
    temp_db: Any,
    read_only: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Told a folder refused, a person has learned nothing to do; told the vault is shut, they
    have a PIN. So the vault's sentence stands however many folders refuse after it."""
    hidden = await add_file(read_only, "hidden.webm", source="accepted.webm")
    refused = await add_file(read_only, "refused.mp4")
    await hide(temp_db, "asset", hidden.asset.id, admin.id)

    done = await deleter.remove_many([hidden.asset.id, refused.asset.id], mode="disk", actor=admin)

    assert (done.removed, done.skipped, done.vault_locked) == (0, 2, True)
    assert done.reason == VAULT_LOCKED
    assert (read_only.path / "hidden.webm").exists()
    assert (read_only.path / "refused.mp4").exists()


# --- what the delete writes down ----------------------------------------------------------------
#
# The event is the whole record of a deleted file, and what it names decides where it is read.

#: Invented for these tests; see `tests/gates/data/names_cast.txt`.
A_PERSON = "Neve Alder"
A_TAG = "poolside"


async def a_person(database: Any, name: str) -> str:
    person_id = new_id()
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    return person_id


async def on_person(database: Any, asset_id: str, person_id: str) -> None:
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (asset_id, person_id)
    )


async def subjects_of_events(database: Any) -> list[tuple[str, str]]:
    """Every subject of every event, in the order the door wrote them."""
    rows = await database.fetch_all(
        "SELECT s.kind AS kind, s.name AS name FROM workbench_decision_subjects s"
        " JOIN workbench_decisions d ON d.id = s.decision_id"
        " ORDER BY d.id, s.rowid"
    )
    return [(str(row["kind"]), str(row["name"])) for row in rows]


async def test_a_delete_names_what_the_file_was_on(
    deleter: Deleter,
    temp_db: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The file first, then the people, tags, Sites and shelves it was on: the order a file's
    own record lists them, and the order that decides which seven survive a file on more."""
    added = await add_file(managed, "clip.mp4")
    await on_person(temp_db, added.asset.id, await a_person(temp_db, A_PERSON))
    tag_id = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag_id, A_TAG)
    )
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (added.asset.id, tag_id)
    )

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    assert await subjects_of_events(temp_db) == [
        ("asset", "clip.mp4"),
        ("person", A_PERSON),
        ("tag", A_TAG),
    ]


async def test_a_file_on_more_than_the_ceiling_names_seven_and_counts_the_rest(
    deleter: Deleter,
    temp_db: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The door refuses an event naming more subjects than an act plausibly has, and a file on ten
    people is exactly that. Seven are named (the first seven of the file's own order) and the
    remainder is a number in the payload, which is what the line reads back as "and 3 more"."""
    added = await add_file(managed, "clip.mp4")
    for at in range(10):
        await on_person(temp_db, added.asset.id, await a_person(temp_db, f"{A_PERSON} {at}"))

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    named = await subjects_of_events(temp_db)
    assert len(named) == MOST_SUBJECTS
    assert [name for _, name in named[1:]] == [f"{A_PERSON} {at}" for at in range(7)]
    rows = await temp_db.fetch_all("SELECT payload AS payload FROM workbench_decisions")
    said = json.loads(str(rows[0]["payload"]))
    assert (said["from"], said["more"]) == ("sift", 3)
    # The payload keeps all ten, with their names: the seven are only what the line can name.
    assert [one["name"] for one in said["people"]] == [f"{A_PERSON} {at}" for at in range(10)]


async def test_a_deleted_file_keeps_what_insights_needs_once_it_is_gone(
    deleter: Deleter,
    temp_db: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Its size, kind, arrival and length, its song, and every person and tag it carried, each
    with the name it had: nothing else can say any of it once the file is gone."""
    added = await add_file(managed, "clip.mp4")
    person = await a_person(temp_db, A_PERSON)
    await on_person(temp_db, added.asset.id, person)
    tag_id = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag_id, A_TAG)
    )
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (added.asset.id, tag_id)
    )
    song = new_id()
    await temp_db.execute(
        "INSERT INTO songs (id, name, created_at) VALUES (?, 'Tidewater', 0)", (song,)
    )
    await temp_db.execute(
        "INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)", (added.asset.id, song)
    )
    facts = await temp_db.fetch_all(
        "SELECT size_bytes, media_type, added_at, duration_ms FROM assets WHERE id = ?",
        (added.asset.id,),
    )

    await deleter.remove(added.asset.id, mode="disk", actor=admin)

    rows = await temp_db.fetch_all("SELECT payload AS payload FROM workbench_decisions")
    said = json.loads(str(rows[0]["payload"]))
    assert said["from"] == "disk"
    assert (said["size"], said["kind"], said["arrived"], said["length_ms"]) == tuple(facts[0])
    assert said["song"] == {"id": song, "name": "Tidewater"}
    assert said["people"] == [{"id": person, "name": A_PERSON}]
    assert said["tags"] == [{"id": tag_id, "name": A_TAG}]


async def test_a_delete_over_a_selection_names_each_file_s_own_things(
    deleter: Deleter,
    temp_db: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """One read for the whole selection, and the answer has to come apart again per file: a bulk
    delete that gave every event the same list would put a person on a file they were never on."""
    one = await add_file(managed, "one.mp4")
    two = await add_file(managed, "two.webm", source="accepted.webm")
    await on_person(temp_db, one.asset.id, await a_person(temp_db, A_PERSON))

    await deleter.remove_many([one.asset.id, two.asset.id], mode="sift", actor=admin)

    assert sorted(await subjects_of_events(temp_db)) == sorted(
        [("asset", "one.mp4"), ("person", A_PERSON), ("asset", "two.webm")]
    )


async def test_a_folder_whose_file_has_a_copy_elsewhere_ends_nothing_and_says_nothing_ended(
    deleter: Deleter,
    content_store: ContentStore,
    temp_db: Database,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The folder's copy goes from the disk; the file does not leave the library, because another
    copy of it is outside the folder. So no "deleted" line is written for it: a line saying a file
    was deleted while it is still on every wall would describe what did not happen."""
    folder = await deleter._library.upsert_folder(managed.root.id, "clips")
    inside = await add_file(managed, "clips/one.mp4", folder_id=folder.id)
    await add_file(managed, "two.mp4")
    assert len(await content_store.locations(inside.asset.id)) == 2

    await deleter.remove_folder(folder.id, actor=admin)

    assert not (managed.path / "clips" / "one.mp4").exists()
    assert (managed.path / "two.mp4").exists()
    assert await content_store.get(inside.asset.id) is not None
    deleted = await temp_db.fetch_all(
        "SELECT id FROM workbench_decisions WHERE verb = 'deleted'", ()
    )
    assert deleted == []
