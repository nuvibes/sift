# SPDX-License-Identifier: AGPL-3.0-or-later
"""The facial fingerprints file carries the people waiting for a matching face as well as the
people Sift can recognize, and refuses whoever a swap refuses."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.slices.faces import packs, recognize, weights
from sift.slices.faces.models import Origin
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests import test_routes
from sift.slices.faces.tests.conftest import make_person, person_vector
from sift.slices.faces.tests.test_routes import (
    _INSERT_PERSON,
    db_path,
    sign_in,
    turn_on,
    unlock,
    write,
)
from sift.testing.library import hidden_row

pytestmark = pytest.mark.integration

app = test_routes.app
client = test_routes.client

_, MODEL = weights.pairing("accurate")

_PACK = (
    "INSERT INTO face_packs (id, name, version, recognizer, dimension, digest, installed_at)"
    " VALUES (?, ?, '1', ?, 0, ?, 0)"
)
_ENTRY = (
    "INSERT INTO pack_entries (id, pack_id, name, aliases, claimed_person_id, created_at,"
    " declined_at) VALUES (?, ?, ?, ?, ?, 0, ?)"
)
_FACE = (
    "INSERT INTO pack_entry_faces (id, entry_id, crop_path, crop_digest, embedding, quality,"
    " recognizer) VALUES (?, ?, NULL, ?, ?, 0.9, ?)"
)
_REFERENCE = (
    "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, quality,"
    " origin, recognizer, created_at) VALUES (?, ?, NULL, ?, ?, 0.9, 'confirmed', ?, 0)"
)
_VECTOR = recognize.pack((1.0, *[0.0] * (MODEL.dimension - 1)))


def _pack(pack_id: str, name: str) -> tuple[str, tuple[object, ...]]:
    return (_PACK, (pack_id, name, MODEL.revision, pack_id))


def _entry(
    entry_id: str,
    name: str,
    digests: tuple[str, ...],
    *,
    pack: str = "folders",
    aliases: str = "[]",
    claimed: str | None = None,
    declined: int | None = None,
    model: str = MODEL.revision,
) -> list[tuple[str, tuple[object, ...]]]:
    return [
        (_ENTRY, (entry_id, pack, name, aliases, claimed, declined)),
        *((_FACE, (f"{entry_id}-{one}", entry_id, one, _VECTOR, model)) for one in digests),
    ]


def _person(
    person_id: str, name: str, digests: tuple[str, ...]
) -> list[tuple[str, tuple[object, ...]]]:
    return [
        (_INSERT_PERSON, (person_id, name)),
        *(
            (_REFERENCE, (f"{person_id}-{one}", person_id, one, _VECTOR, MODEL.revision))
            for one in digests
        ),
    ]


def _export(client: TestClient, **body: object) -> packs.Pack:
    answer = client.post("/api/faces/packs/export", json={"name": "north library", **body})
    assert answer.status_code == 200, answer.text
    return packs.read(answer.content, expect_recognizer=MODEL.revision)


def _rows(pack: packs.Pack) -> dict[str, int]:
    return {person.name: len(person.faces) for person in pack.people}


def test_a_library_that_recognizes_nobody_exports_everyone_waiting_by_name(
    client: TestClient,
) -> None:
    """A folder import on a library with no files: nobody recognized, three waiting, and the
    file carries the three with the faces the waiting list counts, the models never fetched."""
    turn_on(client)
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            _pack("folders", "Folders you imported"),
            *_entry("e1", "Neve Arbor", ("a", "b")),
            *_entry("e2", "Wren Halloway", ("c",)),
            *_entry("e3", "Cass Ivory", ("d", "e", "f")),
        ],
    )

    waiting = client.get("/api/faces/fingerprints/waiting").json()
    pack = _export(client)

    assert waiting["exportable"] == 3
    assert _rows(pack) == {one["name"]: one["faces"] for one in waiting["items"]}
    assert pack.name == "north library"
    assert all(person.confirmed is None for person in pack.people)


def test_a_person_and_an_entry_of_one_name_are_one_person_in_the_file(
    client: TestClient,
) -> None:
    """Faces joined by their picture, the other names joined, and her own confirmed count."""
    turn_on(client)
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            _pack("folders", "Folders you imported"),
            *_person("p1", "Neve Arbor", ("a", "b")),
            *_entry("e1", "neve arbor", ("b", "c"), aliases='["Neve A"]'),
        ],
    )

    waiting = client.get("/api/faces/fingerprints/waiting").json()
    (person,) = _export(client).people

    assert (person.name, len(person.faces), person.confirmed) == ("Neve Arbor", 3, 2)
    assert person.aliases == ("Neve A",)
    # Carried, but as the person she already is, so the line under Export counts nobody more.
    assert (waiting["exportable"], waiting["items"][0]["exportable"]) == (0, True)


def test_only_the_waiting_list_s_entries_with_this_model_s_faces_are_carried(
    client: TestClient,
) -> None:
    """Another model's numbers mean nothing elsewhere; a swap's entry waits on a person's page;
    a claimed entry is somebody already; one an Undo declined is still on the list."""
    turn_on(client)
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            _pack("folders", "Folders you imported"),
            _pack("swapped", "swap one two"),
            (_INSERT_PERSON, ("p9", "Hollis Danforth")),
            *_entry("e1", "Neve Arbor", ("a",), model="another-model"),
            *_entry("e2", "Cass Ivory", ("b",), pack="swapped"),
            *_entry("e3", "Hollis Danforth", ("c",), claimed="p9"),
            *_entry("e4", "Wren Halloway", ("d",), declined=5),
            (_FACE, ("e4-x", "e4", "x", _VECTOR, "another-model")),
        ],
    )

    waiting = client.get("/api/faces/fingerprints/waiting").json()

    assert _rows(_export(client)) == {"Wren Halloway": 1}
    assert waiting["exportable"] == 1
    assert {one["name"]: one["exportable"] for one in waiting["items"]} == {
        "Neve Arbor": False,
        "Wren Halloway": True,
    }


def test_the_request_takes_exactly_the_people_and_the_entries_it_names(
    client: TestClient,
) -> None:
    turn_on(client)
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            _pack("folders", "Folders you imported"),
            *_person("p1", "Imre Vasquez", ("a",)),
            *_entry("e1", "Neve Arbor", ("b",)),
            *_entry("e2", "Wren Halloway", ("c",)),
        ],
    )

    assert set(_rows(_export(client, person_ids=["p1"]))) == {"Imre Vasquez"}
    assert set(_rows(_export(client, entry_ids=["e2"]))) == {"Wren Halloway"}
    assert set(_rows(_export(client, person_ids=["p1"], entry_ids=["e1"]))) == {
        "Imre Vasquez",
        "Neve Arbor",
    }
    assert set(_rows(_export(client))) == {"Imre Vasquez", "Neve Arbor", "Wren Halloway"}


@pytest.mark.parametrize(
    "mark",
    [
        "UPDATE people SET keep_local = 1 WHERE id = ?",
        "UPDATE people SET keep_from_swaps = 1 WHERE id = ?",
    ],
)
def test_a_person_a_swap_refuses_never_leaves_in_a_file_nor_does_their_name(
    client: TestClient, mark: str
) -> None:
    """Kept local or Do not swap: refused from everyone and from a pick, and an entry waiting
    under the same name is refused with them, since it would carry the name out."""
    turn_on(client)
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            _pack("folders", "Folders you imported"),
            *_person("p1", "Imre Vasquez", ("a",)),
            *_person("p2", "Esme Wrenfield", ("b",)),
            *_entry("e1", "imre vasquez", ("c",)),
            *_entry("e2", "Neve Arbor", ("d",)),
            (mark, ("p1",)),
        ],
    )

    assert set(_rows(_export(client))) == {"Esme Wrenfield", "Neve Arbor"}
    refused = client.post(
        "/api/faces/packs/export",
        json={"name": "north library", "person_ids": ["p1"], "entry_ids": ["e1"]},
    )
    assert refused.status_code == 409
    waiting = client.get("/api/faces/fingerprints/waiting").json()
    assert {one["name"]: one["exportable"] for one in waiting["items"]} == {
        "imre vasquez": False,
        "Neve Arbor": True,
    }


def test_an_entry_named_as_somebody_a_shut_hidden_holds_back_waits_out_of_the_file(
    client: TestClient,
) -> None:
    turn_on(client)
    admin = sign_in(client, "admin")
    write(
        db_path(client),
        [
            _pack("folders", "Folders you imported"),
            (_INSERT_PERSON, ("p1", "Imre Vasquez")),
            hidden_row("person", "p1", admin),
            *_entry("e1", "Imre Vasquez", ("a",)),
            *_entry("e2", "Neve Arbor", ("b",)),
        ],
    )

    assert set(_rows(_export(client))) == {"Neve Arbor"}
    assert unlock(client) == 200
    assert set(_rows(_export(client))) == {"Imre Vasquez", "Neve Arbor"}


def test_nobody_to_carry_is_refused_in_words_and_recognition_off_counts_nobody(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    write(
        db_path(client), [_pack("folders", "Folders you imported"), *_entry("e1", "Neve", ("a",))]
    )

    off = client.get("/api/faces/fingerprints/waiting").json()
    assert (off["exportable"], off["items"][0]["exportable"]) == (0, False)
    turn_on(client)
    nobody = client.post("/api/faces/packs/export", json={"name": "x", "entry_ids": ["gone"]})
    assert nobody.status_code == 409
    assert "nobody here" in nobody.json()["detail"]


def test_two_libraries_files_both_stay_where_two_files_of_one_name_replace(
    client: TestClient,
) -> None:
    """A file is an edition of every earlier file of its name, so a library's own name is what
    keeps a second library's file from deleting the first one's waiting people."""
    turn_on(client)
    sign_in(client, "admin")

    def made(name: str, people: list[str]) -> bytes:
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
        )

    def take(raw: bytes) -> int:
        answer = client.post(
            "/api/faces/packs/import", files={"file": ("f.zip", raw, "application/zip")}
        )
        assert answer.status_code == 200, answer.text
        return len(client.get("/api/faces/fingerprints/waiting").json()["items"])

    assert take(made("faces", ["Neve Arbor", "Wren Halloway"])) == 2
    assert take(made("faces", ["Cass Ivory", "Esme Wrenfield", "Fenn Marchetti"])) == 3
    assert take(made("north library", ["Halla Nordquist", "Imre Vasquez"])) == 5
    assert take(made("south library", ["Bryn Calloway", "Cassia Lynn", "Elina Sorrel"])) == 8


async def test_a_file_carries_a_waiting_person_s_pictures_only_when_asked(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """Built from what is stored, no model loaded: the numbers are already here."""
    pack = packs.build(
        name="a file",
        version="1",
        recognizer="test-recognizer",
        dimension=len(person_vector(0)),
        people=[
            packs.PackedPerson(
                name="Neve Arbor",
                aliases=("Neve A",),
                links=("https://example.org/neve",),
                faces=(
                    packs.PackedFace(
                        digest="n1", quality=0.8, vector=person_vector(0), picture=b"picture"
                    ),
                ),
                confirmed=40,
            )
        ],
        include_pictures=True,
    )
    await service.import_pack(pack)
    ((entry_id, _name),) = [(one.entry_id, one.name) for one in await service.held_for_export()]
    cass = await make_person(temp_db, "Cass Ivory")
    await store.add_reference(
        cass,
        vector=person_vector(1),
        quality=0.9,
        crop=b"hers",
        origin=Origin.CONFIRMED,
        recognizer="test-recognizer",
    )
    service._detector = service._recognizer = None

    async def no_models(*_: object) -> object:
        raise AssertionError("the export loaded a model")

    service._models = no_models  # type: ignore[method-assign,assignment]

    pictured = packs.read(
        await service.export_pack(
            name="mine", version="1", person_ids=[cass], entry_ids=[entry_id], include_pictures=True
        ),
        expect_recognizer="test-recognizer",
    )
    bare = packs.read(
        await service.export_pack(
            name="mine",
            version="1",
            person_ids=[],
            # One claimed since the list was read is left out.
            entry_ids=["e-claimed", entry_id],
            include_pictures=False,
        ),
        expect_recognizer="test-recognizer",
    )

    assert [one.name for one in pictured.people] == ["Cass Ivory", "Neve Arbor"]
    neve = pictured.people[1]
    assert (neve.aliases, neve.links, neve.confirmed) == (
        ("Neve A",),
        ("https://example.org/neve",),
        40,
    )
    assert neve.faces[0].picture == b"picture"
    assert [one.name for one in bare.people] == ["Neve Arbor"]
    assert bare.people[0].faces[0].picture is None
