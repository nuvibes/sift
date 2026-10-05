# SPDX-License-Identifier: AGPL-3.0-or-later
"""Over HTTP: a guest's History, box lines and tag lists name nothing they may not be shown."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.vocabulary import Subject
from sift.slices.people.tests.conftest import Library, db_path, share, sign_in, write
from sift.testing.auth import TEST_PIN

pytestmark = pytest.mark.integration

RUN_AT = 1_700_000_500


@dataclass(frozen=True, slots=True)
class Things:
    site: str
    person: str
    tag: str
    network: str
    hidden_tag: str
    joined: str


def _kept(box: str, subject: str, name: str, fields: dict[str, object]) -> str:
    return json.dumps(
        [{"source_id": box, "remote_id": "r1", "subject": subject, "name": name, "fields": fields}]
    )


def _linked_event(path: Path, admin: str, joined: str, person: str) -> None:
    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                made = await record_event(
                    connection,
                    actor=Actor.user(admin),
                    verb="linked",
                    subject=Subject(kind="username", id=joined, name="jettyone"),
                    object=Object(kind="person", id=person, name="Delphine Ostrow"),
                )
                await connection.execute(
                    "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (RUN_AT, made)
                )
        finally:
            await database.close()

    asyncio.run(run())


def seeded(client: TestClient, library: Library, admin: str) -> Things:
    """A Site, person and tag on the shared file, each filled by a box naming a hidden Site, tag
    and username; a second box linked to the Site before runs were recorded."""
    one = Things(new_id(), new_id(), new_id(), new_id(), new_id(), new_id())
    box, older, username = new_id(), new_id(), new_id()
    link = "INSERT INTO {}_stash_box_links ({}_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r1', ?, ?)"
    run = "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied) VALUES (?, ?, ?, ?, ?, 0, ?)"
    write(
        db_path(client),
        [
            ("INSERT INTO sites (id, name) VALUES (?, 'Studio')", (one.site,)),
            ("INSERT INTO sites (id, name) VALUES (?, 'Harbour Network')", (one.network,)),
            (
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'studioone', 0)",
                (username, one.site),
            ),
            (
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'jettyone', 0)",
                (one.joined, one.network),
            ),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (library.shared, username),
            ),
            (
                "INSERT INTO people (id, name, created_at) VALUES (?, 'Delphine Ostrow', 0)",
                (one.person,),
            ),
            (
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                (library.shared, one.person),
            ),
            (
                "INSERT INTO tags (id, name, created_at) VALUES (?, 'poolside', 0)",
                (one.hidden_tag,),
            ),
            (
                "INSERT INTO tags (id, name, parent_id, created_at) VALUES (?, 'seagrass', ?, 0)",
                (one.tag, one.hidden_tag),
            ),
            (
                "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                (library.shared, one.tag),
            ),
            (
                "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                (library.private, one.hidden_tag),
            ),
            (
                "INSERT INTO site_tags (site_id, tag_id, added_at) VALUES (?, ?, ?)",
                (one.site, one.hidden_tag, RUN_AT),
            ),
            (
                "INSERT INTO site_tags (site_id, tag_id, added_at) VALUES (?, ?, ?)",
                (one.site, one.tag, RUN_AT),
            ),
            (
                "INSERT INTO person_tags (person_id, tag_id, added_at) VALUES (?, ?, ?)",
                (one.person, one.hidden_tag, RUN_AT),
            ),
            (
                "INSERT INTO person_tags (person_id, tag_id, added_at) VALUES (?, ?, ?)",
                (one.person, one.tag, RUN_AT),
            ),
            (
                "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'StashDB', ?, 0)",
                (box, "https://stashdb.example/graphql"),
            ),
            (
                "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, 0)",
                (older, "https://fansdb.example/graphql"),
            ),
            (
                link.format("site", "site"),
                (
                    one.site,
                    box,
                    _kept(
                        box, "site", "Studio", {"parent": "harbour network", "tags": ["poolside"]}
                    ),
                    RUN_AT,
                ),
            ),
            (
                link.format("site", "site"),
                (one.site, older, _kept(older, "site", "Studio", {"tags": ["poolside"]}), RUN_AT),
            ),
            (
                link.format("person", "person"),
                (
                    one.person,
                    box,
                    _kept(box, "person", "Delphine Ostrow", {"tags": ["poolside"]}),
                    RUN_AT,
                ),
            ),
            (
                link.format("tag", "tag"),
                (
                    one.tag,
                    box,
                    _kept(box, "tag", "seagrass", {"parent": "harbour network"}),
                    RUN_AT,
                ),
            ),
            (run, (new_id(), "site", one.site, box, RUN_AT, '{"parent": 1, "tags": 1}')),
            (run, (new_id(), "person", one.person, box, RUN_AT, '{"accounts": 1, "tags": 1}')),
            (run, (new_id(), "tag", one.tag, box, RUN_AT, '{"parent": 1}')),
        ],
    )
    _linked_event(db_path(client), admin, one.joined, one.person)
    return one


def _answers(client: TestClient, one: Things, library: Library) -> dict[str, str]:
    reads = {
        "site": f"/api/sites/{one.site}/history",
        "person": f"/api/people/{one.person}/history",
        "tag": f"/api/tags/{one.tag}/history",
        "linked": "/api/stash-boxes/linked",
        "person tags": f"/api/people/{one.person}/tags",
        "site tags": f"/api/sites/{one.site}/tags",
        "tag record": f"/api/tags/{one.tag}",
        "file": f"/api/assets/{library.shared}/history",
    }
    out: dict[str, str] = {}
    for name, url in reads.items():
        answer = client.get(url)
        assert answer.status_code == 200, (name, answer.text)
        out[name] = answer.text
    return out


def _unlock(client: TestClient) -> None:
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200


def _as_guest(client: TestClient, library: Library) -> tuple[Things, str]:
    admin = sign_in(client)
    one = seeded(client, library, admin)
    share(client, library.shared, sign_in(client, role="guest", who="two"))
    return one, admin


_UNSHOWN_WORDS = ("Harbour Network", "poolside", "jettyone")


@pytest.mark.parametrize("vault_open", [False, True])
def test_a_guest_is_told_no_name_or_id_of_what_they_may_not_be_shown(
    client: TestClient, library: Library, vault_open: bool
) -> None:
    one, _admin = _as_guest(client, library)
    if vault_open:
        _unlock(client)
    answers = _answers(client, one, library)
    for name, text in answers.items():
        for hidden in (one.network, one.hidden_tag, one.joined, *_UNSHOWN_WORDS):
            assert hidden not in text, (name, hidden)
    for name in ("site", "tag", "linked"):
        assert "harbour network" in answers[name], name
    for name in ("site", "person", "linked"):
        assert "a tag" in answers[name], name
    assert "a username" in answers["person"]
    assert "seagrass" in answers["person tags"] and "seagrass" in answers["site tags"]


@pytest.mark.parametrize("vault_open", [False, True])
def test_an_admin_is_told_every_name_and_id(
    client: TestClient, library: Library, vault_open: bool
) -> None:
    one = seeded(client, library, sign_in(client))
    if vault_open:
        _unlock(client)
    answers = _answers(client, one, library)
    for name in ("site", "tag", "linked"):
        assert one.network in answers[name] and "Harbour Network" in answers[name], name
    for name in ("site", "person", "linked", "person tags", "site tags", "tag record"):
        assert one.hidden_tag in answers[name] and "poolside" in answers[name], name
    assert one.joined in answers["person"] and "jettyone" in answers["person"]


def test_a_nameless_thing_is_said_by_its_kind_once(client: TestClient, library: Library) -> None:
    one, admin = _as_guest(client, library)
    asyncio.run(_unlinked_from_the_file(db_path(client), admin, library.shared, one.hidden_tag))
    answers = _answers(client, one, library)
    assert "removed a tag from this file" in answers["file"]
    for text in answers.values():
        assert "the username a username" not in text and "the tag a tag" not in text


async def _unlinked_from_the_file(path: Path, admin: str, asset: str, tag: str) -> None:
    database = Database(path, readers=1)
    await database.connect()
    try:
        async with database.write() as connection:
            await record_event(
                connection,
                actor=Actor.user(admin),
                verb="unlinked",
                subject=Subject(kind="asset", id=asset),
                object=Object(kind="tag", id=tag, name="poolside"),
            )
    finally:
        await database.close()
