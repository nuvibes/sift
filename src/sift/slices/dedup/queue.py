# SPDX-License-Identifier: AGPL-3.0-or-later
"""Near-duplicate and exact-copy review as two workbench queues; the decisions live in the
service."""

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

#: The service's, so a receipt and the registry cannot disagree.
NAME = WORKBENCH_QUEUE
RECLAIM_NAME = RECLAIM_QUEUE

#: A carry's receipts: undoable, but no card, since a carry is made on a group already open.
CARRY_NAME = CARRY_QUEUE

#: One job at two confidences, so tabs of one screen.
DUPLICATES_GROUP = "duplicates"

PREVIEW_FILES = 24

#: One still per asset: a copy drawn twice would overstate what waits.
PREVIEW_COPIES = 24


def _group_anchor(method: str, first: str) -> str:
    """The queue's page at one group, which is what a still on the card leads to."""
    return f"/organize/{NAME}#group-{method}:{first}"


def _copy_anchor(asset_id: str) -> str:
    """The exact-copies page at one file, like `_group_anchor`."""
    return f"/organize/{RECLAIM_NAME}#copy-{asset_id}"


class DedupQueue:
    name = NAME
    title = "Near duplicates"
    draws_stills = True
    #: No threshold tells a re-encode, a crop and another shot from one scene apart.
    band = Band.DECISION
    group = DUPLICATES_GROUP
    group_title = "Duplicates"
    purpose = "Choose which copy to keep where files look alike or are stored twice."
    #: Mixed: `reverse` refuses a decision that deleted a file.
    reversible = True
    taken_back = "Taken back, so these can come up as duplicates again."

    def __init__(self, service: DedupService, access: Repository, settings: SettingsSeam) -> None:
        self._service = service
        self._access = access
        #: The dials as set, so the card counts what the screen behind it shows.
        self._settings = settings

    async def available(self) -> bool:
        """Always: comparing fingerprints needs nothing switched on."""
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        """How many groups are waiting (groups, not pairs), and how many a rule could not settle."""
        dials = await read_dials(self._settings)
        groups = await self._service.groups(dials)
        needs = sum(1 for one in groups if one.needs_a_person)
        return Summary(
            name=NAME,
            title=self.title,
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
        """The card's stills, from groups the page shows this viewer and whose stills are built."""
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
        """The files a decision was about, scoped now; old receipts name one `candidate_id`."""
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
            # Deleting either file takes the pair with it.
            return ()
        return await self._pictures(viewer, (pair.asset_a, pair.asset_b))

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a decision's pairs back in the queue; one that deleted a file is refused."""
        recorded = payload_held(payload)
        # Read before either shape: an empty list means nothing was deleted.
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
        """Stills for the files this user may be shown, in order, each leading to `where`."""
        allowed = await self._access.visible_of(viewer, list(asset_ids))
        return tuple(
            Preview(kind=ASSET, id=one, href=(where or {}).get(one, f"/asset/{one}"))
            for one in asset_ids
            if one in allowed
        )


def _closest(groups: Sequence[Group]) -> list[Group]:
    """The closest groups holding enough files for the card, as `_first_files` stops."""
    seen: set[str] = set()
    for count, group in enumerate(groups, start=1):
        seen.update(group.ids)
        if len(seen) >= PREVIEW_FILES:
            return list(groups[:count])
    return list(groups)


def _first_files(
    groups: Sequence[Group], drawn: Container[str] | None = None
) -> tuple[tuple[str, ...], dict[str, str]]:
    """The closest groups' files, each once, with the group it leads to; `drawn` filters them."""
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
    """Undo for a carry: takes the rows off the one file the receipt names."""

    name = CARRY_NAME
    reversible = True

    def __init__(self, service: DedupService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A still of the file that gained the attribution, if this user may be shown it."""
        asset_id, _ = _carry_record(payload)
        if not asset_id:
            return ()
        allowed = await self._access.visible_of(viewer, [asset_id])
        if asset_id not in allowed:
            return ()
        return (Preview(kind=ASSET, id=asset_id, href=f"/asset/{asset_id}"),)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take the carried rows off that file, and nothing else."""
        asset_id, carried = _carry_record(payload)
        if not asset_id or not carried:
            return False
        return await self._service.uncarry(asset_id=asset_id, carried=carried)


def _carry_record(payload: str) -> tuple[str, list[Carried]]:
    """One carry's file and rows, read from the record, never recomputed; the name is not used."""
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
        # Older receipts say `account`; stored JSON is never rewritten by a schema step.
        kind = "username" if kind == "account" else kind
        if kind in ("person", "username") and subject:
            carried.append(Carried(kind=kind, id=subject, name=str(one.get("name") or "")))
    return asset_id, carried


def _ids(recorded: dict[str, Any], key: str) -> tuple[str, ...]:
    """A list of ids off a record, or nothing where it is absent or not a list."""
    found = recorded.get(key)
    if not isinstance(found, list):
        return ()
    return tuple(str(one) for one in found if one)


def _field(recorded: dict[str, Any], key: str) -> str | None:
    """One field of a parsed record, or None where it is absent or empty."""
    value = recorded.get(key)
    return str(value) if value else None


class ReclaimQueue:
    name = RECLAIM_NAME
    title = "Exact duplicates"
    draws_stills = True
    #: Identical bytes need no judgement, only a choice of which copy to keep.
    band = Band.CLEANUP
    group = DUPLICATES_GROUP
    group_title = None
    purpose = "Choose which copy to keep of a file stored in more than one place."
    #: A released copy was deleted from disk.
    reversible = False

    def __init__(self, service: DedupService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def available(self) -> bool:
        """Always: noticing a file in two places needs nothing switched on."""
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
        """The file a copy was let go of, which is still here."""
        recorded = payload_held(payload)
        asset_id = _field(recorded, "asset_id")
        if asset_id is None:
            return ()
        return await self._pictures(viewer, (asset_id,))

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back: the released copy's bytes are gone from disk."""
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown (see `kernel.workbench.Recorded`)."""
        asset_id = recorded.held().get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            return None
        return Worded(
            said=(DOER, " removed an extra copy of ", Named(kind="asset", id=asset_id)),
            more=(recorded.detail,) if recorded.detail else (),
        )

    async def _pictures(self, viewer: Viewer, asset_ids: tuple[str, ...]) -> tuple[Preview, ...]:
        """Stills for the one file a receipt names, if this user may be shown it."""
        allowed = await self._access.visible_of(viewer, list(asset_ids))
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in asset_ids if one in allowed
        )

    async def _stills(self, viewer: Viewer, asset_ids: tuple[str, ...]) -> tuple[Preview, ...]:
        """The card's stills: visible files with a built still, in order, each led to its row."""
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
