# SPDX-License-Identifier: AGPL-3.0-or-later
"""A page of families folded: each top's steps counted, one state, its file and its newest
failure named once."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence

from sift.kernel.content.library import names_for_assets
from sift.kernel.db import Database
from sift.kernel.jobs import Job, JobQueue, StepCounts, folded_state
from sift.kernel.jobs.failure_words import in_one_line
from sift.kernel.jobs.queue_pages import STEP_COUNT_CAP
from sift.slices.media_jobs.activity_wire import FailureLine, StepSummary
from sift.slices.media_jobs.router_controls import failures_of


async def fold_tops(
    queue: JobQueue,
    database: Database | None,
    tops: Sequence[Job],
    subjects: Mapping[str, str],
    assets: Mapping[str, str],
    shown: Callable[[Sequence[str]], Awaitable[set[str]]],
    named: Callable[[str], str],
) -> dict[str, StepSummary]:
    """Each top row's family folded (steps counted, one state, its file named once), in three
    statements for the page."""
    counts = await queue.step_counts([job.id for job in tops])
    # Named from the steps only where the top has no subject and its family was counted whole.
    unnamed = [
        job.id
        for job in tops
        if job.id not in subjects and not counts.get(job.id, _NO_STEPS).at_least
    ]
    files = await queue.family_files(unnamed)
    failures = await failures_of(queue, tops, counts)
    on_files = {one.asset_id for one in failures.values() if one.asset_id is not None}
    # `names_for_assets` answers an empty list with nothing and asks no question.
    wanted = sorted(await shown(sorted({*files.values(), *on_files})))
    names = await names_for_assets(database, wanted) if database is not None else {}
    folded: dict[str, StepSummary] = {}
    for job in tops:
        counted = counts.get(job.id, _NO_STEPS)
        subject, subject_id = subjects.get(job.id), assets.get(job.id)
        if subject is None and files.get(job.id) in names:
            subject_id = files[job.id]
            subject = names[subject_id]
        failure = failures.get(job.id)
        folded[job.id] = StepSummary(
            failure=None
            if failure is None
            else FailureLine(
                name=named(failure.type),
                reason=in_one_line(failure.error or ""),
                subject=None if failure.asset_id is None else names.get(failure.asset_id),
                attempts=failure.attempts,
            ),
            count=counted.steps,
            by_state=counted.by_state,
            at_least=counted.at_least,
            cap=STEP_COUNT_CAP,
            state=folded_state(job.state, counted.by_state),
            subject=subject,
            subject_id=subject_id,
        )
    return folded


#: A top that started nothing. The same answer `step_counts` gives one, for a top it was not asked.
_NO_STEPS = StepCounts(by_state={}, at_least=False)
