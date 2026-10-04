# SPDX-License-Identifier: AGPL-3.0-or-later
"""Handing a folder to Sift, and taking it back.

The backend runs as the logged-in user with every drive in reach, so nothing outside Sift confines
what the folder picker can reach, and a bug in `confine` would reach anything the user can.

A grant is what stands in its place: the record that somebody chose a folder in the operating
system's own dialog, which no page can open, drive or read. The picker lists only what is inside one.
So the tests that matter here are the ones about what CANNOT be granted, and about a grant never
quietly widening to cover more than the folder somebody pointed at.

Grants are permission to LOOK. Whether Sift may CHANGE anything in a folder is the filesystem's
answer, asked by the delete and organise features at the moment of the write.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import Settings, get_settings
from sift.kernel.content import LibraryStore, RootOverlap
from sift.kernel.content.library import LibraryError, NotAFolder, ReservedPath
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import establish_session

GRANTS = "/api/library/grants"
BROWSE = "/api/library/browse"


# --- the store ----------------------------------------------------------------------------------


async def test_a_folder_can_be_handed_over(library_store: LibraryStore, tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()

    grant = await library_store.grant(media)

    assert Path(grant.abs_path) == media.resolve()
    assert [Path(one.abs_path) for one in await library_store.grants()] == [media.resolve()]


async def test_the_stored_path_is_the_resolved_one(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    """Two paths that reach the same folder by different names must not become two grants.

    Everything after this compares stored paths (whether a grant overlaps another, whether a
    requested folder is inside one), and two names for one directory would compare as different
    while behaving as the same.
    """
    real = tmp_path / "real"
    real.mkdir()

    grant = await library_store.grant(tmp_path / "." / "real")

    assert Path(grant.abs_path) == real.resolve()


async def test_a_folder_that_is_not_there_is_refused(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    with pytest.raises(NotAFolder):
        await library_store.grant(tmp_path / "never-made")


async def test_a_file_is_refused(library_store: LibraryStore, tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")

    with pytest.raises(NotAFolder):
        await library_store.grant(clip)


async def test_a_relative_path_is_refused(library_store: LibraryStore) -> None:
    with pytest.raises(NotAFolder):
        await library_store.grant(Path("media"))


async def test_sifts_own_folders_cannot_be_handed_over(
    library_store: LibraryStore, settings: Settings
) -> None:
    """The cache inside a grant is the cache inside the area offered as a library.

    Sift never writes into a library. That is a promise about behaviour; refusing this is what keeps
    it a fact about the layout, because a grant over the cache folder would offer it as somewhere to
    index and then put thumbnails beside the originals with everything working as designed.
    """
    settings.cache_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ReservedPath):
        await library_store.grant(settings.cache_dir)


async def test_the_same_folder_twice_is_refused(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    await library_store.grant(media)

    with pytest.raises(LibraryError):
        await library_store.grant(media)


async def test_a_folder_inside_one_already_handed_over_is_refused(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    """It adds nothing (the picker can already see it), so accepting would be two records of one
    permission, which is how two records come to disagree."""
    outer = tmp_path / "media"
    (outer / "inner").mkdir(parents=True)
    await library_store.grant(outer)

    with pytest.raises(RootOverlap):
        await library_store.grant(outer / "inner")


async def test_the_parent_of_one_already_handed_over_is_refused(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    """The same mistake written backwards, and the more dangerous direction.

    Granting a parent would silently WIDEN what Sift can see (from one folder to a whole drive)
    while the screen listing grants still showed the same number of rows. Explicit is the entire
    point of a grant, so this is refused and the person removes the narrow one first.
    """
    inner = tmp_path / "media" / "inner"
    inner.mkdir(parents=True)
    await library_store.grant(inner)

    with pytest.raises(RootOverlap):
        await library_store.grant(tmp_path / "media")


async def test_a_folder_can_be_taken_back(library_store: LibraryStore, tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    grant = await library_store.grant(media)

    removed = await library_store.revoke_grant(grant.id)

    assert removed is not None
    assert await library_store.grants() == []


async def test_taking_back_something_never_given_answers_nothing(
    library_store: LibraryStore,
) -> None:
    assert await library_store.revoke_grant("01KZQ438X8BDCHMA0ZKPPM9SEH") is None


async def test_a_made_up_identifier_is_refused_before_the_query(
    library_store: LibraryStore,
) -> None:
    """Not a valid identifier at all, so it never reaches the database."""
    assert await library_store.revoke_grant("../../etc") is None


async def test_a_folder_holding_a_library_cannot_be_taken_back(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    """Refused, and not for tidiness.

    Revoking would leave the library indexed and served while the one screen that lists folders
    could no longer see where it came from, so it would keep working with no visible explanation
    anywhere. Removing the library is a decision somebody makes; this happening as a side effect of
    another one is not.
    """
    media = tmp_path / "media"
    (media / "clips").mkdir(parents=True)
    grant = await library_store.grant(media)
    await library_store.create_root(name="clips", abs_path=media / "clips")

    with pytest.raises(LibraryError) as refusal:
        await library_store.revoke_grant(grant.id)

    assert "clips" in str(refusal.value)


async def test_the_library_being_the_granted_folder_itself_also_refuses(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    """The other half of `overlaps`: the root IS the grant rather than being inside it."""
    media = tmp_path / "media"
    media.mkdir()
    grant = await library_store.grant(media)
    await library_store.create_root(name="media", abs_path=media)

    with pytest.raises(LibraryError):
        await library_store.revoke_grant(grant.id)


# --- the endpoints ------------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def sign_in(client: TestClient, role: str) -> None:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, csrf = establish_session(
        db_path, role=role, username=f"grants-{role}", password="Grants-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf


def test_a_granted_folder_becomes_what_the_picker_can_see(
    client: TestClient, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    (media / "clips").mkdir(parents=True)
    sign_in(client, "admin")

    # Before: nothing has been handed over, and the picker says so rather than drawing a blank list.
    assert client.get(BROWSE).json()["nothing_granted"] is True

    created = client.post(GRANTS, json={"path": str(media)})
    assert created.status_code == 201, created.text

    listing = client.get(BROWSE).json()
    assert listing["nothing_granted"] is False
    assert [entry["name"] for entry in listing["entries"]] == ["media"]
    # And it can be walked into, which is the whole purpose of granting it.
    inside = client.get(BROWSE, params={"path": str(media)}).json()
    assert [entry["name"] for entry in inside["entries"]] == ["clips"]


def test_a_folder_that_was_not_granted_still_cannot_be_looked_into(
    client: TestClient, tmp_path: Path
) -> None:
    """The confinement, from the outside. One folder handed over, another not."""
    (tmp_path / "media").mkdir()
    (tmp_path / "elsewhere" / "private").mkdir(parents=True)
    sign_in(client, "admin")
    client.post(GRANTS, json={"path": str(tmp_path / "media")})

    assert client.get(BROWSE, params={"path": str(tmp_path / "elsewhere")}).status_code == 400


def test_the_grants_are_listed_with_their_paths(client: TestClient, tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    sign_in(client, "admin")
    client.post(GRANTS, json={"path": str(media)})

    body = client.get(GRANTS).json()

    assert [one["path"] for one in body["grants"]] == [str(media.resolve())]
    assert body["grants"][0]["granted_at"] > 0


def test_a_refusal_arrives_as_the_sentence_the_kernel_wrote(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in(client, "admin")

    response = client.post(GRANTS, json={"path": str(tmp_path / "never-made")})

    assert response.status_code == 400
    assert "cannot find" in response.json()["detail"]


def test_a_guest_cannot_see_which_folders_sift_was_given(client: TestClient) -> None:
    """The list of grants describes the machine, so it is admin-only like every route that does."""
    sign_in(client, "guest")

    assert client.get(GRANTS).status_code == 403


def test_a_guest_cannot_hand_a_folder_over(client: TestClient, tmp_path: Path) -> None:
    sign_in(client, "guest")

    assert client.post(GRANTS, json={"path": str(tmp_path)}).status_code == 403


def test_nobody_signed_in_can_do_any_of_it(client: TestClient, tmp_path: Path) -> None:
    """Both refused, and the two codes differ for a reason worth writing down.

    Reading needs a session, so with none it is a 401: "say who you are". Writing is refused
    earlier than that, by the check that the request came from Sift's own page at all, which answers
    403. Either way nothing happens; asserting the codes stops a later change from turning one of
    them into a 200 unnoticed.
    """
    assert client.get(GRANTS).status_code == 401
    assert client.post(GRANTS, json={"path": str(tmp_path)}).status_code == 403


def test_a_request_with_no_token_proving_the_page_was_sift_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    sign_in(client, "admin")
    del client.headers[CSRF_HEADER_NAME]

    assert client.post(GRANTS, json={"path": str(media)}).status_code == 403


async def test_a_second_grant_that_does_not_overlap_is_kept_beside_the_first(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    """The loop's other way out, and the ordinary case: two folders that are simply different.

    Every other case here ends in a refusal, so the arc that walks past an existing grant and keeps
    going had never been taken, which means nothing proved a second grant is possible at all.
    """
    one = tmp_path / "photos"
    two = tmp_path / "films"
    one.mkdir()
    two.mkdir()

    await library_store.grant(one)
    await library_store.grant(two)

    assert sorted(Path(g.abs_path).name for g in await library_store.grants()) == [
        "films",
        "photos",
    ]


async def test_a_grant_holding_no_library_is_given_back(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    """The same loop in `revoke_grant`: a root that is somewhere else is walked past.

    The refusal beside it is tested; this is the arc where a library exists and is not in this
    folder, which is what makes the refusal about THIS folder rather than about having any library
    at all.
    """
    granted = tmp_path / "granted"
    elsewhere = tmp_path / "elsewhere"
    granted.mkdir()
    elsewhere.mkdir()
    await library_store.create_root(name="Elsewhere", abs_path=elsewhere)
    given = await library_store.grant(granted)

    taken = await library_store.revoke_grant(given.id)

    assert taken is not None
    assert taken.id == given.id
    assert await library_store.grants() == []


# --- walking the machine itself -----------------------------------------------------------------
#
# The scope that exists so a Sift on another computer can be pointed at a new folder at all. Without
# it the picker could only show what had already been handed over, and handing something over would
# need the operating system's own dialog, which only the application on that machine can open. It
# grants no new permission: an administrator's session can name any path to POST /library/grants,
# and this only makes that reachable without knowing how to spell it.


def test_the_machine_scope_lists_somewhere_to_start_with_nothing_granted(
    client: TestClient,
) -> None:
    """A browser, and not one folder handed over: the machine still offers somewhere to start."""
    sign_in(client, "admin")

    assert client.get(BROWSE).json()["nothing_granted"] is True

    answer = client.get(BROWSE, params={"scope": "machine"})

    assert answer.status_code == 200, answer.text
    listing = answer.json()
    assert listing["entries"], "the machine has to offer somewhere to begin"
    # Every row is named, which a drive root does not get for free: its last component is empty.
    assert all(entry["name"] for entry in listing["entries"])


def test_the_machine_scope_can_walk_into_a_folder_nobody_granted(
    client: TestClient, tmp_path: Path
) -> None:
    """The whole point, and the thing the default scope refuses in the test above this block."""
    (tmp_path / "elsewhere" / "clips").mkdir(parents=True)
    sign_in(client, "admin")

    refused = client.get(BROWSE, params={"path": str(tmp_path / "elsewhere")})
    assert refused.status_code == 400

    allowed = client.get(BROWSE, params={"path": str(tmp_path / "elsewhere"), "scope": "machine"})

    assert allowed.status_code == 200, allowed.text
    assert [entry["name"] for entry in allowed.json()["entries"]] == ["clips"]


def test_the_default_scope_is_still_the_granted_folders(client: TestClient, tmp_path: Path) -> None:
    """A caller that says nothing gets the granted folders, which every other screen wants."""
    (tmp_path / "elsewhere").mkdir()
    sign_in(client, "admin")

    assert client.get(BROWSE, params={"scope": "granted"}).status_code == 200
    assert client.get(BROWSE, params={"path": str(tmp_path / "elsewhere")}).status_code == 400


def test_a_scope_nobody_offers_is_refused_rather_than_guessed_at(client: TestClient) -> None:
    """Unvalidated it would fall through to the granted list, which is a wrong answer given
    confidently rather than a refusal."""
    sign_in(client, "admin")

    assert client.get(BROWSE, params={"scope": "everything"}).status_code == 422


def test_the_machine_scope_is_admin_only(client: TestClient) -> None:
    """It describes the server's disks, so it is behind the same door as the rest of browsing."""
    sign_in(client, "guest")

    assert client.get(BROWSE, params={"scope": "machine"}).status_code == 403
