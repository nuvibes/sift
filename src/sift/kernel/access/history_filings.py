# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines of what is filed on one file (each person, tag and username, and who put it there),
and the pane every source of that file's thread reads beside its own table."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import (
    _actor_name_of_source,
    _via_of_source,
    _Who,
    actor_of_source,
)
from sift.kernel.access.history_folds import _receipt_for
from sift.kernel.access.history_ledger import Lent
from sift.kernel.access.history_line import Actor, Event, by_of
from sift.kernel.access.history_sources import (
    _FILED,
    _NAMED,
    _TAGGED,
    Pressers,
    _filed_site,
    naming_folders,
)
from sift.kernel.access.repository import Repository
from sift.kernel.access.sentences import Piece
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row


@dataclass(slots=True)
class _Pane:
    """What every source of one file's thread reads beside its own table, read once for the pane."""

    database: Database
    access: Repository
    viewer: Viewer
    asset_id: str
    present: set[str]
    here: set[str]
    moves: list[Row]
    folders_seen: Mapping[str, frozenset[str]]
    arrival_folder: tuple[str, str | None] | None
    produced: list[Row]
    made_into: list[Row]
    decided: list[Row]
    receipt_objects: dict[str, tuple[str, str, str | None]]
    pressers: Pressers
    who: _Who
    lent: dict[tuple[str, str], Lent] = field(default_factory=dict)
    by_receipt: dict[tuple[str, str], str] = field(default_factory=dict)
    #: Each folder the moves name, by (library, path), with its id (`folder_ids`), and the id of
    #: the folder the file arrived in: a line links a folder by its id.
    folder_ids: Mapping[tuple[str, str], str] = field(default_factory=dict)
    arrival_folder_id: str | None = None


#: Every stash-box by its id, for naming the box a filing names (`box_id`).
_BOX_NAMES = "SELECT id, name FROM stash_boxes"


async def _box_names(pane: _Pane) -> dict[str, str]:
    """Each stash-box's name by its id, or nothing on a library without the stash-box tables."""
    if "stash_boxes" not in pane.present:
        return {}
    return {str(row["id"]): str(row["name"]) for row in await pane.database.fetch_all(_BOX_NAMES)}


def _actor_of_row(row: Row, boxes: Mapping[str, str]) -> tuple[Actor, str | None]:
    """Who decided a filing row and what they are called: the stash-box BY NAME where the row
    names the box (and the box is still there), else as `actor_of_source` says it."""
    source = None if row["source"] is None else str(row["source"])
    actor = actor_of_source(source)[0]
    if actor is Actor.STASH_BOX and row["box_id"] is not None:
        return actor, boxes.get(str(row["box_id"]))
    return actor, _actor_name_of_source(source)


async def _named_events(pane: _Pane) -> list[Event]:
    """A line per person named on the file."""
    database, access, viewer, asset_id = pane.database, pane.access, pane.viewer, pane.asset_id
    present, folders_seen, lent, by_receipt = (
        pane.present,
        pane.folders_seen,
        pane.lent,
        pane.by_receipt,
    )
    events: list[Event] = []
    named_rows = await database.fetch_all(_NAMED, (asset_id,))
    boxes = await _box_names(pane)
    # WHICH FOLDER a folder-read naming came from, so the line names it. Asked only for those rows.
    from_folders = (
        await naming_folders(
            database,
            access,
            viewer,
            asset_id,
            [str(row["person_id"]) for row in named_rows if row["source"] == "folder"],
            folders_seen or None,
        )
        if "folder_people" in present and any(row["source"] == "folder" for row in named_rows)
        else {}
    )
    for row in named_rows:
        source = None if row["source"] is None else str(row["source"])
        actor, actor_name = _actor_of_row(row, boxes)
        person = say.thing("person", str(row["person_id"]), str(row["name"]))
        moment = None if row["decided_at"] is None else int(row["decided_at"])
        if source is None and (lender := lent.get(("person", str(row["person_id"])))):
            actor, actor_name, moment = lender.actor, lender.name, moment or lender.at
        events.append(
            Event(
                at=moment,
                actor=actor,
                actor_name=actor_name,
                kind="named",
                pieces=say.named_sentence(
                    by_of(actor, actor_name),
                    source,
                    (person,),
                    folder=from_folders.get(str(row["person_id"])),
                ),
                via=_via_of_source(source),
                receipt=_receipt_for(by_receipt, "named", str(row["person_id"])),
                source=source,
            )
        )
    return events


async def _tagged_events(pane: _Pane) -> list[Event]:
    """A line per tag on the file."""
    database, asset_id, lent, by_receipt = pane.database, pane.asset_id, pane.lent, pane.by_receipt
    events: list[Event] = []
    boxes = await _box_names(pane)
    for row in await database.fetch_all(_TAGGED, (asset_id,)):
        source = None if row["source"] is None else str(row["source"])
        actor, actor_name = _actor_of_row(row, boxes)
        tag = say.thing("tag", str(row["tag_id"]), str(row["name"]))
        moment = None if row["decided_at"] is None else int(row["decided_at"])
        if source is None and (lender := lent.get(("tag", str(row["tag_id"])))):
            actor, actor_name, moment = lender.actor, lender.name, moment or lender.at
        events.append(
            Event(
                at=moment,
                actor=actor,
                actor_name=actor_name,
                kind="tagged",
                pieces=say.tagged_sentence(by_of(actor, actor_name), source, (tag,)),
                via=_via_of_source(source),
                receipt=_receipt_for(by_receipt, "tagged", str(row["tag_id"])),
                source=source,
            )
        )
    return events


async def _filed_events(pane: _Pane) -> list[Event]:
    """A line per username the file is filed under."""
    database, asset_id, lent, by_receipt = pane.database, pane.asset_id, pane.lent, pane.by_receipt
    events: list[Event] = []
    boxes = await _box_names(pane)
    for row in await database.fetch_all(_FILED, (asset_id,)):
        source = None if row["source"] is None else str(row["source"])
        actor, actor_name = _actor_of_row(row, boxes)
        site: Piece | str | None = _filed_site(row) or (
            None if row["site"] is None else str(row["site"])
        )
        # The username is a thing with a way to it (its person, or the files under it,
        # `sentences.username_opens`), as it is in the feed.
        username_id = str(row["username_id"])
        handle = str(row["name"] or "")
        username: Piece | str = (
            say.thing(
                "username",
                username_id,
                handle,
                href=say.username_opens(
                    username_id, None if row["person_id"] is None else str(row["person_id"])
                ),
            )
            if handle
            else ""
        )
        moment = None if row["decided_at"] is None else int(row["decided_at"])
        lender = (
            lent.get(("username", username_id))
            or (lent.get(("site", str(row["site_id"]))) if row["site_id"] is not None else None)
            if source is None
            else None
        )
        if lender is not None:
            actor, actor_name, moment = lender.actor, lender.name, moment or lender.at
        events.append(
            Event(
                at=moment,
                actor=actor,
                actor_name=actor_name,
                kind="filed",
                pieces=say.filed_sentence(by_of(actor, actor_name), username, site, source),
                via=_via_of_source(source),
                receipt=_receipt_for(by_receipt, "filed", str(row["username_id"])),
                source=source,
            )
        )
    return events
