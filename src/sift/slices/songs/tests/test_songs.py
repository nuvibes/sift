# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Songs page and a song's own page, over HTTP.

The claims worth breaking the build over: a song is seen through its files (a guest given none of
them is shown no song, and a file in Hidden does not count while it is shut); renaming, merging and
deleting a song moves every file's Music field with it and never a file on disk; a file is put on a
song and taken off it by hand; every write is on the song's History in the family of words a song's
naming is said in; the `songs:` filter, its `song:` spelling and `enriched:acoustid` find the files;
and a song that never existed answers exactly what one this viewer may not see answers.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import wiring
from sift.kernel.db import Database
from sift.slices.songs.tests.conftest import (
    NEVER_EXISTED,
    Clips,
    db_path,
    field_of,
    named_by_acoustid,
    read,
    run,
    share,
    sign_in,
)
from sift.testing.auth import hide_for_caller
from sift.testing.library import a_png

pytestmark = [pytest.mark.integration]


def _ids(client: TestClient, **params: object) -> list[str]:
    answer = client.get("/api/songs", params=params)
    assert answer.status_code == 200, answer.text
    return [entry["id"] for entry in answer.json()["items"]]


def _files_of(client: TestClient, song_id: str) -> list[str]:
    """A song's Files tab, read the way the screen reads it: the files wall, narrowed."""
    answer = client.get("/api/assets", params={"songs": song_id, "limit": 50})
    assert answer.status_code == 200, answer.text
    return sorted(item["id"] for item in answer.json()["items"])


def _history(client: TestClient, song_id: str) -> list[str]:
    answer = client.get(f"/api/songs/{song_id}/history")
    assert answer.status_code == 200, answer.text
    return [line["what"] for line in answer.json()]


# --- the wall and the page ------------------------------------------------------------------


def test_a_song_acoustid_named_is_on_the_wall_with_its_files_counted(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue - Marla Quist")
    listed = client.get("/api/songs").json()["items"]
    assert [(one["id"], one["name"], one["item_count"]) for one in listed] == [
        (song, "Blue - Marla Quist", 1)
    ]
    # No cover chosen: the music glyph is drawn, so nothing names a picture.
    assert listed[0]["cover_asset_id"] is None and listed[0]["cover_upload_id"] is None
    # The card's cells: the marks, who, what and where its files reach, and the Collections.
    assert set(listed[0]["counts"]) == {"loops", "people", "tags", "sites", "collections"}
    page = client.get(f"/api/songs/{song}").json()
    assert (page["recording_id"], page["item_count"]) == ("rec-1", 1)
    assert client.get(f"/api/songs/{song}/made-by").json()["via"] == "music_lookup"
    # The file's record opens its song.
    assert client.get(f"/api/assets/{clips.first}").json()["song_id"] == song
    assert _history(client, song)[-1] == "Sift named this song on first.mp4 from AcoustID"


def test_a_guest_sees_a_song_only_through_a_file_they_may_see(
    client: TestClient, clips: Clips
) -> None:
    guest = sign_in(client, "guest")
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue - Marla Quist")
    sign_in(client, "guest")
    assert _ids(client) == []
    # Denied and missing are one answer.
    assert client.get(f"/api/songs/{song}").status_code == 404
    assert client.get(f"/api/songs/{NEVER_EXISTED}").status_code == 404
    assert client.get(f"/api/songs/{song}/history").status_code == 404
    share(client, clips.first, guest)
    assert _ids(client) == [song]
    # A guest writes nothing to a song: it is everybody's.
    assert client.put(f"/api/songs/{song}", json={"name": "Mine"}).status_code == 403
    # Their own heart, though.
    assert client.put(f"/api/songs/{song}/favorite", json={"favorite": True}).json() == {
        "favorite": True,
        "rating": None,
    }


def test_a_file_in_hidden_is_not_counted_while_the_vault_is_shut(
    client: TestClient, clips: Clips
) -> None:
    guest = sign_in(client, "guest")
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue - Marla Quist")
    named_by_acoustid(client, clips.second, "Blue - Marla Quist")
    for one in (clips.first, clips.second):
        share(client, one, guest)
    sign_in(client, "guest")
    assert client.get(f"/api/songs/{song}").json()["item_count"] == 2
    hide_for_caller(client, "asset", clips.second)
    assert client.get(f"/api/songs/{song}").json()["item_count"] == 1
    hide_for_caller(client, "asset", clips.first)
    # Every file it is on is in Hidden: nothing names it while the vault is shut.
    assert _ids(client) == []


# --- what an admin does to one ----------------------------------------------------------------


def test_renaming_a_song_renames_every_files_music_field_and_says_so(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    named_by_acoustid(client, clips.second, "Blue")
    answer = client.put(f"/api/songs/{song}", json={"name": "Blue - Marla Quist"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["name"] == "Blue - Marla Quist"
    assert {field_of(client, one) for one in (clips.first, clips.second)} == {"Blue - Marla Quist"}
    # Its own page says "it", as every entity's own page does.
    assert "You renamed it from Blue" in _history(client, song)


def test_a_file_put_on_a_song_by_hand_moves_from_its_other_song_and_comes_off_again(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    blue = named_by_acoustid(client, clips.first, "Blue", "rec-1")
    red = named_by_acoustid(client, clips.second, "Red", "rec-2")
    answer = client.post(f"/api/songs/{blue}/files", json={"asset_ids": [clips.second]})
    assert answer.json()["changed"] == 1
    assert _files_of(client, blue) == sorted([clips.first, clips.second])
    assert _files_of(client, red) == []
    assert field_of(client, clips.second) == "Blue"
    # Again: already on it, nothing moves.
    again = client.post(f"/api/songs/{blue}/files", json={"asset_ids": [clips.second]})
    assert again.json()["changed"] == 0
    off = client.post(
        f"/api/songs/{blue}/files", params={"remove": "true"}, json={"asset_ids": [clips.second]}
    )
    assert off.json()["changed"] == 1
    assert field_of(client, clips.second) is None
    lines = _history(client, blue)
    assert "You named this song on second.mp4" in lines
    assert "You removed this song from second.mp4" in lines


def test_merging_songs_moves_their_files_and_keeps_the_recording(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    typed = client.post("/api/songs", json={"name": "Blue (typed)"}).json()["id"]
    blue = named_by_acoustid(client, clips.first, "Blue", "rec-1")
    client.post(f"/api/songs/{typed}/files", json={"asset_ids": [clips.second]})
    weighed = client.post("/api/songs/weigh-merge", json={"into": typed, "songs": [blue]})
    assert weighed.json() == {"into_name": "Blue (typed)", "from_names": ["Blue"], "files": 1}
    merged = client.post("/api/songs/merge", json={"into": typed, "songs": [typed, blue]})
    assert merged.status_code == 200, merged.text
    assert client.get(f"/api/songs/{blue}").status_code == 404
    kept = client.get(f"/api/songs/{typed}").json()
    assert (kept["item_count"], kept["recording_id"]) == (2, "rec-1")
    assert field_of(client, clips.first) == "Blue (typed)"
    # One song cannot be merged into itself.
    assert (
        client.post("/api/songs/merge", json={"into": typed, "songs": [typed]}).status_code == 409
    )


def test_deleting_a_song_keeps_its_files_and_empties_their_music_field(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    assert client.delete(f"/api/songs/{song}").status_code == 204
    assert client.get(f"/api/songs/{song}").status_code == 404
    assert field_of(client, clips.first) is None
    assert read(db_path(client), "SELECT id FROM assets WHERE id = ?", (clips.first,))
    # The fingerprint the lookup kept is the file's, and stays.
    assert client.get(f"/api/assets/{clips.first}").status_code == 200


def test_notes_a_cover_a_pin_and_stars_are_kept(client: TestClient, clips: Clips) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    assert client.put(f"/api/songs/{song}/notes", json={"notes": "Sung live"}).json()["notes"] == (
        "Sung live"
    )
    covered = client.put(f"/api/songs/{song}/cover", json={"asset_id": clips.first})
    assert covered.json()["cover_asset_id"] == clips.first
    assert client.put(f"/api/songs/{song}/pin", json={"pinned": True}).json() == {"pinned": True}
    assert client.put(f"/api/songs/{song}/rating", json={"rating": 8}).json()["rating"] == 8
    assert client.get("/api/songs").json()["items"][0]["pinned"] is True


def test_a_wall_asked_from_a_song_starts_at_it_and_an_unknown_order_is_refused(
    client: TestClient, clips: Clips
) -> None:
    """The way back to a page names the song it began at, so the wall opens on that card again;
    an order the wall does not offer is refused rather than read as the default."""
    sign_in(client)
    for asset_id, name, recording in zip(
        clips.every, ("Amber", "Blue", "Coral"), ("rec-1", "rec-2", "rec-3"), strict=True
    ):
        named_by_acoustid(client, asset_id, name, recording)
    by_name = _ids(client, sort="name_az")
    assert _ids(client, sort="name_az", **{"from": by_name[1]}) == by_name[1:]
    # A song gone since: the page it was on.
    assert _ids(client, sort="name_az", near=2, **{"from": NEVER_EXISTED}) == by_name[2:]
    assert client.get("/api/songs", params={"sort": "loudest"}).status_code == 422


def test_a_song_drawn_from_a_moment_of_a_file_asks_for_that_still(
    app: FastAPI, client: TestClient, clips: Clips
) -> None:
    asked: list[tuple[str, int]] = []

    class _Stills:
        async def wants_still(self, asset_id: str, at_ms: int) -> None:
            asked.append((asset_id, at_ms))

    wiring.provide(app, wiring.STILLS, _Stills())
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    covered = client.put(
        f"/api/songs/{song}/cover", json={"asset_id": clips.first, "at_ms": 61_500}
    )
    assert covered.status_code == 200, covered.text
    assert covered.json()["cover_at_ms"] == 61_500
    assert asked == [(clips.first, 61_500)]
    # Without a moment there is no still to make.
    client.put(f"/api/songs/{song}/cover", json={"asset_id": clips.first})
    assert asked == [(clips.first, 61_500)]


def test_a_picture_from_outside_the_library_is_a_songs_cover_and_is_served(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    assert client.get(f"/api/songs/{song}/cover").status_code == 404
    sent = client.post(
        f"/api/songs/{song}/cover-picture", files={"file": ("chosen.png", a_png(), "image/png")}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["cover_upload_id"] is not None
    served = client.get(f"/api/songs/{song}/cover")
    assert served.status_code == 200
    assert served.content.startswith(b"\xff\xd8\xff")


def test_a_song_with_no_files_is_renamed_merged_and_deleted_naming_nothing_to_the_index(
    client: TestClient, clips: Clips
) -> None:
    """A song somebody typed and put on nothing has no file whose Music field moves: every write
    to it still lands, and a press on files this person may not act on changes nothing."""
    sign_in(client)
    typed = client.post("/api/songs", json={"name": "Typed"}).json()["id"]
    other = client.post("/api/songs", json={"name": "Other"}).json()["id"]
    assert client.put(f"/api/songs/{typed}", json={"name": "typed again"}).json()["name"] == (
        "typed again"
    )
    merged = client.post("/api/songs/merge", json={"into": typed, "songs": [other]})
    assert merged.status_code == 200, merged.text
    assert client.get(f"/api/songs/{other}").status_code == 404
    put = client.post(f"/api/songs/{typed}/files", json={"asset_ids": [NEVER_EXISTED]})
    assert put.status_code == 200, put.text
    assert put.json()["changed"] == 0
    assert client.delete(f"/api/songs/{typed}").status_code == 204
    assert client.get(f"/api/songs/{typed}").status_code == 404


def test_a_song_that_is_not_there_is_written_nowhere(client: TestClient) -> None:
    """Every write of the service answers "not there" for a song that is not, and writes nothing:
    the route resolves the song first, and a song deleted between the two lands here."""
    from sift.kernel.ledger import Actor
    from sift.slices.songs.service import Changed, Credited, SongService

    sign_in(client)
    actor = Actor.user("someone")

    async def work(database: Database) -> list[object]:
        service = SongService(database)
        return [
            await service.get(NEVER_EXISTED),
            await service.rename(NEVER_EXISTED, "Blue", actor=actor),
            await service.set_notes(NEVER_EXISTED, "Sung live", actor=actor),
            await service.set_cover(NEVER_EXISTED, None, actor=actor),
            await service.delete(NEVER_EXISTED, actor=actor),
            await service.add(NEVER_EXISTED, ["a"], actor=actor),
            await service.remove(NEVER_EXISTED, ["a"], actor=actor),
            await service.weigh_merge(NEVER_EXISTED, ["b"]),
            await service.merge(NEVER_EXISTED, ["b"], actor=actor),
            await service.set_artists(NEVER_EXISTED, ["Odo Venn"], actor=actor),
            await service.rename_artist(NEVER_EXISTED, "Odo Venn", actor=actor),
        ]

    assert run(db_path(client), work) == [
        None,
        Changed(done=False),
        False,
        False,
        Changed(done=False),
        [],
        [],
        None,
        None,
        Credited(done=False),
        False,
    ]
    assert read(db_path(client), "SELECT id FROM workbench_decisions WHERE verb <> 'added'") == []


def test_a_merge_keeps_the_recording_the_note_and_the_artists_of_the_first_song_that_has_them(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    kept = client.post("/api/songs", json={"name": "Kept"}).json()["id"]
    bare = client.post("/api/songs", json={"name": "Bare"}).json()["id"]
    full = named_by_acoustid(client, clips.first, "Full", "rec-7")
    client.put(f"/api/songs/{full}/notes", json={"notes": "Sung live"})
    client.put(f"/api/songs/{full}/artists", json={"names": ["Odo Venn"]})

    merged = client.post("/api/songs/merge", json={"into": kept, "songs": [bare, full]})

    assert merged.status_code == 200, merged.text
    now = client.get(f"/api/songs/{kept}").json()
    assert (now["recording_id"], now["notes"]) == ("rec-7", "Sung live")
    assert [one["name"] for one in now["artists"]] == ["Odo Venn"]


def test_a_merge_into_a_song_that_credits_artists_keeps_its_own(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    kept = client.post("/api/songs", json={"name": "Kept"}).json()["id"]
    going = named_by_acoustid(client, clips.first, "Going", "rec-8")
    client.put(f"/api/songs/{kept}/artists", json={"names": ["Marla Quist"]})
    client.put(f"/api/songs/{going}/artists", json={"names": ["Odo Venn"]})

    client.post("/api/songs/merge", json={"into": kept, "songs": [going]})

    artists = client.get(f"/api/songs/{kept}").json()["artists"]
    assert [one["name"] for one in artists] == ["Marla Quist"]


def test_the_same_artists_again_and_an_artist_renamed_to_its_own_name_say_nothing(
    client: TestClient, clips: Clips
) -> None:
    """A write that changed nothing writes no History line: the same list of artists set again,
    an artist renamed to the name it has, the song renamed to its own name, and a file taken off
    a song it is not on."""
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue")
    credited = client.put(f"/api/songs/{song}/artists", json={"names": ["Odo Venn"]}).json()
    before = _history(client, song)
    again = client.put(f"/api/songs/{song}/artists", json={"names": ["Odo Venn"]})
    assert again.status_code == 200
    odo = credited["artists"][0]["id"]
    assert client.put(f"/api/artists/{odo}", json={"name": "Odo Venn"}).status_code == 204
    assert client.put(f"/api/songs/{song}", json={"name": " Blue "}).json()["name"] == "Blue"
    off = client.post(
        f"/api/songs/{song}/files", params={"remove": "true"}, json={"asset_ids": [clips.second]}
    )
    assert off.json()["changed"] == 0
    assert _history(client, song) == before


def test_the_wall_is_counted_along_its_facets(client: TestClient, clips: Clips) -> None:
    sign_in(client)
    named_by_acoustid(client, clips.first, "Blue")
    created = client.get("/api/songs/facets", params={"facet": "created"}).json()
    assert created["values"] == [{"value": "music_lookup", "count": 1, "label": None}]
    assert client.get("/api/songs/facets", params={"facet": "tags"}).status_code == 422
    assert _ids(client, created="me") == []


# --- finding the files --------------------------------------------------------------------------


def test_the_filters_find_a_songs_files_by_id_by_name_and_by_acoustid(
    client: TestClient, clips: Clips
) -> None:
    sign_in(client)
    song = named_by_acoustid(client, clips.first, "Blue - Marla Quist")
    client.post(f"/api/songs/{song}/files", json={"asset_ids": [clips.second]})

    def found(query: str) -> list[str]:
        answer = client.get("/api/assets", params={"q": query, "limit": 50})
        assert answer.status_code == 200, answer.text
        return sorted(item["id"] for item in answer.json()["items"])

    assert found(f"songs:{song}") == sorted([clips.first, clips.second])
    assert found('song:"Blue - Marla Quist"') == sorted([clips.first, clips.second])
    # Enriched by AcoustID is the file AcoustID named, never the one put on the song by hand.
    assert found("enriched:acoustid") == [clips.first]
    assert found("-songs") == [clips.third]
