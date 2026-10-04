# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three pictures as the Build makes them, and a file refused once rather than three times."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else. Without it this module passes only when some
# OTHER module collected in the same run happens to have imported it, and fails on its own;
# the content suite carries the same line for the same reason.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Ingested,
    VerdictProduct,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobContext,
    JobFailedPermanently,
)
from sift.slices.media_jobs import (
    jobs,
    pictures,
)
from sift.slices.media_jobs.tests.conftest import VIDEO_SECONDS
from sift.slices.media_jobs.tests.support import Context

pytestmark = [pytest.mark.integration]


# --- the pieces the Build calls, one file at a time -----------------------------------------------


async def _yes(_job: str, _asset: str | None) -> bool:
    return True


async def _no(_job: str, _asset: str | None) -> bool:
    return False


async def _only_thumbnails(job: str, _asset: str | None) -> bool:
    return job == jobs.THUMBNAIL


async def test_the_pictures_a_page_lacks_are_the_ones_the_switches_want(
    ingested_video: Ingested, content_store: ContentStore
) -> None:
    """The page the pass walks and the switch the sheet ticks read the same kinds: a picture that
    is switched off is not lacking, and a library with every picture off wants none."""
    asset_id = ingested_video.asset.id
    await content_store.record_probe(asset_id, width=320, height=240, duration_ms=1000)
    thumbnails, previews, _sprites = jobs.PICTURES

    for picture in jobs.PICTURES:
        assert await jobs.picture_lacking_among(
            content_store, [asset_id], picture=picture, allowed=_yes
        ) == {asset_id}
        assert (
            await jobs.picture_lacking_among(
                content_store, [asset_id], picture=picture, allowed=_no
            )
            == set()
        )
    assert await jobs.picture_switched_on(picture=thumbnails, allowed=_only_thumbnails) is True
    assert await jobs.picture_switched_on(picture=previews, allowed=_only_thumbnails) is False

    await content_store.add_derivative(
        asset_id, DerivativeKind.THUMB, extension="jpg", params={}, size_bytes=1
    )
    assert (
        await jobs.picture_lacking_among(
            content_store, [asset_id], picture=thumbnails, allowed=_only_thumbnails
        )
        == set()
    ), "the one picture wanted is there, so nothing is lacking"
    assert (
        await jobs.picture_lacking_among(
            content_store, [asset_id], picture=previews, allowed=_only_thumbnails
        )
        == set()
    ), "a picture that is switched off is not lacking"


async def test_the_build_makes_every_picture_the_file_lacks_and_only_those(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The three picture products: the same three handlers an arriving file gets, each only where
    its switch is on and its picture is missing. Switched off, nothing is made; a second run over
    a file that has everything makes nothing again."""
    asset_id = ingested_video.asset.id
    await content_store.record_probe(
        asset_id, width=320, height=240, duration_ms=VIDEO_SECONDS * 1000
    )
    kinds = [picture.kind for picture in jobs.PICTURES]

    async def lacking() -> set[DerivativeKind]:
        return {kind for kind in kinds if await content_store.lacking_derivative(kind, [asset_id])}

    async def build(allowed: jobs.ShouldGenerate) -> None:
        for picture in jobs.PICTURES:
            await jobs.build_picture(
                await context_for(jobs.THUMBNAIL, {"asset_id": asset_id}),
                picture=picture,
                settings=settings,
                hardware=hardware,
                allowed=allowed,
            )

    await build(_no)
    assert await lacking() == set(kinds), "switched off is nothing made"

    await build(_yes)
    assert await lacking() == set()
    made = {one.id for one in await content_store.derivatives(asset_id)}

    await build(_yes)
    assert {one.id for one in await content_store.derivatives(asset_id)} == made, (
        "a picture already there is not made again"
    )


async def test_a_picture_already_there_is_made_again_only_when_a_press_says_again(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Run now" on a file that has its thumbnail: the task carries `AGAIN`, and the picture is
    made again. Skipped, the press would report success over a file it then left alone. Without
    the key it is still skipped, which is what an arriving file and the Build want."""
    from sift.kernel.jobs.families import AGAIN

    asset_id = ingested_video.asset.id
    await content_store.add_derivative(
        asset_id, DerivativeKind.THUMB, extension="jpg", params={}, size_bytes=1
    )
    made: list[str] = []

    async def cut(context: JobContext, **_: object) -> None:
        made.append(str(context.payload["asset_id"]))

    monkeypatch.setattr(pictures, "thumbnail", cut)
    thumbnails = next(one for one in jobs.PICTURES if one.job_type == jobs.THUMBNAIL)

    async def build(payload: dict[str, object]) -> None:
        await jobs.build_picture(
            await context_for(jobs.THUMBNAIL, payload),
            picture=thumbnails,
            settings=settings,
            hardware=hardware,
            allowed=_yes,
        )

    await build({"asset_id": asset_id})
    assert made == [], "no press: the picture is there, so nothing is made"

    await build({"asset_id": asset_id, AGAIN: True})
    assert made == [asset_id], "a press said again, so it is made again"


# --- a file that can never decode is refused once, not three times -------------------------------


async def _raising(error: Exception) -> Callable[[JobContext], Awaitable[None]]:
    async def handler(_: JobContext) -> None:
        raise error

    return handler


async def test_a_truncated_video_stream_is_refused_on_the_first_attempt(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
) -> None:
    """The first of two kinds of broken bytes: `Invalid NAL unit size` from a download that
    stopped part-way. Retried, it would be read three times and the row would then say "failed too
    often": tens of seconds of a worker, three times, for a file that can never succeed."""
    context = await context_for(jobs.PROBE, {"asset_id": ingested_video.asset.id})
    wrapped = jobs.recording_verdicts(
        await _raising(
            media.FFmpegError(
                "ffmpeg.exe failed: [h264 @ 0000] Invalid NAL unit size (1088342112 > 21767)."
            )
        ),
        VerdictProduct.PROBE,
    )

    with pytest.raises(jobs.Unusable) as raised:
        await wrapped(context)

    # Permanent at the queue: one failure with the reason, rather than two more reads of a file
    # already known to be bad.
    assert isinstance(raised.value, JobFailedPermanently)
    verdict = await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.PROBE)
    assert verdict is not None and verdict.transient is False


async def test_a_corrupt_still_is_refused_on_the_first_attempt(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
) -> None:
    """The second kind: two picture jobs at three attempts each on a PNG whose bytes are not a
    picture. Same landing, under the picture's own verdict rather than probing's."""
    context = await context_for(jobs.THUMBNAIL, {"asset_id": ingested_video.asset.id})
    wrapped = jobs.recording_verdicts(
        await _raising(
            media.FFmpegError(
                "ffmpeg.exe failed: [png @ 0000] Decoding error: Invalid data found when "
                "processing input"
            )
        ),
        VerdictProduct.THUMBNAILS,
    )

    with pytest.raises(jobs.Unusable):
        await wrapped(context)

    verdict = await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.THUMBNAILS)
    assert verdict is not None and verdict.transient is False


async def test_a_share_that_blinked_keeps_its_retries_and_leaves_no_verdict(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
) -> None:
    """The half that has to stay true. Marking every ffmpeg failure permanent would take a file
    that is perfectly good out of the library for ever, with nothing to notice, so an unknown
    refusal is passed straight through, retried, and nothing is written on the file."""
    context = await context_for(jobs.PROBE, {"asset_id": ingested_video.asset.id})
    wrapped = jobs.recording_verdicts(
        await _raising(media.FFmpegError("ffmpeg.exe failed: Input/output error")),
        VerdictProduct.PROBE,
    )

    with pytest.raises(media.FFmpegError) as raised:
        await wrapped(context)

    assert not isinstance(raised.value, JobFailedPermanently)
    assert await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.PROBE) is None
