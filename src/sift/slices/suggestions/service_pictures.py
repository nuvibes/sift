# SPDX-License-Identifier: AGPL-3.0-or-later
"""The metadata rule: naming a username's number from two fields inside its pictures."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access import (
    NumberedUsername,
    NumberLearned,
    by_sift,
    remember_username_number,
)
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_METADATA, Subject
from sift.slices.suggestions.metadata import FIELDS, agreed_name
from sift.slices.suggestions.naming import (
    Username,
)
from sift.slices.suggestions.service_base import FILENAMES_QUEUE, SuggestionBase
from sift.slices.suggestions.settings import READ_METADATA_KEY

log = get_logger(__name__)

#: How many of a number's files one pass will open looking for the two fields.
PICTURES_READ = 6


#: The file count a waiting number is remembered at when this pass could not open its pictures.
NOT_READ = 0


class MetadataRuleMixin(SuggestionBase):
    """Learns what a username number is called from the pictures that carry it."""

    async def reads_pictures(self) -> bool:
        """Whether this pass may open a picture to learn a number: the seam is here and the switch
        is on. One answer for `_learn_number` and for what a pass remembers about its numbers."""
        if self._pictures is None:
            return False
        return bool(await self._preferences.get_app(READ_METADATA_KEY))

    async def numbers_waiting(self) -> int:
        """How many username numbers this library has files for and cannot put a name to."""
        return await self._store.count_waiting_numbers()

    async def _learn_number(
        self, where: Username, asset_ids: Sequence[str]
    ) -> NumberedUsername | None:
        """Teach this library what a site's username number is called, from that number's pictures.

        **ONCE PER NUMBER, never once per file, and that inversion is the whole design.**

        **Two fields and nothing else**
        """
        if self._pictures is None or not await self.reads_pictures():
            return None
        # A fixed sample in the library's own order, so two passes over an unchanged number read
        # the same pictures and reach the same answer.
        sample = sorted(asset_ids)[:PICTURES_READ]
        readings = [await self._pictures.read(one) for one in sample]
        agreed = agreed_name(readings, where.site)
        if agreed is None:
            log.info(
                "suggestions.number_not_named",
                site=where.site,
                number=where.number,
                read=len(sample),
            )
            return None
        name, count = agreed
        username_id, wrote = await remember_username_number(
            self._store.database,
            site=where.site,
            name=name,
            number=where.number,
            learned=NumberLearned(via=VIA_METADATA, agreed=count),
            # The username is invented here as often as it is found, and nobody was asked about
            # either. `metadata` is the same word the filings this then writes carry, so the
            # username's own Created by line and `enriched:metadata` agree about it.
            made=by_sift(VIA_METADATA),
        )
        if not wrote:
            # Something else filled the column between the read and the write, or the username of
            # that name already carried a different number. Either way this pass did not learn
            # it, and saying it did would put a sentence on a file that is not true of it.
            log.info(
                "suggestions.number_already_known",
                site=where.site,
                number=where.number,
            )
            return None
        log.info(
            "suggestions.number_learned",
            site=where.site,
            number=where.number,
            name=name,
            agreed=count,
        )
        await self._record_number_learned(
            username_id=username_id, where=where, name=name, agreed=count
        )
        return NumberedUsername(name=name, via=VIA_METADATA, agreed=count)

    async def _record_number_learned(
        self, *, username_id: str, where: Username, name: str, agreed: int
    ) -> None:
        """The username's own line: what Sift learned about it, and out of what."""
        if self._recorder is None:
            return
        said = (
            f"Learned this username's {where.site} ID {where.number} from the "
            f"{FIELDS[0]} and {FIELDS[1]} fields of {agreed} pictures"
        )
        async with self._store.write() as connection:
            await self._recorder.record_on(
                connection,
                queue=FILENAMES_QUEUE,
                # Nobody decided it. The honest value rather than a gap: stamping whoever happened
                # to be signed in when a background pass ran would say they did something they did
                # not, which is the line `_record_filings` draws for the same reason.
                user_id=None,
                # Which task: the pass that read the pictures' own metadata.
                via=VIA_METADATA,
                title=said,
                detail=f"{said}.",
                payload=json.dumps(
                    {
                        "kind": "numbered",
                        "username_id": username_id,
                        "site": where.site,
                        "number": where.number,
                        "name": name,
                        "fields": list(FIELDS),
                        "agreed": agreed,
                    }
                ),
                subjects=[Subject(kind="username", id=username_id)],
            )
