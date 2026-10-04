# SPDX-License-Identifier: AGPL-3.0-or-later
"""Faces' people and filings: who is on a file, and which Sites and usernames it is filed under."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from sift.kernel.access import (
    ObjectType,
    Viewer,
    bump_stamps_for_object,
    clear_refusal_on,
    file_assets_under_site_on,
    refuse_person_on,
)
from sift.kernel.audience import EVERY_ADMIN, NOBODY
from sift.kernel.changes import About, announce, telling
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.vocabulary import Subject
from sift.slices.people.service_base import (
    Attribution,
    Filing,
    PeopleBase,
    Person,
    _and_the_actor,
    _made_by,
    person_from_row,
)
from sift.slices.people.service_statements import (
    _ASSETS_OF_PERSON,
    _ASSETS_OF_SITE,
    _ASSETS_OF_USERNAME,
    _ASSIGN_PERSON,
    _AUTOMATIC_ON_ASSET,
    _FILINGS_OF_ASSET,
    _PEOPLE_OF_ASSET,
    _UNASSIGN_PERSON,
    _UNFILE_ASSET_FROM_SITE,
    _UNFILE_ASSET_FROM_USERNAME,
    _USERNAME_NAME,
)


class FilesMixin(PeopleBase):
    """Puts people on files and takes them off, and files a file under a Site or a username."""

    async def people_of(self, viewer: Viewer, asset_id: str) -> list[Person]:
        """Who is in one asset."""
        rows = await self._db.fetch_all(_PEOPLE_OF_ASSET, (viewer.id, asset_id))
        return [person_from_row(row) for row in rows]

    async def automatic_on(self, asset_id: str) -> dict[str, Attribution]:
        """Which of the people on this file a pass put there, and which pass, id to attribution."""
        rows = await self._db.fetch_all(_AUTOMATIC_ON_ASSET, (asset_id,))
        return {
            str(row["person_id"]): Attribution(
                source=str(row["source"]),
                username=None if row["username"] is None else str(row["username"]),
            )
            for row in rows
        }

    async def assign(
        self,
        asset_ids: Sequence[str],
        person_ids: Sequence[str],
        *,
        add: bool,
        actor: Actor,
        names: Mapping[str, str] | None = None,
    ) -> int:
        """Attach or detach people, and touch no file on disk."""
        statement = _ASSIGN_PERSON if add else _UNASSIGN_PERSON
        written = 0
        moved: set[str] = set()
        #: The pairs that really changed, which is what the record is written from. A drag that
        #: attaches somebody who is already on the file writes no row and is not an act.
        pairs: set[tuple[str, str]] = set()
        now = int(time.time())
        async with self._db.write() as connection:
            for asset_id in asset_ids:
                for person_id in person_ids:
                    cursor = await connection.execute(statement, (asset_id, person_id))
                    # Remembered either way, and in the same transaction as the change itself.
                    if add:
                        await clear_refusal_on(connection, asset_id=asset_id, person_id=person_id)
                    else:
                        await refuse_person_on(
                            connection, asset_id=asset_id, person_id=person_id, now=now
                        )
                    if cursor.rowcount > 0:
                        written += cursor.rowcount
                        moved.add(person_id)
                        pairs.add((asset_id, person_id))
            # A person is what a share or a hide is attached to, so changing who is in a file
            # changes what somebody may see without anything of theirs being written. In the same
            # transaction, and only for the people who actually moved.
            told = NOBODY
            for person_id in sorted(moved):
                told |= await bump_stamps_for_object(connection, ObjectType.PERSON, person_id)
            # ONE event per file and person that actually MOVED, in the transaction that moved it.
            for asset_id, person_id in sorted(pairs):
                await record_event(
                    connection,
                    actor=actor,
                    verb="linked" if add else "unlinked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="person", id=person_id, name=(names or {}).get(person_id)),
                )
            announce(_and_the_actor(told, actor), About.LIBRARY)
        return written

    async def file_under_sites(
        self, asset_ids: Sequence[str], site_names: Sequence[str], *, actor: Actor
    ) -> int:
        """Say that these files came from these sites. Touches no file on disk."""
        if not asset_ids or not site_names:
            return 0
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for name in site_names:
                site_id = await file_assets_under_site_on(
                    connection,
                    asset_ids=list(asset_ids),
                    site=name,
                    # No `source`: whoever filed these decided it, and a Site the filing invents
                    # is theirs. The two are asked separately; see `file_assets_under_site_on`
                    # for why that is not a duplication.
                    made=_made_by(actor),
                )
                # One event per file, in the same transaction. The filing is a join row and a join
                # row is erased on removal, so without this "these came from there" is a fact with
                # no history at all, and neither would the unfiling below have one.
                for asset_id in asset_ids:
                    await record_event(
                        connection,
                        actor=actor,
                        verb="filed",
                        subject=Subject(kind="asset", id=asset_id),
                        object=Object(kind="site", id=site_id or name, name=name),
                    )
        return len(asset_ids)

    async def unfile_from_sites(
        self, asset_ids: Sequence[str], site_ids: Sequence[str], *, actor: Actor
    ) -> int:
        """Take these files off these sites. Touches no file on disk."""
        if not asset_ids or not site_ids:
            return 0
        named = await self._site_names(site_ids)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for asset_id in asset_ids:
                for site_id in site_ids:
                    cursor = await connection.execute(_UNFILE_ASSET_FROM_SITE, (asset_id, site_id))
                    if not cursor.rowcount:
                        continue
                    # Only where a row actually went. Unfiling forty files from a site two of them
                    # were never on is two files' worth of nothing, and a record that said
                    # otherwise would be forty lines describing an act that did not happen.
                    await record_event(
                        connection,
                        actor=actor,
                        verb="unlinked",
                        subject=Subject(kind="asset", id=asset_id),
                        object=Object(kind="site", id=site_id, name=named.get(site_id)),
                    )
        return len(asset_ids)

    async def filings_of(self, asset_id: str) -> list[Filing]:
        """Every site one file is filed under, and the username each filing names."""
        rows = await self._db.fetch_all(_FILINGS_OF_ASSET, (asset_id,))
        return [
            Filing(
                username_id=str(row["username_id"]),
                username=str(row["username"]),
                site_id=None if row["site_id"] is None else str(row["site_id"]),
                site_name=None if row["site_name"] is None else str(row["site_name"]),
                person_id=None if row["person_id"] is None else str(row["person_id"]),
                source=None if row["source"] is None else str(row["source"]),
            )
            for row in rows
        ]

    async def unfile_from_username(self, asset_id: str, username_id: str, *, actor: Actor) -> bool:
        """Take one file off one username. False where there was nothing to take off."""
        named = await self._db.fetch_one(_USERNAME_NAME, (username_id,))
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            cursor = await connection.execute(_UNFILE_ASSET_FROM_USERNAME, (asset_id, username_id))
            if not cursor.rowcount:
                return False
            # Only where a row actually went, which is the same rule `unfile_from_sites` states:
            # this route is deliberately quiet about a filing that was already gone, and a record
            # saying otherwise would describe an act that did not happen.
            await record_event(
                connection,
                actor=actor,
                verb="unlinked",
                subject=Subject(kind="asset", id=asset_id),
                object=Object(
                    kind="username",
                    id=username_id,
                    name=None if named is None else str(named["name"]),
                ),
            )
            return True

    # --- which files a name change reaches -------------------------------------------------

    async def assets_of_person(self, person_id: str) -> list[str]:
        """Every file this person is on. Their name and their aliases are indexed on each."""
        rows = await self._db.fetch_all(_ASSETS_OF_PERSON, (person_id,))
        return [str(row["asset_id"]) for row in rows]

    async def assets_of_username(self, username_id: str) -> list[str]:
        """Every file filed under one username."""
        rows = await self._db.fetch_all(_ASSETS_OF_USERNAME, (username_id,))
        return [str(row["asset_id"]) for row in rows]

    async def assets_of_site(self, site_id: str) -> list[str]:
        """Every file filed under any username on one site."""
        rows = await self._db.fetch_all(_ASSETS_OF_SITE, (site_id,))
        return [str(row["asset_id"]) for row in rows]
