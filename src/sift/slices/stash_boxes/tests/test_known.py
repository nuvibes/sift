# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file linked to a stash-box scene by an id somebody already gave it: fetched by the id, kept as
the file's answer, applied as an exact match is; an answer settled here is left alone."""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's table registers itself)
from sift.kernel.db import Database
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes.adapter import Box, as_json
from sift.slices.stash_boxes.known import KnownScene, link_known_scene
from sift.slices.stash_boxes.service import EXACT, StashBoxService
from sift.slices.stash_boxes.tests.jobs_support import _deps, _Enricher, _Settings

pytestmark = pytest.mark.anyio

A_KEY = b"0" * 32


def _scene(remote_id: str) -> FoundRecord:
    return FoundRecord(
        source_id="box",
        remote_id=remote_id,
        subject=Subject.ASSET,
        name="Tide pools",
        fields={"title": "Tide pools"},
        confidence=EXACT,
    )


class _Adapter:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def scene(self, box: Box, remote_id: str) -> FoundRecord | None:
        self.asked.append(remote_id)
        return _scene(remote_id) if remote_id != "unknown" else None


@pytest.fixture
async def setup(temp_db: Database) -> tuple[StashBoxService, _Adapter, str]:
    await temp_db.initialize_schema()
    adapter = _Adapter()
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    box_id = await service.add(
        name="StashDB", endpoint="https://stashdb.example/graphql", api_key="k", master_key=A_KEY
    )
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('file-1', 'f1', 'video', 0)"
    )
    return service, adapter, box_id


async def _link(
    service: StashBoxService, box_id: str, remote_id: str, enricher: _Enricher
) -> KnownScene:
    deps = _deps(object(), _Settings(), enricher)  # type: ignore[arg-type]
    return await link_known_scene(service, deps, "file-1", box_id, remote_id, A_KEY)


async def _state(service: StashBoxService, box_id: str) -> tuple[str, str, str] | None:
    held = await service.match("file-1", box_id)
    return None if held is None else (held.state, held.remote_id, held.grade.value)


async def test_a_known_id_is_fetched_kept_and_applied(
    setup: tuple[StashBoxService, _Adapter, str],
) -> None:
    service, adapter, box_id = setup
    enricher = _Enricher()

    assert await _link(service, box_id, "scene-7", enricher) is KnownScene.LINKED

    assert adapter.asked == ["scene-7"]
    assert await _state(service, box_id) == ("applied", "scene-7", "certain")
    assert len(enricher.applied) == 1


async def test_a_waiting_answer_naming_the_same_scene_is_applied_without_asking(
    setup: tuple[StashBoxService, _Adapter, str], temp_db: Database
) -> None:
    service, adapter, box_id = setup
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches (asset_id, box_id, remote_id, payload, grade, state,"
        " found_at) VALUES ('file-1', ?, 'scene-7', ?, 'likely', 'waiting', 1)",
        (box_id, as_json([_scene("scene-7")])),
    )

    assert await _link(service, box_id, "scene-7", _Enricher()) is KnownScene.LINKED

    assert adapter.asked == []
    assert await _state(service, box_id) == ("applied", "scene-7", "likely")


async def test_an_answer_settled_here_is_left_as_it_is(
    setup: tuple[StashBoxService, _Adapter, str], temp_db: Database
) -> None:
    service, adapter, box_id = setup
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches (asset_id, box_id, remote_id, payload, grade, state,"
        " found_at, decided_at) VALUES ('file-1', ?, 'scene-2', ?, 'likely', 'refused', 1, 2)",
        (box_id, as_json([_scene("scene-2")])),
    )
    enricher = _Enricher()

    assert await _link(service, box_id, "scene-7", enricher) is KnownScene.ALREADY

    assert adapter.asked == []
    assert enricher.applied == []
    assert await _state(service, box_id) == ("refused", "scene-2", "likely")


async def test_an_id_the_box_does_not_know_keeps_nothing(
    setup: tuple[StashBoxService, _Adapter, str],
) -> None:
    service, _adapter, box_id = setup

    assert await _link(service, box_id, "unknown", _Enricher()) is KnownScene.NOT_ASKED

    assert await _state(service, box_id) is None


async def test_a_file_kept_local_is_never_asked_about(
    setup: tuple[StashBoxService, _Adapter, str], temp_db: Database
) -> None:
    service, adapter, box_id = setup
    await temp_db.execute("UPDATE assets SET keep_local = 1 WHERE id = 'file-1'")

    assert await _link(service, box_id, "scene-7", _Enricher()) is KnownScene.KEPT_LOCAL

    assert adapter.asked == []
    assert await _state(service, box_id) is None


async def test_a_box_that_does_not_answer_the_id_keeps_nothing_and_asks_nothing_more(
    setup: tuple[StashBoxService, _Adapter, str],
) -> None:
    """An unreachable box is a link not made, never a failed job: the file is left to be asked
    again, and nothing is applied."""
    from sift.slices.stash_boxes.adapter import StashBoxUnreachable

    service, adapter, box_id = setup

    async def down(box: Box, remote_id: str) -> FoundRecord | None:
        adapter.asked.append(remote_id)
        raise StashBoxUnreachable("the box did not answer")

    adapter.scene = down  # type: ignore[method-assign]
    enricher = _Enricher()

    assert await _link(service, box_id, "scene-7", enricher) is KnownScene.NOT_ASKED

    assert adapter.asked == ["scene-7"]
    assert enricher.applied == []
    assert await _state(service, box_id) is None


async def test_a_box_removed_since_the_id_was_given_is_not_asked(
    setup: tuple[StashBoxService, _Adapter, str],
) -> None:
    service, adapter, _box_id = setup
    enricher = _Enricher()

    assert await _link(service, "a-box-since-removed", "scene-7", enricher) is KnownScene.NOT_ASKED

    assert adapter.asked == []
    assert enricher.applied == []
