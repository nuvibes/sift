# SPDX-License-Identifier: AGPL-3.0-or-later
"""Disagreements gathered by person: who the rows are about, one person's rows closest to her
first, and a Yes or a No over a page of them, a pick or all of hers.

A pass files a folder in one go, so its mistakes arrive by the hundred under one name; one row per
person asks once. Nothing is decided without a press. Where the name came from is said by the
server, naming only folders this viewer may see.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import quote

from sift.kernel.access import Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history_boxes import boxes_that_named
from sift.kernel.access.history_sources import nearest_naming_folders
from sift.kernel.access.sentences import Line, Piece
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.vocabulary import Subject
from sift.kernel.where import folder_said, whereabouts_from
from sift.slices.faces import matching
from sift.slices.faces.receipts import DISAGREEMENTS_QUEUE
from sift.slices.faces.service_decisions import RunAnswered, Taught
from sift.slices.faces.service_may_be import ToCheckView
from sift.slices.faces.service_review import FiledInReach, ReviewMixin
from sift.slices.faces.store import FiledFace, FiledOff


@dataclass(frozen=True, slots=True)
class DisagreeingPerson:
    """One person the Disagreements tab is about: how many of her files, where most came from."""

    person_id: str
    name: str
    count: int
    source: str
    #: Where the name came from, as the line under her heading says it (`filed_from`).
    filed: Line = field(default=())


@dataclass(frozen=True, slots=True)
class NamingFolder:
    """One folder answered as her, with how many of her files it named; `piece` None if hidden."""

    folder_id: str
    files: int
    piece: Piece | None


#: How many folders the line names before it counts the rest.
FOLDERS_NAMED = 3

#: Where the name on her files came from, by the word the pass wrote, where no folder is named.
_FILED_FROM: Mapping[str, str] = {
    "folder": "Added from a folder name",
    "stash_box": "Added by a stash-box",
    "filename": "Added from the filename",
    "metadata": "Added from the file's details",
    "watermark": "Added from a watermark",
}
_FILED_BY_SIFT = "Added by Sift"


def folder_view(folder_id: str) -> str:
    """One folder in the folder view, by its id: the one address that names exactly one folder."""
    return f"/browse?folders={quote(folder_id, safe='')}"


def filed_from(source: str, folders: Sequence[NamingFolder], boxes: Sequence[str] = ()) -> Line:
    """Where the name on her files came from, as one line: the folders, counted and the first few
    named (never a hidden one), the stash-box, or the pass's own word."""
    if source == "stash_box" and boxes:
        return say.said(f"Added by {say.and_then(list(boxes))}")
    if source != "folder" or not folders:
        return say.said(_FILED_FROM.get(source, _FILED_BY_SIFT))
    named = [one.piece for one in folders if one.piece is not None][:FOLDERS_NAMED]
    if len(folders) == 1:
        if not named:
            return say.said(_FILED_FROM["folder"])
        return say.said("Added from the name of the folder ", named[0])
    lead = f"Added from the names of {say.many(len(folders))} folders"
    if not named:
        return say.said(lead)
    rest = len(folders) - len(named)
    parts: list[str | Piece] = [lead, ": "]
    for at, one in enumerate(named):
        if at:
            parts.append(" and " if at == len(named) - 1 and not rest else ", ")
        parts.append(one)
    if rest:
        parts.append(f" and {say.many(rest)} more")
    return say.said(*parts)


def _taken_off_payload(person_id: str, rows: Sequence[FiledOff]) -> str:
    """What a No's receipt keeps: every row it took off, whole, for `put_filed_back`."""
    return json.dumps(
        {
            "person_id": person_id,
            "files": [[row.asset_id, row.source, row.decided_at, row.box_id] for row in rows],
        }
    )


def filed_rows(payload: str) -> tuple[str, list[FiledOff]] | None:
    """A No's receipt read back: the person and the rows it took off, or None if unreadable."""
    try:
        recorded = json.loads(payload)
        person_id = str(recorded["person_id"])
        rows = [_filed_off(*fields) for fields in recorded["files"]]
    except (ValueError, KeyError, TypeError):
        return None
    return person_id, rows


def _filed_off(
    asset_id: object, source: object, decided_at: object, box_id: object = None
) -> FiledOff:
    """One row of a No's receipt, as `take_filed_off` answered it."""
    return FiledOff(
        asset_id=str(asset_id),
        source=str(source),
        decided_at=None if decided_at is None else int(str(decided_at)),
        box_id=None if box_id is None else str(box_id),
    )


class DisagreementsMixin(ReviewMixin):
    """The disagreements by person, and the two answers over a run of them."""

    async def disagreeing_people(self, viewer: Viewer) -> list[DisagreeingPerson]:
        """Everybody the Disagreements tab is about, the most files first, from the tab's rows."""
        reach = await self._filed_in_reach(viewer)
        sources: dict[str, Counter[str]] = {}
        by_folder: dict[str, list[str]] = {}
        by_box: dict[str, list[str]] = {}
        for one in reach.rows:
            sources.setdefault(one.person_id, Counter())[one.source] += 1
            if one.source == "folder":
                by_folder.setdefault(one.person_id, []).append(one.track.asset_id)
            elif one.source == "stash_box":
                by_box.setdefault(one.person_id, []).append(one.track.asset_id)
        people: list[DisagreeingPerson] = []
        seen: Mapping[str, frozenset[str]] | None = None
        for person_id, counted in sources.items():
            source = counted.most_common(1)[0][0]
            folders: list[NamingFolder] = []
            if source == "folder":
                if seen is None:
                    visible = await self._repository.visible_folders(viewer)
                    seen = whereabouts_from(viewer, visible, root_paths={}, profile=None).seen
                folders = await self._naming_folders(person_id, by_folder[person_id], seen)
            boxes: list[str] = []
            if source == "stash_box":
                boxes = await boxes_that_named(self._store.database, person_id, by_box[person_id])
            people.append(
                DisagreeingPerson(
                    person_id=person_id,
                    name=reach.names[person_id],
                    count=sum(counted.values()),
                    source=source,
                    filed=filed_from(source, folders, boxes),
                )
            )
        return sorted(people, key=lambda one: (-one.count, one.name.casefold(), one.person_id))

    async def _naming_folders(
        self, person_id: str, asset_ids: Sequence[str], seen: Mapping[str, frozenset[str]]
    ) -> list[NamingFolder]:
        """The folders that named her on these files, the most first: each file to its nearest
        folder answered as her, named only where visible all the way down (`kernel.where`)."""
        nearest = await nearest_naming_folders(self._store.database, person_id, asset_ids)
        about = {one[0]: one[1:] for one in nearest.values()}
        files = Counter(one[0] for one in nearest.values())
        names = Counter(about[folder_id][2] for folder_id in files)
        found: list[NamingFolder] = []
        for folder_id, count in files.items():
            root_id, path, name = about[folder_id]
            shown = not path or folder_said(path, seen=seen.get(root_id, frozenset())) == path
            # Two folders of one name are told apart by their paths, or the line names one twice.
            text = path if path and names[name] > 1 else name
            piece = say.thing("folder", path or name, text, href=folder_view(folder_id))
            found.append(NamingFolder(folder_id, count, piece if shown else None))
        return sorted(found, key=lambda one: (-one.files, about[one.folder_id][1], one.folder_id))

    async def disagreements_of(
        self, viewer: Viewer, person_id: str, *, limit: int, offset: int
    ) -> tuple[list[ToCheckView], int]:
        """One page of one person's disagreements, closest to her first, and how many she has."""
        reach = await self._filed_in_reach(viewer)
        hers = await self._closest_first(person_id, _hers(reach, person_id))
        begin = max(0, offset)
        return await self._as_disagreements(viewer, reach, hers[begin : begin + limit]), len(hers)

    async def _closest_first(self, person_id: str, rows: list[FiledFace]) -> list[FiledFace]:
        """Her rows by how much each face looks like her, unmeasured last, the id breaking ties."""
        if len(rows) < 2:
            return rows
        configured = await self.configuration()
        gallery = await self._gallery_for(configured.groups, configured.recognizer)
        where = matching.rows_by_person(gallery).get(person_id)
        vectors = await self._store.best_vectors([one.track.id for one in rows])

        def score(one: FiledFace) -> float | None:
            vector = vectors.get(one.track.id)
            return None if vector is None else matching.likeness(vector, gallery, where)

        scored = [(score(one), one) for one in rows]
        scored.sort(key=lambda pair: (pair[0] is None, -(pair[0] or 0.0), pair[1].track.id))
        return [one for _score, one in scored]

    async def answer_disagreements(
        self,
        viewer: Viewer,
        person_id: str,
        *,
        yes: bool,
        asset_ids: Sequence[str] | None,
    ) -> RunAnswered:
        """Yes or No over some of one person's disagreements: the files named, or all of hers.

        The files are narrowed, never trusted; Yes names each face as her, No takes her off the
        files, each with its receipt.
        """
        reach = await self._filed_in_reach(viewer)
        hers = [one for one in _hers(reach, person_id) if not reach.shown[one.track.asset_id]]
        if asset_ids is not None:
            wanted = set(asset_ids)
            hers = [one for one in hers if one.track.asset_id in wanted]
        if not hers:
            return RunAnswered(changed=0)
        if yes:
            # The other name comes off first, into the naming's receipt, so its Undo restores it.
            taught = Taught()
            for one in hers:
                was = one.track
                other = str(was.person_id)
                taught.named_before[was.id] = (
                    other,
                    None if was.attribution is None else was.attribution.value,
                    was.confidence,
                )
                await self.reject(was.id, other)
            return await self.confirm_answered(
                [one.track.id for one in hers], person_id, viewer=viewer, taught=taught
            )
        taken = await self._store.take_filed_off(person_id, [one.track.asset_id for one in hers])
        if not taken:
            return RunAnswered(changed=0)
        await self._reindexer.touched_many([row.asset_id for row in taken])
        receipt = await self._record_taken_off(viewer, person_id, reach.names[person_id], taken)
        return RunAnswered(changed=len(taken), decision_id=receipt)

    async def put_filed_back(self, person_id: str, rows: Sequence[FiledOff]) -> int:
        """Undo a No: every row it took off, back as it was. How many files she is on again."""
        back = await self._store.put_filed_back(person_id, rows)
        if back:
            await self._reindexer.touched_many(back)
        return len(back)

    async def _record_taken_off(
        self, viewer: Viewer, person_id: str, name: str, taken: Sequence[FiledOff]
    ) -> str:
        """Write down that somebody took her off these files, with the way back."""
        if self._recorder is None:
            return ""
        one = len(taken) == 1
        counted = "1 file" if one else f"{len(taken):,} files"
        them = "it" if one else "them"
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [Subject(kind="asset", id=row.asset_id) for row in taken]
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a re-read tab finds it.
            announce(EVERY_ADMIN, About.LIBRARY)
            return await self._recorder.record_on(
                connection,
                queue=DISAGREEMENTS_QUEUE,
                user_id=viewer.id,
                title=f"You removed {name} from {counted}",
                detail=(
                    f"The face in {them} is named as another person. Taking this back puts "
                    f"{name} on {them} again. No file is touched and nothing is deleted."
                ),
                payload=_taken_off_payload(person_id, taken),
                subjects=subjects,
            )


def _hers(reach: FiledInReach, person_id: str) -> list[FiledFace]:
    """The rows of `reach` about one person, in the store's order."""
    return [one for one in reach.rows if one.person_id == person_id]
