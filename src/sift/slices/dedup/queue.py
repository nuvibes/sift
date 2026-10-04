# SPDX-License-Identifier: AGPL-3.0-or-later
"""Near-duplicate review, as the workbench sees it.

The workbench knows nothing about fingerprints. What it knows is that something registered a queue
with a name, a count and a way to take a decision back, and this is that, for this feature.

## Two queues, because they are two questions

**Near Duplicates** asks whether two files that look alike are the same thing. It is a re-encode, or
a crop, or a genuinely different shot from the same scene, and no threshold settles which: a
person looks at two pictures and says.

**Exact Duplicates** asks nothing of the kind. These are identical bytes, already resolved into one
asset with several locations when they were imported, so there is no judgement about what they are.
What is left is still a decision and still nobody else's to make: which copy to keep. A second copy
on a second disk may be the whole point of it, and no rule can know.

They are two cards here, which is what gives them two things a settings screen cannot: every
decision written into the record, and a way to take one back wherever taking it back is possible
at all.

Everything here is a translation. The decisions themselves, and every rule about who may be told
what, stay in the service beside it.
"""

from __future__ import annotations

from collections.abc import Container, Sequence
from typing import Any

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import Carried
from sift.kernel.seams import SettingsSeam
from sift.kernel.workbench import (
    ASSET,
    DOER,
    Band,
    Named,
    Preview,
    Recorded,
    Summary,
    Worded,
    payload_held,
)
from sift.slices.dedup.grouping import Group
from sift.slices.dedup.service import (
    CARRY_QUEUE,
    RECLAIM_QUEUE,
    WORKBENCH_QUEUE,
    DedupService,
    read_dials,
)

#: What the two queues are called. The service's, so a receipt and the registry cannot disagree.
NAME = WORKBENCH_QUEUE
RECLAIM_NAME = RECLAIM_QUEUE

#: And what the carry's receipts are filed under. A name with no card behind it, deliberately: a
#: carry is offered on the group somebody is already looking at, so there is no pile of them
#: waiting and nothing to survey. What it needs from the workbench is the other half (a way to
#: take one back), which is exactly what a `Reverser` with no `Queue` is for.
CARRY_NAME = CARRY_QUEUE

#: The page these two share. Near duplicates and exact copies are one job at two confidences,
#: so they are tabs of one screen rather than two cards that have to be visited separately.
DUPLICATES_GROUP = "duplicates"

#: How many stills a card puts on screen before the number speaks for itself. Taken from the first
#: groups in the queue (the closest ones), so what is drawn is what would be decided first.
PREVIEW_FILES = 24

#: And how many stills the exact-copies card draws. One per asset here rather than two, because a
#: copy is the same picture twice and drawing it twice would say there is more waiting than there
#: is.
PREVIEW_COPIES = 24


def _group_anchor(method: str, first: str) -> str:
    """Where one group of look-alikes sits, for a still on the board to point at.

    **The queue's own page, at the group, rather than at the file.** Opening the FILE a still is a
    picture of is the one thing pressing it cannot usefully mean here: what the card is about is the
    group, and a picture of one member of it would lead away from the decision instead of into it.

    A fragment rather than an address of its own, because a group has no address of its own and
    cannot be given one. It is computed from the pair table at the dials in force, so it exists for
    exactly as long as those dials do: the screen that holds one is the queue, and the only stable
    name it has is its method and its smallest file, which is the same name the client pages by
    (`keyOf`). The panel marks each group with it.

    A group holding a file this user may not be shown is not on the page at all, so the card draws
    no still of it and this anchor is never made for it (`DedupQueue._card_pictures`). Made for one,
    it would name that file's id on the board and lead to a group the page does not show.
    """
    return f"/organize/{NAME}#group-{method}:{first}"


def _copy_anchor(asset_id: str) -> str:
    """And where one file stored more than once sits, for the same reason.

    Exact copies have no group of their own either: a row there is ONE asset with several locations,
    named by the asset. The same shape as the near-duplicate anchor above, and deliberately so:
    the two are tabs of one screen and a still on the card behaves the same way on both.
    """
    return f"/organize/{RECLAIM_NAME}#copy-{asset_id}"


class DedupQueue:
    """Pairs that look alike and have not been judged, waiting for somebody to say."""

    name = NAME
    title = "Near duplicates"
    #: Whether two files that look alike ARE the same thing is a judgement and no
    #: threshold settles it: a re-encode, a crop and a different shot from one scene all
    #: measure the same. That is the whole reason this is here and not under Maintenance.
    band = Band.DECISION
    #: One page with Exact Duplicates: the same job asked at two confidences, and somebody
    #: clearing duplicates reads them as one thing rather than two destinations.
    group = DUPLICATES_GROUP
    #: And what the whole page is called, because the board draws a group as ONE card.
    group_title = "Duplicates"
    #: What the card on the board is for: this group's page, which this queue leads. See
    #: `Queue.purpose`.
    purpose = "Choose which copy to keep where files look alike or are stored twice."
    #: Mixed, so True: a pair judged different goes back in the queue, and one judged the same
    #: with a file deleted does not. `reverse` refuses that second kind per decision, which is a
    #: thing this flag cannot express.
    reversible = True
    #: What an undone "these are different" leaves true: the pairs are back in the queue. The
    #: sentence the decision wrote ("these will not be raised again") is the one thing that is
    #: no longer so. See `kernel.workbench.TakenBack`.
    taken_back = "Taken back, so these can come up as duplicates again."

    def __init__(self, service: DedupService, access: Repository, settings: SettingsSeam) -> None:
        self._service = service
        self._access = access
        #: The dials as set, read through the same one reader the queue's own screen uses.
        #:
        #: Not the registered defaults, which would be cheaper by two point reads per survey and
        #: would put a different number on the card than the screen behind it shows. A count that
        #: disagrees with the list it links to is the fault this whole feature spent its design
        #: avoiding, and it would be strange to reintroduce it on the way in.
        self._settings = settings

    async def available(self) -> bool:
        """Always. Comparing fingerprints needs nothing switched on and no model downloaded.

        The count can be zero, and a zero here is the honest kind: there is nothing left to answer,
        which is the state this whole screen is trying to reach.
        """
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        """How many groups are waiting, and how many of them a rule could not settle.

        **The count is GROUPS, not pairs.** Three copies of one clip are three pairs and one
        question, so counting pairs would put a number on this card several times the amount of
        work behind it, and it would go down by three when somebody answered once.

        The sentence carries the second number, and the two are different facts. Every group waits
        for a person: Sift proposes and nothing is ever deleted without a press. What the rule could
        not separate is the part that costs a real decision rather than a skim, and that is the
        figure somebody uses to decide whether to open this now or later.
        """
        dials = await read_dials(self._settings)
        groups = await self._service.groups(dials)
        needs = sum(1 for one in groups if one.needs_a_person)
        return Summary(
            name=NAME,
            title=self.title,
            # GROUPS, not files, because that is what the count is. See the docstring above. A
            # verb saying "files" over a figure counting groups would misstate what the count
            # measures.
            verb="groups that look alike",
            verb_one="group that looks alike",
            decision=(
                "Files that look alike, in groups. Sift marks the copy to keep, and you confirm a "
                "page at a time."
                + (
                    f" {needs} {'group needs' if needs == 1 else 'groups need'} you to choose the "
                    "copy."
                    if needs
                    else " Sift chose the copy in every group."
                )
            ),
            icon="content_copy",
            count=len(groups),
            preview=await self._card_pictures(viewer, groups),
        )

    async def _card_pictures(self, viewer: Viewer, groups: Sequence[Group]) -> tuple[Preview, ...]:
        """The card's stills: the closest groups the queue's page shows this viewer, and nothing of
        the others.

        The page leaves out a group holding any file this viewer may not be shown: restricted, or
        held back by a shut vault (`router.list_groups`). A still of one of that group's other files
        would lead to a group that is not there, and its anchor, named by the group's smallest file,
        would put the id of the file being kept from them on the board. So the groups are held to
        the page's own rule before any still is chosen: one visibility read over the groups the card
        could draw, and a card that comes out short where some of them were left out.

        The same read says which files have a still built (`has_thumb`), and only those are sent:
        a file whose picture pass has not run yet, or gave up on it, has nothing at its address,
        and a card asking for it draws a blank.
        """
        window = _closest(groups)
        seen = await self._access.assets_of(
            viewer, sorted({one for group in window for one in group.ids})
        )
        files, leads = _first_files(
            [group for group in window if all(one in seen for one in group.ids)],
            drawn={one for one, view in seen.items() if view.has_thumb},
        )
        return tuple(Preview(kind=ASSET, id=one, href=leads[one]) for one in files)

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """The files a decision was about, whichever shape the record was written in.

        **Two shapes, because a record outlives the version that wrote it.** A receipt from before
        this queue reviewed groups names one `candidate_id` and the two files are read back off the
        pair; one written since carries the group's files in `group`. A restored backup holds both,
        so both are read, and a payload with neither is a decision this version cannot illustrate
        rather than a failure.

        Scoped through the access layer rather than trusted from the record, and that is the whole
        reason this asks rather than simply listing them: a file vaulted or restricted since the
        decision is one this user may no longer be shown, and having decided about it once is
        not a licence to draw it now. A decision whose files are all out of reach shows the
        decision and no pictures.
        """
        recorded = payload_held(payload)
        group = _ids(recorded, "group")
        if group:
            return await self._pictures(viewer, group)
        candidate_id = _field(recorded, "candidate_id")
        if candidate_id is None:
            return ()
        try:
            pair = await self._service.get(candidate_id)
        except Exception:
            # The pair is gone, which is ordinary: deleting either file takes the row with it.
            return ()
        return await self._pictures(viewer, (pair.asset_a, pair.asset_b))

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a decision's pairs back in the queue, from what it wrote down about itself.

        **A decision that deleted a file cannot be taken back, and this says so by refusing.** The
        rows could be set back to pending easily enough, and doing that would be worse than useless:
        it would raise a question about files that no longer exist. Deleting is the one thing in
        Sift that is final, the sentence in the record says so, and undo does not pretend otherwise.

        Both record shapes are read (one pair by `candidate_id`, or a group's pairs in
        `candidates`) because a record outlives the version that wrote it and a restored backup
        holds both.

        Every field is reached for rather than assumed. A payload missing a key it once had is one
        this cannot reverse, and "nothing was put back" is the honest reading of that; reaching
        straight in would fail the request instead, which reads as undo being broken rather than as
        the record being unreadable.
        """
        recorded = payload_held(payload)
        # A file went, so there is nothing to put back. Read before either shape, because it is the
        # same refusal for both, and it is a LIST in the group shape, where an empty one means
        # nothing was deleted rather than the key being absent.
        removed = recorded.get("removed")
        if isinstance(removed, list):
            if removed:
                return False
        elif _field(recorded, "removed") is not None:
            return False
        candidates = _ids(recorded, "candidates")
        if candidates:
            return await self._service.restore_group(candidates)
        candidate_id = _field(recorded, "candidate_id")
        if candidate_id is None:
            return False
        return await self._service.unsettle(candidate_id)

    async def _pictures(
        self,
        viewer: Viewer,
        asset_ids: tuple[str, ...],
        where: dict[str, str] | None = None,
    ) -> tuple[Preview, ...]:
        """Stills for the ones this user may actually be shown, in the order given.

        Asked in ONE batch rather than one question per file. Deciding who may see a file runs a
        recursive walk over the sharing rules, so a card with twenty-four stills on it would be
        twenty-four of those, and this is drawn on the workbench board, which surveys every queue
        at once.

        The order is the caller's and not the answer's. `visible_of` hands back an unordered
        collection, and reading one back would shuffle the pairs on the card into an order nothing
        chose.

        `where` is where each still LEADS, by file, and the default says the file itself. The two
        callers want different answers from the same read and both are right: a still on the CARD is
        one member of a group waiting to be judged, so it opens the group (`_group_anchor`), and a
        still in the RECORD is a file a decision was taken about, so it opens that file. Passed in
        rather than decided here, because this read knows which files it was handed and nothing at
        all about why.

        Never handed nothing: a record with no files under `group` falls through to the pair, and a
        pair is two files.
        """
        allowed = await self._access.visible_of(viewer, list(asset_ids))
        return tuple(
            Preview(kind=ASSET, id=one, href=(where or {}).get(one, f"/asset/{one}"))
            for one in asset_ids
            if one in allowed
        )


def _closest(groups: Sequence[Group]) -> list[Group]:
    """The closest groups, as many as it takes to hold enough files for the card's stills: the
    same groups `_first_files` would stop at."""
    seen: set[str] = set()
    for count, group in enumerate(groups, start=1):
        seen.update(group.ids)
        if len(seen) >= PREVIEW_FILES:
            return list(groups[:count])
    return list(groups)


def _first_files(
    groups: Sequence[Group], drawn: Container[str] | None = None
) -> tuple[tuple[str, ...], dict[str, str]]:
    """The files of the closest groups, in order, with none drawn twice, and where each one leads.

    `drawn` is the files a still can be drawn for; a file outside it is passed over and the next
    one takes its place. None means every file.

    A file can be in more than one group (once per fingerprint method), so the same still would
    otherwise appear across the card twice, which reads as more waiting than there is.

    The FIRST group a file was seen in is the one its still opens, which falls out of the same rule:
    a file kept only the first time it appears, so the group it was kept for is the group it belongs
    to on this card. The alternative (one still pointing at two groups) is not a thing a link
    can be.
    """
    seen: list[str] = []
    leads: dict[str, str] = {}
    for group in groups:
        anchor = _group_anchor(group.method, group.ids[0])
        for one in group.ids:
            if drawn is not None and one not in drawn:
                continue
            if one not in seen:
                seen.append(one)
                leads[one] = anchor
        if len(seen) >= PREVIEW_FILES:
            break
    kept = tuple(seen[:PREVIEW_FILES])
    return kept, {one: leads[one] for one in kept}


class CarriedAttributions:
    """Reads one carry's receipt back, and takes the rows it wrote off the one file it wrote them on.

    A reverser with no card. Everything else on the board is a pile somebody works through; this is
    a decision made on a group already in front of them, so there is nothing to count and nothing to
    survey: only a record, and the ability to take one back.

    **Per file, and that is the shape of the whole feature.** A press carried an attribution onto
    four copies and wrote four receipts, so somebody who later decides one of those four is not her
    takes that one back and the other three stand. Taking back the file it was carried FROM does
    nothing to any of them: the copied row is that file's own fact now, and the file it came from
    may since have been deleted.
    """

    name = CARRY_NAME
    #: Every carry wrote a receipt naming the one file it wrote on, and `reverse` removes the rows.
    reversible = True

    def __init__(self, service: DedupService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A still of the file that gained the attribution, if this user may be shown it.

        Scoped rather than shown, exactly as the two cards above scope theirs: a file restricted
        since the decision is one this user may no longer see, and a record of having written on
        it is not a licence to draw it.
        """
        asset_id, _ = _carry_record(payload)
        if not asset_id:
            return ()
        allowed = await self._access.visible_of(viewer, [asset_id])
        if asset_id not in allowed:
            return ()
        return (Preview(kind=ASSET, id=asset_id, href=f"/asset/{asset_id}"),)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take the carried rows off that file, and nothing else.

        Every field is reached for rather than assumed: a record can outlive the version that wrote
        it, and a payload missing a key it once had is one this cannot reverse. False ("nothing
        was put back") is the honest reading of that, rather than failing the request, which would
        read as Undo being broken rather than as the record being unreadable.
        """
        asset_id, carried = _carry_record(payload)
        if not asset_id or not carried:
            return False
        return await self._service.uncarry(asset_id=asset_id, carried=carried)


def _carry_record(payload: str) -> tuple[str, list[Carried]]:
    """One carry read back out of its own record: the file, and the rows that were written on it.

    The rows are read from the RECORD and never worked out again from the file's copies. What a
    decision did is a fact about the moment it happened (the group it came from may since have
    grown, shrunk or been re-fingerprinted), and an undo that recomputed its subject could remove a
    row this press never wrote.

    The NAME is not read back. It exists in the payload for a reader of the record, and the delete
    is keyed on the id and the source word; taking a name off a record and using it to find a row
    would be matching on the one field somebody is free to change.
    """
    recorded = payload_held(payload)
    asset_id = _field(recorded, "asset_id")
    rows = recorded.get("carried")
    if not asset_id or not isinstance(rows, list):
        return "", []
    carried: list[Carried] = []
    for one in rows:
        if not isinstance(one, dict):
            continue
        kind = str(one.get("kind") or "")
        subject = str(one.get("id") or "")
        # An older receipt says `account` for a username: the payload is stored JSON, which no
        # schema step rewrites, so the former word is read as the new one here.
        kind = "username" if kind == "account" else kind
        if kind in ("person", "username") and subject:
            carried.append(Carried(kind=kind, id=subject, name=str(one.get("name") or "")))
    return asset_id, carried


def _ids(recorded: dict[str, Any], key: str) -> tuple[str, ...]:
    """A list of ids off a record, or nothing where the key is absent or is not a list.

    Its own reader rather than a cast, because this is the half of a payload that decides what undo
    reaches and what a card draws. A record written by another version can carry anything under a
    key, and the honest answer to a shape this one does not recognise is to have nothing rather than
    to fail the request.
    """
    found = recorded.get(key)
    if not isinstance(found, list):
        return ()
    return tuple(str(one) for one in found if one)


def _field(recorded: dict[str, Any], key: str) -> str | None:
    """One field of a record, or None where it is absent or empty.

    Read off an already-parsed record rather than off the payload, so a caller that wants two
    fields parses the JSON once. It also means there is no "the payload was unreadable" answer
    here: whoever parsed it has already dealt with that, and a second copy of the decision is a
    second place for the two to disagree.
    """
    value = recorded.get(key)
    return str(value) if value else None


class ReclaimQueue:
    """Assets whose bytes sit in more than one place, waiting for somebody to choose."""

    name = RECLAIM_NAME
    title = "Exact duplicates"
    #: Nothing to judge: identical bytes are identical. It is still nobody else's decision
    #: (a second copy on a second disk may be exactly what somebody wanted), which is why it
    #: is on the board at all; it is simply not a question being weighed up.
    band = Band.CLEANUP
    #: See `DedupQueue.group`.
    group = DUPLICATES_GROUP
    #: Not the first of its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: What this tab is for, said by its group's card only where this queue leads it. See
    #: `Queue.purpose`.
    purpose = "Choose which copy to keep of a file stored in more than one place."
    #: A released copy was deleted from the disk. Every decision here is final. See `reverse`.
    reversible = False

    def __init__(self, service: DedupService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def available(self) -> bool:
        """Always. Noticing that one file is in two places needs nothing switched on.

        A zero is the honest kind and is the state this is trying to reach: nothing is stored
        twice, or everything that was has been dealt with.
        """
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        totals = await self._service.reclaim_totals()
        page = await self._service.reclaim(limit=PREVIEW_COPIES, offset=0)
        return Summary(
            name=RECLAIM_NAME,
            title=self.title,
            verb="files stored more than once",
            verb_one="file stored more than once",
            decision=(
                "The same file, byte for byte, in more than one place. Choose which copy to keep. "
                "Deleting the others frees their disk space permanently, and the file keeps "
                "everything recorded about it."
            ),
            icon="file_copy",
            count=totals.assets,
            preview=await self._stills(viewer, tuple(one.asset_id for one in page)),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """The file a copy was let go of, which is still here: that is the whole point of it.

        Unlike every other deletion on this board, the asset survives: what went was one of the
        places its bytes sat. So there IS something to draw, and it is the file itself.
        """
        recorded = payload_held(payload)
        asset_id = _field(recorded, "asset_id")
        if asset_id is None:
            return ()
        return await self._pictures(viewer, (asset_id,))

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back.

        A copy that was released was deleted from the disk. The asset is still here and nothing
        about it was lost, but the bytes at that path are gone and no record can bring them back,
        so this refuses rather than restoring a location row pointing at a file that is not there.
        """
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown. See `kernel.workbench.Recorded`.

        The stored title said "Removed an extra copy": nobody doing it and no file named, though
        the payload records which file's copy went. The line under it is the stored detail, which
        is where the copy's own path is said.
        """
        asset_id = recorded.held().get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            return None
        return Worded(
            said=(DOER, " removed an extra copy of ", Named(kind="asset", id=asset_id)),
            more=(recorded.detail,) if recorded.detail else (),
        )

    async def _pictures(self, viewer: Viewer, asset_ids: tuple[str, ...]) -> tuple[Preview, ...]:
        """Stills for the ones this user may be shown, in the order given. See `DedupQueue`.

        Only ever asked about the one file a receipt names; the card's own stills are `_stills`."""
        allowed = await self._access.visible_of(viewer, list(asset_ids))
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in asset_ids if one in allowed
        )

    async def _stills(self, viewer: Viewer, asset_ids: tuple[str, ...]) -> tuple[Preview, ...]:
        """The card's stills: the files this user may be shown whose still has been built, in the
        order given, each leading to its row on the queue's page. One read answers both, as on the
        near-duplicates card."""
        if not asset_ids:
            return ()
        seen = await self._access.assets_of(viewer, list(asset_ids))
        return tuple(
            Preview(kind=ASSET, id=one, href=_copy_anchor(one))
            for one in asset_ids
            if one in seen and seen[one].has_thumb
        )


__all__ = [
    "CARRY_NAME",
    "NAME",
    "PREVIEW_COPIES",
    "PREVIEW_FILES",
    "RECLAIM_NAME",
    "CarriedAttributions",
    "DedupQueue",
    "ReclaimQueue",
]
