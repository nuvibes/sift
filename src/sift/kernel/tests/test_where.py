# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a file sits, said to one viewer (`kernel.where`).

An admin reads the full path, with the profile folder's name hidden when they ask; anyone else
never reads an absolute path; a folder the viewer may not see, or one Hidden hides while it is
shut, is "...". Paths are the server's own, so the expectations are built with its separator.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath
from typing import Any, cast

import pytest

from sift.kernel import where
from sift.kernel.access import Folder, Role, Viewer
from sift.kernel.where import (
    HIDE_ACCOUNT_NAME_KEY,
    REDACTED,
    UNSEEN,
    blurred,
    folder_said,
    said,
    whereabouts_from,
)

ROOT = "01HX0000000000000000000701"
PROFILE = PurePath("C:/Users/someone")
LIBRARY = str(PROFILE / "Videos" / "Library")
ADMIN = Viewer(id="admin", role=Role.ADMIN)
GUEST = Viewer(id="guest", role=Role.GUEST)


def _path(*parts: str) -> str:
    return os.sep.join(parts)


def _folder(rel_path: str, *, concealed: bool = False) -> Folder:
    name = rel_path.rsplit("/", 1)[-1] if rel_path else "Library"
    return Folder(
        id=f"f-{rel_path}",
        root_id=ROOT,
        parent_id=None if rel_path == "" else "parent",
        rel_path=rel_path,
        name=name,
        vault=concealed,
        concealed=concealed,
    )


EVERY_FOLDER = [_folder(""), _folder("2024"), _folder("2024/Beach")]


def _where(viewer: Viewer, folders: list[Folder], *, profile: PurePath | None = None) -> str:
    return whereabouts_from(viewer, folders, root_paths={ROOT: LIBRARY}, profile=profile).of(
        ROOT, "2024/Beach/clip.mp4"
    )


def test_an_admin_reads_the_full_path() -> None:
    assert _where(ADMIN, EVERY_FOLDER) == _path(LIBRARY, "2024", "Beach", "clip.mp4")


def test_an_admin_who_asks_reads_the_profile_folder_as_redacted() -> None:
    redacted = str(PROFILE.parent / REDACTED / "Videos" / "Library")
    assert _where(ADMIN, EVERY_FOLDER, profile=PROFILE) == _path(
        redacted, "2024", "Beach", "clip.mp4"
    )
    # Found from the profile folder itself, and only inside it.
    elsewhere = str(PurePath("D:/Users/someone/x"))
    assert blurred(elsewhere, PROFILE) == elsewhere
    if sys.platform == "win32":
        assert blurred(r"c:\users\SOMEONE\x", PROFILE) == rf"C:\Users\{REDACTED}\x"


def test_a_guest_never_reads_an_absolute_path() -> None:
    """The library folder's name where they may see it, and "..." for each run they may not."""
    assert _where(GUEST, EVERY_FOLDER, profile=PROFILE) == _path(
        "Library", "2024", "Beach", "clip.mp4"
    )
    assert _where(GUEST, [_folder("2024/Beach")]) == _path(UNSEEN, "Beach", "clip.mp4")
    assert _where(GUEST, []) == _path(UNSEEN, "clip.mp4")


def test_a_folder_hidden_hides_is_unseen_while_hidden_is_shut() -> None:
    shut = [_folder(""), _folder("2024", concealed=True), _folder("2024/Beach")]
    assert _where(ADMIN, shut) == _path(LIBRARY, UNSEEN, "Beach", "clip.mp4")
    # A hidden library folder takes the absolute path with it.
    assert _where(ADMIN, [_folder("", concealed=True), *EVERY_FOLDER[1:]]) == _path(
        UNSEEN, "2024", "Beach", "clip.mp4"
    )


def test_a_folder_hidden_hides_is_named_while_hidden_is_open() -> None:
    """The access layer hands back a concealed folder to a viewer who opened Hidden, still marked
    concealed; that viewer reads its name, as they read the files in it."""
    opened = Viewer(id="admin", role=Role.ADMIN, show_hidden=True)
    hidden = [_folder(""), _folder("2024", concealed=True), _folder("2024/Beach")]
    assert _where(opened, hidden) == _path(LIBRARY, "2024", "Beach", "clip.mp4")
    assert (
        folder_said(
            "2024/Beach",
            seen=whereabouts_from(opened, hidden, root_paths={}, profile=None).seen[ROOT],
        )
        == "2024/Beach"
    )


def test_a_file_at_the_top_of_a_drive_has_one_separator() -> None:
    assert said("clip.mp4", seen={""}, top="D:\\", sep="\\") == r"D:\clip.mp4"


def test_a_folder_inside_its_library_is_said_only_as_far_as_it_is_seen() -> None:
    """What a History move line names: the folders the reader may see, and one "..." for each run
    of those they may not."""
    assert folder_said("clips/sealed/deep", seen={"", "clips"}) == f"clips/{UNSEEN}"
    assert folder_said("clips/sealed/open", seen={"clips", "clips/sealed/open"}) == (
        f"clips/{UNSEEN}/open"
    )
    assert folder_said("clips/summer", seen={"clips", "clips/summer"}) == "clips/summer"


class _Settings:
    """A per-user setting store holding one answer."""

    def __init__(self, on: bool) -> None:
        self._on = on

    async def get_user(self, user_id: str, key: str) -> object:
        return self._on if key == HIDE_ACCOUNT_NAME_KEY else None


async def test_only_an_admin_who_asks_has_their_profile_folder_blurred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one reading every place that says an absolute path shares, Settings > Folders included."""
    monkeypatch.setattr(where, "profile_folder", lambda: PROFILE)

    assert await where.profile_to_blur(ADMIN, cast(Any, _Settings(True))) == PROFILE
    assert await where.profile_to_blur(ADMIN, cast(Any, _Settings(False))) is None
    assert await where.profile_to_blur(GUEST, cast(Any, _Settings(True))) is None


def test_the_profile_folder_is_the_home_of_the_account_sift_runs_as() -> None:
    assert where.profile_folder() == Path.home()


def test_a_machine_that_cannot_say_whose_account_it_is_blurs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Path.home()` raises where neither the environment nor the account database names a home
    (a service account with no profile). Nothing is known to blur then, so nothing is, rather
    than the page that asked failing over a courtesy."""

    def _no_home() -> PurePath:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", _no_home)

    assert where.profile_folder() is None


class _Access:
    """The folders one viewer may see, whatever library is asked for."""

    def __init__(self, folders: list[Folder]) -> None:
        self.asked: list[str | None] = []
        self._folders = folders

    async def visible_folders(self, viewer: Viewer, *, root_id: str | None = None) -> list[Folder]:
        self.asked.append(root_id)
        return self._folders


class _Library:
    """One library, at `LIBRARY`."""

    def __init__(self) -> None:
        self.read = False

    async def roots(self) -> list[Any]:
        self.read = True
        return [type("_Root", (), {"id": ROOT, "abs_path": LIBRARY})()]


async def test_an_admin_is_told_the_library_by_its_path_with_the_profile_blurred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(where, "profile_folder", lambda: PROFILE)
    access, library = _Access(EVERY_FOLDER), _Library()

    said_to = await where.whereabouts(
        ADMIN,
        access=cast(Any, access),
        library=cast(Any, library),
        settings=cast(Any, _Settings(True)),
        root_id=ROOT,
    )

    assert access.asked == [ROOT]
    assert said_to.of(ROOT, "2024/Beach/clip.mp4") == _path(
        str(PROFILE.parent / REDACTED / "Videos" / "Library"), "2024", "Beach", "clip.mp4"
    )


async def test_a_guest_is_told_the_library_by_its_name_and_no_path_is_read_for_them() -> None:
    """The absolute paths are not fetched at all for a viewer who may never be told one, so no
    later change to how the answer is assembled can leak one."""
    library = _Library()

    said_to = await where.whereabouts(
        GUEST,
        access=cast(Any, _Access(EVERY_FOLDER)),
        library=cast(Any, library),
        settings=cast(Any, _Settings(True)),
    )

    assert library.read is False
    assert said_to.of(ROOT, "2024/Beach/clip.mp4") == _path("Library", "2024", "Beach", "clip.mp4")
