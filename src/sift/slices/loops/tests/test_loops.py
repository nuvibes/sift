# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loops of a video, and who may do what to them afterwards: a Loop reaches a user only through its
file, a Loop with no length or past the video's end is refused, its maker or an admin may change
it, and its row wears the video's heart."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from sift.kernel import wiring
from sift.slices import loops
from sift.slices.loops.service import MIN_LOOP_MS, LoopService, Refused
from sift.slices.loops.tests.conftest import (
    DURATION_MS,
    NEVER_EXISTED,
    Videos,
    db_path,
    forget,
    loop_ids,
    mark,
    read,
    share,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]


# --- making one ------------------------------------------------------------------------------


def test_a_mark_is_saved_and_carries_its_own_length(client: TestClient, videos: Videos) -> None:
    sign_in(client)
    answer = mark(client, videos.shared, 1_000, 4_000)
    assert answer.status_code == 201, answer.text
    body = answer.json()

    assert body["asset_id"] == videos.shared
    assert body["start_ms"] == 1_000
    assert body["end_ms"] == 4_000
    # The Loop's length, sent so no reader computes it a second time.
    assert body["duration_ms"] == 3_000


def test_the_service_knows_a_stretch_it_has_already_marked(
    client: TestClient, videos: Videos
) -> None:
    """The service knows a stretch of a file it has already marked."""
    sign_in(client)
    assert mark(client, videos.shared, 1_000, 4_000).status_code == 201
    service = wiring.part_of_app(cast(Any, client.app), loops.SERVICE)

    async def asked() -> tuple[bool, bool, bool]:
        return (
            await service.marked(videos.shared, 1_000, 4_000),
            await service.marked(videos.shared, 1_000, 5_000),
            await service.marked(videos.private, 1_000, 4_000),
        )

    assert asyncio.run(asked()) == (True, False, False)


def test_a_guest_may_mark_a_video_they_can_see(client: TestClient, videos: Videos) -> None:
    """A guest may make a Loop of a video they can see, as they may rate it."""
    guest = sign_in(client, "guest")
    sign_in(client)
    share(client, videos.shared, guest)

    sign_in(client, "guest")
    assert mark(client, videos.shared).status_code == 201


def test_a_video_the_caller_cannot_see_cannot_be_marked(client: TestClient, videos: Videos) -> None:
    """A video the caller cannot see is a 404, so a Loop never names a hidden file."""
    guest = sign_in(client, "guest")
    sign_in(client)
    share(client, videos.shared, guest)

    sign_in(client, "guest")
    assert mark(client, videos.private).status_code == 404
    assert mark(client, NEVER_EXISTED).status_code == 404


def test_a_mark_with_no_length_is_refused(client: TestClient, videos: Videos) -> None:
    """A Loop with no length is refused, as a double press would make."""
    sign_in(client)
    assert mark(client, videos.shared, 5_000, 5_000).status_code == 422
    assert mark(client, videos.shared, 5_000, 5_000 + MIN_LOOP_MS - 1).status_code == 422
    assert mark(client, videos.shared, 5_000, 5_000 + MIN_LOOP_MS).status_code == 201


def test_a_mark_ending_past_the_end_of_the_video_is_refused(
    client: TestClient, videos: Videos
) -> None:
    sign_in(client)
    assert mark(client, videos.shared, 1_000, DURATION_MS + 1).status_code == 422
    assert mark(client, videos.shared, 1_000, DURATION_MS).status_code == 201


# --- who is told about one --------------------------------------------------------------------


def test_a_mark_of_a_video_a_guest_cannot_see_is_not_a_row_at_all(
    client: TestClient, videos: Videos
) -> None:
    """A Loop of a video a guest cannot see is no row at all, by the join itself."""
    sign_in(client)
    on_private = mark(client, videos.private).json()["id"]
    on_shared = mark(client, videos.shared).json()["id"]

    guest = sign_in(client, "guest")
    assert loop_ids(client) == []

    sign_in(client)
    share(client, videos.shared, guest)

    sign_in(client, "guest")
    assert loop_ids(client) == [on_shared]
    assert client.get(f"/api/loops/{on_private}").status_code == 404


def test_the_wall_can_be_narrowed_to_one_video(client: TestClient, videos: Videos) -> None:
    """The wall narrowed to one video, as the player asks for its timeline."""
    sign_in(client)
    on_shared = mark(client, videos.shared).json()["id"]
    mark(client, videos.private)

    narrowed = client.get("/api/loops", params={"asset_id": videos.shared}).json()
    assert [entry["id"] for entry in narrowed["items"]] == [on_shared]
    assert narrowed["total"] == 1


# --- one opinion per video ----------------------------------------------------------------------


def test_a_marks_row_carries_the_videos_heart_and_not_one_of_its_own(
    client: TestClient, videos: Videos
) -> None:
    """A Loop's row carries its video's heart: one opinion per video."""
    sign_in(client)
    first = mark(client, videos.shared, 1_000, 4_000).json()["id"]
    second = mark(client, videos.shared, 8_000, 12_000).json()["id"]

    client.put(f"/api/assets/{videos.shared}/favorite", json={"favorite": True})
    client.put(f"/api/assets/{videos.shared}/rating", json={"rating": 5})

    rows = {entry["id"]: entry for entry in client.get("/api/loops").json()["items"]}
    for loop_id in (first, second):
        assert rows[loop_id]["favorite"] is True
        assert rows[loop_id]["rating"] == 5


def test_pinning_a_legacy_mark_pins_the_source_video(client: TestClient, videos: Videos) -> None:
    """Pinning a legacy Loop pins its source video, as every verb on its tile names `asset_id`
    (see the grid's `fileOf`). A Save as Loop clip is a file and pins itself."""
    me = sign_in(client)
    first = mark(client, videos.shared, 1_000, 4_000).json()
    second = mark(client, videos.shared, 8_000, 12_000).json()
    # A legacy Loop points into its video.
    assert first["asset_id"] == videos.shared

    assert (
        client.put(f"/api/assets/{first['asset_id']}/pin", json={"pinned": True}).status_code == 200
    )

    # The source video's row, read at the source.
    held = read(
        db_path(client),
        "SELECT pinned FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (videos.shared, me),
    )
    assert held == [{"pinned": 1}]

    # Both Loops show it.
    rows = {entry["id"]: entry for entry in client.get("/api/loops").json()["items"]}
    assert rows[first["id"]]["pinned"] is True
    assert rows[second["id"]]["pinned"] is True

    # A Loop of a video nobody pinned is not pinned.
    other = mark(client, videos.private, 1_000, 4_000).json()
    again = {entry["id"]: entry for entry in client.get("/api/loops").json()["items"]}
    assert again[other["id"]]["pinned"] is False


def test_a_marks_row_carries_what_a_tile_needs_to_draw_itself(
    client: TestClient, videos: Videos
) -> None:
    """A Loop's row carries what a tile needs: the video's shape, whether its still exists, and the
    Loop's own length."""
    sign_in(client)
    mark(client, videos.shared, 1_000, 4_000)

    row = client.get("/api/loops").json()["items"][0]
    assert row["width"] == 1920
    assert row["height"] == 1080
    # The badge is the Loop's length, not the video's.
    assert row["duration_ms"] == 3_000
    assert row["duration_ms"] != DURATION_MS
    # No still yet, so the tile shimmers rather than reading a 404.
    assert row["thumb"] is False
    # A Loop has no hover clip of its own.
    assert row["preview"] is False
    assert row["concealed"] is False
    assert row["original_filename"] == "shared.mp4"


# --- moving and removing one -------------------------------------------------------------------


def test_a_mark_is_the_makers_to_rename_and_to_remove(client: TestClient, videos: Videos) -> None:
    guest = sign_in(client, "guest")
    sign_in(client)
    share(client, videos.shared, guest)

    sign_in(client, "guest")
    loop_id = mark(client, videos.shared).json()["id"]
    assert client.put(f"/api/loops/{loop_id}", json={"name": "the good bit"}).status_code == 200
    assert client.get(f"/api/loops/{loop_id}").json()["name"] == "the good bit"
    assert forget(client, loop_id) == {"changed": 1, "skipped": 0}


def test_somebody_elses_mark_is_not_theirs_to_rename_or_remove(
    client: TestClient, videos: Videos
) -> None:
    """Somebody else's Loop is refused as missing, so the refusal never says it exists."""
    admin = sign_in(client)
    loop_id = mark(client, videos.shared).json()["id"]

    guest = sign_in(client, "guest")
    assert guest != admin
    sign_in(client)
    share(client, videos.shared, guest)

    sign_in(client, "guest")
    # They can see it.
    assert client.get(f"/api/loops/{loop_id}").status_code == 200
    assert client.put(f"/api/loops/{loop_id}", json={"name": "mine now"}).status_code == 404
    # Forgetting is a bulk write, so it is skipped, still saying nothing.
    assert forget(client, loop_id) == {"changed": 0, "skipped": 1}


def test_an_admin_may_move_and_remove_anybodys_mark(client: TestClient, videos: Videos) -> None:
    guest = sign_in(client, "guest")
    sign_in(client)
    share(client, videos.shared, guest)

    sign_in(client, "guest")
    loop_id = mark(client, videos.shared).json()["id"]

    sign_in(client)
    assert client.put(f"/api/loops/{loop_id}", json={"name": "tidied"}).status_code == 200
    assert forget(client, loop_id) == {"changed": 1, "skipped": 0}
    assert loop_ids(client) == []


def test_removing_a_mark_keeps_every_byte_of_the_video(client: TestClient, videos: Videos) -> None:
    sign_in(client)
    loop_id = mark(client, videos.shared).json()["id"]
    assert forget(client, loop_id) == {"changed": 1, "skipped": 0}
    assert client.get(f"/api/assets/{videos.shared}").status_code == 200


# --- tags ---------------------------------------------------------------------------------------


def test_tagging_a_mark_is_an_admins(client: TestClient, videos: Videos) -> None:
    """Tagging a Loop is an admin's, like every tag write."""
    guest = sign_in(client, "guest")
    sign_in(client)
    share(client, videos.shared, guest)
    tag_id = client.post("/api/tags", json={"name": "the good bit"}).json()["id"]

    sign_in(client, "guest")
    loop_id = mark(client, videos.shared).json()["id"]
    assert client.post(f"/api/loops/{loop_id}/tags", json={"tag_id": tag_id}).status_code == 403

    sign_in(client)
    assert client.post(f"/api/loops/{loop_id}/tags", json={"tag_id": tag_id}).status_code == 200
    assert [tag["id"] for tag in client.get(f"/api/loops/{loop_id}/tags").json()] == [tag_id]


def test_a_tag_reaches_a_mark_carrying_it_and_the_marks_of_a_video_carrying_it(
    client: TestClient, videos: Videos
) -> None:
    """A tag's Loops tab reaches Loops tagged themselves and Loops of tagged videos, with three Loops:
    one tagged, one untagged on the same video, one on a tagged video, so neither rule alone
    passes."""
    sign_in(client)
    tag_id = client.post("/api/tags", json={"name": "the good bit"}).json()["id"]

    tagged_mark = mark(client, videos.shared, 1_000, 4_000).json()["id"]
    plain_mark = mark(client, videos.shared, 8_000, 12_000).json()["id"]
    mark_of_a_tagged_video = mark(client, videos.private, 2_000, 5_000).json()["id"]

    assert client.post(f"/api/loops/{tagged_mark}/tags", json={"tag_id": tag_id}).status_code == 200
    assert (
        client.post(
            "/api/assets/tags", json={"asset_ids": [videos.private], "tag_ids": [tag_id]}
        ).status_code
        == 200
    )

    page = client.get("/api/loops", params={"tag": tag_id}).json()
    found = {row["id"] for row in page["items"]}
    assert found == {tagged_mark, mark_of_a_tagged_video}
    assert plain_mark not in found, "the widening let in a whole video instead of one mark"
    # The tab's number comes off the same listing.
    assert page["total"] == 2
    assert client.get(f"/api/related/tag/{tag_id}").json()["loops"] == 2


def test_the_tag_widening_cannot_show_a_mark_of_a_video_you_may_not_see(
    client: TestClient, videos: Videos
) -> None:
    """Reaching a Loop by its own tag cannot reach one of a video the guest may not see: a Loop is
    an inner join to the visible set (see `loops_query`)."""
    guest = sign_in(client, "guest")
    sign_in(client)
    tag_id = client.post("/api/tags", json={"name": "private moment"}).json()["id"]
    # A Loop with the tag on the video the guest is not shared.
    hidden_mark = mark(client, videos.private, 1_000, 4_000).json()["id"]
    assert client.post(f"/api/loops/{hidden_mark}/tags", json={"tag_id": tag_id}).status_code == 200
    assert client.get("/api/loops", params={"tag": tag_id}).json()["total"] == 1

    sign_in(client, "guest")
    assert guest
    page = client.get("/api/loops", params={"tag": tag_id}).json()
    assert page["total"] == 0, "the widening handed back a mark of a video this account cannot see"
    assert page["items"] == []
    # By id too.
    assert client.get(f"/api/loops/{hidden_mark}").status_code == 404
    assert client.get(f"/api/related/tag/{tag_id}").json()["loops"] == 0


def test_a_wall_that_is_not_a_tags_is_untouched_by_the_mark_widening(
    client: TestClient, videos: Videos
) -> None:
    """A wall with no tag in it is unchanged by the tag clause."""
    sign_in(client)
    mark(client, videos.shared, 1_000, 4_000)
    mark(client, videos.private, 2_000, 5_000)
    assert client.get("/api/loops").json()["total"] == 2
    assert client.get("/api/loops", params={"asset_id": videos.private}).json()["total"] == 1


def test_the_bars_file_filter_narrows_the_wall_to_marks_of_matching_files(
    client: TestClient, videos: Videos
) -> None:
    """The bar's file filter narrows the Loops wall with the library's engine, both signs."""
    sign_in(client)
    beach = client.post("/api/tags", json={"name": "beach"}).json()["id"]
    assert (
        client.post(
            "/api/assets/tags", json={"asset_ids": [videos.shared], "tag_ids": [beach]}
        ).status_code
        == 200
    )
    on_the_beach = mark(client, videos.shared, 1_000, 4_000).json()["id"]
    elsewhere = mark(client, videos.private, 2_000, 5_000).json()["id"]

    asked = client.get("/api/loops", params={"tags": "beach"}).json()
    assert [row["id"] for row in asked["items"]] == [on_the_beach]
    assert asked["total"] == 1
    refused = client.get("/api/loops", params={"tags": "-beach"}).json()
    assert [row["id"] for row in refused["items"]] == [elsewhere]
    assert client.get("/api/loops", params={"q": "tags:beach"}).json()["total"] == 1
    # The anchor resolves in the filtered list.
    anchored = client.get("/api/loops", params={"tags": "beach", "from": elsewhere}).json()
    assert anchored["offset"] == 0
    assert [row["id"] for row in anchored["items"]] == [on_the_beach]


def test_a_file_filter_binds_the_marks_a_tag_reaches_by_their_own_tag(
    client: TestClient, videos: Videos
) -> None:
    """A Loop reached by its own tag still passes the filter's other conditions."""
    sign_in(client)
    beach = client.post("/api/tags", json={"name": "beach"}).json()["id"]
    moment = client.post("/api/tags", json={"name": "the good bit"}).json()["id"]
    assert (
        client.post(
            "/api/assets/tags", json={"asset_ids": [videos.shared], "tag_ids": [beach]}
        ).status_code
        == 200
    )
    tagged_mark = mark(client, videos.private, 1_000, 4_000).json()["id"]
    assert client.post(f"/api/loops/{tagged_mark}/tags", json={"tag_id": moment}).status_code == 200

    # Unfiltered, the tag reaches its Loop.
    assert [
        row["id"] for row in client.get("/api/loops", params={"tag": moment}).json()["items"]
    ] == [tagged_mark]
    narrowed = client.get("/api/loops", params={"tag": moment, "tags": "beach"}).json()
    assert narrowed["items"] == [], "a mark reached by its own tag slipped past the file filter"
    assert narrowed["total"] == 0


def test_an_unknown_order_is_refused_rather_than_quietly_ignored(client: TestClient) -> None:
    """An unknown order is refused."""
    sign_in(client)
    assert client.get("/api/loops", params={"sort": "sideways"}).status_code == 422


def test_the_wall_can_be_put_in_every_order_it_offers(client: TestClient, videos: Videos) -> None:
    """Every order the bar offers is accepted, size meaning a Loop's length."""
    sign_in(client)
    mark(client, videos.shared, 1_000, 4_000)
    mark(client, videos.shared, 8_000, 30_000)
    for order in (
        "newest",
        "oldest",
        "name_az",
        "name_za",
        "largest",
        "smallest",
        "favorite",
        "rating",
        "seen",
    ):
        answer = client.get("/api/loops", params={"sort": order})
        assert answer.status_code == 200, f"{order}: {answer.text}"
        assert len(answer.json()["items"]) == 2


def test_the_size_orders_really_order_a_wall_of_marks(client: TestClient, videos: Videos) -> None:
    """The size orders really order: three Loops made middling, longest, shortest, so the newest-
    first fall-through matches neither size order."""
    sign_in(client)
    middling = mark(client, videos.shared, 0, 20_000).json()["id"]
    longest = mark(client, videos.shared, 0, 30_000).json()["id"]
    shortest = mark(client, videos.shared, 0, 5_000).json()["id"]

    def wall(order: str) -> list[str]:
        return [
            row["id"] for row in client.get("/api/loops", params={"sort": order}).json()["items"]
        ]

    # Newest first, matching neither size order.
    assert wall("newest") == [shortest, longest, middling]

    assert wall("largest") == [longest, middling, shortest]
    assert wall("smallest") == [shortest, middling, longest]


def test_a_mark_cannot_start_before_the_video_does_or_after_it_ends(
    client: TestClient, videos: Videos
) -> None:
    """A Loop cannot start before its video or end after it."""
    sign_in(client)
    assert mark(client, videos.shared, -1, 4_000).status_code == 422
    assert mark(client, videos.shared, DURATION_MS, DURATION_MS + 5_000).status_code == 422


def test_the_service_refuses_a_negative_start_even_though_the_route_cannot_send_one() -> None:
    """The service refuses a negative start that the route's model already refuses, for callers
    that skip the model."""
    with pytest.raises(Refused):
        LoopService._check(-1, 4_000, None)


def test_a_loop_tile_carries_the_view_tally_the_file_has(
    client: TestClient, videos: Videos
) -> None:
    """A Loop tile carries its file's view tally, as the shared tile reads `views`. Seeded here;
    when a sitting counts is the player's rule."""
    me = sign_in(client)
    assert mark(client, videos.shared).status_code == 201
    assert client.get("/api/loops").json()["items"][0]["views"] == 0

    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, view_count, updated_at) "
                "VALUES (?, ?, 4, 0) ON CONFLICT(asset_id, user_id) DO UPDATE SET view_count = 4",
                (videos.shared, me),
            )
        ],
    )

    assert client.get("/api/loops").json()["items"][0]["views"] == 4


#: What the shared tile (`Tile.svelte`) reads off any row. Written out, not derived, so the two row
#: shapes are held to agree.
TILE_FACTS = (
    "views",
    # The O counter, which `Tile.svelte` draws.
    "o_count",
    "hidden",
    "hidden_here",
    "unreachable",
    "shared",
    "restricted",
    "shared_here",
    "restricted_here",
    "pinned",
    "favorite",
    "rating",
)


def test_a_loop_row_and_an_asset_row_say_the_same_things_about_one_file(
    client: TestClient, videos: Videos
) -> None:
    """A Loop row and an asset row agree about one file on every fact the shared tile draws: a
    missing field draws nothing, which looks like a false fact."""
    me = sign_in(client)
    assert mark(client, videos.shared).status_code == 201
    # True on every axis, so the comparison is not all-false.
    share(client, videos.shared, me)
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state "
                "(asset_id, user_id, view_count, o_count, hidden, updated_at) "
                "VALUES (?, ?, 3, 5, 0, 0) ON CONFLICT(asset_id, user_id) DO UPDATE SET "
                "view_count = 3, o_count = 5",
                (videos.shared, me),
            ),
            (
                "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?",
                (videos.shared,),
            ),
        ],
    )

    tile = next(
        row for row in client.get("/api/assets").json()["items"] if row["id"] == videos.shared
    )
    loop = client.get("/api/loops").json()["items"][0]

    assert loop["asset_id"] == videos.shared
    assert {fact: loop.get(fact) for fact in TILE_FACTS} == {
        fact: tile.get(fact) for fact in TILE_FACTS
    }
    # The positive control.
    assert (tile["views"], tile["shared"], tile["unreachable"]) == (3, True, True)


def test_one_loop_read_on_its_own_says_what_the_wall_says(
    client: TestClient, videos: Videos
) -> None:
    """The three single-Loop routes answer the same shape the wall does."""
    me = sign_in(client)
    assert mark(client, videos.shared).status_code == 201
    share(client, videos.shared, me)
    loop_id = loop_ids(client)[0]

    alone = client.get(f"/api/loops/{loop_id}").json()

    assert alone["shared"] is True
    assert {fact: alone.get(fact) for fact in TILE_FACTS} == {
        fact: client.get("/api/loops").json()["items"][0].get(fact) for fact in TILE_FACTS
    }


def test_a_selection_of_marks_is_forgotten_in_one_request(
    client: TestClient, videos: Videos
) -> None:
    """A selection of Loops is forgotten in one request, answering as every bulk write does."""
    sign_in(client)
    for start in (1_000, 5_000, 9_000):
        assert mark(client, videos.shared, start, start + 2_000).status_code == 201
    ids = loop_ids(client)
    assert len(ids) == 3

    done = client.post("/api/loops/forget", json={"loop_ids": ids})

    assert done.status_code == 200, done.text
    assert done.json() == {
        "changed": 3,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert loop_ids(client) == []


def test_forgetting_marks_moves_no_file(client: TestClient, videos: Videos) -> None:
    """Forgetting a Loop deletes no file, Save as Loop clips included."""
    sign_in(client)
    assert mark(client, videos.shared).status_code == 201
    before = read(db_path(client), "SELECT * FROM asset_locations ORDER BY id")

    done = client.post("/api/loops/forget", json={"loop_ids": loop_ids(client)})

    # Status and count first, or a failed request would pass.
    assert done.status_code == 200, done.text
    assert done.json()["changed"] == 1
    assert loop_ids(client) == []
    assert read(db_path(client), "SELECT * FROM asset_locations ORDER BY id") == before
    assert client.get(f"/api/assets/{videos.shared}").status_code == 200


def test_a_mark_that_is_not_yours_is_skipped_and_the_rest_still_go(
    client: TestClient, videos: Videos
) -> None:
    """A Loop that is not yours is skipped, saying nothing of existence, and the rest still go."""
    sign_in(client)
    assert mark(client, videos.shared, 1_000, 3_000).status_code == 201
    ours = loop_ids(client)[0]

    done = client.post("/api/loops/forget", json={"loop_ids": [ours, NEVER_EXISTED]})

    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["changed"], body["skipped"]) == (1, 1)
    assert body["reason"] == "Sift could not find the file."
    assert body["reason_many"] == "Sift could not find the files."
    assert loop_ids(client) == []


def test_a_mark_SOMEBODY_ELSE_made_is_not_yours_to_forget(
    client: TestClient, videos: Videos
) -> None:
    """A real Loop somebody else made is skipped, as a made-up id is."""
    sign_in(client)
    assert mark(client, videos.shared, 1_000, 3_000).status_code == 201
    theirs = loop_ids(client)[0]

    guest = sign_in(client, "guest", who="two")
    share(client, videos.shared, guest)
    assert loop_ids(client) == [theirs], "the guest can SEE it, which is what makes this a test"

    done = client.post("/api/loops/forget", json={"loop_ids": [theirs]})

    assert done.status_code == 200, done.text
    assert (done.json()["changed"], done.json()["skipped"]) == (0, 1)
    assert loop_ids(client) == [theirs]


# --- opening the wall where it was left ------------------------------------------------------


def test_the_loops_wall_can_be_opened_where_it_was_left(client: TestClient, videos: Videos) -> None:
    """`from=` opens the Loops wall at a Loop (see `LOOP_SOURCE.anchored` in the client)."""
    sign_in(client)
    for start in (1_000, 2_000, 3_000, 4_000):
        assert mark(client, videos.shared, start, start + 1_000).status_code == 201

    ordered = loop_ids(client)
    assert len(ordered) == 4

    landed = client.get("/api/loops", params={"limit": 2, "from": ordered[2]}).json()

    assert [one["id"] for one in landed["items"]] == ordered[2:4]
    assert landed["offset"] == 2, "the answer says where it landed, so the pager can say so too"
    assert landed["total"] == 4, "anchoring narrows nothing"


def test_a_loop_anchor_that_resolves_to_nothing_serves_the_first_page(
    client: TestClient, videos: Videos
) -> None:
    """An anchor resolving to nothing serves the first page, whether hidden or never made."""
    sign_in(client)
    for start in (1_000, 2_000):
        assert mark(client, videos.shared, start, start + 1_000).status_code == 201

    ordered = loop_ids(client)
    landed = client.get("/api/loops", params={"limit": 2, "from": NEVER_EXISTED}).json()

    assert landed["offset"] == 0
    assert [one["id"] for one in landed["items"]] == ordered


# --- the search box ---------------------------------------------------------------------------


def test_the_wall_is_searched_by_the_marks_own_name_in_any_case(
    client: TestClient, videos: Videos
) -> None:
    """`called` finds a Loop by its own name, anywhere, in any case; a blank box filters nothing."""
    sign_in(client)
    walk = mark(client, videos.shared, 1_000, 2_000, name="Beach walk").json()["id"]
    night = mark(client, videos.shared, 3_000, 4_000, name="the BEACH at night").json()["id"]
    mark(client, videos.shared, 5_000, 6_000, name="Hill top")
    mark(client, videos.shared, 7_000, 8_000)

    found = client.get("/api/loops", params={"called": "beach"}).json()
    assert sorted(one["id"] for one in found["items"]) == sorted([walk, night])
    assert found["total"] == 2

    assert client.get("/api/loops", params={"called": "%"}).json()["total"] == 0
    assert client.get("/api/loops", params={"called": "   "}).json()["total"] == 4


def test_the_wall_is_searched_by_the_name_of_the_file_a_mark_is_cut_from(
    client: TestClient, videos: Videos
) -> None:
    """An unnamed Loop is found by its file's names, original and current."""
    sign_in(client)
    nameless = mark(client, videos.shared, 1_000, 2_000).json()["id"]
    named = mark(client, videos.shared, 3_000, 4_000, name="Hill top").json()["id"]
    elsewhere = mark(client, videos.private, 1_000, 2_000).json()["id"]
    write(
        db_path(client),
        [
            (
                "UPDATE asset_locations SET filename = ? WHERE asset_id = ?",
                ("harbour-evening-from-24s.mp4", videos.private),
            ),
            # Renamed since it arrived, so each name matches one reading only.
            (
                "UPDATE asset_locations SET filename = ? WHERE asset_id = ?",
                ("clip-0001.mp4", videos.shared),
            ),
        ],
    )

    arrived = client.get("/api/loops", params={"called": "SHARED.mp4"}).json()
    assert sorted(one["id"] for one in arrived["items"]) == sorted([nameless, named])

    on_disk = client.get("/api/loops", params={"called": "harbour-evening"}).json()
    assert [one["id"] for one in on_disk["items"]] == [elsewhere]


def test_a_searched_wall_opens_where_it_was_left_within_what_the_search_found(
    client: TestClient, videos: Videos
) -> None:
    """`from=` resolves within the searched list."""
    sign_in(client)
    for start in (1_000, 2_000, 3_000):
        made = mark(client, videos.shared, start, start + 500, name=f"beach {start}")
        assert made.status_code == 201
    mark(client, videos.shared, 5_000, 6_000, name="hill")

    ordered = [
        one["id"] for one in client.get("/api/loops", params={"called": "beach"}).json()["items"]
    ]
    landed = client.get(
        "/api/loops", params={"called": "beach", "limit": 2, "from": ordered[2]}
    ).json()

    assert landed["offset"] == 2
    assert [one["id"] for one in landed["items"]] == ordered[2:]
    assert landed["total"] == 3
