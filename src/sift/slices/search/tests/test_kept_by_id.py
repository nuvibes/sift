# SPDX-License-Identifier: AGPL-3.0-or-later
"""A saved filter is kept by id, reads back under today's names, and survives a rename.

Kept by name, a tag renamed from one name to another would leave every saved filter asking for the
old name, which names nothing, so the filter would match no file and its chip would go on reading
the old name. Each test here renames or deletes the thing a filter names and asks the two
questions: what the filter reads as, and what it finds.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import parse_qs

from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.search.filters import Field
from sift.slices.search.schema import initialize
from sift.slices.search.stored import entity_values, swapped
from sift.slices.search.tests.conftest import (
    World,
    db_path,
    found,
    put_song,
    read,
    sign_in,
    write,
)


def _kept(client: TestClient) -> dict[str, object]:
    items = client.get("/api/search/saved").json()["items"]
    assert len(items) == 1
    return dict(items[0])


def _stored(client: TestClient) -> str:
    return str(read(db_path(client), "SELECT query FROM saved_searches")[0]["query"])


def test_every_entity_field_takes_an_id(client: TestClient, world: World) -> None:
    """The id form finds exactly what the name form finds, for all six things a filter names."""
    sign_in(client, "admin")
    for field, key in (
        ("tags", world.tag_beach),
        ("people", world.person),
        ("sites", world.site),
        ("collections", world.collection),
        ("photo_sets", world.photo_set),
        ("in", world.holiday),
    ):
        assert found(client, **{field: key})[0] == [world.beach], field


def test_a_saved_filter_is_stored_by_id_and_read_back_by_name(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    query = "tags=beach&people=Jane+Doe&platforms=TikTok&collections=Best+of&in=holiday&media=video"
    assert (
        client.post("/api/search/saved", json={"name": "Kept", "query": query}).status_code == 204
    )

    stored = parse_qs(_stored(client))
    assert stored["tags"] == [world.tag_beach]
    assert stored["people"] == [world.person]
    assert stored["platforms"] == [world.site]
    assert stored["collections"] == [world.collection]
    assert stored["in"] == [world.holiday]
    assert stored["media"] == ["video"]

    kept = _kept(client)
    assert parse_qs(str(kept["query"])) == parse_qs(query)
    assert kept["named"] == []


def test_a_rename_is_read_back_under_the_new_name_and_still_matches(
    client: TestClient, world: World
) -> None:
    """Renamed after it was kept, the filter says and finds the new name."""
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Playlist", "query": "tags=beach&in=holiday"})
    write(
        db_path(client), [("UPDATE tags SET name = ? WHERE id = ?", ("seaside", world.tag_beach))]
    )

    kept = _kept(client)
    assert parse_qs(str(kept["query"]))["tags"] == ["seaside"]
    assert found(client, **{key: value[0] for key, value in parse_qs(str(kept["query"])).items()})[
        0
    ] == [world.beach]


def test_a_thing_deleted_since_reads_as_gone_and_matches_nothing(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Gone", "query": "tags=-city|beach"})
    write(db_path(client), [("DELETE FROM tags WHERE id = ?", (world.tag_beach,))])

    kept = _kept(client)
    # The id stays where it was, and the note says nothing answers to it any more.
    assert world.tag_beach in str(kept["query"])
    assert kept["named"] == [{"field": "tags", "value": world.tag_beach, "name": None}]
    assert found(client, tags=world.tag_beach) == ([], 0)


def test_a_name_that_names_nothing_is_kept_and_noted(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Old", "query": "tags=nobody-has-this"})

    assert _stored(client) == "tags=nobody-has-this"
    assert _kept(client)["named"] == [{"field": "tags", "value": "nobody-has-this", "name": None}]


def test_a_shared_name_is_kept_as_the_name(client: TestClient, world: World) -> None:
    """Two people called the same thing: the filter meant both, so it is not narrowed to one."""
    sign_in(client, "admin")
    twin = new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (twin, "Jane Doe")),
            ("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.walk, twin)),
        ],
    )
    client.post("/api/search/saved", json={"name": "Both", "query": "people=Jane+Doe"})

    assert parse_qs(_stored(client))["people"] == ["Jane Doe"]
    assert _kept(client)["named"] == []


def test_typed_filters_in_q_are_kept_by_id_too(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    client.post(
        "/api/search/saved", json={"name": "Typed", "query": "q=tags%3Abeach+sunset&rating=4"}
    )

    assert parse_qs(_stored(client))["q"] == [f"tags:{world.tag_beach} sunset"]
    assert parse_qs(str(_kept(client)["query"]))["q"] == ["tags:beach sunset"]


def test_a_filter_kept_on_another_wall_is_left_as_it_was(client: TestClient, world: World) -> None:
    """Those walls write ids already; their words are not the query language's."""
    sign_in(client, "admin")
    client.post(
        "/api/search/saved", json={"name": "People", "query": "tags=beach", "kind": "person"}
    )

    assert _stored(client) == "tags=beach"


def test_the_version_eight_step_rewrites_kept_filters_from_names_to_ids(
    client: TestClient, world: World
) -> None:
    """Filters kept before the step: one by a name one thing has, one by a name nothing has."""
    admin = sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
                " VALUES (?, ?, 'asset', ?, ?, 0)",
                (new_id(), admin, name, query),
            )
            for name, query in (
                ("Named", "from=X&tags=Beach|city&people=JD&in=clips/holiday&q=sites%3ATikTok"),
                ("Renamed away", "tags=gone-already&media=video"),
            )
        ],
    )

    async def step() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                await initialize(connection, 7)
        finally:
            await database.close()

    asyncio.run(step())
    rows = {
        str(row["name"]): parse_qs(str(row["query"]))
        for row in read(db_path(client), "SELECT name, query FROM saved_searches")
    }
    assert rows["Named"]["tags"] == [f"{world.tag_beach}|{world.tag_city}"]
    assert rows["Named"]["people"] == [world.person]
    assert rows["Named"]["in"] == [world.holiday]
    assert rows["Named"]["q"] == [f"sites:{world.site}"]
    assert rows["Named"]["from"] == ["X"]
    assert rows["Renamed away"]["tags"] == ["gone-already"]


def test_swapping_leaves_a_filter_with_nothing_to_swap_exactly_as_written() -> None:
    written = "tags=a%20b&q=words+only"
    assert swapped(written, {Field.TAGS: {}}) == written
    assert swapped("q=just+words", {Field.TAGS: {"x": "y"}}) == "q=just+words"
    assert entity_values("tags=none&people=-any|Ann") == {Field.PEOPLE: {"Ann": {"people"}}}
    # A quoted value of nothing but spaces names no tag.
    assert entity_values("q=tags%3A%22++%22") == {}
    assert swapped('tags=-"a,b"|c', {Field.TAGS: {"a,b": "ID"}}) == "tags=-ID%7Cc"


def test_a_value_swapped_back_to_a_name_holding_a_separator_is_quoted() -> None:
    assert swapped("tags=ID", {Field.TAGS: {"ID": "a|b"}}) == "tags=%22a%7Cb%22"
    # A name beginning with a minus is quoted, or the parameter would read as refusing it.
    assert swapped("tags=ID", {Field.TAGS: {"ID": "-raw"}}) == "tags=%22-raw%22"
    assert swapped("tags=-ID", {Field.TAGS: {"ID": "-raw"}}) == "tags=-%22-raw%22"


# --- the box's memory of what was picked ---------------------------------------------------------


def _pick(client: TestClient, kind: str, subject: str, label: str) -> None:
    answer = client.post(
        "/api/search/history", json={"kind": kind, "subject": subject, "label": label}
    )
    assert answer.status_code == 204, answer.text


def _recent(client: TestClient, typed: str = "") -> list[tuple[str, str, str]]:
    rows = client.get("/api/search/suggest", params={"q": typed}).json()["recent"]
    return [(row["kind"], row["subject"], row["label"]) for row in rows]


def test_a_tag_picked_by_name_comes_back_under_its_new_name(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    _pick(client, "tags", "beach", "beach")
    stored = read(db_path(client), "SELECT subject FROM search_history")
    assert stored == [{"subject": world.tag_beach}]

    write(
        db_path(client), [("UPDATE tags SET name = ? WHERE id = ?", ("seaside", world.tag_beach))]
    )
    assert _recent(client) == [("tags", "seaside", "seaside")]
    assert _recent(client, "sea") == [("tags", "seaside", "seaside")]
    assert _recent(client, "bea") == []


def test_a_person_picked_by_id_reads_live_and_leaves_once_gone(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    _pick(client, "people", world.person, "Jane Doe")
    write(
        db_path(client), [("UPDATE people SET name = ? WHERE id = ?", ("Jane Roe", world.person))]
    )
    assert _recent(client) == [("people", world.person, "Jane Roe")]

    write(db_path(client), [("DELETE FROM people WHERE id = ?", (world.person,))])
    assert _recent(client) == []


def test_the_step_keeps_a_picked_folder_by_id(client: TestClient, world: World) -> None:
    admin = sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO search_history (id, user_id, kind, subject, label, created_at)"
                " VALUES (?, ?, ?, ?, ?, 0)",
                (new_id(), admin, kind, subject, subject),
            )
            for kind, subject in (("in", "holiday"), ("tags", "nobody-has-this"))
        ],
    )

    async def step() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                await initialize(connection, 7)
        finally:
            await database.close()

    asyncio.run(step())
    subjects = {
        str(row["kind"]): str(row["subject"])
        for row in read(db_path(client), "SELECT kind, subject FROM search_history")
    }
    assert subjects == {"in": world.holiday, "tags": "nobody-has-this"}


def test_the_step_follows_a_name_the_record_says_was_renamed_away(
    client: TestClient, world: World
) -> None:
    """A tag renamed BEFORE filters were kept by id: its old name names
    nothing; the record of the rename says which tag had it, and the filter follows it."""
    admin = sign_in(client, "admin")
    decision = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
                " VALUES (?, ?, 'asset', 'Playlist', 'tags=Town&in=holiday', 0)",
                (new_id(), admin),
            ),
            (
                "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
                " decided_at, verb) VALUES (?, 'ledger', ?, '', '', ?, 1, 'renamed')",
                (decision, admin, '{"before": "town"}'),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
                " VALUES (?, 'tag', ?, 'city')",
                (decision, world.tag_city),
            ),
        ],
    )

    async def step() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                await initialize(connection, 7)
        finally:
            await database.close()

    asyncio.run(step())
    assert parse_qs(_stored(client)) == {"tags": [world.tag_city], "in": [world.holiday]}
    assert parse_qs(str(_kept(client)["query"])) == {"tags": ["city"], "in": ["holiday"]}


def test_the_step_follows_a_name_the_record_says_was_merged_away(
    client: TestClient, world: World
) -> None:
    """A Site merged into another before filters were kept by id: its name names nothing, and the
    record of the merge says which Site took it in, so the filter follows the survivor."""
    admin = sign_in(client, "admin")
    decision = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
                " VALUES (?, ?, 'asset', 'Playlist', 'tags=gone+town', 0)",
                (new_id(), admin),
            ),
            (
                "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
                " decided_at, verb, object_kind, object_id) VALUES"
                " (?, 'ledger', ?, '', '', '', 1, 'merged', 'tag', ?)",
                (decision, admin, world.tag_city),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
                " VALUES (?, 'tag', ?, 'gone town')",
                (decision, new_id()),
            ),
        ],
    )

    async def step() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                await initialize(connection, 7)
        finally:
            await database.close()

    asyncio.run(step())
    assert parse_qs(_stored(client)) == {"tags": [world.tag_city]}
    assert parse_qs(str(_kept(client)["query"])) == {"tags": ["city"]}


def _step(client: TestClient, on_disk: int) -> None:
    async def run() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                await initialize(connection, on_disk)
        finally:
            await database.close()

    asyncio.run(run())


def _cells(client: TestClient) -> list[str]:
    rows = read(db_path(client), "SELECT source FROM theater_cells ORDER BY position")
    return [str(row["source"]) for row in rows]


def test_the_version_nine_step_keeps_each_theater_cell_by_id(
    client: TestClient, world: World
) -> None:
    """A saved Theater wall keeps each cell as typed text. The step rewrites a name that names one
    thing, through a negation, an either-or and a presence alike; a name naming nothing, and an id
    already kept, are left as they were written."""
    admin = sign_in(client, "admin")
    wall = new_id()
    sources = (
        'tags:beach -people:"Jane Doe"',
        "tags:city or sites:TikTok -collections",
        "tags:Harbourside",
        f"tags:{world.tag_city}",
    )
    write(
        db_path(client),
        [
            (
                "INSERT INTO theater_arrangements (id, user_id, name, layout, created_at,"
                " updated_at) VALUES (?, ?, 'Wall', 'grid', 0, 0)",
                (wall, admin),
            ),
            *(
                (
                    "INSERT INTO theater_cells (arrangement_id, position, source, media_kind,"
                    " ordering, end_behaviour, volume) VALUES (?, ?, ?, 'all', 'random', 'next', 0)",
                    (wall, position, source),
                )
                for position, source in enumerate(sources)
            ),
        ],
    )

    _step(client, 8)

    assert _cells(client) == [
        f"-people:{world.person} tags:{world.tag_beach}",
        f"-collections sites:{world.site} OR tags:{world.tag_city}",
        "tags:Harbourside",
        f"tags:{world.tag_city}",
    ]
    # Twice is once.
    _step(client, 8)
    assert _cells(client)[0] == f"-people:{world.person} tags:{world.tag_beach}"


def test_the_record_of_renames_is_followed_only_where_it_says_something_of_a_thing_still_here(
    client: TestClient, world: World
) -> None:
    """A rename whose record cannot be read, says no name, or is about a thing gone or a kind the
    kept filters do not name is passed over; so is a merge into a thing no longer here."""
    admin = sign_in(client, "admin")
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
            " VALUES (?, ?, 'asset', 'Kept', 'tags=Harbour&in=holiday', 0)",
            (new_id(), admin),
        ),
        # A library's own top folder has no name and no path, and names nothing a filter says.
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
            " SELECT ?, root_id, NULL, '', '' FROM folders WHERE id = ?",
            (new_id(), world.clips),
        ),
    ]
    for kind, subject, payload in (
        ("tag", world.tag_city, "{not json"),
        ("tag", world.tag_city, '{"before": "  "}'),
        ("tag", new_id(), '{"before": "harbour"}'),
        ("person", world.person, '{"before": "harbour"}'),
    ):
        decision = new_id()
        statements += [
            (
                "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
                " decided_at, verb) VALUES (?, 'ledger', ?, '', '', ?, 1, 'renamed')",
                (decision, admin, payload),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
                " VALUES (?, ?, ?, 'x')",
                (decision, kind, subject),
            ),
        ]
    merged = new_id()
    statements += [
        (
            "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
            " decided_at, verb, object_kind, object_id) VALUES (?, 'ledger', ?, '', '', '', 1,"
            " 'merged', 'tag', ?)",
            (merged, admin, new_id()),
        ),
        (
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
            " VALUES (?, 'tag', ?, 'Harbour')",
            (merged, new_id()),
        ),
    ]
    write(db_path(client), statements)

    _step(client, 7)

    assert parse_qs(_stored(client)) == {"tags": ["Harbour"], "in": [world.holiday]}


def test_without_a_record_of_renames_nothing_is_followed(tmp_path: Path) -> None:
    from sift.slices.search.schema import _renamed_from

    async def run() -> dict[Field, dict[str, set[str]]]:
        database = Database(tmp_path / "bare.sqlite3", readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                return await _renamed_from(connection, {Field.TAGS: {"harbour": {"t1"}}})
        finally:
            await database.close()

    assert asyncio.run(run()) == {}


def test_a_song_is_found_by_its_name_and_by_its_id(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    song = put_song(db_path(client), world.beach, "Blue - Marla Quist")
    assert found(client, songs="Blue - Marla Quist")[0] == [world.beach]
    assert found(client, songs=song)[0] == [world.beach]
    assert found(client, songs="nothing called this")[0] == []


def test_typed_text_is_kept_by_id_and_read_back_under_todays_names(
    client: TestClient, world: World
) -> None:
    """The typed form a Theater cell keeps: each name that names one thing kept as its id, and
    read back under the name it has now, through a negation, an either-or and a presence."""
    from sift.kernel.access import Repository, Role
    from sift.kernel.config import get_settings
    from sift.kernel.content import ContentStore
    from sift.slices.search.filters import FilterCompiler
    from sift.testing.fixtures import create_user

    sign_in(client, "admin")
    song = put_song(db_path(client), world.beach, "Blue - Marla Quist")

    async def run() -> tuple[list[str], list[str], list[str], list[str]]:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            access = Repository(database, ContentStore(database, get_settings()))
            compiler = FilterCompiler(access)
            viewer = await create_user(database, Role.ADMIN)
            nothing = await compiler.kept(viewer, []) + await compiler.shown(viewer, [])
            # Kept by id already: nothing to look up.
            assert await compiler.kept(viewer, [f"people:{world.person}"]) == [
                f"people:{world.person}"
            ]
            kept = await compiler.kept(
                viewer, ['tags:beach -songs:"Blue - Marla Quist"', "tags:city or -people"]
            )
            await database.execute(
                "UPDATE tags SET name = 'shore' WHERE id = ?", (world.tag_beach,)
            )
            shown = await compiler.shown(viewer, kept)
            return nothing, kept, shown, await compiler.shown(viewer, ["tags:Harbourside"])
        finally:
            await database.close()

    nothing, kept, shown, unknown = asyncio.run(run())
    assert nothing == []
    assert kept == [f"tags:{world.tag_beach} -songs:{song}", f"tags:{world.tag_city} OR -people"]
    assert shown == ['tags:shore -songs:"Blue - Marla Quist"', "tags:city OR -people"]
    assert unknown == ["tags:Harbourside"]


def test_a_pick_by_a_name_that_names_nothing_is_kept_as_it_came(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    _pick(client, "tags", "Harbourside", "Harbourside")
    assert read(db_path(client), "SELECT subject FROM search_history") == [
        {"subject": "Harbourside"}
    ]


def test_the_dropdown_offers_a_song_by_any_word_of_its_name(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    song = put_song(db_path(client), world.beach, "Blue - Marla Quist")
    answer = client.get("/api/search/suggest", params={"q": "songs:marla"}).json()
    assert [(row["value"], row["id"], row["count"]) for row in answer["matches"]] == [
        ("Blue - Marla Quist", song, 1)
    ]


def test_the_version_eleven_step_puts_an_old_refusal_back_on_the_token(
    client: TestClient, world: World
) -> None:
    """A refused value once written inside the quotes is respelled where only its name names
    something; a name that begins with a minus, or one naming nothing, is left as written."""
    admin = sign_in(client, "admin")
    wall, kept, plain = new_id(), new_id(), new_id()
    sources = (
        'in:"-holiday" media:video',
        'tags:"-raw"',
        'tags:"-nowhere"',
        'tags:"-beach|city" people:"-Jane Doe,beach"',
    )
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?, '-raw', 0)", (new_id(),)),
            ("INSERT INTO tags (id, name, created_at) VALUES (?, 'raw', 0)", (new_id(),)),
            (
                "INSERT INTO theater_arrangements (id, user_id, name, layout, created_at,"
                " updated_at) VALUES (?, ?, 'Wall', 'grid', 0, 0)",
                (wall, admin),
            ),
            *(
                (
                    "INSERT INTO theater_cells (arrangement_id, position, source, media_kind,"
                    " ordering, end_behaviour, volume) VALUES (?, ?, ?, 'all', 'random', 'next', 0)",
                    (wall, position, source),
                )
                for position, source in enumerate(sources)
            ),
            (
                "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
                " VALUES (?, ?, 'asset', 'Kept', ?, 0)",
                (kept, admin, "q=people%3A%22-Jane+Doe%22&media=video"),
            ),
            (
                "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
                " VALUES (?, ?, 'asset', 'Plain', 'q=tags%3A%22-nowhere%22', 0)",
                (plain, admin),
            ),
        ],
    )

    _step(client, 10)

    assert _cells(client) == [
        "-in:holiday media:video",
        'tags:"-raw"',
        'tags:"-nowhere"',
        '-tags:beach|city people:"-Jane Doe,beach"',
    ]
    query = read(db_path(client), "SELECT query FROM saved_searches WHERE id = ?", (kept,))
    assert parse_qs(str(query[0]["query"])) == {"q": ['-people:"Jane Doe"'], "media": ["video"]}
    untouched = read(db_path(client), "SELECT query FROM saved_searches WHERE id = ?", (plain,))
    assert str(untouched[0]["query"]) == "q=tags%3A%22-nowhere%22", (
        "a name nothing has: left as it is"
    )
    # Twice is once.
    _step(client, 10)
    assert _cells(client)[0] == "-in:holiday media:video"
