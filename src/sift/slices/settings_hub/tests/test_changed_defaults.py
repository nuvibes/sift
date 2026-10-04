# SPDX-License-Identifier: AGPL-3.0-or-later
"""The first start of a release says on History which defaults its update changed.

One line per setting whose default changed since this library last started and that nobody chose,
named by the update and its release, with no Undo. A new library is told nothing, since no default
changed under anybody there, and a second start of the same release is told nothing again.
"""

from __future__ import annotations

import json

import pytest

# The lines are written into the ledger, whose table registers itself on this import.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role
from sift.kernel.access import sentences as say
from sift.kernel.db import Database
from sift.kernel.settings_registry import get_registered, register_setting
from sift.kernel.vocabulary import LEDGER_QUEUE
from sift.slices.settings_hub.defaults import tell_changed_defaults
from sift.slices.settings_hub.service import SettingsService
from sift.testing.fixtures import create_user

pytestmark = [pytest.mark.integration]

#: Written out: the releases a library in the world started as.
EARLIER = "0.1.218"
NOW = "0.1.219"

SHARED = "test.shared_switch"
OWN = "test.own_switch"
SETTLED = "test.settled_switch"
MIDDLE = "test.middle_switch"
LATER = "test.later_switch"


@pytest.fixture
def changed(clean_settings_registry: None) -> None:
    """Two defaults changed in the release being started, one of each scope; one in the release
    before it; one changed long ago; one changed in a release not started yet."""
    for key, scope, since in (
        (SHARED, "app", NOW),
        (OWN, "user", NOW),
        (MIDDLE, "app", EARLIER),
        (SETTLED, "app", "0.1.210"),
        (LATER, "app", "0.1.230"),
    ):
        register_setting(
            key=key,
            scope=scope,
            default=True,
            section="Privacy and Security",
            label=f"Switch {key}",
            help="A switch for the test.",
            default_since=since,
        )


async def _library_from(database: Database, release: str | None) -> None:
    """A library as a start of `release` left it; None is one from before the record began."""
    await database.initialize_schema()
    await create_user(database, Role.ADMIN)
    await database.execute("DELETE FROM version_booted")
    if release is not None:
        await database.execute("INSERT INTO version_booted (id, version) VALUES (1, ?)", (release,))


async def _told(database: Database) -> list[tuple[str, dict[str, object]]]:
    rows = await database.fetch_all(
        "SELECT s.subject_id, d.payload, d.actor_kind, d.actor_id, d.queue"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'edited' AND s.kind = 'setting' ORDER BY s.subject_id"
    )
    for row in rows:
        assert (row["actor_kind"], row["actor_id"]) == ("sift", "update")
        # Not a receipt: a default is not a decision, so its line has no Undo.
        assert row["queue"] == LEDGER_QUEUE
    return [(str(row["subject_id"]), json.loads(str(row["payload"]))) for row in rows]


async def test_a_library_from_the_release_before_is_told_each_changed_default(
    temp_db: Database, changed: None
) -> None:
    await _library_from(temp_db, EARLIER)

    assert await tell_changed_defaults(temp_db, NOW) == 2

    told = await _told(temp_db)
    assert [key for key, _ in told] == sorted([OWN, SHARED])
    payload = told[0][1]
    assert payload["update_to"] == NOW
    line = say.text_of(say.setting_changed("Sift", say.Piece(f"Switch {OWN}"), payload))
    assert line == f"The update to {NOW} changed Switch {OWN} to on"


async def test_a_library_that_never_recorded_a_start_reads_as_the_release_before_the_record(
    temp_db: Database, changed: None
) -> None:
    await _library_from(temp_db, None)

    assert await tell_changed_defaults(temp_db, NOW) == 3

    assert {key for key, _ in await _told(temp_db)} == {OWN, SHARED, MIDDLE}


async def test_a_chosen_value_is_not_told_and_a_second_start_tells_nothing(
    temp_db: Database, changed: None
) -> None:
    await _library_from(temp_db, EARLIER)
    await temp_db.execute("INSERT INTO app_settings (key, value) VALUES (?, 'false')", (SHARED,))

    assert await tell_changed_defaults(temp_db, NOW) == 1
    assert await tell_changed_defaults(temp_db, NOW) == 0

    assert [key for key, _ in await _told(temp_db)] == [OWN]
    booted = await temp_db.fetch_all("SELECT version FROM version_booted")
    assert [str(row["version"]) for row in booted] == [NOW]


async def test_a_new_library_is_told_nothing(
    temp_db: Database, changed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sift.slices.settings_hub.schema as schema

    monkeypatch.setattr(schema, "app_version", lambda: NOW)
    await temp_db.initialize_schema()
    await create_user(temp_db, Role.ADMIN)

    assert await SettingsService(temp_db).tell_changed_defaults(NOW) == 0
    assert await _told(temp_db) == []


async def test_an_unreadable_release_writes_nothing(temp_db: Database, changed: None) -> None:
    await _library_from(temp_db, EARLIER)

    assert await tell_changed_defaults(temp_db, "") == 0
    assert await _told(temp_db) == []


def test_the_defaults_the_update_to_0_1_218_changed_say_so() -> None:
    """Show every field on a record and Create people from these fingerprints went from off to on,
    and the log, which hid personal details always, has a switch for it that is off."""
    import sift.kernel.log_settings
    import sift.slices.faces.settings
    import sift.slices.stash_boxes.settings  # noqa: F401

    for key in ("records.show_every_field", "faces.people_from_files", "logs.hide_personal"):
        declared = get_registered(key)
        assert declared is not None, key
        assert declared.default_since == "0.1.218", key


def test_a_default_since_that_is_not_a_release_is_refused(clean_settings_registry: None) -> None:
    from sift.kernel.settings_registry import SettingError

    with pytest.raises(SettingError, match="not a release"):
        register_setting(
            key="test.bad_since",
            scope="app",
            default=True,
            section="Privacy and Security",
            label="Bad",
            help="A switch for the test.",
            default_since="soon",
        )
