# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repairs that run at boot over rows already stored and read no model, and the control that
deletes every face.
"""

from __future__ import annotations

from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.slices.faces.forget_all import Progress
from sift.slices.faces.service_base import FaceServiceBase

log = get_logger(__name__)

#: How many files the reconcile settles in one turn at the single writer.
#:
#: Sized so a turn is short beside a press somebody makes while it runs: `_settle_all` reads each
#: batch's faces and claims in pages of 900 and writes them in one transaction, so 500 files is one
#: page of each read and one short write, and three thousand files are six turns.


class UpkeepMixin(FaceServiceBase):
    """Boot repairs, and clearing every face out."""

    async def forget_everything(self, *, actor: Actor, progress: Progress | None = None) -> None:
        """Delete every face and every reference. Deliberate, and separate from the switch.

        The index is told about each batch of files that lost a name, or a search for a person
        would still find a file that no longer says they are in it.
        """
        unnamed = await self._store.forget_everything(
            actor=actor, touched=self._reindexer.touched_many, progress=progress
        )
        log.info("faces.forgotten", files_renamed=unnamed)
