# SPDX-License-Identifier: AGPL-3.0-or-later
"""A picture from outside the library as the cover, for the two things this slice draws one for.

A cover is ordinarily a still from a file already in the library, which is the right default and
is no help at all for a person nobody has a good frame of or a site whose logo is not in the
library. The other way in is an upload, and it is the one
route in Sift where a stranger chooses the bytes, so what is asserted here is that what comes back
out is Sift's own JPEG and never the file that was sent.

The moment a cover names is the same feature reached from the other control and is proved for a
person in `test_cover_moment.py`; what is here is the site beside them, and the read.

A username has no cover: it has no page to choose one on and its links open the person or Browse.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    make_person,
    sign_in,
)
from sift.testing.library import a_png

pytestmark = [pytest.mark.integration]


def _make_site(client: TestClient, name: str) -> str:
    response = client.post("/api/sites", json={"name": name, "kind": None})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_a_person_wears_a_picture_that_was_never_in_the_library(client: TestClient) -> None:
    """In as a PNG, out as Sift's own JPEG, read back through the route that serves it."""
    sign_in(client)
    person = make_person(client, "Nadia Vance")

    sent = client.post(
        f"/api/people/{person}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 200, sent.text
    assert sent.json()["cover_upload_id"] is not None
    # An upload and a file are alternatives, never both: one statement writes the pair.
    assert sent.json()["cover_asset_id"] is None

    served = client.get(f"/api/people/{person}/cover")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content.startswith(b"\xff\xd8\xff"), "what is served is a JPEG Sift wrote"


def test_a_site_wears_one_too(client: TestClient) -> None:
    """The case an upload was really built for: a site's own mark is never a file in the library."""
    sign_in(client)
    site = _make_site(client, "Northlight Media Group")

    sent = client.post(
        f"/api/sites/{site}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 200, sent.text
    assert sent.json()["cover_upload_id"] is not None

    served = client.get(f"/api/sites/{site}/cover")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"


def test_a_moment_of_a_clip_can_be_a_sites_cover(client: TestClient, library: Library) -> None:
    sign_in(client)
    site = _make_site(client, "Northlight Media Group")

    written = client.put(
        f"/api/sites/{site}/cover", json={"asset_id": library.shared, "at_ms": 4200}
    )

    assert written.status_code == 200, written.text
    assert written.json()["cover_asset_id"] == library.shared


def test_bytes_that_are_not_a_picture_are_refused(client: TestClient) -> None:
    """ffmpeg is what reads them, and a refusal has to be an answer rather than a stack trace."""
    sign_in(client)
    person = make_person(client, "Nadia Vance")

    sent = client.post(
        f"/api/people/{person}/cover-picture",
        files={"file": ("chosen.png", b"this is a sentence, not a picture", "image/png")},
    )

    assert sent.status_code == 400


def test_uploading_onto_something_that_is_not_there_is_a_404(client: TestClient) -> None:
    """Checked BEFORE the bytes are read, so naming a row that does not exist cannot make Sift run
    ffmpeg. Both, because each resolves its own entity and they are two separate checks."""
    sign_in(client)
    picture = {"file": ("chosen.png", a_png(), "image/png")}

    assert (
        client.post(f"/api/people/{NEVER_EXISTED}/cover-picture", files=picture).status_code == 404
    )
    assert (
        client.post(f"/api/sites/{NEVER_EXISTED}/cover-picture", files=picture).status_code == 404
    )


def test_nothing_with_no_cover_serves_a_404(client: TestClient) -> None:
    """The same 404 as a row that is not there, and as a cover the asker may not open."""
    sign_in(client)
    person = make_person(client, "Nadia Vance")
    site = _make_site(client, "Northlight Media Group")

    assert client.get(f"/api/people/{person}/cover").status_code == 404
    assert client.get(f"/api/sites/{site}/cover").status_code == 404
    assert client.get(f"/api/people/{NEVER_EXISTED}/cover").status_code == 404
    assert client.get(f"/api/sites/{NEVER_EXISTED}/cover").status_code == 404


def test_a_site_listing_names_an_uploaded_cover_so_it_is_kept(client: TestClient) -> None:
    """The wall hands out what the address needs to name this upload, so the picture is KEPT.

    The server makes the week-long promise only to an address carrying the user's token and the
    upload id (`kernel/covers.py names_its_cover`); a wall row that carried no token would leave
    every uploaded cover re-checked on every visit. Built from the wire exactly as `coverToken` in
    `lib/entity/art.ts` builds it.
    """
    sign_in(client)
    thing = _make_site(client, "Northlight Media Group")
    sent = client.post(
        f"/api/sites/{thing}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )
    assert sent.status_code == 200, sent.text

    listed = client.get("/api/sites")
    assert listed.status_code == 200, listed.text
    row = next(one for one in listed.json()["items"] if one["id"] == thing)
    assert row["art"], "the wall row carries the user's token"
    served = client.get(
        f"/api/sites/{thing}/cover", params={"v": f"{row['art']}.{row['cover_upload_id']}"}
    )

    assert served.status_code == 200
    assert "immutable" in served.headers["cache-control"]


def test_the_walls_name_a_chosen_moment(client: TestClient, library: Library) -> None:
    """A cover that is one MOMENT of a clip is on the wall with its moment, for both kinds here.

    Without it the address would name only the file, which the server rightly will not keep for a
    week (a different moment of the same file is a different picture), so a chosen frame would be
    re-checked on every visit.
    """
    sign_in(client)
    person = make_person(client, "Nadia Vance")
    site = _make_site(client, "Northlight Media Group")
    for wall, thing in (("people", person), ("sites", site)):
        written = client.put(
            f"/api/{wall}/{thing}/cover", json={"asset_id": library.shared, "at_ms": 4200}
        )
        assert written.status_code == 200, written.text

        listed = client.get(f"/api/{wall}")
        row = next(one for one in listed.json()["items"] if one["id"] == thing)
        assert row["cover_asset_id"] == library.shared, wall
        assert row["cover_at_ms"] == 4200, wall
        assert row["art"], wall


@pytest.mark.parametrize("kind", ["people", "sites"])
def test_an_uploaded_cover_is_removed_by_writing_no_cover(client: TestClient, kind: str) -> None:
    """The page's "Remove the cover" is this write: no file, and so no upload either.

    One statement writes the file, its moment and the upload together, so clearing the file clears
    an uploaded picture with it and the entity goes back to whatever it is drawn as with nothing
    chosen (here nothing), so its cover address answers 404.
    """
    sign_in(client)
    entity = (
        make_person(client, "Nadia Vance")
        if kind == "people"
        else _make_site(client, "Northlight Media Group")
    )
    sent = client.post(
        f"/api/{kind}/{entity}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )
    assert sent.json()["cover_upload_id"] is not None

    removed = client.put(f"/api/{kind}/{entity}/cover", json={"asset_id": None})

    assert removed.status_code == 200, removed.text
    assert removed.json()["cover_upload_id"] is None
    assert removed.json()["cover_asset_id"] is None
    assert client.get(f"/api/{kind}/{entity}/cover").status_code == 404
