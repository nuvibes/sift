# SPDX-License-Identifier: AGPL-3.0-or-later
"""A library folder's path on Settings > Folders is said by the rule every other path is
(`kernel.where`): with the profile folder's name taken out when the viewer's own setting says
to hide it."""

from __future__ import annotations

from pathlib import PurePath

from sift.kernel.content import Root, RootKind
from sift.kernel.where import REDACTED
from sift.slices.library_roots.router import _root_view

PROFILE = PurePath("C:/Users/someone")
ROOT = Root(
    id="01KZF0VNDR00TPATH000000000",
    name="Library",
    abs_path=str(PROFILE / "Videos" / "Library"),
    kind=RootKind.LOCAL,
    created_at=0,
)


def test_a_library_folder_is_said_without_the_profile_folder_when_asked() -> None:
    assert _root_view(ROOT).path == ROOT.abs_path

    said = _root_view(ROOT, profile=PROFILE).path
    assert "someone" not in said
    assert said == str(PROFILE.parent / REDACTED / "Videos" / "Library")
    assert _root_view(ROOT, profile=PROFILE).name == "Library"
