# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one site does differently: which tool fetches it, what its files are called, where they land.

The same shape as the routing store beside it: one row per site that has been given something of
its own, plus a reserved scope for the answer everything else follows. It is one question asked at
two levels, and answering it in two places is how a screen ends up disagreeing with what actually
happens.

**A destination is a folder that already exists.** Sift does not build a tree of its own from a
template. Creating folders inside somebody's library is the library's business, and the rule this
sits under is written into the import step itself: a person organises by dropping and dragging, and
nothing files itself by site or by creator behind their back. Choosing a folder for a site is a
different thing: it is the same choice a drop target makes, made once instead of every time.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part
from sift.slices.download.naming import DEFAULT_TEMPLATE
from sift.slices.download.sources.sites.catalog import by_key

#: The scope every site follows unless it has a row of its own. A word no site key can be: site keys
#: are plain lowercase names, and a test holds them to that.
DEFAULT_SCOPE = "*default*"

_READ_ALL = "SELECT scope, naming, dest_folder_id, downloader FROM site_options"
_READ_ONE = "SELECT naming, dest_folder_id, downloader FROM site_options WHERE scope = ?"
_WRITE = (
    "INSERT INTO site_options (scope, naming, dest_folder_id, downloader, updated_at)"
    " VALUES (?, ?, ?, ?, ?)"
    " ON CONFLICT(scope) DO UPDATE SET naming = excluded.naming,"
    " dest_folder_id = excluded.dest_folder_id, downloader = excluded.downloader,"
    " updated_at = excluded.updated_at"
)
_CLEAR = "DELETE FROM site_options WHERE scope = ?"


@dataclass(frozen=True, slots=True)
class SiteOptions:
    """What a site was given, or what everything follows. Absent fields mean "no opinion here"."""

    #: The name template. None means follow the default; empty string means the default explicitly
    #: chosen as "leave the name the fetcher gave it", which is a real answer and not the same one.
    naming: str | None = None
    #: A folder that already exists, or None to land wherever a download without a target lands.
    dest_folder_id: str | None = None
    #: Which tool fetches from this site, overriding what the catalog names. None means Sift's own
    #: answer: the tool the catalog names, and for the two sites that have one, the middleman
    #: service. A value here is a deliberate "use this instead", so it also turns off the per-site
    #: reader: choosing a tool is what somebody does when Sift's own way of reading a site is what
    #: is failing them.
    downloader: str | None = None


class SiteOptionStore:
    """Reading and writing what each site does differently. One per database, held on the app."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def all(self) -> dict[str, SiteOptions]:
        """Everything set, by scope, for the settings screen. One read rather than one per site."""
        rows = await self._db.fetch_all(_READ_ALL)
        return {
            str(row["scope"]): SiteOptions(
                naming=row["naming"],
                dest_folder_id=row["dest_folder_id"],
                downloader=row["downloader"],
            )
            for row in rows
        }

    async def resolve(self, site_key: str | None) -> SiteOptions:
        """What applies to one site: its own answer where it has one, otherwise the default.

        Read per download rather than held, for the reason every other live read in this slice is:
        a change made while a queue is draining should reach the next download rather than the next
        restart.

        The three fields fall back INDEPENDENTLY. A site given a folder of its own and no naming
        rule still follows the default naming. Treating one row as an all-or-nothing override
        would make setting a folder quietly undo a template somebody set globally, and the same is
        true of the tool: picking one for a site must not also detach it from the shared naming.

        The NAME has one step more than the other two, because Sift ships an answer for it per
        Site (`SiteRecord.default_naming`) and ships none for a folder or a tool. In order:

        1. the rule somebody typed for this Site, EMPTY included, which is somebody choosing to
           keep the name the file arrived with, and must not fall through to anything;
        2. the name Sift ships for this Site;
        3. the rule for all Sites, which therefore reaches only an address the catalog does not
           know (`site_key` None) or a key it no longer has;
        4. keep the name (`DEFAULT_TEMPLATE`), when nothing above says anything.

        A shipped name sits ABOVE the rule for all Sites on purpose. The rule for all Sites is one
        template for every Site, and no one template can be right for them: `{name}` is a random
        code on TikTok and the only meaningful thing there is on a file host. Somebody who wants
        their own rule on a Site types it there, and it wins.
        """
        fallback = await self._one(DEFAULT_SCOPE)
        if site_key is None:
            return fallback
        chosen = await self._one(site_key)
        return SiteOptions(
            naming=_naming(chosen.naming, site_key, fallback.naming),
            dest_folder_id=(
                chosen.dest_folder_id
                if chosen.dest_folder_id is not None
                else fallback.dest_folder_id
            ),
            downloader=(
                chosen.downloader if chosen.downloader is not None else fallback.downloader
            ),
        )

    async def set(
        self,
        scope: str,
        *,
        naming: str | None,
        dest_folder_id: str | None,
        downloader: str | None = None,
        actor: Actor | None = None,
        folders: Mapping[str, str] | None = None,
    ) -> None:
        """Give a site (or everything, under the reserved scope) its own answers.

        **SAID IN HISTORY, one line per answer that moved**, the way the settings hub says a
        setting (`settings_hub.service`): the setting as the subject, and its key, what it was and
        what it is in the payload. A Site's settings are the one place a person changes what
        Sift does to every later download from it, and a row that keeps only its current value
        could never answer "since when are Instagram files called this". `folders` is the words
        for each folder id the change names (the route reads them), because an id in a line is
        no answer; `actor` None writes no line (a caller that is not a person's press).
        """
        now = SiteOptions(naming=naming, dest_folder_id=dest_folder_id, downloader=downloader)
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            was = await self._held_on(connection, scope)
            await connection.execute(
                _WRITE, (scope, naming, dest_folder_id, downloader, int(time.time()))
            )
            if actor is not None:
                await _say_changes(connection, actor, scope, was, now, folders or {})

    async def clear(
        self, scope: str, *, actor: Actor | None = None, folders: Mapping[str, str] | None = None
    ) -> None:
        """Put a site back to following the default. Idempotent. Said in History as `set` is."""
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            was = await self._held_on(connection, scope)
            await connection.execute(_CLEAR, (scope,))
            if actor is not None:
                await _say_changes(connection, actor, scope, was, SiteOptions(), folders or {})

    async def held(self, scope: str) -> SiteOptions:
        """What this scope's own row says, with nothing filled in from anywhere else."""
        async with self._db.read() as connection:
            return await self._held_on(connection, scope)

    @staticmethod
    async def _held_on(connection: Connection, scope: str) -> SiteOptions:
        row = await (await connection.execute(_READ_ONE, (scope,))).fetchone()
        if row is None:
            return SiteOptions()
        return SiteOptions(
            naming=row["naming"],
            dest_folder_id=row["dest_folder_id"],
            downloader=row["downloader"],
        )

    async def _one(self, scope: str) -> SiteOptions:
        row = await self._db.fetch_one(_READ_ONE, (scope,))
        if row is None:
            return SiteOptions(naming=None if scope != DEFAULT_SCOPE else DEFAULT_TEMPLATE)
        return SiteOptions(
            naming=row["naming"],
            dest_folder_id=row["dest_folder_id"],
            downloader=row["downloader"],
        )


#: A Site's three answers, by field: the words a line calls it, and what "nothing of its own" means
#: for it, in the words its card draws (`NamingTemplate.svelte`).
_SAID: Mapping[str, tuple[str, str]] = {
    "naming": ("name template", "Sift's name"),
    "dest_folder_id": ("download folder", "the default downloads folder"),
    "downloader": ("downloader", "the downloader Sift chooses"),
}

#: The key a Site's setting is recorded under: `site_options.<site key>.<field>`, the shape the
#: History feed links to the Downloads pane (`workbench.router.setting_href`).
SITE_OPTIONS_KEY = "site_options.{scope}.{field}"


def _whose(scope: str) -> str:
    """Whose setting it is, as a line says it: "Instagram's"."""
    record = by_key(scope)
    return f"{record.site if record is not None else scope}'s"


def _value_said(field: str, value: str | None, folders: Mapping[str, str]) -> str:
    """One answer in the words its card draws: a folder by its name, nothing of its own as what
    the Site then follows, an empty rule as keeping the Site's own name."""
    if value is None:
        return _SAID[field][1]
    if field == "dest_folder_id":
        return folders.get(value) or "a folder since removed"
    if field == "naming" and value == "":
        return "the name the Site gave it"
    return value


async def _say_changes(
    connection: Connection,
    actor: Actor,
    scope: str,
    was: SiteOptions,
    now: SiteOptions,
    folders: Mapping[str, str],
) -> None:
    """One History line per field of a Site's settings that moved, in the settings hub's shape."""
    for field, (words, _unset) in _SAID.items():
        before, after = getattr(was, field), getattr(now, field)
        if before == after:
            continue
        key = SITE_OPTIONS_KEY.format(scope=scope, field=field)
        name = (
            f"The {words} for other addresses"
            if scope == DEFAULT_SCOPE
            else f"{_whose(scope)} {words}"
        )

        await record_event(
            connection,
            actor=actor,
            verb="edited",
            subject=Subject(kind="setting", id=key, name=name),
            payload=json.dumps(
                {
                    "key": key,
                    "before": None if field == "dest_folder_id" else before,
                    "after": None if field == "dest_folder_id" else after,
                    "before_said": _value_said(field, before, folders),
                    "after_said": _value_said(field, after, folders),
                }
            ),
        )


def _naming(typed: str | None, site_key: str, for_all_sites: str | None) -> str | None:
    """The name one Site's files get: what was typed for it, else what Sift ships for it, else
    the rule for all Sites. See `SiteOptionStore.resolve` for the order and why.

    `is not None` and never truthiness at the first step, because an empty rule is an answer.
    """
    if typed is not None:
        return typed
    record = by_key(site_key)
    if record is not None:
        return record.default_naming
    return for_all_sites


__all__ = ["DEFAULT_SCOPE", "SITE_OPTIONS_KEY", "SiteOptionStore", "SiteOptions"]


#: What each site does differently: what its files are called, and where they land.
SITE_OPTIONS: Part[SiteOptionStore] = Part("site_options")
