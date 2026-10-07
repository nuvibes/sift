# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where faces are kept, who may see one, and what deleting really deletes.

The visibility test is the one that matters here. **A face crop is a fragment of the file it came
from, so being shown one asks exactly the question being shown the file asks**, and it asks it of
the same rule, rather than of a second copy of that rule which would drift and turn this surface
into the way around the first one.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

# For its side effect: registering the table the ledger is written to, so a slice test's
# database has it. Registration happens at import and the fixtures migrate at setup.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Role
from sift.kernel.access.history_events import events_of_entity
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Ingested,
    Root,
    VerdictProduct,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.ledger import Actor
from sift.slices.faces import recognize, tracking
from sift.slices.faces.models import (
    Appearance,
    Attribution,
    Box,
    Described,
    Detection,
    PileStatus,
    Quality,
    ScanStatus,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import PassRecord, Remeasured, Store
from sift.slices.faces.tests.conftest import CORPUS, make_person, person_vector
from sift.slices.faces.tuning import QUALITY_VERSION
from sift.testing.fixtures import Actors, create_user

pytestmark = pytest.mark.integration


def an_appearance(timestamp_ms: int = 0, who: int = 0) -> Appearance:
    """One appearance. `who` picks the description, for a test that needs two that differ.

    Everything keyed on a description (a naming remembered, a grouping remembered) finds its
    rows by the numbers rather than by an id, because the ids do not survive a rescan. Two
    appearances sharing one description are therefore genuinely one thing to those tables, so a
    test about "only these faces" has to give them descriptions that differ.
    """
    detection = Detection(
        box=Box(x=10, y=10, width=100, height=100),
        score=0.9,
        landmarks=((1.0, 1.0),) * 5,
        timestamp_ms=timestamp_ms,
    )
    quality = Quality(pixels=100, sharpness=500.0, frontality=0.9, score=0.8, accepted=True)
    return Appearance(
        started_ms=timestamp_ms,
        ended_ms=timestamp_ms,
        seen_in=1,
        quality=0.8,
        faces=(
            Described(
                detection=detection,
                quality=quality,
                vector=person_vector(who),
                # The picture is passed in separately here: this is about what the store writes,
                # not about what the pipeline produced.
                chip=np.zeros((1, 1, 3), dtype=np.uint8),
            ),
        ),
    )


async def test_one_description_per_appearance_is_taken_and_it_is_the_best(
    store: Store, clip: Ingested
) -> None:
    """What a resumed pass compares its own findings against.

    One per appearance, not one per face: an appearance with three faces in it is still one thing
    that either continues or does not, and comparing against all of them would let a poor frame of
    somebody decide that a good frame of somebody else is the same person.
    """
    one = an_appearance(0)
    several = Appearance(
        started_ms=0,
        ended_ms=1000,
        seen_in=3,
        quality=0.8,
        faces=(one.faces[0], one.faces[0], one.faces[0]),
    )
    await store.replace_pass(
        clip.asset.id,
        [several],
        [[b"\xff\xd8\xff one", b"\xff\xd8\xff two", b"\xff\xd8\xff three"]],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=3,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest="abcd1234",
        ),
    )

    assert len(await store.appearance_vectors(clip.asset.id)) == 1


async def record(
    store: Store, asset_id: str, count: int = 1, *, distinct: bool = False
) -> list[str]:
    """A pass over one file. `distinct` gives each appearance a description of its own.

    Off by default: most tests here need only one description. On for anything that reads a
    table keyed by description, where two identical ones are one row rather than two.
    """
    appearances = [
        an_appearance(index * 1000, who=index * 2 if distinct else 0) for index in range(count)
    ]
    return await store.replace_pass(
        asset_id,
        appearances,
        [
            [b"\xff\xd8\xff picture %d" % index if distinct else b"\xff\xd8\xff picture"]
            for index in range(len(appearances))
        ],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=count,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest="abcd1234",
        ),
    )


# --- visibility ---------------------------------------------------------------------------------------


async def test_a_crop_from_a_file_the_viewer_may_not_see_is_not_served(
    service: FaceService,
    store: Store,
    access: Repository,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """Asked of the one rule that decides it, never re-implemented here.

    An admin may see the file, so may see the face taken out of it. A guest who may not see the
    file may not see the face either. Otherwise this surface becomes a way around concealment.
    """
    track_id = (await record(store, clip.asset.id))[0]
    admin = await create_user(temp_db, Role.ADMIN)
    guest = await create_user(temp_db, Role.GUEST)

    assert await service.may_see_crop(admin, track_id) is True
    assert await service.may_see_crop(guest, track_id) is False


async def test_a_face_that_does_not_exist_is_not_served_to_anybody(
    service: FaceService, temp_db: Database
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)

    assert await service.may_see_crop(admin, "no-such-face") is False


# --- what a pass writes -----------------------------------------------------------------------------------


async def test_a_pass_writes_its_faces_their_pictures_and_where_it_got_to(
    store: Store, clip: Ingested
) -> None:
    track_ids = await record(store, clip.asset.id, count=2)

    assert len(track_ids) == 2
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert scan.status is ScanStatus.NONE_IDENTIFIED
    assert scan.coverage == 1.0
    assert scan.detector == "test-detector"
    assert scan.settings_digest == "abcd1234"
    faces = await store.faces_of(track_ids[0])
    assert len(faces) == 1
    assert store.resolve(faces[0].crop_path).is_file()


async def test_every_appearances_faces_come_back_in_one_read(store: Store, clip: Ingested) -> None:
    """What the matching pass asks for, rather than a read once per APPEARANCE inside its loop
    (over a hundred reads on a busy file, each answering in microseconds and each a hop off the
    loop): every one of those appearances belongs to the one file being scanned."""
    track_ids = await record(store, clip.asset.id, count=3)

    together = await store.faces_of_many(track_ids)

    assert set(together) == set(track_ids)
    for track_id in track_ids:
        assert [face.id for face in together[track_id]] == [
            face.id for face in await store.faces_of(track_id)
        ]


async def test_an_appearance_nobody_has_faces_for_is_simply_absent(
    store: Store, clip: Ingested
) -> None:
    """Rather than an empty list under a key, and rather than an error. The caller reads absent and
    empty as the same answer, which is the honest one: there is nothing to compare."""
    track_ids = await record(store, clip.asset.id, count=1)

    together = await store.faces_of_many([*track_ids, "no-such-appearance"])

    assert "no-such-appearance" not in together
    assert len(together) == 1


async def test_asking_for_the_same_appearance_twice_reads_it_once(
    store: Store, clip: Ingested
) -> None:
    track_ids = await record(store, clip.asset.id, count=1)

    together = await store.faces_of_many([track_ids[0], track_ids[0]])

    assert len(together[track_ids[0]]) == len(await store.faces_of(track_ids[0]))


async def test_a_second_pass_replaces_the_first_rather_than_adding_to_it(
    store: Store, clip: Ingested
) -> None:
    """Merging would mean deciding whether a face found this time is the same one found last time
    under a different model, which is a question with no good answer."""
    await record(store, clip.asset.id, count=3)
    await record(store, clip.asset.id, count=1)

    assert len(await store.tracks_of(clip.asset.id)) == 1


async def test_a_pass_that_writes_a_picture_where_the_last_one_was_keeps_it(
    store: Store, clip: Ingested, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pass being replaced has its pictures removed afterwards, and this is the case that has
    to be excluded from that: a new face written to the very path an old one held.

    It cannot happen with real identities, which is exactly why the guard is worth having rather
    than trusting: the cost of being wrong is a face that renders as a broken picture, and the
    thing that would make it happen is a change to how identities are minted, somewhere else.
    """
    await record(store, clip.asset.id, count=1)
    first = [
        store.resolve(face.crop_path)
        for face in await store.faces_of((await store.tracks_of(clip.asset.id))[0].id)
    ]

    # The first identity minted by a pass is the track's; the face's is the one after it, and the
    # face's is what names the picture.
    from sift.kernel import ids

    real = ids.new_id
    handed_out = iter([real(), *(path.stem for path in first)])
    monkeypatch.setattr("sift.slices.faces.store_found.new_id", lambda: next(handed_out, real()))
    await record(store, clip.asset.id, count=1)

    assert first[0].is_file()


async def test_removing_a_face_from_a_file_with_no_scan_row_is_not_an_error(
    store: Store, clip: Ingested
) -> None:
    """A row written by an import that never finished, or a scan row cleared by hand. What is
    recorded is which recognizer described the face, and the honest answer is that nothing says."""
    track_ids = await record(store, clip.asset.id, count=1)
    await store._db.execute("DELETE FROM face_scans WHERE asset_id = ?", (clip.asset.id,))

    assert await store.remove_faces(track_ids) == 1


async def test_removing_an_appearance_seen_in_several_frames_records_it_once(
    store: Store, clip: Ingested
) -> None:
    """An appearance keeps its best few frames, so a removal has several rows to read and one thing
    to remember. Recorded per frame, the same face would be compared against several times over on
    every later scan of that file, for no gain."""
    one = an_appearance(0)
    several = Appearance(
        started_ms=0,
        ended_ms=1000,
        seen_in=3,
        quality=0.8,
        faces=(one.faces[0], one.faces[0], one.faces[0]),
    )
    track_ids = await store.replace_pass(
        clip.asset.id,
        [several],
        [[b"\xff\xd8\xff one", b"\xff\xd8\xff two", b"\xff\xd8\xff three"]],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=3,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest="abcd1234",
        ),
    )

    assert await store.remove_faces(track_ids) == 1

    assert len(await store.removals_for(clip.asset.id, "test-recognizer")) == 1
    assert await store.removals_for(clip.asset.id, "another-model") == [], (
        "a removal is a description, and only comparable with the model that made it"
    )
    assert not any(store.detected_root.rglob("*.jpg"))


async def test_each_persons_newest_faces_come_first_and_one_run_crowds_out_nobody(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The first faces of each card on the People Sift can recognize wall, read for several people at
    once: each person's newest decisions first, and at most `each` of each, so one person's run
    of thousands takes no place from anybody else. Read library-wide, the newest few thousand faces
    can all belong to one busy person.
    """
    track_ids = await record(store, clip.asset.id, count=5)
    busy = await make_person(temp_db, "Ada Lovelace")
    quiet = await make_person(temp_db, "Grace Hopper")
    await store.attribute(track_ids[0], quiet, confidence=0.9, attribution=Attribution.MATCHED)
    for track_id in track_ids[1:]:
        await store.attribute(track_id, busy, confidence=0.9, attribution=Attribution.MATCHED)
    # Three writes in one millisecond tie in a test and in no real pass at all, so the
    # moments are moved apart by hand: the busy person's run is the newest.
    await temp_db.execute("UPDATE face_tracks SET attributed_at = 10 WHERE person_id = ?", (quiet,))
    for moment, track_id in enumerate(track_ids[1:], start=20):
        await temp_db.execute(
            "UPDATE face_tracks SET attributed_at = ? WHERE id = ?", (moment, track_id)
        )

    heads = await store.attributed_heads([busy, quiet], attribution=None, each=2)

    assert [track.id for track in heads] == [track_ids[4], track_ids[3], track_ids[0]]
    only_confirmed = await store.attributed_heads(
        [busy, quiet], attribution=Attribution.CONFIRMED, each=2
    )
    assert only_confirmed == []
    page = await store.attributed_page([busy], attribution=None, limit=2, offset=2)
    assert [track.id for track in page] == [track_ids[2], track_ids[1]]


async def test_taking_a_person_off_takes_their_faces_off_the_wall(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """Otherwise the wall lists an appearance nobody is attached to any more."""
    (track_id,) = await record(store, clip.asset.id, count=1)
    person = await make_person(temp_db, "Ada Lovelace")
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)
    assert [track.id for track in await store.surest_matched(person, limit=5, offset=0)] == [
        track_id
    ]

    await store.attribute(track_id, None, confidence=None, attribution=None)

    assert await store.attributed_heads([person], attribution=None, each=5) == []
    assert await store.surest_matched(person, limit=5, offset=0) == []


async def test_removing_nothing_asks_the_database_nothing(store: Store) -> None:
    assert await store.remove_faces([]) == 0
    assert await store.assets_of([]) == []


async def test_a_file_that_has_never_been_scanned_has_no_row_at_all(
    store: Store, clip: Ingested
) -> None:
    """The fifth state is the absence of a row, so it cannot disagree with reality."""
    assert await store.scan_of(clip.asset.id) is None
    assert await store.settled_ids("any-digest") == set()


async def test_which_of_these_files_still_need_looking_at(
    store: Store, clip: Ingested, other_clip: Ingested, content_store: ContentStore
) -> None:
    """The page-at-a-time form the Build asks: the same rule as `settled_ids`, over the ids given
    and no others, so a page of a thousand is one short query rather than the whole table.

    And the same rule a third time, as the term the Build's sheet counts the library by: asked
    the same questions as the page form here, so the two cannot drift apart unnoticed."""
    await store.replace_pass(clip.asset.id, [], [], a_pass_record())
    for one in (clip, other_clip):
        await content_store.record_probe(one.asset.id, width=16, height=16, duration_ms=1000)

    assert await store.unsettled_among([clip.asset.id, "never-seen"], "abcd1234") == {"never-seen"}
    # A different tuning SHAPE is not settled; a different digest alone with the same shape and a
    # density no higher is, which is the "looked at harder" half of the rule `settled_ids` states.
    assert await store.unsettled_among(
        [clip.asset.id], "another-tuning", shape="deeper", density=1.0
    ) == {clip.asset.id}
    assert await store.unsettled_among([], "abcd1234") == set()

    both = [clip.asset.id, other_clip.asset.id]
    for digest, shape, density in (("abcd1234", "", 0.0), ("another-tuning", "deeper", 1.0)):
        page = await store.unsettled_among(both, digest, shape=shape, density=density)
        counted = await content_store.count_lacking(
            [store.lack(digest, shape=shape, density=density)]
        )
        assert counted.each == (len(page),), (digest, shape, density)


async def test_the_backlog_says_why_each_file_wants_a_look_and_adds_up_to_the_whole_rule(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    content_store: ContentStore,
    temp_db: Database,
) -> None:
    """Never looked at, and looked at under an older rule: the two reasons `backlog` gives.

    Every file the whole rule finds wanting is in exactly one of them (a scan row exists or it
    does not), so the two always add up to what the Build counts for Faces. A file scanned under
    the rule in force is in neither; one scanned under an older quality rule moves to the second;
    and a file this feature has said it cannot read is in neither, as the sweep's own page leaves
    it out.
    """
    for one in (clip, other_clip):
        await content_store.record_probe(one.asset.id, width=16, height=16, duration_ms=1000)
    configured = await service.configuration()
    current = replace(
        a_pass_record(),
        settings_digest=configured.digest,
        settings_shape=configured.shape,
        settings_density=configured.density,
    )
    await store.replace_pass(clip.asset.id, [], [], current)

    async def counted() -> tuple[int, int, int]:
        never, before = await service.backlog()
        # The whole rule as the Build counts it: `lack` with the verdict it is filed under.
        whole = await content_store.count_lacking(
            [
                replace(
                    store.lack(
                        configured.digest, shape=configured.shape, density=configured.density
                    ),
                    product=VerdictProduct.FACES.value,
                )
            ]
        )
        return never, before, whole.each[0]

    assert await counted() == (1, 0, 1), "one never scanned, one scanned under the rule in force"
    await temp_db.execute(
        "UPDATE face_scans SET quality_version = ? WHERE asset_id = ?",
        (QUALITY_VERSION - 1, clip.asset.id),
    )
    assert await counted() == (1, 1, 2), "an older quality rule is the second reason"
    await content_store.record_verdict(
        other_clip.asset.id, VerdictProduct.FACES, code="no_copy", reason="gone"
    )
    assert await counted() == (0, 1, 1), "a file given up on is not waiting to be looked at"


async def test_the_best_description_of_each_appearance_is_what_matching_reads(
    store: Store, clip: Ingested
) -> None:
    track_ids = await record(store, clip.asset.id, count=2)

    best = await store.best_face_per_track()

    assert sorted(track_id for track_id, _ in best) == sorted(track_ids)


async def test_deleting_a_file_takes_its_faces_with_it(
    store: Store, content_store: ContentStore, clip: Ingested, temp_db: Database
) -> None:
    """An ordinary cascade, and worth a test because the pictures on disk are not part of it."""
    await record(store, clip.asset.id)

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (clip.asset.id,))

    assert await store.tracks_of(clip.asset.id) == []
    assert await store.scan_of(clip.asset.id) is None


# --- references -------------------------------------------------------------------------------------------


async def test_the_same_picture_offered_twice_for_one_person_is_one_reference(
    store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")

    first = await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"a picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    second = await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"a picture",
        origin=FaceOrigin.PACK,
        recognizer="test-recognizer",
    )

    assert first is not None
    assert second is None
    assert len(await store.references(person)) == 1


async def test_a_batch_of_references_lands_in_one_go_under_the_same_identity_rule(
    store: Store, temp_db: Database
) -> None:
    """A pack is a batch. One transaction for a person's faces rather than one per face, and the
    same rule as the single form: a picture already held for this person adds nothing, and a
    face may arrive as numbers alone with the identity of its picture stated."""
    person = await make_person(temp_db, "Ada Lovelace")
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"already here",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    added = await store.add_references(
        person,
        [
            (person_vector(0), 0.9, b"already here", None, None),
            (person_vector(1), 0.8, b"a new picture", None, 120),
            (person_vector(2), 0.7, None, "stated-identity", None),
            (person_vector(2), 0.7, None, "stated-identity", None),
        ],
        origin=FaceOrigin.PACK,
        recognizer="test-recognizer",
    )

    assert added == 2
    held = await store.references(person)
    assert len(held) == 3
    assert sum(1 for path in store.reference_root.rglob("*.jpg")) == 2, "one picture per face"
    with pytest.raises(ValueError, match="either a picture"):
        await store.add_references(
            person,
            [(person_vector(3), 0.5, None, None, None)],
            origin=FaceOrigin.PACK,
            recognizer="r",
        )


async def test_a_reference_of_numbers_alone_has_nothing_to_show_and_still_matches(
    store: Store, temp_db: Database
) -> None:
    """A pack carries descriptions by default and pictures only if whoever made it chose to."""
    person = await make_person(temp_db, "Ada Lovelace")

    reference_id = await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=None,
        digest="a-stated-identity",
        origin=FaceOrigin.PACK,
        recognizer="test-recognizer",
    )

    assert reference_id is not None
    assert await store.reference_picture(reference_id) is None
    assert len(await store.references(person)) == 1


async def test_a_reference_needs_either_a_picture_or_the_identity_of_one(
    store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")

    with pytest.raises(ValueError, match="either its picture or the identity"):
        await store.add_reference(
            person,
            vector=person_vector(0),
            quality=0.9,
            crop=None,
            origin=FaceOrigin.PACK,
            recognizer="test-recognizer",
        )


async def test_removing_a_persons_references_takes_their_pictures_too(
    store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    reference_id = await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"a picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    assert reference_id is not None
    on_disk = store.crop_path(store.reference_root, reference_id)
    assert on_disk.is_file()

    removed = await store.remove_references(person_id=person)

    assert removed == 1
    assert not on_disk.exists()


async def test_removing_references_needs_to_be_told_whose(store: Store) -> None:
    with pytest.raises(ValueError, match="needs a person or a pack"):
        await store.remove_references()


async def test_people_are_matched_by_name_ignoring_case_and_never_created(
    store: Store, temp_db: Database
) -> None:
    """The same rule usernames and site names already use. A name that matches nobody is a
    message, not an invitation to invent somebody."""
    person = await make_person(temp_db, "Ada Lovelace")

    found = await store.existing_people(["ADA LOVELACE", "Nobody At All", "  "])

    assert found == {"ada lovelace": person}
    assert await store.existing_people([]) == {}


async def test_a_name_that_is_somebodys_alias_finds_them_and_a_shared_alias_finds_nobody(
    store: Store, temp_db: Database
) -> None:
    """A gallery folder or a pack named by an alias is the person who carries it, so the import
    files their faces rather than creating a second of them. An alias two People share is left
    unmatched, and a name always wins over somebody else's alias."""
    ada = await make_person(temp_db, "Ada Lovelace")
    grace = await make_person(temp_db, "Grace Hopper")
    countess = await make_person(temp_db, "Countess Lovelace")
    for person, alias in (
        (ada, "Ada Byron"),
        (ada, "Bryn Calloway"),
        (grace, "Bryn Calloway"),
        (grace, "Countess Lovelace"),
    ):
        await temp_db.execute(
            "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
            (new_id(), person, alias),
        )

    found = await store.existing_people(["ada byron", "Bryn Calloway", "Countess Lovelace"])

    assert found == {"ada byron": ada, "countess lovelace": countess}


async def test_a_persons_name_is_read_and_a_missing_one_is_reported_as_missing(
    store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")

    assert await store.person_name(person) == "Ada Lovelace"
    assert await store.person_name("nobody") is None


# --- clearing out ---------------------------------------------------------------------------------------------


async def test_deleting_everything_removes_every_row_and_every_picture(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The plainly-labelled control, separate from the switch. Turning the feature off leaves what
    was collected; this is what somebody presses when they mean it."""
    person = await make_person(temp_db, "Ada Lovelace")
    await record(store, clip.asset.id, count=2)
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"a picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    await store.forget_everything(actor=Actor.sift("faces"))

    assert await store.tracks_of(clip.asset.id) == []
    assert await store.references() == []
    assert await store.scan_of(clip.asset.id) is None
    assert not store.detected_root.exists()
    assert not store.reference_root.exists()


async def test_deleting_everything_twice_is_not_an_error(store: Store) -> None:
    await store.forget_everything(actor=Actor.sift("faces"))
    await store.forget_everything(actor=Actor.sift("faces"))


async def test_the_faces_feature_being_wiped_is_written_down(
    store: Store, temp_db: Database, actors: Actors
) -> None:
    """Eleven tables emptied together leave a record behind; without one the library looks exactly
    like one that never had the feature switched on. The subject is the feature's own SETTING,
    which is the honest one: this is not an act on any person or file, because every one of them
    has gone."""
    await store.forget_everything(actor=Actor.user(actors.admin.id))

    (event,) = await events_of_entity(temp_db, actors.admin, "setting", "faces.enabled")
    assert (event.verb, event.actor_kind) == ("forgot", "user")


async def test_the_wipe_counts_the_files_it_took_its_names_off(
    store: Store, clip: Ingested, temp_db: Database, actors: Actors
) -> None:
    """A whole-library pass hands a count, and this one's is the fact nothing else keeps: how many
    files lost a name recognizing faces had put on them. A name put on by hand is not counted,
    because the wipe does not take it."""
    by_hand = await make_person(temp_db, "Bryn Calloway")
    by_face = await make_person(temp_db, "Cassia Lynn")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (clip.asset.id, by_hand)
    )
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, by_face, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)

    await store.forget_everything(actor=Actor.user(actors.admin.id))

    (event,) = await events_of_entity(temp_db, actors.admin, "setting", "faces.enabled")
    assert event.count == 1


# --- paths ---------------------------------------------------------------------------------------------------


async def test_a_stored_picture_path_that_points_outside_the_face_directory_is_refused(
    store: Store,
) -> None:
    """A row outlives the code that wrote it, and a database restored from a backup is exactly
    where a row written before a guard meets the code that assumes it."""
    with pytest.raises(ValueError, match="not inside"):
        store.resolve("../../etc/passwd")


async def test_pictures_are_fanned_out_rather_than_piled_into_one_directory(store: Store) -> None:
    """A single directory with a hundred thousand files in it is slow to list on every filesystem
    and unusable on some, and a library of any size produces exactly that."""
    path = store.crop_path(store.detected_root, "01ABCDEF")

    assert path.parent.name == "01"
    assert path.name == "01ABCDEF.jpg"


# --- installed models --------------------------------------------------------------------------------------------


async def test_what_was_installed_is_recorded_and_updated_in_place(store: Store) -> None:
    await store.record_weight("accurate.detector", "rev-1", "digest-1", 100)
    await store.record_weight("accurate.detector", "rev-2", "digest-2", 200)

    rows = await store.weights()

    assert len(rows) == 1
    assert str(rows[0]["revision"]) == "rev-2"


async def test_who_is_in_a_file_is_the_distinct_people_across_its_faces(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    track_ids = await record(store, clip.asset.id, count=2)
    for track_id in track_ids:
        await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)

    assert await store.people_with_faces(clip.asset.id) == [person]


async def test_taking_a_person_off_a_face_clears_the_confidence_that_went_with_it(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """A confidence left behind describes a decision nobody is making any more."""
    person = await make_person(temp_db, "Ada Lovelace")
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)

    await store.attribute(track_id, None, confidence=0.9, attribution=None)

    track = await store.track(track_id)
    assert track is not None
    assert track.person_id is None
    assert track.confidence is None


# --- the names this feature put on a file --------------------------------------------------------------------


async def people_on(database: Database, asset_id: str) -> list[str]:
    rows = await database.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ? ORDER BY person_id", (asset_id,)
    )
    return [str(row["person_id"]) for row in rows]


async def test_attributing_a_face_puts_that_person_on_the_file(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The point of recognizing a face: the person turns up where the rest of Sift reads them."""
    person = await make_person(temp_db, "Ada Lovelace")
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)

    added, removed = await store.reconcile_people(clip.asset.id)

    assert added == [person]
    assert removed == []
    assert await people_on(temp_db, clip.asset.id) == [person]


async def test_the_batched_reconcile_answers_what_asking_one_file_at_a_time_answers(
    store: Store, clip: Ingested, other_clip: Ingested, temp_db: Database
) -> None:
    """The property the batched form has to have: the same answer, per file, and the same rows.

    It stands in for a call per file on the path that names a group of faces, so what matters is
    that it is the SAME answer: a second account of what a face attribution does to a file would put
    somebody on a file on one screen and not on another.

    Two files with DIFFERENT people, because one person across both would pass with the batch
    keeping only the last file's answer, or with the two files' answers swapped. A file asked about
    with nothing to change is in the answer holding two empty lists, which is what lets a caller
    read it without checking.
    """
    ada = await make_person(temp_db, "Ada Lovelace")
    zelda = await make_person(temp_db, "Zelda Fitzgerald")
    first = (await record(store, clip.asset.id))[0]
    second = (await record(store, other_clip.asset.id))[0]
    await store.attribute(first, ada, confidence=0.9, attribution=Attribution.MATCHED)
    await store.attribute(second, zelda, confidence=0.9, attribution=Attribution.MATCHED)

    moved = await store.reconcile_people_of([clip.asset.id, other_clip.asset.id, "no-such-file"])

    assert moved[clip.asset.id] == ([ada], [])
    assert moved[other_clip.asset.id] == ([zelda], [])
    assert moved["no-such-file"] == ([], []), (
        "a file with nothing to change is present, not missing"
    )
    assert await people_on(temp_db, clip.asset.id) == [ada]
    assert await people_on(temp_db, other_clip.asset.id) == [zelda]


async def test_the_batched_reconcile_takes_people_off_as_well_as_putting_them_on(
    store: Store, clip: Ingested, other_clip: Ingested, temp_db: Database
) -> None:
    """Both directions in one batch, which is the case a one-directional loop passes and is wrong
    about: one file gaining somebody while another loses somebody, settled by one transaction."""
    ada = await make_person(temp_db, "Ada Lovelace")
    zelda = await make_person(temp_db, "Zelda Fitzgerald")
    first = (await record(store, clip.asset.id))[0]
    second = (await record(store, other_clip.asset.id))[0]
    await store.attribute(first, ada, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people_of([clip.asset.id])

    await store.attribute(first, None, confidence=None, attribution=None)
    await store.attribute(second, zelda, confidence=0.9, attribution=Attribution.MATCHED)
    moved = await store.reconcile_people_of([clip.asset.id, other_clip.asset.id])

    assert moved[clip.asset.id] == ([], [ada]), "the one that lost somebody"
    assert moved[other_clip.asset.id] == ([zelda], []), "and the one that gained somebody"
    assert await people_on(temp_db, clip.asset.id) == []
    assert await people_on(temp_db, other_clip.asset.id) == [zelda]


async def test_withdrawing_the_last_face_takes_that_person_back_off_the_file(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)

    await store.attribute(track_id, None, confidence=None, attribution=None)
    added, removed = await store.reconcile_people(clip.asset.id)

    assert added == []
    assert removed == [person]
    assert await people_on(temp_db, clip.asset.id) == []


async def test_a_name_somebody_put_on_by_hand_is_never_taken_off(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The whole reason the claim is recorded at all.

    Somebody drags a person onto a file. Later a face in that file is attributed to them and then
    withdrawn: a rejected suggestion, a re-match that changed its mind. The pair in the shared
    table looks identical either way, so without a record of who wrote it the withdrawal would take
    away what a person decided.

    The insert is what decides: it found the row already there, wrote nothing, and so claimed
    nothing. Nothing this feature did not create is ever removed.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (clip.asset.id, person)
    )
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)

    added, _ = await store.reconcile_people(clip.asset.id)
    assert added == [], "a pair that was already there is not this feature's to claim"
    assert await store.claimed_people(clip.asset.id) == []

    await store.attribute(track_id, None, confidence=None, attribution=None)
    _, removed = await store.reconcile_people(clip.asset.id)

    assert removed == []
    assert await people_on(temp_db, clip.asset.id) == [person], (
        "withdrawing a face attribution removed a name somebody attached by hand"
    )


async def test_a_person_stays_on_the_file_while_any_face_still_names_them(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """Several faces in one file can be the same person, so the name goes when the last one does.

    Withdrawn by the pair rather than by the face for exactly this: a photograph where somebody
    appears twice, one of the two rejected, and they are still in the picture.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    first, second = await record(store, clip.asset.id, count=2)
    for track_id in (first, second):
        await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)

    await store.attribute(first, None, confidence=None, attribution=None)
    added, removed = await store.reconcile_people(clip.asset.id)

    assert (added, removed) == ([], [])
    assert await people_on(temp_db, clip.asset.id) == [person]


async def test_deleting_everything_takes_back_its_own_names_and_leaves_the_rest(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The plainly-labelled control removes what recognizing faces produced. A decision somebody
    made themselves has never been part of that."""
    by_hand = await make_person(temp_db, "Grace Hopper")
    by_face = await make_person(temp_db, "Ada Lovelace")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (clip.asset.id, by_hand)
    )
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, by_face, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)
    assert await people_on(temp_db, clip.asset.id) == sorted([by_hand, by_face])

    await store.forget_everything(actor=Actor.sift("faces"))

    assert await people_on(temp_db, clip.asset.id) == [by_hand]
    assert await store.claimed_people(clip.asset.id) == []


# --- a pile of part of a pile --------------------------------------------------------------------


async def test_the_whole_list_of_piles_can_be_read_without_a_page(
    store: Store, clip: Ingested
) -> None:
    """Both routes through the reader, because they are two statements and the unpaged one is what
    the regrouping reads."""
    await record(store, clip.asset.id, count=1)
    tracks = await store.tracks_of(clip.asset.id)
    middle = tuple(0.0 for _ in range(8))
    await store.replace_piles([(middle, [tracks[0].id])])

    assert len(await store.piles(None)) == 1
    assert len(await store.piles(None, limit=10, offset=0)) == 1
    assert len(await store.piles(PileStatus.OPEN, limit=10, offset=0)) == 1


async def test_a_pile_can_be_read_by_its_own_identity(store: Store, clip: Ingested) -> None:
    await record(store, clip.asset.id, count=1)
    tracks = await store.tracks_of(clip.asset.id)
    made = await store.replace_piles([(tuple(0.0 for _ in range(8)), [tracks[0].id])])

    assert await store.pile_of(made[0]) is not None
    assert await store.pile_of("no-such-pile") is None


async def test_a_piles_faces_can_be_read_a_page_at_a_time(store: Store, clip: Ingested) -> None:
    await record(store, clip.asset.id, count=3)
    tracks = await store.tracks_of(clip.asset.id)
    made = await store.replace_piles(
        [(tuple(0.0 for _ in range(8)), [track.id for track in tracks])]
    )

    assert len(await store.pile_tracks(made[0])) == 3
    assert len(await store.pile_tracks(made[0], limit=2)) == 2
    assert len(await store.pile_tracks(made[0], limit=2, offset=2)) == 1
    assert await store.count_pile_tracks(made[0]) == 3
    assert await store.count_pile_tracks("no-such-pile") == 0


async def test_a_rebuild_keeps_the_piles_it_is_told_to_and_deletes_the_rest(
    store: Store, clip: Ingested
) -> None:
    first, second = await record(store, clip.asset.id, 2, distinct=True)
    old_a, old_b = await store.replace_piles(
        [(person_vector(0), [first]), (person_vector(2), [second])]
    )

    made = await store.replace_piles(
        [(person_vector(2, 1), [second]), (person_vector(5), [first])], kept={0: old_b}
    )

    assert made[0] == old_b, "the pile it was told to keep is the same pile"
    assert made[1] not in (old_a, old_b), "the other is new"
    assert {str(row["id"]) for row in await store.piles(PileStatus.OPEN)} == {old_b, made[1]}
    kept = await store.pile_of(old_b)
    assert kept is not None and recognize.unpack(bytes(kept["centroid"])) == pytest.approx(
        person_vector(2, 1), abs=1e-6
    )
    assert [track.id for track in await store.pile_tracks(old_b)] == [second]


async def test_the_floor_pass_reads_only_refusals_a_lower_floor_would_accept(
    store: Store, temp_db: Database, content_store: ContentStore, library: Root, settings: Settings
) -> None:
    """Between today's floor and the one every earlier scan was taken at, exclusive at both ends:
    a face refused at a floor of 96 is under 96 and rounds to 96 at most, so a file looked at again
    leaves the list whatever it finds, and the pass ends."""
    largest = {"a": 95, "b": 96, "c": 97, "d": 105, "e": 111, "f": 112, "g": None}
    ids: dict[str, str] = {}
    for name, refused in largest.items():
        target = Path(str(library.abs_path)) / f"{name}.mp4"
        target.write_bytes((CORPUS / "accepted.mp4").read_bytes() + name.encode())
        checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
        landed = await content_store.ingest(checked, root_id=library.id, rel_path=f"{name}.mp4")
        ids[name] = landed.asset.id
        await record(store, landed.asset.id, 1)
        await temp_db.execute(
            "UPDATE face_scans SET refused_largest = ? WHERE asset_id = ?",
            (refused, landed.asset.id),
        )

    band = await store.under_an_earlier_floor(96)

    assert band == sorted(ids[name] for name in ("c", "d", "e"))
    assert await store.under_an_earlier_floor(112) == []
    assert await store.under_an_earlier_floor(96, after=band[0], limit=1) == [band[1]]


async def test_a_heif_photo_whose_faces_were_read_from_one_tile_is_looked_at_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    content_store: ContentStore,
    clip: Ingested,
    other_clip: Ingested,
) -> None:
    """A HEIF still's scan older than its whole-picture copy, or with no copy yet, read one tile
    of it; a scan taken since reads the copy and leaves the list, so the pass ends. A file that is
    not a HEIF still is never on it."""
    await temp_db.execute(
        "UPDATE assets SET media_type = 'image', mime = 'image/heic' WHERE id = ?",
        (clip.asset.id,),
    )
    await record(store, clip.asset.id, 1)
    await record(store, other_clip.asset.id, 1)

    assert (await service.read_from_a_tile())[0] == [clip.asset.id], "no copy yet: a tile"
    assert await service.tile_pass_owed() is True

    await content_store.add_derivative(
        clip.asset.id, DerivativeKind.RENDITION, extension="jpg", size_bytes=1
    )
    await temp_db.execute(
        "UPDATE face_scans SET scanned_at = 0 WHERE asset_id = ?", (clip.asset.id,)
    )
    assert (await service.read_from_a_tile())[0] == [clip.asset.id], "older than its copy"

    await temp_db.execute(
        "UPDATE face_scans SET scanned_at = ? WHERE asset_id = ?",
        (4_000_000_000_000, clip.asset.id),
    )
    assert (await service.read_from_a_tile())[0] == []
    assert await service.tile_pass_owed() is False


async def _proposal_folder(temp_db: Database, clip: Ingested) -> str:
    """A folder for a proposal to name, under the clip's own root."""
    row = await temp_db.fetch_one(
        "SELECT root_id FROM asset_locations WHERE asset_id = ?", (clip.asset.id,)
    )
    assert row is not None
    folder_id = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
        "VALUES (?, ?, NULL, 'Hers', 'Hers')",
        (folder_id, str(row["root_id"])),
    )
    return folder_id


async def test_a_rebuild_carries_a_proposal_to_the_pile_that_holds_most_of_its_faces(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A full regroup that remakes a proposed group under a new id keeps the question on the
    board: the proposal follows the faces, in the state it was in."""
    faces = await record(store, clip.asset.id, 3, distinct=True)
    (old,) = await store.replace_piles([(person_vector(0), faces)])
    person = await make_person(temp_db, "Esme Wrenfield")
    folder = await _proposal_folder(temp_db, clip)
    assert await store.propose_pile(
        old, person, reason="folder", folder_id=folder, files=3, of_files=3
    )

    made = await store.replace_piles([(person_vector(5), faces[:2]), (person_vector(7), faces[2:])])

    rows = await temp_db.fetch_all(
        "SELECT pile_id, person_id, folder_id, files, state FROM face_pile_proposals", ()
    )
    assert [tuple(row) for row in rows] == [(made[0], person, folder, 3, "pending")]


async def test_a_rebuild_that_scatters_a_group_leaves_its_proposal_to_the_folder_pass(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """No pile holds more than half of the old group's faces: the group did not survive, so
    nothing is carried, and the folder pass makes the proposal for whichever group leads."""
    faces = await record(store, clip.asset.id, 2, distinct=True)
    (old,) = await store.replace_piles([(person_vector(0), faces)])
    person = await make_person(temp_db, "Esme Wrenfield")
    folder = await _proposal_folder(temp_db, clip)
    await store.propose_pile(old, person, reason="folder", folder_id=folder, files=2, of_files=2)

    await store.replace_piles([(person_vector(5), faces[:1]), (person_vector(7), faces[1:])])

    assert await temp_db.fetch_all("SELECT pile_id FROM face_pile_proposals", ()) == []


async def test_faces_in_no_pile_are_what_an_incremental_grouping_places(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """In no pile: never piled, or piled into a pile that has since gone. Not the faces in a
    pile that exists, not the named ones, and not the faces another model described."""
    piled, gone, never, named = await record(store, clip.asset.id, 4, distinct=True)
    (pile,) = await store.replace_piles([(person_vector(0), [piled])])
    await store.add_piles([(person_vector(2), [gone])])
    await temp_db.execute("DELETE FROM face_piles WHERE id != ?", (pile,))
    person = await make_person(temp_db, "Ada Lovelace")
    await store.attribute(named, person, confidence=1.0, attribution=Attribution.CONFIRMED)

    loose = await store.unpiled("test-recognizer")

    assert sorted(track_id for track_id, _, _ in loose) == sorted([gone, never])
    assert await store.unpiled("another-model") == []


async def test_joining_nothing_or_a_named_face_leaves_a_piles_middle_where_it_was(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """A pile handed no faces is not touched, and a face that has a person is not one of the
    unnamed the middle is measured over: a pile of named faces only has no middle to move."""
    first, second = await record(store, clip.asset.id, 2, distinct=True)
    (pile,) = await store.replace_piles([(person_vector(0), [first])])
    was = await store.pile_of(pile)
    assert was is not None
    person = await make_person(temp_db, "Ada Lovelace")
    for track_id in (first, second):
        await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)

    await store.add_to_piles({pile: []})
    await store.add_to_piles({pile: [second]})

    now = await store.pile_of(pile)
    assert now is not None
    assert bytes(now["centroid"]) == bytes(was["centroid"])


async def test_joining_a_pile_moves_its_middle_and_its_count(store: Store, clip: Ingested) -> None:
    first, second, third = await record(store, clip.asset.id, 3, distinct=True)
    (pile,) = await store.replace_piles([(person_vector(0), [first])])
    was = await store.pile_of(pile)
    assert was is not None and int(was["size"]) == 1

    await store.add_to_piles({pile: [second, third]})

    now = await store.pile_of(pile)
    assert now is not None and int(now["size"]) == 3
    assert sorted(track.id for track in await store.pile_tracks(pile)) == sorted(
        [first, second, third]
    )
    expected = tracking.centroid([person_vector(0), person_vector(2), person_vector(4)])
    assert recognize.unpack(bytes(now["centroid"])) == pytest.approx(expected, abs=1e-6)
    await store.add_to_piles({})


async def test_setting_aside_nothing_makes_no_pile(store: Store) -> None:
    """A call naming nothing is not a call about everything."""
    assert await store.set_aside([]) is None
    assert await store.set_aside(["no-such-track"]) is None


async def test_a_pile_with_nothing_unclaimed_left_in_it_is_dropped(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A question that has been answered. Left behind, it is a count that never comes down."""
    await record(store, clip.asset.id, count=1)
    tracks = await store.tracks_of(clip.asset.id)
    made = await store.replace_piles([(tuple(0.0 for _ in range(8)), [tracks[0].id])])
    person_id = await make_person(temp_db, "Ada")

    assert await store.drop_empty_piles() == 0

    await store.attribute(tracks[0].id, person_id, confidence=1.0, attribution=None)

    assert await store.drop_empty_piles() == 1
    assert await store.pile_of(made[0]) is None


async def test_how_many_reference_faces_somebody_has(store: Store, temp_db: Database) -> None:
    person_id = await make_person(temp_db, "Ada")

    assert await store.reference_count(person_id) == 0

    await store.add_reference(
        person_id,
        vector=tuple(1.0 if index == 0 else 0.0 for index in range(8)),
        quality=0.9,
        crop=b"\xff\xd8\xff\xdb x",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )

    assert await store.reference_count(person_id) == 1


async def _cover_of(temp_db: Database, person: str) -> tuple[object, object, object]:
    row = await temp_db.fetch_one(
        "SELECT cover_asset_id, cover_track_id, cover_by_default FROM people WHERE id = ?",
        (person,),
    )
    assert row is not None
    return row["cover_asset_id"], row["cover_track_id"], row["cover_by_default"]


async def test_a_cover_taken_back_is_only_the_face_that_gave_it_and_the_default_returns(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The undo of a face's cover takes it off only while it is still that face, and leaves the
    person as the rule for an empty cover would: their first file's still, marked as the default."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=1)
    # A face cover as a receipt written before catalog 79 knows it: nothing makes one any more.
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_track_id = ? WHERE id = ?",
        (clip.asset.id, tracks[0], person),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, NULL, 0)",
        (clip.asset.id, person),
    )

    assert await store.take_back_a_cover(person, "another-face", actor=Actor.sift("faces")) is False
    assert await store.take_back_a_cover(person, tracks[0], actor=Actor.sift("faces")) is True

    assert await _cover_of(temp_db, person) == (clip.asset.id, None, clip.asset.id)


def test_where_a_cover_picture_is_written(store: Store) -> None:
    """Fanned out like the crops. One directory holding a hundred thousand files is slow on every
    filesystem and unusable on some."""
    path = store.cover_path("01HXABCDEF")

    assert path.parent == store.cover_root / "01"
    assert path.name == "01HXABCDEF.jpg"


# --- moving faces between piles by hand -----------------------------------------------------------


NO_SUCH_PILE = "01HX0000000000000000000099"


async def test_moving_no_faces_at_all_is_not_a_move(store: Store) -> None:
    """A call naming nothing is not a call about everything: the same rule setting aside has."""
    assert await store.move_tracks([], None) is None
    assert await store.move_tracks([], NO_SUCH_PILE) is None


async def test_moving_faces_with_no_description_behind_them_makes_no_pile(store: Store) -> None:
    """A hand-made pile's middle is the average of what went into it, and there is nothing to
    average. Refused rather than written with an empty centroid, which would match everything."""
    assert await store.move_tracks(["01HX0000000000000000000098"], None) is None


async def test_moving_faces_into_a_pile_that_is_not_there_is_refused(
    store: Store, clip: Ingested
) -> None:
    """The destination is chosen from a list on screen, and a group can be named or set aside
    between the list being drawn and the press. Nothing is moved rather than a pile invented."""
    tracks = await record(store, clip.asset.id, count=1)

    assert await store.move_tracks(tracks, NO_SUCH_PILE) is None


async def test_moving_faces_into_a_new_pile_marks_it_as_one_somebody_built(
    store: Store, clip: Ingested
) -> None:
    """The split. The flag is the whole of what stops the next grouping pass throwing it away."""
    tracks = await record(store, clip.asset.id, count=2)

    made = await store.move_tracks(tracks, None)

    assert made is not None
    pile = await store.pile_of(made)
    assert pile is not None
    assert pile["by_hand"] == 1
    assert pile["size"] == 2
    assert await store.by_hand_track_ids() == set(tracks)


async def test_moving_faces_into_an_existing_pile_merges_them_into_it(
    store: Store, clip: Ingested
) -> None:
    """The merge, and the same call: a pile id is a destination, no pile id is a new one."""
    tracks = await record(store, clip.asset.id, count=3)
    grouped = await store.replace_piles([(tuple(0.0 for _ in range(8)), tracks[:1])])

    made = await store.move_tracks(tracks[1:], grouped[0])

    assert made == grouped[0]
    pile = await store.pile_of(grouped[0])
    assert pile is not None
    assert pile["by_hand"] == 1, "a pile merged into by hand must survive the next grouping pass"
    assert pile["size"] == 3


async def test_a_pile_split_out_by_hand_survives_the_next_grouping_pass(
    store: Store, clip: Ingested
) -> None:
    """The split, and the protection that makes it stick.

    Re-grouping clears the open piles and writes them again from the arithmetic. A pile somebody
    made by splitting faces out is open too, so without being marked as theirs it is deleted by the
    very next pass and the faces are scattered back to wherever the clustering puts them, silently,
    and with nothing on any screen to say a merge was undone.
    """
    tracks = await record(store, clip.asset.id, count=3, distinct=True)
    made = await store.move_tracks(tracks[:2], None)
    assert made is not None

    pile = await store.pile_of(made)
    assert pile is not None
    assert pile["by_hand"] == 1

    # A pass that knows nothing about it, which is the ordinary case.
    await store.replace_piles([(tuple(0.0 for _ in range(8)), tracks[2:])])

    assert await store.pile_of(made) is not None, "the next pass took a pile somebody had made"


async def test_a_hand_made_grouping_is_written_down_once_per_face(
    store: Store, clip: Ingested
) -> None:
    """Written as descriptions, because a rescan deletes the rows the pile sits on. One row per
    face and not one per frame: an appearance seen six times is still one decision."""
    tracks = await record(store, clip.asset.id, count=2)
    made = await store.move_tracks(tracks, None)
    assert made is not None

    written = await store.remember_grouping(made)

    assert written == 2
    assert len(await store.grouped_for(clip.asset.id)) == 2


async def test_remembering_a_grouping_that_has_gone_writes_nothing(store: Store) -> None:
    """Not an error. The pile can be emptied by a name landing on its last face between the move
    and the write, and there is then nothing to remember."""
    assert await store.remember_grouping(NO_SUCH_PILE) == 0


async def test_putting_faces_back_into_a_hand_made_pile_needs_faces(store: Store) -> None:
    """The other half of the memory: nothing found in this file that belongs to it, so nothing to
    put back. Recreating the empty pile would put a card on the wall holding nothing."""
    await store.group_again(NO_SUCH_PILE, [], b"")

    assert await store.pile_of(NO_SUCH_PILE) is None


async def test_a_hand_made_pile_is_recreated_under_its_own_identity_after_a_rescan(
    store: Store, clip: Ingested
) -> None:
    """The whole point of writing the grouping down. A rescan deletes every track the file had, so
    the pile is tidied away, and the faces have to come back TOGETHER rather than as a heap."""
    tracks = await record(store, clip.asset.id, count=2, distinct=True)
    made = await store.move_tracks(tracks, None)
    assert made is not None
    await store.remember_grouping(made)
    remembered = await store.grouped_for(clip.asset.id)
    assert remembered

    fresh = await record(store, clip.asset.id, count=2, distinct=True)
    # The rescan takes the tracks; the empty row goes in the tidy-up the pipeline runs after it.
    assert await store.drop_empty_piles() == 1
    assert await store.pile_of(made) is None

    await store.group_again(made, fresh, remembered[0][2])

    back = await store.pile_of(made)
    assert back is not None
    assert back["by_hand"] == 1
    assert back["size"] == 2


async def test_forgetting_a_grouping_takes_back_only_those_faces(
    store: Store, clip: Ingested
) -> None:
    """Moving a face out of a pile it was moved into has to clear the old memory, or the move
    works until the next scan of the file and then quietly undoes itself."""
    tracks = await record(store, clip.asset.id, count=2, distinct=True)
    made = await store.move_tracks(tracks, None)
    assert made is not None
    await store.remember_grouping(made)

    gone = await store.forget_grouping(tracks[:1])

    assert gone == 1
    assert len(await store.grouped_for(clip.asset.id)) == 1


# --- what a person is recognized by ---------------------------------------------------------------


async def test_matching_and_grouping_see_only_the_faces_the_model_in_use_described(
    store: Store, temp_db: Database, clip: Ingested, other_clip: Ingested
) -> None:
    """Two models' numbers are the same length and mean nothing to each other. A file the
    previous model described is out of matching and grouping until it is measured again, and it
    is what the pass that measures again is handed."""
    await record(store, clip.asset.id)
    await record(store, other_clip.asset.id)
    await temp_db.execute(
        "UPDATE face_scans SET recognizer = 'older' WHERE asset_id = ?", (other_clip.asset.id,)
    )

    listed = await store.unattributed("test-recognizer")

    assert [asset_id for _, asset_id, _ in listed] == [clip.asset.id]
    assert await store.measured_by_others("test-recognizer", limit=10) == [other_clip.asset.id]
    assert await store.count_measured_by_others("test-recognizer") == 1


async def test_the_gallery_is_one_models_references_and_the_rest_wait_to_be_measured_again(
    store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    kept = await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"one picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )
    older = await store.add_reference(
        person,
        vector=person_vector(1),
        quality=0.9,
        crop=b"another picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="older",
    )
    numbers_only = await store.add_reference(
        person,
        vector=person_vector(2),
        quality=0.9,
        crop=None,
        digest="abc123",
        origin=FaceOrigin.PACK,
        recognizer="older",
    )
    assert kept and older and numbers_only

    gallery = await store.reference_gallery("test-recognizer")

    assert [reference.id for reference in gallery[person]] == [kept]
    assert [
        reference_id
        for reference_id, _ in await store.references_measured_by_others(
            "test-recognizer", limit=10
        )
    ] == [older], "the one with a picture can be measured again; numbers alone cannot"
    assert await store.count_unmeasurable_references("test-recognizer") == 1

    await store.remeasure_reference(
        older, embedding=recognize.pack(person_vector(3)), recognizer="test-recognizer"
    )
    assert {
        reference.id for reference in (await store.reference_gallery("test-recognizer"))[person]
    } == {
        kept,
        older,
    }
    assert await store.references_measured_by_others("test-recognizer", limit=10) == []


async def test_measuring_a_file_again_replaces_its_numbers_carries_every_decision_and_undoes_the_arithmetic(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """The one transaction that a model change rests on. Each face's numbers are replaced; the
    three memories that hold a copy of a face's numbers (a name given, a pile set aside, a pile
    built by hand) are found by the copy and given the new ones, which is only possible at
    this moment; what Sift decided by arithmetic is undone and what a person decided stays; and
    the scan is restamped, which puts the file back into matching."""
    person = await make_person(temp_db, "Ada Lovelace")
    named, matched = await record(store, clip.asset.id, 2, distinct=True)
    named_face = (await store.faces_of(named))[0]
    matched_face = (await store.faces_of(matched))[0]
    await store.attribute(named, person, confidence=1.0, attribution=Attribution.CONFIRMED)
    await store.remember_confirmation(named, person)
    await store.attribute(matched, person, confidence=0.9, attribution=Attribution.MATCHED)
    old_matched = recognize.pack(matched_face.vector)
    await temp_db.execute(
        "INSERT INTO face_ignored (id, asset_id, pile_id, embedding, centroid, created_at) "
        "VALUES (?, ?, 'pile-1', ?, ?, 1)",
        (new_id(), clip.asset.id, old_matched, old_matched),
    )
    await temp_db.execute(
        "INSERT INTO face_grouping (id, asset_id, pile_id, embedding, centroid, created_at) "
        "VALUES (?, ?, 'pile-1', ?, ?, 1)",
        (new_id(), clip.asset.id, old_matched, old_matched),
    )

    removed = await store.remeasure_file(
        clip.asset.id,
        [
            Remeasured(
                id=named_face.id,
                previous=recognize.pack(named_face.vector),
                embedding=recognize.pack(person_vector(5)),
                strength=17.0,
            ),
            Remeasured(
                id=matched_face.id,
                previous=old_matched,
                embedding=recognize.pack(person_vector(6)),
                strength=18.0,
            ),
        ],
        gone=[],
        recognizer="newest",
    )

    assert removed == 0
    assert recognize.similarity((await store.faces_of(named))[0].vector, person_vector(5)) > 0.99
    assert recognize.similarity((await store.faces_of(matched))[0].vector, person_vector(6)) > 0.99
    remembered = await store.confirmations_for(clip.asset.id)
    assert [who for who, _ in remembered] == [person]
    assert recognize.similarity(remembered[0][1], person_vector(5)) > 0.99, "the name's copy moved"
    assert (
        recognize.similarity((await store.ignored_for(clip.asset.id))[0][1], person_vector(6))
        > 0.99
    )
    assert (
        recognize.similarity((await store.grouped_for(clip.asset.id))[0][1], person_vector(6))
        > 0.99
    )
    kept = await store.track(named)
    assert (
        kept is not None and kept.attribution is Attribution.CONFIRMED and kept.person_id == person
    )
    undone = await store.track(matched)
    assert undone is not None and undone.person_id is None and undone.attribution is None
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None and scan.recognizer == "newest"
    assert await store.count_measured_by_others("newest") == 0


async def test_a_face_whose_picture_has_gone_is_dropped_and_its_appearance_with_it_when_it_was_the_last(
    store: Store, clip: Ingested
) -> None:
    kept, lost = await record(store, clip.asset.id, 2, distinct=True)
    kept_face = (await store.faces_of(kept))[0]
    lost_face = (await store.faces_of(lost))[0]

    removed = await store.remeasure_file(
        clip.asset.id,
        [
            Remeasured(
                id=kept_face.id,
                previous=recognize.pack(kept_face.vector),
                embedding=recognize.pack(person_vector(5)),
                strength=17.0,
            )
        ],
        gone=[lost_face.id],
        recognizer="newest",
    )

    assert removed == 1
    assert [track.id for track in await store.tracks_of(clip.asset.id)] == [kept]
    assert await store.faces_of(lost) == []


async def test_stored_pictures_are_read_together_and_a_missing_one_reads_as_none(
    store: Store, clip: Ingested
) -> None:
    await record(store, clip.asset.id, 2, distinct=True)
    listed = await store.detections_of_file(clip.asset.id)
    assert len(listed) == 2
    stored = [path for _, path, _ in listed]
    store.resolve(stored[1]).unlink()

    pictures = await store.read_pictures(stored)

    assert pictures[0] == b"\xff\xd8\xff picture 0"
    assert pictures[1] is None


async def test_taking_a_name_off_takes_back_the_pictures_that_face_gave_them(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """What taking a name off has to do as well as taking the name off. Otherwise a face agreed to
    by mistake goes on being one of the things that person is recognized by, permanently."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=1)
    detections = await store.faces_of(tracks[0])
    picture = store.resolve(detections[0].crop_path).read_bytes()
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=picture,
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )
    assert await store.reference_count(person) == 1

    gone = await store.remove_references_from_track(person, tracks[0])

    assert gone == 1
    assert await store.reference_count(person) == 0


async def test_taking_a_name_off_a_face_with_no_picture_left_takes_nothing_back(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A reference is keyed by the identity of its picture, so a face whose crops have gone from
    disk cannot say which rows it put there. Nothing is removed rather than something guessed."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=1)
    for detection in await store.faces_of(tracks[0]):
        store.resolve(detection.crop_path).unlink()
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"some other picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )

    assert await store.remove_references_from_track(person, tracks[0]) == 0
    assert await store.reference_count(person) == 1


async def test_how_many_appearances_somebody_agreed_to(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """Not the same number as their reference count, and the difference is why it is reported:
    agreeing to one appearance files every stored picture of it, so eight decisions can add
    thirty-odd references and a bar that jumps by thirty reads as broken when it is truthful."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=3)

    assert await store.confirmed_appearances(person) == 0

    await store.attribute(tracks[0], person, confidence=1.0, attribution=Attribution.CONFIRMED)
    # Two of the other kind rather than one, so that counting the wrong side of the comparison
    # gives a different number rather than the same one by coincidence.
    await store.attribute(tracks[1], person, confidence=0.7, attribution=Attribution.MATCHED)
    await store.attribute(tracks[2], person, confidence=0.6, attribution=Attribution.MATCHED)

    assert await store.confirmed_appearances(person) == 1


async def test_everybodys_reference_count_in_one_answer(store: Store, temp_db: Database) -> None:
    """For a picker drawing several people together. Asking per row turns choosing a name into one
    request per keystroke per candidate."""
    ada = await make_person(temp_db, "Ada Lovelace")
    grace = await make_person(temp_db, "Grace Hopper")
    await make_person(temp_db, "Nobody At All")
    for index in range(2):
        await store.add_reference(
            ada,
            vector=person_vector(index * 2),
            quality=0.9,
            crop=f"ada {index}".encode(),
            origin=FaceOrigin.CONFIRMED,
            recognizer="test-recognizer",
        )
    await store.add_reference(
        grace,
        vector=person_vector(1),
        quality=0.9,
        crop=b"grace",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )

    counts = await store.reference_counts()

    # People with no references are absent rather than zero: that is what the table can say.
    assert counts == {ada: 2, grace: 1}


async def test_the_count_that_decides_a_bar_is_of_this_models_pictures_only(
    store: Store, temp_db: Database
) -> None:
    """Two questions, two answers, and only one of them is about the comparison being made.

    A screen reporting how well covered somebody is wants every picture of them. The bar Sift
    attaches to them at wants the ones a comparison actually reads, and after a model change
    those differ, so counting all of them there would lower the bar on evidence nothing is using.
    """
    ada = await make_person(temp_db, "Ada Lovelace")
    for index, model in enumerate(("test-recognizer", "test-recognizer", "an-older-model")):
        await store.add_reference(
            ada,
            vector=person_vector(index),
            quality=0.9,
            crop=f"ada {index}".encode(),
            origin=FaceOrigin.CONFIRMED,
            recognizer=model,
        )

    assert await store.reference_counts() == {ada: 3}
    assert await store.reference_counts(recognizer="test-recognizer") == {ada: 2}


# --- what a resumed pass adds, and what is written down about it ----------------------------------


def a_pass_record(frames: int = 1) -> PassRecord:
    return PassRecord(
        status=ScanStatus.NONE_IDENTIFIED,
        depth="fast",
        coverage=1.0,
        frames_sampled=frames,
        detector="test-detector",
        recognizer="test-recognizer",
        settings_digest="abcd1234",
    )


async def test_a_resumed_pass_widens_the_appearance_it_had_already_found(
    store: Store, clip: Ingested
) -> None:
    """The one write in the feature that is not a replacement.

    A pass cut short and carried on finds the same person again in the part it re-reads. Written as
    a new appearance it would be one face counted twice; the caller recognizes it and names the
    track to widen, and the two pictures both stay: nothing a pass wrote is deleted here.
    """
    tracks = await record(store, clip.asset.id, count=1)

    fresh = await store.extend_pass(
        clip.asset.id,
        [an_appearance(2000)],
        [[b"\xff\xd8\xff a later picture"]],
        [tracks[0]],
        a_pass_record(frames=2),
    )

    assert fresh == [], "the rejoined appearance was written as a face of its own"
    assert len(await store.tracks_of(clip.asset.id)) == 1
    assert len(await store.faces_of(tracks[0])) == 2
    widened = await store.track(tracks[0])
    assert widened is not None
    assert widened.ended_ms == 2000, "the appearance was not widened to where it was seen again"


async def test_an_appearance_seen_twice_is_written_down_once_when_it_is_set_aside(
    store: Store, clip: Ingested
) -> None:
    """The memory is one row per FACE, not one per picture of it.

    A face found twice by a resumed pass has two descriptions stored, and both come back from the
    query this walks. Written twice, the same decision would be counted twice by everything that
    reads the table and put back twice by the rescan.
    """
    tracks = await record(store, clip.asset.id, count=1)
    await store.extend_pass(
        clip.asset.id,
        [an_appearance(2000)],
        [[b"\xff\xd8\xff a later picture"]],
        [tracks[0]],
        a_pass_record(frames=2),
    )
    aside = await store.set_aside(tracks)
    assert aside is not None

    assert await store.remember_ignored(aside) == 1
    assert len(await store.ignored_for(clip.asset.id)) == 1


async def test_an_appearance_seen_twice_is_written_down_once_when_it_is_grouped_by_hand(
    store: Store, clip: Ingested
) -> None:
    """The same claim about the fourth decision of this shape."""
    tracks = await record(store, clip.asset.id, count=1)
    await store.extend_pass(
        clip.asset.id,
        [an_appearance(2000)],
        [[b"\xff\xd8\xff a later picture"]],
        [tracks[0]],
        a_pass_record(frames=2),
    )
    made = await store.move_tracks(tracks, None)
    assert made is not None

    assert await store.remember_grouping(made) == 1
    assert len(await store.grouped_for(clip.asset.id)) == 1


async def test_setting_aside_a_pile_that_has_gone_writes_nothing(store: Store) -> None:
    """Not an error: the last unnamed face in a pile can be named between the decision and the
    write, which takes the pile with it."""
    assert await store.remember_ignored(NO_SUCH_PILE) == 0


async def test_putting_faces_back_into_a_pile_that_was_set_aside_needs_faces(store: Store) -> None:
    """Nothing found in this file that belongs to it, so nothing to put back. Recreating the empty
    pile would put a card under Discarded holding nobody."""
    await store.set_aside_again(NO_SUCH_PILE, [], b"")

    assert await store.pile_of(NO_SUCH_PILE) is None


async def test_a_naming_cannot_be_written_down_against_a_face_with_no_description(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """The memory is keyed on the face's own description, which is what lets it outlive the row.

    A track whose detections have gone (what a half-finished pass leaves behind) has no
    description to key on, so nothing is written rather than a row keyed on nothing.
    """
    from sift.kernel.ids import new_id

    person = await make_person(temp_db, "Ada Lovelace")
    track_id = new_id()
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "created_at) VALUES (?, ?, 0, 0, 1, 0.5, 0)",
        (track_id, clip.asset.id),
    )

    await store.remember_confirmation(track_id, person)

    assert await store.confirmations_for(clip.asset.id) == []


async def test_a_face_whose_picture_was_never_stored_takes_no_reference_back(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A detection can hold no picture path at all: a pass that stored the numbers and failed
    before the crop reached disk leaves exactly that. There is then nothing to recompute an
    identity from, so nothing is taken back rather than something guessed."""
    person = await make_person(temp_db, "Ada Lovelace")
    tracks = await record(store, clip.asset.id, count=1)
    await temp_db.execute(
        "UPDATE face_detections SET crop_path = '' WHERE track_id = ?", (tracks[0],)
    )
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=0.9,
        crop=b"a picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
    )

    assert await store.remove_references_from_track(person, tracks[0]) == 0
    assert await store.reference_count(person) == 1


# --- how much work is left ----------------------------------------------------------------------


async def _queued_scan(
    temp_db: Database, job_id: str, asset_id: str, state: str = "queued"
) -> None:
    """One scan job in the queue, written directly.

    Enqueued through the real queue this would need a running worker pool to hold anything in
    `queued` for long enough to count it, and what is under test is the counting.
    """
    await temp_db.execute(
        "INSERT INTO jobs (id, type, state, priority, payload, max_attempts, attempts, progress, "
        "created_at, updated_at) VALUES (?, 'face_scan', ?, 0, ?, 3, 0, 0, 0, 0)",
        (job_id, state, f'{{"asset_id": "{asset_id}"}}'),
    )


async def test_what_is_waiting_comes_back_as_ids_rather_than_as_rows(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """Ids, because how long each of those files is comes from the access layer.

    A join to `assets` written here would answer for files the user asking may not see, which is
    the whole reason that rule exists, and a gate refuses it.
    """
    await _queued_scan(temp_db, "job-1", clip.asset.id)

    ids, waiting = await store.waiting_asset_ids(200)

    assert ids == [clip.asset.id]
    assert waiting == 1


async def test_a_scan_already_finished_is_not_still_waiting(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """The whole number is what is LEFT. Counting finished work into it is an estimate that never
    reaches zero."""
    await _queued_scan(temp_db, "job-1", clip.asset.id, state="done")

    ids, waiting = await store.waiting_asset_ids(200)

    assert ids == []
    assert waiting == 0


async def test_a_file_being_scanned_right_now_still_counts_as_waiting(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """It is not finished, and dropping it would make the estimate short by however many run at
    once, which on a machine set to use most of its processor is not a rounding error."""
    await _queued_scan(temp_db, "job-1", clip.asset.id, state="running")

    _, waiting = await store.waiting_asset_ids(200)

    assert waiting == 1


async def test_the_sample_is_capped_while_the_count_stays_exact(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """The queue on a large library is the size of the library, and an average does not improve for
    being taken over a hundred thousand rows. So the ids are a sample and the count is not: a bar
    counting down has to be right, an estimate only has to be close.
    """
    for index in range(5):
        await _queued_scan(temp_db, f"job-{index}", clip.asset.id)

    ids, waiting = await store.waiting_asset_ids(2)

    assert len(ids) == 2
    assert waiting == 5


async def _stamp(database: Database, user_id: str) -> int:
    row = await database.fetch_one("SELECT cache_stamp FROM users WHERE id = ?", (user_id,))
    assert row is not None
    return int(row["cache_stamp"])


async def test_a_scan_naming_somebody_reaches_the_guest_that_person_was_shared_with(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """**A scan changes who a file belongs to, which changes who may see it.**

    A person is what a share is attached to, so a pass attributing a face puts the file inside a
    guest's reach and withdrawing one takes it back out. Nothing belonging to the guest is written
    either way, so unless the reconcile raises their number the picture addresses they already hold
    go on working out of their browser's own store, with no request and therefore no check.
    """
    guest = await create_user(temp_db, Role.GUEST)
    person = await make_person(temp_db, "Ada Lovelace")
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'person', ?, ?, 'share', 0)",
        (new_id(), person, guest.id),
    )
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)

    await store.reconcile_people(clip.asset.id)
    after_naming = await _stamp(temp_db, guest.id)
    assert after_naming > 0, "the guest was never told the file had come into their reach"

    await store.attribute(track_id, None, confidence=None, attribution=None)
    await store.reconcile_people(clip.asset.id)

    assert await _stamp(temp_db, guest.id) > after_naming, (
        "the guest's picture addresses still work after the person who reached them was withdrawn"
    )


async def test_a_scan_that_confirms_what_was_already_known_costs_nobody_anything(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The reason this is affordable on a path a scan takes once per file. A second pass over an
    unchanged file writes no membership, so nobody pays a re-fetch of their grid for it."""
    guest = await create_user(temp_db, Role.GUEST)
    person = await make_person(temp_db, "Ada Lovelace")
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'person', ?, ?, 'share', 0)",
        (new_id(), person, guest.id),
    )
    track_id = (await record(store, clip.asset.id))[0]
    await store.attribute(track_id, person, confidence=0.9, attribution=Attribution.MATCHED)
    await store.reconcile_people(clip.asset.id)
    settled = await _stamp(temp_db, guest.id)

    await store.reconcile_people(clip.asset.id)

    assert await _stamp(temp_db, guest.id) == settled, "an unchanged rescan invalidated the grid"


async def test_a_stored_path_outside_the_face_directory_abandons_the_whole_removal(
    store: Store, clip: Ingested
) -> None:
    """The path check has to be able to call the removal off, so it runs before the delete commits.

    A row outlives the code that wrote it, and a database restored from a backup is exactly where a
    row written before a guard meets the code that assumes it. Resolving after the commit would
    leave the appearances gone and the refusal raised anyway, which reads as a successful removal
    right up until somebody looks for the faces.
    """
    track_ids = await record(store, clip.asset.id, count=1)
    await store._db.execute(
        "UPDATE face_detections SET crop_path = ? WHERE track_id = ?",
        ("../../etc/passwd", track_ids[0]),
    )

    with pytest.raises(ValueError, match="inside the face directory"):
        await store.remove_faces(track_ids)

    # Still there: the transaction was abandoned rather than committed and then complained about.
    held = await store._db.fetch_all("SELECT id FROM face_tracks WHERE id = ?", (track_ids[0],))
    assert len(held) == 1


async def test_every_measurement_lands_in_its_own_column(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """Six measurements, six columns, and one statement listing them in one order and their values
    in another.

    Written with six values that are all different on purpose. A column added to that insert
    without a placeholder is caught by SQLite; two of them written the wrong way round is not
    caught by anything, and a pair of measurements that happened to be equal in a fixture would
    hide it here too.
    """
    detailed = Quality(
        pixels=137,
        sharpness=411.5,
        frontality=0.73,
        score=0.61,
        accepted=True,
        containment=0.97,
        strength=18.25,
        agreement=0.42,
    )
    appearance = an_appearance(0)
    faces = (replace(appearance.faces[0], quality=detailed),)
    await store.replace_pass(
        clip.asset.id,
        [replace(appearance, faces=faces)],
        [[b"\xff\xd8\xff picture"]],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=1,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest="abcd1234",
        ),
    )

    rows = await temp_db.fetch_all(
        "SELECT quality, pixels, sharpness, frontality, containment, strength, agreement "
        "FROM face_detections"
    )

    assert len(rows) == 1
    assert rows[0]["quality"] == pytest.approx(0.61)
    assert rows[0]["pixels"] == 137
    assert rows[0]["sharpness"] == pytest.approx(411.5)
    assert rows[0]["frontality"] == pytest.approx(0.73)
    assert rows[0]["containment"] == pytest.approx(0.97)
    assert rows[0]["strength"] == pytest.approx(18.25)
    assert rows[0]["agreement"] == pytest.approx(0.42)


# --- the batched reads, asked for nothing --------------------------------------------------------
#
# Three of these serve the batched settle path, and the empty case of each is the one a caller
# reaches without meaning to: a settle over a batch that turned out to hold nothing. Every one of
# them answers rather than running a statement with an empty IN clause.


async def test_asking_about_no_files_at_all_answers_without_a_statement(store: Store) -> None:
    """`IN ()` is not valid SQL and an empty batch is an ordinary state: a settle over a page
    where every file turned out to be already settled hands these an empty list."""
    assert await store.tracks_in_files([]) == {}
    assert await store.tracks_in_piles([]) == {}
    assert await store.reconcile_people_of([]) == {}


async def test_writing_no_statuses_takes_no_turn_at_the_writer(store: Store) -> None:
    """The whole reason this exists is to take the writer ONCE for a batch. Taking it for a batch
    of nothing would queue every other write behind a transaction with no statements in it."""
    await store.set_statuses({})


# --- what a pass refused, kept beside what it found ---------------------------------------------


def a_refusing_record(small: int | None, closer: int | None) -> PassRecord:
    """A pass that found nobody and says how many faces it refused, at each gate."""
    return PassRecord(
        status=ScanStatus.NO_FACES,
        depth="fast",
        coverage=0.5,
        frames_sampled=1,
        detector="test-detector",
        recognizer="test-recognizer",
        settings_digest="abcd1234",
        reached_ms=1000,
        refused_small=small,
        refused_closer=closer,
    )


async def test_a_pass_keeps_what_it_refused_and_a_rescan_replaces_it(
    store: Store, clip: Ingested
) -> None:
    """The counts are the reason a file with nobody in it has nobody in it, so they are written
    with the status, and a pass that replaces a file's faces replaces its reason too, or a
    rescan that found the people would go on carrying the old pass's excuse."""
    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(16, 1))
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (16, 1)

    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(2, 0))
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (2, 0)


async def test_a_resumed_pass_adds_what_it_refused_to_what_the_last_one_did(
    store: Store, clip: Ingested
) -> None:
    """A resumed pass read only the part the last one did not, so its counts are the rest of the
    same answer. Replaced, the refusals of the head of the file would vanish the moment its tail
    was read, and a tail with nobody in it would say "found none" about a file full of faces."""
    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(16, 1))

    await store.extend_pass(clip.asset.id, [], [], [], a_refusing_record(3, 2))

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (19, 3)


async def test_a_resumed_pass_over_a_scan_that_never_counted_stays_not_known(
    store: Store, clip: Ingested
) -> None:
    """Half a count is not a count. The head of the file was read before anything was counted, so
    a total made of the tail alone would be a reason given for a part as if it were the whole."""
    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(None, None))

    await store.extend_pass(clip.asset.id, [], [], [], a_refusing_record(3, 2))

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (None, None)


def a_record_with_reasons(largest: int, blurred: int, turned: int, edge: int) -> PassRecord:
    """A refusing pass that also says why, reason by reason, and how big its biggest small face was."""
    return replace(
        a_refusing_record(1, blurred + turned + edge),
        refused_largest=largest,
        refused_blurred=blurred,
        refused_turned=turned,
        refused_edge=edge,
    )


async def test_a_pass_keeps_its_reasons_and_a_resume_adds_them_and_keeps_the_largest(
    store: Store, clip: Ingested
) -> None:
    """The reasons are the rest of the same answer, so a resume adds them like the counts they
    split; the size is the biggest face refused across both stretches, never the last one's."""
    await store.replace_pass(clip.asset.id, [], [], a_record_with_reasons(98, 1, 0, 0))
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_largest, scan.refused_blurred, scan.refused_turned, scan.refused_edge) == (
        98,
        1,
        0,
        0,
    )

    await store.extend_pass(clip.asset.id, [], [], [], a_record_with_reasons(60, 0, 2, 1))

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_largest, scan.refused_blurred, scan.refused_turned, scan.refused_edge) == (
        98,
        1,
        2,
        1,
    )


# --- the reads and writes a caller cannot reach every edge of -------------------------------------


async def test_a_read_about_nobody_answers_nothing_rather_than_asking(store: Store) -> None:
    """An empty list is "match nothing", which the statement builder refuses to guess: each read
    that takes a list decides it here instead."""
    assert await store.attributed_page([], attribution=None, limit=5, offset=0) == []
    assert await store.pile_proposals([]) == []


async def test_a_proposal_is_answered_accepted_refused_or_pending_and_nothing_else(
    store: Store,
) -> None:
    with pytest.raises(ValueError, match="accepted or refused"):
        await store.settle_pile_proposal("pile", "person", state="maybe")


async def test_an_appearance_with_no_face_left_has_no_standing_to_carry(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """A rescan pairs by description, and an appearance whose faces have gone has none to be
    known by, so it is left out of what the rescan reads rather than read as a blank."""
    kept, emptied = await record(store, clip.asset.id, 2, distinct=True)
    await temp_db.execute("DELETE FROM face_detections WHERE track_id = ?", (emptied,))

    assert [one.track_id for one in await store.standing_of(clip.asset.id)] == [kept]


async def _rejected_memories(temp_db: Database) -> int:
    row = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM face_rejected", ())
    assert row is not None
    return int(row["n"])


async def test_a_no_said_twice_is_remembered_once_and_retires_no_starter_the_second_time(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """Only a refusal this press wrote is remembered and retires her starters: the same no said
    again is one decision, and a starter filed since it was first said was not what it answered."""
    person = await make_person(temp_db, "Ada Lovelace")
    (track_id,) = await record(store, clip.asset.id)

    assert await store.reject(track_id, person)
    starter = await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"a box picture",
        origin=FaceOrigin.SEED,
        recognizer="test-recognizer",
        source="FansDB",
    )
    assert await store.reject(track_id, person)

    assert await _rejected_memories(temp_db) == 1
    row = await temp_db.fetch_one("SELECT retired_at FROM face_references WHERE id = ?", (starter,))
    assert row is not None and row["retired_at"] is None


async def test_a_no_about_an_appearance_with_no_face_left_is_kept_with_nothing_to_remember(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The refusal stands on the appearance; there is no description to remember it by, so a later
    rescan has nothing to put it back from, and nothing is invented to stand in for one."""
    person = await make_person(temp_db, "Ada Lovelace")
    (track_id,) = await record(store, clip.asset.id)
    await temp_db.execute("DELETE FROM face_detections WHERE track_id = ?", (track_id,))

    assert await store.reject(track_id, person)

    assert (await store.rejections()).get(track_id) == {person}
    assert await _rejected_memories(temp_db) == 0


async def test_measuring_a_file_again_restamps_the_group_its_faces_are_in(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """A group's middle is only as good as the model its faces were described by, so a group with
    a face on a file measured again carries that file's new model: what the grouping reads to know
    the middle wants rebuilding."""
    (track_id,) = await record(store, clip.asset.id)
    face = (await store.faces_of(track_id))[0]
    (pile,) = await store.add_piles([(face.vector, [track_id])])

    await store.remeasure_file(
        clip.asset.id,
        [
            Remeasured(
                id=face.id,
                previous=recognize.pack(face.vector),
                embedding=recognize.pack(person_vector(5)),
                strength=17.0,
            )
        ],
        gone=[],
        recognizer="newest",
    )

    row = await temp_db.fetch_one("SELECT recognizer FROM face_piles WHERE id = ?", (pile,))
    assert row is not None and row["recognizer"] == "newest"
