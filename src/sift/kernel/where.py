# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a file sits, said to one viewer: a full path for an admin, names or "..." for others."""

from __future__ import annotations

import os
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from sift.kernel.content import ROOT_REL_PATH

if TYPE_CHECKING:
    # Types only: History reads this module while the access package is being imported.
    from sift.kernel.access import Folder, Repository, Viewer
    from sift.kernel.content import LibraryStore
    from sift.kernel.seams import SettingsSeam

#: A folder, or a run of folders, the viewer may not see.
UNSEEN = "..."
#: The profile folder's name when hidden, replaced on the server so the name never leaves it;
#: a whole segment, which is how the page finds it and draws it covered (`PathText.svelte`).
REDACTED = "[redacted]"
HIDE_ACCOUNT_NAME_KEY = "paths.hide_account_name"


def profile_folder() -> PurePath | None:
    """The folder of the account Sift runs as, whose name a path gives away, or None."""
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
    """One file's place: `top`, each folder of `rel_path` the viewer may see, then its name."""
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
    """A folder's relative path as the viewer may be told it; unseen runs are one `UNSEEN`."""
    return "/".join(_folders_said(rel_folder.split("/"), set(seen)))


def _folders_said(folders: Sequence[str], visible: Collection[str]) -> list[str]:
    """Each folder of a path, top first, a run of unseen ones said once."""
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

    #: Each library's folder as it is said, by root id; absent where the viewer may not see it.
    tops: Mapping[str, str] = field(default_factory=dict)
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
    """The answer for folders `viewer` may see; concealed ones count only with Hidden open."""
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
    """The profile folder to take out of an admin's paths, or None; admins only."""
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
