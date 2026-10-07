# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking about one person, Site or tag costs that row: a point read, seeking its own keys.

A cover picture and an entity page each ask first whether the viewer may be shown the thing. The
wall's statement answers that, and unfiltered it walks every row of the wall before the id
condition is reached; the one-row form reads the row and its stored counts by their keys.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every component registers itself)
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.repository.entities import PERSON_BY_ID, SITE_BY_ID, TAG_BY_ID
from sift.kernel.access.repository.grants import _EFFECTIVE_ASSET_MARKS
from sift.kernel.access.repository.wall_collections import COLLECTION_BY_ID
from sift.kernel.access.repository.wall_photo_sets import PHOTO_SET_BY_ID
from sift.kernel.access.repository.wall_songs import SONG_BY_ID
from sift.kernel.content.user_state import UserStateStore
from sift.kernel.db import Database, PointRead
from sift.kernel.seams import DisagreementSeam
from sift.slices.related.router import related_counts
from sift.testing.fixtures import Actors, World

#: Each one-row read and the alias its statement gives the row.
ONE_ROW: list[tuple[PointRead, str]] = [
    (PERSON_BY_ID, "p"),
    (SITE_BY_ID, "pl"),
    (TAG_BY_ID, "t"),
    (COLLECTION_BY_ID, "c"),
    (PHOTO_SET_BY_ID, "ps"),
    (SONG_BY_ID, "sg"),
]


async def _plan(database: Database, statement: PointRead) -> list[str]:
    """The plan with every parameter bound NULL; the schema decides it, not the rows."""
    params = dict.fromkeys(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", statement.sql))
    rows = await database.fetch_all(
        "EXPLAIN QUERY PLAN " + statement.sql,  # nosemgrep: sift-no-string-built-sql
        params,
    )
    return [str(row["detail"]) for row in rows]


@pytest.mark.parametrize(("statement", "alias"), ONE_ROW, ids=[one[0].name for one in ONE_ROW])
async def test_a_read_by_id_seeks_its_row(
    access: Repository, temp_db: Database, statement: PointRead, alias: str
) -> None:
    plan = await _plan(temp_db, statement)
    walked = [step for step in plan if step == f"SCAN {alias}" or step.startswith(f"SCAN {alias} ")]
    assert not walked, f"{statement.name} walks the wall to find its row: {walked}"
    assert any(step.startswith(f"SEARCH {alias} ") and "id=?" in step for step in plan)


class _Recorded:
    """The statements a read sent, so a test can say which kind each was."""

    def __init__(self, database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sent: list[object] = []
        real_all, real_one = database.fetch_all, database.fetch_one

        async def fetch_all(statement: Any, params: Any = ()) -> Any:
            self.sent.append(statement)
            return await real_all(statement, params)

        async def fetch_one(statement: Any, params: Any = ()) -> Any:
            self.sent.append(statement)
            return await real_one(statement, params)

        monkeypatch.setattr(database, "fetch_all", fetch_all)
        monkeypatch.setattr(database, "fetch_one", fetch_one)


ById = Callable[[Repository, Viewer, str], Awaitable[object | None]]

BY_ID: list[tuple[str, PointRead, ById]] = [
    ("person", PERSON_BY_ID, lambda a, v, i: a.visible_person(v, i)),
    ("site", SITE_BY_ID, lambda a, v, i: a.visible_site(v, i)),
    ("tag", TAG_BY_ID, lambda a, v, i: a.visible_tag(v, i)),
]


@pytest.mark.parametrize(("kind", "statement", "read"), BY_ID, ids=[one[0] for one in BY_ID])
async def test_the_check_behind_a_cover_is_one_point_read(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    statement: PointRead,
    read: ById,
) -> None:
    recorded = _Recorded(temp_db, monkeypatch)
    assert await read(access, actors.admin, str(world.object_id(kind))) is not None
    assert recorded.sent == [statement]


class _NoWaiting:
    async def disagreement_mark(
        self, viewer: Viewer, kind: str, entity_id: str
    ) -> tuple[None, list[str]]:
        return None, []


class _Asked:
    """The repository, counting which listings a request asked for."""

    def __init__(self, access: Repository) -> None:
        self._access = access
        self.listings: list[str] = []

    def __getattr__(self, name: str) -> Any:
        found = getattr(self._access, name)
        if name.startswith(("list_", "suggest_", "visible_assets")):
            self.listings.append(name)
        return found


#: The tabs a card cannot answer, each still off its own listing: Seen with, and the tags within.
LEFT_TO_LISTINGS = {"person": ["suggest_people"], "tag": ["list_tags"], "site": []}


@pytest.mark.parametrize("kind", sorted(LEFT_TO_LISTINGS))
async def test_a_visible_subject_s_tab_strip_reads_the_stored_counts(
    access: Repository, actors: Actors, world: World, temp_db: Database, kind: str
) -> None:
    asked = _Asked(access)
    counts = await related_counts(
        kind,
        str(world.object_id(kind)),
        cast(Repository, asked),
        actors.admin,
        cast(DisagreementSeam, _NoWaiting()),
    )
    assert counts.files == 1
    assert asked.listings == LEFT_TO_LISTINGS[kind]


async def test_a_page_of_states_is_a_point_read(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = UserStateStore(temp_db)
    recorded = _Recorded(temp_db, monkeypatch)
    await store.states_of([world.solo, world.twin], actors.admin.id)
    await store.state_of(world.solo, actors.admin.id)
    assert len(recorded.sent) == 2
    assert all(isinstance(one, PointRead) for one in recorded.sent)


async def test_a_page_of_marks_works_each_bit_out_once(
    access: Repository, temp_db: Database
) -> None:
    rows = await temp_db.fetch_all(
        "EXPLAIN QUERY PLAN " + _EFFECTIVE_ASSET_MARKS,  # nosemgrep: sift-no-string-built-sql
        {"asset_ids": "[]"},
    )
    assert "MATERIALIZE standing" in [str(row["detail"]) for row in rows]
