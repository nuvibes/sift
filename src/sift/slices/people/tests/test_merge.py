# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two people who turn out to be one.

There is no undo, and that is deliberate rather than unfinished: putting a merge back would mean
recreating a deleted person and then deciding, row by row, which of the survivor's rows had been
theirs, and a half-accurate undo of an identity is a library that looks correct and is quietly
wrong. So the guard is BEFORE the fact: the count of what would move is what somebody presses
against, and the name that goes is written onto the survivor as an also-known-as, so nothing that
found them before stops finding them.

Two refusals carry as much as the write does. Both halves are resolved through the scoped read
first: a merge names two ids, and answering about one the vault is concealing would say they are
there as plainly as showing their page would. And a person cannot be merged into themselves, which
would otherwise delete them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Repository
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    db_path,
    grant_on,
    grants_naming,
    make_person,
    make_username,
    read,
    sign_in,
    write,
)


def _weigh(client: TestClient, losing: str, keeping: str) -> dict[str, object]:
    answer = client.post("/api/people/weigh-merge", json={"people": [losing], "into": keeping})
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


def _merge(client: TestClient, losing: str, keeping: str):  # type: ignore[no-untyped-def]
    """One person folded into another, through the one route there is.

    A set of one is a set: one route, so there is one copy of the sequence of tables a merge
    moves rather than a pairwise copy beside a set's.
    """
    return client.post("/api/people/merge", json={"people": [losing], "into": keeping})


def test_weighing_counts_what_would_move_and_writes_nothing(
    client: TestClient, library: Library
) -> None:
    """The numbers the confirm screen shows. Counted from the database rather than estimated,
    because they are the whole guard on an act with no undo."""
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane])
    client.post(f"/api/people/{jane}/aliases", json={"alias": "JD"})

    weighed = _weigh(client, jane, doe)

    assert (weighed["from_name"], weighed["into_name"]) == ("Jane", "Jane Doe")
    assert weighed["files"] == 1
    assert weighed["aliases"] == 1
    # Nothing moved: both are still there, and the files are still hers.
    assert client.get(f"/api/people/{jane}").status_code == 200
    assert [one["id"] for one in client.get(f"/api/assets/{library.shared}/people").json()] == [
        jane
    ]


def test_a_merge_moves_what_it_counted_and_removes_the_one_left_empty(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane])

    answer = _merge(client, jane, doe)

    assert answer.status_code == 200, answer.text
    assert answer.json()["files"] == 1
    assert client.get(f"/api/people/{jane}").status_code == 404
    assert [one["id"] for one in client.get(f"/api/assets/{library.shared}/people").json()] == [doe]


def _record(client: TestClient, person_id: str) -> dict[str, object]:
    return dict(client.get(f"/api/people/{person_id}").json().get("record") or {})


def test_what_the_one_that_goes_KNEW_is_not_deleted_with_their_row(
    client: TestClient,
) -> None:
    """The half of a person that no table moves.

    Every table that points at somebody moves. Everything a person's record is made of sits on
    their own row instead, and the row is deleted at the end, so merging somebody who had been
    looked up into somebody who had not would destroy every fact learned about them, on an
    operation with no undo and with nothing on screen saying it had happened.
    """
    sign_in(client)
    known = make_person(client, "Jane")
    bare = make_person(client, "Jane Doe")
    saved = client.put(
        f"/api/people/{known}",
        json={
            "name": "Jane",
            "record": {"birth_date": "1992-06-30", "country": "US", "hair_color": "AUBURN"},
        },
    )
    assert saved.status_code == 200, saved.text

    weighed = _weigh(client, known, bare)
    assert weighed["facts"] == 3, "the confirm screen has to say what would be gained"

    answer = _merge(client, known, bare)
    assert answer.status_code == 200, answer.text

    kept = _record(client, bare)
    assert kept["birth_date"] == "1992-06-30"
    assert kept["country"] == "US"
    assert kept["hair_color"] == "AUBURN"


def test_what_the_survivor_already_knows_is_never_overwritten(
    client: TestClient,
) -> None:
    """Fill what is blank, never replace what is filled.

    The only rule that cannot lose anything: where both know something the survivor's own answer
    stands, and where one of them does the library ends up knowing it too. It is also the rule the
    opinions table follows and the one the look-up sheet states in words.
    """
    sign_in(client)
    going = make_person(client, "Jane")
    staying = make_person(client, "Jane Doe")
    client.put(
        f"/api/people/{going}",
        json={"name": "Jane", "record": {"hair_color": "AUBURN", "ethnicity": "CAUCASIAN"}},
    )
    client.put(
        f"/api/people/{staying}",
        json={"name": "Jane Doe", "record": {"hair_color": "BLONDE"}},
    )

    assert _weigh(client, going, staying)["facts"] == 1, "only the blank one is gained"
    _merge(client, going, staying)

    kept = _record(client, staying)
    assert kept["hair_color"] == "BLONDE", "the survivor's own answer stands"
    assert kept["ethnicity"] == "CAUCASIAN", "and the blank one is filled"


def test_the_name_that_goes_becomes_an_also_known_as_on_the_survivor(
    client: TestClient,
) -> None:
    """Nothing becomes unfindable. Every search, filename and folder that found them before still
    does, which is what makes an act with no undo safe enough to offer."""
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")

    _merge(client, jane, doe)

    aliases = client.get(f"/api/people/{doe}/aliases").json()
    assert [one["alias"] for one in aliases] == ["Jane"]


def test_a_name_the_survivor_already_answers_to_is_not_written_twice(
    client: TestClient,
) -> None:
    """The survivor's own NAME is not in the alias table, so the unique constraint would not catch
    it, and an alias that repeats the name it sits under adds nothing and reads as a mistake."""
    sign_in(client)
    lower = make_person(client, "jane doe")
    upper = make_person(client, "Jane Doe")

    _merge(client, upper, lower)

    assert client.get(f"/api/people/{lower}/aliases").json() == []


def test_an_alias_the_survivor_already_holds_is_not_written_twice(
    client: TestClient,
) -> None:
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    client.post(f"/api/people/{doe}/aliases", json={"alias": "Jane"})

    _merge(client, jane, doe)

    assert [one["alias"] for one in client.get(f"/api/people/{doe}/aliases").json()] == ["Jane"]


def test_the_aliases_of_the_one_that_goes_come_across(client: TestClient) -> None:
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    client.post(f"/api/people/{jane}/aliases", json={"alias": "JD"})

    _merge(client, jane, doe)

    assert sorted(one["alias"] for one in client.get(f"/api/people/{doe}/aliases").json()) == [
        "JD",
        "Jane",
    ]


def test_a_file_both_of_them_were_on_is_not_carried_twice(
    client: TestClient, library: Library
) -> None:
    """The row moves onto a join that already has it, so the move has to tolerate the collision:
    a merge that failed on it would leave the library half-moved."""
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane, doe])

    answer = _merge(client, jane, doe)

    assert answer.status_code == 200, answer.text
    assert [one["id"] for one in client.get(f"/api/assets/{library.shared}/people").json()] == [doe]


def test_a_merge_keeps_the_attribution_a_person_made_over_the_one_sift_guessed(
    client: TestClient, library: Library
) -> None:
    """On a file both were on, the survivor's row stands, and with it the survivor's word for
    how the file got there. Put there by a person under the one going and by Sift under the
    survivor, the deliberate decision is the one that holds after the merge."""
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane, doe])
    write(
        db_path(client),
        [
            (
                "UPDATE asset_people SET source = 'faces' WHERE person_id = ? AND asset_id = ?",
                (doe, library.shared),
            )
        ],
    )

    def sources() -> dict[str, object]:
        rows = read(
            db_path(client),
            "SELECT person_id, source FROM asset_people WHERE asset_id = ?",
            (library.shared,),
        )
        return {str(row["person_id"]): row["source"] for row in rows}

    assert sources() == {jane: None, doe: "faces"}

    answer = _merge(client, jane, doe)

    assert answer.status_code == 200, answer.text
    assert sources() == {doe: None}, "the person's own decision, not Sift's guess"


def test_a_grant_naming_the_person_who_goes_is_forgotten_rather_than_moved(
    client: TestClient, library: Library
) -> None:
    """A grant is a permission somebody set on one specific person. Moving it would hand somebody
    access to the survivor's whole body of work on the strength of a decision about somebody else.
    Forgetting only ever removes access, which is the safe direction to be wrong in."""
    guest = sign_in(client, "guest", who="two")
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    grant_on(client, "person", jane, guest)
    assert grants_naming(client, "person", jane)

    _merge(client, jane, doe)

    assert grants_naming(client, "person", jane) == []
    assert grants_naming(client, "person", doe) == []


def test_several_are_folded_into_one_in_a_single_act(client: TestClient, library: Library) -> None:
    """Three rows somebody noticed were one person, taken together.

    One call rather than one per pair, and that is not an economy. A merge cannot be taken back, so
    three of them taken as three calls is three chances to stop halfway, and halfway through is a
    library where one of them is gone, two are still there, and nothing anywhere says which state
    it is in or how to finish it.
    """
    sign_in(client)
    keeping = make_person(client, "Jane Doe")
    first = make_person(client, "Jane")
    second = make_person(client, "J. Doe")
    assign(client, [library.shared], [first])

    answer = client.post(
        "/api/people/merge", json={"people": [first, second, keeping], "into": keeping}
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["files"] == 1, "the counts are added up over the whole set"
    assert client.get(f"/api/people/{first}").status_code == 404
    assert client.get(f"/api/people/{second}").status_code == 404
    assert client.get(f"/api/people/{keeping}").status_code == 200
    # The survivor was named among the people going and is dropped rather than refused: somebody
    # selects four rows and then says which of the four to keep, so the whole selection is the
    # obvious thing to send.
    aliases = {one["alias"] for one in client.get(f"/api/people/{keeping}/aliases").json()}
    assert aliases == {"Jane", "J. Doe"}


def test_folding_several_leaves_nothing_half_done(client: TestClient, library: Library) -> None:
    """Every one of them is resolved before anything is written.

    A set where one id names nobody is refused whole. The alternative (fold the ones that resolve
    and report the rest) is the half-done library this route exists to make impossible.
    """
    sign_in(client)
    keeping = make_person(client, "Jane Doe")
    real = make_person(client, "Jane")
    assign(client, [library.shared], [real])

    answer = client.post(
        "/api/people/merge", json={"people": [real, NEVER_EXISTED], "into": keeping}
    )

    assert answer.status_code == 404
    assert client.get(f"/api/people/{real}").status_code == 200, "nothing was written"
    assert [one["id"] for one in client.get(f"/api/assets/{library.shared}/people").json()] == [
        real
    ]


def test_somebody_cannot_be_merged_into_themselves(client: TestClient) -> None:
    """It would delete them. A 409 rather than a quiet no-op, because the press was a mistake and a
    screen that says nothing is a screen that leaves somebody expecting a merge."""
    sign_in(client)
    jane = make_person(client, "Jane")

    assert _merge(client, jane, jane).status_code == 409
    assert (
        client.post("/api/people/weigh-merge", json={"people": [jane], "into": jane}).status_code
        == 409
    )


def test_a_merge_naming_somebody_who_is_not_there_is_a_404(client: TestClient) -> None:
    """And the same answer whichever half is missing, so the route cannot become a way to ask
    whether somebody exists."""
    sign_in(client)
    jane = make_person(client, "Jane")

    assert _merge(client, jane, NEVER_EXISTED).status_code == 404
    assert _merge(client, NEVER_EXISTED, jane).status_code == 404
    assert (
        client.post(
            "/api/people/weigh-merge", json={"people": [jane], "into": NEVER_EXISTED}
        ).status_code
        == 404
    )


def test_a_merge_naming_somebody_the_vault_is_concealing_is_a_404(client: TestClient) -> None:
    """The refusal that is not about the id existing, and the one a 404 cannot be mistaken for.

    Both halves are resolved through the SCOPED read before anything moves, and the scoped read is
    the only thing that knows about the vault: `weigh` reads the database directly and would fold
    a concealed person in without a word, reporting their name back in the count as it went. An id
    outlives being vaulted: anybody who saw them before the flag was set still holds one.

    So this is what separates the loop from the refusal underneath it. A merge naming somebody who
    does not exist is refused twice over, by the loop and again by the weighing, which is why a
    test using a made-up id proves nothing about the loop at all. This one names somebody who is
    REALLY THERE and may not be acted on, where the two answers differ.
    """
    sign_in(client)
    jane = make_person(client, "Jane")
    hidden = make_person(client, "Hidden One", vault=True)

    assert _merge(client, hidden, jane).status_code == 404
    assert (
        client.post("/api/people/weigh-merge", json={"people": [hidden], "into": jane}).status_code
        == 404
    )

    # And they are still there. A 404 that had already merged them is the worse of the two faults.
    assert _still_there(client, hidden), "the concealed person was folded in and then refused"


def test_a_guest_may_neither_weigh_a_merge_nor_take_one(client: TestClient) -> None:
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    sign_in(client, "guest", who="two")

    assert (
        client.post("/api/people/weigh-merge", json={"people": [jane], "into": doe}).status_code
        == 403
    )
    assert _merge(client, jane, doe).status_code == 403


def test_weighing_a_merge_naming_nobody_answers_with_nothing(client: TestClient) -> None:
    """A merge whose halves cannot both be found is not a merge that failed: it is one that was
    never possible. Driven against the function because the route resolves both ids first, which is
    a different refusal for a different reason."""
    import asyncio

    from sift.kernel.db import Database
    from sift.slices.people.merge import merge_many, weigh

    sign_in(client)
    jane = make_person(client, "Jane")
    path = db_path(client)

    async def run() -> tuple[object, object, object]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return (
                await weigh(database, losing=NEVER_EXISTED, keeping=jane),
                await weigh(database, losing=jane, keeping=NEVER_EXISTED),
                await merge_many(
                    database,
                    _access(client),
                    losing=[jane],
                    keeping=NEVER_EXISTED,
                    actor=Actor.sift("stash"),
                ),
            )
        finally:
            await database.close()

    assert asyncio.run(run()) == (None, None, None)


def test_the_tables_a_merge_moves_are_read_out_of_the_statements_that_move_them(
    client: TestClient,
) -> None:
    """A list of table names kept beside the statements naming them is a list that drifts the first
    time somebody adds a table and edits only one of the two."""
    from sift.slices.people.merge import tables_that_move

    moved = tables_that_move()

    assert "asset_people" in moved
    assert "usernames" in moved
    assert all(name and not name.isspace() for name in moved)


def _still_there(client: TestClient, person_id: str) -> bool:
    """Whether the row survives, read past the access layer.

    Past it deliberately: the vault is what makes every route answer 404 about this person, so a
    route is exactly the wrong instrument for asking whether they were deleted.
    """
    import asyncio

    from sift.kernel.db import Database

    path = db_path(client)

    async def run() -> bool:
        database = Database(path, readers=1)
        await database.connect()
        try:
            found = await database.fetch_one("SELECT id FROM people WHERE id = ?", (person_id,))
            return found is not None
        finally:
            await database.close()

    return asyncio.run(run())


def _access(client: TestClient) -> Repository:
    """The application's own permission layer, which a merge forgets grants through."""
    return client.app.state.access  # type: ignore[attr-defined,no-any-return]


def test_weighing_a_set_that_names_only_the_survivor_answers_with_nothing(
    client: TestClient,
) -> None:
    """Not a merge that failed: one that was never possible.

    The whole set is deduplicated and the survivor taken out of it, so naming them twice, or naming
    only them, leaves nothing to fold in. `None` rather than a Weighed of zeroes: a screen showing
    "0 files would move" is a confirm button for an act that would delete somebody.
    """
    import asyncio

    from sift.kernel.db import Database
    from sift.slices.people.merge import weigh_many

    sign_in(client)
    jane = make_person(client, "Jane")
    path = db_path(client)

    async def run() -> object:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return await weigh_many(database, losing=[jane, jane], keeping=jane)
        finally:
            await database.close()

    assert asyncio.run(run()) is None


def test_weighing_a_set_where_one_of_them_is_not_there_answers_with_nothing(
    client: TestClient,
) -> None:
    """One missing id is the whole set unanswerable, not a partial total. A number that quietly
    left one of the four out is a confirm screen promising less than the press would do."""
    import asyncio

    from sift.kernel.db import Database
    from sift.slices.people.merge import weigh_many

    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    path = db_path(client)

    async def run() -> object:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return await weigh_many(database, losing=[jane, NEVER_EXISTED], keeping=doe)
        finally:
            await database.close()

    assert asyncio.run(run()) is None


def test_a_set_that_cannot_be_finished_is_rolled_back_whole(client: TestClient) -> None:
    """The transaction boundary, and the reason the refusal RAISES rather than returning.

    Leaving the block by raising is what rolls it back; returning would commit what had been done,
    and half of four people folded into one is a library where two of them are gone, two are
    still there, and nothing anywhere says which state it is in or how to finish.

    Driven by making the last removal answer with nothing, which is what a row disappearing between
    the weighing and the delete would look like. It cannot happen through a route (one writer,
    one transaction, every id resolved a moment earlier), so the only way to watch the rollback is
    to arrange the impossible case on purpose.
    """
    import asyncio

    from sift.kernel.db import Database
    from sift.slices.people import merge as merge_module

    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    keeper = make_person(client, "The Survivor")
    path = db_path(client)
    real = merge_module._one_into
    seen: list[str] = []

    async def refuse_the_second(connection, *, losing: str, keeping: str, name: str):  # type: ignore[no-untyped-def]
        seen.append(losing)
        if len(seen) == 1:
            return await real(connection, losing=losing, keeping=keeping, name=name)
        return []

    async def run() -> object:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return await merge_module.merge_many(
                database,
                _access(client),
                losing=[jane, doe],
                keeping=keeper,
                actor=Actor.sift("stash"),
            )
        finally:
            await database.close()

    merge_module._one_into = refuse_the_second
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(run())
    finally:
        merge_module._one_into = real

    # Neither of them went. The first one really was folded in inside the transaction, and the
    # rollback is what puts it back, which is the whole claim.
    still_here = {
        str(one["id"]) for one in client.get("/api/people", params={"limit": 50}).json()["items"]
    }
    assert {jane, doe, keeper} <= still_here


# --- what the sheet says, BY NAME -----------------------------------------------------------------


def test_the_weigh_names_what_moves_and_whose_it_was(client: TestClient) -> None:
    """Counts alone leave the question somebody has in front of a press with no undo unanswered:
    WHICH username, on which Site, and WHAT field, filled with what. A sheet saying only "1
    username they post from moves too" and "1 field is filled in" says nothing more."""
    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    username = make_username(client, "Another Studio", "janedoe")
    write(
        db_path(client),
        [
            ("UPDATE usernames SET person_id = ? WHERE id = ?", (going, username)),
            (
                "INSERT INTO people_links (id, person_id, url, site_id, created_at)"
                " VALUES (?, ?, 'https://another.example/janedoe',"
                " (SELECT id FROM sites WHERE name = 'Another Studio'), 0)",
                (new_id(), going),
            ),
        ],
    )
    client.post(f"/api/people/{going}/aliases", json={"alias": "JD"})
    saved = client.put(
        f"/api/people/{going}", json={"name": "Jane", "record": {"birth_date": "1998-04-02"}}
    )
    assert saved.status_code == 200, saved.text

    weighed = _weigh(client, going, keeping)

    assert weighed["usernames_named"] == [
        {"name": "janedoe", "where": "Another Studio", "whose": "Jane"}
    ]
    assert weighed["aliases_named"] == [{"name": "JD", "where": None, "whose": "Jane"}]
    assert weighed["links_named"] == [{"name": "Another Studio", "where": None, "whose": "Jane"}]
    assert weighed["filled"] == [
        {"key": "birth_date", "label": "Birthdate", "value": "1998-04-02", "whose": "Jane"}
    ]
    assert weighed["facts"] == 1


def test_the_weigh_names_exactly_the_boxes_the_merge_then_fills(
    client: TestClient, library: Library
) -> None:
    """The fill is spelled twice (the statement that writes it and the list the weigh reads), and
    this holds the two together: every record column filled on the one going, and the weigh must
    name precisely the columns the merge then changed on the one kept. A column added to one and
    not the other is a fact that moves without the sheet saying so, or a line about nothing."""
    from sift.slices.people.merge import _RECORD_COLUMNS

    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    columns = [column for column, _ in _RECORD_COLUMNS]
    write(
        db_path(client),
        [
            (
                "UPDATE people SET disambiguation = 'x', gender = 'x', birth_date = '1990-01-01',"
                " country = 'US', ethnicity = 'x', eye_color = 'x', hair_color = 'x',"
                " height_cm = 170, measurements = 'x', breast_type = 'x',"
                " career_start_year = 2010, career_end_year = 2020, tattoos = '[\"rose\"]',"
                " piercings = '[\"ring\", \"stud\"]', notes = 'x', cover_asset_id = ? WHERE id = ?",
                (library.shared, going),
            )
        ],
    )
    wanted = ", ".join([*columns, "cover_asset_id"])
    before = read(db_path(client), f"SELECT {wanted} FROM people WHERE id = ?", (keeping,))[0]  # noqa: S608 (column names from the module's own literal list)

    weighed = _weigh(client, going, keeping)
    assert _merge(client, going, keeping).status_code == 200

    after = read(db_path(client), f"SELECT {wanted} FROM people WHERE id = ?", (keeping,))[0]  # noqa: S608 (as above)
    changed = {column for column in before if before[column] != after[column]}
    key_of = {key: column for column, key in _RECORD_COLUMNS} | {"cover": "cover_asset_id"}
    named = {key_of[str(one["key"])] for one in weighed["filled"]}  # type: ignore[attr-defined]
    assert named == changed
    assert weighed["facts"] == len(changed)
    height = next(one for one in weighed["filled"] if one["key"] == "height_cm")  # type: ignore[attr-defined]
    assert height["value"] == "170 cm"
    # A list is said as a list, not as the JSON text it is stored as.
    pierced = next(one for one in weighed["filled"] if one["key"] == "piercings")  # type: ignore[attr-defined]
    assert pierced["value"] == "ring, stud"


def test_where_two_going_know_the_same_thing_the_first_named_fills_it_once(
    client: TestClient,
) -> None:
    """The fill goes left to right and leaves a box alone once it holds something, so of two people
    going who both know a birthdate the FIRST one named fills it and the second is never read. The
    weigh must not add each pair's gain up separately and count that one box twice."""
    sign_in(client)
    keeping = make_person(client, "Jane Doe")
    first = make_person(client, "Jane")
    second = make_person(client, "J. Doe")
    for one, name, when in ((first, "Jane", "1990-01-01"), (second, "J. Doe", "1991-02-02")):
        client.put(f"/api/people/{one}", json={"name": name, "record": {"birth_date": when}})

    answer = client.post(
        "/api/people/weigh-merge", json={"people": [first, second], "into": keeping}
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["facts"] == 1
    assert answer.json()["filled"] == [
        {"key": "birth_date", "label": "Birthdate", "value": "1990-01-01", "whose": "Jane"}
    ]


# --- what a merge TELLS, and what its record keeps ------------------------------------------------


def test_every_table_naming_a_person_is_one_this_merge_touches(client: TestClient) -> None:
    """Read from the SCHEMA rather than from a list somebody maintains: the check the header of
    `merge.py` names. A table pointing at a person and named nowhere in the list
    cascades away with whoever went."""
    from sift.slices.people.merge import tables_that_move

    naming_a_person = {
        str(row["name"])
        for row in read(
            db_path(client),
            "SELECT m.name AS name FROM sqlite_master m"
            " WHERE m.type = 'table' AND m.sql LIKE '%REFERENCES people%'",
        )
    }

    assert len(naming_a_person) >= 10, "the schema walk found too few tables, so it proves nothing"
    assert naming_a_person <= tables_that_move(), (
        f"these point at a person and the merge does not touch them: "
        f"{sorted(naming_a_person - tables_that_move())}"
    )


def test_a_merge_forgets_that_either_ones_starter_pictures_were_refused(
    client: TestClient,
) -> None:
    """The note keeps a person out of the starter count because every stash-box picture OF THEM was
    refused. The survivor now holds both people's box records, so neither note is about the person
    who is left. Moved onto the survivor, the one going would keep their own untried pictures out
    of the count."""
    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_starter_refusals (person_id, recognizer, pictures, refused_at)"
                " VALUES (?, 'test-recognizer', 2, 0)",
                (one,),
            )
            for one in (going, keeping)
        ],
    )

    assert _merge(client, going, keeping).status_code == 200

    assert read(db_path(client), "SELECT person_id FROM face_starter_refusals") == []


def test_a_merge_carries_the_group_proposals_to_the_survivor(
    client: TestClient, library: Library
) -> None:
    """A folder's proposal that a face group is the person going becomes the survivor's, so the
    may-be card is still asked; one already made for the survivor on the same group wins."""
    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    moved, shared = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
                " VALUES (?, 'open', x'00', 1, 0, 0)",
                (pile,),
            )
            for pile in (moved, shared)
        ]
        + [
            (
                "INSERT INTO face_pile_proposals (pile_id, person_id, reason, folder_id, files,"
                " of_files, state, created_at, updated_at)"
                " VALUES (?, ?, 'folder', ?, 1, 1, 'pending', 0, 0)",
                (pile, person, library.folder),
            )
            for pile, person in ((moved, going), (shared, going), (shared, keeping))
        ],
    )

    assert _merge(client, going, keeping).status_code == 200

    left = read(
        db_path(client),
        "SELECT pile_id, person_id FROM face_pile_proposals ORDER BY pile_id, person_id",
    )
    assert left == [{"pile_id": pile, "person_id": keeping} for pile in sorted((moved, shared))]


def test_a_merge_carries_a_folder_taken_back_to_the_survivor(
    client: TestClient, library: Library
) -> None:
    """A folder taken back from the person going stays taken back from the survivor, and one taken
    back from both is kept once: left behind, the refusal would cascade away with them and the
    next folder pass would file the folder under the survivor again."""
    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    only_theirs = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, ?, 'clips/older', 'older')",
                (only_theirs, library.root, library.folder),
            )
        ]
        + [
            (
                "INSERT INTO folder_refusals (folder_id, person_id, created_at) VALUES (?, ?, 0)",
                (folder, person),
            )
            for folder, person in (
                (only_theirs, going),
                (library.folder, going),
                (library.folder, keeping),
            )
        ],
    )

    assert _merge(client, going, keeping).status_code == 200

    left = read(
        db_path(client),
        "SELECT folder_id, person_id FROM folder_refusals ORDER BY folder_id",
    )
    assert left == [
        {"folder_id": folder, "person_id": keeping}
        for folder in sorted((only_theirs, library.folder))
    ]


def test_a_merge_carries_the_remembered_refusals_to_the_survivor(
    client: TestClient, library: Library
) -> None:
    """A face refused as the person going stays refused as the survivor: left behind, the
    refusal would cascade away with them and the same face would be offered again."""
    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_rejected (id, asset_id, person_id, embedding, recognizer,"
                " created_at) VALUES (?, ?, ?, x'00', 'test-recognizer', 0)",
                (new_id(), library.shared, going),
            )
        ],
    )

    assert _merge(client, going, keeping).status_code == 200

    assert read(db_path(client), "SELECT person_id FROM face_rejected") == [{"person_id": keeping}]


def test_a_merge_moves_the_shoots_proposed_under_the_one_going(
    client: TestClient, library: Library
) -> None:
    """A shoot proposed under the person going is proposed under the survivor: its own row, so
    nothing collides; left out, it would cascade away with them."""
    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    write(
        db_path(client),
        [
            (
                "INSERT INTO shoot_proposals (id, person_id, name, found_at) VALUES (?, ?, ?, 0)",
                (new_id(), going, "clips"),
            )
        ],
    )

    assert _merge(client, going, keeping).status_code == 200

    assert read(db_path(client), "SELECT person_id FROM shoot_proposals") == [
        {"person_id": keeping}
    ]


def test_a_merge_tells_every_screen_once_it_has_landed(client: TestClient) -> None:
    """THE MERGED PERSON MUST NOT STAY ON THE WALL UNTIL THE PAGE IS RELOADED.

    The announcement from forgetting the grants of the person going commits BEFORE the merge's
    transaction opens, so a screen re-reading on it alone could draw the person as still there,
    and never be told again. The property is about timing, so it is measured at the moment of
    telling: some announcement that the library moved has to be delivered when the person going is
    already gone.
    """
    import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (the bus is told at a moment only the driver can observe)

    from sift.kernel import changes

    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    path = db_path(client)
    heard: list[tuple[str, bool]] = []

    def still_there() -> bool:
        with sqlite3.connect(path) as looking:
            return (
                looking.execute("SELECT 1 FROM people WHERE id = ?", (jane,)).fetchone() is not None
            )

    class Listening(changes.ChangeBus):
        def publish(self, audience, about, *, picture=False):  # type: ignore[no-untyped-def]
            heard.append((str(about), still_there()))
            super().publish(audience, about)

    was = changes._LISTENER
    changes.listens(Listening())
    try:
        assert _merge(client, jane, doe).status_code == 200
    finally:
        changes.listens(was)

    assert ("library", False) in heard, heard


def _ledger(client: TestClient, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    write(db_path(client), statements)


def test_the_record_follows_the_person_going_with_the_name_they_had(
    client: TestClient, library: Library
) -> None:
    """An event outlives its subject and names it by a SNAPSHOT. A merge is not a deletion: the
    person going turned out to be the survivor, so the ID follows them and the NAME stays what it
    was. Left behind, every event about them would be unreachable (their page answers 404), and a
    file whose History named them would draw the name with nowhere to go. A row that never wrote a
    name down is given the one they had, never the survivor's, which is what reading it live would
    say."""
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    about, named, nameless = new_id(), new_id(), new_id()
    _ledger(
        client,
        [
            # An act ABOUT the person going, with a snapshot and one without.
            (
                "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at,"
                " verb, actor_kind) VALUES (?, 'ledger', '', '', '', 1, 'edited', 'sift')",
                (about,),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
                " VALUES (?, 'person', ?, 'Jane')",
                (about, jane),
            ),
            (
                "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at,"
                " verb, actor_kind) VALUES (?, 'ledger', '', '', '', 2, 'edited', 'sift')",
                (nameless,),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
                " VALUES (?, 'person', ?, NULL)",
                (nameless, jane),
            ),
            # An act on a FILE done WITH them: the file's History names them.
            (
                "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at,"
                " verb, actor_kind, object_kind, object_id, object_name)"
                " VALUES (?, 'ledger', '', '', '', 3, 'linked', 'sift', 'person', ?, NULL)",
                (named, jane),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
                " VALUES (?, 'asset', ?, NULL)",
                (named, library.shared),
            ),
            # What a stash-box was asked about them, and what it could not decide.
            (
                "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic)"
                " VALUES (?, 'person', ?, 'some-box', 5, 0)",
                (new_id(), jane),
            ),
            (
                "INSERT INTO stash_box_undecided (subject, local_id, candidates, seen_at)"
                " VALUES ('person', ?, 2, 5)",
                (jane,),
            ),
        ],
    )

    assert _merge(client, jane, doe).status_code == 200

    path = db_path(client)
    subjects = read(
        path,
        "SELECT decision_id, subject_id, name FROM workbench_decision_subjects"
        " WHERE kind = 'person' AND decision_id IN (?, ?)",
        (about, nameless),
    )
    assert {(one["subject_id"], one["name"]) for one in subjects} == {(doe, "Jane")}
    assert len(subjects) == 2
    (done_with,) = read(
        path, "SELECT object_id, object_name FROM workbench_decisions WHERE id = ?", (named,)
    )
    assert done_with == {"object_id": doe, "object_name": "Jane"}
    assert read(path, "SELECT local_id FROM enrichment_runs WHERE box_id = 'some-box'") == [
        {"local_id": doe}
    ]
    assert read(path, "SELECT local_id FROM stash_box_undecided WHERE subject = 'person'") == [
        {"local_id": doe}
    ]


def test_the_merge_event_says_what_came_over(client: TestClient, library: Library) -> None:
    """The confirm sheet counted what would move and was gone once the button was pressed; nothing
    else wrote those numbers down. So the `merged` event carries them, per person going."""
    import json

    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane])
    client.put(f"/api/people/{jane}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}})

    assert _merge(client, jane, doe).status_code == 200

    (event,) = read(
        db_path(client),
        "SELECT d.payload AS payload, d.object_id AS object_id FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'merged' AND s.kind = 'person' AND s.subject_id = ?",
        (jane,),
    )
    assert event["object_id"] == doe
    brought = json.loads(str(event["payload"]))
    assert brought["files"] == 1
    assert brought["filled"] == ["birth_date"]


def test_the_survivors_page_says_what_came_over_and_whose_acts_they_were(
    client: TestClient, library: Library
) -> None:
    """THE SURVIVOR'S HISTORY, read over HTTP after a merge.

    "Jane was merged into them" says what came over as well as that it happened, folded the way
    an edit and a box's fill are. And every act about Jane is re-pointed at the survivor with
    Jane's name kept, so "Edited the birthdate" on Jane Doe's page says whose it was rather than
    reading as Jane Doe's own act.
    """
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane])
    client.put(f"/api/people/{jane}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}})

    assert _merge(client, jane, doe).status_code == 200
    events = client.get(f"/api/people/{doe}/history").json()

    (merged,) = [one for one in events if one["what"] == "You merged Jane into them"]
    (fold,) = merged["detail"]
    assert fold["words"] == "Brought over"
    assert [one["name"] for one in fold["entries"]] == ["1 file", "birthdate filled in"]
    theirs = [one["what"] for one in events if one["what"].endswith(", when they were called Jane")]
    assert theirs, [one["what"] for one in events]
    # The survivor's own acts are never said as somebody else's.
    assert not [one for one in events if one["what"].endswith(", when they were called Jane Doe")]


def test_what_came_over_is_named_five_and_then_counted(
    client: TestClient, library: Library
) -> None:
    """ "Brought over 7 other names" alone says how many and never which, and once the rows are on
    the survivor nothing tells hers from Jane's. So the merge writes the names down as it moves
    them, and the fold names them: five, then "and N more". A tag they both carry did not come
    over."""
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    username = make_username(client, "Another Studio", "janedoe")
    seaside, poolside = new_id(), new_id()
    write(
        db_path(client),
        [
            ("UPDATE usernames SET person_id = ? WHERE id = ?", (jane, username)),
            (
                "INSERT INTO people_links (id, person_id, url, site_id, created_at)"
                " VALUES (?, ?, 'https://another.example/janedoe',"
                " (SELECT id FROM sites WHERE name = 'Another Studio'), 0)",
                (new_id(), jane),
            ),
            (
                "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, 'seaside', 'seaside', 0),"
                " (?, 'poolside', 'poolside', 0)",
                (seaside, poolside),
            ),
            (
                "INSERT INTO person_tags (person_id, tag_id) VALUES (?, ?), (?, ?), (?, ?)",
                (jane, seaside, jane, poolside, doe, poolside),
            ),
        ],
    )
    for alias in ("Wren", "Ash", "Bay", "Cove", "Dune", "Elm", "Fern"):
        client.post(f"/api/people/{jane}/aliases", json={"alias": alias})

    assert _merge(client, jane, doe).status_code == 200
    events = client.get(f"/api/people/{doe}/history").json()

    (merged,) = [one for one in events if one["what"] == "You merged Jane into them"]
    (fold,) = merged["detail"]
    assert [one["name"] for one in fold["entries"]] == [
        "1 username (janedoe on Another Studio)",
        "7 other names (Ash, Bay, Cove, Dune, Elm and 2 more)",
        "1 link (Another Studio)",
        "1 tag (seaside)",
    ]


def test_the_blanks_and_what_each_brings_answer_nothing_for_somebody_who_is_not_there(
    client: TestClient,
) -> None:
    """The two reads a merge makes before it writes, asked about an id that names nobody: no
    answer rather than an empty one, which a sheet would draw as "nothing is filled in"."""
    import asyncio

    from sift.kernel.db import Database
    from sift.slices.people.merge import _filled, _what_each_brings

    sign_in(client)
    jane = make_person(client, "Jane")
    path = db_path(client)

    async def run() -> tuple[object, object, object]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return (
                await _filled(database, going=[jane], keeping=NEVER_EXISTED),
                await _filled(database, going=[NEVER_EXISTED], keeping=jane),
                await _what_each_brings(database, going=[NEVER_EXISTED], keeping=jane),
            )
        finally:
            await database.close()

    assert asyncio.run(run()) == (None, None, None)


def test_the_cover_is_named_as_coming_over_only_onto_somebody_who_has_none(
    client: TestClient, library: Library
) -> None:
    """The cover moves as one thing onto a survivor with none. Onto one that has their own it moves
    nowhere, and the sheet does not say it does."""
    sign_in(client)
    going = make_person(client, "Jane")
    keeping = make_person(client, "Jane Doe")
    write(
        db_path(client),
        [("UPDATE people SET cover_asset_id = ? WHERE id = ?", (library.shared, going))],
    )

    onto_none = _weigh(client, going, keeping)
    write(
        db_path(client),
        [("UPDATE people SET cover_asset_id = ? WHERE id = ?", (library.private, keeping))],
    )
    onto_their_own = _weigh(client, going, keeping)

    assert [one["key"] for one in onto_none["filled"]] == ["cover"]  # type: ignore[attr-defined]
    assert onto_their_own["filled"] == []


@pytest.mark.parametrize(
    ("stored", "said"),
    [
        ('["rose, left wrist", "star"]', "rose, left wrist, star"),
        ("rose on the left wrist", "rose on the left wrist"),
        ('{"not": "a list"}', '{"not": "a list"}'),
    ],
)
def test_a_list_of_names_is_said_as_a_list_and_anything_else_as_stored(
    stored: str, said: str
) -> None:
    """A list of names is stored as JSON text; the sheet says the names. A value from before the
    field held a list is said exactly as it was written rather than dropped."""
    from sift.kernel.records import Kind as RecordKind
    from sift.slices.people.merge import fill_value

    assert fill_value(RecordKind.NAMES, stored) == said


def test_a_value_too_long_for_its_line_is_cut_with_an_ellipsis() -> None:
    from sift.slices.people.merge import _VALUE_AT_MOST, fill_value

    said = fill_value(None, "word " * 100)

    assert len(said) <= _VALUE_AT_MOST
    assert said.endswith("\u2026")
    assert fill_value(None, "short") == "short"


def test_a_download_kept_for_the_person_going_follows_them(client: TestClient) -> None:
    """A download keeps the person it was for by id. Left behind by a merge, the key would empty
    the row (`ON DELETE SET NULL`) and the download would stop saying whose it was."""
    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    write(
        db_path(client),
        [
            (
                "INSERT INTO downloads (id, url, url_hash, state, person_id, created_at)"
                " VALUES ('d-merge', 'https://example.test/a', 'h-merge', 'done', ?, 0)",
                (jane,),
            )
        ],
    )

    assert _merge(client, jane, doe).status_code == 200

    (row,) = read(db_path(client), "SELECT person_id FROM downloads WHERE id = 'd-merge'")
    assert row["person_id"] == doe


def test_a_cover_nobody_chose_is_not_carried_and_the_survivor_gets_its_own(
    client: TestClient, library: Library
) -> None:
    """The rule's pick for the person going (their first file's picture) is not a choice. Carried,
    it would arrive as a chosen cover the rule may never replace, and the merge would say "cover
    picture filled in" about a choice nobody made. The survivor is given its own by the rule, from
    the files it holds afterwards, and it is still the rule's."""
    import json

    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane])
    (before,) = read(
        db_path(client), "SELECT cover_asset_id, cover_by_default FROM people WHERE id = ?", (jane,)
    )
    assert before["cover_asset_id"] == before["cover_by_default"] == library.shared

    assert _merge(client, jane, doe).status_code == 200

    (after,) = read(
        db_path(client), "SELECT cover_asset_id, cover_by_default FROM people WHERE id = ?", (doe,)
    )
    assert after["cover_asset_id"] == library.shared
    assert after["cover_by_default"] == library.shared, "it arrived as a chosen cover"
    (event,) = read(
        db_path(client),
        "SELECT d.payload AS payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'merged' AND s.kind = 'person' AND s.subject_id = ?",
        (jane,),
    )
    assert "cover" not in json.loads(str(event["payload"]))["filled"]


def test_a_cover_somebody_chose_is_carried_and_said(client: TestClient, library: Library) -> None:
    """The other half: a picture the person going was GIVEN by somebody comes over as theirs, and
    the merge says so."""
    import json

    sign_in(client)
    jane = make_person(client, "Jane")
    doe = make_person(client, "Jane Doe")
    assign(client, [library.shared], [jane])
    # Chosen: the cover stands and the rule's mark is gone, which is what choosing writes.
    write(
        db_path(client),
        [("UPDATE people SET cover_by_default = NULL WHERE id = ?", (jane,))],
    )

    assert _merge(client, jane, doe).status_code == 200

    (after,) = read(
        db_path(client), "SELECT cover_asset_id, cover_by_default FROM people WHERE id = ?", (doe,)
    )
    assert after["cover_asset_id"] == library.shared
    assert after["cover_by_default"] is None
    (event,) = read(
        db_path(client),
        "SELECT d.payload AS payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'merged' AND s.kind = 'person' AND s.subject_id = ?",
        (jane,),
    )
    assert "cover" in json.loads(str(event["payload"]))["filled"]
