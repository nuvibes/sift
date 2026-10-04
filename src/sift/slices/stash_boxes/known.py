# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file linked to a stash-box's scene by an id somebody already gave it.

A Stash library keeps the stash-box id of every scene its owner matched. That id is the person's
own answer, so it is fetched by the id (`StashBoxService.keep_known_scene`), kept as the file's
answer, and applied the way an exact match is (`jobs._apply`): under the person's field rules,
inventing only what they allow. An answer already settled here is left as it is: an agreement or a
refusal made in Sift is newer than the one Stash kept.
"""

from __future__ import annotations

from enum import StrEnum

from sift.kernel.jobs import JobQueue
from sift.kernel.records import Subject
from sift.slices.stash_boxes.adapter import StashBoxUnreachable
from sift.slices.stash_boxes.jobs import ScanDeps, _apply, strategies_for
from sift.slices.stash_boxes.service import KeptLocal, StashBoxService
from sift.slices.stash_boxes.settings import may_invent


class KnownScene(StrEnum):
    """How one known scene id went."""

    LINKED = "linked"
    #: The file's answer from that box was already agreed to or refused here.
    ALREADY = "already"
    KEPT_LOCAL = "kept_local"
    #: The box could not be asked, does not know the id, or the answer did not apply.
    NOT_ASKED = "not_asked"


async def link_known_scene(
    service: StashBoxService,
    deps: ScanDeps,
    asset_id: str,
    box_id: str,
    remote_id: str,
    master_key: bytes | None,
    *,
    queue: JobQueue | None = None,
) -> KnownScene:
    """Link one file to the scene `remote_id` names on one box, and apply what the box says."""
    held = await service.match(asset_id, box_id)
    if held is not None and held.state != "waiting":
        return KnownScene.ALREADY
    if await service.kept_local(Subject.ASSET, asset_id):
        return KnownScene.KEPT_LOCAL
    if held is None or held.remote_id != remote_id:
        try:
            kept = await service.keep_known_scene(asset_id, box_id, remote_id, master_key)
        except (StashBoxUnreachable, KeptLocal):
            kept = False
        if not kept:
            return KnownScene.NOT_ASKED
    await _apply(
        asset_id,
        box_id,
        service=service,
        deps=deps,
        rules=await strategies_for(deps.settings, Subject.ASSET),
        inventable=await may_invent(deps.settings),
        master_key=master_key,
        queue=queue,
    )
    done = await service.match(asset_id, box_id)
    return (
        KnownScene.LINKED if done is not None and done.state == "applied" else KnownScene.NOT_ASKED
    )
