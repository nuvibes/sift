# SPDX-License-Identifier: AGPL-3.0-or-later
"""A song as a thing of its own, over HTTP: Hidden and sharing, its artists, and a Music tab.

The claims worth breaking the build over: a song somebody hid is gone from every read of theirs
(the wall, the page, its cover, its History, the files that carry it, the Music tab of every other
page) until the vault is open, and the panel that says why a file is hidden names it; a song shared
with a guest hands over the files that carry it; a song credits its artists in order, as rows the
Music wall narrows, sorts and counts by, a rename of an artist reaches every song and the song's
name is never touched; and every page but a Photo Set's counts the songs its files carry.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.songs.models import MAX_SONG_NAME
from sift.slices.songs.tests.conftest import (
    Clips,
    db_path,
    named_by_acoustid,
    read,
    share,
    sign_in,
)
from sift.testing.auth import TEST_PIN, hide_for_caller

pytestmark = [pytest.mark.integration]


def _ids(client: TestClient, **params: object) -> list[str]:
    answer = client.get("/api/songs", params=params)
    assert answer.status_code == 200, answer.text
    return [entry["id"] for entry in answer.json()["items"]]


def _files(client: TestClient, song_id: str) -> list[str]:
    answer = client.get("/api/assets", params={"songs": song_id, "limit": 50})
    assert answer.status_code == 200, answer.text
    return sorted(item["id"] for item in answer.json()["items"])


# --- Hidden ------------------------------------------------------------------------------------


def test_a_hidden_song_is_gone_from_every_read_and_takes_its_files_with_it(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue - Marla Quist")
    named_by_acoustid(client, clips.second, "Blue - Marla Quist")
    assert _files(client, song) == sorted([clips.first, clips.second])
    assert client.put(f"/api/songs/{song}/vault", json={"vault": True}).status_code == 204
    # The vault is shut: the song is no row on the wall, no page, no cover, no History, no tab.
    assert _ids(client) == []
    for address in ("", "/cover", "/history", "/made-by"):
        assert client.get(f"/api/songs/{song}{address}").status_code == 404, address
    assert "history" not in client.get(f"/api/related/song/{song}").json()
    # And the files that carry it are concealed with it, as a hidden Photo Set's pictures are.
    assert _files(client, song) == []
    # Sealed while the vault is shut: a 204 where a 404 belongs would say the song is there.
    assert client.put(f"/api/songs/{song}/vault", json={"vault": False}).status_code == 404
    # Opened, the song is back with its files, and the panel that says why a file is hidden (which
    # answers only while Hidden is open) names the song.
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    assert _ids(client) == [song]
    assert _files(client, song) == sorted([clips.first, clips.second])
    why = client.get(
        "/api/sharing/hidden-by", params={"object_type": "item", "object_id": clips.first}
    ).json()
    assert [(one["source_type"], one["source_id"]) for one in why] == [("song", song)]
    rows = read(
        db_path(client),
        "SELECT hidden FROM song_user_state WHERE song_id = ?",
        (song,),
    )
    assert rows == [{"hidden": 1}]


def test_a_hidden_song_leaves_the_music_tab_of_every_other_page(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue - Marla Quist")
    collection = client.post("/api/collections", json={"name": "Evening"}).json()["id"]
    client.post(f"/api/collections/{collection}/items", json={"asset_ids": [clips.first]})
    assert client.get(f"/api/related/collection/{collection}").json()["songs"] == 1
    assert _ids(client, collection=collection) == [song]
    hide_for_caller(client, "song", song)
    assert client.get(f"/api/related/collection/{collection}").json()["songs"] == 0
    assert _ids(client, collection=collection) == []


# --- sharing -----------------------------------------------------------------------------------


def test_a_song_shared_with_a_guest_hands_over_the_files_that_carry_it(
    client: TestClient, clips: Clips
) -> None:
    guest = sign_in(client, "guest")
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue - Marla Quist")
    named_by_acoustid(client, clips.second, "Blue - Marla Quist")
    body = {"object_type": "song", "object_id": song, "subject_user_id": guest, "effect": "share"}
    shared = client.put("/api/sharing", json=body)
    assert shared.status_code == 200, shared.text
    assert [one["subject_user_id"] for one in shared.json()] == [guest]
    # The sharing facet counts it, for an admin.
    facet = client.get("/api/songs/facets", params={"facet": "sharing"}).json()
    assert facet["values"] == [{"value": "shared", "count": 1, "label": None}]
    sign_in(client, "guest")
    assert _ids(client) == [song]
    assert _files(client, song) == sorted([clips.first, clips.second])
    # A file the share does not reach stays out of sight.
    assert client.get(f"/api/assets/{clips.third}").status_code == 404


def test_a_deleted_song_takes_its_grants_with_it(client: TestClient, clips: Clips) -> None:
    guest = sign_in(client, "guest")
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    body = {"object_type": "song", "object_id": song, "subject_user_id": guest, "effect": "share"}
    assert client.put("/api/sharing", json=body).status_code == 200
    assert client.delete(f"/api/songs/{song}").status_code == 204
    left = read(db_path(client), "SELECT id FROM acl_grants WHERE object_type = 'song'")
    assert left == []


# --- artists -----------------------------------------------------------------------------------


def test_a_song_credits_its_artists_in_order_and_keeps_its_name(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    blue = named_by_acoustid(client, clips.first, "Blue - Marla Quist, Odo Venn")
    green = named_by_acoustid(client, clips.second, "Green - Odo Venn", recording="rec-2")
    answer = client.put(
        f"/api/songs/{blue}/artists", json={"names": ["Marla Quist", "Odo Venn", "odo venn"]}
    )
    assert answer.status_code == 200, answer.text
    assert [one["name"] for one in answer.json()["artists"]] == ["Marla Quist", "Odo Venn"]
    assert answer.json()["name"] == "Blue - Marla Quist, Odo Venn"
    client.put(f"/api/songs/{green}/artists", json={"names": ["Odo Venn"]})
    odo = next(one["id"] for one in answer.json()["artists"] if one["name"] == "Odo Venn")
    # The wall narrows by one artist, sorts by the first credited, and counts along them.
    assert sorted(_ids(client, artists=odo)) == sorted([blue, green])
    counted = client.get("/api/songs/facets", params={"facet": "artists"}).json()["values"]
    assert {(one["label"], one["count"]) for one in counted} == {
        ("Marla Quist", 1),
        ("Odo Venn", 2),
    }
    assert _ids(client, sort="artist") == [blue, green]
    # The History line says what changed.
    lines = [line["what"] for line in client.get(f"/api/songs/{blue}/history").json()]
    assert lines[-1] == "You edited the artists", lines


def test_an_artist_renamed_reaches_every_song_and_an_uncredited_artist_goes(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    blue = named_by_acoustid(client, clips.first, "Blue")
    green = named_by_acoustid(client, clips.second, "Green", recording="rec-2")
    client.put(f"/api/songs/{blue}/artists", json={"names": ["Odo Ven"]})
    client.put(f"/api/songs/{green}/artists", json={"names": ["Odo Venn", "Ilsa Moor"]})
    typo = client.get(f"/api/songs/{blue}").json()["artists"][0]["id"]
    # Renamed to a name another artist has: the two are one artist, credited once on each song.
    assert client.put(f"/api/artists/{typo}", json={"name": "Odo Venn"}).status_code == 204
    names = {one["name"] for one in client.get(f"/api/songs/{blue}").json()["artists"]}
    assert names == {"Odo Venn"}
    # The box finds a song by an artist it credits, though the song's name does not say her.
    assert _ids(client, prefix="ilsa", anywhere="true") == [green]
    # Taken off the only song crediting her, an artist is gone.
    client.put(f"/api/songs/{green}/artists", json={"names": ["Odo Venn"]})
    left = read(db_path(client), "SELECT name FROM artists ORDER BY name")
    assert left == [{"name": "Odo Venn"}]
    # An artist this viewer may be shown no song of answers what one that never was answers.
    assert client.put(f"/api/artists/{typo}", json={"name": "Anything"}).status_code == 404


def test_an_artists_name_longer_than_a_songs_is_refused(client: TestClient, clips: Clips) -> None:
    """An artist's name is held to the length a song's name is, and a refused list leaves the
    song's artists as they were."""
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    client.put(f"/api/songs/{song}/artists", json={"names": ["Odo Venn"]})
    long = client.put(f"/api/songs/{song}/artists", json={"names": ["x" * (MAX_SONG_NAME + 1)]})
    assert long.status_code == 422
    assert [one["name"] for one in client.get(f"/api/songs/{song}").json()["artists"]] == [
        "Odo Venn"
    ]


def test_only_an_admin_credits_an_artist(client: TestClient, clips: Clips) -> None:
    guest = sign_in(client, "guest")
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    share(client, clips.first, guest)
    sign_in(client, "guest")
    assert client.put(f"/api/songs/{song}/artists", json={"names": ["X"]}).status_code == 403


# --- the Music tab on every page, and a song's own tabs ---------------------------------------


def test_a_songs_page_counts_every_tab_but_photo_sets(client: TestClient, clips: Clips) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    collection = client.post("/api/collections", json={"name": "Evening"}).json()["id"]
    client.post(f"/api/collections/{collection}/items", json={"asset_ids": [clips.first]})
    counts = client.get(f"/api/related/song/{song}").json()
    assert counts["files"] == 1 and counts["collections"] == 1 and counts["loops"] == 0
    assert counts["photo_sets"] is None and counts["songs"] is None
    # The Collections wall narrowed to the song is the tab's own list.
    listed = client.get("/api/collections", params={"song": song}).json()["items"]
    assert [one["id"] for one in listed] == [collection]
    # And the collection's card counts the song it holds.
    card = client.get("/api/collections").json()["items"][0]["counts"]
    assert card["songs"] == 1


def test_a_song_picked_on_a_music_tab_narrows_the_files_tab_of_every_page(
    client: TestClient, clips: Clips
) -> None:
    """A Collection's Files tab takes `songs` as it takes people and tags; a person's, a tag's and
    a Site's Files tab is the files wall, whose own grammar takes `songs` already."""
    sign_in(client)
    blue = named_by_acoustid(client, clips.first, "Blue")
    collection = client.post("/api/collections", json={"name": "Evening"}).json()["id"]
    client.post(
        f"/api/collections/{collection}/items",
        json={"asset_ids": [clips.first, clips.second]},
    )
    items = client.get(f"/api/collections/{collection}/items", params={"songs": blue})
    assert items.status_code == 200, items.text
    assert [one["id"] for one in items.json()["items"]] == [clips.first]
    tag = client.post("/api/tags", json={"name": "dusk"}).json()["id"]
    client.post(
        "/api/assets/tags", json={"asset_ids": [clips.first, clips.second], "tag_ids": [tag]}
    )
    narrowed = client.get("/api/assets", params={"tags": tag, "songs": blue, "limit": 50})
    assert [one["id"] for one in narrowed.json()["items"]] == [clips.first]
