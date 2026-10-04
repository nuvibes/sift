# SPDX-License-Identifier: AGPL-3.0-or-later
"""The work already coming for some files, and a press taking over the part of it still waiting.

ONE ANSWER FOR BOTH PRESSES THAT START A FILE'S WORK: a file's own "Run task" and a Build over the
library. Each asks the same question of the queue (which of these files already has this work
queued or running) and each does the same thing with the answer:

* **Work still WAITING is pulled forward**, not duplicated. The payload is enqueued again with
  `dedupe`, which collapses onto the waiting row and makes it the press: run now (or at quiet hours,
  if that is what was pressed), at the press's priority, named for the presser. Otherwise a file's
  pictures held for quiet hours would be made by the press and then again when the held rows ran
  at the range's opening.
* **Work already RUNNING is left alone**: a press has nothing to add to it.

Either way the file is not handed a second task for this work.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.jobs import JobQueue


@dataclass(frozen=True, slots=True)
class Coming:
    """Of the files asked about, which already have this work queued or running, and which of
    those were still waiting and are now the press's own."""

    files: frozenset[str]
    pulled: frozenset[str]


def _about(payload: Mapping[str, object], key: str) -> bool:
    """Whether a queued payload is this pass's work: one naming its products names this one."""
    products = payload.get("products")
    return not (isinstance(products, list) and key not in products)


async def take_over(
    queue: JobQueue,
    job_types: Sequence[str],
    asset_ids: Collection[str],
    key: str,
    *,
    priority: int,
    requested_by: str | None,
    at: str | None = None,
) -> Coming:
    """The work of `key` already coming for these files, with its waiting part pulled forward.

    `job_types` are every queue type that would answer this for a file: the Build's own per-file
    type and, for a product, the arrival type its When governs. One write per type
    (`enqueue_many`), whatever the number of files.
    """
    wanted = set(asset_ids)
    files: set[str] = set()
    pulled: set[str] = set()
    for job_type in job_types:
        for payload in await queue.live_payloads(job_type):
            asset_id = str(payload.get("asset_id"))
            if asset_id in wanted and _about(payload, key):
                files.add(asset_id)
        waiting = [
            payload
            for payload in await queue.queued_payloads(job_type)
            if str(payload.get("asset_id")) in wanted and _about(payload, key)
        ]
        await queue.enqueue_many(
            job_type,
            waiting,
            priority=priority,
            dedupe=True,
            requested_by=requested_by,
            at=at,
        )
        pulled.update(str(payload.get("asset_id")) for payload in waiting)
    return Coming(files=frozenset(files), pulled=frozenset(pulled))
