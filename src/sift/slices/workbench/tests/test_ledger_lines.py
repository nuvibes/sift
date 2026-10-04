# SPDX-License-Identifier: AGPL-3.0-or-later
"""The parts of one feed line the route assembles itself, asked of the helpers directly.

The feed over HTTP is `test_ledger_route.py`; what is here is the handful of decisions the route
makes on the way to a line that a seeded page would have to be built very carefully to reach: who a
doer with no name left is said to be, which files a face match asks the figures of, how many of a
folded press's things are listed, what a fold's "Show each" opens to, and what a setting retired
into others is called.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from sift.kernel import settings_registry as registry
from sift.kernel.access import Role, Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history_events import FEED_FOLD_SHOWN, LedgerEvent, Thing
from sift.kernel.access.history_feed import Press
from sift.slices.workbench.models import LedgerActorView
from sift.slices.workbench.router import _by, _detail, _face_pairs, _setting_label, _shown

pytestmark = pytest.mark.unit

ME = Viewer(id="01HX000000000000000000ME01", role=Role.ADMIN)


def _event(
    *,
    verb: str = "linked",
    actor_kind: str | None = "sift",
    actor_id: str | None = "faces",
    object_kind: str = "person",
    subjects: tuple[Thing, ...] = (),
) -> LedgerEvent:
    return LedgerEvent(
        id="01HX00000000000000000EVT01",
        at=0,
        verb=verb,
        actor_kind=actor_kind,
        actor_id=actor_id,
        user_id=None,
        object=Thing(kind=object_kind, id="01HX0000000000000000PERSON"),
        count=None,
        queue="ledger",
        payload="{}",
        title="",
        detail="",
        reversed_at=None,
        subjects=subjects,
    )


def test_a_user_since_deleted_is_said_to_be_gone_rather_than_printed_as_an_id() -> None:
    gone = LedgerActorView(kind="user", id="01HX00000000000000000GONE1", name=None)
    assert _by(gone, ME) == "A user who is gone"
    assert _by(LedgerActorView(kind="user", id=ME.id, name="me"), ME) == say.YOU


def test_a_stash_box_is_named_and_one_since_removed_is_still_a_stash_box() -> None:
    assert _by(LedgerActorView(kind="box", id="b", name="StashDB"), ME) == "StashDB"
    assert _by(LedgerActorView(kind="box", id="b", name=None), ME) == say.A_STASH_BOX


def test_only_a_face_match_asks_after_the_figures_of_its_files() -> None:
    """The how-sure figures belong to Sift recognizing somebody from a face and nothing else; the
    pairs asked for are that event's files with the person it named."""
    files = (Thing(kind="asset", id="a1"), Thing(kind="asset", id="a2"), Thing(kind="tag", id="t"))
    matched = _event(subjects=files)
    by_hand = _event(actor_kind="user", actor_id=ME.id, subjects=files)
    assert _face_pairs([matched, by_hand]) == [
        ("a1", "01HX0000000000000000PERSON"),
        ("a2", "01HX0000000000000000PERSON"),
    ]


def test_a_folded_press_lists_at_most_a_page_of_each_kind_it_was_about() -> None:
    """The counts are the press's own, so a press over four thousand files asks after a page of
    them, per kind, so a flood of one kind cannot crowd out the other."""
    files = tuple(Thing(kind="asset", id=f"a{at}") for at in range(FEED_FOLD_SHOWN + 5))
    press = Press(event=_event(), folded=2, subjects=(*files, Thing(kind="tag", id="t")))
    _objects, subjects = _shown(press)
    assert sum(one.kind == "asset" for one in subjects) == FEED_FOLD_SHOWN
    assert [one.id for one in subjects if one.kind == "tag"] == ["t"]


def test_a_line_that_counts_its_fields_opens_to_the_fields_it_counted() -> None:
    said = say.Said(pieces=(), folded=("5 details", ("Name", "Notes")))
    (detail,) = _detail(said)
    assert (detail.kind, detail.words) == ("field", "5 details")
    assert [one.name for one in detail.entries] == ["Name", "Notes"]


@pytest.fixture
def a_retired_setting(clean_settings_registry: None) -> Iterator[None]:
    registry.register_setting(
        key="lines.now",
        scope="app",
        default=True,
        section="Library",
        label="What answers it now",
        help="A setting a retired key was folded into.",
    )
    registry.retire_setting(
        "lines.before",
        into=("lines.now",),
        read=lambda values: values[0],
        write=lambda value, values: (value,),
        why="a test",
    )
    yield


@pytest.mark.usefixtures("a_retired_setting")
def test_a_setting_retired_into_another_is_called_by_what_answers_it_now() -> None:
    """Its snapshot is the key or its last label, and neither is what a person can find today."""
    assert _setting_label("lines.before", "Old words") == "What answers it now"
