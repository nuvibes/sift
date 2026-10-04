# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and undoing which sites a file is filed under, asked from the file's end. Taking off the
unattributed filing must keep the username that posted the file."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.kernel import site_icons
from sift.kernel.access import (
    MADE_BY_A_PERSON,
    link_username_to_asset,
    seed_site_username,
)
from sift.kernel.db import Database
from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    db_path,
    make_username,
    read,
    share,
    sign_in,
    write,
)


def make_site(client: TestClient, name: str) -> str:
    response = client.post("/api/sites", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def file_under(client: TestClient, asset_ids: list[str], site_ids: list[str]):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/assets/sites",
        json={"asset_ids": asset_ids, "site_ids": site_ids},
    )


def unfile_from(client: TestClient, asset_ids: list[str], site_ids: list[str]):  # type: ignore[no-untyped-def]
    """The same route, told to take them off. See `SiteAssignment.add`."""
    return client.post(
        "/api/assets/sites",
        json={"asset_ids": asset_ids, "site_ids": site_ids, "add": False},
    )


def seed_site(client: TestClient, name: str) -> str:
    """A site made as a download makes one."""
    import asyncio

    async def run() -> str:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            site_id, _ = await seed_site_username(
                database, site=name, name="poster", made=MADE_BY_A_PERSON
            )
            return site_id
        finally:
            await database.close()

    return asyncio.run(run())


def filings(client: TestClient, asset_id: str) -> list[dict[str, object]]:
    response = client.get(f"/api/assets/{asset_id}/filings")
    assert response.status_code == 200, response.text
    return list(response.json())


def _files_on(client: TestClient, site_id: str) -> int:
    """How many files the Sites wall says this site reaches, from the join."""
    listed = client.get("/api/sites").json()["items"]
    return int(next(row["asset_count"] for row in listed if row["id"] == site_id))


def _link(client: TestClient, asset_id: str, username_id: str) -> None:
    """Put one file under one existing username, the way a download does."""
    write(
        db_path(client),
        [
            (
                "INSERT OR IGNORE INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (asset_id, username_id),
            )
        ],
    )


def test_a_file_filed_under_a_site_says_so_on_itself(client: TestClient, library: Library) -> None:
    """The whole of the visibility half: drop a file on a site, and the file now says which.

    `username` is null rather than the empty string the column holds. A drop names nobody, and a
    chip reading "@ on Vimeo" would be the storage detail leaking onto the screen.
    """
    sign_in(client)
    site = make_site(client, "Vimeo")

    assert file_under(client, [library.shared], [site]).status_code == 200

    listed = filings(client, library.shared)
    assert [(row["site"], row["username"]) for row in listed] == [("Vimeo", None)]
    assert listed[0]["site_id"] == site
    assert listed[0]["username_id"]


def test_a_filing_a_stash_box_made_names_the_box(client: TestClient, library: Library) -> None:
    """A filing a stash-box made names the box, read off the file's applied match; a hand filing
    on the same file names none."""
    sign_in(client)
    # Seeded through the tables, as `make_username` is.
    by_box = make_username(client, "Vimeo", "")
    by_hand = make_username(client, "Dailymotion", "")
    _link(client, library.shared, by_box)
    _link(client, library.shared, by_hand)
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
                " VALUES ('box', 'StashDB', 'https://example.invalid/box', 0)",
                (),
            ),
            (
                "INSERT INTO asset_stash_box_matches"
                " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
                " VALUES (?, 'box', 'remote', '[]', 'certain', 'applied', 0, 1)",
                (library.shared,),
            ),
            (
                "UPDATE asset_usernames SET source = 'stash_box'"
                " WHERE asset_id = ? AND username_id = ?",
                (library.shared, by_box),
            ),
        ],
    )

    listed = filings(client, library.shared)

    assert {row["site"]: (row["source"], row["source_name"]) for row in listed} == {
        "Vimeo": ("stash_box", "StashDB"),
        "Dailymotion": (None, None),
    }


def test_a_file_nobody_filed_is_filed_under_nothing(client: TestClient, library: Library) -> None:
    """Most of a scanned library. An empty list, not a 404: the file exists and came from nowhere
    Sift knows about, which is a fact rather than a missing answer."""
    sign_in(client)

    assert filings(client, library.shared) == []


def test_the_filing_can_be_taken_off_again(client: TestClient, library: Library) -> None:
    """The undo of a filing: from the file's end, the way a chip takes it off."""
    sign_in(client)
    site = make_site(client, "Vimeo")
    file_under(client, [library.shared], [site])
    username = str(filings(client, library.shared)[0]["username_id"])

    removed = client.delete(f"/api/assets/{library.shared}/filings/{username}")

    assert removed.status_code == 204, removed.text
    assert filings(client, library.shared) == []


def test_taking_a_filing_off_takes_the_file_off_the_site(
    client: TestClient, library: Library
) -> None:
    """Taking a filing off takes the file off the site, through `asset_usernames`."""
    sign_in(client)
    site = make_site(client, "Vimeo")
    file_under(client, [library.shared], [site])
    username = str(filings(client, library.shared)[0]["username_id"])
    assert _files_on(client, site) == 1

    client.delete(f"/api/assets/{library.shared}/filings/{username}")

    assert _files_on(client, site) == 0


def test_taking_off_the_unattributed_filing_keeps_the_username_that_posted_it(
    client: TestClient, library: Library
) -> None:
    """Taking off the unattributed filing keeps the username that posted the file: removal names a
    username, not a site."""
    sign_in(client)
    posted_by = make_username(client, "Vimeo", "alice")
    _link(client, library.shared, posted_by)
    site = str(client.get("/api/sites").json()["items"][0]["id"])
    file_under(client, [library.shared], [site])

    listed = filings(client, library.shared)
    assert sorted(str(row["username"] or "") for row in listed) == ["", "alice"]
    unattributed = next(row for row in listed if row["username"] is None)

    client.delete(f"/api/assets/{library.shared}/filings/{unattributed['username_id']}")

    assert [(row["site"], row["username"]) for row in filings(client, library.shared)] == [
        ("Vimeo", "alice")
    ]


def test_taking_off_a_filing_that_is_already_gone_is_quiet(
    client: TestClient, library: Library
) -> None:
    """204, not 404.

    Two people pressing the same chip is a race, and answering the second one with "not found" tells
    them something is wrong when what they asked for is true. There is nothing to undo about an undo.
    """
    sign_in(client)

    removed = client.delete(f"/api/assets/{library.shared}/filings/{NEVER_EXISTED}")

    assert removed.status_code == 204, removed.text


def test_a_file_this_user_cannot_see_is_a_404_either_way(
    client: TestClient, library: Library
) -> None:
    """Read and removal both resolve the asset first, so an id out of reach answers as it does
    everywhere else, and the same answer whether the file is absent or simply not theirs."""
    sign_in(client)

    assert client.get(f"/api/assets/{NEVER_EXISTED}/filings").status_code == 404
    assert client.delete(f"/api/assets/{NEVER_EXISTED}/filings/{NEVER_EXISTED}").status_code == 404


def test_a_site_in_the_vault_conceals_the_FILE_rather_than_hiding_the_chip(
    client: TestClient, library: Library
) -> None:
    """A site in the vault conceals the file (`_HIDDEN_BY_MEMBERSHIP`), so the screen is refused
    with 423 before any chip; `filings_of_asset` needs no per-site check."""
    sign_in(client)
    site = make_site(client, "Vimeo")
    file_under(client, [library.shared], [site])
    assert client.put(f"/api/sites/{site}/vault", json={"vault": True}).status_code == 204

    refused = client.get(f"/api/assets/{library.shared}/filings")

    assert refused.status_code == 423, refused.text


def test_a_guest_may_read_where_a_file_came_from_and_may_not_change_it(
    client: TestClient, library: Library
) -> None:
    """Where a file came from is part of what the file IS: the same split the people on a file
    have. Reading is everybody's; the write is admin-only."""
    sign_in(client)
    site = make_site(client, "Vimeo")
    file_under(client, [library.shared], [site])
    username = str(filings(client, library.shared)[0]["username_id"])
    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    assert client.get(f"/api/assets/{library.shared}/filings").status_code == 200
    assert client.delete(f"/api/assets/{library.shared}/filings/{username}").status_code == 403


def test_a_filing_carries_what_names_its_sites_cover(client: TestClient, library: Library) -> None:
    """The file's page draws each filing as the site's chip, and a chip's address is kept by the
    browser only when it names WHICH cover it is (`kernel/covers.py names_its_cover`). The row
    carries the site's cover fields, the user's token and the logo's token, off the Sites
    wall's own batched read, so the chip and the wall cannot disagree about the picture.
    """
    sign_in(client)
    site = make_site(client, "Marrowvale Studios")
    assert file_under(client, [library.shared], [site]).status_code == 200
    chosen = client.put(
        f"/api/sites/{site}/cover", json={"asset_id": library.shared, "at_ms": 4200}
    )
    assert chosen.status_code == 200, chosen.text

    (row,) = filings(client, library.shared)
    assert (row["cover_asset_id"], row["cover_at_ms"], row["cover_upload_id"]) == (
        library.shared,
        4200,
        None,
    )
    assert row["art"]
    assert row["icon"] == site_icons.icon_token(None, "Marrowvale Studios")
    # The same fields the Sites wall hands the same site, so the chip names what the card names.
    wall = next(one for one in client.get("/api/sites").json()["items"] if one["id"] == site)
    assert {key: row[key] for key in _COVER} == {key: wall[key] for key in _COVER}


_COVER = ("art", "cover_asset_id", "cover_upload_id", "cover_at_ms", "icon")


def test_a_filing_whose_site_was_deleted_is_still_listed(
    client: TestClient, library: Library
) -> None:
    """`usernames.site_id` is `ON DELETE SET NULL`, so a username outlives its site while still
    holding files. An inner join would drop exactly these rows: the ones nothing else in the
    application shows either, which makes them the ones most in need of a way off."""
    sign_in(client)
    posted_by = make_username(client, "Vimeo", "alice")
    _link(client, library.shared, posted_by)
    write(db_path(client), [("DELETE FROM sites", ())])

    assert [(row["site"], row["username"]) for row in filings(client, library.shared)] == [
        (None, "alice")
    ]
    # A site that is gone has no cover address, so nothing names one.
    (row,) = filings(client, library.shared)
    assert all(row[key] is None for key in _COVER)


def test_reading_the_filings_of_a_file_is_the_same_list_the_download_wrote(
    client: TestClient, library: Library
) -> None:
    """The kernel's own two calls, which is what a download makes. Imported from where the download
    service imports them, so a change to either is a change to both."""
    import asyncio

    async def run() -> str:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            _, username_id = await seed_site_username(
                database, site="OnlyFans", name="bob", made=MADE_BY_A_PERSON
            )
            await link_username_to_asset(database, asset_id=library.shared, username_id=username_id)
            return username_id
        finally:
            await database.close()

    sign_in(client)
    username_id = asyncio.run(run())

    assert [
        (row["username_id"], row["site"], row["username"])
        for row in filings(client, library.shared)
    ] == [(username_id, "OnlyFans", "bob")]
    assert read(db_path(client), "SELECT * FROM asset_usernames")


def test_a_selection_comes_off_a_site_in_one_request(client: TestClient, library: Library) -> None:
    """The bulk undo.

    The per-FILE one (`DELETE /assets/{id}/filings/{username}`) cannot answer a tick cleared
    over a selection without one request per file, each of which has to discover the username id
    first. This is the other direction of the write that put them there.
    """
    sign_in(client)
    site = seed_site(client, "Harbour")
    assert file_under(client, [library.shared, library.private], [site]).status_code == 200
    assert _files_on(client, site) == 2

    taken = unfile_from(client, [library.shared, library.private], [site])

    assert taken.status_code == 200, taken.text
    assert taken.json()["changed"] == 2
    assert _files_on(client, site) == 0
    assert filings(client, library.shared) == []


def test_taking_a_file_off_a_site_leaves_the_site_and_its_other_files(
    client: TestClient, library: Library
) -> None:
    """Rows in a join table go and nothing else does.

    The same promise the filing makes in the other direction, and worth asserting rather than
    reasoning about: a removal by site that reached the site itself, or the files nobody named,
    would be the undo destroying more than it undid.
    """
    sign_in(client)
    site = seed_site(client, "Harbour")
    file_under(client, [library.shared, library.private], [site])

    unfile_from(client, [library.shared], [site])

    assert _files_on(client, site) == 1
    assert [row["site"] for row in filings(client, library.private)] == ["Harbour"]
    listed = client.get("/api/sites").json()["items"]
    assert [row["id"] for row in listed if row["id"] == site] == [site]


def test_taking_a_file_off_a_site_it_was_never_on_changes_nothing(
    client: TestClient, library: Library
) -> None:
    """Quiet, for the reason the per-file removal above is: two people clearing one tick is a race
    with a loser, and telling the second that something is wrong when the thing they asked for is
    already true is the wrong answer."""
    sign_in(client)
    site = seed_site(client, "Harbour")

    taken = unfile_from(client, [library.shared], [site])

    assert taken.status_code == 200, taken.text
    assert filings(client, library.shared) == []


def test_a_guest_may_not_take_a_file_off_a_site(client: TestClient, library: Library) -> None:
    sign_in(client)
    site = seed_site(client, "Harbour")
    file_under(client, [library.shared], [site])

    sign_in(client, "guest")
    share(client, library.shared, sign_in(client, "guest"))

    assert unfile_from(client, [library.shared], [site]).status_code == 403
