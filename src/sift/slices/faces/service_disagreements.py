# SPDX-License-Identifier: AGPL-3.0-or-later
"""Disagreements gathered by person: who the rows are about, one person's rows closest to
her first, and a Yes or a No over a page of them, a pick or all of hers.

**By person, because that is the shape the rows come in.** A disagreement is a name a pass filed
(a folder, a filename, a stash-box) on a file whose one face is named as somebody else: recognized
by Sift or confirmed by a person. A face that matches nobody is no evidence against the filing,
since a covered or poorly lit face matches nobody whoever it is, so it is not one. A pass
files a whole folder in one go, so its mistakes arrive by the hundred under one name, and a thousand
rows are about a handful of people. One card per file would ask the same question about the same
person a thousand times; one row per person asks it once, with her faces as a wall to be answered a
page at a time.

**Closest to her first,** so the faces that may be her after all (a name Sift added that is
wrong) are the first page somebody sees, and what is left behind them is the part a No over all of
it is for.
The number is the one a re-match compares (`matching.likeness` against her pictures), taken for
one person's rows at a time and never stored: it moves every time her pictures do.

**Nothing is decided without a press.** A name on the face is a likeness, and taking her off a
file she may well be in is not a guess to make on somebody's behalf.

A Yes is the naming every faces screen already writes (`confirm_many`, a receipt under
`IDENTIFIED_QUEUE`). The No takes her off the files and remembers it (`Store.take_filed_off`), with
a receipt of its own under `DISAGREEMENTS_QUEUE` that puts every row back as it was.

**Where the name came from is said by the server, folder and all.** "Added from a folder name"
with no folder named leaves somebody weighing a hundred files unable to tell which filing to look
at. A folder filing names the folder it was read from (the nearest folder above the file that was
answered as her, the rule a file's History line reads), as a way to it in the folder view; several
are counted and the first few named, the ones with the most of her files first. A folder this
viewer may not see is never named: it is counted and stays nameless.
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
    """One person the Disagreements tab is about: how many of her files, and where most came from.

    `source` is the word the pass wrote on the most of them (`folder`, `stash_box` and the rest),
    so the row can say where the name came from before anybody opens it.
    """

    person_id: str
    name: str
    count: int
    source: str
    #: Where the name came from, as the line under her heading says it: the folders named and
    #: linked where a folder's name gave it. See `filed_from`.
    filed: Line = field(default=())


@dataclass(frozen=True, slots=True)
class NamingFolder:
    """One folder that was answered as her, with how many of her files here it named.

    `piece` is the folder as a way to it, or None where this viewer may not see it: counted,
    never named.
    """

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
    """Where the name on her files came from, as one line: the folders named where it was folders.

    One folder: "Added from the name of the folder <name>". Several: how many, then the first few
    by name ("Added from the names of 7 folders: A, B, C and 4 more"). A folder the viewer may not
    see is in the count and never in the names; with none to name, the words say only how many.
    A stash-box's filing names the box ("Added by FansDB") wherever the filings say which.
    Any other word the pass wrote reads as it always has.
    """
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
    """A No's receipt read back: the person and the rows it took off. None for one this build
    cannot read, which is not a decision it can put back. A receipt written before filings named
    their stash-box holds three fields a row, and puts each row back with no box."""
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
        """Everybody the Disagreements tab is about, the most files first.

        The same rows the tab counts (`_filed_in_reach`), so the people's counts add up to the tab's
        number and cannot disagree with it. Ties go to the name, then the id, so the order is the
        same on every read.
        """
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
        """The folders that named her on these files, the most of them first.

        Each file goes to its NEAREST folder answered as her, as its History line says it; a file
        no answered folder holds any more (the folder deleted, the answer taken back) is in no
        folder's count. A folder is named only where this viewer may see it all the way down,
        the rule every place a location is said reads (`kernel.where`).
        """
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
        """Her rows ordered by how much each face looks like her, the most first.

        A face with no description, or a person with no picture in the gallery, has no number and
        goes last; the face's id breaks every tie, so two reads of the same library agree.
        """
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

        The files are NARROWED, never trusted: only a file still standing as one of her
        disagreements for this viewer is acted on, so a row answered since the page was drawn, or
        one that was never hers, is simply not part of the press. A file behind a shut vault is
        left alone, as every bulk write leaves it.

        Yes names each face as her, the naming every faces screen writes, with its receipt. No
        takes her off the files and writes the receipt that puts her back.
        """
        reach = await self._filed_in_reach(viewer)
        hers = [one for one in _hers(reach, person_id) if not reach.shown[one.track.asset_id]]
        if asset_ids is not None:
            wanted = set(asset_ids)
            hers = [one for one in hers if one.track.asset_id in wanted]
        if not hers:
            return RunAnswered(changed=0)
        if yes:
            # Every face here is named as somebody else (`filed_but_unrecognised`): that name comes
            # off first, refused, and goes into the naming's receipt, so the toast's Undo names the
            # face as that person again as well as taking her name off.
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
            # Rung on the receipt's own commit, so a tab that re-read on the press's earlier bell
            # reads again with this receipt in it.
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
