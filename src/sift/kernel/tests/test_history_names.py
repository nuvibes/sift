# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a thing an older event named is called now; a thing with no name left keeps its kind."""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository
from sift.kernel.access.history_names import names_now
from sift.kernel.db import Database

pytestmark = pytest.mark.anyio


async def test_a_thing_with_no_name_to_give_is_left_out_and_a_kind_with_none_is_not_asked(
    temp_db: Database, access: Repository
) -> None:
    """A file with no title, no location left and no name it arrived with has nothing to be called;
    a kind the ledger names nothing of, and a kind asked about with no ids, are not read at all."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a-1', 'd-1', 'video', 0)"
    )
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at, original_filename)"
        " VALUES ('a-2', 'd-2', 'video', 0, 'clip.mp4')"
    )

    named = await names_now(temp_db, {"asset": ["a-1", "a-2"], "box": ["b-1"], "tag": []})

    assert named == {("asset", "a-2"): "clip.mp4"}


async def test_an_older_tunnel_setting_line_is_said_by_the_tunnels_name_now(
    temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from types import SimpleNamespace

    import sift.kernel.tunnels  # noqa: F401 (its table registers on import)
    from sift.kernel import settings_registry
    from sift.kernel.access.history_events import LedgerEvent
    from sift.kernel.access.history_names import objects_named

    monkeypatch.setitem(
        settings_registry._REGISTRY,
        "t.route",
        SimpleNamespace(choice_labels=("Direct",), names_a_tunnel=True),
    )
    monkeypatch.setitem(
        settings_registry._REGISTRY,
        "t.volume",
        SimpleNamespace(choice_labels=None, names_a_tunnel=False),
    )
    await temp_db.initialize_schema()
    await temp_db.execute(
        "INSERT INTO tunnels (id, name, enabled, created_at, updated_at)"
        " VALUES ('tun-1', 'Home VPN', 0, 0, 0)"
    )

    def edited(event_id: str, verb: str = "edited", **payload: object) -> LedgerEvent:
        return LedgerEvent(
            id=event_id,
            at=0,
            verb=verb,
            actor_kind="user",
            actor_id="u-1",
            user_id=None,
            object=None,
            count=None,
            queue="ledger",
            payload=json.dumps(payload),
            title="",
            detail="",
            reversed_at=None,
        )

    events = [
        edited("e-1", key="t.route", before="null", after='"tun-1"'),
        edited("e-2", key="t.route", before='"tun-1"', after='"tun-gone"'),
        edited("e-3", key="t.route", before="null", after='"tun-1"', after_said="Kept"),
        edited("e-4", key="t.volume", before='"tun-1"', after='"loud"'),
        edited("e-5", verb="renamed", key="t.route", after='"tun-1"'),
        edited("e-6", key="t.route", before="tun-1", after='"tun-1"'),
    ]

    said = [json.loads(one.payload) for one in await objects_named(temp_db, events)]

    assert [(one.get("before_said"), one.get("after_said")) for one in said] == [
        ("Direct", "Home VPN"),
        ("Home VPN", "a tunnel since removed"),
        (None, "Kept"),
        (None, None),
        (None, None),
        ("Direct", "Home VPN"),
    ]
