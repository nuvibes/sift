# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every route that CHANGES a cover answers with the thing whose cover it changed.

The client replaces the row it holds with the reply, so a cover route answering `null` makes the
screen report a failure for a write that landed. `api.put<T>` asserts the reply type rather than
deriving it, so no type checker sees this. Every mounted route ending `/cover` or
`/cover-picture` that writes must declare a response model; `ANSWERS_WITH_NOTHING` is empty.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from sift.kernel.config import get_settings
from sift.kernel.jobs import WorkerPool
from sift.main import create_app


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """The real application, for its ROUTE TABLE only, with no worker pool: no request is sent."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


#: Cover writers deliberately answering with no body, and why. Empty: every one's client reads a
#: field off the reply.
ANSWERS_WITH_NOTHING: dict[tuple[str, str], str] = {}

_WRITES = {"PUT", "POST", "PATCH"}


def _leaf_routes(routes: object, prefix: str = "") -> Iterator[tuple[str, object]]:
    """Every route with a path and methods, descending through mounts and included routers.

    `app.routes` holds a lazy wrapper for an included router, not its routes, so the walk descends;
    the authz matrix walks the same way.
    """
    for route in routes:  # type: ignore[attr-defined]
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            yield from _leaf_routes(
                included.routes, prefix + (getattr(context, "prefix", "") or "")
            )
        elif hasattr(route, "routes") and not hasattr(route, "methods"):
            yield from _leaf_routes(route.routes, prefix + str(getattr(route, "path", "")))
        else:
            yield prefix + str(getattr(route, "path", "")), route


def _cover_writers(app: FastAPI) -> list[tuple[str, str, APIRoute]]:
    found: list[tuple[str, str, APIRoute]] = []
    for path, route in _leaf_routes(app.routes):
        if not isinstance(route, APIRoute):
            continue
        if not (path.endswith("/cover") or path.endswith("/cover-picture")):
            continue
        for method in sorted((route.methods or set()) & _WRITES):
            found.append((method, path, route))
    return found


#: The things that carry a cover, by route stem, each set by picking a file (`/cover`) or
#: uploading a picture (`/cover-picture`). A username has no page to choose one on.
COVERED = (
    "/api/collections/{collection_id}",
    "/api/people/{person_id}",
    "/api/photo-sets/{photo_set_id}",
    "/api/sites/{site_id}",
    "/api/songs/{song_id}",
    "/api/tags/{tag_id}",
)


def test_the_family_is_all_here(app: FastAPI) -> None:
    """The family is all here, named, so a scan matching nothing fails."""
    found = sorted((method, path) for method, path, _ in _cover_writers(app))
    expected = sorted(
        [("PUT", stem + "/cover") for stem in COVERED]
        + [("POST", stem + "/cover-picture") for stem in COVERED]
    )
    assert found == expected, (
        f"the cover writers are not the family in COVERED, a pick and an upload each: {found}"
    )


def test_every_cover_write_answers_with_the_row(app: FastAPI) -> None:
    silent = [
        (method, path)
        for method, path, route in _cover_writers(app)
        if route.response_model is None and (method, path) not in ANSWERS_WITH_NOTHING
    ]
    assert not silent, (
        f"these routes change a cover and answer with no body: {sorted(silent)}. "
        "Their clients read the new cover off the reply, so a null body is a TypeError in the "
        "browser that reads as 'that could not be used as the cover' over a write that landed. "
        "Declare the entity's own view as the return type, or say here why this one is different."
    )


@pytest.mark.parametrize("suffix", ["/cover", "/cover-picture"])
def test_the_two_ways_in_answer_alike(app: FastAPI, suffix: str) -> None:
    """Picking a file and uploading a picture answer with the SAME shape, per entity: the screen
    replaces its row with either."""
    by_entity = {
        path.removesuffix(suffix): route.response_model
        for _method, path, route in _cover_writers(app)
        if path.endswith(suffix)
    }
    other = "/cover-picture" if suffix == "/cover" else "/cover"
    theirs = {
        path.removesuffix(other): route.response_model
        for _method, path, route in _cover_writers(app)
        if path.endswith(other)
    }
    differing = {stem for stem, model in by_entity.items() if theirs.get(stem) is not model}
    assert not differing, (
        f"the two ways to set a cover answer with different shapes for: {sorted(differing)}. "
        "One sheet offers both, and the screen replaces its row with whichever answered."
    )
