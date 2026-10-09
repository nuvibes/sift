# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every route, called over HTTP as each kind of user, answers what `MATRIX` says it should.

A behavioural gate: a static check proves a route has an authorization check, not that it answers
right. A route missing from `MATRIX` fails the build, so adding one means deciding who may call it.
"""

from __future__ import annotations

import re
from collections.abc import (
    Iterator,
    Mapping,
)
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi import (
    FastAPI,
    WebSocket,
)
from fastapi.routing import APIWebSocketRoute
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from starlette.routing import WebSocketRoute

from sift.kernel.access import (
    Role,
    Viewer,
)
from sift.kernel.config import get_settings
from sift.kernel.http import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
)
from sift.main import create_app
from sift.slices.backup import recycle
from sift.slices.media_edit.jobs import SAMPLES_DIRECTORY
from sift.slices.media_edit.service import COMPRESS_SAMPLE
from sift.testing.auth import establish_session
from sift.testing.authz import (
    booted,
    shared_client,
)
from sift.testing.jobs import (
    seed_job,
    seed_run,
)
from sift.testing.library import (
    attach_person,
    attach_tag,
    attach_username,
    seed_alias,
    seed_art,
    seed_asset,
    seed_claim,
    seed_collection,
    seed_cover_picture,
    seed_decision,
    seed_face,
    seed_grant,
    seed_guest_user,
    seed_link,
    seed_loop,
    seed_loop_still,
    seed_move,
    seed_person,
    seed_photo_set,
    seed_quarantined,
    seed_root,
    seed_site,
    seed_song,
    seed_stash_box,
    seed_stash_box_link,
    seed_tag,
    seed_username,
    share_folder,
)
from sift.testing.settings import set_app_setting
from sift.wiring.routes import API_PREFIX
from tests.gates.authz import (
    rows_access,
    rows_catalog,
    rows_downloads,
    rows_faces,
    rows_insights,
    rows_library,
    rows_media,
    rows_shell,
)
from tests.gates.authz.seeds import (
    A_COLLECTION,
    A_COVER_PICTURE,
    A_DECISION,
    A_DELETABLE_COLLECTION,
    A_DELETABLE_GUEST_USER,
    A_DELETABLE_PHOTO_SET,
    A_DELETABLE_SITE,
    A_DELETABLE_SONG,
    A_DELETABLE_STASH_BOX,
    A_DELETABLE_USERNAME,
    A_FACE,
    A_FINISHED_RUN,
    A_FOLDER,
    A_GRANT,
    A_GUEST_USER,
    A_LINK,
    A_LINKED_PERSON,
    A_LOOP,
    A_MERGED_SONG,
    A_MOVE,
    A_PERSON,
    A_PHOTO_SET,
    A_QUARANTINED,
    A_ROOT,
    A_SAMPLE_JOB,
    A_SITE,
    A_SONG,
    A_STASH_BOX,
    A_SUGGESTION,
    A_TAG,
    A_USERNAME,
    AN_ALIAS,
    AN_ASSET,
    AN_UNMARKED_BACKUP,
    ANOTHER_SUGGESTION,
    ART_CREATOR_SCOPE,
    ART_SITE,
    CANCELABLE_JOB,
    DELETABLE_ASSET,
    DELETABLE_FOLDER,
    DELETABLE_ROOT,
    FAILED_JOB,
    MERGE_FROM,
    MERGE_INTO,
    SITE_MERGE_FROM,
    SITE_MERGE_INTO,
    WEBSOCKET,
    Case,
    Policy,
)

pytestmark = [pytest.mark.gate, pytest.mark.integration]

MATRIX: dict[tuple[str, str], Case] = {
    **rows_access.ROWS,
    **rows_library.ROWS,
    **rows_downloads.ROWS,
    **rows_media.ROWS,
    **rows_catalog.ROWS,
    **rows_shell.ROWS,
    **rows_faces.ROWS,
    **rows_insights.ROWS,
}

#: What being turned away looks like. Which of these a route returns is the route's business:
#: an object nobody may see is a 404 rather than a 403, because a 403 confirms it exists.
DENIED = frozenset({401, 403, 404})

#: Named once so the app-lock tests can set a PIN with it.
MATRIX_PASSWORD = "Matrix-Test-Passw0rd!"

#: HEAD runs the GET handler and OPTIONS is answered before any handler, so the matrix is written
#: in handlers rather than verbs.
MIRRORED_METHODS = frozenset({"HEAD", "OPTIONS"})

#: A path placeholder, `{id}` or `{path:path}`: str.format reads `:path` as a format spec.
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^{}]+)?\}")


def fill(path: str, params: Mapping[str, str], query: Mapping[str, str] | None = None) -> str:
    """Substitute a route's placeholders from a Case's params, and add anything it requires."""

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(
                f"{path} has a {{{name}}} placeholder and its matrix entry gives no value for it; "
                f"add params={{'{name}': ...}} to its Case."
            )
        return params[name]

    filled = _PLACEHOLDER.sub(one, path)
    if not query:
        return filled
    return f"{filled}?{urlencode(query)}"


def _leaf_routes(routes: object, prefix: str = "") -> Iterator[tuple[str, object]]:
    """Every route with a path and methods, descending through mounts and included routers.

    Yields the path the app really answers on: newer FastAPI keeps an included router as a wrapper
    holding the prefix, so the prefix is carried down."""
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


def _methods_of(route: object) -> set[str]:
    """The ways a route can be called, as the matrix names them.

    A WebSocket route has no `methods`; it is recognized by its websocket-scope ASGI handler.
    """
    methods = getattr(route, "methods", None)
    if methods:
        return {method for method in methods if method not in MIRRORED_METHODS}
    if isinstance(route, APIWebSocketRoute | WebSocketRoute):
        return {WEBSOCKET}
    return set()


def mounted_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, path) for path, route in _leaf_routes(app.routes) for method in _methods_of(route)
    }


def seed_the_library(db_path: Path, tmp_path: Path) -> None:
    """Everything the routes in `MATRIX` are asked about, written into one booted app."""
    seed_job(db_path, FAILED_JOB, state="failed")
    seed_job(db_path, CANCELABLE_JOB, state="blocked")
    seed_run(db_path, A_FINISHED_RUN)
    seed_root(db_path, A_ROOT, folder_id=A_FOLDER, path=tmp_path / "library")
    seed_grant(db_path, A_GRANT, path=tmp_path / "granted")
    seed_root(db_path, DELETABLE_ROOT, folder_id=DELETABLE_FOLDER, path=tmp_path / "library-two")
    seed_asset(
        db_path,
        AN_ASSET,
        root_id=A_ROOT,
        folder_id=A_FOLDER,
        root_path=tmp_path / "library",
        cache_dir=tmp_path / "cache",
    )
    seed_asset(
        db_path,
        DELETABLE_ASSET,
        root_id=A_ROOT,
        folder_id=A_FOLDER,
        root_path=tmp_path / "library",
        cache_dir=tmp_path / "cache",
        filename="deletable.mp4",
    )
    seed_move(db_path, A_MOVE, root_id=A_ROOT)
    seed_decision(db_path, A_DECISION)
    seed_claim(db_path, A_SUGGESTION, folder_id=A_FOLDER, proposed="Seeded Claim")
    seed_claim(db_path, ANOTHER_SUGGESTION, folder_id=A_FOLDER, proposed="Second Claim")
    seed_job(db_path, A_SAMPLE_JOB, state="done", job_type=COMPRESS_SAMPLE)
    samples = tmp_path / "cache" / SAMPLES_DIRECTORY
    samples.mkdir(parents=True, exist_ok=True)
    (samples / f"{A_SAMPLE_JOB}.mp4").write_bytes(b"not really a video")
    seed_tag(db_path, A_TAG)
    # On the visible asset, or the by-id read would answer by the scoping rule.
    attach_tag(db_path, AN_ASSET, A_TAG)
    seed_art(db_path, ART_SITE, cache_dir=tmp_path / "cache")
    seed_art(db_path, ART_CREATOR_SCOPE, cache_dir=tmp_path / "cache")
    seed_face(db_path, A_FACE, AN_ASSET, data_dir=tmp_path / "data")
    seed_person(db_path, A_PERSON)
    seed_person(db_path, MERGE_FROM)
    seed_person(db_path, MERGE_INTO)
    seed_person(db_path, A_LINKED_PERSON)
    # On the visible asset, so the people routes answer by their own rule.
    attach_person(db_path, AN_ASSET, A_PERSON)
    seed_alias(db_path, AN_ALIAS, A_PERSON)
    seed_link(db_path, A_LINK, A_PERSON)
    seed_site(db_path, A_SITE)
    seed_username(db_path, A_USERNAME, A_SITE)
    attach_username(db_path, AN_ASSET, A_USERNAME)
    seed_collection(db_path, A_COLLECTION, holding=AN_ASSET)
    seed_photo_set(db_path, A_PHOTO_SET, holding=AN_ASSET)
    seed_photo_set(db_path, A_DELETABLE_PHOTO_SET, name="deletable photo set", holding=AN_ASSET)
    seed_song(db_path, A_SONG, holding=AN_ASSET)
    seed_song(db_path, A_DELETABLE_SONG, name="deletable song")
    seed_song(db_path, A_MERGED_SONG, name="merged song")
    seed_cover_picture(
        db_path,
        A_COVER_PICTURE,
        cache_dir=tmp_path / "cache",
        wearing={
            "people": A_PERSON,
            "sites": A_SITE,
            "tags": A_TAG,
            "collections": A_COLLECTION,
            "photo_sets": A_PHOTO_SET,
            "songs": A_SONG,
        },
    )
    seed_loop(db_path, A_LOOP, AN_ASSET)
    seed_stash_box(db_path, A_STASH_BOX)
    seed_stash_box_link(db_path, A_LINKED_PERSON, A_STASH_BOX)
    seed_quarantined(tmp_path / "data", A_QUARANTINED)
    # Read from the folder used when none is chosen.
    (tmp_path / "data" / "backups").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "backups" / AN_UNMARKED_BACKUP).write_bytes(b"seeded backup")
    seed_stash_box(
        db_path,
        A_DELETABLE_STASH_BOX,
        name="deletable stash-box",
        endpoint="https://other-stash-box.invalid/graphql",
    )
    # The Loop's own still; the job that builds one does not run here.
    seed_loop_still(db_path, tmp_path / "cache", AN_ASSET)
    seed_collection(db_path, A_DELETABLE_COLLECTION, name="deletable collection", holding=AN_ASSET)
    seed_site(db_path, A_DELETABLE_SITE, name="deletable site")
    seed_site(db_path, SITE_MERGE_FROM, name="merging site")
    seed_site(db_path, SITE_MERGE_INTO, name="surviving site")
    seed_username(db_path, A_DELETABLE_USERNAME, A_DELETABLE_SITE, name="deletable_username")
    seed_guest_user(db_path, A_GUEST_USER)
    seed_guest_user(db_path, A_DELETABLE_GUEST_USER, username="deletable guest")
    # Left at its default the capability refuses every guest, and the route would look
    # role-restricted when it is not.
    set_app_setting(db_path, "guests.can_save_to_device", "true")
    # A gate whose result depends on the machine having a network is not a gate.
    set_app_setting(db_path, "updates.check_for_new_versions", "false")


#: One app, booted and seeded once for the module, for every case that only reads.
library = shared_client(seed_the_library, name="library")


@pytest.fixture
def app(library: TestClient) -> FastAPI:
    """The shared app, for the cases that read its route table."""
    assert isinstance(library.app, FastAPI)
    return library.app


@pytest.fixture
def own_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app and a seeded library of the case's own, for the sweep that signs in.

    A deleted backup would go to the real Recycle Bin of whoever runs the suite on Windows, so the
    drive is told it has none.
    """
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda _place: False)
    with booted(tmp_path, seed_the_library) as client:
        yield client


@pytest.fixture
def own_app(tmp_path: Path) -> Iterator[TestClient]:
    """An app of the case's own and no library: a locked session is refused before any lookup."""
    with booted(tmp_path) as client:
        yield client


@pytest.fixture
def app_to_plant_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """An app of the case's own, never started, for a case that plants a route."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def call(
    client: TestClient, method: str, path: str, body: Mapping[str, object] | None = None
) -> int:
    """Make the request and report a status, whatever protocol it took.

    A WebSocket is accepted or closed, so its answer is translated into a status and held to the
    same table as every other route.
    """
    if method != WEBSOCKET:
        if body is None:
            return client.request(method, path).status_code
        return client.request(method, path, json=body).status_code
    try:
        with client.websocket_connect(path):
            return 200
    except WebSocketDisconnect:
        return 403


def sign_in(client: TestClient, viewer: Viewer) -> str:
    """Give the client a real session of a fresh user of this role.

    The user and session rows are written as the auth slice writes them, so routes resolve the
    role the ordinary way. The passed viewer's id is ignored.
    """
    role = viewer.role.value
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path,
        role=role,
        username=f"matrix-{role}",
        password=MATRIX_PASSWORD,
    )
    # A folder nobody shared is *absent* to a guest, which this file cannot tell from a refusal.
    share_folder(db_path, A_FOLDER, user_id, grant_id=f"01HX{user_id[4:]}")
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


# --- the gate -------------------------------------------------------------------------------


def test_every_mounted_route_is_in_the_matrix(app: FastAPI) -> None:
    """The one that makes all the others matter."""
    undeclared = mounted_routes(app) - set(MATRIX)
    assert not undeclared, (
        f"these routes are mounted and nobody has said who may call them: {sorted(undeclared)}. "
        "Add a line to MATRIX saying whether it is public, for any signed-in user, or admin-only."
    )


def test_the_matrix_has_no_entries_for_routes_that_are_gone(app: FastAPI) -> None:
    """A stale entry is a claim about a route that no longer exists, and it reads as coverage."""
    stale = set(MATRIX) - mounted_routes(app)
    assert not stale, f"the matrix describes routes that are not mounted: {sorted(stale)}"


def test_the_route_walk_agrees_with_the_schema(app: FastAPI) -> None:
    """Everything the generated schema lists is something the route walk found.

    The walk depends on where the framework keeps an included router's prefix; the schema is the
    supported answer. Not the reverse: the client's pages are served outside the schema.
    """
    from_schema = {
        (method.upper(), path)
        for path, operations in app.openapi().get("paths", {}).items()
        for method in operations
        if method.upper() not in MIRRORED_METHODS
    }
    missed = from_schema - mounted_routes(app)
    assert not missed, (
        f"the schema says the app serves {sorted(missed)} and the route walk did not find them. "
        "The walk has drifted from how the framework stores routes, so every path it reports is "
        "suspect: fix `_leaf_routes` before trusting this matrix again."
    )


def check(method: str, path: str, case: Case, status: int, *, role: Role | None) -> None:
    """Compare one answer with what the matrix promised; raises AssertionError if it disagrees.

    A function, so it can be pointed at a deliberately broken route and shown to notice.
    """
    who = "an anonymous caller" if role is None else f"a {role.value}"
    denied = status in DENIED

    allowed_to_call = case.policy is Policy.PUBLIC or (
        role is not None and (case.policy is Policy.AUTHENTICATED or role is Role.ADMIN)
    )

    if allowed_to_call:
        # Read as an answer rather than a refusal, and only here. See `Case.answers_anyway`.
        denied = denied and status != case.answers_anyway
        assert not denied, (
            f"{method} {path} is declared {case.policy.value} but turned {who} away ({status})"
        )
    else:
        assert denied, (
            f"{method} {path} is declared {case.policy.value} but served {who} ({status})"
        )


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(MATRIX),
    ids=[f"{method} {path}" for method, path in sorted(MATRIX)],
)
def test_every_route_answers_an_anonymous_caller_the_way_the_matrix_says(
    library: TestClient, method: str, path: str
) -> None:
    """No cookie, no header, no session: the request a stranger with curl makes, the jar cleared."""
    library.cookies.clear()
    assert CSRF_HEADER_NAME not in library.headers
    case = MATRIX[(method, path)]
    check(
        method,
        path,
        case,
        call(library, method, fill(path, case.params, case.query), case.body),
        role=None,
    )


# --- the gate can fail ------------------------------------------------------------------------


def test_the_addresses_retired_with_the_old_words_answer_as_unknown(library: TestClient) -> None:
    """The old addresses for sites, usernames and sign-in accounts are retired, not redirected.

    An old address answers 404, or where it now matches another route's shape (`/assets/platforms`
    is the file called `platforms`), that route's refusal of a stranger. Never a move.
    """
    for was in (
        "/platforms",
        "/platforms/merge",
        "/assets/platforms",
        "/accounts/some-id",
        "/auth/accounts",
        "/sharing/accounts",
    ):
        answer = library.get(API_PREFIX + was, follow_redirects=False)
        assert answer.status_code in (401, 404), was
        assert "location" not in answer.headers, was


def test_a_route_nobody_declared_fails_the_gate(app_to_plant_on: FastAPI) -> None:
    """A route planted on the real application fails the check that guards the build."""

    @app_to_plant_on.get("/planted-by-a-test")
    async def planted() -> dict[str, str]:
        return {}

    undeclared = mounted_routes(app_to_plant_on) - set(MATRIX)
    assert undeclared == {("GET", "/planted-by-a-test")}


def test_a_websocket_nobody_declared_fails_the_gate(app_to_plant_on: FastAPI) -> None:
    """A planted WebSocket fails the gate too.

    A socket has no HTTP methods, and OpenAPI does not describe WebSockets, so the schema
    cross-check cannot catch one."""

    @app_to_plant_on.websocket("/planted-socket")
    async def planted(websocket: WebSocket) -> None:  # pragma: no cover
        await websocket.accept()

    undeclared = mounted_routes(app_to_plant_on) - set(MATRIX)
    assert undeclared == {(WEBSOCKET, "/planted-socket")}


def test_a_route_that_disappears_is_noticed(app: FastAPI) -> None:
    """The other direction: an entry describing a route that is not there."""
    matrix = {**MATRIX, ("GET", "/removed-last-week"): Case(Policy.ADMIN)}
    assert set(matrix) - mounted_routes(app) == {("GET", "/removed-last-week")}


def test_a_path_with_a_converter_can_be_asked_for() -> None:
    """`fill` handles a converter: str.format reads `{path:path}`'s second half as a format spec."""
    assert fill("/{path:path}", {"path": "browse"}) == "/browse"
    assert fill("/{path:path}", {"path": ""}) == "/"
    assert fill("/assets/{asset_id}", {"asset_id": "01HX"}) == "/assets/01HX"
    assert fill("/health", {}) == "/health"

    with pytest.raises(ValueError, match="Invalid format specifier"):
        "/{path:path}".format(path="browse")

    with pytest.raises(KeyError, match="gives no value for it"):
        fill("/{asset_id}", {})


def test_the_matrix_notices_an_admin_route_that_serves_a_guest() -> None:
    """`check` refuses an admin-only route that answers a guest."""
    case = Case(Policy.ADMIN)

    # The route forgot its check and returned 200 to a guest.
    with pytest.raises(AssertionError, match="served a guest"):
        check("GET", "/jobs", case, 200, role=Role.GUEST)

    # The same route, doing its job.
    check("GET", "/jobs", case, 403, role=Role.GUEST)
    check("GET", "/jobs", case, 200, role=Role.ADMIN)
    check("GET", "/jobs", case, 401, role=None)


def test_the_matrix_notices_a_public_route_that_turns_everyone_away() -> None:
    """Declaring an admin-only route public to dodge the role check fails the anonymous check."""
    with pytest.raises(AssertionError, match="turned an anonymous caller away"):
        check("GET", "/jobs", Case(Policy.PUBLIC), 401, role=None)


# --- each role, on a library of its own


def signed_in_as(client: TestClient, role: Role, user_id: str) -> bool:
    """Whether the client still holds an open, unlocked session of this user and role."""
    answer = client.get("/api/auth/me")
    if answer.status_code != 200:
        return False
    me = answer.json()
    return me["id"] == user_id and me["role"] == role.value and not me["locked"]


@pytest.mark.parametrize("role", list(Role))
def test_every_route_answers_each_role_the_way_the_matrix_says(
    own_library: TestClient, role: Role
) -> None:
    """Signed in as each kind of user, every route that needs a session answers as declared.

    Not parametrized per route: an empty parameter set makes pytest skip, and a skipped
    authorization gate reads like a passed one.
    """
    protected = sorted(
        (entry for entry in MATRIX if MATRIX[entry].policy is not Policy.PUBLIC),
        # Destructive last, then by name: one boot for the whole sweep.
        key=lambda entry: (MATRIX[entry].destructive, entry),
    )
    if not protected:
        return

    viewer = Viewer(id=role.value, role=role)
    user_id = sign_in(own_library, viewer)
    for method, path in protected:
        # A route that ends or locks its session (logout does) is followed by a fresh one.
        if not signed_in_as(own_library, role, user_id):
            user_id = sign_in(own_library, viewer)
        case = MATRIX[(method, path)]
        check(
            method,
            path,
            case,
            call(own_library, method, fill(path, case.params, case.query), case.body),
            role=role,
        )


#: A tag reaching no file a guest may see; only the case below seeds it, in its own library.
AN_UNSEEN_TAG = "01HX0000000000000000000098"

#: An id shaped like a tag's that names nothing.
AN_UNKNOWN_TAG = "01HX00000000000000000000T9"


def test_a_guest_cannot_tell_an_unseen_tag_from_an_unknown_one(own_library: TestClient) -> None:
    """The per-user tag writes answer an unseen tag exactly as one that is not there.

    The tag is looked up the way the Tags wall looks it up, or trying ids would teach which exist.
    The tag the guest CAN see answers 204, so the 404s are the scoping rule.
    """
    db_path = own_library.app.state.database.path  # type: ignore[attr-defined]
    seed_tag(db_path, AN_UNSEEN_TAG, name="unseen")
    sign_in(own_library, Viewer(id=Role.GUEST.value, role=Role.GUEST))

    def vault(tag_id: str) -> int:
        return own_library.put(f"/api/tags/{tag_id}/vault", json={"vault": False}).status_code

    assert vault(A_TAG) == 204
    assert vault(AN_UNSEEN_TAG) == vault(AN_UNKNOWN_TAG) == 404


# --- the app lock, swept the same way
#
# The lock is a claim about the SERVER: while a session is locked, every authenticated route on it
# is refused, whatever client holds the cookie. So it is asked of every mounted route.

#: The routes a locked session may still call: the PIN and the password unlocks, signing out,
#: `me` (what the lock screen reads; nothing about the library), and locking, so a panic control
#: pressed twice is no worse than pressed once.
UNLOCKED_WHILE_LOCKED = frozenset(
    {
        ("POST", "/api/auth/lock"),
        ("POST", "/api/auth/unlock"),
        ("POST", "/api/auth/unlock/password"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
    }
)

#: Its own status, so a client can tell "shut" from "signed out" and draw a PIN box.
LOCKED = 423


def lock_this_session(client: TestClient) -> None:
    """Lock Sift on the session the client holds, through the real route.

    With the setting off, locking ends the session; the shape under test is a session shut but
    alive, so the setting is turned on first.
    """
    pin = client.put("/api/auth/pin", json={"pin": "246810", "current_password": MATRIX_PASSWORD})
    assert pin.status_code == 204, pin.text
    allowed = client.put("/api/settings", json={"values": {"vault.app_lock_enabled": True}})
    assert allowed.status_code == 204, allowed.text
    assert client.post("/api/auth/lock").json()["outcome"] == "locked"


def test_a_locked_session_is_refused_every_authenticated_route(own_app: TestClient) -> None:
    """Every authenticated or admin route answers 423 to a locked session's credential.

    Replayed straight at the API, with no browser. Public routes answer a stranger anyway.
    """
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    lock_this_session(own_app)

    served: list[str] = []
    for (method, path), case in sorted(MATRIX.items()):
        if case.policy is Policy.PUBLIC or (method, path) in UNLOCKED_WHILE_LOCKED:
            continue
        if method == WEBSOCKET:
            # A socket is refused by being closed, which `call` reports as 403.
            assert call(own_app, method, fill(path, case.params, case.query), case.body) != 200, (
                f"{method} {path}"
            )
            continue
        status_code = call(own_app, method, fill(path, case.params, case.query), case.body)
        if status_code != LOCKED:
            served.append(f"{method} {path} -> {status_code}")

    assert not served, (
        "a locked session reached these routes; the lock is a cover rather than a lock: "
        + ", ".join(served)
    )


def test_the_locked_sweep_notices_a_route_that_lets_a_locked_session_through(
    own_app: TestClient,
) -> None:
    """A route excused from the lock through the allow-list is shown answering while locked."""
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    lock_this_session(own_app)

    assert own_app.get("/api/auth/me").status_code == 200
    assert own_app.get("/api/assets").status_code == LOCKED


def test_a_locked_session_can_still_sign_out(own_app: TestClient) -> None:
    """Signing out needs no PIN, so a forgotten PIN is never a lock on your own library."""
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    lock_this_session(own_app)

    assert own_app.post("/api/auth/logout").status_code == 204
    assert own_app.get("/api/auth/me").status_code == 401


def test_a_pin_is_never_a_way_in_for_a_client_with_no_session(own_app: TestClient) -> None:
    """The PIN unlocks and never authenticates: a client with no session gets nothing from it."""
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    assert (
        own_app.put(
            "/api/auth/pin", json={"pin": "246810", "current_password": MATRIX_PASSWORD}
        ).status_code
        == 204
    )
    signed_out = TestClient(own_app.app)

    assert signed_out.post("/api/auth/unlock", json={"pin": "246810"}).status_code in DENIED
    assert signed_out.get("/api/auth/me").status_code == 401
    assert signed_out.get("/api/assets").status_code == 401


# --- one app at a time


def test_booting_an_app_stops_the_shared_one_and_its_client_says_so(
    library: TestClient, own_app: TestClient
) -> None:
    """The shared library and a case's own app never run side by side.

    A boot takes over the process's thread pools and its shutdown closes them, so a shared app is
    stopped, and its client refuses, rather than answering from a closed pool.
    """
    assert library.portal is None
    with pytest.raises(RuntimeError, match="another app booted after it"):
        library.get("/health")
    assert own_app.get("/health").status_code == 200
