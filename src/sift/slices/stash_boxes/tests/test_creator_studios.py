# SPDX-License-Identifier: AGPL-3.0-or-later
"""A creator's own studio as her username: the repair, the question, the Undo, the next lookup.

The rule is the kernel's and its cases are held there (`kernel/tests/test_creator_studios.py`).
What is held here is what it does to a library: the catalog step moves a Site a box made of one
creator's store to her username and says so once with an Undo that puts the Site back whole; a
Site somebody filed a file under by hand keeps that file; a studio whose signs are thin is a
question on the stash-box page, and both answers are remembered; and a lookup still running files
the studio's next scene under her username rather than making the Site again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from typing import Any, cast

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role, Viewer
from sift.kernel.access.catalog import MADE_BY_A_PERSON, by_sift, create_person_on
from sift.kernel.access.creator_studios import (
    QUEUE,
    Verdict,
    account_for,
    put_back,
    read_studio,
    repair,
    signs_of,
    turn_into_username,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.vocabulary import VIA_UPDATE
from sift.kernel.workbench import DOER, Band, Named, Recorded
from sift.slices.stash_boxes.enrich import AssetWriter
from sift.slices.stash_boxes.queue import StudioQueue
from sift.slices.stash_boxes.service import StashBoxService  # noqa: F401  (its schema)
from sift.slices.stash_boxes.studios import CreatorStudios
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.anyio

HER = "Esme Wrenfield"
STORE = "https://www.manyvids.com/Profile/1001/Esme-Wrenfield/Store/Videos/"
SOCIAL = "https://x.com/esmewrenfield"
A_BOX = "box-1"
HER_SITE = "site-her"
ADMIN = Viewer(id="admin", role=Role.ADMIN)

#: Everything a move touches, read whole, so "put back whole" is a comparison of two of these.
TABLES = (
    "SELECT * FROM sites ORDER BY id",
    "SELECT * FROM site_aliases ORDER BY id",
    "SELECT * FROM site_links ORDER BY id",
    "SELECT * FROM site_stash_box_links ORDER BY site_id",
    "SELECT id, site_id, name, url, person_id FROM usernames ORDER BY id",
    "SELECT * FROM asset_usernames ORDER BY asset_id, username_id",
)


async def _whole(db: Database) -> list[list[dict[str, object]]]:
    return [[dict(row) for row in await db.fetch_all(sql)] for sql in TABLES]


def _scene(*people: str, site: str = HER) -> str:
    return json.dumps([{"fields": {"site": site, "people": list(people)}}])


async def _a_site(
    db: Database,
    site_id: str,
    name: str,
    *,
    links: tuple[str, ...] = (STORE, SOCIAL),
    scenes: tuple[tuple[str, ...], ...] = (),
    by_hand: int = 0,
) -> None:
    """A Site a box made, its names and links, and files the box filed under it with its answers."""
    async with db.write() as connection:
        await connection.execute(
            "INSERT INTO sites (id, name, name_sort, created_at, created_by_kind, created_by_via,"
            " created_by_box_id, cover_upload_id) VALUES (?, ?, ?, 1, 'box', 'stash', ?, 'pic-1')",
            (site_id, name, name.casefold(), A_BOX),
        )
        await connection.execute(
            "INSERT INTO site_aliases (id, site_id, alias, alias_sort, added_at)"
            " VALUES (?, ?, 'esmewrenfield clips', 'esmewrenfield clips', 2)",
            (f"{site_id}-alias", site_id),
        )
        for at, url in enumerate(links):
            await connection.execute(
                "INSERT INTO site_links (id, site_id, url, created_at) VALUES (?, ?, ?, 3)",
                (f"{site_id}-link-{at}", site_id, url),
            )
        await connection.execute(
            "INSERT INTO site_stash_box_links (site_id, box_id, remote_id, payload, fetched_at)"
            " VALUES (?, ?, 'st-9', '[]', 4)",
            (site_id, A_BOX),
        )
        await connection.execute(
            "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
            " VALUES (?, ?, '', '', 5)",
            (f"{site_id}-nameless", site_id),
        )
        for at, people in enumerate(scenes):
            asset_id = f"{site_id}-file-{at}"
            await connection.execute(
                "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
                (asset_id, asset_id),
            )
            await connection.execute(
                "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at, box_id)"
                " VALUES (?, ?, 'stash_box', 6, ?)",
                (asset_id, f"{site_id}-nameless", A_BOX),
            )
            await connection.execute(
                "INSERT INTO asset_stash_box_matches"
                " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
                " VALUES (?, ?, ?, ?, 'certain', 'applied', 7)",
                (asset_id, A_BOX, f"scene-{at}", _scene(*people, site=name)),
            )
        for at in range(by_hand):
            asset_id = f"{site_id}-by-hand-{at}"
            await connection.execute(
                "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
                (asset_id, asset_id),
            )
            await connection.execute(
                "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
                " VALUES (?, ?, NULL, 8)",
                (asset_id, f"{site_id}-nameless"),
            )


@pytest.fixture
async def db(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
            " VALUES (?, 'StashDB', 'https://stashdb.example/graphql', 0)",
            (A_BOX,),
        )
        await create_person_on(connection, HER, made=MADE_BY_A_PERSON)
    return temp_db


async def _repaired(db: Database) -> dict[str, int]:
    async with db.write() as connection:
        return await repair(connection)


async def _her_username(db: Database) -> Mapping[str, object] | None:
    row = await db.fetch_one(
        "SELECT u.id, u.name, s.name AS site, u.url, u.person_id FROM usernames u"
        " JOIN sites s ON s.id = u.site_id WHERE u.name = 'Esme-Wrenfield'"
    )
    return None if row is None else dict(row)


async def _files_under(db: Database, username_id: object) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM asset_usernames WHERE username_id = ?", (username_id,)
    )
    return 0 if row is None else int(row["n"])


async def test_the_repair_moves_her_store_to_her_username_with_one_line_and_an_undo(
    db: Database,
) -> None:
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 4 + ((HER, "Wren Halloway"),))

    counts = await _repaired(db)

    assert counts == {"moved": 1, "files": 5, "kept": 0, "asked": 0, "removed": 1}
    assert await db.fetch_one("SELECT 1 FROM sites WHERE id = ?", (HER_SITE,)) is None
    username = await _her_username(db)
    assert username is not None
    assert (username["site"], username["url"]) == ("ManyVids", STORE)
    assert username["person_id"] is not None, "her username is not said to be her"
    assert await _files_under(db, username["id"]) == 5
    # The box that filed each one moves with it, so its lines still name the box.
    boxes = await db.fetch_all(
        "SELECT DISTINCT box_id FROM asset_usernames WHERE username_id = ?", (username["id"],)
    )
    assert [row["box_id"] for row in boxes] == [A_BOX]
    receipts = await db.fetch_all(
        "SELECT verb, actor_kind, actor_id, title FROM workbench_decisions WHERE queue = ?",
        (QUEUE,),
    )
    assert [dict(one) for one in receipts] == [
        {
            "verb": "moved",
            "actor_kind": "sift",
            "actor_id": "update",
            "title": f"{HER} is a username on ManyVids, not a Site",
        }
    ]


async def test_undo_puts_the_site_back_whole(db: Database) -> None:
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 3)
    before = await _whole(db)
    await _repaired(db)
    receipt = await db.fetch_one(
        "SELECT id, payload FROM workbench_decisions WHERE queue = ?", (QUEUE,)
    )
    assert receipt is not None
    queue = StudioQueue(CreatorStudios(db), _Access())  # type: ignore[arg-type]

    assert await queue.reverse(ADMIN, str(receipt["id"]), str(receipt["payload"]))

    assert await _whole(db) == before
    assert await account_for(db, HER) is None, "the answer outlived its Undo"


async def test_a_site_with_a_file_filed_by_hand_keeps_it_and_the_box_files_move(
    db: Database,
) -> None:
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 3, by_hand=1)

    counts = await _repaired(db)

    assert counts["moved"] == 1 and counts["removed"] == 0
    kept = await db.fetch_all(
        "SELECT au.asset_id, au.source FROM asset_usernames au"
        " JOIN usernames u ON u.id = au.username_id WHERE u.site_id = ?",
        (HER_SITE,),
    )
    assert [dict(one) for one in kept] == [{"asset_id": f"{HER_SITE}-by-hand-0", "source": None}]
    username = await _her_username(db)
    assert username is not None and await _files_under(db, username["id"]) == 3


async def test_a_producer_stays_a_site_and_a_thin_one_is_asked_and_answered_once(
    db: Database,
) -> None:
    await _a_site(db, "site-a", "Another Studio", scenes=(("Wren Halloway",),) * 4)
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 2 + (("Wren Halloway",),) * 2)

    counts = await _repaired(db)
    studios = CreatorStudios(db)
    queue = StudioQueue(studios, _Access())  # type: ignore[arg-type]

    assert counts == {"moved": 0, "files": 0, "kept": 1, "asked": 1, "removed": 0}
    asked = await studios.questions()
    assert [(one.name, one.files, one.scenes, one.credited) for one in asked] == [(HER, 4, 4, 2)]
    assert queue.band is Band.DECISION
    assert queue.group_title is None, "a tab of the stash-box page drew a card of its own"
    assert await queue.available()
    assert (await queue.survey(ADMIN)).count == 1
    admin = await create_user(db, Role.ADMIN)
    assert await studios.turn("site-a", admin.id) is None, "a Site that is no question moved"

    receipt = await studios.keep(HER_SITE, admin.id)

    assert receipt is not None
    assert await studios.questions() == []
    assert await studios.keep(HER_SITE, admin.id) is None, "asked twice"
    payload = await db.fetch_one("SELECT payload FROM workbench_decisions WHERE id = ?", (receipt,))
    assert payload is not None
    assert await queue.reverse(ADMIN, receipt, str(payload["payload"]))
    assert [one.name for one in await studios.questions()] == [HER], "Undo did not ask again"


async def test_yes_moves_a_question_to_her_username_as_the_repair_would(db: Database) -> None:
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 2 + (("Wren Halloway",),) * 2)
    await _repaired(db)

    admin = await create_user(db, Role.ADMIN)

    turned = await CreatorStudios(db).turn(HER_SITE, admin.id)

    assert turned is not None and turned.files == 4 and turned.site_gone
    actor = await db.fetch_one(
        "SELECT actor_kind, actor_id FROM workbench_decisions WHERE id = ?", (turned.receipt_id,)
    )
    assert actor is not None and dict(actor) == {"actor_kind": "user", "actor_id": admin.id}


async def test_a_lookup_still_running_files_her_next_scene_under_her_username(
    db: Database,
) -> None:
    """The Site is not made again: not from a scene that does not credit her, not from a kept one."""
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 3)
    await _repaired(db)
    filing, naming = _Filing(), _Naming(known={"ManyVids", "Wren Halloway"})
    writer = AssetWriter(
        content=_Content(),  # type: ignore[arg-type]
        filing=filing,  # type: ignore[arg-type]
        naming=naming,  # type: ignore[arg-type]
        reindex=_Reindex(),  # type: ignore[arg-type]
        studio_account=partial(account_for, db),
    )
    second = {"site": HER, "people": ["Wren Halloway"]}

    missing = await writer.missing(second)
    written = await writer.write(
        "a-later-file", second, creating=frozenset({("site", HER)}), actor=Actor.sift("stash")
    )

    assert missing == ()
    assert HER not in naming.made, "the Site was made again"
    assert filing.filed == []
    assert filing.usernames == [("ManyVids", "Esme-Wrenfield", STORE)]
    assert written.get("accounts") == 1 and "site" not in written


# --- doubles --------------------------------------------------------------------------------------


class _Access:
    async def get_asset(self, viewer: Viewer, asset_id: str) -> object:
        return object()

    async def visible_sites(self, viewer: Viewer, ids: list[str]) -> dict[str, object]:
        return {one: _Named(one) for one in ids}

    async def visible_people(self, viewer: Viewer, ids: list[str]) -> dict[str, object]:
        return {}

    async def visible_tags(self, viewer: Viewer, ids: list[str]) -> dict[str, object]:
        return {}


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


class _Content:
    async def get(self, asset_id: str) -> None:
        return None

    async def links_of(self, asset_id: str) -> tuple[str, ...]:
        return ()


class _Filing:
    def __init__(self) -> None:
        self.filed: list[str] = []
        self.usernames: list[tuple[str, str, str]] = []

    async def people_on(self, asset_id: str) -> tuple[str, ...]:
        return ()

    async def tags_on(self, asset_id: str) -> tuple[str, ...]:
        return ()

    async def site_of(self, asset_id: str) -> str | None:
        return None

    async def accounts_on(self, asset_id: str) -> tuple[dict[str, str], ...]:
        return ()

    async def attribute(
        self, asset_id: str, person_id: str, *, source: str, box_id: str | None = None
    ) -> None:
        return None

    async def file_under_site(
        self, asset_id: str, site: str, *, source: str, box_id: str | None = None
    ) -> None:
        self.filed.append(site)

    async def file_under_username(
        self,
        asset_id: str,
        *,
        site: str,
        handle: str,
        url: str | None,
        source: str,
        person_id: str | None = None,
        box_id: str | None = None,
    ) -> bool:
        self.usernames.append((site, handle, url or ""))
        return True


class _Naming:
    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.made: list[str] = []

    async def _named(self, name: str, *, creating: bool) -> str | None:
        if name in self.known:
            return f"id-{name}"
        if not creating:
            return None
        self.made.append(name)
        self.known.add(name)
        return f"id-{name}"

    async def person_named(self, name: str, *, creating: bool) -> str | None:
        return await self._named(name, creating=creating)

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        return await self._named(name, creating=creating)

    async def tag_named(self, name: str, *, creating: bool) -> str | None:
        return await self._named(name, creating=creating)


class _Reindex:
    async def touched(self, asset_id: str) -> None:
        return None


async def test_catalog_80_is_the_step_that_moves_them(db: Database) -> None:
    """A library at catalog 79 moves her store when the catalog is brought to 80."""
    from sift.kernel.access import schema

    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 2)
    async with db.write() as connection:
        await connection.execute("DROP TABLE creator_studios")
        await schema.initialize_catalog(connection, 79)

    assert schema.CATALOG_VERSION >= 80
    assert await db.fetch_one("SELECT 1 FROM sites WHERE id = ?", (HER_SITE,)) is None
    assert await account_for(db, HER) is not None


# --- the decision's line, worded when it is shown ---------------------------------------------


def _recorded(payload: dict[str, object]) -> Recorded:
    return Recorded(
        id="r-1", queue=QUEUE, payload=json.dumps(payload), title="", detail="", decided_at=1
    )


def test_a_move_is_worded_from_what_it_recorded_and_names_the_site_by_its_kind() -> None:
    """The Site is gone once its files moved, so the line says it by the name it had, with its
    kind before the name, and links the Site the username is on."""
    queue = StudioQueue(cast(Any, None), cast(Any, None))

    worded = queue.worded(
        _recorded(
            {"kind": "turned", "site_id": "s-1", "site_name": "Cedar Vale", "host_id": "host-1"}
        )
    )

    assert worded is not None
    doer, filed, site, on, host = worded.said
    assert doer is DOER
    assert (filed, on) == (" filed ", " as a username on ")
    assert site == Named(kind="site", id="s-1", recorded="Cedar Vale", kind_said=True)
    assert host == Named(kind="site", id="host-1")


def test_a_kept_site_is_worded_and_an_unreadable_record_keeps_its_stored_title() -> None:
    queue = StudioQueue(cast(Any, None), cast(Any, None))

    kept = queue.worded(_recorded({"kind": "kept", "site_id": "s-2", "site_name": "Cedar Vale"}))

    assert kept is not None
    assert kept.said == (
        DOER,
        " kept ",
        Named(kind="site", id="s-2", recorded="Cedar Vale"),
        " as a Site",
    )
    # Nothing to word a line from: the row keeps the title it was stored with.
    assert queue.worded(_recorded({"kind": "turned", "site_name": "Cedar Vale"})) is None
    assert queue.worded(_recorded({"kind": "kept"})) is None
    assert queue.worded(_recorded({})) is None


async def test_a_repairs_line_draws_the_files_it_moved_that_the_reader_may_see() -> None:
    """Each still links to its file, and a file the reader may not be shown is left out."""

    class _Shows:
        async def get_asset(self, viewer: Viewer, asset_id: str) -> object | None:
            return None if asset_id == "kept-from-them" else object()

    queue = StudioQueue(cast(Any, None), cast(Any, _Shows()))

    shown = await queue.pictures_of(ADMIN, json.dumps({"moved": ["a1", "kept-from-them", "a2"]}))

    assert [(one.id, one.href) for one in shown] == [("a1", "/asset/a1"), ("a2", "/asset/a2")]
    assert await queue.pictures_of(ADMIN, "{}") == ()


# --- the edges of a move and of its Undo ---------------------------------------------------------


async def _moved_payload(db: Database) -> str:
    row = await db.fetch_one(
        "SELECT payload FROM workbench_decisions WHERE queue = ? AND verb = 'moved'", (QUEUE,)
    )
    assert row is not None
    return str(row["payload"])


async def _files_on_site(db: Database, site_id: str) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM asset_usernames au JOIN usernames u ON u.id = au.username_id"
        " WHERE u.site_id = ?",
        (site_id,),
    )
    return 0 if row is None else int(row["n"])


async def test_an_answer_about_another_studio_or_one_unreadable_is_a_file_but_not_a_scene(
    db: Database,
) -> None:
    """The share is taken over scenes of THIS studio: an answer that names another studio, or that
    cannot be read, must not count against her (or for her)."""
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 2)
    odd = ("not json", "[]", _scene(HER, site="Another Studio"))
    async with db.write() as connection:
        for at, payload in enumerate(odd):
            asset_id = f"odd-{at}"
            await connection.execute(
                "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
                (asset_id, asset_id),
            )
            await connection.execute(
                "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
                " VALUES (?, ?, 'stash_box', 6)",
                (asset_id, f"{HER_SITE}-nameless"),
            )
            await connection.execute(
                "INSERT INTO asset_stash_box_matches"
                " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
                " VALUES (?, ?, ?, ?, 'certain', 'applied', 7)",
                (asset_id, A_BOX, f"odd-scene-{at}", payload),
            )
        signs = await signs_of(connection, HER_SITE)

    assert signs is not None
    assert (signs.files, signs.reading.scenes, signs.reading.credited) == (5, 2, 2)
    assert signs.reading.verdict is Verdict.USERNAME


async def test_a_site_gone_or_with_nothing_the_box_filed_has_nothing_to_move(db: Database) -> None:
    """Even read as hers, a Site with no nameless row or no box filings moves nothing."""
    await _a_site(db, HER_SITE, HER)
    hers = read_studio(HER, [], [STORE], [[HER]])
    async with db.write() as connection:
        assert await signs_of(connection, "no-such-site") is None
        await connection.execute(
            "INSERT INTO sites (id, name, name_sort, created_at, created_by_kind)"
            " VALUES ('bare', 'Cedar Vale', 'cedar vale', 1, 'box')"
        )
        bare = await signs_of(connection, "bare")
        filed = await signs_of(connection, HER_SITE)
        assert bare is not None and filed is not None
        assert (bare.nameless_id, bare.files, bare.reading.verdict) == (None, 0, Verdict.SITE)
        assert (filed.files, filed.reading.verdict) == (0, Verdict.SITE)
        for signs in (bare, filed):
            moved = await turn_into_username(
                connection,
                replace(signs, reading=hers),
                made=by_sift(VIA_UPDATE),
                actor=Actor.sift(VIA_UPDATE),
            )
            assert moved is None
    assert await db.fetch_one("SELECT 1 FROM sites WHERE id = ?", (HER_SITE,)) is not None
    assert await db.fetch_one("SELECT 1 FROM sites WHERE name = 'ManyVids'") is None


async def test_her_username_already_on_the_store_is_used_and_undo_leaves_it_as_it_was(
    db: Database,
) -> None:
    """Her username there is the one filed under (spelled as it is), the store Site is not given a
    second address, a file already filed under her stays, and with no one person of the studio's
    name nobody is said to be her. The Undo takes back only what the move did."""
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 3)
    async with db.write() as connection:
        await connection.execute("DELETE FROM people WHERE name = ?", (HER,))
        await connection.execute(
            "INSERT INTO sites (id, name, name_sort, created_at)"
            " VALUES ('mv', 'ManyVids', 'manyvids', 1)"
        )
        await connection.execute(
            "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
            " VALUES ('held', 'mv', 'esme-wrenfield', 'esme-wrenfield', 1)"
        )
        await connection.execute(
            "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
            " VALUES (?, 'held', NULL, 9)",
            (f"{HER_SITE}-file-0",),
        )

    counts = await _repaired(db)

    assert counts["moved"] == 1 and counts["files"] == 3
    on_store = await db.fetch_all(
        "SELECT id, name, url, person_id FROM usernames WHERE site_id = 'mv'"
    )
    assert [dict(one) for one in on_store] == [
        {"id": "held", "name": "esme-wrenfield", "url": STORE, "person_id": None}
    ]
    assert await _files_under(db, "held") == 3
    assert await db.fetch_one("SELECT 1 FROM site_links WHERE site_id = 'mv'") is None
    payload = await _moved_payload(db)
    recorded = json.loads(payload)
    assert recorded["moved"] == [f"{HER_SITE}-file-1", f"{HER_SITE}-file-2"]
    assert (recorded["username_made"], recorded["host_made"], recorded["person_linked"]) == (
        False,
        False,
        None,
    )

    assert await CreatorStudios(db).put_back(payload)

    assert await _files_on_site(db, HER_SITE) == 3
    assert await _files_under(db, "held") == 1, "the file she was already filed under was taken"
    assert await db.fetch_one("SELECT 1 FROM sites WHERE id = 'mv'") is not None


async def test_undo_of_a_move_that_left_the_site_standing_puts_the_files_back_on_it(
    db: Database,
) -> None:
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 3, by_hand=1)
    await _repaired(db)
    payload = await _moved_payload(db)
    assert "site" not in json.loads(payload), "a Site still standing was written down to put back"

    assert await CreatorStudios(db).put_back(payload)

    assert await _files_under(db, f"{HER_SITE}-nameless") == 4
    assert await _her_username(db) is None


async def test_undo_after_the_site_was_deleted_since_puts_nothing_back(db: Database) -> None:
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 3, by_hand=1)
    await _repaired(db)
    payload = await _moved_payload(db)
    async with db.write() as connection:
        await connection.execute("DELETE FROM sites WHERE id = ?", (HER_SITE,))

    assert not await CreatorStudios(db).put_back(payload)

    assert await db.fetch_one("SELECT 1 FROM sites WHERE name = ?", (HER,)) is None


async def test_undo_files_them_back_under_a_site_of_her_name_made_again_since(
    db: Database,
) -> None:
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 2)
    await _repaired(db)
    payload = await _moved_payload(db)
    async with db.write() as connection:
        await connection.execute(
            "INSERT INTO sites (id, name, name_sort, created_at) VALUES ('again', ?, ?, 9)",
            (HER, HER.casefold()),
        )
        # Somebody else's username on the store the move made: the store stays for it.
        await connection.execute(
            "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
            " SELECT 'other', id, 'wren-halloway', 'wren-halloway', 9 FROM sites"
            " WHERE name = 'ManyVids'"
        )

    assert await CreatorStudios(db).put_back(payload)

    assert await _her_username(db) is None
    assert await db.fetch_one("SELECT 1 FROM usernames WHERE id = 'other'") is not None
    assert await db.fetch_one("SELECT 1 FROM sites WHERE id = ?", (HER_SITE,)) is None
    assert await _files_on_site(db, "again") == 2
    aliases = await db.fetch_all("SELECT site_id FROM site_aliases")
    assert [str(one["site_id"]) for one in aliases] == ["again"]


async def test_a_site_somebody_shared_stays_and_a_table_never_made_is_passed_over(
    db: Database,
) -> None:
    """A grant on the Site is something a person put on it, so the Site is not removed; a table
    belonging to a feature this library never switched on is not asked about."""
    await _a_site(db, HER_SITE, HER, scenes=((HER,),) * 3)
    guest = await create_user(db, Role.GUEST)
    async with db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS watermark_reads")
        await connection.execute(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES ('g-1', 'site', ?, ?, 'share', 1)",
            (HER_SITE, guest.id),
        )

    counts = await _repaired(db)

    assert (counts["moved"], counts["removed"]) == (1, 0)
    assert await db.fetch_one("SELECT 1 FROM sites WHERE id = ?", (HER_SITE,)) is not None


async def test_an_undo_record_that_cannot_be_read_puts_nothing_back(db: Database) -> None:
    unreadable = (
        "not json",
        "[]",
        json.dumps({"kind": "turned"}),
        json.dumps({"kind": "renamed", "site_name": HER}),
    )
    async with db.write() as connection:
        for payload in unreadable:
            assert not await put_back(connection, payload), payload


async def test_a_blank_studio_has_no_username_and_a_library_with_no_ledger_moves_nothing(
    temp_db: Database,
) -> None:
    assert await account_for(temp_db, "  ") is None
    async with temp_db.write() as connection:
        counts = await repair(connection)
    assert counts == {"moved": 0, "files": 0, "kept": 0, "asked": 0, "removed": 0}
