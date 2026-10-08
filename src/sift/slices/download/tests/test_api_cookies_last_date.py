# SPDX-License-Identifier: AGPL-3.0-or-later
"""A saved jar is judged by its last date: one cookie long run out beside one good for years is
still a jar that works."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.download.tests.api_support import _exported, _unlock

pytestmark = [pytest.mark.integration]

LONG_AGO = 1_000_000_000
YEARS_AHEAD = 4_102_444_800


def test_a_jar_whose_first_cookie_ran_out_reads_by_its_last(client: TestClient) -> None:
    _unlock(client)
    jar = _exported(LONG_AGO, name="clearance") + _exported(YEARS_AHEAD).split("\n", 1)[1]
    made = client.post("/api/site-connections", json={"site": "TikTok", "cookie": jar})
    assert made.status_code == 201

    (row,) = client.get("/api/site-connections").json()

    assert (row["expires_at"], row["expires_last"]) == (LONG_AGO, YEARS_AHEAD)
    assert row["state"] == "saved"
