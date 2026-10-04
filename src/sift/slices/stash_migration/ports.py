# SPDX-License-Identifier: AGPL-3.0-or-later
"""The doors a Stash run writes through that belong to other features.

A Loop belongs to Loops, a heart on a person to People, a saved search to Search, a stash-box link
to the stash-box feature, a Photo Set's pictures to Photo Sets, a Collection to Collections, a cover
to the cover door and a new library to Libraries. A slice never imports another slice, so
the run is handed these at the composition root, which is the one place that knows both sides.
Each door is the owning feature's own writer, so a mark made here gets its still, a heart gets its
History row, and a link is fetched and kept exactly as one a person makes.

`None` for the whole set means nothing was handed in (the service's own narrow tests); each part
of the run that needs a door then counts itself as not done, and the report says so.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from sift.kernel.access import Viewer
from sift.kernel.ledger import Actor

#: What an opinion is about: the three kinds of named thing Stash hearts and rates.
OpinionOn = Literal["person", "site", "tag"]

#: What a link is about, in Stash's words: a performer, a studio or a tag.
LinkOn = Literal["person", "site", "tag"]


class Linked(StrEnum):
    """How one stash-box id went."""

    LINKED = "linked"
    #: This library already links that row to that box; nothing was asked.
    ALREADY = "already"
    #: No stash-box here has that address.
    NO_BOX = "no_box"
    #: The box files studios as People, so a Site cannot carry its id.
    NOT_A_SITE = "not_a_site"
    #: Kept local, so nothing about it may leave this device.
    KEPT_LOCAL = "kept_local"
    #: The box could not be asked (no key that opens, no answer), or does not know the id.
    NOT_ASKED = "not_asked"


#: Seeds a library that has just been made, before it is opened: its database file and its data
#: folder. Called once, inside the libraries feature's own making, so a failure takes the new
#: library away again.
Seed = Callable[[Path, Path], Awaitable[None]]


class StashDoors(Protocol):
    """The other features' writers, as a Stash run needs them."""

    async def mark(
        self,
        asset_id: str,
        start_ms: int,
        end_ms: int,
        *,
        name: str | None,
        tag_ids: Sequence[str],
        created_by: str,
    ) -> bool:
        """Mark a stretch of a video, with its tags. False when that stretch is already marked, or
        cannot be (it runs past the file's end)."""
        ...

    async def opinion(
        self,
        kind: OpinionOn,
        entity_id: str,
        user_id: str,
        *,
        favorite: bool,
        rating: int | None,
    ) -> bool:
        """Heart and rate a person, a Site or a tag for this user, filling only what they have not
        said themselves. True when anything was written."""
        ...

    async def keep_search(self, user_id: str, name: str, query: str) -> bool:
        """Keep a search over files under a name. False when that name is already one of theirs,
        which is left as it is, or when they hold as many as Sift keeps."""
        ...

    async def link(
        self,
        kind: LinkOn,
        local_id: str,
        endpoint: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> Linked:
        """Link a row to what a stash-box calls it, fetched and kept as a link a person makes."""
        ...

    async def link_file(
        self, asset_id: str, endpoint: str, remote_id: str, master_key: bytes | None
    ) -> Linked:
        """Link a file to the scene a stash-box files under that id, fetched and applied as an
        exact match is, unless its answer from that box was agreed to or refused here."""
        ...

    async def new_library(self, name: str, actor: Viewer, seed: Seed) -> str:
        """Make a library beside this one, seed it, and switch to it. Answers its id."""
        ...

    async def add_to_photo_set(
        self, photo_set_id: str, asset_ids: Sequence[str], *, actor: Actor
    ) -> int | None:
        """Put pictures into a Photo Set a run made earlier, those already in it left alone.
        Answers how many went in, or None when that set is gone (somebody deleted it)."""
        ...

    async def make_collection(self, name: str, owner_id: str, *, actor: Actor) -> str | None:
        """Make an empty Collection owned by this user, through the Collections feature's own
        writer. Answers its id, or None when that user is gone."""
        ...

    async def add_to_collection(
        self, collection_id: str, asset_ids: Sequence[str], *, actor: Actor
    ) -> int | None:
        """Put files at the end of a Collection a run made earlier, those already in it left
        alone. Answers how many went in, or None when that Collection is gone."""
        ...

    async def picture(self, kind: OpinionOn, entity_id: str, blob: bytes, *, actor: Actor) -> bool:
        """Make these bytes a person's, a Site's or a tag's cover where it has none, through the
        cover door, which re-encodes them (never kept as they came). True when a picture landed;
        False where one was there already or the bytes are not a picture."""
        ...
