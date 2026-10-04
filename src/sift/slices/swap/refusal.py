# SPDX-License-Identifier: AGPL-3.0-or-later
"""Do not swap: the mark that keeps a file, a person, a Site, a tag or a folder out of every swap.

The mark is the kernel's refusal with its "swap" purpose (`catalog.Purpose`), the door "Do not
enrich" goes through: a column per purpose, a file under the mark of anything it is filed under,
and the decision written to the ledger with the column. The offer reads it
(`offer.NOT_KEPT_FROM_SWAPS`) and so does the guest's answer (`diff.assess`), so a marked thing is
neither offered nor admitted to on either side.

"Do not enrich" keeps a thing out of swaps as well, and the state below says so rather than
drawing a switch that is off over a thing that is refused.
"""

from __future__ import annotations

from typing import Literal

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import (
    refused_here,
    refused_over,
    refusers_of_file,
    set_refused_on,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.db import Database
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject as DecisionSubject

#: What can carry the mark: the kinds "Do not enrich" covers, a folder's reaching every file in it.
Kind = Literal["asset", "person", "site", "tag", "folder"]
KINDS: tuple[Kind, ...] = ("asset", "person", "site", "tag", "folder")
#: What a file can be kept out by: everything it is filed under, and every folder it sits in.
Above = Literal["folder", "person", "site", "tag"]

#: Why a thing is out of swaps when its own switch is not the reason, in the words a row shows.
BY_ENRICHMENT = "Kept out by Don't enrich, which keeps everything about it on this device"
BY_FILING = "Kept out by something it's filed under"
BY_A_FOLDER_ABOVE = "Kept out by a folder it's inside"


async def name_for(access: Repository, viewer: Viewer, kind: Kind, local_id: str) -> str | None:
    """What this thing is called, for this viewer, or None where they may not see it."""
    if kind == "asset":
        asset = await access.open_asset(viewer, local_id)
        if asset is None:
            return None
        # The file page's order: a title, the name on disk now, the name it arrived under. The
        # imported name alone would keep a file renamed on disk under a name nobody sees any more.
        on_disk = (await access.names_on_disk(viewer, [local_id])).get(local_id)
        return asset.title or on_disk or asset.original_filename or ""
    if kind == "person":
        person = await access.visible_person(viewer, local_id)
        return None if person is None else person.name
    if kind == "site":
        site = await access.visible_site(viewer, local_id)
        return None if site is None else site.name
    if kind == "folder":
        # The scoped read the folder pages use: a folder in a shut vault is not there.
        folder = await access.get_folder(viewer, local_id)
        return None if folder is None else folder.name
    tag = await access.visible_tag(viewer, local_id)
    return None if tag is None else tag.name


async def state_of(database: Database, kind: Kind, local_id: str) -> tuple[bool, bool, str]:
    """(the switch on the row itself, whether it is out of swaps at all, and why when not by that
    switch). The two halves `kept_local_here` / `kept_local` answer for enrichment."""
    here = await refused_here(database, "swap", kind, local_id)
    if here:
        return True, True, ""
    if await refused_here(database, "enrich", kind, local_id):
        return False, True, BY_ENRICHMENT
    if await refused_over(database, "swap", kind, local_id):
        return False, True, BY_A_FOLDER_ABOVE if kind == "folder" else BY_FILING
    return False, False, ""


#: The kinds `refusers_of_file` answers with, as the wire names them.
_ABOVE: dict[str, Above] = {"folder": "folder", "person": "person", "site": "site", "tag": "tag"}


async def refused_by(
    access: Repository, database: Database, viewer: Viewer, kind: Kind, local_id: str
) -> list[tuple[Above, str, str, bool]]:
    """What a FILE is filed under that keeps it out of swaps, as this viewer may be told it:
    (the kind, the id, the name, and whether the mark is Kept local rather than "Don't swap").

    A person, a tag or a Site this viewer cannot see (in Hidden with the vault shut) is left out
    rather than named: the screen then says "something it's filed under", which is still true.
    Empty for anything but a file: an entity answers for itself alone.
    """
    if kind != "asset":
        return []
    named: list[tuple[Above, str, str, bool]] = []
    for one in await refusers_of_file(database, local_id):
        above = _ABOVE.get(one.kind, "site")
        name = await name_for(access, viewer, above, one.id)
        if name:
            named.append((above, one.id, name, one.kept_local))
    return named


async def set_kept_from_swaps(
    database: Database, viewer: Viewer, kind: Kind, local_id: str, kept: bool, *, name: str | None
) -> bool:
    """Put the mark on or take it off, and record who did, in one transaction. False when there
    was no such row. Recorded only when the column moved."""
    # Read before the write opens, so the write holds only the change and its record. A press
    # that changes nothing still answers True, and records nothing.
    moved = await refused_here(database, "swap", kind, local_id) != kept
    async with database.write() as connection:
        if not await set_refused_on(connection, "swap", kind, local_id, kept):
            return False
        if moved:
            await record_event(
                connection,
                actor=Actor.user(viewer.id),
                verb="kept_from_swaps" if kept else "allowed_in_swaps",
                subject=DecisionSubject(kind=kind, id=local_id, name=name),
            )
    if moved:
        announce_now(EVERY_ADMIN, About.LIBRARY)
    return True
