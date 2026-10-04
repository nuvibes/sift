# SPDX-License-Identifier: AGPL-3.0-or-later
"""The People and Sites walls, filtered by what the row IS and counted along one dimension.

Over HTTP rather than against the access layer, because what is in question here is the half the
access layer's own tests cannot see: that the route declares the parameters, that FastAPI does not
discard them in silence, and that the answer is the shape the panel reads. The counting rules
themselves (who is counted, whose vault holds what) are put to the statement in the kernel's
own suite; what is asserted here is that the wire carries them.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import (
    Library,
    assign,
    db_path,
    make_person,
    make_username,
    sign_in,
    write,
)

_HAIR = "UPDATE people SET hair_color = ? WHERE id = ?"
_COUNTRY = "UPDATE people SET country = ? WHERE id = ?"


def _facets(client: TestClient, noun: str, facet: str, **params: str) -> list[dict[str, object]]:
    response = client.get(f"/api/{noun}/facets", params={"facet": facet, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["facet"] == facet
    return list(body["values"])


def test_the_people_wall_counts_people_and_not_their_files(
    client: TestClient, library: Library
) -> None:
    """A row on the People panel is a number of PEOPLE.

    One of the two blondes is on both files, so a count of files would say three and a count of
    people says two. That is the whole reason the counts come out of the wall's own statement: a
    number beside a row has to describe the wall the row is drawn beside.
    """
    sign_in(client)
    first = make_person(client, "Neve Arbor")
    second = make_person(client, "Wren Halloway")
    third = make_person(client, "Cass Ivory")
    write(
        db_path(client),
        [
            (_HAIR, ("BLONDE", first)),
            # The other spelling, on purpose: a stash-box sends upper case and a person typing into
            # the record does not, and two rows for one colour is a column that has stopped
            # describing the library.
            (_HAIR, ("blonde", second)),
            (_HAIR, ("RED", third)),
        ],
    )
    assign(client, [library.shared, library.private], [first])
    assign(client, [library.shared], [second, third])

    counted = _facets(client, "people", "hair_color")

    assert [(one["value"], one["count"]) for one in counted] == [("BLONDE", 2), ("RED", 1)]
    # A word is already readable. Only a value that is an id is sent with a name beside it.
    assert all(one["label"] is None for one in counted)


def test_a_person_the_vault_is_holding_is_not_counted(client: TestClient) -> None:
    """A concealed person is absent from the count, exactly as they are absent from the wall.

    A row saying two over a wall showing one has published that somebody is being kept back, which
    is most of what the vault is for keeping back.
    """
    sign_in(client)
    seen = make_person(client, "Neve Arbor")
    hidden = make_person(client, "Wren Halloway", vault=True)
    write(db_path(client), [(_HAIR, ("RED", seen)), (_HAIR, ("RED", hidden))])

    assert _facets(client, "people", "hair_color") == [{"value": "RED", "count": 1, "label": None}]


def test_the_wall_narrows_by_two_keys_and_by_a_repeated_one(client: TestClient) -> None:
    """Repeated key is OR within one facet; two keys are AND across them.

    Both halves in one test on purpose: each is only meaningful against the other, and a wall that
    ORed everything would pass a test of the OR alone.
    """
    sign_in(client)
    both = make_person(client, "Neve Arbor")
    other_hair = make_person(client, "Wren Halloway")
    other_country = make_person(client, "Cass Ivory")
    write(
        db_path(client),
        [
            (_HAIR, ("BLONDE", both)),
            (_COUNTRY, ("US", both)),
            (_HAIR, ("RED", other_hair)),
            (_COUNTRY, ("US", other_hair)),
            (_HAIR, ("BLONDE", other_country)),
            (_COUNTRY, ("DE", other_country)),
        ],
    )

    response = client.get("/api/people?hair_color=BLONDE&hair_color=RED&country=us")

    assert response.status_code == 200, response.text
    body = response.json()
    assert {one["id"] for one in body["items"]} == {both, other_hair}
    assert body["total"] == 2

    # The same filter, on the panel that is drawn beside it.
    assert _facets(client, "people", "hair_color", hair_color="BLONDE", country="us") == [
        {"value": "BLONDE", "count": 1, "label": None}
    ]


def test_a_from_link_into_a_narrowed_wall_lands_on_its_own_row(
    client: TestClient, library: Library
) -> None:
    """`from=` is resolved against the wall the page is, filtering and all.

    Resolved against the whole wall whatever the page was filtered by, it would be wrong: here Wren
    is third of everybody and second of the blondes, so the filtered link would open one row past
    her, at an offset the filtered wall does not even have. Both filters a People wall takes
    are put to it: a facet pick (the row filter) and a file filter (the "seen with" tab,
    `with_person`).
    """
    sign_in(client)
    red = make_person(client, "Cass Ivory")
    first = make_person(client, "Neve Arbor")
    wanted = make_person(client, "Wren Halloway")
    write(
        db_path(client),
        [(_HAIR, ("RED", red)), (_HAIR, ("BLONDE", first)), (_HAIR, ("BLONDE", wanted))],
    )

    whole = client.get("/api/people", params={"sort": "name_az", "from": wanted})
    narrowed = client.get(
        "/api/people", params={"sort": "name_az", "hair_color": "BLONDE", "from": wanted}
    )

    assert whole.status_code == 200, whole.text
    assert narrowed.status_code == 200, narrowed.text
    assert whole.json()["offset"] == 2, "the check needs her further down the whole wall"
    assert narrowed.json()["offset"] == 1
    assert narrowed.json()["items"][0]["id"] == wanted

    # The file filter: Neve and Wren are on one file and Cass is not, so Neve's "seen with" wall is
    # the two of them: Neve's own row is ranked there and dropped from the page afterwards.
    assign(client, [library.shared], [first, wanted])
    assign(client, [library.private], [red])
    seen_with = client.get(
        "/api/people", params={"sort": "name_az", "with_person": first, "from": wanted}
    )
    assert seen_with.status_code == 200, seen_with.text
    assert seen_with.json()["offset"] == 1
    assert [one["id"] for one in seen_with.json()["items"]] == [wanted]


def test_a_facet_the_wall_does_not_have_is_refused(client: TestClient) -> None:
    """Refused rather than ignored: a caller who asked for one dimension and silently got another
    has a panel that looks wrong for no visible reason."""
    sign_in(client)

    response = client.get("/api/people/facets", params={"facet": "favourite_colour"})

    assert response.status_code == 422, response.text


def test_the_sites_wall_counts_sites(client: TestClient) -> None:
    """The same panel over a different noun, counting sites rather than people.

    `usernames` is one of the facets that is not a field: nobody types "has usernames" onto a
    record, and a site with none is a site nothing has been filed under yet, which is a real thing
    to go looking for.
    """
    user = sign_in(client)
    assert user
    make_username(client, "A Studio", "somebody")
    make_username(client, "Another Studio", "somebody-else")
    assert _facets(client, "sites", "usernames") == [{"value": "yes", "count": 2, "label": None}]

    # And the wall obeys the same words the panel hands back.
    narrowed = client.get("/api/sites?usernames=yes")
    assert narrowed.status_code == 200, narrowed.text
    assert sorted(one["name"] for one in narrowed.json()["items"]) == ["A Studio", "Another Studio"]


def test_a_refused_value_leaves_its_rows_out_and_keeps_the_rows_with_none(
    client: TestClient,
) -> None:
    """`-value` is "not this", the spelling the wall of files reads, on the rows' own facets.

    The person with no hair colour stays: she is not red-haired, and a refusal that dropped every
    row with nothing written would empty the wall for a reason nobody can see. A pick and a
    refusal on one facet compose, and the panel counts under the refusal too.
    """
    sign_in(client)
    blonde = make_person(client, "Neve Arbor")
    red = make_person(client, "Wren Halloway")
    unknown = make_person(client, "Cass Ivory")
    write(
        db_path(client),
        [
            (_HAIR, ("BLONDE", blonde)),
            (_HAIR, ("RED", red)),
            (_COUNTRY, ("US", blonde)),
            (_COUNTRY, ("US", red)),
            (_COUNTRY, ("US", unknown)),
        ],
    )

    def ids(query: str) -> set[str]:
        answer = client.get(f"/api/people?{query}")
        assert answer.status_code == 200, answer.text
        return {one["id"] for one in answer.json()["items"]}

    assert ids("hair_color=-RED") == {blonde, unknown}
    assert ids("hair_color=-red") == {blonde, unknown}, "a refusal folds case as a pick does"
    assert ids("hair_color=BLONDE&hair_color=-RED") == {blonde}
    assert ids("hair_color=-BLONDE&hair_color=-RED") == {unknown}
    assert _facets(client, "people", "country", hair_color="-RED") == [
        {"value": "US", "count": 2, "label": None}
    ]


def test_the_sites_wall_reads_a_refusal_through_the_same_narrowing(client: TestClient) -> None:
    """The sites route hands its picks to the one narrowing, so "not this" reaches it unchanged."""
    sign_in(client)
    make_username(client, "A Studio", "somebody")
    client.post("/api/sites", json={"name": "Quiet Studio"})

    refused = client.get("/api/sites?usernames=-yes")

    assert refused.status_code == 200, refused.text
    assert [one["name"] for one in refused.json()["items"]] == ["Quiet Studio"]


def test_a_facet_the_sites_wall_does_not_have_is_refused(client: TestClient) -> None:
    """The same refusal on the other noun, and it is asserted separately because it is a second
    copy of the check: the two walls have different dimensions, so one list saying no proves
    nothing about the other."""
    sign_in(client)

    response = client.get("/api/sites/facets", params={"facet": "hair_color"})

    assert response.status_code == 422, response.text


def test_the_disagreements_column_is_declared_on_both_of_this_wall_s_routes(
    client: TestClient, library: Library
) -> None:
    """The one dimension whose values are not in the database, over the wire.

    What is in question is the half a kernel test cannot see: that the route declares `disagrees`,
    that FastAPI does not drop it, and that the ids reach the statement. Nothing here is linked to a
    stash-box, so nothing disagrees, which is the ordinary state of a library and is a real answer
    rather than a missing one: both people are counted under the second row, and picking the first
    row filters the wall to nobody instead of leaving it as it was.
    """
    _ = library
    sign_in(client)
    make_person(client, "Neve Arbor")
    make_person(client, "Wren Halloway")

    counted = _facets(client, "people", "disagrees")
    assert [(one["value"], one["count"]) for one in counted] == [("no", 2)]

    narrowed = client.get("/api/people", params={"disagrees": "yes"})
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["total"] == 0

    everybody = client.get("/api/people", params={"disagrees": "no"})
    assert everybody.status_code == 200, everybody.text
    assert everybody.json()["total"] == 2


def test_the_sites_wall_declares_the_disagreements_column_too(
    client: TestClient, library: Library
) -> None:
    """The second of the three walls a stash-box can know. See the People wall's test above."""
    _ = library
    sign_in(client)
    make_username(client, "Kestrel Media", "aria.solano")

    counted = _facets(client, "sites", "disagrees")
    assert [(one["value"], one["count"]) for one in counted] == [("no", 1)]

    narrowed = client.get("/api/sites", params={"disagrees": "yes"})
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["total"] == 0


def test_the_people_wall_counts_which_box_enriched_each_person(
    client: TestClient, library: Library
) -> None:
    """WHICH box, not whether one.

    The wall of files has a column of this name, and the question behind it is about a PERSON: a
    performer FansDB had enriched cannot be found under that one, because that column counts files a
    box recognised. `linked` alone answers yes and no, and on an
    install with three services configured that disagree, "a stash-box knows about her" is the
    answer that sends somebody through Settings to find out which.

    The three things a row has to do are all here, because a column that counts one set and selects
    another is the fault this shape exists to prevent:

      - one row per box, counted from that kind's link table;
      - a row for the people NO box has written to (what `linked:no` asks);
      - each row selects exactly the people it counted.

    A person two boxes know about is under BOTH rows, the same overlap two tags already have, so
    the rows do not add up to the wall and are not meant to.
    """
    _ = library
    sign_in(client)
    known = make_person(client, "Neve Arbor")
    shared = make_person(client, "Wren Halloway")
    make_person(client, "Cass Ivory")
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at)"
                " VALUES (?, ?, ?, ?, 0)",
                (box, name, f"https://{box}.test/graphql", slug),
            )
            for box, name, slug in (
                ("box-fans", "FansDB", "fansdb"),
                ("box-stash", "StashDB", "stashdb"),
            )
        ]
        + [
            (
                "INSERT INTO person_stash_box_links"
                " (person_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, ?, '{}', 0)",
                (person, box, f"remote-{box}"),
            )
            for person, box in (
                (known, "box-fans"),
                (shared, "box-fans"),
                (shared, "box-stash"),
            )
        ],
    )

    counted = _facets(client, "people", "enriched")
    assert sorted((str(one["value"]), one["count"]) for one in counted) == [
        ("fansdb", 2),
        ("none", 1),
        ("stashdb", 1),
    ]

    only_fans = client.get("/api/people", params={"enriched": "fansdb"})
    assert only_fans.status_code == 200, only_fans.text
    assert only_fans.json()["total"] == 2

    untouched = client.get("/api/people", params={"enriched": "none"})
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["total"] == 1

    # Two rows picked is "either", which is what picking two rows of any column means.
    either = client.get("/api/people", params=[("enriched", "stashdb"), ("enriched", "none")])
    assert either.status_code == 200, either.text
    assert either.json()["total"] == 2


def test_the_retired_linked_word_still_narrows_and_is_not_a_spelling_of_enriched(
    client: TestClient, library: Library
) -> None:
    """`linked` is not a column, and it is still a word.

    A bookmarked `?linked=yes` must not come back as the whole wall: everything, with nothing on
    screen saying anything had been widened. This is the server half, and it asserts the half a
    client test cannot: that the parameter is still declared on the listing and still filters.

    ## And why it is kept rather than mapped on to `enriched`

    Because they are not the same set, and the difference is the person linked to a SELF-HOSTED
    box. A value of `enriched` is a box's slug, derived from its address so that it means the same
    thing on every install, and a box Sift has no word for has no slug, so that person has no
    value of the dimension and is under no row of the column. She is `linked=yes` all the same.
    Mapping the old word on to every slug the column offers would therefore hand back a SUBSET of
    what the address asked for, silently, which is the same class of fault as widening it.
    """
    _ = library
    sign_in(client)
    known = make_person(client, "Neve Arbor")
    own = make_person(client, "Wren Halloway")
    make_person(client, "Cass Ivory")
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at)"
                " VALUES (?, ?, ?, ?, 0)",
                (box, name, f"https://{box}.test/graphql", slug),
            )
            # The second has no slug, which is what a box at an address Sift has never heard of is
            # stored as. Written here rather than derived, because it is the whole case.
            for box, name, slug in (
                ("box-fans", "FansDB", "fansdb"),
                ("box-own", "A box of my own", None),
            )
        ]
        + [
            (
                "INSERT INTO person_stash_box_links"
                " (person_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, ?, '{}', 0)",
                (person, box, f"remote-{box}"),
            )
            for person, box in ((known, "box-fans"), (own, "box-own"))
        ],
    )

    linked = client.get("/api/people", params={"linked": "yes"})
    assert linked.status_code == 200, linked.text
    assert linked.json()["total"] == 2

    unlinked = client.get("/api/people", params={"linked": "no"})
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["total"] == 1

    # The self-hosted box is in no row of the column, so no pick of rows can reach her: `none` is
    # "no box wrote to this" and she is not that either.
    counted = _facets(client, "people", "enriched")
    assert sorted((str(one["value"]), one["count"]) for one in counted) == [
        ("fansdb", 1),
        ("none", 1),
    ]
    every_row = client.get("/api/people", params=[("enriched", "fansdb"), ("enriched", "none")])
    assert every_row.status_code == 200, every_row.text
    assert every_row.json()["total"] == 2
    assert {one["name"] for one in every_row.json()["items"]} == {"Neve Arbor", "Cass Ivory"}


def test_the_created_by_column_names_the_task_sift_made_each_person_by(
    client: TestClient,
) -> None:
    """Sift's people are counted by how Sift made them, and `sift` still selects every one."""
    sign_in(client)
    by_folder = make_person(client, "Neve Arbor")
    by_prints = make_person(client, "Wren Halloway")
    older = make_person(client, "Cass Ivory")
    write(
        db_path(client),
        [
            (
                "UPDATE people SET created_by_kind = 'sift', created_by_user_id = NULL,"
                " created_by_via = ? WHERE id = ?",
                (via, person),
            )
            for via, person in (
                ("folder", by_folder),
                ("facial_fingerprints", by_prints),
                (None, older),
            )
        ],
    )

    counted = _facets(client, "people", "created")
    assert sorted((str(one["value"]), one["count"]) for one in counted) == [
        ("facial_fingerprints", 1),
        ("folder", 1),
        ("sift", 1),
    ]

    def total(**params: str) -> int:
        answer = client.get("/api/people", params=params)
        assert answer.status_code == 200, answer.text
        return int(answer.json()["total"])

    assert total(created="facial_fingerprints") == 1
    assert total(created="folder") == 1
    assert total(created="sift") == 3
