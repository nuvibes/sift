# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the panel is told, and what starting a compression actually queues.

The refusals are the interesting half. Three things stop a file being acted on (it is a
photograph, it sits in a folder Sift was given read-only, or the user cannot see it) and each
of them is a case where the verb should never have been offered in the first place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.kernel.reach import VAULT_LOCKED
from sift.slices.media_edit import service as service_module
from sift.slices.media_edit.models import CompressRequest
from sift.slices.media_edit.provenance import MOST_COPIES
from sift.slices.media_edit.refusals import NotAllowed, NotFound, Refused
from sift.slices.media_edit.service import CompressService, Target
from sift.slices.media_edit.settings import (
    BYTES_PER_MEGABYTE,
    DEFAULTS,
    KEY_FOR_PRESET,
    MAXIMUM_TARGET_MB,
    Preset,
    resolve_target_mb,
)
from sift.slices.media_edit.tests.conftest import Library
from sift.testing.fixtures import FakeClock, png_bytes
from sift.testing.library import hidden_row

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stub_handlers")]


# --- the targets are settings, not constants ---------------------------------------------------


async def test_a_preset_resolves_to_the_stored_number(
    compressor: CompressService, targets: dict[str, Any]
) -> None:
    """The name is a convenience. The number is the contract, and it is editable."""
    targets[KEY_FOR_PRESET[Preset.SMALL]] = 25
    target = await compressor.resolve_target(CompressRequest(asset_ids=["a"], preset=Preset.SMALL))
    assert target.bytes == 25 * BYTES_PER_MEGABYTE


@pytest.mark.parametrize("preset", list(KEY_FOR_PRESET))
async def test_every_preset_reads_its_own_stored_number_and_no_other(
    compressor: CompressService, targets: dict[str, Any], preset: Preset
) -> None:
    """Each size row on Settings > Editing moves its own preset. Every preset is stored a different
    number, so a preset reading a neighbour's key, or its starting number, answers wrongly."""
    for at, one in enumerate(KEY_FOR_PRESET):
        targets[KEY_FOR_PRESET[one]] = 7 + at
    wanted = 7 + list(KEY_FOR_PRESET).index(preset)
    target = await compressor.resolve_target(CompressRequest(asset_ids=["a"], preset=preset))
    assert target.bytes == wanted * BYTES_PER_MEGABYTE


async def test_a_preset_with_nothing_stored_uses_its_starting_number(
    compressor: CompressService,
) -> None:
    target = await compressor.resolve_target(
        CompressRequest(asset_ids=["a"], preset=Preset.STANDARD)
    )
    assert target.bytes == DEFAULTS[Preset.STANDARD] * BYTES_PER_MEGABYTE


@pytest.mark.parametrize("stored", [0, -5, True, "50", MAXIMUM_TARGET_MB + 1, None])
async def test_an_unusable_stored_number_falls_back_rather_than_failing(stored: object) -> None:
    """A bad row must not be a compression screen that will not open."""
    assert resolve_target_mb(stored, Preset.LARGE) == DEFAULTS[Preset.LARGE]


async def test_a_custom_target_carries_its_own_number(compressor: CompressService) -> None:
    target = await compressor.resolve_target(
        CompressRequest(asset_ids=["a"], preset=Preset.CUSTOM, custom_target_mb=7)
    )
    assert target.bytes == 7 * BYTES_PER_MEGABYTE


async def test_compatibility_alone_has_no_size_target(compressor: CompressService) -> None:
    target = await compressor.resolve_target(CompressRequest(asset_ids=["a"], compatibility=True))
    assert target.bytes is None


def test_a_request_that_asks_for_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="size target"):
        CompressRequest(asset_ids=["a"])


def test_a_custom_target_with_no_size_is_refused() -> None:
    with pytest.raises(ValueError, match="custom target"):
        CompressRequest(asset_ids=["a"], preset=Preset.CUSTOM)


# --- what the copy is called ------------------------------------------------------------------


def test_the_output_carries_the_target_in_its_name() -> None:
    """So the two are told apart at a glance in a file manager, not only in Sift."""
    target = Target(bytes=10 * BYTES_PER_MEGABYTE, compatibility=False, preset=Preset.SMALL)
    assert service_module.output_filename("beach trip.mp4", target) == "beach trip-10MB.mp4"


def test_two_targets_on_one_file_produce_two_names() -> None:
    """The reason the size is in the name rather than the word "compressed"."""
    small = Target(bytes=10 * BYTES_PER_MEGABYTE, compatibility=False, preset=Preset.SMALL)
    large = Target(bytes=100 * BYTES_PER_MEGABYTE, compatibility=False, preset=Preset.LARGE)
    assert service_module.output_filename("clip.mkv", small) != service_module.output_filename(
        "clip.mkv", large
    )


def test_a_fractional_target_keeps_its_fraction_in_the_name() -> None:
    target = Target(bytes=int(1.5 * BYTES_PER_MEGABYTE), compatibility=False, preset=Preset.CUSTOM)
    assert service_module.output_filename("clip.mp4", target) == "clip-1.5MB.mp4"


def test_a_compatibility_only_output_says_so_in_its_name() -> None:
    target = Target(bytes=None, compatibility=True, preset=None)
    assert service_module.output_filename("clip.mkv", target) == "clip-compatible.mp4"


def test_a_file_with_no_name_still_gets_one() -> None:
    target = Target(bytes=None, compatibility=True, preset=None)
    assert service_module.output_filename(None, target).endswith(".mp4")
    assert service_module.output_filename(".hidden", target).startswith("file-")


# --- who and what is eligible -------------------------------------------------------------------


async def test_a_photograph_is_not_offered_compression(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    """Everything in the design is video-shaped. A picture's answer is Resize."""
    ingested = await add_file(managed, "shot.jpg", source="accepted.jpg")
    answer = await compressor.preflight(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL), viewer=admin
    )
    verdict = answer.files[0]
    assert verdict.skip_reason is not None
    assert "resized" in verdict.skip_reason
    assert answer.eligible_count == 0


async def test_a_file_in_a_read_only_folder_is_not_offered_compression(
    compressor: CompressService,
    read_only: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    """The required test. Asked of the same function the move menu asks.

    Writing a NEW file into a folder is as much a write as replacing one, so the folder has to have
    been handed over read-write for either.
    """
    ingested = await add_file(read_only, "clip.mp4")
    answer = await compressor.preflight(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL), viewer=admin
    )
    assert answer.files[0].skip_reason is not None
    assert answer.eligible_count == 0


async def test_a_file_the_account_cannot_see_is_reported_as_absent(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
) -> None:
    """The same answer as a file that is not there. The other answer confirms it is."""
    ingested = await add_file(managed, "clip.mp4")
    answer = await compressor.preflight(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL), viewer=guest
    )
    assert answer.files[0].skip_reason == "Sift could not find the file."


async def test_a_file_in_the_accounts_own_vault_says_so_rather_than_that_it_is_gone(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The one exception to the answer above, and the same one the rest of the app makes.

    The vault is presentation-layer concealment: it defends against somebody reading your screen
    and has never claimed to keep a file from the user who put it there. "There is no such
    file" tells that user their file has been deleted: a lie, the more alarming of the two
    readings, and one they can do nothing about. The real reason comes with a PIN.

    The test above is the other half and is what stops this leaking: a GUEST asking about the same
    file still gets the undifferentiated answer, because it is not their vault.
    """
    ingested = await add_file(managed, "clip.mp4")
    statement, parameters = hidden_row("asset", ingested.asset.id, admin.id)
    async with temp_db.write() as connection:
        await connection.execute(statement, parameters)

    answer = await compressor.preflight(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL), viewer=admin
    )

    assert answer.files[0].skip_reason == VAULT_LOCKED


async def test_an_id_that_names_nothing_is_reported_as_absent(
    compressor: CompressService, admin: Viewer
) -> None:
    answer = await compressor.preflight(
        CompressRequest(asset_ids=["01HX0000000000000000000099"], preset=Preset.SMALL),
        viewer=admin,
    )
    assert answer.files[0].skip_reason == "Sift could not find the file."


async def test_only_an_admin_may_start_one(compressor: CompressService, guest: Viewer) -> None:
    with pytest.raises(Refused, match="admin"):
        await compressor.start(CompressRequest(asset_ids=["a"], preset=Preset.SMALL), viewer=guest)


async def test_a_guest_asking_for_a_sample_is_told_there_is_no_such_file(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
) -> None:
    """Visibility before permission. "Admins only" would confirm the file is there."""
    ingested = await add_file(managed, "clip.mp4")
    with pytest.raises(NotFound):
        await compressor.sample(
            ingested.asset.id,
            CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL),
            viewer=guest,
        )


async def test_a_guest_who_can_see_a_file_still_may_not_sample_it(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second half of the same rule: seeing a file is not permission to work the machine on it."""
    ingested = await add_file(managed, "clip.mp4")
    asset = await _asset_of(compressor, ingested.asset.id)
    monkeypatch.setattr(
        compressor._access,
        "open_asset",
        _always(asset),
    )
    with pytest.raises(NotAllowed, match="admin"):
        await compressor.sample(
            ingested.asset.id,
            CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL),
            viewer=guest,
        )


# --- the warning, per file and counted --------------------------------------------------------


async def test_an_unreachable_file_is_warned_about_and_not_started(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    job_queue: JobQueue,
    targets: dict[str, Any],
) -> None:
    """The required test. The warning comes before any encoding, and nothing is queued.

    The seeded clip is tiny, so it is made unreachable by asking for something under a megabyte
    that the floor cannot deliver, which is the same arithmetic a two-hour video hits.
    """
    ingested = await add_file(managed, "clip.mp4")
    await _pretend_it_is_a_long_video(compressor, ingested.asset.id)
    targets[KEY_FOR_PRESET[Preset.SMALL]] = 1

    answer = await compressor.preflight(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL), viewer=admin
    )
    assert answer.unreachable_count == 1
    assert answer.files[0].reason is not None
    assert answer.suggested_target_bytes is not None

    started = await compressor.start(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL), viewer=admin
    )
    assert started.started == 0
    assert started.skipped == 1


async def test_an_unreachable_file_can_still_be_forced(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    targets: dict[str, Any],
) -> None:
    """The warning informs; it does not refuse."""
    ingested = await add_file(managed, "clip.mp4")
    await _pretend_it_is_a_long_video(compressor, ingested.asset.id)
    targets[KEY_FOR_PRESET[Preset.SMALL]] = 1

    started = await compressor.start(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL, force=True),
        viewer=admin,
    )
    assert started.started == 1


async def test_a_selection_reports_how_many_are_affected_rather_than_one_banner(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    targets: dict[str, Any],
) -> None:
    """A single sentence over forty files says nothing anybody can act on."""
    reachable = await add_file(managed, "small.mp4")
    unreachable = await add_file(managed, "big.mkv", source="accepted.mkv")
    await _pretend_it_is_a_long_video(compressor, unreachable.asset.id)
    targets[KEY_FOR_PRESET[Preset.SMALL]] = 1

    answer = await compressor.preflight(
        CompressRequest(asset_ids=[reachable.asset.id, unreachable.asset.id], preset=Preset.SMALL),
        viewer=admin,
    )
    assert answer.unreachable_count == 1
    assert len(answer.files) == 2


async def test_a_file_that_already_fits_is_not_re_encoded(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    targets: dict[str, Any],
) -> None:
    """Spending four minutes making something worse is not a compression."""
    ingested = await add_file(managed, "clip.mp4")
    targets[KEY_FOR_PRESET[Preset.LARGE]] = 500

    answer = await compressor.preflight(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.LARGE), viewer=admin
    )
    assert answer.copy_only_count == 1
    started = await compressor.start(
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.LARGE), viewer=admin
    )
    assert started.started == 0


# --- provenance ---------------------------------------------------------------------------------


async def test_an_ordinary_file_has_no_provenance(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    ingested = await add_file(managed, "clip.mp4")
    assert await compressor.produced_for(ingested.asset.id, viewer=admin) is None


async def test_a_copy_says_what_it_was_made_from(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    original = await add_file(managed, "clip.mp4")
    copy = await add_file(managed, "clip-10MB.mkv", source="accepted.mkv")
    await compressor.record(
        asset_id=copy.asset.id,
        source_asset_id=original.asset.id,
        preset="small",
        target_bytes=10 * BYTES_PER_MEGABYTE,
        actor_id=admin.id,
    )
    produced = await compressor.produced_for(copy.asset.id, viewer=admin)
    assert produced is not None
    assert produced.source_asset_id == original.asset.id
    assert produced.source_filename == "clip.mp4"


async def test_a_copy_whose_original_is_out_of_reach_says_it_is_a_copy_and_no_more(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    """The original is resolved through the access layer, never read straight out of the table.

    So a copy whose original the caller cannot reach (because it is gone, or because it was never
    theirs to see) says it is a copy and does not say what of. A feature nobody thought of as a
    way to read a filename is exactly how one leaks.
    """
    original = await add_file(managed, "private.mp4")
    copy = await add_file(managed, "private-10MB.mkv", source="accepted.mkv")
    await compressor.record(
        asset_id=copy.asset.id,
        source_asset_id=original.asset.id,
        preset="small",
        target_bytes=1,
        actor_id=admin.id,
    )
    # The original leaves the library. The record survives it, deliberately (the copy is still a
    # copy), and the link has nowhere to go.
    await compressor._db.execute(
        "DELETE FROM asset_locations WHERE asset_id = ?", (original.asset.id,)
    )

    produced = await compressor.produced_for(copy.asset.id, viewer=admin)
    assert produced is not None
    assert produced.source_asset_id is None
    assert produced.source_filename is None


async def test_asking_about_a_file_you_cannot_see_says_there_is_none(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
) -> None:
    ingested = await add_file(managed, "clip.mp4")
    with pytest.raises(NotFound):
        await compressor.produced_for(ingested.asset.id, viewer=guest)


# --- provenance, read from the original's end ---------------------------------------------------


async def test_a_file_nothing_was_made_from_lists_nothing(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    """The ordinary answer. Most files are nobody's original."""
    ingested = await add_file(managed, "clip.mp4")
    assert await compressor.made_from(ingested.asset.id, viewer=admin) == []


async def test_the_original_names_what_was_made_from_it_newest_first(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    clock: FakeClock,
) -> None:
    """Newest first, and the order is asserted from copies made at DIFFERENT times.

    Two rows written in the same second cannot tell an ordered query from an unordered one: they
    come back in whatever order the table happens to hold them, and both orders satisfy the test.
    The clock is moved between them so the two really do disagree.
    """
    original = await add_file(managed, "clip.mp4")
    older = await add_file(managed, "clip-older.mkv", source="accepted.mkv")
    await compressor.record(
        asset_id=older.asset.id,
        source_asset_id=original.asset.id,
        preset="small",
        target_bytes=BYTES_PER_MEGABYTE,
        actor_id=admin.id,
    )
    clock.advance(60)
    newer = await add_file(managed, "clip-newer.mov", source="accepted.mov")
    await compressor.record(
        asset_id=newer.asset.id,
        source_asset_id=original.asset.id,
        preset="small",
        target_bytes=BYTES_PER_MEGABYTE,
        actor_id=admin.id,
    )

    copies = await compressor.made_from(original.asset.id, viewer=admin)

    assert [copy.asset_id for copy in copies] == [newer.asset.id, older.asset.id]
    assert [copy.filename for copy in copies] == ["clip-newer.mov", "clip-older.mkv"]
    assert {copy.operation for copy in copies} == {"compress"}
    assert copies[0].produced_at > copies[1].produced_at


async def test_a_copy_the_account_may_not_see_is_left_out_rather_than_named(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    access: Repository,
    admin: Viewer,
    guest: Viewer,
) -> None:
    """The list is scoped, and the two viewers are made to disagree so the scoping is load-bearing.

    The guest is shared the ORIGINAL and not the copy. If the filter were removed the guest would
    be handed a filename for a file nobody ever shared with them, through a feature whose whole
    purpose is to say what happened to a file they are allowed to look at.
    """
    original = await add_file(managed, "holiday.mp4")
    copy = await add_file(managed, "holiday-small.mkv", source="accepted.mkv")
    await compressor.record(
        asset_id=copy.asset.id,
        source_asset_id=original.asset.id,
        preset="small",
        target_bytes=BYTES_PER_MEGABYTE,
        actor_id=admin.id,
    )
    await access.grant(ObjectType.ITEM, original.asset.id, guest.id, Effect.SHARE)

    # An admin sees it, so the row is really there and the guest's empty answer is the filter.
    assert [
        one.asset_id for one in await compressor.made_from(original.asset.id, viewer=admin)
    ] == [copy.asset.id]
    assert await compressor.made_from(original.asset.id, viewer=guest) == []


async def test_a_copy_that_has_left_the_library_is_left_out(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    """The record survives the file. A row with nowhere to point is not a line on a page."""
    original = await add_file(managed, "clip.mp4")
    copy = await add_file(managed, "clip-small.mkv", source="accepted.mkv")
    await compressor.record(
        asset_id=copy.asset.id,
        source_asset_id=original.asset.id,
        preset="small",
        target_bytes=BYTES_PER_MEGABYTE,
        actor_id=admin.id,
    )
    await compressor._db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (copy.asset.id,))

    assert await compressor.made_from(original.asset.id, viewer=admin) == []


async def test_asking_what_was_made_from_a_file_you_cannot_see_says_there_is_none(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
) -> None:
    """Refused before the table is read, exactly as the other direction is."""
    ingested = await add_file(managed, "clip.mp4")
    with pytest.raises(NotFound):
        await compressor.made_from(ingested.asset.id, viewer=guest)


async def test_the_list_stops_at_the_cap(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    clock: FakeClock,
) -> None:
    """A file somebody has trimmed thirty times is a list rather than a fact.

    One more copy than the cap, so the answer is the cap and not "however many there are".

    The second assertion is the one that matters, and it is deliberately not written in terms of
    the constant: an expectation that says `== MOST_COPIES` while the fixture makes `MOST_COPIES +
    1` moves with the code, so raising the cap raises the expectation too and the check can never
    fail. Fewer came back than were made is a claim the code cannot satisfy by agreeing with itself.
    """
    original = await add_file(managed, "clip.mp4")
    made = MOST_COPIES + 1
    for number in range(made):
        clock.advance(60)
        # Distinct BYTES, not just distinct names: the store is content-addressed, so the same
        # fixture copied under thirteen names is one asset with thirteen locations and the cap
        # would never be reached.
        copy = await add_file(
            managed, f"clip-{number}.png", data=png_bytes(bytes([0, number, 0, 0]))
        )
        await compressor.record(
            asset_id=copy.asset.id,
            source_asset_id=original.asset.id,
            preset="small",
            target_bytes=BYTES_PER_MEGABYTE,
            actor_id=admin.id,
        )

    copies = await compressor.made_from(original.asset.id, viewer=admin)

    assert len(copies) == MOST_COPIES
    assert len(copies) < made, "every copy came back, so nothing is capping the list"


# --- the sample ---------------------------------------------------------------------------------


async def test_a_sample_is_queued_for_a_video(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    job_queue: JobQueue,
) -> None:
    ingested = await add_file(managed, "clip.mp4")
    started = await compressor.sample(
        ingested.asset.id,
        CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL),
        viewer=admin,
    )
    job = await job_queue.get(started.job_id)
    assert job is not None
    assert job.type == "compress_sample"


async def test_a_sample_of_a_photograph_is_refused(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    ingested = await add_file(managed, "shot.jpg", source="accepted.jpg")
    with pytest.raises(Refused):
        await compressor.sample(
            ingested.asset.id,
            CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL),
            viewer=admin,
        )


async def test_a_sample_of_a_file_you_cannot_see_says_there_is_none(
    compressor: CompressService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
) -> None:
    ingested = await add_file(managed, "clip.mp4")
    with pytest.raises(NotFound):
        await compressor.sample(
            ingested.asset.id,
            CompressRequest(asset_ids=[ingested.asset.id], preset=Preset.SMALL),
            viewer=guest,
        )


# --- helpers ------------------------------------------------------------------------------------


async def _pretend_it_is_a_long_video(compressor: CompressService, asset_id: str) -> None:
    """Give an indexed clip the shape of a feature-length 4K video.

    The seeded fixtures are a few kilobytes, which is the right thing for a gate about bytes on
    disk and the wrong thing for arithmetic about running time. What the arithmetic reads is the
    indexed row, so the row is what is changed, not the file, which nothing here decodes.
    """
    await compressor._db.execute(
        "UPDATE assets SET width = 3840, height = 2160, duration_ms = 8040000, fps = 24,"
        " size_bytes = 32000000000 WHERE id = ?",
        (asset_id,),
    )


async def _asset_of(compressor: CompressService, asset_id: str) -> Any:
    """The asset row, unscoped, so a test can hand it back from a stubbed permission check."""
    from sift.kernel.content import asset_from_row

    row = await compressor._db.fetch_one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    assert row is not None
    return asset_from_row(row)


def _always(value: Any) -> Callable[..., Any]:
    async def answer(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return answer
