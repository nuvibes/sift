# SPDX-License-Identifier: AGPL-3.0-or-later
"""Facial fingerprints placed by face: the pass that gives a held entry to the person whose own
pictures already match the faces it matches, or makes the person from it, and the question a group
asks instead while `Create people from these fingerprints as their faces are recognized` is off.

**A name never places an entry.** A fingerprints file or a folder of people is taken in as named
entries and nothing else (`import_pack`, `import_folder`). Two people share a name often enough
that the name a file gives somebody is no evidence about the person this library calls that, and a
person nobody's face matched is a row nobody can recognize. So an entry waits until faces in the
library match it, and then the faces decide who it is.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from sift.kernel.access import (
    Made,
    Viewer,
    by_sift,
    by_user,
    person_is_bare_on,
    remove_alias_on,
    remove_person_on,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.db import Connection, Row
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FACIAL_FINGERPRINTS, Subject
from sift.kernel.workbench import DOER, Named, Preview, Recorded, Reversal, Worded
from sift.slices.faces import settings as face_settings
from sift.slices.faces import tuning
from sift.slices.faces.models import Attribution
from sift.slices.faces.receipts import FINGERPRINTS_QUEUE
from sift.slices.faces.service_matching import MatchingMixin
from sift.slices.faces.service_references import EntryTaken, ReferencesMixin
from sift.slices.faces.store_records import Ruling, StoredTrack

log = get_logger(__name__)

#: The attributions that say a face's person's own pictures match it: Sift named it at her bar, or
#: somebody confirmed it. A question is not a match, so it never decides an entry for anybody.
_HERS = frozenset({Attribution.MATCHED.value, Attribution.CONFIRMED.value})
_NAMED = frozenset({Attribution.MATCHED, Attribution.CONFIRMED})
_ATTRIBUTIONS = frozenset(one.value for one in Attribution)

#: Rows per block when the library's faces are compared with the held entries: bounds one product
#: to a few megabytes whatever the size of the library.
_BLOCK = 8192

#: The two acts a receipt of the pass records: a person made from an entry, and an entry given to
#: somebody whose own pictures matched the same faces.
MADE = "made"
CLAIMED = "claimed"

#: One face of the library as the pass reads it: (track, file, person, attribution, group, numbers).
Face = tuple[str, str, str | None, str | None, str | None, bytes]


@dataclass(frozen=True, slots=True)
class Held:
    """The held entries, arranged for comparison: one blended description per entry and the bar
    each is held to. Built once and kept while `Store.fingerprints_stamp` is unchanged."""

    entries: tuple[str, ...]
    vectors: np.ndarray
    bars: np.ndarray

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True, slots=True)
class Placement:
    """What the faces of the library say about one entry.

    `faces` is how many of them match it above its bar. `person_id` is whose own pictures match the
    most of those faces, None where nobody's do; `pile_id` the group most of the unnamed ones sit
    in, where any do. `unnamed` is each of those faces nobody's own pictures match, in a group or
    in none, as (face, file, likeness): the faces the person the entry goes to is named on.
    """

    entry_id: str
    faces: int
    person_id: str | None
    pile_id: str | None
    unnamed: tuple[tuple[str, str, float], ...] = field(default=(), compare=False)


@dataclass(frozen=True, slots=True)
class FingerprintOffer:
    """A group that looks like a held entry, asked about while making people is off, with what
    the entry holds: how many faces, how many confirmed faces its file said they had (None where
    it said none) and the file or folder it came from."""

    entry_id: str
    name: str
    pile_id: str
    likeness: float
    faces: int = 0
    confirmed: int | None = None
    source: str = ""


@dataclass
class FingerprintsRun:
    """What one pass did, by name, for the log and the task's line."""

    made: list[str] = field(default_factory=list)
    claimed: list[str] = field(default_factory=list)
    asked: int = 0


def held_from(rows: Sequence[tuple[str, bytes]], *, bar: float) -> Held:
    """One description per entry, the middle of its faces scaled back to unit length (as a person's
    references are blended for matching), and its bar: `tuning.bar_for` over how many faces it
    holds, so an entry of twenty pictures is trusted as a person of twenty references is."""
    grouped: dict[str, list[bytes]] = {}
    for entry_id, raw in rows:
        grouped.setdefault(entry_id, []).append(raw)
    if not grouped:
        return Held(entries=(), vectors=np.zeros((0, 0), dtype=np.float32), bars=np.zeros(0))
    entries = tuple(sorted(grouped))
    middles = []
    for entry_id in entries:
        stacked = np.frombuffer(b"".join(grouped[entry_id]), dtype="<f4").reshape(
            len(grouped[entry_id]), -1
        )
        middle = stacked.mean(axis=0)
        length = float(np.linalg.norm(middle))
        middles.append(middle / length if length > 0 else middle)
    bars = np.asarray(
        [tuning.bar_for(len(grouped[entry_id]), bar=bar) for entry_id in entries], dtype=np.float32
    )
    return Held(entries=entries, vectors=np.asarray(middles, dtype=np.float32), bars=bars)


def place(held: Held, faces: Sequence[Face], *, left_out: Collection[str] = ()) -> list[Placement]:
    """Which faces match which entry, and whose own pictures match those faces.

    Each face counts for the ONE entry it is likest, and only above that entry's bar. A face left
    out (set aside by somebody) counts for nothing. The person an entry is given to is the one with
    the most of its faces named as theirs (MATCHED at her bar, or CONFIRMED), the lower id on a
    tie so two runs agree; an entry none of whose faces is named belongs to nobody here yet.
    """
    if len(held) == 0 or not faces:
        return []
    width = held.vectors.shape[1]
    hers: dict[str, Counter[str]] = {}
    unnamed: dict[str, Counter[str | None]] = {}
    nobodys: dict[str, list[tuple[str, str, float]]] = {}
    counted: Counter[str] = Counter()
    skip = set(left_out)
    for start in range(0, len(faces), _BLOCK):
        block = [one for one in faces[start : start + _BLOCK] if one[0] not in skip]
        if not block:
            continue
        stacked = np.frombuffer(b"".join(one[5] for one in block), dtype="<f4")
        if stacked.size != len(block) * width:
            # Another model's numbers in the block: nothing here compares with them.
            continue
        scores = stacked.reshape(len(block), width) @ held.vectors.T
        best = scores.argmax(axis=1)
        sure = scores[np.arange(len(block)), best]
        for index in np.flatnonzero(sure >= held.bars[best]):
            track_id, asset_id, person_id, attribution, pile_id, _ = block[int(index)]
            entry_id = held.entries[int(best[int(index)])]
            counted[entry_id] += 1
            if person_id is not None and attribution in _HERS:
                hers.setdefault(entry_id, Counter())[person_id] += 1
            else:
                unnamed.setdefault(entry_id, Counter())[pile_id] += 1
                nobodys.setdefault(entry_id, []).append(
                    (track_id, asset_id, float(sure[int(index)]))
                )
    placements: list[Placement] = []
    for entry_id in sorted(counted):
        people = hers.get(entry_id)
        person_id = min(people, key=lambda one: (-people[one], one)) if people else None
        piles = Counter(
            {pile: n for pile, n in (unnamed.get(entry_id) or Counter()).items() if pile}
        )
        pile_id = min(piles, key=lambda one: (-piles[one], one or "")) if piles else None
        placements.append(
            Placement(
                entry_id=entry_id,
                faces=counted[entry_id],
                person_id=person_id,
                pile_id=pile_id,
                unnamed=tuple(nobodys.get(entry_id, ())),
            )
        )
    return placements


class FingerprintsMixin(ReferencesMixin, MatchingMixin):
    """Placing the held entries of facial fingerprints by face."""

    _held_fingerprints: tuple[tuple[int, int, int], str, Held] | None = None

    async def _held(self, recognizer: str) -> Held:
        """The held entries arranged for comparison, rebuilt only when they have moved."""
        stamp = await self._store.fingerprints_stamp()
        kept = self._held_fingerprints
        if kept is not None and kept[0] == stamp and kept[1] == recognizer:
            return kept[2]
        rows = await self._store.fingerprints_held(recognizer)
        held = await asyncio.to_thread(held_from, rows, bar=tuning.AUTO_APPLY_CONFIDENCE)
        self._held_fingerprints = (stamp, recognizer, held)
        return held

    async def fingerprints_match_file(self, asset_id: str) -> bool:
        """Whether a face of this file matches a held entry above its bar: what makes a scan ask
        for the pass. One file's faces against the held entries, so it costs a scan nothing while
        no entry is held."""
        if not await self.enabled():
            return False
        configured = await self.configuration()
        held = await self._held(configured.recognizer)
        if len(held) == 0:
            return False
        faces = await self._store.library_faces(configured.recognizer, asset_id)
        return bool(await asyncio.to_thread(place, held, faces))

    async def _placements(self, recognizer: str) -> list[Placement]:
        held = await self._held(recognizer)
        if len(held) == 0:
            return []
        faces = await self._store.library_faces(recognizer)
        left_out = await self._store.ignored_track_ids()
        return await asyncio.to_thread(place, held, faces, left_out=left_out)

    async def recognize_from_fingerprints(self) -> FingerprintsRun:
        """Place every held entry the library's faces match.

        An entry whose matching faces are named as somebody (her own pictures matched them, or
        somebody confirmed them) is given to her: its faces become her references and its names
        the names she also answers to, where nobody else answers to them. An entry whose matching
        faces are nobody's makes a new person, made by Sift from facial fingerprints, whatever
        People already holds by that name: a person there whose pictures do not match these faces
        is somebody else. With `PEOPLE_FROM_FILES_KEY` off it is asked about instead, on the group
        (`fingerprint_offers`). An entry no face matches stays held.

        Either way the faces that matched the entry and were nobody's (in a group or in none, or
        only a question) are named as the person it went to, and their files filed under her, in
        the write that gives her the entry: a person made from facial fingerprints always holds
        the files her faces are in. Each person made or given an entry is one History line with an
        Undo. Returns what it did; the caller matches the library again after it.
        """
        run = FingerprintsRun()
        if not await self.enabled():
            return run
        configured = await self.configuration()
        create = bool(await self._preferences.get_app(face_settings.PEOPLE_FROM_FILES_KEY))
        refused: Mapping[str, set[str]] | None = None
        for placement in await self._placements(configured.recognizer):
            entry = await self._store.entry(placement.entry_id)
            if entry is None or entry["claimed_person_id"] is not None or entry["declined_at"]:
                continue
            name = str(entry["name"])
            if placement.person_id is not None:
                # A face somebody said is not her stays off her, whatever the entry says.
                refused = await self._store.rejections() if refused is None else refused
                faces = [
                    one
                    for one in placement.unnamed
                    if placement.person_id not in refused.get(one[0], ())
                ]
                given = await self._give_entry(
                    entry, placement.person_id, act=CLAIMED, by=None, faces=faces, made=None
                )
                if given is not None:
                    run.claimed.append(name)
            elif create:
                made = await self._give_entry(
                    entry,
                    None,
                    act=MADE,
                    by=None,
                    faces=placement.unnamed,
                    made=by_sift(VIA_FACIAL_FINGERPRINTS),
                )
                if made is not None:
                    run.made.append(name)
            else:
                run.asked += 1
        log.info(
            "faces.fingerprints.placed",
            made=len(run.made),
            claimed=len(run.claimed),
            asked=run.asked,
        )
        return run

    async def fingerprint_offers(self) -> list[FingerprintOffer]:
        """The groups that look like a held entry, closest first, one group per entry: what each
        group asks while making people from fingerprints is off ("This group looks like Liora
        Fenwick, from a fingerprints file. Make her a person?"). Empty while it is on, since the
        pass makes them. A group is compared by its middle, as the groups' own cards are."""
        if not await self.enabled():
            return []
        if await self._preferences.get_app(face_settings.PEOPLE_FROM_FILES_KEY):
            return []
        configured = await self.configuration()
        held = await self._held(configured.recognizer)
        if len(held) == 0:
            return []
        groups = await self._store.open_groups(configured.recognizer)
        if not groups:
            return []
        # The waiting list's own rows: the same name, count and source that list draws.
        entries = {str(row["entry_id"]): row for row in await self._store.waiting_entries()}
        offers: list[FingerprintOffer] = []
        taken: set[str] = set()
        for entry_id, pile_id, likeness in await asyncio.to_thread(_closest_groups, held, groups):
            row = entries.get(entry_id)
            if entry_id in taken or row is None:
                continue
            taken.add(entry_id)
            offers.append(
                FingerprintOffer(
                    entry_id=entry_id,
                    name=str(row["name"]),
                    pile_id=pile_id,
                    likeness=likeness,
                    faces=int(row["faces"]),
                    confirmed=None if row["confirmed"] is None else int(row["confirmed"]),
                    source=str(row["source"]),
                )
            )
        return offers

    async def make_person_from_entry(self, entry_id: str, *, by: str) -> str | None:
        """The answer Yes to a group's question: make the person a held entry names, with its
        faces as her references and the library's faces that match it above its bar named as her.
        Pressed by `by`, so the person is theirs and so is the History line, with its Undo. None
        when the entry is gone or already somebody's."""
        await self._require_enabled()
        entry = await self._store.entry(entry_id)
        if entry is None or entry["claimed_person_id"] is not None:
            return None
        configured = await self.configuration()
        faces: Sequence[tuple[str, str, float]] = ()
        for placement in await self._placements(configured.recognizer):
            if placement.entry_id == entry_id:
                faces = placement.unnamed
        return await self._give_entry(entry, None, act=MADE, by=by, faces=faces, made=by_user(by))

    async def _give_entry(
        self,
        entry: Row,
        person_id: str | None,
        *,
        act: str,
        by: str | None,
        faces: Sequence[tuple[str, str, float]],
        made: Made | None,
    ) -> str | None:
        """Give one entry to a person, made here from `made` where `person_id` is None, in ONE
        write with everything that follows: its faces as her references, the names she also
        answers to (skipping her own and any somebody else answers to), `faces` named as her at
        the likeness each matched the entry with, their files filed under her, and one History
        line holding all of it, which is what the Undo takes away. Answers whom it went to, or
        None where another write placed the entry first and nothing was written."""
        held = await self._entry_held(entry)
        own = (
            str(entry["name"]) if person_id is None else await self._store.person_name(person_id)
        ) or ""
        words = [str(entry["name"]), *json.loads(str(entry["aliases"] or "[]"))]
        offered = [
            word
            for word in dict.fromkeys(one.strip() for one in words if str(one).strip())
            if word.casefold() != own.casefold() and await self._store.alias_owner(word) is None
        ]
        standing = await self._store.tracks([one[0] for one in faces])
        pictures: list[tuple[Path, bytes]] = []
        try:
            async with self._store.database.write() as connection:
                if person_id is None:
                    if made is None:  # pragma: no cover (every caller making a person says who)
                        raise ValueError("a person made here needs a maker")
                    person_id = await self._store.create_person_on(
                        connection, str(entry["name"]), made
                    )
                references = await self._claim_entry_on(connection, held, person_id, pictures)
                aliases = [
                    word
                    for word in offered
                    if await self._store.add_alias_on(connection, person_id, word)
                ]
                named = await self._name_on(connection, person_id, faces, standing)
                filed = await self._store.reconcile_people_on(
                    connection, [str(one["file"]) for one in named]
                )
                announce(EVERY_ADMIN, About.LIBRARY)
                await self._record_fingerprints_on(
                    connection,
                    entry,
                    person_id,
                    act=act,
                    by=by,
                    name=own or str(entry["name"]),
                    references=references,
                    aliases=aliases,
                    named=named,
                )
        except EntryTaken:
            return None
        await self._store.write_pictures(pictures)
        await self._settled_after_naming(filed)
        return person_id

    async def _name_on(
        self,
        connection: Connection,
        person_id: str,
        faces: Sequence[tuple[str, str, float]],
        standing: Mapping[str, StoredTrack],
    ) -> list[dict[str, object]]:
        """Name each of these faces as her, MATCHED at its likeness, inside the caller's write.
        Only a face still nobody's own match: one named as anybody (her included) or answered by
        somebody since the pass read it is left as it stands. Answers each face named with where
        it stood before, which is what the Undo puts back."""
        rulings: list[Ruling] = []
        for track_id, _asset_id, likeness in faces:
            track = standing.get(track_id)
            if track is None or (track.person_id is not None and track.attribution in _NAMED):
                continue
            rulings.append(
                Ruling(
                    track_id=track_id,
                    was_person=track.person_id,
                    was=track.attribution,
                    person_id=person_id,
                    attribution=Attribution.MATCHED,
                    confidence=likeness,
                )
            )
        landed = await self._store.restate_on(connection, rulings)
        return [
            {
                "track": ruling.track_id,
                "file": standing[ruling.track_id].asset_id,
                "was_person": ruling.was_person,
                "was": None if ruling.was is None else ruling.was.value,
                "confidence": standing[ruling.track_id].confidence,
            }
            for ruling in rulings
            if ruling.track_id in landed
        ]

    async def _settled_after_naming(self, filed: Mapping[str, tuple[list[str], list[str]]]) -> None:
        """What follows a naming written inside a larger write, once it has landed: each file's
        standing counted again (`_settle_all`), and the search index told of every file whose
        People moved, which `_settle_all` cannot see since the filing was already made."""
        await self._settle_all(list(filed))
        for asset_id, (added, removed) in filed.items():
            if added or removed:
                await self._reindexer.touched(asset_id)

    async def _record_fingerprints_on(
        self,
        connection: Connection,
        entry: Row,
        person_id: str,
        *,
        act: str,
        by: str | None,
        name: str,
        references: Sequence[str],
        aliases: Sequence[str],
        named: Sequence[Mapping[str, object]],
    ) -> None:
        if self._recorder is None:
            return
        pack = str(entry["pack"])
        title = (
            f"Created {name} from facial fingerprints"
            if act == MADE
            else f"Added facial fingerprints of {entry['name']} to {name}"
        )
        files = dict.fromkeys(str(one["file"]) for one in named)
        await self._recorder.record_on(
            connection,
            queue=FINGERPRINTS_QUEUE,
            user_id=by,
            via=VIA_FACIAL_FINGERPRINTS,
            title=title,
            detail="",
            payload=json.dumps(
                {
                    "act": act,
                    "person_id": person_id,
                    "entry_id": str(entry["id"]),
                    "entry": str(entry["name"]),
                    "from": pack,
                    "references": list(references),
                    "aliases": list(aliases),
                    "named": [dict(one) for one in named],
                }
            ),
            subjects=[
                Subject(kind="person", id=person_id),
                *(Subject(kind="asset", id=asset_id) for asset_id in files),
            ],
        )

    async def _unname(self, person_id: str, raw: object) -> int:
        """Put the faces an entry named as her back where each stood before, and her off their
        files. Only a face still carrying that naming moves: one somebody has since answered keeps
        the answer. How many moved."""
        named = _was_named(raw)
        if not named:
            return 0
        live = await self._store.live_ids([one[0] for one in named])
        rulings = [
            Ruling(
                track_id=live.get(track_id, track_id),
                was_person=person_id,
                was=Attribution.MATCHED,
                person_id=before,
                attribution=how if before is not None else None,
                confidence=sure if before is not None else None,
            )
            for track_id, before, how, sure in named
        ]
        landed = await self._store.restate(rulings)
        found = await self._store.tracks(sorted(landed))
        files = sorted({track.asset_id for track in found.values()})
        await self._store.unlearn_recognitions(person_id, files)
        await self._settle_all(files)
        if any(one.person_id is None for one in found.values()) and await self.enabled():
            # Back with nobody and in no group: placed now, as `unfile_matches` places its own.
            await self.regroup(full=False)
        return len(landed)

    async def undo_fingerprints(self, receipt_id: str, payload: Mapping[str, object]) -> Reversal:
        """Take back what the pass or a press did with one entry: the faces it named as her, each
        back where it stood and its file off her, the faces Sift named from those references alone,
        the references, the names added, and the person where the entry made her and nothing else
        rests on her now. The entry is held again and declined, so the next pass leaves it alone;
        adding the person by hand still finds it."""
        person_id = str(payload.get("person_id") or "")
        entry_id = str(payload.get("entry_id") or "")
        raw_references = payload.get("references")
        references = (
            [str(one) for one in raw_references] if isinstance(raw_references, list) else []
        )
        raw_aliases = payload.get("aliases")
        aliases = [str(one) for one in raw_aliases] if isinstance(raw_aliases, list) else []
        if not person_id or not entry_id:
            return Reversal(put_back=0, of=1, said="That record no longer says what it added.")
        await self._unname(person_id, payload.get("named"))
        taken: list[str] = []
        if await self.enabled():
            taken, _ = await self.take_back_recognitions(person_id, references, since=receipt_id)
        await self._store.remove_references_by_id(person_id, references)
        await self._store.decline_entry(entry_id)
        removed = False
        async with self._store.database.write() as connection:
            for alias in aliases:
                await remove_alias_on(connection, person_id=person_id, alias=alias)
            if payload.get("act") == MADE and await person_is_bare_on(connection, person_id):
                announce(EVERY_ADMIN, About.LIBRARY)
                removed = await remove_person_on(connection, person_id)
            else:
                announce(EVERY_ADMIN, About.LIBRARY)
        if removed and await self.enabled():
            await self.release_deleted()
        log.info("faces.fingerprints.undone", removed=removed, references=len(references))
        return Reversal(put_back=1, of=1, along=tuple(taken))


def _was_named(raw: object) -> list[tuple[str, str | None, Attribution | None, float | None]]:
    """The faces a receipt says the entry named, as (face, whose before, how, at what likeness),
    skipping any entry it cannot read."""
    found: list[tuple[str, str | None, Attribution | None, float | None]] = []
    for one in raw if isinstance(raw, list) else ():
        if not isinstance(one, dict) or not isinstance(one.get("track"), str):
            continue
        was = one.get("was")
        before = one.get("was_person")
        sure = one.get("confidence")
        found.append(
            (
                str(one["track"]),
                before if isinstance(before, str) else None,
                Attribution(was) if was in _ATTRIBUTIONS else None,
                float(sure) if isinstance(sure, (int, float)) else None,
            )
        )
    return found


def _closest_groups(
    held: Held, groups: Sequence[tuple[str, bytes]]
) -> list[tuple[str, str, float]]:
    """Every (entry, group, likeness) where a group's middle clears the entry's bar, closest
    first."""
    width = held.vectors.shape[1]
    usable = [(pile_id, raw) for pile_id, raw in groups if len(raw) == width * 4]
    if not usable:
        return []
    middles = np.frombuffer(b"".join(raw for _, raw in usable), dtype="<f4").reshape(
        len(usable), width
    )
    lengths = np.linalg.norm(middles, axis=1, keepdims=True)
    middles = middles / np.where(lengths > 0, lengths, 1.0)
    scores = middles @ held.vectors.T
    found: list[tuple[str, str, float]] = []
    for group, entry in zip(*np.nonzero(scores >= held.bars[np.newaxis, :]), strict=True):
        found.append(
            (held.entries[int(entry)], usable[int(group)][0], float(scores[int(group), int(entry)]))
        )
    found.sort(key=lambda one: (-one[2], one[0], one[1]))
    return found


class FingerprintRecords:
    """A person made from facial fingerprints, or given an entry of them, in History, and the Undo
    that takes it back (`FaceService.undo_fingerprints`)."""

    name = FINGERPRINTS_QUEUE
    reversible = True

    def __init__(self, service: FingerprintsMixin) -> None:
        self._service = service

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the person is its subject, and a fingerprint is no file."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool | Reversal:
        """Take back the references, the names and, where the entry made her, the person."""
        try:
            held = json.loads(payload)
        except ValueError:
            return False
        if not isinstance(held, dict):
            return False
        return await self._service.undo_fingerprints(receipt_id, held)

    def worded(self, recorded: Recorded) -> Worded | None:
        """ "Sift created Liora Fenwick from facial fingerprints", or "Sift added the facial
        fingerprints of Wren Halloway to Wren". The person is named as she is called now; the
        entry by the name its file gave it, which is the fact."""
        held = recorded.held()
        person_id, entry = held.get("person_id"), held.get("entry")
        if not isinstance(person_id, str) or not isinstance(entry, str):
            return None
        person = Named(kind="person", id=person_id, recorded=entry)
        if held.get("act") == "made":
            return Worded(said=(DOER, " created ", person, " from facial fingerprints"))
        return Worded(
            said=(DOER, f" added the facial fingerprints of {entry} to ", person),
            more=("Sift went by the faces that match them, never by the name.",),
        )
