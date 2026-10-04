# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Yes on a name answers every folder asking it.

Two folders spelled the same are two questions about one name, and once one is answered the name
belongs to somebody. So the others are answered by the same press: their files filed as the pass's
silent rung files them, with its vetoes, and their questions settled. All of it is written on the
Yes's own connection and named in its receipt, because one press is one line and its Undo takes
back everything the press wrote, these folders included.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sift.kernel.access import attribute_assets_recording_on
from sift.kernel.db import Connection
from sift.slices.suggestions.service_base import AUTOMATIC
from sift.slices.suggestions.store import Claim

if TYPE_CHECKING:
    from sift.slices.suggestions.service_folders import FolderRuleMixin


@dataclass(frozen=True, slots=True)
class Namesake:
    """Another folder asking the answered name, and the files the pass's vetoes leave it."""

    claim_id: str
    folder_id: str
    assets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NamesakesFiled:
    """What answering the namesakes wrote, for the Yes's receipt."""

    attributed: tuple[str, ...] = ()
    remembered: tuple[str, ...] = ()
    answered: tuple[tuple[str, str], ...] = ()


async def namesakes_of(service: FolderRuleMixin, answered: Claim, person_id: str) -> list[Namesake]:
    """Every other folder asking `answered`'s name. Read before the Yes's write opens, because the
    vetoes ask the faces, and the single writer is not held across that."""
    found: list[Namesake] = []
    for one in await service._store.every_pending():
        if one.kind != "person" or one.name_key != answered.name_key or one.id == answered.id:
            continue
        assets = await service._store.assets_under(one.folder_id)
        held = await service._held_back(one.folder_id, person_id, assets)
        kept = tuple(asset for asset in assets if asset not in held)
        found.append(Namesake(claim_id=one.id, folder_id=one.folder_id, assets=kept))
    return found


async def file_namesakes_on(
    service: FolderRuleMixin,
    connection: Connection,
    namesakes: Sequence[Namesake],
    person_id: str,
) -> NamesakesFiled:
    """Answer each namesake on the Yes's connection. A question somebody else settled meanwhile is
    left as they left it. A folder whose every file is held back keeps no standing answer, as the
    pass's own rung leaves it."""
    attributed: list[str] = []
    remembered: list[str] = []
    answered: list[tuple[str, str]] = []
    for one in namesakes:
        if not await service._store.settle_on(connection, one.claim_id, "confirmed"):
            continue
        answered.append((one.claim_id, one.folder_id))
        if not one.assets:
            continue
        attributed += await attribute_assets_recording_on(
            connection, asset_ids=list(one.assets), person_id=person_id, source=AUTOMATIC
        )
        if await service._store.remember_folder_person_on(
            connection, folder_id=one.folder_id, person_id=person_id
        ):
            remembered.append(one.folder_id)
    return NamesakesFiled(
        attributed=tuple(attributed), remembered=tuple(remembered), answered=tuple(answered)
    )
