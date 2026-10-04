# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merging what an enrichment found into a person's or a Site's record."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Row
from sift.kernel.ledger import Actor
from sift.kernel.sorting import sort_key
from sift.slices.people.service_base import (
    PeopleBase,
    _held_from_row,
    _record_from_row,
    _record_params,
)
from sift.slices.people.service_statements import (
    _ALIASES_OF_PEOPLE,
    _LINKS_OF_PEOPLE,
    _PEOPLE_AMONG,
    _SET_SITE_PARENT,
    _TAGS_OF_PEOPLE,
    _UPDATE_PERSON,
    _UPDATE_PERSON_RECORD,
    _UPDATE_SITE,
    _UPDATE_SITE_DETAILS,
    _USERNAMES_OF_PEOPLE,
)


class MergesMixin(PeopleBase):
    """Reads the records an enrichment works from, and merges what it found back."""

    async def enrichment_view_of_people(
        self, person_ids: Sequence[str]
    ) -> dict[str, dict[str, object]]:
        """What `person_record` and the four list reads answer, for many people in five reads."""
        if not person_ids:
            return {}
        bound = (json.dumps(list(dict.fromkeys(person_ids))),)
        held: dict[str, dict[str, object]] = {
            str(row["id"]): _held_from_row(row)
            for row in await self._db.fetch_all(_PEOPLE_AMONG, bound)
        }
        lists: dict[str, dict[str, list[object]]] = {
            one: {"aliases": [], "links": [], "tags": [], "accounts": []} for one in held
        }

        def add(row: Row, field: str, value: object) -> None:
            # A person made between the first read and this one has no record to add to.
            if (theirs := lists.get(str(row["person_id"]))) is not None:  # pragma: no branch
                theirs[field].append(value)

        for row in await self._db.fetch_all(_ALIASES_OF_PEOPLE, bound):
            add(row, "aliases", row["alias"])
        for row in await self._db.fetch_all(_LINKS_OF_PEOPLE, bound):
            add(row, "links", row["url"])
        for row in await self._db.fetch_all(_TAGS_OF_PEOPLE, bound):
            add(row, "tags", str(row["name"]))
        for row in await self._db.fetch_all(_USERNAMES_OF_PEOPLE, bound):
            add(
                row,
                "accounts",
                {
                    "site": str(row["site_name"] or ""),
                    "handle": str(row["name"]),
                    "url": str(row["url"] or ""),
                },
            )
        for one, fields in held.items():
            fields.update(lists[one])
        return held

    async def merge_person_record(
        self,
        person_id: str,
        *,
        name: str | None = None,
        details: str | None = None,
        columns: Mapping[str, object] | None = None,
        actor: Actor,
    ) -> bool:
        """Write some of a person's record, leaving every field not named exactly as it was.

        **Read and write in ONE transaction**
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall("SELECT * FROM people WHERE id = ?", (person_id,))
            )
            if not rows:
                return False
            row = rows[0]
            merged = _record_from_row(row)
            merged.update(columns or {})
            # `execute_fetchall`, and it is not a style choice. Both statements carry RETURNING, and
            # a RETURNING statement whose rows are never read is still IN PROGRESS, so the commit
            # at the end of this block fails outright with "SQL statements in progress" and the
            # whole write is lost. Reading the rows is what finishes the statement.
            await connection.execute_fetchall(
                _UPDATE_PERSON,
                (
                    name or str(row["name"]),
                    sort_key(name or str(row["name"])),
                    details if details is not None else row["notes"],
                    person_id,
                ),
            )
            await connection.execute_fetchall(
                _UPDATE_PERSON_RECORD, (*_record_params(merged), person_id)
            )
            # NO EVENT HERE, and that is the ledger's rule rather than an omission. This writes some
            # of the record and is handed neither the box nor the rest of what the ask landed
            # (aliases and links go through other statements), so the `enriched` event is written
            # beside the run, by `StashBoxService.record_enrichment`, naming the box and every field
            # that landed; written here, nameless, a thread could only fold it under the box's
            # latest run.
        return True

    async def merge_site_record(
        self,
        site_id: str,
        *,
        name: str | None = None,
        details: str | None = None,
        parent_id: str | None = None,
        actor: Actor,
    ) -> bool:
        """Write some of a site's record, leaving what is not named alone."""
        row = await self._db.fetch_one("SELECT * FROM sites WHERE id = ?", (site_id,))
        if row is None:
            return False
        if parent_id == site_id:
            parent_id = None
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            # `execute_fetchall` for the reason written over `merge_person_record`: both of these
            # carry RETURNING, and a RETURNING statement nobody reads is still in progress when the
            # commit comes.
            kept = name or str(row["name"])
            await connection.execute_fetchall(_UPDATE_SITE, (kept, sort_key(kept), site_id))
            await connection.execute_fetchall(
                _UPDATE_SITE_DETAILS,
                (details if details is not None else row["notes"], site_id),
            )
            if parent_id is not None:
                await connection.execute(_SET_SITE_PARENT, (parent_id, site_id))
            # No event: see `merge_person_record`. The run's writer says it, with the box.
        return True
