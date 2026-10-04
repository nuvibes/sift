# SPDX-License-Identifier: AGPL-3.0-or-later
"""Putting a file Sift produced into a library folder, beside the one it came from.

The third thing this service does, after renaming and moving, and the one with the widest blast
radius: it is how a feature that PRODUCES a file gets it into somebody's library, and it is the
only way one may. What is proven here is that it refuses everything a rename refuses, and that it
cannot land on top of anything, which it inherits from claim-then-move rather than re-implements.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.ingress import ALLOWED_EXTENSIONS
from sift.slices.organize.service import (
    WORKING_SUFFIX,
    NotFound,
    Organizer,
    OrganizeRefused,
    working_name,
)
from sift.slices.organize.tests.conftest import Library

pytestmark = pytest.mark.anyio


async def test_a_scratch_name_is_one_the_folder_scan_walks_past() -> None:
    """A half-written video carrying a real extension is one the scan tries to take in.

    Asserted against the walk's own extension list rather than a hand-written one, so this fails if
    either side changes and the two stop agreeing.
    """
    scratch = Path(working_name("clip-10MB.mp4", "01HX0000000000000000000101"))
    assert scratch.suffix.lower() not in ALLOWED_EXTENSIONS
    assert scratch.name.endswith(WORKING_SUFFIX)


async def test_two_at_once_build_in_different_files() -> None:
    """Two runs producing the same output name must not write into each other's scratch file."""
    first = working_name("clip-10MB.mp4", "01HX0000000000000000000101")
    second = working_name("clip-10MB.mp4", "01HX0000000000000000000102")
    assert first != second


async def test_staging_says_where_to_build_and_what_it_will_be_called(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    ingested = await add_file(managed, "clip.mp4")

    staged = await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)

    assert staged.filename == "clip-10MB.mp4"
    assert staged.rel_path == "clip-10MB.mp4"
    # Built in the folder it will land in, so putting it in place is a rename rather than a copy.
    assert staged.working.parent == managed.path
    assert not staged.working.exists()


async def test_staging_puts_the_scratch_file_beside_the_source_in_a_subfolder(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    ingested = await add_file(managed, "2024/clip.mp4")

    staged = await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)

    assert staged.rel_path == "2024/clip-10MB.mp4"
    assert staged.working.parent == managed.path / "2024"


async def test_staging_is_refused_on_a_read_only_folder(
    organizer: Organizer, read_only: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    """Writing a NEW file into a folder is as much a write as replacing one."""
    ingested = await add_file(read_only, "clip.mp4")

    with pytest.raises(OrganizeRefused, match="read-only"):
        await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)


async def test_staging_is_refused_for_a_guest(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], guest: Viewer
) -> None:
    ingested = await add_file(managed, "clip.mp4")

    with pytest.raises(NotFound):
        await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=guest)


async def test_staging_is_refused_for_a_file_that_is_not_there(
    organizer: Organizer, admin: Viewer
) -> None:
    with pytest.raises(NotFound):
        await organizer.stage_beside(
            "01HX0000000000000000000099", filename="clip-10MB.mp4", actor=admin
        )


async def test_staging_refuses_a_name_that_is_a_path(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    """The same check a rename box goes through. A slash makes it a path, not a name."""
    ingested = await add_file(managed, "clip.mp4")

    with pytest.raises(OrganizeRefused, match="slash"):
        await organizer.stage_beside(ingested.asset.id, filename="../outside/clip.mp4", actor=admin)


async def test_staging_refuses_a_name_that_is_already_taken(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    """Told before four minutes of encoding rather than after. `keep` settles it for real."""
    ingested = await add_file(managed, "clip.mp4")
    (managed.path / "clip-10MB.mp4").write_bytes(b"somebody else's file")

    with pytest.raises(OrganizeRefused, match="already something called"):
        await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)


async def test_asking_whether_a_name_is_taken_answers_without_staging_anything(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    """Both answers, and the promise that makes it safe to ask while somebody is still dragging: it
    produces nothing.

    It exists because a route that only QUEUES work answers 200: the screen says the file is being
    saved and the job then dies out of sight because the name was taken, with nothing on screen ever
    saying so. Asked early, the person is told before they wait.
    """
    ingested = await add_file(managed, "clip.mp4")
    before = sorted(managed.path.rglob("*"))

    assert (
        await organizer.name_taken_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)
        is False
    )

    (managed.path / "clip-10MB.mp4").write_bytes(b"somebody else's file")
    assert (
        await organizer.name_taken_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)
        is True
    )
    assert sorted(managed.path.rglob("*")) == sorted([*before, managed.path / "clip-10MB.mp4"])


async def test_keeping_gives_the_finished_file_its_real_name(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    ingested = await add_file(managed, "clip.mp4")
    staged = await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)
    staged.working.write_bytes(b"the finished bytes")

    placed = await organizer.keep(staged)

    assert placed.path == managed.path / "clip-10MB.mp4"
    assert placed.path.read_bytes() == b"the finished bytes"
    assert not staged.working.exists()


async def test_a_kept_file_carries_no_location_and_the_file_it_came_from_keeps_its_own(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    """A produced file is a copy Sift makes, so it keeps no place: ffmpeg carries a MOV's location
    tag into a trim of it. The source is not touched."""
    from PIL import Image

    ingested = await add_file(managed, "clip.mp4")
    source = managed.path / "clip.mp4"
    before = source.read_bytes()
    staged = await organizer.stage_beside(ingested.asset.id, filename="still.jpg", actor=admin)
    exif = Image.Exif()
    exif[0x0110] = "Model Q"
    exif.get_ifd(0x8825)[2] = (48.0, 51.0, 29.17)
    Image.new("RGB", (16, 12)).save(staged.working, format="JPEG", exif=exif)

    placed = await organizer.keep(staged)

    with Image.open(placed.path) as opened:
        assert 0x8825 not in opened.getexif()
        assert opened.getexif()[0x0110] == "Model Q"
    assert source.read_bytes() == before
    assert sorted(one.name for one in managed.path.iterdir() if one.name.startswith(".")) == []


async def test_keeping_cannot_land_on_top_of_something(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    """The property the whole design rests on, and it is claim-then-move's rather than this code's.

    Something arriving in the folder WHILE the encode ran is the case that matters: staging checked
    and found the name free, and by the time the file is finished it is not.
    """
    ingested = await add_file(managed, "clip.mp4")
    staged = await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)
    staged.working.write_bytes(b"the finished bytes")
    (managed.path / "clip-10MB.mp4").write_bytes(b"arrived while the encode ran")

    with pytest.raises(OrganizeRefused, match="already something called"):
        await organizer.keep(staged)

    assert (managed.path / "clip-10MB.mp4").read_bytes() == b"arrived while the encode ran"


async def test_keeping_is_refused_once_the_folder_stops_being_writable(
    organizer: Organizer,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked again at the end, not only at the start: minutes pass between the two."""
    ingested = await add_file(managed, "clip.mp4")
    staged = await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)
    staged.working.write_bytes(b"the finished bytes")
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)

    with pytest.raises(OrganizeRefused, match="not allowed to write"):
        await organizer.keep(staged)


async def test_discarding_clears_the_scratch_file(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    ingested = await add_file(managed, "clip.mp4")
    staged = await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)
    staged.working.write_bytes(b"half a video")

    await organizer.discard(staged)

    assert not staged.working.exists()


async def test_discarding_when_there_is_nothing_to_discard_is_silent(
    organizer: Organizer, managed: Library, add_file: Callable[..., Any], admin: Viewer
) -> None:
    """The ordinary case: a failure before ffmpeg wrote anything."""
    ingested = await add_file(managed, "clip.mp4")
    staged = await organizer.stage_beside(ingested.asset.id, filename="clip-10MB.mp4", actor=admin)

    await organizer.discard(staged)


async def test_writability_answers_the_same_question_the_menu_asks(
    organizer: Organizer,
    managed: Library,
    read_only: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    guest: Viewer,
) -> None:
    """So what a panel offers and what the server would do cannot disagree."""
    here = await add_file(managed, "clip.mp4")
    # A different file, not another copy of the same one: identical bytes are ONE asset in two
    # folders, and anything acting on a file refuses that rather than guessing which was meant.
    there = await add_file(read_only, "other.mkv", source="accepted.mkv")

    assert await organizer.writable_beside(here.asset.id, actor=admin) is None

    refusal = await organizer.writable_beside(there.asset.id, actor=admin)
    assert refusal is not None
    assert refusal == (await organizer.organizability(there.asset.id, actor=admin)).reason

    # A guest is told there is no such file rather than being handed a reason, because the reason
    # would confirm it is there. Raised rather than returned, which is what `organizability` does
    # and is the whole point of asking the same function.
    with pytest.raises(NotFound):
        await organizer.writable_beside(here.asset.id, actor=guest)


async def test_producing_beside_an_unstorable_path_is_refused(
    organizer: Organizer,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Any,
) -> None:
    """A row written before the path was checked on the way in: a restored backup, an older
    version. The produced file inherits the source's folder, so a stored path that walks out of the
    root produces one that does too, and it is refused before anything is written.

    The same case a rename has, and refused in the same words, because it is the same disagreement:
    the name check and the column's own check are two pieces of code, and this is where they meet.
    """
    added = await add_file(managed, "clip.mp4")
    await temp_db.execute(
        "UPDATE asset_locations SET rel_path = ? WHERE id = ?",
        ("../escape.mp4", added.location.id),
    )

    with pytest.raises(OrganizeRefused, match="isn't a name Sift can store"):
        await organizer.stage_beside(added.asset.id, filename="escape-10MB.mp4", actor=admin)

    assert not (managed.path.parent / "escape-10MB.mp4").exists()
