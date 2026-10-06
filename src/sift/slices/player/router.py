# SPDX-License-Identifier: AGPL-3.0-or-later
"""The playback endpoints, and the most careful permission code in the application.

These are the routes that return the actual content. Everything else in Sift returns a description
of a file; these return the file. That makes them the easiest place in the whole application to
leak, and the place where an access mistake is not a disclosure of metadata but a disclosure of
the media itself.

Three rules hold on every route here, without exception:

1. **Nothing is resolved without a viewer.** Every path comes from `Repository.locate` or
   `locate_derivative`, which apply the permission scope and the concealment rule inside their
   SQL.
   No route here builds a path from an asset id by itself, because a path built outside the access
   layer is a path nobody checked.
2. **Denied and missing are the same answer.** A 403 on an asset a guest was never shown *confirms
   the asset exists*, which for a concealed file is the entire thing being protected.
   Both come back as the same 404, from the same helper, with the same body.
3. **Nothing is cached anywhere but the browser that asked.** `private, no-cache` on every byte-
   returning response. A shared cache in front of these routes would answer from storage without
   re-running the permission check, and `no-cache` (which means "revalidate", not "do not store")
   is what stops the segments of something hidden again staying readable from the local
   browser cache.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, cast, get_args

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field
from starlette.background import BackgroundTask

from sift.kernel import places, wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.attention import PLAYED
from sift.kernel.client import Client, client_of
from sift.kernel.content import Asset, ContentStore, DerivativeKind, UserStateStore, VerdictProduct
from sift.kernel.content.user_state import HEAT_BUCKETS, resume_minimum_ms, resume_point
from sift.kernel.db import Database
from sift.kernel.http import is_local_request
from sift.kernel.jobs import WAITED_ON_PRIORITY
from sift.kernel.log import get_logger
from sift.kernel.mp4 import NEEDS_REPAIR_BYTES
from sift.kernel.numbers import as_int
from sift.kernel.reach import refuse_one
from sift.kernel.serving import keeps, serve_file
from sift.kernel.threads import on_serving_thread
from sift.kernel.use_history import keeps_history
from sift.kernel.wire import Wire
from sift.kernel.wiring import HARDWARE, QUEUE, SETTINGS, SETTINGS_HUB, part_of
from sift.slices import media_jobs
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.player import policy, tuning
from sift.slices.player.about import about_of
from sift.slices.player.plays import (
    MOST_SEEKS,
    MOST_SPEEDS,
    Inside,
    Kind,
    OpenedFrom,
    Place,
    Screen,
    record_play,
    sitting_is_here,
)
from sift.slices.player.service import (
    SERVICE,
    PlayerService,
    SegmentUnavailable,
    byte_range,
    capped,
    master_playlist,
    plan_query,
    playlist,
    read_range,
    rung_query,
)

log = get_logger(__name__)

router = APIRouter(tags=["player"])

#: See the module docstring, rule 3. Identical to the header browse puts on derivatives, and for
#: the same reason.
_CACHE_PRIVATE = {"Cache-Control": "private, no-cache"}


def _player(request: Request) -> PlayerService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    """The one answer for "no such asset" and "not yours". See rule 2."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def _why_the_copy(asset: Asset) -> policy.Copy:
    """Which of the two problems this file's second copy was written for.

    Both write the same derivative kind, so the row cannot say. What can say is the file: an audio
    gap wide enough to need repairing is a measurement stored on it, compared against the one
    number the repair job and the browse screen also compare against.

    Read off the STORED asset, never the served one: `as_served` has already rewritten the
    container by the time the plan is decided, so the served asset cannot tell the two apart.
    """
    gap = asset.interleave_gap
    if gap is not None and gap >= NEEDS_REPAIR_BYTES:
        return policy.Copy.REPAIRED
    return policy.Copy.REPACKAGED


async def _open(access: Repository, viewer: Viewer, asset_id: str) -> Asset:
    """The asset, if this viewer may actually watch it. 404 otherwise, either way."""
    asset = await access.open_asset(viewer, asset_id)
    if asset is None:
        raise _missing()
    return asset


# --- The plan ---------------------------------------------------------------------------------


class Capabilities(Wire):
    """What the browser reported it can decode and read.

    Sent by the client because the client is the only party that knows. A server-side guess from
    the user-agent string is wrong for somebody: an iPhone 15 Pro and an iPhone 14 give different
    answers about AV1, and the same Chrome build plays HEVC on a machine with a hardware decoder
    and not on one without.
    """

    video_codecs: list[str] = Field(default_factory=list, max_length=32)
    #: The codecs the browser decodes at TEN bits a sample as well. A browser answers HEVC Main
    #: and HEVC Main 10 as two different questions, and a decoder that takes only the first is
    #: handed a 10-bit file as if it could play it: a black stage with no sentence. Empty from a
    #: client that has not asked, which reads as "eight bits only" and converts, the safe way round.
    video_codecs_10bit: list[str] = Field(default_factory=list, max_length=32)
    audio_codecs: list[str] = Field(default_factory=list, max_length=32)
    containers: list[str] = Field(default_factory=list, max_length=32)


class PlaybackPlan(Wire):
    """How this file will be played, and what the player should say about it."""

    route: str
    reason: str
    #: Which of the two second copies this is, when a copy is what is being played.
    #:
    #: The route says `remux` for both, because one derivative kind covers both: a container this
    #: browser cannot read, and a file whose audio sits too far from its video. A screen with room
    #: for one word needs to know which, or every repaired file is described as repackaged.
    copy_kind: str | None = None
    url: str
    scale_height: int | None = None
    projected_realtime: float | None = None
    streamable: bool = True
    duration_ms: int | None = None
    #: Where to start, in milliseconds, or None for the beginning. The plan already answers "how
    #: should this be played", and "start here" is the same kind of answer, so it arrives with
    #: the rest rather than costing the player a second question before it can begin.
    resume_ms: int | None = None
    #: How long this file has to be on screen before the sitting counts as a view.
    #:
    #: Here for the same reason `resume_ms` is: the plan is already the answer to "how should this
    #: be played", and "tell me when it has been watched enough to count" is the same kind of
    #: answer. Without it a player can only report on the way out, so a number on a tile changes
    #: when a window closes rather than when the thing it counts actually happened.
    #:
    #: The rule is NOT moved to the browser by sending this. What travels is one integer for one
    #: file, computed by the same function that judges the sitting when it is reported, so a
    #: client cannot disagree with the server, and a client that ignores this is simply counted
    #: later, when it reports on the way out. See `policy.watch_needed`.
    view_at_ms: int = 0

    #: Every size this file can be watched at, in the order the menu shows them.
    #:
    #: Sent with the plan rather than fetched when the menu opens, because it is the same question
    #: `/playback` already answered ("how can this be played"), and asking twice would mean the
    #: menu could disagree with the picture behind it.
    qualities: list[Quality] = Field(default=[])


class Quality(Wire):
    """One entry in the quality menu."""

    label: str
    """What the person reads. "Auto", "1080p", or the file's own size."""

    height: int | None = None
    """The height this asks for, or None for the file exactly as it is."""

    url: str
    """Where to attach for this choice. A different address, not a parameter on the same one."""

    auto: bool = False
    """True for the entry that lets the player choose, and change its mind while watching."""

    smooth: bool = True
    """False where this machine is not expected to keep up. Offered anyway, and marked.

    The person is entitled to overrule the arithmetic (the same judgement the transcode plan
    already makes), but they should be able to see which choice is the risky one before they make
    it rather than by watching it stutter."""

    detail: str | None = None
    """What this entry actually is, beside the name. Only the file's own entry carries one.

    "Original" is the right NAME for the file and a poor description of it: on a 4K video, 4K is
    on the menu as the top entry, under a name that says nothing about size. There is no rung at
    the file's own height and there should not be (re-encoding a file at the size it already is
    looks worse for no reason), so the answer is to say what "Original" comes to rather than to add
    a rung.

    Named by the SHORT side, which is how everybody names a video: a phone clip stored 1080 wide and
    1920 tall is a 1080p video, and naming it by its height would make it "1920p" sitting above a
    rung called "1080p" that is the same 1080 across, which this field must never do."""


@router.post("/assets/{asset_id}/playback", dependencies=[Depends(csrf_protect)])
async def plan_playback(
    asset_id: str,
    capabilities: Capabilities,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
    player: Annotated[PlayerService, Depends(_player)],
    content: Annotated[ContentStore, Depends(wiring.content)],
) -> PlaybackPlan:
    """Decide how this browser should play this file.

    A POST rather than a GET because it carries a body and because its answer is specific to the
    client that asked: there is nothing here a shared cache should ever hold. It carries the CSRF
    token like every other unsafe method, so the SameSite cookie is not the only thing standing
    between a cross-site page and it.
    """
    asset = await _open(access, viewer, asset_id)
    # Decided from the file that will actually be sent rather than from the row. They differ for
    # exactly one kind of file (see `policy.as_served`).
    repaired = await access.locate_derivative(viewer, asset.id, DerivativeKind.REMUX)
    # NOWHERE TO READ IT FROM (every copy is on a drive that is not there) is the answer
    # `stream` gives, and it is given here first. A plan naming an address that will refuse it
    # would send a Theater cell to attach, fail and hold still on "couldn't be played"; a 404 is
    # what a cell steps over, and what the player says "not found" to.
    if repaired is None and await access.locate(viewer, asset.id) is None:
        raise _missing()

    client = policy.ClientCapabilities(
        video_codecs=frozenset(c.lower() for c in capabilities.video_codecs),
        video_codecs_10bit=frozenset(c.lower() for c in capabilities.video_codecs_10bit),
        audio_codecs=frozenset(c.lower() for c in capabilities.audio_codecs),
        containers=frozenset(c.lower() for c in capabilities.containers),
    )
    settings = part_of(request, SETTINGS)
    hardware = part_of(request, HARDWARE)
    max_height = await part_of(request, SETTINGS_HUB).get_app(MAX_TRANSCODE_HEIGHT_KEY)

    served = policy.as_served(asset, repaired=repaired is not None)

    # THE REMUX TIER. A browser that can decode both streams but cannot read the box they are in
    # gets a repackaged copy built once, in the background; this viewing still converts.
    #
    # Asked of the STORED file rather than the served one: a file that already has a copy is not a
    # file that needs one, and `as_served` has already rewritten the container by this point.
    if repaired is None and policy.needs_repackaging(asset, client):
        await _repackage(request, asset.id)

    copy = _why_the_copy(asset) if repaired is not None else None
    plan = policy.decide(
        served,
        client,
        max_height=int(max_height),
        cpu_count=hardware.cpu_count,
        encoder_rate=player.encoder_rate,
        copy=copy,
        stored_container=asset.container,
    )
    del settings  # the plan needs none of it; kept explicit so the dependency is not re-added

    # A file nobody has read is read now, ahead of the queue: somebody pressed play on it,
    # unless the read has already been tried and given up on, in which case the honest answer
    # is why, and nothing is queued again.
    if plan.route is policy.Route.UNREAD:
        given_up = await content.verdict_of(asset.id, VerdictProduct.PROBE)
        if given_up is not None and not given_up.transient:
            plan = replace(plan, reason=f"Sift could not read this file: {given_up.reason}")
        else:
            await _read(request, asset.id)

    base = f"/api/assets/{asset_id}"
    # Both cheap routes are the same request: bytes off the disk, over ranges. They differ only in
    # WHICH file, and that choice is `stream`'s own: it prefers the repackaged copy already. An
    # unread file names the same address and nothing opens it until the read has landed.
    if plan.route in (policy.Route.DIRECT, policy.Route.REMUX, policy.Route.UNREAD):
        url = f"{base}/stream"
    else:
        # The decision travels in the address. Nothing is held between requests, so a playlist URL
        # without it is a playlist whose segments get re-decided from scratch, and the default is
        # a full-size transcode, which is neither of the two cheaper answers this may have picked.
        url = f"{base}/hls/index.m3u8{plan_query(plan)}"

    # Put through the same rule that decided whether to store it. A stored position is not
    # automatically one to act on: the user's minimum can have been raised since, and the answer
    # somebody expects from raising it is that short videos stop resuming NOW, not from their next
    # sitting onwards.
    state = await user_state.state_of(asset_id, viewer.id)
    resume_ms = await _resume_where(request, viewer.id, asset, state.resume_ms)

    return PlaybackPlan(
        route=plan.route.value,
        reason=plan.reason,
        copy_kind=copy.value if copy is not None else None,
        url=url,
        scale_height=plan.scale_height,
        projected_realtime=plan.projected_realtime,
        streamable=plan.streamable,
        duration_ms=asset.duration_ms,
        resume_ms=resume_ms,
        view_at_ms=policy.watch_needed(asset.media_type, asset.duration_ms),
        qualities=_qualities(
            asset,
            plan,
            base=base,
            default_url=url,
            cpu_count=hardware.cpu_count,
            encoder_rate=player.encoder_rate,
        ),
    )


def _qualities(
    asset: Asset,
    plan: policy.Plan,
    *,
    base: str,
    default_url: str,
    cpu_count: int,
    encoder_rate: float | None,
) -> list[Quality]:
    """The menu: how this file can be watched, biggest choice first.

    A photograph has no menu at all. Neither does a video with nothing under it: a 480p clip has
    no rung below 480p worth making, and a menu with one entry is a control that cannot do
    anything, which is worse than no control.

    **Auto is only offered where there is something to be automatic about.** It appears once the
    file is already being converted, because that is when the ladder exists as several variants a
    player can move between. On a file playing untouched there is nothing to switch to without
    starting a conversion, and doing that on its own (without being asked) is exactly the
    behaviour the free path exists to avoid.
    """
    if asset.media_type != "video":
        return []
    ladder = policy.rungs(asset.width, asset.height)
    if not ladder:
        return []

    entries: list[Quality] = []
    if plan.route is not policy.Route.DIRECT:
        entries.append(Quality(label="Auto", height=None, url=f"{base}/hls/master.m3u8", auto=True))

    # The file as it is. Whatever `/playback` decided is how it gets there (served off the disk,
    # copied, or converted at its own size), so this entry is the plan already made, named.
    #
    # Unless the plan REDUCED it. A plan that downscaled to keep playback smooth is not the file
    # as it is, and handing its address to the entry called "Original" would make that entry on a
    # 4K file play the 1080p rendition on any machine below the realtime line. So a reduced plan's
    # entry is the full-size conversion, with its own projection, and the reduced picture is the entry the
    # player opens on, under the name of its size.
    reduced = plan.scale_height is not None
    native = policy.projected_realtime(
        width=asset.width,
        height=asset.height,
        fps=asset.fps,
        vcodec=(asset.vcodec or "").lower(),
        cpu_count=cpu_count,
        encoder_rate=encoder_rate,
    )
    entries.append(
        Quality(
            # "Original" rather than the height, and that is not a cosmetic choice. The rungs are
            # named by height, which is the right name for a rung and the WRONG name for the file:
            # a phone video stored 1080 wide and 1920 tall would come out "1920p", sitting above
            # a rung called "1080p" that is in fact the same 1080 across. Every way of naming the
            # source by a number is wrong for some shape of file, and it does not need one: it is
            # the file, and the stats panel says how big it is for anyone who wants to know.
            label="Original",
            height=None,
            url=f"{base}/hls/index.m3u8{rung_query(None)}" if reduced else default_url,
            smooth=(native is None or native >= tuning.MIN_REALTIME_RATIO)
            if reduced
            else plan.streamable,
            detail=policy.size_name(asset.width, asset.height),
        )
    )
    if reduced and all(rung.height != plan.scale_height for rung in ladder):
        # The reduced picture matches no rung (its height is the ceiling as a height, and a
        # portrait ladder is keyed by the short side), so it is named for what it comes to.
        across = policy.scaled_width(asset.width, asset.height, plan.scale_height or 0)
        entries.append(
            Quality(
                label=f"{min(across or 0, plan.scale_height or 0)}p",
                height=plan.scale_height,
                url=default_url,
                smooth=plan.streamable,
            )
        )

    for rung in ladder:
        ratio = policy.projected_realtime(
            width=asset.width,
            height=asset.height,
            fps=asset.fps,
            vcodec=(asset.vcodec or "").lower(),
            cpu_count=cpu_count,
            scale_height=rung.height,
            encoder_rate=encoder_rate,
        )
        entries.append(
            Quality(
                # NAMED by the short side and ASKED FOR by the height. The two are the same number
                # for a landscape file and are not for a portrait one, where naming a rung by its
                # height would produce "2160p" for a picture 1215 across, reading as 4K, directly
                # under the file itself, which really is 4K.
                label=f"{rung.short}p",
                height=rung.height,
                url=f"{base}/hls/index.m3u8{rung_query(rung.height)}",
                smooth=ratio is None or ratio >= tuning.MIN_REALTIME_RATIO,
            )
        )
    return entries


async def _read(request: Request, asset_id: str) -> None:
    """Ask for this file to be read, at the priority of somebody waiting for it.

    Fire and forget, like `_repackage`: the answer this request is building says the file has not
    been read, and the read is what turns that answer into a plan. A failure to enqueue is logged,
    because the scan or the catch-up pass at start will read the file anyway.

    ASKED WHETHER OR NOT A READ IS ALREADY COMING. Standing down when one was coming looks like the
    obvious saving and is a silent wait: a scan hands out a read per file at the ordinary priority,
    so a file the scan has already reached has a read coming, somewhere behind every other file in
    the scan, possibly thousands of jobs down the queue.

    `dedupe` is what makes asking again cheap rather than duplicative. It collapses onto the read
    already waiting and RAISES that row to this priority, so the row that runs is the one job both
    askers wanted, at the urgency of the one who is waiting on it.

    Two shapes it cannot collapse onto, and both are bounded rather than guarded: a probe already
    RUNNING (the collapse is onto waiting rows only, deliberately: see `enqueue`), and the
    `scan_only` read the Scan button hands out, whose payload carries a field this one does not.
    Each of those queues one more row, once (every later press collapses onto that row), so the
    cost is a single extra read of one file, and nobody is left waiting. That is the right way
    round for this: the check `_repackage` keeps is guarding a whole-file copy that runs for
    minutes, and this is a read that is the thing somebody is waiting for.
    """
    queue = part_of(request, QUEUE)
    try:
        await queue.enqueue(
            media_jobs.PROBE, {"asset_id": asset_id}, priority=WAITED_ON_PRIORITY, dedupe=True
        )
    except Exception as error:
        log.info("player.read_not_queued", asset_id=asset_id, detail=str(error))


async def _repackage(request: Request, asset_id: str) -> None:
    """Ask for a repackaged copy of this file, unless one is already being made.

    Fire and forget. Nothing here waits for it: the answer this request is building says how to
    play the file NOW, and the copy is for next time. A failure to enqueue is logged and otherwise
    ignored, because the fallback is the conversion that was going to happen anyway.

    The duplicate check is what stops a playlist request storm (or somebody opening the same file
    five times) from queueing five copies of the same whole file.
    """
    queue = part_of(request, QUEUE)
    payload = {"asset_id": asset_id, "because": media_jobs.BECAUSE_CONTAINER}
    try:
        # `is_live` covers one already RUNNING, which `dedupe` does not, and a whole-file copy
        # runs long enough that the running case is the likely one when somebody presses play twice.
        if await queue.is_live(media_jobs.REMUX, payload):
            return
        await queue.enqueue(media_jobs.REMUX, payload, dedupe=True)
    except Exception as error:
        log.info("player.repackage_not_queued", asset_id=asset_id, detail=str(error))


# --- Direct play ------------------------------------------------------------------------------


class LocalFile(Wire):
    """Where this asset's file is, for a caller that could actually open it."""

    path: str | None = None
    """The absolute path, or None when the caller is not on this machine or is a guest. See the
    route."""

    shared_path: str | None = None
    r"""The same file named in a way that means the same thing on ANY computer, or None.

    THIS IS WHAT MAKES A DRAG ON A SECOND COMPUTER COST NOTHING. A library on a network share is
    reachable from every machine that can see the share, so there is no reason for a second Sift to
    copy a video across the network in order to hand it to something running beside it: it can
    hand over the share path and let the receiving application read it exactly as the first machine
    would.

    ONLY A UNC PATH QUALIFIES, and the distinction is the whole safety of it. `\\nas\Media\a.mp4`
    names a server and a share, so it is the same file wherever it is opened. `D:\Media\a.mp4` is a
    different file on every computer that happens to have a D: drive, and handing one of those to
    another machine is how a drag quietly produces somebody else's video.

    It is answered to remote callers, which the plain `path` deliberately is not. What it discloses
    is the name of a share on the local network, to somebody the library has already agreed may
    watch and download the file itself, and the caller still has to be able to reach that share.
    """

    filename: str
    """What the file is called. Answered in both cases: it is what a fetched copy gets named."""

    size_bytes: int | None = None
    """So a download can show progress. None when the row has never been probed. For a file that
    holds a place, the size of the copy without it, which is what arrives."""

    holds_a_place: bool = False
    """The file says where it was made, so it never leaves as it is: `path` and `shared_path` are
    None, whoever asks, and the shell fetches the copy without the place from `/outgoing`
    instead of the original from `/stream`."""


def _machine_independent(path: Path) -> str | None:
    r"""The path, when it names the same file on any computer; otherwise None.

    A UNC path carries the server and the share in it, so it resolves the same way everywhere. The
    two `\\?\` and `\\.\` prefixes are excluded because they start with the same two characters
    and mean the opposite: they are ways of naming a LOCAL device without the usual parsing.
    """
    text = str(path)
    if not text.startswith("\\\\"):
        return None
    if text.startswith("\\\\?\\") or text.startswith("\\\\.\\"):
        return None
    return text


@router.get("/assets/{asset_id}/local-file")
async def local_file(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LocalFile:
    """Where the file is, so the desktop shell can drag it into another application.

    THE ONLY CALLER IS THE SHELL, and this exists because a real Windows drag hands over a path on
    the local disk: there is no "stream it from over there" in the format Windows uses. So the
    shell needs either a path it can open or a name to save a copy under, and this one route
    answers both without the page ever being told either.

    Rule 1 of this module still holds: the path comes from `Repository.locate`, which applies the
    permission scope inside its SQL. The authority is exactly the authority to watch the thing:
    somebody who can play an asset can already download it, so being able to drag it out is not
    new. That is why this is not admin-only: a guest whom the app lets watch something would
    otherwise get LESS out of the desktop application than out of a plain browser.

    `path` is None for a caller on another machine, which is what makes the shell fetch a copy
    instead. It is a correctness answer rather than a secrecy one: `is_local_request` says why.

    `shared_path` is the way out of that copy: a library kept on a network share is reachable from
    both machines, so the second one has no reason to fetch anything. See the field.

    A guest is given neither. Where the library lives on this device or on the network is for
    admins to know, so a guest's drag is always a copy.
    """
    # Not through `_open`, which answers one 404 for every reason. The shell's drag is a deliberate
    # act on a named file, so the vault's OWNER is told that is what stopped it (see
    # `sift.kernel.reach`). Everything else keeps the undifferentiated 404 it had.
    asset = await access.open_asset(viewer, asset_id)
    if asset is None:
        raise await refuse_one(access, viewer, asset_id, _missing)
    located = await access.locate(viewer, asset.id)
    if located is None:
        raise _missing()

    # A FILE THAT SAYS WHERE IT WAS MADE IS NEVER DRAGGED AS IT IS. Neither the path nor the share
    # path is answered, since both name the original with its place in it, and the shell fetches
    # the copy the door makes (`outgoing`). Its size is the copy's, which a drag hands over first.
    try:
        unplaced = await on_serving_thread(places.size_without_places, located)
    except places.CannotRemovePlaces as refusal:
        raise _cannot_take_the_place_out() from refusal
    except OSError:
        raise _missing() from None
    if unplaced is not None:
        return LocalFile(
            filename=located.name,
            size_bytes=unplaced,
            holds_a_place=True,
        )

    local = is_local_request(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for", ""),
    )
    return LocalFile(
        path=str(located) if local and viewer.is_admin else None,
        # On this machine the plain path above is the better one and the shell prefers it; from
        # another machine this is the difference between handing over a path and copying a video
        # across the network to hand over a copy of it.
        shared_path=_machine_independent(located) if viewer.is_admin else None,
        # The name on disk, not the one the file was imported under: it is the name the person sees
        # in their own folders, and a copy dragged out should match what they would find there.
        filename=located.name,
        size_bytes=asset.size_bytes,
    )


def _cannot_take_the_place_out() -> HTTPException:
    """The refusal of a file whose location cannot be taken out: it does not leave with it."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Sift couldn't take the location out of this file, so it can't be copied out of Sift.",
    )


@router.get("/assets/{asset_id}/outgoing")
async def outgoing(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The file as it may leave Sift: its own bytes, or a copy with every location taken out.

    What the desktop shell's drag out of the window fetches, and what Copy image puts on the
    clipboard: two ways a picture leaves for a chat window, and neither may carry where it was
    taken. `stream` is the original, for Sift's own player; this is the same file through the door
    (`sift.kernel.places`). The file in the library is not touched: a copy is written into Sift's
    own cache and removed once it has been sent, and a file with no place is sent as it is.

    The same authority as `stream` (whoever may watch a file may take it), and its media type, so
    a picture on the clipboard is a picture. A file whose location cannot be taken out is refused
    with a sentence, and its HEAD says so too (`outgoing_head`).
    """
    asset = await _open(access, viewer, asset_id)
    path = await access.locate(viewer, asset.id)
    if path is None or not await on_serving_thread(path.is_file):
        raise _missing()
    media_type = asset.mime or "application/octet-stream"
    if request.method == "HEAD":
        try:
            await on_serving_thread(places.places_in, path)
        except places.CannotRemovePlaces as refusal:
            raise _cannot_take_the_place_out() from refusal
        return FileResponse(path, media_type=media_type, headers=dict(_CACHE_PRIVATE))
    try:
        unplaced = await on_serving_thread(
            places.remove_places, path, wiring.settings(request).cache_dir / places.OUTGOING_FOLDER
        )
    except places.CannotRemovePlaces as refusal:
        raise _cannot_take_the_place_out() from refusal
    if unplaced is None:
        return FileResponse(path, media_type=media_type, headers=dict(_CACHE_PRIVATE))
    return FileResponse(
        unplaced,
        media_type=media_type,
        headers=dict(_CACHE_PRIVATE),
        background=BackgroundTask(places.discard, unplaced),
    )


@router.head("/assets/{asset_id}/outgoing", include_in_schema=False)
async def outgoing_head(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The same question without the bytes, answered by the route above so the two cannot
    disagree: may I have this file, is it there, and can its location be taken out (422 when
    not). A route of its own for the reason `browse.router.save_to_device_head` gives."""
    return await outgoing(asset_id, request, access, viewer)


@router.get("/assets/{asset_id}/stream")
async def stream(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The file itself, over HTTP range requests. No ffmpeg runs.

    This is the majority path and the whole of its cost is reading bytes off a disk. There is a
    test that counts ffmpeg processes across a direct-play request and asserts the count is zero,
    because the day this route starts transcoding is the day the cheap path stops being cheap and
    nobody notices.
    """
    asset = await _open(access, viewer, asset_id)
    # A file whose audio sits too far from its video is served from its repaired copy instead: the
    # same streams in a container that keeps them together, so seeking does not make the browser
    # thrash. Absent until the repair job has run, and absent forever for the files that never
    # needed one.
    repaired = await access.locate_derivative(viewer, asset.id, DerivativeKind.REMUX)
    path = repaired or await access.locate(viewer, asset.id)
    if path is None:
        raise _missing()

    try:
        stat = await on_serving_thread(path.stat)
    except OSError:
        # The row says it is there and the disk disagrees: an unplugged drive, a deleted file.
        raise _missing() from None

    size = stat.st_size

    raw_range = request.headers.get("range")
    try:
        span = byte_range(raw_range, size)
    except ValueError:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{size}", **_CACHE_PRIVATE},
        )

    # A repaired copy is always MP4, whatever the original was, so the type is taken from what is
    # actually being sent rather than from the asset. Serving a re-muxed .mov as `video/quicktime`
    # would be a lie a browser acts on.
    media_type = policy.REPAIRED_MIME if repaired else (asset.mime or "application/octet-stream")
    # `Accept-Ranges` is what tells the player it may seek at all. Without it a browser downloads
    # from the start every time somebody drags the scrubber.
    headers = {"Accept-Ranges": "bytes", **_CACHE_PRIVATE}
    # A picture opened isn't a clip playing.
    played = PLAYED.now if asset.media_type == "video" else None

    if span is None:
        headers["Content-Length"] = str(size)
        whole = byte_range(f"bytes=0-{size - 1}", size) if size else None
        if whole is None:
            return Response(status_code=status.HTTP_200_OK, headers=headers, media_type=media_type)
        return StreamingResponse(
            read_range(path, whole, gone=request.is_disconnected, played=played),
            media_type=media_type,
            headers=headers,
        )

    span = capped(span, raw_range, tuning.MAX_OPEN_RANGE_BYTES)
    headers["Content-Range"] = span.content_range
    headers["Content-Length"] = str(span.length)
    return StreamingResponse(
        read_range(path, span, gone=request.is_disconnected, played=played),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers=headers,
    )


# --- HLS --------------------------------------------------------------------------------------


# HEAD on every route that answers with bytes. A player, a download manager and a proxy all ask
# HEAD before GET to learn the size and whether ranges are honoured, and without these every one
# of these routes would answer 405. Out of the schema and delegating to the GET handler, for the
# reasons the browse router's save-to-device HEAD gives: `methods=["GET", "HEAD"]` breaks the
# generated client, and a HEAD is not a second thing a client can do.
@router.head("/assets/{asset_id}/stream", include_in_schema=False)
async def stream_head(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    return await stream(asset_id, request, access, viewer)


@router.get("/assets/{asset_id}/rendition")
async def rendition(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """A photograph as a JPEG, for a browser that cannot draw the original. No decoder runs.

    A phone's HEIC is a picture only Safari draws: everywhere else the original arrives whole and
    the browser shows nothing. The copy is the whole picture, decoded once when the file was read
    (`kernel.heif`), so this is bytes off a disk like `stream`. The viewer asks for it only after
    the original failed to draw, and Save to device keeps handing over the original.

    A JPEG or nothing. The same kind of derivative is an animated WebP's decoder copy, an MP4 that
    no picture element draws, and a still's copy is the only one this answers with. A 404 covers
    "not allowed", "no copy" and "not made yet" alike, as every picture route does.
    """
    try:
        served = await access.serve_derivative(viewer, asset_id, DerivativeKind.RENDITION)
    except ValueError:
        # A row naming a path outside the cache: a restored backup or an older build's row. There
        # is nothing to serve, which is the same answer as any other miss.
        raise _missing() from None
    if served is None or served.path.suffix.lower() != ".jpg":
        raise _missing()
    return await serve_file(
        request,
        served.path,
        media_type="image/jpeg",
        headers=keeps(request, version=served.version, concealed=served.concealed),
    )


@router.get("/assets/{asset_id}/hls/master.m3u8")
async def hls_master(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The ladder: every size this file can be watched at, for the player to choose between.

    DECLARED BEFORE the segment route below, and that is load-bearing rather than tidy. Routes are
    matched in the order they are written, and `hls/{segment}` matches literally anything, so a
    master playlist declared after it would be looked up as a segment called "master.m3u8", refused
    by the strict name check there, and answered with a 404 that looks like a missing file.

    It takes no query string of its own. Everything a variant needs is written into the variant's
    own URL inside it, which is the same rule the media playlist already follows: nothing is held
    between requests, so the decision travels in the address or it does not travel.
    """
    asset = await _open(access, viewer, asset_id)
    body = master_playlist(asset, base_url=f"/api/assets/{asset_id}/hls")
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        headers=_CACHE_PRIVATE,
    )


@router.head("/assets/{asset_id}/hls/master.m3u8", include_in_schema=False)
async def hls_master_head(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    return await hls_master(asset_id, access, viewer)


@router.get("/assets/{asset_id}/hls/index.m3u8")
async def hls_playlist(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The list of segments. None of them exist yet; they are made as they are asked for.

    The plan arrives in this request's own query string and is written onto every URL in the
    playlist, so the segment requests that follow act on the decision `/playback` made rather than
    re-deriving it and landing on the default.
    """
    asset = await _open(access, viewer, asset_id)
    plan = _plan_from_query(request, asset)
    body = playlist(asset, base_url=f"/api/assets/{asset_id}/hls", query=plan_query(plan))
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        headers=_CACHE_PRIVATE,
    )


@router.head("/assets/{asset_id}/hls/index.m3u8", include_in_schema=False)
async def hls_playlist_head(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    return await hls_playlist(asset_id, request, access, viewer)


@router.get("/assets/{asset_id}/hls/{segment}")
async def hls_segment(
    asset_id: str,
    segment: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    player: Annotated[PlayerService, Depends(_player)],
) -> Response:
    """One segment, transcoded on demand if it is not already cached.

    Scoped exactly as tightly as `stream` is. It is a separate route and it gets a separate test:
    two endpoints that return bytes are two endpoints that can leak, and testing one of them
    proves nothing about the other.
    """
    asset = await _open(access, viewer, asset_id)
    plan = _plan_from_query(request, asset)
    index = _segment_index(segment, asset)

    try:
        produced = await player.segment(asset, index=index, plan=plan)
    except SegmentUnavailable as error:
        log.warning("player.segment_unavailable", asset_id=asset_id, index=index, error=str(error))
        # 503, not 404: the segment exists as a concept and could not be built. A 404 here would
        # be indistinguishable from a permission refusal, which would make a transient encoder
        # failure look exactly like a file the viewer is not allowed to see.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="That part of the video could not be prepared. Try again.",
        ) from None

    size = (await asyncio.to_thread(produced.path.stat)).st_size
    span = byte_range(None, size) or _whole(size)

    async def body() -> Any:
        try:
            async for chunk in read_range(produced.path, span, played=PLAYED.now):
                yield chunk
        finally:
            if produced.ephemeral:
                # Larger than the entire cache cap, so it was never admitted. Served, then gone.
                await asyncio.to_thread(produced.path.unlink, missing_ok=True)

    return StreamingResponse(
        body(),
        media_type=tuning.SEGMENT_MIME,
        headers={"Content-Length": str(size), **_CACHE_PRIVATE},
    )


def _whole(size: int) -> Any:
    span = byte_range(f"bytes=0-{max(0, size - 1)}", max(1, size))
    assert span is not None  # noqa: S101 - a constructed range is always parseable
    return span


def _segment_index(segment: str, asset: Asset) -> int:
    """Which segment `0.m4s` or `init.mp4` names. 404 for anything else.

    Strict on purpose. This value comes off the URL and is used to build a filename, so anything
    that is not a plain number or the one known init name is refused here rather than being
    allowed to reach the filesystem.

    """
    if segment == tuning.INIT_SEGMENT_NAME:
        return -1
    if not segment.endswith(tuning.SEGMENT_SUFFIX):
        raise _missing()
    raw = segment[: -len(tuning.SEGMENT_SUFFIX)]
    index = as_int(raw)
    # Negative is refused here rather than left to the bound below: a sign parses, and the init
    # segment is the only thing allowed to be under zero: it is named, not numbered.
    if index is None or index < 0 or index >= policy.segment_count(asset.duration_ms):
        raise _missing()
    return index


def _plan_from_query(request: Request, asset: Asset) -> policy.Plan:
    """The plan this segment is being requested under, from the playlist's own query string.

    The player is handed these values in the playlist it was given, so they arrive back unchanged.
    They are validated rather than trusted, and the validation is the ladder: a height is accepted
    only if it is one this file's ladder offers. Taking any positive number up to the source would
    start a distinct transcode for each distinct one, so any signed-in caller could widen the
    ladder to a thousand rungs and fill the cache with them.

    A segment is by definition a transcode, so the route is not read: `route=` still travels in
    the playlist's URLs for the player's benefit, and there is no distinction here for it to make.
    """
    height: int | None = None
    candidate = as_int(request.query_params.get("height"))
    if candidate is not None:
        offered = {rung.height for rung in policy.rungs(asset.width, asset.height)}
        if candidate in offered:
            height = candidate

    return policy.Plan(route=policy.Route.TRANSCODE, reason="", scale_height=height)


@router.head("/assets/{asset_id}/hls/{segment}", include_in_schema=False)
async def hls_segment_head(
    asset_id: str,
    segment: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    player: Annotated[PlayerService, Depends(_player)],
) -> Response:
    return await hls_segment(asset_id, segment, request, access, viewer, player)


# --- Watch state ------------------------------------------------------------------------------


#: The longest one sitting may claim to have been. A day, which no viewing is and every runaway
#: counter exceeds: a tab left open across a suspend, a loop running all night. Named rather than
#: written out at each field, because the ceiling on the whole and the ceiling on one slice of it
#: have to be the same number or the parts can add up to more than the total.
_MAX_SITTING_MS = 24 * 60 * 60 * 1000

#: An id as the server mints them, or anything shaped like one: letters, digits, `_` and `-`.
_AN_ID = r"^[A-Za-z0-9_-]{1,64}$"

#: The longest search a sitting will look up: the bound the search routes themselves keep.
_MOST_SEARCHED = 1000

#: The slowest and fastest speeds a sitting keeps time at. Wider than any player offers, so this
#: bounds a browser rather than a person.
_SLOWEST = 0.0625
_FASTEST = 16.0


class SeekReport(Wire):
    """One move of the playhead by hand: where it was and where it went, in milliseconds."""

    from_ms: int = Field(ge=0, le=_MAX_SITTING_MS)
    to_ms: int = Field(ge=0, le=_MAX_SITTING_MS)


class ViewReport(Wire):
    """What one sitting with one file actually consisted of.

    Facts, and only facts. Not one field here says "this was a view" or "this is finished": those
    are conclusions, and the server draws them, because three screens send this and a rule enforced
    in a browser is enforced three times. See `record_view`.
    """

    watch_ms: int = Field(default=0, ge=0, le=_MAX_SITTING_MS)
    #: How much of THIS SITTING was already reported, or None where this report is the whole of it.
    #:
    #: A sitting can arrive in more than one piece, and this is the only thing that says so. A
    #: player that waits until it is closed tells a tile about a view minutes after it happened, so
    #: it sends one report the moment the sitting has earned one and another with the remainder on
    #: the way out. Both are additive (the time and the replay map in each are that piece's own,
    #: never a running total), and this is what stops the second piece being counted as a second
    #: view: the server asks whether the sitting had already qualified BEFORE this piece, and only
    #: counts one where it had not.
    #:
    #: Still a fact rather than a conclusion, which is the line every field here stays on. It says
    #: what this client already sent; it does not say what any of it was worth. Zero and None are
    #: genuinely different: None is "this is the whole sitting", zero is "I have already reported a
    #: piece of this sitting, and there was no time in it", which is exactly what opening a
    #: photograph sends, and it is what makes the picture counted once rather than twice.
    #:
    #: A lost first piece costs nothing: the second arrives saying None, and the sitting is judged
    #: whole, on the way out.
    already_reported_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: Where the playhead was when the sitting ended. Sent on every report, and None for anything
    #: with no timeline. What is STORED is not this: the server decides that, below.
    position_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: The file played to its own end during this sitting.
    #:
    #: Reported separately from `position_ms` because the two are not the same claim and the
    #: difference is the whole of a looping clip: something set to repeat fires `ended` and is back
    #: at zero a frame later, so its final position says it was never finished. Only the element
    #: knows this happened; nothing on the server can infer it after the fact.
    ended: bool = False
    #: Which SITTING this report is a piece of, or None from a client that does not mint one.
    #:
    #: Minted by the player when it opens a file and repeated on every piece of that sitting, which
    #: is the one fact the server could not work out for itself. Without it a sitting delivered in
    #: two pieces is two rows in `plays` (the time and the replay map right in total, the COUNT of
    #: sittings high by one for every file left open long enough to earn a view), and a photograph
    #: is always two, one of them zero-length, because that is how a still is reported at all.
    #:
    #: Opaque here, and deliberately: it identifies a sitting and nothing else, it is never shown,
    #: and it is only ever compared with another one from the same user. Bounded because it
    #: arrives from a browser and everything that does is bounded.
    sitting: str | None = Field(default=None, max_length=64)
    #: How many times the playhead was moved by something other than playing during this piece.
    #:
    #: The client detects this anyway (it is how a stretch of replay map is charged to where
    #: somebody was sitting rather than smeared across everything the scrubber crossed). A fact
    #: about the sitting, like everything else here: what it means about the file is a conclusion
    #: and not this report's business.
    #:
    #: Bounded by the ceiling on the sitting itself: a report cannot carry more jumps than there
    #: were quarter-seconds in a day, and an unbounded counter from a client is an unbounded number
    #: offered to a write path.
    seeks: int = Field(default=0, ge=0, le=_MAX_SITTING_MS)
    #: How long each slice of the file was on screen, keyed by slice index as a string.
    #:
    #: Sparse: only the slices actually played appear, so a glance at a long film sends one entry
    #: rather than a hundred zeroes. Keys arrive as strings because that is what JSON objects have,
    #: and are parsed rather than trusted (see `record_view`).
    #:
    #: Bounded at the number of slices there are. A file cannot have more parts than it is cut into,
    #: so anything longer is not a sitting, and an unbounded map from a client is a hundred
    #: thousand rows offered to a write path in one request.
    heat: dict[str, int] = Field(default_factory=dict, max_length=HEAT_BUCKETS)
    #: WHICH SCREEN this sitting is happening on. See `plays.Screen`.
    #:
    #: Optional on the wire, and None is stored as NULL rather than guessed: a client that has not
    #: been rebuilt sends none, and "the panel, probably" is exactly the invented value the column
    #: exists not to hold. Refused when it is not one of the list, like every closed set here.
    screen: Screen | None = None
    #: The screen the panel was opened over. See `plays.OpenedFrom`.
    opened_from: OpenedFrom | None = None
    #: The saved Loop the file was opened from, when it was. Bounded like every id from a browser.
    loop: str | None = Field(default=None, max_length=64)
    #: The kept filter the screen behind the panel was showing, when it was showing exactly one.
    kept_filter: str | None = Field(default=None, max_length=64)
    #: The Theater session a cell's sitting belongs to: the name the wall minted when it opened.
    theater_session: str | None = Field(default=None, max_length=64)
    #: Which one thing the screen behind the panel was about, when `opened_from` names a thing: the
    #: person's, tag's, Site's, Collection's, Photo Set's, song's or folder's id.
    opened_from_id: str | None = Field(default=None, max_length=64, pattern=_AN_ID)
    #: The words of the search the file was opened from, when `opened_from` is `search`. Not kept:
    #: the sitting keeps the record of that search instead (`search_events`), found by them.
    searched: str | None = Field(default=None, max_length=_MOST_SEARCHED)
    #: Where the playhead was when the sitting began. Kept from the first piece.
    start_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: Each move of the playhead by hand during this piece, in order. See `plays.MOST_SEEKS`.
    seek_log: list[SeekReport] | None = Field(default=None, max_length=MOST_SEEKS)
    #: How long this piece played at each speed, keyed by the rate as a decimal ("1", "1.5").
    #: Keys that do not read as a speed a player offers are dropped, for the reason a replay
    #: slice that does not parse is (`_heat_from`).
    speeds: dict[str, int] | None = Field(default=None, max_length=MOST_SPEEDS)
    #: How long this piece filled the screen.
    fullscreen_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: How many times the file played through to its own end during this piece: every pass under
    #: Repeat, where `ended` says only that one did.
    completions: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: Whether a picture was magnified during this piece.
    magnified: bool | None = None


#: The file's kind as the sitting stores it: the asset's own media type, or nothing for a value the
#: column would refuse. A media type outside the three would be a new kind of file, and dropping it
#: to NULL costs one fact on one row where refusing it would cost the whole sitting.
_KINDS: frozenset[str] = frozenset(get_args(Kind))


def _place_of(
    report: ViewReport, asset: Asset, client: Client, opened_from_id: str | None
) -> Place:
    """Where this sitting happened, and what the file was when it did.

    The KIND is the server's, read off the file rather than taken from the report: it is the one
    fact here the server already holds, and a fact the browser could get wrong has no business
    arriving from it. So is the CLIENT, read off the request the way every other record of use
    reads it (`kernel/client.py`). The rest only the browser can know.
    """
    kind = cast(Kind, asset.media_type) if asset.media_type in _KINDS else None
    return Place(
        screen=report.screen,
        opened_from=report.opened_from,
        kind=kind,
        loop_id=report.loop,
        kept_filter_id=report.kept_filter,
        theater_session=report.theater_session,
        opened_from_id=opened_from_id,
        device_id=client.device,
        client_kind=client.kind,
    )


#: The latest record of one typed search by one User: the search a file opened from that wall was
#: opened from. The same reading the search's own record of an opened result makes.
_LATEST_SEARCH = (
    "SELECT id FROM search_events WHERE user_id = ? AND kind = 'query' AND subject = ?"
    " ORDER BY id DESC LIMIT 1"
)


async def _opened_from_id(database: Database, viewer: Viewer, report: ViewReport) -> str | None:
    """The one thing the screen behind the file was about, as the sitting keeps it.

    A search is kept by its RECORD rather than its words: the words are already in the record, the
    record says when and on what, and Forget and Clear take it away with the sitting's link to it.
    None where there is no record (the history was paused when the search was made, or the
    address was pasted in), which is the honest answer: the sitting still says it was a search.
    """
    if report.opened_from != "search":
        return report.opened_from_id
    words = " ".join((report.searched or "").split())
    if not words:
        return None
    row = await database.fetch_one(_LATEST_SEARCH, (viewer.id, words))
    return None if row is None else str(row["id"])


def _inside_of(report: ViewReport) -> Inside:
    """What happened inside this piece of the sitting, with any speed nobody could play at dropped.

    Dropped rather than refused, for the reason `_heat_from` gives: this report arrives from a page
    that is closing, and a 422 would take the whole sitting with it.
    """
    speeds: dict[float, int] | None = None
    if report.speeds is not None:
        speeds = {}
        for key, spent in report.speeds.items():
            try:
                rate = float(key)
            except ValueError:
                continue
            if _SLOWEST <= rate <= _FASTEST and spent > 0:
                speeds[rate] = speeds.get(rate, 0) + min(int(spent), _MAX_SITTING_MS)
    return Inside(
        start_ms=report.start_ms,
        seek_log=None
        if report.seek_log is None
        else tuple((seek.from_ms, seek.to_ms) for seek in report.seek_log),
        speeds=speeds,
        fullscreen_ms=report.fullscreen_ms,
        completions=report.completions,
        magnified=report.magnified,
    )


@router.post(
    "/assets/{asset_id}/view",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def record_view(
    asset_id: str,
    report: ViewReport,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
    database: Annotated[Database, Depends(wiring.database)],
) -> Response:
    """Record one sitting: the time, the place, whether it was a view, and which parts were played.

    Scoped like everything else here: somebody who cannot see an asset cannot record a view against
    it, which would otherwise be a way to confirm an id exists. It writes state, so it carries the
    CSRF token every other unsafe method does rather than resting on the SameSite cookie alone.

    ## The client reports; this decides

    Everything on `ViewReport` is a fact the browser is the only one able to observe: how long the
    file was on screen, where the playhead stopped, whether it reached the end. Every CONCLUSION is
    drawn here, against the file's own length and the user's own settings, because three separate
    screens send this report and a rule that lives in one of them is not a rule.

    Three separate things come out of that, and they are separate on purpose:

    - **The time watched is always kept.** A sitting too short to be a view is still time that
      really passed, and dropping it would leave `watched_ms` describing only the sittings that
      happened to clear a threshold.
    - **A view is counted when the sitting earns it** (see `policy.counts_as_a_view`, which asks a
      different question of a picture than of a video). There is deliberately no dedup window:
      watching something twice in five minutes is watching it twice, and it should read as two.
      A sitting reported in pieces is counted on the piece that CROSSES the threshold, so it is
      still one view: see `ViewReport.already_reported_ms`, which is what tells the two apart.
    - **Where somebody got to is always kept**, view or not. The two are different questions and the
      first does not wait on the second: opening a film, skipping two minutes in and leaving is
      exactly the sitting somebody wants picked up again, and it is nowhere near the threshold.
      `_resume_where` has already applied the user's own rule about what is worth keeping.
    - **The replay curve takes whatever was played**, view or not. Ten seconds of a favourite moment
      is exactly the thing that curve exists to show, and refusing it because the sitting was short
      would erase the shortest and most repeated visits, which are the peaks.
    """
    asset = await _open(access, viewer, asset_id)
    resume_ms = await _resume_where(request, viewer.id, asset, report.position_ms)
    # What this sitting had already earned before this piece of it, and what it has earned now that
    # this piece is in. A view is counted on the CROSSING (the report that takes a sitting over
    # the line), so a sitting delivered in two pieces is one view and not two, and a sitting
    # delivered whole is judged on its one report.
    before = report.already_reported_ms
    earned_already = before is not None and policy.counts_as_a_view(
        asset.media_type, asset.duration_ms, before
    )
    earned_now = policy.counts_as_a_view(
        asset.media_type, asset.duration_ms, (before or 0) + report.watch_ms
    )
    if earned_now and not earned_already:
        await user_state.record_view(
            asset.id,
            viewer.id,
            watch_ms=report.watch_ms,
            resume_ms=resume_ms,
            # Either way of finishing. A clip set to repeat is back at the start a frame after it
            # ends, so its position says it never got there; the element saying `ended` is the only
            # record that it did.
            completed=report.ended
            or policy.watched_to_the_end(asset.duration_ms, report.position_ms),
        )
    else:
        await user_state.record_watch_time(
            asset.id,
            viewer.id,
            watch_ms=report.watch_ms,
            resume_ms=resume_ms,
            # THE SAME TWO WAYS OF FINISHING AS THE BRANCH ABOVE: leaving them out here would be a
            # fault, not a distinction. A sitting earns its view on the report that CROSSES the
            # threshold (thirty seconds into anything two minutes or longer) and reaches the end on
            # a LATER report, which lands here because the view has already been counted. Without
            # them the branch told a file had finished could never write it down, and every video
            # of two minutes or more would be watched, counted, and left unfinished.
            completed=report.ended
            or policy.watched_to_the_end(asset.duration_ms, report.position_ms),
        )

    heat = _heat_from(report.heat)
    if heat:
        await user_state.add_replay_heat(asset.id, viewer.id, heat)

    # And the sitting itself, kept whole.
    #
    # Everything above this line folds the report into a counter (how many views, how many
    # milliseconds, how tall each slice of the curve), and a counter can say how much and nothing
    # else. This is the row the counters cannot be un-summed back into: when it happened, how long
    # it lasted, where it stopped. See `plays.py`; it is deliberately not part of the ledger.
    #
    # Last, and after the counters, so a fault in the newest thing here cannot cost the oldest.
    #
    # Not at all while this User has paused their history (`Settings > Privacy`): the sitting is
    # the record of what they watched, and the pause is theirs. The counters above are not that
    # record: they are what picks a file up where it was left and what Unwatched reads, and a
    # pause that broke those would be a pause nobody could leave on.
    if not await keeps_history(part_of(request, SETTINGS_HUB), viewer.id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # What the file carried and what it was opened from are read only for a sitting's FIRST piece,
    # which is the only one that writes them: a later piece would be reading what the file carries
    # minutes later, and paying for the reads to throw them away.
    first = report.sitting is None or not await sitting_is_here(
        database, viewer.id, asset.id, report.sitting
    )
    await record_play(
        database,
        user_id=viewer.id,
        asset_id=asset.id,
        sitting=report.sitting,
        watch_ms=report.watch_ms,
        already_reported_ms=report.already_reported_ms,
        position_ms=report.position_ms,
        seeks=report.seeks,
        heat=heat,
        place=_place_of(
            report,
            asset,
            client_of(request),
            await _opened_from_id(database, viewer, report) if first else None,
        ),
        length_ms=asset.duration_ms,
        inside=_inside_of(report),
        about=await about_of(database, access, viewer, asset.id) if first else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _heat_from(reported: dict[str, int]) -> dict[int, int]:
    """The replay map as slice indexes, with anything unreadable dropped.

    Dropped rather than refused, and the difference matters at this end of a request. This arrives
    with `keepalive` from a page that is closing, so nothing is watching for a reply: a 422 would
    be invisible and would take the whole sitting with it, including the view and the time watched,
    which are the parts that were fine. A slice index that will not parse is one entry lost from a
    curve; refusing would be the whole viewing lost to it.

    The values are clamped to a day rather than trusted. A browser tab left open across a suspend
    can report a stretch of wall-clock nobody watched, and one such report would flatten a real
    curve into a single peak for ever.
    """
    heat: dict[int, int] = {}
    for key, milliseconds in reported.items():
        try:
            bucket = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= bucket < HEAT_BUCKETS and milliseconds > 0:
            heat[bucket] = min(int(milliseconds), _MAX_SITTING_MS)
    return heat


class ReplayCurve(Wire):
    """How much of each slice of a file this user has watched, across every sitting.

    Sent as a normalised curve rather than as milliseconds, because milliseconds is not what the
    reader is being asked. The question a curve under a scrubber answers is "which parts of this
    have I come back to", which is entirely about the peaks relative to each other, and raw times
    would make every consumer divide by its own maximum, which is three places to disagree about
    what the tallest point is.
    """

    #: One value per slice, 0 to 1, always `buckets` long. The tallest slice is 1 by construction.
    heat: list[float]
    #: How many slices the file is cut into, so a reader never has to assume the length of the list
    #: above matches what it was written against.
    buckets: int
    #: Whether there is anything here worth drawing. A file played straight through once has a flat
    #: curve, which is true and says nothing, and a shape that means nothing is worse on screen
    #: than no shape at all, because it invites the reader to find a pattern in it.
    worth_drawing: bool


#: How much taller than the average a curve's peak must be before it is showing anything.
#:
#: A file watched straight through once has every slice equal; one watched straight through with a
#: single moment replayed twice has a peak at double. The line between them is not zero, because
#: rounding and a pause on a frame put a percent or two into any curve, so a peak has to stand a
#: quarter above the average before it is a peak rather than noise.
_HEAT_WORTH_DRAWING = 1.25


@router.get("/assets/{asset_id}/replays")
async def replay_curve(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> ReplayCurve:
    """The parts of this file this user keeps coming back to.

    Per user, like the heart and the stars: what somebody else has replayed is not a fact about
    the file. That is also what makes this honest on a self-hosted library where the whole audience
    is one person: the sites draw this curve from millions of strangers and call it "most
    replayed", and the same shape drawn from one person's own history is a more useful thing to be
    handed, not a lesser one.

    Scoped through `_open` like every other route here, so a curve cannot confirm that an id exists.
    """
    asset = await _open(access, viewer, asset_id)
    raw = await user_state.replay_heat(asset.id, viewer.id)
    return _curve_from(raw)


def _curve_from(raw: list[int]) -> ReplayCurve:
    """Milliseconds per slice as a normalised curve, and whether it says anything.

    Its own function so the arithmetic can be tested without a database, a request or a viewer:
    the decision "is this worth drawing" is the whole of what a reader sees and it is three lines of
    division, which is exactly the shape that goes wrong silently.
    """
    peak = max(raw, default=0)
    if peak <= 0:
        return ReplayCurve(heat=[0.0] * len(raw), buckets=len(raw), worth_drawing=False)

    # The average over the slices that were PLAYED, not over all of them. A film watched for its
    # first ten minutes has ninety empty slices, and averaging those in would make any peak at all
    # look enormous: the question is whether the parts that were watched were watched unevenly.
    played = [one for one in raw if one > 0]
    average = sum(played) / len(played)
    return ReplayCurve(
        heat=[one / peak for one in raw],
        buckets=len(raw),
        worth_drawing=peak >= average * _HEAT_WORTH_DRAWING,
    )


async def _resume_where(
    request: Request, user_id: str, asset: Asset, position_ms: int | None
) -> int | None:
    """Where this user would resume this asset, given its own two settings.

    Turned off is not the same as a minimum of nothing, and it is not the same as a minimum so
    large nothing reaches it either: off means no position is ever kept OR offered, which is what
    somebody who does not want a machine remembering what they were partway through is asking for.
    Both halves are in the kernel (the grid draws the same rule as a bar on a tile and the
    query language narrows by it), so this reads the rule rather than being it.
    """
    hub = part_of(request, SETTINGS_HUB)
    minimum_ms = await resume_minimum_ms(hub.get_user, user_id)
    return resume_point(asset.duration_ms, position_ms, minimum_ms=minimum_ms)


MAX_TRANSCODE_HEIGHT_KEY = "playback.max_transcode_height"

#: How much disk the converted copies may take up.
#:
#: A cap rather than a count, because what fills a disk is bytes: a thousand small segments
#: and one enormous one are the same number of files. A setting rather than an environment
#: variable, which on an application somebody installs is not reachable at all.
CACHE_MAX_GB_KEY = "playback.cache_max_gb"
LOOP_MODE_KEY = "playback.loop_mode"
VOLUME_KEY = "playback.volume"
MUTED_KEY = "playback.muted"
DWELL_PICTURES_KEY = "playback.dwell_pictures"
