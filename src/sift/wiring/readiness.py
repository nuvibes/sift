# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a pass can run on this machine just now, and why not, in each feature's own words."""

from __future__ import annotations

from sift.kernel.jobs import Readiness
from sift.slices import faces, semantic, watermarks
from sift.slices.faces.service_base import FACES_SWITCHED_OFF

SMART_SEARCH_OFF = "Smart Search is turned off. Turn it on under Smart Search."


async def meaning_cannot_run(meaning: semantic.SemanticService) -> str | None:
    """Why Smart Search cannot describe a file just now, for a "Run task" press."""
    readiness = await meaning.readiness()
    if readiness.ready:
        return None
    # Readiness leaves the problem empty only when the feature is off.
    return readiness.problem or SMART_SEARCH_OFF


async def marks_cannot_run(marks: watermarks.WatermarkService) -> str | None:
    """Why a watermark cannot be read on this machine just now, for a "Run task" press."""
    ready, problem = await marks.ready()
    if ready:
        return None
    return problem or "Reading watermarks is switched off."


async def recognition_can_run(service: faces.FaceService) -> Readiness:
    """Whether a face scan can do anything on this machine, and the first thing in the way."""
    if not await service.enabled():
        return Readiness(ready=False, problem=FACES_SWITCHED_OFF)
    problem = await service.device_problem()
    if problem is not None:
        return Readiness(ready=False, problem=problem)
    if not await service.ready():
        return Readiness(ready=False, problem=faces.MODELS_NOT_INSTALLED)
    return Readiness(ready=True)


async def meaning_can_run(service: semantic.SemanticService) -> Readiness:
    """Whether a file can be described here, in Smart Search's own words, never silent."""
    state = await service.readiness()
    if state.ready:
        return Readiness(ready=True)
    return Readiness(ready=False, problem=state.problem or SMART_SEARCH_OFF)
