# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Music pile on Organize: what is waiting, counted for whoever is looking, and a card that
opens the music task's row, where Run now and its When are.

The row never leaves the board: a library with nothing waiting keeps it at zero.
"""

from __future__ import annotations

import pytest

import sift.slices.music  # noqa: F401 (declares the music task)
from sift.kernel.access import Role, Viewer
from sift.kernel.jobs.schedules import get_schedule
from sift.slices.music.queue import TASK, MusicQueue

pytestmark = [pytest.mark.unit]

_ADMIN = Viewer(id="user-1", role=Role.ADMIN)


def _queue(*, files: int = 1000, any_waiting: bool = True) -> tuple[MusicQueue, list[str]]:
    asked: list[str] = []

    async def waiting(viewer: Viewer) -> int:
        asked.append(viewer.id)
        return files

    async def anything() -> bool:
        return any_waiting

    return MusicQueue(waiting=waiting, any_waiting=anything), asked


@pytest.mark.asyncio
async def test_the_pile_counts_what_this_user_can_see() -> None:
    queue, asked = _queue(files=601)
    summary = await queue.survey(_ADMIN)
    assert summary.count == 601
    assert asked == [_ADMIN.id]


@pytest.mark.asyncio
async def test_a_library_with_nothing_waiting_keeps_the_row_at_zero_without_the_scoped_count() -> (
    None
):
    """The empty board draws every pile, this one too, and the cheap question answers it: the
    library-wide count is not asked."""
    queue, asked = _queue(files=1000, any_waiting=False)
    assert await queue.available() is True
    summary = await queue.survey(_ADMIN)
    assert summary.count == 0
    assert asked == []


@pytest.mark.asyncio
async def test_the_card_offers_no_undo() -> None:
    queue, _asked = _queue()
    assert queue.reversible is False
    assert await queue.pictures_of(_ADMIN, "{}") == ()
    assert await queue.reverse(_ADMIN, "receipt-1", "{}") is False


@pytest.mark.asyncio
async def test_the_card_opens_the_music_task_where_its_question_is_answered() -> None:
    """A card on the board only opens its page, and this question is about the library, so its page
    is the task's own row under `Settings > Tasks` (Run now and When), never a list of files."""
    queue, _asked = _queue()
    opens = (await queue.survey(_ADMIN)).opens
    assert opens == "/settings/tasks#tasks.music.when"
    assert opens == f"/settings/tasks#{get_schedule(TASK).when_key}"  # type: ignore[union-attr]


def test_the_task_the_card_opens_is_a_declared_task() -> None:
    """The card names its task by id; a rename of the declaration must not leave it opening nothing."""
    assert get_schedule(TASK) is not None
