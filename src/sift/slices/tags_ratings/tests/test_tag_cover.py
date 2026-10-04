# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture a tag is drawn as, a still from a real file. The asset is checked against the
setter, and a cover the asker may not open reads as no cover."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    make_tag,
    share,
    sign_in,
)
from sift.testing.library import a_png

pytestmark = [pytest.mark.integration]


def test_a_tag_can_be_given_one_of_its_own_files_as_a_cover(
    client: TestClient, library: Library
) -> None:
    """The ordinary path, and the answer carries the new cover so the screen need not re-ask."""
    sign_in(client)
    tag = make_tag(client, "beach")
    assign(client, [library.shared], [tag])

    written = client.put(f"/api/tags/{tag}/cover", json={"asset_id": library.shared})

    assert written.status_code == 200
    assert written.json()["cover_asset_id"] == library.shared
    assert client.get(f"/api/tags/{tag}").json()["cover_asset_id"] == library.shared


def test_a_cover_can_be_taken_off_again(client: TestClient, library: Library) -> None:
    """None is a value here, not a missing field: it is how a tag goes back to having no picture."""
    sign_in(client)
    tag = make_tag(client, "beach")
    assign(client, [library.shared], [tag])
    client.put(f"/api/tags/{tag}/cover", json={"asset_id": library.shared})

    cleared = client.put(f"/api/tags/{tag}/cover", json={"asset_id": None})

    assert cleared.status_code == 200
    assert cleared.json()["cover_asset_id"] is None


def test_a_tag_that_was_never_minted_is_a_404(client: TestClient) -> None:
    """And it is the same 404 an unreachable tag gets, so a deep link cannot probe for one."""
    sign_in(client)

    assert (
        client.put(f"/api/tags/{NEVER_EXISTED}/cover", json={"asset_id": None}).status_code == 404
    )


def test_a_file_the_setter_cannot_see_is_refused(client: TestClient, library: Library) -> None:
    """An admin cannot set a cover from a file they cannot see: 404. A guest is refused earlier, by
    the admin check."""
    sign_in(client)
    tag = make_tag(client, "beach")
    assign(client, [library.shared], [tag])

    refused = client.put(f"/api/tags/{tag}/cover", json={"asset_id": NEVER_EXISTED})

    assert refused.status_code == 404
    # And nothing was written on the way to refusing: the tag still has no cover. A tag never
    # takes its first file's picture (`kernel/access/default_covers.py`), so it is its letter.
    assert client.get(f"/api/tags/{tag}").json()["cover_asset_id"] is None


def test_a_cover_the_asker_may_not_open_reads_as_no_cover(
    client: TestClient, library: Library
) -> None:
    """A cover the asker may not open reads as no cover."""
    sign_in(client)
    tag = make_tag(client, "beach")
    assign(client, [library.shared, library.private], [tag])
    client.put(f"/api/tags/{tag}/cover", json={"asset_id": library.private})

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)
    assert guest is not None

    seen = client.get(f"/api/tags/{tag}")

    assert seen.status_code == 200
    assert seen.json()["cover_asset_id"] is None


def test_a_guest_cannot_choose_the_picture_a_tag_wears(
    client: TestClient, library: Library
) -> None:
    """A guest cannot choose a tag's picture."""
    sign_in(client)
    tag = make_tag(client, "beach")
    assign(client, [library.shared], [tag])
    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)
    assert guest is not None

    assert (
        client.put(f"/api/tags/{tag}/cover", json={"asset_id": library.shared}).status_code == 403
    )


def test_a_moment_of_a_clip_can_be_the_cover(client: TestClient, library: Library) -> None:
    """A moment of a clip can be the cover; the choice is written and the still queued."""
    sign_in(client)
    tag = make_tag(client, "beach")
    assign(client, [library.shared], [tag])

    written = client.put(f"/api/tags/{tag}/cover", json={"asset_id": library.shared, "at_ms": 4200})

    assert written.status_code == 200
    assert written.json()["cover_asset_id"] == library.shared


def test_a_picture_from_outside_the_library_can_be_uploaded_and_read_back(
    client: TestClient, library: Library
) -> None:
    """An uploaded picture goes in as a PNG and reads back through the route as Sift's JPEG."""
    sign_in(client)
    tag = make_tag(client, "beach")

    sent = client.post(
        f"/api/tags/{tag}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 200
    assert sent.json()["cover_upload_id"] is not None
    # An upload and a file are alternatives, never both: one statement writes the pair.
    assert sent.json()["cover_asset_id"] is None

    served = client.get(f"/api/tags/{tag}/cover")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content.startswith(b"\xff\xd8\xff"), "what is served is a JPEG Sift wrote"


def test_bytes_that_are_not_a_picture_are_refused(client: TestClient) -> None:
    """The one route in Sift where a stranger chooses the bytes. ffmpeg is what reads them, and a
    refusal has to be an answer rather than a stack trace."""
    sign_in(client)
    tag = make_tag(client, "beach")

    sent = client.post(
        f"/api/tags/{tag}/cover-picture",
        files={"file": ("chosen.png", b"this is a sentence, not a picture", "image/png")},
    )

    assert sent.status_code == 400


def test_a_tag_with_no_cover_serves_a_404(client: TestClient) -> None:
    """The same 404 as a tag that is not there and as a cover the asker may not open. Telling those
    apart is what would say a hidden file exists."""
    sign_in(client)
    tag = make_tag(client, "beach")

    assert client.get(f"/api/tags/{tag}/cover").status_code == 404
    assert client.get(f"/api/tags/{NEVER_EXISTED}/cover").status_code == 404


def test_uploading_onto_a_tag_that_is_not_there_is_a_404(client: TestClient) -> None:
    """Checked BEFORE the bytes are read, so a stranger cannot make Sift run ffmpeg by naming a tag
    that does not exist."""
    sign_in(client)

    sent = client.post(
        f"/api/tags/{NEVER_EXISTED}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 404


def test_a_tag_listing_names_an_uploaded_cover_so_it_is_kept(client: TestClient) -> None:
    """A tag listing carries the token and upload id (`coverToken` in `lib/entity/art.ts`)."""
    sign_in(client)
    thing = make_tag(client, "beach")
    sent = client.post(
        f"/api/tags/{thing}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )
    assert sent.status_code == 200, sent.text

    listed = client.get("/api/tags")
    assert listed.status_code == 200, listed.text
    row = next(one for one in listed.json()["items"] if one["id"] == thing)
    assert row["art"], "the wall row carries the account's token"
    served = client.get(
        f"/api/tags/{thing}/cover", params={"v": f"{row['art']}.{row['cover_upload_id']}"}
    )

    assert served.status_code == 200
    assert "immutable" in served.headers["cache-control"]
