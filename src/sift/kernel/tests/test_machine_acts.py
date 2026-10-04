# SPDX-License-Identifier: AGPL-3.0-or-later
"""An act on the computer running Sift, written into History: who asked, and from which device.

Against a real database, because the line is a row of the record read back by the one builder the
Settings feed says it with. Each verb is written and said once; the device's words are held to a
name's shape before any line may say them.
"""

from __future__ import annotations

import json

import pytest

import sift.slices.workbench.schema  # noqa: F401 (its tables hold the record)
from sift.kernel import machine_acts
from sift.kernel.access import Role
from sift.kernel.access import sentences as say
from sift.kernel.db import Database
from sift.kernel.ledger import VERBS
from sift.kernel.machine_acts import act_payload, device_words, record_act
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

#: Every act that changes the computer running Sift, and nothing else.
ACTS = sorted(say.MACHINE_ACTS)


@pytest.fixture
async def database(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    return temp_db


def test_every_act_is_a_verb_the_ledger_knows() -> None:
    assert say.MACHINE_ACTS <= VERBS
    assert len(ACTS) == 9


@pytest.mark.parametrize(
    ("asked", "kept"),
    [
        (None, None),
        ("LAPTOP-TWO", "LAPTOP-TWO"),
        ("  LAPTOP-TWO\n", "LAPTOP-TWO"),
        ("", None),
        ("{by}", None),
        ("x" * 200, "x" * machine_acts.DEVICE_LONGEST),
        ("\x00\x07", None),
    ],
    ids=["none", "a name", "trimmed", "empty", "a slot", "too long", "unprintable"],
)
def test_a_device_is_kept_only_as_a_name(asked: str | None, kept: str | None) -> None:
    assert device_words(asked) == kept


def test_the_computer_itself_is_said_as_its_own_screen() -> None:
    here = json.loads(act_payload("desk-one", "DESK-ONE"))
    elsewhere = json.loads(act_payload("LAPTOP-TWO", "DESK-ONE", version="0.1.300"))
    browser = json.loads(act_payload(None, "DESK-ONE"))

    assert here == {"from": None, "here": True}
    assert elsewhere == {"from": "LAPTOP-TWO", "here": False, "version": "0.1.300"}
    assert browser == {"from": None, "here": False}


@pytest.mark.parametrize("verb", ACTS)
async def test_each_act_writes_one_line_naming_who_and_from_which_device(
    database: Database, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    monkeypatch.setattr(machine_acts, "machine_name", lambda: "DESK-ONE")
    admin = await create_user(database, Role.ADMIN)

    await record_act(database, admin, verb, "LAPTOP-TWO")

    row = await database.fetch_one(
        "SELECT verb, actor_kind, actor_id, user_id, payload, queue FROM workbench_decisions",
        (),
    )
    assert row is not None
    assert (row["verb"], row["actor_kind"], row["actor_id"], row["user_id"]) == (
        verb,
        "user",
        admin.id,
        admin.id,
    )
    assert row["queue"] == "ledger", "an act on a machine is not a card anybody can undo"
    assert json.loads(row["payload"]) == {"from": "LAPTOP-TWO", "here": False}
    subject = await database.fetch_one(
        "SELECT kind, subject_id, name FROM workbench_decision_subjects", ()
    )
    assert subject is not None
    assert (subject["kind"], subject["subject_id"], subject["name"]) == (
        "computer",
        "DESK-ONE",
        "DESK-ONE",
    )


async def test_a_machine_with_no_name_is_still_the_computer_running_sift(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(machine_acts, "machine_name", lambda: None)
    admin = await create_user(database, Role.ADMIN)

    await record_act(database, admin, "restarted", None)

    subject = await database.fetch_one(
        "SELECT subject_id, name FROM workbench_decision_subjects", ()
    )
    assert subject is not None and (subject["subject_id"], subject["name"]) == (
        machine_acts.UNNAMED,
        None,
    )
    line = say.text_of(
        say.feed_line(
            "restarted", by=say.YOU, subjects=[], payload={"from": None, "here": False}
        ).pieces
    )
    assert line == "You restarted Sift on the computer running Sift, from another computer"
