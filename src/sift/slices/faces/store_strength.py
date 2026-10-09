# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a person's strength is read from, and the cap on the faces Sift files of her itself."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.access import Viewer
from sift.kernel.access.ranked import VIEWER_FILES, ranked_among
from sift.kernel.db import Connection
from sift.kernel.sql_splice import splice
from sift.slices.faces.models import Attribution, Origin
from sift.slices.faces.store_models import ModelsStore


@dataclass(frozen=True, slots=True)
class Counted:
    """One person's numbers behind her strength: her pictures by where they came from, and her
    faces in the library by what Sift did with them."""

    imported: int = 0
    confirmed: int = 0
    learned: int = 0
    turned: int = 0
    matched: int = 0
    asked: int = 0
    yes: int = 0
    no: int = 0


#: Her own pictures by origin, and the turned faces held for her and never compared.
_PICTURES = (
    "SELECT person_id, origin, COUNT(*) AS n FROM face_references "
    "WHERE origin != 'seed' {{WHO}}GROUP BY person_id, origin "
    "UNION ALL SELECT e.claimed_person_id, 'turned', COUNT(*) FROM pack_entry_faces AS f "
    "JOIN pack_entries AS e ON e.id = f.entry_id "
    "WHERE f.turned = 1 AND e.claimed_person_id IS NOT NULL {{WHO_HELD}}"
    "GROUP BY e.claimed_person_id"
)

#: Her faces in the files a viewer may see, by attribution, and the faces refused as her.
_FACES = (
    "SELECT person_id, attribution, COUNT(*) AS n FROM face_tracks "
    "WHERE person_id IS NOT NULL AND attribution IS NOT NULL {{WHO}}{{SEEN}}"
    "GROUP BY person_id, attribution "
    "UNION ALL SELECT j.person_id, 'no', COUNT(*) FROM face_rejections AS j "
    "JOIN face_tracks AS t ON t.id = j.track_id WHERE 1 = 1 {{WHO_REFUSED}}{{SEEN_REFUSED}}"
    "GROUP BY j.person_id"
)

_ONE = "AND person_id = :person "
_SEEN = splice("AND asset_id IN ({{VIEWER_FILES}}) ", VIEWER_FILES=VIEWER_FILES)
_SEEN_REFUSED = splice("AND t.asset_id IN ({{VIEWER_FILES}}) ", VIEWER_FILES=VIEWER_FILES)
_PICTURES_ALL = splice(_PICTURES, WHO="", WHO_HELD="")
_PICTURES_ONE = splice(_PICTURES, WHO=_ONE, WHO_HELD="AND e.claimed_person_id = :person ")
_FACES_SHAPES = {
    (one, seen): splice(
        _FACES,
        WHO=_ONE if one else "",
        WHO_REFUSED="AND j.person_id = :person " if one else "",
        SEEN=_SEEN if seen else "",
        SEEN_REFUSED=_SEEN_REFUSED if seen else "",
    )
    for one in (False, True)
    for seen in (False, True)
}

_BY_ORIGIN = {
    Origin.ADDED.value: "imported",
    Origin.PACK.value: "imported",
    Origin.CONFIRMED.value: "confirmed",
    Origin.RECOGNIZED.value: "learned",
    "turned": "turned",
}
_BY_ATTRIBUTION = {
    Attribution.MATCHED.value: "matched",
    Attribution.SUGGESTED.value: "asked",
    Attribution.CONFIRMED.value: "yes",
    "no": "no",
}

#: How many more faces Sift may file of her itself: never more than people confirmed of her.
_ROOM = (
    "SELECT COALESCE(SUM(origin = ?), 0) - COALESCE(SUM(origin = ?), 0) AS room "
    "FROM face_references WHERE person_id = ?"
)


class StrengthStore(ModelsStore):
    """The counts a person's strength rests on, and the cap on Sift's own picks of her."""

    async def strength_counts(
        self, person_id: str | None = None, *, viewer: Viewer | None = None
    ) -> dict[str, Counted]:
        """Everybody's numbers (or one person's), her faces counted in the files `viewer` may see.

        Two statements whatever the number of people: the pictures, then the faces.
        """
        among = None if viewer is None else await ranked_among(self._db, viewer)
        person = {} if person_id is None else {"person": person_id}
        found: dict[str, dict[str, int]] = {}
        pictures = _PICTURES_ALL if person_id is None else _PICTURES_ONE
        for row in await self._db.fetch_all(pictures, person):
            _add(found, row["person_id"], _BY_ORIGIN.get(str(row["origin"])), int(row["n"]))
        faces = _FACES_SHAPES[(person_id is not None, among is not None)]
        for row in await self._db.fetch_all(faces, {**person, **(among or {})}):
            _add(found, row["person_id"], _BY_ATTRIBUTION.get(str(row["attribution"])), row["n"])
        return {who: Counted(**numbers) for who, numbers in found.items()}

    async def room_for_picks_on(self, connection: Connection, person_id: str) -> int:
        """How many faces Sift may still file as her references itself, inside the caller's write:
        her confirmed pictures less the ones it filed already, never below none."""
        rows = list(
            await connection.execute_fetchall(
                _ROOM, (Origin.CONFIRMED.value, Origin.RECOGNIZED.value, person_id)
            )
        )
        return max(0, int(rows[0]["room"])) if rows else 0

    async def picks_over_cap(self) -> list[tuple[str, int]]:
        """The people with more of Sift's own picks than confirmed pictures, and by how many."""
        rows = await self._db.fetch_all(
            "SELECT person_id, SUM(origin = ?) - SUM(origin = ?) AS over FROM face_references "
            "WHERE origin IN (?, ?) GROUP BY person_id HAVING over > 0 ORDER BY person_id",
            (Origin.RECOGNIZED.value, Origin.CONFIRMED.value) * 2,
        )
        return [(str(row["person_id"]), int(row["over"])) for row in rows]

    async def retire_picks_on(
        self, connection: Connection, person_id: str, over: int
    ) -> Sequence[str]:
        """Take her `over` oldest picks out of her references, inside the caller's write. The
        faces keep their name; the pictures are the tidy-up's, as every removed reference's."""
        rows = await connection.execute_fetchall(
            "DELETE FROM face_references WHERE id IN (SELECT id FROM face_references "
            "WHERE person_id = ? AND origin = ? ORDER BY id LIMIT ?) RETURNING id",
            (person_id, Origin.RECOGNIZED.value, over),
        )
        return [str(row["id"]) for row in rows]


def _add(found: dict[str, dict[str, int]], who: object, field: str | None, n: object) -> None:
    if who is None or field is None:
        return
    numbers = found.setdefault(str(who), {})
    numbers[field] = numbers.get(field, 0) + int(str(n))
