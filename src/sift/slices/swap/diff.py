# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the guest wants of an offer, who the offered people are here, and the screen that asks.

## A file is wanted when nothing held looks like it, by the duplicate rule in force

The first comparison is the fingerprint, because it is taken from the pictures: it survives the
strip both sides make, a remux and a re-encode, where every exact hash does not. A file the guest
received in an earlier swap has different bytes from the host's original and the same fingerprint.
So an offered file is NOT wanted when the guest holds a file of the same kind whose fingerprint is
within the duplicate rule this device is set to, and whose running time is within its gap:

- **The rule is the duplicate queue's own**, read through `NearRule.from_bound`, which takes the
  very numbers the queue's statements bind (the dedup feature's `_filter_params`: `video_phash`,
  `phash` and `gap`). There is no second copy of a threshold here, so moving the Duplicates
  closeness control moves what a swap sends in the same step.
- **The gap reads exactly as the queue reads it** (`_DIALS_SHOW`): a length nobody knows, on
  either file, is not evidence the two differ, and a gap rule that is off compares no lengths.
- **Fingerprints compare only within one generation.** `fingerprint_version` travels with the
  offer; two different generations are two different numbers for one picture, so a file whose
  generation does not match anything held is wanted. Sending a duplicate costs time; skipping a new
  file would cost the file.
- **The exact keys confirm and never decide.** When the file the fingerprint found also agrees on
  `identity`, or on `oshash` and size, the screen says "the same file" rather than "a copy that
  looks the same". An exact key that agrees with a file the fingerprint did NOT find decides
  nothing: that is the rule as the design states it (the phash decides, the exact keys confirm).

What this does NOT cover: a GIF. A GIF's fingerprint is thirty frames judged by how many agree,
and that judgement lives in the dedup feature's matcher; until the matcher is readable from here
a GIF is always wanted.

The comparison is bounded the way the queue's scan is: every fingerprint is split into four
blocks, and a pair within N bits must have a block within N // 4 bits, so the held files are
indexed by block and only those sharing a near block are compared at all.

## People: a shared stash-box id first, then the name, then an alias

A match on a stash-box id is automatic because two installs linked to the same box name one entry
the same way. Otherwise the offered name, then each offered alias, is looked up by the kernel's own
matcher (`catalog.people_named`: a person's name or any alias, ignoring case). At each step exactly
one person is a match; two is a question this cannot answer, so the person arrives as somebody new
and the ordinary merge suggestions point at the likely match. Every candidate is filtered through
the scoped read of people, so a match never names somebody the viewer could not see.

## "Do not swap" shapes nothing the other side reads

A file marked "Do not swap" here (by itself or by anything it is filed under, or by "Do not
enrich") is left out of what the guest holds, so a match against it can never turn an offered
file into one the guest says it does not want. A person is matched for the landing only: the
answer SENT carries the wanted keys and no person at all (`Diff.for_the_other_side`), so the host
cannot learn which people this library holds, marked or not.

## The screen (up to ten people a row each; above that, the whole offer)

`assess` is the whole of it for the session: the assessment, the matches, the screen, and the
function that turns the guest's Take / Skip / untick into the diff frame. `screen` counts what
Take would bring per person (the files not already here, and the facial fingerprints that come
with them: a person offered for those alone is a row with no files) and the totals
count each file once; a file under two offered people counts under both rows and is counted once
in `shared`. `choose` is what the answer finally wants: a file is wanted unless it was unticked or
every offered person it is under was skipped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from itertools import combinations
from typing import Protocol

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import (
    files_kept_from_swaps,
    people_named,
)
from sift.kernel.content.perceptual import distance
from sift.kernel.db import Database
from sift.slices.swap import store
from sift.slices.swap.models import (
    Diff,
    HeldFile,
    HeldReason,
    MatchedBy,
    Offer,
    OfferedFile,
    OfferedPerson,
    OfferScreen,
    PersonRow,
)

#: Up to this many offered people, the screen gives each a row; above it, the whole offer in one go.
#: Ten rows fit one screen without scrolling.
ROWS_UP_TO = 10

#: How many of the largest people the whole-offer screen names above its list.
LARGEST = 5

#: How many blocks a fingerprint is split into for the search. See the module docstring.
_BLOCKS = 4

#: Which fingerprint each kind of file is compared by, and the rule's name for it.
_METHOD: Mapping[str, str] = {"video": "video_phash", "image": "phash"}


@dataclass(frozen=True, slots=True)
class NearRule:
    """How close two files' fingerprints must be, and how close their lengths, to be one file."""

    #: The most bits two whole-video fingerprints may differ by.
    video_phash: int
    #: The most bits two pictures' fingerprints may differ by.
    phash: int
    #: The widest gap in running time, in milliseconds; None compares no lengths.
    max_duration_gap_ms: int | None

    @classmethod
    def from_bound(cls, bound: Mapping[str, object]) -> NearRule:
        """The rule from the numbers the duplicate queue binds: `video_phash`, `phash`, `gap`."""
        video, picture, gap = bound["video_phash"], bound["phash"], bound["gap"]
        if not isinstance(video, int) or not isinstance(picture, int):
            raise TypeError("the duplicate rule's thresholds are whole numbers of bits")
        if gap is not None and not isinstance(gap, int):
            raise TypeError("the duplicate rule's gap is milliseconds or nothing")
        return cls(video_phash=video, phash=picture, max_duration_gap_ms=gap)

    def bits(self, method: str) -> int:
        return self.video_phash if method == "video_phash" else self.phash


@dataclass(frozen=True, slots=True)
class Held:
    """One file the guest holds, as much as the comparison needs."""

    asset_id: str
    media_type: str
    identity: str
    video_phash: str | None = None
    phash: str | None = None
    fingerprint_version: int | None = None
    duration_ms: int | None = None
    oshash: str | None = None
    size: int | None = None

    def fingerprint(self, method: str) -> str | None:
        return self.video_phash if method == "video_phash" else self.phash


class HeldRow(Protocol):
    """A row of the kernel's whole-library fingerprint read (`DuplicateReads.fingerprints`)."""

    @property
    def asset_id(self) -> str: ...
    @property
    def identity(self) -> str: ...
    @property
    def media_type(self) -> str: ...
    @property
    def phash(self) -> str | None: ...
    @property
    def video_phash(self) -> str | None: ...
    @property
    def duration_ms(self) -> int | None: ...
    @property
    def fingerprint_version(self) -> int | None: ...
    @property
    def oshash(self) -> str | None: ...
    @property
    def size_bytes(self) -> int | None: ...


def held_from(rows: Iterable[HeldRow]) -> list[Held]:
    """The guest's holdings, from the kernel's whole-library fingerprint read.

    That read and not the scoped one, because "is this already here" is a fact about the library
    rather than about a viewer: a file in the vault is held, and receiving it again would put a
    second copy beside it. Nothing it returns leaves this device: only which offered keys are
    wanted does.
    """
    return [
        Held(
            asset_id=row.asset_id,
            media_type=row.media_type,
            identity=row.identity,
            video_phash=row.video_phash,
            phash=row.phash,
            fingerprint_version=row.fingerprint_version,
            duration_ms=row.duration_ms,
            oshash=row.oshash,
            size=row.size_bytes,
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class Already:
    """Why one offered file is not wanted: which held file it is, and how sure."""

    reason: HeldReason
    asset_id: str


@dataclass(frozen=True, slots=True)
class Assessment:
    """The guest's first answer to an offer: what it lacks, and what it has and why.

    Never sent. The `Diff` that is sent carries only the wanted keys; which of the
    guest's files matched is the guest's own library.
    """

    wanted: tuple[str, ...]
    already: Mapping[str, Already]


class _Index:
    """The held files of one kind, by fingerprint block, for one rule's number of bits."""

    def __init__(self, held: Iterable[Held], method: str, bits: int) -> None:
        self._method = method
        self._bits = bits
        self._radius = max(0, bits) // _BLOCKS
        self._buckets: list[dict[int, list[Held]]] = [{} for _ in range(_BLOCKS)]
        for one in held:
            blocks = _blocks(one.fingerprint(method))
            if blocks is None or one.fingerprint_version is None:
                continue
            for position, value in enumerate(blocks):
                self._buckets[position].setdefault(value, []).append(one)

    def near(self, fingerprint: str | None) -> list[Held]:
        """Every held file that could be within the rule of this fingerprint. Complete: a file
        within the rule shares a block within `bits // 4` bits, and every such block is looked up."""
        blocks = _blocks(fingerprint)
        if blocks is None or self._bits < 0 or fingerprint is None:
            return []
        width = len(fingerprint) // _BLOCKS * 4
        masks = _masks(width, self._radius)
        found: dict[str, Held] = {}
        for position, value in enumerate(blocks):
            bucket = self._buckets[position]
            for mask in masks:
                for one in bucket.get(value ^ mask, ()):
                    found.setdefault(one.asset_id, one)
        return list(found.values())


def _blocks(fingerprint: str | None) -> tuple[int, ...] | None:
    if not fingerprint or len(fingerprint) % _BLOCKS:
        return None
    step = len(fingerprint) // _BLOCKS
    try:
        return tuple(
            int(fingerprint[start : start + step], 16) for start in range(0, len(fingerprint), step)
        )
    except ValueError:
        return None


@cache
def _masks(width: int, radius: int) -> tuple[int, ...]:
    """Every value of `width` bits with at most `radius` bits set, zero first."""
    out = [0]
    for count in range(1, min(radius, width) + 1):
        for bits in combinations(range(width), count):
            mask = 0
            for bit in bits:
                mask |= 1 << bit
            out.append(mask)
    return tuple(out)


def _gap_allows(offered: OfferedFile, held: Held, gap: int | None) -> bool:
    """The queue's length rule: unknown on either side, or no rule, is no objection."""
    if gap is None or offered.duration_ms is None or held.duration_ms is None:
        return True
    return abs(offered.duration_ms - held.duration_ms) <= gap


def _same_bytes(offered: OfferedFile, held: Held) -> bool:
    """Whether an exact key agrees: the identity, or the `oshash` with the size."""
    if offered.identity == held.identity:
        return True
    return (
        offered.oshash is not None
        and offered.oshash == held.oshash
        and held.size is not None
        and offered.size == held.size
    )


def wanted(offer: Offer, held: Iterable[Held], rule: NearRule) -> Assessment:
    """Which offered files the guest lacks, and for the rest what it holds and why."""
    holdings = list(held)
    indexes = {
        method: _Index(
            (one for one in holdings if _METHOD.get(one.media_type) == method),
            method,
            rule.bits(method),
        )
        for method in set(_METHOD.values())
    }
    keys: list[str] = []
    already: dict[str, Already] = {}
    for offered in offer.files:
        method = _METHOD.get(offered.kind)
        fingerprint = offered.video_phash if method == "video_phash" else offered.phash
        best: tuple[bool, int, str] | None = None
        if method is not None and offered.fingerprint_version is not None:
            for one in indexes[method].near(fingerprint):
                if one.fingerprint_version != offered.fingerprint_version:
                    continue
                apart = distance(fingerprint or "", one.fingerprint(method) or "")
                if apart is None or apart > rule.bits(method):
                    continue
                if not _gap_allows(offered, one, rule.max_duration_gap_ms):
                    continue
                candidate = (not _same_bytes(offered, one), apart, one.asset_id)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            keys.append(offered.key)
        else:
            already[offered.key] = Already(reason="near" if best[0] else "same", asset_id=best[2])
    return Assessment(wanted=tuple(keys), already=already)


@dataclass(frozen=True, slots=True)
class PersonMatch:
    """Who an offered person is on this device, or nobody yet."""

    person_id: str | None = None
    name: str | None = None
    by: MatchedBy | None = None


async def match_people(
    database: Database, access: Repository, viewer: Viewer, people: Sequence[OfferedPerson]
) -> dict[int, PersonMatch]:
    """Each offered person's match here, by index. See the module docstring for the order."""
    by_box = await store.people_by_box(database, [box for one in people for box in one.boxes])
    matches: dict[int, PersonMatch] = {}
    for index, person in enumerate(people):
        matches[index] = await _match_one(database, access, viewer, person, by_box)
    return matches


async def _match_one(
    database: Database,
    access: Repository,
    viewer: Viewer,
    person: OfferedPerson,
    by_box: Mapping[str, set[str]],
) -> PersonMatch:
    boxed = {person_id for box in person.boxes for person_id in by_box.get(box, set())}
    decided = await _decide(access, viewer, boxed, "box")
    if decided is not None:
        return decided
    decided = await _decide(access, viewer, set(await people_named(database, person.name)), "name")
    if decided is not None:
        return decided
    aliased: set[str] = set()
    for alias in person.aliases:
        aliased.update(await people_named(database, alias))
    return await _decide(access, viewer, aliased, "alias") or PersonMatch()


async def _decide(
    access: Repository, viewer: Viewer, candidates: set[str], by: MatchedBy
) -> PersonMatch | None:
    """One step's answer: the one person this viewer may see among the candidates, nobody when two
    answer (the search stops, see below), or None to go on to the next step."""
    if not candidates:
        return None
    visible = await access.visible_people(viewer, sorted(candidates))
    if not visible:
        return None
    if len(visible) > 1:
        # Two people answer to it here. Not a guess this may make: a wrong link files somebody's
        # files under somebody else, so the person arrives as new and a merge settles it.
        return PersonMatch()
    ((person_id, seen),) = visible.items()
    return PersonMatch(person_id=person_id, name=seen.name, by=by)


def screen(offer: Offer, assessment: Assessment, matches: Mapping[int, PersonMatch]) -> OfferScreen:
    """What the guest's offer screen draws: a row per person up to ten, the whole offer above."""
    wanted_keys = set(assessment.wanted)
    rows: list[PersonRow] = []
    per_files: list[int] = [0] * len(offer.people)
    per_bytes: list[int] = [0] * len(offer.people)
    per_held: list[int] = [0] * len(offer.people)
    total_files = total_bytes = shared = unfiled_files = unfiled_bytes = 0
    for one in offer.files:
        people = set(one.people)
        if one.key not in wanted_keys:
            for index in people:
                per_held[index] += 1
            continue
        total_files += 1
        total_bytes += one.size
        if len(people) >= 2:
            shared += 1
        if not people:
            unfiled_files += 1
            unfiled_bytes += one.size
        for index in people:
            per_files[index] += 1
            per_bytes[index] += one.size
    for index, person in enumerate(offer.people):
        match = matches.get(index) or PersonMatch()
        rows.append(
            PersonRow(
                index=index,
                name=person.name,
                files=per_files[index],
                bytes=per_bytes[index],
                held=per_held[index],
                faces=len(person.faces.faces) if person.faces is not None else 0,
                confirmed=person.faces.confirmed if person.faces is not None else None,
                match_id=match.person_id,
                match_name=match.name,
                matched_by=match.by,
            )
        )
    held = [
        HeldFile(key=one.key, title=one.title, reason=assessment.already[one.key].reason)
        for one in offer.files
        if one.key in assessment.already
    ]
    whole = len(rows) > ROWS_UP_TO
    largest = sorted(rows, key=lambda row: (-row.bytes, -row.files, row.index))[:LARGEST]
    return OfferScreen(
        layout="whole" if whole else "rows",
        rows=largest if whole else rows,
        everyone=rows if whole else [],
        offered_files=len(offer.files),
        offered_bytes=sum(one.size for one in offer.files),
        files=total_files,
        bytes=total_bytes,
        shared=shared,
        unfiled_files=unfiled_files,
        unfiled_bytes=unfiled_bytes,
        held=held,
    )


def choose(
    offer: Offer,
    assessment: Assessment,
    *,
    skipped: Iterable[int] = (),
    unticked: Iterable[str] = (),
) -> list[str]:
    """The keys the guest finally wants, in the offer's order.

    A file is wanted when the guest lacks it, nobody unticked it, and it is under no offered person
    or under at least one who was not skipped: skipping a person leaves their files to anybody else
    offered on them who was taken.
    """
    lacking = set(assessment.wanted)
    passed = set(skipped)
    dropped = set(unticked)
    return [
        one.key
        for one in offer.files
        if one.key in lacking
        and one.key not in dropped
        and (not one.people or any(index not in passed for index in one.people))
    ]


def answer(
    offer: Offer,
    assessment: Assessment,
    matches: Mapping[int, PersonMatch],
    *,
    skipped: Iterable[int] = (),
    unticked: Iterable[str] = (),
) -> Diff:
    """The diff frame: the keys wanted after the guest's choices, and who each person is here."""
    return Diff(
        wanted=choose(offer, assessment, skipped=skipped, unticked=unticked),
        people={
            index: (matches.get(index) or PersonMatch()).person_id
            for index in range(len(offer.people))
        },
    )


class Choices(Protocol):
    """What the guest chose on the offer screen: the offered people it skipped, the files it
    unticked. The session's own record of the answer has this shape."""

    @property
    def skipped(self) -> Iterable[int]: ...
    @property
    def unticked(self) -> Iterable[str]: ...


async def assess(
    database: Database,
    access: Repository,
    viewer: Viewer,
    offer: Offer,
    *,
    held: Iterable[Held],
    rule: NearRule,
) -> tuple[OfferScreen, Callable[[Choices], Diff]]:
    """The guest's reading of an offer the moment it arrives: what the offer screen draws, and the
    function that makes the diff frame from the answer. Nothing leaves this device until that
    function is called, and then only the wanted keys (see the module docstring)."""
    kept = await files_kept_from_swaps(database)
    judged = wanted(offer, (one for one in held if one.asset_id not in kept), rule)
    matches = await match_people(database, access, viewer, offer.people)

    def reply(choices: Choices) -> Diff:
        return answer(offer, judged, matches, skipped=choices.skipped, unticked=choices.unticked)

    return screen(offer, judged, matches), reply
