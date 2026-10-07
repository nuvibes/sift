# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repository's entry points: what takes a viewer and what does not, a non-id never read as a
wildcard, the tag and people suggesters, reading one file, and no existence oracle.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

# Imported for their tables: the unnamed-face condition, `same_music`, the `enriched:` predicates
# and the ledger every concealment writes all read one, and the application always has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    NO_FILTER,
    AllOf,
    AssetFilter,
    ConcealerType,
    Concealment,
    Effect,
    ObjectType,
    Repository,
    Viewer,
    Where,
    repository,
    seed_site_username,
)
from sift.kernel.access.catalog import (
    MADE_BY_A_PERSON,
)
from sift.kernel.access.related import related_filter
from sift.kernel.access.repository import ENTITY_SORT_SEEN, MAX_PAGE_SIZE
from sift.kernel.access.repository.entities import _entity_sort
from sift.kernel.access.repository.read_people import username_sites_of
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, DerivativeKind
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.tests.access_helpers import (
    SUBTREE,
    VAULT_CASES,
    VaultCase,
    _person,
    conceal,
    folder_named,
)
from sift.testing.fixtures import Actors, World, hide
from sift.testing.library import HIDE_STATEMENTS

# --- entry points nobody outside the tests calls
#
# `Repository` is the one door onto the permission-carrying tables, and every public method on it
# is one more a feature must read past to pick the right one. The allowlist below is EMPTY: the
# next name that appears has to be argued for in writing.


#: Public methods on `Repository` reached only from the tests, each with why it is still here. A
#: bare name is not an entry: wire it up or delete it.
UNCALLED_ENTRY_POINTS: dict[str, str] = {}


def _public_methods() -> dict[str, set[str]]:
    """Every public method of `Repository` with its parameter names, read off the class."""
    return {
        name: set(inspect.signature(member).parameters)
        for name, member in inspect.getmembers(Repository, inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_public_entry_point_is_called_by_something_that_is_not_a_test() -> None:
    """Every public entry point is called outside the tests. Counted loosely by attribute call (any
    receiver), which can only err towards "called", the safe direction."""
    source_root = Path(repository.__file__).parents[3]
    public = list(_public_methods())

    production = [
        path.read_text(encoding="utf-8")
        for path in source_root.rglob("*.py")
        # `as_posix()`: a Windows path prints backslashes, and `/tests/` would match nothing.
        if "/tests/" not in path.as_posix()
        and "/testing/" not in path.as_posix()
        and "/repository/" not in path.as_posix()
    ]
    uncalled = {
        name
        for name in public
        if not any(re.search(rf"\.{name}\s*\(", text) for text in production)
    }

    unlisted = uncalled - set(UNCALLED_ENTRY_POINTS)
    assert not unlisted, (
        f"public on Repository and called by nothing outside the tests: {sorted(unlisted)}. "
        "Wire it up, delete it, or add it to UNCALLED_ENTRY_POINTS with a reason."
    )
    wired = set(UNCALLED_ENTRY_POINTS) - uncalled
    assert not wired, (
        f"these now have a caller and can come off UNCALLED_ENTRY_POINTS: {sorted(wired)}"
    )


# --- public methods that do not take a viewer
#
# A method with no viewer cannot apply the rules, so it answers whoever asks: `visible_marks`
# refuses non-admins but hands off to viewerless reads, which must not be public beside it. A public
# method without a viewer is never accidental: it is listed, with why it is safe.


#: Public methods that deliberately take no `viewer`, each with why it is safe; a method that has
#: no reason gets a leading underscore instead.
VIEWERLESS_ENTRY_POINTS = {
    "load_viewer": "builds the viewer from a user id; there is none to be handed yet",
    "an_admin": "names the user a pass nobody pressed reads the library as; it is how such a"
    " pass gets a viewer at all",
    "is_concealed": (
        "takes a user id ON PURPOSE: it asks on somebody's behalf rather than as them, and "
        "accepting a viewer would let a caller pass one with the vault already open and get False "
        "for everything"
    ),
    "memberships_of": (
        "tallies which of a handful of files carry what, for a picker's ticks; every caller resolves "
        "the files through actionable_of first, and the one route is admin-only, so a viewer here "
        "could never narrow anything"
    ),
    "reach_of": (
        "reports every user's reach for one object, which is a list ABOUT the users rather "
        "than a list one of them may see; the one route is admin-only, and a viewer here could "
        "only narrow a report whose whole point is everybody"
    ),
    "reach_through_files": (
        "names which files under one entity one named user can reach and what let each through, "
        "for the same admin report `reach_of` feeds; it is a list ABOUT that user and the route "
        "is admin-only, and the user it is about is a parameter rather than the caller"
    ),
    "grant": "writes a sharing decision; admin-only at the route, and it IS the decision",
    "revoke": "the same write in the other direction",
    "forget_object": "drops every grant naming an object being deleted; no reader, no subject",
    "forget_items": "the same drop for the files a delete over a selection ended, in one write",
    "grants_of": "what one user has been given, read by the admin sharing screen",
    "grants_on": "what was written on one object, read by the controls that change it",
    "grant_sources": (
        "every grant reaching one object, for the admin 'why is this shared' panel. Its neighbour "
        "`vault_sources` DOES take a viewer, because concealment is personal and grants are not"
    ),
    "enriched_by": (
        "which of the three things wrote to one file and which stash-box where one did, asked "
        "AFTER the route has already resolved that file through the scoped read, so a second "
        "scoping rule here would be a second place to get it wrong, which is the reasoning "
        "`links_of` gives beside it"
    ),
}


def test_every_public_method_either_takes_a_viewer_or_says_why_not() -> None:
    """Every public method takes a viewer or is listed: read off the signature, since a method with
    no viewer cannot be made to scope itself. A method that gains one must come off the list."""
    viewerless: set[str] = set()
    scoped: set[str] = set()
    for name, taken in _public_methods().items():
        (scoped if "viewer" in taken else viewerless).add(name)

    unlisted = viewerless - set(VIEWERLESS_ENTRY_POINTS)
    assert not unlisted, (
        f"public on Repository and takes no viewer: {sorted(unlisted)}. Give it one, make it "
        "private, or add it to VIEWERLESS_ENTRY_POINTS with a reason."
    )
    now_scoped = set(VIEWERLESS_ENTRY_POINTS) & scoped
    assert not now_scoped, (
        f"these take a viewer now and can come off VIEWERLESS_ENTRY_POINTS: {sorted(now_scoped)}"
    )
    gone = set(VIEWERLESS_ENTRY_POINTS) - viewerless - scoped
    assert not gone, f"these are no longer public and can come off the list: {sorted(gone)}"


@pytest.mark.parametrize("case", VAULT_CASES, ids=[case.name for case in VAULT_CASES])
async def test_every_site_reveals_the_same_files_when_the_vault_is_open(
    case: VaultCase,
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """Each site counts again what it was hiding once the vault is unlocked, so the counts above are
    of files withheld rather than never counted: `:reveal` does the work."""
    await conceal(temp_db, world, case, actors)
    unlocked = replace(actors.admin, show_hidden=True)

    page = await access.visible_assets(unlocked, limit=50)
    assert {item.asset.id for item in page.items} == {world.solo, world.twin, world.loose}

    for name, holds in SUBTREE.items():
        folder = await folder_named(access, unlocked, world, name)
        assert folder is not None, f"{name} must come back when the vault is open"
        assert await access.folder_file_count(unlocked, folder) == len(holds)

    counts = {tag.id: tag.asset_count for tag in await access.suggest_tags(unlocked)}
    assert counts[world.tag] == 1, "the tag count stayed at zero rather than being withheld"

    people_counts = {p.id: p.asset_count for p in (await access.suggest_people(unlocked)).items}
    assert people_counts[world.person] == 1, "the person count was withheld rather than absent"

    # The rows themselves come back too.
    assert world.tag in {tag.id for tag in await access.suggest_tags(unlocked)}
    assert world.site in {site.id for site in await access.suggest_sites(unlocked)}

    # And the named set gives back what it withheld.
    named = await access.visible_assets(
        unlocked,
        limit=50,
        asset_filter=AssetFilter(where=Where("assets", (world.solo, world.twin))),
    )
    assert {item.asset.id for item in named.items} == {world.solo, world.twin}


async def test_a_site_counts_a_concealed_person_exactly_when_its_tab_lists_them(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A Site card's people count and its People tab read one flag: shut, neither says anything of
    a concealed person; open, both do. Placeholder mode, which sets the FILE count's `:reveal`,
    counts the file and not the person, as the tab does."""
    await hide(temp_db, "person", world.person, actors.admin.id)
    peeking = replace(actors.admin, concealment=Concealment.PLACEHOLDER)
    unlocked = replace(actors.admin, show_hidden=True)

    for viewer, people, files in ((actors.admin, 0, 0), (peeking, 0, 1), (unlocked, 1, 1)):
        sites = {site.id: site for site in await access.suggest_sites(viewer)}
        listed = await access.suggest_people(viewer, site_id=world.site)
        # The card's people number is its People cell, sent as `people_count`.
        cells = await access.card_counts(viewer, "site", [world.site])
        assert cells[world.site]["people"] == people
        assert sites[world.site].asset_count == files
        assert len(listed.items) == people, "the card and the tab disagreed about one person"


def test_a_wall_is_a_site_s_people_only_when_the_site_is_all_it_is_narrowed_to() -> None:
    """A Site's People are bound off the file filter only when one bare `sites` condition is all it
    is narrowed to; anything more is the people on its matching files, which a username has none
    of."""
    site = new_id()
    assert username_sites_of(related_filter(site=site)) == json.dumps([site])
    assert username_sites_of(AssetFilter(where=Where("sites", (site,)))) == json.dumps([site])
    assert username_sites_of(NO_FILTER) is None
    assert username_sites_of(related_filter(site=site, tag=new_id())) is None
    assert username_sites_of(related_filter(tag=new_id())) is None
    worded = AssetFilter(where=AllOf((Where("sites", (site,)),)), text="lantern")
    assert username_sites_of(worded) is None


async def _somebody_with_a_username_on(
    db: Database, site_id: str, name: str, *, carrying: str | None = None
) -> str:
    """A person with one username on a Site, filing `carrying` if given."""
    person_id, username_id = new_id(), new_id()
    await db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (person_id, name, sort_key(name)),
    )
    await db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, person_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, 0)",
        (username_id, site_id, name.lower(), sort_key(name), person_id),
    )
    if carrying is not None:
        await db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
            (carrying, username_id),
        )
    return person_id


async def test_a_site_s_people_are_the_people_with_a_file_or_a_username_there(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A Site's People tab and People cell: a file shared with it, OR a username on it.

    A username is held to the usernames wall's rule (an admin sees all, anybody else only one
    carrying a file they may see, of a person they are shown); a label under the Site counts as it.
    """
    label = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, parent_id, created_at) VALUES (?, ?, ?, ?, 0)",
        (label, "label", sort_key("label"), world.site),
    )
    bare = await _somebody_with_a_username_on(temp_db, world.site, "Bryn Calloway")
    unseen = await _somebody_with_a_username_on(
        temp_db, world.site, "Esme Wrenfield", carrying=world.twin
    )
    shown = await _somebody_with_a_username_on(
        temp_db, world.site, "Fenn Marchetti", carrying=world.twin
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.loose, shown)
    )
    labelled = await _somebody_with_a_username_on(temp_db, label, "Halla Nordquist")
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)

    async def people(viewer: Viewer, **narrowed: str) -> set[str]:
        wall = await access.suggest_people(
            viewer, limit=50, asset_filter=related_filter(site=world.site, **narrowed)
        )
        listed = {one.id for one in wall.items}
        assert wall.total == len(listed)
        if not narrowed:
            cell = (await access.card_counts(viewer, "site", [world.site]))[world.site]["people"]
            assert cell == len(listed), "the card and the tab disagreed"
        return listed

    everyone = {world.person, bare, unseen, shown, labelled}
    assert await people(actors.admin) == everyone
    assert await people(actors.guest) == {world.person, shown}
    # Filtered by a tag, the wall is the Site's files carrying it.
    assert await people(actors.admin, tag=world.tag) == {world.person}

    await hide(temp_db, "person", bare, actors.admin.id)
    assert await people(actors.admin) == everyone - {bare}
    assert await people(replace(actors.admin, show_hidden=True)) == everyone

    await hide(temp_db, "site", label, actors.admin.id)
    assert await people(actors.admin) == everyone - {bare, labelled}


@pytest.mark.parametrize("case", VAULT_CASES, ids=[case.name for case in VAULT_CASES])
async def test_whatever_conceals_a_file_can_be_named(
    case: VaultCase,
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
) -> None:
    """A concealed file can say what conceals it, and an ordinary one has nothing to say; held to
    what the grid withholds, so a way of hiding the explanation misses shows up as unexplained."""
    await conceal(temp_db, world, case, actors)
    page = await access.visible_assets(actors.admin, limit=50)
    visible = {item.asset.id for item in page.items}
    flagged = world.object_id(case.target)

    for asset in (world.solo, world.twin, world.loose):
        sources = await access.vault_sources(actors.admin, ObjectType.ITEM, asset)
        assert bool(sources) == (asset not in visible), (
            "a concealed file with nothing to say for itself, or a visible one claiming to be hidden"
        )
        if not sources:
            continue
        assert any(source.source_id == flagged for source in sources), (
            "the file is concealed and the flag that did it is not among the reasons given"
        )
        # `here` is exactly the file's own flag. `ConcealerType`: what can HIDE a thing and what a
        # grant can NAME are two lists (a photo set is in one only).
        assert all(source.here == (source.source_type is ConcealerType.ITEM) for source in sources)


async def test_nothing_conceals_a_file_in_an_ordinary_library(
    access: Repository, actors: Actors, world: World
) -> None:
    """An ordinary file has no concealers: a list never empty would agree with everything."""
    for asset in (world.solo, world.twin, world.loose):
        assert await access.vault_sources(actors.admin, ObjectType.ITEM, asset) == []


async def test_a_folder_names_the_folder_above_it_that_is_concealing_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A folder is concealed by itself (`here`, drawn solid), a folder above or its library."""
    await hide(temp_db, "folder", world.mid, actors.admin.id)

    from_mid = await access.vault_sources(actors.admin, ObjectType.FOLDER, world.mid)
    assert [(source.source_id, source.here) for source in from_mid] == [(world.mid, True)]

    from_leaf = await access.vault_sources(actors.admin, ObjectType.FOLDER, world.leaf)
    assert [(source.source_id, source.here) for source in from_leaf] == [(world.mid, False)]


async def test_an_entity_names_only_its_own_mark(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A person, a collection and a library carry their own and have nothing above them."""
    await hide(temp_db, "person", world.person, actors.admin.id)

    named = await access.vault_sources(actors.admin, ObjectType.PERSON, world.person)
    assert [(source.source_type, source.here) for source in named] == [(ConcealerType.PERSON, True)]

    # The type is compared in the statement, or a collection would answer with a person's mark.
    assert await access.vault_sources(actors.admin, ObjectType.COLLECTION, world.person) == []
    assert await access.vault_sources(actors.admin, ObjectType.TAG, world.person) == []


#: Tables with a `hidden` column that is not a way of hiding anything.
HIDDEN_BUT_NOT_A_WAY_TO_HIDE: dict[str, str] = {
    "insight_days": "how much of a day's tally is about hidden things, not a flag",
    "page_visits": "whether the page visited was about a hidden thing then, not a flag",
}


async def test_the_vault_cases_cover_every_way_of_hiding_something(
    temp_db: Database, access: Repository
) -> None:
    """Every per-user table with a `hidden` column, read off the schema, is a vault case or is named
    in `HIDDEN_BUT_NOT_A_WAY_TO_HIDE` with its reason."""
    rows = await temp_db.fetch_all(
        "SELECT m.name AS name FROM sqlite_master m JOIN pragma_table_info(m.name) c "
        "WHERE m.type = 'table' AND c.name = 'hidden'"
    )
    # Which tables exist depends on the slices this run imported.
    carrying = {str(row["name"]) for row in rows}
    # From the statements the scaffolding writes through, not a second list.
    written_to = {
        statement.split("INSERT INTO ", 1)[1].split(" ", 1)[0]
        for statement in HIDE_STATEMENTS.values()
    }
    assert carrying - set(HIDDEN_BUT_NOT_A_WAY_TO_HIDE) == written_to
    assert {case.kind for case in VAULT_CASES} == set(HIDE_STATEMENTS)


async def test_what_a_folder_holds_is_the_same_pass_as_the_count_a_move_reads_out(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The properties panel's numbers and the count before a move are ONE query
    (`folder_contents`), so they cannot come from two worlds. Sizes and times are written here,
    since a sum over nulls and a max over one value pass without reading a row."""
    await temp_db.execute(
        "UPDATE assets SET size_bytes = ?, added_at = ? WHERE id = ?", (100, 10, world.solo)
    )
    await temp_db.execute(
        "UPDATE assets SET size_bytes = ?, added_at = ? WHERE id = ?", (250, 40, world.twin)
    )

    folder = await folder_named(access, actors.admin, world, "top")
    assert folder is not None

    held = await access.folder_contents(actors.admin, folder)
    assert held is not None
    assert held.files == await access.folder_file_count(actors.admin, folder), (
        "the panel and the move confirm disagree about how many files are under this folder"
    )
    assert held.files == 2
    assert held.bytes == 350, "a size summed over the copies under the folder"
    assert held.folders == 2, "mid and leaf are both under top, and top does not count itself"
    assert held.newest_at == 40, "the latest arrival, not the first"


async def test_an_empty_folder_holds_no_bytes_and_has_no_newest_file(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """An empty folder is nought bytes and NO date: an invented date would sort it."""
    nothing_in_it = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
        (nothing_in_it, world.root, world.top, "top/empty", "empty"),
    )

    folder = await access.get_folder(actors.admin, nothing_in_it)
    assert folder is not None

    empty = await access.folder_contents(actors.admin, folder)
    assert empty is not None
    assert empty.files == 0
    assert empty.bytes == 0
    assert empty.folders == 0
    assert empty.newest_at is None


async def test_a_guest_is_never_told_what_a_folder_holds(
    access: Repository, actors: Actors, world: World
) -> None:
    """A guest gets no folder sizes: they are physical facts, and would say how much is hidden."""
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    folder = await access.get_folder(actors.guest, world.leaf)
    assert folder is not None
    assert await access.folder_contents(actors.guest, folder) is None


async def test_a_guest_is_never_told_how_many_files_a_folder_holds(
    access: Repository, actors: Actors, world: World
) -> None:
    """A guest is offered no count, since a scoped one understates a move and a physical one leaks:
    None, not zero."""
    await access.grant(ObjectType.FOLDER, world.leaf, actors.guest.id, Effect.SHARE)

    folder = await access.get_folder(actors.guest, world.leaf)
    assert folder is not None
    assert await access.folder_file_count(actors.guest, folder) is None


# --- a non-id is never a wildcard


async def test_a_none_id_is_not_a_wildcard(
    access: Repository, actors: Actors, world: World
) -> None:
    """A single-object read handed None or a malformed id returns nothing, never the grid's first
    visible row: its statement reads NULL as "everything". Asked as an admin, who would leak
    most."""
    for bad in (None, "", "not-an-id", "../etc/passwd", 12345):
        assert await access.get_asset(actors.admin, bad) is None  # type: ignore[arg-type]
        assert await access.open_asset(actors.admin, bad) is None  # type: ignore[arg-type]
        assert await access.can_view(actors.admin, bad) is False  # type: ignore[arg-type]
        assert await access.locations(actors.admin, bad) == []  # type: ignore[arg-type]
        assert await access.get_folder(actors.admin, bad) is None  # type: ignore[arg-type]
        assert await access.can_view_folder(actors.admin, bad) is False  # type: ignore[arg-type]

    # The folder filters are OPTIONAL: None means "do not narrow", which the tree relies on, and at
    # worst shows the viewer's own whole tree. Every malformed value still fails closed.
    for bad in ("", "not-an-id", "../etc/passwd", 12345):
        assert await access.visible_folders(actors.admin, parent_id=bad) == []  # type: ignore[arg-type]
        assert await access.visible_folders(actors.admin, root_id=bad) == []  # type: ignore[arg-type]

    everything = await access.visible_folders(actors.admin)
    assert await access.visible_folders(actors.admin, root_id=None, parent_id=None) == everything


async def test_a_well_formed_but_unknown_id_is_simply_absent(
    access: Repository, actors: Actors, world: World
) -> None:
    """A well-formed id for no row resolves to nothing, indistinguishable from a malformed one."""
    assert await access.get_asset(actors.admin, new_id()) is None
    assert await access.get_folder(actors.admin, new_id()) is None


# --- filtering by tag, and the tag list


async def test_filtering_by_tag_narrows_to_the_tagged_files(
    access: Repository, actors: Actors, world: World
) -> None:
    page = await access.visible_assets(actors.admin, tag_id=world.tag)

    assert [item.asset.id for item in page.items] == [world.solo]
    assert page.total == 1


async def test_the_total_beside_a_tag_counts_only_what_the_viewer_may_see(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Both files carry the tag and one is shared: a total of two would tell a guest how many they
    are not shown."""
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.twin, world.tag)
    )
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    page = await access.visible_assets(actors.guest, tag_id=world.tag)

    assert [item.asset.id for item in page.items] == [world.solo]
    assert page.total == 1, "the total counted a file the guest was never shown"


async def test_a_tag_id_that_is_not_an_id_matches_nothing(
    access: Repository, actors: Actors, world: World
) -> None:
    """An id naming no tag filters to nothing: only NULL means "every tag", and a malformed id is
    a string, which this asserts in case the predicate becomes a join."""
    for bad in ("", "not-an-id", "../etc/passwd", "'; DROP TABLE assets;--"):
        page = await access.visible_assets(actors.admin, tag_id=bad)
        assert page.items == []
        assert page.total == 0

    unknown = await access.visible_assets(actors.admin, tag_id=new_id())
    assert unknown.items == []
    assert unknown.total == 0


async def test_a_page_past_the_end_of_a_tag_still_reports_the_tag_total(
    access: Repository, actors: Actors, world: World
) -> None:
    """The empty-page re-query for the total carries the tag too."""
    page = await access.visible_assets(actors.admin, tag_id=world.tag, limit=5, offset=50)

    assert page.items == []
    assert page.total == 1


async def test_the_suggester_lists_every_tag_for_an_admin_with_scoped_counts(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The tag list includes one nothing carries, which the editor must rename."""
    empty_tag = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, 'unused', 0)", (empty_tag,)
    )

    suggestions = await access.suggest_tags(actors.admin)

    assert {tag.id: tag.asset_count for tag in suggestions} == {world.tag: 1, empty_tag: 0}
    assert [tag.name for tag in suggestions] == ["tag", "unused"], "most-used first, then by name"


async def test_the_suggester_hides_a_tag_a_guest_can_reach_nothing_through(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A tag reaching nothing a guest may see is absent, not shown at zero: its name is what they
    would get."""
    empty_tag = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, 'unused', 0)", (empty_tag,)
    )

    assert await access.suggest_tags(actors.guest) == []

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    assert [(tag.id, tag.asset_count) for tag in await access.suggest_tags(actors.guest)] == [
        (world.tag, 1)
    ]


async def test_the_suggester_matches_a_prefix_without_regard_to_case(
    access: Repository, actors: Actors, world: World
) -> None:
    """Matching is case-blind."""
    assert [tag.id for tag in await access.suggest_tags(actors.admin, "ta")] == [world.tag]
    assert [tag.id for tag in await access.suggest_tags(actors.admin, "TA")] == [world.tag]
    assert await access.suggest_tags(actors.admin, "zz") == []


async def test_a_wildcard_typed_into_the_suggester_is_a_character(
    access: Repository, actors: Actors, world: World
) -> None:
    """`%` and `_` are escaped, or typing either would match every tag."""
    assert await access.suggest_tags(actors.admin, "_") == []
    assert await access.suggest_tags(actors.admin, "%") == []
    assert await access.suggest_tags(actors.admin, "t%g") == []


async def test_the_suggester_refuses_a_list_with_no_rows_in_it(
    access: Repository, actors: Actors, world: World
) -> None:
    with pytest.raises(ValueError, match="at least one row"):
        await access.suggest_tags(actors.admin, limit=0)


async def test_asking_which_of_these_are_visible_is_not_capped_at_a_page(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """`visible_of` is not capped: past a page's cap reads like "you may not see this"."""
    ids = []
    for index in range(MAX_PAGE_SIZE + 26):
        asset_id = new_id()
        ids.append(asset_id)
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
            (asset_id, f"h{index:05d}", index),
        )
        await temp_db.execute(
            "INSERT INTO asset_locations "
            "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
            (new_id(), asset_id, world.root, world.leaf, f"f{index}.mp4", f"f{index}.mp4"),
        )

    assert await access.visible_of(actors.admin, ids) == set(ids)


async def test_asking_about_no_files_asks_the_database_nothing(
    access: Repository, actors: Actors
) -> None:
    """An empty list asks nothing."""
    assert await access.visible_of(actors.admin, []) == set()


async def test_which_of_these_are_visible_still_answers_only_for_what_may_be_seen(
    access: Repository, actors: Actors, world: World
) -> None:
    """Lifting the cap keeps the rules."""
    assert await access.visible_of(actors.guest, [world.solo]) == set()
    assert await access.visible_of(actors.admin, [world.solo]) == {world.solo}


async def test_the_rows_behind_that_answer_are_scoped_the_same_way(
    access: Repository, actors: Actors, world: World
) -> None:
    """`assets_of` reads facts about a list of ids through the same scoped read."""
    assert await access.assets_of(actors.guest, [world.solo]) == {}
    found = await access.assets_of(actors.admin, [world.solo])
    assert set(found) == {world.solo}
    assert found[world.solo].asset.id == world.solo


async def test_asking_for_no_rows_asks_the_database_nothing(
    access: Repository, actors: Actors
) -> None:
    assert await access.assets_of(actors.admin, []) == {}


async def test_the_suggester_will_not_build_an_unbounded_list(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The tag list's limit is clamped where it is served, as the grid's is."""
    for index in range(MAX_PAGE_SIZE + 5):
        await temp_db.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (new_id(), f"t{index:04d}")
        )

    assert len(await access.suggest_tags(actors.admin, limit=10_000)) == MAX_PAGE_SIZE


async def _alias(temp_db: Database, person_id: str, alias: str) -> None:
    await temp_db.execute(
        "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
        (new_id(), person_id, alias),
    )


def _unlocked(viewer: Viewer) -> Viewer:
    return replace(viewer, show_hidden=True)


async def test_the_people_suggester_counts_only_what_the_asker_may_see(
    access: Repository, actors: Actors, world: World
) -> None:
    """The count is scoped, as a tag's is."""
    assert [(p.id, p.asset_count) for p in (await access.suggest_people(actors.admin)).items] == [
        (world.person, 1)
    ]
    assert (await access.suggest_people(actors.guest)).items == []

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    assert [(p.id, p.asset_count) for p in (await access.suggest_people(actors.guest)).items] == [
        (world.person, 1)
    ]


async def test_the_people_suggester_completes_a_name_from_its_start(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """By default a suggester finishes what is being typed: a prefix."""
    await _person(temp_db, "Neve Arbogast")

    found = [p.name for p in (await access.suggest_people(actors.admin, "Neve")).items]

    assert found == ["Neve Arbogast"]
    assert (await access.suggest_people(actors.admin, "Arbogast")).items == []


async def test_the_people_suggester_can_match_anywhere_in_a_name(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """The People wall's filter box matches anywhere in a name, so a surname finds the person."""
    await _person(temp_db, "Neve Arbogast")

    found = [
        p.name for p in (await access.suggest_people(actors.admin, "Arbogast", anywhere=True)).items
    ]

    assert found == ["Neve Arbogast"]


async def test_a_wildcard_typed_into_the_people_filter_is_text_and_not_a_pattern(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    """`%` is escaped, or typing one would return the whole library."""
    await _person(temp_db, "Neve Arbogast")

    assert (await access.suggest_people(actors.admin, "%", anywhere=True)).items == []
    assert (await access.suggest_people(actors.admin, "_", anywhere=True)).items == []


async def test_a_vaulted_person_is_absent_from_the_people_suggester(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A vaulted person is absent from the list and the count, admin included, until unlocked."""
    hidden = await _person(temp_db, "hidden one", hidden_by=actors.admin)

    assert hidden not in [p.id for p in (await access.suggest_people(actors.admin)).items]

    revealed = [p.id for p in (await access.suggest_people(_unlocked(actors.admin))).items]

    assert hidden in revealed


async def test_placeholder_mode_does_not_bring_back_a_vaulted_persons_name(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Placeholder mode leaves a gap for a person: their row is nothing but the name."""
    hidden = await _person(temp_db, "hidden one", hidden_by=actors.admin)
    peeking = replace(actors.admin, concealment=Concealment.PLACEHOLDER)

    assert hidden not in [p.id for p in (await access.suggest_people(peeking)).items]


async def _rate(
    temp_db: Database, person_id: str, viewer: Viewer, *, favorite: bool = False, rating: int = 0
) -> None:
    """One user's heart and stars on one person, written straight into the table."""
    await temp_db.execute(
        "INSERT INTO person_user_state (person_id, user_id, favorite, rating, updated_at) "
        "VALUES (?, ?, ?, ?, 0) "
        "ON CONFLICT(person_id, user_id) DO UPDATE SET "
        "favorite = excluded.favorite, rating = excluded.rating",
        (person_id, viewer.id, 1 if favorite else 0, rating or None),
    )


async def test_a_favourited_person_sorts_above_an_unfavourited_one(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The heart order puts the hearted first, and no order falls back to names, so the CASE falls
    through rather than always firing."""
    zed = await _person(temp_db, "zed")
    amy = await _person(temp_db, "amy")
    await _rate(temp_db, zed, actors.admin, favorite=True)

    by_heart = [p.id for p in (await access.suggest_people(actors.admin, sort="favorite")).items]
    ordinary = [p.id for p in (await access.suggest_people(actors.admin)).items]

    assert by_heart[0] == zed
    assert ordinary.index(amy) < ordinary.index(zed)


async def test_the_wall_can_be_ordered_by_stars_and_unrated_sorts_last(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    low = await _person(temp_db, "aaa low")
    high = await _person(temp_db, "bbb high")
    await _rate(temp_db, low, actors.admin, rating=2)
    await _rate(temp_db, high, actors.admin, rating=5)

    by_stars = [p.id for p in (await access.suggest_people(actors.admin, sort="rating")).items]

    assert by_stars.index(high) < by_stars.index(low)
    # Unrated sorts behind both, not first as a NULL would.
    assert by_stars.index(low) < by_stars.index(world.person)


async def test_one_users_heart_does_not_order_anothers_wall(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A heart is per user, not a property of the person."""
    zed = await _person(temp_db, "zed")
    await _person(temp_db, "amy")
    await _rate(temp_db, zed, actors.admin, favorite=True)
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)

    theirs = [p.id for p in (await access.suggest_people(actors.guest, sort="favorite")).items]

    assert theirs == [] or theirs[0] != zed


async def test_the_new_order_shows_a_guest_nobody_they_were_not_shown(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """No order becomes a way in: under every order a guest sees only who they are shown."""
    unseen = await _person(temp_db, "never shown")
    await _rate(temp_db, unseen, actors.admin, favorite=True, rating=5)
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    for order in ("seen", "favorite", "rating"):
        seen = [p.id for p in (await access.suggest_people(actors.guest, sort=order)).items]
        assert seen == [world.person], f"the {order} order changed who a guest may see"
        assert unseen not in seen


async def test_a_vaulted_person_stays_out_of_every_order(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Hearted and five-starred, a concealed person is still absent under every order."""
    hidden = await _person(temp_db, "hidden one", hidden_by=actors.admin)
    await _rate(temp_db, hidden, actors.admin, favorite=True, rating=5)

    for order in ("seen", "favorite", "rating"):
        listed = [p.id for p in (await access.suggest_people(actors.admin, sort=order)).items]
        assert hidden not in listed, f"the {order} order revealed a vaulted person"

    # Unlocking brings them back.
    revealed = [p.id for p in (await access.suggest_people(_unlocked(actors.admin))).items]
    assert hidden in revealed


async def test_an_order_the_wall_does_not_know_is_the_ordinary_one(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """An unknown sort shows the ordinary order; the normaliser is asserted directly too, since the
    wall alone cannot tell it was used."""
    await _person(temp_db, "amy")

    assert _entity_sort("nonsense") == ENTITY_SORT_SEEN
    assert _entity_sort("rating") == "rating"

    strange = [p.id for p in (await access.suggest_people(actors.admin, sort="nonsense")).items]
    ordinary = [p.id for p in (await access.suggest_people(actors.admin)).items]

    assert strange == ordinary


async def test_the_site_wall_takes_the_same_orders(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Written out in its own statement, so asserted separately."""
    await temp_db.execute(
        "INSERT INTO site_user_state (site_id, user_id, favorite, rating, updated_at) "
        "VALUES (?, ?, 1, 5, 0)",
        (world.site, actors.admin.id),
    )

    by_heart = [p.id for p in await access.suggest_sites(actors.admin, sort="favorite")]
    by_stars = [p.id for p in await access.suggest_sites(actors.admin, sort="rating")]

    assert by_heart[0] == world.site
    assert by_stars[0] == world.site


async def test_the_people_suggester_matches_a_prefix_without_regard_to_case(
    access: Repository, actors: Actors, world: World
) -> None:
    assert [p.id for p in (await access.suggest_people(actors.admin, "pe")).items] == [world.person]
    assert [p.id for p in (await access.suggest_people(actors.admin, "PE")).items] == [world.person]
    assert (await access.suggest_people(actors.admin, "zz")).items == []


async def test_a_wildcard_typed_into_the_people_suggester_is_a_character(
    access: Repository, actors: Actors, world: World
) -> None:
    assert (await access.suggest_people(actors.admin, "_")).items == []
    assert (await access.suggest_people(actors.admin, "%")).items == []


async def test_the_people_suggester_is_bounded_and_refuses_an_empty_list(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    with pytest.raises(ValueError, match="at least one row"):
        await access.suggest_people(actors.admin, limit=0)

    for index in range(MAX_PAGE_SIZE + 5):
        await _person(temp_db, f"p{index:04d}")

    assert len((await access.suggest_people(actors.admin, limit=10_000)).items) == MAX_PAGE_SIZE


async def test_the_people_suggester_reports_the_vault_flag_it_is_asked_to_draw(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The screen offering the toggle knows which way it is set."""
    hidden = await _person(temp_db, "hidden one", hidden_by=actors.admin)

    flags = {p.id: p.vault for p in (await access.suggest_people(_unlocked(actors.admin))).items}

    assert flags[hidden] is True
    assert flags[world.person] is False


async def test_one_person_by_id_is_scoped_the_way_the_list_is(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The by-id lookup and the list are the same query, so they cannot answer differently."""
    assert (await access.visible_person(actors.admin, world.person)) is not None
    assert (await access.visible_person(actors.guest, world.person)) is None

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    found = await access.visible_person(actors.guest, world.person)

    assert found is not None
    assert found.asset_count == 1


async def test_one_person_by_id_conceals_a_vaulted_person_and_an_unknown_id(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """From outside, "no such person" and "not for you" are one answer."""
    hidden = await _person(temp_db, "hidden one", hidden_by=actors.admin)

    assert (await access.visible_person(actors.admin, hidden)) is None
    assert (await access.visible_person(_unlocked(actors.admin), hidden)) is not None
    assert (await access.visible_person(actors.admin, new_id())) is None


async def test_the_resolver_names_nobody_the_asker_could_not_already_see(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """A term is a probe: an answer says somebody by that name is here."""
    await _alias(temp_db, world.person, "stage name")

    assert (await access.resolve_alias_targets(actors.guest, "stage name")).person_ids == ()

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    match = await access.resolve_alias_targets(actors.guest, "stage name")

    assert match.person_ids == (world.person,)


async def test_the_resolver_is_bounded_and_refuses_an_empty_list(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Bounded: two people may share a name."""
    with pytest.raises(ValueError, match="at least one row"):
        await access.resolve_alias_targets(actors.admin, "person", limit=0)

    for _ in range(MAX_PAGE_SIZE + 5):
        await _person(temp_db, "person")

    found = await access.resolve_alias_targets(actors.admin, "person", limit=10_000)

    assert len(found.person_ids) == MAX_PAGE_SIZE


async def test_one_username_on_two_sites_resolves_to_both_people_wearing_it(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The same name on two sites is two usernames, so the resolver returns two PEOPLE for a
    username worn by two of them."""
    other = await _person(temp_db, "the other one")
    _, twin_username = await seed_site_username(
        temp_db, site="elsewhere", name="handle", made=MADE_BY_A_PERSON
    )
    assert twin_username != world.username, (
        "the two sites must not have collapsed into one username"
    )

    await temp_db.execute(
        "UPDATE usernames SET person_id = ? WHERE id = ?", (world.person, world.username)
    )
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (other, twin_username))

    found = await access.resolve_alias_targets(actors.admin, "handle")

    assert set(found.person_ids) == {world.person, other}


async def test_one_resolver_finds_a_person_by_name_by_alias_and_by_a_linked_username(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """The resolver answers a name, an alias and a linked username, the last with no re-indexing:
    search uses this one function."""
    await _alias(temp_db, world.person, "stage name")
    await temp_db.execute(
        "UPDATE usernames SET person_id = ? WHERE id = ?", (world.person, world.username)
    )

    for term in ("person", "stage name", "handle"):
        match = await access.resolve_alias_targets(actors.admin, term)
        assert match.person_ids == (world.person,), term

    assert (await access.resolve_alias_targets(actors.admin, "nobody")).person_ids == ()


async def test_the_resolver_matches_without_regard_to_case_and_ignores_an_empty_term(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await _alias(temp_db, world.person, "Stage Name")

    assert (await access.resolve_alias_targets(actors.admin, "PERSON")).person_ids == (
        world.person,
    )
    assert (await access.resolve_alias_targets(actors.admin, "stage NAME")).person_ids == (
        world.person,
    )

    blank = await access.resolve_alias_targets(actors.admin, "   ")

    assert blank.person_ids == ()
    assert blank.term == ""


async def test_the_resolver_returns_everyone_a_term_names(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Two people really can share a name."""
    twin = await _person(temp_db, "person")

    match = await access.resolve_alias_targets(actors.admin, "person")

    assert set(match.person_ids) == {world.person, twin}


async def test_the_resolver_never_names_a_vaulted_person(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """Hidden people are absent through a name, an alias or a linked username, admin included; no
    viewer at all gets the locked answer."""
    hidden = await _person(temp_db, "hidden one", hidden_by=actors.admin)
    await _alias(temp_db, hidden, "her other name")
    _, username_id = await seed_site_username(
        temp_db, site="elsewhere", name="herhandle", made=MADE_BY_A_PERSON
    )
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (hidden, username_id))

    for term in ("hidden one", "her other name", "herhandle"):
        assert (await access.resolve_alias_targets(actors.guest, term)).person_ids == (), term
        assert (await access.resolve_alias_targets(actors.admin, term)).person_ids == ()

    unlocked = _unlocked(actors.admin)

    for term in ("hidden one", "her other name", "herhandle"):
        assert (await access.resolve_alias_targets(unlocked, term)).person_ids == (hidden,)


async def test_an_unlinked_username_names_nobody(
    access: Repository, actors: Actors, world: World
) -> None:
    """A username becomes an alias only once linked."""
    assert (await access.resolve_alias_targets(actors.admin, "handle")).person_ids == ()


# --- reading a file


async def test_locations_come_back_only_for_someone_allowed_to_open_it(
    access: Repository, actors: Actors, world: World
) -> None:
    """A viewer who may not have the bytes gets no paths."""
    assert await access.locations(actors.guest, world.solo) == []

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    locations = await access.locations(actors.guest, world.solo)
    assert [location.asset_id for location in locations] == [world.solo]


async def test_locate_returns_a_path_only_for_someone_allowed_to_open_it(
    access: Repository, actors: Actors, world: World
) -> None:
    """`locate` is the scoped way from an asset to a file: None when denied, else its path."""
    assert await access.locate(actors.guest, world.solo) is None

    path = await access.locate(actors.admin, world.solo)
    assert path is not None
    assert path.name == "solo.mp4"
    # By parts: a separator is the operating system's.
    assert path.parts[-4:] == ("top", "mid", "leaf", "solo.mp4")
    assert str(world.root) in str(path)


async def test_locate_skips_a_missing_copy_and_serves_a_present_one(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """With one of `twin`'s copies missing, `locate` serves the other."""
    locations = await access.locations(actors.admin, world.twin)
    first = min(locations, key=lambda location: (location.first_seen_at, location.id))
    await temp_db.execute("UPDATE asset_locations SET status = 'missing' WHERE id = ?", (first.id,))

    path = await access.locate(actors.admin, world.twin)
    assert path is not None
    assert path.name == "twin.mp4"
    assert first.rel_path not in str(path)


async def test_locate_returns_none_when_every_copy_is_missing(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (world.solo,)
    )
    assert await access.locate(actors.admin, world.solo) is None


async def test_locate_conceals_a_vaulted_asset(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    """No path for concealed bytes, admin included, until unlocked."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    assert await access.locate(actors.admin, world.solo) is None

    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.locate(unlocked, world.solo) is not None


async def a_thumbnail(
    content_store: ContentStore, settings: Settings, asset_id: str, *, body: bytes = b"thumb-bytes"
) -> Path:
    """Register a thumbnail and put real bytes where it says they are."""
    derivative = await content_store.add_derivative(
        asset_id, DerivativeKind.THUMB, extension="jpg", size_bytes=len(body)
    )
    path = settings.cache_dir / derivative.rel_cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


async def test_a_derivative_is_scoped_the_same_way_the_original_is(
    access: Repository,
    actors: Actors,
    world: World,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """A thumbnail tells what a file is, so it is refused with the file."""
    await a_thumbnail(content_store, settings, world.solo)

    assert await access.locate_derivative(actors.guest, world.solo, DerivativeKind.THUMB) is None

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    path = await access.locate_derivative(actors.guest, world.solo, DerivativeKind.THUMB)
    assert path is not None
    assert path.read_bytes() == b"thumb-bytes"


async def test_a_vaulted_asset_has_no_thumbnail_either(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """Concealed means no bytes of any size."""
    await a_thumbnail(content_store, settings, world.solo)
    await hide(temp_db, "asset", world.solo, actors.admin.id)

    assert await access.locate_derivative(actors.admin, world.solo, DerivativeKind.THUMB) is None

    unlocked = replace(actors.admin, show_hidden=True)
    assert await access.locate_derivative(unlocked, world.solo, DerivativeKind.THUMB) is not None


async def test_a_placeholder_tile_still_gets_no_art(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """Placeholder mode's locked tile gets no thumbnail."""
    await a_thumbnail(content_store, settings, world.solo)
    await hide(temp_db, "asset", world.solo, actors.admin.id)

    placeholder = replace(actors.admin, concealment=Concealment.PLACEHOLDER)
    assert await access.get_asset(placeholder, world.solo) is not None, "the tile should survive"
    assert await access.locate_derivative(placeholder, world.solo, DerivativeKind.THUMB) is None, (
        "a concealed asset served its thumbnail"
    )


async def test_a_derivative_that_was_never_built_is_the_same_answer_as_a_denied_one(
    access: Repository, actors: Actors, world: World
) -> None:
    """A thumbnail not built yet and one not allowed are the same None."""
    assert await access.locate_derivative(actors.admin, world.solo, DerivativeKind.THUMB) is None


async def test_a_derivative_swept_from_the_cache_is_not_served(
    access: Repository,
    actors: Actors,
    world: World,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """A cache row pointing at a deleted file is ordinary."""
    path = await a_thumbnail(content_store, settings, world.solo)
    path.unlink()

    assert await access.locate_derivative(actors.admin, world.solo, DerivativeKind.THUMB) is None


async def test_derivatives_are_told_apart_by_their_settings(
    access: Repository,
    actors: Actors,
    world: World,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """A sprite sheet is per layout."""
    sheet = await content_store.add_derivative(
        world.solo, DerivativeKind.SPRITE, extension="jpg", params={"columns": 6}
    )
    path = settings.cache_dir / sheet.rel_cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"six-across")

    found = await access.locate_derivative(
        actors.admin, world.solo, DerivativeKind.SPRITE, params={"columns": 6}
    )
    assert found is not None
    assert found.read_bytes() == b"six-across"

    assert (
        await access.locate_derivative(
            actors.admin, world.solo, DerivativeKind.SPRITE, params={"columns": 8}
        )
        is None
    )
    assert await access.locate_derivative(actors.admin, world.solo, DerivativeKind.SPRITE) is None


# --- the newest hover clip, whatever recipe built it, until its replacement lands


async def _a_clip(
    content_store: ContentStore,
    settings: Settings,
    asset_id: str,
    params: dict[str, int],
    body: bytes,
) -> Path:
    """Register a hover clip cut to one recipe, with real bytes."""
    derivative = await content_store.add_derivative(
        asset_id, DerivativeKind.PREVIEW, extension="mp4", params=params, size_bytes=len(body)
    )
    path = settings.cache_dir / derivative.rel_cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


async def test_a_clip_cut_to_a_recipe_nobody_uses_any_more_is_still_served(
    access: Repository,
    actors: Actors,
    world: World,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """Asking by today's recipe finds nothing; asking for the newest finds the clip that is
    there."""
    await _a_clip(content_store, settings, world.solo, {"pieces": 5, "each_ms": 2000}, b"old-clip")

    today = {"pieces": 6, "each_ms": 1500}
    assert (
        await access.serve_derivative(
            actors.admin, world.solo, DerivativeKind.PREVIEW, params=today
        )
        is None
    )

    served = await access.serve_newest_derivative(actors.admin, world.solo, DerivativeKind.PREVIEW)
    assert served is not None
    assert served.path.read_bytes() == b"old-clip"


async def test_the_newest_of_two_clips_is_the_one_served(
    access: Repository,
    actors: Actors,
    world: World,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """A rebuild writes the new row after the old, so the newest is current immediately."""
    await _a_clip(content_store, settings, world.solo, {"pieces": 5, "each_ms": 2000}, b"old-clip")
    await _a_clip(content_store, settings, world.solo, {"pieces": 6, "each_ms": 1500}, b"new-clip")

    served = await access.serve_newest_derivative(actors.admin, world.solo, DerivativeKind.PREVIEW)
    assert served is not None
    assert served.path.read_bytes() == b"new-clip"


async def test_the_newest_clip_is_scoped_the_same_way_every_other_picture_is(
    access: Repository,
    actors: Actors,
    world: World,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    """The newest clip is refused to a guest as the file is."""
    await _a_clip(content_store, settings, world.solo, {"pieces": 5, "each_ms": 2000}, b"old-clip")

    assert (
        await access.serve_newest_derivative(actors.guest, world.solo, DerivativeKind.PREVIEW)
        is None
    )

    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    served = await access.serve_newest_derivative(actors.guest, world.solo, DerivativeKind.PREVIEW)
    assert served is not None
    assert served.path.read_bytes() == b"old-clip"


async def test_a_kind_that_was_never_built_has_no_newest(
    access: Repository, actors: Actors, world: World
) -> None:
    """A missing clip gets the same None as a denied one."""
    assert (
        await access.serve_newest_derivative(actors.admin, world.solo, DerivativeKind.PREVIEW)
        is None
    )


# --- no existence oracle


async def test_a_denied_asset_is_indistinguishable_from_a_missing_one(
    access: Repository, actors: Actors, world: World
) -> None:
    """Denied and missing are both None, the same 404: a 403 would say the file exists."""
    denied = await access.get_asset(actors.guest, world.solo)
    missing = await access.get_asset(actors.guest, new_id())
    assert denied is None
    assert missing is None


async def test_a_valid_id_is_not_a_key(access: Repository, actors: Actors, world: World) -> None:
    """Every real id handed to a guest still gets nothing: ids are unguessable only as a
    backstop."""
    for object_id in (world.solo, world.twin, world.loose):
        assert await access.can_view(actors.guest, object_id) is False
        assert await access.get_asset(actors.guest, object_id) is None
        assert await access.open_asset(actors.guest, object_id) is None
        assert await access.locations(actors.guest, object_id) == []

    for folder_id in (world.top, world.mid, world.leaf, world.other):
        assert await access.can_view_folder(actors.guest, folder_id) is False
        assert await access.get_folder(actors.guest, folder_id) is None
