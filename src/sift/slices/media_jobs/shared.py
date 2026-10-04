# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every job here shares: the payload's readers, a file's refusal, and the staged write."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from sift.kernel import media
from sift.kernel.content import Asset, DerivativeKind, VerdictProduct
from sift.kernel.content.identity import made_for
from sift.kernel.ingress import TRANSIENT_REASONS, Reason
from sift.kernel.jobs import JobContext, JobFailedPermanently
from sift.kernel.media import FFmpegError
from sift.slices.media_jobs import ffmpeg


class Unusable(JobFailedPermanently):
    """The file failed the gate (`reason` is the gate's), or the decoder returned nothing from it.

    Permanent: a file that does not decode does not start decoding.
    """

    def __init__(self, message: str, *, reason: Reason | None = None) -> None:
        super().__init__(message)
        self.reason = reason

    @property
    def code(self) -> str:
        return self.reason.value if self.reason is not None else "no_frame"

    @property
    def transient(self) -> bool:
        """About a moment (a share away) rather than the bytes; the next scan clears it."""
        return self.reason is not None and self.reason in TRANSIENT_REASONS


def recording_verdicts(
    handler: Callable[[JobContext], Awaitable[None]], product: VerdictProduct
) -> Callable[[JobContext], Awaitable[None]]:
    """The handler, with a file it cannot use refused once and written down as a verdict.

    A refusal that means the BYTES are broken becomes an `Unusable` rather than a retry; anything
    `media.is_broken_data` does not recognise keeps its retries, the mistake that is recoverable.
    """

    async def wrapped(context: JobContext) -> None:
        try:
            await _refusing_broken_bytes(handler, context)
        except Unusable as exc:
            await context.content.record_verdict(
                _asset_id(context),
                product,
                code=exc.code,
                reason=str(exc),
                transient=exc.transient,
            )
            raise

    return wrapped


async def _refusing_broken_bytes(
    handler: Callable[[JobContext], Awaitable[None]], context: JobContext
) -> None:
    """The handler, with a refusal that means the BYTES are broken turned into `Unusable`."""
    try:
        await handler(context)
    except FFmpegError as error:
        refused = broken_bytes(error)
        if refused is None:
            raise
        raise refused from error


def broken_bytes(error: FFmpegError) -> Unusable | None:
    """The same refusal as an `Unusable`, or None where what the tool said is not about the bytes."""
    if not media.is_broken_data(str(error)):
        return None
    return Unusable(f"this file could not be decoded ({error})", reason=Reason.NOT_DECODABLE)


def _made_for(kind: DerivativeKind, asset: Asset) -> bool:
    """Whether this picture is ever made for this file: the rule every count of what is missing
    carries, so a Generate never waits on a picture nothing will make. See `made_for`."""
    return made_for(kind, media_type=asset.media_type, duration_ms=asset.duration_ms)


async def _render(argv: list[str], destination: Path, *, reads: Path | None = None) -> None:
    """Run an ffmpeg that writes a file, and leave nothing behind if it does not.

    `reads` is the library file the command opens, so the read takes its storage's lane; the one
    caller that only stitches tiles Sift wrote itself passes nothing.

    Written beside the target and moved into place, so a worker killed halfway leaves no partial
    file; the staging name keeps the extension because ffmpeg picks its format from it.
    """
    staging = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    argv = [*argv[:-1], str(staging)]
    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
    try:
        await ffmpeg.run(argv, reads=reads)
        # ffmpeg can exit 0 having written nothing: a seek past the end of a file whose duration
        # reads as zero, or whose only frames are unreadable, leaves no frame to save.
        if not await asyncio.to_thread(staging.exists):
            raise Unusable(
                "ffmpeg read the file but produced no image from it \u2014 it has no frame that "
                "can be decoded at that point, which usually means the file is truncated or is not "
                "the kind of media it claims to be"
            )
        await asyncio.to_thread(shutil.move, staging, destination)
    finally:
        await asyncio.to_thread(staging.unlink, True)


async def _size_of(path: Path) -> int:
    return (await asyncio.to_thread(path.stat)).st_size


def _asset_id(context: JobContext) -> str:
    return context.require_str(
        "asset_id", "this job needs an asset_id in its payload, and there is not one"
    )


def _at_ms(context: JobContext) -> int:
    """The moment a still is wanted at, in milliseconds.

    A number, never a string or a bool, because it is folded into the cache key.
    """
    value = context.payload.get("at_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("this job needs a whole, non-negative at_ms in its payload")
    return value
