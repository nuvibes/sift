# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the library screens send and receive: ids, never paths, except where a folder is chosen."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.content import RootKind
from sift.kernel.wire import Wire


class RootView(Wire):
    """A library root, as a screen is allowed to see it; the path only reaches an admin."""

    id: str
    #: Read off the path every time, so a folder renamed on the host is never stale.
    name: str
    path: str
    kind: RootKind
    vault: bool
    created_at: int
    #: The tree's four marks, read off this root's top folder. Filled for an admin only.
    shared: bool = False
    restricted: bool = False
    shared_here: bool = False
    restricted_here: bool = False
    machine: str | None = None
    reachable: bool | None = None
    #: The folder answered (here), its drive or share did not (silent), or the folder is gone
    #: (missing).
    presence: Literal["here", "silent", "missing"] | None = None


class RootsView(Wire):
    roots: list[RootView]


class RejectionView(Wire):
    """One file Sift walked past, and why: `reason` to group by, `detected` to read."""

    rel_path: str
    reason: str
    detected: str | None
    size_bytes: int
    first_seen_at: int
    last_seen_at: int


class QuarantinedView(Wire):
    """One file Sift moved out of the way, and why."""

    id: str = Field(
        description="What to name this file in a request to delete it. A bare filename, never a path."
    )
    original_name: str
    reason: str
    detected: str | None = None
    origin: str
    size_bytes: int | None = None
    quarantined_at: int
    explained: bool = Field(
        default=True,
        description=(
            "False where no reason was recorded beside the file. It is still listed, because a "
            "file sitting in quarantine unexplained is exactly what somebody needs to be told."
        ),
    )


class RootRejectionsView(Wire):
    """What one library folder is refusing, with the folder named so the list can say where."""

    root_id: str
    root_name: str
    rejections: list[RejectionView]
    rejections_total: int = 0


class QuarantineView(Wire):
    """Both piles together: `moved` is in Sift's folder; `left_alone` is still where somebody put
    it."""

    moved: list[QuarantinedView]
    left_alone: list[RootRejectionsView]
    keep_days: int = Field(
        description="How long a moved file is kept before it is removed. Zero means for ever."
    )


class NewRoot(Wire):
    """Where somebody's files are, as a string so a bad one is refused with a sentence."""

    abs_path: str = Field(min_length=1)
    vault: bool = False
    #: False in the first-run flow, so a scan does not compete with setting up.
    scan: bool = True


class RootChange(Wire):
    """What may be changed about a root after it exists: only the vault. None leaves it alone."""

    vault: bool | None = None


class BrowseEntry(Wire):
    """A folder somebody could pick, and where it is."""

    name: str
    path: str


class GrantView(Wire):
    """A folder somebody handed to Sift, with its whole path so similar names are told apart."""

    id: str
    path: str
    granted_at: int


class GrantsView(Wire):
    """Every folder Sift has been given."""

    grants: list[GrantView]


class NewGrant(Wire):
    """A folder chosen in the operating system's own dialog; a string, refused with a sentence."""

    path: str = Field(min_length=1)


class BrowseView(Wire):
    """One directory's subfolders, the way back out, and what Sift could do here."""

    path: str
    entries: list[BrowseEntry]
    breadcrumb: list[BrowseEntry]
    #: A count, never names, so a folder with only files does not read as empty.
    file_count: int = 0
    nothing_granted: bool
    writable: bool
    read_only_mount: bool


class FolderView(Wire):
    """A folder in the tree. No `has_children`: that would be a second copy of the visibility rule."""

    id: str
    root_id: str
    parent_id: str | None
    name: str
    #: Names repeat, so this is shown beside a folder and sent to name one.
    rel_path: str = ""
    #: Whether anybody can reach this folder, and whether decided here. Filled for an admin only.
    shared: bool = False
    restricted: bool = False
    shared_here: bool = False
    restricted_here: bool = False
    #: In the vault and listed because the vault is open.
    hidden: bool = False
    #: Reaching every file under it. Filled for an admin only.
    keep_local: bool = False
    keep_from_swaps: bool = False
    #: Asked of the disk only with `writable=true`.
    writable: bool | None = None


class FoldersView(Wire):
    folders: list[FolderView]


class FolderDetail(Wire):
    """One folder, and what moving it would do; `file_count` is None for a non-admin."""

    id: str
    root_id: str
    parent_id: str | None
    name: str
    file_count: int | None


class FolderFacts(Wire):
    """One folder, as much of it as an ORDER needs."""

    id: str
    file_count: int
    size_bytes: int
    #: Absent for an empty folder, which sorts last both ways.
    newest_at: int | None


class FoldersFacts(Wire):
    """The facts an order needs, for every folder directly inside one. Admin-only."""

    folders: list[FolderFacts]


class FolderProperties(Wire):
    """What a folder IS, for the properties panel. Admin-only; `created_at` absent where unreadable."""

    location: str
    #: Over every copy under it, subfolders included.
    size_bytes: int
    file_count: int
    folder_count: int
    created_at: float | None


class NewFolder(Wire):
    """Somewhere to put things, inside a library folder that was handed over read-write."""

    #: Always inside another folder, which the permission was resolved against.
    parent_id: str
    name: str


class FolderChange(Wire):
    """Renaming a folder, moving it, or both. None leaves a field alone."""

    name: str | None = None
    #: Inside the same library folder: between libraries permissions would change.
    parent_id: str | None = None


class RootMoved(Wire):
    """A library folder that is not where Sift last saw it, and where it is now."""

    abs_path: str
