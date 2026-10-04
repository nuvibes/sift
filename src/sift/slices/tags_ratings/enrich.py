# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a tag is read and written when a stash-box answers about one.

The smallest of the writers, and it is worth saying why it exists at all rather than the tags being
written from wherever they are needed. A tag's record (what it means, its other words, its
category) is written by ONE statement that replaces all three, because the form that edits it
sends all three. An import sends whatever the stash-box happened to carry, so it reads the record
first and writes the merge.

Adding an alias is the same shape: the form's save clears the list and rewrites it, which is right
for a form and wrong for an import, so this adds to what is there.
"""

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
        """What this tag says now. Its NAME is not among the fields a stash-box may write. See
        the record registry, where that is declared once and read from here."""
        return dict(await self._service.record_of(local_id))

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """Nothing. A tag's record names no other rows: its words are words, not references."""
        _ = values
        return ()

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write what a plan decided, on top of what the tag already says. Answers with what landed,
        and with how many rows each field gained. See `Writer.write`.

        `creating` is unused and is part of the shape rather than an oversight: it is permission to
        invent a row from a name, and every field here is a word rather than a reference. Naming it
        in the signature is what keeps the seam one shape for every subject.

        A tag's NAME is not among the fields a stash-box may write, so it is not among the ones this
        can answer with either. The registry declares that once and the reason is written there: a
        tag is matched BY its name, so an import that renamed one would rename the very thing it
        matched on.
        """
        _ = creating
        held = await self._service.record_of(local_id)
        kept = held.get("aliases")
        aliases = [str(one) for one in kept] if isinstance(kept, list) else []
        offered = values.get("aliases")
        written: dict[str, int] = {}
        if isinstance(offered, (list, tuple)):
            # The plan already merged what is there with what arrived, so what comes in IS the whole
            # list. Taken as it stands rather than added to what was read a line ago, which would
            # union it with itself.
            aliases = [str(one).strip() for one in offered if str(one).strip()]
            # How many the tag ends up WITH, which is the honest number for a field replaced whole:
            # this writer's save is the whole list every time, so "how many were new" is a question
            # about a diff nobody here takes. `aliases` is empty only where the offer was, and an
            # empty offer still says the field was written: the plan asked for it to be cleared.
            written["aliases"] = len(aliases)
        description = stripped_or_none(values.get("description"))
        category = stripped_or_none(values.get("category"))
        # `merge_enriched_record` and not `set_record`: the ask's own `enriched` event (written
        # beside the run) is the record of this, and `set_record`'s `edited` event would draw the
        # press as a second line. `actor` is therefore unused here, as it is by the person writer.
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
