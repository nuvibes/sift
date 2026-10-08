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

A refusal a person can act on (a tunnel that cannot host, a token that has run out, no tunnel on
the guest's side, keys locked since a restart) answers 409 with the sentence to show, as it is.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import refused_here
from sift.kernel.content import LibraryStore
from sift.kernel.db import Database
from sift.kernel.seams import RecognitionSeam
from sift.kernel.tunnels import TUNNELS, TunnelStore
from sift.kernel.wiring import RECOGNITION, part_of
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.swap import device, refusal
from sift.slices.swap.models import (
    CodeAnswer,
    HeldFaces,
    JoinSwap,
    KeepFromSwaps,
    LeftOut,
    RefusedBy,
    SessionState,
    StartSwap,
    SwapDevice,
    SwapDirection,
    SwapJoined,
    SwapRefusal,
    SwapSession,
    SwapStarted,
    SwapTunnel,
    SwapWeight,
    TakeOffer,
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
from sift.slices.swap.token import SENTENCE

router = APIRouter(tags=["swap"])

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
    )
    return (first, back) if row.role == "host" else (back, first)


async def _session_view(sessions: SwapSessions, session_id: str) -> SwapSession:
    row = await sessions.row(session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    facts = sessions.facts(session_id)
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
        rate_bps=row.rate_bps if facts is None or facts.rate_bps is None else facts.rate_bps,
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
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """One session: its state, the code once there is one, the counts and the measured rate."""
    return await _session_view(sessions, session_id)


@router.post("/swap/sessions/{session_id}/code", dependencies=[Depends(csrf_protect)])
async def answer_code(
    session_id: str,
    body: CodeAnswer,
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
    return await _session_view(sessions, session_id)


@router.post("/swap/sessions/{session_id}/end", dependencies=[Depends(csrf_protect)])
async def end_swap(
    session_id: str,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """End the swap. The other side is told; what has arrived stays."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    await sessions.end(session_id)
    return await _session_view(sessions, session_id)


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
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SwapSession:
    """Take these: the answer to the other side's offer (the guest's, and the host's in a swap
    that sends and receives). What is taken starts arriving."""
    if await sessions.row(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such swap.")
    try:
        await sessions.take(
            session_id, Taken(skipped=frozenset(body.skipped), unticked=frozenset(body.unticked))
        )
    except SwapRefused as error:
        raise _refused(error) from None
    return await _session_view(sessions, session_id)


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
        files, size = sessions.weigh_answer(
            session_id, Taken(skipped=frozenset(body.skipped), unticked=frozenset(body.unticked))
        )
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
    )


@router.get("/swap/tunnels")
async def swap_tunnels(
    tunnels: Annotated[TunnelStore, Depends(_tunnels)],
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
