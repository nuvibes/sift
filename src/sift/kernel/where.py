# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a file sits, said to one viewer. Every screen that names a file's place reads it here.

An admin is told the full path on this machine, the path they can paste into a file manager,
with their profile folder's name replaced by `REDACTED` when they ask for that. Anyone else is
never told an absolute path: they get the library folder's name where they may see that folder,
and "..." for every run of folders they may not. A Hidden folder is "..." while Hidden is shut,
for an admin too, because it is not a folder anybody may see then.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from sift.kernel.content import ROOT_REL_PATH

if TYPE_CHECKING:
    # Types only: History reads this module while the access package is still being imported.
    from sift.kernel.access import Folder, Repository, Viewer
    from sift.kernel.content import LibraryStore
    from sift.kernel.seams import SettingsSeam

#: A folder, or a run of folders, the viewer may not see.
UNSEEN = "..."
#: What the profile folder's name becomes when the viewer asks for it to be hidden. Always a whole
#: segment of the path, which is how the page finds it and draws it covered, a filled box over a
#: stand-in word (`PathText.svelte` holds the same string). Replaced here rather than covered on
#: the page, so the name itself never leaves the server; the word reads plainly wherever a path is
#: copied or put into a sentence.
REDACTED = "[redacted]"
#: The per-user setting that asks for it.
HIDE_ACCOUNT_NAME_KEY = "paths.hide_account_name"


def profile_folder() -> PurePath | None:
    """The folder of the account Sift runs as (the one whose name a path gives away) or None
    where the machine cannot say."""
    try:
        return Path.home()
    except RuntimeError:
        return None


def blurred(path: str, profile: PurePath | None) -> str:
    """`path` with the profile folder's own name replaced by `REDACTED`, where it is inside it."""
    if profile is None or not profile.name:
        return path
    try:
        inside = PurePath(path).relative_to(profile)
    except ValueError:
        return path
    return str(profile.parent / REDACTED / inside)


def said(
    rel_path: str,
    *,
    seen: Iterable[str],
    top: str | None,
    profile: PurePath | None = None,
    sep: str = os.sep,
) -> str:
    """One file's place: `top`, then each folder of `rel_path` the viewer may see, then its name.

    `seen` is the relative paths of the folders in this library the viewer may see now,
    `ROOT_REL_PATH` standing for the library folder itself. `top` is what the library folder is
    said as: the absolute path for an admin, its name for anyone else.
    """
    visible = set(seen)
    *folders, name = rel_path.split("/")
    parts = [
        blurred(top, profile).rstrip("\\/")
        if top is not None and ROOT_REL_PATH in visible
        else UNSEEN
    ]
    for part in _folders_said(folders, visible):
        if part == UNSEEN and parts[-1] == UNSEEN:
            continue
        parts.append(part)
    parts.append(name)
    return sep.join(parts)


def folder_said(rel_folder: str, *, seen: Iterable[str]) -> str:
    """A folder inside its library, by its relative path, as the viewer may be told it: each folder
    of it they may not see is `UNSEEN`, and a run of them is one."""
    return "/".join(_folders_said(rel_folder.split("/"), set(seen)))


def _folders_said(folders: Sequence[str], visible: Collection[str]) -> list[str]:
    """Each folder of a path, top first: its name where it is in `visible`, else `UNSEEN`, with a
    run of unseen folders said once."""
    parts: list[str] = []
    for depth, folder in enumerate(folders):
        part = folder if "/".join(folders[: depth + 1]) in visible else UNSEEN
        if part == UNSEEN and parts and parts[-1] == UNSEEN:
            continue
        parts.append(part)
    return parts


@dataclass(frozen=True, slots=True)
class Whereabouts:
    """What one viewer may be told about where files sit, read once for a page."""

    #: The library folder of each library, by root id, as it is said: its absolute path for an
    #: admin, its name for anyone else. Absent where the viewer may not see it.
    tops: Mapping[str, str] = field(default_factory=dict)
    #: The folders the viewer may see, by root id, as relative paths.
    seen: Mapping[str, frozenset[str]] = field(default_factory=dict)
    profile: PurePath | None = None

    def of(self, root_id: str, rel_path: str) -> str:
        return said(
            rel_path,
            seen=self.seen.get(root_id, frozenset()),
            top=self.tops.get(root_id),
            profile=self.profile,
        )


def whereabouts_from(
    viewer: Viewer,
    folders: Iterable[Folder],
    *,
    root_paths: Mapping[str, str],
    profile: PurePath | None,
) -> Whereabouts:
    """The answer for these folders, which are the ones `viewer` may see. A concealed folder is not
    seen while Hidden is shut; an absolute path is used only for an admin.

    `concealed` is where a folder sits, not whether this viewer is being kept from it: the access
    layer hands a concealed folder back both to a viewer with Hidden open and to one whose mode
    keeps a locked placeholder, and only the second is kept from its name.
    """
    seen: dict[str, set[str]] = {}
    tops: dict[str, str] = {}
    for folder in folders:
        if folder.concealed and not viewer.show_hidden:
            continue
        seen.setdefault(folder.root_id, set()).add(folder.rel_path)
        if folder.rel_path == ROOT_REL_PATH:
            tops[folder.root_id] = folder.name
    if viewer.is_admin:
        tops = {root_id: root_paths.get(root_id, name) for root_id, name in tops.items()}
    return Whereabouts(
        tops=tops,
        seen={root_id: frozenset(paths) for root_id, paths in seen.items()},
        profile=profile if viewer.is_admin else None,
    )


async def profile_to_blur(viewer: Viewer, settings: SettingsSeam) -> PurePath | None:
    """The profile folder whose name `blurred` takes out of this viewer's paths, or None. Only an
    admin is ever told an absolute path, so only an admin's setting is read."""
    if viewer.is_admin and await settings.get_user(viewer.id, HIDE_ACCOUNT_NAME_KEY):
        return profile_folder()
    return None


async def whereabouts(
    viewer: Viewer,
    *,
    access: Repository,
    library: LibraryStore,
    settings: SettingsSeam,
    root_id: str | None = None,
) -> Whereabouts:
    """What `viewer` may be told about where the files in one library sit, or in every library."""
    folders = await access.visible_folders(viewer, root_id=root_id)
    root_paths: dict[str, str] = {}
    if viewer.is_admin:
        root_paths = {root.id: root.abs_path for root in await library.roots()}
    profile = await profile_to_blur(viewer, settings)
    return whereabouts_from(viewer, folders, root_paths=root_paths, profile=profile)
