# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the editor panel is told, and what pressing the button queues.

The refusals are the interesting half again, and they fall into two kinds on purpose. Something
that should never have been offered (a user who cannot see the file, a user who is not
an admin, a crop asked of a video) is raised. Something somebody is in the middle of doing wrong
(a rectangle dragged off the edge, a clip that runs past the end) comes back as an answer with
a sentence, because it will be right again in a moment and a panel that throws at every frame of a
drag is unusable.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue
from sift.slices.media_edit import editor as editor_module
from sift.slices.media_edit import orientation as orientation_module
from sift.slices.media_edit.editor import EDIT, EditService, Shape, walk_steps
from sift.slices.media_edit.models import EditRequest, EditStep
from sift.slices.media_edit.operations import Operation, Turn
from sift.slices.media_edit.orientation import UPRIGHT, Orientation
from sift.slices.media_edit.refusals import NotAllowed, NotFound, Refused
from sift.slices.media_edit.settings import GIF_FORMAT_KEY
from sift.slices.media_edit.tests.conftest import Library
from sift.slices.media_edit.tuning import LONGEST_EXACT_CUT_MS, LONGEST_GIF_MS

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stub_handlers")]


def asking(**numbers: Any) -> EditRequest:
    """One operation, as the request carrying only it.

    Most of what is asserted below is about one thing being done to one file, which is still the
    ordinary case. The tests about several at once build their own list.
    """
    return EditRequest(steps=[EditStep(**numbers)])


async def _shape(
    database: Database,
    asset_id: str,
    *,
    width: int | None = 4000,
    height: int | None = 3000,
    duration_ms: int | None = None,
) -> None:
    """Give an indexed row the measurements the panel's arithmetic reads.

    Written straight onto the row rather than by probing, for the reason the compression tests give:
    the fixtures are a few kilobytes and every rule here is about a picture's size or a video's
    running time.
    """
    await database.execute(
        "UPDATE assets SET width = ?, height = ?, duration_ms = ? WHERE id = ?",
        (width, height, duration_ms, asset_id),
    )


async def _share_the_folder(database: Database, ingested: Any, viewer: Viewer) -> None:
    """Let one user see the folder a seeded file sits in."""
    await database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'folder', ?, ?, 'share', 0)",
        (new_id(), ingested.location.folder_id, viewer.id),
    )


# --- the shape of a request is settled before the file is looked at ------------------------------


@pytest.mark.parametrize(
    ("payload", "complaint"),
    [
        ({"operation": "crop", "left": 0, "top": 0, "width": 10}, "rectangle"),
        ({"operation": "resize"}, "width"),
        ({"operation": "rotate"}, "direction"),
        ({"operation": "trim", "start_ms": 0}, "length"),
        ({"operation": "clip", "start_ms": 0}, "length"),
    ],
)
def test_an_operation_without_the_numbers_it_runs_on_is_refused(
    payload: dict[str, Any], complaint: str
) -> None:
    """A crop with no rectangle is not a crop that might work on a bigger picture."""
    with pytest.raises(ValueError, match=complaint):
        EditStep(**payload)


# --- who may ask ---------------------------------------------------------------------------------


async def test_a_file_the_account_cannot_see_is_simply_not_there(
    editor: EditService, guest: Viewer
) -> None:
    """The same answer a missing file gets. Anything else confirms the file exists."""
    with pytest.raises(NotFound):
        await editor.verdict(
            "01HX0000000000000000000009",
            asking(operation=Operation.ROTATE, turn=Turn.RIGHT),
            viewer=guest,
        )


async def test_somebody_who_can_see_it_but_may_not_edit_it_is_told_that(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
    temp_db: Database,
) -> None:
    """The other answer, and it needs a share to reach at all.

    A folder nobody shared is simply absent to a guest, which makes "no grant" and "not an admin"
    identical from outside. Granting the folder first is what makes this test about the second one.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id)
    await _share_the_folder(temp_db, original, guest)
    with pytest.raises(NotAllowed, match="admin"):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.ROTATE, turn=Turn.RIGHT),
            viewer=guest,
        )


async def test_visibility_is_settled_before_permission(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    guest: Viewer,
    temp_db: Database,
) -> None:
    """Concealed from this user AND not an admin, and the answer is the one about the file.

    The other order tells a guest that a file they were not allowed to know about is there, by
    answering "only an admin can do that" instead of "there is no such file".
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (original.asset.id, guest.id, 1_700_000_000, 1_700_000_000),
    )
    with pytest.raises(NotFound):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.ROTATE, turn=Turn.RIGHT),
            viewer=guest,
        )


# --- what each kind of file is offered -------------------------------------------------------------


async def test_a_photograph_is_not_cut(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id)
    with pytest.raises(Refused, match="not cut"):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.TRIM, start_ms=0, duration_ms=1_000),
            viewer=admin,
        )


async def test_a_video_is_not_cropped(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)
    with pytest.raises(Refused, match="not cropped"):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.CROP, left=0, top=0, width=10, height=10),
            viewer=admin,
        )


async def test_a_gif_is_not_edited_at_all(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """And the sentence says why, because "cannot" without a reason reads as a bug."""
    original = await add_file(managed, "loop.gif", source="accepted.gif")
    await _shape(temp_db, original.asset.id, duration_ms=2_000)
    with pytest.raises(Refused, match="rebuilding every frame"):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.TRIM, start_ms=0, duration_ms=1_000),
            viewer=admin,
        )


@pytest.mark.usefixtures("managed")
async def test_there_are_only_three_kinds_of_file_for_the_editor_to_know_about(
    temp_db: Database,
) -> None:
    """Why the editor's fourth branch cannot be reached, written down rather than assumed.

    It is the database that guarantees it, not this feature, so the guarantee is asserted where
    it lives. A migration that widens this CHECK is a migration that has to revisit the editor.
    """
    with pytest.raises(Exception, match="CHECK constraint failed: media_type"):
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('x', 'y', 'hologram', 0)"
        )


async def test_a_picture_in_a_format_sift_cannot_write_is_refused(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The other side of the table that is checked as the module loads."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await temp_db.execute(
        "UPDATE assets SET mime = 'image/tiff' WHERE id = ?", (original.asset.id,)
    )
    with pytest.raises(Refused, match="save a picture in that format"):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.ROTATE, turn=Turn.RIGHT),
            viewer=admin,
        )


async def test_a_video_in_a_container_sift_cannot_cut_is_refused(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "clip.mp4")
    await temp_db.execute(
        "UPDATE assets SET mime = 'video/mpeg' WHERE id = ?", (original.asset.id,)
    )
    with pytest.raises(Refused, match="without re-encoding"):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.CLIP, start_ms=0, duration_ms=1_000),
            viewer=admin,
        )


# --- the numbers have to be true of the file ---------------------------------------------------------


async def test_a_rectangle_that_falls_off_the_edge_is_answered_not_raised(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CROP, left=700, top=0, width=200, height=100),
        viewer=admin,
    )
    assert answer.allowed is False
    assert "800 by 600" in (answer.reason or "")


async def test_a_rectangle_that_exactly_fills_the_picture_is_allowed(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The boundary, in the direction that matters: off by one here refuses a legitimate crop."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CROP, left=0, top=0, width=800, height=600),
        viewer=admin,
    )
    assert answer.allowed is True
    assert answer.output_filename == "photo-cropped-800x600.jpg"


async def test_making_a_picture_bigger_is_refused_and_says_why(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """Enlarging adds no detail and produces a bigger file that looks worse.

    Refused rather than warned about, unlike a compression target that cannot be reached: a target
    out of reach still has a best answer, and an enlargement has none.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.RESIZE, width=1600), viewer=admin
    )
    assert answer.allowed is False
    assert "800 pixels across" in (answer.reason or "")


async def test_making_a_picture_smaller_is_the_ordinary_case(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.RESIZE, width=400), viewer=admin
    )
    assert answer.allowed is True
    assert answer.output_filename == "photo-400px.jpg"


async def test_a_picture_nobody_has_measured_yet_says_to_come_back(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=None, height=None)
    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.RESIZE, width=400), viewer=admin
    )
    assert answer.allowed is False
    assert "not measured" in (answer.reason or "")


async def test_a_clip_that_runs_past_the_end_is_refused(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=50_000, duration_ms=30_000),
        viewer=admin,
    )
    assert answer.allowed is False
    assert "past the end" in (answer.reason or "")


async def test_a_clip_that_starts_after_the_end_is_refused(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=60_000, duration_ms=1_000),
        viewer=admin,
    )
    assert answer.allowed is False
    assert "after the end" in (answer.reason or "")


async def test_a_video_nobody_has_measured_yet_says_to_come_back(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=None)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=0, duration_ms=1_000),
        viewer=admin,
    )
    assert answer.allowed is False
    assert "not measured" in (answer.reason or "")


# --- what the answer says about what will come out --------------------------------------------------


async def test_a_clip_from_the_beginning_is_not_described_as_approximate(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """There is nothing to seek past, so there is nothing to land early on."""
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=0, duration_ms=15_000),
        viewer=admin,
    )
    assert answer.allowed is True
    assert answer.approximate_start is False
    assert answer.output_filename == "clip-from-0s.mp4"


async def test_a_trim_from_the_middle_says_it_may_begin_early(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The price of the cut being a COPY rather than an encode, said before it runs."""
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=600_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.TRIM, start_ms=90_000, duration_ms=15_000),
        viewer=admin,
    )
    assert answer.approximate_start is True


async def test_a_clip_from_the_middle_does_NOT_say_it_may_begin_early(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """It begins exactly where it was marked, and the panel must not say otherwise.

    A clip is re-encoded from the moment asked for: that is the whole difference between it and a
    trim, and it is why the loops screen asks for a clip rather than a trim. A panel telling people
    it might land a few seconds early anyway would take back the ONE thing choosing a clip buys
    them. The field is about a copied stream; only a trim is one.
    """
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=600_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=90_000, duration_ms=15_000),
        viewer=admin,
    )
    assert answer.approximate_start is False
    assert answer.output_filename == "clip-from-1m30s.mp4"


# --- how long a piece may be ----------------------------------------------------------------


async def test_a_clip_longer_than_the_ceiling_is_refused_and_offered_the_trim(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The encoder settings for a clip are chosen to be indistinguishable from the source, and
    every comment justifying that expense says the piece is short. Unchecked, a caller could ask
    for a near-lossless re-encode of a whole film."""
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=3_600_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=0, duration_ms=LONGEST_EXACT_CUT_MS + 1),
        viewer=admin,
    )
    assert answer.allowed is False
    assert "at most" in (answer.reason or "")
    # The refusal names the way to take a longer piece, because there is one.
    assert "Trim" in (answer.reason or "")


async def test_a_TRIM_of_a_whole_film_is_not_refused(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The ceiling is about re-encoding, and a trim does not. Copying the packets already in the
    file costs almost nothing on a two-hour video, which is the whole reason the two verbs differ:
    a ceiling that caught both would have taken the cheap one away for the expensive one's sake."""
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=3_600_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.TRIM, start_ms=0, duration_ms=3_500_000),
        viewer=admin,
    )
    assert answer.allowed is True


# --- a GIF ------------------------------------------------------------------------------


def _writing(service: EditService, gif_format: str) -> None:
    """Point one service at a named GIF format.

    Both tests below are ABOUT the gif (its extension, and the ceiling its frame-per-frame
    storage forces), so they say gif rather than leaning on whichever format happens to ship as
    the default: a test whose subject is one format should not be able to be changed by a setting
    about another.
    """

    async def stored(key: str) -> object:
        return gif_format if key == GIF_FORMAT_KEY else None

    # The fixture's own documented seam: it answers None for everything so a test can say more.
    service._read_app_setting = stored


async def test_a_gif_comes_out_as_a_gif_whatever_the_source_was(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The one operation whose output format is not the input's. Every other one keeps the file's
    own container or the photograph's own format; this one is a destination somebody asked for."""
    _writing(editor, "gif")
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.GIF, start_ms=12_000, duration_ms=4_000),
        viewer=admin,
    )
    assert answer.allowed is True
    assert answer.output_filename == "clip-gif-from-12s.gif"
    # Rebuilt frame by frame from the moment asked for, so it begins exactly there.
    assert answer.approximate_start is False


async def test_a_gif_longer_than_the_ceiling_is_refused(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """A GIF stores every frame whole, so its size grows with the NUMBER of frames rather than with
    what changes between them. The limit is what the format is, not a preference."""
    _writing(editor, "gif")
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=600_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.GIF, start_ms=0, duration_ms=LONGEST_GIF_MS["gif"] + 1),
        viewer=admin,
    )
    assert answer.allowed is False
    assert "GIF can be at most 15 seconds" in (answer.reason or "")


async def test_a_gif_is_named_after_the_format_it_is_about_to_be(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """A stem saying `gif` whatever was about to be written would offer an AVIF as
    `clip-gif-from-30s.avif`, on the panel, before anything was saved, which is the one screen
    the format is resolved early to keep honest."""
    _writing(editor, "avif")
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.GIF, start_ms=30_000, duration_ms=4_000),
        viewer=admin,
    )
    assert answer.allowed is True
    assert answer.output_filename == "clip-avif-from-30s.avif"


def test_a_ceiling_names_the_format_the_way_the_menu_names_it() -> None:
    """Built out of the stored key (upper-cased, with "A" in front of it), the sentence would read
    "A AVIF can be at most 60 seconds", and "A WEBP" beside a menu that says WebP. Both the
    article and the spelling come from one table."""
    for gif_format, opening in (
        ("gif", "A GIF can be at most 15 seconds."),
        ("webp", "A WebP can be at most 60 seconds."),
        ("avif", "An AVIF can be at most 60 seconds."),
    ):
        said = editor_module._too_long(Operation.GIF, LONGEST_GIF_MS[gif_format] + 1, gif_format)
        assert said is not None
        assert said.startswith(opening)


async def test_a_gif_of_a_gif_is_refused(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    """A GIF is already one, and Sift cannot read one back frame by frame anyway, which is the
    same reason it will not trim one."""
    original = await add_file(managed, "already.gif", source="accepted.gif")
    with pytest.raises(Refused):
        await editor.verdict(
            original.asset.id,
            asking(operation=Operation.GIF, start_ms=0, duration_ms=2_000),
            viewer=admin,
        )


async def test_a_photograph_off_a_phone_says_it_is_coming_back_as_a_jpeg(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """Said before anything runs, not discovered afterwards by looking at the file that appeared."""
    original = await add_file(managed, "phone.heic", source="accepted.heic")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.ROTATE, turn=Turn.LEFT), viewer=admin
    )
    assert answer.allowed is True
    assert answer.converted_from == "heic"
    assert answer.output_filename == "phone-rotated-left.jpg"


async def test_a_picture_keeping_its_own_format_says_nothing_about_converting(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "shot.png", source="accepted.png")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.ROTATE, turn=Turn.RIGHT), viewer=admin
    )
    assert answer.converted_from is None
    assert answer.lossy is False


async def test_saving_a_jpeg_says_it_costs_a_generation(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.ROTATE, turn=Turn.RIGHT), viewer=admin
    )
    assert answer.lossy is True


# --- the read-only root ------------------------------------------------------------------------------


async def test_a_folder_handed_over_read_only_is_not_written_beside(
    editor: EditService,
    read_only: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The same question moving a file asks, asked of the same function."""
    original = await add_file(read_only, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.RESIZE, width=400), viewer=admin
    )
    assert answer.allowed is False
    assert answer.reason


async def test_a_copy_that_would_land_on_an_existing_name_is_refused_before_it_is_queued(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The fault this check exists for, and it is about WHERE the refusal happens rather than
    whether it happens.

    Saving the same cut of the same moment twice produces the same name, and the write seam
    refuses to overwrite, correctly. Said only by the job, the refusal would reach nobody: the
    route that queues the work would answer 200, the screen would say the file was being saved,
    and the job would die out of sight three attempts later.

    So the preflight is asked, and it says no, with the name in the sentence.
    """
    original = await add_file(managed, "video.mkv", source="accepted.mkv")
    await _shape(temp_db, original.asset.id, duration_ms=30_000)

    request = asking(operation=Operation.CLIP, start_ms=1_000, duration_ms=2_000)
    first = await editor.verdict(original.asset.id, request, viewer=admin)
    assert first.allowed is True
    assert first.output_filename

    # The copy, made by hand rather than by running the job: what is being tested is the ANSWER,
    # and standing up an encode to produce a file whose only property that matters is its name
    # would be a slower test of something else.
    beside = Path(managed.root.abs_path) / first.output_filename
    beside.write_bytes(b"something somebody already has")

    again = await editor.verdict(original.asset.id, request, viewer=admin)
    assert again.allowed is False
    assert again.reason and first.output_filename in again.reason


async def test_a_cut_into_a_read_only_root_is_refused_too(
    editor: EditService,
    read_only: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(read_only, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)
    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=0, duration_ms=1_000),
        viewer=admin,
    )
    assert answer.allowed is False
    assert answer.reason


# --- starting it ---------------------------------------------------------------------------------------


async def test_starting_queues_one_job_carrying_everything_it_needs(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    started = await editor.start(
        original.asset.id,
        asking(operation=Operation.CROP, left=10, top=20, width=300, height=200),
        viewer=admin,
    )

    assert started.output_filename == "photo-cropped-300x200.jpg"
    claimed = await job_queue.claim("test")
    assert claimed is not None and claimed.type == EDIT
    assert claimed.payload["steps"] == [
        {
            "operation": "crop",
            "left": 10,
            "top": 20,
            "width": 300,
            "height": 200,
            "turn": None,
            "start_ms": None,
            "duration_ms": None,
        }
    ]
    assert claimed.payload["filename"] == "photo-cropped-300x200.jpg"
    assert claimed.payload["actor_id"] == admin.id


async def test_the_rules_are_asked_again_when_the_button_is_pressed(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The panel is where a request comes from, never where a rule lives.

    Everything between the panel drawing and the button being pressed can have changed, and a
    client sending numbers the panel would have refused is the ordinary case rather than the
    suspicious one.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    with pytest.raises(Refused, match="pixels across"):
        await editor.start(
            original.asset.id, asking(operation=Operation.RESIZE, width=4000), viewer=admin
        )


async def test_nothing_is_queued_when_the_folder_cannot_be_written_to(
    editor: EditService,
    read_only: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    original = await add_file(read_only, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    with pytest.raises(Refused):
        await editor.start(
            original.asset.id, asking(operation=Operation.RESIZE, width=400), viewer=admin
        )
    assert await job_queue.claim("test") is None


async def test_settling_on_its_own_answers_the_same_way_the_verdict_does(
    editor: EditService, guest: Viewer
) -> None:
    """The call the routes make before they look at the body."""
    with pytest.raises(NotFound):
        await editor.settle("01HX0000000000000000000009", viewer=guest)


# --- the rectangle that is really cut ---------------------------------------------------------------


async def test_the_rectangle_reported_back_is_the_one_that_will_be_cut(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The screen must not promise 605 tall when 604 lands on disk.

    A crop lands on the picture's colour blocks whether anybody rounds it or not: ffmpeg does it
    silently if nothing else has. So it is done here, before the answer is built, and the answer
    carries the rectangle rather than echoing the one that was sent.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=1600, height=1200)

    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CROP, left=401, top=301, width=785, height=605),
        viewer=admin,
    )

    assert answer.allowed is True
    assert (answer.left, answer.top, answer.width, answer.height) == (400, 300, 784, 604)
    assert answer.output_filename == "photo-cropped-784x604.jpg"


async def test_the_job_is_queued_the_rectangle_it_was_promised(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    """Queueing the numbers that arrived would make a file whose size contradicts its own name."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=1600, height=1200)

    started = await editor.start(
        original.asset.id,
        asking(operation=Operation.CROP, left=401, top=301, width=785, height=605),
        viewer=admin,
    )

    claimed = await job_queue.claim("test")
    assert claimed is not None
    assert claimed.payload["steps"][0]["width"] == 784
    assert claimed.payload["steps"][0]["height"] == 604
    assert claimed.payload["filename"] == started.output_filename == "photo-cropped-784x604.jpg"


async def test_a_rectangle_of_barely_anything_is_refused_rather_than_cut_to_nothing(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """One pixel across snaps to none across, and ffmpeg's answer to that is not a picture."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=1600, height=1200)

    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CROP, left=10, top=10, width=1, height=1),
        viewer=admin,
    )

    assert answer.allowed is False
    assert "too small" in (answer.reason or "")


async def test_nothing_but_a_crop_is_touched_on_the_way_through(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """A resize of an odd width is an odd width. The rounding is about a rectangle, not about sizes."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=1600, height=1200)

    answer = await editor.verdict(
        original.asset.id, asking(operation=Operation.RESIZE, width=333), viewer=admin
    )

    assert answer.output_filename == "photo-333px.jpg"
    assert answer.width is None


# --- one Save, several operations ------------------------------------------------------------------


def _compound(*steps: EditStep, filename: str | None = None) -> EditRequest:
    return EditRequest(steps=list(steps), filename=filename)


async def test_a_crop_and_a_turn_together_are_allowed_and_named_once(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The whole reason the request is a list: two operations, one copy.

    Asked one at a time the first one has already written a file nobody wanted by the time the
    second is sent.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)

    answer = await editor.verdict(
        original.asset.id,
        _compound(
            EditStep(operation=Operation.CROP, left=0, top=0, width=400, height=300),
            EditStep(operation=Operation.ROTATE, turn=Turn.RIGHT),
        ),
        viewer=admin,
    )

    assert answer.allowed is True
    assert answer.output_filename == "photo-edited.jpg"
    # The turn is last, so the copy comes out the other way round from the crop that fed it.
    assert (answer.result_width, answer.result_height) == (300, 400)


async def test_a_list_whose_later_step_is_refused_refuses_the_whole_thing(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    """A crop to 400 across and then a resize to 800 is an enlargement, and only the order says so.

    Checked against the original photograph both steps pass on their own. Nothing may be written:
    half of this list is not a smaller version of it, it is a picture nobody asked for.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)
    asked = _compound(
        EditStep(operation=Operation.CROP, left=0, top=0, width=400, height=300),
        EditStep(operation=Operation.RESIZE, width=800),
    )

    answer = await editor.verdict(original.asset.id, asked, viewer=admin)
    assert answer.allowed is False
    assert "only 400 pixels across" in (answer.reason or "")

    with pytest.raises(Refused, match="400 pixels across"):
        await editor.start(original.asset.id, asked, viewer=admin)
    assert await job_queue.claim("test") is None, "a refused list must queue nothing at all"


def test_a_cut_cannot_be_combined_with_anything() -> None:
    """Not a rule about what kind of file it is: a cut copies packets and a filter needs a frame."""
    with pytest.raises(ValueError, match="on its own"):
        EditRequest(
            steps=[
                EditStep(operation=Operation.TRIM, start_ms=0, duration_ms=1_000),
                EditStep(operation=Operation.ROTATE, turn=Turn.RIGHT),
            ]
        )


def test_a_save_carrying_nothing_is_refused() -> None:
    with pytest.raises(ValueError):
        EditRequest(steps=[])


# --- the name on the copy --------------------------------------------------------------------------


async def test_a_typed_name_is_used_and_keeps_the_extension_the_copy_really_has(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The stem is the person's and the extension is not.

    A perfectly good picture named `.txt` cannot be opened by name, and what it comes back as is
    decided by what it was encoded as rather than by anybody typing.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)

    answer = await editor.verdict(
        original.asset.id,
        _compound(EditStep(operation=Operation.ROTATE, turn=Turn.LEFT), filename="the good one"),
        viewer=admin,
    )

    assert answer.output_filename == "the good one.jpg"


@pytest.mark.parametrize(
    ("typed", "complaint"),
    [
        ("holiday/clip", "slash"),
        ("..", "not a file name"),
        ("   ", "Give the file a name"),
        # Short enough to be a name on its own and too long once the extension is on it, which
        # is why the whole thing is checked as well as the stem.
        ("a" * 199, "too long"),
    ],
)
async def test_a_name_that_is_not_a_name_is_refused_in_the_same_words_renaming_uses(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    typed: str,
    complaint: str,
) -> None:
    """One rule, in one place, so a name this screen takes is a name the rename box takes."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)

    with pytest.raises(Refused, match=complaint):
        await editor.verdict(
            original.asset.id,
            _compound(EditStep(operation=Operation.ROTATE, turn=Turn.LEFT), filename=typed),
            viewer=admin,
        )


async def test_a_typed_name_is_used_for_a_cut_as_well(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=120_000)

    answer = await editor.verdict(
        original.asset.id,
        _compound(
            EditStep(operation=Operation.CLIP, start_ms=1_000, duration_ms=5_000),
            filename="the bit I wanted",
        ),
        viewer=admin,
    )

    assert answer.output_filename == "the bit I wanted.mp4"


# --- a photograph the camera turned ------------------------------------------------------------------


async def test_the_rectangle_is_measured_against_the_picture_as_it_is_seen(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tall rectangle over a photograph a phone turned, which is stored lying on its side.

    Probing records the size as seen (3000 by 4000 for a 4000 by 3000 file with a quarter-turn
    note), so the note must not be applied to it again: measured against the stored size this
    falls off the edge and is refused, with the person looking at a rectangle that is plainly
    inside the picture.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=3000, height=4000)
    monkeypatch.setattr(EditService, "orientation_of", _always(Orientation(quarter_turns=1)))

    answer = await editor.verdict(
        original.asset.id,
        asking(operation=Operation.CROP, left=0, top=0, width=3000, height=4000),
        viewer=admin,
    )

    assert answer.allowed is True
    assert (answer.frame_width, answer.frame_height) == (3000, 4000)


async def test_the_note_is_carried_to_the_job_rather_than_read_again(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read twice, the picture somebody aimed at and the picture that gets cut can differ."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=4000, height=3000)
    monkeypatch.setattr(
        EditService, "orientation_of", _always(Orientation(quarter_turns=3, mirrored=True))
    )

    await editor.start(
        original.asset.id,
        asking(operation=Operation.CROP, left=0, top=0, width=300, height=400),
        viewer=admin,
    )

    claimed = await job_queue.claim("test")
    assert claimed is not None
    assert claimed.payload["quarter_turns"] == 3
    assert claimed.payload["mirrored"] is True


def _always(answer: Orientation) -> Callable[..., Any]:
    """A stand-in for the one thing in the editor that opens a file."""

    async def read(self: EditService, asset_id: str) -> Orientation:
        return answer

    return read


# --- the size the panel aims at ---------------------------------------------------------------------


async def test_the_frame_is_the_picture_as_it_is_seen(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked once when the editor opens, because a rectangle needs something to be dragged over.

    The recorded size, which probing already turned by the note: turned again it would be the size
    the picture is stored at."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=3000, height=4000)
    monkeypatch.setattr(EditService, "orientation_of", _always(Orientation(quarter_turns=1)))

    seen = await editor.frame_of(original.asset.id, viewer=admin)

    assert (seen.width, seen.height) == (3000, 4000)


async def test_a_video_is_not_opened_to_ask_which_way_up_it_is(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A video's note is applied by the decoder on the way past, so its recorded size is the size it
    plays at, and reading one would be a frame decoded out of a two-hour video for nothing."""
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, width=1920, height=1080, duration_ms=60_000)

    async def refuse(self: EditService, asset_id: str) -> Orientation:
        raise AssertionError("a video must not be opened to read a note")

    monkeypatch.setattr(EditService, "orientation_of", refuse)

    seen = await editor.frame_of(original.asset.id, viewer=admin)

    assert (seen.width, seen.height) == (1920, 1080)


async def test_the_note_is_read_once_and_then_remembered(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file's identity in Sift IS its bytes, so a file whose note changed is a different file.

    Which makes this a cache with no staleness question attached, and it has to be one, because
    the alternative is opening the photograph again on every frame of a drag.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=4000, height=3000)
    reads = 0

    async def count(path: Any, *, settings: Any) -> Orientation:
        nonlocal reads
        reads += 1
        return Orientation(quarter_turns=2)

    monkeypatch.setattr(orientation_module, "read_orientation", count)

    first = await editor.orientation_of(original.asset.id)
    second = await editor.orientation_of(original.asset.id)

    assert first == second == Orientation(quarter_turns=2)
    assert reads == 1


async def test_a_photograph_that_cannot_be_opened_is_treated_as_upright(
    editor: EditService,
) -> None:
    """Which is what every other part of Sift already assumes about every file.

    Refusing instead would make the editor worse on the photographs that were never affected, to
    say nothing useful about the one that was.
    """
    assert await editor.orientation_of("01HX0000000000000000000009") == UPRIGHT


def test_a_half_turn_and_a_mirror_leave_the_picture_the_size_they_found_it() -> None:
    """Only a quarter turn swaps the sides, and everything downstream that reports a size follows."""
    shape = Shape(800, 600)
    for turn in (Turn.HALF, Turn.MIRROR, Turn.FLIP):
        _, after = walk_steps([EditStep(operation=Operation.ROTATE, turn=turn)], shape)
        assert after == shape
    _, sideways = walk_steps([EditStep(operation=Operation.ROTATE, turn=Turn.RIGHT)], shape)
    assert sideways == Shape(600, 800)


async def test_what_is_remembered_is_bounded(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bound on memory, not on staleness: a note cannot change under an asset id.

    Somebody who has opened the editor on a great many photographs without restarting is the only
    person this is for, and what it does when it fills is start again rather than grow.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await _shape(temp_db, original.asset.id, width=800, height=600)

    async def a_turn(path: Any, *, settings: Any) -> Orientation:
        return Orientation(quarter_turns=1)

    monkeypatch.setattr(orientation_module, "read_orientation", a_turn)
    monkeypatch.setattr(editor_module, "_REMEMBERED_ORIENTATIONS", 1)

    # The first fills it; the second finds it full and starts again, and still answers correctly.
    assert await editor.orientation_of(original.asset.id) == Orientation(quarter_turns=1)
    editor._orientations.clear()
    editor._orientations["something else"] = UPRIGHT
    assert await editor.orientation_of(original.asset.id) == Orientation(quarter_turns=1)
    assert "something else" not in editor._orientations


async def test_starting_a_cut_does_not_open_the_video_to_ask_which_way_up_it_is(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cut copies the packets that are already there, so the note travels with them untouched.

    Reading one would be a frame decoded out of a two-hour video, on the request somebody is waiting
    for, to answer a question the job never asks.
    """
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=120_000)

    async def refuse(self: EditService, asset_id: str) -> Orientation:
        raise AssertionError("a cut must not open the file to read a note")

    monkeypatch.setattr(EditService, "orientation_of", refuse)

    await editor.start(
        original.asset.id,
        asking(operation=Operation.TRIM, start_ms=0, duration_ms=5_000),
        viewer=admin,
    )

    claimed = await job_queue.claim("test")
    assert claimed is not None
    assert claimed.payload["quarter_turns"] == 0
    assert claimed.payload["mirrored"] is False


def test_a_typed_name_longer_than_a_name_can_be_is_turned_away_by_the_shape() -> None:
    """A megabyte of text should not reach a rule written to answer a person typing."""
    with pytest.raises(ValueError):
        EditRequest(
            steps=[EditStep(operation=Operation.ROTATE, turn=Turn.RIGHT)],
            filename="a" * 5_000,
        )


async def test_asking_for_a_loop_carries_the_INTENT_and_never_a_job_name(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    """ "Save as Loop" cuts a clip and then marks the file it produced.

    The flag travels with the edit rather than being a second call from the screen, because the
    produced file does not exist when the button is pressed: it is a background encode whose id
    nobody knows yet. And it is a BOOLEAN: a request that could name the job to run would be a
    request choosing what the server executes, and this slice would have to know what a loop is.
    """
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)

    await editor.start(
        original.asset.id,
        EditRequest(
            steps=[EditStep(operation=Operation.CLIP, start_ms=0, duration_ms=4_000)],
            as_loop=True,
        ),
        viewer=admin,
    )

    claimed = await job_queue.claim("test")
    assert claimed is not None
    assert claimed.payload["as_loop"] is True
    # Nothing in the payload names a handler. What the flag MEANS is decided outside this slice.
    assert "loop" not in str(claimed.payload).replace("as_loop", "")


async def test_an_ordinary_edit_asks_for_no_loop(
    editor: EditService,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    """The default, and the one that matters: saving a copy out of the editor makes a file and puts
    nothing on the Loops screen. A flag that defaulted the other way would fill that screen with a
    row for every crop somebody ever saved."""
    original = await add_file(managed, "clip.mp4")
    await _shape(temp_db, original.asset.id, duration_ms=60_000)

    await editor.start(
        original.asset.id,
        asking(operation=Operation.CLIP, start_ms=0, duration_ms=4_000),
        viewer=admin,
    )

    claimed = await job_queue.claim("test")
    assert claimed is not None
    assert claimed.payload["as_loop"] is False
