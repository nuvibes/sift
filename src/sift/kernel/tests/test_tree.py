# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shape of the library, read for nobody.

The unscoped reads a background pass needs, which the access layer (what may THIS user see) cannot
give; a feature showing any of it resolves it through the access layer first. The arithmetic is
done in SQL, so a caller is never handed the file-to-folder map of a whole library to add up.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import tree as tree_module
from sift.kernel.content.tree import FolderTotals, TreeReads
from sift.kernel.db import Database

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


@pytest.fixture
async def tree(temp_db: Database) -> TreeReads:
    """Two libraries, a nest of folders, and files in three of them.

        one/            (root)          nothing directly in it
          shoot/                        two files
            outtakes/                   one file
        two/            (root)          one file directly in it

    The empty folder must not appear in the tree.
    """
    await temp_db.initialize_schema()
    async with temp_db.write() as c:
        for root, name in (("r1", "one"), ("r2", "two")):
            await c.execute(
                "INSERT INTO library_roots (id, name, abs_path, kind, created_at)"
                " VALUES (?, ?, ?, 'local', ?)",
                (root, name, f"/library/{name}", _EPOCH),
            )
        for folder, root, parent, rel_path, name in (
            ("f-top", "r1", None, "", "one"),
            ("f-shoot", "r1", "f-top", "shoot", "shoot"),
            ("f-cuts", "r1", "f-shoot", "shoot/outtakes", "outtakes"),
            ("f-two", "r2", None, "", "two"),
        ):
            await c.execute(
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, ?, ?, ?)",
                (folder, root, parent, rel_path, name),
            )
        for index, (asset, folder, root, rel_path, filename) in enumerate(
            (
                ("a1", "f-shoot", "r1", "shoot/001.jpg", "001.jpg"),
                ("a2", "f-shoot", "r1", "shoot/002.jpg", "002.jpg"),
                ("a3", "f-cuts", "r1", "shoot/outtakes/003.jpg", "003.jpg"),
                ("a4", "f-two", "r2", "004.jpg", "004.jpg"),
            )
        ):
            await c.execute(
                "INSERT INTO assets (id, identity, media_type, original_filename, added_at)"
                " VALUES (?, ?, 'image', ?, ?)",
                (asset, f"digest-{asset}", filename, _EPOCH + index),
            )
            await c.execute(
                "INSERT INTO asset_locations"
                " (id, asset_id, root_id, folder_id, rel_path, filename,"
                "  status, first_seen_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'present', ?, ?)",
                (f"l-{asset}", asset, root, folder, rel_path, filename, _EPOCH, _EPOCH),
            )
    return TreeReads(temp_db)


async def test_only_the_folders_holding_files_are_in_the_tree(tree: TreeReads) -> None:
    """A folder holding nothing directly is not in the tree."""
    found = {node.id: node for node in await tree.folders_with_files()}

    assert set(found) == {"f-shoot", "f-cuts", "f-two"}
    assert found["f-shoot"].files == 2
    assert found["f-cuts"].files == 1


async def test_a_folder_carries_the_chain_of_names_above_it(tree: TreeReads) -> None:
    """The chain of names above a folder comes from one recursive statement, not a walk per
    folder."""
    found = {node.id: node for node in await tree.folders_with_files()}

    assert found["f-cuts"].chain == ("one", "shoot", "outtakes")
    assert found["f-cuts"].name == "outtakes"
    assert found["f-two"].chain == ("two",)


async def test_the_newest_file_in_a_folder_is_reported(tree: TreeReads) -> None:
    """The newest file in a folder, read to decide whether it has anything new."""
    found = {node.id: node for node in await tree.folders_with_files()}

    assert found["f-shoot"].newest == _EPOCH + 1


async def test_everything_under_a_folder_includes_what_is_below_it(tree: TreeReads) -> None:
    """A subtree, read off the folder's ancestry, so two libraries' `shoot` folders are two
    answers."""
    assert sorted(await tree.assets_under("f-shoot")) == ["a1", "a2", "a3"]
    assert sorted(await tree.assets_under("f-cuts")) == ["a3"]
    assert await tree.assets_under("f-nowhere") == [], "a folder that is not there holds nothing"


async def test_many_folders_read_together_answer_as_each_read_alone(
    tree: TreeReads, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every folder asked is a key, a repeated one once, one not there with nothing; in batches too."""
    monkeypatch.setattr(tree_module, "_FOLDERS_PER_ASK", 2)
    asked = ["f-shoot", "f-cuts", "f-shoot", "f-nowhere"]
    under = await tree.assets_under_many(asked)
    assert {key: sorted(found) for key, found in under.items()} == {
        "f-shoot": ["a1", "a2", "a3"],
        "f-cuts": ["a3"],
        "f-nowhere": [],
    }


async def test_the_filenames_in_one_folder_are_the_ones_sitting_in_it(tree: TreeReads) -> None:
    """Directly in it: a convention shared by parent and child is two facts."""
    assert sorted(await tree.filenames_in("f-shoot")) == ["001.jpg", "002.jpg"]
    assert await tree.filenames_in("f-top") == []


async def test_several_folders_filenames_come_back_in_one_read(tree: TreeReads) -> None:
    """Several folders' filenames in one read; a folder with none is absent."""
    found = await tree.filenames_by_folder(["f-shoot", "f-cuts", "f-top"])

    assert sorted(found["f-shoot"]) == ["001.jpg", "002.jpg"]
    assert found["f-cuts"] == ["003.jpg"]
    assert "f-top" not in found


async def test_asking_about_more_folders_than_one_statement_may_bind(tree: TreeReads) -> None:
    """More folders than one statement may bind are served by several statements."""
    many = [f"absent-{index}" for index in range(1200)]

    found = await tree.filenames_by_folder([*many, "f-cuts"])

    assert found == {"f-cuts": ["003.jpg"]}


def test_a_short_list_is_one_batch_and_a_long_one_is_several() -> None:
    """The split into batches, without a database."""
    from sift.kernel.content.tree import _CHUNK, _batched

    assert _batched([]) == []
    assert _batched(["a", "b"]) == [["a", "b"]]
    assert [len(batch) for batch in _batched([str(n) for n in range(_CHUNK + 1)])] == [_CHUNK, 1]


async def test_the_files_in_a_folder_come_back_with_their_ids(tree: TreeReads) -> None:
    """The files in a folder come back as (id, name) pairs, so a name says which file it was."""
    assert sorted(await tree.files_in("f-shoot")) == [("a1", "001.jpg"), ("a2", "002.jpg")]


async def test_the_same_files_come_back_on_a_connection_the_caller_already_holds(
    temp_db: Database, tree: TreeReads
) -> None:
    """The connection-taking form answers what the other does: inside a write transaction it is the
    only one a caller may use, the write guard not being reentrant."""
    async with temp_db.write() as connection:
        inside = await tree.files_in_on(connection, "f-shoot")

    assert sorted(inside) == [("a1", "001.jpg"), ("a2", "002.jpg")]
    assert sorted(inside) == sorted(await tree.files_in("f-shoot"))


async def test_where_a_folder_sits_or_that_it_has_gone(tree: TreeReads) -> None:
    assert await tree.place_of("f-cuts") == ("r1", "shoot/outtakes")
    assert await tree.place_of("never-existed") is None


async def test_a_folder_is_said_by_its_path_or_by_its_name_at_the_top(
    tree: TreeReads, temp_db: Database
) -> None:
    async with temp_db.write() as connection:
        assert await tree.said_on(connection, "f-cuts") == "shoot/outtakes"
        assert await tree.said_on(connection, "f-top") == "one"
        assert await tree.said_on(connection, "never-existed") is None


async def test_a_folder_is_found_by_its_name_inside_its_parent(tree: TreeReads) -> None:
    assert await tree.folder_named("f-shoot", "outtakes") == ("f-cuts", "r1", "shoot/outtakes")
    assert await tree.folder_named("f-top", "outtakes") is None


async def test_the_folders_inside_one_are_every_depth_below_it_and_nothing_beside_it(
    tree: TreeReads, temp_db: Database
) -> None:
    async with temp_db.write() as c:
        # A neighbour sharing a prefix is beside the folder, not in it.
        await c.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
            " VALUES ('f-near', 'r1', 'f-top', 'shoot-two', 'shoot-two')"
        )
    assert await tree.folders_inside("r1", "shoot") == [("f-cuts", "shoot/outtakes")]
    assert await tree.folders_inside("r1", "shoot/outtakes") == []
    assert await tree.folders_inside("r1", "") == []


async def test_every_folder_is_keyed_by_where_it_sits(tree: TreeReads) -> None:
    """Every folder is keyed, those above the files included."""
    found = await tree.folder_ids()

    assert found[("r1", "shoot")] == "f-shoot"
    assert found[("r1", "")] == "f-top", "a folder holding nothing directly is still a folder"
    assert found[("r2", "")] == "f-two"


async def test_per_file_numbers_are_added_up_per_folder_by_the_database(tree: TreeReads) -> None:
    """Per-file numbers are folded per folder by the database: the sum, the largest stamp (not the
    last written), and how many of the folder's files were in the counted set."""
    found = await tree.totals_by_folder(
        {"a1": (2, 500), "a2": (3, 100), "a3": (7, 900)}, counted={"a1", "a3"}
    )

    assert found["f-shoot"] == FolderTotals(summed=5, newest=500, counted=1)
    assert found["f-cuts"] == FolderTotals(summed=7, newest=900, counted=1)
    # A folder whose files are all absent still comes back, with zeroes.
    assert found["f-two"] == FolderTotals(summed=0, newest=0, counted=0)


async def test_a_file_that_is_only_counted_carries_no_value(tree: TreeReads) -> None:
    """A file only counted adds to the count and not to the sum."""
    found = await tree.totals_by_folder({"a1": (4, 50)}, counted={"a1", "a2"})

    assert found["f-shoot"] == FolderTotals(summed=4, newest=50, counted=2)


async def test_folding_nothing_answers_zero_for_every_folder(tree: TreeReads) -> None:
    """Folding nothing answers zeroes for every folder: absence would read as "no folder"."""
    found = await tree.totals_by_folder({}, counted=set())

    assert set(found) == {"f-shoot", "f-cuts", "f-two"}
    assert set(found.values()) == {FolderTotals(summed=0, newest=0, counted=0)}


async def test_a_second_fold_does_not_see_the_first_ones_numbers(tree: TreeReads) -> None:
    """A second fold does not see the first's rows in the reused temporary table."""
    await tree.totals_by_folder({"a1": (100, 1)}, counted={"a1"})

    found = await tree.totals_by_folder({"a2": (1, 2)}, counted={"a2"})

    assert found["f-shoot"] == FolderTotals(summed=1, newest=2, counted=1)
