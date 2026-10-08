# SPDX-License-Identifier: AGPL-3.0-or-later
"""A picture whose bytes Sift never recorded is careful even when asked for by its token."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.content import DerivativeKind
from sift.kernel.serving import ART_KEY, CAREFUL, KEEPABLE
from sift.slices.browse.tests.conftest import Library, give_derivative, sign_in


@pytest.mark.parametrize(
    ("kind", "address"), [(DerivativeKind.THUMB, "thumb"), (DerivativeKind.SPRITE, "sprite")]
)
@pytest.mark.parametrize(("digest", "kept"), [(None, CAREFUL), ("0123456789abcdef", KEEPABLE)])
def test_a_tokened_address_is_kept_only_over_recorded_bytes(
    client: TestClient,
    library: Library,
    kind: DerivativeKind,
    address: str,
    digest: str | None,
    kept: dict[str, str],
) -> None:
    params = {"columns": 5, "rows": 6, "tile_width": 320} if kind is DerivativeKind.SPRITE else None
    give_derivative(client, library.shared, kind, params=params, digest=digest)
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/{address}?{ART_KEY}=1")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == kept["Cache-Control"]
