# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one site does differently: its tool, what its files are called, where they land.

A destination is a folder that already exists; Sift never builds a tree of its own."""

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

#: No site key can be this: keys are plain lowercase names.
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

    #: None follows the default; empty keeps the fetcher's name, a different answer.
    naming: str | None = None
    dest_folder_id: str | None = None
    #: A tool chosen here also turns off Sift's own reader for the site.
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
        """What applies to one site, each field falling back on its own; the name per `_naming`."""
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
        """Give a site (or everything) its own answers, one History line per answer that moved."""
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


_SAID: Mapping[str, tuple[str, str]] = {
    "naming": ("name template", "Sift's name"),
    "dest_folder_id": ("download folder", "the default downloads folder"),
    "downloader": ("downloader", "the downloader Sift chooses"),
}

SITE_OPTIONS_KEY = "site_options.{scope}.{field}"

DOWNLOAD_FOLDER = "Download folder"


def _whose(scope: str) -> str:
    """Whose setting it is, as a line says it: "Instagram's"."""
    record = by_key(scope)
    return f"{record.site if record is not None else scope}'s"


def _value_said(field: str, value: str | None, folders: Mapping[str, str]) -> str:
    """One answer in the words its card draws."""
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
        if scope != DEFAULT_SCOPE:
            name = f"{_whose(scope)} {words}"
        elif field == "dest_folder_id":
            name = DOWNLOAD_FOLDER
        else:
            name = f"The {words} for other addresses"

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
    """Typed for the Site (empty included), else Sift's shipped name, else the all-Sites rule."""
    if typed is not None:
        return typed
    record = by_key(site_key)
    if record is not None:
        return record.default_naming
    return for_all_sites


__all__ = ["DEFAULT_SCOPE", "DOWNLOAD_FOLDER", "SITE_OPTIONS_KEY", "SiteOptionStore", "SiteOptions"]


SITE_OPTIONS: Part[SiteOptionStore] = Part("site_options")
