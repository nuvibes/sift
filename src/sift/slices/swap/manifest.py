# SPDX-License-Identifier: AGPL-3.0-or-later
"""The manifest: an offer as the groups Sift files things under, each with its totals and state.

One list, written when the offer is read and filled in as the swap goes: the same rows answer
what is offered, what is taken, what is crossing and what has landed. A file sits under every
offered person on it; a file under nobody sits under exactly one other group, its Site if it has
one, else Songs if it carries a song, else the loose files. So the totals count each file once.

Collections and Photo Sets are not carried by an offer, so the side that receives never draws
them; the side that offers does, from its own picks (`weight.py`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from sift.slices.swap.models import (
    GROUP_SAMPLE,
    GroupKind,
    GroupState,
    HeldAs,
    HeldReason,
    ManifestFile,
    ManifestGroup,
    Offer,
    OfferedFile,
    PersonRow,
)

SONGS = "songs"
LOOSE = "loose"

#: The screen's word for a held file, by the diff's.
_HELD_AS: Mapping[HeldReason, HeldAs] = {"same": "same", "near": "look"}


def person_key(index: int) -> str:
    return f"person:{index}"


def site_key(index: int) -> str:
    return f"site:{index}"


@dataclass(frozen=True, slots=True)
class Grouping:
    """Which groups an offer has, in order, and which of its files sit under each."""

    keys: tuple[str, ...]
    members: Mapping[str, tuple[int, ...]]
    #: Per file, by its place in the offer: the groups it sits under.
    of_file: tuple[tuple[str, ...], ...]


def grouping(offer: Offer) -> Grouping:
    """The offer's groups: every offered person (one offered for faces alone has no files), then
    each Site, Songs and the loose files that hold any."""
    site_of = {one.name: number for number, one in enumerate(offer.sites)}
    members: dict[str, list[int]] = {person_key(n): [] for n in range(len(offer.people))}
    members.update({site_key(n): [] for n in range(len(offer.sites))})
    members[SONGS] = []
    members[LOOSE] = []
    of_file: list[tuple[str, ...]] = []
    for number, one in enumerate(offer.files):
        keys = _keys_of(one, site_of)
        for key in keys:
            members[key].append(number)
        of_file.append(keys)
    keys = tuple(key for key, files in members.items() if files or key.startswith("person:"))
    return Grouping(
        keys=keys,
        members={key: tuple(members[key]) for key in keys},
        of_file=tuple(of_file),
    )


def _keys_of(one: OfferedFile, site_of: Mapping[str, int]) -> tuple[str, ...]:
    if one.people:
        return tuple(person_key(index) for index in dict.fromkeys(one.people))
    if one.site is not None and one.site in site_of:
        return (site_key(site_of[one.site]),)
    if one.song is not None:
        return (SONGS,)
    return (LOOSE,)


def kind_of(key: str) -> GroupKind:
    """A group's kind, from its key."""
    head = key.split(":", 1)[0]
    if head == "person":
        return "person"
    if head == "site":
        return "site"
    return "songs" if key == SONGS else "loose"


def name_of(offer: Offer, key: str) -> str | None:
    """What a group is called on the offer: a person's or a Site's name, else nothing."""
    head, _, place = key.partition(":")
    if head == "person":
        return offer.people[int(place)].name
    if head == "site":
        return offer.sites[int(place)].name
    return None


def index_of(key: str) -> int | None:
    """A person's place in the offer, the one Take and Skip already name them by."""
    head, _, place = key.partition(":")
    return int(place) if head == "person" else None


def held_as(held: Mapping[str, HeldReason]) -> dict[str, HeldAs]:
    """The diff's reasons in the screen's words, by file key."""
    return {key: _HELD_AS[reason] for key, reason in held.items()}


def file_of(one: OfferedFile, held: Mapping[str, HeldAs] | None) -> ManifestFile:
    return ManifestFile(
        key=one.key,
        title=one.title,
        kind=one.kind,
        size=one.size,
        duration_ms=one.duration_ms,
        width=one.width,
        height=one.height,
        held=None if held is None else held.get(one.key),
    )


def files_of_group(
    offer: Offer,
    groups: Grouping,
    key: str,
    *,
    held: Mapping[str, HeldAs] | None,
    offset: int,
    limit: int,
) -> tuple[list[ManifestFile], int]:
    """A page of one group's files, in the offer's order, and how many it holds. KeyError for a
    group the offer does not have."""
    members = groups.members[key]
    page = members[max(0, offset) : max(0, offset) + max(0, limit)]
    return [file_of(offer.files[n], held) for n in page], len(members)


def keys_skipped(
    offer: Offer, groups: Grouping, skipped: Iterable[str]
) -> tuple[set[int], set[str]]:
    """Whole groups skipped, as the answer already says them: a person by their place, any other
    group as its files unticked. ValueError for a group the offer does not have."""
    people: set[int] = set()
    unticked: set[str] = set()
    for key in skipped:
        if key not in groups.members:
            raise ValueError("That group isn't in the offer.")
        index = index_of(key)
        if index is not None:
            people.add(index)
        else:
            unticked.update(offer.files[n].key for n in groups.members[key])
    return people, unticked


@dataclass(frozen=True, slots=True)
class Progress:
    """Where the files of a direction stand, by their place in the offer."""

    answered: bool = False
    wanted: Collection[int] = ()
    done: Collection[int] = ()
    failed: Collection[int] = ()
    crossing: Collection[int] = ()


def _counts(groups: Grouping, indexes: Iterable[int], within: Collection[int]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for number in indexes:
        if number not in within or not 0 <= number < len(groups.of_file):
            continue
        for key in groups.of_file[number]:
            counted[key] = counted.get(key, 0) + 1
    return counted


def manifest_of(
    offer: Offer,
    groups: Grouping,
    progress: Progress,
    *,
    held: Mapping[str, HeldAs] | None,
    rows: Mapping[int, PersonRow] | None = None,
    local: Mapping[str, str] | None = None,
) -> list[ManifestGroup]:
    """The manifest as it stands. `held` is the receiver's (None on the side that offered), `rows`
    the offer screen's person rows with their matches, `local` the ids of what landed, by group."""
    wanted = set(progress.wanted)
    taking = _counts(groups, wanted, wanted) if progress.answered else {}
    done = _counts(groups, progress.done, wanted)
    failed = _counts(groups, progress.failed, wanted)
    crossing = _counts(groups, progress.crossing, wanted)
    out: list[ManifestGroup] = []
    for key in groups.keys:
        members = groups.members[key]
        files = [offer.files[n] for n in members]
        reasons = [None if held is None else held.get(one.key) for one in files]
        same = sum(1 for reason in reasons if reason == "same")
        look = sum(1 for reason in reasons if reason == "look")
        have = None if held is None else same + look
        if progress.answered:
            to_take = taking.get(key, 0)
        else:
            to_take = len(files) - (have or 0) if held is not None else 0
        index = index_of(key)
        row = None if rows is None or index is None else rows.get(index)
        person = offer.people[index] if index is not None else None
        out.append(
            ManifestGroup(
                key=key,
                kind=kind_of(key),
                index=index,
                name=name_of(offer, key),
                match_name=None if row is None else row.match_name,
                matched_by=None if row is None else row.matched_by,
                local_id=(local or {}).get(key) or (None if row is None else row.match_id),
                usernames=len({one.username for one in files if one.username}),
                faces=len(person.faces.faces) if person is not None and person.faces else 0,
                files=len(files),
                bytes=sum(one.size for one in files),
                have=have,
                to_take=to_take,
                held_same=same,
                held_look=look,
                state=_state(
                    progress.answered,
                    receiving=held is not None,
                    files=len(files),
                    have=have or 0,
                    to_take=to_take,
                    settled=done.get(key, 0) + failed.get(key, 0),
                    crossing=crossing.get(key, 0),
                ),
                done=done.get(key, 0),
                failed=failed.get(key, 0),
                crossing=crossing.get(key, 0),
                sample=[file_of(one, held) for one in files[:GROUP_SAMPLE]],
                more=max(0, len(files) - GROUP_SAMPLE),
            )
        )
    return out


def _state(
    answered: bool,
    *,
    receiving: bool,
    files: int,
    have: int,
    to_take: int,
    settled: int,
    crossing: int,
) -> GroupState:
    """Where a group stands, from the facts this side has."""
    if not answered:
        return "held" if receiving and files and have == files else "offered"
    if not to_take:
        return "held" if receiving and files and have == files else "skipped"
    if settled >= to_take:
        return "landed" if receiving else "sent"
    if crossing:
        return "crossing"
    return "waiting" if receiving else "taking"
