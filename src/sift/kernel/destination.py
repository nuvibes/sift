# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a file taken into the library lands: a folder that was named, and the root it is in.

In the kernel because two features ask it and neither may import the other: the import pipeline
asks it before a file is copied in, and the downloader asks it before a link is fetched, so a
download with nowhere to land is refused before a byte is spent on it rather than after.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.content import LibraryStore, Root
from sift.kernel.ingress import NoDestination

#: Said when nothing chose a folder: no drop target, and no download folder in the settings.
NOTHING_CHOSEN = "Choose where this should go, or set a download folder in your download settings."

#: Said when the chosen folder, or the library it was in, has gone since it was chosen.
FOLDER_GONE = "That folder isn't there any more. Pick another one."


@dataclass(frozen=True, slots=True)
class Destination:
    """A folder to put a file in, and the root it belongs to."""

    root: Root
    folder_id: str
    #: The folder's path relative to its root. Empty for the root's own folder.
    rel_dir: str


async def resolve_destination(library: LibraryStore, dest_folder_id: str | None) -> Destination:
    """The folder a file should land in. A FOLDER, and one that was named.

    The drop target sets the destination: a link dropped on "Vacations" downloads into Vacations.
    There is no forced filing by site or username: the person organises by dropping and dragging.

    **This does not know what the default is, and that is deliberate.** Where downloads go when
    nobody says is one answer for the whole install, it is stored beside the naming rule that is
    chosen with it, and it belongs to the feature that owns that. Asked here it would be a second
    answer to the same question, free to disagree with the one the settings screen shows.

    Whoever calls this resolves the default first and hands over a folder. Handed nothing, this
    refuses rather than guessing: a file put somewhere nobody chose is worse than a file refused.
    """
    if dest_folder_id is None:
        raise NoDestination(NOTHING_CHOSEN)
    folder = await library.get_folder(dest_folder_id)
    if folder is None:
        raise NoDestination(FOLDER_GONE)
    root = await library.get_root(folder.root_id)
    if root is None:
        raise NoDestination(FOLDER_GONE)
    return Destination(root=root, folder_id=folder.id, rel_dir=folder.rel_path)


__all__ = ["FOLDER_GONE", "NOTHING_CHOSEN", "Destination", "resolve_destination"]
