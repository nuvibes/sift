# SPDX-License-Identifier: AGPL-3.0-or-later
"""The settings service: reading, writing, per-user isolation, and the live-capability rule."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from sift.kernel import changes
from sift.kernel import db as db_module
from sift.kernel.changes import About, ChangeBus
from sift.kernel.db import Database
from sift.kernel.jobs.switchboard import one_reading
from sift.kernel.settings_registry import Scope, SettingError, get_registered, register_setting
from sift.slices.music.settings import LOOKUP_ROUTE_KEY
from sift.slices.settings_hub.service import (
    ScopeForbidden,
    SettingsService,
    UnknownSetting,
)
from sift.slices.settings_hub.tests.conftest import (
    CONCEALMENT_KEY,
    GUEST_SAVE_KEY,
    LOOP_KEY,
    Users,
)
from sift.slices.swap import GUEST_TUNNEL_KEY

pytestmark = [pytest.mark.integration]


# --- reading defaults and overrides ---------------------------------------------------------


async def test_an_unset_user_setting_reads_its_default(
    service: SettingsService, users: Users
) -> None:
    assert await service.get_user(users.guest_a.id, LOOP_KEY) == "loop_one"


async def test_a_written_user_setting_reads_back(service: SettingsService, users: Users) -> None:
    await service.apply(users.guest_a, {LOOP_KEY: "once"})
    assert await service.get_user(users.guest_a.id, LOOP_KEY) == "once"


async def test_an_unset_app_setting_reads_its_default(service: SettingsService) -> None:
    assert await service.get_app(GUEST_SAVE_KEY) is False


async def test_an_app_setting_says_whether_somebody_chose_it(
    service: SettingsService, users: Users
) -> None:
    """`get_app` answers the default and a chosen value alike; a rule that turns on the choice
    (a chosen wait raising a service's own floor) asks `app_is_stored` beside it."""
    assert await service.app_is_stored(GUEST_SAVE_KEY) is False
    await service.apply(users.admin, {GUEST_SAVE_KEY: True})
    assert await service.app_is_stored(GUEST_SAVE_KEY) is True


async def test_one_question_of_the_switchboard_reads_its_settings_once_and_live(
    service: SettingsService, users: Users, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every setting a switchboard question asks comes from one read; the next question reads
    again, so a switch moved between two is seen by the second."""
    heard: list[str] = []
    judged = db_module._judged

    @contextmanager
    def counted(stage: str, statement: Any, *rest: Any, **options: Any) -> Iterator[Any]:
        heard.append(db_module.statement_name(statement))
        with judged(stage, statement, *rest, **options) as timing:
            yield timing

    monkeypatch.setattr(db_module, "_judged", counted)
    async with one_reading():
        first = [await service.get_app(GUEST_SAVE_KEY) for _ in range(5)]
        stored = await service.app_is_stored(GUEST_SAVE_KEY)
    assert (first, stored, len(heard)) == ([False] * 5, False, 1)

    await service.apply(users.admin, {GUEST_SAVE_KEY: True})
    heard.clear()
    async with one_reading():
        assert await service.get_app(GUEST_SAVE_KEY) is True
    assert len(heard) == 1


# --- per-user isolation ---------------------------------------------------------------------


async def test_one_users_change_does_not_move_anothers(
    service: SettingsService, users: Users
) -> None:
    """The multi-user shape has to be real from the first user, even with one in use."""
    await service.apply(users.guest_a, {LOOP_KEY: "loop_all"})
    assert await service.get_user(users.guest_a.id, LOOP_KEY) == "loop_all"
    assert await service.get_user(users.guest_b.id, LOOP_KEY) == "loop_one"


# --- validation on write --------------------------------------------------------------------


async def test_an_unknown_key_is_refused(service: SettingsService, users: Users) -> None:
    with pytest.raises(UnknownSetting, match="no setting named"):
        await service.apply(users.guest_a, {"not.a.real.key": 1})


async def test_a_value_the_validator_rejects_is_refused(
    service: SettingsService, users: Users
) -> None:
    from sift.kernel.settings_registry import SettingError

    with pytest.raises(SettingError, match="must be one of"):
        await service.apply(users.guest_a, {LOOP_KEY: "sideways"})


async def test_a_guest_cannot_write_a_global_setting(
    service: SettingsService, users: Users
) -> None:
    """Enforced in the service, not merely hidden on a screen."""
    with pytest.raises(ScopeForbidden):
        await service.apply(users.guest_a, {GUEST_SAVE_KEY: True})
    # And nothing was stored.
    assert await service.get_app(GUEST_SAVE_KEY) is False


async def test_an_admin_can_write_a_global_setting(service: SettingsService, users: Users) -> None:
    await service.apply(users.admin, {GUEST_SAVE_KEY: True})
    assert await service.get_app(GUEST_SAVE_KEY) is True


async def test_a_write_is_written_down_under_the_settings_label(
    service: SettingsService, users: Users, temp_db: Database
) -> None:
    """The feed draws the snapshot, so the snapshot is the words the person pressed and not the
    key: "Edited playback.volume" was a machine's name for the thing."""
    await service.apply(users.admin, {GUEST_SAVE_KEY: True})

    rows = await temp_db.fetch_all(
        "SELECT s.kind AS kind, s.subject_id AS subject_id, s.name AS name"
        " FROM workbench_decision_subjects s JOIN workbench_decisions d ON d.id = s.decision_id"
        " WHERE d.verb = 'edited'"
    )
    assert [tuple(row) for row in rows] == [
        ("setting", GUEST_SAVE_KEY, "Let guests save files to their device")
    ]


async def test_a_batch_is_all_or_nothing(service: SettingsService, users: Users) -> None:
    """A batch with one bad key stores none of it: a half-applied change leaves the caller not
    knowing which half took."""
    with pytest.raises(UnknownSetting):
        await service.apply(users.guest_a, {LOOP_KEY: "once", "not.real": 1})
    assert await service.get_user(users.guest_a.id, LOOP_KEY) == "loop_one"


async def test_a_value_an_asynchronous_check_refuses_is_not_stored(
    service: SettingsService, users: Users
) -> None:
    """A refusal about the machine (a folder that is not there) is asked on the way in; the value
    it refuses is never written, and one it accepts is."""
    asked: list[object] = []

    async def only_once(value: object) -> str | None:
        asked.append(value)
        return "that one cannot be used here" if value == "loop_all" else None

    service.check_with(LOOP_KEY, only_once)

    with pytest.raises(SettingError, match="cannot be used here"):
        await service.apply(users.guest_a, {LOOP_KEY: "loop_all"})
    assert await service.get_user(users.guest_a.id, LOOP_KEY) == "loop_one"

    await service.apply(users.guest_a, {LOOP_KEY: "once"})
    assert await service.get_user(users.guest_a.id, LOOP_KEY) == "once"
    assert asked == ["loop_all", "once"]


# --- the live-capability rule (nothing is cached) -------------------------------------------


async def test_a_global_capability_is_read_fresh_every_time(
    service: SettingsService, users: Users
) -> None:
    """The capability that a later request depends on must reflect an admin's most recent decision.
    Reading it twice across a change returns the new value with no reload: if this service cached
    the first answer, the second read would be stale and a revoked capability would still be granted.
    """
    assert await service.get_app(GUEST_SAVE_KEY) is False
    await service.apply(users.admin, {GUEST_SAVE_KEY: True})
    assert await service.get_app(GUEST_SAVE_KEY) is True
    await service.apply(users.admin, {GUEST_SAVE_KEY: False})
    assert await service.get_app(GUEST_SAVE_KEY) is False


# --- reading through the wrong scope --------------------------------------------------------


async def test_get_user_refuses_a_global_key(service: SettingsService, users: Users) -> None:
    with pytest.raises(ScopeForbidden):
        await service.get_user(users.guest_a.id, GUEST_SAVE_KEY)


async def test_get_app_refuses_a_per_user_key(service: SettingsService) -> None:
    with pytest.raises(ScopeForbidden):
        await service.get_app(LOOP_KEY)


# --- the effective view ---------------------------------------------------------------------


async def test_effective_groups_settings_into_all_sections(
    service: SettingsService, users: Users
) -> None:
    from sift.kernel.settings_registry import SECTIONS

    view = await service.effective(users.admin)
    names = [section["name"] for section in view["sections"]]
    assert names == list(SECTIONS)


async def test_effective_shows_a_guest_only_their_own_settings(
    service: SettingsService, users: Users
) -> None:
    """A guest does not see the instance-wide settings at all: they are admin-only."""
    view = await service.effective(users.guest_a)
    keys = {entry["key"] for section in view["sections"] for entry in section["settings"]}
    assert LOOP_KEY in keys
    assert GUEST_SAVE_KEY not in keys


async def test_effective_shows_an_admin_the_global_settings_too(
    service: SettingsService, users: Users
) -> None:
    view = await service.effective(users.admin)
    keys = {entry["key"] for section in view["sections"] for entry in section["settings"]}
    assert GUEST_SAVE_KEY in keys


async def test_a_default_that_changes_reaches_only_those_who_never_chose(
    service: SettingsService, users: Users
) -> None:
    """A new release that changes a default (a record showing every field) moves everybody who
    never pressed the switch, and leaves alone anybody who did, even to the old default: a press
    is stored whatever it says, so the stored answer stands."""
    from sift.kernel.settings_registry import _REGISTRY

    key = "test.shown_by_default"

    def declare(default: bool) -> None:
        _REGISTRY.pop(key, None)
        register_setting(
            key=key, scope="user", default=default, section="Appearance", label="Shown", help="."
        )

    declare(False)
    await service.apply(users.guest_a, {key: False})
    declare(True)
    assert await service.get_user(users.guest_a.id, key) is False
    assert await service.get_user(users.guest_b.id, key) is True


async def test_effective_reports_the_override_not_the_default(
    service: SettingsService, users: Users
) -> None:
    await service.apply(users.guest_a, {LOOP_KEY: "once"})
    view = await service.effective(users.guest_a)
    loop = next(
        entry
        for section in view["sections"]
        for entry in section["settings"]
        if entry["key"] == LOOP_KEY
    )
    assert loop["value"] == "once"
    assert loop["default"] == "loop_one"


# --- a stored value that no longer validates ------------------------------------------------


async def test_a_stored_value_that_no_longer_validates_falls_back_to_the_default(
    temp_db: object, service: SettingsService, users: Users
) -> None:
    """A row can outlive the code that wrote it: a restored backup, or a setting whose choices
    narrowed. Rather than serve something the current validator rejects, the read returns the
    default. Simulated by writing a value straight to the table that the registry would refuse.
    """
    from sift.kernel.db import Database

    assert isinstance(temp_db, Database)
    await temp_db.execute(
        "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
        (users.guest_a.id, LOOP_KEY, '"a-mode-that-was-removed"'),
    )
    assert await service.get_user(users.guest_a.id, LOOP_KEY) == "loop_one"


# --- types survive the round trip -----------------------------------------------------------


async def test_a_numeric_value_round_trips_as_a_number(
    service: SettingsService, users: Users, registered: None
) -> None:
    register_setting(
        key="library.keep_last",
        scope="user",
        default=5,
        minimum=1,
        maximum=30,
        section="Library",
        label="Backups to keep",
        help="How many automatic backups to keep before deleting the oldest.",
    )
    await service.apply(users.guest_a, {"library.keep_last": 12})
    value = await service.get_user(users.guest_a.id, "library.keep_last")
    assert value == 12
    assert isinstance(value, int)


def test_scopes_are_the_two_expected() -> None:
    assert {scope.value for scope in Scope} == {"user", "app"}


async def test_the_tables_are_only_made_once(temp_db: object) -> None:
    """The step is guarded on the recorded version, and the guarded-out case is the one that runs
    on every start after the first.

    A fresh database is the only case the rest of the suite ever builds, so the branch that says
    "these tables are already here" is taken nowhere else. It has to be a no-op rather than an
    error: it is what every boot after the install does.
    """
    from sift.kernel.db import Database
    from sift.slices.settings_hub.schema import SETTINGS_VERSION, initialize_settings

    assert isinstance(temp_db, Database)
    async with temp_db.write() as connection:
        await initialize_settings(connection, on_disk=0)
        await connection.execute("INSERT INTO app_settings (key, value) VALUES ('x', '1')")

    # The second start, with the version already recorded: nothing is made and nothing is lost.
    #
    # `SETTINGS_VERSION` rather than a number typed here. A typed `1` would say "already at v1"
    # and quietly exercise every step added since, so the day a step touched a table this fixture
    # does not build, a test about doing nothing would fail on a migration it was never about.
    # The version that means "nothing left to do" is the current one, by definition.
    async with temp_db.write() as connection:
        await initialize_settings(connection, on_disk=SETTINGS_VERSION)

    rows = await temp_db.fetch_all("SELECT key FROM app_settings")
    assert [row["key"] for row in rows] == ["x"]


async def test_a_choice_this_machine_cannot_make_is_refused_with_the_reason(
    service: SettingsService, users: Users, registered: None
) -> None:
    """A refusal is about the MACHINE rather than about the value, which is why it lives here.

    The distinction is not cosmetic: written as a validator, the same check also runs in
    `_decode` (on the way OUT of the database) and a stored value it rejects is silently replaced
    by the default. A device somebody had chosen would come back as the processor, so recognition
    would run there instead of refusing, which is the exact silent fallback the refusal exists to
    prevent.
    """
    register_setting(
        key="library.a_device_this_machine_lacks",
        scope="app",
        default="cpu",
        choices=("cpu", "nvidia"),
        choice_labels=("Processor", "Graphics card"),
        section="Library",
        label="Device",
        help="Which device to use.",
        refuse=lambda value: (
            "There is no such card in this machine." if value == "nvidia" else None
        ),
    )

    # The value it CAN make is stored, so the refusal is about one answer rather than the setting.
    await service.apply(users.admin, {"library.a_device_this_machine_lacks": "cpu"})

    with pytest.raises(SettingError, match="no such card"):
        await service.apply(users.admin, {"library.a_device_this_machine_lacks": "nvidia"})

    # And nothing was written: a refused write leaves what was there.
    assert await service.get_app("library.a_device_this_machine_lacks") == "cpu"


async def test_a_refusal_that_waits_on_the_machine_leaves_the_server_answering(
    service: SettingsService, users: Users, registered: None
) -> None:
    """Asking which devices this machine can drive starts a process; the loop answers meanwhile."""
    import asyncio
    import time

    def slow(value: object) -> str | None:
        time.sleep(0.5)
        return None

    register_setting(
        key="library.a_device_asked_slowly",
        scope="app",
        default="cpu",
        choices=("cpu", "nvidia"),
        choice_labels=("Processor", "Graphics card"),
        section="Library",
        label="Device",
        help="Which device to use.",
        refuse=slow,
    )
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticking = asyncio.create_task(tick())
    try:
        await service.apply(users.admin, {"library.a_device_asked_slowly": "nvidia"})
    finally:
        ticking.cancel()

    assert ticks >= 20, f"the loop answered {ticks} times in half a second"


# --- a setting that changes what the user may SEE ---------------------------------------------


async def test_a_visibility_setting_rings_the_library_bell_as_well_as_the_settings_one(
    service: SettingsService, users: Users
) -> None:
    """Otherwise the change is real on the server and invisible in the browser until a reload.

    The screens holding a scoped list listen for `library` and deliberately not for `settings`:
    the grid re-reading every time anybody nudges a slider would be most of the cost of polling.
    So a preference that decides what a scoped read RETURNS has to say so, and `vault.concealment`
    is the one that does: without it, turning "Show a locked tile" off would leave the
    placeholders on screen until somebody navigated, which reads exactly like the setting not
    working.
    """
    made = ChangeBus()
    changes.listens(made)
    try:
        watching = made.subscribe(users.guest_a.id)
        await service.apply(users.guest_a, {CONCEALMENT_KEY: "placeholder"})
        assert set(watching.take(as_admin=False).about) == {About.SETTINGS, About.LIBRARY}
    finally:
        changes.listens(None)


async def test_an_ordinary_preference_rings_only_the_settings_bell(
    service: SettingsService, users: Users
) -> None:
    """The half that keeps the rule narrow, and the reason the flag exists rather than a blanket.

    Without this the rule could become "every settings write re-reads every list in the application",
    which is the poll the whole live feed exists instead of, and nothing in the suite would say so.
    """
    made = ChangeBus()
    changes.listens(made)
    try:
        watching = made.subscribe(users.guest_a.id)
        await service.apply(users.guest_a, {LOOP_KEY: "once"})
        assert watching.take(as_admin=False).about == (About.SETTINGS,)
    finally:
        changes.listens(None)


# --- Sift saving for itself -----------------------------------------------------------------


async def test_a_batch_sift_saves_moves_what_differs_and_writes_one_receipt(
    service: SettingsService, temp_db: Database
) -> None:
    """Sift's own save goes through the checks a press does, and the line it leaves says each
    value before and after, which is what its Undo reads. A batch that moves nothing writes
    nothing and says so."""
    receipt = await service.apply_as_sift(
        {GUEST_SAVE_KEY: True}, via="benchmark", queue="settings.sift", title="t", detail="d"
    )

    assert receipt is not None
    assert await service.get_app(GUEST_SAVE_KEY) is True
    row = await temp_db.fetch_one(
        "SELECT actor_kind, actor_id, payload FROM workbench_decisions WHERE id = ?", (receipt,)
    )
    assert row is not None and (row["actor_kind"], row["actor_id"]) == ("sift", "benchmark")
    (change,) = json.loads(row["payload"])["changes"]
    assert (change["key"], change["before"], change["after"]) == (GUEST_SAVE_KEY, "false", "true")

    again = await service.apply_as_sift(
        {GUEST_SAVE_KEY: True}, via="benchmark", queue="settings.sift", title="t", detail="d"
    )

    assert again is None


async def test_sift_never_saves_a_personal_setting(service: SettingsService) -> None:
    with pytest.raises(ScopeForbidden):
        await service.apply_as_sift(
            {LOOP_KEY: "once", GUEST_SAVE_KEY: True},
            via="benchmark",
            queue="settings.sift",
            title="t",
            detail="d",
        )

    assert await service.app_is_stored(GUEST_SAVE_KEY) is False


async def test_a_tunnel_setting_is_written_down_by_the_tunnels_name_and_never_its_id(
    service: SettingsService, users: Users, temp_db: Database
) -> None:
    from sift.kernel.access import sentences as say

    def route(value: object) -> object:
        return None if value in (None, "direct") else value

    register_setting(
        key="t.join_tunnel",
        scope="app",
        default="",
        validator=str,
        section="Sites and Tunnels",
        label="Tunnel for joining a swap",
        help="h",
        names_a_tunnel=True,
    )
    register_setting(
        key="t.lookup_route",
        scope="app",
        default=None,
        choices=("direct",),
        choice_labels=("Direct",),
        validator=route,
        section="Music",
        label="Connect to AcoustID through",
        help="h",
        names_a_tunnel=True,
    )
    for tunnel, name in (("tun-home-0001", "Home VPN"), ("tun-away-0002", "Away VPN")):
        await temp_db.execute(
            "INSERT INTO tunnels (id, name, enabled, created_at, updated_at)"
            " VALUES (?, ?, 0, 0, 0)",
            (tunnel, name),
        )
    for key in ("t.join_tunnel", "t.lookup_route"):
        await service.apply(users.admin, {key: "tun-home-0001"})
        await service.apply(users.admin, {key: "tun-away-0002"})
    await temp_db.execute("DELETE FROM tunnels WHERE id = 'tun-away-0002'")
    await service.apply(users.admin, {"t.lookup_route": "direct"})

    rows = await temp_db.fetch_all(
        "SELECT s.name AS name, d.payload AS payload FROM workbench_decision_subjects s"
        " JOIN workbench_decisions d ON d.id = s.decision_id WHERE d.verb = 'edited'"
        " ORDER BY d.rowid"
    )
    lines = [
        say.text_of(say.setting_changed("You", say.Piece(row["name"]), json.loads(row["payload"])))
        for row in rows
    ]
    assert lines == [
        "You changed Tunnel for joining a swap to Home VPN",
        "You changed Tunnel for joining a swap from Home VPN to Away VPN",
        "You changed Connect to AcoustID through from Direct to Home VPN",
        "You changed Connect to AcoustID through from Home VPN to Away VPN",
        "You changed Connect to AcoustID through from a tunnel since removed to Direct",
    ]
    assert not [line for line in lines if "tun-" in line]


def test_both_settings_that_hold_a_tunnel_say_so() -> None:
    for key in (LOOKUP_ROUTE_KEY, GUEST_TUNNEL_KEY):
        declared = get_registered(key)
        assert declared is not None and declared.names_a_tunnel
