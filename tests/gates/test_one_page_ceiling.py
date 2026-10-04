# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every paged route accepts the same largest page, and it is the one the client asks for.

The client sizes a page by measuring the screen, capped at `MAX_PAGE_SIZE`; a route declaring a
lower ceiling is refused as malformed before any handler runs, so a big monitor fails where a laptop
works. Every route with a `limit` declares `MAX_PAGE_SIZE` or is named in `NOT_A_PAGE` with why,
read from the published schema the framework enforces.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from sift.kernel.config import get_settings
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.main import create_app

pytestmark = [pytest.mark.gate]

#: Routes whose `limit` means something other than rows on a page, each with why.
NOT_A_PAGE: dict[tuple[str, str], str] = {
    ("GET", "/api/assets/{asset_id}/history"): (
        "not a page at all: there is no offset and no pager, so `limit` bounds ONE read of a whole "
        "thread rather than opening a window into it. The panel draws the thread from the top and "
        "a screen measures nothing against it, so there is no client page size for this ceiling to "
        "agree with, and borrowing the page ceiling would say this is a page and invite an offset "
        "that does not exist. When a file's history outgrows one read it gets a cursor over the "
        "moment each event happened, and becomes an ordinary page"
    ),
    ("GET", "/api/people/{person_id}/history"): (
        "not a page at all, for the same reason a file's history is not: there is no offset and no "
        "pager, so `limit` bounds ONE read of a whole thread rather than opening a window into it. "
        "The tab draws the thread from the top and no screen measures anything against it, so "
        "there is no client page size for this ceiling to agree with"
    ),
    # The four entity histories beside a person's: one read, no offset, no pager.
    ("GET", "/api/sites/{site_id}/history"): (
        "not a page at all, for the reason a person's history is not: there is no offset and no "
        "pager, so `limit` bounds ONE read of a whole thread rather than opening a window into it"
    ),
    ("GET", "/api/tags/{tag_id}/history"): (
        "not a page at all, for the reason a person's history is not: there is no offset and no "
        "pager, so `limit` bounds ONE read of a whole thread rather than opening a window into it"
    ),
    ("GET", "/api/collections/{collection_id}/history"): (
        "not a page at all, for the reason a person's history is not: there is no offset and no "
        "pager, so `limit` bounds ONE read of a whole thread rather than opening a window into it"
    ),
    ("GET", "/api/photo-sets/{photo_set_id}/history"): (
        "not a page at all, for the reason a person's history is not: there is no offset and no "
        "pager, so `limit` bounds ONE read of a whole thread rather than opening a window into it"
    ),
    ("GET", "/api/songs/{song_id}/history"): (
        "not a page at all, for the reason a person's history is not: there is no offset and no "
        "pager, so `limit` bounds ONE read of a whole thread rather than opening a window into it"
    ),
}


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def _limit_ceilings(app: FastAPI) -> dict[tuple[str, str], int | None]:
    """Every (method, path) carrying a `limit` and its maximum; None is no upper bound at all."""
    found: dict[tuple[str, str], int | None] = {}
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                if parameter.get("name") != "limit" or parameter.get("in") != "query":
                    continue
                schema = parameter.get("schema", {})
                # An optional parameter is published as a union with null, and the constraint sits
                # on the non-null branch rather than at the top level.
                branches = schema.get("anyOf", [schema])
                ceiling = next(
                    (branch.get("maximum") for branch in branches if "maximum" in branch), None
                )
                found[(method.upper(), path)] = ceiling
    return found


def test_every_paged_route_accepts_the_page_the_client_asks_for(app: FastAPI) -> None:
    wrong = {
        route: ceiling
        for route, ceiling in _limit_ceilings(app).items()
        if route not in NOT_A_PAGE and ceiling != MAX_PAGE_SIZE
    }

    assert not wrong, (
        "These routes cap `limit` somewhere other than MAX_PAGE_SIZE, so a screen big enough to\n"
        "want a larger page has every one of its requests refused as malformed, and the screen\n"
        "reports that as the thing being missing. Raise the ceiling, or name the route in\n"
        "NOT_A_PAGE with the reason its limit is not a page:\n"
        + "\n".join(
            f"  {method} {path}: {ceiling if ceiling is not None else 'NO CEILING AT ALL'}"
            for (method, path), ceiling in sorted(wrong.items())
        )
    )


def test_nothing_is_excused_that_is_not_there(app: FastAPI) -> None:
    """No excuse outlives its route."""
    stale = sorted(set(NOT_A_PAGE) - set(_limit_ceilings(app)))

    assert not stale, (
        "These are excused in NOT_A_PAGE and no longer take a `limit` query parameter. Remove\n"
        "them, or the list becomes a description of a server that no longer exists:\n"
        + "\n".join(f"  {method} {path}" for method, path in stale)
    )
