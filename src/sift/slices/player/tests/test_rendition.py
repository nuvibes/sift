# SPDX-License-Identifier: AGPL-3.0-or-later
"""The copy of a photograph a browser is given when it cannot draw the original.

A phone's HEIC is drawn by Safari and by nothing else, so everywhere else the viewer falls back to
the JPEG Sift makes of the whole picture when it reads the file. The route is bytes off a disk and
is scoped exactly as the stream is: a copy of a picture is not a lesser secret than the picture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.player.tests.conftest import Library, conceal, db_path, sign_in, write

pytestmark = [pytest.mark.integration]

NEVER_EXISTED = "01HX0000000000000000000099"


def _copy(client: TestClient, asset_id: str, body: bytes, *, extension: str = "jpg") -> Path:
    """Put a copy in the cache, as the read of a HEIF photograph would have."""
    cache_dir = Path(client.app.state.settings.cache_dir)  # type: ignore[attr-defined]
    relative = f"re/nd/{asset_id}/rendition.{extension}"
    made = cache_dir / relative
    made.parent.mkdir(parents=True, exist_ok=True)
    made.write_bytes(body)
    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives"
                " (id, asset_id, kind, rel_cache_path, params, size_bytes, created_at)"
                " VALUES (?, ?, 'rendition', ?, '{}', ?, 1)",
                (new_id(), asset_id, relative, len(body)),
            )
        ],
    )
    return made


def test_the_copy_is_served_as_a_jpeg(client: TestClient, library: Library) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    body = b"\xff\xd8\xff standing in for the whole picture"
    _copy(client, asset_id, body)

    response = client.get(f"/api/assets/{asset_id}/rendition")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == body


def test_a_file_with_no_copy_answers_as_a_miss(client: TestClient, library: Library) -> None:
    """Nearly every file: a photograph a browser draws needs none, and a video is played."""
    sign_in(client)

    refused = client.get(f"/api/assets/{library.id_of('h264')}/rendition")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/rendition")

    assert refused.status_code == 404
    assert refused.json() == absent.json()


def test_an_animated_webps_decoder_copy_is_never_handed_to_a_picture(
    client: TestClient, library: Library
) -> None:
    """The same kind of derivative is an animated WebP's MP4, made for the decoders. A picture
    element handed an MP4 draws nothing, so only a JPEG is ever this route's answer."""
    sign_in(client)
    asset_id = library.id_of("h264")
    _copy(client, asset_id, b"an mp4 made for the decoders", extension="mp4")

    assert client.get(f"/api/assets/{asset_id}/rendition").status_code == 404


def test_a_guest_gets_no_copy_of_what_was_not_shared(client: TestClient, library: Library) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    _copy(client, asset_id, b"\xff\xd8\xff")
    sign_in(client, "guest", who="outsider")

    refused = client.get(f"/api/assets/{asset_id}/rendition")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/rendition")

    assert refused.status_code == 404
    assert refused.json() == absent.json(), "a refusal must not read differently from a miss"


def test_a_hidden_photographs_copy_is_hidden_with_it(client: TestClient, library: Library) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    _copy(client, asset_id, b"\xff\xd8\xff")
    conceal(client, asset_id)

    assert client.get(f"/api/assets/{asset_id}/rendition").status_code == 404


def test_a_copy_whose_row_names_a_place_outside_the_cache_answers_as_a_miss(
    client: TestClient, library: Library
) -> None:
    """A row from a restored backup or an older build can name a path that is not in the cache.
    Nothing outside the cache is ever served, and the answer reads like any other miss."""
    sign_in(client)
    asset_id = library.id_of("h264")
    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives"
                " (id, asset_id, kind, rel_cache_path, params, size_bytes, created_at)"
                " VALUES (?, ?, 'rendition', '../../outside.jpg', '{}', 3, 1)",
                (new_id(), asset_id),
            )
        ],
    )

    refused = client.get(f"/api/assets/{asset_id}/rendition")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/rendition")

    assert refused.status_code == 404
    assert refused.json() == absent.json()
