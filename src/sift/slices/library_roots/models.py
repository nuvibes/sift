# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the library screens send and receive.

A payload carries ids, never paths: a root's absolute path is the layout of the server's disk, and
the folder tree is built from rows so the browser can draw a library without it. The exceptions
are about CHOOSING a folder: adding a root sends a path one way, and the admin-only picker
(`BrowseView`) sends paths back from inside folders deliberately handed to Sift, to whoever handed
them. Everything about an indexed folder carries ids.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.content import RootKind
from sift.kernel.wire import Wire


class RootView(Wire):
    """A library root, as a screen is allowed to see it.

    The path is the one field not for everybody: listing roots is admin-only, and an admin typed it.
    It tells apart two folders with similar names, which a generic phrase could not.
    """

    id: str
    #: The folder's name on disk, read off the path every time: the filesystem owns it, so a folder
    #: renamed on the host is never shown under a stale name.
    name: str
    #: The folder on disk. Admin-only, by virtue of the route this is served from.
    path: str
    #: Local or on another machine. Detected from the filesystem, never asked for.
    kind: RootKind
    vault: bool
    created_at: int
    #: The tree's four marks, read off this root's top folder; here too because a folder at any
    #: depth can be shared. Filled for an admin only, as a folder's are.
    shared: bool = False
    restricted: bool = False
    shared_here: bool = False
    restricted_here: bool = False
    #: Which computer this folder is on, by name, since "this computer" is untrue for anybody
    #: looking from another machine. Shown, never matched on: the path is the identity. Local roots
    #: only; a share is named by its address.
    machine: str | None = None
    #: Whether this folder is there right now, asked of every root: a local disk can vanish as
    #: completely as a share, and an unasked root would draw as ordinary while downloads fail.
    #: A share that does not answer in a moment counts as not there.
    reachable: bool | None = None
    #: What `reachable` is short for, where the two kinds of "not there" can be told apart: the
    #: folder answered (here), the drive or share it lives on did not (silent), or the drive
    #: answered and the folder is not on it (missing). Null where nobody asked.
    presence: Literal["here", "silent", "missing"] | None = None


class RootsView(Wire):
    roots: list[RootView]


class RejectionView(Wire):
    """One file Sift walked past, and why.

    The name is the point: somebody needs the file to judge whether Sift was right. Relative to the
    root, as the rest of this module is. `reason` is the decision and `detected` what the file
    turned out to be: one to group by, one to read.
    """

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
    #: The first page of them, by path: a root can refuse a hundred thousand files.
    rejections: list[RejectionView]
    #: How many there are in all, so a page that is shorter than the whole says so.
    rejections_total: int = 0


class QuarantineView(Wire):
    """Both piles, together, because a screen showing one of them is misleading.

    One screen for both, since they answer "where is my file" oppositely: `moved` is in Sift's own
    folder for it to clear; `left_alone` is still where somebody put it, and Sift will not touch it.
    """

    moved: list[QuarantinedView]
    left_alone: list[RootRejectionsView]
    keep_days: int = Field(
        description="How long a moved file is kept before it is removed. Zero means for ever."
    )


class NewRoot(Wire):
    """Where somebody's files are, and what to call the place.

    `abs_path` is a string so it can be refused with a sentence, not a schema error; every check
    runs in the kernel against the resolved directory, wherever the path came from.
    """

    abs_path: str = Field(min_length=1)
    vault: bool = False
    #: Whether to read the folder NOW: true by default, since watching reports only later changes.
    #: The first-run flow says false, so a scan does not compete with setting up; it is offered as a
    #: press once the library opens.
    scan: bool = True


class RootChange(Wire):
    """What may be changed about a root after it exists. Neither its name nor its path is here.

    A root is named by its directory, so renaming the directory is the only rename. Moving a library
    is its own request (`RootMoved`), with every check adding one needs. None leaves a field alone.
    Every root is watched, and handing a folder over is the permission, so only the vault is left.
    """

    vault: bool | None = None


class BrowseEntry(Wire):
    """A folder somebody could pick, and where it is.

    Carries a path, since the picker exists so nobody types one; admin-only, inside folders
    deliberately handed to Sift.
    """

    name: str
    path: str


class GrantView(Wire):
    """A folder somebody handed to Sift, as the screen that lists them needs it.

    The whole path is sent: two folders whose names end alike need it. Admin-only, like every route
    naming a directory on the server.
    """

    id: str
    path: str
    granted_at: int


class GrantsView(Wire):
    """Every folder Sift has been given."""

    grants: list[GrantView]


class NewGrant(Wire):
    """A folder chosen in the operating system's own dialog.

    A string, refused with a sentence, as `NewRoot.abs_path` is. Arriving from the client is not a
    hole: an admin session can call anything, and what matters is that a folder must be NAMED,
    which a page cannot do by opening the dialog. The picker can then only list inside folders
    somebody chose deliberately.
    """

    path: str = Field(min_length=1)


class BrowseView(Wire):
    """One directory's subfolders, the way back out, and what Sift could do here."""

    path: str
    entries: list[BrowseEntry]
    breadcrumb: list[BrowseEntry]
    #: Files in this folder, a count never names, so a folder of files and no subfolders does not
    #: read as empty.
    file_count: int = 0
    #: No folder has been handed to Sift at all: answered with how to hand one over.
    nothing_granted: bool
    writable: bool
    read_only_mount: bool


class FolderView(Wire):
    """A folder in the tree.

    No `has_children`: it would have to mean children this viewer may see, a second copy of the
    visibility rule that could disagree and point a disclosure triangle at something concealed. The
    client asks for children on opening, and none means a leaf.
    """

    id: str
    root_id: str
    parent_id: str | None
    name: str
    #: Where the folder sits inside its root, as the machine spells it: names repeat (two "2024"
    #: folders), so this is shown beside a folder and sent to name one.
    rel_path: str = ""
    #: Whether anybody can reach this folder or is kept from it (decided here or above), and whether
    #: decided here. Filled for an admin only, as an asset's are.
    shared: bool = False
    restricted: bool = False
    shared_here: bool = False
    restricted_here: bool = False
    #: In the vault and listed anyway (the vault is open), so the row offers to take it back out.
    hidden: bool = False
    #: "Don't enrich" and "Don't swap" on the folder's own row, each reaching every file under it.
    #: Filled for an admin only, as the sharing marks are.
    keep_local: bool = False
    keep_from_swaps: bool = False


class FoldersView(Wire):
    folders: list[FolderView]


class FolderDetail(Wire):
    """One folder, and what moving it would do.

    `file_count` is None, not zero, for a non-admin: it counts what the folder physically holds,
    not what the asker may open, so it goes only to the role that moves folders.
    """

    id: str
    root_id: str
    parent_id: str | None
    name: str
    file_count: int | None


class FolderFacts(Wire):
    """One folder, as much of it as an ORDER needs. See `FoldersFacts`."""

    id: str
    #: Every file under it, subfolders included (see `SORT_OPTIONS`: Biggest first counts files).
    file_count: int
    size_bytes: int
    #: When the newest file under it arrived, or absent for an empty folder (folder rows carry no
    #: creation date), which sorts last both ways.
    newest_at: int | None


class FoldersFacts(Wire):
    """The facts an order needs, for every folder directly inside one.

    A second request, not columns on the tree: the tree is sent flat in one answer without counts,
    because a right count needs the whole concealment rule (including a copy vaulted elsewhere),
    which only this query carries. Admin-only, as `FolderProperties` is.
    """

    folders: list[FolderFacts]


class FolderProperties(Wire):
    """What a folder IS: the panel a file manager opens on right-click.

    Admin-only, whole: every field is a fact about the server's disk or what a folder physically
    holds. `created_at` is absent, never zero (1970), where the folder could not be read.
    """

    #: Where it is on the server's disk, in full, as a person would paste it into a file manager.
    location: str
    #: Over every copy under it, folders inside it included. See `FolderContents`.
    size_bytes: int
    file_count: int
    folder_count: int
    #: Seconds since the epoch, or None where the disk did not say.
    created_at: float | None


# Moving a folder is answered elsewhere, so the shapes it takes and returns live with it.


class NewFolder(Wire):
    """Somewhere to put things, inside a library folder that was handed over read-write."""

    #: The folder it goes in: always inside another, never at a path, since the parent is what the
    #: permission was resolved against.
    parent_id: str
    name: str


class FolderChange(Wire):
    """Renaming a folder, moving it, or both. Both are one act on a disk, so both are one request.

    Every field is optional and None means "leave it alone", which is what makes this a PATCH.
    """

    name: str | None = None
    #: Where it should sit, inside the same library folder: between libraries its files' permissions
    #: would change, which is another operation.
    parent_id: str | None = None


class RootMoved(Wire):
    """A library folder that is not where Sift last saw it, and where it is now.

    Its own request, not a `RootChange` field, so it passes every check adding a library passes.
    """

    abs_path: str
