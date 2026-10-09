# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every route the server mounts is one the interface can actually reach.

A finished, tested feature with no caller looks exactly like finished code, at full coverage. So
every method-and-path the app mounts must be either called from the browser client with that
method or named in `NOT_CALLED_FROM_THE_CLIENT` with the reason it is not. The method is part of
the question: a read of an address does not stand in for a delete of it.

A floor, not a ceiling: a route called from a module nothing renders still passes. The generated
declarations (`*.d.ts`) are not read: they list every address the server mounts, so reading them
would pass every route.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI

from sift.main import create_app
from tests.gates import client_source
from tests.gates.test_authz_matrix import mounted_routes

pytestmark = [pytest.mark.gate]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src"

#: Routes with no caller in the client, and why each is not a fault. A reason, not a name: if the
#: sentence is hard to write, somebody usually forgot the button.
NOT_CALLED_FROM_THE_CLIENT: dict[tuple[str, str], str] = {
    ("GET", "/health"): "a container probe and an uptime monitor, deliberately outside the API",
    ("GET", "/{path:path}"): "the client itself, and the pages of it: this is what serves them",
    ("GET", "/api/loops/{loop_id}/thumb"): (
        "a Loop's own picture on its wall. `rowStillUrl` (entity/art.ts) builds it from the wall's "
        "path, so no literal in the client spells it"
    ),
    # --- The player's addresses, consumed but never written by the client: `/playback` answers
    # with the address to use, and every later one comes out of the playlist it points at.
    ("GET", "/api/assets/{asset_id}/hls/master.m3u8"): (
        "the quality ladder. Reached through the address `/playback` hands out for Auto, never "
        "one the client writes"
    ),
    ("GET", "/api/assets/{asset_id}/hls/index.m3u8"): (
        "one rung of that ladder. Its address comes out of the master playlist, or out of the "
        "quality menu the server built"
    ),
    (
        "GET",
        "/api/assets/{asset_id}/hls/{segment}",
    ): "the segments behind those playlists, listed by them",
    # --- Not the browser client at all.
    ("GET", "/api/assets/{asset_id}/local-file"): (
        "the DESKTOP SHELL asks this, not the page: it is how a drag-out finds the file to hand "
        "Windows. The page only ever passes an asset id, which is the security property the bridge "
        "is built on: a page that could name a path could name any path"
    ),
    # `DELETE /api/assets/{asset_id}` is not excused: the entity walls delete through
    # `/${kind}/${id}`, which the wildcard rule reads as reaching it.
}

#: Any path the client writes down, however it uses it: the thumbnail, the preview, the stream and
#: the sprite are addresses put in a `src`, never fetched.
_PATH = re.compile(r"""(['"`])(?P<path>/[^'"`\n]*)\1""")

#: An address written in two pieces, `` `${ROOT}/${id}` ``: the name is resolved against the plain
#: bindings in the same file.
_PREFIXED = re.compile(r"`\$\{(?P<name>[A-Za-z_$][\w$]*)\}(?P<rest>/[^`\n]*)`")

#: A name bound to a path, so the pattern above can be put back together.
_BINDING = re.compile(
    r"""\b(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*['"`](?P<path>/[^'"`\n]*)['"`]"""
)

#: A value the client interpolates into a path, read to the END of the interpolation:
#: `${encodeURIComponent(id)}` holds a `}` of its own.
_PARAM = re.compile(r"\$\{.*?\}")

#: The start of a call through the API helper, and the method it makes.
_CALL = re.compile(
    r"\bapi\s*\.\s*(?P<verb>get|post|put|patch|del|postForFile)\b\s*(?:<.*?>\s*)?\(", re.S
)

#: The same client helper at the level below the verb: `request('PATCH', path)` is what
#: `api.patch(path)` wraps.
_VERB_FIRST_CALL = re.compile(
    r"\brequest\s*(?:<.*?>\s*)?\(\s*['\"](?P<verb>[A-Za-z]+)['\"]\s*,", re.S
)

#: The method named in a hand-written fetch, for the few places that do not go through the helper.
_FETCH_METHOD = re.compile(r"""\bmethod\s*:\s*['"](?P<method>[A-Za-z]+)['"]""")

_METHOD_OF_CALL = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "del": "DELETE",
    "postForFile": "POST",
}


def _call_spans(source: str) -> list[tuple[int, int, str]]:
    """Where each call through the API helper starts and ends, and the method it makes.

    Brackets are counted, because the argument is regularly an object with calls of its own.
    """
    spans: list[tuple[int, int, str]] = []
    found = [(call, _METHOD_OF_CALL[call.group("verb")]) for call in _CALL.finditer(source)]
    found += [(call, call.group("verb").upper()) for call in _VERB_FIRST_CALL.finditer(source)]
    for call, method in found:
        at, depth = call.end(), 1
        while at < len(source) and depth:
            if source[at] == "(":
                depth += 1
            elif source[at] == ")":
                depth -= 1
            at += 1
        spans.append((call.end(), at, method))
    return spans


def _methods_used_in(source: str) -> set[str]:
    """What this file does with an address it holds but does not itself pass to a call.

    An address is often written in one statement and called in another, which no text scan can
    follow, so it is credited with every method the file uses: never wrong in the direction that
    hides a route. A file that makes no request holds addresses to render, which are reads.
    """
    used = {_METHOD_OF_CALL[call.group("verb")] for call in _CALL.finditer(source)}
    used |= {call.group("verb").upper() for call in _VERB_FIRST_CALL.finditer(source)}
    if re.search(r"\b(?:src|href)\s*=", source):
        used.add("GET")
    if re.search(r"\bfetch\s*\(", source):
        used |= {found.group("method").upper() for found in _FETCH_METHOD.finditer(source)} or {
            "GET"
        }
    if re.search(r"\b(?:WebSocket|EventSource)\b", source):
        used.add("WS")
    return used or {"GET"}


def _client_calls() -> set[tuple[str, str]]:
    """Every API path the browser client asks for, with its method.

    `/assets/${id}/faces` and `/api/assets/{asset_id}/faces` both become `/api/assets/*/faces`.
    """
    found: set[tuple[str, str]] = set()
    for source_file in client_source(CLIENT, ".ts", ".svelte"):
        source = source_file.read_text(encoding="utf-8")
        spans = _call_spans(source)
        otherwise = _methods_used_in(source)
        bindings = {bind.group("name"): bind.group("path") for bind in _BINDING.finditer(source)}
        # The prefix the API helper puts in front of every path it is given.
        bindings.setdefault("API_PREFIX", "/api")

        written: list[tuple[int, str]] = [
            (match.start(), match.group("path")) for match in _PATH.finditer(source)
        ]
        written += [
            (match.start(), bindings[match.group("name")] + match.group("rest"))
            for match in _PREFIXED.finditer(source)
            if match.group("name") in bindings
        ]

        for at, raw in written:
            path = _PARAM.sub("*", raw).split("?")[0].rstrip("/")
            if not path:
                continue
            inside = {method for start, end, method in spans if start <= at < end}
            for method in inside or otherwise:
                # Recorded with and without the prefix the API helper adds.
                found.add((method, path))
                if not path.startswith("/api"):
                    found.add((method, f"/api{path}"))
    return found


def _shape(path: str) -> str:
    """A declared path with its parameters blanked, so the two sides can be compared."""
    return re.sub(r"\{[^}]*\}", "*", path).rstrip("/")


def _reached_by(method: str, shape: str, called: set[tuple[str, str]]) -> bool:
    """Whether any address the client asks for with this method could be this route.

    Segment by segment, because a wildcard can sit on EITHER side: the client's
    `/${kind}/${id}/tags` blanks to `/api/*/*/tags`, the server's `/api/people/{person_id}/tags` to
    `/api/people/*/tags`.
    """
    wanted = shape.split("/")
    for asked, candidate in called:
        if asked != method:
            continue
        parts = candidate.split("/")
        if len(parts) != len(wanted):
            continue
        if all(a == b or "*" in (a, b) for a, b in zip(wanted, parts, strict=True)):
            return True
    return False


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


def test_every_route_has_something_that_calls_it(app: FastAPI) -> None:
    called = _client_calls()
    unreachable = sorted(
        f"{method} {path}"
        for method, path in mounted_routes(app)
        if (method, path) not in NOT_CALLED_FROM_THE_CLIENT
        and not _reached_by(method, _shape(path), called)
    )

    assert not unreachable, (
        "\nThese routes exist and nothing in the browser client calls them.\n\n"
        "That is the failure this gate was written for: a feature finished, tested, merged and\n"
        "unreachable, which looks exactly like a feature that works. Either wire it up, or add it\n"
        "to NOT_CALLED_FROM_THE_CLIENT with the reason it does not need wiring.\n\n  "
        + "\n  ".join(unreachable)
        + "\n"
    )


def test_the_excuses_are_all_still_routes(app: FastAPI) -> None:
    """Every excuse names a route that is still mounted."""
    mounted = mounted_routes(app)
    stale = sorted(
        f"{method} {path}"
        for method, path in NOT_CALLED_FROM_THE_CLIENT
        if (method, path) not in mounted
    )

    assert not stale, f"\nThese are excused and no longer exist:\n  {'\n  '.join(stale)}\n"


def test_no_excuse_is_for_a_route_the_client_now_calls() -> None:
    """No excuse is for a route the client now calls: wiring a screen to one makes its reason
    false, and nothing makes anybody re-read this file."""
    called = _client_calls()
    answered = sorted(
        f"{method} {path}"
        # Only the API: `/health` and `/*` are two segments, so any one-segment address would
        # match them under the wildcard rule.
        for method, path in NOT_CALLED_FROM_THE_CLIENT
        if path.startswith("/api/") and _reached_by(method, _shape(path), called)
    )

    assert not answered, (
        "\nThese are excused as having no caller, and the client now calls them.\n\n"
        "The excuse is the stale thing here, not the code: delete the entry.\n\n  "
        + "\n  ".join(answered)
        + "\n"
    )


def test_the_check_can_tell_when_a_route_is_unreachable() -> None:
    """A route the client certainly does not call is caught: an empty list is also what a broken
    scan produces."""
    called = _client_calls()

    assert ("POST", _shape("/api/faces/packs/import")) in called, (
        "the scan cannot see a call it should: pack import is called from the Identify pane"
    )
    assert ("POST", _shape("/api/nothing/calls/this")) not in called


def test_the_check_can_tell_one_method_from_another() -> None:
    """The search history is written and cleared by the client and never read back, so two of its
    methods are reached and the third is not."""
    called = _client_calls()
    history = _shape("/api/search/history")

    assert ("POST", history) in called
    assert ("DELETE", history) in called
    assert ("GET", history) not in called, (
        "the scan credits a method the client does not use, so a route can pass on a sibling's call"
    )


def test_a_call_with_one_method_does_not_reach_a_route_with_another() -> None:
    """A call with one method does not reach a route with another.

    Removing the method comparison changes no other assertion in this file.
    """
    called = {("DELETE", "/api/search/history")}

    assert _reached_by("DELETE", "/api/search/history", called)
    assert not _reached_by("GET", "/api/search/history", called), (
        "a read is being served by a delete of the same address"
    )


def test_a_call_is_read_to_its_own_closing_bracket() -> None:
    """An address in a later argument still belongs to the call it is written in."""
    spans = _call_spans("api.post(build(a), '/somewhere')")

    assert len(spans) == 1
    start, end, method = spans[0]
    assert method == "POST"
    assert "'/somewhere'" in "api.post(build(a), '/somewhere')"[start:end]


def test_the_generated_declarations_are_not_read() -> None:
    """The generated schema is really there, and the scan skips it: read, it names every route."""
    generated = CLIENT / "lib" / "api" / "schema.d.ts"
    assert generated.exists(), "the generated schema moved; the exclusion below now guards nothing"

    text = generated.read_text(encoding="utf-8")
    # Taken from the excuses: a route written out here goes stale the day a screen calls it.
    uncalled = [
        (method, path)
        for method, path in NOT_CALLED_FROM_THE_CLIENT
        if path.startswith("/api/") and f'"{path}"' in text
    ]
    assert uncalled, "no excused route is in the generated schema; this test guards nothing"

    called = _client_calls()

    assert not [
        f"{method} {path}" for method, path in uncalled if _reached_by(method, _shape(path), called)
    ], "the generated declarations are being read as if they were calls"
