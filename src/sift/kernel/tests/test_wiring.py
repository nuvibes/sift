# SPDX-License-Identifier: AGPL-3.0-or-later
"""The typed lookup every route reads the application through.

Every request executes this module, so its coverage is high whether or not it is checked. So the
behaviour is asserted: a missing part fails loudly by name, an optional one is a quiet None, and a
socket is served exactly as a request is.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection, Request

from sift.kernel.wiring import (
    Part,
    hold,
    part_of,
    part_of_app,
    part_of_app_or_none,
    part_or_none,
    provide,
)

SOMETHING: Part[str] = Part("something_for_the_tests")
ABSENT: Part[str] = Part("never_provided_at_all")


def _connection(app: FastAPI, *, kind: str = "http") -> HTTPConnection:
    """A connection carrying the application, without a server: HTTP, or a WebSocket, which is an
    `HTTPConnection` that never reaches the request class."""
    return HTTPConnection(
        {
            "type": kind,
            "app": app,
            "headers": Headers({}).raw,
            "path": "/",
            "query_string": b"",
        }
    )


@pytest.fixture
def app() -> FastAPI:
    return FastAPI()


# --- what a part is


def test_a_part_is_its_name() -> None:
    """Two parts of the same name are the same part: the downloader is published under the kernel's
    declared name and read through it."""
    assert Part("downloads").name == Part("downloads").name
    assert repr(SOMETHING) == "Part('something_for_the_tests')"


# --- reading one


def test_a_provided_part_comes_back(app: FastAPI) -> None:
    provide(app, SOMETHING, "the value")

    assert part_of(_connection(app), SOMETHING) == "the value"
    assert part_of_app(app, SOMETHING) == "the value"


def test_a_socket_reads_a_part_exactly_as_a_request_does(app: FastAPI) -> None:
    """A socket reads a part as a request does: the live job feed is one."""
    provide(app, SOMETHING, "the value")

    assert part_of(_connection(app, kind="websocket"), SOMETHING) == "the value"


def test_a_missing_part_raises_and_names_itself(app: FastAPI) -> None:
    """A missing part raises naming itself at the first request that wants it."""
    with pytest.raises(RuntimeError, match="never_provided_at_all"):
        part_of(_connection(app), ABSENT)

    with pytest.raises(RuntimeError, match="never_provided_at_all"):
        part_of_app(app, ABSENT)


def test_an_optional_part_is_none_rather_than_a_fault(app: FastAPI) -> None:
    """An optional part is None; the downloader is the live example."""
    assert part_or_none(_connection(app), ABSENT) is None

    provide(app, SOMETHING, "the value")
    assert part_or_none(_connection(app), SOMETHING) == "the value"


def test_an_optional_part_read_from_the_application_is_none_before_it_is_built(
    app: FastAPI,
) -> None:
    """At start-up too: a builder reads a part built after it when USED, and until then it is
    None."""
    assert part_of_app_or_none(app, ABSENT) is None

    provide(app, SOMETHING, "the value")
    assert part_of_app_or_none(app, SOMETHING) == "the value"


# --- publishing one from a running request


def test_hold_publishes_from_a_request_and_is_read_back(app: FastAPI) -> None:
    """State built on first use (the self-test) is published from a request and read back."""
    connection = _connection(app)
    assert part_or_none(connection, SOMETHING) is None

    hold(connection, SOMETHING, "built on demand")

    assert part_of(connection, SOMETHING) == "built on demand"
    assert part_of_app(app, SOMETHING) == "built on demand"


def test_a_part_held_once_is_the_same_object_next_time(app: FastAPI) -> None:
    """A part held once is the same object next time, which is why the self-test cannot start
    twice."""
    connection = _connection(app)
    first = object()
    held: Part[object] = Part("held_once")

    hold(connection, held, first)

    assert part_of(_connection(app), held) is first


# --- the named accessors


def _named_accessors() -> list[str]:
    """Every accessor this module publishes: a module-level function of one `request` argument."""
    from sift.kernel import wiring

    found = []
    for name in dir(wiring):
        thing = getattr(wiring, name)
        if not inspect.isfunction(thing) or name.startswith("_"):
            continue
        if thing.__module__ != wiring.__name__:
            continue
        arguments = list(inspect.signature(thing).parameters)
        if arguments == ["request"] and name.upper() in dir(wiring):
            found.append(name)
    return sorted(found)


def test_every_accessor_reads_the_part_it_is_named_for(app: FastAPI) -> None:
    """Every accessor reads the part it is named for, the copy-paste mistake checked as one
    correspondence."""
    from sift.kernel import wiring

    names = _named_accessors()
    assert len(names) > 10, "the accessors were not found where this test looks for them"

    for name in names:
        part: Part[str] = Part(getattr(wiring, name.upper()).name)
        marker = f"the {name} part"
        provide(app, part, marker)

        got = getattr(wiring, name)(_connection(app))

        assert got == marker, f"the {name}() accessor read something other than {part.name!r}"


@pytest.mark.anyio
async def test_where_files_sit_is_read_from_the_apps_own_three_parts(app: FastAPI) -> None:
    """`kernel.where` is read from the viewer's folders, the libraries' paths and the viewer's
    setting, each from its own part."""
    from sift.kernel import where, wiring
    from sift.kernel.access import Role, Viewer

    asked: list[str | None] = []

    class Folders:
        async def visible_folders(self, viewer: Viewer, *, root_id: str | None = None) -> list[Any]:
            asked.append(root_id)
            return [SimpleNamespace(root_id="r1", rel_path="", name="Library", concealed=False)]

    class Libraries:
        async def roots(self) -> list[Any]:
            return [SimpleNamespace(id="r1", abs_path="/srv/Library")]

    class Settings:
        async def get_user(self, user_id: str, key: str) -> object:
            return False

    provide(app, cast(Any, wiring.ACCESS), Folders())
    provide(app, cast(Any, wiring.LIBRARY), Libraries())
    provide(app, cast(Any, wiring.SETTINGS_HUB), Settings())
    admin = Viewer(id="admin", role=Role.ADMIN)
    request = Request({"type": "http", "app": app, "headers": [], "path": "/", "query_string": b""})

    said = await wiring.whereabouts(request, admin, root_id="r1")

    assert said == await where.whereabouts(
        admin,
        access=cast(Any, Folders()),
        library=cast(Any, Libraries()),
        settings=cast(Any, Settings()),
        root_id="r1",
    )
    assert said.tops == {"r1": "/srv/Library"}
    assert asked == ["r1", "r1"]
