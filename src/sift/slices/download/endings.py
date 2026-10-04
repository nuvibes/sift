# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a download ends when it does not land: given up, retried, or written down with its reason."""

from __future__ import annotations

from typing import NoReturn

from sift.kernel.jobs import (
    JobContext,
    JobFailedPermanently,
)
from sift.slices.download.service import DownloadService
from sift.slices.download.sources import failures


async def _give_up(service: DownloadService, download_id: str, sentence: str) -> NoReturn:
    """Record a failure no retry can fix, and end the job as failed with the same sentence."""
    await service.mark_failed(download_id, error=sentence)
    raise JobFailedPermanently(sentence)


async def _record_failure(
    service: DownloadService, download_id: str, exc: Exception, *, direct: bool
) -> str:
    """Write a failure down with what was learned about it, and the one action that ever helps.
    The sentence written is returned, for the job to end on.
    """
    code = getattr(exc, "code", None)
    # The reading's own tier where it gave one; derived from the code's shape only where it did not.
    tier = getattr(exc, "tier", None) or _tier_of(code)
    sentence = str(exc)
    if direct and getattr(exc, "a_tunnel_would_help", False):
        sentence = (
            f"{sentence} The Site seems to block the region the request came from. Route this "
            "Site through a tunnel to get past it."
        )
    await service.mark_failed(download_id, error=sentence, code=code, tier=tier)
    return sentence


def _tier_of(code: str | None) -> int | None:
    """How much a failure's sentence is worth, from the shape of its code.

    Kept here rather than carried on the exception because it is derivable, and one derived value is
    better than a second field two places have to remember to set. A phrase written about this site
    is the strongest reading; a status code's standard wording is the weakest.
    """
    if code is None:
        return None
    if code.startswith("wall-"):
        return 3
    if code.startswith("http-"):
        return 2 if code in _NAMED_HTTP_CODES else 1
    return 2


#: The codes the failure reader names itself rather than taking from the standard phrase list. Read
#: from that module so the two cannot drift into disagreeing about which tier a code belongs to.
_NAMED_HTTP_CODES = frozenset(f"http-{status}" for status in failures.NAMED_CODES)


async def _retry_or_give_up(
    context: JobContext, service: DownloadService, download_id: str, message: str
) -> None:
    """Record a plain-language failure only once the job has no retries left.

    The job is about to raise, so the queue will requeue it while attempts remain and fail it when
    they run out. The ledger row is only told on that last attempt, so a download that recovers on a
    retry never shows a failure it went on to overcome.
    """
    if context.attempt >= context.job.max_attempts:
        await service.mark_failed(download_id, error=message)


def _transient_message() -> str:
    return "The download did not finish. Try again in a few minutes."


def _truncated_message() -> str:
    return "The download arrived incomplete. Try again; it usually works the second time."


def _paused_message() -> str:
    return "Paused. What has downloaded so far is kept, and Resume continues from there."


def _disk_full_message() -> str:
    return (
        "The disk is nearly full, so the download did not finish. Free some space, then try again."
    )


def _animated_webp_message() -> str:
    return (
        "Sift could not read this animated WebP, so it was quarantined instead of added. The file "
        "is kept. It may be incomplete, or the WebP tools may be missing from this device. Save it "
        "as a GIF or MP4 and import that instead."
    )


def _quarantined_message() -> str:
    return (
        "The file the site served was not valid media and was quarantined. Nothing was added to the "
        "library."
    )
