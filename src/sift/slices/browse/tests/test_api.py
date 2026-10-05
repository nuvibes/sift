# SPDX-License-Identifier: AGPL-3.0-or-later
"""The grid over HTTP, as each kind of user: mostly what does not come back, since one row too many
is somebody seeing a file never shared with them."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import AssetFilter, Concealment, Role, Viewer
from sift.kernel.access.repository.asset_orders import ordering_for
from sift.kernel.access.repository.assets import DEFAULT_SORT, RELEVANCE, SIMILARITY
from sift.kernel.content import DerivativeKind
from sift.kernel.jobs.failure_words import VERDICT_WORDS
from sift.kernel.mp4 import NEEDS_REPAIR_BYTES
from sift.slices.auth import current_viewer
from sift.slices.browse.tests.conftest import (
    Library,
    db_path,
    give_derivative,
    share,
    sign_in,
    write,
)
from sift.slices.performance import REPAIR_PLAYBACK_KEY
from sift.testing.auth import TEST_PIN, give_pin, hide_for_caller

pytestmark = [pytest.mark.integration]


# --- who sees what -------------------------------------------------------------------


def test_the_pin_orders_a_wall_that_asks_for_it_and_no_other(
    client: TestClient, library: Library
) -> None:
    """A pin floats a file only on a wall that honours one, asserted both ways from one pin."""
    sign_in(client, "admin")
    # The one the default order puts LAST, so floating it is the only thing that can move it.
    plain = [item["id"] for item in client.get("/api/assets").json()["items"]]
    assert len(plain) == 2
    last = plain[-1]

    assert client.put(f"/api/assets/{last}/pin", json={"pinned": True}).status_code == 200

    # Browse: unchanged. This is what the parameter exists for.
    assert [item["id"] for item in client.get("/api/assets").json()["items"]] == plain

    # A wall that curates: the pinned one comes first.
    floated = client.get("/api/assets", params={"pinned_first": True}).json()["items"]
    assert [item["id"] for item in floated] == [last, plain[0]]
    assert floated[0]["pinned"] is True

    # And the anchor agrees with the page it is an anchor into, or a link lands in the wrong place.
    anchored = client.get("/api/assets", params={"pinned_first": True, "from": last}).json()[
        "items"
    ]
    assert anchored[0]["id"] == last


def test_the_pins_are_read_first_and_the_rest_of_the_page_walks_the_index(
    client: TestClient, library: Library
) -> None:
    """Pins are read on their own and the index walk fills the page after them, spliced correctly
    across a page boundary with the whole total on both pages."""
    sign_in(client, "admin")
    plain = [item["id"] for item in client.get("/api/assets").json()["items"]]
    last = plain[-1]
    assert client.put(f"/api/assets/{last}/pin", json={"pinned": True}).status_code == 200

    first = client.get("/api/assets", params={"pinned_first": True, "limit": 1}).json()
    second = client.get(
        "/api/assets", params={"pinned_first": True, "limit": 1, "offset": 1}
    ).json()

    assert [item["id"] for item in first["items"]] == [last]
    assert [item["id"] for item in second["items"]] == [plain[0]]
    assert first["total"] == second["total"] == len(plain)
    # A wall with nothing pinned is the plain index walk, in the plain order.
    assert client.put(f"/api/assets/{last}/pin", json={"pinned": False}).status_code == 200
    unpinned = client.get("/api/assets", params={"pinned_first": True}).json()["items"]
    assert [item["id"] for item in unpinned] == plain


def test_a_page_narrowed_by_a_value_nobody_could_act_on_says_so(
    client: TestClient, library: Library
) -> None:
    """The wall is empty on purpose; the caption must not say the library holds no such file."""
    sign_in(client, "admin")
    page = client.get("/api/assets", params={"q": "rating:4+x"}).json()
    assert page["total"] == 0 and page["items"] == []
    assert page["problems"] == [
        {"field": "rating", "value": "4+x", "reason": "a rating is a number of stars"}
    ]
    assert client.get("/api/assets").json()["problems"] == []


def test_the_admin_sees_the_whole_library(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    body = client.get("/api/assets").json()

    assert {item["id"] for item in body["items"]} == {library.shared, library.private}
    assert body["total"] == 2


def test_a_guest_sees_nothing_until_something_is_shared(
    client: TestClient, library: Library
) -> None:
    """Default-deny, and it is the default because files arrive on their own. Anything else would
    make every newly imported file guest-visible before anyone had looked at it."""
    sign_in(client, "guest")
    body = client.get("/api/assets").json()

    assert body["items"] == []
    assert body["total"] == 0


def test_a_guest_sees_only_what_was_shared_and_the_total_agrees(
    client: TestClient, library: Library
) -> None:
    """A guest sees only what was shared and the total agrees: filtering is in the database."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    body = client.get("/api/assets").json()
    assert [item["id"] for item in body["items"]] == [library.shared]
    assert body["total"] == 1, "the total counted rows the viewer cannot see"


def test_the_page_can_be_ordered_and_an_unknown_order_is_refused(
    client: TestClient, library: Library
) -> None:
    """The page can be ordered, and an unknown order is a 422."""
    sign_in(client, "admin")

    az = [
        item["id"] for item in client.get("/api/assets", params={"sort": "name_az"}).json()["items"]
    ]
    za = [
        item["id"] for item in client.get("/api/assets", params={"sort": "name_za"}).json()["items"]
    ]

    assert az == [library.private, library.shared]  # private.mp4 before shared.mp4
    assert za == list(reversed(az))

    # No sort is newest-first, the long-standing default.
    default = client.get("/api/assets").json()["items"]
    assert {item["id"] for item in default} == {library.shared, library.private}

    assert client.get("/api/assets", params={"sort": "by-vibes"}).status_code == 422


def test_favorites_can_be_ordered_by_when_each_heart_was_pressed(
    client: TestClient, library: Library
) -> None:
    """Favorites order by when each heart was pressed, turned over between two reads."""
    sign_in(client, "admin")

    def wall(order: str) -> list[str]:
        answer = client.get("/api/assets", params={"fav": "yes", "sort": order})
        assert answer.status_code == 200, answer.text
        return [item["id"] for item in answer.json()["items"]]

    client.put(f"/api/assets/{library.shared}/favorite", json={"favorite": True})
    client.put(f"/api/assets/{library.private}/favorite", json={"favorite": True})
    assert wall("favorited") == [library.private, library.shared]
    assert wall("favorited_oldest") == [library.shared, library.private]

    client.put(f"/api/assets/{library.shared}/favorite", json={"favorite": False})
    client.put(f"/api/assets/{library.shared}/favorite", json={"favorite": True})
    assert wall("favorited") == [library.shared, library.private]
    assert wall("favorited_oldest") == [library.private, library.shared]


def test_which_shuffle_is_a_parameter_the_edge_bounds(client: TestClient, library: Library) -> None:
    """`seed` picks the shuffle and is bounded, since a large one overflows the stride multiply."""
    sign_in(client, "admin")

    assert client.get("/api/assets", params={"sort": "random", "seed": -1}).status_code == 422
    assert (
        client.get("/api/assets", params={"sort": "random", "seed": 2_147_483_647}).status_code
        == 422
    )

    seeded = client.get("/api/assets", params={"sort": "random", "seed": 2_147_483_646})
    assert seeded.status_code == 200
    assert {item["id"] for item in seeded.json()["items"]} == {library.shared, library.private}

    # A seed beside another order is ignored, since addresses are edited and kept.
    stale = client.get("/api/assets", params={"sort": "name_az", "seed": 4242})
    assert stale.status_code == 200
    assert [item["id"] for item in stale.json()["items"]] == [library.private, library.shared]


def test_the_page_is_bounded(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    assert client.get("/api/assets", params={"limit": 0}).status_code == 422
    assert client.get("/api/assets", params={"offset": -1}).status_code == 422
    assert client.get("/api/assets", params={"limit": 10_000}).status_code == 422


def test_paging_walks_the_library_without_repeating_itself(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    first = client.get("/api/assets", params={"limit": 1, "offset": 0}).json()
    second = client.get("/api/assets", params={"limit": 1, "offset": 1}).json()

    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["total"] == second["total"] == 2


def test_a_page_past_the_end_still_reports_the_real_total(
    client: TestClient, library: Library
) -> None:
    """An empty page that reported a total of zero would contradict the pages before it."""
    sign_in(client, "admin")
    body = client.get("/api/assets", params={"limit": 10, "offset": 99}).json()

    assert body["items"] == []
    assert body["total"] == 2


# --- starting where a link says, rather than at a page number ---------------------------------
#
#
# A page number names different files on different screens, so the address carries the file.


def test_a_page_starts_where_the_link_says(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    second = client.get("/api/assets", params={"limit": 1, "offset": 1}).json()["items"][0]["id"]

    body = client.get("/api/assets", params={"limit": 1, "from": second}).json()

    assert [item["id"] for item in body["items"]] == [second]
    assert body["offset"] == 1


def test_the_offset_that_comes_back_is_where_it_actually_started(
    client: TestClient, library: Library
) -> None:
    """The caller sent no offset, so the one in the answer is the server's, and the range readout
    under the grid ("3-48 of 9,000") is computed from it. Echoing back the nothing that was
    sent would put every anchored page at "1-".
    """
    sign_in(client, "admin")
    last = client.get("/api/assets", params={"limit": 1, "offset": 1}).json()["items"][0]["id"]

    assert client.get("/api/assets", params={"from": last}).json()["offset"] == 1


def test_a_link_to_a_file_that_has_gone_opens_at_the_top(
    client: TestClient, library: Library
) -> None:
    """A link to a file that has gone opens at the top."""
    sign_in(client, "admin")
    body = client.get("/api/assets", params={"from": "01ARZ3NDEKTSV4RRFFQ69G5FAV"}).json()

    assert body["offset"] == 0
    assert len(body["items"]) == 2


def test_a_link_to_a_file_this_viewer_may_not_see_opens_at_the_top(
    client: TestClient, library: Library
) -> None:
    """A link to a file the viewer may not see opens at the top too, the same answer."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    private = client.get("/api/assets", params={"from": library.private}).json()
    missing = client.get("/api/assets", params={"from": "01ARZ3NDEKTSV4RRFFQ69G5FAV"}).json()

    assert private["offset"] == missing["offset"] == 0
    assert private["items"] == missing["items"]
    assert private["total"] == missing["total"] == 1


def test_an_anchor_that_is_not_an_id_opens_at_the_top(client: TestClient, library: Library) -> None:
    """Never a 500, and never bound into the statement as a NULL, which it would read as "no file
    named" and answer with the position of whatever the window ranked first."""
    sign_in(client, "admin")
    for nonsense in ("", "  ", "../../etc/passwd", "1 OR 1=1", "\N{SUPERSCRIPT TWO}"):
        body = client.get("/api/assets", params={"from": nonsense})
        assert body.status_code == 200
        assert body.json()["offset"] == 0


def test_an_anchor_is_a_place_in_the_ORDER_that_was_asked_for(
    client: TestClient, library: Library
) -> None:
    """An anchor is resolved under the order asked for."""
    sign_in(client, "admin")

    az = client.get("/api/assets", params={"sort": "name_az", "from": library.shared}).json()
    za = client.get("/api/assets", params={"sort": "name_za", "from": library.shared}).json()

    assert az["offset"] == 1  # private.mp4, then shared.mp4
    assert za["offset"] == 0
    assert az["items"][0]["id"] == za["items"][0]["id"] == library.shared


def test_an_anchor_is_a_place_in_the_FILTERED_set(client: TestClient, library: Library) -> None:
    """Narrowed by whatever the screen is narrowed by, or following a link out of a search would
    land somewhere with no relation to what was searched for."""
    sign_in(client, "admin")

    whole = client.get("/api/assets", params={"from": library.shared}).json()
    narrowed = client.get(
        "/api/assets", params={"filename": "shared", "from": library.shared}
    ).json()

    assert narrowed["total"] == 1
    assert narrowed["offset"] == 0
    assert whole["total"] == 2


# --- no existence oracle --------------------------------------------------------------


def test_an_asset_a_guest_may_not_see_answers_like_one_that_is_not_there(
    client: TestClient, library: Library
) -> None:
    """The same status and the same body. A 403 here would confirm the file exists, and for a
    library organised by person that is most of what somebody was trying to find out."""
    sign_in(client, "guest")

    denied = client.get(f"/api/assets/{library.private}")
    missing = client.get("/api/assets/01HX0000000000000000000000")

    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()


def test_a_thumbnail_is_refused_for_an_asset_the_viewer_may_not_open(
    client: TestClient, library: Library
) -> None:
    """A thumbnail is a picture of the file. Refusing the detail and serving the still would give
    away the thing the refusal was protecting."""
    give_derivative(client, library.private)
    sign_in(client, "guest")

    assert client.get(f"/api/assets/{library.private}/thumb").status_code == 404


def test_a_thumbnail_that_was_never_built_is_the_same_answer_as_a_refused_one(
    client: TestClient, library: Library
) -> None:
    """Thumbnailing happens after import, so "not yet" is common and "not allowed" is rare."""
    sign_in(client, "admin")
    assert client.get(f"/api/assets/{library.shared}/thumb").status_code == 404


def test_a_tile_reports_whether_its_thumbnail_has_been_built(
    client: TestClient, library: Library
) -> None:
    """The grid draws a tile the moment an asset is indexed, before its still is made, so it has to
    tell "the picture is on its way" (a shimmer) from "there is no picture" (the empty frame). The
    tile carries that fact rather than finding out by requesting the still and reading the 404."""
    sign_in(client, "admin")

    def tile(asset_id: str) -> dict[str, Any]:
        items = client.get("/api/assets").json()["items"]
        return next(item for item in items if item["id"] == asset_id)

    # Freshly indexed: no thumbnail derivative, so the tile says so and will shimmer.
    assert tile(library.shared)["thumb"] is False

    # Once the thumbnail job has run, the very same asset reports it built.
    give_derivative(client, library.shared)
    assert tile(library.shared)["thumb"] is True


def test_one_file_opened_by_its_id_says_what_its_tile_says_about_its_pictures(
    client: TestClient, library: Library
) -> None:
    """A wall sent from another device reaches the other end as ids, and each is read back one at
    a time. A detail that left the still and the clip at their defaults would give a cell opened
    that way no poster, black until it played. The one file says what its tile says."""
    sign_in(client, "admin")

    def detail() -> Any:
        return client.get(f"/api/assets/{library.shared}").json()

    assert (detail()["thumb"], detail()["preview"]) == (False, False)
    give_derivative(client, library.shared)
    assert (detail()["thumb"], detail()["preview"]) == (True, False)
    give_derivative(client, library.shared, DerivativeKind.PREVIEW, extension="mp4")
    assert (detail()["thumb"], detail()["preview"]) == (True, True)


def test_a_tile_reports_whether_its_hover_clip_has_been_built(
    client: TestClient, library: Library
) -> None:
    """And says it separately from the still, which is the whole of why the field exists.

    Deciding by whether ANY picture had been built (the art token) would make a file with a
    still and no clip ask for the clip and get a 404 every time it scrolled past. It is the server
    that knows, so the server says it.
    """
    sign_in(client, "admin")

    def tile(asset_id: str) -> dict[str, Any]:
        items = client.get("/api/assets").json()["items"]
        return next(item for item in items if item["id"] == asset_id)

    assert tile(library.shared)["preview"] is False

    # The still alone: the case the art token could not see. A token now, and still no clip.
    give_derivative(client, library.shared)
    assert tile(library.shared)["thumb"] is True
    assert tile(library.shared)["art"] is not None
    assert tile(library.shared)["preview"] is False

    give_derivative(client, library.shared, DerivativeKind.PREVIEW, extension="mp4")
    assert tile(library.shared)["preview"] is True


def test_a_tile_whose_still_will_never_come_carries_the_verdicts_reason(
    client: TestClient, library: Library
) -> None:
    """A tile whose still will never come carries the standing verdict's reason, filed under
    `thumbnails`, in the code's words."""
    sign_in(client, "admin")

    def tile(asset_id: str) -> dict[str, Any]:
        items = client.get("/api/assets").json()["items"]
        return next(item for item in items if item["id"] == asset_id)

    assert tile(library.shared)["verdict"] is None
    write(
        db_path(client),
        [
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'thumbnails', 'no_frame', ?, 0, 1)",
                (library.shared, "ffmpeg read the file but produced no image from it"),
            ),
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'thumbnails', 'unreadable', ?, 1, 1)",
                (library.private, "This file could not be read just now."),
            ),
        ],
    )
    assert tile(library.shared)["verdict"] == VERDICT_WORDS["no_frame"]
    assert tile(library.private)["verdict"] is None


def test_a_wall_of_the_files_a_product_left_out_says_under_each_why(
    client: TestClient, library: Library
) -> None:
    """The `left_out=thumbnails` wall says under each file why, from standing verdicts only."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'thumbnails', 'not_decodable', ?, 0, 1)",
                (library.shared, "this file could not be decoded (Invalid NAL unit size)"),
            ),
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'faces', 'no_frame_decoded', ?, 0, 1)",
                (library.shared, "No moment of this file could be decoded."),
            ),
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'thumbnails', 'unreadable', ?, 1, 1)",
                (library.private, "This file could not be read just now."),
            ),
        ],
    )

    def wall(query: str) -> list[tuple[str, str | None]]:
        page = client.get(f"/api/assets?{query}").json()
        assert page["total"] == len(page["items"])
        return [(item["id"], item["left_out"]) for item in page["items"]]

    assert wall("left_out=thumbnails") == [(library.shared, "It wouldn't open.")]
    assert wall("q=left_out:thumbnails") == [(library.shared, "It wouldn't open.")]
    # Two products, two reasons, in the Build's order.
    assert wall("left_out=any") == [
        (library.shared, f"It wouldn't open. {VERDICT_WORDS['no_frame_decoded']}")
    ]
    assert wall("left_out=meaning") == []
    # Every other wall reads no verdicts and carries no line.
    assert {left for _, left in wall("")} == {None}
    assert wall("left_out=-thumbnails") == [(library.private, None)]


def test_a_shared_asset_serves_its_thumbnail(client: TestClient, library: Library) -> None:
    give_derivative(client, library.shared)
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    answer = client.get(f"/api/assets/{library.shared}/thumb")
    assert answer.status_code == 200
    assert answer.content == b"jpeg-bytes"


def test_a_thumbnail_is_not_cached_by_anything_shared(client: TestClient, library: Library) -> None:
    """A shared cache sits in front of a permission check it cannot re-run, and would hand one
    viewer's thumbnail to the next person who asked for the same address."""
    give_derivative(client, library.shared)
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/thumb")
    assert "private" in answer.headers["cache-control"]


def test_a_thumbnail_already_held_is_not_sent_again(client: TestClient, library: Library) -> None:
    """`no-cache` means "ask before reusing", and this is the answer to the asking.

    Without it the browser asks the question on every return to a grid and is sent the whole
    picture regardless, which is the expensive half of revalidation with none of the saving.
    """
    give_derivative(client, library.shared)
    sign_in(client, "admin")

    first = client.get(f"/api/assets/{library.shared}/thumb")
    assert first.status_code == 200
    again = client.get(
        f"/api/assets/{library.shared}/thumb", headers={"If-None-Match": first.headers["etag"]}
    )

    assert again.status_code == 304
    assert again.content == b""
    assert "private" in again.headers["cache-control"]


def test_a_thumbnail_whose_bytes_are_known_may_be_kept_without_asking(
    client: TestClient, library: Library
) -> None:
    """The saving this whole mechanism is for.

    Answering the question cheaply is still answering it: a grid of a hundred tiles is a hundred
    round trips on every visit. Once the address names the picture there is nothing to ask.

    Asked for the way the client asks for it: with the token on the end. The token is what makes
    the address name the picture, so it is what earns the promise; see the test below.
    """
    give_derivative(client, library.shared, digest="0123456789abcdef")
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/thumb?v=whatever-the-client-was-told")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, max-age=604800, immutable"


def test_the_same_thumbnail_asked_for_bare_is_checked_every_time(
    client: TestClient, library: Library
) -> None:
    """A still asked for with no token is checked every time: `immutable` is a promise about the
    address, and only the token makes it name the picture."""
    give_derivative(client, library.shared, digest="0123456789abcdef")
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/thumb")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, no-cache"


def test_a_thumbnail_nothing_is_known_about_is_still_checked_every_time(
    client: TestClient, library: Library
) -> None:
    """Every picture built before Sift recorded what it had made. There is nothing to promise about
    the address, so the careful rule stands until the catch-up pass has read the file."""
    give_derivative(client, library.shared)
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/thumb")

    assert answer.headers["cache-control"] == "private, no-cache"


def test_a_hidden_asset_is_never_given_a_keepable_address(
    client: TestClient, library: Library
) -> None:
    """**The rule the vault depends on**, and the one case where knowing the bytes is not enough.

    Its picture is reachable at all only because the vault is open. Kept without asking, it would
    go on being readable out of the browser's own store after the vault was shut, on the machine
    that was looking at it, which is precisely the person concealment is for.
    """
    give_derivative(client, library.shared, digest="0123456789abcdef")
    give_pin(db_path(client), sign_in(client, "admin"))
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    assert (
        client.put(f"/api/assets/{library.shared}/vault", json={"vault": True}).status_code == 204
    )

    answer = client.get(f"/api/assets/{library.shared}/thumb")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, no-cache"


def test_hiding_something_changes_the_token_on_everything_else(
    client: TestClient, library: Library
) -> None:
    """What makes a concealment reach a browser that has stopped asking.

    The pictures of the OTHER file did not change, and its address still has to. Otherwise every
    copy fetched before the hide stays readable for a week, with nothing on screen to say so.
    """
    give_derivative(client, library.shared, digest="0123456789abcdef")
    give_derivative(client, library.private, digest="fedcba9876543210")
    give_pin(db_path(client), sign_in(client, "admin"))
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200

    def token_for(asset_id: str) -> str | None:
        rows = client.get("/api/assets").json()["items"]
        return str(next(item["art"] for item in rows if item["id"] == asset_id))

    before = token_for(library.shared)
    assert before is not None

    assert (
        client.put(f"/api/assets/{library.private}/vault", json={"vault": True}).status_code == 204
    )

    assert token_for(library.shared) != before


def test_the_detail_carries_the_same_picture_token_the_tile_does(
    client: TestClient, library: Library
) -> None:
    """The screen that draws a file at full size draws pictures too: the poster behind the video,
    and the scrub strip the player asks for. Without the token every one of them is checked with the
    server on every open, which is the request the token exists to skip."""
    give_derivative(client, library.shared, digest="0123456789abcdef")
    sign_in(client, "admin")

    tile = next(
        item for item in client.get("/api/assets").json()["items"] if item["id"] == library.shared
    )
    detail = client.get(f"/api/assets/{library.shared}").json()

    assert detail["art"] == tile["art"]
    assert detail["art"] is not None


def test_a_thumbnail_is_still_refused_to_somebody_who_may_not_see_it_however_they_ask(
    client: TestClient, library: Library
) -> None:
    """The permission check runs before the question about freshness, and outranks it.

    A 304 for something concealed would confirm both that it exists and that the copy held is
    current, which is the whole of what concealment hides.
    """
    give_derivative(client, library.private)
    sign_in(client, "admin")
    tag = client.get(f"/api/assets/{library.private}/thumb").headers["etag"]

    guest = sign_in(client, "guest")
    del guest
    assert (
        client.get(
            f"/api/assets/{library.private}/thumb", headers={"If-None-Match": tag}
        ).status_code
        == 404
    )


def test_signed_out_gets_nothing(client: TestClient, library: Library) -> None:
    for path in (
        "/api/assets",
        f"/api/assets/{library.shared}",
        f"/api/assets/{library.shared}/thumb",
        f"/api/assets/{library.shared}/preview",
        f"/api/assets/{library.shared}/save-to-device",
        "/api/save-log",
    ):
        assert client.get(path).status_code == 401, path


# --- the detail view -----------------------------------------------------------------


def test_the_detail_view_describes_the_asset(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["id"] == library.shared
    assert (body["width"], body["height"]) == (1920, 1080)
    assert body["duration_ms"] == 4000
    assert body["original_filename"] == "shared.mp4"


def test_the_detail_view_carries_the_same_marks_the_tile_does(
    client: TestClient, library: Library
) -> None:
    """The screen showing one file at full size must be able to say what the grid says about it.

    Left at their defaults, opening a shared file would say nothing about it, while the tile it
    was opened from drew the badge. One file, two screens, two answers.
    """
    guest = sign_in(client, "guest")
    admin = sign_in(client, "admin")
    del admin
    share(client, library.shared, guest)

    marked = client.get(f"/api/assets/{library.shared}").json()
    assert marked["shared"] is True
    assert marked["shared_here"] is True
    assert marked["restricted"] is False

    # ...and a file nobody has said anything about still says nothing, so this is not a field that
    # is simply always true.
    plain = client.get(f"/api/assets/{library.private}").json()
    assert plain["shared"] is False


def _sprite(client: TestClient, asset_id: str, **params: object) -> Path:
    """A scrub strip for an asset, with the layout its builder would have recorded."""
    return give_derivative(
        client,
        asset_id,
        DerivativeKind.SPRITE,
        body=b"sheet-bytes",
        params={"columns": 5, "rows": 6, "tile_width": 320, **params},
    )


def test_a_shared_asset_serves_its_scrub_strip(client: TestClient, library: Library) -> None:
    """The strip is looked up without knowing the layout it was filed under, which is the whole
    reason it does not go through the same lookup the thumbnail does."""
    sign_in(client, "admin")
    _sprite(client, library.shared)

    answer = client.get(f"/api/assets/{library.shared}/sprite")

    assert answer.status_code == 200
    assert answer.content == b"sheet-bytes"
    assert answer.headers["content-type"] == "image/jpeg"
    assert answer.headers["cache-control"] == "private, no-cache"


def test_a_scrub_strip_is_refused_for_an_asset_the_viewer_may_not_open(
    client: TestClient, library: Library
) -> None:
    """The permission check runs before anything reads a row, and its refusal is a miss."""
    sign_in(client, "admin")
    _sprite(client, library.private)

    sign_in(client, "guest")
    assert client.get(f"/api/assets/{library.private}/sprite").status_code == 404


def test_a_scrub_strip_that_was_never_built_is_the_same_answer_as_a_refused_one(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    assert client.get(f"/api/assets/{library.shared}/sprite").status_code == 404


def test_the_detail_view_reports_the_strips_layout(client: TestClient, library: Library) -> None:
    """A sheet cannot be cut up without it, and it cannot be read off the image: the tile height
    varies with the video's shape, so the row count is not the sheet height over anything known."""
    sign_in(client, "admin")
    _sprite(client, library.shared)

    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["sprite"] == {"columns": 5, "rows": 6, "tile_width": 320, "frames": 8}


def test_the_layout_says_how_many_cells_hold_a_frame(client: TestClient, library: Library) -> None:
    """A sheet is as many rows as it takes, so the last row is usually short.

    Without this a scrubber near the end of a clip picks one of the empty cells and shows a blank
    square. The seeded file is four seconds long, which is eight frames in a grid of thirty.
    """
    sign_in(client, "admin")
    _sprite(client, library.shared)

    layout = client.get(f"/api/assets/{library.shared}").json()["sprite"]

    assert layout["frames"] == 8
    assert layout["frames"] < layout["columns"] * layout["rows"]


def test_the_frame_count_can_never_run_past_the_grid(client: TestClient, library: Library) -> None:
    """A sheet built at one density and read at another.

    The count comes from the file's length rather than from beside the sheet, so a sheet built by
    an older build can hold fewer frames than the length now implies. Capped at the grid, which
    makes that case a strip cut too coarsely rather than one read off the end of itself.
    """
    sign_in(client, "admin")
    _sprite(client, library.shared)
    # A minute of video, which the sampler would fill sixty cells with. The seeded grid holds 30.
    write(
        db_path(client),
        [("UPDATE assets SET duration_ms = 60000 WHERE id = ?", (library.shared,))],
    )

    layout = client.get(f"/api/assets/{library.shared}").json()["sprite"]

    assert layout["frames"] == layout["columns"] * layout["rows"]


def test_an_asset_with_no_strip_reports_no_layout(client: TestClient, library: Library) -> None:
    """Absent rather than zeroed. A player reads it as "there is no scrub preview" and does not
    ask for a sheet that is not there; a layout of zeros would be a sheet it tried to cut up."""
    sign_in(client, "admin")

    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["sprite"] is None


def test_a_strip_filed_under_something_unreadable_reports_no_layout(
    client: TestClient, library: Library
) -> None:
    """A row from an older build, or one whose settings were something else entirely.

    Half a layout is worse than none: a player handed a partial arrangement draws the wrong frame,
    which reads as a broken seek rather than as missing data. The bytes are still served: the
    sheet is fine, it is the description of it that is not.
    """
    sign_in(client, "admin")
    give_derivative(
        client,
        library.shared,
        DerivativeKind.SPRITE,
        body=b"sheet-bytes",
        params={"columns": 5},  # no rows, no tile width
    )

    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["sprite"] is None
    assert client.get(f"/api/assets/{library.shared}/sprite").status_code == 200


def _plant_sprite_row(client: TestClient, asset_id: str, *, params: str, rel_path: str) -> None:
    """A scrub-strip row written straight in, past everything that would normally shape it.

    The application only ever writes these through one function, so a row like this cannot come from
    Sift running normally: it comes from a restored backup, a build old enough to have written
    something else, or somebody's repair script. Those are exactly the rows worth being sure of.
    """
    from sift.kernel.ids import new_id

    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives "
                "(id, asset_id, kind, rel_cache_path, params, size_bytes, created_at) "
                "VALUES (?, ?, 'sprite', ?, ?, 4, 0)",
                (new_id(), asset_id, rel_path, params),
            )
        ],
    )


@pytest.mark.parametrize(
    "params",
    [
        pytest.param("not json at all", id="unparseable"),
        pytest.param('"a string"', id="parses, but is not a set of settings"),
        pytest.param('{"columns": 5, "rows": 6}', id="a layout with a piece missing"),
        pytest.param(
            '{"columns": 0, "rows": 6, "tile_width": 320}',
            id="numbers that cannot describe a sheet",
        ),
    ],
)
def test_a_strip_filed_under_something_that_is_not_settings_reports_no_layout(
    client: TestClient, library: Library, params: str
) -> None:
    """Neither shape is a crash and neither is a guess: both are "there is no layout to report"."""
    _plant_sprite_row(client, library.shared, params=params, rel_path="ab/cd/x/sprite.jpg")
    sign_in(client, "admin")

    assert client.get(f"/api/assets/{library.shared}").json()["sprite"] is None


@pytest.mark.parametrize(
    "rel_path",
    [
        pytest.param("../../../etc/shadow", id="a path that escapes the cache"),
        pytest.param("ab/cd/gone/sprite.jpg", id="a path inside it with no file there"),
    ],
)
def test_a_strip_that_cannot_be_read_is_a_miss_not_a_crash(
    client: TestClient, library: Library, rel_path: str
) -> None:
    """The same rule the thumbnail follows, checked separately because it is a separate handler.

    Both cases here, because the store tells them apart and this route must not. An escaping path is
    a corrupt row and raises; a path inside the cache with nothing at it is a file that has been
    cleared out and comes back empty. To the person who asked for a picture they are one answer.
    """
    _plant_sprite_row(client, library.shared, params='{"columns":5}', rel_path=rel_path)
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/sprite")

    assert answer.status_code == 404
    assert answer.json() == {"detail": "not found"}


def test_the_newest_strip_wins_where_a_file_was_rebuilt(
    client: TestClient, library: Library
) -> None:
    """Rebuilding at a different density leaves both rows, because the layout is the key they are
    filed under. The one that describes the current build is the one to report."""
    sign_in(client, "admin")
    _sprite(client, library.shared, columns=4, rows=3, tile_width=160)
    write(db_path(client), [("UPDATE derivatives SET created_at = 1 WHERE kind = 'sprite'", ())])
    _sprite(client, library.shared, columns=5, rows=6, tile_width=320)
    write(
        db_path(client),
        [
            (
                "UPDATE derivatives SET created_at = 2"
                " WHERE kind = 'sprite' AND params LIKE '%\"tile_width\":320%'",
                (),
            )
        ],
    )

    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["sprite"] == {"columns": 5, "rows": 6, "tile_width": 320, "frames": 8}


def test_the_detail_view_does_not_carry_the_stored_mime_type(
    client: TestClient, library: Library
) -> None:
    """It was on the wire and nothing read it. The container is the word the screen shows.

    The row is given a MIME type first, so this is the field being absent rather than the column
    happening to be empty: a declared field would come back as a null, which is still a field.
    """
    sign_in(client, "admin")
    write(
        db_path(client),
        [("UPDATE assets SET mime = ? WHERE id = ?", ("video/mp4", library.shared))],
    )

    body = client.get(f"/api/assets/{library.shared}").json()

    assert "mime" not in body
    assert body["container"] is None or isinstance(body["container"], str)


def test_the_detail_says_a_heic_is_a_picture_some_browsers_cannot_draw(
    client: TestClient, library: Library
) -> None:
    """So a browser that cannot draw it asks for the copy first and never fetches the original
    only to fail; every other picture says no."""
    sign_in(client, "admin")
    assert client.get(f"/api/assets/{library.shared}").json()["browser_may_not_draw"] is False

    write(
        db_path(client),
        [("UPDATE assets SET mime = ? WHERE id = ?", ("image/heic", library.shared))],
    )

    assert client.get(f"/api/assets/{library.shared}").json()["browser_may_not_draw"] is True


def test_the_detail_view_says_what_the_file_is_called_now(
    client: TestClient, library: Library
) -> None:
    """The name on disk, not the name it was imported under.

    `original_filename` is written once and never again, so a file renamed in its own folder would
    go on being labelled with a name that exists nowhere, which reads as Sift having lost track of
    it.
    Here the two disagree on purpose: the row was imported as `shared.mp4` and the location says the
    file is now `renamed.mp4`.
    """
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "UPDATE asset_locations SET rel_path = ?, filename = ? WHERE asset_id = ?",
                ("clips/renamed.mp4", "renamed.mp4", library.shared),
            )
        ],
    )

    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["filename"] == "renamed.mp4"
    # The imported name is still reported beside it. It is what the file was called when it arrived,
    # which is a different fact rather than an out-of-date version of this one.
    assert body["original_filename"] == "shared.mp4"


def test_an_asset_with_no_reachable_copy_reports_no_current_name(
    client: TestClient, library: Library
) -> None:
    """An unplugged drive. There is no name to report, because there is no file to look at, and
    the screen falls back to the imported one, which is then the only name anybody has."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?",
                (library.shared,),
            )
        ],
    )

    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["filename"] is None
    assert body["original_filename"] == "shared.mp4"


def test_the_grid_carries_no_path_at_all(client: TestClient, library: Library) -> None:
    """Where a file sits names directories, usernames and mount points. The browser needs none of
    it to draw a tile, and the listing is the payload every screen fetches."""
    sign_in(client, "admin")
    page = client.get("/api/assets").text

    assert "rel_path" not in page
    assert "abs_path" not in page
    assert "/media" not in page


def test_where_a_file_is_the_full_path_for_an_admin_and_never_for_a_guest(
    client: TestClient, library: Library
) -> None:
    """An admin reads the path on this machine, one they can paste into a file manager; a guest
    shown the same file reads the library folder's name and never the machine's path."""
    from sift.kernel.ids import new_id

    # The library folder's own row, which every scanned library has.
    write(
        db_path(client),
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, NULL, '', 'library')",
                (new_id(), library.root),
            )
        ],
    )
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    _grant(client, library.shared, guest, "share")

    where = client.get(f"/api/assets/{library.shared}").json()["where"]

    assert where == os.sep.join([str(library.media), "clips", f"{Path(where).name}"])
    assert where.endswith(".mp4")
    assert "abs_path" not in client.get(f"/api/assets/{library.shared}").text

    sign_in(client, "guest")
    seen = client.get(f"/api/assets/{library.shared}").json()["where"]

    assert str(library.media) not in seen, "the absolute path leaked"
    assert seen.endswith(".mp4")


def test_where_a_file_is_drops_the_library_name_when_the_viewer_cannot_see_the_top(
    client: TestClient, library: Library
) -> None:
    """A guest who cannot see the library's top folder gets the path inside the library alone,
    never the root's real path."""
    from sift.kernel.ids import new_id

    inner, sibling, asset = new_id(), new_id(), new_id()
    (library.media / "clips" / "deep").mkdir(parents=True, exist_ok=True)
    (library.media / "clips" / "deep" / "inner.mp4").write_bytes(b"bytes of inner")
    write(
        db_path(client),
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
                (inner, library.root, library.folder, "clips/deep", "deep"),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
                (sibling, library.root, library.folder, "clips/other", "other"),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, width, height, duration_ms, "
                "size_bytes, original_filename, added_at) "
                "VALUES (?, 'digest-inner', 'video', 1920, 1080, 4000, 14, 'inner.mp4', 0)",
                (asset,),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                "filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                (new_id(), asset, library.root, inner, "clips/deep/inner.mp4", "inner.mp4"),
            ),
        ],
    )

    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    _grant(client, asset, guest, "share")
    # The top folder is shared with nobody; a second visible folder makes the search loop go round.
    shared_sibling = client.put(
        "/api/sharing",
        json={
            "object_type": "folder",
            "object_id": sibling,
            "subject_user_id": guest,
            "effect": "share",
        },
    )
    assert shared_sibling.status_code == 200, shared_sibling.text

    sign_in(client, "guest")
    where = client.get(f"/api/assets/{asset}").json()["where"]

    # Neither the library folder nor the two folders on the way are the guest's to see.
    assert where == os.sep.join(["...", "inner.mp4"])


def test_a_derivative_row_pointing_outside_the_cache_is_a_miss_not_a_crash(
    client: TestClient, library: Library
) -> None:
    """A row naming a path that is not in the cache any more.

    It comes from a restored backup, or from a build that predates the check that now refuses it.
    The refusal is right; answering it with a server error is not. The caller asked for a picture
    and there is not one, which is the same answer as every other miss.
    """
    from sift.kernel.ids import new_id

    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives "
                "(id, asset_id, kind, rel_cache_path, params, size_bytes, created_at) "
                "VALUES (?, ?, 'thumb', ?, '{}', 4, 0)",
                (new_id(), library.shared, "../../../etc/shadow"),
            )
        ],
    )
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/thumb")

    assert answer.status_code == 404
    assert answer.json() == {"detail": "not found"}


def _grant(client: TestClient, asset_id: str, subject: str, effect: str) -> None:
    response = client.put(
        "/api/sharing",
        json={
            "object_type": "item",
            "object_id": asset_id,
            "subject_user_id": subject,
            "effect": effect,
        },
    )
    assert response.status_code == 200, response.text


def test_an_admin_is_told_which_files_carry_a_sharing_decision(
    client: TestClient, library: Library
) -> None:
    """What the badge in the corner of a tile is drawn from.

    One read for the whole page rather than one per tile, and it says only THAT a decision was made,
    never to whom. The names are in the sharing panel, which is a route of its own.
    """
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    _grant(client, library.shared, guest, "share")
    _grant(client, library.private, guest, "restrict")

    items = {row["id"]: row for row in client.get("/api/assets").json()["items"]}

    assert (items[library.shared]["shared"], items[library.shared]["restricted"]) == (True, False)
    assert (items[library.private]["shared"], items[library.private]["restricted"]) == (False, True)


def test_a_guest_is_told_nothing_about_grants(client: TestClient, library: Library) -> None:
    """Not even the ones about them.

    Their screen already IS the answer (everything on it is something they can reach), so the
    badge would be the same word on every tile. What it would also do, on a screen an admin and a
    guest can both reach, is describe decisions that are not theirs to see.
    """
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    _grant(client, library.shared, guest, "share")

    sign_in(client, "guest")
    items = client.get("/api/assets").json()["items"]

    assert items and all(not row["shared"] and not row["restricted"] for row in items)


# --- a file nothing can compare -------------------------------------------------------------


def _verdict(client: TestClient, asset_id: str, *, transient: int) -> None:
    write(
        db_path(client),
        [
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'fingerprints', 'not_decodable', ?, ?, 1)",
                (asset_id, "this file could not be decoded (Invalid NAL unit size)", transient),
            )
        ],
    )


def test_a_file_that_cannot_be_fingerprinted_says_why_on_its_own_record(
    client: TestClient, library: Library
) -> None:
    """A file whose frames the decoder refuses is whole enough to keep and to play, and the one
    thing Sift cannot do with it is compare it to anything else. Said on the record, because
    "no near-copies found" and "this file was never in the comparison" are otherwise the same
    silence: on the file's own page and on the duplicates screen alike. In the code's own words,
    never the decoder's text, which is no sentence a person can act on."""
    sign_in(client, "admin")
    _verdict(client, library.shared, transient=0)

    detail = client.get(f"/api/assets/{library.shared}").json()

    assert detail["fingerprint_verdict"] == VERDICT_WORDS["not_decodable"]
    assert "Invalid NAL unit size" not in detail["fingerprint_verdict"]


def test_a_file_that_was_merely_away_says_nothing_about_its_fingerprints(
    client: TestClient, library: Library
) -> None:
    """The known negative. A transient verdict is about a moment, and the next scan of the file
    clears it, so reading it out as a fact about the file would be a permanent-sounding sentence
    about something that is not."""
    sign_in(client, "admin")
    _verdict(client, library.shared, transient=1)

    assert client.get(f"/api/assets/{library.shared}").json()["fingerprint_verdict"] is None


def test_an_ordinary_file_says_nothing_about_its_fingerprints(
    client: TestClient, library: Library
) -> None:
    """Most of a library. A file whose fingerprints have merely not been taken yet is waiting, not
    refused, and the field carries the permanent answer only."""
    sign_in(client, "admin")

    assert client.get(f"/api/assets/{library.shared}").json()["fingerprint_verdict"] is None


# --- a file being repaired ----------------------------------------------------------------------


def _measure(client: TestClient, asset_id: str, gap: int) -> None:
    write(db_path(client), [("UPDATE assets SET interleave_gap = ? WHERE id = ?", (gap, asset_id))])


def test_a_well_made_file_says_nothing_about_repair(client: TestClient, library: Library) -> None:
    """Almost every file. A control explaining something that did not happen is noise on every
    screen it appears on."""
    sign_in(client, "admin")
    _measure(client, library.shared, NEEDS_REPAIR_BYTES - 1)

    detail = client.get(f"/api/assets/{library.shared}").json()

    assert detail["playback_repair"] is None


def test_an_unmeasured_file_says_nothing_about_repair(client: TestClient, library: Library) -> None:
    """Unmeasured is not the same as bad, and the screen must not imply it is."""
    sign_in(client, "admin")

    detail = client.get(f"/api/assets/{library.shared}").json()

    assert detail["playback_repair"] is None


def test_a_file_awaiting_repair_says_so(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    _measure(client, library.shared, NEEDS_REPAIR_BYTES)

    detail = client.get(f"/api/assets/{library.shared}").json()

    assert detail["playback_repair"] == "pending"


def test_a_repaired_file_says_it_is_repaired(client: TestClient, library: Library) -> None:
    """The two states are different sentences on the screen, so they must be distinguishable here."""
    sign_in(client, "admin")
    _measure(client, library.shared, NEEDS_REPAIR_BYTES + 1)
    give_derivative(client, library.shared, DerivativeKind.REMUX, extension="mp4", body=b"copy")

    detail = client.get(f"/api/assets/{library.shared}").json()

    assert detail["playback_repair"] == "repaired"


def test_a_file_that_needs_repair_says_so_when_the_repair_is_switched_off(
    client: TestClient, library: Library
) -> None:
    """The one case where something really is wrong from where the person is sitting.

    With the switch on, a file waiting for its copy is `pending`: nothing is asked of anybody and
    it sorts itself out. With it off the file stutters for good, and the setting that explains why
    is three screens away, so the player has to be able to tell the two apart.
    """
    sign_in(client, "admin")
    _measure(client, library.shared, NEEDS_REPAIR_BYTES)

    assert (
        client.put("/api/settings", json={"values": {REPAIR_PLAYBACK_KEY: False}}).status_code
        == 204
    )
    assert client.get(f"/api/assets/{library.shared}").json()["playback_repair"] == "off"

    # And back: the same file stops complaining the moment the switch is on again.
    client.put("/api/settings", json={"values": {REPAIR_PLAYBACK_KEY: True}})
    assert client.get(f"/api/assets/{library.shared}").json()["playback_repair"] == "pending"


def test_turning_the_repair_off_keeps_every_copy_and_the_file_says_so(
    client: TestClient, library: Library
) -> None:
    """Off stops Sift MAKING repaired copies and deletes none: a delete there would also take the
    copies the player makes for a container the browser cannot read, which the switch does not
    govern. A copy is cache, deleted on purpose in `Settings > Maintenance`, so a file whose copy
    is still there goes on playing from it and says so.
    """
    sign_in(client, "admin")
    _measure(client, library.shared, NEEDS_REPAIR_BYTES + 1)
    give_derivative(client, library.shared, DerivativeKind.REMUX, extension="mp4", body=b"copy")
    assert client.get(f"/api/assets/{library.shared}").json()["playback_repair"] == "repaired"

    client.put("/api/settings", json={"values": {REPAIR_PLAYBACK_KEY: False}})

    assert client.get(f"/api/assets/{library.shared}").json()["playback_repair"] == "repaired"


def test_turning_the_repair_back_on_asks_for_the_work_again(
    client: TestClient, library: Library
) -> None:
    """On has to ask for the copies that are missing, because nothing else will.

    The pass that looks for files needing repair only examines ones nobody has MEASURED. A file
    measured while the switch was off (or whose copy was deleted in Maintenance) was measured
    already, so without this, off-then-on leaves exactly the files the feature exists for
    permanently unrepaired, and nothing anywhere says so.
    """
    sign_in(client, "admin")
    client.put("/api/settings", json={"values": {REPAIR_PLAYBACK_KEY: False}})
    _measure(client, library.shared, NEEDS_REPAIR_BYTES + 1)
    assert client.get(f"/api/assets/{library.shared}").json()["playback_repair"] == "off"

    client.put("/api/settings", json={"values": {REPAIR_PLAYBACK_KEY: True}})

    # Waiting for the copy to be built, rather than "off".
    assert client.get(f"/api/assets/{library.shared}").json()["playback_repair"] == "pending"
    queued = client.get("/api/jobs", params={"type": "remux"}).json()
    assert queued["jobs"], "turning the repair on asked for no work"


# --- something else to look at ----------------------------------------------------------


def test_randomize_answers_with_something_from_the_library(
    client: TestClient, library: Library
) -> None:
    """The control's whole job: hand back a file, whichever one."""
    sign_in(client, "admin")

    answer = client.get("/api/assets/random")

    assert answer.status_code == 200
    assert answer.json()["id"] in {library.shared, library.private}


def test_randomize_reaches_past_whatever_is_on_screen(client: TestClient, library: Library) -> None:
    """Randomize with nothing in the address can answer outside the narrowed view."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}/favorite", json={"favorite": True})
    narrowed = client.get("/api/assets", params={"fav": "1"}).json()
    assert [item["id"] for item in narrowed["items"]] == [library.shared]

    reached = {client.get("/api/assets/random").json()["id"] for _ in range(25)}

    assert library.private in reached, "randomize never left the filtered view"


def test_a_filtered_draw_never_answers_with_a_file_outside_the_filter(
    client: TestClient, library: Library
) -> None:
    """A Theater cell showing only video, on a library that is half photographs.

    Asked enough times that a version drawing from the whole library and leaving the narrowing to
    the caller would have to produce the picture. It is not that the picture is dropped afterwards:
    it is not in the set that gets counted, which is what makes the count and the answer
    describe the same rows.
    """
    sign_in(client, "admin")
    write(
        db_path(client),
        [("UPDATE assets SET media_type = 'image' WHERE id = ?", (library.private,))],
    )

    answers = {
        client.get("/api/assets/random", params={"media": "video|gif"}).json()["id"]
        for _ in range(25)
    }

    assert answers == {library.shared}


def test_a_filtered_draw_still_prefers_not_to_answer_with_what_is_already_open(
    client: TestClient, library: Library
) -> None:
    """The two narrowings compose: `avoiding` is about the caller, the filter is about the files.

    Both files are favourites, so the filter alone leaves the draw a choice of two, and the one
    already open is still the one it walks away from.
    """
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}/favorite", json={"favorite": True})
    client.put(f"/api/assets/{library.private}/favorite", json={"favorite": True})

    answers = {
        client.get("/api/assets/random", params={"fav": "1", "avoiding": library.shared}).json()[
            "id"
        ]
        for _ in range(15)
    }

    assert answers == {library.private}


def test_a_filter_matching_nothing_is_the_ordinary_miss(
    client: TestClient, library: Library
) -> None:
    """The same 404 an empty library gets, and deliberately the same.

    A filter matching nothing and a library holding nothing this user may see are told apart
    nowhere in this module, because the difference between them is exactly what it refuses to
    publish.
    """
    sign_in(client, "admin")

    answer = client.get("/api/assets/random", params={"filetype": "mkv"})

    assert answer.status_code == 404
    assert answer.json() == {"detail": "not found"}


def test_randomize_never_answers_with_something_out_of_scope(
    client: TestClient, library: Library
) -> None:
    """A guest, and a library where one file was shared with them and one was not.

    Asked enough times that a version picking from the whole table would have to produce the other
    one. It is not that the private file is filtered out afterwards: it is not in the set that
    gets counted, which is what makes the count and the answer describe the same rows.
    """
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    answers = {client.get("/api/assets/random").json()["id"] for _ in range(25)}

    assert answers == {library.shared}


def test_randomize_never_answers_with_a_concealed_file(
    client: TestClient, library: Library
) -> None:
    """The vault, shut. A concealed file is not in the set to be counted, so it cannot be chosen."""
    sign_in(client, "admin")
    hide_for_caller(client, "asset", library.private)

    answers = {client.get("/api/assets/random").json()["id"] for _ in range(25)}

    assert answers == {library.shared}


def test_randomize_skips_a_placeholder_as_well(client: TestClient, library: Library) -> None:
    """The other concealment mode, where a concealed file still reaches the grid as a locked tile.

    A placeholder is not something anybody can play, so handing one back at random would look like
    the control being broken: it opens on the word Hidden and nothing else.
    """
    user_id = sign_in(client, "admin")

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id,
            role=Role.ADMIN,
            show_hidden=False,
            concealment=Concealment.PLACEHOLDER,
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        hide_for_caller(client, "asset", library.private)

        answers = {client.get("/api/assets/random").json()["id"] for _ in range(25)}

        assert answers == {library.shared}
    finally:
        client.app.dependency_overrides.pop(current_viewer, None)  # type: ignore[attr-defined]


def test_randomize_gives_up_when_everything_it_can_see_is_a_placeholder(
    client: TestClient, library: Library
) -> None:
    """A library where every file is behind the vault, in the mode that still draws locked tiles.

    There is a count and there are rows, and not one of them is something to play. The answer is the
    ordinary miss rather than a locked tile handed over as though it were a suggestion.
    """
    user_id = sign_in(client, "admin")

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id,
            role=Role.ADMIN,
            show_hidden=False,
            concealment=Concealment.PLACEHOLDER,
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        hide_for_caller(client, "asset", library.shared)
        hide_for_caller(client, "asset", library.private)

        assert client.get("/api/assets/random").status_code == 404
    finally:
        client.app.dependency_overrides.pop(current_viewer, None)  # type: ignore[attr-defined]


def test_randomize_prefers_not_to_answer_with_what_is_already_open(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")

    answers = {
        client.get("/api/assets/random", params={"avoiding": library.shared}).json()["id"]
        for _ in range(15)
    }

    assert answers == {library.private}


def test_randomize_will_repeat_the_only_file_there_is(client: TestClient, library: Library) -> None:
    """Avoiding is a preference, not a rule. A library of one has nowhere else to go, and refusing
    would make the control look broken on exactly the install least able to explain it."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    answer = client.get("/api/assets/random", params={"avoiding": library.shared})

    assert answer.status_code == 200
    assert answer.json()["id"] == library.shared


def test_randomize_answers_the_ordinary_miss_when_there_is_nothing(
    client: TestClient, library: Library
) -> None:
    """A guest nobody has shared anything with. The same 404 as everything else."""
    sign_in(client, "guest")

    answer = client.get("/api/assets/random")

    assert answer.status_code == 404
    assert answer.json() == {"detail": "not found"}


def test_the_wall_narrows_to_one_username_and_the_narrowing_folds_into_the_query(
    client: TestClient, library: Library
) -> None:
    """What a username's own page draws with, and it is not a search word.

    A username is a row a download wrote, not something anybody typed, so it narrows the wall the
    way a folder does, folded into whatever the query language already said rather than replacing
    it. The count has to agree with the page, which is what says the narrowing happened in the
    database and not afterwards.
    """
    from sift.kernel.ids import new_id

    sign_in(client, "admin")
    site_id, username_id = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO sites (id, name) VALUES (?, ?)",
                (site_id, "SomeSite"),
            ),
            (
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 0)",
                (username_id, site_id, "esmewrenfield"),
            ),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (library.shared, username_id),
            ),
        ],
    )

    body = client.get("/api/assets", params={"username": username_id}).json()

    assert [one["id"] for one in body["items"]] == [library.shared]
    assert body["total"] == 1
    # And it names the username, which is what the filter chip draws: the id in the address is
    # opaque and a username has no page of its own to ask instead.
    named = {"id": username_id, "username": "esmewrenfield", "site": "SomeSite", "person_id": None}
    assert body["username"] == named
    assert client.get("/api/assets").json()["username"] is None

    # And it narrows rather than replaces: a search term nothing under this username matches leaves
    # the page empty instead of showing everything the username posted.
    both = client.get("/api/assets", params={"username": username_id, "q": "nothing-like-this"})
    assert both.json()["items"] == []


def test_a_username_nothing_was_posted_under_narrows_to_nothing(
    client: TestClient, library: Library
) -> None:
    """An id nobody minted is a filter that matches nothing, never a filter that is ignored."""
    from sift.kernel.ids import new_id

    sign_in(client, "admin")

    body = client.get("/api/assets", params={"username": new_id()}).json()

    assert body["items"] == []
    assert body["total"] == 0


# --- a file that is no longer where Sift left it ------------------------------------------------


def test_a_file_with_no_readable_copy_is_marked_on_the_wall(
    client: TestClient, library: Library
) -> None:
    """A file with no readable copy is marked on the wall, from the status a scan's sweep writes."""
    sign_in(client, "admin")
    asset = library.shared

    listed = client.get("/api/assets").json()["items"]
    assert [one["unreachable"] for one in listed if one["id"] == asset] == [False]

    write(
        db_path(client),
        [("UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (asset,))],
    )

    listed = client.get("/api/assets").json()["items"]
    assert [one["unreachable"] for one in listed if one["id"] == asset] == [True]
    assert client.get(f"/api/assets/{asset}").json()["unreachable"] is True


def test_one_readable_copy_is_enough(client: TestClient, library: Library) -> None:
    """Several copies, one of them on a drive that is not mounted, is an ordinary file.

    The question the mark asks is whether there is ONE copy to read, not whether every copy is
    there, which is the whole point of an asset being content rather than a path.
    """
    sign_in(client, "admin")
    asset = library.shared
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, size_bytes, mtime, first_seen_at, last_seen_at, status)"
                " SELECT 'second-copy', asset_id, root_id, folder_id, 'elsewhere/copy.mp4',"
                " 'copy.mp4', size_bytes, mtime, first_seen_at, last_seen_at, 'missing'"
                " FROM asset_locations WHERE asset_id = ?",
                (asset,),
            )
        ],
    )

    listed = client.get("/api/assets").json()["items"]

    assert [one["unreachable"] for one in listed if one["id"] == asset] == [False]


# --- the hover clip is served whatever shape it happens to be -----------------------------------


def test_a_hover_clip_built_to_an_older_shape_is_still_served(
    client: TestClient, library: Library
) -> None:
    """A hover clip built to an older recipe is still served after a settings change."""
    give_derivative(
        client,
        library.shared,
        DerivativeKind.PREVIEW,
        extension="mp4",
        body=b"an-older-clip",
        params={"clip_ms": 3000, "cut_ms": 3000, "v": 0},
    )
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/preview")

    assert answer.status_code == 200
    assert answer.content == b"an-older-clip"


def test_the_newest_hover_clip_is_the_one_served(client: TestClient, library: Library) -> None:
    """Which is what makes "newest" the right question rather than a preference.

    A rebuild writes the new row after the old one, so from the moment the replacement exists it is
    the one that answers, with no knowledge of the recipe on the serving side, which matters
    because the feature that decides the recipe may not be imported by the one that serves it.
    """
    give_derivative(
        client,
        library.shared,
        DerivativeKind.PREVIEW,
        extension="mp4",
        body=b"the-old-one",
        params={"clip_ms": 3000, "cut_ms": 3000, "v": 0},
    )
    give_derivative(
        client,
        library.shared,
        DerivativeKind.PREVIEW,
        extension="mp4",
        body=b"the-new-one",
        params={"clip_ms": 10000, "cut_ms": 2000, "v": 1},
    )
    sign_in(client, "admin")

    assert client.get(f"/api/assets/{library.shared}/preview").content == b"the-new-one"


def test_a_still_serving_no_clip_is_still_a_miss(client: TestClient, library: Library) -> None:
    """Asking for the newest of nothing is not an excuse to answer with something else's."""
    sign_in(client, "admin")

    assert client.get(f"/api/assets/{library.shared}/preview").status_code == 404


# --- what a search with WORDS in it is ordered by ------------------------------------------------
#
# Tested on `ordering_for`, the whole decision: these fixtures never fill the search index.


def test_a_query_with_words_asks_for_the_closest_match() -> None:
    """The grid defaults to closest-match for words, as the search route does.

    Otherwise one screen would answer one question two ways depending on which box it was typed
    into.
    """
    assert ordering_for(None, AssetFilter(text="a face")) == RELEVANCE


def test_an_order_somebody_asked_for_wins_over_the_words() -> None:
    """Named beats derived, always, or the sort control stops working the moment anything is typed."""
    assert ordering_for("name_za", AssetFilter(text="a face")) == "name_za"
    assert ordering_for("newest", AssetFilter(text="a face")) == "newest"


def test_a_query_with_no_words_is_still_newest_first() -> None:
    """`media:video` is not a search with words in it.

    It names a dimension; every row that comes back matches it exactly, so ordering by relevance
    there is ordering by a column of ties. The free-text part is what decides, which is why this
    reads the FILTER rather than the address.
    """
    assert ordering_for(None, AssetFilter()) == DEFAULT_SORT
    assert ordering_for(None, AssetFilter(text=None)) == DEFAULT_SORT
    assert ordering_for(None, AssetFilter(text="")) == DEFAULT_SORT


def test_a_wall_of_files_similar_to_one_is_closest_first() -> None:
    """With no words, a ranking on the filter is a `like:`, and a wall of lookalikes reads closest
    first. Words still order by relevance, and a named order still wins."""
    ranked = (("a", 0.1), ("b", 0.2))
    assert ordering_for(None, AssetFilter(neighbours=ranked)) == SIMILARITY
    assert ordering_for(None, AssetFilter(text="a face", neighbours=ranked)) == RELEVANCE
    assert ordering_for("newest", AssetFilter(neighbours=ranked)) == "newest"


def test_closest_match_on_a_wall_of_lookalikes_is_closeness_to_the_file() -> None:
    """The grid names Closest match on a ranked wall. With no words to score, that is the like
    ranking, never a column of tied text scores falling through to newest."""
    ranked = (("a", 0.1), ("b", 0.2))
    assert ordering_for(RELEVANCE, AssetFilter(neighbours=ranked)) == SIMILARITY
    assert ordering_for(RELEVANCE, AssetFilter(text="a face", neighbours=ranked)) == RELEVANCE
    assert ordering_for(RELEVANCE, AssetFilter()) == RELEVANCE


def test_an_unnamed_order_that_cannot_be_continued_is_refused_not_failed(
    client: TestClient, library: Library
) -> None:
    """Asked of the order the page is READ in. Words with no order named are read by relevance,
    which no row can continue, and the read refuses it: checked against the default instead, the
    request would pass the route and fail in the read."""
    sign_in(client, "admin")
    first = client.get("/api/assets", params={"limit": 1}).json()["items"][0]["id"]

    continued = client.get("/api/assets", params={"after": first, "q": "beach"})

    assert continued.status_code == 422


def test_a_wall_that_cannot_be_continued_from_a_row_is_told_so(
    client: TestClient, library: Library
) -> None:
    """Continuing from a row is a seek on the sort's own index, and a wall with something arranged
    in front of that order has no such index. It is refused rather than quietly paged from the
    top, which is a page that looks right and is not."""
    sign_in(client, "admin")
    first = client.get("/api/assets", params={"limit": 1}).json()["items"][0]["id"]

    continued = client.get("/api/assets", params={"after": first, "pinned_first": True})

    assert continued.status_code == 422
    assert "continued" in continued.json()["detail"]


def test_the_detail_carries_the_view_tally_under_one_name(
    client: TestClient, library: Library
) -> None:
    """The tally of views is `views`, the same field the tile carries, and nothing else. A second
    copy under another name that nothing read could disagree without anybody noticing; one name is
    what makes the number checkable."""
    sign_in(client, "admin")
    body = client.get(f"/api/assets/{library.shared}").json()
    assert "views" in body
    assert "view_count" not in body
