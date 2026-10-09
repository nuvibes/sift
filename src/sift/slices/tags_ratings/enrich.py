# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a tag is read and written when a stash-box answers: merged, never replaced."""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.enrichment import Creating, Missing
from sift.kernel.ledger import Actor
from sift.kernel.records import Subject
from sift.kernel.text import stripped_or_none
from sift.slices.tags_ratings.service import TagService


class TagWriter:
    """Reads and writes one tag, for whoever is applying a stash-box's answer."""

    subject = Subject.TAG

    def __init__(self, service: TagService) -> None:
        self._service = service

    async def current(self, local_id: str) -> Mapping[str, object]:
        """What this tag says now; its name is never a field a stash-box may write."""
        return dict(await self._service.record_of(local_id))

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """Nothing. A tag's record names no other rows: its words are words, not references."""
        _ = values
        return ()

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write what a plan decided over what the tag already says; answers with what landed."""
        _ = creating
        held = await self._service.record_of(local_id)
        kept = held.get("aliases")
        aliases = [str(one) for one in kept] if isinstance(kept, list) else []
        offered = values.get("aliases")
        written: dict[str, int] = {}
        if isinstance(offered, (list, tuple)):
            # The plan already merged what was there, so the incoming list is the whole list.
            aliases = [str(one).strip() for one in offered if str(one).strip()]
            # How many the tag ends up with: the save is the whole list, so there is no diff.
            written["aliases"] = len(aliases)
        description = stripped_or_none(values.get("description"))
        category = stripped_or_none(values.get("category"))
        # Not `set_record`, whose `edited` event would draw the press as a second line.
        _ = actor
        await self._service.merge_enriched_record(
            local_id,
            description=description or stripped_or_none(held.get("description")),
            category=category or stripped_or_none(held.get("category")),
            aliases=aliases,
        )
        if description is not None:
            written["description"] = 1
        if category is not None:
            written["category"] = 1
        return written
