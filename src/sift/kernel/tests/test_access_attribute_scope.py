# SPDX-License-Identifier: AGPL-3.0-or-later
"""A filter on a thing or on what a person is like is tried, for anybody but an admin, only on
their own files: its work never follows the files they may not see."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from typing import Any, cast

import pytest

from sift.kernel.access import (
    AllOf,
    AssetFilter,
    Effect,
    Node,
    Not,
    ObjectType,
    Repository,
    Viewer,
    Where,
)
from sift.kernel.access.repository.read_files import FileReads, words_of
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import _EPOCH
from sift.testing.fixtures import Actors

_HIDDEN = 40
_THINGS = {
    "tags": "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
    "sites": "INSERT INTO sites (id, name, created_at) VALUES (?, ?, ?)",
    "collections": "INSERT INTO collections (id, name, created_at) VALUES (?, ?, ?)",
    "photo_sets": "INSERT INTO photo_sets (id, name, created_at) VALUES (?, ?, ?)",
    "songs": "INSERT INTO songs (id, name, created_at) VALUES (?, ?, ?)",
}
_LINKS = {
    "people": "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 0)",
    "tags": "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, 0)",
    "usernames": "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
    " VALUES (?, ?, ?, 0)",
}
_PERSON = (
    "INSERT INTO people (id, name, created_at, gender, hair_color, eye_color, ethnicity, country,"
    " breast_type, height_cm, birth_date) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_PAIR = (
    "INSERT INTO music_pairs (a_id, b_id, ber, offset_s, windows, matching, computed_at)"
    " VALUES (?, ?, 0.1, 0, 10, 9, 0)"
)


async def _file(db: Database, root: str, folder: str | None) -> str:
    asset_id = new_id()
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
        (asset_id, f"digest-{asset_id}", _EPOCH),
    )
    await db.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), asset_id, root, folder, asset_id, "f.mp4", _EPOCH, _EPOCH),
    )
    return asset_id


async def _person(db: Database, carries: str, height: int, born: str) -> str:
    person = new_id()
    await db.execute(_PERSON, (person, carries, *([carries] * 6), height, born))
    return person


@pytest.fixture
async def library(access: Repository, actors: Actors, temp_db: Database) -> dict[str, Any]:
    """Two files shared with the guest, beside forty kept ones carrying things of their own."""
    root, folder = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root, "root", "/library", _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (folder, root, "kept", "kept"),
    )
    shown = [await _file(temp_db, root, None) for _ in range(2)]
    kept = [await _file(temp_db, root, folder) for _ in range(_HIDDEN)]
    made: dict[str, Any] = {"shown": shown, "kept": kept, "folder": folder}
    made["person"] = await _person(temp_db, "ZQXV", 205, "1912-06-01")
    made["seen_person"] = await _person(temp_db, "SEEN", 165, "1990-01-01")
    for kind, statement in _THINGS.items():
        made[kind] = new_id()
        await temp_db.execute(statement, (made[kind], f"kept {kind}", _EPOCH))
    made["usernames"] = new_id()
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
        (made["usernames"], made["sites"], "kept", _EPOCH),
    )
    for n, asset_id in enumerate(kept):
        source = "folder" if n % 2 else "stash_box"
        for kind, statement in _LINKS.items():
            subject = made["person"] if kind == "people" else made[kind]
            await temp_db.execute(statement, (asset_id, subject, source))
        await temp_db.execute(
            "INSERT INTO collection_items (collection_id, asset_id) VALUES (?, ?)",
            (made["collections"], asset_id),
        )
        await temp_db.execute(
            "INSERT INTO photo_set_items (photo_set_id, asset_id) VALUES (?, ?)",
            (made["photo_sets"], asset_id),
        )
        await temp_db.execute(
            "INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)", (asset_id, made["songs"])
        )
        await temp_db.execute(
            "INSERT INTO file_verdicts (asset_id, product, code, reason, at)"
            " VALUES (?, 'faces', 'x', 'x', 0)",
            (asset_id,),
        )
        if n:
            await temp_db.execute(_PAIR, (*sorted((kept[0], asset_id)),))
    await temp_db.execute(_PAIR, (*sorted(shown),))
    await temp_db.execute(_PAIR, (*sorted((shown[0], kept[1])),))
    for asset_id in (shown[0], kept[2]):
        await temp_db.execute(_LINKS["people"], (asset_id, made["seen_person"], "folder"))
    for asset_id in shown:
        await access.grant(ObjectType.ITEM, asset_id, actors.guest.id, Effect.SHARE)
    return made


def _pairs(m: dict[str, Any]) -> dict[str, tuple[Node, Node]]:
    """Each leaf with a value only kept files carry, beside a same-shape one nobody does."""

    def one(key: str, *values: object) -> Where:
        return Where(key, values)

    pairs: dict[str, tuple[Node, Node]] = {
        key: (one(key, "ZQXV"), one(key, "ZQXW"))
        for key in ("gender", "hair", "eyes", "ethnicity", "nationality", "breasts")
    }
    pairs["height"] = (one("height", "200+"), one("height", "300+"))
    pairs["age"] = (one("age", 110, 119), one("age", 100, 109))
    for key in ("tags", "usernames", "sites", "collections", "photo_sets", "songs"):
        pairs[key] = (one(key, m[key]), one(key, new_id()))
    pairs["people"] = (one("people", m["person"]), one("people", new_id()))
    for key, subject in (("filed_under", "sites"), ("tagged_with", "tags"), ("named_as", "person")):
        pairs[key] = (one(key, m[subject], "folder", 0), one(key, new_id(), "folder", 0))
        box = f"{key}_box"
        pairs[box] = (one(box, m[subject], 0, None), one(box, new_id(), 0, None))
    pairs["unnamed_face"] = (one("unnamed_face", m["person"]), one("unnamed_face", new_id()))
    pairs["left_out"] = (one("left_out", "faces"), one("left_out", "sprites"))
    kept, other = m["kept"][0], new_id()
    pairs["same_music"] = (one("same_music", kept, kept, kept), one("same_music", *[other] * 3))
    return pairs


class _Counted:
    """A plain connection that counts the statement steps every read takes."""

    def __init__(self, path: Any) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.steps = 0
        self.connection.set_progress_handler(self._step, 1)

    def _step(self) -> int:
        self.steps += 1
        return 0

    async def fetch_all(self, statement: Any, params: Any = ()) -> list[Any]:
        return self.connection.execute(getattr(statement, "sql", statement), params).fetchall()

    async def fetch_one(self, statement: Any, params: Any = ()) -> Any:
        rows = await self.fetch_all(statement, params)
        return rows[0] if rows else None


def _filtered(node: Node, folder: str | None = None) -> AssetFilter:
    return AssetFilter(where=AllOf((node,)), folder_scope=((folder,),) if folder else ())


async def _steps(db: Database, viewer: Viewer, asked: AssetFilter) -> tuple[int, object]:
    """The page, its total and a Filter column, as one count of steps and one answer."""
    counted = _Counted(db.path)
    try:
        reads = FileReads(cast(Any, counted), cast(Any, None))
        page = await reads.visible_assets(viewer, limit=50, asset_filter=asked)
        column = await reads.facet_counts(viewer, "media", asset_filter=asked)
        answer = (
            page.total,
            [i.asset.id for i in page.items],
            [(c.value, c.count) for c in column],
        )
        return counted.steps, answer
    finally:
        counted.connection.close()


async def _same_for_a_guest(
    db: Database, actors: Actors, kept: AssetFilter, none: AssetFilter
) -> None:
    """Equal for the guest; the admin, who may see the kept files, pays for them."""
    hidden, nobody = await _steps(db, actors.guest, kept), await _steps(db, actors.guest, none)
    assert hidden == nobody
    assert (await _steps(db, actors.admin, kept))[0] != (await _steps(db, actors.admin, none))[0]


@pytest.mark.parametrize(
    "key",
    [
        "gender",
        "hair",
        "eyes",
        "ethnicity",
        "nationality",
        "breasts",
        "height",
        "age",
        "people",
        "tags",
        "usernames",
        "sites",
        "collections",
        "photo_sets",
        "songs",
        "filed_under",
        "filed_under_box",
        "tagged_with",
        "tagged_with_box",
        "named_as",
        "named_as_box",
        "unnamed_face",
        "left_out",
        "same_music",
    ],
)
async def test_a_value_only_kept_files_carry_costs_a_guest_what_one_nobody_does(
    actors: Actors, library: dict[str, Any], temp_db: Database, key: str
) -> None:
    kept, none = _pairs(library)[key]
    await _same_for_a_guest(temp_db, actors, _filtered(kept), _filtered(none))


_LARGE = (
    ("viewer_assets", "viewer_assets", "101742 20349 1"),
    ("asset_people", "sqlite_autoindex_asset_people_1", "44502 1 1"),
    ("asset_people", "ix_asset_people_person", "44502 58"),
    ("people", "sqlite_autoindex_people_1", "1086 1"),
    ("asset_usernames", "sqlite_autoindex_asset_usernames_1", "8987 1 1"),
    ("usernames", "sqlite_autoindex_usernames_1", "220 1"),
    ("usernames", "ix_usernames_site", "220 9"),
)


@pytest.mark.parametrize("key", ["nationality", "height", "filed_under", "people"])
async def test_a_large_librarys_planner_builds_nothing_over_kept_files(
    actors: Actors, library: dict[str, Any], temp_db: Database, key: str
) -> None:
    """A real library's statistics, under which SQLite builds filters over a joined table."""
    await temp_db.execute("ANALYZE")
    for table, index, stat in _LARGE:
        await temp_db.execute("DELETE FROM sqlite_stat1 WHERE tbl = ? AND idx = ?", (table, index))
        await temp_db.execute("INSERT INTO sqlite_stat1 VALUES (?, ?, ?)", (table, index, stat))
    kept, none = _pairs(library)[key]
    await _same_for_a_guest(temp_db, actors, _filtered(kept), _filtered(none))


async def test_a_folder_of_kept_files_costs_a_guest_what_no_folder_does(
    actors: Actors, library: dict[str, Any], temp_db: Database
) -> None:
    folder = Where("folder", (0,))
    await _same_for_a_guest(
        temp_db, actors, _filtered(folder, library["folder"]), _filtered(folder, new_id())
    )


async def test_kept_files_made_by_a_pmv_creator_cost_a_guest_nothing(
    actors: Actors, library: dict[str, Any], temp_db: Database
) -> None:
    asked = _filtered(Where("pmv_creator"))
    before = await _steps(temp_db, actors.guest, asked)
    admin = await _steps(temp_db, actors.admin, asked)
    await temp_db.execute("UPDATE people SET pmv_creator = 1 WHERE id = ?", (library["person"],))
    assert await _steps(temp_db, actors.guest, asked) == before
    assert (await _steps(temp_db, actors.admin, asked))[0] != admin[0]


async def _ids(access: Repository, viewer: Viewer, node: Node) -> set[str]:
    page = await access.visible_assets(viewer, limit=200, asset_filter=_filtered(node))
    return {item.asset.id for item in page.items}


async def test_a_guest_finds_what_the_admin_finds_among_their_files(
    access: Repository, actors: Actors, library: dict[str, Any]
) -> None:
    shown, kept = library["shown"], library["kept"]
    asked: list[Node] = [
        Where("people", (library["seen_person"],)),
        Where("nationality", ("seen",)),
        Not(Where("nationality", ("SEEN",))),
        Where("same_music", (shown[1],) * 3),
        Where("same_music", (kept[1],) * 3),
    ]
    found = [await _ids(access, actors.guest, node) for node in asked]
    assert found == [{shown[0]}, {shown[0]}, {shown[1]}, {shown[0]}, set(shown)]
    for node, theirs in zip(asked, found, strict=True):
        assert theirs == await _ids(access, actors.admin, node) & set(shown)
    assert await _ids(access, actors.guest, Where("same_music", (kept[0],) * 3)) == set()


def test_words_beside_a_picked_value_are_still_matched_once() -> None:
    picked = AssetFilter(
        where=AllOf((Where("gender", ("ZQXV",)), Where("text_match"))), text="zqxv"
    )
    assert words_of(picked) == ("zqxv", ("text_match",))
