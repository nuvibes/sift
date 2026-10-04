# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Site's own permanent number for a username, learned by a pass or typed by a person."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sift.kernel.access.catalog.made import Made, _clean_username
from sift.kernel.access.catalog.usernames import _SET_USERNAME_NUMBER, _seed_username_on
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, point_read
from sift.kernel.ledger import Actor as LedgerActor
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.ledger import record_event
from sift.kernel.text import clean_token_text
from sift.kernel.vocabulary import VIA_METADATA
from sift.kernel.vocabulary import Subject as LedgerSubject

#: The same question asked by the site's NAME, for a caller that must not cause a site to exist,
#: with the provenance beside the name because the caller has to say why it filed something.
_SELECT_USERNAME_NAME_BY_NUMBER = point_read(
    "catalog.username_for_number",
    "SELECT a.name AS name, a.number_via AS via,"
    " a.number_agreed AS agreed"
    " FROM usernames a JOIN sites p ON p.id = a.site_id "
    "WHERE p.name = ? AND a.number = ?",
)

#: The same fill, with how the number was come by (`NumberLearned`). Its own statement, because
#: NULLs written through it would erase a provenance a later pass had written.
_SET_USERNAME_NUMBER_LEARNED = (
    "UPDATE usernames SET number = ?, number_via = ?,"
    " number_agreed = ? WHERE id = ? AND number IS NULL"
)

#: One username's number and where it came from, asked only by the page that draws one username,
#: so the list statement does not carry three more columns.
_SELECT_USERNAME_NUMBER = point_read(
    "catalog.username_number",
    "SELECT number AS number, number_via AS via,"
    " number_agreed AS agreed FROM usernames WHERE id = ?",
)

#: A username on this site under this name, however it is spelled in the index.
_SELECT_USERNAME_BY_NAME_ON_SITE = (
    "SELECT a.id AS id, a.number AS number FROM usernames a"
    " JOIN sites p ON p.id = a.site_id WHERE p.name = ? AND a.name = ?"
)


async def set_username_numbers(db: Database, by_username: Mapping[str, str]) -> int:
    """Fill in the site's own number for each username id named here. Returns how many it filled.

    By id, never by name: one name on two sites is two usernames, and since only a blank is ever
    filled (in the SQL, so two passes at once are harmless), a wrong number would be permanent.
    """
    filled = 0
    async with db.write() as connection:
        for username_id, number in by_username.items():
            if not username_id or not number:
                continue
            cursor = await connection.execute(_SET_USERNAME_NUMBER, (number, username_id))
            filled += int(cursor.rowcount or 0)
    return filled


#: The word on a number somebody TYPED onto a username: a person, where `MADE_VIAS` names passes.
NUMBER_TYPED = "typed"

#: Every word `usernames.number_via` may hold. NULL, a number the filename hunt found, is not one:
#: "the row does not say" is a fact rather than a gap.
NUMBER_VIAS = (VIA_METADATA, NUMBER_TYPED)


@dataclass(frozen=True, slots=True)
class NumberLearned:
    """How a username came by the site's own number, and how many pictures agreed (None if typed)."""

    via: str
    agreed: int | None = None


@dataclass(frozen=True, slots=True)
class NumberedUsername:
    """The username a site knows by one number, with how this library came to know it."""

    name: str
    #: One of `NUMBER_VIAS`, or None where the number arrived by a path that records nothing.
    via: str | None = None
    #: How many pictures agreed, where pictures were read. None otherwise.
    agreed: int | None = None


async def username_for_number(db: Database, *, site: str, number: str) -> NumberedUsername | None:
    """What this library calls the username a site knows by this number, or None. Unscoped.

    The read half of the number rule, so a pass can decline: a filename that carries only a number
    has no name to make a row from, and a row named after a number could never be corrected.
    """
    row = await db.fetch_one(
        _SELECT_USERNAME_NAME_BY_NUMBER, (clean_token_text(site).strip(), number)
    )
    if row is None:
        return None
    return NumberedUsername(
        name=str(row["name"]),
        via=None if row["via"] is None else str(row["via"]),
        agreed=None if row["agreed"] is None else int(row["agreed"]),
    )


@dataclass(frozen=True, slots=True)
class UsernameNumber:
    """The site's own permanent number for one username, and how this library came by it."""

    number: str | None = None
    #: One of `NUMBER_VIAS`, or None where the number arrived by a path that records nothing.
    via: str | None = None
    #: How many pictures agreed, where pictures were read. None otherwise.
    agreed: int | None = None


async def username_number(db: Database, username_id: str) -> UsernameNumber:
    """One username's number, with its provenance. Empty where the row has none or is not there."""
    row = await db.fetch_one(_SELECT_USERNAME_NUMBER, (username_id,))
    if row is None:
        return UsernameNumber()
    return UsernameNumber(
        number=None if row["number"] is None else str(row["number"]),
        via=None if row["via"] is None else str(row["via"]),
        agreed=None if row["agreed"] is None else int(row["agreed"]),
    )


async def remember_username_number(
    db: Database,
    *,
    site: str,
    name: str,
    number: str,
    learned: NumberLearned,
    made: Made,
) -> tuple[str, bool]:
    """Write a site's own number onto that username, making the username if there is none.

    `(username_id, whether this call filled it)`; a number already there is never overwritten. The
    evidence is the pictures' own metadata, agreed on by more than one, and `made` names the pass.
    """
    site = clean_token_text(site).strip()
    async with db.write() as connection:
        known = await (
            await connection.execute(
                _SELECT_USERNAME_BY_NAME_ON_SITE, (site, _clean_username(name))
            )
        ).fetchone()
        if known is not None and known["number"] is not None:
            return str(known["id"]), False
        _, username_id = await _seed_username_on(connection, site=site, name=name, made=made)
        cursor = await connection.execute(
            _SET_USERNAME_NUMBER_LEARNED,
            (number, learned.via, learned.agreed, username_id),
        )
        return username_id, bool(cursor.rowcount)


#: What typing a number onto a username did. See `set_username_number`.
NumberOutcome = Literal["written", "same", "held", "clash", "missing"]


@dataclass(frozen=True, slots=True)
class NumberTyped:
    """What one typed number did, and who already holds it where it clashed.

    `written`, `same`, `held` (a different one is there and replacing was not asked), `clash`
    (another username on the Site has it, often the same person under an old name) or `missing`.
    """

    outcome: NumberOutcome
    #: For a clash: the other username, by id, and as a screen shows it (its display name where it
    #: has one, else its username, empty for the blank "poster unknown" username).
    other_id: str | None = None
    other_name: str | None = None


#: The number a username carries now, read inside the write that may change it.
_NUMBER_OF_USERNAME = "SELECT number FROM usernames WHERE id = ?"

#: Another username on the same Site with this number, asked inside the write so the uniqueness
#: index (`ux_usernames_number`) is never what refuses it, as an unhandled error.
_OTHER_USERNAME_WITH_NUMBER = """
SELECT o.id AS id, COALESCE(NULLIF(trim(o.display_name), ''), o.name) AS name
  FROM usernames u JOIN usernames o ON o.site_id = u.site_id AND o.number = ? AND o.id <> u.id
 WHERE u.id = ?
 LIMIT 1
"""

#: A typed number, onto a blank OR over the one there, with its provenance: typed, nothing counted.
#: Only ever run after `_NUMBER_OF_USERNAME` said which of the two it is and the caller allowed it.
_WRITE_TYPED_USERNAME_NUMBER = (
    "UPDATE usernames SET number = ?, number_via = ?, number_agreed = NULL WHERE id = ?"
)

#: What a line about a username's number names: the username, its Site and who it belongs to.
_USERNAME_FOR_A_LINE = (
    "SELECT u.name AS name, u.person_id AS person_id, s.name AS site"
    " FROM usernames u LEFT JOIN sites s ON s.id = u.site_id WHERE u.id = ?"
)


async def set_username_number(
    db: Database,
    *,
    username_id: str,
    number: str,
    replace: bool = False,
    actor: LedgerActor | None = None,
) -> NumberTyped:
    """Type the Site's own number onto one username by its id, and say what that did.

    Filling a blank is the default and replacing must be asked for; a number another username on
    the Site holds is refused and named. History keeps the old number where a person typed it.
    """
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        row = await (await connection.execute(_NUMBER_OF_USERNAME, (username_id,))).fetchone()
        if row is None:  # pragma: no cover (the route resolved the username a line ago)
            return NumberTyped("missing")
        held = row["number"]
        if held == number:
            return NumberTyped("same")
        if held is not None and not replace:
            return NumberTyped("held")
        other = await (
            await connection.execute(_OTHER_USERNAME_WITH_NUMBER, (number, username_id))
        ).fetchone()
        if other is not None:
            return NumberTyped("clash", other_id=str(other["id"]), other_name=str(other["name"]))
        await connection.execute(_WRITE_TYPED_USERNAME_NUMBER, (number, NUMBER_TYPED, username_id))
        if actor is not None:
            await _say_number_written(connection, actor, username_id, was=held, now=number)
        return NumberTyped("written")


async def _say_number_written(
    connection: Connection, actor: LedgerActor, username_id: str, *, was: object, now: str
) -> None:
    """The History line for a number typed onto a username: the Site's word for it, from and to."""
    about = await (await connection.execute(_USERNAME_FOR_A_LINE, (username_id,))).fetchone()
    if about is None:  # pragma: no cover (written a line ago, in this transaction)
        return
    person = about["person_id"]
    await record_event(
        connection,
        actor=actor,
        verb="edited",
        subject=LedgerSubject(kind="username", id=username_id, name=str(about["name"])),
        object=None if person is None else LedgerObject(kind="person", id=str(person)),
        payload=json.dumps(
            {
                "fields": ["number"],
                "number_site": about["site"],
                "number_before": was,
                "number_after": now,
            }
        ),
    )
