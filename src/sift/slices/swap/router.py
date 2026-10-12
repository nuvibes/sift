# SPDX-License-Identifier: AGPL-3.0-or-later
"""The swap's routes.

## The session's routes (at the top)

Start, Join, the session read, the code answered, End the swap, and the device id with its
reset.

## The screens' routes (at the foot)

"Do not swap" on a file, a person, a Site or a tag (read and set, `refusal.py`); the guest's answer to the offer screen (Take / Skip per person, and the files it unticked), without
which a guest waits at the offer until the session ends; the tunnels a swap can go through,
each with whether it can host one; and the face descriptions a swap brought for somebody already
here, which wait on that person's page until they are added. Every one is admin-only: a swap decides what of the library leaves this device, and a
guest session holds no master key to seal a device key with anyway. Every write is CSRF-checked.

## The manifest's and the receipt's routes (at the very foot)

A page of one group's files, a token read before Join (who it is from, until when; nothing is
dialled), Try now for a guest a tunnel cut off, the receipt of a swap that has ended, and its Undo.

A refusal a person can act on (a tunnel that cannot host, a token that has run out, no tunnel on
the guest's side, keys locked since a restart) answers 409 with the sentence to show, as it is.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import refused_here
from sift.kernel.content import LibraryStore
from sift.kernel.db import Database
from sift.kernel.seams import RecognitionSeam
from sift.kernel.tunnels import TUNNELS, TunnelStore
from sift.kernel.wiring import RECOGNITION, part_of
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.swap import device, receipt, refusal, router_probe
from sift.slices.swap.handshake import PASTED_FIELD
from sift.slices.swap.models import (
    CodeAnswer,
    GroupFiles,
    HeldFaces,
    JoinSwap,
    KeepFromSwaps,
    LeftOut,
    ReadToken,
    ReceiptFolder,
    RefusedBy,
    SessionState,
    StartSwap,
    SwapDevice,
    SwapDirection,
    SwapJoined,
    SwapReceipt,
    SwapRefusal,
    SwapSession,
    SwapStarted,
    SwapTunnel,
    SwapWeight,
    TakeOffer,
    TokenRead,
    WeighSwap,
    chosen_list,
)
from sift.slices.swap.session import (
    FOLDER_FIELD,
    NO_FOLDER,
    NO_TUNNEL,
    SESSIONS,
    TUNNEL_FIELD,
    LiveFacts,
    SwapRefused,
    SwapSessions,
    Taken,
)
from sift.slices.swap.store import SessionRow
from sift.slices.swap.token import SENTENCE, TokenRefused, parse

router = APIRouter(tags=["swap"])
router.include_router(router_probe.router)

# ================================================================================================
# THE SESSION'S ROUTES
# ================================================================================================


def _sessions(request: Request) -> SwapSessions:
    return part_of(request, SESSIONS)


def _refused(error: SwapRefused | device.DeviceLocked) -> HTTPException:
    if isinstance(error, device.DeviceLocked):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved keys are locked. Enter your password in the box at the top of this page to "
            "unlock them, then try again.",
        )
    # The part of the form it is about, so the form says it there (the `Sift-Field` header).
    return HTTPException(
        status.HTTP_409_CONFLICT,
        str(error),
        headers={"Sift-Field": error.field} if error.field else None,
    )


def _directions(
    row: SessionRow, facts: LiveFacts | None
) -> tuple[SwapDirection | None, SwapDirection | None]:
    """A swap that sends and receives, as this side's sending and receiving: the row's first
    figures are the host's to the guest, its `back_` figures the guest's to the host."""
    if not row.two_way:
        return None, None
    first = SwapDirection(
        offered_files=row.offered_files,
        wanted_files=row.wanted_files,
        files=row.sent_files,
        bytes=max(row.sent_bytes, 0 if facts is None else facts.moved_bytes),
        wanted_bytes=None if facts is None else facts.wanted_bytes,
        rate_bps=None if facts is None else facts.rate_bps,
        moving=facts is not None and facts.moving,
        received_files=None if facts is None or row.role == "host" else facts.received_files,
        streams=None if facts is None else facts.streams,
    )
    back = SwapDirection(
        offered_files=row.back_offered,
        wanted_files=row.back_wanted,
        files=row.back_files,
        bytes=max(row.back_bytes, 0 if facts is None else facts.back_moved_bytes),
        wanted_bytes=None if facts is None else facts.back_wanted_bytes,
        rate_bps=None if facts is None else facts.back_rate_bps,
        moving=facts is not None and facts.back_moving,
        received_files=None if facts is None or row.role != "host" else facts.back_received_files,
        streams=None if facts is None else facts.back_streams,
    )
    for one in (first, back):
        # Every file it wanted has crossed: counted by the sender as the receiver checks it, and
        # by the receiver as it checks it.
        crossed = one.files if one.received_files is None else one.received_files
        one.done = one.wanted_files > 0 and crossed >= one.wanted_files
    return (first, back) if row.role == "host" else (back, first)


def _rate(
    row: SessionRow,
    facts: LiveFacts | None,
    ways: tuple[SwapDirection | None, SwapDirection | None],
) -> int | None:
    """The session's rate: one way's, or in an exchange the sum of the directions still moving."""
    if not row.two_way:
        return row.rate_bps if facts is None or facts.rate_bps is None else facts.rate_bps
    moving = [one.rate_bps for one in ways if one is not None and one.moving and not one.done]
    return sum(rate for rate in moving if rate) or None


async def _tunnel_name(request: Request | None, tunnel_id: str | None) -> str | None:
    """A tunnel's name as Sites and Tunnels says it, or None for one that is gone."""
    if tunnel_id is None or request is None:
        return None
    return next(
        (view.name for view in await _tunnels(request).list() if view.id == tunnel_id), None
    )


async def _said_of_peer(
    request: Request | None, sessions: SwapSessions, row: SessionRow
) -> tuple[int | None, str | None]:
    """Earlier swaps with this device and the tunnel's name, read once a session and kept: they do
    not change while it runs, and the screen reads the session every second."""
    known = sessions.said.get(row.id)
    if known is not None:
        return known
    before = None
    if row.peer_device is not None:
        before = await sessions.store.swapped_before(row.peer_device, row.id)
    said = (before, await _tunnel_name(request, row.tunnel_id))
    if row.peer_device is not None and row.live:
        sessions.said[row.id] = said
    return said


async def _session_view(
    sessions: SwapSessions, session_id: str, request: Request | None = None
) -> SwapSession:
    row = await sessions.row(session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    facts = sessions.facts(session_id)
    extras = sessions.extras(session_id)
    before, tunnel_name = await _said_of_peer(request, sessions, row)
    sending, receiving = _directions(row, facts)
    return SwapSession(
        id=row.id,
        short_id=row.short_id,
        role="host" if row.role == "host" else "guest",
        # The table's CHECK is the same list, so a row cannot hold another state. A live row a
        # tunnel cut off is drawn as that, its step kept for when it is joined again.
        state="cut_off"
        if row.live and row.cut_off_at is not None
        else cast(SessionState, row.state),
        code=None if facts is None else facts.code,
        token=None if facts is None else facts.token,
        sentence=SENTENCE if facts is not None and facts.token else None,
        peer_device=row.peer_device,
        offered_files=row.offered_files,
        wanted_files=row.wanted_files,
        sent_files=row.sent_files,
        sent_bytes=max(row.sent_bytes, 0 if facts is None else facts.moved_bytes),
        wanted_bytes=None if facts is None else facts.wanted_bytes,
        rate_bps=_rate(row, facts, (sending, receiving)),
        unwanted_files=0 if facts is None else facts.unwanted,
        received_files=None if facts is None or row.role == "host" else facts.received_files,
        started_at=row.started_at,
        ended_at=row.ended_at,
        end_reason=row.end_reason,
        offer=None if facts is None else facts.screen,
        rejoin_until=row.token_expires if row.live and row.cut_off_at is not None else None,
        two_way=row.two_way,
        answered=facts is not None and facts.answered,
        sending=sending,
        receiving=receiving,
        manifest=None if extras is None else extras.manifest,
        now=None if extras is None else extras.now,
        streams=None if extras is None else extras.streams,
        moving_since=None if extras is None else extras.moving_since,
        token_expires=None if extras is None else extras.token_expires,
        swapped_before=before,
        tunnel_name=tunnel_name,
        refused=[] if extras is None else list(extras.refused),
        refused_count=0 if extras is None else extras.refused_count,
        kept_files=row.sent_files if extras is None or row.role == "host" else extras.kept_files,
        kept_bytes=0 if extras is None else extras.kept_bytes,
        retry_at=None if extras is None else extras.retry_at,
    )


@router.post("/swap/start", dependencies=[Depends(csrf_protect)])
async def start_swap(
    body: StartSwap,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> SwapStarted:
    """Start a swap: restart the chosen tunnel with a listener, and answer the token. A swap
    that sends and receives names the folder of this library its received files go in."""
    try:
        chosen = chosen_list([one.model_dump() for one in body.chosen])
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if body.two_way and (
        body.dest_folder_id is None or await library.get_folder(body.dest_folder_id) is None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            NO_FOLDER,
            headers={"Sift-Field": FOLDER_FIELD},
        )
    try:
        started = await sessions.start(
            viewer=viewer,
            chosen=chosen,
            tunnel_id=body.tunnel_id,
            share_boxes=body.share_boxes,
            master_key=key,
            two_way=body.two_way,
            dest_folder_id=body.dest_folder_id if body.two_way else None,
        )
    except SwapRefused as error:
        raise _refused(error) from None
    return SwapStarted(
        session_id=started.session_id,
        token=started.token.text,
        sentence=SENTENCE,
        expires=started.token.expires,
    )


@router.post("/swap/join", dependencies=[Depends(csrf_protect)])
async def join_swap(
    body: JoinSwap,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> SwapJoined:
    """Join a swap with a pasted token, into a folder of this library, through the tunnel
    chosen beside Join."""
    if await library.get_folder(body.dest_folder_id) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Choose a folder to put received files in.",
            headers={"Sift-Field": "dest_folder_id"},
        )
    # A tunnel removed since the chooser was drawn: said as a missing one is, beside the chooser.
    if body.tunnel_id is not None and body.tunnel_id not in {
        view.id for view in await _tunnels(request).list()
    }:
        raise HTTPException(
            status.HTTP_409_CONFLICT, NO_TUNNEL, headers={"Sift-Field": TUNNEL_FIELD}
        )
    try:
        session_id = await sessions.join(
            viewer_id=viewer.id,
            token_text=body.token,
            dest_folder_id=body.dest_folder_id,
            master_key=key,
            tunnel_id=body.tunnel_id,
        )
    except SwapRefused as error:
        raise _refused(error) from None
    return SwapJoined(session_id=session_id)


@router.get("/swap/sessions/{session_id}")
async def swap_session(
    session_id: str,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """One session: its state, the code once there is one, the counts and the measured rate, and
    while it runs, its manifest and the file crossing now."""
    return await _session_view(sessions, session_id, request)


@router.post("/swap/sessions/{session_id}/code", dependencies=[Depends(csrf_protect)])
async def answer_code(
    session_id: str,
    body: CodeAnswer,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """They match / They don't match. On the host, They match releases the offer; on the guest
    of a swap that sends and receives, the guest's own offer, of what the body chose."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    try:
        chosen = chosen_list([one.model_dump() for one in body.chosen]) if body.chosen else ()
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    try:
        await sessions.answer_code(
            session_id, body.match, viewer=viewer, chosen=chosen, share_boxes=body.share_boxes
        )
    except SwapRefused as error:
        raise _refused(error) from None
    return await _session_view(sessions, session_id, request)


@router.post("/swap/sessions/{session_id}/end", dependencies=[Depends(csrf_protect)])
async def end_swap(
    session_id: str,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """End the swap. The other side is told; what has arrived stays."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    await sessions.end(session_id)
    return await _session_view(sessions, session_id, request)


@router.get("/swap/device")
async def swap_device(
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> SwapDevice:
    """This install's device id, made the first time it is asked for when the keys are open."""
    known = await device.device_id(sessions.store)
    if known is not None:
        return SwapDevice(device_id=known, locked=key is None)
    if key is None:
        return SwapDevice(device_id=None, locked=True)
    return SwapDevice(device_id=await device.ensure_device(sessions.store, sessions.secrets, key))


@router.post("/swap/device/reset", dependencies=[Depends(csrf_protect)])
async def reset_swap_device(
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> SwapDevice:
    """Reset device id: a new key and a new id. Refused while a swap is running."""
    if sessions.any_live():
        raise HTTPException(
            status.HTTP_409_CONFLICT, "End the swap first, then reset the device id."
        )
    try:
        return SwapDevice(device_id=await device.reset(sessions.store, sessions.secrets, key))
    except device.DeviceLocked as error:
        raise _refused(error) from None


# ================================================================================================
# THE SCREENS' ROUTES
# ================================================================================================


def _tunnels(request: Request) -> TunnelStore:
    return part_of(request, TUNNELS)


def _refusal_kind(subject: str) -> refusal.Kind:
    """One of the four kinds that can be kept out of swaps, or a 404."""
    for kind in refusal.KINDS:
        if kind == subject:
            return kind
    raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")


async def _refusal_view(
    access: Repository, database: Database, viewer: Viewer, kind: refusal.Kind, local_id: str
) -> SwapRefusal:
    here, out, why = await refusal.state_of(database, kind, local_id)
    by = await refusal.refused_by(access, database, viewer, kind, local_id) if out else []
    return SwapRefusal(
        subject=kind,
        id=local_id,
        kept_out_here=here,
        kept_out=out,
        why=why,
        kept_local_here=await refused_here(database, "enrich", kind, local_id),
        by=[
            RefusedBy(kind=above, id=one, name=name, mark="local" if local else "swap")
            for above, one, name, local in by
        ],
    )


@router.get("/swap/keep-out/{subject}/{local_id}")
async def kept_from_swaps(
    subject: str,
    local_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapRefusal:
    """Where this stands with swaps, without changing it: what a menu row reads to say "Don't swap"
    or "Allow swapping" before it is pressed. The same answer the PUT gives back, and the same 404
    for a kind nothing is refused for or a thing that is not there."""
    kind = _refusal_kind(subject)
    if await refusal.name_for(access, viewer, kind, local_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    return await _refusal_view(access, database, viewer, kind, local_id)


@router.put("/swap/keep-out/{subject}/{local_id}", dependencies=[Depends(csrf_protect)])
async def keep_from_swaps(
    subject: str,
    local_id: str,
    body: KeepFromSwaps,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapRefusal:
    """Keep this out of every swap, or let it back in. An admin's, like every swap route: the mark
    decides what may leave this device, for every user of it. A PUT: the same statement however
    many times it is made."""
    kind = _refusal_kind(subject)
    name = await refusal.name_for(access, viewer, kind, local_id)
    if name is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    if not await refusal.set_kept_from_swaps(
        database, viewer, kind, local_id, body.kept_out, name=name or None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    return await _refusal_view(access, database, viewer, kind, local_id)


@router.post("/swap/sessions/{session_id}/take", dependencies=[Depends(csrf_protect)])
async def take_offer(
    session_id: str,
    body: TakeOffer,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """Take these: the answer to the other side's offer (the guest's, and the host's in a swap
    that sends and receives). What is taken starts arriving."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    try:
        await sessions.take(session_id, _taken(sessions, session_id, body))
    except SwapRefused as error:
        raise _refused(error) from None
    return await _session_view(sessions, session_id, request)


def _taken(sessions: SwapSessions, session_id: str, body: TakeOffer) -> Taken:
    """The answer as the session takes it, whole groups skipped included; 422 for a group the
    offer does not have."""
    taken = Taken(skipped=frozenset(body.skipped), unticked=frozenset(body.unticked))
    try:
        return sessions.with_groups(session_id, taken, body.skipped_groups)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None


@router.post("/swap/sessions/{session_id}/weigh", dependencies=[Depends(csrf_protect)])
async def weigh_answer(
    session_id: str,
    body: TakeOffer,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapWeight:
    """What this answer to the offer would bring, before Take these is pressed: the files and the
    bytes, by the same rule the answer itself is made by. Changes nothing."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    try:
        files, size = sessions.weigh_answer(session_id, _taken(sessions, session_id, body))
    except SwapRefused as error:
        raise _refused(error) from None
    return SwapWeight(files=files, bytes=size)


@router.post("/swap/weigh", dependencies=[Depends(csrf_protect)])
async def weigh_picks(
    body: WeighSwap,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapWeight:
    """What the picks would offer, as files and bytes, before Start: read as this admin with
    Hidden open, through the same read the offer makes, so the figure is the offer's. A POST
    because the picks are a body (up to `MAX_CHOSEN` of them); it changes nothing."""
    try:
        chosen = chosen_list([one.model_dump() for one in body.chosen])
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    weight = await sessions.weigh(viewer, chosen)
    return SwapWeight(
        files=weight.files,
        bytes=weight.bytes,
        left_out=[
            LeftOut(kind=one.kind, id=one.id, name=one.name, mark=one.mark, files=one.files)
            for one in weight.left_out
        ],
        left_out_other=weight.left_out_other,
        groups=list(weight.groups),
        learns=weight.learns,
    )


@router.get("/swap/tunnels")
async def swap_tunnels(
    tunnels: Annotated[TunnelStore, Depends(_tunnels)],
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[SwapTunnel]:
    """Every tunnel, with whether it can host a swap: yes, no, or not tried yet.

    Every tunnel rather than only the ones that can: whether one can is learned by starting a swap
    on it, so a tunnel nobody has tried is a real choice and the screen marks it as untried."""
    return [
        SwapTunnel(
            id=view.id,
            name=view.name,
            can_host=view.can_host,
            # Only while it is up, as Sites and Tunnels says it: the server a tunnel that is not
            # connected was last on is not where a swap would go.
            endpoint=view.endpoint if view.up else None,
            rate_bps=await sessions.store.last_rate(view.id),
        )
        for view in await tunnels.list()
    ]


def _recognition(request: Request) -> RecognitionSeam:
    return part_of(request, RECOGNITION)


async def _person_named(access: Repository, viewer: Viewer, person_id: str) -> str:
    """Their name, or a 404 where this viewer may not see them."""
    person = await access.visible_person(viewer, person_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such person.")
    return person.name


@router.get("/swap/people/{person_id}/held-faces")
async def held_faces(
    person_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    recognition: Annotated[RecognitionSeam, Depends(_recognition)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> HeldFaces:
    """How many face descriptions swaps brought for this person that are waiting to be added.

    Read-only: the page offers them, and nothing is added until the offer is taken. Zero with
    recognition off, which is also the ordinary answer."""
    name = await _person_named(access, viewer, person_id)
    return HeldFaces(waiting=await recognition.held_for(person_id, name))


@router.post("/swap/people/{person_id}/held-faces", dependencies=[Depends(csrf_protect)])
async def add_held_faces(
    person_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    recognition: Annotated[RecognitionSeam, Depends(_recognition)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> HeldFaces:
    """Add them: what was held becomes this person's references, and the library's faces are
    matched against them. Pressing it again adds nothing, because a claimed description is spent."""
    name = await _person_named(access, viewer, person_id)
    added = await recognition.claim_for(person_id, name)
    return HeldFaces(waiting=await recognition.held_for(person_id, name), added=added)


# ================================================================================================
# THE MANIFEST'S AND THE RECEIPT'S ROUTES
# ================================================================================================

#: The most files one page of a group holds.
GROUP_PAGE_MOST = 200


@router.get("/swap/sessions/{session_id}/groups/{key}/files")
async def group_files(
    session_id: str,
    key: str,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=GROUP_PAGE_MOST)] = 100,
) -> GroupFiles:
    """A page of one group's files, in the offer's order: what a group row folds open to past
    the first few the session view carries."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    try:
        files, total = sessions.group_files(session_id, key, offset=offset, limit=limit)
    except SwapRefused as error:
        raise _refused(error) from None
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That group isn't in the offer.") from None
    return GroupFiles(files=files, total=total)


@router.post("/swap/token/read", dependencies=[Depends(csrf_protect)])
async def read_token(
    body: ReadToken,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> TokenRead:
    """Who a pasted token is from and until when it is good, before Join: nothing is dialled, and
    the address inside it is never said."""
    try:
        token = parse(body.token, sessions.now())
    except TokenRefused as error:
        raise _refused(SwapRefused(str(error), field=PASTED_FIELD)) from None
    return TokenRead(
        device_id=token.host_device,
        expires=token.expires,
        swapped_before=await sessions.store.swapped_before(token.host_device, ""),
    )


@router.post("/swap/sessions/{session_id}/retry", dependencies=[Depends(csrf_protect)])
async def retry_swap(
    session_id: str,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """Try now: the guest of a swap a tunnel cut off dials again immediately."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    try:
        sessions.try_now(session_id)
    except SwapRefused as error:
        raise _refused(error) from None
    return await _session_view(sessions, session_id, request)


async def _receipt_view(
    request: Request, sessions: SwapSessions, library: LibraryStore, session_id: str
) -> SwapReceipt:
    row = await sessions.row(session_id)
    if row is None or row.live:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This swap has no receipt yet.")
    kept = await receipt.read(sessions.store.database, session_id)
    folder = None if row.folder_id is None else await library.get_folder(row.folder_id)
    return receipt.view(
        row,
        kept,
        folder=None
        if folder is None
        else ReceiptFolder(id=folder.id, path=folder.rel_path or folder.name),
        tunnel=await _tunnel_name(request, row.tunnel_id),
    )


@router.get("/swap/receipts/{session_id}")
async def swap_receipt(
    session_id: str,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapReceipt:
    """A swap that has ended, signed: what landed or was sent per group and how it was filed, where,
    how long, how fast, and whether it was undone. What History opens for a swap."""
    return await _receipt_view(request, sessions, library, session_id)


def _remover(request: Request) -> receipt.Remover:
    return part_of(request, receipt.REMOVER)


@router.post("/swap/receipts/{session_id}/undo", dependencies=[Depends(csrf_protect)])
async def undo_swap(
    session_id: str,
    request: Request,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    access: Annotated[Repository, Depends(wiring.access)],
    remover: Annotated[receipt.Remover, Depends(_remover)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapReceipt:
    """Undo this swap: every file it landed goes from the library, and from the disk since they
    sit in the folder the swap made; then the People, Sites and Usernames it made that nothing
    else holds. Once, unless files were in use: then again for those."""
    shown = await _receipt_view(request, sessions, library, session_id)
    if shown.undo is not None and shown.undo.in_use == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "This swap was undone already.")
    if session_id in sessions.undoing:
        raise HTTPException(status.HTTP_409_CONFLICT, "This swap is being undone.")
    if not shown.can_undo:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Nothing this swap brought is here to take back."
        )
    row = await sessions.row(session_id)
    if row is None:  # pragma: no cover (read just above)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    sessions.undoing.add(session_id)
    try:
        undone = await receipt.undo(
            row,
            database=sessions.store.database,
            library=library,
            access=access,
            remover=remover,
            actor=viewer,
        )
        await receipt.mark_undone(
            sessions.store.database,
            session_id,
            receipt.undone_of(row, shown.undo, undone, sessions.now()),
        )
    finally:
        sessions.undoing.discard(session_id)
    return await _receipt_view(request, sessions, library, session_id)
