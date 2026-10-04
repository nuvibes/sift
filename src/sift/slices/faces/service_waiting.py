# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people a facial fingerprints file or a folder brought whom no face here matches yet: the
list `Settings > Faces` draws, removing one of them, and the record that says so in History."""

from __future__ import annotations

import json

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FACIAL_FINGERPRINTS
from sift.kernel.workbench import DOER, Preview, Recorded, Worded
from sift.slices.faces.receipts import FINGERPRINTS_REMOVED_QUEUE
from sift.slices.faces.service_base import FaceServiceBase
from sift.slices.faces.service_fingerprints import MADE

log = get_logger(__name__)


class WaitingMixin(FaceServiceBase):
    """Listing the waiting entries, and forgetting one."""

    async def waiting(self) -> list[dict[str, object]]:
        """Every entry held and matched by no face yet, newest first: its id, name, how many
        faces, how many confirmed faces the file said they had, the file or folder it came from
        and when it was taken in."""
        return [
            {
                "entry_id": str(row["entry_id"]),
                "name": str(row["name"]),
                "faces": int(row["faces"]),
                "confirmed": None if row["confirmed"] is None else int(row["confirmed"]),
                "source": str(row["source"]),
                "added_at": int(row["added_at"]),
            }
            for row in await self._store.waiting_entries()
        ]

    async def created_from(self, person_ids: list[str]) -> dict[str, str]:
        """Of these People, the ones facial fingerprints created, each with the file or folder
        their History line names."""
        return await self._store.created_from_fingerprints(person_ids, act=MADE)

    async def remove_waiting(self, entry_id: str, *, by: str) -> bool:
        """Forget one waiting entry and its faces, pressed by `by`, with its History line in the
        same write. False when it is gone or already somebody's: nothing is written then."""
        async with self._store.database.write() as connection:
            removed = await self._store.remove_entry_on(connection, entry_id)
            if removed is None:
                return False
            announce(EVERY_ADMIN, About.LIBRARY)
            if self._recorder is not None:
                await self._recorder.record_on(
                    connection,
                    queue=FINGERPRINTS_REMOVED_QUEUE,
                    user_id=by,
                    via=VIA_FACIAL_FINGERPRINTS,
                    title=(
                        f"Removed the facial fingerprints of {removed['name']}"
                        f" from {removed['source']}"
                    ),
                    detail="",
                    payload=json.dumps(
                        {"entry": str(removed["name"]), "from": str(removed["source"])}
                    ),
                )
        crops = [str(one) for one in json.loads(str(removed["crops"] or "[]")) if one]
        await self._store.remove_entry_pictures(crops)
        log.info("faces.fingerprints.removed", faces=len(crops))
        return True


class RemovedFingerprintRecords:
    """A removed waiting entry in History, worded, and final: what it held came from a file or a
    folder, which can be imported again."""

    name = FINGERPRINTS_REMOVED_QUEUE
    #: Final. See the class.
    reversible = False

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the faces went with the entry."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back. See the class."""
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """ "You removed the facial fingerprints of <name> from <file or folder>"."""
        held = recorded.held()
        name, source = held.get("entry"), held.get("from")
        if not isinstance(name, str) or not isinstance(source, str):
            return None
        return Worded(said=(DOER, f" removed the facial fingerprints of {name} from {source}"))
