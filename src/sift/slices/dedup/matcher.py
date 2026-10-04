# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding the pairs worth asking about, without comparing everything to everything.

The naive version of this is four lines and it works perfectly on a test library. Every asset
against every other asset is n(n-1)/2 comparisons: 20,000 at two hundred files, and over a
billion at fifty thousand. The second number is not slow, it is a job that never finishes, on the
machine of somebody whose library is the reason they wanted this feature.

So nothing here compares two fingerprints until something cheaper has said they might be close.

## How the search is bounded

A fingerprint is 64 bits. Split it into four 16-bit blocks and the pigeonhole principle does the
rest: if two fingerprints differ by no more than 11 bits in total, then at least one of their four
blocks differs by no more than 2, because four blocks each differing by 3 or more is already 12
bits. So indexing every asset under its four block values, and looking up every value within 2
bits of each of the query's blocks, cannot miss such a pair. It is a filter, not an approximation.

That costs 137 lookups per block (one exact, 16 at one bit, 120 at two) and 548 per asset,
which is a constant that does not grow with the library.

## What that does and does not buy

On uniformly random fingerprints, this makes **1/120th** the comparisons the all-pairs
version makes, and the ratio holds at every size tried: 104 thousand against 12.5 million at five
thousand assets, 417 thousand against 50 million at ten thousand, 1.7 million against 200 million
at twenty thousand: 120x, 120x, 120x.

**It is not sublinear in the way "bucket it and the problem goes away" suggests, and the number
above is a constant factor rather than a change of shape.** Each of the 548 lookups returns roughly
one asset per 65,536 in the library, so the candidates per asset still grow with the library, and
doubling the library still roughly quadruples the work. A hundred and twenty times less of
a square is still a square.

That is a real answer rather than a disappointing one, and it is worth being exact about which:

- Fifty thousand assets is about ten million comparisons rather than one and a quarter billion:
  seconds against most of an hour. Two hundred thousand is a job that finishes rather than one that
  does not. That is the difference this makes, and it is the difference that decides whether this
  feature works on a mini PC or only on a workstation.
- Going genuinely sub-quadratic means giving up completeness: accepting that some duplicates are
  never found. A duplicate-finder that quietly misses duplicates is the exact failure this file
  exists to prevent, because it looks like a working one. The trade was available and was refused.

A test pins the count at ten thousand assets, so the all-pairs version cannot come back unnoticed.

## Why the threshold is 11 and not 12

A re-encode sits at 0 bits, a 4x downscale at 4, a brightness shift at 4, and two genuinely
different pictures at 26 to 38. The decision boundary is anywhere in that gap; 12 is the obvious
figure.

11 is used here instead, for one reason: it is exactly the distance the block search is *complete*
for. At 12 the search would still find nearly every pair, but "nearly" would be a property nothing
could state precisely, and a duplicate-finder that silently misses some duplicates is the failure
this whole file exists to avoid: the one that looks like it is working. Nothing real sits between
11 and 12: the nearest measured non-duplicate is more than twice as far away.

## Videos are judged by one number, and photos and GIFs are not

A video's fingerprint is a single 64-bit number covering the whole video: twenty-five moments laid
out as a grid and reduced once. That is the fingerprint the public stash-boxes share, and a video
carries it so that ONE fingerprint answers both "is this the same as that" and "does anybody else
know what this is". Comparing two of them is a bit count and nothing more.

A photograph carries one still's fingerprint and a GIF carries thirty of them, and neither has a
whole-video number, because nobody fingerprints photographs or GIFs: there would be nothing on earth
to compare the value to. So the three kinds are searched separately and never against each other,
and the section below describes the GIF case only.

### What that trade costs, stated rather than discovered

The thirty-frame comparison below tolerates a copy that starts at a different point; a single
whole-video number does not. Trim thirty seconds off the front and the twenty-five sample moments
all land somewhere else, so the number changes wholesale rather than a little. That is a real loss
against a per-frame comparison, taken deliberately in exchange for a fingerprint the rest of the
world recognises. What replaces it in practice is the length check: two files of the same video
almost always agree on how long they are, and two files that do not agree are not a pair anybody
wanted found.

## A GIF is judged by agreement, not by a total

A GIF's fingerprint is thirty frames laid end to end, so the obvious whole-loop threshold is thirty
times the frame one. That number is wrong in a way worth naming. A copy with a five-frame caption
added has twenty-five frames that match perfectly and five that do not match at all, and the five
drag the total past any threshold the twenty-five would pass. Summing hides the shape of the
disagreement, which is the only interesting thing about it.

So a GIF pair is scored by *how many frames agree*, and the pair is reported with the number that
did not. Twenty-four of thirty is the bar: a fifth of it may differ and it is still the same
thing, which is about what a caption or a trimmed end costs.

## The timeline shift

A copy that starts a little earlier samples thirty different moments, so every frame moves and a
position-by-position comparison sees nothing in common. No frame count fixes this and a finer
sample does not either; it needs a comparison that does not care about position.

It gets one here, and almost for free: the same block index that bounds the search is built over
*individual frames*, so two loops become candidates when any one of their sixty frames is close to
another, wherever it sits in either. Agreement is then counted twice, once by position and once
as a set, and the better of the two answers wins. A shifted copy scores nothing positionally and
near-perfectly as a set, which is the correct answer arrived at by the cheaper route.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.content import perceptual
from sift.kernel.content.duplicates import Fingerprint
from sift.kernel.content.perceptual import distance
from sift.kernel.media import MAX_DURATION_GAP_MS

#: Bits in one frame's fingerprint, as text: 16 hex characters.
FRAME_HEX = 16

#: How many frames a GIF's fingerprint carries.
VIDEO_FRAMES = 30

#: A whole GIF fingerprint's width in hex characters. A value of any other width does not hold
#: thirty frames and is comparable to nothing, so it is skipped rather than compared, which is what
#: `distance` would decide anyway.
VIDEO_HEX = FRAME_HEX * VIDEO_FRAMES

#: What each kind of asset is called on its row. Matched on directly, because which fingerprint an
#: asset is judged by is decided by what it IS, never by which column happens to be filled in.
_IMAGE = "image"
_GIF = "gif"
_VIDEO = "video"

#: Bits that may differ before two frames are different pictures. See the note above on why this is
#: one below the measured figure. The content layer's, so the lookalikes' cheap tier holds the same
#: edge (`perceptual.NEAR_SHARE`).
NEAR_FRAME_BITS = perceptual.NEAR_FRAME_BITS

#: How many of a GIF's thirty frames must agree before it is the same GIF.
NEAR_VIDEO_FRAMES = 24

#: The block search. Four blocks of four hex characters each, and every block value within two bits
#: is looked up, which together are complete for any pair within NEAR_FRAME_BITS.
_BLOCKS = 4
_BLOCK_HEX = FRAME_HEX // _BLOCKS
_BLOCK_BITS = _BLOCK_HEX * 4
_BLOCK_RADIUS = 2


class Accuracy(StrEnum):
    """How alike two videos have to look before Sift asks about them.

    Named rather than numbered, and that is the whole point of it. Underneath this is a count of
    differing bits, and "6" means nothing to the person reading it: there is no way to know from
    the number whether it is generous or strict, and the failure mode of setting it too low is
    silence: duplicates that exist and are never mentioned. A short list of names can be explained
    in the sentence next to it; a slider cannot.

    The numbers behind the names come from measurement rather than from anybody's default. Two
    encodes of one video, submitted by different people to a public stash-box, sit two bits apart; a
    more distant encode of the same video reaches six; and six is also where the ecosystem warns that
    unrelated videos start colliding. So the useful range is entirely inside nought to six, and the
    names divide it.
    """

    EXACT = "exact"
    """Bit for bit. The same video, encoded the same way, which after re-encoding is rare."""

    HIGH = "high"
    """Two bits. Where two encodes of one video submitted by two people were measured to sit."""

    MEDIUM = "medium"
    """Four bits. The default: it reaches a distant encode without reaching the collision edge."""

    LOW = "low"
    """Six bits. The outer edge, where unrelated videos begin to collide. Expect false alarms."""


#: How far apart a pair may be, per fingerprint kind, at each named level.
#:
#: One table per kind, because the three measure different things and a single column of numbers
#: would be three different claims wearing one set of names. The names still mean the same thing
#: across all three (exact is bit-for-bit, and low is the outer edge), and only the numbers
#: behind them differ.
#:
#: The outer edges are not the same number and are not arbitrary. For a video, six is where the
#: ecosystem warns that unrelated videos start colliding, which is a fact about the fingerprint the
#: public stash-boxes share. For a photograph, the nearest measured non-duplicate is 26 bits away
#: and the block search is complete to eleven, so eleven is the edge and nothing real sits below it.
#: For a GIF the unit is frames rather than bits (six of thirty may differ), and it coincides
#: with the video figure by arithmetic rather than by meaning.
LEVELS: dict[str, dict[Accuracy, int]] = {
    "phash": {
        Accuracy.EXACT: 0,
        Accuracy.HIGH: 2,
        Accuracy.MEDIUM: 4,
        Accuracy.LOW: NEAR_FRAME_BITS,
    },
    "video_phash": {
        Accuracy.EXACT: 0,
        Accuracy.HIGH: 2,
        Accuracy.MEDIUM: 4,
        Accuracy.LOW: 6,
    },
    "videohash": {
        Accuracy.EXACT: 0,
        Accuracy.HIGH: 2,
        Accuracy.MEDIUM: 4,
        Accuracy.LOW: VIDEO_FRAMES - NEAR_VIDEO_FRAMES,
    },
}

#: What a scan actually files: the most generous level there is, for every kind.
#:
#: **The scan is deliberately wider than anybody's setting**, and that is the whole arrangement. A
#: narrower level is a filter over rows already written, so moving the control answers instantly
#: and can never require the library to be compared again. The alternative (storing the level and
#: scanning to it) means every change to the dial is an hour of work on a large library, and a
#: dial nobody can afford to move is a dial that does not exist.
#:
#: It costs rows rather than time. The comparisons are fixed by the block search and do not depend
#: on where the cutoff falls; only how many of them end up in the table does.
WIDEST: dict[str, int] = {kind: table[Accuracy.LOW] for kind, table in LEVELS.items()}

#: What the level control starts on, and what a reader who has never touched it is looking at.
DEFAULT_ACCURACY = Accuracy.MEDIUM

#: How far apart two videos' running times may be and still be worth showing, in milliseconds.
#:
#: A second opinion that costs nothing and is worth more than any threshold tweak: two unrelated
#: videos that happen to collide on a fingerprint almost never also agree on how long they are. It
#: cuts false alarms from the one direction a threshold cannot.
#:
#: Applied when the queue is read rather than when it is written, for the reason `WIDEST` gives:
#: the gap is recorded on the pair, so tightening or loosening it is a different question asked of
#: the same rows. The scan itself applies no length rule at all.
#:
#: The number itself lives in the kernel, because the same question is asked of a public stash-box
#: elsewhere and one of the two would eventually be edited without the other. See its comment there
#: for what ten seconds was measured against.
DEFAULT_MAX_DURATION_GAP_MS = MAX_DURATION_GAP_MS


def _variants(value: int) -> Iterator[int]:
    """Every block value within `_BLOCK_RADIUS` bits of this one, including it.

    Built by flipping bits rather than by scanning the bucket space: 137 of them for a 16-bit
    block, against 65,536 buckets to walk. The count is fixed, so this is the part of the search
    that does not grow with the library.
    """
    yield value
    for first in range(_BLOCK_BITS):
        yield value ^ (1 << first)
        for second in range(first + 1, _BLOCK_BITS):
            yield value ^ (1 << first) ^ (1 << second)


def _blocks(fingerprint: str) -> tuple[int, ...]:
    """One frame fingerprint as its four block values."""
    return tuple(
        int(fingerprint[at : at + _BLOCK_HEX], 16) for at in range(0, FRAME_HEX, _BLOCK_HEX)
    )


def _frames(videohash: str) -> list[str]:
    """A video fingerprint as its thirty frame fingerprints."""
    return [videohash[at : at + FRAME_HEX] for at in range(0, len(videohash), FRAME_HEX)]


@dataclass(frozen=True, slots=True)
class Pair:
    """Two assets that look alike, and by how much they do not.

    `distance` means something different per method, because the three methods measure different
    things and pretending otherwise would make the number meaningless. For `phash` it is bits that
    differ, out of 63. For `videohash` it is *frames* that differ, out of thirty. For `video_phash`
    it is bits again, out of 64. All three answer "how much of this is not the same", which is what
    the column is for, and the method is stored beside it so nothing has to guess which scale it is
    reading, including the screen, which turns each into a sentence rather than showing a figure
    whose units the reader cannot know.
    """

    asset_a: str
    asset_b: str
    method: str
    distance: int
    #: How far apart the two run, in milliseconds, or None when that cannot be said: either
    #: because one of them has no duration recorded yet, or because the kind has no duration at
    #: all. None is not zero and must not be read as agreement: it means unknown, and the queue
    #: shows such a pair rather than hiding it on a fact nobody has.
    duration_gap_ms: int | None = None


class _Index:
    """Frame fingerprints, filed under their block values so near ones can be found."""

    def __init__(self) -> None:
        self._buckets: list[dict[int, list[int]]] = [{} for _ in range(_BLOCKS)]

    def add(self, fingerprint: str, owner: int) -> None:
        for position, value in enumerate(_blocks(fingerprint)):
            self._buckets[position].setdefault(value, []).append(owner)

    def near(self, fingerprint: str) -> set[int]:
        """Everything filed close enough to this frame to be worth actually comparing."""
        found: set[int] = set()
        for position, value in enumerate(_blocks(fingerprint)):
            bucket = self._buckets[position]
            for variant in _variants(value):
                owners = bucket.get(variant)
                if owners is not None:
                    found.update(owners)
        return found


class Matcher:
    """Compares fingerprints, and counts how many comparisons it took.

    The count is not instrumentation. It is the only way to state the property that makes this
    feature work on a large library, and a test asserts on it, so it is part of what this class
    is for, not something bolted to the side of it.
    """

    def __init__(self) -> None:
        self.comparisons = 0

    def _distance(self, first: str, second: str) -> int | None:
        """`distance`, counted.

        None comes straight back out. It means the two are not comparable (a still against a
        video, or a fingerprint of the wrong width) and it is emphatically not
        zero. Reading it as zero would make every photograph a perfect duplicate of every video,
        and the queue would fill with pairs nobody could act on.
        """
        self.comparisons += 1
        return distance(first, second)

    def find(self, fingerprints: Sequence[Fingerprint]) -> list[Pair]:
        """Every near-duplicate pair in the library, exact duplicates excluded.

        The three kinds are searched separately and never against each other, sorted by what the
        asset IS rather than by which columns happen to be filled in. A video is judged on its
        whole-video fingerprint, a GIF on its thirty frames, a photograph on its one.

        Sorting by kind rather than by column is what keeps an older row safe. A video that
        carries only a thirty-frame value is not comparable to what videos are judged on: judging
        it by whichever column was populated would compare the whole-video fingerprints of some
        files against the frame fingerprints of others and report the differences as similarity.
        Such a file is skipped here and picked up by the pass that fills the whole-video one in.
        """
        # An asset with no usable fingerprint is dropped here rather than trusted to have been
        # filtered upstream. A still whose phash is the wrong width, or absent, has nothing to
        # compare, and letting it through would mean every loop below carrying a check for it.
        stills = [
            row
            for row in fingerprints
            if row.media_type == _IMAGE and _usable(row.phash, FRAME_HEX)
        ]
        loops = [
            row
            for row in fingerprints
            if row.media_type == _GIF and _usable(row.videohash, VIDEO_HEX)
        ]
        scenes = [
            row
            for row in fingerprints
            if row.media_type == _VIDEO and _usable(row.video_phash, FRAME_HEX)
        ]
        return self._still_pairs(stills) + self._loop_pairs(loops) + self._scene_pairs(scenes)

    # --- videos ---------------------------------------------------------------------------

    def _scene_pairs(self, scenes: Sequence[Fingerprint]) -> list[Pair]:
        """Videos that look like the same video.

        The same bounded search the stills use, and it is complete here by a wider margin: the
        block index cannot miss a pair within eleven bits, and the most generous named level asks
        for six. So nothing that would be reported can be filtered out by the search, and that is a
        property rather than a hope.

        Filed at that most generous level whatever the reader's setting says, and with the length
        difference written down rather than acted on. See `WIDEST`. Both dials then work on the
        rows instead of on the library.
        """
        index = _Index()
        found: list[Pair] = []
        widest = WIDEST["video_phash"]

        for position, row in enumerate(scenes):
            mine = row.video_phash or ""
            for other in sorted(index.near(mine)):
                candidate = scenes[other]
                apart = self._distance(mine, candidate.video_phash or "")
                if apart is None or apart > widest:
                    continue
                pair = _pair(candidate, row, "video_phash", apart, gap=_length_gap(row, candidate))
                if pair is not None:
                    found.append(pair)
            index.add(mine, position)

        return found

    # --- stills ---------------------------------------------------------------------------

    def _still_pairs(self, stills: Sequence[Fingerprint]) -> list[Pair]:
        index = _Index()
        found: list[Pair] = []

        for position, row in enumerate(stills):
            mine = row.phash or ""
            for other in sorted(index.near(mine)):
                candidate = stills[other]
                apart = self._distance(mine, candidate.phash or "")
                if apart is None or apart > WIDEST["phash"]:
                    continue
                pair = _pair(candidate, row, "phash", apart)
                if pair is not None:
                    found.append(pair)
            index.add(mine, position)

        return found

    # --- GIFs -----------------------------------------------------------------------------

    def _loop_pairs(self, videos: Sequence[Fingerprint]) -> list[Pair]:
        index = _Index()
        found: list[Pair] = []

        for position, row in enumerate(videos):
            mine = row.videohash or ""
            frames = _frames(mine)

            for other in sorted(self._video_candidates(index, frames)):
                candidate = videos[other]
                differing = self._frames_apart(candidate.videohash or "", mine)
                if differing is None or differing > WIDEST["videohash"]:
                    continue
                pair = _pair(candidate, row, "videohash", differing)
                if pair is not None:
                    found.append(pair)

            for frame in frames:
                index.add(frame, position)

        return found

    def _video_candidates(self, index: _Index, frames: Iterable[str]) -> set[int]:
        """Videos worth actually comparing: the ones enough of these frames landed near.

        *How many* frames hit, not whether any did. One shared frame is nothing (a fade to black,
        a title card, a common establishing shot) and treating it as grounds for comparison makes
        every video in the library a candidate for every other, which is how a bounded search becomes
        a slower version of the unbounded one.

        The bar is the same as agreement's, and that is what makes it safe rather than a guess: a
        frame within the threshold of one in the other video always lands in the same bucket, so a
        pair that would agree on twenty-four frames must hit on at least twenty-four here. Nothing
        that would be reported can be filtered out by this.
        """
        hits: dict[int, int] = {}
        for frame in frames:
            for owner in index.near(frame):
                hits[owner] = hits.get(owner, 0) + 1
        return {owner for owner, count in hits.items() if count >= NEAR_VIDEO_FRAMES}

    def _frames_apart(self, first: str, second: str) -> int | None:
        """How many of thirty frames differ, by the kinder of the two readings.

        None when the two cannot be compared at all, which for a video means one of them is not
        thirty frames wide. It is not zero, and
        treating it as zero here would mean reporting a truncated fingerprint as a perfect match
        for everything else that was also truncated.
        """
        if len(first) != VIDEO_HEX or len(second) != VIDEO_HEX:
            return None

        mine = _frames(first)
        theirs = _frames(second)

        in_place = sum(1 for a, b in zip(mine, theirs, strict=True) if self._agree(a, b))
        if in_place >= NEAR_VIDEO_FRAMES:
            return VIDEO_FRAMES - in_place

        # The shifted case. Position said no, so ask whether the frames are the same frames at all.
        # Only reached when the cheap answer already failed and the index already said most of these
        # frames land near each other, so the cost falls on pairs that earned it.
        as_a_set = min(self._paired_off(mine, theirs), self._paired_off(theirs, mine))
        return VIDEO_FRAMES - max(in_place, as_a_set)

    def _paired_off(self, mine: list[str], theirs: list[str]) -> int:
        """How many frames pair up one-to-one. A frame answers for at most one other.

        The one-to-one part is the whole of it. Counting each frame that has *any* partner lets a
        single frame in one video answer for all thirty of the other's, so a static shot, a
        slideshow, or anything holding one image reads as a perfect match for every video it shares
        a frame with, and lands at the top of the queue as the most confident duplicate in the
        library. It is the most wrong a duplicate-finder can be while still looking like it works.

        Greedy rather than a maximum matching: the answer can differ by a frame or two from the
        best possible pairing, which is well inside a threshold of twenty-four out of thirty, and a
        maximum matching is a great deal of machinery for that. What it must not be is
        order-dependent, so the caller takes the smaller of both directions.
        """
        spare = list(theirs)
        paired = 0
        for frame in mine:
            for at, other in enumerate(spare):
                if self._agree(frame, other):
                    spare.pop(at)
                    paired += 1
                    break
        return paired

    def _agree(self, first: str, second: str) -> bool:
        apart = self._distance(first, second)
        return apart is not None and apart <= NEAR_FRAME_BITS


def _usable(fingerprint: str | None, width: int) -> bool:
    """Whether a fingerprint is the width its kind is always stored at.

    A video fingerprint that is not thirty frames wide is comparable to nothing at all, so it
    would match no duplicate, silently. Dropping it here means the asset is skipped rather than
    compared and quietly found to be unlike everything.
    """
    return fingerprint is not None and len(fingerprint) == width


def _length_gap(first: Fingerprint, second: Fingerprint) -> int | None:
    """How far apart these two run, in milliseconds, or None when nobody can say.

    None where either duration is missing, and it is emphatically not zero. An absent duration is
    not evidence that two files agree OR that they differ, so the honest record is that the question
    has no answer, and the queue shows such a pair rather than deciding on a fact it does not
    have. Reading it as zero would make every file not yet fully probed look like a perfect length
    match, which is the failure this whole feature is written to avoid.
    """
    if first.duration_ms is None or second.duration_ms is None:
        return None
    return abs(first.duration_ms - second.duration_ms)


def _pair(
    first: Fingerprint,
    second: Fingerprint,
    method: str,
    apart: int,
    *,
    gap: int | None = None,
) -> Pair | None:
    """A pair, in a settled order, or nothing, if these two are the same bytes.

    The digest check is what keeps exact duplicates out of the review queue, and it is here rather
    than in the caller because this is the only place that has both halves in hand. Two assets with
    one digest should be impossible (identical bytes are one asset with two locations), but if a
    library ever holds two, the honest place for them is the reclaim view, not a screen asking
    somebody to judge whether they look alike.

    The order is by id, always, so the same pair found from either direction produces the same row
    and a re-scan collides with what it wrote last time instead of doubling it.
    """
    if first.identity == second.identity:
        return None
    asset_a, asset_b = sorted((first.asset_id, second.asset_id))
    return Pair(
        asset_a=asset_a, asset_b=asset_b, method=method, distance=apart, duration_gap_ms=gap
    )
