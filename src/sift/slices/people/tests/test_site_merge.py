# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two sites that turn out to be one.

The same act a person merge is, and the tests that matter here are the three things a person merge
never had to answer. **A username carries FILES**, through `asset_usernames`, and that cascades when
the username goes, so folding two sites that both know the username `jane` must move her files
before it removes the row, or a merge quietly destroys a site's whole attribution history on an
operation with no undo. **A site can be a site's parent**, so merging a network into one of its own
labels can leave the survivor pointing at itself. **A site can hold a sealed login**, and only one
of two can survive, which is refused rather than decided, because a secret destroyed is the one
loss the confirm screen could not honestly count.

Everything else is `test_merge.py`'s shape: weigh writes nothing, the name that goes becomes an
also-known-as, and both halves are resolved through the scoped read first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    db_path,
    make_person,
    read,
    sign_in,
    write,
)


def _make_site(client: TestClient, name: str) -> str:
    response = client.post("/api/sites", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _username(client: TestClient, site_id: str, name: str, *, number: str | None = None) -> str:
    """One username on one site, seeded the way a download writes it.

    Straight to the table because there is no endpoint: a username is an internal record of where a
    file came from rather than something anybody manages.
    """
    username_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO usernames (id, site_id, name, number, created_at)"
                " VALUES (?, ?, ?, ?, 0)",
                (username_id, site_id, name, number),
            )
        ],
    )
    return username_id


def _alias(client: TestClient, site_id: str, alias: str) -> None:
    """One of a site's other names, seeded straight to the table.

    There is no endpoint of its own: a site's aliases are a field of its RECORD, edited with the
    rest of what is known about it. Seeding the row is what the record's save does underneath.
    """
    write(
        db_path(client),
        [
            (
                "INSERT INTO site_aliases (id, site_id, alias, alias_sort) VALUES (?, ?, ?, ?)",
                (new_id(), site_id, alias, alias.casefold()),
            )
        ],
    )


def _attribute(client: TestClient, asset_id: str, username_id: str) -> None:
    write(
        db_path(client),
        [
            (
                "INSERT OR IGNORE INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (asset_id, username_id),
            )
        ],
    )


def _weigh(client: TestClient, losing: str, keeping: str) -> dict[str, object]:
    answer = client.post("/api/sites/weigh-merge", json={"sites": [losing], "into": keeping})
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


def _merge(client: TestClient, losing: str, keeping: str):  # type: ignore[no-untyped-def]
    return client.post("/api/sites/merge", json={"sites": [losing], "into": keeping})


# --- the usernames, which are the part that can lose something ----------------------------------


def test_a_twinned_username_keeps_its_files(client: TestClient, library: Library) -> None:
    """The load-bearing one.

    Both sites know `jane`. Only one `jane` can exist on the survivor, so the losing row has to go,
    and `asset_usernames` cascades from it, so the file it carried would go with it. What must
    happen is the file moving onto the twin FIRST.
    """
    sign_in(client)
    keep = _make_site(client, "PMVHaven")
    gone = _make_site(client, "pmv-haven")
    twin = _username(client, keep, "jane")
    doomed = _username(client, gone, "jane")
    _attribute(client, library.shared, doomed)
    _attribute(client, library.private, twin)

    assert _merge(client, gone, keep).status_code == 200

    held = read(
        db_path(client),
        "SELECT au.asset_id FROM asset_usernames au JOIN usernames u ON u.id = au.username_id"
        " WHERE u.site_id = ? ORDER BY au.asset_id",
        (keep,),
    )
    assert sorted(str(row["asset_id"]) for row in held) == sorted([library.shared, library.private])


def test_a_twinned_username_pours_what_it_knew_into_the_survivor(
    client: TestClient, library: Library
) -> None:
    """`person_id` is somebody's answer to "whose username is this". The twin is about to become the
    only record of that username, so an answer a human gave must not go with the row."""
    sign_in(client)
    keep = _make_site(client, "Site A")
    gone = _make_site(client, "Site B")
    jane = make_person(client, "Jane")
    _username(client, keep, "jane")
    doomed = _username(client, gone, "jane")
    write(
        db_path(client),
        [("UPDATE usernames SET person_id = ? WHERE id = ?", (jane, doomed))],
    )

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT person_id FROM usernames WHERE site_id = ?", (keep,))
    assert [row["person_id"] for row in rows] == [jane]


def test_a_twin_takes_the_losers_number_together_with_how_it_was_learned(
    client: TestClient,
) -> None:
    """A number is only as good as the record of where it came from. Taken where the twin has none,
    it brings `number_via` and `number_agreed` with it; where the twin has its own, the twin keeps
    its own number AND its own provenance, never the loser's beside it."""
    sign_in(client)
    keep = _make_site(client, "Site A")
    gone = _make_site(client, "Site B")
    bare = _username(client, keep, "jane")
    doomed = _username(client, gone, "jane", number="31415926")
    known = _username(client, keep, "ilva", number="111")
    rival = _username(client, gone, "ilva", number="222")
    write(
        db_path(client),
        [
            (
                "UPDATE usernames SET number_via = 'metadata', number_agreed = 3 WHERE id = ?",
                (doomed,),
            ),
            (
                "UPDATE usernames SET number_via = 'typed', number_agreed = NULL WHERE id = ?",
                (known,),
            ),
            (
                "UPDATE usernames SET number_via = 'metadata', number_agreed = 9 WHERE id = ?",
                (rival,),
            ),
        ],
    )

    assert _merge(client, gone, keep).status_code == 200

    rows = read(
        db_path(client),
        "SELECT id, number, number_via, number_agreed FROM usernames WHERE site_id = ?",
        (keep,),
    )
    said = {
        str(row["id"]): (row["number"], row["number_via"], row["number_agreed"]) for row in rows
    }
    assert said == {bare: ("31415926", "metadata", 3), known: ("111", "typed", None)}


def test_a_username_with_no_twin_simply_changes_sites(client: TestClient) -> None:
    """The ordinary case, and the one that must not be folded: nothing collides, so the row moves
    with everything on it rather than being rebuilt."""
    sign_in(client)
    keep = _make_site(client, "Site A")
    gone = _make_site(client, "Site B")
    moving = _username(client, gone, "someone-else")

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT id, site_id FROM usernames", ())
    assert [(str(row["id"]), str(row["site_id"])) for row in rows] == [(moving, keep)]


def test_a_twin_matched_by_NUMBER_rather_than_username_is_still_folded(
    client: TestClient,
) -> None:
    """A site's own id for a username is unique per site too, through a partial index. A fold that
    only asked about the name would move a row whose NUMBER already exists and the whole
    transaction would fail on a constraint nothing on screen can explain."""
    sign_in(client)
    keep = _make_site(client, "Site A")
    gone = _make_site(client, "Site B")
    _username(client, keep, "old-name", number="4242")
    _username(client, gone, "new-name", number="4242")

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT name FROM usernames WHERE site_id = ?", (keep,))
    assert [str(row["name"]) for row in rows] == ["old-name"]


# --- a site can be a site's parent ---------------------------------------------------------------


def test_the_labels_of_a_merged_network_move_to_the_survivor(client: TestClient) -> None:
    """A label whose network has gone is a label; one pointing at a deleted row is a broken
    record."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Network")
    label = _make_site(client, "Label")
    write(db_path(client), [("UPDATE sites SET parent_id = ? WHERE id = ?", (gone, label))])

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT parent_id FROM sites WHERE id = ?", (label,))
    assert rows[0]["parent_id"] == keep


def test_a_site_never_becomes_its_own_parent(client: TestClient) -> None:
    """Merging a network into one of its own labels. Reachable two ways and both are ordinary, so
    the guard is on the survivor's row rather than on either path into it."""
    sign_in(client)
    label = _make_site(client, "Label")
    network = _make_site(client, "Network")
    write(db_path(client), [("UPDATE sites SET parent_id = ? WHERE id = ?", (network, label))])

    assert _merge(client, network, label).status_code == 200

    rows = read(db_path(client), "SELECT parent_id FROM sites WHERE id = ?", (label,))
    assert rows[0]["parent_id"] is None


# --- a site can hold a sealed login --------------------------------------------------------------


def _give_a_login(client: TestClient, site_id: str) -> None:
    write(
        db_path(client),
        [
            (
                "INSERT INTO site_connections (id, site_id, status, updated_at)"
                " VALUES (?, ?, 'ready', 0)",
                (new_id(), site_id),
            )
        ],
    )


def test_two_logins_refuse_the_merge_rather_than_choosing_one(client: TestClient) -> None:
    """The only refusal here that is not a 404, because it is the only one somebody can act on. A
    merge cannot be taken back and cannot report having destroyed a secret."""
    sign_in(client)
    keep = _make_site(client, "Site A")
    gone = _make_site(client, "Site B")
    _give_a_login(client, keep)
    _give_a_login(client, gone)

    answer = _merge(client, gone, keep)

    assert answer.status_code == 409
    assert "login" in answer.json()["detail"]
    # Refused before anything was written: both sites are still there.
    assert client.get(f"/api/sites/{gone}").status_code == 200


def test_one_login_moves_to_the_survivor(client: TestClient) -> None:
    """Only two of them is a problem. One is the ordinary case and it must not be lost."""
    sign_in(client)
    keep = _make_site(client, "Site A")
    gone = _make_site(client, "Site B")
    _give_a_login(client, gone)

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT site_id FROM site_connections", ())
    assert [row["site_id"] for row in rows] == [keep]


# --- the shape a person merge already settled ----------------------------------------------------


def test_weighing_counts_what_would_move_and_writes_nothing(
    client: TestClient, library: Library
) -> None:
    """The numbers the confirm screen shows, counted rather than estimated: they are the whole
    guard on an act with no undo."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    username = _username(client, gone, "jane")
    _attribute(client, library.shared, username)
    _alias(client, gone, "GN")

    weighed = _weigh(client, gone, keep)

    assert (weighed["from_name"], weighed["into_name"]) == ("Goner", "Keeper")
    assert weighed["files"] == 1
    assert weighed["usernames"] == 1
    assert weighed["aliases"] == 1
    # Nothing moved.
    assert client.get(f"/api/sites/{gone}").status_code == 200


def test_the_name_that_goes_becomes_an_also_known_as(client: TestClient) -> None:
    """Nothing becomes unfindable. A site's other names are in the word index as well as on its
    page, so a library that found `Goner` before still does."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT alias FROM site_aliases WHERE site_id = ?", (keep,))
    assert [str(row["alias"]) for row in rows] == ["Goner"]


def test_a_survivor_already_holding_that_alias_does_not_gain_a_second(
    client: TestClient,
) -> None:
    """Checked rather than left to `INSERT OR IGNORE` alone.

    The insert would be quietly correct either way (the constraint folds case), but asking first
    is what keeps the two spellings from being a question anybody has to think about. The other
    branch of the same guard, where the survivor's own NAME is what is going, cannot be reached for
    a site: `sites.name` is unique without regard to case, so two rows spelled the same never
    exist. It is kept because a person's can, and one rule in one shape beats two.
    """
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    _alias(client, keep, "goner")

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT alias FROM site_aliases WHERE site_id = ?", (keep,))
    assert [str(row["alias"]) for row in rows] == ["goner"]


def test_the_survivors_blanks_are_filled_and_what_it_knows_is_left_alone(
    client: TestClient,
) -> None:
    """The delete takes the row and everything on it, and there is no undo. Fill what is blank,
    never overwrite what is filled: the only rule that cannot lose anything.

    The ADDRESS is the first of a site's links by id (`sites.SITE_ADDRESS`), and the links move
    keeping nothing but their rows, so the going site's link is written FIRST here, with the
    smaller id: moved as it was, it would become the survivor's address. It must land after."""
    from sift.slices.people.site_merge import _RECORD_ROW

    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    link = "INSERT INTO site_links (id, site_id, url, label, created_at) VALUES (?, ?, ?, NULL, 0)"
    write(
        db_path(client),
        [
            (link, (new_id(), gone, "https://goner.example")),
            (link, (new_id(), keep, "https://keeper.example")),
            ("UPDATE sites SET notes = 'worth keeping' WHERE id = ?", (gone,)),
        ],
    )

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), _RECORD_ROW, (keep,))
    assert (rows[0]["site_url"], rows[0]["notes"]) == ("https://keeper.example", "worth keeping")
    links = read(
        db_path(client), "SELECT url FROM site_links WHERE site_id = ? ORDER BY id", (keep,)
    )
    assert [one["url"] for one in links] == ["https://keeper.example", "https://goner.example"]


def test_a_folder_suggestion_naming_the_old_site_is_rewritten(
    client: TestClient, library: Library
) -> None:
    """`folder_claims.site` holds the site's NAME rather than its id, so nothing about it moves
    on a key. Left alone, Organize would go on proposing a site that no longer exists."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    write(
        db_path(client),
        [
            (
                "INSERT INTO folder_claims"
                " (id, folder_id, kind, name_key, proposed, site, evidence, created_at)"
                " VALUES (?, ?, 'site', 'goner', 'Goner', 'Goner', 'by_hand', 0)",
                (new_id(), library.folder),
            )
        ],
    )

    assert _merge(client, gone, keep).status_code == 200

    rows = read(db_path(client), "SELECT site FROM folder_claims", ())
    assert [str(row["site"]) for row in rows] == ["Keeper"]


def test_a_site_cannot_be_merged_into_itself(client: TestClient) -> None:
    """It would delete it. A set that is only the survivor is not a merge that failed, it is one
    that was never possible."""
    sign_in(client)
    only = _make_site(client, "Only")

    assert _merge(client, only, only).status_code == 409
    assert client.get(f"/api/sites/{only}").status_code == 200


def test_an_id_that_names_nothing_is_a_404_either_way(client: TestClient) -> None:
    """Denied and missing are the same answer everywhere in Sift, and a merge names two ids."""
    sign_in(client)
    keep = _make_site(client, "Keeper")

    assert _merge(client, NEVER_EXISTED, keep).status_code == 404
    assert _merge(client, keep, NEVER_EXISTED).status_code == 404


def test_a_guest_cannot_merge_sites(client: TestClient) -> None:
    """It changes what the library says is true for everybody, so it is admin-only: the same rule
    renaming follows."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    sign_in(client, role="guest", who="two")

    assert _merge(client, gone, keep).status_code in (401, 403)


def test_the_list_of_tables_is_read_out_of_the_statements(client: TestClient) -> None:
    """A list of table names kept beside the statements naming them is a list that drifts the first
    time somebody adds a table and edits only one of the two."""
    from sift.slices.people.site_merge import tables_that_move

    moved = tables_that_move()

    assert {"usernames", "sites", "site_connections", "site_aliases"} <= moved
    assert all(name and not name.isspace() for name in moved)


def test_every_table_naming_a_site_is_one_this_merge_touches(client: TestClient) -> None:
    """Read from the SCHEMA rather than from a list somebody maintains.

    This is the check the whole module is written to satisfy: a table added later that points at a
    site and is not handled is a merge that silently leaves rows behind, and nothing else in the
    suite would notice. `folder_claims` is named as an exception because it holds the site's NAME
    rather than a key, and it is rewritten by its own statement.
    """
    from sift.slices.people.site_merge import tables_that_move

    naming_a_site = {
        str(row["name"])
        for row in read(
            db_path(client),
            "SELECT m.name AS name FROM sqlite_master m"
            " WHERE m.type = 'table' AND m.sql LIKE '%REFERENCES sites%'",
        )
    }

    assert naming_a_site, "the schema walk found no tables at all, so it proves nothing"
    assert naming_a_site <= tables_that_move(), (
        f"these point at a site and the merge does not touch them: "
        f"{sorted(naming_a_site - tables_that_move())}"
    )


# --- the answers a ROUTE can never ask for -------------------------------------------------------
#
# Every one of these is driven against the function rather than through HTTP, and that is not a
# shortcut: the route resolves both halves of the merge through the scoped read before it calls in,
# so a 404 is the only thing an unknown id can produce there. What these ask is what the function
# answers when it is handed one anyway, which is what a second caller would get, and what the
# next caller will get if the resolve is ever moved.


def _direct(client: TestClient, work: Callable[[Database], Awaitable[object]]) -> object:
    """Run one coroutine against the application's own database file.

    A handle of its own rather than the application's: the test client is synchronous and its loop
    is not this one, so a coroutine cannot be handed to it. WAL makes a second reader free.
    """
    path = db_path(client)

    async def run() -> object:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return await work(database)
        finally:
            await database.close()

    return asyncio.run(run())


def _access(client: TestClient) -> Repository:
    """The application's own permission layer, which a merge forgets grants through."""
    return client.app.state.access  # type: ignore[attr-defined,no-any-return]


def test_weighing_a_merge_naming_a_site_that_is_not_there_answers_with_nothing(
    client: TestClient,
) -> None:
    """A merge whose halves cannot both be found is not a merge that failed: it is one that was
    never possible, and a screen wants to hear that rather than a zero it would draw as a real
    answer. Both directions, because either id can be the missing one."""
    from sift.slices.people.site_merge import weigh

    sign_in(client)
    real = _make_site(client, "Somewhere")

    async def both(database: Database) -> object:
        return (
            await weigh(database, losing=NEVER_EXISTED, keeping=real),
            await weigh(database, losing=real, keeping=NEVER_EXISTED),
        )

    assert _direct(client, both) == (None, None)


def test_weighing_a_SET_that_cannot_be_weighed_answers_with_nothing(client: TestClient) -> None:
    """Three ways for a set to have no answer, and each one alone would pass against the other two
    being broken: nothing left after the survivor is taken out of it, a survivor that is not there,
    and one of the set that is not there."""
    from sift.slices.people.site_merge import weigh_many

    sign_in(client)
    real = _make_site(client, "Somewhere")
    other = _make_site(client, "Elsewhere")

    async def three(database: Database) -> object:
        return (
            # The whole set is deduplicated and the survivor taken out of it, so naming only the
            # survivor (or naming them twice) leaves nothing going.
            await weigh_many(database, losing=[real, real], keeping=real),
            await weigh_many(database, losing=[other], keeping=NEVER_EXISTED),
            await weigh_many(database, losing=[NEVER_EXISTED], keeping=real),
        )

    assert _direct(client, three) == (None, None, None)


def test_merging_a_SET_that_cannot_be_merged_writes_nothing_and_says_so(
    client: TestClient,
) -> None:
    """The same two refusals one layer up, and they matter more here: this one writes. A set with
    nothing going, and a set naming a site that is not there."""
    from sift.slices.people.site_merge import merge_many

    sign_in(client)
    real = _make_site(client, "Somewhere")

    async def two(database: Database) -> object:
        return (
            await merge_many(
                database, _access(client), losing=[real], keeping=real, actor=Actor.sift("stash")
            ),
            await merge_many(
                database,
                _access(client),
                losing=[NEVER_EXISTED],
                keeping=real,
                actor=Actor.sift("stash"),
            ),
        )

    assert _direct(client, two) == (None, None)
    assert len(read(db_path(client), "SELECT id FROM sites")) == 1


def test_a_set_that_cannot_be_finished_leaves_every_site_where_it_was(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge over several sites is ONE transaction, and this is what says so.

    There is no undo. Stopping halfway through folding four sites leaves a library where two are
    gone, two are still there, and nothing on screen says which state it is in, so the guard
    leaves the block by RAISING rather than by returning, because returning would commit what had
    already been done. That distinction is invisible in a normal run: every id was resolved a moment
    earlier, so the removal cannot come back empty.

    Made to come back empty here, which is the only way to see the rollback at all. What is asserted
    is not the exception: it is that the FIRST site's files did not move.
    """
    from sift.slices.people import site_merge

    sign_in(client)
    keeping = _make_site(client, "Somewhere")
    first = _make_site(client, "Elsewhere")
    second = _make_site(client, "Nowhere")
    username = _username(client, first, "someone")

    monkeypatch.setattr(site_merge, "_REMOVE", "SELECT id FROM sites WHERE id = ? AND 1 = 0")

    async def attempt(database: Database) -> object:
        try:
            await site_merge.merge_many(
                database,
                _access(client),
                losing=[first, second],
                keeping=keeping,
                actor=Actor.sift("stash"),
            )
        except RuntimeError:
            return "refused"
        return "committed"

    assert _direct(client, attempt) == "refused"

    still = read(db_path(client), "SELECT site_id FROM usernames WHERE id = ?", (username,))
    assert still and still[0]["site_id"] == first, "half a merge was committed"
    assert len(read(db_path(client), "SELECT id FROM sites")) == 3


# --- what the sheet says, BY NAME -----------------------------------------------------------------


def test_the_weigh_names_what_moves_and_whose_it_was(client: TestClient) -> None:
    """The same question a person merge answers by name: WHICH usernames, which other names, which
    addresses, which Sites published under it, and what fills which blank."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    network = _make_site(client, "Another Studio")
    label = _make_site(client, "Marrowvale Studio")
    _username(client, gone, "janedoe")
    _alias(client, gone, "GN")
    write(
        db_path(client),
        [
            (
                "INSERT INTO site_links (id, site_id, url, created_at)"
                " VALUES (?, ?, 'https://goner.example', 0)",
                (new_id(), gone),
            ),
            ("UPDATE sites SET parent_id = ? WHERE id = ?", (gone, label)),
            (
                "UPDATE sites SET parent_id = ?, notes = 'worth keeping' WHERE id = ?",
                (network, gone),
            ),
        ],
    )

    weighed = _weigh(client, gone, keep)

    assert weighed["usernames_named"] == [{"name": "janedoe", "where": "Goner", "whose": "Goner"}]
    assert weighed["aliases_named"] == [{"name": "GN", "where": None, "whose": "Goner"}]
    assert weighed["links_named"] == [
        {"name": "https://goner.example", "where": None, "whose": "Goner"}
    ]
    assert weighed["children_named"] == [
        {"name": "Marrowvale Studio", "where": None, "whose": "Goner"}
    ]
    # The keeper has no web address, so the going site's first becomes its address: a blank the
    # merge fills, like the details and the parent (`site_merge._RECORD_COLUMNS`).
    assert weighed["filled"] == [
        {
            "key": "address",
            "label": "Web address",
            "value": "https://goner.example",
            "whose": "Goner",
        },
        {"key": "details", "label": "Details", "value": "worth keeping", "whose": "Goner"},
        {"key": "parent", "label": "Part of", "value": "Another Studio", "whose": "Goner"},
    ]
    assert weighed["facts"] == 3


def test_the_weigh_does_not_call_the_blank_row_a_username(client: TestClient) -> None:
    """Never "Username on Discordapp moves too" with a blank name because the row is blank.
    That row is a filing's own "from this site, poster unknown" and nobody gave it: what it
    carries is files, which the files line counts. So it is neither counted nor named as one."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    _username(client, gone, "")
    _username(client, gone, "janedoe")

    weighed = _weigh(client, gone, keep)

    assert weighed["usernames"] == 1
    assert weighed["usernames_named"] == [{"name": "janedoe", "where": "Goner", "whose": "Goner"}]


def test_the_weigh_names_exactly_the_boxes_the_merge_then_fills(client: TestClient) -> None:
    """The fill is spelled twice, and this holds the weigh's list to the statement: every column it
    names filled on the site going, and the weigh must name precisely the ones the merge changed.
    `kind` is filled and deliberately not named (nothing reads it), so it is left out of both.
    Read through the merge's own record statement, which reads the address as the site's first
    link (the column went in catalog v66)."""
    from sift.slices.people.site_merge import _RECORD_COLUMNS, _RECORD_ROW

    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    network = _make_site(client, "Another Studio")
    write(
        db_path(client),
        [
            (
                "INSERT INTO site_links (id, site_id, url, label, created_at)"
                " VALUES (?, ?, 'https://goner.example', NULL, 0)",
                (new_id(), gone),
            ),
            ("UPDATE sites SET notes = 'x', parent_id = ? WHERE id = ?", (network, gone)),
        ],
    )
    columns = [column for column, _ in _RECORD_COLUMNS]
    before = read(db_path(client), _RECORD_ROW, (keep,))[0]

    weighed = _weigh(client, gone, keep)
    assert _merge(client, gone, keep).status_code == 200

    after = read(db_path(client), _RECORD_ROW, (keep,))[0]
    changed = {column for column in columns if before[column] != after[column]}
    key_of = {key: column for column, key in _RECORD_COLUMNS}
    named = {key_of[str(one["key"])] for one in weighed["filled"]}  # type: ignore[attr-defined]
    assert named == changed == set(columns)


def test_the_survivor_is_never_counted_as_published_under_itself(client: TestClient) -> None:
    """Merging a network into one of its own labels: the label was published under the network,
    and after the merge it is not published under ITSELF: that self-parent is cleared. So it is
    not one of the Sites the sheet says change networks."""
    sign_in(client)
    network = _make_site(client, "Another Studio")
    label = _make_site(client, "Marrowvale Studio")
    write(db_path(client), [("UPDATE sites SET parent_id = ? WHERE id = ?", (network, label))])

    weighed = _weigh(client, network, label)

    assert weighed["children"] == 0
    assert weighed["children_named"] == []


# --- the stores that name a Site by a KIND WORD, which no key reaches -----------------------------


def test_a_site_merge_takes_its_record_with_it(client: TestClient) -> None:
    """Folding Twitter into X must not leave a stash-box run, answer or event about Twitter under
    an id nothing answers to: none of those stores has a key to cascade or to move. They follow
    (through the SAME list the person merge runs), and the merge's own event keeps naming who went,
    with what came over in its payload."""
    import json

    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    _username(client, gone, "jane")
    about, done_with = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at,"
                " verb, actor_kind) VALUES (?, 'ledger', '', '', '', 1, 'edited', 'sift')",
                (about,),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
                " VALUES (?, 'site', ?, NULL)",
                (about, gone),
            ),
            (
                "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at,"
                " verb, actor_kind, object_kind, object_id, object_name)"
                " VALUES (?, 'ledger', '', '', '', 2, 'downloaded', 'sift', 'site', ?, 'Goner')",
                (done_with, gone),
            ),
            (
                "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic)"
                " VALUES (?, 'site', ?, 'some-box', 5, 0)",
                (new_id(), gone),
            ),
            (
                "INSERT INTO stash_box_undecided (subject, local_id, candidates, seen_at)"
                " VALUES ('site', ?, 2, 5)",
                (gone,),
            ),
        ],
    )

    assert _merge(client, gone, keep).status_code == 200

    path = db_path(client)
    assert read(
        path,
        "SELECT subject_id, name FROM workbench_decision_subjects WHERE decision_id = ?",
        (about,),
    ) == [{"subject_id": keep, "name": "Goner"}]
    assert read(path, "SELECT object_id FROM workbench_decisions WHERE id = ?", (done_with,)) == [
        {"object_id": keep}
    ]
    assert read(path, "SELECT local_id FROM enrichment_runs WHERE subject = 'site'") == [
        {"local_id": keep}
    ]
    assert read(path, "SELECT local_id FROM stash_box_undecided WHERE subject = 'site'") == [
        {"local_id": keep}
    ]
    (event,) = read(
        path,
        "SELECT s.subject_id AS gone, d.payload AS payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id WHERE d.verb = 'merged'",
    )
    assert event["gone"] == gone
    assert json.loads(str(event["payload"]))["usernames"] == 1


def test_every_store_naming_a_thing_by_a_kind_word_is_followed_or_says_why(
    client: TestClient,
) -> None:
    """The schema walk the `REFERENCES` checks cannot do.

    Both merges hold their key tables to every `REFERENCES people` / `REFERENCES sites` in the
    schema. The other shape escapes that check: an id with a kind word beside it, no key at all, so
    nothing would notice it being left behind. Every table of that shape is either followed by both
    merges (`kernel/access/merged.FOLLOWS`) or named in `NOT_FOLLOWED` with the reason, and a new
    one fails here until somebody decides which.
    """
    import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (the schema walk reads the catalog of every table)

    from sift.kernel.access.merged import NOT_FOLLOWED, tables_that_follow

    # One plain connection for the whole walk: a helper that opens the database once per table
    # costs a minute over two hundred tables. A virtual table is skipped: its module is not loaded
    # here, and a full-text or vector index names nothing by a kind word.
    kind_worded = set()
    with sqlite3.connect(db_path(client)) as looking:
        tables = [
            str(name)
            for (name,) in looking.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE '%VIRTUAL%'"
            )
        ]
        for table in tables:
            columns = {
                str(name)
                for (name,) in looking.execute("SELECT name FROM pragma_table_info(?)", (table,))
            }
            if columns & {"subject", "subject_kind", "object_kind", "object_type"} or (
                "kind" in columns and columns & {"subject_id", "object_id", "local_id", "opened_id"}
            ):
                kind_worded.add(table)

    assert {"enrichment_runs", "workbench_decisions", "opinions"} <= kind_worded, (
        "the walk found too little, so it proves nothing"
    )
    decided = tables_that_follow() | set(NOT_FOLLOWED)
    assert kind_worded <= decided, f"undecided: {sorted(kind_worded - decided)}"


def test_the_blanks_and_what_each_brings_answer_nothing_for_a_site_that_is_not_there(
    client: TestClient,
) -> None:
    """The two reads a merge makes before it writes, asked about an id that names nothing: no
    answer rather than an empty one, which a sheet would draw as "nothing is filled in"."""
    from sift.slices.people.site_merge import _filled, _what_each_brings

    sign_in(client)
    real = _make_site(client, "Somewhere")

    async def reads(database: Database) -> object:
        return (
            await _filled(database, going=[real], keeping=NEVER_EXISTED),
            await _filled(database, going=[NEVER_EXISTED], keeping=real),
            await _what_each_brings(database, going=[NEVER_EXISTED], keeping=real),
        )

    assert _direct(client, reads) == (None, None, None)


def test_the_cover_is_named_as_coming_over_only_onto_a_site_that_has_none(
    client: TestClient, library: Library
) -> None:
    """The cover moves as one thing onto a survivor with none. Onto one that has its own it moves
    nowhere, and the sheet does not say it does."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    write(
        db_path(client),
        [("UPDATE sites SET cover_asset_id = ? WHERE id = ?", (library.shared, gone))],
    )

    onto_none = _weigh(client, gone, keep)
    write(
        db_path(client),
        [("UPDATE sites SET cover_asset_id = ? WHERE id = ?", (library.private, keep))],
    )
    onto_its_own = _weigh(client, gone, keep)

    assert [one["key"] for one in onto_none["filled"]] == ["cover"]  # type: ignore[attr-defined]
    assert onto_its_own["filled"] == []


def test_the_going_sites_chosen_picture_is_kept_over_the_frame_its_usernames_bring(
    client: TestClient, library: Library
) -> None:
    """The going Site's picture comes across with its chosen moment. Neither the survivor nor the
    network above it takes a frame of a file it now holds: the default-cover rule does not reach a
    Site (`kernel/access/default_covers.py`), so the network stays its letter."""
    sign_in(client)
    keep = _make_site(client, "Keeper")
    gone = _make_site(client, "Goner")
    network = _make_site(client, "Network")
    write(db_path(client), [("UPDATE sites SET parent_id = ? WHERE id = ?", (network, keep))])
    _attribute(client, library.shared, _username(client, gone, "someone"))
    write(
        db_path(client),
        [
            (
                "UPDATE sites SET cover_asset_id = ?, cover_at_ms = 1500, cover_by_default = NULL"
                " WHERE id = ?",
                (library.private, gone),
            )
        ],
    )

    assert _merge(client, gone, keep).status_code == 200

    covers = {
        str(row["id"]): (row["cover_asset_id"], row["cover_at_ms"])
        for row in read(db_path(client), "SELECT id, cover_asset_id, cover_at_ms FROM sites", ())
    }
    assert covers[keep] == (library.private, 1500)
    assert covers[network] == (None, None)


def test_what_is_kept_by_id_follows_the_site(client: TestClient, library: Library) -> None:
    """A download, the downloader's key for a web site and a watermark reading each keep their Site
    by id. The key matters most: it cascades on delete, so left behind the merge would delete it
    and the next download from that web site would make the merged-away Site again."""
    sign_in(client)
    going = _make_site(client, "Riverbend")
    staying = _make_site(client, "Harbour Films")
    write(
        db_path(client),
        [
            (
                "INSERT INTO downloads (id, url, url_hash, state, site_id, created_at)"
                " VALUES ('d-site', 'https://example.test/b', 'h-site', 'done', ?, 0)",
                (going,),
            ),
            (
                "INSERT INTO download_sites (key, site_id, filed_at) VALUES ('riverbend', ?, 0)",
                (going,),
            ),
            (
                "INSERT INTO watermark_reads (asset_id, text, site, site_id, confidence, read_at)"
                " VALUES (?, 'riverbend', 'Riverbend', ?, 0.9, 0)",
                (library.shared, going),
            ),
        ],
    )

    assert _merge(client, going, staying).status_code == 200

    (download,) = read(db_path(client), "SELECT site_id FROM downloads WHERE id = 'd-site'")
    (key,) = read(db_path(client), "SELECT site_id FROM download_sites WHERE key = 'riverbend'")
    (reading,) = read(
        db_path(client), "SELECT site_id FROM watermark_reads WHERE asset_id = ?", (library.shared,)
    )
    assert download["site_id"] == key["site_id"] == reading["site_id"] == staying


def test_a_filter_kept_by_id_follows_the_site(client: TestClient) -> None:
    """A saved filter and a saved Theater wall's cell keep the Sites they name by id, inside their
    text. Left behind, both read "a Site that no longer exists" after the merge and match nothing."""
    sign_in(client)
    going = _make_site(client, "Riverbend")
    staying = _make_site(client, "Harbour Films")
    (user,) = read(db_path(client), "SELECT id FROM users LIMIT 1")
    write(
        db_path(client),
        [
            (
                "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
                " VALUES ('s-site', ?, 'asset', 'Kept', ?, 0)",
                (user["id"], f"sites={going}&media=video"),
            ),
            (
                "INSERT INTO theater_arrangements (id, user_id, name, layout, created_at,"
                " updated_at) VALUES ('w-site', ?, 'Wall', 'side_by_side', 0, 0)",
                (user["id"],),
            ),
            (
                "INSERT INTO theater_cells (arrangement_id, position, source, media_kind, ordering,"
                " end_behaviour, volume) VALUES ('w-site', 0, ?, 'video', 'shuffle', 'once', 0)",
                (f"sites:{going}",),
            ),
        ],
    )

    assert _merge(client, going, staying).status_code == 200

    (kept,) = read(db_path(client), "SELECT query FROM saved_searches WHERE id = 's-site'")
    (cell,) = read(
        db_path(client), "SELECT source FROM theater_cells WHERE arrangement_id = 'w-site'"
    )
    assert kept["query"] == f"sites={staying}&media=video"
    assert cell["source"] == f"sites:{staying}"
