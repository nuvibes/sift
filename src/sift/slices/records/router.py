# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a person, a site, a tag or a file is made of, described once and served to every screen.

## Why this is a route and not a constant in the client

Three surfaces render the same field and none of them may hold its own idea of what it is: the
record on an entity page, the panel that says what a file is, and, when there is more than one
answer to a field, the screen that settles which one to keep. A field described in the browser is
a description that has to be edited twice, and the copy nobody edits is the one that ships.

It is also what makes an older client meeting a newer server behave sensibly. A field this version
has never heard of arrives fully described, so it draws; a field it knows and the server no longer
sends simply is not there. Neither case needs the two halves to have been released together.

## Why it is a slice of its own

The record spans every kind of thing that has one, so no single feature owns it: putting the
registry inside the one that owns people would make sites and files guests in a slice about people.
It holds no table, no service and no write path: it reads a kernel registry and hands it over.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from sift.kernel.access import Viewer
from sift.kernel.records import every_field
from sift.slices.auth import current_viewer
from sift.slices.records.models import FieldDescription, FieldRegistry

router = APIRouter(tags=["records"])


@router.get("/records/fields")
async def read_fields(
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> FieldRegistry:
    """Every field this version describes, grouped by what it belongs to, in reading order.

    The order is the registry's own and is part of the design: a record whose rows come back
    alphabetical reads as a dump of a table rather than as something somebody laid out.

    The same answer for everybody. This says what a field IS (its name in plain language and its
    type) and never what any particular thing's value is, so there is nothing here to scope. It
    still needs a session, because the shape of a record is a description of the library's own
    vocabulary and an anonymous caller has no business reading it.
    """
    grouped: dict[str, list[FieldDescription]] = {}
    for one in every_field():
        grouped.setdefault(one.subject.value, []).append(
            FieldDescription(
                key=one.key,
                subject=one.subject.value,
                label=one.label,
                kind=one.kind.value,
                shown=one.shown.value,
                group=one.group.value,
                editable=one.editable,
                imported=one.imported,
                help=one.help,
                suggests=one.suggests,
                links_to=one.links_to,
                entry=one.entry,
                ordered=one.ordered,
            )
        )
    return FieldRegistry(subjects=grouped)
