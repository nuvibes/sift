# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a viewer can reach, and what the app says when they cannot. The one judgement is `after`'s
choice between two reasons: backwards, it would offer an Unlock button no PIN can satisfy."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, status

from sift.kernel.access import Actionable, Repository, Role, Viewer
from sift.kernel.reach import (
    OUT_OF_REACH,
    OUT_OF_REACH_MANY,
    VAULT_LOCKED,
    VAULT_LOCKED_MANY,
    BulkWriteDone,
    require_reachable,
    vault_locked,
)

pytestmark = pytest.mark.unit


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


def test_nothing_skipped_carries_no_reason_at_all() -> None:
    """Nothing skipped carries no reason: a sentence beside a zero explains nothing."""
    done = BulkWriteDone.after(Actionable(allowed=("a", "b"), concealed=(), refused=()), 2)

    assert done == BulkWriteDone(changed=2, skipped=0, reason=None, vault_locked=False)


def test_the_vault_wins_when_a_selection_was_skipped_for_both_reasons() -> None:
    """When both happened, the vault is the reason said: a PIN is something a person can act on."""
    done = BulkWriteDone.after(
        Actionable(allowed=("a",), concealed=("b",), refused=("c",)), changed=1
    )

    assert done.reason == VAULT_LOCKED
    assert done.vault_locked is True
    assert done.skipped == 2, "both are counted even though only one is named"


def test_a_refusal_that_is_not_the_vault_offers_nothing_to_unlock() -> None:
    """A refusal that is not the vault offers nothing to unlock."""
    done = BulkWriteDone.after(Actionable(allowed=(), concealed=(), refused=("c",)), changed=0)

    assert (done.reason, done.vault_locked) == (OUT_OF_REACH, False)


def test_both_wordings_are_sent_together_or_neither_is() -> None:
    """The plural and singular wordings are sent together or not at all, or the screen prints a
    sentence about one file beside a count of three."""
    vault = BulkWriteDone.after(Actionable(allowed=(), concealed=("a",), refused=()), changed=0)
    gone = BulkWriteDone.after(Actionable(allowed=(), concealed=(), refused=("b",)), changed=0)
    clean = BulkWriteDone.after(Actionable(allowed=("c",), concealed=(), refused=()), changed=1)

    assert (vault.reason, vault.reason_many) == (VAULT_LOCKED, VAULT_LOCKED_MANY)
    assert (gone.reason, gone.reason_many) == (OUT_OF_REACH, OUT_OF_REACH_MANY)
    assert (clean.reason, clean.reason_many) == (None, None)


def test_each_plural_is_a_different_sentence_from_its_singular() -> None:
    """Each plural differs from its singular."""
    assert VAULT_LOCKED_MANY != VAULT_LOCKED
    assert OUT_OF_REACH_MANY != OUT_OF_REACH


def test_the_two_reasons_are_different_sentences() -> None:
    """The two reasons are different sentences: one carries a button."""
    assert VAULT_LOCKED != OUT_OF_REACH


def test_the_single_item_refusal_is_a_423_that_says_vault() -> None:
    """The single-item refusal is a 423 whose detail says vault; a HEAD has no body, so the client
    reads the status too."""
    refusal = vault_locked()

    assert refusal.status_code == status.HTTP_423_LOCKED
    assert refusal.detail == VAULT_LOCKED
    assert "vault" in str(refusal.detail).lower()


def test_the_count_of_skipped_items_comes_from_both_piles() -> None:
    """`skipped` counts both piles."""
    both = Actionable(allowed=("a",), concealed=("b", "c"), refused=("d",))

    assert both.skipped == 3


# --- the single-item refusal, which needs a repository to ask


class _Answers:
    """Just enough repository for the DECISION under test; the queries are `test_access.py`'s."""

    def __init__(self, *, opens: bool, concealed: bool) -> None:
        self._opens = opens
        self._concealed = concealed
        self.asked_whether_concealed = False

    async def open_asset(self, viewer: object, asset_id: str) -> object | None:
        return SimpleNamespace(id=asset_id) if self._opens else None

    async def is_concealed(self, user_id: str, asset_id: str) -> bool:
        self.asked_whether_concealed = True
        return self._concealed


def _viewer(*, show_hidden: bool = False) -> Viewer:
    return Viewer(id="01HX000000000000000000000A", role=Role.ADMIN, show_hidden=show_hidden)


async def test_something_reachable_comes_straight_back_and_costs_no_second_read() -> None:
    """Something reachable comes straight back: the vault question is on the refusal path only."""
    answers = _Answers(opens=True, concealed=False)

    got = await require_reachable(cast(Repository, answers), _viewer(), "a1", _missing)

    assert got == "a1"
    assert answers.asked_whether_concealed is False


async def test_a_concealed_file_is_a_423_for_the_user_concealing_it() -> None:
    with pytest.raises(HTTPException) as refused:
        await require_reachable(
            cast(Repository, _Answers(opens=False, concealed=True)), _viewer(), "a1", _missing
        )

    assert refused.value.status_code == status.HTTP_423_LOCKED


async def test_anything_else_is_the_CALLER_S_OWN_404_and_not_one_written_here() -> None:
    """Anything else is the CALLER's own 404, never one written here: two spellings would tell an
    unplugged drive from a file that never existed."""
    mine = HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")

    with pytest.raises(HTTPException) as refused:
        await require_reachable(
            cast(Repository, _Answers(opens=False, concealed=False)),
            _viewer(),
            "a1",
            lambda: mine,
        )

    assert refused.value is mine


async def test_a_viewer_with_the_vault_already_open_is_never_asked_about_it() -> None:
    """A viewer whose vault is open is never asked about it."""
    answers = _Answers(opens=False, concealed=True)

    with pytest.raises(HTTPException) as refused:
        await require_reachable(
            cast(Repository, answers), _viewer(show_hidden=True), "a1", _missing
        )

    assert refused.value.status_code == status.HTTP_404_NOT_FOUND
    assert answers.asked_whether_concealed is False
