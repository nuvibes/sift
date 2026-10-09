# SPDX-License-Identifier: AGPL-3.0-or-later
"""The quality menu a playback plan carries: the file as it is, then the ladder under it."""

from __future__ import annotations

from sift.kernel.content import Asset
from sift.kernel.wire import Wire
from sift.slices.player import policy, tuning
from sift.slices.player.service import rung_query


class Quality(Wire):
    """One entry in the quality menu."""

    label: str
    """What the person reads. "Auto", "1080p", or the file's own size."""

    height: int | None = None
    """The height this asks for, or None for the file exactly as it is."""

    url: str
    """Where to attach for this choice. A different address, not a parameter on the same one."""

    auto: bool = False
    """True for the entry that lets the player choose, and change its mind while watching."""

    smooth: bool = True
    """False where this machine is not expected to keep up. Offered anyway, and marked.

    The person is entitled to overrule the arithmetic (the same judgement the transcode plan
    already makes), but they should be able to see which choice is the risky one before they make
    it rather than by watching it stutter."""

    detail: str | None = None
    """What this entry actually is, beside the name. Only the file's own entry carries one.

    "Original" is the right NAME for the file and a poor description of it: on a 4K video, 4K is
    on the menu as the top entry, under a name that says nothing about size. There is no rung at
    the file's own height and there should not be (re-encoding a file at the size it already is
    looks worse for no reason), so the answer is to say what "Original" comes to rather than to add
    a rung.

    Named by the SHORT side, which is how everybody names a video: a phone clip stored 1080 wide and
    1920 tall is a 1080p video, and naming it by its height would make it "1920p" sitting above a
    rung called "1080p" that is the same 1080 across, which this field must never do."""


def _qualities(
    asset: Asset,
    plan: policy.Plan,
    *,
    base: str,
    default_url: str,
    cpu_count: int,
    encoder_rate: float | None,
) -> list[Quality]:
    """The quality menu, biggest first; none for a photograph or a file with no rung under it.

    Auto is offered only once the file is being converted and the ladder exists.
    """
    if asset.media_type != "video":
        return []
    ladder = policy.rungs(asset.width, asset.height)
    if not ladder:
        return []

    entries: list[Quality] = []
    if plan.route is not policy.Route.DIRECT:
        entries.append(Quality(label="Auto", height=None, url=f"{base}/hls/master.m3u8", auto=True))

    # The file as it is, by the plan made; a reduced plan's entry is the full-size conversion.
    reduced = plan.scale_height is not None
    native = policy.projected_realtime(
        width=asset.width,
        height=asset.height,
        fps=asset.fps,
        vcodec=(asset.vcodec or "").lower(),
        cpu_count=cpu_count,
        encoder_rate=encoder_rate,
    )
    entries.append(
        Quality(
            # "Original", since any number is the wrong name for some shape of file.
            label="Original",
            height=None,
            url=f"{base}/hls/index.m3u8{rung_query(None)}" if reduced else default_url,
            smooth=(native is None or native >= tuning.MIN_REALTIME_RATIO)
            if reduced
            else plan.streamable,
            detail=policy.size_name(asset.width, asset.height),
        )
    )
    if reduced and all(rung.height != plan.scale_height for rung in ladder):
        # Named for what the reduced picture comes to: it matches no rung.
        across = policy.scaled_width(asset.width, asset.height, plan.scale_height or 0)
        entries.append(
            Quality(
                label=f"{min(across or 0, plan.scale_height or 0)}p",
                height=plan.scale_height,
                url=default_url,
                smooth=plan.streamable,
            )
        )

    for rung in ladder:
        ratio = policy.projected_realtime(
            width=asset.width,
            height=asset.height,
            fps=asset.fps,
            vcodec=(asset.vcodec or "").lower(),
            cpu_count=cpu_count,
            scale_height=rung.height,
            encoder_rate=encoder_rate,
        )
        entries.append(
            Quality(
                # Named by the short side, asked for by the height.
                label=f"{rung.short}p",
                height=rung.height,
                url=f"{base}/hls/index.m3u8{rung_query(rung.height)}",
                smooth=ratio is None or ratio >= tuning.MIN_REALTIME_RATIO,
            )
        )
    return entries
