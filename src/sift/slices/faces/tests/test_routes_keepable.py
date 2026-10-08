# SPDX-License-Identifier: AGPL-3.0-or-later
"""A face picture asked for by its tokened address: kept a week only when nothing makes it careful."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.kernel.serving import ART_KEY, CAREFUL, KEEPABLE
from sift.slices.faces.tests.test_routes import (  # noqa: F401
    Scene,
    app,
    client,
    scene,
    sign_in,
    turn_on,
    unlock,
)


def test_a_crop_by_its_token_is_kept_unless_it_is_concealed(
    client: TestClient,  # noqa: F811
    scene: Scene,  # noqa: F811
) -> None:
    turn_on(client)
    admin = sign_in(client, "admin")
    crop = f"/api/faces/{scene.track}/crop?{ART_KEY}=1"
    assert client.get(crop).headers["cache-control"] == KEEPABLE["Cache-Control"]

    scene.hide(client, "asset", scene.asset, admin)
    assert unlock(client) == 200
    answer = client.get(crop)

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == CAREFUL["Cache-Control"]


def test_a_cover_falling_back_to_the_small_square_is_careful_by_its_token_too(
    client: TestClient,  # noqa: F811
    scene: Scene,  # noqa: F811
) -> None:
    turn_on(client)
    sign_in(client, "admin")

    answer = client.get(f"/api/faces/{scene.track}/cover?{ART_KEY}=1")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == CAREFUL["Cache-Control"]
