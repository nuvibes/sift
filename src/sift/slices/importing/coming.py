# SPDX-License-Identifier: AGPL-3.0-or-later
"""The work already coming for some files, and a press taking over the part still waiting.

Waiting work is pulled forward onto the press; running work is left alone; nothing is queued twice.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.jobs import JobQueue


@dataclass(frozen=True, slots=True)
class Coming:
    """Of the files asked about, which already have this work coming, and which were taken over."""

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
    """The work of `key` already coming for these files, its waiting part pulled forward."""
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
