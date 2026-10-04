# SPDX-License-Identifier: AGPL-3.0-or-later
"""What may be a library root, and what may happen to the folders in one.

Two of these tests are the reason the module exists. A root that overlaps another one makes the
question "which permissions apply to this file" have two answers; a root that holds Sift's own
directories makes Sift write thumbnails into somebody's media folder while working exactly as
designed. Both are refused where a root is created, so neither can be reached by any caller, and
both are checked here in both directions: the mirror case is the half that gets forgotten.

The tests run against a real database and real directories on disk. A move renames a real
directory, so a fake filesystem would prove nothing about the one thing in Sift that touches a
person's files.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

# For its side effect: registering the table the ledger is written to, so this test's database
# has it. A kernel process that has never imported the workbench slice genuinely does not.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role
from sift.kernel.access.history_events import events_of_entity, events_recent
from sift.kernel.config import Settings
from sift.kernel.content import (
    LibraryError,
    LibraryStore,
    NotAFolder,
    NotWritable,
    ReservedPath,
    RootKind,
    RootOverlap,
    check_name,
    check_not_reserved,
    overlaps,
    subtree_prefix,
)
from sift.kernel.content import library as library_module
from sift.kernel.content.library import names_for_assets, names_for_roots
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.testing.fixtures import Actors, create_user, hide

pytestmark = pytest.mark.usefixtures("library_store")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A directory to point a root at. Not `tmp_path` itself, which holds Sift's own dirs."""
    directory = tmp_path / "library"
    directory.mkdir()
    return directory


async def make_asset(database: Database, root_id: str, folder_id: str | None, path: str) -> str:
    """A file Sift knows about, sitting at `path`. Returns the asset id."""
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 1)",
        (asset_id, f"digest-{asset_id}"),
    )
    await database.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
        (new_id(), asset_id, root_id, folder_id, path, path.rsplit("/", 1)[-1]),
    )
    return asset_id


async def rel_paths(database: Database) -> list[str]:
    rows = await database.fetch_all("SELECT rel_path FROM asset_locations ORDER BY rel_path")
    return [str(row["rel_path"]) for row in rows]


# --- the rules, before any database is involved ----------------------------------------------


def test_a_folder_inside_another_overlaps_it() -> None:
    assert overlaps(Path("/media/clips"), Path("/media")) is True


def test_a_folder_that_contains_another_overlaps_it() -> None:
    """The half that reads backwards, and the half that gets forgotten."""
    assert overlaps(Path("/media"), Path("/media/clips")) is True


def test_a_folder_overlaps_itself() -> None:
    assert overlaps(Path("/media"), Path("/media")) is True


def test_siblings_do_not_overlap() -> None:
    assert overlaps(Path("/media/clips"), Path("/media/photos")) is False


def test_a_name_that_shares_a_prefix_is_not_inside_anything() -> None:
    """`/media2` is not in `/media`, and a prefix comparison on strings would say it was."""
    assert overlaps(Path("/media2"), Path("/media")) is False


def test_a_root_folder_has_an_empty_prefix_because_everything_is_under_it() -> None:
    assert subtree_prefix("") == ""
    assert subtree_prefix("clips") == "clips/"


def test_a_root_needs_a_name() -> None:
    with pytest.raises(LibraryError):
        check_name("   ")


def test_a_name_is_trimmed() -> None:
    assert check_name("  Videos  ") == "Videos"


def test_a_name_cannot_run_on_forever() -> None:
    with pytest.raises(LibraryError):
        check_name("x" * 500)


def test_a_name_cannot_carry_a_line_break() -> None:
    """A name is rendered next to other names, and a log line is one line."""
    with pytest.raises(LibraryError):
        check_name("Videos\nand more")


# --- pointing Sift at a directory ------------------------------------------------------------


async def test_a_new_root_gets_a_folder_row_standing_for_itself(
    library_store: LibraryStore, library: Path
) -> None:
    """Without it the tree has no top and a file dropped on the root has nowhere to land."""
    root = await library_store.create_root(name="Videos", abs_path=library)

    folder = await library_store.root_folder(root.id)
    assert folder is not None
    assert folder.parent_id is None
    assert folder.rel_path == ""
    assert folder.name == "Videos"
    assert folder.root_id == root.id


async def test_a_root_on_this_machine_is_local(library_store: LibraryStore, library: Path) -> None:
    root = await library_store.create_root(name="Videos", abs_path=library)
    assert root.kind is RootKind.LOCAL


async def test_a_relative_path_is_refused(library_store: LibraryStore) -> None:
    with pytest.raises(NotAFolder):
        await library_store.create_root(name="Videos", abs_path=Path("media/clips"))


async def test_a_folder_that_is_not_there_is_refused(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    with pytest.raises(NotAFolder):
        await library_store.create_root(name="Videos", abs_path=tmp_path / "nowhere")


async def test_a_file_is_not_a_folder(library_store: LibraryStore, library: Path) -> None:
    clip = library / "clip.mp4"
    clip.write_bytes(b"data")

    with pytest.raises(NotAFolder):
        await library_store.create_root(name="Videos", abs_path=clip)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Creating a symbolic link needs a privilege an ordinary Windows account does not hold "
        "(WinError 1314). The same resolution is proved there by the junction test below, which "
        "needs no privilege"
    ),
)
async def test_a_root_is_stored_as_the_directory_it_really_is(
    library_store: LibraryStore, tmp_path: Path, library: Path
) -> None:
    """A symlink is followed once, here, and the resolved path is what is kept.

    Everything downstream compares stored paths (whether two roots overlap, whether a root holds
    the cache), and two names for one directory would compare as different while behaving as the
    same.
    """
    link = tmp_path / "shortcut"
    link.symlink_to(library)

    root = await library_store.create_root(name="Videos", abs_path=link)

    assert root.abs_path == str(library)


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows reparse point")
async def test_a_root_behind_a_junction_is_stored_as_the_directory_it_really_is(
    library_store: LibraryStore, tmp_path: Path, library: Path
) -> None:
    """The same rule, by the mechanism a Windows user actually has.

    `mklink /J` needs no privilege, and unlike a symbolic link a junction reports
    `is_symlink() == False`, so nothing that looks for a link notices one. What keeps the stored
    path honest is resolving it, which is the half worth proving on the platform Sift ships on.
    """
    link = tmp_path / "shortcut"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(library)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr

    root = await library_store.create_root(name="Videos", abs_path=link)

    assert root.abs_path == str(library)
    with pytest.raises(RootOverlap):
        await library_store.create_root(name="Again", abs_path=library)


# --- the roots do not overlap ----------------------------------------------------------------


async def test_a_root_inside_an_existing_root_is_refused(
    library_store: LibraryStore, library: Path
) -> None:
    inner = library / "clips"
    inner.mkdir()
    await library_store.create_root(name="Videos", abs_path=library)

    with pytest.raises(RootOverlap):
        await library_store.create_root(name="Clips", abs_path=inner)


async def test_a_root_that_contains_an_existing_root_is_refused(
    library_store: LibraryStore, library: Path
) -> None:
    """The same mistake written backwards, and the one an interface will not think to stop."""
    inner = library / "clips"
    inner.mkdir()
    await library_store.create_root(name="Clips", abs_path=inner)

    with pytest.raises(RootOverlap):
        await library_store.create_root(name="Videos", abs_path=library)


async def test_the_same_folder_twice_is_refused(library_store: LibraryStore, library: Path) -> None:
    await library_store.create_root(name="Videos", abs_path=library)

    with pytest.raises(RootOverlap):
        await library_store.create_root(name="Videos again", abs_path=library)


async def test_two_roots_beside_each_other_are_fine(
    library_store: LibraryStore, tmp_path: Path, library: Path
) -> None:
    """The rule refuses an ambiguous parent, not a second library."""
    other = tmp_path / "photos"
    other.mkdir()

    await library_store.create_root(name="Videos", abs_path=library)
    await library_store.create_root(name="Photos", abs_path=other)

    assert [root.name for root in await library_store.roots()] == ["Videos", "Photos"]


async def test_two_roots_added_at_once_cannot_both_win(
    library_store: LibraryStore, library: Path
) -> None:
    """The overlap rule is checked inside the write transaction, and this is why.

    Checked before it, two roots added at the same moment each read a library that does not
    contain the other, both pass, and the rule holds everywhere except the case it exists for.
    """
    inner = library / "clips"
    inner.mkdir()

    results = await asyncio.gather(
        library_store.create_root(name="Videos", abs_path=library),
        library_store.create_root(name="Clips", abs_path=inner),
        return_exceptions=True,
    )

    refused = [result for result in results if isinstance(result, RootOverlap)]
    assert len(refused) == 1, f"exactly one of the two must be refused, got {results}"
    assert len(await library_store.roots()) == 1


# --- the roots are not Sift's own directories ------------------------------------------------


async def test_sifts_own_directory_cannot_be_a_root(
    library_store: LibraryStore, settings: Settings
) -> None:
    settings.cache_dir.mkdir(parents=True)

    with pytest.raises(ReservedPath):
        await library_store.create_root(name="Cache", abs_path=settings.cache_dir)


async def test_a_root_that_would_contain_sifts_own_directory_is_refused(
    library_store: LibraryStore, tmp_path: Path, settings: Settings
) -> None:
    """The direction that matters most: nothing else stops Sift writing into a library.

    With the cache directory inside a root, thumbnails and transcoded segments appear beside the
    originals, put there by a system doing exactly what it was built to do.
    """
    settings.cache_dir.mkdir(parents=True)

    with pytest.raises(ReservedPath):
        await library_store.create_root(name="Everything", abs_path=tmp_path)


async def test_a_root_inside_sifts_own_directory_is_refused(
    library_store: LibraryStore, settings: Settings
) -> None:
    inside = settings.cache_dir / "media"
    inside.mkdir(parents=True)

    with pytest.raises(ReservedPath):
        await library_store.create_root(name="Media", abs_path=inside)


async def test_every_directory_sift_writes_to_is_covered(
    library_store: LibraryStore, settings: Settings
) -> None:
    """Each one, not just the two the first test happened to name.

    The rule reads `settings.managed_dirs` rather than a list written out beside it, so a
    directory added there is protected the moment it is added. This is what says so.
    """
    for owned in settings.managed_dirs:
        owned.mkdir(parents=True, exist_ok=True)
        with pytest.raises(ReservedPath):
            await library_store.create_root(name="Mine", abs_path=owned)


async def test_an_id_that_is_not_an_id_is_simply_not_a_root(library_store: LibraryStore) -> None:
    assert await library_store.get_root("../../etc") is None
    assert await library_store.delete_root("../../etc", actor=Actor.sift("folder")) is False


# --- removing a root -------------------------------------------------------------------------


async def test_removing_a_root_forgets_the_rows_and_keeps_the_files(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    """Sift never deletes anybody's media. Removing a root is Sift forgetting, not deleting.

    The assets stay too. An asset with no location left is content Sift knows about and cannot
    currently see (the same state as an unplugged drive), and it comes back with its tags still
    attached the moment those bytes turn up anywhere Sift can read.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    clips = await library_store.upsert_folder(root.id, "clips")
    clip = library / "clips" / "a.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"bytes")
    await make_asset(temp_db, root.id, clips.id, "clips/a.mp4")

    assert await library_store.delete_root(root.id, actor=Actor.sift("folder")) is True

    folders = await temp_db.fetch_all("SELECT COUNT(*) AS c FROM folders")
    locations = await temp_db.fetch_all("SELECT COUNT(*) AS c FROM asset_locations")
    assets = await temp_db.fetch_all("SELECT COUNT(*) AS c FROM assets")
    assert folders[0]["c"] == 0
    assert locations[0]["c"] == 0
    assert assets[0]["c"] == 1
    assert clip.read_bytes() == b"bytes"
    # And the moment its last place went is written on it, which is what keeps the promise made
    # at the remove (add the folder again and everything comes back) against the Maintenance
    # card that throws stranded records away. See `stranded_asset_ids`.
    (row,) = await temp_db.fetch_all("SELECT stranded_at FROM assets")
    assert row["stranded_at"] is not None


async def test_removing_an_EMPTY_root_strands_nothing_and_writes_nothing(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    """A root with nothing indexed under it is removed without a stranding write.

    The write beside it stamps the moment an asset's LAST place went, so the Maintenance card that
    throws stranded records away can keep the promise made at the remove for a while. An empty root
    strands nobody, and a stamp written over every asset in the library because a folder somebody
    added by mistake was taken away again would start that clock on files that never moved.
    """
    root = await library_store.create_root(name="Empty", abs_path=library)
    before = await temp_db.fetch_all(
        "SELECT COUNT(*) AS c FROM assets WHERE stranded_at IS NOT NULL"
    )

    assert await library_store.delete_root(root.id, actor=Actor.sift("folder")) is True

    after = await temp_db.fetch_all(
        "SELECT COUNT(*) AS c FROM assets WHERE stranded_at IS NOT NULL"
    )
    assert (before[0]["c"], after[0]["c"]) == (0, 0)
    assert await library_store.roots() == []


async def test_removing_a_root_that_is_not_there_is_not_an_error(
    library_store: LibraryStore,
) -> None:
    assert await library_store.delete_root(new_id(), actor=Actor.sift("folder")) is False


async def test_the_folders_of_a_root_can_be_listed_before_it_goes(
    library_store: LibraryStore, library: Path
) -> None:
    """Deleting a root cascades its folders away and nothing cascades the grants naming them.

    Those grants carry no foreign key (the id beside them could belong to any of several tables),
    so the caller has to drop them deliberately, and this is what tells it which ids to drop.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    await library_store.upsert_folder(root.id, "clips/holiday")

    ids = await library_store.folder_ids_in_root(root.id)

    root_folder = await library_store.root_folder(root.id)
    assert root_folder is not None
    assert root_folder.id in ids, "the root's own folder row is grantable and must be listed"
    assert len(ids) == 3


# --- folders ---------------------------------------------------------------------------------


async def test_a_folder_deep_in_a_tree_creates_every_folder_above_it(
    library_store: LibraryStore, library: Path
) -> None:
    """A scan walks straight to a file. Each folder above it is somewhere a permission attaches."""
    root = await library_store.create_root(name="Videos", abs_path=library)

    leaf = await library_store.upsert_folder(root.id, "clips/holiday/2024")

    assert leaf.rel_path == "clips/holiday/2024"
    assert leaf.name == "2024"

    top = await library_store.root_folder(root.id)
    assert top is not None
    under = await library_store.folders_under(top)
    assert [folder.rel_path for folder in under] == [
        "clips",
        "clips/holiday",
        "clips/holiday/2024",
    ]


async def test_every_folder_in_a_library_comes_back_in_one_answer(
    library_store: LibraryStore, library: Path
) -> None:
    """What the catch-up at start reads. It asks about each folder in turn and needs all of them in
    front of it, so a partial answer there is a folder nothing ever looks at again."""
    root = await library_store.create_root(name="Videos", abs_path=library)
    await library_store.upsert_folder(root.id, "clips/holiday")
    await library_store.upsert_folder(root.id, "photos")

    everything = await library_store.folders_in_root(root.id)

    assert sorted(folder.rel_path for folder in everything) == [
        "",
        "clips",
        "clips/holiday",
        "photos",
    ]


async def test_what_a_folder_looked_like_when_it_was_walked_is_remembered(
    library_store: LibraryStore, library: Path
) -> None:
    """Written by the scan and read by the catch-up at start.

    `None` is stored rather than skipped when the directory could not be stat'd: a folder Sift could
    not look at is one it cannot rule out later, and leaving the old value would say the opposite.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    folder = await library_store.upsert_folder(root.id, "clips")

    await library_store.record_folder_mtime(folder.id, 1_700_000_000.5)
    written = await library_store.get_folder(folder.id)
    assert written is not None
    assert written.seen_mtime == 1_700_000_000.5

    await library_store.record_folder_mtime(folder.id, None)
    cleared = await library_store.get_folder(folder.id)
    assert cleared is not None
    assert cleared.seen_mtime is None, "a folder that could not be looked at still says so"


async def test_an_id_that_is_not_an_id_is_not_given_a_walk_time(
    library_store: LibraryStore,
) -> None:
    """The same refusal every other id-taking method here makes, and for the same reason: this is
    handed an id read out of a row, and a value that is not one belongs to no folder."""
    await library_store.record_folder_mtime("../../etc", 1.0)


async def test_upserting_the_same_folder_twice_is_the_same_folder(
    library_store: LibraryStore, library: Path
) -> None:
    """A scan calls this for every directory it walks, every time it runs."""
    root = await library_store.create_root(name="Videos", abs_path=library)

    first = await library_store.upsert_folder(root.id, "clips")
    second = await library_store.upsert_folder(root.id, "clips")

    assert first.id == second.id


async def test_scanning_a_folder_again_does_not_unhide_it(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    """The scan must not disturb who has hidden a folder.

    A folder somebody hid is walked by every scan that runs afterwards. Who hid it lives in its own
    table rather than on the folder row, so the upsert cannot reach it, and this is what says so:
    a statement that could reach it would have a scan silently unhide it.
    """
    someone = await create_user(temp_db, Role.ADMIN)
    root = await library_store.create_root(name="Videos", abs_path=library)
    folder = await library_store.upsert_folder(root.id, "private")
    await library_store.set_root_hidden(someone.id, root.id, hidden=True)
    await hide(temp_db, "folder", folder.id, someone.id)

    await library_store.upsert_folder(root.id, "private")

    still = await temp_db.fetch_one(
        "SELECT hidden FROM folder_user_state WHERE folder_id = ? AND user_id = ?",
        (folder.id, someone.id),
    )
    assert still is not None
    assert still["hidden"] == 1
    assert await library_store.hidden_roots(someone.id) == {root.id}


async def test_a_folder_is_not_under_itself(library_store: LibraryStore, library: Path) -> None:
    root = await library_store.create_root(name="Videos", abs_path=library)
    clips = await library_store.upsert_folder(root.id, "clips")
    await library_store.upsert_folder(root.id, "clips/holiday")

    under = await library_store.folders_under(clips)

    assert [folder.rel_path for folder in under] == ["clips/holiday"]


# --- moving a folder: the rows here, the directory through the caller's seam ------------------
#
# `move_folder` does not rename the directory itself: it takes that operation as an argument,
# because changing a file somebody else put there goes through one feature that checks the folder
# was handed over read-write. What is here is the half this module owns: the rules about
# which moves are allowed at all, the rewriting of every row under the folder, and the transaction
# the two happen in. The directory move is stood in for below.
#
# Every refusal names the message it expects, and every one of them puts the real directories on
# disk first. Both matter: a refusal test that does not say which refusal it wants is testing that
# something, somewhere, said no.


def rename_directory(source: Path, destination: Path) -> None:
    """A caller's directory move, reduced to the part this module depends on."""
    os.rename(source, destination)


def refuse_to_rename(source: Path, destination: Path) -> None:
    """A caller's directory move that will not do it, whatever the reason."""
    raise LibraryError("Sift could not move that folder.")


# --- the sweep -------------------------------------------------------------------------------


async def test_the_enumerator_yields_every_present_file_a_batch_at_a_time(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    """A scan ends by marking what it did not see as missing, and it cannot ask for a list.

    A library of a million files is a million rows, and reading them into memory to compare
    against a directory walk is a design that works on the machine it was written on.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    folder = await library_store.upsert_folder(root.id, "clips")
    for index in range(5):
        await make_asset(temp_db, root.id, folder.id, f"clips/{index}.mp4")

    seen = [
        location.rel_path
        async for location in library_store.iter_locations_in_root(root.id, batch=2)
    ]

    assert sorted(seen) == [f"clips/{index}.mp4" for index in range(5)]


async def test_the_enumerator_skips_what_is_already_known_to_be_missing(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    root = await library_store.create_root(name="Videos", abs_path=library)
    folder = await library_store.upsert_folder(root.id, "clips")
    await make_asset(temp_db, root.id, folder.id, "clips/here.mp4")
    await make_asset(temp_db, root.id, folder.id, "clips/gone.mp4")
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE rel_path = ?", ("clips/gone.mp4",)
    )

    seen = [location.rel_path async for location in library_store.iter_locations_in_root(root.id)]

    assert seen == ["clips/here.mp4"]


async def test_the_enumerator_stays_inside_its_root(
    library_store: LibraryStore, temp_db: Database, tmp_path: Path, library: Path
) -> None:
    elsewhere = tmp_path / "photos"
    elsewhere.mkdir()
    one = await library_store.create_root(name="Videos", abs_path=library)
    two = await library_store.create_root(name="Photos", abs_path=elsewhere)
    await make_asset(temp_db, one.id, None, "a.mp4")
    await make_asset(temp_db, two.id, None, "b.mp4")

    seen = [location.rel_path async for location in library_store.iter_locations_in_root(one.id)]

    assert seen == ["a.mp4"]


# --- the schema says the same thing the code does --------------------------------------------


async def test_the_kind_check_constraint_matches_the_enum(
    library_store: LibraryStore, temp_db: Database
) -> None:
    """A kind the code can produce and the database refuses is a crash waiting for a user."""
    row = await temp_db.fetch_one("SELECT sql FROM sqlite_master WHERE name = 'library_roots'")
    assert row is not None
    ddl = str(row["sql"])

    for kind in RootKind:
        assert f"'{kind.value}'" in ddl, f"the database refuses kind {kind.value!r}"


@pytest.mark.skipif(
    # `os.geteuid` does not exist on Windows, and this decorator runs at IMPORT time:
    # reaching for it there fails the whole module before a test is collected.
    sys.platform == "win32" or os.geteuid() == 0,
    reason="root can read a directory whose mode says it cannot, and the permission bits this relies on are accepted and then ignored by Windows, so the folder stays readable and the refusal being tested never happens on Windows",
)
async def test_a_folder_sift_cannot_read_is_refused_with_something_to_do_about_it(
    library_store: LibraryStore, library: Path
) -> None:
    """The commonest real failure: a container that cannot see the folder it was pointed at.

    Refused at once and named, rather than accepted and turned into a scan that finds nothing.
    """
    os.chmod(library, 0o000)
    try:
        with pytest.raises(NotAFolder) as refusal:
            await library_store.create_root(name="Videos", abs_path=library)
    finally:
        os.chmod(library, 0o755)  # noqa: S103

    assert "mounted" in str(refusal.value)


async def test_a_folder_can_be_read_without_a_viewer(
    library_store: LibraryStore, library: Path
) -> None:
    """The no-viewer read, which is what a scan and the downloader both use.

    Both are acting for nobody (a scan walks a root it was handed, and the downloader is filing a
    file into a folder somebody already chose), so neither has a viewer to check against. A caller
    that does have one asks the access layer instead.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    top = await library_store.root_folder(root.id)
    assert top is not None

    found = await library_store.get_folder(top.id)

    assert found is not None
    assert (found.id, found.root_id) == (top.id, root.id)
    assert await library_store.get_folder(new_id()) is None


async def test_a_malformed_folder_id_gets_no_folder(library_store: LibraryStore) -> None:
    """A caller that hands over something shaped nothing like an id gets nothing, not an error.

    The callers above pass ids they built themselves, but the check is here so a bad one is a
    `None`, not a query against a value that was never an id.
    """
    assert await library_store.get_folder("not an id") is None


async def test_requiring_the_folder_of_a_root_without_one_is_an_error(
    library_store: LibraryStore,
) -> None:
    """The same guard for a root's own folder row. A created root always has one, so this defends
    against a root left half-written by something else, reached here by asking for one that was
    never created at all."""
    async with library_store._db.write() as connection:
        with pytest.raises(LibraryError):
            await library_store._require_root_folder(connection, new_id())


def test_a_managed_directory_that_cannot_be_resolved_is_stepped_over(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory Sift owns but cannot resolve (a stale mount, a permission it has lost) is a
    startup problem, not a reason to refuse an unrelated root. It overlaps nothing, so the
    reserved-directory check steps over it rather than falling over on it."""
    unreachable = tmp_path / "unreachable"
    real_resolve = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        if self == unreachable:
            raise OSError(40, "cannot resolve")
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(type(settings), "managed_dirs", property(lambda self: (unreachable,)))

    candidate = tmp_path / "media"
    candidate.mkdir()
    check_not_reserved(candidate, settings)  # steps over the unresolvable dir, does not raise


# --- a folder Sift cannot write in is still a library ------------------------------------------
#
# Reading is all an index needs. Whether Sift may WRITE in a folder is the filesystem's answer and
# it is asked at the moment of a write; nothing about it is recorded when the folder is added.
# No per-folder consent flag is recorded or checked.


async def test_a_folder_sift_cannot_write_to_is_still_fine_as_a_root(
    library_store: LibraryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library_module, "is_writable", lambda _path: False)
    locked = tmp_path / "locked"
    locked.mkdir()

    root = await library_store.create_root(name="Locked", abs_path=locked)

    assert (await library_store.get_root(root.id)) == root


async def test_a_read_only_folder_is_still_perfectly_good_to_read(
    library_store: LibraryStore, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mounting a filesystem read-only needs privileges a test does not have, so what is faked is
    the answer the kernel gives about the mount; that answer is proved where it is read."""
    monkeypatch.setattr("sift.kernel.content.library.mount_is_readonly", lambda path: True)

    root = await library_store.create_root(name="Videos", abs_path=library)

    assert root.name == "Videos"


# --- what a job row is about -----------------------------------------------------------------
#
# A queue row that said only what kind of work it was would draw a scan of twenty files as twenty
# identical lines. The subject is looked up here, in bulk, once per page of the queue, and the
# lookup has to be tolerant, because a job outlives the thing it was queued for.


async def test_the_name_a_file_is_known_by_comes_back_for_every_id_that_still_exists(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    root = await library_store.create_root(name="Videos", abs_path=library)
    first = await make_asset(temp_db, root.id, None, "holiday.mp4")
    second = await make_asset(temp_db, root.id, None, "nested/clip.mkv")

    found = await names_for_assets(temp_db, [first, second, new_id()])

    assert found == {first: "holiday.mp4", second: "clip.mkv"}


async def test_a_file_whose_only_copy_has_gone_still_has_a_name(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    """The name it arrived under, because a row that says nothing is worse than a stale name.

    The job that wanted it has already failed or is about to; what the screen has to be able to do
    is say which file it was about.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    asset_id = await make_asset(temp_db, root.id, None, "gone.mp4")
    await temp_db.execute(
        "UPDATE assets SET original_filename = ? WHERE id = ?", ("gone.mp4", asset_id)
    )
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (asset_id,)
    )

    assert await names_for_assets(temp_db, [asset_id]) == {asset_id: "gone.mp4"}


async def test_a_file_with_no_name_anywhere_is_simply_absent(
    library_store: LibraryStore, temp_db: Database, library: Path
) -> None:
    """Rather than a row labelled with an empty string, which reads as a bug on screen."""
    root = await library_store.create_root(name="Videos", abs_path=library)
    asset_id = await make_asset(temp_db, root.id, None, "orphan.mp4")
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (asset_id,))

    assert await names_for_assets(temp_db, [asset_id]) == {}


async def test_what_each_library_folder_is_called(
    library_store: LibraryStore, temp_db: Database, tmp_path: Path
) -> None:
    first = tmp_path / "videos"
    second = tmp_path / "photos"
    first.mkdir()
    second.mkdir()
    videos = await library_store.create_root(name="Videos", abs_path=first)
    photos = await library_store.create_root(name="Photos", abs_path=second)

    found = await names_for_roots(temp_db, [videos.id, photos.id, new_id()])

    assert found == {videos.id: "Videos", photos.id: "Photos"}


async def test_asking_about_nothing_asks_the_database_nothing(temp_db: Database) -> None:
    """Both lookups take whatever the page held, and a page can hold no jobs of either kind.

    An empty list built into an `IN ()` is a syntax error in SQLite, so the short circuit is the
    difference between an empty answer and a crash on an idle queue.
    """
    assert await names_for_assets(temp_db, []) == {}
    assert await names_for_roots(temp_db, []) == {}


def test_a_folder_sift_may_not_read_is_refused_with_what_to_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal itself, on a platform whose permission bits cannot produce it.

    The test above arranges it with `chmod`, which Windows accepts and ignores. What matters is
    what the refusal SAYS: a person who has pointed Sift at a folder it cannot open needs to be
    told who owns it and, in a container, that it has to be mounted, not that a path is invalid.
    """
    folder = tmp_path / "someone-elses"
    folder.mkdir()
    monkeypatch.setattr(os, "access", lambda _path, _mode: False)

    with pytest.raises(NotAFolder, match="not allowed to read"):
        library_module.resolve_directory(folder)


def test_a_folder_sift_cannot_write_in_is_refused_a_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Managing means deleting and organising, so the answer has to come from the filesystem rather
    than from the flag. The sentence names the two things somebody can check."""
    folder = tmp_path / "read-only"
    folder.mkdir()
    monkeypatch.setattr(library_module, "is_writable", lambda _path: False)

    with pytest.raises(NotWritable, match="not allowed to write"):
        library_module.check_folder_writable(folder)


def test_a_folder_on_a_read_only_DISK_is_refused_before_the_permission_is_even_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The HARD half of the answer, and it is a different refusal from the one above.

    A directory on a filesystem mounted read-only cannot be written by Sift, by a bug in Sift, or
    by anything that has taken Sift over: the refusal happens below the process and no amount of
    being wrong up here can talk the filesystem out of it. The permission bits are a preference the
    owner of the files can change; this is not. So the sentence is a different one: it names the
    disk and says plainly that it cannot be changed from inside Sift.

    Asked FIRST, which is what this pins: a folder somebody has been given write permission on, on
    a disk that is mounted read-only, must be refused rather than offered.
    """
    folder = tmp_path / "on-a-read-only-disk"
    folder.mkdir()
    monkeypatch.setattr(library_module, "mount_is_readonly", lambda _path: True)
    monkeypatch.setattr(library_module, "is_writable", lambda _path: True)

    with pytest.raises(NotWritable, match="decided by the disk"):
        library_module.check_folder_writable(folder)


# --- the refusals the folder verbs are made of ------------------------------------------------
#
# Each of these is a way the store says no. They are separated from the verbs' own tests because
# what they guard is the store being handed something by a caller that got it wrong: an id that is
# not an id, a row that is gone, a destination already occupied. Every one of them is the
# difference between a refusal and a query run against a value that was never an id.


async def test_repointing_something_that_is_not_an_id_is_refused(
    library_store: LibraryStore, library: Path
) -> None:
    assert await library_store.repoint_root("not an id", library) is None


async def test_repointing_a_library_that_is_not_there_is_refused(
    library_store: LibraryStore, library: Path
) -> None:
    """A well-formed id for a row that has gone. The path is checked before the row is looked up,
    so this reaches the lookup rather than falling out of the shape check above it."""
    assert await library_store.repoint_root(new_id(), library) is None


async def test_the_files_of_something_that_is_not_a_folder_id_are_none_rather_than_an_error(
    library_store: LibraryStore,
) -> None:
    assert await library_store.locations_in_folder("not an id") == []


async def test_the_row_standing_for_the_library_itself_does_not_move(
    library_store: LibraryStore, library: Path
) -> None:
    """Moving a library is re-pointing it, which is a different operation with its own checks,
    so the row whose path is the empty string refuses rather than rewriting every path under it."""
    root = await library_store.create_root(name="Videos", abs_path=library)
    itself = await library_store.root_folder(root.id)
    assert itself is not None

    assert await library_store.move_folder(itself, "somewhere", actor=Actor.sift("folder")) is None


async def test_a_folder_cannot_move_onto_one_that_is_already_there(
    library_store: LibraryStore, library: Path
) -> None:
    """Two folders at one path is a tree with no answer to "what is in here", and the store is the
    last place that can say so: the caller has already decided this is what somebody asked for."""
    root = await library_store.create_root(name="Videos", abs_path=library)
    moving = await library_store.upsert_folder(root.id, "clips")
    await library_store.upsert_folder(root.id, "keep")

    assert await library_store.move_folder(moving, "keep", actor=Actor.sift("folder")) is None


async def test_a_folder_cannot_move_under_one_that_does_not_exist(
    library_store: LibraryStore, library: Path
) -> None:
    """A folder's place is recorded twice, as a path and as a parent. With nothing to be the parent
    the move would leave it sitting under a heading it is not inside, so it is refused here rather
    than inventing the missing row, which would be the store deciding where somebody's folder
    went."""
    root = await library_store.create_root(name="Videos", abs_path=library)
    moving = await library_store.upsert_folder(root.id, "clips")

    assert (
        await library_store.move_folder(moving, "nowhere/clips", actor=Actor.sift("folder")) is None
    )


async def test_removing_something_that_is_not_a_folder_id_is_refused(
    library_store: LibraryStore,
) -> None:
    assert await library_store.remove_folder("not an id") is False


async def test_removing_a_folder_that_is_not_there_is_refused(
    library_store: LibraryStore,
) -> None:
    """Well-formed and gone. The answer says whether a row was removed, so a caller cannot read
    "nothing to do" as "done"."""
    assert await library_store.remove_folder(new_id()) is False


async def test_a_folder_that_is_there_is_removed(
    library_store: LibraryStore, library: Path
) -> None:
    root = await library_store.create_root(name="Videos", abs_path=library)
    folder = await library_store.upsert_folder(root.id, "clips")

    assert await library_store.remove_folder(folder.id) is True
    assert await library_store.get_folder(folder.id) is None


async def test_a_library_sift_may_only_read_is_repointed_without_asking_to_write(
    library_store: LibraryStore, tmp_path: Path, library: Path
) -> None:
    """Whether Sift may write in a folder is asked at the moment of a write, never here.

    A library is re-pointed at a folder it could never write in, and that is fine: reading is all
    an index needs, and a delete there is refused when somebody asks for one.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    moved = tmp_path / "moved"
    moved.mkdir()

    repointed = await library_store.repoint_root(root.id, moved)

    assert repointed is not None
    assert repointed.abs_path == str(moved)


async def test_another_library_somewhere_else_does_not_block_a_repoint(
    library_store: LibraryStore, tmp_path: Path, library: Path
) -> None:
    """The overlap check walks every other library, and most of them are nowhere near.

    Worth its own test because the loop's interesting case is the one that keeps going: with a
    single library on the machine the check has nothing to step over, and a rule that refused every
    neighbour would pass just the same.
    """
    root = await library_store.create_root(name="Videos", abs_path=library)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    await library_store.create_root(name="Photos", abs_path=elsewhere)
    moved = tmp_path / "moved"
    moved.mkdir()

    repointed = await library_store.repoint_root(root.id, moved)

    assert repointed is not None
    assert repointed.abs_path == str(moved)


# --- and what is written down about both ------------------------------------------------------


async def test_a_library_folder_dropped_says_how_many_files_it_stranded(
    library_store: LibraryStore, temp_db: Database, library: Path, actors: Actors
) -> None:
    """The row goes, every folder under it cascades away with it, and afterwards nothing in the
    database says this library was ever attached."""
    root = await library_store.create_root(name="Videos", abs_path=library)
    clips = await library_store.upsert_folder(root.id, "clips")
    await make_asset(temp_db, root.id, clips.id, "clips/a.mp4")

    assert await library_store.delete_root(root.id, actor=Actor.user(actors.admin.id))

    (event,) = await events_recent(temp_db, actors.admin)
    assert (event.verb, event.actor_kind) == ("removed", "user")
    assert '"stranded": 1' in event.payload


async def test_a_folder_moved_says_where_it_was(
    library_store: LibraryStore, temp_db: Database, library: Path, actors: Actors
) -> None:
    """The path is the only record of where a folder was, and a move rewrites it in five tables at
    once, so without this nothing says the folder somebody is looking for used to be elsewhere."""
    root = await library_store.create_root(name="Videos", abs_path=library)
    await library_store.upsert_folder(root.id, "clips")
    folder = await library_store.upsert_folder(root.id, "clips/summer")

    moved = await library_store.move_folder(folder, "clips/winter", actor=Actor.sift("folder"))

    assert moved is not None and moved.rel_path == "clips/winter"
    (event,) = await events_of_entity(temp_db, actors.admin, "folder", folder.id)
    assert (event.verb, event.actor_kind, event.actor_id) == ("moved", "sift", "folder")
    assert '"before": "clips/summer"' in event.payload
