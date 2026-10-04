# SPDX-License-Identifier: AGPL-3.0-or-later
"""Usernames: what a Site calls somebody, joined to a person or taken apart from one."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access import (
    UsernameNumber,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, in_clause
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.partial_write import UNCHANGED, Unchanged
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import Subject
from sift.slices.people.service_base import JoinReceipt, PeopleBase, UsernameFacts
from sift.slices.people.service_statements import (
    _FILE_UNDER_PERSON,
    _INSERT_PERSON,
    _SET_USERNAME_PERSON,
    _UNFILE_FROM_PERSON,
    _UPDATE_USERNAME_DISPLAY_NAME,
    _UPDATE_USERNAME_URL,
    _USERNAME_FACTS,
    _USERNAME_NAME,
)


class UsernamesMixin(PeopleBase):
    """Joins a username to a person and takes it apart again."""

    # --- sites ----------------------------------------------------------------------

    # --- usernames ---------------------------------------------------------------------------

    async def set_username_details(
        self,
        username_id: str,
        *,
        display_name: str | Unchanged | None = UNCHANGED,
        url: str | Unchanged | None = UNCHANGED,
    ) -> None:
        """Write what a username is called and where its page is. Blank clears either."""
        if not isinstance(display_name, Unchanged):
            cleaned = (display_name or "").strip()
            await self._write_shared(_UPDATE_USERNAME_DISPLAY_NAME, (cleaned or None, username_id))
        if not isinstance(url, Unchanged):
            cleaned = (url or "").strip()
            await self._write_shared(_UPDATE_USERNAME_URL, (cleaned or None, username_id))

    async def attach_username(
        self,
        username_id: str,
        *,
        person_id: str | None = None,
        new_person_name: str | None = None,
        as_alias: bool = True,
        by_user: str | None = None,
        actor: Actor,
        receipt: JoinReceipt | None = None,
    ) -> str | None:
        """Say who a username belongs to. Returns the person it landed on, or None if it did not."""
        if (person_id is None) == (new_person_name is None):
            raise ValueError("say either who this is, or that it is somebody new \u2014 not both")

        row = await self._db.fetch_one(_USERNAME_NAME, (username_id,))
        if row is None:
            return None
        username = str(row["name"])

        landed = person_id
        if landed is None:
            landed = await self._new_person_named(new_person_name, by_user)

        # The pointer, the files and the RECORD of it, in one transaction: the shape
        # `detach_username` below has. Without an event nothing could say this username was ever
        # joined to this person: the pointer carries no moment and is overwritten by the next join,
        # and a username has no page of its own. The person is the event's OBJECT, and a person's
        # thread reads the ledger from both sides (`history_events.events_of_entity`), so the line
        # lands on their History (and on the whole-install feed) with the username named as what
        # was joined.
        person_name = await self._person_name(landed)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            written = list(
                await connection.execute_fetchall(_SET_USERNAME_PERSON, (landed, username_id))
            )
            if not written:  # pragma: no cover (the username was read a few lines above)
                return None
            # And the files. The pointer alone is invisible everywhere except where a username is
            # listed (see the statement's own note).
            await connection.execute_fetchall(
                _FILE_UNDER_PERSON, (landed, self._now(), username_id)
            )
            # And the username written on as an also-known-as name, in the same transaction, so
            # the receipt below can say whether THIS join added it. Skipped when the username IS
            # the name, which is the ordinary case for a username somebody has just been created
            # from: an alias identical to the name adds nothing to find them by and puts a row in a
            # list that is meant to hold the OTHER spellings.
            added = None
            if as_alias and username.casefold() != person_name.casefold():
                added = await self._ensure_alias_on(connection, landed, username)
            await self._record_join_on(
                connection,
                actor=actor,
                receipt=receipt,
                username_id=username_id,
                username=username,
                landed=landed,
                person_name=person_name,
                alias_id=added.id if added is not None else None,
            )
        return landed

    async def _new_person_named(self, new_person_name: str | None, by_user: str | None) -> str:
        """A person made for a username somebody said is somebody new. Returns their id."""
        name = (new_person_name or "").strip()
        if not name:
            raise ValueError("a new person needs a name")
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            made = list(
                await connection.execute_fetchall(
                    _INSERT_PERSON,
                    (new_id(), name, sort_key(name), None, self._now(), by_user),
                )
            )
        return str(made[0]["id"])

    async def _record_join_on(
        self,
        connection: Connection,
        *,
        actor: Actor,
        receipt: JoinReceipt | None,
        username_id: str,
        username: str,
        landed: str,
        person_name: str,
        alias_id: str | None,
    ) -> None:
        """The record of one join, as a ledger line or as the Usernames queue's receipt."""
        if receipt is None:
            await record_event(
                connection,
                actor=actor,
                verb="linked",
                subject=Subject(kind="username", id=username_id, name=username),
                object=Object(kind="person", id=landed, name=person_name or None),
            )
        else:
            # A PERSON'S DECISION ON THE USERNAMES QUEUE: the same event, written as that
            # queue's receipt (`JoinReceipt`), so it is one line, counted as their decision,
            # with Undo. Never both: two lines for one act.
            await receipt.recorder.record_on(
                connection,
                queue=receipt.queue,
                user_id=receipt.user_id,
                title=receipt.title(username, person_name),
                detail=receipt.detail,
                # Everything the join wrote that is not already named by the username: the
                # person, and the alias when this join is what added it. The undo reads it
                # back and removes only what is named here (`UsernameQueue.reverse`).
                payload=json.dumps(
                    {
                        "username_id": username_id,
                        "person_id": landed,
                        "alias_id": alias_id,
                    }
                ),
                subjects=[Subject(kind="username", id=username_id, name=username)],
                verb="linked",
                object=Object(kind="person", id=landed, name=person_name or None),
            )

    async def detach_username(
        self, username_id: str, *, actor: Actor, alias: tuple[str, str] | None = None
    ) -> bool:
        """Take a username off whoever it was joined to, and unfile what the join filed."""
        row = await self._db.fetch_one(_USERNAME_NAME, (username_id,))
        if row is None:
            return False
        held = row["person_id"]
        name = await self._person_name(str(held)) if held else ""
        alias_holder = await self._person_name(alias[0]) if alias is not None else ""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(_SET_USERNAME_PERSON, (None, username_id))
            )
            if not rows:  # pragma: no cover (the username was read a few lines above)
                return False
            if held:
                await connection.execute_fetchall(_UNFILE_FROM_PERSON, (held, username_id))
            if alias is not None:
                await self._remove_alias_on(connection, *alias, alias_holder, actor=actor)
            # One transaction for both writes AND the event, which `_write_shared` could not give:
            # it opens one of its own per statement, so the pointer and the attributions could land
            # without the record of either. The pointer is deleted rather than dated, so nothing
            # else can ever say this username was joined to that person.
            await record_event(
                connection,
                actor=actor,
                verb="unlinked",
                subject=Subject(kind="username", id=username_id, name=str(row["name"])),
                object=(
                    None if not held else Object(kind="person", id=str(held), name=name or None)
                ),
            )
        return True

    async def any_username(self) -> bool:
        """Whether this install records a username at all. Unscoped: a fact about the install."""
        return await self._db.fetch_one("SELECT 1 FROM usernames LIMIT 1") is not None

    async def username_facts(self, username_ids: Sequence[str]) -> dict[str, UsernameFacts]:
        """What a person's Sites tab and a site's People tab draw beside each username, by its id."""
        if not username_ids:
            return {}
        statement, bound = in_clause(_USERNAME_FACTS, sorted(set(username_ids)))
        return {
            str(row["id"]): UsernameFacts(
                number=UsernameNumber(
                    number=None if row["number"] is None else str(row["number"]),
                    via=None if row["via"] is None else str(row["via"]),
                    agreed=None if row["agreed"] is None else int(row["agreed"]),
                ),
                site_url=row["site_url"],
                site_name=row["site_name"],
            )
            for row in await self._db.fetch_all(statement, bound)
        }
