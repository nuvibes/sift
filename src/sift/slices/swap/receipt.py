# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receipt each side keeps when a swap ends, and the Undo that takes the landing back.

## The receipt

Written once, as the session ends (`keep`), from what the session held in memory: per group what
landed or was sent and how it was filed here (a person created or added to, the Site, the
usernames, the faces held), what was offered and not taken, how many were refused and how many
were already here, and how often a tunnel cut it off. The session's own row keeps the counts, the
times, the rate and the reason; the receipt is the two read together (`view`). A session a restart
cut short ended before anything was kept, and its receipt is the row alone.

## The Undo

Takes back every file the swap landed: the files it put in its own folder (`Swap-<short id>`) go
from the disk and from the library through the one remover (`Remover`, the delete feature's,
reached by its part's name). Which files those are is read from the ledger, never from the folder
alone: the swap's `added` lines (each naming the session's short id) name every file it landed,
so a file somebody else put in that folder stays. A file the swap found already here was never
copied, and stays. Then the People, Sites and Usernames this swap made and nothing else holds are
removed in one transaction, and the folder's emptied directories after them. The receipt then says
undone, with the counts.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog.take_back import POINTING_AT, SHELL_TABLES, remove_shell_on
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.content import FolderRow, LibraryStore
from sift.kernel.db import Connection, Database
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import VIA_SWAP, Subject
from sift.kernel.wiring import Part
from sift.slices.swap.manifest import (
    Grouping,
    Progress,
    kind_of,
    manifest_of,
    name_of,
    person_key,
)
from sift.slices.swap.models import (
    Offer,
    ReceiptFolder,
    ReceiptGroup,
    ReceiptSkipped,
    ReceiptUndo,
    SwapReceipt,
)
from sift.slices.swap.store import SessionRow

if TYPE_CHECKING:
    from sift.slices.swap.live import _Live
    from sift.slices.swap.receiving import _Receiving
    from sift.slices.swap.sending import _Sending

log = get_logger(__name__)

# ================================================================================================
# THE RECEIPT
# ================================================================================================

_READ = "SELECT body, undone_at, undo FROM swap_receipts WHERE session_id = ?"
_KEEP = (
    "INSERT INTO swap_receipts (session_id, body, written_at) VALUES (?, ?, ?)"
    " ON CONFLICT(session_id) DO UPDATE SET body = excluded.body, written_at = excluded.written_at"
)
# Once: a second Undo of the same swap finds it undone and writes nothing.
_UNDONE = (
    "INSERT INTO swap_receipts (session_id, body, written_at, undone_at, undo)"
    " VALUES (?, '{}', ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET"
    " undone_at = excluded.undone_at, undo = excluded.undo WHERE swap_receipts.undone_at IS NULL"
)


@dataclass(frozen=True, slots=True)
class Kept:
    """A receipt as its row holds it."""

    body: Mapping[str, Any]
    undo: ReceiptUndo | None = None


async def read(database: Database, session_id: str) -> Kept | None:
    row = await database.fetch_one(_READ, (session_id,))
    if row is None:
        return None
    body = _json(row["body"])
    undo = None
    if row["undone_at"] is not None:
        said = _json(row["undo"])
        undo = ReceiptUndo(
            at=int(row["undone_at"]),
            files=int(said.get("files", 0)),
            from_disk=bool(said.get("from_disk", False)),
            people=int(said.get("people", 0)),
            sites=int(said.get("sites", 0)),
        )
    return Kept(body=body, undo=undo)


def _json(text: object) -> dict[str, Any]:
    try:
        value = json.loads(str(text or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def keep(database: Database, session_id: str, body: Mapping[str, Any], now: int) -> None:
    """Write the receipt, as the session ends. Told, so a receipt open on a screen is re-read."""
    async with telling(database, EVERY_ADMIN, About.JOBS) as connection:
        await connection.execute(_KEEP, (session_id, json.dumps(body), now))


async def mark_undone(database: Database, session_id: str, undo: ReceiptUndo) -> bool:
    """Say the swap was undone, once. False when it already was."""
    said = json.dumps(
        {
            "files": undo.files,
            "from_disk": undo.from_disk,
            "people": undo.people,
            "sites": undo.sites,
        }
    )
    async with telling(database, EVERY_ADMIN, About.JOBS) as connection:
        cursor = await connection.execute(_UNDONE, (session_id, undo.at, undo.at, said))
        return bool(cursor.rowcount)


def body_of(live: _Live) -> dict[str, Any]:
    """What the receipt keeps of a session that is ending, read from its memory."""
    groups: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    refused = held = 0
    receiving, sending = live.receiving_half(), live.sending_half()
    if receiving is not None and receiving.offer is not None and receiving.groups is not None:
        landed, passed = _landed(receiving, receiving.offer, receiving.groups)
        groups.extend(one.model_dump(mode="json") for one in landed)
        skipped.extend(one.model_dump(mode="json") for one in passed)
        refused = len(receiving.refusals)
        held = 0 if receiving.screen is None else len(receiving.screen.held)
    if sending is not None and sending.offer is not None and sending.groups is not None:
        sent, passed = _sent(sending, sending.offer, sending.groups)
        groups.extend(one.model_dump(mode="json") for one in sent)
        if receiving is None:
            skipped.extend(one.model_dump(mode="json") for one in passed)
    return {
        "groups": groups,
        "skipped": skipped,
        "refused": refused,
        "held": held,
        "cut_offs": live.cut_offs,
        "moving_since": live.moving_since,
    }


def _bytes_of(sizes: Sequence[int], members: Iterable[int], done: set[int]) -> tuple[int, int]:
    files = [n for n in members if n in done]
    return len(files), sum(sizes[n] for n in files)


def _landed(
    receiving: _Receiving, offer: Offer, groups: Grouping
) -> tuple[list[ReceiptGroup], list[ReceiptSkipped]]:
    local, created, faces = filed_here(receiving, offer, groups)
    answered = receiving.diff is not None
    rows = manifest_of(
        offer,
        groups,
        Progress(answered=answered, wanted=receiving.wanted, done=receiving.done_files),
        held={one.key: "same" for one in (receiving.screen.held if receiving.screen else [])},
    )
    sizes = [one.size for one in offer.files]
    out: list[ReceiptGroup] = []
    passed: list[ReceiptSkipped] = []
    for row in rows:
        files, size = _bytes_of(sizes, groups.members[row.key], receiving.done_files)
        local_id = local.get(row.key)
        if files:
            done_here = [n for n in groups.members[row.key] if n in receiving.done_files]
            out.append(
                ReceiptGroup(
                    way="landed",
                    kind=row.kind,
                    id=local_id,
                    name=row.name,
                    files=files,
                    bytes=size,
                    filed=_filed_as(row.kind, local_id, created),
                    faces=faces.get(local_id or "", 0),
                    usernames=len(
                        {offer.files[n].username for n in done_here if offer.files[n].username}
                    ),
                )
            )
        elif answered and row.state == "skipped":
            passed.append(
                ReceiptSkipped(kind=row.kind, id=local_id, name=row.name, files=row.files)
            )
    return out, passed


def _filed_as(
    kind: str, local_id: str | None, created: set[str]
) -> Literal["created", "added"] | None:
    if kind != "person" or local_id is None:
        return None
    return "created" if local_id in created else "added"


def filed_here(
    receiving: _Receiving, offer: Offer, groups: Grouping
) -> tuple[dict[str, str], set[str], dict[str, int]]:
    """What each group was filed as here: its person or Site id by group key, the people this swap
    created, and the faces held for each person."""
    by_name: dict[str, int] = {}
    for number, person in enumerate(offer.people):
        by_name.setdefault(person.name, number)
    local: dict[str, str] = {}
    created: set[str] = set()
    faces: dict[str, int] = {}
    for number, landed in receiving.landed.items():
        for name, person_id, made in landed.named:
            if name in by_name:
                local.setdefault(person_key(by_name[name]), person_id)
            if made:
                created.add(person_id)
        for person_id, count in landed.faces_by_person:
            faces[person_id] = faces.get(person_id, 0) + count
        if landed.site_id is not None:
            for key in groups.of_file[number]:
                if kind_of(key) == "site":
                    local.setdefault(key, landed.site_id)
    return local, created, faces


def _sent(
    sending: _Sending, offer: Offer, groups: Grouping
) -> tuple[list[ReceiptGroup], list[ReceiptSkipped]]:
    sizes = [one.size for one in offer.files]
    wanted = set(sending.wanted)
    out: list[ReceiptGroup] = []
    passed: list[ReceiptSkipped] = []
    for key in groups.keys:
        files, size = _bytes_of(sizes, groups.members[key], sending.done_files)
        if files:
            out.append(
                ReceiptGroup(
                    way="sent", kind=kind_of(key), name=name_of(offer, key), files=files, bytes=size
                )
            )
        elif sending.answered and not any(n in wanted for n in groups.members[key]):
            passed.append(
                ReceiptSkipped(
                    kind=kind_of(key), name=name_of(offer, key), files=len(groups.members[key])
                )
            )
    return out, passed


def view(
    row: SessionRow,
    kept: Kept | None,
    *,
    folder: ReceiptFolder | None,
    tunnel: str | None,
    device_name: str | None = None,
) -> SwapReceipt:
    """The receipt: the session's row and what was kept of it read together."""
    body = kept.body if kept is not None else {}
    moving_since = body.get("moving_since")
    seconds = None if row.ended_at is None else max(0, row.ended_at - row.started_at)
    moving = (
        None
        if row.ended_at is None or not isinstance(moving_since, int)
        else max(0, row.ended_at - moving_since)
    )
    files, size = row.sent_files, row.sent_bytes
    rate: float | None = None
    if moving:
        rate = round(size / moving, 1)
    elif row.rate_bps:
        rate = round(row.rate_bps / 8, 1)
    undo = kept.undo if kept is not None else None
    landed_here = row.sent_files if row.role == "guest" else row.back_files
    return SwapReceipt(
        session_id=row.id,
        role="host" if row.role == "host" else "guest",
        two_way=row.two_way,
        device=row.peer_device,
        device_name=device_name,
        started_at=row.started_at,
        ended_at=row.ended_at,
        seconds=seconds,
        reason=row.end_reason,
        files=files,
        bytes=size,
        back_files=row.back_files,
        back_bytes=row.back_bytes,
        rate=rate,
        tunnel=tunnel,
        cut_offs=int(body.get("cut_offs", 0) or 0),
        folder=folder,
        groups=[ReceiptGroup.model_validate(one) for one in body.get("groups", [])],
        skipped=[ReceiptSkipped.model_validate(one) for one in body.get("skipped", [])],
        refused=int(body.get("refused", 0) or 0),
        held=int(body.get("held", 0) or 0),
        undo=undo,
        can_undo=undo is None and not row.live and landed_here > 0 and row.folder_id is not None,
    )


# ================================================================================================
# THE UNDO
# ================================================================================================

Mode = Literal["sift", "disk"]


class Remover(Protocol):
    """The delete feature's remover (`slices/delete`): the one thing in Sift that removes a file."""

    async def remove(
        self, asset_id: str, *, mode: Mode, actor: Viewer, location_id: str | None = None
    ) -> None: ...

    async def remove_many(self, asset_ids: Sequence[str], *, mode: Mode, actor: Viewer) -> Any: ...

    async def remove_folder(self, folder_id: str, *, actor: Viewer) -> Any: ...


#: The remover, by the name its feature publishes it under: parts compare by name.
REMOVER: Part[Remover] = Part("deleter")

#: Files removed together: one remover call, one write of its bookkeeping.
UNDO_BATCH = 50

#: The swap's `added` lines, each naming the session and the device: read on the ledger's index of
#: who acted and when, between the session's start and its end.
_LANDED_BY = (
    "SELECT DISTINCT s.subject_id AS asset_id FROM workbench_decisions d"
    " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
    " WHERE d.actor_id = ? AND d.decided_at >= ? AND d.decided_at <= ?"
    " AND d.actor_kind = 'sift' AND d.verb = 'added' AND s.kind = 'asset'"
    " AND CASE WHEN json_valid(d.payload) THEN json_extract(d.payload, '$.session') END = ?"
    " AND (? IS NULL OR CASE WHEN json_valid(d.payload)"
    " THEN json_extract(d.payload, '$.device') END = ?)"
)

#: The people the swap filed on these files that it made, read before the files go.
_PEOPLE_MADE = (
    "SELECT DISTINCT p.id AS id FROM asset_people ap JOIN people p ON p.id = ap.person_id"
    " WHERE ap.asset_id IN (SELECT value FROM json_each(?)) AND ap.source = ?"
    " AND p.created_by_kind = 'sift' AND p.created_by_via = ? AND p.created_at >= ?"
)
#: The usernames and Sites the swap filed these files under, with whether it made each.
_FILED_UNDER = (
    "SELECT DISTINCT u.id AS username_id, u.name AS name, u.created_at AS username_made,"
    " s.id AS site_id, s.created_by_kind AS kind, s.created_by_via AS via, s.created_at AS site_made"
    " FROM asset_usernames au JOIN usernames u ON u.id = au.username_id"
    " JOIN sites s ON s.id = u.site_id"
    " WHERE au.asset_id IN (SELECT value FROM json_each(?)) AND au.source = ?"
)

#: What may hold a row this swap made, from the catalog's own list of what points at each table.
#: The face rows a swap held for a person it made go with that person.
_NOT_HOLDING = frozenset({("people", "pack_entries"), ("people", "face_references")})
_HOLDS = "holds"
_HOLDERS: Mapping[str, tuple[tuple[str, str], ...]] = {
    table: tuple(
        (
            other,
            splice(
                "SELECT 1 FROM {{TABLE}} WHERE {{COLUMN}} = ?{{UNLESS}} LIMIT 1",
                TABLE=other,
                COLUMN=column,
                UNLESS="" if rule == _HOLDS else f" AND NOT ({rule})",
            ),
        )
        for other, column, rule in pointing
        if rule is not None and (table, other) not in _NOT_HOLDING
    )
    for table, pointing in POINTING_AT.items()
}
_TABLE_HERE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
#: Somebody did something to it by hand: it is theirs now, and stays. A file of theirs deleted
#: names them too, and is no act on them: the Undo's own removals are those.
_TOUCHED = (
    "SELECT 1 FROM workbench_decision_subjects s JOIN workbench_decisions d"
    " ON d.id = s.decision_id WHERE s.kind = ? AND s.subject_id = ? AND d.actor_kind = 'user'"
    " AND d.verb <> 'deleted' LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class _Made:
    people: tuple[str, ...] = ()
    usernames: tuple[str, ...] = ()
    sites: tuple[str, ...] = ()


async def _landed_assets(database: Database, row: SessionRow, now: int) -> set[str]:
    device = row.peer_device
    until = now if row.ended_at is None else row.ended_at
    found = await database.fetch_all(
        _LANDED_BY, (VIA_SWAP, row.started_at, until, row.short_id, device, device)
    )
    return {str(one["asset_id"]) for one in found}


async def _made_by(database: Database, row: SessionRow, asset_ids: Sequence[str]) -> _Made:
    """What this swap made on these files, read before they go: the links go with them."""
    if not asset_ids:
        return _Made()
    wanted = json.dumps(list(asset_ids))
    people = await database.fetch_all(_PEOPLE_MADE, (wanted, VIA_SWAP, VIA_SWAP, row.started_at))
    usernames: list[str] = []
    sites: list[str] = []
    for one in await database.fetch_all(_FILED_UNDER, (wanted, VIA_SWAP)):
        if one["name"] and int(one["username_made"] or 0) >= row.started_at:
            usernames.append(str(one["username_id"]))
        if (
            one["kind"] == "sift"
            and one["via"] == VIA_SWAP
            and int(one["site_made"] or 0) >= row.started_at
        ):
            sites.append(str(one["site_id"]))
    return _Made(
        people=tuple(str(one["id"]) for one in people),
        usernames=tuple(dict.fromkeys(usernames)),
        sites=tuple(dict.fromkeys(sites)),
    )


async def _holds_nothing(connection: Connection, kind: str, row_id: str) -> bool:
    table = SHELL_TABLES[kind]
    for other, statement in _HOLDERS[table]:
        here = await (await connection.execute(_TABLE_HERE, (other,))).fetchone()
        if here is None:
            continue
        if await (await connection.execute(statement, (row_id,))).fetchone() is not None:
            return False
    touched = await (await connection.execute(_TOUCHED, (kind, row_id))).fetchone()
    return touched is None


async def _let_go(database: Database, made: _Made) -> tuple[int, int]:
    """Remove what the swap made and nothing holds now, in one transaction: the usernames first,
    then the people, then the Sites the usernames were holding."""
    people = sites = 0
    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        for kind, ids in (
            ("username", made.usernames),
            ("person", made.people),
            ("site", made.sites),
        ):
            for row_id in ids:
                if not await _holds_nothing(connection, kind, row_id):
                    continue
                if await remove_shell_on(connection, kind, row_id):
                    people += kind == "person"
                    sites += kind == "site"
    return people, sites


async def _clear_emptied(
    library: LibraryStore,
    remover: Remover,
    inside: Sequence[FolderRow],
    *,
    actor: Viewer,
    short_id: str,
) -> int:
    """Remove every folder of the swap's that the Undo emptied, the widest first, through the
    remover (which takes only empty directories off the disk). How many files are left in them."""
    counts = {one.id: len(await library.locations_in_folder(one.id)) for one in inside}
    left = sum(counts.values())

    def holds(folder: FolderRow) -> bool:
        prefix = f"{folder.rel_path}/"
        return any(
            counts[one.id]
            for one in inside
            if one.id == folder.id or one.rel_path.startswith(prefix)
        )

    gone: list[str] = []
    for folder in sorted(inside, key=lambda one: one.rel_path.count("/")):
        if holds(folder) or any(folder.rel_path.startswith(f"{one}/") for one in gone):
            continue
        try:
            await remover.remove_folder(folder.id, actor=actor)
            gone.append(folder.rel_path)
        except Exception as error:
            log.warning("swap.undo_folder_kept", swap=short_id, error=type(error).__name__)
    return left


@dataclass(frozen=True, slots=True)
class Undone:
    """What an Undo took back."""

    files: int
    people: int
    sites: int
    left: int


async def _by_place(
    access: Repository, actor: Viewer, in_folder: dict[str, list[str]], here: set[str]
) -> tuple[list[str], list[tuple[str, str]]]:
    """The landed files whose every copy is in the swap's folders, and the copies of the rest."""
    whole: list[str] = []
    partly: list[tuple[str, str]] = []
    for asset_id, locations in in_folder.items():
        everywhere = await access.locations(actor, asset_id)
        if all(one.folder_id in here for one in everywhere):
            whole.append(asset_id)
        else:
            partly.extend((asset_id, location_id) for location_id in locations)
    return whole, partly


async def undo(
    row: SessionRow,
    *,
    database: Database,
    library: LibraryStore,
    access: Repository,
    remover: Remover,
    actor: Viewer,
) -> Undone:
    """Take back every file the swap landed in its own folder, then what it made. See the module
    docstring."""
    if row.folder_id is None:
        return Undone(files=0, people=0, sites=0, left=0)
    folder = await library.get_folder(row.folder_id)
    if folder is None:
        return Undone(files=0, people=0, sites=0, left=0)
    ours = await _landed_assets(database, row, int(time.time()))
    inside = [folder, *await library.folders_in_subtree(folder.root_id, folder.rel_path)]
    here = {one.id for one in inside}
    in_folder: dict[str, list[str]] = {}
    for one in inside:
        for location in await library.locations_in_folder(one.id):
            if location.asset_id in ours:
                in_folder.setdefault(location.asset_id, []).append(location.id)
    made = await _made_by(database, row, list(in_folder))
    whole, partly = await _by_place(access, actor, in_folder, here)
    removed = 0
    for start in range(0, len(whole), UNDO_BATCH):
        batch = whole[start : start + UNDO_BATCH]
        result = await remover.remove_many(batch, mode="disk", actor=actor)
        removed += int(getattr(result, "removed", len(batch)))
    for asset_id, location_id in partly:
        try:
            await remover.remove(asset_id, mode="disk", actor=actor, location_id=location_id)
            removed += 1
        except Exception as error:
            log.warning("swap.undo_kept", swap=row.short_id, error=type(error).__name__)
    left = await _clear_emptied(library, remover, inside, actor=actor, short_id=row.short_id)
    # After the folders: a folder read as somebody's holds them while it stands.
    people, sites = await _let_go(database, made)
    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        await record_event(
            connection,
            actor=Actor.sift(VIA_SWAP),
            verb="swap_undone",
            subject=Subject(kind="swap", id=row.id),
            payload=json.dumps(
                {"device": row.peer_device, "files": removed, "people": people, "sites": sites}
            ),
        )
    log.info(
        "swap.undone",
        swap=row.short_id,
        files=removed,
        people=people,
        sites=sites,
        left=left,
    )
    return Undone(files=removed, people=people, sites=sites, left=left)
