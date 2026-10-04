# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who gets the bytes, tested on every route that returns any: a viewer who may not see an asset
gets nothing, byte for byte the answer for a made-up id. Each route has its own test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.player import tuning
from sift.slices.player.tests.conftest import (
    ANCIENT,
    Library,
    conceal,
    share,
    sign_in,
)

pytestmark = [pytest.mark.integration]

#: An id that is well-formed and has never existed. The control in every comparison below.
NEVER_EXISTED = "01HX0000000000000000000099"


def _guest(client: TestClient) -> str:
    return sign_in(client, "guest", who="outsider")


# --- a guest who was shown nothing ---------------------------------------------------------------


def test_a_guest_cannot_stream_an_asset_nobody_shared(client: TestClient, library: Library) -> None:
    _guest(client)
    asset_id = library.id_of("h264")

    refused = client.get(f"/api/assets/{asset_id}/stream")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/stream")

    assert refused.status_code == 404
    assert refused.status_code == absent.status_code
    assert refused.json() == absent.json(), "a refusal must not read differently from a miss"


def test_a_guest_cannot_fetch_the_playlist_of_an_asset_nobody_shared(
    client: TestClient, library: Library
) -> None:
    """A playlist is not bytes of video, but it is a description of a file that exists.

    Leaking it would confirm the asset, its duration and its segment count.
    """
    _guest(client)
    asset_id = library.id_of("hevc")

    refused = client.get(f"/api/assets/{asset_id}/hls/index.m3u8")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/hls/index.m3u8")

    assert refused.status_code == 404
    assert refused.json() == absent.json()


def test_a_guest_cannot_fetch_a_segment_of_an_asset_nobody_shared(
    client: TestClient, library: Library
) -> None:
    """The one that actually returns video. Tested separately from the playlist on purpose."""
    _guest(client)
    asset_id = library.id_of("hevc")
    segment = f"0{tuning.SEGMENT_SUFFIX}"

    refused = client.get(f"/api/assets/{asset_id}/hls/{segment}")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/hls/{segment}")

    assert refused.status_code == 404
    assert refused.json() == absent.json()


def test_a_guest_cannot_ask_for_a_playback_plan_for_an_asset_nobody_shared(
    client: TestClient, library: Library
) -> None:
    """The plan names the codec, the resolution and the duration. That is metadata about a file
    this person is not supposed to know exists."""
    _guest(client)
    asset_id = library.id_of("hevc")

    refused = client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT)
    absent = client.post(f"/api/assets/{NEVER_EXISTED}/playback", json=ANCIENT)

    assert refused.status_code == 404
    assert refused.json() == absent.json()


def test_a_guest_cannot_record_a_view_against_an_asset_nobody_shared(
    client: TestClient, library: Library
) -> None:
    """Otherwise it is an oracle: try ids, and the ones that accept a view are real."""
    _guest(client)
    asset_id = library.id_of("h264")

    refused = client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 0})
    absent = client.post(f"/api/assets/{NEVER_EXISTED}/view", json={"watch_ms": 0})

    assert refused.status_code == 404
    assert refused.json() == absent.json()


# --- a guest who was shown the asset --------------------------------------------------------------


def test_a_guest_can_play_what_was_actually_shared_with_them(
    client: TestClient, library: Library
) -> None:
    """The other half. A scope that refuses everybody is not a scope, it is a broken route."""
    user_id = _guest(client)
    asset_id = library.id_of("h264")
    share(client, asset_id, user_id)

    assert client.get(f"/api/assets/{asset_id}/stream").status_code == 200


def test_a_guest_shown_an_asset_can_fetch_its_segments(
    client: TestClient, library: Library
) -> None:
    user_id = _guest(client)
    asset_id = library.id_of("hevc")
    share(client, asset_id, user_id)

    assert client.get(f"/api/assets/{asset_id}/hls/index.m3u8").status_code == 200
    assert client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}").status_code == 200


# --- the vault --------------------------------------------------------------------------------------


def test_a_vaulted_asset_is_not_streamable_even_by_an_admin_who_has_not_unlocked_it(
    client: TestClient, library: Library
) -> None:
    """Concealment is not a permission, and this is the distinction that makes it work.

    An admin owns the library and may see everything in it, but a vaulted file is *concealed*
    until Show Hidden is unlocked, and the whole point is that until then it does not appear to
    exist. An admin bypass here would mean the vault protects nothing on a single-admin instance,
    which is most instances.
    """
    sign_in(client)
    asset_id = library.id_of("h264")
    conceal(client, asset_id)

    refused = client.get(f"/api/assets/{asset_id}/stream")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/stream")

    assert refused.status_code == 404
    assert refused.json() == absent.json()


def test_a_vaulted_asset_serves_no_segments_either(client: TestClient, library: Library) -> None:
    """Tested on the HLS route separately, because it is a different handler."""
    sign_in(client)
    asset_id = library.id_of("hevc")
    conceal(client, asset_id)

    assert client.get(f"/api/assets/{asset_id}/hls/index.m3u8").status_code == 404
    assert client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}").status_code == 404


def test_a_vaulted_asset_yields_no_playback_plan(client: TestClient, library: Library) -> None:
    sign_in(client)
    asset_id = library.id_of("hevc")
    conceal(client, asset_id)

    assert client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT).status_code == 404


# --- signed out --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/stream",
        "/hls/index.m3u8",
        f"/hls/0{tuning.SEGMENT_SUFFIX}",
    ],
)
def test_nothing_is_served_to_somebody_who_is_not_signed_in(
    client: TestClient, library: Library, path: str
) -> None:
    """No session, no bytes. The authz matrix asserts this across every route in the app; this
    says it again here, where the bytes are, because it is the assumption everything else rests on.
    """
    response = client.get(f"/api/assets/{library.id_of('hevc')}{path}")
    assert response.status_code == 401
