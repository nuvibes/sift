# SPDX-License-Identifier: AGPL-3.0-or-later
"""A facial fingerprints file is an edition of an earlier one from the same library, never of one
that merely shares its name."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.slices.faces import packs
from sift.slices.faces import schema as faces_schema
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests import test_routes
from sift.slices.faces.tests.test_export_waiting import MODEL, _entry, _pack
from sift.slices.faces.tests.test_packs import RECOGNISER, _as_edition, a_pack
from sift.slices.faces.tests.test_routes import db_path, sign_in, turn_on, write

pytestmark = pytest.mark.integration

app = test_routes.app
client = test_routes.client


def _file(people: list[str], *, library: str | None, name: str = "Sift") -> bytes:
    return packs.build(
        name=name,
        version="1",
        recognizer=MODEL.revision,
        dimension=MODEL.dimension,
        people=[
            packs.PackedPerson(
                name=one,
                aliases=(),
                links=(),
                faces=(
                    packs.PackedFace(
                        digest=one,
                        quality=0.9,
                        vector=tuple([1.0] + [0.0] * (MODEL.dimension - 1)),
                        picture=None,
                    ),
                ),
            )
            for one in people
        ],
        include_pictures=False,
        library=library,
    )


def _take(client: TestClient, raw: bytes) -> list[dict[str, object]]:
    answer = client.post(
        "/api/faces/packs/import", files={"file": ("f.zip", raw, "application/zip")}
    )
    assert answer.status_code == 200, answer.text
    items: list[dict[str, object]] = client.get("/api/faces/fingerprints/waiting").json()["items"]
    return items


def test_two_libraries_of_one_name_both_stay_and_each_says_that_name(client: TestClient) -> None:
    turn_on(client)
    sign_in(client, "admin")

    first = _take(client, _file(["Neve Arbor", "Orla Tennant"], library="north-0001"))
    both = _take(
        client, _file(["Bryn Calloway", "Cassia Lynn", "Elina Sorrel"], library="south-0002")
    )

    assert (len(first), len(both)) == (2, 5)
    assert {one["source"] for one in both} == {"Sift"}


def test_the_same_library_exported_twice_replaces_its_first_file(client: TestClient) -> None:
    turn_on(client)
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            _pack("folders", "Folders you imported"),
            *_entry("e1", "Neve Arbor", ("a", "b")),
            *_entry("e2", "Orla Tennant", ("c",)),
            *_entry("e3", "Elina Sorrel", ("d",)),
        ],
    )

    def export(name: str, **body: object) -> bytes:
        answer = client.post("/api/faces/packs/export", json={"name": name, **body})
        assert answer.status_code == 200, answer.text
        return bytes(answer.content)

    everyone, one = export("Sift"), export("renamed", entry_ids=["e1"])
    read = [packs.read(raw, expect_recognizer=MODEL.revision) for raw in (everyone, one)]

    assert read[0].library is not None
    assert read[0].library == read[1].library
    assert len(_take(client, everyone)) == 6
    after = _take(client, one)
    assert len(after) == 4
    assert sorted(str(one["source"]) for one in after if one["name"] == "Neve Arbor") == [
        "Folders you imported",
        "renamed",
    ]


def test_an_older_file_of_the_same_name_lands_beside_a_library_s_file(
    client: TestClient,
) -> None:
    turn_on(client)
    sign_in(client, "admin")

    def older(people: list[str]) -> bytes:
        return _as_edition(_file(people, library="north-0001"), {"format": 2})

    assert len(_take(client, _file(["Neve Arbor", "Orla Tennant"], library="north-0001"))) == 2
    assert len(_take(client, older(["Bryn Calloway", "Cassia Lynn"]))) == 4
    assert len(_take(client, older(["Elina Sorrel"]))) == 3


@pytest.mark.parametrize(
    ("edit", "library"),
    [
        ({"library": " north-0001 "}, "north-0001"),
        ({"library": "x" * (packs.LIBRARY_ID_MAX + 1)}, None),
        ({"library": 7}, None),
        ({"library": "  "}, None),
        ({"format": 2, "library": "north-0001"}, None),
        ({}, None),
    ],
)
def test_a_file_s_library_is_read_only_from_a_file_that_can_give_one(
    edit: dict[str, object], library: str | None
) -> None:
    raw = _as_edition(a_pack(), edit)
    assert packs.read(raw, expect_recognizer=RECOGNISER).library == library


async def test_a_library_before_41_keeps_its_packs_by_name_and_gains_a_library_s_beside(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    async with temp_db.write() as connection:
        await connection.execute("DROP INDEX ix_face_packs_library")
        await connection.execute("ALTER TABLE face_packs DROP COLUMN library")
        await connection.execute("DROP TABLE face_own_library")
        await connection.execute(
            "INSERT INTO face_packs (id, name, version, recognizer, dimension, digest,"
            " installed_at) VALUES ('old', 'Sample set', '1', ?, 4, 'd', 0)",
            (RECOGNISER,),
        )
        await connection.execute(
            "INSERT INTO pack_entries (id, pack_id, name, created_at)"
            " VALUES ('e1', 'old', 'Neve Arbor', 0)"
        )
        await faces_schema.initialize(connection, on_disk=40)
        await faces_schema.initialize(connection, on_disk=40)

    await service.import_pack(_as_edition(a_pack(people=["Orla Tennant"]), {"library": "n-1"}))
    exported = packs.read(
        await service.export_pack(name="Sift", version="1", person_ids=[], include_pictures=False),
        expect_recognizer=RECOGNISER,
    )

    waiting = {str(one["name"]): str(one["source"]) for one in await service.waiting()}
    assert waiting == {"Neve Arbor": "Sample set", "Orla Tennant": "Sample set"}
    assert [row["library"] for row in await store.packs()].count(None) == 1
    assert exported.library == await store.own_library()
