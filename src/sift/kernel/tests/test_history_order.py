# SPDX-License-Identifier: AGPL-3.0-or-later
"""The order a History thread reads in: by moment, a tie by cause before effect, one act one line.

- A download that brought a file in IS its arrival, so it is one line naming the file, and it
  comes first; a later download of the same file is an act of its own.
- A file's housekeeping is one line per sitting, dated at its first step, its steps in time order.
- A rename Sift made on its own says why.
"""

from __future__ import annotations

from typing import cast

import pytest

import sift.slices.download.schema
import sift.slices.organize.schema
import sift.slices.semantic.schema  # noqa: F401
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history import (
    CAUSE_ORDER,
    CAUSE_RANK,
    EPISODE_GAP,
    KINDS,
    Actor,
    Event,
    _move_events,
    _one_line_per_download,
    _one_processed_line,
    _Who,
    history_of_asset,
    ordered,
)
from sift.kernel.access.sentences import RENAMED_FOR_TOOL_ID
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.kernel.tests.history_helpers import ADDED_AT, ASSET, LIBRARY, make_file
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


def _line(kind: str, when: int | None, words: str = "", *, routine: bool = False) -> Event:
    return Event(
        at=when,
        actor=Actor.SIFT,
        actor_name="Sift",
        kind=kind,
        pieces=say.said(words or kind),
        routine=routine,
    )


# --- the order ------------------------------------------------------------------------------------


def test_every_kind_has_one_place_in_the_order() -> None:
    placed = [kind for kinds in CAUSE_ORDER for kind in kinds]
    assert sorted(placed) == sorted(KINDS)
    assert len(placed) == len(set(placed))


def test_acts_in_one_second_read_cause_before_effect_and_the_timeless_first() -> None:
    """Written in the wrong order on purpose: a sort by time alone would keep it that way."""
    events = [
        _line("undone", 5),
        _line("ready", 5),
        _line("named", 5),
        _line("renamed", 5),
        _line("filed", 5),
        _line("added", 5),
        _line("downloaded", 5),
        _line("face_run", 4),
        _line("tagged", None),
    ]
    assert [one.kind for one in ordered(events)] == [
        "tagged",
        "face_run",
        "downloaded",
        "added",
        "filed",
        "renamed",
        "named",
        "ready",
        "undone",
    ]
    assert CAUSE_RANK["confirmed"] > CAUSE_RANK["matched"] > CAUSE_RANK["face_run"]


# --- the arrival is the download ------------------------------------------------------------------


def test_the_download_that_brought_a_file_in_is_its_arrival_and_a_second_one_stands() -> None:
    added = _line("added", 10, "Sift added this file to the library as image.png")
    first = Event(
        at=10,
        actor=Actor.SIFT,
        actor_name="Sift",
        kind="downloaded",
        pieces=say.downloaded("Discord", None, "image.png"),
        landed=True,
    )
    again = _line("downloaded", 900, "Sift downloaded this file from Discord")

    kept = _one_line_per_download([added, first, again])

    assert kept == [first, again]
    assert first.what == "Sift downloaded this file from Discord as image.png"
    # A download that found the file already here says nothing about its arrival.
    assert _one_line_per_download([added, again]) == [added, again]


async def _download(
    database: Database, *, state: str, created_at: int, finished_at: int | None
) -> None:
    await database.execute(
        "INSERT INTO downloads (id, url, url_hash, state, site, username, asset_id,"
        " created_at, finished_at) VALUES (?, 'https://example.invalid/x', ?, ?, 'Discord',"
        " NULL, ?, ?, ?)",
        (new_id(), new_id(), state, ASSET, created_at, finished_at),
    )


async def test_a_file_s_thread_opens_with_its_download_and_a_re_download_keeps_its_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The download finished AFTER the import wrote the arrival (which is how "added" came to sort
    above "downloaded"), and it is still the first line, dated at the arrival."""
    await make_file(temp_db)
    await temp_db.execute(
        "UPDATE assets SET original_filename = 'image.png' WHERE id = ?", (ASSET,)
    )
    await _download(temp_db, state="done", created_at=ADDED_AT - 5, finished_at=ADDED_AT + 3)
    await _download(
        temp_db, state="duplicate", created_at=ADDED_AT + 5000, finished_at=ADDED_AT + 5001
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(one.kind, one.at, one.what) for one in events] == [
        ("downloaded", ADDED_AT, "Sift downloaded this file from Discord as image.png"),
        ("downloaded", ADDED_AT + 5001, "Sift downloaded this file from Discord"),
    ]


async def test_a_gallery_s_download_naming_a_file_already_here_is_not_its_arrival(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`done` names a gallery's last file, which may have been in the library long before."""
    await make_file(temp_db)
    await _download(temp_db, state="done", created_at=ADDED_AT + 5000, finished_at=None)

    kinds = [one.kind for one in await history_of_asset(temp_db, access, actors.admin, ASSET)]

    assert kinds == ["added", "downloaded"]


# --- the housekeeping, per sitting ---------------------------------------------------------------


def test_housekeeping_is_one_line_per_sitting_dated_at_its_first_step() -> None:
    """Two sittings a day apart are two lines; the steps of each are listed in time order."""
    late = ADDED_AT + 86_400
    events = [
        _line("added", ADDED_AT),
        _line("ready", ADDED_AT + 30, "indexed", routine=True),
        _line("ready", ADDED_AT + 10, "thumbnail", routine=True),
        _line("renamed", ADDED_AT + 3600),
        _line("asked", late + EPISODE_GAP, "asked", routine=True),
        _line("ready", late, "preview", routine=True),
    ]

    folded = ordered(_one_processed_line(events))

    assert [(one.kind, one.at) for one in folded] == [
        ("added", ADDED_AT),
        ("ready", ADDED_AT + 10),
        ("renamed", ADDED_AT + 3600),
        ("ready", late),
    ]
    first, second = folded[1], folded[3]
    assert [link.name for link in first.detail[0].links] == ["thumbnail", "indexed"]
    assert [link.name for link in second.detail[0].links] == ["preview", "asked"]
    assert first.what == "Sift processed this file"


def test_a_sitting_of_one_step_is_that_step() -> None:
    step = _line("ready", ADDED_AT, "thumbnail", routine=True)
    alone = _line("ready", ADDED_AT + EPISODE_GAP + 1, "indexed", routine=True)
    assert _one_processed_line([step, alone]) == [step, alone]


# --- a rename says why ----------------------------------------------------------------------------


def test_a_rename_says_its_reason_and_a_word_nobody_knows_says_nothing() -> None:
    assert (
        say.text_of(say.renamed("Sift", "a/clip.mp4", RENAMED_FOR_TOOL_ID))
        == "Sift renamed this file to clip.mp4 to remove the download tool's id from its name"
    )
    assert say.text_of(say.renamed("You", "a/clip.mp4", "unheard")) == (
        "You renamed this file to clip.mp4"
    )


def test_a_rename_says_the_name_it_had_before() -> None:
    """The move's record keeps the old name, so the file's line says what the rename changed, as
    the feed's does. The same name on both sides (a move that kept it) says the new one alone."""
    assert say.text_of(say.renamed("You", "a/beach.mp4", None, "a/clip.mp4")) == (
        "You renamed this file from clip.mp4 to beach.mp4"
    )
    assert say.text_of(say.renamed(None, "a/beach.mp4", None, "b/clip.mp4")) == (
        "This file was renamed from clip.mp4 to beach.mp4"
    )
    assert say.text_of(say.renamed("You", "a/clip.mp4", None, "b/clip.mp4")) == (
        "You renamed this file to clip.mp4"
    )


def test_a_move_names_its_folder_only_as_far_as_the_reader_may_see_it() -> None:
    """A folder the reader may not see (a Hidden one while Hidden is shut) is "..." in the line
    and is not a way there; one they may see is named and linked as before."""
    who = _Who(viewer=Viewer(id="admin", role=Role.ADMIN), names={})
    row = {
        "id": "01KZF0VNDM0VE0000000000000",
        "kind": "move",
        "root_id": LIBRARY,
        "to_rel_path": "clips/sealed/holiday.mp4",
        "moved_by": None,
        "moved_by_sift": 0,
        "reason": None,
        "moved_at": ADDED_AT,
        "undone_at": None,
    }

    # The function reads a row by column name and nothing else, which a dict answers the same way.
    rows = [cast(Row, row)]
    (hidden,) = _move_events(rows, who, {LIBRARY: frozenset({"", "clips"})})
    assert say.text_of(hidden.pieces).endswith("moved to clips/...")
    assert not any(isinstance(one, say.Piece) and one.kind == "folder" for one in hidden.pieces)

    (shown,) = _move_events(rows, who, {LIBRARY: frozenset({"", "clips", "clips/sealed"})})
    assert say.text_of(shown.pieces).endswith("moved to clips/sealed")


def test_an_arrival_says_the_folder_it_was_found_in_as_far_as_the_reader_may_see_it() -> None:
    """Where a file came in is recorded twice over (its first move's old path, else its oldest
    copy's place) and the arrival line says it. Neither is where the file is now. The top of the
    library says nothing, and a folder the reader may not see is said only as far as they may."""
    from sift.kernel.access.history_sources import arrived_in

    moved = cast(Row, {"root_id": LIBRARY, "from_rel_path": "inbox/new/beach.mp4"})
    place = cast(
        Row, {"root_id": LIBRARY, "rel_path": "sorted/beach.mp4", "archive_rel_path": None}
    )
    seen = {LIBRARY: frozenset({"", "inbox", "inbox/new", "sorted"})}

    found = arrived_in([moved], place, seen)
    assert found is not None
    assert say.text_of(say.added("beach.mp4", *found)) == (
        "Sift added this file to the library as beach.mp4 from inbox/new"
    )
    assert arrived_in([], place, seen) == ("sorted/beach.mp4", "sorted")
    top = cast(Row, {"root_id": LIBRARY, "rel_path": "beach.mp4", "archive_rel_path": None})
    assert arrived_in([], top, seen) is None
    assert arrived_in([], None, seen) is None
    hidden = arrived_in([moved], place, {LIBRARY: frozenset({"", "inbox"})})
    assert hidden is not None
    line = say.added(None, *hidden)
    assert say.text_of(line) == "Sift added this file to the library from inbox/..."
    assert not any(isinstance(one, say.Piece) and one.kind == "folder" for one in line)


async def test_sifts_own_rename_on_a_file_s_history_says_why(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_file(temp_db)
    await temp_db.execute(
        "INSERT INTO file_moves (id, kind, asset_id, root_id, from_rel_path, to_rel_path,"
        " moved_by, moved_by_sift, reason, moved_at) VALUES (?, 'rename', ?, ?,"
        " 'clip _x1_.mp4', 'clip.mp4', NULL, 1, ?, ?)",
        (new_id(), ASSET, LIBRARY, RENAMED_FOR_TOOL_ID, ADDED_AT + 60),
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert events[-1].what == (
        "Sift renamed this file from clip _x1_.mp4 to clip.mp4 to remove the download tool's id"
        " from its name"
    )
