# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder picker: what it will show, and everything it will not.

It is the one thing in Sift that lists directories on its machine, so most tests prove it cannot be
walked out of the media area by `..`, by naming somewhere else, or through a link, each refused in
the same sentence. The trees are real directories.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import execute_blocking
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.main import create_app
from sift.slices.library_roots import browse
from sift.slices.library_roots.browse import BrowseRefused, look
from sift.slices.library_roots.tests.conftest import POSIX_ONLY, WINDOWS, WINDOWS_ONLY, junction
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]


BROWSE = "/api/library/browse"


# --- where a walk of the machine itself can start -----------------------------------------------
#
# Which drives exist, and nothing else. `sys.platform` is patched so both arms are checked on any
# machine.


def test_a_machine_that_is_not_windows_starts_at_the_single_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One root, and everything is under it. There are no drive letters to enumerate."""
    monkeypatch.setattr(sys, "platform", "linux")

    assert browse._machine_roots() == [Path("/")]


def test_a_machine_that_will_not_list_its_drives_offers_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine that will not list its drives offers none; the picker still opens."""
    monkeypatch.setattr(sys, "platform", "win32")

    def _refuse() -> list[str]:
        raise OSError("the drive list is unavailable")

    monkeypatch.setattr(os, "listdrives", _refuse, raising=False)

    assert browse._machine_roots() == []


def test_a_drive_that_is_not_ready_is_left_out_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drive that is not ready is left out, whether it says no or says nothing."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "listdrives", lambda: ["A:\\", "B:\\", "C:\\"], raising=False)

    def _asked(self: Path) -> bool:
        if str(self).startswith("A:"):
            raise OSError(21, "The device is not ready")
        return not str(self).startswith("B:")

    monkeypatch.setattr(Path, "is_dir", _asked)

    assert browse._machine_roots() == [Path("C:\\")]


@pytest.fixture
def media(tmp_path: Path) -> Path:
    """A media area with two folders handed over and one file sitting beside them."""
    area = tmp_path / "media"
    (area / "library" / "holidays").mkdir(parents=True)
    (area / "tv" / "shows").mkdir(parents=True)
    (area / "notes.txt").write_text("not a folder")
    return area


@pytest.fixture
def app(tmp_path: Path, media: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI, media: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        _MEDIA[id(c)] = media
        yield c
        _MEDIA.pop(id(c), None)


#: The media area each client's tests arrange, by client. See `sign_in`.
_MEDIA: dict[int, Path] = {}


def sign_in(client: TestClient, role: str) -> None:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, csrf = establish_session(
        db_path, role=role, username=f"browse-{role}", password="Browse-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    # The handed-over folders are each a grant, as the folder dialog makes one; links are not.
    media = _MEDIA.get(id(client))
    if media is None or not media.is_dir():
        return
    for entry in sorted(media.iterdir()):
        if entry.is_dir() and not entry.is_symlink() and not os.path.isjunction(entry):
            execute_blocking(
                db_path,
                "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, ?)",
                (new_id(), str(entry), 1_700_000_000),
            )


# --- what it shows ----------------------------------------------------------------------------


def test_the_media_area_lists_the_folders_that_were_handed_over(
    client: TestClient, media: Path
) -> None:
    sign_in(client, "admin")

    body = client.get(BROWSE).json()

    assert [entry["name"] for entry in body["entries"]] == ["library", "tv"]
    # The grant list is not itself a folder, so it has no path.
    assert body["path"] == ""
    assert body["nothing_granted"] is False
    # Each row carries its whole path, since two grants can end in the same word.
    assert [entry["path"] for entry in body["entries"]] == [
        str(media / "library"),
        str(media / "tv"),
    ]


def test_the_top_level_is_every_folder_sift_has_library_folders_and_grants(
    client: TestClient, media: Path, tmp_path: Path
) -> None:
    """A library folder added by its typed path is no grant, and is still listed and walkable."""
    typed = tmp_path / "typed"
    (typed / "inside").mkdir(parents=True)
    sign_in(client, "admin")
    execute_blocking(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (new_id(), "typed", str(typed), 1_700_000_000),
    )

    top = client.get(BROWSE).json()
    assert [entry["name"] for entry in top["entries"]] == ["library", "tv", "typed"]

    inside = client.get(BROWSE, params={"path": str(typed)}).json()
    assert [entry["name"] for entry in inside["entries"]] == ["inside"]


def test_a_file_chooser_is_told_the_files_matching_its_names_and_no_others(
    client: TestClient, media: Path
) -> None:
    library = media / "library"
    for name in ("stash-go.sqlite", "stash-go.sqlite.20260101_120000", "config.yml", "a.mp4"):
        (library / name).write_bytes(b"x")
    sign_in(client, "admin")

    asked = client.get(
        BROWSE, params={"path": str(library), "files": ["stash-go.sqlite*", "CONFIG.YML"]}
    ).json()
    plain = client.get(BROWSE, params={"path": str(library)}).json()

    assert [entry["name"] for entry in asked["files"]] == [
        "config.yml",
        "stash-go.sqlite",
        "stash-go.sqlite.20260101_120000",
    ]
    assert asked["file_count"] == 4
    assert plain["files"] == []
    too_many = client.get(BROWSE, params={"path": str(library), "files": ["*"] * 9})
    assert too_many.status_code == 400


def test_a_folder_inside_one_can_be_looked_into(client: TestClient, media: Path) -> None:
    sign_in(client, "admin")

    body = client.get(BROWSE, params={"path": str(media / "library")}).json()

    assert [entry["name"] for entry in body["entries"]] == ["holidays"]


def test_the_breadcrumb_is_the_way_back_out(client: TestClient, media: Path) -> None:
    sign_in(client, "admin")

    body = client.get(BROWSE, params={"path": str(media / "library" / "holidays")}).json()

    # It stops at the granted folder; the client draws the crumb back to the grant list.
    assert [crumb["name"] for crumb in body["breadcrumb"]] == ["library", "holidays"]
    # Every crumb is a path the endpoint accepts back.
    for crumb in body["breadcrumb"]:
        assert client.get(BROWSE, params={"path": crumb["path"]}).status_code == 200


def test_a_file_is_not_offered_as_somewhere_to_go(client: TestClient) -> None:
    """Directories only. A file in the media area is not a folder and is not a listing either."""
    sign_in(client, "admin")

    assert "notes.txt" not in [entry["name"] for entry in client.get(BROWSE).json()["entries"]]


def test_a_file_cannot_be_browsed_into(client: TestClient, media: Path) -> None:
    sign_in(client, "admin")

    response = client.get(BROWSE, params={"path": str(media / "notes.txt")})

    assert response.status_code == 400


def test_the_folders_come_back_in_the_order_somebody_reads_them(
    client: TestClient, media: Path
) -> None:
    (media / "Archive" / "old").mkdir(parents=True)
    (media / "beach" / "2024").mkdir(parents=True)
    sign_in(client, "admin")

    names = [entry["name"] for entry in client.get(BROWSE).json()["entries"]]

    assert names == ["Archive", "beach", "library", "tv"]


def test_a_granted_folder_with_nothing_in_it_is_still_offered(
    client: TestClient, media: Path
) -> None:
    """An empty granted folder is still offered: somebody chose it."""
    (media / "empty").mkdir()
    sign_in(client, "admin")

    names = [entry["name"] for entry in client.get(BROWSE).json()["entries"]]

    assert names == ["empty", "library", "tv"]


def test_one_folder_handed_over_is_not_nothing_handed_over(client: TestClient, media: Path) -> None:
    """An empty folder is a folder. It draws a row, not the how-to-hand-a-folder-over screen."""
    shutil.rmtree(media)
    (media / "library").mkdir(parents=True)
    sign_in(client, "admin")

    body = client.get(BROWSE).json()

    assert [entry["name"] for entry in body["entries"]] == ["library"]
    assert body["nothing_granted"] is False


# --- confinement ------------------------------------------------------------------------------
#
# Remove the `confine` call in `browse._look` and all three go red.


def test_a_path_that_climbs_out_with_dot_dot_is_refused(client: TestClient, media: Path) -> None:
    sign_in(client, "admin")

    response = client.get(BROWSE, params={"path": str(media / ".." / "..")})

    assert response.status_code == 400
    assert "handed to it" in response.json()["detail"]


def test_a_path_somewhere_else_entirely_is_refused(client: TestClient, tmp_path: Path) -> None:
    """Not a trick, just asking for somewhere else. The commonest shape of this by far."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    sign_in(client, "admin")

    assert client.get(BROWSE, params={"path": str(elsewhere)}).status_code == 400


def test_the_root_of_the_filesystem_is_refused(client: TestClient) -> None:
    """The sharp edge this picker exists not to have."""
    sign_in(client, "admin")

    assert client.get(BROWSE, params={"path": "/"}).status_code == 400
    assert client.get(BROWSE, params={"path": "/etc"}).status_code == 400


@POSIX_ONLY
def test_a_symlink_pointing_out_of_the_media_area_is_refused(
    client: TestClient, media: Path, tmp_path: Path
) -> None:
    """A symlink pointing out is refused: only resolving it shows where it lands."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets").mkdir()
    (media / "escape").symlink_to(outside)
    sign_in(client, "admin")

    response = client.get(BROWSE, params={"path": str(media / "escape")})

    assert response.status_code == 400
    # Nor is it offered.
    assert "escape" not in [entry["name"] for entry in client.get(BROWSE).json()["entries"]]


@WINDOWS_ONLY
def test_a_junction_pointing_out_of_the_media_area_is_refused(
    client: TestClient, media: Path, tmp_path: Path
) -> None:
    """A junction pointing out is refused: `mklink /J` needs no privilege and is not a symlink."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets").mkdir()
    junction(media / "escape", outside)
    sign_in(client, "admin")

    response = client.get(BROWSE, params={"path": str(media / "escape")})

    assert response.status_code == 400
    # Nor is it offered.
    assert "escape" not in [entry["name"] for entry in client.get(BROWSE).json()["entries"]]


@POSIX_ONLY
def test_a_symlink_pointing_out_cannot_be_walked_through(
    client: TestClient, media: Path, tmp_path: Path
) -> None:
    """Not just the link itself: nothing reached through it, either."""
    outside = tmp_path / "outside"
    (outside / "secrets").mkdir(parents=True)
    (media / "escape").symlink_to(outside)
    sign_in(client, "admin")

    assert client.get(BROWSE, params={"path": str(media / "escape" / "secrets")}).status_code == 400


@POSIX_ONLY
def test_a_symlink_that_stays_inside_the_media_area_is_allowed(
    client: TestClient, media: Path
) -> None:
    """A symlink staying inside the media area is allowed: confinement is about where it lands."""
    (media / "shortcut").symlink_to(media / "library")
    sign_in(client, "admin")

    body = client.get(BROWSE, params={"path": str(media / "shortcut")}).json()

    assert [entry["name"] for entry in body["entries"]] == ["holidays"]


# --- who may ask ------------------------------------------------------------------------------


def test_a_guest_cannot_look_at_the_machine(client: TestClient) -> None:
    sign_in(client, "guest")

    assert client.get(BROWSE).status_code == 403


def test_nobody_signed_in_cannot_look_at_the_machine(client: TestClient) -> None:
    assert client.get(BROWSE).status_code == 401


# --- nothing handed over yet ------------------------------------------------------------------


def test_nothing_handed_over_at_all_says_so(client: TestClient, media: Path) -> None:
    """With nothing handed over the answer says so, and the screen explains how to hand one over."""
    shutil.rmtree(media)
    media.mkdir()
    sign_in(client, "admin")

    body = client.get(BROWSE).json()

    assert body["nothing_granted"] is True
    assert body["entries"] == []


@pytest.mark.skipif(
    not WINDOWS,
    reason=(
        "A UNC path only HAS no last component on Windows. A POSIX Path of the same string has no "
        "separators in it at all, so `.name` is the whole string and the branch under test never "
        "runs, so the assertion would pass without proving anything"
    ),
)
async def test_a_granted_folder_with_no_name_of_its_own_is_named_by_its_path() -> None:
    """A UNC share or drive root with no last component is named by its path, separator trimmed."""
    listing = await look([Path("\\\\nas\\Media")], None)

    assert [entry.name for entry in listing.entries] == ["\\\\nas\\Media"]


async def test_no_granted_folders_says_nothing_has_been_handed_over() -> None:
    """The app's first run: the grant list is empty, so there is nothing to pick."""
    listing = await look([], None)

    assert listing.nothing_granted is True
    assert listing.entries == ()


async def test_a_granted_folder_that_is_offline_is_still_listed(tmp_path: Path) -> None:
    """A granted folder that is offline is still listed, and refuses when clicked."""
    here = tmp_path / "here"
    here.mkdir()

    listing = await look([here, tmp_path / "offline-nas"], None)

    assert sorted(entry.name for entry in listing.entries) == ["here", "offline-nas"]
    assert listing.nothing_granted is False


async def test_a_folder_inside_an_offline_grant_is_refused(tmp_path: Path) -> None:
    """A folder inside an offline grant is refused: `confine` needs a real directory."""
    with pytest.raises(BrowseRefused):
        await look([tmp_path / "offline-nas"], tmp_path / "offline-nas" / "clips")


async def test_an_empty_folder_inside_a_grant_is_not_nothing_granted(tmp_path: Path) -> None:
    """The distinction the flag exists for. This folder is empty; the grant list is not."""
    (tmp_path / "library").mkdir()

    listing = await look([tmp_path], tmp_path / "library")

    assert listing.entries == ()
    assert listing.nothing_granted is False


async def test_a_path_is_proved_against_every_grant_not_just_the_first(tmp_path: Path) -> None:
    """A path inside the last of several grants is accepted."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "a").mkdir(parents=True)
    (second / "b" / "deep").mkdir(parents=True)

    listing = await look([first, second], second / "b")

    assert [entry.name for entry in listing.entries] == ["deep"]
    assert listing.breadcrumb[0].path == str(second)


async def test_a_path_inside_none_of_the_grants_is_refused(tmp_path: Path) -> None:
    """Two folders handed over and a third that was not. The commonest shape of this by far."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    elsewhere = tmp_path / "elsewhere"
    for each in (first, second, elsewhere):
        each.mkdir()

    with pytest.raises(BrowseRefused):
        await look([first, second], elsewhere)


# --- what it says about writing -----------------------------------------------------------------


async def test_a_folder_sift_can_write_to_is_reported_as_writable(tmp_path: Path) -> None:
    (tmp_path / "library").mkdir()

    listing = await look([tmp_path], tmp_path / "library")

    assert listing.writable is True
    assert listing.read_only_mount is False


@POSIX_ONLY
async def test_a_folder_sift_has_no_permission_in_is_not_writable(tmp_path: Path) -> None:
    """A folder whose files belong to another account is not writable, mount permitting or not."""
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        listing = await look([tmp_path], locked)
        assert listing.writable is False
        # The reason is told apart.
        assert listing.read_only_mount is False
    finally:
        locked.chmod(0o700)


# --- the module on its own ----------------------------------------------------------------------


async def test_a_folder_that_cannot_be_read_lists_as_empty_rather_than_failing(
    tmp_path: Path,
) -> None:
    """One unreadable folder must not take the whole picker down."""
    (tmp_path / "library").mkdir()
    unreadable = tmp_path / "library" / "private"
    unreadable.mkdir(mode=0o000)
    try:
        listing = await look([tmp_path], unreadable)
        assert listing.entries == ()
    finally:
        unreadable.chmod(0o700)


@POSIX_ONLY
async def test_a_mount_that_cannot_be_read_is_still_offered(tmp_path: Path) -> None:
    """A granted folder that cannot be read stays listed and lists as empty."""
    media = tmp_path / "media"
    media.mkdir()
    (media / "library").mkdir()
    (media / "library" / "clip.mp4").write_bytes(b"bytes")
    locked = media / "locked"
    locked.mkdir(mode=0o000)
    try:
        listing = await look([media / "library", locked], None)

        assert sorted(entry.name for entry in listing.entries) == ["library", "locked"]
    finally:
        locked.chmod(0o700)


async def test_looking_outside_the_root_raises_rather_than_returning_something(
    tmp_path: Path,
) -> None:
    """The module's own refusal, under the route that turns it into a 400."""
    root = tmp_path / "media"
    root.mkdir()
    (tmp_path / "outside").mkdir()

    with pytest.raises(BrowseRefused):
        await look([root], tmp_path / "outside")


async def test_a_folder_says_how_many_files_are_in_it(tmp_path: Path) -> None:
    """A folder says how many files it holds, so an empty picker is told from an empty folder,
    without naming any file."""
    (tmp_path / "holiday.mp4").write_bytes(b"x")
    (tmp_path / "birthday.mp4").write_bytes(b"x")
    (tmp_path / "clips" / "trip").mkdir(parents=True)

    listing = await look([tmp_path], tmp_path)

    assert listing.file_count == 2
    # Subfolders are not files, and no file name is in the answer.
    assert [entry.name for entry in listing.entries] == ["clips"]
    assert "holiday" not in str(listing)


async def test_a_folder_with_no_files_says_none_rather_than_nothing(tmp_path: Path) -> None:
    (tmp_path / "clips").mkdir()

    assert (await look([tmp_path], tmp_path)).file_count == 0


async def test_a_folder_beside_the_media_area_sharing_its_name_is_refused(tmp_path: Path) -> None:
    """`/media-backup` is not inside `/media`: confinement compares parents, not strings."""
    area = tmp_path / "media"
    area.mkdir()
    neighbour = tmp_path / "media-backup"
    neighbour.mkdir()

    with pytest.raises(BrowseRefused):
        await look([area], neighbour)


# --- when the operating system refuses --------------------------------------------------------
#
# The call is made to fail, since `chmod` bits are ignored on Windows: a refusal is skipped or
# stood in for, never taking the picker down.


def _refusing(name: str) -> Callable[[str | os.PathLike[str]], object]:
    """A `scandir` that refuses one named directory and answers normally elsewhere."""
    real = os.scandir

    def refuse(directory: str | os.PathLike[str]) -> object:
        if Path(directory).name == name:
            raise PermissionError(13, "Permission denied")
        return real(directory)

    return refuse


async def test_a_folder_the_machine_will_not_list_reads_as_empty_rather_than_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder the machine will not list reads as empty rather than failing."""
    (tmp_path / "library").mkdir()
    monkeypatch.setattr(os, "scandir", _refusing("library"))

    listing = await look([tmp_path], tmp_path / "library")

    assert listing.entries == ()
    assert listing.file_count == 0


async def test_a_file_is_not_a_folder_and_is_refused_as_one(tmp_path: Path) -> None:
    """A file inside the granted area is refused as not a folder."""
    (tmp_path / "notes.txt").write_text("not a folder")

    with pytest.raises(BrowseRefused, match="a folder"):
        await look([tmp_path], tmp_path / "notes.txt")
