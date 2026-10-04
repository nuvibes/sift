# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face endpoints, called over HTTP the way a hostile client calls them.

Everything here runs against a real application with a real library on disk, because what is being
checked is what comes back on the wire. A test against the service would be asking the wrong
object: the service is where the rules are decided, and the route is where they are applied, and
the failure worth catching is a route that never asked.

Two claims are the reason this file exists, and both are asked as raw requests rather than through
a screen:

**A crop is a fragment of its file.** One from a file this user hid, or was never shown, is not
served, and the refusal is the 404 an unknown id gets, because a 403 on a face nobody was shown
confirms the file behind it exists.

**A name is a second question.** Being allowed to see a file does not make somebody allowed to know
who is in it. An appearance attributed to a person this user hid comes back as a face with no
name on it, and the face stays: hiding somebody conceals the person, not the pixels.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.main import create_app
from sift.slices.faces import jobs as faces_jobs
from sift.slices.faces.router import _drop_the_chosen_folder, _safe_relative
from sift.testing.auth import TEST_PIN, establish_session, give_pin
from sift.testing.library import hidden_row, seed_face
from sift.testing.settings import set_app_setting

pytestmark = pytest.mark.integration

PASSWORD = "A-Faces-Test-Passw0rd!"

#: Well-formed and never minted. The control every refusal is compared against, so a test can tell
#: "you may not" from "there is no such thing", which is exactly the distinction the routes are
#: built never to make.
NEVER_EXISTED = "01HX0000000000000000000099"

_EPOCH = 1_700_000_000

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1920, 1080, 4000, ?, ?, ?)
"""

_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_PERSON = "INSERT INTO people (id, name, notes, created_at) VALUES (?, ?, NULL, 0)"

_ATTRIBUTE = "UPDATE face_tracks SET person_id = ?, confidence = 0.9, attribution = ? WHERE id = ?"

_SHARE_ITEM = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'item', ?, ?, 'share', 0)
ON CONFLICT DO NOTHING
"""


def write(db_path: Path, statements: Sequence[tuple[str, tuple[object, ...]]]) -> None:
    """Run writes on a connection of this helper's own, on its own loop.

    The test client drives the application on its own event loop, and a write issued from the
    test's loop meets a lock held on the app's.
    """

    async def run() -> None:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                for sql, params in statements:
                    await connection.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def priority_of(db: Path, job_id: str) -> int:
    """How urgent one queued job is, on a connection of this helper's own. See `write`."""

    async def run() -> int:
        database = Database(db, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all("SELECT priority FROM jobs WHERE id = ?", (job_id,))
        finally:
            await database.close()
        return int(rows[0]["priority"])

    return asyncio.run(run())


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as running:
        yield running


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def faces_of(client: TestClient) -> object:
    """The running application's face service, for a test that stands part of it in.

    Its own helper because the app object is untyped here (the same reason `db_path` exists two
    lines up), and eight `type: ignore` comments in a row is a worse way to say it once.
    """
    return client.app.state.faces  # type: ignore[attr-defined]


def queued_types(client: TestClient) -> set[str]:
    """Every kind of job waiting to run.

    Read straight out of the queue rather than through a stand-in, because the thing being checked
    is whether the route puts anything there at all. A route can do its own work correctly and
    ask for none of the work that follows, and a test that mocked the queue would pass anyway.
    """
    path = db_path(client)

    async def run() -> set[str]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all("SELECT DISTINCT type FROM jobs")
            return {str(row["type"]) for row in rows}
        finally:
            await database.close()

    return asyncio.run(run())


def data_dir(client: TestClient) -> Path:
    return client.app.state.settings.data_dir  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    """Become somebody, with a PIN so a test can open and shut their Hidden at will."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"faces-{role}-{who}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def set_user_setting(client: TestClient, user_id: str, key: str, value: str) -> None:
    """One user's own preference, written straight to the row.

    Not through the settings route, deliberately: what a test of these endpoints needs is the
    state, so setting it up through some other endpoint would make the test partly a test of that
    one. Stored as JSON, which is how the settings store keeps every value.
    """
    write(
        db_path(client),
        [
            (
                "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                (user_id, key, f'"{value}"'),
            )
        ],
    )


def unlock(client: TestClient) -> int:
    return int(client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code)


class Scene:
    """One file, one found face on it, and one person: the smallest thing every route needs.

    Built by writing rows rather than by running a pass, and that is the point rather than a
    shortcut: no model ships with Sift, so there is nothing here that could find a face. What these
    tests are about is what a route does with a face that has already been found.
    """

    def __init__(self, client: TestClient, tmp_path: Path, *, name: str = "clip") -> None:
        media = tmp_path / "media"
        (media / "clips").mkdir(parents=True, exist_ok=True)
        self.root, self.folder = new_id(), new_id()
        self.asset, self.person = new_id(), new_id()
        self.track = new_id()
        # Distinct bytes per scene: Sift identifies a file by its content, so two scenes built from
        # the same payload would be one asset with two rows trying to claim it.
        payload = f"bytes of {name}".encode()
        (media / "clips" / f"{name}.mp4").write_bytes(payload)
        write(
            db_path(client),
            [
                (
                    "INSERT INTO library_roots (id, name, abs_path, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (self.root, "library", str(media), _EPOCH),
                ),
                (
                    "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                    "VALUES (?, ?, NULL, ?, ?)",
                    (self.folder, self.root, "clips", "clips"),
                ),
                (
                    _INSERT_ASSET,
                    (self.asset, f"digest-{name}", len(payload), f"{name}.mp4", _EPOCH),
                ),
                (
                    _INSERT_LOCATION,
                    (
                        new_id(),
                        self.asset,
                        self.root,
                        self.folder,
                        f"clips/{name}.mp4",
                        f"{name}.mp4",
                        _EPOCH,
                        _EPOCH,
                    ),
                ),
                (_INSERT_PERSON, (self.person, "Ada Lovelace")),
            ],
        )
        seed_face(db_path(client), self.track, self.asset, data_dir=data_dir(client))

    def attribute(self, client: TestClient, *, how: str = "matched") -> None:
        """Put the person on the face AND on the file, which is what a real pass does.

        Both halves, because the second is what the People screen reads: somebody attributed to a
        face and not attached to any file is on no list, so a route that resolves them would
        answer as if they did not exist, and the test would then be measuring the seeding rather
        than the route.
        """
        write(
            db_path(client),
            [
                (_ATTRIBUTE, (self.person, how, self.track)),
                (
                    "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?) "
                    "ON CONFLICT DO NOTHING",
                    (self.asset, self.person),
                ),
                (
                    "INSERT INTO face_asset_people (asset_id, person_id, created_at) "
                    "VALUES (?, ?, 0) ON CONFLICT DO NOTHING",
                    (self.asset, self.person),
                ),
            ],
        )

    def unname_by_hand(self, client: TestClient) -> None:
        """Take the person off the file's own list, leaving the face still naming them.

        What an admin does by dragging a name off the item detail. The face attribution is a
        separate fact and survives it, which is exactly the state where a face is the only thing
        left holding a name.
        """
        write(
            db_path(client),
            [
                (
                    "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ?",
                    (self.asset, self.person),
                )
            ],
        )

    def hide(self, client: TestClient, kind: str, object_id: str, user_id: str) -> None:
        write(db_path(client), [hidden_row(kind, object_id, user_id)])

    def share_with(self, client: TestClient, user_id: str) -> None:
        write(db_path(client), [(_SHARE_ITEM, (new_id(), self.asset, user_id))])


@pytest.fixture
def scene(client: TestClient, tmp_path: Path) -> Scene:
    return Scene(client, tmp_path)


def turn_on(client: TestClient) -> None:
    set_app_setting(db_path(client), "faces.enabled", "true")


@pytest.fixture
def models_counted_as_here(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a press past the models question, and nothing else.

    No model is installed under a test's data directory, and a press now refuses in that state
    (`FaceService.cannot_scan`). The tests that use this are about what a press QUEUES, so the
    press is told the models are here, while `ready`, which the queue asks before it hands any
    Identify work out, still says they are not, so what was queued stays queued to be looked at.
    """
    service = faces_of(client)

    async def here(*, run: str | None = None) -> str | None:
        return None

    monkeypatch.setattr(service, "weights_problem", here)


# --- the crop, asked for directly ------------------------------------------------------------


def test_a_crop_from_a_file_the_viewer_may_not_see_is_not_served(
    client: TestClient, scene: Scene
) -> None:
    """A crop from a file never shared, as a raw request rather than through a screen.

    A guest who was never given this file asks for the face in it by name. The answer has to be
    the answer an id that was never minted gets, not a 403, which would confirm there is a face
    there and therefore a file behind it.
    """
    turn_on(client)
    sign_in(client, "guest", who="stranger")

    refused = client.get(f"/api/faces/{scene.track}/crop")
    unknown = client.get(f"/api/faces/{NEVER_EXISTED}/crop")

    assert refused.status_code == 404
    assert refused.status_code == unknown.status_code
    assert refused.json() == unknown.json(), (
        "a crop from a file this account may not see answered differently from one that "
        "never existed, which is the difference somebody would read as confirmation"
    )


def test_a_crop_from_a_file_the_viewer_was_given_is_served(
    client: TestClient, scene: Scene
) -> None:
    """The other half, and the half that stops the test above passing on a route that refuses
    everybody. Share the file and the same request returns the picture."""
    turn_on(client)
    guest = sign_in(client, "guest", who="invited")
    scene.share_with(client, guest)

    answer = client.get(f"/api/faces/{scene.track}/crop")

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/jpeg"
    assert answer.content.startswith(b"\xff\xd8\xff")


def test_a_crop_already_held_is_not_sent_again(client: TestClient, scene: Scene) -> None:
    """The saving half of `no-cache`: the browser asks, and asking is cheap to answer."""
    turn_on(client)
    sign_in(client, "admin")

    first = client.get(f"/api/faces/{scene.track}/crop")
    again = client.get(
        f"/api/faces/{scene.track}/crop", headers={"If-None-Match": first.headers["etag"]}
    )

    assert again.status_code == 304
    assert again.content == b""


def test_a_crop_is_still_refused_to_somebody_who_may_not_see_it_however_they_ask(
    client: TestClient, scene: Scene
) -> None:
    """The visibility rule runs first. A 304 would confirm the face exists and the copy is current."""
    turn_on(client)
    sign_in(client, "admin")
    tag = client.get(f"/api/faces/{scene.track}/crop").headers["etag"]

    sign_in(client, "guest", who="uninvited")
    assert (
        client.get(f"/api/faces/{scene.track}/crop", headers={"If-None-Match": tag}).status_code
        == 404
    )


def test_a_crop_may_be_kept_without_asking(client: TestClient, scene: Scene) -> None:
    """A crop is written once, when the face is found, and nothing ever rewrites it, so its
    address cannot come to mean a different picture and there is nothing to re-check.

    `private` keeps it out of any shared cache, which would otherwise sit in front of a permission
    check it cannot re-run and hand one viewer's face to the next person who asked.
    """
    turn_on(client)
    sign_in(client, "admin")

    answer = client.get(f"/api/faces/{scene.track}/crop?v=whatever-the-client-was-told")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, max-age=604800, immutable"


def test_a_crop_asked_for_without_its_token_is_checked_every_time(
    client: TestClient, scene: Scene
) -> None:
    """The promise is about the address, and `/crop` on its own names nothing.

    A crop's token is the user's stamp, which is what takes every face picture away the moment
    somebody is hidden, so an address that leaves it off would be the one address the hiding
    cannot reach, and is not answered like the tokened one.
    """
    turn_on(client)
    sign_in(client, "admin")

    answer = client.get(f"/api/faces/{scene.track}/crop")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, no-cache"


def test_the_address_of_a_crop_carries_the_token_that_hiding_moves(
    client: TestClient, scene: Scene
) -> None:
    """**Without this the whole thing leaks.**

    The picture is kept for a week with no request behind it, so the only way hiding somebody can
    reach a copy already on their machine is for the address to stop being the one they hold. That
    is what the token on the row is for, and it has to move when anything about what this user
    may see does.
    """
    turn_on(client)
    sign_in(client, "admin")

    before = client.get(f"/api/assets/{scene.asset}/faces").json()
    assert before[0]["art"], "a face with no token would be kept at an address nothing can retire"

    # Through the route rather than into the row. The stamp goes up in the same transaction as the
    # hide, so a test that wrote the row itself would set up the state and skip the only part of it
    # under test here.
    assert client.put(f"/api/assets/{scene.asset}/vault", json={"vault": True}).status_code == 204
    assert unlock(client) == 200
    after = client.get(f"/api/assets/{scene.asset}/faces").json()

    assert after[0]["art"] != before[0]["art"]


def test_a_concealed_crop_is_never_given_an_address_that_may_be_kept(
    client: TestClient, scene: Scene
) -> None:
    """Reachable only because Hidden is open, so it must stop being reachable the moment it shuts.

    A week-long copy of it would outlive the lock by a week, on the machine that was looking, and
    that machine is precisely the one concealment is for.
    """
    turn_on(client)
    admin = sign_in(client, "admin")
    scene.hide(client, "asset", scene.asset, admin)
    assert unlock(client) == 200

    answer = client.get(f"/api/faces/{scene.track}/crop")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, no-cache"


def test_a_cover_falling_back_to_the_small_square_is_re_checked_every_time(
    client: TestClient, scene: Scene
) -> None:
    """**The reason this address is only sometimes keepable.**

    One address serves two different pictures over its life: the recognizer's 112-pixel square
    while the real portrait cannot be cut, and the portrait afterwards. A copy kept from the first
    would be the wrong picture for a week, with nothing on screen to say why, so the fallback is
    served the careful way and only a real cover is allowed to be kept.

    The fixture's file is a still with no frame to cut a portrait from, which is exactly the case.
    """
    turn_on(client)
    sign_in(client, "admin")

    answer = client.get(f"/api/faces/{scene.track}/cover")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, no-cache"


def test_a_crop_from_a_file_this_account_hid_is_not_served_while_hidden_is_shut(
    client: TestClient, scene: Scene
) -> None:
    """Hiding is per-user, so this asks the resolver's own question rather than an install-wide
    flag: an admin hid the file, and their own crop goes with it until they open Hidden."""
    turn_on(client)
    admin = sign_in(client, "admin")
    scene.hide(client, "asset", scene.asset, admin)

    assert client.get(f"/api/faces/{scene.track}/crop").status_code == 404

    assert unlock(client) == 200
    assert client.get(f"/api/faces/{scene.track}/crop").status_code == 200


def test_a_face_with_no_picture_behind_it_is_a_miss_rather_than_an_error(
    client: TestClient, scene: Scene, tmp_path: Path
) -> None:
    """A row outlives the file it names: a restored backup, a half-finished copy. There is
    nothing to serve, and that is the same answer as any other miss rather than a 500."""
    turn_on(client)
    sign_in(client, "admin")
    for picture in (data_dir(client) / "faces").rglob("*.jpg"):
        picture.unlink()

    assert client.get(f"/api/faces/{scene.track}/crop").status_code == 404


# --- who is in this --------------------------------------------------------------------------


def test_who_is_in_a_file_is_answered_for_anybody_who_may_see_the_file(
    client: TestClient, scene: Scene
) -> None:
    """A guest browsing something they were given is shown the People on it, exactly as they are
    shown the tags."""
    turn_on(client)
    scene.attribute(client)
    guest = sign_in(client, "guest", who="invited")
    scene.share_with(client, guest)

    answer = client.get(f"/api/assets/{scene.asset}/faces")

    assert answer.status_code == 200
    assert [face["person_name"] for face in answer.json()] == ["Ada Lovelace"]


def test_a_file_the_viewer_may_not_see_has_no_faces_to_report(
    client: TestClient, scene: Scene
) -> None:
    """The 404 an unknown id gets, for the reason the crop route gives: answering at all would
    confirm the file is there."""
    turn_on(client)
    sign_in(client, "guest", who="stranger")

    assert client.get(f"/api/assets/{scene.asset}/faces").status_code == 404
    assert client.get(f"/api/assets/{NEVER_EXISTED}/faces").status_code == 404


def test_hiding_somebody_takes_the_whole_file_with_them(client: TestClient, scene: Scene) -> None:
    """The rule this route inherits rather than implements, asserted so it stays inherited.

    Hiding a person conceals every file they are on, so the faces in one are not reached by a
    second rule of their own: the file is simply not there. That is the resolver's answer and it
    is the right one; what matters here is that this route agrees with it rather than having an
    opinion, because a face-listing route that answered for a concealed file would be the way
    around concealment.
    """
    turn_on(client)
    admin = sign_in(client, "admin")
    scene.attribute(client)
    scene.hide(client, "person", scene.person, admin)

    assert client.get(f"/api/assets/{scene.asset}/faces").status_code == 404

    assert unlock(client) == 200
    named = client.get(f"/api/assets/{scene.asset}/faces").json()
    assert [face["person_name"] for face in named] == ["Ada Lovelace"]


def test_a_face_naming_somebody_the_viewer_has_no_other_way_of_knowing_is_unnamed(
    client: TestClient, scene: Scene
) -> None:
    """The second question, and the one a route is most likely to forget to ask.

    The state is reachable and ordinary: an admin takes a person off a file by hand while a face in
    it still names them. The file's own list of People no longer mentions them, so to a guest who
    was given the file that person is on no list, in no count and in no search box, and the face
    is the one surface that still holds the name.

    Answering it would tell that guest a person exists whom nothing else would have told them
    about. So the name goes and the face stays: concealing somebody hides the person, not the
    pixels, and a file whose number of faces changed with who was looking would be its own
    disclosure.
    """
    turn_on(client)
    scene.attribute(client)
    guest = sign_in(client, "guest", who="invited")
    scene.share_with(client, guest)
    scene.unname_by_hand(client)

    faces = client.get(f"/api/assets/{scene.asset}/faces").json()

    assert len(faces) == 1, "the face itself is not concealed by concealing the person in it"
    assert faces[0]["person_name"] is None
    assert faces[0]["person_id"] is None, (
        "the id was handed over while the name was withheld, which says a concealed person is here"
    )
    assert faces[0]["confidence"] is None
    assert faces[0]["attribution"] is None, (
        "how sure Sift was, about a person this viewer is not being told about"
    )


def test_the_same_face_is_named_for_somebody_who_may_know_the_person(
    client: TestClient, scene: Scene
) -> None:
    """The half that stops the test above passing on a route that never names anybody."""
    turn_on(client)
    scene.attribute(client)
    guest = sign_in(client, "guest", who="invited")
    scene.share_with(client, guest)

    faces = client.get(f"/api/assets/{scene.asset}/faces").json()

    assert [face["person_name"] for face in faces] == ["Ada Lovelace"]
    assert faces[0]["person_id"] == scene.person


_ADD_DETECTION = """
INSERT INTO face_detections
  (id, track_id, timestamp_ms, box_x, box_y, box_w, box_h, score, quality, crop_path, crop_digest,
   embedding, created_at)
VALUES (?, ?, ?, 0, 0, 64, 64, 0.9, ?, ?, 'seeded-digest', X'0000803F', 0)
"""


def test_a_face_plays_from_the_moment_of_its_picture_not_its_first_appearance(
    client: TestClient, scene: Scene
) -> None:
    """Pressing a face in a video plays from the frame its PICTURE shows.

    Not from `started_ms`, the first frame the face was seen in, often a blur turning towards the
    camera, seconds away from the clear view the chip shows. The picture is the clearest of the
    appearance's faces, so its moment is what the chip carries; and it is
    checked against the crop the route actually serves, so the moment and the picture cannot come
    to name two different faces.
    """
    turn_on(client)
    sign_in(client, "admin")
    clear = f"detected/{scene.track[:2]}/{scene.track}-clear.jpg"
    soft = f"detected/{scene.track[:2]}/{scene.track}-soft.jpg"
    (data_dir(client) / "faces" / clear).write_bytes(b"\xff\xd8\xff\xdb the clearest view")
    (data_dir(client) / "faces" / soft).write_bytes(b"\xff\xd8\xff\xdb a softer view")
    write(
        db_path(client),
        [
            (
                "UPDATE face_tracks SET started_ms = 1000, ended_ms = 9000 WHERE id = ?",
                (scene.track,),
            ),
            (
                "UPDATE face_detections SET timestamp_ms = 1000, quality = 0.4 WHERE track_id = ?",
                (scene.track,),
            ),
            (_ADD_DETECTION, (new_id(), scene.track, 6000, 0.95, clear)),
            (_ADD_DETECTION, (new_id(), scene.track, 8000, 0.7, soft)),
        ],
    )

    face = client.get(f"/api/assets/{scene.asset}/faces").json()[0]

    assert face["started_ms"] == 1000, "where the face was first seen is still said"
    assert face["picture_ms"] == 6000, "the press would play from the first sighting, not the chip"
    assert client.get(f"/api/faces/{scene.track}/crop").content.endswith(b"the clearest view")


def test_with_the_feature_off_a_file_simply_has_no_faces(client: TestClient, scene: Scene) -> None:
    """Drawn as a block on a screen somebody is already looking at, so "nothing found" and "never
    looked" are the same answer to it. A refusal here would be an error message on the item detail
    of every install that has not turned this on."""
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{scene.asset}/faces")

    assert answer.status_code == 200
    assert answer.json() == []


# --- a person's appearances --------------------------------------------------------------------


def make_pile(client: TestClient, scene: Scene, *, status: str = "open") -> str:
    pile_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
                "VALUES (?, ?, X'0000803F', 1, 0, 0)",
                (pile_id, status),
            ),
            ("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile_id, scene.track)),
        ],
    )
    return pile_id


def test_a_pile_the_viewer_may_see_none_of_is_absent_rather_than_shown_empty(
    client: TestClient, scene: Scene
) -> None:
    """A row saying "1 face" over a screen that can show none of them is a count of what is being
    kept back, which is the whole thing concealment is for.

    Asked as an admin with the file hidden from themselves. This surface is an admin's, so a
    guest never gets an answer to compare, and an admin concealing something from themselves is
    the case that tells "resolved for whoever is asking" from "resolved for an admin"."""
    turn_on(client)
    make_pile(client, scene)
    admin = sign_in(client)
    scene.hide(client, "asset", scene.asset, admin)

    answer = client.get("/api/faces/groups").json()

    assert answer["groups"] == []
    # And the pager says nothing is there either. A count of one over an empty screen is the same
    # disclosure the row would have been, arrived at from the other side, and the count and the
    # page are taken from one list precisely so they cannot say different things.
    assert answer["total"] == 0


def test_a_pile_is_counted_as_this_viewer_may_see_it(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    pile = make_pile(client, scene)
    sign_in(client)

    groups = client.get("/api/faces/groups").json()["groups"]

    assert [group["id"] for group in groups] == [pile]
    assert groups[0]["size"] == 1
    assert [face["track_id"] for face in groups[0]["faces"]] == [scene.track]


def test_every_face_in_a_pile_is_listed_by_id_for_a_verb_over_the_whole_pile(
    client: TestClient, scene: Scene
) -> None:
    """`GET /faces/groups/{pile_id}/tracks`: what a verb pressed over the WHOLE pile acts on.

    The screen pages its faces, so "name everybody in this pile" cannot be built from the page: it
    has to ask for the whole set.
    """
    turn_on(client)
    pile = make_pile(client, scene)
    second = new_id()
    seed_face(db_path(client), second, scene.asset, data_dir=data_dir(client))
    write(db_path(client), [("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile, second))])
    sign_in(client, "admin")

    answer = client.get(f"/api/faces/groups/{pile}/tracks").json()

    assert sorted(answer["track_ids"]) == sorted([scene.track, second])
    assert answer["total"] == 2


def test_the_faces_of_a_pile_are_the_ones_this_account_may_see(
    client: TestClient, scene: Scene
) -> None:
    """Scoped like every other read of a pile: a file concealed from the asker is not in the list,
    and a pile they may see nothing of is a miss rather than an empty set.

    An empty set would say the pile is there and holds faces you are not being shown, which is the
    disclosure concealment exists to prevent: the same argument
    `test_a_pile_the_viewer_may_see_none_of_is_absent_rather_than_shown_empty` makes for the wall.
    """
    turn_on(client)
    pile = make_pile(client, scene)
    admin = sign_in(client)
    scene.hide(client, "asset", scene.asset, admin)

    assert client.get(f"/api/faces/groups/{pile}/tracks").status_code == 404


def test_the_faces_of_a_pile_say_the_feature_is_off_rather_than_saying_nothing(
    client: TestClient, scene: Scene
) -> None:
    """The same rule the pile routes beside it follow: 404 would say "there is no such pile",
    which is a different and wrong thing to tell somebody about a feature merely switched off."""
    pile = make_pile(client, scene)
    sign_in(client, "admin")

    assert client.get(f"/api/faces/groups/{pile}/tracks").status_code == 409


def test_the_faces_of_a_pile_that_is_not_there_is_a_miss(client: TestClient) -> None:
    """A name nothing answers to, which is the other way to get nothing, and it must be the
    SAME answer a pile they may see nothing of gets, or the difference between the two becomes a
    way of asking whether a pile exists."""
    turn_on(client)
    sign_in(client, "admin")

    assert client.get(f"/api/faces/groups/{NEVER_EXISTED}/tracks").status_code == 404


def test_the_faces_of_a_pile_are_the_review_queue_and_a_guest_is_refused(
    client: TestClient, scene: Scene
) -> None:
    """Admin, like every other way of reading the queue.

    A pile's membership is the GROUPING itself: it says these faces were judged to be one person,
    which is a fact about the review queue rather than about any file, and looking at what there is
    to decide is an admin's for the same reason deciding it is.
    """
    turn_on(client)
    pile = make_pile(client, scene)
    sign_in(client, "guest")

    assert client.get(f"/api/faces/groups/{pile}/tracks").status_code == 403


def test_ignored_piles_are_a_list_of_their_own_and_not_a_deletion(
    client: TestClient, scene: Scene
) -> None:
    """Setting a pile aside must not be a trapdoor: it stays listed, under its own status, and can
    be brought back with its faces."""
    turn_on(client)
    pile = make_pile(client, scene)
    sign_in(client, "admin")

    # Answers with what it did and the record it wrote, rather than with nothing: the screen that
    # set it aside offers to take that back, and it needs the record to do it with.
    aside = client.post(f"/api/faces/groups/{pile}/ignore")
    assert aside.status_code == 200
    assert aside.json()["settled"] is True
    assert aside.json()["decision_id"]
    assert client.get("/api/faces/groups").json()["groups"] == []
    ignored = client.get("/api/faces/groups", params={"status": "ignored"}).json()["groups"]
    assert [group["id"] for group in ignored] == [pile]

    assert client.post(f"/api/faces/groups/{pile}/restore").status_code == 204
    assert [group["id"] for group in client.get("/api/faces/groups").json()["groups"]] == [pile]


def test_setting_aside_a_pile_that_is_not_there_is_a_miss(client: TestClient) -> None:
    turn_on(client)
    sign_in(client, "admin")

    assert client.post(f"/api/faces/groups/{NEVER_EXISTED}/ignore").status_code == 404
    assert client.post(f"/api/faces/groups/{NEVER_EXISTED}/restore").status_code == 404


def test_with_the_feature_off_the_pile_routes_say_so_rather_than_saying_nothing(
    client: TestClient, scene: Scene
) -> None:
    """A screen told 404 would report "there is no such pile", which is a different and wrong
    thing to tell somebody about a feature that is merely switched off."""
    pile = make_pile(client, scene)
    sign_in(client, "admin")

    assert client.get("/api/faces/groups").json()["groups"] == []
    assert client.post(f"/api/faces/groups/{pile}/ignore").status_code == 409
    assert client.post(f"/api/faces/groups/{pile}/restore").status_code == 409


# --- deciding ------------------------------------------------------------------------------------


def test_confirming_a_face_names_the_person_on_the_file(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    assert answer.status_code == 204
    people = client.get(f"/api/assets/{scene.asset}/people").json()
    assert [person["name"] for person in people] == ["Ada Lovelace"]


# --- a folder of people ---------------------------------------------------------------------------
#
# The routes in front of the folder service: the tests of the way in, not of the service itself.


def test_a_file_claiming_to_sit_outside_the_chosen_folder_is_refused(
    client: TestClient, scene: Scene
) -> None:
    """The path comes from the client, so it is a request and not a fact.

    Refused whole rather than quietly dropped: an import that silently skipped the files it did not
    like would report somebody as thin when what happened is that Sift declined to read half their
    folder.
    """
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[("files", ("../../etc/passwd", b"stand-in", "image/jpeg"))],
    )

    assert answer.status_code == 400


@pytest.mark.parametrize(
    "claimed",
    ["/etc/passwd", "C:/Windows/system32/x.jpg", "people/../../out.jpg"],
    ids=["absolute", "drive-letter", "traversal-in-the-middle"],
)
def test_every_shape_of_escape_is_refused(client: TestClient, scene: Scene, claimed: str) -> None:
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[("files", (claimed, b"stand-in", "image/jpeg"))],
    )

    assert answer.status_code == 400


@pytest.mark.parametrize(
    "claimed",
    ["", ".", "..", "a/../../b.jpg", "\\\\server\\share\\a.jpg"],
    ids=["empty", "dot", "dotdot", "traversal", "unc"],
)
def test_the_path_guard_refuses_everything_that_is_not_inside_the_folder(claimed: str) -> None:
    """Asked of the guard directly, because some of these never survive the web framework.

    A test that only went through a request would look like it covered them and would not: an empty
    filename is dropped before any handler sees it, so the case would pass whether the guard
    existed or not.
    """
    assert _safe_relative(claimed) is None


def test_the_path_guard_keeps_an_ordinary_nested_path() -> None:
    """The other half. A guard that refused everything would pass every test above."""
    kept = _safe_relative("Gallery/Ada Lovelace/one.jpg")
    assert kept is not None and kept.parts == ("Gallery", "Ada Lovelace", "one.jpg")


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [("a//b.jpg", ("a", "b.jpg")), ("./a.jpg", ("a.jpg",))],
    ids=["doubled-separator", "leading-dot"],
)
def test_harmless_untidiness_is_kept_rather_than_refused(
    claimed: str, expected: tuple[str, ...]
) -> None:
    """A doubled separator and a leading `./` name the same file inside the folder.

    Worth asserting rather than assuming, because both are absorbed by the path type before the
    guard's own checks run, so the guard's clause about empty segments can never fire for these,
    and a test that expected a refusal would have been testing nothing. What matters is where they
    land, and they land inside.
    """
    kept = _safe_relative(claimed)
    assert kept is not None and kept.parts == expected


def test_the_wrapper_folder_a_browser_adds_is_stripped_only_when_there_is_one() -> None:
    """Two top-level names means several folders were picked, and merging them merges two people."""
    wrapped = [PurePosixPath("Gallery/Ada/one.jpg"), PurePosixPath("Gallery/Grace/one.jpg")]
    assert [str(one) for one in _drop_the_chosen_folder(wrapped)] == [
        "Ada/one.jpg",
        "Grace/one.jpg",
    ]

    two_folders = [PurePosixPath("Ada/one.jpg"), PurePosixPath("Grace/one.jpg")]
    assert _drop_the_chosen_folder(two_folders) == two_folders


def test_a_folder_of_people_is_answered_with_its_task_even_without_models(
    client: TestClient, scene: Scene
) -> None:
    """The request only takes the folder in: whether the models are here is the task's question,
    answered on Activity in words, so a fresh install's press is never a 500."""
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[
            ("files", ("Gallery/Ada Lovelace/one.jpg", b"stand-in", "image/jpeg")),
            ("files", ("Gallery/Grace Hopper/one.jpg", b"stand-in", "image/jpeg")),
        ],
    )

    assert answer.status_code == 202
    assert "face_folder_import" in queued_types(client)


def test_naming_a_face_gives_that_person_the_whole_file_never_the_face(
    client: TestClient, scene: Scene
) -> None:
    """Somebody who came into existence by having a face named is shown as the whole first picture
    filed under them: a cover nobody chose is never a face cut out of one."""
    turn_on(client)
    sign_in(client, "admin")

    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    card = client.get(f"/api/assets/{scene.asset}/people").json()[0]
    assert card["cover_track_id"] is None
    assert card["cover_asset_id"] == scene.asset


def test_a_faces_cover_address_is_rechecked_where_nothing_was_cut(
    client: TestClient, scene: Scene
) -> None:
    """The address a face cover made before catalog 79 is drawn from still answers, cut on the
    first request for it. The fixture is a still, so there is no frame to cut a portrait from and
    no file appears: nothing was cut, so the address stays one that is re-checked rather than one
    that hands out a square somebody keeps.
    """
    turn_on(client)
    sign_in(client, "admin")

    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    answer = client.get(f"/api/faces/{scene.track}/cover")
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "private, no-cache"


def test_a_cover_somebody_chose_survives_a_face_being_named(
    client: TestClient, scene: Scene
) -> None:
    """Filling a gap, never overruling a decision. A chosen cover is a decision."""
    turn_on(client)
    sign_in(client, "admin")
    write(
        db_path(client),
        [("UPDATE people SET cover_asset_id = ? WHERE id = ?", (scene.asset, scene.person))],
    )

    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    card = client.get(f"/api/assets/{scene.asset}/people").json()[0]
    assert card["cover_track_id"] is None


def test_confirming_a_face_asks_for_everything_else_to_be_matched_again(
    client: TestClient, scene: Scene
) -> None:
    """Naming one face is meant to claim the faces like it, so the route asks for it.

    A confirmed face becomes a reference, which changes what every unclaimed face is compared
    against, and that comparison has to be asked for again: work that exists, is tested and is
    correct does nothing if nobody calls it.
    """
    turn_on(client)
    sign_in(client, "admin")

    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    assert "face_rematch" in queued_types(client)


def test_rejecting_a_face_takes_the_person_back_off_the_file(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    sign_in(client, "admin")
    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    answer = client.post(f"/api/faces/{scene.track}/reject", json={"person_id": scene.person})

    assert answer.status_code == 204
    assert client.get(f"/api/assets/{scene.asset}/people").json() == []


def test_deciding_about_a_person_this_account_hid_is_a_miss(
    client: TestClient, scene: Scene
) -> None:
    """An admin who has not opened Hidden is in the same position everybody else is in about
    somebody they concealed. Confirming a face against a person absent from every list they can
    see would be a way to find out that person exists."""
    turn_on(client)
    admin = sign_in(client, "admin")
    scene.hide(client, "person", scene.person, admin)

    confirm = client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})
    reject = client.post(f"/api/faces/{scene.track}/reject", json={"person_id": scene.person})

    assert confirm.status_code == 404
    assert reject.status_code == 404


def test_deciding_about_a_face_from_a_file_the_admin_hid_is_a_miss(
    client: TestClient, scene: Scene
) -> None:
    """The crop check guards the write as well as the picture: a decision about a face is a
    decision about the file it is in."""
    turn_on(client)
    admin = sign_in(client, "admin")
    scene.hide(client, "asset", scene.asset, admin)

    confirm = client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})
    reject = client.post(f"/api/faces/{scene.track}/reject", json={"person_id": scene.person})

    assert confirm.status_code == 404
    assert reject.status_code == 404


def test_with_the_feature_off_deciding_says_so(client: TestClient, scene: Scene) -> None:
    sign_in(client, "admin")

    confirm = client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})
    reject = client.post(f"/api/faces/{scene.track}/reject", json={"person_id": scene.person})

    assert confirm.status_code == 409
    assert reject.status_code == 409
    assert "switched off" in confirm.json()["detail"]


# --- the feature itself ----------------------------------------------------------------------------


def test_the_settings_route_reports_off_and_not_ready_on_a_fresh_install(
    client: TestClient,
) -> None:
    """Two fields rather than one. Switched on with no models present is the ordinary state a
    second after somebody turns it on, and collapsing the pair would make that read as broken."""
    sign_in(client, "admin")

    body = client.get("/api/faces/settings").json()

    assert body["enabled"] is False
    assert body["ready"] is False
    assert body["installed"] == []
    assert body["family"] == "accurate"


def test_switching_the_feature_on_does_not_by_itself_make_it_ready(client: TestClient) -> None:
    """No model ships with Sift, so an install that has just turned this on has nothing to run
    with, and the screen has to be able to tell that from a broken feature."""
    turn_on(client)
    sign_in(client, "admin")

    body = client.get("/api/faces/settings").json()

    assert body["enabled"] is True
    assert body["ready"] is False


def test_forgetting_everything_is_queued_and_leaves_the_file(
    client: TestClient, scene: Scene
) -> None:
    """The plainly-labelled control. Separate from the switch, because switching the feature off
    temporarily is not a request to throw away what was curated."""
    turn_on(client)
    sign_in(client, "admin")
    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    answer = client.post("/api/faces/forget")

    assert answer.status_code == 200
    assert answer.json()["job_id"]
    assert faces_jobs.FACE_FORGET in queued_types(client)
    assert client.get(f"/api/assets/{scene.asset}").status_code == 200


def test_a_pile_whose_faces_are_all_gone_costs_no_resolve_at_all(
    client: TestClient, scene: Scene
) -> None:
    """A pile with nothing left in it (every file deleted out from under it) is absent, and
    getting there does not run the most expensive query in the application to be told the obvious.

    Reachable rather than theoretical: deleting a file takes its faces with it, and the pile row
    outlives them until the next regroup.
    """
    turn_on(client)
    pile = make_pile(client, scene)
    write(db_path(client), [("DELETE FROM face_tracks WHERE pile_id = ?", (pile,))])
    sign_in(client, "admin")

    assert client.get("/api/faces/groups").json()["groups"] == []


def test_a_face_whose_detections_have_gone_has_no_picture_to_serve(
    client: TestClient, scene: Scene
) -> None:
    """A track with nothing under it. Different from a track whose picture is missing off the disk,
    and it lands in the same place: there is nothing to serve, so it is a miss rather than a 500."""
    turn_on(client)
    sign_in(client, "admin")
    write(db_path(client), [("DELETE FROM face_detections WHERE track_id = ?", (scene.track,))])

    assert client.get(f"/api/faces/{scene.track}/crop").status_code == 404


def test_a_pile_reports_how_many_of_it_this_viewer_may_see_not_how_big_it_is(
    client: TestClient, scene: Scene, tmp_path: Path
) -> None:
    """The number beside a pile is recounted per viewer, and this is the case that proves it.

    Two faces in one pile, in two different files, and one of the files is hidden from the user
    asking. The stored size says two. Reporting that would say there is a face here they are not
    being shown, and roughly where, which is the disclosure the whole recount exists to prevent.

    Asked as an ADMIN, because this surface is an admin's. An admin can conceal things from
    themselves, so "resolved for whoever is asking" is not satisfied by "resolved for an admin",
    and this is the case that tells the two apart.

    A pile whose stored size happens to equal the visible count cannot tell a route that recounts
    from one that reads the column, which is why this needs two files rather than one.
    """
    turn_on(client)
    elsewhere = Scene(client, tmp_path / "elsewhere", name="elsewhere")
    pile = make_pile(client, scene)
    admin = sign_in(client)
    write(
        db_path(client),
        [
            ("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile, elsewhere.track)),
            ("UPDATE face_piles SET size = 2 WHERE id = ?", (pile,)),
        ],
    )
    elsewhere.hide(client, "asset", elsewhere.asset, admin)

    groups = client.get("/api/faces/groups").json()["groups"]

    assert len(groups) == 1
    assert groups[0]["size"] == 1, "the pile reported a face this viewer is not being shown"
    assert [face["track_id"] for face in groups[0]["faces"]] == [scene.track]


# --- how strong a person's recognition is --------------------------------------------------------


def test_how_well_somebody_can_be_recognized_is_reported(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    sign_in(client)

    answer = client.get(f"/api/people/{scene.person}/recognition")

    assert answer.status_code == 200
    body = answer.json()
    assert body["references"] == 0
    assert body["target"] > body["floor"] > 0
    assert body["verdict"] == "none"


def test_recognition_strength_is_zero_rather_than_refused_while_faces_are_off(
    client: TestClient, scene: Scene
) -> None:
    """Drawn as a line on a screen about something else, and "cannot identify them" is the true
    answer whether the feature is off or nobody has named a face."""
    sign_in(client)

    answer = client.get(f"/api/people/{scene.person}/recognition")

    assert answer.status_code == 200
    assert answer.json()["references"] == 0


def test_recognition_strength_of_somebody_this_account_cannot_see_is_a_miss(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    user = sign_in(client)
    scene.hide(client, "person", scene.person, user)

    assert client.get(f"/api/people/{scene.person}/recognition").status_code == 404


# --- the cover, which is not the recognizer's square ---------------------------------------------


def test_a_cover_falls_back_to_the_crop_when_there_is_nothing_to_cut_from(
    client: TestClient, scene: Scene
) -> None:
    """The file behind a seeded face is not a real video, so nothing can be decoded from it. What
    was being shown before is what comes back, which is better than a hole."""
    turn_on(client)
    sign_in(client)

    answer = client.get(f"/api/faces/{scene.track}/cover")

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/jpeg"


def test_a_cover_from_a_file_the_viewer_may_not_see_is_not_served(
    client: TestClient, scene: Scene
) -> None:
    """Same fragment of the same file as the crop, so the same rule and the same refusal."""
    turn_on(client)
    sign_in(client, "guest", who="stranger")

    assert client.get(f"/api/faces/{scene.track}/cover").status_code == 404


def test_a_cover_is_served_with_the_feature_off_because_the_picture_is_already_there(
    client: TestClient, scene: Scene
) -> None:
    sign_in(client)

    assert client.get(f"/api/faces/{scene.track}/cover").status_code == 200


def test_a_cover_already_cut_is_served_without_decoding_anything_again(
    client: TestClient, scene: Scene
) -> None:
    """One decode, once, per face that is ever used as a cover."""
    turn_on(client)
    sign_in(client)
    cut = data_dir(client) / "faces" / "covers" / scene.track[:2] / f"{scene.track}.jpg"
    cut.parent.mkdir(parents=True, exist_ok=True)
    cut.write_bytes(b"\xff\xd8\xff\xdb an already-cut cover")

    answer = client.get(f"/api/faces/{scene.track}/cover")

    assert answer.status_code == 200
    assert answer.content == cut.read_bytes()


def test_a_cover_with_no_picture_anywhere_behind_it_is_a_miss(
    client: TestClient, scene: Scene
) -> None:
    """Nothing to cut from and nothing to fall back to. A miss, not a broken image."""
    turn_on(client)
    sign_in(client)
    (data_dir(client) / "faces" / f"detected/{scene.track[:2]}/{scene.track}.jpg").unlink()

    assert client.get(f"/api/faces/{scene.track}/cover").status_code == 404


def test_setting_aside_a_face_with_nothing_recorded_behind_it_is_a_miss(
    client: TestClient, scene: Scene
) -> None:
    """A pile is built from the descriptions of its faces, so a face carrying none cannot make
    one, and saying it worked would be reporting a pile that is not there."""
    turn_on(client)
    sign_in(client)
    write(db_path(client), [("DELETE FROM face_detections WHERE track_id = ?", (scene.track,))])

    assert client.post("/api/faces/set-aside", json={"track_ids": [scene.track]}).status_code == 404


def test_asking_for_the_piles_to_be_rebuilt_queues_the_work(
    client: TestClient, scene: Scene
) -> None:
    """A way to ask. Grouping runs when a batch of scanning settles, so without it a change to how
    grouping works would do nothing until something unrelated triggered a rebuild."""
    turn_on(client)
    sign_in(client)

    answer = client.post("/api/faces/regroup")

    assert answer.status_code == 200
    assert answer.json()["job_id"]
    assert "face_regroup" in queued_types(client)


def test_asking_for_a_rebuild_is_refused_while_recognition_is_off(client: TestClient) -> None:
    sign_in(client)

    assert client.post("/api/faces/regroup").status_code == 409


# --- what has been recognized, gathered by person -------------------------------------------------


def test_what_has_been_recognized_is_gathered_by_person(client: TestClient, scene: Scene) -> None:
    """One card per face says nothing about who is on it: thirteen appearances of one person would
    read as thirteen separate answers to thirteen separate questions."""
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})

    answer = client.get("/api/faces/identified/people")

    assert answer.status_code == 200
    body = answer.json()
    assert body["total"] == 1
    card = body["people"][0]
    assert card["person_id"] == scene.person
    assert card["person_name"] == "Ada Lovelace"
    assert card["size"] == 1
    assert [face["track_id"] for face in card["faces"]] == [scene.track]


def test_a_card_says_how_many_of_its_faces_are_still_waiting(
    client: TestClient, scene: Scene
) -> None:
    """The number the card exists to show. A person with faces waiting is one press away from
    being finished, and that cannot be seen when every face is its own card."""
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="suggested")

    card = client.get("/api/faces/identified/people").json()["people"][0]

    assert card["waiting"] == 1


def test_faces_of_somebody_this_account_may_not_be_told_about_gather_namelessly(
    client: TestClient, scene: Scene
) -> None:
    """Under ONE nameless card rather than one card per hidden person, which would be a count of
    how many hidden people are in the library.

    The reachable state, and an ordinary one: a person is hidden, and a face in a file the user
    may see still names them. The file is perfectly visible; the person is on no list this user
    can see, so the face is the one surface still holding the name.

    Asked as an admin, who can hide somebody from themselves, which is the case that proves the
    names are resolved per user rather than per role.
    """
    turn_on(client)
    scene.attribute(client)
    admin = sign_in(client)
    scene.hide(client, "person", scene.person, admin)
    # And taken off the file's own list by hand, which is the other half of the reachable state:
    # the face attribution is a separate fact and survives it, so the face is then the only thing
    # left holding a name this user may not be told.
    scene.unname_by_hand(client)

    card = client.get("/api/faces/identified/people").json()["people"][0]

    assert card["person_id"] is None
    assert card["person_name"] is None
    assert card["size"] == 1, "the face itself is not concealed by concealing the person in it"


def test_the_gathered_wall_is_empty_while_recognition_is_off(
    client: TestClient, scene: Scene
) -> None:
    """A screen somebody is already on, so it says "nothing" rather than raising."""
    sign_in(client)

    answer = client.get("/api/faces/identified/people")

    assert answer.status_code == 200
    assert answer.json() == {"people": [], "total": 0, "offset": 0, "starters_only": 0, "others": 0}


def test_the_wall_is_searched_by_name_on_the_server(client: TestClient, scene: Scene) -> None:
    """The tab's search box narrows the list itself, so the count and the pages follow the words
    rather than a filter of the page in hand. Any part of the name, any case."""
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})

    found = client.get("/api/faces/identified/people", params={"q": "LOVE"}).json()
    missed = client.get("/api/faces/identified/people", params={"q": "Quill"}).json()

    assert [card["person_id"] for card in found["people"]] == [scene.person]
    assert found["total"] == 1
    assert missed["people"] == []
    assert missed["total"] == 0
    assert (missed["starters_only"], missed["others"]) == (0, 0)


def test_the_wall_is_searched_by_alias_too(client: TestClient, scene: Scene) -> None:
    """A person is found by any name they answer to, as on the People wall, and a wildcard typed
    is a character looked for rather than a pattern."""
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})
    write(
        db_path(client),
        [
            (
                "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
                (new_id(), scene.person, "Engine_Keeper"),
            )
        ],
    )

    by_alias = client.get("/api/faces/identified/people", params={"q": "e_k"}).json()
    by_wildcard = client.get("/api/faces/identified/people", params={"q": "%"}).json()

    assert by_alias["total"] == 1
    assert by_wildcard["total"] == 0


def test_a_search_leaves_out_the_nameless_card(client: TestClient, scene: Scene) -> None:
    """Every narrowing drops the card gathering People this account may not be told about: a
    search matching their name would otherwise say which of them is in there."""
    turn_on(client)
    scene.attribute(client)
    admin = sign_in(client)
    scene.hide(client, "person", scene.person, admin)
    scene.unname_by_hand(client)

    answer = client.get("/api/faces/identified/people", params={"q": "Ada"}).json()

    assert answer["people"] == []
    assert answer["total"] == 0


def test_faces_to_confirm_are_searched_by_name_on_the_server(
    client: TestClient, scene: Scene
) -> None:
    """The proposals tab narrows on the server too: the person's card, or none, and a count that
    says which. A group of faces names nobody, so no group is on a searched list."""
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="suggested")
    asked = {"kind": ["person", "may_be"]}

    found = client.get("/api/faces/to-check", params={**asked, "q": "ada"}).json()
    missed = client.get("/api/faces/to-check", params={**asked, "q": "Quill"}).json()
    groups = client.get("/api/faces/to-check", params={"kind": "group", "q": "ada"}).json()
    aside = client.get("/api/faces/to-check", params={"show": "ignored", "q": "ada"}).json()

    assert [item["id"] for item in found["items"]] == [scene.person]
    assert found["total"] == 1
    assert (missed["items"], missed["total"]) == ([], 0)
    assert (groups["items"], groups["total"]) == ([], 0)
    assert (aside["items"], aside["total"]) == ([], 0)


def test_a_searched_list_reopens_at_the_row_it_was_left_on(
    client: TestClient, scene: Scene
) -> None:
    """`from` is found in the searched list, so Back lands on the page that was being read; a row
    the search does not hold resolves to nothing and the top is served."""
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="suggested")
    asked = {"kind": ["person", "may_be"], "from": scene.person}

    held = client.get("/api/faces/to-check", params={**asked, "q": "ada"}).json()
    elsewhere = client.get("/api/faces/to-check", params={**asked, "q": "Quill"}).json()
    lone_group = client.get(
        "/api/faces/to-check", params={"kind": "group", "q": "ada", "from": scene.person}
    ).json()
    set_aside = client.get(
        "/api/faces/to-check", params={"show": "ignored", "q": "ada", "from": scene.person}
    ).json()
    every_tier = client.get("/api/faces/to-check", params={"q": "ada", "from": NEVER_EXISTED})
    wall = client.get(
        "/api/faces/identified/people", params={"q": "ada", "from": scene.person}
    ).json()

    assert held["offset"] == 0
    assert held["total"] == 1
    assert elsewhere["total"] == 0
    assert lone_group["total"] == 0
    assert set_aside["total"] == 0
    assert every_tier.status_code == 200
    assert wall["offset"] == 0


def test_one_persons_decided_faces_open_up_on_their_own(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})

    answer = client.get(f"/api/faces/identified/people/{scene.person}")

    assert answer.status_code == 200
    assert [item["track_id"] for item in answer.json()["items"]] == [scene.track]
    assert answer.json()["total"] == 1


def test_one_persons_page_says_who_it_is_about_even_when_the_tab_is_empty(
    client: TestClient, scene: Scene
) -> None:
    """Taken off the first face, the name would leave a tab with nothing on it calling her
    "Identified faces" in its title and its trail. The name is the page's, not the list's."""
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})

    empty = client.get(f"/api/faces/identified/people/{scene.person}?attribution=matched").json()

    assert empty["items"] == []
    assert empty["person_name"] == "Ada Lovelace"


def test_one_persons_page_withholds_a_name_the_viewer_may_not_be_told(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    admin = sign_in(client)
    scene.hide(client, "person", scene.person, admin)

    answer = client.get(f"/api/faces/identified/people/{scene.person}").json()

    assert answer["person_name"] is None


def test_one_persons_page_is_scoped_from_scratch_rather_than_from_the_wall(
    client: TestClient, scene: Scene
) -> None:
    """A share can move between one press and the next, and a page that trusted what the previous
    one handed it would show a face that no longer belongs there."""
    turn_on(client)
    admin = sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})
    scene.hide(client, "asset", scene.asset, admin)

    answer = client.get(f"/api/faces/identified/people/{scene.person}").json()

    assert answer["items"] == []
    assert answer["total"] == 0


def test_one_persons_page_counts_the_folder_files_whose_face_is_still_unnamed(
    client: TestClient, scene: Scene
) -> None:
    """A folder's name filed the file under her and its face waits in an unnamed group, so none of
    the three counts reaches it. The fourth number does, and a file the viewer cannot see is not
    in it."""
    turn_on(client)
    admin = sign_in(client)
    pile = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'folder')",
                (scene.asset, scene.person),
            ),
            (
                "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
                " VALUES (?, 'open', X'00', 1, 0, 0)",
                (pile,),
            ),
            ("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile, scene.track)),
        ],
    )

    answer = client.get(f"/api/faces/identified/people/{scene.person}").json()
    assert answer["unnamed_from_folder"] == 1
    assert answer["waiting"] == answer["matched"] == answer["confirmed"] == 0

    scene.hide(client, "asset", scene.asset, admin)
    hidden = client.get(f"/api/faces/identified/people/{scene.person}").json()
    assert hidden["unnamed_from_folder"] == 0


def test_one_persons_faces_are_empty_while_recognition_is_off(
    client: TestClient, scene: Scene
) -> None:
    sign_in(client)

    assert client.get(f"/api/faces/identified/people/{scene.person}").json()["items"] == []


# --- everybody's reference count, for a picker ----------------------------------------------------


def test_everybodys_reference_count_comes_back_in_one_answer(
    client: TestClient, scene: Scene
) -> None:
    """Asking per row turns choosing a name into one request per keystroke per candidate."""
    turn_on(client)
    sign_in(client)

    answer = client.get("/api/faces/references/strength")

    assert answer.status_code == 200
    body = answer.json()
    # Nobody has been agreed to yet, so nobody has references: absent rather than zero.
    assert body["people"] == {}
    assert body["verdicts"] == {}
    assert body["target"] > body["strong"] > body["floor"] > 0


def test_reference_counts_are_an_admins(client: TestClient, scene: Scene) -> None:
    """A list covering people a guest may have no business knowing exist."""
    turn_on(client)
    sign_in(client, "guest", who="nosy")

    assert client.get("/api/faces/references/strength").status_code == 403


def test_reference_counts_are_refused_while_recognition_is_off(
    client: TestClient, scene: Scene
) -> None:
    sign_in(client)

    assert client.get("/api/faces/references/strength").status_code == 409
