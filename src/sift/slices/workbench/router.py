# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workbench endpoints, admin-only on the server; each queue also scopes to the asking admin."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history import Link, face_sures, is_face_match
from sift.kernel.access.history_boxes import named_of_events
from sift.kernel.access.history_events import (
    FEED_FOLD_SHOWN,
    LedgerEvent,
    Thing,
    actor_names,
    can_be_found,
    names_now,
    own_filters,
    subjects_present,
)
from sift.kernel.access.history_feed import Press, press_of, presses_recent
from sift.kernel.access.history_removals import removals_named
from sift.kernel.access.sentences import today_words, username_opens
from sift.kernel.access.worded import StoredRow, worded_or_stored
from sift.kernel.db import Database, in_clause
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.ledger import ACTOR_SIFT
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.serving import face_version
from sift.kernel.settings_registry import get_registered, get_removed, label_of
from sift.kernel.vocabulary import LEDGER_QUEUE
from sift.kernel.wire import HistoryDetail, link_of, pieces_of
from sift.kernel.wiring import part_of
from sift.kernel.workbench import (
    ASSET,
    FACE,
    TAKEN_BACK,
    Preview,
    Summary,
    TakenBack,
    Workbench,
)
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.workbench.models import (
    AsideView,
    BoardView,
    LedgerActorView,
    LedgerEventView,
    LedgerPage,
    LedgerReceiptView,
    LedgerThingView,
    PreviewView,
    QueueView,
    UndoneFoldView,
    UndoneView,
)
from sift.slices.workbench.service import SERVICE, Board, NotFound, WorkbenchService

router = APIRouter(prefix="/workbench", tags=["workbench"])


def _service(request: Request) -> WorkbenchService:
    return part_of(request, SERVICE)


@dataclass(frozen=True, slots=True)
class _Tokens:
    """The token every picture in one answer is addressed by, minted once here for every queue."""

    face: str
    files: Mapping[str, str | None]
    #: Files the vault holds back from this viewer: a card has no locked tile, so it draws nothing.
    withheld: frozenset[str] = frozenset()
    #: Files whose picture is not made yet, left off the card; read from the same answer.
    unmade: frozenset[str] = frozenset()

    def of(self, one: Preview) -> str | None:
        if one.kind == FACE:
            return self.face
        if one.kind == ASSET:
            return self.files.get(one.id)
        return None

    def shows(self, one: Preview) -> bool:
        """Whether this picture's file may be spoken of: visible, and not held by the vault."""
        return one.kind != ASSET or (one.id in self.files and one.id not in self.withheld)

    def draws(self, one: Preview) -> bool:
        """Whether this picture is drawn: it `shows`, and its picture has been made."""
        return self.shows(one) and not (one.kind == ASSET and one.id in self.unmade)


async def _tokens(access: Repository, viewer: Viewer, pictures: Iterable[Preview]) -> _Tokens:
    """Read what the pictures of one answer are addressed by, in one scoped read."""
    wanted = [one.id for one in pictures if one.kind == ASSET]
    seen = await access.assets_of(viewer, wanted) if wanted else {}
    return _Tokens(
        face=face_version(viewer.cache_stamp),
        files={asset_id: view.art_version for asset_id, view in seen.items()},
        withheld=frozenset(asset_id for asset_id, view in seen.items() if view.concealed),
        unmade=frozenset(asset_id for asset_id, view in seen.items() if not view.has_thumb),
    )


def _pictures(found: Board) -> Iterable[Preview]:
    """Every picture the board is about to draw: each pile's own stills."""
    for queue in found.queues:
        yield from queue.preview


def _preview(one: Preview, art: _Tokens) -> PreviewView:
    return PreviewView(kind=one.kind, id=one.id, href=one.href, art=art.of(one))


def _queue(summary: Summary, art: _Tokens) -> QueueView:
    return QueueView(
        name=summary.name,
        title=summary.title,
        decision=summary.decision,
        icon=summary.icon,
        count=summary.count,
        verb=summary.verb,
        verb_one=summary.verb_one,
        band=summary.band.value,
        group=summary.group,
        group_title=summary.group_title,
        purpose=summary.purpose,
        pending=summary.pending,
        advice=summary.advice,
        aside=(
            None
            if summary.aside is None
            else AsideView(
                said=summary.aside.said, link=summary.aside.link, href=summary.aside.href
            )
        ),
        opens=summary.opens,
        preview=[_preview(one, art) for one in summary.preview if art.draws(one)],
    )


@router.get("")
async def board(
    service: Annotated[WorkbenchService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    only: Annotated[list[str] | None, Query()] = None,
) -> BoardView:
    """What needs somebody; a queue with nothing behind it is absent, never empty."""
    found = await service.board(viewer, None if only is None else set(only))
    art = await _tokens(access, viewer, _pictures(found))
    return BoardView(
        queues=[_queue(one, art) for one in found.queues],
    )


@router.post("/decisions/{decision_id}/undo", dependencies=[Depends(csrf_protect)])
async def undo(
    decision_id: str,
    service: Annotated[WorkbenchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> UndoneView:
    """Put back what one decision did, and only what it did, from what it recorded."""
    try:
        undone = await service.undo(viewer, decision_id)
    except NotFound as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refused)) from refused
    # The counts beside the yes, so a partial undo is said as that.
    return UndoneView(
        undone=undone.put_back > 0,
        put_back=undone.put_back,
        of=undone.of,
        said=undone.said,
    )


# There is no per-queue history route: the thread and its Undo live on Settings > History.


# The ledger feed: everything that has happened, newest first, at an address of its own.

ledger_router = APIRouter(prefix="/ledger", tags=["ledger"])

#: Smaller than the ceiling: a column read down a day at a time, with "Show more".
PAGE_OF_EVENTS = 50

#: Where each named kind lives in the client, kept on the server; a kind not here gets no link.
_ADDRESS = {
    "asset": "/asset/{0}",
    "person": "/people/{0}",
    "site": "/sites/{0}",
    "tag": "/tags/{0}",
    "collection": "/collections/{0}",
    "photo_set": "/photo-sets/{0}",
    "song": "/songs/{0}",
    # A username has no page (`sentences.username_opens`); a face group opens on the Faces page.
    "pile": "/organize/faces-to-name/{0}",
    # A folder is the Files wall filtered by its id, as the folder browser writes it.
    "folder": "/browse?in={0}",
    # A download is its queue with that row picked out.
    "download": "/downloads?row={0}",
}


#: Which Settings pane draws each registry section, held equal to the client's by a test.
SETTINGS_PANES: Mapping[str, str] = {
    "Library": "library",
    "Importing": "importing",
    "Editing": "editing",
    "Downloads": "downloads",
    "Sites and Tunnels": "sites",
    "Playback": "playback",
    "Theater": "playback",
    "Identify": "faces",
    "Smart Search": "semantic",
    "Watermarks": "watermarks",
    "Stash-boxes": "stash-boxes",
    "Music": "music",
    "Performance": "performance",
    "Scheduled tasks": "tasks",
    "Maintenance": "maintenance",
    "Logs": "logs",
    "Privacy and Security": "privacy",
    "Insights": "insights",
    "Appearance": "appearance",
    "Backup": "backup",
    "Updates": "updates",
    "About": "about",
}

#: A Site's own settings live in the Downloads pane's per-Site list.
SITE_OPTIONS_PREFIX = "site_options."
_SITE_OPTIONS_ROW = "/settings/downloads#downloads.name_template"
_DEFAULT_FOLDER = ("site_options.*default*.dest_folder_id", "Download folder")


def setting_href(key: str) -> str | None:
    """Where a line about a setting opens: its row in Settings, or None where it has no row."""
    if key.startswith(SITE_OPTIONS_PREFIX):
        return _SITE_OPTIONS_ROW
    setting = get_registered(key)
    pane = None if setting is None else SETTINGS_PANES.get(setting.section)
    return None if pane is None else f"/settings/{pane}#{quote(key, safe='.-_')}"


def _href(kind: str, address: str | None) -> str | None:
    """Where pressing one named thing goes, or None for no page or a thing that has gone."""
    shape = _ADDRESS.get(kind)
    if shape is None or address is None:
        return None
    return shape.format(quote(address, safe=""))


_USERNAME_PEOPLE = "SELECT id AS id, person_id AS person_id FROM usernames WHERE id IN (?*)"


def _piece(view: LedgerThingView, *, ended_here: bool = False) -> say.Piece:
    """One named thing as a line's piece: a link, struck-through words if gone, or plain words."""
    kind = say.LINKED_KINDS.get(view.kind)
    words = view.name or (say.A_GONE if view.gone else say.A_THING).get(view.kind, "something")
    if view.kind == "setting":
        # A setting's way there is its row (`setting_href`).
        return (
            say.thing("setting", view.id, words, href=view.href) if view.href else say.Piece(words)
        )
    if view.name and view.href is None and view.kind in say.DELETED_KINDS and not ended_here:
        words = say.since_deleted(view.name)
    if kind is None or (view.href is None and not view.gone):
        return say.Piece(words)
    return say.thing(kind, view.id, words, href=view.href, gone=view.gone)


def _by(actor: LedgerActorView, viewer: Viewer) -> str:
    """Who took the act, as the first word of its line: "You", a user's name, a box, or Sift."""
    if actor.kind == "user":
        if actor.id is not None and actor.id == viewer.id:
            return say.YOU
        return actor.name or "A user who is gone"
    if actor.kind == "box":
        return actor.name or say.A_STASH_BOX
    return say.SIFT


def _said(
    event: LedgerEvent,
    view: LedgerEventView,
    viewer: Viewer,
    sures: Mapping[tuple[str, str], list[float | None]],
    named: Sequence[say.FilledField] | None = None,
) -> say.Said:
    """The one line for one event, built by the History builders with every slot a name."""
    ended = (event.verb or "") in say.ENDS_ITS_THING
    subjects = [(one.kind, _piece(one, ended_here=ended)) for one in view.subjects]
    person = None if event.object is None else event.object.id
    files = [one.id for one in view.subjects if one.kind == "asset"]
    return say.feed_line(
        event.verb or "",
        by=_by(view.actor, viewer),
        task=event.actor_id if view.actor.kind == ACTOR_SIFT else None,
        subjects=subjects,
        object_kind=None if view.object is None else view.object.kind,
        object_piece=None if view.object is None else _piece(view.object, ended_here=ended),
        count=event.count,
        title=event.title,
        payload=say.payload_of(event.payload),
        sures=[sure for one in files for sure in sures.get((one, person or ""), [])],
        named=named,
    )


def _face_pairs(events: Sequence[LedgerEvent]) -> list[tuple[str, str]]:
    """Every (file, person) a page's face matches name, so their figures are read once."""
    pairs: list[tuple[str, str]] = []
    for event in events:
        kind = None if event.object is None else event.object.kind
        if event.object is not None and is_face_match(
            event.verb, kind, event.actor_kind, event.actor_id
        ):
            pairs.extend((one.id, event.object.id) for one in event.subjects if one.kind == "asset")
    return pairs


def _with_line(
    view: LedgerEventView,
    event: LedgerEvent,
    viewer: Viewer,
    sures: Mapping[tuple[str, str], list[float | None]],
    named: Sequence[say.FilledField] | None = None,
) -> LedgerEventView:
    """The view with its line: the pieces and what its "Show each" opens to."""
    said = _said(event, view, viewer, sures, named)
    return view.model_copy(update={"pieces": pieces_of(said.pieces), "detail": _detail(said)})


def _detail(said: say.Said) -> list[HistoryDetail]:
    """What a line's "Show each" opens to: its groups of things, and the fields an edit counted."""
    detail = [
        HistoryDetail(
            kind=group.kind,
            words=group.words,
            entries=[
                link_of(Link(kind=one.kind or "", id=one.id or "", name=one.text, href=one.href))
                for one in group.things
            ],
        )
        for group in said.groups
    ]
    if said.folded is not None:
        words, members = said.folded
        detail.append(
            HistoryDetail(
                kind="field",
                words=words,
                entries=[link_of(Link(kind="field", id=one, name=one)) for one in members],
            )
        )
    return detail


def _one_fill(press: Press) -> LedgerEvent | None:
    """The act a box's one-thing press is said by, where its link and fill were one press."""
    if press.folded != 2 or press.first is None or (press.event.verb or "") != "enriched":
        return None
    if len(press.subjects) != 1 or len(press.objects) > 1:
        return None
    filled = [one for one in (press.event, press.first) if say.payload_of(one.payload)]
    return filled[0] if filled else press.event


def _shown(press: Press) -> tuple[list[Thing], list[Thing]]:
    """The things a folded press lists under "Show each", at most `FEED_FOLD_SHOWN` of each kind."""
    objects = [Thing(kind=one.kind, id=one.id, name=one.name) for one in press.objects]
    taken: Counter[str] = Counter()
    subjects: list[Thing] = []
    for one in press.subjects:
        if taken[one.kind] < FEED_FOLD_SHOWN:
            taken[one.kind] += 1
            subjects.append(one)
    return objects[:FEED_FOLD_SHOWN], subjects


def _with_fold(
    press: Press,
    view: LedgerEventView,
    viewer: Viewer,
    present: Mapping[tuple[str, str], str],
    called: Mapping[tuple[str, str], str],
    usernames: Mapping[str, str],
) -> LedgerEventView:
    """A press of many acts as its one line, and the things it stands for under "Show each"."""
    event = press.event
    objects, subjects = _shown(press)

    ended = (event.verb or "") in say.ENDS_ITS_THING

    def piece(one: Thing) -> say.Piece:
        return _piece(_thing(one, present, called, usernames), ended_here=ended)

    said = say.feed_folded(
        event.verb or "",
        by=_by(view.actor, viewer),
        task=event.actor_id if view.actor.kind == ACTOR_SIFT else None,
        acts=press.folded,
        object_kind=None if event.object is None else event.object.kind,
        objects=[piece(one) for one in objects],
        objects_total=len(press.objects),
        subjects=[(one.kind, piece(one)) for one in subjects],
        subject_counts=Counter(one.kind for one in press.subjects),
        title=event.title,
        first=None if press.first is None else say.payload_of(press.first.payload),
        payload=say.payload_of(event.payload),
        untold=press.untold,
    )
    return view.model_copy(update={"pieces": pieces_of(said.pieces), "detail": _detail(said)})


async def _username_hrefs(
    database: Database, present: Mapping[tuple[str, str], str]
) -> dict[str, str]:
    """Where each username a page names opens, by id, in one bounded statement."""
    ids = sorted(thing_id for (kind, thing_id) in present if kind == "username")
    if not ids:
        return {}
    statement, bound = in_clause(_USERNAME_PEOPLE, ids)
    return {
        str(row["id"]): username_opens(str(row["id"]), row["person_id"])
        for row in await database.fetch_all(statement, bound)
    }


def _thing(
    one: Thing,
    present: Mapping[tuple[str, str], str],
    named: Mapping[tuple[str, str], str],
    usernames: Mapping[str, str],
) -> LedgerThingView:
    """One named thing as the feed draws it: the snapshot first, then today's name, else gone."""
    address = present.get((one.kind, one.id))
    if one.kind == "setting":
        return LedgerThingView(
            kind=one.kind,
            id=one.id,
            name=_setting_label(one.id, one.name),
            href=setting_href(one.id),
        )
    name = one.name or named.get((one.kind, one.id))
    href = usernames.get(one.id) if one.kind == "username" else _href(one.kind, address)
    return LedgerThingView(
        kind=one.kind,
        id=one.id,
        name=name,
        href=href,
        gone=name is None and address is None and can_be_found(one.kind),
    )


def _setting_label(key: str, snapshot: str | None = None) -> str:
    """What a setting is called now, from the registry, else the snapshot, else the key."""
    if key == _DEFAULT_FOLDER[0]:
        return _DEFAULT_FOLDER[1]
    setting = get_registered(key)
    if setting is not None:
        return setting.label
    # A retired key is called by what answers it now; a removed one by its last name, as gone.
    became = label_of(key)
    if became is not None:
        return became
    removed = get_removed(key)
    if removed is not None:
        return f"{removed.label} (since removed)"
    return snapshot or key


def _actor(event: LedgerEvent, names: Mapping[tuple[str, str], str]) -> LedgerActorView:
    """Who took the act; a row from before the ledger is Sift with no pass."""
    kind = event.actor_kind or ACTOR_SIFT
    return LedgerActorView(
        kind=kind,
        id=event.actor_id,
        name=None if event.actor_id is None else names.get((kind, event.actor_id)),
    )


def _receipt(event: LedgerEvent, bench: Workbench) -> LedgerReceiptView | None:
    """The decision behind an event, where it was one; `final` is asked of the registry."""
    if event.queue == LEDGER_QUEUE:
        return None
    queue = bench.reverser(event.queue)
    return LedgerReceiptView(
        queue=event.queue,
        # Today's words for a retired phrase a saved title still carries. See
        # `sentences.STALE_IN_A_TITLE`.
        title=today_words(event.title),
        detail=event.detail,
        reversed_at=event.reversed_at,
        final=queue is not None and not queue.reversible,
        taken_back=None if event.reversed_at is None else _taken_back(queue),
    )


def _taken_back(queue: object) -> str:
    """What an undone decision's line says: its area's sentence, else the one true of every undo."""
    return queue.taken_back if isinstance(queue, TakenBack) else TAKEN_BACK


def _things_of(presses: Sequence[Press]) -> list[Thing]:
    """Every thing one page of presses names, including what a folded press lists (`_shown`)."""
    things: list[Thing] = []
    for press in presses:
        event = press.event
        things.extend(event.subjects)
        if event.object is not None:
            things.append(event.object)
        if press.folded > 1:
            objects, subjects = _shown(press)
            things.extend([*objects, *subjects])
    return things


def _named(things: Sequence[Thing]) -> dict[str, list[str]]:
    """Every thing one page names, gathered by kind, so each kind is asked about once."""
    wanted: dict[str, list[str]] = {}
    for one in things:
        wanted.setdefault(one.kind, []).append(one.id)
    return wanted


def _nameless(things: Sequence[Thing]) -> dict[str, list[str]]:
    """Every thing one page names without a name, by kind; a setting's name is its label."""
    wanted: dict[str, list[str]] = {}
    for one in things:
        if one.name is None and one.kind != "setting":
            wanted.setdefault(one.kind, []).append(one.id)
    return wanted


def _acted(events: Sequence[LedgerEvent]) -> dict[str, list[str]]:
    """Every actor one page names, gathered the same way."""
    wanted: dict[str, list[str]] = {}
    for event in events:
        if event.actor_kind is not None and event.actor_id is not None:
            wanted.setdefault(event.actor_kind, []).append(event.actor_id)
    return wanted


@ledger_router.get("")
async def ledger(
    database: Annotated[Database, Depends(wiring.database)],
    bench: Annotated[Workbench, Depends(wiring.workbench)],
    runs: Annotated[Ledger, Depends(wiring.ledger)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_OF_EVENTS,
    offset: Annotated[int, Query(ge=0)] = 0,
    kind: Annotated[str | None, Query()] = None,
    verb: Annotated[str | None, Query()] = None,
    decisions: Annotated[bool, Query()] = False,
    access: Annotated[Repository | None, Depends(wiring.access)] = None,
) -> LedgerPage:
    """Everything that has happened in this library, newest first; admin-only, vault applied.
    `kind`, `verb` and `decisions` narrow it; an unknown word matches nothing."""
    # One line per press, and the pager counts lines.
    presses, total = await presses_recent(
        database, viewer, limit=limit, offset=offset, kind=kind, verb=verb, decisions=decisions
    )
    found = [press.event for press in presses]
    things = _things_of(presses)
    present = await subjects_present(database, _named(things))
    called = await names_now(database, _nameless(things))
    called |= await own_filters(database, viewer, _nameless(things).get("saved_filter", []))
    names = await actor_names(database, _acted(found))
    usernames = await _username_hrefs(database, present)
    sures = await face_sures(database, _face_pairs(found))
    fills = {press.event.id: one for press in presses if (one := _one_fill(press)) is not None}
    named = await named_of_events(database, [*found, *fills.values()])
    boxed = await _removals_boxed(database, presses)
    reported = await _reports_among(runs, found)
    items: list[LedgerEventView] = []
    for press in presses:
        event = press.event
        view = LedgerEventView(
            id=event.id,
            at=event.at,
            actor=_actor(event, names),
            verb=event.verb,
            subjects=[_thing(one, present, called, usernames) for one in event.subjects],
            object=(
                None if event.object is None else _thing(event.object, present, called, usernames)
            ),
            count=event.count,
            receipt=_receipt(event, bench),
            folded=press.folded,
            first=event.id if press.first is None else press.first.id,
            standing=press.standing,
            report=next(
                (one.id for one in event.subjects if one.kind == "run" and one.id in reported),
                None,
            )
            if event.verb == "ran"
            else None,
        )
        fill = fills.get(event.id)
        if fill is not None:
            # Said by the act that filled in, under the press's own id and moment.
            told = view.model_copy(
                update={
                    "subjects": [_thing(one, present, called, usernames) for one in fill.subjects],
                    "object": None
                    if fill.object is None
                    else _thing(fill.object, present, called, usernames),
                }
            )
            items.append(_with_line(told, fill, viewer, sures, named.get(fill.id)))
            continue
        items.append(
            _with_line(view, boxed.get(event.id, event), viewer, sures, named.get(event.id))
            if press.folded == 1
            else _with_fold(press, view, viewer, present, called, usernames)
        )
    items = await _decisions_worded(database, bench, viewer, presses, items)
    if decisions and access is not None:
        stills = await _stills(bench, access, viewer, presses)
        items = [
            one.model_copy(update={"still": stills[one.id]}) if one.id in stills else one
            for one in items
        ]
    return LedgerPage(items=items, total=total, offset=offset)


async def _removals_boxed(database: Database, presses: Sequence[Press]) -> dict[str, LedgerEvent]:
    """A filing Sift took off on a box's answer names the box on a one-act line."""
    return {
        one.id: one
        for one in await removals_named(
            database, [press.event for press in presses if press.folded == 1]
        )
    }


async def _reports_among(runs: Ledger, found: Sequence[LedgerEvent]) -> set[str]:
    """Which "ran" lines have a report: a library pass does, a task's own run line does not."""
    return await runs.recorded_among(
        [
            subject.id
            for event in found
            if event.verb == "ran"
            for subject in event.subjects
            if subject.kind == "run"
        ]
    )


async def _stills(
    bench: Workbench, access: Repository, viewer: Viewer, presses: Sequence[Press]
) -> dict[str, PreviewView]:
    """The picture each decision on the page is about, asked of its queue, once per page."""
    asked = [press.event for press in presses if press.event.queue != LEDGER_QUEUE]

    async def pictures(event: LedgerEvent) -> tuple[Preview, ...]:
        queue = bench.reverser(event.queue)
        if queue is None:
            return ()
        try:
            return await queue.pictures_of(viewer, event.payload)
        except Exception:
            return ()

    found = await asyncio.gather(*(pictures(one) for one in asked))
    art = await _tokens(access, viewer, (one for shown in found for one in shown))
    stills: dict[str, PreviewView] = {}
    for event, shown in zip(asked, found, strict=True):
        drawn = next((one for one in shown if art.draws(one)), None)
        if drawn is not None:
            stills[event.id] = _preview(drawn, art)
    return stills


def _receipt_of(event: LedgerEvent) -> StoredRow:
    """A ledger row that is a decision, as the stored receipt a line is worded from."""
    return StoredRow(
        id=event.id,
        queue=event.queue,
        user_id=event.user_id,
        title=event.title,
        detail=event.detail,
        payload=event.payload,
        decided_at=event.at,
    )


async def _decisions_worded(
    database: Database,
    bench: Workbench,
    viewer: Viewer,
    presses: Sequence[Press],
    items: list[LedgerEventView],
) -> list[LedgerEventView]:
    """The page with every decision, folded or not by verb, said in its area's own words."""
    asked = [
        (press, view)
        for press, view in zip(presses, items, strict=True)
        if press.event.queue != LEDGER_QUEUE
        and (press.event.verb == "decided" or press.folded == 1)
    ]
    if not asked:
        return items
    lines = await worded_or_stored(
        database,
        bench,
        viewer,
        [(_receipt_of(press.event), press.folded) for press, _view in asked],
    )
    worded: dict[str, LedgerEventView] = {}
    for press, view in asked:
        line = lines.get(press.event.id)
        if line is None or not line.pieces:
            continue
        times = say.times(press.folded) if press.folded > 1 else None
        # The line under belongs to one act: a fold's acts each wrote their own.
        worded[view.id] = view.model_copy(
            update={
                "pieces": pieces_of(say.said(line.pieces, times)),
                "more": line.more if press.folded == 1 else "",
            }
        )
    return [worded.get(one.id, one) for one in items]


@ledger_router.post("/{event_id}/undo-all", dependencies=[Depends(csrf_protect)])
async def undo_all(
    event_id: str,
    database: Annotated[Database, Depends(wiring.database)],
    service: Annotated[WorkbenchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    kind: Annotated[str | None, Query()] = None,
    verb: Annotated[str | None, Query()] = None,
    decisions: Annotated[bool, Query()] = False,
) -> UndoneFoldView:
    """Put back every decision a folded feed line stands for, each through its own undo."""
    receipts = [
        one
        for one, queue in await press_of(
            database, viewer, event_id, kind=kind, verb=verb, decisions=decisions
        )
        if queue != LEDGER_QUEUE
    ]
    if not receipts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing on that line can be undone")
    undone = await service.undo_each(viewer, receipts)
    return UndoneFoldView(undone=undone, of=len(receipts))
