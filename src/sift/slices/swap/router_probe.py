# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnel speed probe's routes: host a sink, read it, stop it, send to one."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel.tunnels import TunnelError
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.swap import lock
from sift.slices.swap.models import ProbeHost, ProbeHosted, ProbeReading, ProbeSend, ProbeSent
from sift.slices.swap.session import SESSIONS, SwapSessions

router = APIRouter(tags=["swap"])

LOCKED = "Your saved keys are locked. Enter your password at the top of this page, then try again."
NO_SINK = "No probe is hosted on this tunnel."
UNREACHED = "The other device's sink couldn't be reached through this tunnel."


def _sessions(request: Request) -> SwapSessions:
    return part_of(request, SESSIONS)


@router.post("/swap/tunnels/{tunnel_id}/probe", dependencies=[Depends(csrf_protect)])
async def host_probe(
    tunnel_id: str,
    body: ProbeHost,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    _viewer: Annotated[object, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> ProbeHosted:
    """Host a sink behind this tunnel's mapped port for `seconds`; answer where to send."""
    if key is None:
        raise HTTPException(status.HTTP_409_CONFLICT, LOCKED)
    try:
        sink = await sessions.probes.host(tunnel_id, key, body.seconds)
    except TunnelError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    return ProbeHosted(
        address=sink.address,
        port=sink.external_port,
        secret=sink.secret.hex(),
        seconds=sink.seconds,
    )


@router.get("/swap/tunnels/{tunnel_id}/probe")
async def read_probe(
    tunnel_id: str,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    _viewer: Annotated[object, Depends(require_admin)],
) -> ProbeReading:
    sink = sessions.probes.reading(tunnel_id)
    if sink is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SINK)
    seconds = None
    if sink.first_byte_at is not None and sink.last_byte_at is not None:
        seconds = round(sink.last_byte_at - sink.first_byte_at, 3)
    return ProbeReading(
        received=sink.received,
        connections=sink.connections,
        refused=sink.refused,
        seconds=seconds,
        rate_bps=sink.rate_bps,
        done=sink.done,
    )


@router.delete(
    "/swap/tunnels/{tunnel_id}/probe",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def stop_probe(
    tunnel_id: str,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    _viewer: Annotated[object, Depends(require_admin)],
) -> None:
    await sessions.probes.stop(tunnel_id)


@router.post("/swap/tunnels/{tunnel_id}/probe/send", dependencies=[Depends(csrf_protect)])
async def send_probe(
    tunnel_id: str,
    body: ProbeSend,
    sessions: Annotated[SwapSessions, Depends(_sessions)],
    _viewer: Annotated[object, Depends(require_admin)],
) -> ProbeSent:
    """Push bytes to a sink the other device hosts, through this tunnel, on `streams`."""
    try:
        secret = bytes.fromhex(body.secret)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The secret isn't hex."
        ) from None
    try:
        sent = await sessions.probes.send(
            tunnel_id, body.address, body.port, secret, body.seconds, body.streams
        )
    except (lock.LockFailed, TunnelError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    if sent.failed == sent.streams:
        raise HTTPException(status.HTTP_409_CONFLICT, UNREACHED)
    return ProbeSent(
        sent=sent.sent,
        seconds=round(sent.seconds, 3),
        streams=sent.streams,
        failed=sent.failed,
        rate_bps=sent.rate_bps,
    )
