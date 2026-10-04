# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two background jobs loops own.

A mark asks for its own picture the moment it is written. A mark with no picture yet is drawn as
its video's first frame, which makes two moments of one video two identical tiles, and a scan
cannot fix it, because a scan looks at files and a mark is not one.

So this is a catch-up, in the shape the content store's other catch-ups already take: asked for once
at boot, only when there is something to do, and idempotent: the stills are filed under
`(asset_id, kind, params)`, so a second run over a library that has already been swept enqueues
nothing.
"""

from __future__ import annotations

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.log import get_logger
from sift.slices.loops.service import LoopService

log = get_logger(__name__)

LOOP_STILLS = "loop_stills"

#: Mark a file that was JUST PRODUCED, whole, as a loop.
#:
#: The other half of "Save as Loop". Pressing it cuts a clip (a real file, with bytes of its own)
#: and the row on the Loops screen has to be about THAT file rather than about the video it came
#: from, or opening it plays the whole video with two markers on it.
#:
#: A job rather than a second call from the screen, because the clip does not exist yet when the
#: button is pressed: it is a background encode, and its id is not known until it has landed. The
#: editor names this job type as a follow-on the way it names probing, from the composition
#: root, so neither slice learns what the other is.
LOOP_WHOLE = "loop_whole"

#: How many moments one sweep asks for. A ceiling rather than "all of them" because each row becomes
#: a queued job, and a library with ten thousand marks should not turn one boot into ten thousand
#: rows on the dashboard at once. What is left is picked up by the next sweep, and the count is
#: LOGGED rather than left silent: a bounded pass that says nothing reads as a complete one.
SWEEP_LIMIT = 500


async def backfill_stills(context: JobContext, *, service: LoopService) -> None:
    """Ask for a still for every marked moment that has none."""
    pending = await service.marks_without_still(SWEEP_LIMIT)
    for row in pending:
        await service.wants_still(str(row["asset_id"]), int(row["start_ms"]))
    log.info("loops.stills.swept", asked=len(pending), capped=len(pending) == SWEEP_LIMIT)


async def mark_whole(context: JobContext, *, service: LoopService) -> None:
    """Put a loop on a file that has just been produced, covering the whole of it.

    Start to end, because the file IS the stretch: it was cut to exactly the piece somebody marked,
    so a mark inside it would be a mark of a mark.

    **The length is HANDED IN rather than read off the file, and both reasons matter.** The obvious
    implementation asks the assets table how long the new file is, and at the moment this runs,
    nobody knows: the probing that measures a produced file is a sibling follow-on enqueued in the
    same breath, with no ordering between them, so the answer would be NULL about half the time and
    this would skip. It is also not this slice's table to read; the kernel owns it, and semgrep
    says so. What is handed in is what the cut was asked for, which for a CLIP is exact: a clip
    is re-encoded from the moment marked rather than copied from the nearest keyframe.

    **Nothing here fails the file.** The clip is already in the library and is already what was
    asked for; a loop row that could not be written is a missing row on one screen, and raising
    would mark a job red for a file that is perfectly fine.
    """
    asset_id = context.require_str("asset_id", "this job needs an asset_id")
    running = int(context.payload.get("duration_ms") or 0)
    if running <= 0:
        # Nothing was handed in, so there is no stretch to mark and inventing one would be a row
        # about a length nobody asked for.
        log.info("loops.whole.skipped", asset_id=asset_id)
        return
    made = await service.create(
        asset_id=asset_id,
        start_ms=0,
        end_ms=running,
        name=None,
        created_by=None,
        duration_ms=running,
    )
    # And the mark that stood for this stretch, if there was one, is now a second row for
    # something that has become a file. See `LoopService.supersede`: it is what turns a mark of a
    # video into a clip of it rather than adding a clip beside the mark, and it moves the mark's
    # name and tags onto the new row before removing it.
    #
    # After the new row, never before: it is what the mark's tags are moved ONTO, so there is no
    # earlier moment this could run at.
    retired = await _supersede_the_mark(context, service=service, running=running, into=made.id)
    log.info("loops.whole.marked", asset_id=asset_id, duration_ms=running, retired=retired)


async def _supersede_the_mark(
    context: JobContext, *, service: LoopService, running: int, into: str
) -> int:
    """Forget the mark this file was cut from, where the payload says which one that was.

    The two keys are OPTIONAL and their absence is ordinary rather than an error: a job queued
    without them is still a perfectly good "mark this file
    whole": it simply has nothing to retire. So is a cut made from a stretch nobody had marked,
    which is what pressing Save as Loop in the player normally is.
    """
    cut_from = str(context.payload.get("cut_from_asset_id") or "")
    if not cut_from:
        return 0
    started = int(context.payload.get("cut_from_start_ms") or 0)
    return await service.supersede(cut_from, started, started + running, into=into)


def register_handlers(*, service: LoopService) -> None:
    """Claim the sweep and the whole-file mark. Called once, at boot."""
    register_handler(
        LOOP_STILLS,
        lambda context: backfill_stills(context, service=service),
        name="Generating stills for saved Loops",
    )
    register_handler(
        LOOP_WHOLE,
        lambda context: mark_whole(context, service=service),
        name="Saving a clip as a loop",
    )
