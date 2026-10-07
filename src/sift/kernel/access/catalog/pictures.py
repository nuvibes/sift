# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loose pictures: a creator's stills in no Photo Set, for the pass that proposes shoots."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.db import Database

# --- LOOSE PICTURES: one creator's stills that are in no Photo Set ------------------------------
#
# Unscoped system reads for the pass that proposes shoots, kept in the kernel because a gate refuses
# a feature's own SQL against `assets`. Loose is a picture in no Photo Set (a shoot is a sitting of
# stills), and both go through the sweep lane, since they grow with the library.
#: Which creators have enough loose pictures to look at: one row per person, not per file.
_CREATORS_WITH_LOOSE_PICTURES = """
SELECT ap.person_id AS person_id, p.name AS name, COUNT(*) AS loose
  FROM asset_people ap
  JOIN assets a ON a.id = ap.asset_id AND a.media_type IN ('image', 'gif')
  JOIN people p ON p.id = ap.person_id
 WHERE NOT EXISTS (SELECT 1 FROM photo_set_items i WHERE i.asset_id = ap.asset_id)
 GROUP BY ap.person_id, p.name
HAVING COUNT(*) >= ?
 ORDER BY loose DESC, ap.person_id
"""

#: One creator's loose pictures in name order, the order a gallery is numbered in.
_LOOSE_PICTURES_OF = """
SELECT a.id AS id
  FROM asset_people ap
  JOIN assets a ON a.id = ap.asset_id AND a.media_type IN ('image', 'gif')
 WHERE ap.person_id = ?
   AND NOT EXISTS (SELECT 1 FROM photo_set_items i WHERE i.asset_id = a.id)
 ORDER BY COALESCE(a.filename_sort, a.original_filename), a.id
 LIMIT ?
"""


@dataclass(frozen=True, slots=True)
class LoosePictures:
    """One creator and how many of their pictures are in no Photo Set."""

    person_id: str
    name: str
    loose: int


async def creators_with_loose_pictures(db: Database, *, least: int) -> list[LoosePictures]:
    """Every creator with at least `least` pictures in no Photo Set, most first."""
    rows = await db.sweep_all(
        _CREATORS_WITH_LOOSE_PICTURES, (least,), what="creators with loose pictures"
    )
    return [
        LoosePictures(
            person_id=str(row["person_id"]), name=str(row["name"]), loose=int(row["loose"])
        )
        for row in rows
    ]


async def loose_pictures_of(db: Database, person_id: str, *, limit: int) -> list[str]:
    """One creator's pictures that are in no Photo Set, in name order, at most `limit` of them."""
    rows = await db.sweep_all(
        _LOOSE_PICTURES_OF, (person_id, limit), what="loose pictures of one creator"
    )
    return [str(row["id"]) for row in rows]


#: Which of these files are pictures that carry nobody and are in no Photo Set: the ones worth
#: offering to name. A picture carrying somebody else is not: disagreeing is another decision.
_UNNAMED_PICTURES_AMONG = """
SELECT a.id AS id
  FROM assets a
 WHERE a.id IN (SELECT value FROM json_each(?))
   AND a.media_type IN ('image', 'gif')
   AND NOT EXISTS (SELECT 1 FROM asset_people ap WHERE ap.asset_id = a.id)
   AND NOT EXISTS (SELECT 1 FROM photo_set_items i WHERE i.asset_id = a.id)
"""


async def unnamed_pictures_among(db: Database, asset_ids: Sequence[str]) -> set[str]:
    """Which of these are pictures with nobody named in them and no Photo Set of their own."""
    wanted = sorted({str(one) for one in asset_ids})
    if not wanted:
        return set()
    rows = await db.fetch_all(_UNNAMED_PICTURES_AMONG, (json.dumps(wanted),))
    return {str(row["id"]) for row in rows}


#: Which Photo Set holds each of these files: a proposal whose pictures were filed since is a
#: question already answered, and making it anyway would make a second set of them.
_PHOTO_SETS_HOLDING = """
SELECT i.asset_id AS asset_id, i.photo_set_id AS photo_set_id
  FROM photo_set_items i
 WHERE i.asset_id IN (SELECT value FROM json_each(?))
 ORDER BY i.asset_id, i.photo_set_id
"""


async def photo_sets_holding(db: Database, asset_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Photo Sets holding each of these files, by file. A file in none is not in the answer."""
    held: dict[str, list[str]] = {}
    wanted = sorted({str(one) for one in asset_ids})
    if not wanted:
        return {}
    for row in await db.fetch_all(_PHOTO_SETS_HOLDING, (json.dumps(wanted),)):
        held.setdefault(str(row["asset_id"]), []).append(str(row["photo_set_id"]))
    return {asset_id: tuple(sets) for asset_id, sets in held.items()}
