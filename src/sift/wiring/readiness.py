# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a pass can run on this machine just now, and why not, in each feature's own words."""

from __future__ import annotations

from sift.kernel.jobs import Readiness
from sift.slices import faces, semantic, watermarks
from sift.slices.faces.service_base import FACES_SWITCHED_OFF

#: What a Smart Search press and the Activity screen say when Smart Search is simply off. One
#: sentence for both, because they answer the same question from the same readiness.
SMART_SEARCH_OFF = "Smart Search is turned off. Turn it on under Smart Search."


async def meaning_cannot_run(meaning: semantic.SemanticService) -> str | None:
    """Why Smart Search cannot describe a file on this machine just now, for a "Run task" press.

    `describe_asset` returns without a word while the feature is not ready, so a task queued then
    would finish having described nothing; the press is refused with the feature's own sentence.
    """
    readiness = await meaning.readiness()
    if readiness.ready:
        return None
    # Readiness leaves the problem empty in exactly one case, the feature switched off, so the
    # empty answer IS that sentence, as it is for Watermarks beside it.
    return readiness.problem or SMART_SEARCH_OFF


async def marks_cannot_run(marks: watermarks.WatermarkService) -> str | None:
    """Why a watermark cannot be read on this machine just now, for a "Run task" press. The same
    reason as above: `read_asset` returns quietly while the models are not there."""
    ready, problem = await marks.ready()
    if ready:
        return None
    return problem or "Reading watermarks is switched off."


async def recognition_can_run(service: faces.FaceService) -> Readiness:
    """Whether a face scan can do anything on this machine, and the reason it cannot.

    Both halves, and it has to be both: `ready` can be true while the card's runtime is not
    installed, the state where every scan fails into the job log. Asked in the order somebody would
    ask it (switched off, then a device that is not there, then models that have not arrived), so
    the sentence names the first thing standing in the way rather than the last.
    """
    if not await service.enabled():
        return Readiness(ready=False, problem=FACES_SWITCHED_OFF)
    problem = await service.device_problem()
    if problem is not None:
        return Readiness(ready=False, problem=problem)
    if not await service.ready():
        # The feature's own sentence, the one a parked scan carries: two copies of one piece of
        # copy drift.
        return Readiness(ready=False, problem=faces.MODELS_NOT_INSTALLED)
    return Readiness(ready=True)


async def meaning_can_run(service: semantic.SemanticService) -> Readiness:
    """Whether a file can be described on this machine, in Smart Search's own words.

    Its `problem` is None when the feature is merely switched off, which is the right answer to
    "is something wrong" and the wrong answer to "why is this pass not moving". A sentence is put
    there, so a row that says nothing is happening always says why.
    """
    state = await service.readiness()
    if state.ready:
        return Readiness(ready=True)
    return Readiness(ready=False, problem=state.problem or SMART_SEARCH_OFF)
