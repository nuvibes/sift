# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sites, and what deleting one is allowed to take with it.

Deleting a site is the destructive operation in this slice, and the interesting part is what
it refuses to do. The foreign key from a username to its site is `ON DELETE SET NULL`, so a
delete that went ahead regardless would not remove the usernames: it would leave them
belonging to no site, which no screen can explain and nobody would have chosen on purpose.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

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
from sift.testing.auth import TEST_PIN


def _make_site(client: TestClient, name: str) -> str:
    response = client.post("/api/sites", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_a_site_carries_how_many_people_it_has_media_of(client: TestClient) -> None:
    """People, not USERNAMES: counted by username, somebody with three usernames on one site
    would be three. A site with attribution and no People counts
    nobody."""
    sign_in(client)
    _make_site(client, "Instagram")
    make_username(client, "Instagram", "jane")

    listed = client.get("/api/sites").json()["items"]

    assert [(one["name"], one["people_count"]) for one in listed] == [("Instagram", 0)]


def _file_under(client: TestClient, asset_id: str, username_id: str) -> None:
    """File one asset under one username, the way a download does."""
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (asset_id, username_id),
            )
        ],
    )


def test_a_person_with_two_usernames_on_one_site_is_counted_once(
    client: TestClient, library: Library
) -> None:
    """The count is DISTINCT over people, and a file reaching it twice must not say two.

    The test above says what the number means; this one says what it does when one file arrives at a
    site down two paths at once, which is what a repost or a renamed username looks like in the
    tables. The number is in the wall's own count CTEs, and a GROUP BY fans a file out exactly where
    a per-row subquery would, so this is the property that would go quietly wrong if the DISTINCT
    were ever dropped from either.
    """
    sign_in(client)
    _make_site(client, "Instagram")
    jane = make_person(client, "Wren Halloway")
    assign(client, [library.shared], [jane])
    for name in ("wren", "wren_backup"):
        _file_under(client, library.shared, make_username(client, "Instagram", name))

    listed = client.get("/api/sites").json()["items"]

    assert [(one["name"], one["people_count"], one["asset_count"]) for one in listed] == [
        ("Instagram", 1, 1)
    ]


def test_a_person_this_viewer_has_concealed_is_not_counted(
    client: TestClient, library: Library
) -> None:
    """Somebody in the vault drops out of the tally without the number saying they were ever there.

    The point of scoping a count: a site reading two over a People tab holding one has published
    that there is a second, which is the whole of what concealing them was for. Two files here and
    not one, because concealing somebody conceals the files they are in as well: with a single
    file the whole card falls to nought and the test would pass without the person count being
    scoped at all.
    """
    sign_in(client)
    _make_site(client, "Instagram")
    username = make_username(client, "Instagram", "wren")
    for asset in (library.shared, library.private):
        _file_under(client, asset, username)
    seen = make_person(client, "Wren Halloway")
    concealed = make_person(client, "Marlow Vane")
    assert assign(client, [library.shared], [seen]).status_code == 200
    assert assign(client, [library.private], [concealed]).status_code == 200

    assert [
        (one["people_count"], one["asset_count"])
        for one in client.get("/api/sites").json()["items"]
    ] == [(2, 2)]

    # ...and then one of them goes into the vault. Concealed AFTER the attribution rather than
    # before it, because a write naming somebody concealed is answered the way an unknown id is,
    # so seeding it the other way round assigns nobody and the test passes for the wrong reason.
    assert client.put(f"/api/people/{concealed}/vault", json={"vault": True}).status_code == 204

    listed = client.get("/api/sites").json()["items"]

    assert [(one["people_count"], one["asset_count"]) for one in listed] == [(1, 1)]


def test_one_site_named_twice_is_one_site(client: TestClient) -> None:
    """Names are unique without regard to case, so a library's attribution is not split in two."""
    sign_in(client)

    assert _make_site(client, "TikTok") == _make_site(client, "tiktok")


def test_an_empty_site_deletes_without_being_asked_twice(client: TestClient) -> None:
    sign_in(client)
    site = _make_site(client, "Instagram")

    assert client.delete(f"/api/sites/{site}").status_code == 204
    assert client.get("/api/sites").json()["items"] == []


def test_deleting_a_site_takes_what_it_recorded_with_it(client: TestClient) -> None:
    sign_in(client)
    site = _make_site(client, "Instagram")
    make_username(client, "Instagram", "jane")

    response = client.delete(f"/api/sites/{site}")

    assert response.status_code == 204
    assert client.get("/api/sites").json()["items"] == []
    assert not read(db_path(client), "SELECT * FROM usernames")


def test_deleting_a_site_moves_no_file(client: TestClient, library: Library) -> None:
    """The attribution goes; the media stays exactly where it is."""
    sign_in(client)
    site = _make_site(client, "Instagram")
    username = make_username(client, "Instagram", "jane")
    read(db_path(client), "SELECT 1")

    before_bytes = library.path_of("shared").read_bytes()
    before_locations = read(db_path(client), "SELECT * FROM asset_locations ORDER BY id")

    client.delete(f"/api/sites/{site}")

    assert not read(db_path(client), "SELECT * FROM usernames WHERE id = ?", (username,))
    assert library.path_of("shared").read_bytes() == before_bytes
    assert read(db_path(client), "SELECT * FROM asset_locations ORDER BY id") == before_locations


def test_deleting_a_site_forgets_every_grant_that_named_it(client: TestClient) -> None:
    """`acl_grants.object_id` carries no foreign key, so nothing cascades and nothing else does it.

    A grant left behind goes on applying to whatever object later gets that id. A stale share
    hands somebody a file they were never meant to have; a stale restrict is a promise that
    quietly stopped being kept. Neither announces itself, which is what makes this worth a test.
    """
    sign_in(client)
    viewer = sign_in(client, "guest", who="two")
    sign_in(client)
    site = _make_site(client, "Instagram")
    grant_on(client, "site", site, viewer)

    assert grants_naming(client, "site", site), "the grant is there to begin with"

    assert client.delete(f"/api/sites/{site}").status_code == 204
    assert grants_naming(client, "site", site) == []


def test_deleting_a_person_forgets_every_grant_that_named_them(client: TestClient) -> None:
    """The same leak, on the other object type a grant in this slice can name."""
    sign_in(client)
    viewer = sign_in(client, "guest", who="two")
    sign_in(client)
    person = make_person(client, "Jane Doe")
    grant_on(client, "person", person, viewer, effect="restrict")

    assert grants_naming(client, "person", person), "the grant is there to begin with"

    assert client.delete(f"/api/people/{person}").status_code == 204
    assert grants_naming(client, "person", person) == []


def test_renaming_a_site_onto_a_taken_name_is_refused(client: TestClient) -> None:
    sign_in(client)
    _make_site(client, "Instagram")
    tiktok = _make_site(client, "TikTok")

    response = client.put(f"/api/sites/{tiktok}", json={"name": "Instagram"})

    assert response.status_code == 409
    assert {p["name"] for p in client.get("/api/sites").json()["items"]} == {
        "Instagram",
        "TikTok",
    }


def test_renaming_a_site_works(client: TestClient) -> None:
    sign_in(client)
    site = _make_site(client, "Instagam")

    response = client.put(f"/api/sites/{site}", json={"name": "Instagram"})

    assert response.status_code == 200
    assert response.json()["name"] == "Instagram"


def test_a_site_deleted_by_somebody_else_mid_delete_reads_like_any_other_miss(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two people deleting one site at once: the second passes the visibility check, and by its
    own delete the row is gone. It answers the miss an unknown id does, not a success for a delete
    that removed nothing."""
    from sift.slices.people.service import PeopleService

    sign_in(client)
    site = client.post("/api/sites", json={"name": "Somewhere"}).json()["id"]
    real = PeopleService.assets_of_site

    async def gone_meanwhile(self: PeopleService, site_id: str) -> list[str]:
        found = await real(self, site_id)
        await self._db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        return found

    monkeypatch.setattr(PeopleService, "assets_of_site", gone_meanwhile)

    assert client.delete(f"/api/sites/{site}").status_code == 404


def test_an_unknown_site_is_missing_rather_than_refused(client: TestClient) -> None:
    sign_in(client)

    assert client.delete(f"/api/sites/{NEVER_EXISTED}").status_code == 404
    assert client.put(f"/api/sites/{NEVER_EXISTED}", json={"name": "X"}).status_code == 404


def _set_vault(client: TestClient, site_id: str, vault: bool) -> int:
    return int(client.put(f"/api/sites/{site_id}/vault", json={"vault": vault}).status_code)


def _site_names(client: TestClient) -> list[str]:
    return [site["name"] for site in client.get("/api/sites").json()["items"]]


def test_a_vaulted_site_is_absent_from_the_list(client: TestClient) -> None:
    """Absent, not listed with a zero. Concealed from admins too.

    A site is a thing a library is organised by, so a name left on the wall over a count of nothing
    says that something came from there and is being kept back.
    """
    sign_in(client)
    site = _make_site(client, "Instagram")
    _make_site(client, "TikTok")

    assert _set_vault(client, site, True) == 204

    assert _site_names(client) == ["TikTok"]


def test_a_vaulted_site_takes_everything_under_it_with_it(client: TestClient) -> None:
    """A concealed site is an unknown site, and so is everything reached through it."""
    sign_in(client)
    site = _make_site(client, "Instagram")
    make_username(client, "Instagram", "jane")

    _set_vault(client, site, True)

    assert client.get(f"/api/sites/{site}").status_code == 404, (
        "a concealed site answers as an unknown one does, so nothing under it is reached either"
    )


def test_a_vaulted_site_cannot_be_taken_back_out_while_the_vault_is_locked(
    client: TestClient,
) -> None:
    """The seal, the same one collections and tags have."""
    sign_in(client)
    site = _make_site(client, "Instagram")
    _set_vault(client, site, True)

    assert _set_vault(client, site, False) == 404
    assert _site_names(client) == [], "still concealed after the refused write"


@dataclass(frozen=True)
class _Seeds:
    """What a write names besides the site: another visible site, a real tag, a real file.

    Real on purpose. A write that names a tag or a file nobody minted answers 404 for THAT, before
    or after the site is asked about, and a case built that way passes with the site check deleted.
    """

    other: str
    tag: str
    asset: str


def _seed(client: TestClient, library: Library) -> _Seeds:
    tag = client.post("/api/tags", json={"name": "Outdoors"})
    assert tag.status_code == 201, tag.text
    return _Seeds(
        other=_make_site(client, "TikTok"), tag=str(tag.json()["id"]), asset=library.shared
    )


#: Every write that names a site by id, as `(client, the site, the seeds) -> status`.
#: A route that is added and not listed here is a route nobody has asked this of. The table is
#: the whole of the rule's reach, so it is kept beside the test rather than inferred.
_SITE_WRITES: dict[str, Callable[[TestClient, str, _Seeds], int]] = {
    "hide": lambda client, site, _seeds: _set_vault(client, site, True),
    "unhide": lambda client, site, _seeds: _set_vault(client, site, False),
    "rename": lambda client, site, _seeds: (
        client.put(f"/api/sites/{site}", json={"name": "Renamed"}).status_code
    ),
    "delete": lambda client, site, _seeds: client.delete(f"/api/sites/{site}").status_code,
    "cover pick": lambda client, site, _seeds: (
        client.put(f"/api/sites/{site}/cover", json={"asset_id": None, "at_ms": None}).status_code
    ),
    "cover upload": lambda client, site, _seeds: (
        client.post(
            f"/api/sites/{site}/cover-picture", files={"file": ("x.png", b"not a picture")}
        ).status_code
    ),
    "details": lambda client, site, _seeds: (
        client.put(f"/api/sites/{site}/details", json={"notes": "a note"}).status_code
    ),
    "favorite": lambda client, site, _seeds: (
        client.put(f"/api/sites/{site}/favorite", json={"favorite": True}).status_code
    ),
    "pin": lambda client, site, _seeds: (
        client.put(f"/api/sites/{site}/pin", json={"pinned": True}).status_code
    ),
    "rating": lambda client, site, _seeds: (
        client.put(f"/api/sites/{site}/rating", json={"rating": 3}).status_code
    ),
    "tag": lambda client, site, seeds: (
        client.post(f"/api/sites/{site}/tags", json={"tag_id": seeds.tag}).status_code
    ),
    "merge into it": lambda client, site, seeds: (
        client.post("/api/sites/merge", json={"sites": [seeds.other], "into": site}).status_code
    ),
    "merge it away": lambda client, site, seeds: (
        client.post("/api/sites/merge", json={"sites": [site], "into": seeds.other}).status_code
    ),
    "weigh a merge into it": lambda client, site, seeds: (
        client.post(
            "/api/sites/weigh-merge", json={"sites": [seeds.other], "into": site}
        ).status_code
    ),
    "weigh merging it away": lambda client, site, seeds: (
        client.post(
            "/api/sites/weigh-merge", json={"sites": [site], "into": seeds.other}
        ).status_code
    ),
    "file under it": lambda client, site, seeds: (
        client.post(
            "/api/assets/sites", json={"asset_ids": [seeds.asset], "site_ids": [site]}
        ).status_code
    ),
}

#: The cases whose other ids are real, proved so: once Hidden is open the same write SUCCEEDS, so
#: the 404 while it was shut can only have been the site's.
_PROVEN_BY_SUCCEEDING = {"tag", "file under it"}


@pytest.mark.parametrize("route", list(_SITE_WRITES))
def test_a_vaulted_site_answers_every_write_the_way_an_unknown_id_does(
    client: TestClient, library: Library, route: str
) -> None:
    """One id, one answer, on EVERY write, not only the hide route.

    Reading the table rather than the wall would let a site this user hid behind a locked Hidden
    section be renamed, merged away or deleted by id while the hide route answered 404 for it.
    Nothing is written either: opening Hidden afterwards finds the site exactly as it was left.
    """
    sign_in(client)
    site = _make_site(client, "Instagram")
    seeds = _seed(client, library)
    assert _set_vault(client, site, True) == 204
    write = _SITE_WRITES[route]

    assert write(client, site, seeds) == 404, "a concealed site"
    assert write(client, NEVER_EXISTED, seeds) == 404, "an id nobody minted"

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    held = client.get(f"/api/sites/{site}")
    assert held.status_code == 200, "still there once Hidden is open"
    assert held.json()["name"] == "Instagram"
    assert held.json()["favorite"] is False
    assert client.get(f"/api/sites/{site}/tags").json() == [], "nothing was tagged"
    if route in _PROVEN_BY_SUCCEEDING:
        assert write(client, site, seeds) == 200, "the same write, once the site may be shown"


def test_a_link_to_a_hidden_site_reads_as_unfiled_until_hidden_is_open(
    client: TestClient,
) -> None:
    """A link is filed by what its address says, through an unscoped name lookup, so an address on
    a site this user hid is filed under it. A reply carrying that site's name would let somebody
    paste an address on a guessed name and learn whether a hidden site by it exists. It reads as
    unfiled while Hidden is shut (in the add reply and in the list) and as filed once it is
    open, because the filing itself was kept."""
    sign_in(client)
    site = _make_site(client, "Instagram")
    assert _set_vault(client, site, True) == 204
    person = make_person(client, "Odalie")

    added = client.post(
        f"/api/people/{person}/links", json={"url": "https://www.instagram.com/odaliefrisk"}
    )
    assert added.status_code == 201, added.text
    assert (added.json()["site_id"], added.json()["site_name"]) == (None, None)
    again = client.post(
        f"/api/people/{person}/links", json={"url": "https://www.instagram.com/odaliefrisk"}
    )
    assert (again.json()["site_id"], again.json()["site_name"]) == (None, None), "the repeat too"
    listed = client.get(f"/api/people/{person}/links").json()
    assert [(one["site_id"], one["site_name"]) for one in listed] == [(None, None)]

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    listed = client.get(f"/api/people/{person}/links").json()
    assert [(one["site_id"], one["site_name"]) for one in listed] == [(site, "Instagram")]


def test_adding_a_site_by_the_name_of_one_this_user_hid_is_a_404(client: TestClient) -> None:
    """The create route's half of the same rule. Names are unique, so the upsert lands on the
    concealed site, and must not hand that site back, cover and counts, to anybody who can guess
    what it is called. Asked in another case on purpose: the match is case-insensitive."""
    sign_in(client)
    site = _make_site(client, "Instagram")
    assert _set_vault(client, site, True) == 204

    assert client.post("/api/sites", json={"name": "instagram"}).status_code == 404

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    again = client.post("/api/sites", json={"name": "instagram"})
    assert again.status_code == 201, again.text
    assert again.json()["id"] == site, "the one site, handed back once it may be shown"


def test_an_unlocked_vault_brings_the_site_and_what_is_under_it_back(client: TestClient) -> None:
    """The half that says they were withheld rather than never there."""
    sign_in(client)
    site = _make_site(client, "Instagram")
    make_username(client, "Instagram", "jane")
    _set_vault(client, site, True)

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200

    assert _site_names(client) == ["Instagram"]
    assert client.get(f"/api/sites/{site}").status_code == 200
    assert _set_vault(client, site, False) == 204


def test_a_site_keeps_every_address_it_is_given(client: TestClient) -> None:
    """A site has several addresses: its own page, the service that hosts it, a mirror. One
    column could hold the first and would lose the rest."""
    sign_in(client)
    site = client.post("/api/sites", json={"name": "Manysite"}).json()["id"]

    answer = client.put(
        f"/api/sites/{site}/details",
        json={
            "notes": None,
            "links": ["https://manysite.test", "https://mirror.manysite.test"],
        },
    )
    assert answer.status_code == 200, answer.text

    held = client.get(f"/api/sites/{site}").json()
    assert held["record"]["links"] == ["https://manysite.test", "https://mirror.manysite.test"]
    # The FIRST of them is the site's address (`sites.SITE_ADDRESS`), which every one-address
    # screen reads.
    assert held["site_url"] == "https://manysite.test"


def test_a_sites_links_are_replaced_whole_and_not_merged(client: TestClient) -> None:
    """The form sends the entire list on every save, so its silence about an address means it was
    removed. Merging would leave a link nobody could delete."""
    sign_in(client)
    site = client.post("/api/sites", json={"name": "Manysite"}).json()["id"]
    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "links": ["https://one.test", "https://two.test"]},
    )
    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "links": ["https://one.test"]},
    )
    assert client.get(f"/api/sites/{site}").json()["record"]["links"] == ["https://one.test"]


def test_a_site_that_is_sent_no_links_keeps_the_ones_it_has(client: TestClient) -> None:
    """A screen that edits the notes knows nothing about the addresses, and its silence must not
    delete them. The same rule the aliases already follow."""
    sign_in(client)
    site = client.post("/api/sites", json={"name": "Manysite"}).json()["id"]
    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "links": ["https://one.test"]},
    )
    client.put(f"/api/sites/{site}/details", json={"notes": "a note"})
    assert client.get(f"/api/sites/{site}").json()["record"]["links"] == ["https://one.test"]


def test_an_address_that_is_not_a_web_address_is_refused(client: TestClient) -> None:
    """`javascript:` and `data:` are both accepted by an href and both run as the page. Checked
    where it is written, not where it is drawn: there will be more than one place that renders one."""
    sign_in(client)
    site = client.post("/api/sites", json={"name": "Manysite"}).json()["id"]
    answer = client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "links": ["javascript:alert(1)"]},
    )
    assert answer.status_code == 422


def test_an_address_that_is_not_a_web_address_is_refused_for_every_entry_in_the_list(
    client: TestClient,
) -> None:
    """`javascript:` and `data:` are both accepted by an `href` and both run as the page. Checked
    where the list arrives rather than where it is drawn, because there will be more than one place
    that renders one and the one that forgets is never the one you expect."""
    sign_in(client)
    site = _make_site(client, "Somewhere")

    refused = client.put(
        f"/api/sites/{site}/details",
        json={"links": ["https://example.test/a", "javascript:alert(1)"]},
    )

    assert refused.status_code == 422


def test_a_blank_among_the_addresses_is_dropped_rather_than_refusing_the_whole_save(
    client: TestClient,
) -> None:
    """A form sends every box it drew, and an empty one is not a bad address."""
    sign_in(client)
    site = _make_site(client, "Somewhere")

    client.put(
        f"/api/sites/{site}/details",
        json={"links": ["https://example.test/a", "   ", ""]},
    )

    assert client.get(f"/api/sites/{site}").json()["record"]["links"] == ["https://example.test/a"]


def test_a_save_that_says_nothing_about_the_addresses_leaves_them_alone(
    client: TestClient,
) -> None:
    sign_in(client)
    site = _make_site(client, "Somewhere")
    client.put(f"/api/sites/{site}/details", json={"links": ["https://example.test/a"]})

    client.put(f"/api/sites/{site}/details", json={"notes": "A note."})

    assert client.get(f"/api/sites/{site}").json()["record"]["links"] == ["https://example.test/a"]


def test_naming_the_addresses_with_nothing_in_them_takes_them_off(client: TestClient) -> None:
    """Null and ABSENT are different answers, and the difference is the whole shape of this write.

    Absent is a screen that knows nothing about the addresses (the form that edits the notes),
    and its silence must not delete them. Null is a caller that named the field and sent no
    addresses, which is what clearing the list looks like on the wire.
    """
    sign_in(client)
    site = _make_site(client, "Somewhere")
    client.put(f"/api/sites/{site}/details", json={"links": ["https://example.test/a"]})

    client.put(f"/api/sites/{site}/details", json={"links": None, "notes": "A note."})

    held = client.get(f"/api/sites/{site}").json()
    assert "links" not in held["record"]
    assert held["notes"] == "A note."


# --- a site is found by every name it goes by --------------------------------------------------


def test_a_sites_other_name_finds_the_site_and_not_only_its_files(client: TestClient) -> None:
    """The same half a tag has, on a site.

    A site's other names reach the FILE index (searching one finds the files that came from it),
    and they must match the SITE itself as well when it is the thing being looked for. A site
    linked to a stash-box usually carries three or four of them, so a library where searching one
    spelling finds no site is a library that is wrong in the way people notice.

    Checked through the suggester every surface reads, which is what makes this the search box, the
    filter bar and the site wall at once.
    """
    sign_in(client)
    # The other name deliberately shares NOTHING with the name on the row. A fixture whose two
    # spellings overlap cannot tell an alias match from a name match: the name would answer, the
    # row would come back, and the test would pass with the alias never consulted.
    site = _make_site(client, "Pier Nine Media")
    make_username(client, "Pier Nine Media", "someone")
    saved = client.put(f"/api/sites/{site}/details", json={"aliases": ["Sorrelcast"]})
    assert saved.status_code == 200, saved.text

    found = client.get("/api/search/suggest", params={"field": "sites", "prefix": "Sorrel"})

    assert found.status_code == 200, found.text
    offered = found.json()["matches"]
    assert [one["value"] for one in offered] == ["Pier Nine Media"]
    assert offered[0]["detail"] == "Sorrelcast", "the row has to say which spelling brought it back"


def test_a_prefix_narrows_the_wall_of_sites(client: TestClient) -> None:
    """Without a prefix on the server a picker over sites would have to fetch the whole list and
    filter it in the browser: a ceiling the moment there are more sites than a page holds, and a
    silent one. Matched without regard to case, and a wildcard typed into
    the box is an ordinary character rather than a pattern."""
    sign_in(client)
    _make_site(client, "Instagram")
    _make_site(client, "TikTok")

    def names(prefix: str) -> list[str]:
        answer = client.get("/api/sites", params={"sort": "name_az", "limit": 50, "prefix": prefix})
        assert answer.status_code == 200, answer.text
        return [one["name"] for one in answer.json()["items"]]

    assert names("") == ["Instagram", "TikTok"]
    assert names("ins") == ["Instagram"]
    assert names("_") == []


def test_the_wall_of_sites_matches_words_anywhere_in_a_name(client: TestClient) -> None:
    """`anywhere` is the People wall's option, and the box above this wall sends it: part of a
    name finds the site. The panel beside the wall counts under the same words."""
    sign_in(client)
    _make_site(client, "Instagram")
    _make_site(client, "TikTok")

    def names(**params: str) -> list[str]:
        answer = client.get("/api/sites", params={"sort": "name_az", "limit": 50, **params})
        assert answer.status_code == 200, answer.text
        return [one["name"] for one in answer.json()["items"]]

    assert names(prefix="gram") == []
    assert names(prefix="gram", anywhere="true") == ["Instagram"]

    def counted(**params: str) -> int:
        answer = client.get("/api/sites/facets", params={"facet": "created", **params})
        assert answer.status_code == 200, answer.text
        return sum(int(one["count"]) for one in answer.json()["values"])

    assert counted() == 2
    assert counted(prefix="gram", anywhere="true") == 1


def test_a_site_added_here_says_the_user_made_it_and_names_no_pass(client: TestClient) -> None:
    """Somebody typed the name and pressed the button, so the row is theirs.

    This screen goes through the SHARED upsert, which is the same one a download reaches, and
    writing the downloader's word ('sift') would make a site an admin added and a site a pasted
    address invented the same row, with the maker line on a site's page saying Sift had made
    something only a person touched. The act says who it is; the pass column stays empty, because a
    person is not a pass.
    """
    sign_in(client)
    made = _make_site(client, "Sorrelcast")

    row = read(
        db_path(client),
        "SELECT created_by_kind, created_by_via, created_by_user_id FROM sites WHERE id = ?",
        (made,),
    )[0]
    assert row["created_by_kind"] == "user"
    assert row["created_by_via"] is None
    assert row["created_by_user_id"] is not None


def test_a_sites_own_address_comes_first_when_its_links_are_written() -> None:
    """A stash-box lists another site's page about a site ahead of its home; the first link is
    what the address column gets, and the logo is matched on it."""
    from sift.slices.people.service import _own_address_first

    ordered = _own_address_first(
        [
            "https://theporndb.net/sites/marrowvale",
            "https://www.marrowvale.example/",
            "https://x.com/marrowvale",
        ],
        "Marrowvale",
    )
    assert ordered[0] == "https://www.marrowvale.example/"
    assert ordered[1:] == ["https://theporndb.net/sites/marrowvale", "https://x.com/marrowvale"]
    # A name with no word of its own changes nothing.
    assert _own_address_first(["https://a.example/", "https://b.example/"], "X") == [
        "https://a.example/",
        "https://b.example/",
    ]
