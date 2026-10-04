# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder rule: what one folder's path claims, asked as a question or written without asking."""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access import (
    attribute_assets_recording_on,
    by_sift,
    create_person_on,
    give_person_a_cover_on,
    people_named,
    people_named_on,
    refused_for,
)
from sift.kernel.access.sentences import plural
from sift.kernel.attribution import FolderFaces
from sift.kernel.content import FolderNode
from sift.kernel.db import Connection
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FILENAME, VIA_FOLDER, Subject
from sift.slices.suggestions.ladder import Action, Evidence, Verdict, decide, dominant
from sift.slices.suggestions.naming import (
    KnownName,
    Reading,
    fold,
    fold_known,
    known_names_in,
    mirror_in_folder,
    people_in,
    person_in_filename,
    repeated_prefix,
    strip_noise,
)
from sift.slices.suggestions.service_base import (
    AUTOMATIC,
    FILED_QUEUE,
    SILENT,
    Arrivals,
    SuggestionBase,
)

log = get_logger(__name__)


def _silent_sentence(name: str, place: str, files: int) -> str:
    """The line a folder filed without asking reads as: "Added 240 files in the folder Shoots to
    Neve Alder". The words of the "Added without asking" tab it is listed under ("Sift added their
    files to that person"), and the folder said by its path, as every line names a folder. No full
    stop, like every line in a History pane."""
    if files:
        return f"Added {plural(files, 'file', 'files')} in the folder {place} to {name}"
    return f"Added the folder {place} to {name}"


def _claiming_folder(
    folder: FolderNode, reading: Reading, folders_by_place: dict[tuple[str, str], str]
) -> tuple[str, tuple[str, str]]:
    """Which folder a reading is ABOUT, as `(id, (root_id, rel_path))`."""
    here = (folder.root_id, folder.rel_path)
    if reading.depth < 0 or reading.depth >= len(folder.chain):
        return folder.id, here
    rel_path = "/".join(folder.chain[1 : reading.depth + 1])
    found = folders_by_place.get((folder.root_id, rel_path))
    return (found, (folder.root_id, rel_path)) if found is not None else (folder.id, here)


def _owned_inside(
    place: tuple[str, str],
    places: Sequence[tuple[str, str]],
    folders_by_place: Mapping[tuple[str, str], str],
    answered: Mapping[str, set[str]],
    known: Sequence[KnownName],
) -> set[str]:
    """Everybody a folder under this one is already the own folder of.

    **Answered as them, or named for them by its own name.**
    """
    root, rel_path = place
    low = (root, f"{rel_path}/" if rel_path else "")
    high = (root, f"{rel_path}0" if rel_path else "\U0010ffff")
    by_name: dict[str, set[str]] = {}
    for one in known:
        by_name.setdefault(one.folded, set()).add(one.person_id)
    owners: set[str] = set()
    for under in places[bisect_right(places, low) : bisect_left(places, high)]:
        owners |= answered.get(folders_by_place[under], set())
        named = by_name.get(fold(strip_noise(under[1].rsplit("/", 1)[-1])), set())
        if len(named) == 1:
            owners |= named
    return owners


def _one_known_in(filename: str, known: Sequence[KnownName]) -> tuple[str, str] | None:
    """The single person this library already holds who is named in this filename, or nothing.

    **One, or none.**
    """
    hits = known_names_in(filename, known)
    if len(hits) != 1:
        return None
    person_id = hits[0]
    return next(((one.person_id, one.name) for one in known if one.person_id == person_id), None)


@dataclass(frozen=True, slots=True)
class PassReads:
    """What one pass reads once and every folder it considers is weighed against."""

    rejected: set[str]
    answered: dict[str, set[str]]
    folders_by_place: dict[tuple[str, str], str]
    site_folders: set[str]
    looking: bool
    known: Sequence[KnownName]
    sites: Sequence[str]
    arrivals: Arrivals
    places: Sequence[tuple[str, str]]
    refused: Collection[tuple[str, str]]


class FolderRuleMixin(SuggestionBase):
    """Reads one folder at a time and turns what it claims into a question or a write."""

    async def _consider(
        self,
        folder: FolderNode,
        reading: Reading,
        reads: PassReads,
        *,
        filenames: list[str],
        made: dict[str, set[str]],
        cards: set[str],
    ) -> int:
        """One folder of files, all the way from what its path says to what was written or filed.

        **The claim belongs to the folder that MADE it, not to the one holding the files.**
        """
        claim_folder = _claiming_folder(folder, reading, reads.folders_by_place)
        assets = await self._store.assets_under(claim_folder[0])
        if not assets:  # pragma: no cover (the tree read only offers folders that hold files)
            return 0
        # Every question this reading reaches, asked now or already standing, by the folder it is
        # about. Both folders are entered even when nothing is asked of them: a folder whose reading
        # now asks nothing is the plainest case of a question no longer asked.
        made.setdefault(folder.id, set())
        made.setdefault(claim_folder[0], set())

        # A folder a swap made to hold what it brought is nobody's (see `Arrivals`): no rung reads
        # it, and no standing answer is carried into it, since one is a silent write to whatever
        # lands there next.
        if claim_folder[0] in reads.arrivals.containers:
            return 0

        written = await self._carry_answers(folder.id, claim_folder[0], assets, reads)

        faces = await self._faces.faces_in(claim_folder[0]) if reads.looking else FolderFaces()
        already = await self._store.claims_for(claim_folder[0])
        claimed = {claim.name_key for claim in already}

        proposed = await self._propose_for_answered(claim_folder[0], faces, reads, cards)

        # The Site shape next, because it is a fact about the FILENAMES and it decides what the
        # folder name is allowed to mean.
        prefix = repeated_prefix(filenames)
        if prefix:
            return written + await self._file_site(
                folder, prefix, filenames, claimed, reads.rejected, made[folder.id]
            )

        # And a folder named for ONE USERNAME on one site: `harlowquin (RedGifs)`, read before the
        # name may mean a person, or one folder would be asked about twice.
        mirror = mirror_in_folder(folder.name, sites=reads.sites)
        if mirror is not None:
            return written + await self._claim_username(
                folder, mirror, claimed, reads.rejected, made[folder.id]
            )

        names = people_in(reading.name, known=reads.known) if reading.name else []
        # Nothing in the path named anybody. Before giving up, ask whether the FILES name somebody
        # this library already holds: a title says nothing about where a name begins or ends, but
        # "is this person in it" is a question the library can answer about itself.
        if not names:
            written += await self._known_person_in_files(
                claim_folder[0],
                filenames,
                reads.known,
                claimed,
                reads.rejected,
                made[claim_folder[0]],
            )
        return written + await self._ask_names(
            names or [""],
            reading,
            claim_folder[0],
            faces,
            assets,
            claimed,
            reads,
            proposed,
            cards,
            made=made[claim_folder[0]],
            place=claim_folder[1],
        )

    async def _carry_answers(
        self, own_id: str, folder_id: str, assets: list[str], reads: PassReads
    ) -> int:
        """Apply what is already decided about a folder to everything in it. Returns files written."""
        # Anything already decided about this folder is applied first, to everything in it.
        written = 0
        for person_id in sorted(reads.answered.get(folder_id, ())):
            written += await self._attribute(folder_id, person_id, assets)

        # And the same for a folder answered as a site, whose people are one per file. It has no
        # standing person to carry forward, so the filenames are read again, which is what puts a
        # name on a show that lands in it next week.
        if own_id in reads.site_folders:
            async with self._store.write() as connection:
                landed, _, _ = await self._name_by_filename(
                    connection, await self._store.files_in(own_id), unticked=set()
                )
            written += len(landed)
        return written

    async def _propose_for_answered(
        self, folder_id: str, faces: FolderFaces, reads: PassReads, cards: set[str]
    ) -> set[str]:
        """Propose the folder's main group as each person it is already filed under. Returns who."""
        # The folder's main group, proposed as each person the folder is already filed under, here
        # for a folder answered before, and below for one the ladder files on this pass.
        proposed: set[str] = set()
        if reads.looking:
            for person_id in sorted(reads.answered.get(folder_id, ())):
                if await self._propose_main_group(folder_id, person_id, faces):
                    cards.add(folder_id)
                proposed.add(person_id)
        return proposed

    async def _ask_names(
        self,
        names: list[str],
        reading: Reading,
        folder_id: str,
        faces: FolderFaces,
        assets: list[str],
        claimed: set[str],
        reads: PassReads,
        proposed: set[str],
        cards: set[str],
        *,
        made: set[str],
        place: tuple[str, str],
    ) -> int:
        """Each name the folder's path gave, put up the ladder and acted on. Returns files written."""
        written = 0
        for name in names:
            one = Reading(name=name, site=reading.site, is_username=reading.is_username)
            named_by = await people_named(self._store.database, name) if name else []
            by_name_only = folder_id in reads.arrivals.by_name
            # Who already has a folder of their own under this one: asked only where rung 1 could
            # fire, since it is the only rung that reads it and the walk is over every folder below.
            owned: set[str] = set()
            if not by_name_only and dominant(faces.named, with_faces=faces.with_faces):
                owned = _owned_inside(
                    place, reads.places, reads.folders_by_place, reads.answered, reads.known
                )
            verdict = decide(
                one,
                faces,
                files=len(assets),
                named_by=named_by,
                looking=reads.looking,
                rejected=fold(name) in reads.rejected,
                owned_inside=owned,
                by_name_only=by_name_only,
            )
            # A silent write somebody took back stays taken back (see `take_back_silent`): the
            # faces or the name that decided it are still here, and nothing else would stop it.
            if (
                verdict.action is Action.ATTRIBUTE
                and (folder_id, verdict.person_id) in reads.refused
            ):
                verdict = Verdict()
            written += await self._act(
                folder_id, one, verdict, assets, claimed, reads.answered, made
            )
            # A rung that files the folder under somebody already known (the name is theirs, or
            # their recognized faces run through it) is where the folder's main UNNAMED group is
            # worth asking about: the files are theirs, and a group that is most of the folder's
            # faces and still unnamed is most likely them, not yet recognized.
            filed_as = verdict.person_id if verdict.action is Action.ATTRIBUTE else None
            if reads.looking and filed_as is not None and filed_as not in proposed:
                if await self._propose_main_group(folder_id, filed_as, faces):
                    cards.add(folder_id)
                proposed.add(filed_as)
        return written

    async def _propose_main_group(self, folder_id: str, person_id: str, faces: FolderFaces) -> bool:
        """Tell the faces side "this group is most of a folder filed under this person", or unsay it.

        **The same question rung 3 asks, answered by the same rule**
        """
        group = dominant(faces.piles, with_faces=faces.with_faces)
        if group is None:
            return await self._faces.withdraw_proposals(folder_id, person_id) > 0
        return await self._faces.propose_group(
            group, person_id, folder_id=folder_id, files=faces.piles[group], of=faces.with_faces
        )

    async def _act(
        self,
        folder_id: str,
        reading: Reading,
        verdict: Verdict,
        assets: Sequence[str],
        claimed: set[str],
        answered: dict[str, set[str]],
        asked: set[str],
    ) -> int:
        if verdict.action is Action.ATTRIBUTE and verdict.person_id is not None:
            if verdict.person_id in answered.get(folder_id, set()):
                return 0
            return await self._attribute(folder_id, verdict.person_id, assets, decided=True)

        # Undecided rather than answered: a question already standing for this name stays until the
        # faces are read and the reading can say. The name is never empty here: the ladder answers
        # nothing at all for a nameless reading, so WAIT and OFFER both carry one.
        if verdict.action is Action.WAIT:
            asked.add(fold(reading.name))
            return 0
        if verdict.action is not Action.OFFER:
            return 0

        key = fold(verdict.name)
        asked.add(key)
        if key in claimed:
            return 0
        claimed.add(key)
        return await self._store.add_claim(
            folder_id=folder_id,
            kind="person",
            name_key=key,
            proposed=verdict.name,
            person_id=None,
            group_id=verdict.group_id,
            site=reading.site or None,
            is_username=reading.is_username,
            evidence=(
                Evidence.FACE_GROUP.value
                if verdict.evidence is Evidence.FACE_GROUP
                else Evidence.NAME_ONLY.value
            ),
        )

    async def _file_site(
        self,
        folder: FolderNode,
        prefix: str,
        filenames: Sequence[str],
        claimed: set[str],
        rejected: set[str],
        asked: set[str],
    ) -> int:
        """The repeated-prefix shape: one claim for the folder, the names offered underneath it."""
        key = fold(prefix)
        if not key or key in rejected:
            return 0
        found = {person_in_filename(name, after_prefix=True) for name in filenames}
        if not any(found - {""}):
            return 0
        asked.add(key)
        if key in claimed:
            return 0
        claimed.add(key)
        return await self._store.add_claim(
            folder_id=folder.id,
            kind="site",
            name_key=key,
            proposed=prefix,
            person_id=None,
            group_id=None,
            site=prefix,
            is_username=False,
            evidence=Evidence.FILENAMES.value,
        )

    async def _claim_username(
        self,
        folder: FolderNode,
        mirror: tuple[str, str],
        claimed: set[str],
        rejected: set[str],
        asked: set[str],
    ) -> int:
        """`<username> (<site>)`: one claim this folder is that username's, for the board to answer."""
        name, site = mirror
        key = fold(f"{name} {site}")
        if not key or key in rejected:
            return 0
        asked.add(key)
        if key in claimed:
            return 0
        claimed.add(key)
        return await self._store.add_claim(
            folder_id=folder.id,
            kind="username",
            name_key=key,
            proposed=name,
            person_id=None,
            group_id=None,
            site=site,
            is_username=True,
            evidence=Evidence.USERNAME_FOLDER.value,
        )

    async def _attribute(
        self, folder_id: str, person_id: str, assets: Sequence[str], *, decided: bool = False
    ) -> int:
        """The two silent rungs. One transaction, and it records the standing decision too."""
        held = await self._held_back(folder_id, person_id, assets)
        keep = [asset for asset in assets if asset not in held]
        if not keep:
            return 0
        async with self._store.write() as connection:
            written = await attribute_assets_recording_on(
                connection, asset_ids=keep, person_id=person_id, source=AUTOMATIC
            )
            linked = await self._store.remember_folder_person_on(
                connection, folder_id=folder_id, person_id=person_id
            )
            if decided:
                await self._record_silent(connection, folder_id, person_id, written, linked=linked)
        log.info("suggestions.attributed", folder_id=folder_id, files=len(written))
        return len(written)

    async def _held_back(self, folder_id: str, person_id: str, assets: Sequence[str]) -> set[str]:
        """The files of a folder the pass may not file under this person without asking."""
        keep = list(assets)
        # Two vetoes, and they are different in kind. The faces say the evidence disagrees; a
        # refusal says a person disagreed, and that one is not overridden by anything a pass works
        # out for itself.
        refused = await refused_for(self._store.database, person_id, keep)
        contradicting = await self._faces.contradicting(person_id, keep)
        # And a third: a file whose own NAME says it is somebody else's.
        named_elsewhere = await self._files_naming_somebody_else(folder_id, person_id, set(keep))
        # And a fourth: a file under a folder somebody took back from this person (`folder_refusals`).
        # A folder above it still answered as them would otherwise put them back on its files the
        # next time it is read.
        taken_back: set[str] = set()
        for refused_folder, refused_person in await self._store.folder_refusals():
            if refused_person == person_id:
                taken_back |= set(await self._store.assets_under(refused_folder)) & set(keep)
        held = refused | contradicting | named_elsewhere | taken_back
        if held:
            log.info(
                "suggestions.attribute.held_back",
                folder_id=folder_id,
                files=len(held),
                refused=len(refused),
                named_elsewhere=len(named_elsewhere),
            )
        return held

    async def _record_silent(
        self,
        connection: Connection,
        folder_id: str,
        person_id: str,
        written: Sequence[str],
        *,
        linked: bool,
    ) -> None:
        """Write down a folder filed under somebody without asking, so it can be taken back whole."""
        if self._recorder is None:
            return
        name = await self._store.person_name_on(connection, person_id)
        place = await self._store.folder_said_on(connection, folder_id)
        title = _silent_sentence(name, place, len(written))
        record = {
            "kind": SILENT,
            "folder_id": folder_id,
            "person_id": person_id,
            "assets": list(written),
            "linked": linked,
        }
        await self._recorder.record_on(
            connection,
            queue=FILED_QUEUE,
            user_id=None,
            via=VIA_FOLDER,
            title=title,
            detail=f"{title}.",
            payload=json.dumps(record),
            subjects=[
                Subject(kind="folder", id=folder_id),
                *(Subject(kind="asset", id=one) for one in written),
                Subject(kind="person", id=person_id),
            ],
            object=LedgerObject(kind="person", id=person_id, name=name or None),
        )

    async def _known_person_in_files(
        self,
        folder_id: str,
        filenames: Sequence[str],
        known: Sequence[KnownName],
        claimed: set[str],
        rejected: set[str],
        asked: set[str],
    ) -> int:
        """The library recognising itself in a folder whose own name says nothing.

        **One person, or nothing.**

        **It only ever OFFERS.**
        """
        if not known:
            return 0
        seen: Counter[str] = Counter()
        for filename in filenames:
            # ONE, or nothing: the same bar the per-file half uses, and it has to be the same or the
            # two disagree about the same file.
            found = known_names_in(filename, known)
            if len(found) == 1:
                seen[found[0]] += 1
        # The denominator is the files that name ANYBODY known, not every file in the folder:
        # the same shape as the face rule, where a folder of landscapes does not count against the
        # person in the portraits. Most filenames name nobody and they are not evidence either way.
        person_id = dominant(seen, with_faces=sum(seen.values()))
        if person_id is None:
            return 0
        name = next((one.name for one in known if one.person_id == person_id), "")
        key = fold(name)
        if not key or key in rejected:
            return 0
        asked.add(key)
        if key in claimed:
            return 0
        claimed.add(key)
        return await self._store.add_claim(
            folder_id=folder_id,
            kind="person",
            name_key=key,
            proposed=name,
            person_id=person_id,
            group_id=None,
            site="",
            is_username=False,
            evidence=Evidence.KNOWN_IN_FILENAMES.value,
        )

    async def _files_naming_somebody_else(
        self, folder_id: str, person_id: str, assets: set[str]
    ) -> set[str]:
        """Which of these files name a person who is not the one being attributed."""
        held: set[str] = set()
        for asset_id, filename in await self._store.files_in(folder_id):
            if asset_id not in assets:
                continue
            found = person_in_filename(filename, after_prefix=False)
            if not found:
                continue
            named = await people_named(self._store.database, found)
            if named and person_id not in named:
                held.add(asset_id)
        return held

    async def _name_by_filename(
        self,
        connection: Connection,
        files: Sequence[tuple[str, str]],
        *,
        unticked: set[str],
    ) -> tuple[list[tuple[str, str]], set[str], list[str]]:
        """Attribute each file to the person its own filename names."""
        attributed: list[tuple[str, str]] = []
        named: set[str] = set()
        invented: list[str] = []
        # Everybody the library already holds, read once for the whole folder rather than per file.
        # It is what lets a name INSIDE a filename be found at all: the reader can only pull out a
        # name a tool assembled, and a title that mentions somebody is not that shape.
        known = fold_known(await self._store.people_names_on(connection))
        for asset_id, filename in files:
            name = person_in_filename(filename, after_prefix=True)
            recognised = "" if name else _one_known_in(filename, known)
            if recognised:
                # Somebody already in the library, named in this file's own name. Attributed
                # directly rather than through the name: it is already a person, so there is nothing
                # to look up and nothing that could be invented.
                if fold(recognised[1]) in unticked:
                    continue
                written = await attribute_assets_recording_on(
                    connection, asset_ids=[asset_id], person_id=recognised[0]
                )
                attributed.extend((one, recognised[0]) for one in written)
                named.add(recognised[0])
                continue
            if not name or fold(name) in unticked:
                continue
            found = await people_named_on(connection, name)
            if len(found) > 1:
                continue
            person_id = (
                found[0]
                if found
                else await create_person_on(connection, name, made=by_sift(VIA_FILENAME))
            )
            if person_id is None:  # pragma: no cover (the reader never reads an empty name)
                continue
            if not found:
                invented.append(person_id)
            written = await attribute_assets_recording_on(
                connection, asset_ids=[asset_id], person_id=person_id
            )
            # And the file that named them becomes their picture, for the reason `confirm` gives in
            # full. A folder of two hundred shows invents people a file at a time, so the monogram
            # wall this closes is at its widest here.
            if not found and written:
                await give_person_a_cover_on(connection, person_id=person_id, asset_id=written[0])
            attributed.extend((one, person_id) for one in written)
            named.add(person_id)
        return attributed, named, invented
