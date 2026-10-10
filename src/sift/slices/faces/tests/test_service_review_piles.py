# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review's pile pages: a page every window of which shows nothing stops after a few reads."""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.access import Role
from sift.kernel.db import Database
from sift.slices.faces import service_review
from sift.slices.faces.models import PileStatus
from sift.slices.faces.service import FaceService
from sift.testing.fixtures import create_user


async def test_a_page_of_piles_that_show_no_face_stops_after_a_few_reads(
    service: FaceService, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every window comes back with nothing this viewer may see: the page is cut short after a
    few reads, with the total still the whole count, rather than read to the end of the library."""
    admin = await create_user(temp_db, Role.ADMIN)
    reads: list[int] = []

    async def windows(*_args: object, offset: int, **_kwargs: object) -> tuple[list[Any], int]:
        reads.append(offset)
        return [(f"pile-{offset}", 1)], 9

    async def none_drawn(*_args: object, **_kwargs: object) -> list[Any]:
        return []

    monkeypatch.setattr(service._repository, "waiting_piles", windows)
    monkeypatch.setattr(service, "_drawn_piles", none_drawn)

    assert await service.piles(admin, PileStatus.OPEN, page_size=5) == ([], 9)
    assert reads == list(range(service_review._WINDOWS_AT_MOST))
