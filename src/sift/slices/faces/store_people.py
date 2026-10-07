# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people the face feature knows: the roster, the counts of their references, their aliases."""

from __future__ import annotations

import time
from collections.abc import Iterable

from sift.kernel.access import (
    Made,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, telling
from sift.kernel.db import Connection, in_clause
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.slices.faces.models import (
    Attribution,
    Reference,
)
from sift.slices.faces.store_pictures import (
    PicturesStore,
)
from sift.slices.faces.store_records import (
    _NAMES_THE_FILE,
    _reference,
)

#: The references a comparison may be made against: all of somebody's own, and her starters only
#: while in use AND she has no own reference under the same model (a merge or a model change can
#: leave both kinds standing). Bound as `(recognizer, recognizer)`.
_GALLERY_ROWS = (
    "SELECT r.* FROM face_references AS r "
    "WHERE r.recognizer = ? AND (r.origin != 'seed' OR (r.retired_at IS NULL AND NOT EXISTS ("
    "SELECT 1 FROM face_references AS own WHERE own.person_id = r.person_id "
    "AND own.origin != 'seed' AND own.recognizer = ?))) "
    "ORDER BY r.person_id, r.id"
)


class PeopleStore(PicturesStore):
    """The people the face feature knows: the roster, the counts of their references, their aliases."""

    async def confirmed_appearances(self, person_id: str) -> int:
        """How many separate appearances somebody agreed were this person: fewer than her
        references, since one agreement files every stored picture of that appearance."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM face_tracks WHERE person_id = ? AND attribution = ?",
            (person_id, Attribution.CONFIRMED.value),
        )
        return 0 if row is None else int(row["n"])

    async def roster(self, prefix: str = "") -> list[tuple[str, str, int, str | None, int]]:
        """Everybody who has reference faces here: id, name, how many, one crop, how many starters.

        How many counts her OWN pictures; starters are counted apart so the screen can mark
        somebody known by starters alone (asked about, never named). Retired starters are not
        listed. Keyed on having references, not on being a Person: a person with none is not
        recognizable. Named by joining `people` and ordered by name.
        """
        rows = await self._db.fetch_all(
            """
            SELECT r.person_id AS person_id,
                   p.name      AS name,
                   SUM(r.origin != 'seed') AS faces,
                   MIN(r.crop_path) AS crop,
                   SUM(r.origin = 'seed') AS starters
              FROM face_references r
              JOIN people p ON p.id = r.person_id
             WHERE (? = '' OR p.name LIKE ? ESCAPE '\\')
               AND (r.origin != 'seed' OR r.retired_at IS NULL)
             GROUP BY r.person_id, p.name
             ORDER BY COALESCE(p.name_sort, p.name), r.person_id
            """,
            (prefix, f"%{prefix}%"),
        )
        return [
            (
                str(row["person_id"]),
                str(row["name"]),
                int(row["faces"]),
                row["crop"],
                int(row["starters"]),
            )
            for row in rows
        ]

    async def reference_counts(self, *, recognizer: str | None = None) -> dict[str, int]:
        """How many reference faces each person has, keyed by id; absent rather than zero.

        **Named a model, it counts only the pictures that model measured**, the ones a comparison
        is made against, mirroring `reference_gallery`'s filter: after a model change the bar
        must not be lowered by evidence no comparison uses.
        """
        # Her OWN pictures: a starter lends no strength and must not lower the bar.
        rows = await (
            self._db.fetch_all(
                "SELECT person_id AS person_id, COUNT(*) AS faces "
                "FROM face_references WHERE origin != 'seed' GROUP BY person_id",
                (),
            )
            if recognizer is None
            else self._db.fetch_all(
                "SELECT person_id AS person_id, COUNT(*) AS faces FROM face_references "
                "WHERE recognizer = ? AND origin != 'seed' GROUP BY person_id",
                (recognizer,),
            )
        )
        return {str(row["person_id"]): int(row["faces"]) for row in rows}

    async def reference_stamp(self) -> tuple[int, str, int]:
        """A cheap value that changes whenever the reference faces do.

        The count, the highest id and how many starters are retired: ids are minted under a floor
        that never goes down, so any insert or removal moves one of the first two, and retiring a
        starter (an UPDATE) moves the third. Read from the table, so it cannot go out of step
        with it; compared for being different, never for being higher.
        """
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS held, COALESCE(MAX(id), '') AS newest, "
            "COUNT(retired_at) AS retired FROM face_references",
            (),
        )
        return (
            (0, "", 0)
            if row is None
            else (int(row["held"]), str(row["newest"]), int(row["retired"]))
        )

    async def reference_gallery(self, recognizer: str) -> dict[str, list[Reference]]:
        """Everybody's reference faces described by this model, by person: a gallery blending
        two models' numbers matches nothing in particular."""
        # With her starters only while they are all Sift has of her. See `_GALLERY_ROWS`.
        rows = await self._db.fetch_all(_GALLERY_ROWS, (recognizer, recognizer))
        by_person: dict[str, list[Reference]] = {}
        for row in rows:
            by_person.setdefault(str(row["person_id"]), []).append(_reference(row))
        return by_person

    async def people_with_faces(self, asset_id: str) -> list[str]:
        """Who this file's faces name. A question names nobody. See `NAMES_THE_FILE`."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT person_id FROM face_tracks WHERE asset_id = ? "
            "AND person_id IS NOT NULL AND attribution IN (?, ?)",
            (asset_id, *_NAMES_THE_FILE),
        )
        return [str(row["person_id"]) for row in rows]

    async def person_name(self, person_id: str) -> str | None:
        """A person's name, read from the shared table, which this feature never writes."""
        row = await self._db.fetch_one("SELECT name FROM people WHERE id = ?", (person_id,))
        return str(row["name"]) if row is not None else None

    async def create_person(self, name: str, made: Made) -> str:
        """Add somebody to the shared table, and hand back their id.

        The one write this feature makes to `people`. Elsewhere a name that matches nobody is
        reported back rather than invented, since a person invented from a misspelt folder must
        be found and merged later. The maker comes from the caller, so a name typed on a screen
        is not claimed as Sift's.
        """
        # New on People for every admin, the only ones who see a person with no grants yet.
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return await self.create_person_on(connection, name, made)

    async def create_person_on(self, connection: Connection, name: str, made: Made) -> str:
        """`create_person` inside the caller's write, for a person made together with what she
        holds. The caller tells every admin."""
        person_id = new_id()
        await connection.execute(
            "INSERT INTO people"
            " (id, name, name_sort, notes, created_at, created_by_kind, created_by_via,"
            " created_by_user_id)"
            " VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                person_id,
                name,
                sort_key(name),
                int(time.time()),
                made.kind,
                made.via,
                made.user_id,
            ),
        )
        return person_id

    async def aliases_of(self, person_id: str) -> list[str]:
        rows = await self._db.fetch_all(
            "SELECT alias FROM people_aliases WHERE person_id = ?"
            " ORDER BY COALESCE(alias_sort, alias), id",
            (person_id,),
        )
        return [str(row["alias"]) for row in rows]

    async def people_called(self, words: str) -> frozenset[str]:
        """Everybody whose name or one of whose aliases holds these words, anywhere and any case.

        The narrowing behind the search boxes on the two Faces tabs. Anywhere rather than a prefix,
        as every wall's box matches: somebody known by a surname is found by it. The wildcards are
        taken out first, so an underscore typed is an underscore looked for.
        """
        like = "%" + words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = await self._db.fetch_all(
            "SELECT p.id FROM people p WHERE p.name LIKE ? ESCAPE '\\'"
            " UNION SELECT al.person_id FROM people_aliases al WHERE al.alias LIKE ? ESCAPE '\\'",
            (like, like),
        )
        return frozenset(str(row["id"]) for row in rows)

    async def alias_owner(self, alias: str) -> str | None:
        """Who already answers to this name, ignoring case as the table's uniqueness does."""
        row = await self._db.fetch_one(
            "SELECT person_id FROM people_aliases WHERE alias = ? COLLATE NOCASE LIMIT 1",
            (alias,),
        )
        return str(row["person_id"]) if row is not None else None

    async def add_alias(self, person_id: str, alias: str) -> bool:
        """Give somebody another name they answer to. False when they already had it.

        `OR IGNORE` lets the table's uniqueness decide between two imports at the same time. A name
        that lands tells every admin in the same transaction: the person's page draws it.
        """
        async with self._db.write() as connection:
            return await self.add_alias_on(connection, person_id, alias)

    async def add_alias_on(self, connection: Connection, person_id: str, alias: str) -> bool:
        """`add_alias` inside the caller's write."""
        cursor = await connection.execute(
            "INSERT OR IGNORE INTO people_aliases (id, person_id, alias, alias_sort,"
            " added_at) VALUES (?, ?, ?, ?, ?)",
            (new_id(), person_id, alias, sort_key(alias), int(time.time())),
        )
        added = cursor.rowcount > 0
        if added:
            announce(EVERY_ADMIN, About.LIBRARY)
        return added

    async def existing_people(self, names: Iterable[str]) -> dict[str, str]:
        """Match names to People who already exist, by name and then by alias, ignoring case.

        A name wins over an alias; an alias of exactly one person is that person, and one two
        People share names neither. Nothing here creates a person: a name that matches nobody is
        reported back to whoever supplied it.
        """
        wanted = [name.strip() for name in names if name.strip()]
        if not wanted:
            return {}
        sql, params = in_clause(
            "SELECT id, name FROM people WHERE name COLLATE NOCASE IN (?*)", wanted
        )
        rows = await self._db.fetch_all(sql, tuple(params))
        found = {str(row["name"]).casefold(): str(row["id"]) for row in rows}
        rest = [name for name in wanted if name.casefold() not in found]
        if not rest:
            return found
        sql, params = in_clause(
            "SELECT alias, person_id FROM people_aliases WHERE alias COLLATE NOCASE IN (?*)", rest
        )
        owners: dict[str, set[str]] = {}
        for row in await self._db.fetch_all(sql, tuple(params)):
            owners.setdefault(str(row["alias"]).casefold(), set()).add(str(row["person_id"]))
        for alias, people in owners.items():
            if len(people) == 1:
                found[alias] = next(iter(people))
        return found
