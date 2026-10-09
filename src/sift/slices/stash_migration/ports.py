# SPDX-License-Identifier: AGPL-3.0-or-later
"""The other features' writers a Stash run is handed at the composition root; None counts as not
done."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from sift.kernel.access import Viewer
from sift.kernel.ledger import Actor

OpinionOn = Literal["person", "site", "tag"]

LinkOn = Literal["person", "site", "tag"]


class Linked(StrEnum):
    """How one stash-box id went."""

    LINKED = "linked"
    ALREADY = "already"
    NO_BOX = "no_box"
    #: The box files studios as People.
    NOT_A_SITE = "not_a_site"
    KEPT_LOCAL = "kept_local"
    #: No key that opens, no answer, or an unknown id.
    NOT_ASKED = "not_asked"


#: Called inside the new library's creation, so a failure removes it again.
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
        """Mark a stretch of a video with its tags; False when already marked or past the end."""
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
        """Heart and rate for this user, filling only what they left unsaid; True when written."""
        ...

    async def keep_search(self, user_id: str, name: str, query: str) -> bool:
        """Keep a named search; False when the name is taken or they hold the most Sift keeps."""
        ...

    async def link(
        self,
        kind: LinkOn,
        local_id: str,
        endpoint: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> Linked:
        """Link a row to what a stash-box calls it, as a person's link is made."""
        ...

    async def link_file(
        self, asset_id: str, endpoint: str, remote_id: str, master_key: bytes | None
    ) -> Linked:
        """Link a file to a box's scene and apply it, unless that answer was already decided."""
        ...

    async def new_library(self, name: str, actor: Viewer, seed: Seed) -> str:
        """Create a library beside this one, seed it and switch to it; answers its id."""
        ...

    async def add_to_photo_set(
        self, photo_set_id: str, asset_ids: Sequence[str], *, actor: Actor
    ) -> int | None:
        """Add pictures to a Photo Set; how many went in, or None when the set is gone."""
        ...

    async def make_collection(self, name: str, owner_id: str, *, actor: Actor) -> str | None:
        """Create an empty Collection for this user; its id, or None when the user is gone."""
        ...

    async def add_to_collection(
        self, collection_id: str, asset_ids: Sequence[str], *, actor: Actor
    ) -> int | None:
        """Append files to a Collection; how many went in, or None when it is gone."""
        ...

    async def picture(self, kind: OpinionOn, entity_id: str, blob: bytes, *, actor: Actor) -> bool:
        """Give a person, Site or tag a re-encoded cover where it has none; True when one landed."""
        ...
