# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding what else looks like a file, by whichever of the two ways can answer.

**The cheap tier answers on every install**, including one that has never turned search by meaning
on: every file already carries a fingerprint of what it looks like, and comparing two is
arithmetic. The better tier needs the description pass to have reached this particular file, which
on a library part-way through is most files, so "not indexed yet" has to fall back rather than
return nothing.

**Nothing here knows how a fingerprint is made.** Not its width, not how many bits differ before
two files are called alike. That belongs to the content layer, is being changed there, and is read
through the one interface it offers (`perceptual.apart`, `perceptual.NEAR_SHARE`), so a better
fingerprint improves this without this file being touched. The tests below say the same thing:
the edge they hold is the content layer's, never a number of their own.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass
from typing import Any

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.content import perceptual
from sift.slices.semantic import similar
from sift.slices.semantic.similar import SimilarFinder, Tier

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class Fingerprint:
    """The shape the content layer hands back. Stood in for, so this test needs no library."""

    asset_id: str
    identity: str = "digest"
    media_type: str = "video"
    phash: str | None = None
    videohash: str | None = None


class Reads:
    def __init__(self, *fingerprints: Fingerprint) -> None:
        self._fingerprints = list(fingerprints)

    async def fingerprints(self) -> list[Any]:
        return list(self._fingerprints)


def finder(*fingerprints: Fingerprint) -> SimilarFinder:
    return SimilarFinder(Reads(*fingerprints), None)  # type: ignore[arg-type]


async def test_the_nearest_fingerprints_come_back_closest_first() -> None:
    found = await finder(
        Fingerprint("mine", phash="0000000000000000"),
        Fingerprint("nearer", phash="0000000000000001"),
        Fingerprint("near", phash="0000000000000007"),
    ).perceptual("mine")

    assert found.tier is Tier.MATCHES
    assert [asset_id for asset_id, _ in found.neighbours] == ["nearer", "near"]


def _bits(count: int, width: int = 16) -> str:
    """A fingerprint of `width` hex characters with its lowest `count` bits set."""
    return f"{(1 << count) - 1:0{width}x}"


async def test_a_file_further_than_the_content_layers_edge_is_no_answer() -> None:
    """ "Similar to this" with Smart Search off says these are files whose frames nearly match. The
    whole library sorted by distance would put unrelated photographs there."""
    edge = int(perceptual.NEAR_SHARE * 64)
    found = await finder(
        Fingerprint("mine", phash=_bits(0)),
        Fingerprint("at the edge", phash=_bits(edge)),
        Fingerprint("past it", phash=_bits(edge + 1)),
        Fingerprint("unrelated", phash="ffffffffffffffff"),
    ).perceptual("mine")

    assert [asset_id for asset_id, _ in found.neighbours] == ["at the edge"]


async def test_a_count_on_a_long_fingerprint_is_ranked_as_the_share_it_is() -> None:
    """Fourteen bits apart across a video's thirty frames is nearly the same video; fourteen bits
    apart on one frame is another picture. Ranked by count, the photograph came first."""
    found = await finder(
        Fingerprint("mine", phash=_bits(0), videohash=_bits(0, 480)),
        Fingerprint("same video", phash=_bits(30), videohash=_bits(14, 480)),
        Fingerprint("a photograph", media_type="image", phash=_bits(9)),
    ).perceptual("mine")

    assert [asset_id for asset_id, _ in found.neighbours] == ["same video", "a photograph"]


async def test_a_file_is_never_its_own_lookalike() -> None:
    found = await finder(
        Fingerprint("mine", phash="0000000000000000"),
        Fingerprint("other", phash="0000000000000001"),
    ).perceptual("mine")

    assert [asset_id for asset_id, _ in found.neighbours] == ["other"]


async def test_a_file_with_no_fingerprint_of_its_own_finds_nothing() -> None:
    """Rather than comparing against nothing and calling everything a match."""
    found = await finder(
        Fingerprint("mine", phash=None),
        Fingerprint("other", phash="0000000000000001"),
    ).perceptual("mine")

    assert found.neighbours == ()


async def test_a_file_the_library_has_never_heard_of_finds_nothing() -> None:
    found = await finder(Fingerprint("other", phash="0000")).perceptual("missing")

    assert found.neighbours == ()


async def test_a_file_whose_fingerprint_cannot_be_compared_is_skipped() -> None:
    """Two fingerprints computed differently cannot be measured against each other. Skipped rather
    than given a made-up distance, which would rank a file that was never compared."""
    found = await finder(
        Fingerprint("mine", phash="0000000000000000"),
        Fingerprint("odd", phash="00"),
        Fingerprint("fine", phash="0000000000000001"),
    ).perceptual("mine")

    assert [asset_id for asset_id, _ in found.neighbours] == ["fine"]


async def test_the_closest_kind_answers_where_both_files_have_several() -> None:
    """Which kinds exist and what each means belongs to the content layer. This asks for each and
    takes the closest that answers: a clip cut from a video shares its cover bit for bit and its
    thirty frames sample a different stretch, and it nearly matches its source by the cover."""
    found = await finder(
        Fingerprint("mine", phash="0123456789abcdef", videohash=_bits(0, 480)),
        Fingerprint("source", phash="0123456789abcdef", videohash=_bits(200, 480)),
    ).perceptual("mine")

    assert found.neighbours == (("source", 0.0),)


async def test_only_as_many_as_were_asked_for_come_back() -> None:
    many = [Fingerprint(f"a{index}", phash=f"{index:016x}") for index in range(20)]
    found = await finder(Fingerprint("mine", phash="0" * 16), *many).perceptual("mine", limit=3)

    assert len(found.neighbours) == 3


async def test_an_asker_is_ranked_nothing_when_their_files_cannot_be_read() -> None:
    lookalikes = finder(Fingerprint("mine", phash="0" * 16), Fingerprint("other", phash="0" * 16))

    assert (await lookalikes.perceptual("mine", asker=Viewer("u", Role.GUEST))).neighbours == ()
    assert (await lookalikes.perceptual("mine")).neighbours == (("other", 0.0),)


async def test_the_comparison_leaves_the_loop_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """One comparison per fingerprinted file is a whole library's worth: on the loop it would hold
    every page and video while "Similar to this" was asked."""

    def slow(
        fingerprints: Any, asset_id: str, limit: int, among: Any
    ) -> tuple[tuple[str, float], ...]:
        time.sleep(0.3)
        return ()

    monkeypatch.setattr(similar, "_nearest", slow)
    ticks: list[float] = []

    async def tick() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.02)
    try:
        found = await finder(Fingerprint("mine", phash="0000000000000000")).perceptual("mine")
        await asyncio.sleep(0.02)
    finally:
        ticker.cancel()
    assert found.neighbours == ()
    longest = max(later - earlier for earlier, later in itertools.pairwise(ticks))
    assert longest < 0.15, f"the loop was held for {longest:.3f} s by the comparison"
