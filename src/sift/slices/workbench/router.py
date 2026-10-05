# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workbench endpoints. Admin-only, and the server is what says so.

`require_admin` sits on every route here. Deciding what a library says about the people in it is
admin work, whole, so this is refused outright rather than answered with an empty board. A control
a guest cannot see but can still ask for directly is not access control.

Necessary and not sufficient: each queue resolves what it reports against the user asking as
well, because an admin can conceal things from themselves, and a count built for "an admin" rather
than for THIS admin would hand back what they hid.

Nothing here applies anything on its own. The board is a read, and the one write is taking a
decision back.
"""

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
    """The token every picture in one answer is addressed by, minted once for the whole reply.

    **Here and not in the queues, and that is the whole design of it.** A token is what lets a
    browser keep a picture for a week rather than asking about every card on every visit, and it is
    the same rule for every queue: what the picture currently is, folded with how many times what
    this user may see has changed. Twenty-odd places mint a `Preview` across eight areas, so a
    token filled in at each of them is a token forgotten at one, and a card whose picture quietly
    goes back to being re-checked is not a thing anybody can see. Filled where a `Preview` becomes
    the thing on the wire instead, a queue written next year inherits it without knowing it exists.

    A face needs no read at all: its crop is written once when the face is found and never
    rewritten, so the whole of its token is the user's stamp. See `face_version`. A file's is
    read through the same view the grid draws its tiles from, so the address a card builds and the
    address the wall builds for the same file are one address and the browser holds one copy.

    A kind this version has no token for gets none, and that is the safe direction: the address is
    left bare, and a bare address is re-checked on every use.
    """

    face: str
    files: Mapping[str, str | None]
    #: The files among them the vault is holding back from this viewer: drawn as nothing at all,
    #: because a card has no locked tile to put in their place.
    withheld: frozenset[str] = frozenset()
    #: The files among them whose picture has not been made yet: a card leaves them out rather than
    #: drawing a box that waits for an address with nothing behind it. Read from the same answer as
    #: the rest, so it costs no read of its own, and it is every queue's at once for the reason the
    #: token is: a queue written next year cannot forget it.
    unmade: frozenset[str] = frozenset()

    def of(self, one: Preview) -> str | None:
        if one.kind == FACE:
            return self.face
        if one.kind == ASSET:
            return self.files.get(one.id)
        return None

    def shows(self, one: Preview) -> bool:
        """Whether this picture's file may be spoken of at all: one this viewer may be shown and
        the vault is not holding back. A queue resolves its own previews; this is the lock on the
        way out."""
        return one.kind != ASSET or (one.id in self.files and one.id not in self.withheld)

    def draws(self, one: Preview) -> bool:
        """Whether this picture is drawn: it `shows`, and its picture has been made."""
        return self.shows(one) and not (one.kind == ASSET and one.id in self.unmade)


async def _tokens(access: Repository, viewer: Viewer, pictures: Iterable[Preview]) -> _Tokens:
    """Read what the pictures of one answer are addressed by, in one go.

    Scoped, like every read of a file: `assets_of` answers only about files this user may be
    shown, so a picture it may not have simply carries no token and is asked about every time. That
    is a second lock on a door the queues already lock (each of them resolves its own previews
    against the viewer) and the cheaper of the two mistakes if either ever stops.
    """
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
    """What needs somebody. What was decided is the feed's, narrowed to Decisions.

    Only the queues with something behind them. A feature that is switched off, or whose source of
    work has never run, is absent rather than present and empty: an empty panel reads as a broken
    one, and it is the difference between a screen that is finished and a screen that is failing.
    """
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
    """Put back what one decision did, and only what it did.

    Reversed from what the decision wrote down about itself at the time, so a person who already
    existed is not deleted and an attribution that predates it is not detached.
    """
    try:
        undone = await service.undo(viewer, decision_id)
    except NotFound as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refused)) from refused
    # The counts beside the yes, so a batch that went back only in part is said as that.
    return UndoneView(
        undone=undone.put_back > 0,
        put_back=undone.put_back,
        of=undone.of,
        said=undone.said,
    )


# There is no per-queue history route: the thread and its Undo live on Settings > History.


# --- the ledger feed -----------------------------------------------------------------------------
#
# What the whole installation has been doing, newest first: the record that lives in Settings beside
# Activity and Logs.
#
# A ROUTER OF ITS OWN rather than a path under `/workbench`, and the reason is what the address
# means. `/api/workbench` is the Organize board (the work waiting on somebody) and this is not
# that. It is everything that has already happened, most of it to nobody's queue, and a screen that
# empties is the whole promise of the board: what was decided is read here, under Decisions, and
# never on the board, where it would compete with the work. It lives in the workbench slice because the events are rows of
# `workbench_decisions` widened, which is that slice's table; the address says what it is.

#: The feed's own router. Mounted beside the board's in `sift/wiring/routes.py`.
ledger_router = APIRouter(prefix="/ledger", tags=["ledger"])

#: How many events one page holds unless the pane asks for another number.
#:
#: Smaller than the page ceiling on purpose: this is a column of sentences somebody reads DOWN, one
#: day at a time, and "Show more" is the gesture. Two hundred lines is not a longer answer, it is
#: the same answer with the newest of it pushed off the screen.
PAGE_OF_EVENTS = 50

#: Where each kind of named thing lives in the client, as a format for its id.
#:
#: ON THE SERVER, and that is the same call `Preview.href` and a queue's `opens` already make: the
#: area that knows WHAT is being named is the only one that can say which screen it means. A client
#: assembling `/photo-sets/{id}` out of a kind and an id is a second vocabulary, and the day one of
#: these paths moves it goes wrong quietly, on a screen nobody is looking at.
#:
#: A kind that is not here has no page and gets no link: a shoot, a stash-box, a grant. A setting
#: has a ROW rather than a page, and is linked to it by `setting_href` below.
#: Absent rather than a guess: an address that lands nowhere is worse than plain words.
_ADDRESS = {
    "asset": "/asset/{0}",
    "person": "/people/{0}",
    "site": "/sites/{0}",
    "tag": "/tags/{0}",
    "collection": "/collections/{0}",
    "photo_set": "/photo-sets/{0}",
    "song": "/songs/{0}",
    # A USERNAME IS NOT HERE, and has no page. See `sentences.username_opens`, which sends a press
    # on one to its person or to the files under it. A GROUP OF FACES opens where the Faces page
    # draws one group: the address the client's own `face_pile` link uses
    # (`components/common/history.ts`), never the retired `/organize/to-check/{0}`, which lands on
    # "There is nothing of that name here".
    "pile": "/organize/faces-to-name/{0}",
    # A FOLDER is the Files wall filtered to it, and named by its ID: the address the folder
    # browser writes when somebody opens one (`FolderExplorer.svelte`, `nameFor`), so a line here
    # and a click land on one list. Never the PATH, which names nothing for a library folder's own
    # folder (its path is empty: `/browse?in=`) and two folders at once where two library folders
    # share a "2024". See `history_events._STILL_THERE`.
    "folder": "/browse?in={0}",
    # A DOWNLOAD IS A ROW ON A QUEUE, so its address is the queue with that row picked out. The
    # same shape a folder has and for the same reason: it has no page of its own, it has a place on
    # one. The Downloads screen reads `row` and scrolls to it.
    "download": "/downloads?row={0}",
}


#: WHICH SETTINGS PANE DRAWS EACH REGISTRY SECTION, as the client's address for it: the client's
#: `REGISTRY_HOME` (`settings-ui/sections.ts`), which is the one join between the two lists of
#: sections. Written here because a setting's line is linked on the server like every other named
#: thing (see `_ADDRESS`), and held equal to the client's by `test_history_names_what_it_knows`,
#: so a pane renamed on one side and not the other goes red rather than linking nowhere. The client
#: follows its own redirects from there (`resolveAddress`: Logs is a tab of Tasks and Activity).
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
    "Appearance": "appearance",
    "Backup": "backup",
    "Updates": "updates",
    "About": "about",
}

#: A Site's own settings are not registry settings: they are the Downloads pane's per-Site list,
#: under its "Name template" group (`NamingTemplate.svelte`), keyed `site_options.<site>.<field>`.
SITE_OPTIONS_PREFIX = "site_options."
_SITE_OPTIONS_ROW = "/settings/downloads#downloads.name_template"
_DEFAULT_FOLDER = ("site_options.*default*.dest_folder_id", "Download folder")


def setting_href(key: str) -> str | None:
    """Where a line about a setting opens: its row in Settings, or None where it has no row.

    A registered setting opens its own row, which the pane rings on arrival (`settings-anchor`);
    a Site's own setting opens the Downloads pane's per-Site list. A key the registry no longer
    knows (retired, removed) is plain words: there is no row to land on.
    """
    if key.startswith(SITE_OPTIONS_PREFIX):
        return _SITE_OPTIONS_ROW
    setting = get_registered(key)
    pane = None if setting is None else SETTINGS_PANES.get(setting.section)
    return None if pane is None else f"/settings/{pane}#{quote(key, safe='.-_')}"


def _href(kind: str, address: str | None) -> str | None:
    """Where pressing one named thing goes, or None where there is nowhere.

    None for two different reasons that are one answer on screen: a kind with no page, and a thing
    that is no longer there. The caller cannot tell them apart and does not need to: both are a
    name drawn as the plain words it already is.
    """
    shape = _ADDRESS.get(kind)
    if shape is None or address is None:
        return None
    return shape.format(quote(address, safe=""))


#: Who each username on a page belongs to, for the usernames found still there.
_USERNAME_PEOPLE = "SELECT id AS id, person_id AS person_id FROM usernames WHERE id IN (?*)"


def _piece(view: LedgerThingView, *, ended_here: bool = False) -> say.Piece:
    """One named thing as the piece a line places: a way there where the kind has a page and the
    thing is still here; plain words, struck through, where it has gone; plain words otherwise.

    A NAMED thing of a kind with a page and no way to it is one that has gone (`_href` answers None
    for a kind with a page only when the thing was not found), and it is said the way every worded
    line says it (`sentences.since_deleted`), unless the line is the act that ended it
    (`ended_here`, `sentences.ENDS_ITS_THING`), which is why it is gone.
    """
    kind = say.LINKED_KINDS.get(view.kind)
    words = view.name or (say.A_GONE if view.gone else say.A_THING).get(view.kind, "something")
    if view.kind == "setting":
        # A setting has a row rather than a page, so it is not one of the ledger's linked kinds
        # (those are probed for being there); its way there is its row (`setting_href`).
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
    """The act a box's press on ONE thing is said by, where its two acts were one press.

    Linking writes a bare link and the fill writes the fields, in the same second, so the press
    folds to two acts on one person and one box, and the folded words count "1 person" and name
    nothing it filled. That press is one act: the one of its two that recorded what was filled in.
    Where both did (an answer applied twice over, the second the one in force) it is the newer;
    where neither did, the newer says what is true of both (the box recognized them). A fold of
    several things stays folded.
    """
    if press.folded != 2 or press.first is None or (press.event.verb or "") != "enriched":
        return None
    if len(press.subjects) != 1 or len(press.objects) > 1:
        return None
    filled = [one for one in (press.event, press.first) if say.payload_of(one.payload)]
    return filled[0] if filled else press.event


def _shown(press: Press) -> tuple[list[Thing], list[Thing]]:
    """The things a folded press lists under its "Show each": what it was done with, and the newest
    `FEED_FOLD_SHOWN` of each kind it was about. Only these are looked up: the counts are the
    press's own, so a press over four thousand files asks after a hundred of them."""
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
    """Where each username a page names opens, by the username's id. See `sentences.username_opens`.

    One statement for the page, bounded by it, and none at all for a page that names no username:
    the same budget `subjects_present` keeps. Asked only about the usernames that probe found, so a
    username that has gone is still plain words rather than a link to nothing.
    """
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
    """One named thing as the feed draws it: what to call it, and where pressing it goes.

    THE SNAPSHOT FIRST, ALWAYS: it is what the thing was called when the act was taken, and the
    whole reason the ledger stores it. `named` is the fallback and only the fallback: rows from
    before names were snapshotted carry none, and drawn bare they would read "a file", "a
    username", "a tag": the record answering "what did Sift delete" with the word for a category.
    See `history_names.names_now`.

    `gone` is the third case and is a different sentence from either: nothing was written down, the
    thing is of a kind Sift can look up, and it is not there, so the line says it has gone rather
    than naming a category as though it were still on a shelf somewhere.
    """
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
    """What a setting is called NOW, for a line about it.

    The one kind whose name is read live rather than off the row, and it is the opposite of the
    snapshot rule for a reason the rule itself gives: a snapshot is for a thing that can be renamed
    or deleted, and a setting is neither: its key is fixed and its label is copy, which is edited.
    Older rows snapshotted the KEY, so reading the label back is also what makes those rows read as
    the label ("Volume") rather than as `playback.volume`.

    A key the registry does not know falls back to the SNAPSHOT, then to the key. Two kinds of row
    reach that branch: a setting since retired, whose snapshot is its last label (or, on an older
    row, the key itself); and a Site's own setting, whose writer kept words like "Instagram's
    name template". The default download folder is its row's name, whatever older rows kept.
    """
    if key == _DEFAULT_FOLDER[0]:
        return _DEFAULT_FOLDER[1]
    setting = get_registered(key)
    if setting is not None:
        return setting.label
    # A key RETIRED into others is called by what answers it now; one REMOVED with nothing in its
    # place by what it was last called, said to be gone: its snapshot can hold a word this
    # application no longer says (`settings_registry.Removed`).
    became = label_of(key)
    if became is not None:
        return became
    removed = get_removed(key)
    if removed is not None:
        return f"{removed.label} (since removed)"
    return snapshot or key


def _actor(event: LedgerEvent, names: Mapping[tuple[str, str], str]) -> LedgerActorView:
    """Who took the act. Sift needs no lookup; the other two are read by the feed.

    A row from before the ledger existed carries no actor kind at all. It is reported as Sift with
    no pass, which is what the migration decided for it and is the honest reading: nothing recorded
    a user, and inventing one would be indistinguishable from a real answer.
    """
    kind = event.actor_kind or ACTOR_SIFT
    return LedgerActorView(
        kind=kind,
        id=event.actor_id,
        name=None if event.actor_id is None else names.get((kind, event.actor_id)),
    )


def _receipt(event: LedgerEvent, bench: Workbench) -> LedgerReceiptView | None:
    """The decision behind an event, where it was one.

    Told by the queue, which is `LEDGER_QUEUE` on everything that was not a judgement: a name no
    queue may claim, so the same lookup that offers an undo finds nothing for it.

    `final` is asked of the registry rather than stored on the row, which is the call the per-file
    history already makes (`_decision_events`): a queue that becomes reversible in a later version
    says so about the decisions it already wrote. A queue this build does not have is NOT final:
    the undo route refuses it with a sentence saying nothing here knows how, which is truer than a
    button that is greyed out as though the decision itself could not be taken back.
    """
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
    """What an undone decision's line says under it: its area's own sentence, else the one true of
    every undo. See `kernel.workbench.TakenBack`."""
    return queue.taken_back if isinstance(queue, TakenBack) else TAKEN_BACK


def _things_of(presses: Sequence[Press]) -> list[Thing]:
    """Every thing one page of presses names: each act's subjects and object, and what a folded
    press lists under its "Show each" (`_shown`)."""
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
    """Every thing one page names WITHOUT a name, gathered by kind, so each kind is asked once.

    A setting is left out: its name is its label and is read from the registry, which is a lookup
    with no database in it. See `_setting_label`.
    """
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
    """Everything that has happened in this library, newest first.

    Admin-only, like Activity and Logs beside it, and for the sharper reason: this is a picture of
    the whole installation (every user's acts, every pass, every file) and no narrowing of it
    would be a guest's own record. What a guest may be told about their own files is their file
    histories, which are a different address and are scoped by the file.

    Necessary and not sufficient. The read applies the vault to every event it hands back, because
    an admin can conceal things from themselves and a feed built for "an admin" rather than for THIS
    one would hand back what they hid.

    `kind` narrows to the events that named a thing of that kind and `verb` to one act. A word
    neither vocabulary knows matches nothing rather than being refused: the two lists live in the
    kernel, and a copy of them here would be a second opinion about what a verb is. `decisions`
    narrows to the acts a queue can take back: Organize's answers and Sift's own filings, each
    with its Undo, which is the whole record of what was decided and has no list of its own. A
    line there carries the picture its area draws for the decision (`still`), so the record can be
    checked and not only read.

    **No per-user "your year" here, and that is a decision rather than an omission.** A record of
    what the installation did and a story about what one person did are different surfaces with
    different audiences: this one is admin-only by its nature, and that one must not be. It gets
    its own address when it is built.
    """
    # ONE LINE PER PRESS: a task's thousands of filings are one line that opens to them, and the
    # pager counts lines (`history_feed.presses_recent`).
    presses, total = await presses_recent(
        database, viewer, limit=limit, offset=offset, kind=kind, verb=verb, decisions=decisions
    )
    found = [press.event for press in presses]
    things = _things_of(presses)
    present = await subjects_present(database, _named(things))
    # What the rows that wrote no name down are called NOW. One read per kind that needs one, and
    # none at all for a page whose rows all carry a snapshot. See `history_names.names_now`.
    called = await names_now(database, _nameless(things))
    called |= await own_filters(database, viewer, _nameless(things).get("saved_filter", []))
    names = await actor_names(database, _acted(found))
    usernames = await _username_hrefs(database, present)
    # How sure Sift was of each face match, as the file's own line says it. See `face_sures`.
    sures = await face_sures(database, _face_pairs(found))
    # What each stash-box press on the page filled in, every field with its values, as the thing's
    # own History says it. See `history_boxes.named_of_events`.
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
    """A filing Sift took off on a stash-box's answer names the box, on a line of one act. A folded
    line keeps "a stash-box": its acts may be several boxes'. See `history_removals.removals_named`."""
    return {
        one.id: one
        for one in await removals_named(
            database, [press.event for press in presses if press.folded == 1]
        )
    }


async def _reports_among(runs: Ledger, found: Sequence[LedgerEvent]) -> set[str]:
    """Which "ran" lines have a report: a pass over the library is a run on record; a task's own
    run line names the task under the same subject kind and has none, so it offers no Report."""
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
    """The picture each decision on the page is about, keyed by its line: the first one its area
    draws for the line's newest receipt that this viewer may see and that has been made.

    Asked of the queue that wrote it, because only it knows what its payload names (a file, a
    face, a folder's files): the shell learns nothing about any area, as the board's cards do not.
    Together, and once for the page: one read per decision on the server, none from the browser.
    A queue this build no longer has, or one that cannot answer, costs its line its picture and
    nothing else, because what was decided still happened.
    """
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
    """The page with every decision said in its area's own words.

    A `decided` event's stored title (`sentences.feed_line`) is the words of the day it was taken:
    "Kept your answer for Ada Byron", "A group of 90 faces discarded", "Created a Photo Set of 3
    pictures posted together": no doer, and names that may have changed since. So the feed asks
    the reader every History page asks (`kernel.access.worded`), which words a decision from what it
    recorded: one decision, one sentence, on every screen. A fold keeps its count after the line. A
    receipt its area cannot word keeps its title: an old row that recorded nothing else.

    A RECEIPT IS A DECISION WHATEVER ITS VERB. A face match is recorded as `linked` and a filing as
    `filed`, each with its decision card, and asking only the `decided` ones would leave
    those in the feed's own composed words beside a card that said them another way ("Sift filed
    x.jpg under ada" here, "... under ada on Instagram from its file name" on the card). Only a FOLD
    of one of them keeps the feed's own line: a task's press folds across the different things it
    acted on (`history_events._FOLD_KEY`), so "Sift filed 4,000 files under 7 usernames" is the
    press, and one receipt's words with a count after them would name one username for all seven. A
    `decided` press folds only over the same words, which is why its fold can take the count.
    """
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
    """Put back every decision a folded line of the feed stands for: its Undo all.

    The line is a PRESS (`history_feed.presses_recent`), which the narrowing it was drawn under
    decides, so the same `kind` and `verb` come back here and the press is read again the same
    way (`decisions` included): what is undone is exactly the acts that line said. Each goes through its own receipt's
    undo (`WorkbenchService.undo_each`), so one already undone is skipped and a queue that refuses
    one does not cost the rest. A line whose acts were not decisions has nothing to undo, and says so.
    """
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
