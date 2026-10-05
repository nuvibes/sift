# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing preference values, against the declarations in the kernel registry.

The registry says what a setting is; this says what its value currently is. A value is stored only
when it differs from the default, so a read is "the override if there is one, otherwise the
default", and a write validates against the registered declaration before it lands.

The registry is read fresh on every call, never held. A capability that a later request depends on
(whether a guest may save a file to their device, say) has to reflect an admin's most recent
decision, not the one that was in force when someone logged in. So there is no cache here to go
stale: revoking a capability denies the very next request that checks it.

No value is ever logged, only the key it belongs to. A setting value is user data (a folder path,
a preference) and the log is the thing people paste into a bug report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.history_names import tunnel_ids, tunnels_said_in
from sift.kernel.audience import EVERY_ADMIN, NOBODY, Audience
from sift.kernel.changes import About, announce_now, telling
from sift.kernel.db import Database, point_read
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
    """A key that no feature has registered. Not stored and not read: an unknown key is a bug or a
    probe, never a preference, so it is refused rather than quietly kept."""


class ScopeForbidden(SettingError):
    """A guest tried to write a global (app) setting. Those are admin-only, enforced here on the
    server and not merely hidden from the screen."""


#: How long a comma-separated arrangement may be. Generous for a list of a dozen short names, and
#: far short of anything worth storing in a table that exists to hold a list of a dozen short names.
MAX_INTERFACE_VALUE = 512

#: What such a value may contain: short tokens, comma separated, and nothing else.
#:
#: The server deliberately does not know what a rail row is called: that is the client's
#: vocabulary and it changes with the client. So this checks the SHAPE and stops there. The client
#: repairs the meaning: an id this version does not have is dropped when the arrangement is read,
#: which it has to do anyway for an arrangement written by an older version.
_INTERFACE_VALUE = re.compile(r"\A[A-Za-z0-9._-]{1,40}(?:,[A-Za-z0-9._-]{1,40})*\Z")

#: There is no single-name pattern: no key holds one name alone (see the table below), and a
#: pattern is added the day a key needs it rather than kept as a shape nothing has.

#: The lists a picker can be asked to order by what this user reached for last.
#:
#: One key per kind, because they are picked from independently and a single blob would be rewritten
#: in full every time any one of them moved. The words are the client's own. See `FrequentKind` in
#: `frontend/src/lib/search/frequent.svelte.ts`, which is the other half of this pair.
FREQUENT_KINDS: tuple[str, ...] = ("collection", "photo_set", "person", "site", "tag", "song")

#: How many picks one of those lists may remember. The client trims to the same figure before it
#: writes; this is what stops a client that does not from turning the row into a scratchpad.
FREQUENT_KEPT = 50

#: How long a name may be inside one of those entries, and how long the whole value may be.
#:
#: The name is carried BESIDE the id, and it earns its place: a picker draws the rows it remembers
#: before it has any page from the server, and a remembered row with only an id would have to be
#: fetched one request at a time to be drawn at all. It is a copy, so it can go stale when something
#: is renamed: the picker prefers the name on the page it just fetched wherever it has one, and
#: rewrites what it remembers on the next pick, so the staleness is visible only on a remembered row
#: that is off the current page and only until it is next used.
MAX_PICK_NAME = 200
MAX_PICKED_VALUE = 4096

#: An id in one of those entries. A ULID as this application mints them, checked as a shape rather
#: than looked up: the server does not know which lists these belong to and must not start to.
_PICK_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def _token_list(value: str) -> bool:
    """A comma-separated arrangement: the rail's order, the rows it has put away."""
    return len(value) <= MAX_INTERFACE_VALUE and _INTERFACE_VALUE.fullmatch(value) is not None


def _skipped(value: str) -> bool:
    """A guard this user has agreed to stop being shown: the word `skip`, and nothing else.

    One word rather than a yes/no pair, because "never answered" and "answered no" are the same
    state and an empty value is already stored as no row at all, so a second word for them would
    be a third thing the client and this table would have to agree about.
    """
    return value == "skip"


def _open_or_shut(value: str) -> bool:
    """Which way a disclosure was left: the word `open` or the word `shut`, and nothing else.

    TWO words where `_skipped` above has one, and the difference is which way the default falls. A
    guard is ON until somebody turns it off, so "never answered" and "answered no" are the same
    state and one word covers both. A disclosure that defaults to OPEN cannot do that: an absent row
    means open, so "shut" has to be storable, and a word for open has to exist as well or clearing
    the row would be the only way back and a client would have to know that.
    """
    return value in {"open", "shut"}


def _on_or_off(value: str) -> bool:
    """A behaviour this user can switch off and switch back on: the word `on` or the word `off`.

    A THIRD shape beside the two above, and the reason is the same axis both of those turn on: which
    way the default falls, and whether the client needs a word for it. `_skipped` has one word
    because a guard is on until somebody says otherwise, so "never answered" and "answered no" are
    one state. `_open_or_shut` has two because a disclosure defaults to open. This defaults to ON and
    therefore needs two as well, but its words are not `open` and `shut`, because what it stores is
    not a disclosure, and a value read back as "the popout is shut" would be a sentence about a
    different thing entirely. One vocabulary per question; a shared validator would have been a
    saving of four lines paid for by a word that lies.
    """
    return value in {"on", "off"}


def _except_or_only(value: str) -> bool:
    """Who a fingerprint export starts with: `except` (all but the picks) or `only` (the picks)."""
    return value in {"except", "only"}


def _picked_list(value: str) -> bool:
    """What this user picked recently: `[{"id": ..., "name": ..., "used": n}]`, newest-used first.

    Checked for SHAPE and not for meaning, exactly as the arrangement above is. The server has no
    idea whether an id here still names a collection, and it must not acquire one: that would be
    this table reaching into five other slices to validate a display preference. What it does owe is
    that the row cannot become a scratchpad: a bounded number of bounded entries, and nothing else.
    """
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
        # `bool` is an `int` in Python, and `True` would sort as a count of one. Refused here so
        # a client that sends the wrong shape hears about it rather than being quietly re-ranked.
        if not isinstance(used, int) or isinstance(used, bool) or used < 1:
            return False
    return True


#: What the interface may remember about itself, per user, and what each key may hold.
#:
#: A closed list rather than free-form storage. Everything here is written by the client and read
#: back by the client, so an open key space would be a per-user scratchpad anybody signed in
#: could fill with whatever they liked, and nothing on the server would ever notice, because
#: nothing on the server reads it.
#:
#: A mapping rather than a set, because the families of keys do not hold the same sort of value:
#: most are lists of short client-side names, while the picker memory carries a name a person typed,
#: and widening the one pattern to admit that would widen it for the rail as well.
INTERFACE_KEYS: dict[str, Callable[[str], bool]] = {
    "rail.order": _token_list,
    "rail.hidden": _token_list,
    #: Which folders this user downloaded into last, most recent first. The same comma-separated
    #: shape the rail's arrangement uses, and checked the same way: ids, in an order, and no
    #: meaning the server is entitled to. How many of them a chooser puts in front of the
    #: alphabetical list is the client's decision (see `recent-folders` in the frontend) for the
    #: reason the whole of this table is the client's: the server does not know what a chooser
    #: looks like and must not start to.
    "recent.folders": _token_list,
    #: Whether the cross on a chip takes something off a file without asking first. The guard is
    #: on by default and is turned off by ticking a box on the question itself; clearing the key
    #: puts it back. It follows the USER rather than the browser because that is what somebody
    #: agreed to: they said to stop being asked, not to stop being asked on this laptop.
    "confirm.chip_remove": _skipped,
    #: Whether taking a face off a file (ignoring it or removing it for good) happens without
    #: asking first. ONE key for both verbs and not two: somebody ticking the box is answering "stop
    #: asking me about faces on this strip", and two keys would mean answering it twice to be rid of
    #: a question that reads as one. The safer of the two verbs decides the wording on the box, so
    #: what is agreed to is the stronger claim. Same shape and same reasoning as
    #: `confirm.chip_remove` above.
    "confirm.face_removal": _skipped,
    #: Whether the popout opens with everything under the picture showing: the lookalikes, the
    #: faces and the record, folded away behind one Expand control on the file's own row. It follows
    #: the USER rather than the browser, and that is the opposite call from the withdrawn key
    #: below on purpose: how much of a screen somebody wants is a settled preference, where which
    #: PANE of a record they last read is a fact about one visit.
    "popout.expanded": _open_or_shut,
    #: Whether leaving the popout for another page hands what is PLAYING to the mini player instead
    #: of stopping it. On until somebody turns it off, which is why the value has a word for both
    #: states. See `_on_or_off`. It covers any press in the popout that would otherwise close it,
    #: such as a chip or a face group leading to another page. It follows the USER rather than the
    #: browser for the reason `popout.expanded` above does: it is an answer about how somebody wants
    #: to move around a library, not about one laptop.
    "popout.leave_to_mini": _on_or_off,
    #: How Settings > Faces' export chooser opens: per user, as how somebody shares, not a browser.
    "faces.export_way": _except_or_only,
    #: Withdrawn keys are not in this list, so an old client writing them meets the closed list's
    #: ordinary refusal rather than a row that quietly stays: `organize.history_open` (the band it
    #: folded is gone; every act is in Settings > History), `popout.record_tab` (a popout always
    #: opens on About; which pane holds while stepping between files is the client's own state),
    #: `path.quest.declined` (the weekly quests left when Get to know Sift became learning paths),
    #: and the three `path.hint.<name>.seen` (Get to know Sift shows no hints).
    **{f"frequent.{kind}": _picked_list for kind in FREQUENT_KINDS},
}

# Read on every request that resolves a viewer, and answered from a two-column primary key, so
# it is declared a point read and runs on the event loop where the machine allows it.
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
    """Preference storage. One per application, built at boot and held on app.state.

    Held rather than reached for through a global so a test can hand it its own database, and so
    nothing outside the kernel's wiring has to hold a raw database handle to read a setting.
    """

    def __init__(self, database: Database) -> None:
        self._db = database
        #: A check a slice registers for one key, asked on the way in beside the registry's own
        #: refusal: it may read the disk or the library (a backup folder against the granted
        #: folders), which a registry refusal, a pure function of the value, cannot.
        self._checks: dict[str, Callable[[Any], Awaitable[str | None]]] = {}

    def check_with(self, key: str, check: Callable[[Any], Awaitable[str | None]]) -> None:
        """Register the one asynchronous check a key is refused by, beside the registry's own."""
        self._checks[key] = check

    # --- reading one value -----------------------------------------------------------------

    async def get_user(self, user_id: str, key: str) -> Any:
        """One user's value for a per-user setting, or its default. Raises UnknownSetting if the key
        is not registered, or ScopeForbidden if it is a global setting asked for by user."""
        retired = get_retired(key)
        if retired is not None:
            return retired.read(tuple([await self.get_user(user_id, one) for one in retired.into]))
        setting = self._require(key, Scope.USER)
        row = await self._db.fetch_one(_GET_USER, (user_id, key))
        return self._decode(setting, row["value"]) if row is not None else setting.default

    async def get_app(self, key: str) -> Any:
        """The global value for an app setting, or its default. Read from the database on every
        call: this is the live-capability path, and a cached answer here would be a stale
        permission.

        A RETIRED key is answered from the settings it was retired into (see
        `settings_registry.retire_setting`), so every caller written before the change (and every
        folder's own answer stored under the old key) goes on reading the one stored value."""
        retired = get_retired(key)
        if retired is not None:
            return retired.read(tuple([await self.get_app(one) for one in retired.into]))
        setting = self._require(key, Scope.APP)
        row = await self._db.fetch_one(_GET_APP, (key,))
        return self._decode(setting, row["value"]) if row is not None else setting.default

    async def app_is_stored(self, key: str) -> bool:
        """Whether somebody chose a value for this app setting, as against it answering its default.

        `get_app` cannot say: a chosen 60 and the default 60 read the same. A rule that turns on a
        person having chosen (a wait they typed raises a service's own floor; the default raises
        nothing) asks this beside it. A retired key is stored when any key it retired into is."""
        retired = get_retired(key)
        if retired is not None:
            return any([await self.app_is_stored(one) for one in retired.into])
        self._require(key, Scope.APP)
        return await self._db.fetch_one(_GET_APP, (key,)) is not None

    # --- writing ---------------------------------------------------------------------------

    async def apply(self, viewer: Viewer, updates: dict[str, Any]) -> None:
        """Validate a batch of changes and write them in one transaction.

        Everything is checked before anything is written: an unknown key, a global key from a guest,
        or a value its validator rejects fails the whole request and stores nothing. A half-applied
        settings change is a worse outcome than a rejected one: the caller is left not knowing
        which half took, so it is all or none.
        """
        planned = await self._planned(viewer, updates)

        # Who is told follows the scope: a personal preference its user, a shared one every admin.
        told = NOBODY
        if any(setting.scope is Scope.USER for setting, _ in planned):
            told |= Audience.of_user(viewer.id)
        if any(setting.scope is Scope.APP for setting, _ in planned):
            told = told.widened_to_admins()
        # What each said BEFORE, read outside the write: the event is the only record of it.
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
                # One event per key, and only where the value actually moved. A screen that sends
                # back every field it drew would otherwise write an event for each one on every
                # Save, and a record where most rows say nothing happened is a record nobody reads.
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
                    # THE LABEL, not the key. The snapshot is what the feed draws, and a feed that
                    # read "Edited playback.volume" would be drawing a machine's word for the
                    # thing; the label is what the person pressed. An older row may carry the key,
                    # and the feed's reader puts the current label over any setting it still knows.
                    subject=Subject(kind="setting", id=setting.key, name=setting.label),
                    payload=json.dumps(payload),
                )

        # A setting that changes what the user may see rings `library` too, which the scoped lists
        # listen for (`vault.concealment`); after the block, as `telling` carries one subject.
        if any(setting.changes_visibility for setting, _ in planned):
            announce_now(Audience.of_user(viewer.id), About.LIBRARY)

        log.info(
            "settings.updated",
            user_id=viewer.id,
            keys=sorted(setting.key for setting, _ in planned),
        )

    async def tell_changed_defaults(self, running: str) -> int:
        """At a start: one History line per default the update to `running` (or a release since
        this library last started) changed for somebody who never chose. See `defaults`."""
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
        """Write a batch Sift chose for itself, through the same checks a person's save goes through.

        Validated by `_planned` exactly as `apply` validates a press, so a value Sift picked gets
        no privilege a typed number lacks, and it is all or none for the same reason. Only the
        settings the whole installation shares: a personal preference is a person's to set.

        One line on History for the whole batch, not one per key: it is one act, and it is a
        RECEIPT, so its Undo (the reverser registered under `queue`) can put every value back at
        once. The payload is what the undo reads: each key with the value before and after, as this
        door read them. The words are the caller's (`title`, `detail`), since only it knows why.

        Answers the receipt's id, or None where no value moved and so nothing was written.
        """
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
        """Every change in a batch checked and encoded, or the first refusal raised.

        `viewer` None is Sift saving for itself (`apply_as_sift`): only the settings the whole
        installation shares, and no retired spelling, since nothing of Sift's writes an old key.
        """
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
            # And only here. A refusal is about the machine, not about the value, so it belongs on
            # the way IN and nowhere else: `_decode` runs the validator on every read, and a
            # machine check there would quietly rewrite a stored choice somebody made deliberately.
            refused = setting.refuse(normalized) if setting.refuse is not None else None
            if refused is None and (check := self._checks.get(key)) is not None:
                refused = await check(normalized)
            if refused is not None:
                raise SettingError(refused)
            planned.append((setting, json.dumps(normalized)))
        return planned

    # --- where somebody arranged the interface ------------------------------------------------

    async def interface(self, viewer: Viewer) -> dict[str, str]:
        """This user's arrangement of the interface. Nobody else's, ever.

        Empty means "never arranged anything", which is not the same as "arranged it to be empty":
        the client draws what Sift ships with, and a row appears only once somebody has moved
        something.
        """
        rows = await self._db.fetch_all(_ALL_INTERFACE, (viewer.id,))
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def arrange(self, viewer: Viewer, updates: dict[str, str | None]) -> None:
        """Remember how this user has arranged the interface. All of it or none of it.

        A key of `None` means "back to what Sift ships with", stored as no row at all rather than
        as an empty string, so a reset leaves the user in the state a new one is in, and the
        default can change in a later version and reach them.

        Every key is checked before anything is written, for the same reason a batch of settings is:
        a half-applied arrangement leaves somebody with a rail that is neither what they had nor
        what they asked for.
        """
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
        """Every setting this viewer may see, with its current value, grouped into the screen's
        sections.

        A guest sees only their own per-user settings; the global ones are admin-only and are left
        out of a guest's view entirely rather than shown read-only. Every section is present even
        when nothing has registered into it, so the screen can draw the shell and later features
        fill it.
        """
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

        # Each retired key and the settings that answer it now, so an address naming the old row
        # can be sent to the new one. Never drawn: a retired key has no row of its own.
        retired = {key: list(one.into) for key, one in sorted(retired_settings().items())}
        return {
            "sections": [{"name": name, "settings": sections[name]} for name in SECTIONS],
            "retired": retired,
        }

    # --- internals -------------------------------------------------------------------------

    async def _successors(self, viewer: Viewer, updates: dict[str, Any]) -> dict[str, Any]:
        """The batch with every retired key written as the settings that answer it now.

        A value for the old key is translated against the successors' CURRENT values, so writing
        "on" to a switch that became a three-way choice keeps whichever "on" answer was already
        chosen. A successor named in the same batch in its own right wins: it is the newer spelling
        and the more exact answer. An old key whose successor is per-user is written for this viewer.
        """
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
        """Parse a stored value and re-check it against the setting as it is declared now.

        A row can outlive the code that wrote it: a restored backup, or a setting whose allowed
        values narrowed in an update. Rather than hand back something the current validator would
        reject, fall back to the default and say so, so the screen shows a valid value and the odd
        one out is visible in the log.
        """
        try:
            value = json.loads(raw)
            return setting.validate(value)
        except (json.JSONDecodeError, SettingError):
            log.warning("settings.stored_value_invalid", key=setting.key)
            return setting.default


#: The preference store, as the feature that owns it uses it.
#:
#: The kernel publishes the same object under the same name as a read-only interface, which is what
#: every other feature takes. This is the whole of it, and only this feature may have that.
SERVICE: Part[SettingsService] = Part("settings_hub")
