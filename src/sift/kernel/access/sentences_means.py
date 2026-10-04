# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each mark means, for the tooltip beside it."""

from __future__ import annotations

from collections.abc import Mapping

# --- what each mark means, for the tooltip beside it ---------------------------------------------
#
# The server's, beside the sentences they describe, so a kind added here is a kind with words in
# the same edit, and no pause, cancel or finished task is told it was "Settled at the workbench,
# and it can be taken back".

#: What each kind's mark means, as a tooltip: what KIND of act the line is, not the line again.
MEANS: Mapping[str, str] = {
    "added": "Added to the library",
    "moved": "Moved to another folder",
    "renamed": "Renamed",
    "undone": "Undone",
    "decided": "Decided in Organize. You can undo it.",
    "named": "A person named in a file",
    "tagged": "A tag added to a file",
    "filed": "Filed under a username or a Site",
    "enriched": "A stash-box recognized this and filled in details",
    # A stash-box that matched nothing or holds a match still waiting, and AcoustID: each a service
    # somebody else runs, asked about this file.
    "asked": "A stash-box or AcoustID was asked about this file",
    "face_run": "Sift looked for faces",
    "pressed": "A task run on this file at a press",
    # Not "from the faces you confirmed": a person is recognized from reference pictures too.
    "matched": "Recognized by Sift",
    "confirmed": "A face confirmed as a person",
    "rejected": "A face marked as not a person",
    "copied_from": "Created from another file",
    "copied_into": "Another file was created from this one",
    "shared": "Shared with a guest, or kept private from one",
    "watermark": "Sift looked for a watermark on the picture",
    "downloaded": "Downloaded",
    "download_failed": "Download failed",
    "ready": "Thumbnails and previews generated",
    "left_out": "Sift could not do this for the file",
    "face_off": "A face was removed or discarded",
    "ruled_out": "A person marked as not in a file",
    "hidden": "Hidden",
    "kept_mine": "Your answer kept over a stash-box's",
    "taught": "Faces Sift recognizes this person by",
    "removed": "Removed",
    "revealed": "Unhidden",
    "edited": "Edited",
    "deleted": "Deleted",
    "merged": "Merged",
    "kept_local": "Stash-box lookups turned off",
    "allowed": "Stash-box lookups turned on",
    "kept_from_swaps": "Kept out of swaps",
    "allowed_in_swaps": "Let into swaps again",
    "scanned": "Fingerprints generated",
    "saved": "Saved to a device",
    "wall_sent": "A Theater wall sent from one device to another",
    "paused": "A download was paused",
    "resumed": "A paused download was started again",
    "cookies_saved": "Cookies were saved for this Site",
    "cookies_replaced": "This Site's cookies were replaced",
    "cookies_forgotten": "This Site's cookies were deleted",
    "canceled": "A download was canceled",
    "ran": "A task finished",
    # What KIND of act, as every row here says: the line itself names the source ("from AcoustID",
    # "from the same music as beach.mp4", "from Studio's page"), so the mark's words name all three rather
    # than saying the line again.
    "song_named": "A song Sift named from a Site's page, AcoustID or another file with the song",
    "swap_started": "A swap with another Sift started",
    "swap_ended": "A swap with another Sift ended",
    "restored": "This library was restored from a backup",
    "adopted": "This library was created from a database file",
    "sharing_turned_on": "The computer running Sift started sharing the library on its network",
    "sharing_turned_off": "The computer running Sift stopped sharing the library on its network",
    "start_with_windows_on": "Sift set to start with Windows on the computer running it",
    "start_with_windows_off": "Sift no longer starts with Windows on the computer running it",
    "firewall_opened": "The firewall port opened on the computer running Sift",
    "storage_moved": "Sift data moved to another folder on the computer running Sift",
    "update_started": "A newer Sift started installing on the computer running it",
    "library_opened": "Another library opened on the computer running Sift",
    "restarted": "Sift restarted on the computer running it",
}

#: What the VERB that made a copy means, where the row says which verb it was.
_COPY_MEANS: Mapping[str, str] = {
    "trim": "A copy with its ends trimmed",
    "clip": "A copy of one stretch of another file",
    "gif": "A GIF created from another file",
    "compress": "A smaller copy of another file",
    "crop": "A copy with its edges cropped",
    "rotate": "A copy turned the other way up",
}


def means(kind: str, how: str | None = None, actor: str | None = None) -> str:
    """What one line's mark means. Your own act is said first: the ring around it is the one thing
    colour is spent on, and a ring nobody has been told about is a ring nobody reads."""
    if actor == "you":
        return "You did this"
    return (_COPY_MEANS.get(how or "") if how else None) or MEANS.get(kind, "Something happened")
