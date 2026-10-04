# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sharing reference faces, and the two properties that make it safe to do twice.

**A pack made by a different model is refused outright.** Descriptions from two different models
occupy different spaces: compared against each other they do not produce a worse answer, they
produce a meaningless one, and the symptom would be matching that had quietly stopped working
rather than an error anybody could act on.

**Importing the same pack twice writes nothing the second time**, and importing a later edition
updates what the earlier one brought rather than stacking beside it. Both are mechanics (keyed by
the identity of each picture, and by the pack's name) rather than intentions.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.slices.faces import packs, recognize
from sift.slices.faces import settings as face_settings
from sift.slices.faces import store_people as store_module
from sift.slices.faces.models import Origin
from sift.slices.faces.packs import PackedFace, PackedPerson, PackError
from sift.slices.faces.service import FaceService
from sift.slices.faces.service_base import FacesDisabled
from sift.slices.faces.service_references import EntryHeld
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import (
    DIMENSION,
    FakePreferences,
    make_person,
    person_vector,
)

pytestmark = pytest.mark.integration

RECOGNISER = "test-recognizer"


def a_pack(
    *,
    name: str = "Sample set",
    version: str = "1",
    recognizer: str = RECOGNISER,
    people: list[str] | None = None,
    include_pictures: bool = True,
    faces_each: int = 2,
    aliases: dict[str, tuple[str, ...]] | None = None,
    confirmed: int | None = None,
) -> bytes:
    entries = []
    for index, person in enumerate(people or ["Ada Lovelace"]):
        entries.append(
            PackedPerson(
                name=person,
                aliases=(aliases or {}).get(person, ()),
                links=(),
                faces=tuple(
                    PackedFace(
                        digest=f"{person}-{variant}",
                        quality=0.8,
                        vector=person_vector(index, variant),
                        picture=b"picture-bytes" if include_pictures else None,
                    )
                    for variant in range(faces_each)
                ),
                confirmed=confirmed,
            )
        )
    return packs.build(
        name=name,
        version=version,
        recognizer=recognizer,
        dimension=DIMENSION,
        people=entries,
        include_pictures=include_pictures,
    )


# --- the format -----------------------------------------------------------------------------------


def test_a_pack_can_be_written_and_read_back() -> None:
    raw = a_pack(people=["Ada Lovelace", "Grace Hopper"])

    pack = packs.read(raw, expect_recognizer=RECOGNISER)

    assert pack.name == "Sample set"
    assert [person.name for person in pack.people] == ["Ada Lovelace", "Grace Hopper"]
    assert pack.face_count == 4
    assert pack.people[0].faces[0].picture == b"picture-bytes"


def test_a_pack_of_numbers_carries_no_pictures() -> None:
    """The default, and the reason for it: a description cannot be turned back into a photograph of
    somebody, so a pack of numbers hands on far less about real people."""
    raw = a_pack(include_pictures=False)

    pack = packs.read(raw, expect_recognizer=RECOGNISER)

    assert all(face.picture is None for person in pack.people for face in person.faces)
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        assert not [name for name in bundle.namelist() if name.startswith(packs.PICTURES)]


def test_a_pack_made_by_a_different_model_is_refused_and_says_why() -> None:
    """Refused rather than imported and quietly useless."""
    raw = a_pack(recognizer="some-other-model")

    with pytest.raises(PackError) as failure:
        packs.read(raw, expect_recognizer=RECOGNISER)

    message = str(failure.value)
    assert "some-other-model" in message
    assert RECOGNISER in message
    assert "can't be compared" in message


def test_something_that_is_not_a_pack_is_refused() -> None:
    with pytest.raises(PackError):
        packs.read(b"not a zip at all", expect_recognizer=RECOGNISER)


def test_a_pack_from_a_different_version_of_the_format_is_refused() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(packs.MANIFEST, json.dumps({"format": 99, "recognizer": RECOGNISER}))
    with pytest.raises(PackError, match="different version"):
        packs.read(buffer.getvalue(), expect_recognizer=RECOGNISER)


def _as_edition(raw: bytes, edit: dict[str, object]) -> bytes:
    """The same file with its manifest's top level changed and every person's `confirmed` taken
    away: a file as a Sift before the count wrote it, when `edit` sets its format to 1."""
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(out, "w") as bundle:
        for name in source.namelist():
            body = source.read(name)
            if name == packs.MANIFEST:
                manifest = {**json.loads(body), **edit}
                for person in manifest["people"]:
                    person.pop("confirmed", None)
                body = json.dumps(manifest).encode()
            bundle.writestr(name, body)
    return out.getvalue()


def test_a_pack_says_how_many_confirmed_faces_each_person_had_and_an_older_one_is_still_read() -> (
    None
):
    """Version 2 gives each person their confirmed count; a version 1 file reads as before, with
    no count rather than refused."""
    raw = a_pack(people=["Ada Lovelace"], confirmed=3)
    assert packs.read(raw, expect_recognizer=RECOGNISER).people[0].confirmed == 3
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        assert json.loads(bundle.read(packs.MANIFEST))["format"] == 2

    older = packs.read(_as_edition(raw, {"format": 1}), expect_recognizer=RECOGNISER)
    assert older.people[0].confirmed is None
    assert older.face_count == 2


def test_a_count_that_is_not_one_reads_as_no_count() -> None:
    for odd in (-1, True, "3", 2.5):
        raw = a_pack(confirmed=3)
        out = BytesIO()
        with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(out, "w") as bundle:
            for name in source.namelist():
                body = source.read(name)
                if name == packs.MANIFEST:
                    manifest = json.loads(body)
                    manifest["people"][0]["confirmed"] = odd
                    body = json.dumps(manifest).encode()
                bundle.writestr(name, body)
        read = packs.read(out.getvalue(), expect_recognizer=RECOGNISER)
        assert read.people[0].confirmed is None, odd


def test_a_pack_with_a_manifest_that_is_not_an_object_is_refused() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(packs.MANIFEST, json.dumps([1, 2, 3]))
    with pytest.raises(PackError):
        packs.read(buffer.getvalue(), expect_recognizer=RECOGNISER)


def test_a_pack_whose_numbers_do_not_match_its_manifest_is_refused_as_damaged() -> None:
    raw = a_pack()
    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as original, zipfile.ZipFile(buffer, "w") as rebuilt:
        for item in original.namelist():
            payload = original.read(item)
            rebuilt.writestr(item, payload[:4] if item == packs.VECTORS else payload)

    with pytest.raises(PackError, match="damaged"):
        packs.read(buffer.getvalue(), expect_recognizer=RECOGNISER)


def test_a_pack_that_does_not_say_how_faces_are_described_is_refused() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(
            packs.MANIFEST,
            json.dumps({"format": packs.FORMAT, "recognizer": RECOGNISER, "dimension": 0}),
        )
    with pytest.raises(PackError, match="doesn't say"):
        packs.read(buffer.getvalue(), expect_recognizer=RECOGNISER)


def test_a_set_described_by_two_different_models_cannot_be_written() -> None:
    person = PackedPerson(
        name="Ada",
        aliases=(),
        links=(),
        faces=(PackedFace(digest="a", quality=1.0, vector=(1.0, 0.0), picture=None),),
    )
    with pytest.raises(PackError, match=r"[Mm]ore than one model"):
        packs.build(
            name="x",
            version="1",
            recognizer=RECOGNISER,
            dimension=DIMENSION,
            people=[person],
            include_pictures=False,
        )


# --- importing ------------------------------------------------------------------------------------


async def test_an_import_holds_every_name_and_gives_nobody_anything_by_name(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A name is no evidence about who somebody is here: an import holds every person the file
    names, the one People already has a person called as well, and gives nobody a reference."""
    known = await make_person(temp_db, "Ada Lovelace")

    outcome = await service.import_pack(a_pack(people=["Ada Lovelace", "Nobody In Particular"]))

    assert outcome.added == 4
    assert outcome.held == ["Ada Lovelace", "Nobody In Particular"]
    assert await store.references(known) == [], "a name attached faces to somebody"
    assert [str(row["name"]) for row in await store.unclaimed_entries()] == [
        "Ada Lovelace",
        "Nobody In Particular",
    ]
    assert await store.existing_people(["Nobody In Particular"]) == {}


async def test_an_import_keeps_the_confirmed_count_on_each_entry_and_the_waiting_list_says_it(
    service: FaceService,
) -> None:
    """The count the file gave travels on to the list of who is waiting, where three faces of
    somebody known by three read differently from three of somebody known by two hundred."""
    await service.import_pack(a_pack(people=["Ada Lovelace"], confirmed=210))
    await service.import_pack(
        _as_edition(a_pack(name="Older set", people=["Nobody In Particular"]), {"format": 1})
    )

    waiting = {str(one["name"]): one for one in await service.waiting()}
    assert (waiting["Ada Lovelace"]["faces"], waiting["Ada Lovelace"]["confirmed"]) == (2, 210)
    assert waiting["Ada Lovelace"]["source"] == "Sample set"
    assert waiting["Nobody In Particular"]["confirmed"] is None


async def test_a_waiting_entry_is_removed_by_a_service_with_no_history_to_write_to(
    service: FaceService, store: Store
) -> None:
    """The recorder is optional: without one the entry still goes, with no line to say so."""
    assert service._recorder is None
    await service.import_pack(a_pack(people=["Ada Lovelace"]))
    (entry,) = await service.waiting()

    assert await service.remove_waiting(str(entry["entry_id"]), by="an-admin")

    assert await service.waiting() == []
    assert await store.entry_faces(str(entry["entry_id"])) == []


async def test_a_pack_taken_in_while_recognition_is_off_is_held_without_a_model(
    service: FaceService, store: Store, temp_db: Database, preferences: FakePreferences
) -> None:
    """A new library brings its people before anybody agrees to faces being measured. The file
    lands with the switch off and loads no model; the entries wait for the pass."""
    preferences.set(face_settings.ENABLED_KEY, False)
    models = (service._detector, service._recognizer)
    # No model to hand: taking the file in must not need one.
    service._detector = service._recognizer = None

    outcome = await service.import_pack(a_pack(people=["Bryn Calloway"]), while_off=True)

    assert outcome.added == 2
    assert [str(row["name"]) for row in await store.unclaimed_entries()] == ["Bryn Calloway"]
    # Every other door keeps the consent gate: only the fingerprints file's own takes it in.
    with pytest.raises(FacesDisabled):
        await service.import_pack(a_pack(people=["Bryn Calloway"], version="2"))
    service._detector, service._recognizer = models


async def test_a_swap_holds_the_faces_of_somebody_known_until_they_are_given_over(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A swap's descriptions of somebody this library has are a suggestion: held for them, never a
    reference, until somebody gives them over (`claim_for`)."""
    known = await make_person(temp_db, "Ada Lovelace")

    outcome = await service.take_from_swap(a_pack(people=["Ada Lovelace"]), suggest_only=True)

    assert outcome.added == 0
    assert await store.references(known) == []
    assert await service.waiting_for("Ada Lovelace") == ["Ada Lovelace"]
    assert await service.claim_for(known, "Ada Lovelace") == 2
    assert len(await store.references(known)) == 2


def overtaken(
    service: FaceService, store: Store, monkeypatch: pytest.MonkeyPatch, *, by: str
) -> None:
    """Another write places each held entry for `by` between a claim's read and its write."""
    read = service._entry_held

    async def then_taken(entry: Row) -> EntryHeld:
        held = await read(entry)
        async with store.database.write() as connection:
            assert await store.claim_entry_on(connection, held.entry_id, by)
        return held

    monkeypatch.setattr(service, "_entry_held", then_taken)


async def test_a_claim_another_write_placed_first_gives_nothing(
    service: FaceService, store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two presses on one held entry: the one whose write lands second adds no reference."""
    known = await make_person(temp_db, "Ada Lovelace")
    namesake = await make_person(temp_db, "Ada Lovelace")
    await service.take_from_swap(a_pack(people=["Ada Lovelace"]), suggest_only=True)
    overtaken(service, store, monkeypatch, by=namesake)

    assert await service.claim_for(known, "Ada Lovelace") == 0

    assert await store.references(known) == []
    assert await service.waiting_for("Ada Lovelace") == []


async def test_what_is_held_for_somebody_is_counted_as_the_claim_would_add_it(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The offer on their page counts what the press adds: a held description they already have
    as a reference is not counted, and nothing is counted once the claim has been made."""
    known = await make_person(temp_db, "Bryn Calloway")
    await service.take_from_swap(
        a_pack(name="Theirs", people=["Bryn Calloway"], faces_each=2), suggest_only=False
    )
    assert await service.held_for(known, "Bryn Calloway") == 0

    held = a_pack(name="From a swap", people=["Bryn Calloway"], faces_each=3)
    await service.take_from_swap(held, suggest_only=True)

    assert await service.held_for(known, "Bryn Calloway") == 1
    assert len(await store.references(known)) == 2
    assert await service.claim_for(known, "Bryn Calloway") == 1
    assert await service.held_for(known, "Bryn Calloway") == 0


async def test_importing_the_same_pack_twice_writes_nothing_the_second_time(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The property, tested the way it is stated: same counts, no new rows, no new files."""
    raw = a_pack()

    first = (await service.import_pack(raw)).added
    files_after_first = sorted(path.name for path in store.reference_root.rglob("*.jpg"))

    second = (await service.import_pack(raw)).added

    assert first == 2
    assert second == 0
    entries = await store.unclaimed_entries()
    assert len(entries) == 1
    assert len(await store.entry_faces(str(entries[0]["id"]))) == 2
    assert sorted(path.name for path in store.reference_root.rglob("*.jpg")) == files_after_first


async def test_a_later_edition_of_a_pack_updates_what_the_earlier_one_brought(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """Updates rather than stacking a second copy beside it, because a pack carries a name."""
    await service.import_pack(a_pack(version="1", faces_each=2))
    await service.import_pack(a_pack(version="2", faces_each=3))

    entries = await store.unclaimed_entries()
    assert len(entries) == 1
    assert len(await store.entry_faces(str(entries[0]["id"]))) == 3
    installed = await store.packs()
    assert len(installed) == 1
    assert str(installed[0]["version"]) == "2"


async def test_a_later_edition_takes_the_earlier_one_s_pictures_off_the_disk_too(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The rows and the pictures go together, and only one of them is the database's job.

    A held face names its entry and the database removes it with the pack, so an order that
    deletes the pack first leaves this side nothing to find and the pictures on the disk for ever.
    """
    await service.import_pack(a_pack(version="1", faces_each=2))
    first_edition = sorted(path.name for path in store.reference_root.rglob("*.jpg"))
    assert len(first_edition) == 2

    await service.import_pack(a_pack(version="2", faces_each=3))

    remaining = sorted(path.name for path in store.reference_root.rglob("*.jpg"))
    assert len(remaining) == 3
    assert not set(remaining) & set(first_edition)


async def test_removing_a_pack_removes_only_what_it_brought(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"added-by-hand",
        origin=Origin.ADDED,
        recognizer=RECOGNISER,
    )
    await service.take_from_swap(a_pack(), suggest_only=False)
    assert len(await store.references(person)) == 3

    pack_id = str((await store.packs())[0]["id"])
    removed = await store.remove_references(pack_id=pack_id)

    assert removed == 2
    survivors = await store.references(person)
    assert len(survivors) == 1


async def test_exporting_a_pack_is_choosing_people_and_pressing_export(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A confirmed face is already a clean single-face picture with numbers attached, which is
    exactly what a pack is made of."""
    person = await make_person(temp_db, "Ada Lovelace")
    for variant in range(3):
        await store.add_reference(
            person,
            vector=person_vector(0, variant),
            quality=0.9,
            crop=f"picture-{variant}".encode(),
            origin=Origin.CONFIRMED,
            recognizer=RECOGNISER,
        )

    raw = await service.export_pack(
        name="Mine", version="1", person_ids=[person], include_pictures=True
    )

    pack = packs.read(raw, expect_recognizer=RECOGNISER)
    assert [item.name for item in pack.people] == ["Ada Lovelace"]
    assert pack.face_count == 3
    assert pack.people[0].faces[0].picture is not None
    # Her confirmed faces, the number her page bands, so the other side can say how surely.
    assert pack.people[0].confirmed == 3


async def test_a_pack_asked_for_nobody_in_particular_carries_everybody_sift_can_recognize(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The one export button names no People, and that is not a pack of an empty list. Everybody with a reference somebody chose travels; a starter-only person does not, and
    neither does a reference another model measured."""
    grace = await make_person(temp_db, "Grace Hopper")
    ada = await make_person(temp_db, "Ada Lovelace")
    starter_only = await make_person(temp_db, "Bryn Calloway")
    other_model = await make_person(temp_db, "Cassia Lynn")
    await make_person(temp_db, "Nobody Yet")
    for person, origin, recognizer in (
        (grace, Origin.CONFIRMED, RECOGNISER),
        (ada, Origin.ADDED, RECOGNISER),
        (starter_only, Origin.SEED, RECOGNISER),
        (other_model, Origin.ADDED, "another-recognizer"),
    ):
        await store.add_reference(
            person,
            vector=person_vector(0, 1),
            quality=0.9,
            crop=f"picture-{person}".encode(),
            origin=origin,
            recognizer=recognizer,
        )

    raw = await service.export_pack(
        name="Everybody", version="1", person_ids=[], include_pictures=False
    )

    pack = packs.read(raw, expect_recognizer=RECOGNISER)
    assert sorted(item.name for item in pack.people) == ["Ada Lovelace", "Grace Hopper"]


async def test_the_descriptions_a_swap_offers_are_the_chosen_references_and_never_a_starter(
    service: FaceService, store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a swap's offer is made from: the model named from the catalog without loading it, and
    per person the references somebody chose: a starter never travels, a person with none is
    absent, and with faces off there is nothing at all."""
    from sift.slices.faces import weights

    person = await make_person(temp_db, "Ada Lovelace")
    empty = await make_person(temp_db, "Nobody Yet")
    for variant in range(3):
        await store.add_reference(
            person,
            vector=person_vector(0, variant),
            quality=0.9,
            crop=f"picture-{variant}".encode(),
            origin=Origin.CONFIRMED,
            recognizer=RECOGNISER,
        )
    await store.add_reference(
        person,
        vector=person_vector(0, 7),
        quality=0.5,
        crop=b"starter",
        origin=Origin.SEED,
        recognizer=RECOGNISER,
    )

    found = await service.descriptions_for_swap([person, empty])

    assert found is not None
    recognizer, dimension, per_person = found
    _, weight = weights.pairing((await service.configuration()).family)
    assert (recognizer, dimension) == (weight.revision, weight.dimension)
    assert set(per_person) == {person}
    assert len(per_person[person]) == 3
    assert all(len(vector) == DIMENSION for _digest, _quality, vector in per_person[person])

    async def off() -> bool:
        return False

    monkeypatch.setattr(service, "enabled", off)
    assert await service.descriptions_for_swap([person]) is None


async def test_the_pictures_a_swap_sends_are_her_best_chosen_ones_that_still_have_a_picture(
    service: FaceService, store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked when the other side uses the other face model and must describe them itself: the
    best by quality, never a starter, and a reference that is numbers alone sends nothing."""
    person = await make_person(temp_db, "Ada Lovelace")
    for crop, quality in ((b"best", 0.9), (b"second", 0.8)):
        await store.add_reference(
            person,
            vector=person_vector(0),
            quality=quality,
            crop=crop,
            origin=Origin.CONFIRMED,
            recognizer=RECOGNISER,
        )
    await store.add_reference(
        person,
        vector=person_vector(0, 1),
        quality=0.95,
        crop=None,
        digest="numbers-alone",
        origin=Origin.CONFIRMED,
        recognizer=RECOGNISER,
    )
    await store.add_reference(
        person,
        vector=person_vector(0, 7),
        quality=1.0,
        crop=b"starter",
        origin=Origin.SEED,
        recognizer=RECOGNISER,
    )
    digest_of = {one.quality: one.crop_digest for one in await store.references(person)}

    found = await service.pictures_for_swap([person], best=2)

    assert found == {digest_of[0.9]: b"best"}

    async def off() -> bool:
        return False

    monkeypatch.setattr(service, "enabled", off)
    assert await service.pictures_for_swap([person], best=2) == {}


async def test_exporting_skips_a_person_with_no_reference_faces(
    service: FaceService, temp_db: Database
) -> None:
    empty = await make_person(temp_db, "Nobody Yet")

    raw = await service.export_pack(
        name="Mine", version="1", person_ids=[empty], include_pictures=False
    )

    assert packs.read(raw, expect_recognizer=RECOGNISER).people == ()


# --- creating the People a pack names ---------------------------------------------------------


async def test_an_import_makes_nobody_whatever_the_switch_says(
    service: FaceService, store: Store, temp_db: Database, preferences: FakePreferences
) -> None:
    """Making people is the pass's, by face, never the import's by name: with the switch on, a
    file naming somebody nobody here is called still makes no person until faces match."""
    preferences.set(face_settings.PEOPLE_FROM_FILES_KEY, True)

    outcome = await service.import_pack(a_pack(people=["Ada Lovelace"]))

    assert outcome.held == ["Ada Lovelace"]
    assert await store.existing_people(["Ada Lovelace"]) == {}


async def test_the_other_names_somebody_answers_to_are_merged_rather_than_replaced(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A swap knows what the other side calls somebody and not what this library calls them, so
    what it carries is added to what is already there and nothing is taken away."""
    person_id = await make_person(temp_db, "Ada Lovelace")
    await store.add_alias(person_id, "Ada Byron")

    outcome = await service.take_from_swap(
        a_pack(aliases={"Ada Lovelace": ("Countess Lovelace", "Ada Byron")}), suggest_only=False
    )

    assert outcome.alias_clashes == []
    assert await store.aliases_of(person_id) == ["Ada Byron", "Countess Lovelace"]


async def test_a_pack_does_not_make_somebody_an_alias_of_themselves(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A row saying somebody is also called what they are called is noise in a table whose whole
    purpose is to be read by the term resolver."""
    person_id = await make_person(temp_db, "Ada Lovelace")

    await service.take_from_swap(
        a_pack(aliases={"Ada Lovelace": ("ada lovelace",)}), suggest_only=False
    )

    assert await store.aliases_of(person_id) == []


async def test_an_alias_that_already_belongs_to_somebody_else_is_reported_not_taken(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """One alias may not belong to two People, and this is the one thing here that is refused
    rather than merged: a name pointing at two humans makes every search using it ambiguous. Both
    names are in the report, because "the alias is taken" without saying by whom is not something
    anybody can act on.
    """
    ada = await make_person(temp_db, "Ada Lovelace")
    grace = await make_person(temp_db, "Grace Hopper")
    await store.add_alias(grace, "The Countess")

    outcome = await service.take_from_swap(
        a_pack(people=["Ada Lovelace"], aliases={"Ada Lovelace": ("The Countess",)}),
        suggest_only=False,
    )

    assert [clash.alias for clash in outcome.alias_clashes] == ["The Countess"]
    assert outcome.alias_clashes[0].wanted_by == "Ada Lovelace"
    assert outcome.alias_clashes[0].held_by == "Grace Hopper"
    assert await store.aliases_of(ada) == [], "the clashing alias was taken anyway"
    assert await store.aliases_of(grace) == ["The Countess"]
    assert outcome.added == 2, "the faces still landed; only the name was refused"


async def test_a_pack_carries_the_other_names_out_as_well_as_in(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The round trip. Without this a pack made here and read back somewhere else would quietly
    lose them, and the only packs that ever carried any would be the ones written by hand."""
    person_id = await make_person(temp_db, "Ada Lovelace")
    await store.add_reference(
        person_id,
        vector=person_vector(0),
        quality=0.9,
        crop=b"picture-bytes",
        origin=Origin.ADDED,
        recognizer=RECOGNISER,
    )
    await store.add_alias(person_id, "Ada Byron")

    raw = await service.export_pack(
        name="Mine", version="1", person_ids=[person_id], include_pictures=True
    )

    read_back = packs.read(raw, expect_recognizer=RECOGNISER)
    assert read_back.people[0].aliases == ("Ada Byron",)


# --- what a pack brought and could not place -------------------------------------------------
#
# Kept rather than discarded: without a place for an unclaimed person, a name matching nobody would
# leave two answers: create hundreds of People on the spot, or drop every face under it.


async def test_a_name_that_matches_nobody_is_kept_rather_than_discarded(
    service: FaceService, store: Store
) -> None:
    """The whole point. Reported AND held, with everything the pack said about them."""
    await service.import_pack(a_pack(people=["Ada Lovelace"], aliases={"Ada Lovelace": ("Ada",)}))

    waiting = await store.unclaimed_entries()

    assert [row["name"] for row in waiting] == ["Ada Lovelace"]
    assert len(await store.entry_faces(str(waiting[0]["id"]))) == 2, "the faces were kept too"


async def test_somebody_added_later_is_given_the_faces_that_were_waiting(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The payoff: added by hand weeks after the import, and recognizable immediately."""
    await service.import_pack(a_pack(people=["Ada Lovelace"]))
    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Ada Lovelace', 0)", (person_id,)
    )

    claimed = await service.claim_for(person_id, "Ada Lovelace")

    assert claimed == 2
    assert len(await store.references(person_id)) == 2
    # And they say where they came from, which a claim is the last chance to write down.
    origins = await temp_db.fetch_all(
        "SELECT origin FROM face_references WHERE person_id = ?", (person_id,)
    )
    assert {str(row["origin"]) for row in origins} == {"pack"}


async def test_a_name_a_pack_knew_only_as_an_also_known_as_still_matches(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A pack's aliases are how somebody is recognized under the name THIS library calls them."""
    await service.import_pack(
        a_pack(people=["Ada Lovelace"], aliases={"Ada Lovelace": ("Countess",)})
    )
    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Countess', 0)", (person_id,)
    )

    assert await service.claim_for(person_id, "Countess") == 2


async def test_claiming_twice_hands_over_nothing_the_second_time(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    await service.import_pack(a_pack(people=["Ada Lovelace"]))
    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Ada Lovelace', 0)", (person_id,)
    )

    first = await service.claim_for(person_id, "Ada Lovelace")
    again = await service.claim_for(person_id, "Ada Lovelace")

    assert (first, again) == (2, 0)
    assert len(await store.references(person_id)) == 2


async def test_importing_the_same_pack_twice_keeps_one_entry(
    service: FaceService, store: Store
) -> None:
    raw = a_pack(people=["Ada Lovelace"])

    await service.import_pack(raw)
    await service.import_pack(raw)

    waiting = await store.unclaimed_entries()
    assert len(waiting) == 1
    assert len(await store.entry_faces(str(waiting[0]["id"]))) == 2


async def test_a_name_nothing_is_waiting_for_claims_nothing(
    service: FaceService, temp_db: Database
) -> None:
    """The ordinary case, and it must be silent: most People are added with nobody waiting."""
    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Nobody Special', 0)", (person_id,)
    )

    assert await service.claim_for(person_id, "Nobody Special") == 0
    assert await service.waiting_for("Nobody Special") == []


async def test_a_person_a_swap_made_leaves_nothing_waiting(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A swap's person it made here gets the faces at once. An entry for her would be offered
    forever."""
    await make_person(temp_db, "Ada Lovelace")

    await service.take_from_swap(a_pack(people=["Ada Lovelace"]), suggest_only=False)

    assert await store.unclaimed_entries() == []


async def test_a_pack_that_adds_nothing_asks_for_no_rematching(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The second import of the same pack. Nothing changed, so no whole-library pass is worth it."""
    raw = a_pack(people=["Ada Lovelace"])
    first = await service.import_pack(raw)
    assert first.added == 2

    again = await service.import_pack(raw)

    assert again.added == 0


async def test_a_pack_face_with_no_picture_is_still_kept_and_still_claimed(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """A pack of numbers alone: far smaller, still able to recognize, and carrying no photographs
    of anybody off the machine it was made on. It has to survive being held and handed over."""
    await service.import_pack(a_pack(people=["Ada Lovelace"], include_pictures=False))
    waiting = await store.unclaimed_entries()
    assert len(await store.entry_faces(str(waiting[0]["id"]))) == 2

    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Ada Lovelace', 0)", (person_id,)
    )

    assert await service.claim_for(person_id, "Ada Lovelace") == 2


async def test_claiming_a_face_the_person_already_holds_counts_it_once(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """The entry and the person can hold the same picture: somebody imported the pack twice with
    creation on in between. The count says what ARRIVED, not what was attempted."""
    await service.import_pack(a_pack(people=["Ada Lovelace"]))
    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Ada Lovelace', 0)", (person_id,)
    )
    # One of the two already theirs, by the same picture identity the entry holds it under.
    waiting = await store.unclaimed_entries()
    faces = await store.entry_faces(str(waiting[0]["id"]))
    await store.add_reference(
        person_id,
        vector=recognize.unpack(bytes(faces[0]["embedding"])),
        quality=1.0,
        crop=None,
        digest=str(faces[0]["crop_digest"]),
        origin=Origin.PACK,
        recognizer=str(faces[0]["recognizer"]),
    )

    assert await service.claim_for(person_id, "Ada Lovelace") == 1
    assert len(await store.references(person_id)) == 2


# --- a pack from the other face model, with its pictures (a swap's) -------------------------------


def test_a_pack_from_the_other_model_is_read_only_when_the_caller_asks() -> None:
    raw = a_pack(recognizer="some-other-model")
    with pytest.raises(PackError):
        packs.read(raw, expect_recognizer=RECOGNISER)
    pack = packs.read(raw, expect_recognizer=RECOGNISER, other_model=True)
    assert pack.recognizer == "some-other-model"


async def test_the_other_models_faces_are_described_again_here_from_their_pictures(
    service: FaceService, store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The numbers that came are thrown away and the pictures described by this machine's model;
    a face with no picture is left out, because its numbers mean nothing here."""
    import numpy as np

    from sift.slices.faces import crop as cropping

    async def decode(pictures: list[bytes], settings: object) -> list[np.ndarray]:
        return [np.zeros((4, 4, 3), dtype=np.uint8) for _ in pictures]

    monkeypatch.setattr(cropping, "decode", decode)
    known = await make_person(temp_db, "Ada Lovelace")
    pictured = a_pack(recognizer="some-other-model", people=["Ada Lovelace"], faces_each=2)
    numbers_only = a_pack(
        name="Numbers",
        recognizer="some-other-model",
        people=["Ada Lovelace"],
        include_pictures=False,
    )

    outcome = await service.take_from_swap(pictured, suggest_only=False, other_model=True)
    none = await service.take_from_swap(numbers_only, suggest_only=False, other_model=True)

    assert (outcome.added, none.added) == (2, 0)
    assert len(await store.references(known)) == 2
    rows = await temp_db.fetch_all(
        "SELECT recognizer FROM face_references WHERE person_id = ?", (known,)
    )
    assert {str(row["recognizer"]) for row in rows} == {RECOGNISER}


async def test_pictures_this_machine_cannot_read_cost_that_person_their_faces_and_nobody_else(
    service: FaceService, store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decoder refuses a partial answer, so a person whose pictures fail to read is taken in
    with no faces, and the next person's are still described."""
    import numpy as np

    from sift.slices.faces import crop as cropping

    calls: list[int] = []

    async def decode(pictures: list[bytes], settings: object) -> list[np.ndarray]:
        calls.append(len(pictures))
        if len(calls) == 1:
            raise ValueError("not a picture this decoder reads")
        return [np.zeros((4, 4, 3), dtype=np.uint8) for _ in pictures]

    monkeypatch.setattr(cropping, "decode", decode)
    first = await make_person(temp_db, "Ada Lovelace")
    second = await make_person(temp_db, "Grace Hopper")
    pictured = a_pack(
        recognizer="some-other-model", people=["Ada Lovelace", "Grace Hopper"], faces_each=2
    )

    outcome = await service.take_from_swap(pictured, suggest_only=False, other_model=True)

    assert outcome.added == 2
    assert calls == [2, 2]
    kept = [len(await store.references(one)) for one in (first, second)]
    assert sorted(kept) == [0, 2]


async def test_a_pack_from_the_other_model_is_still_refused_from_a_file(
    service: FaceService,
) -> None:
    with pytest.raises(PackError, match="can't be compared"):
        await service.import_pack(a_pack(recognizer="some-other-model"))


async def test_a_name_a_pack_adds_tells_every_admin_and_one_already_held_tells_nobody(
    store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person's page draws the names they answer to. A pack adding one tells every tab, or
    a page left open would keep the old names until it was reloaded."""
    told: list[tuple[object, object]] = []
    monkeypatch.setattr(store_module, "announce", lambda who, about: told.append((who, about)))
    person_id = await make_person(temp_db, "Nadia Vance")

    assert await store.add_alias(person_id, "Nadia V")
    assert told == [(EVERY_ADMIN, About.LIBRARY)]
    assert not await store.add_alias(person_id, "Nadia V")
    assert told == [(EVERY_ADMIN, About.LIBRARY)]
