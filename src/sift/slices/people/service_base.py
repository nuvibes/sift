# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every part of the people service stands on: the shapes it hands back, reading a row
into one, and the doors every write goes through. Each part is a mixin naming the parts it relies
on as its bases; this is the one they all share."""

from __future__ import annotations

import dataclasses
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast
from urllib.parse import urlsplit

from sift.kernel.access import (
    MADE_UNSAID,
    AliasMatch,
    Made,
    Repository,
    UsernameNumber,
    Viewer,
    by_sift,
)
from sift.kernel.access.catalog import by_user as made_by_user
from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.cache_stamp import bump_cache_stamp
from sift.kernel.changes import About, announce, telling
from sift.kernel.content import MAX_RATING, MIN_RATING
from sift.kernel.content.entity_state import OpinionSubject, opinion_before
from sift.kernel.content.user_state import OpinionKind, record_opinion
from sift.kernel.cover_frame import CoverFrame, frame_of
from sift.kernel.covers import chosen_from_row, cover_change
from sift.kernel.db import Connection, Database, Row
from sift.kernel.ids import new_id
from sift.kernel.ledger import ACTOR_SIFT, ACTOR_USER, Actor, Object, record_event
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import MADE_VIAS, Subject, SubjectKind
from sift.kernel.when import today as machine_today
from sift.kernel.workbench import Recorder
from sift.slices.people.service_statements import (
    _COUNT_SITE_USERNAMES,
    _DELETE_ALIAS,
    _INSERT_ALIAS,
    _SITE_NAME,
)


def _and_the_actor(told: Audience, actor: Actor) -> Audience:
    """The audience of a membership write, plus whoever made it."""
    if actor.kind == ACTOR_USER and actor.id:
        return told | Audience.of_user(actor.id)
    return told


def _made_by(actor: Actor) -> Made:
    """Who made a Site that an act of `actor` invents: the user who acted, or the pass of Sift's
    that did. An act of anything else invents no Site on these paths, and says so as unsaid."""
    if actor.kind == ACTOR_USER:
        return made_by_user(actor.id)
    if actor.kind == ACTOR_SIFT and actor.id in MADE_VIAS:
        return by_sift(actor.id)
    return MADE_UNSAID


#: What a UUID looks like: eight hex digits, three groups of four, twelve. The shape every
#: stash-box gives its own rows, and the one thing an alias may never be (see `ensure_alias`).
_IS_A_RECORD_ID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


class SiteLoop(Exception):
    """The parent asked for is the Site itself, or a Site already part of it."""


class DuplicateAlias(Exception):
    """This person already carries that alias."""


#: The record columns, paired with the field key each one answers to.
PROMOTED: tuple[tuple[str, str], ...] = (
    ("disambiguation", "disambiguation"),
    ("gender", "gender"),
    ("birth_date", "birth_date"),
    ("country", "country"),
    ("ethnicity", "ethnicity"),
    ("eye_color", "eye_color"),
    ("hair_color", "hair_color"),
    ("height_cm", "height_cm"),
    ("measurements", "measurements"),
    ("breast_type", "breast_type"),
    ("career_start_year", "career_start_year"),
    ("career_end_year", "career_end_year"),
    ("tattoos", "tattoos"),
    ("piercings", "piercings"),
    # LAST, and it has to stay last: `_record_params` binds in this order and the statement names
    # its columns in the same one, so a pair moved here without the statement writes a height into
    # a flag. Added at the end for that reason rather than beside the field it reads best next to.
    ("pmv_creator", "pmv_creator"),
)


#: The two that hold a LIST, kept as JSON in one column each. Read back as a list, always: a reader
#: that got a string on a bad row would render the raw JSON onto somebody's record.
_AS_LIST = frozenset({"tattoos", "piercings"})


#: The two that hold a WHOLE NUMBER. A year and a height arrive off a form as text, and `"1991"` in
#: an INTEGER column is a string SQLite will happily store and every later comparison will get
#: wrong.
_AS_NUMBER = frozenset({"height_cm", "career_start_year", "career_end_year"})


#: The one that holds YES OR NO, in the one column on this table that refuses NULL.
_AS_FLAG = frozenset({"pmv_creator"})


#: Everything a form can send that means the switch is OFF. A checkbox that is not ticked reaches a
#: JSON body as `false`, and a form that sends its controls as text reaches it as one of the words.
_NOT_SET = frozenset({"", "0", "false", "no", "off", "none", "null"})


@dataclass(frozen=True, slots=True)
class Person:
    id: str
    name: str
    vault: bool
    notes: str | None
    cover_asset_id: str | None
    created_at: int
    #: The face out of that cover, when the cover is a face rather than the whole frame. Set only
    #: alongside `cover_asset_id`, so whether it may be shown is decided by the file's own rule.
    cover_track_id: str | None = None
    #: An uploaded cover: a picture put on this row rather than a file in the library.
    #: Never both this and `cover_asset_id` (one statement writes the pair), and this one
    #: carries no visibility rule, because there is no file behind it to allow or to refuse.
    cover_upload_id: str | None = None
    #: Which moment of `cover_asset_id`, when the cover is a chosen frame of a video. Carried so a
    #: write reply names the same cover address the wall does (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None = None
    #: The window of that picture it is drawn as. See `kernel/cover_frame.py frame_of`.
    cover_frame: CoverFrame | None = None
    #: Everything on the record, by field key: birthdate, nationality, the rest.
    keep_local: bool = False
    #: Marked "Don't swap": kept out of every swap with another Sift. The row's own mark.
    keep_from_swaps: bool = False
    record: Mapping[str, object] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Link:
    """Somewhere a person can be found."""

    id: str
    person_id: str
    url: str
    site_id: str | None
    site_name: str | None
    label: str | None


@dataclass(frozen=True, slots=True)
class Alias:
    id: str
    person_id: str
    alias: str


@dataclass(frozen=True, slots=True)
class Site:
    id: str
    name: str
    #: No attribution count here. "What a delete would strand" is `_COUNT_SITE_USERNAMES`,
    #: deliberately WITHOUT subtracting concealed people, because a concealed username is stranded
    #: just as thoroughly as a visible one; the card's number is `people_count` off the scoped
    #: wall. A number nobody draws, carrying a concealment rule written nowhere else, is where a
    #: fault survives unseen.

    #: Hidden by the user asking. A site they hid does not come back from the scoped list at all
    #: while their Hidden is shut, so anything reading this has already entered the PIN, which is
    #: what makes it safe to draw.
    vault: bool = False
    #: When the site appeared, in seconds: the same field `Person` carries and, from v41 of the
    #: catalog, read off the same kind of column.
    created_at: int | None = None
    #: Whether this row may never be sent outside the machine. See `SiteSuggestion` in the
    #: repository's views: a write reply is what a screen puts in place of the row it was holding,
    #: so a mark missing here is a mark a rename takes off the card.
    keep_local: bool = False
    #: Marked "Don't swap": kept out of every swap with another Sift. The row's own mark.
    keep_from_swaps: bool = False
    #: The site's own address, where one is recorded. Carried for ONE question: which shipped logo
    #: the pack answers this site with (`site_icons.icon_for(address, name)`). The listing and the
    #: cover route both ask by the address first and the name second, and a write reply that asked
    #: by the name alone could name a different logo than the cover route then served, a token
    #: that disagreed with its own picture after an edit.
    site_url: str | None = None


@dataclass(frozen=True, slots=True)
class EntityState:
    """One viewer's heart and stars on a person or a site."""

    favorite: bool
    rating: int | None


@dataclass(frozen=True, slots=True)
class Username:
    id: str
    name: str
    display_name: str | None
    url: str | None
    site_id: str | None
    person_id: str | None


@dataclass(frozen=True, slots=True)
class Filing:
    """One `asset_usernames` row, read from the FILE's side. See `_FILINGS_OF_ASSET`."""

    username_id: str
    username: str
    site_id: str | None
    site_name: str | None
    person_id: str | None
    #: How the filing was decided: None when somebody did it, a word when a pass did. The same
    #: column an attribution and a tag on a file already carry, and it means the same thing.
    source: str | None = None


@dataclass(frozen=True, slots=True)
class UsernameFacts:
    """What is drawn beside one username where it is shown under a person or a site. See
    `PeopleService.username_facts`."""

    number: UsernameNumber
    #: The site's own address and name: the pair the shipped logo is looked up by.
    site_url: str | None = None
    site_name: str | None = None


@dataclass(frozen=True, slots=True)
class Attribution:
    """Why one person is on one file, where a pass and not somebody put them there."""

    #: `folder`, `username` or `stash_box`. Never None: a pairing nobody marked is not in the map.
    source: str
    #: The username that resolved to this person on this file. None for every other source.
    username: str | None = None


def _state_from_row(row: Row) -> EntityState:
    return EntityState(
        favorite=bool(row["favorite"]),
        rating=None if row["rating"] is None else int(row["rating"]),
    )


def _check_rating(rating: int | None) -> None:
    """The same bounds an asset's rating is held to, from the same constants."""
    if rating is not None and not MIN_RATING <= rating <= MAX_RATING:
        raise ValueError(f"a rating is {MIN_RATING} to {MAX_RATING}, or None to clear it")


def _record_from_row(row: Row) -> dict[str, object]:
    """The record columns off a person row, by field key."""
    out: dict[str, object] = {}
    for key, column in PROMOTED:
        held = row[column]
        if key in _AS_FLAG:
            # Never skipped, unlike every line below it: this column cannot be null, so "not filled
            # in" is not one of its answers and a reader given nothing would have to invent one.
            out[key] = bool(held)
            continue
        if held is None:
            continue
        out[key] = json.loads(str(held)) if key in _AS_LIST else held
    age = _age_from(out.get("birth_date"))
    if age is not None:
        out["age"] = age
    return out


def _age_from(birth_date: object) -> int | None:
    """How old somebody is today, from the birthdate already on the row."""
    if not isinstance(birth_date, str):
        return None
    try:
        born = date.fromisoformat(birth_date.strip()[:10])
    except ValueError:
        return None
    today = machine_today()
    # The birthday-this-year test, rather than dividing days by 365. A person born on 29 February
    # has no birthday in most years, and `(month, day)` compares correctly for them without the
    # date ever being constructed.
    had_birthday = (today.month, today.day) >= (born.month, born.day)
    years = today.year - born.year - (0 if had_birthday else 1)
    # A date in the future, or one far enough back to be a typo rather than a person.
    return years if 0 <= years <= 120 else None


def _record_params(record: Mapping[str, object]) -> tuple[object, ...]:
    """The record as bound parameters, in the order the statement names its columns."""
    out: list[object] = []
    for key, _column in PROMOTED:
        held = record.get(key)
        if key in _AS_FLAG:
            # A flag is written every time, including when the form sent nothing: the column refuses
            # NULL, and an absent switch is one that is off. `"false"` and `"0"` are spelled out
            # because a form that sends its value as text would otherwise store the string, which is
            # truthy: the same trap `_AS_NUMBER` exists for, one column along.
            out.append(0 if held is None or str(held).strip().lower() in _NOT_SET else 1)
            continue
        if key in _AS_LIST:
            entries = [str(one).strip() for one in held] if isinstance(held, list) else []
            entries = [one for one in entries if one]
            out.append(json.dumps(entries) if entries else None)
            continue
        if held is None or (isinstance(held, str) and not held.strip()):
            out.append(None)
            continue
        if key in _AS_NUMBER:
            try:
                out.append(int(str(held).strip()))
            except ValueError:
                # Not a number is not a value. Refusing the whole save over one box would throw away
                # fourteen good fields to punish a typo in the fifteenth.
                out.append(None)
            continue
        out.append(str(held).strip())
    return tuple(out)


def _frame_of_row(row: Row) -> CoverFrame | None:
    """The window of a person row's cover, bound to the pointers on the same row. See `frame_of`."""
    return frame_of(
        row["cover_frame"],
        asset_id=row["cover_asset_id"],
        at_ms=row["cover_at_ms"],
        upload_id=row["cover_upload_id"],
    )


def person_from_row(row: Row) -> Person:
    return Person(
        id=row["id"],
        name=row["name"],
        vault=bool(row["vault"]),
        notes=row["notes"],
        cover_asset_id=row["cover_asset_id"],
        cover_track_id=row["cover_track_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame_of_row(row),
        created_at=int(row["created_at"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        record=_record_from_row(row),
    )


def _unscoped_person(row: Row) -> Person:
    """A person row straight from `people`, with no user attached."""
    return Person(
        id=row["id"],
        name=row["name"],
        vault=False,
        notes=row["notes"],
        cover_asset_id=row["cover_asset_id"],
        cover_track_id=row["cover_track_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame_of_row(row),
        created_at=int(row["created_at"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        record=_record_from_row(row),
    )


def _held_from_row(row: Row) -> dict[str, object]:
    """A person's record fields from their row, with the name and the details folded in."""
    held = _record_from_row(row)
    held["name"] = str(row["name"])
    if row["notes"] is not None:
        held["details"] = str(row["notes"])
    return held


def _link_from_row(row: Row) -> Link:
    return Link(
        id=row["id"],
        person_id=row["person_id"],
        url=row["url"],
        site_id=row["site_id"],
        site_name=row["site_name"],
        label=row["label"],
    )


def site_links_in_order(urls: Sequence[str], name: str | None) -> list[str]:
    """A Site's addresses as they are kept: trimmed, each once, the site's OWN address first."""
    cleaned = [one.strip() for one in dict.fromkeys(urls) if one.strip()]
    return _own_address_first(cleaned, name)


def _own_address_first(urls: list[str], name: str | None) -> list[str]:
    """The addresses whose host carries a word of the site's name, ahead of the rest, order kept."""
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if len(w) >= 3]
    if not words:
        return urls

    def own(url: str) -> bool:
        host = urlsplit(url if "://" in url else f"https://{url}").hostname or ""
        return any(w in host for w in words)

    return [u for u in urls if own(u)] + [u for u in urls if not own(u)]


@dataclass(frozen=True, slots=True)
class JoinReceipt:
    """A username joined to a person by somebody answering the usernames queue, written as its
    receipt: the queue, who, and the words (`people.queue` owns them), so the join is counted as
    their decision and can be taken back (`UsernamesQueue.reverse`)."""

    recorder: Recorder
    queue: str
    user_id: str
    title: Callable[[str, str], str]
    detail: str


class PeopleBase:
    """The service's state, and the doors a write of a person, a Site or a username goes through."""

    def __init__(
        self,
        database: Database,
        access: Repository,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._access = access
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    # --- what an import writes -----------------------------------------------------------------

    @property
    def database(self) -> Database:
        """The database itself, for the kernel's shared upserts."""
        return self._db

    async def is_visible_to(self, viewer: Viewer, person_id: str) -> bool:
        """Whether this viewer may be shown this person at all."""
        return await self._access.visible_person(viewer, person_id) is not None

    async def resolve(self, term: str, viewer: Viewer) -> AliasMatch:
        """Who this term names: by name, by alias, or by a linked username."""
        return await self._access.resolve_alias_targets(viewer, term)

    async def _person_name(self, person_id: str) -> str:
        """One person's own name. Empty when there is no such person."""
        row = await self._db.fetch_one("SELECT name FROM people WHERE id = ?", (person_id,))
        return str(row["name"]) if row is not None else ""

    async def _site_names(self, site_ids: Sequence[str]) -> dict[str, str]:
        """What these sites are called, in one read, for the snapshot an event carries."""
        found: dict[str, str] = {}
        for site_id in dict.fromkeys(site_ids):
            row = await self._db.fetch_one(_SITE_NAME, (site_id,))
            if row is not None:
                found[site_id] = str(row["name"])
        return found

    async def _count_usernames(self, site_id: str) -> int:
        row = await self._db.fetch_one(_COUNT_SITE_USERNAMES, (site_id,))
        return 0 if row is None else int(row["n"])

    async def _ensure_alias_on(
        self, connection: Connection, person_id: str, alias: str
    ) -> Alias | None:
        """`ensure_alias`, on the caller's connection: the alias it ADDED, or None."""
        if _IS_A_RECORD_ID.match(alias.strip()):
            return None
        rows = list(
            await connection.execute_fetchall(
                _INSERT_ALIAS, (new_id(), person_id, alias, sort_key(alias), self._now())
            )
        )
        if not rows:
            return None
        row = rows[0]
        return Alias(id=row["id"], person_id=row["person_id"], alias=row["alias"])

    async def _remove_alias_on(
        self, connection: Connection, person_id: str, alias_id: str, name: str, *, actor: Actor
    ) -> bool:
        """`remove_alias`, on the caller's connection, with its event in the caller's transaction."""
        rows = list(await connection.execute_fetchall(_DELETE_ALIAS, (alias_id, person_id)))
        if rows:
            await record_event(
                connection,
                actor=actor,
                verb="removed",
                subject=Subject(kind="person", id=person_id, name=name or None),
                payload=json.dumps({"alias": str(rows[0]["alias"])}),
            )
        return bool(rows)

    async def _cover_set(
        self,
        statement: str,
        reading: str,
        kind: SubjectKind,
        entity_id: str,
        chosen: tuple[str | None, int | None, str | None, CoverFrame | None],
        actor: Actor,
        box: Object | None = None,
    ) -> bool:
        """One cover write and its event, in one transaction. The body both of the two above share."""
        asset_id, at_ms, upload_id, frame = chosen
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            was = list(await connection.execute_fetchall(reading, (entity_id,)))
            if not was:
                return False
            change = cover_change(
                chosen_from_row(was[0]),
                asset_id=asset_id,
                at_ms=at_ms,
                upload_id=upload_id,
                frame=frame,
                box=box,
            )
            # The row was read in this same transaction a line ago, so the write finds it: there is
            # no answer here but the row, and the event is written from what it says.
            (row,) = await connection.execute_fetchall(
                statement,
                (asset_id, at_ms, upload_id, change.frame, change.cleared_at, entity_id),
            )
            await record_event(
                connection,
                actor=actor,
                verb="edited",
                subject=Subject(kind=kind, id=entity_id, name=str(row["name"])),
                object=change.object,
                payload=change.payload,
            )
        return True

    async def _write_shared(self, sql: str, params: tuple[object, ...]) -> list[Row]:
        """One write to something the whole library shares, and the screens drawing it told."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return list(await connection.execute_fetchall(sql, params))

    async def _write_mine(
        self,
        user_id: str,
        sql: str,
        params: tuple[object, ...],
        *,
        about: OpinionSubject,
        records: OpinionKind,
    ) -> list[Row]:
        """One write to something that is a single user's own, and that user's screens told."""
        async with telling(self._db, Audience.of_user(user_id), About.MINE) as connection:
            before = await opinion_before(
                connection,
                subject_kind=about,
                subject_id=str(params[0]),
                user_id=user_id,
                kind=records,
            )
            rows = list(await connection.execute_fetchall(sql, params))
            await record_opinion(
                connection,
                user_id=user_id,
                subject_kind=about,
                subject_id=str(params[0]),
                kind=records,
                before=before,
                after=cast("int | None", params[2]),
                at=self._now(),
            )
            return rows

    async def _write_for(
        self,
        user_id: str,
        sql: str,
        params: tuple[object, ...],
        *,
        about: OpinionSubject,
        records: OpinionKind,
    ) -> list[Row]:
        """A write that changes what one user may see, and the note that says so."""
        async with self._db.write() as connection:
            before = await opinion_before(
                connection,
                subject_kind=about,
                subject_id=str(params[0]),
                user_id=user_id,
                kind=records,
            )
            rows = list(await connection.execute_fetchall(sql, params))
            await record_opinion(
                connection,
                user_id=user_id,
                subject_kind=about,
                subject_id=str(params[0]),
                kind=records,
                before=before,
                after=cast("int | None", params[2]),
                at=self._now(),
            )
            announce(await bump_cache_stamp(connection, user_id), About.LIBRARY)
            return rows
