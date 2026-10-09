# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing preference values against the kernel registry; read fresh, never logged."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.history_names import tunnel_ids, tunnels_said_in
from sift.kernel.audience import EVERY_ADMIN, NOBODY, Audience
from sift.kernel.changes import About, announce_now, telling
from sift.kernel.db import Database, point_read
from sift.kernel.jobs.switchboard import one_reading_now
from sift.kernel.ledger import Actor, Reversal, record_event
from sift.kernel.log import get_logger
from sift.kernel.settings_registry import (
    SECTIONS,
    Scope,
    Setting,
    SettingError,
    get_registered,
    get_retired,
    registered_settings,
    retired_settings,
)
from sift.kernel.tunnels.store import tunnels_said
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part
from sift.slices.settings_hub.defaults import tell_changed_defaults

log = get_logger(__name__)


class UnknownSetting(SettingError):
    """A key no feature has registered: a bug or a probe, refused rather than quietly kept."""


class ScopeForbidden(SettingError):
    """A guest tried to write a global (app) setting, refused here on the server."""


MAX_INTERFACE_VALUE = 512

#: Checks the shape only: the client drops an id this version does not know when it reads.
_INTERFACE_VALUE = re.compile(r"\A[A-Za-z0-9._-]{1,40}(?:,[A-Za-z0-9._-]{1,40})*\Z")

#: The lists a picker orders by what this user reached for last; the other half is `FrequentKind`.
FREQUENT_KINDS: tuple[str, ...] = ("collection", "photo_set", "person", "site", "tag", "song")

#: Stops a client that does not trim from turning the row into a scratchpad.
FREQUENT_KEPT = 50

#: The name rides beside the id so a picker can draw remembered rows before any page arrives.
MAX_PICK_NAME = 200
MAX_PICKED_VALUE = 4096

#: Checked as a shape, never looked up: the server must not learn which lists these belong to.
_PICK_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def _token_list(value: str) -> bool:
    """A comma-separated arrangement: the rail's order, the rows it has put away."""
    return len(value) <= MAX_INTERFACE_VALUE and _INTERFACE_VALUE.fullmatch(value) is not None


def _skipped(value: str) -> bool:
    """A guard this user has agreed to stop being shown: the word `skip`, and nothing else."""
    return value == "skip"


def _open_or_shut(value: str) -> bool:
    """Which way a disclosure was left: `open` or `shut`, since an absent row means open."""
    return value in {"open", "shut"}


def _on_or_off(value: str) -> bool:
    """A behaviour this user can switch off and on again: `on` or `off`, defaulting to on."""
    return value in {"on", "off"}


def _except_or_only(value: str) -> bool:
    """Who a fingerprint export starts with: `except` (all but the picks) or `only` (the picks)."""
    return value in {"except", "only"}


def _picked_list(value: str) -> bool:
    """What this user picked recently, newest first, checked for shape and never for meaning."""
    if len(value) > MAX_PICKED_VALUE:
        return False
    try:
        read = json.loads(value)
    except ValueError:
        return False
    if not isinstance(read, list) or len(read) > FREQUENT_KEPT:
        return False
    for one in read:
        if not isinstance(one, dict) or set(one) - {"id", "name", "used"}:
            return False
        found = one.get("id")
        if not isinstance(found, str) or _PICK_ID.fullmatch(found) is None:
            return False
        name = one.get("name", "")
        if not isinstance(name, str) or len(name) > MAX_PICK_NAME:
            return False
        used = one.get("used", 1)
        # `bool` is an `int`, and `True` would sort as a count of one.
        if not isinstance(used, int) or isinstance(used, bool) or used < 1:
            return False
    return True


#: What the interface may remember about itself, per user: a closed list, never a scratchpad.
INTERFACE_KEYS: dict[str, Callable[[str], bool]] = {
    "rail.order": _token_list,
    "rail.hidden": _token_list,
    #: The folders this user downloaded into last, most recent first.
    "recent.folders": _token_list,
    #: Whether a chip's cross removes without asking; per user, since that is who agreed.
    "confirm.chip_remove": _skipped,
    #: One key for ignoring and removing a face, since ticking the box answers both.
    "confirm.face_removal": _skipped,
    "popout.expanded": _open_or_shut,
    #: Whether leaving the popout hands what is playing to the mini player; on by default.
    "popout.leave_to_mini": _on_or_off,
    "faces.export_way": _except_or_only,
    #: Withdrawn keys are left out so an old client writing them is refused.
    **{f"frequent.{kind}": _picked_list for kind in FREQUENT_KINDS},
}

# A point read on a two-column primary key, so it runs on the event loop where allowed.
_GET_USER = point_read(
    "settings.user_value", "SELECT value FROM user_settings WHERE user_id = ? AND key = ?"
)
_GET_APP = point_read("settings.app_value", "SELECT value FROM app_settings WHERE key = ?")
_ALL_USER = "SELECT key, value FROM user_settings WHERE user_id = ?"
_ALL_APP = "SELECT key, value FROM app_settings"

_SET_USER = (
    "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) "
    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value"
)
_SET_APP = (
    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)

_ALL_INTERFACE = "SELECT key, value FROM interface_state WHERE user_id = ?"
_SET_INTERFACE = (
    "INSERT INTO interface_state (user_id, key, value) VALUES (?, ?, ?) "
    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value"
)
_CLEAR_INTERFACE = "DELETE FROM interface_state WHERE user_id = ? AND key = ?"


class SettingsService:
    """Preference storage, one per application, built at boot and held on app.state."""

    def __init__(self, database: Database) -> None:
        self._db = database
        #: A slice's check for one key that may read the disk or the library, beside the registry's.
        self._checks: dict[str, Callable[[Any], Awaitable[str | None]]] = {}

    def check_with(self, key: str, check: Callable[[Any], Awaitable[str | None]]) -> None:
        """Register the one asynchronous check a key is refused by, beside the registry's own."""
        self._checks[key] = check

    # --- reading one value -----------------------------------------------------------------

    async def get_user(self, user_id: str, key: str) -> Any:
        """One user's value for a per-user setting, or its default."""
        retired = get_retired(key)
        if retired is not None:
            return retired.read(tuple([await self.get_user(user_id, one) for one in retired.into]))
        setting = self._require(key, Scope.USER)
        row = await self._db.fetch_one(_GET_USER, (user_id, key))
        return self._decode(setting, row["value"]) if row is not None else setting.default

    async def get_app(self, key: str) -> Any:
        """The global value for an app setting, or its default, read live on every call."""
        retired = get_retired(key)
        if retired is not None:
            return retired.read(tuple([await self.get_app(one) for one in retired.into]))
        setting = self._require(key, Scope.APP)
        stored = await self._stored_app(key)
        return self._decode(setting, stored) if stored is not None else setting.default

    async def app_is_stored(self, key: str) -> bool:
        """Whether somebody chose this app setting rather than it answering its default."""
        retired = get_retired(key)
        if retired is not None:
            return any([await self.app_is_stored(one) for one in retired.into])
        self._require(key, Scope.APP)
        return await self._stored_app(key) is not None

    async def _stored_app(self, key: str) -> str | None:
        """One key's stored text, or None, from one read of the whole table per question."""
        reading = one_reading_now()
        if reading is None:
            row = await self._db.fetch_one(_GET_APP, (key,))
            return None if row is None else str(row["value"])
        if reading.values is None:
            rows = await self._db.fetch_all(_ALL_APP)
            reading.values = {str(row["key"]): str(row["value"]) for row in rows}
        return reading.values.get(key)

    # --- writing ---------------------------------------------------------------------------

    async def apply(self, viewer: Viewer, updates: dict[str, Any]) -> None:
        """Validate a batch of changes and write them in one transaction: all or none."""
        planned = await self._planned(viewer, updates)

        # Who is told follows the scope: a personal preference its user, a shared one every admin.
        told = NOBODY
        if any(setting.scope is Scope.USER for setting, _ in planned):
            told |= Audience.of_user(viewer.id)
        if any(setting.scope is Scope.APP for setting, _ in planned):
            told = told.widened_to_admins()
        # What each said before, read outside the write: the event is the only record of it.
        was = {
            setting.key: json.dumps(
                await (
                    self.get_user(viewer.id, setting.key)
                    if setting.scope is Scope.USER
                    else self.get_app(setting.key)
                )
            )
            for setting, _ in planned
        }
        async with telling(self._db, told, About.SETTINGS) as connection:
            for setting, encoded in planned:
                if setting.scope is Scope.USER:
                    await connection.execute(_SET_USER, (viewer.id, setting.key, encoded))
                else:
                    await connection.execute(_SET_APP, (setting.key, encoded))
                # One event per key, and only where the value actually moved.
                if was.get(setting.key) == encoded:
                    continue
                payload: dict[str, object] = {
                    "key": setting.key,
                    "before": was.get(setting.key),
                    "after": encoded,
                }
                if setting.names_a_tunnel:
                    # Named as it was then: a tunnel renamed or removed later does not change it.
                    payload = tunnels_said_in(
                        payload, await tunnels_said(connection, tunnel_ids(payload))
                    )
                await record_event(
                    connection,
                    actor=Actor.user(viewer.id),
                    verb="edited",
                    # The label, not the key: the feed draws what the person pressed.
                    subject=Subject(kind="setting", id=setting.key, name=setting.label),
                    payload=json.dumps(payload),
                )

        # A setting that changes what the user may see rings `library` too, after the block.
        if any(setting.changes_visibility for setting, _ in planned):
            announce_now(Audience.of_user(viewer.id), About.LIBRARY)

        log.info(
            "settings.updated",
            user_id=viewer.id,
            keys=sorted(setting.key for setting, _ in planned),
        )

    async def tell_changed_defaults(self, running: str) -> int:
        """At a start: one History line per default that changed for somebody who never chose."""
        return await tell_changed_defaults(self._db, running)

    async def apply_as_sift(
        self,
        updates: dict[str, Any],
        *,
        via: str,
        queue: str,
        title: str,
        detail: str,
    ) -> str | None:
        """Write a batch Sift chose, through a person's checks, as one undoable receipt."""
        planned = await self._planned(None, updates)
        was = {setting.key: json.dumps(await self.get_app(setting.key)) for setting, _ in planned}
        moving = [(setting, encoded) for setting, encoded in planned if was[setting.key] != encoded]
        if not moving:
            return None
        changes = [
            {
                "key": setting.key,
                "label": setting.label,
                "before": was[setting.key],
                "after": encoded,
            }
            for setting, encoded in moving
        ]
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            for setting, encoded in moving:
                await connection.execute(_SET_APP, (setting.key, encoded))
            receipt = await record_event(
                connection,
                actor=Actor.sift(via),
                verb="edited",
                subject=[
                    Subject(kind="setting", id=setting.key, name=setting.label)
                    for setting, _ in moving
                ],
                payload=json.dumps({"changes": changes}),
                receipt=Reversal(queue=queue, title=title, detail=detail),
            )
        log.info("settings.updated_by_sift", via=via, keys=sorted(one["key"] for one in changes))
        return receipt

    async def _planned(
        self, viewer: Viewer | None, updates: dict[str, Any]
    ) -> list[tuple[Setting, str]]:
        """Every change in a batch checked and encoded, or the first refusal raised."""
        if viewer is None:
            if any(get_retired(key) is not None for key in updates):
                raise UnknownSetting("Sift writes settings by their current keys only")
            resolved = dict(updates)
        else:
            resolved = await self._successors(viewer, updates)
        planned: list[tuple[Setting, str]] = []
        for key, value in resolved.items():
            setting = self._require(key)
            if viewer is None and setting.scope is not Scope.APP:
                raise ScopeForbidden(f"{key!r} is a personal setting, and only its user sets it")
            if viewer is not None and setting.scope is Scope.APP and not viewer.is_admin:
                raise ScopeForbidden(f"{key!r} is a global setting; only an admin may change it")
            normalized = setting.validate(value)
            # Only on the way in, and on a thread: in `_decode` it would rewrite a stored choice.
            refused = (
                await asyncio.to_thread(setting.refuse, normalized)
                if setting.refuse is not None
                else None
            )
            if refused is None and (check := self._checks.get(key)) is not None:
                refused = await check(normalized)
            if refused is not None:
                raise SettingError(refused)
            planned.append((setting, json.dumps(normalized)))
        return planned

    # --- where somebody arranged the interface ------------------------------------------------

    async def interface(self, viewer: Viewer) -> dict[str, str]:
        """This user's arrangement of the interface; empty means never arranged."""
        rows = await self._db.fetch_all(_ALL_INTERFACE, (viewer.id,))
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def arrange(self, viewer: Viewer, updates: dict[str, str | None]) -> None:
        """Remember how this user has arranged the interface, all or none; `None` resets a key."""
        planned: list[tuple[str, str | None]] = []
        for key, value in updates.items():
            holds = INTERFACE_KEYS.get(key)
            if holds is None:
                raise UnknownSetting(f"no interface state named {key!r}")
            if value is None or value == "":
                planned.append((key, None))
                continue
            if not holds(value):
                raise SettingError(f"{key!r} was not given something it can hold")
            planned.append((key, value))

        async with telling(self._db, Audience.of_user(viewer.id), About.SETTINGS) as connection:
            for key, value in planned:
                if value is None:
                    await connection.execute(_CLEAR_INTERFACE, (viewer.id, key))
                else:
                    await connection.execute(_SET_INTERFACE, (viewer.id, key, value))

        log.info("interface.arranged", user_id=viewer.id, keys=sorted(key for key, _ in planned))

    # --- the screen ------------------------------------------------------------------------

    async def effective(self, viewer: Viewer) -> dict[str, Any]:
        """Every setting this viewer may see, with its value, grouped into the screen's sections."""
        registry = registered_settings()
        user_overrides = await self._overrides(_ALL_USER, (viewer.id,))
        app_overrides = await self._overrides(_ALL_APP, ()) if viewer.is_admin else {}

        sections: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTIONS}
        for setting in registry.values():
            if setting.scope is Scope.APP and not viewer.is_admin:
                continue
            overrides = app_overrides if setting.scope is Scope.APP else user_overrides
            raw = overrides.get(setting.key)
            value = self._decode(setting, raw) if raw is not None else setting.default
            entry = setting.metadata()
            entry["value"] = value
            sections[setting.section].append(entry)

        for entries in sections.values():
            entries.sort(key=lambda entry: entry["key"])

        # Each retired key and the settings that answer it now, to redirect an old address.
        retired = {key: list(one.into) for key, one in sorted(retired_settings().items())}
        return {
            "sections": [{"name": name, "settings": sections[name]} for name in SECTIONS],
            "retired": retired,
        }

    # --- internals -------------------------------------------------------------------------

    async def _successors(self, viewer: Viewer, updates: dict[str, Any]) -> dict[str, Any]:
        """The batch with every retired key written as the settings that answer it now."""
        plain = {key: value for key, value in updates.items() if get_retired(key) is None}
        for key, value in updates.items():
            retired = get_retired(key)
            if retired is None:
                continue
            current: list[Any] = []
            for one in retired.into:
                declared = self._require(one)
                current.append(
                    await self.get_user(viewer.id, one)
                    if declared.scope is Scope.USER
                    else await self.get_app(one)
                )
            for one, new in zip(retired.into, retired.write(value, tuple(current)), strict=True):
                plain.setdefault(one, new)
        return plain

    def _require(self, key: str, expected: Scope | None = None) -> Setting:
        setting = get_registered(key)
        if setting is None:
            raise UnknownSetting(f"no setting named {key!r}")
        if expected is not None and setting.scope is not expected:
            other = "global" if expected is Scope.USER else "per-user"
            raise ScopeForbidden(f"{key!r} is a {other} setting")
        return setting

    async def _overrides(self, sql: str, params: tuple[Any, ...]) -> dict[str, str]:
        rows = await self._db.fetch_all(sql, params)
        return {str(row["key"]): str(row["value"]) for row in rows}

    def _decode(self, setting: Setting, raw: str) -> Any:
        """Parse a stored value and re-check it; the default if it no longer fits."""
        try:
            value = json.loads(raw)
            return setting.validate(value)
        except (json.JSONDecodeError, SettingError):
            log.warning("settings.stored_value_invalid", key=setting.key)
            return setting.default


#: The preference store as its owning feature uses it; others take the read-only kernel view.
SERVICE: Part[SettingsService] = Part("settings_hub")
