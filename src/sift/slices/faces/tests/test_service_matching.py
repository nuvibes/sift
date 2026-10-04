# SPDX-License-Identifier: AGPL-3.0-or-later
"""Matching: agreeing with what Sift matched, the bar a person is matched at, the bulk answers
and their records, a rescan's faces, and a re-match over the questions standing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sift.kernel.access import Role
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.vocabulary import FACE_SAID_NO, RECEIPT_FACES
from sift.slices.faces import (
    recognize,
    tuning,
    weights,
)
from sift.slices.faces import service as service_module
from sift.slices.faces import settings as face_settings
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import (
    AskedBy,
    Attribution,
    PileStatus,
    Vector,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.queue import IdentifiedRecords
from sift.slices.faces.service import (
    AGREED_WITH_MATCHES,
    AGREED_WITH_PROPOSALS,
    FaceService,
    Sighting,
    _attention_first,
)
from sift.slices.faces.store import Store
from sift.slices.faces.tests import test_service
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
)
from sift.slices.faces.tests.test_service import (
    Scripted,
    _one_face_recognized_as,
    _two_files_of_one_stranger,
    install_reader,
)
from sift.slices.faces.tests.test_service_review import (
    _one_face_named_by_a_scan,
)
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

#: The fixtures this file shares with the files they are defined in, found here by name.
clip = test_service.clip
library = test_service.library
other_clip = test_service.other_clip
restore_pipeline = test_service.restore_pipeline


# --- agreeing with everything Sift matched to one person ------------------------------------------


async def _matched_on_both(
    service: FaceService,
    store: Store,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    clip: Ingested,
    other_clip: Ingested,
    person: str,
) -> None:
    """Two files, each with one appearance Sift attached to this person on its own.

    The reference is filed first so the scan has somebody to match against, which is what makes
    these MATCHED rather than strangers: the state the press this exercises is about.

    The two faces are drawn at different sizes on purpose. A face here is a plain square, so two
    of them at the same size and place are the same PICTURE whatever the frame around them holds,
    and a reference is keyed by the identity of its picture, so the second would be refused as one
    already held. That is real behaviour and it is tested where it belongs; here it would make one
    appearance silently stop filing anything.
    """
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    recognizer.rule = lambda chip: person_vector(0)
    for index, one in enumerate((clip, other_clip)):
        frame = noisy_frame(400, 300, seed=40 + index)
        detector.placed = {0: [(draw_face(frame, x=60, y=40, size=180 - index * 24), 0.9)]}
        await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
        await service.scan(one.asset.id)


async def test_agreeing_with_every_match_confirms_them_and_learns_from_each(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """ "These matches are right", which is the one press that may write over a face carrying a name.

    `confirm_many` skips a face that already has somebody, deliberately, so that naming a pile never
    overwrites a decision already taken. A match IS a face with somebody on it, so this press goes
    through `accept_suggestions` instead (the same path the board's Confirm all takes), and every
    matched appearance becomes that person's own answer.

    Two numbers, and they are not the same number: the appearances agreed to, and the pictures Sift
    learned from them. Here they agree because each appearance kept one frame; they diverge the
    moment a crop is refused or already held, which is why the second is measured rather than
    assumed from the first.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)
    before = len(await store.references(person))

    confirmed, references = await service.confirm_matches(admin, person)

    assert confirmed == 2
    assert references == len(await store.references(person)) - before == 2
    for one in (clip, other_clip):
        assert (await store.tracks_of(one.asset.id))[0].attribution is Attribution.CONFIRMED


async def test_a_face_somebody_already_agreed_to_is_left_exactly_where_it_is(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Agreeing again with an answer already given is not a decision, so it is not counted as one.

    It matters for the receipt more than for the number: an appearance confirmed last week is not
    this press's to take back, and a payload that named it would let one undo reach into somebody
    else's decision.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)
    settled = (await store.tracks_of(clip.asset.id))[0]
    await service.confirm(settled.id, person)

    confirmed, _references = await service.confirm_matches(admin, person)

    assert confirmed == 1
    receipts, _total = await written.recent(limit=1, offset=0)
    assert json.loads(receipts[0].payload)["track_ids"] == [
        (await store.tracks_of(other_clip.asset.id))[0].id
    ]


async def test_agreeing_writes_one_receipt_that_says_what_it_learned(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """One record for the press, naming the person and every file it touched.

    The pictures are said in their own words rather than left to be read off the count of faces:
    they are the lasting half of the decision, and they are what the undo has to remove.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)

    await service.confirm_matches(admin, person)

    receipts, total = await written.recent(limit=5, offset=0)
    agreed = [one for one in receipts if one.title.startswith("You agreed")]
    assert total == 3, "two matches were recorded by the scans; this is the third"
    assert len(agreed) == 1
    assert agreed[0].queue == "identified"
    assert agreed[0].title == "You agreed with 2 matches for Ada Lovelace"
    assert agreed[0].detail.startswith("Sift learned from 2 faces of theirs.")
    named = await temp_db.fetch_all(
        "SELECT kind, subject_id FROM workbench_decision_subjects WHERE decision_id = ?",
        (agreed[0].id,),
    )
    assert {(str(row["kind"]), str(row["subject_id"])) for row in named} == {
        ("person", person),
        ("asset", clip.asset.id),
        ("asset", other_clip.asset.id),
    }


async def test_naming_one_face_sift_already_put_on_her_is_an_agreement_and_one_she_has_is_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Naming a face on the popout as the person Sift matched or proposed it as is agreeing with
    Sift, and its Undo says where the face goes back to; naming one already confirmed as her
    decides nothing, so it writes no receipt."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)
    matched, proposed = [(await store.tracks_of(one.asset.id))[0] for one in (clip, other_clip)]
    await store.attribute(proposed.id, person, confidence=0.72, attribution=Attribution.SUGGESTED)
    _receipts, before = await written.recent(limit=1, offset=0)

    await service.confirm(matched.id, person, viewer=admin)
    await service.confirm(proposed.id, person, viewer=admin)
    await service.confirm(matched.id, person, viewer=admin)

    receipts, total = await written.recent(limit=2, offset=0)
    assert total == before + 2
    acts = {json.loads(one.payload)["act"]: one for one in receipts}
    for act, back in (
        (AGREED_WITH_MATCHES, "leaves it Recognized by Sift, as before"),
        (AGREED_WITH_PROPOSALS, "leaves it waiting under Needs your input, as before"),
    ):
        assert acts[act].title == "You agreed with 1 face for Ada Lovelace"
        assert back in acts[act].detail


async def test_taking_an_agreement_back_leaves_the_matches_as_matches(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The undo takes back the agreement and NOT the match, which is the whole of the difference.

    Sift still says those appearances are this person (it did before anybody pressed anything),
    so an undo that detached them would turn "I do not agree" into a claim that Sift was wrong. What
    does come off is the pictures it learned, because that is what agreeing bought.

    The confidence comes back with them, off the receipt: confirming writes certainty over it, so
    the number the comparison produced exists nowhere else by then.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)
    was = (await store.tracks_of(clip.asset.id))[0].confidence
    before = len(await store.references(person))
    await service.confirm_matches(admin, person)
    receipts, _total = await written.recent(limit=5, offset=0)
    agreed = next(one for one in receipts if one.title.startswith("You agreed"))

    assert await IdentifiedRecords(service).reverse(admin, agreed.id, agreed.payload) is True

    for one in (clip, other_clip):
        track = (await store.tracks_of(one.asset.id))[0]
        assert track.person_id == person
        assert track.attribution is Attribution.MATCHED
        assert track.confidence == was
    assert len(await store.references(person)) == before


async def test_an_agreement_a_later_decision_moved_is_not_the_undos_to_write_over(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """From what the decision wrote down, never from the state it finds: the rule `unmatch` keeps.

    Here the face has been taken off the person since, so it no longer carries the agreement this
    receipt is about and nothing is put back on it.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)
    await service.confirm_matches(admin, person)
    receipts, _total = await written.recent(limit=5, offset=0)
    agreed = next(one for one in receipts if one.title.startswith("You agreed"))
    moved = (await store.tracks_of(clip.asset.id))[0]
    await store.attribute(moved.id, None, confidence=None, attribution=None)

    assert await IdentifiedRecords(service).reverse(admin, agreed.id, agreed.payload) is True

    assert (await store.tracks_of(clip.asset.id))[0].person_id is None
    assert (await store.tracks_of(other_clip.asset.id))[0].attribution is Attribution.MATCHED


async def test_a_person_this_account_may_not_be_told_about_changes_nothing(
    service: FaceService,
    temp_db: Database,
    person: str,
) -> None:
    """The same answer as a person with nothing standing, rather than a refusal.

    A 404 here would say whether somebody exists, which is precisely what withholding a person is
    for: the rule the look-alike route next door already keeps.
    """
    admin = await create_user(temp_db, Role.ADMIN)

    assert await service.confirm_matches(admin, "01M0NOSUCHPERSON000000000") == (0, 0)


async def test_a_person_with_nothing_matched_is_agreed_with_quietly(
    service: FaceService,
    temp_db: Database,
    person: str,
) -> None:
    """Nothing to agree with is not an error, and it writes no receipt: a record of a decision
    that decided nothing is a row somebody has to read and then discard."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)

    assert await service.confirm_matches(admin, person) == (0, 0)
    assert (await written.recent(limit=5, offset=0))[1] == 0


# --- the bar follows how well Sift knows somebody -------------------------------------------------


async def _seen_at(
    service: FaceService,
    store: Store,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    clip: Ingested,
    person: str,
    *,
    pictures: int,
) -> Attribution | None:
    """Scan one file holding a face that resembles this person at 0.57, and say what Sift did.

    0.57 is between the two bars on purpose: above the one a well-described person earns and below
    the one everybody else is held to, so the answer IS the bar rather than the arithmetic.
    """
    for index in range(pictures):
        await store.add_reference(
            person,
            vector=person_vector(0),
            quality=1.0,
            crop=f"reference-picture-{index}".encode(),
            origin=FaceOrigin.ADDED,
            recognizer="test-recognizer",
        )
    recognizer.rule = lambda chip: person_vector(0, variant=12)
    frame = noisy_frame(400, 300, seed=57)
    detector.placed = {0: [(draw_face(frame, x=60, y=40, size=180), 0.9)]}
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    return (await store.tracks_of(clip.asset.id))[0].attribution


async def test_a_match_at_the_middle_bar_is_only_offered_while_sift_has_little_to_go_on(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Nine pictures is under `STRONG_REFERENCES`, so the starting bar stands and this is a
    question rather than an answer."""
    assert (
        await _seen_at(service, store, detector, recognizer, clip, person, pictures=9)
        is Attribution.SUGGESTED
    )


async def test_the_same_match_is_attached_once_that_person_is_well_described(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The identical comparison, against a person Sift knows ten pictures of.

    What changed is not the face and not the arithmetic: it is what the number MEANS. A comparison
    against ten pictures of somebody is evidence a comparison against two is not, and the measured
    table says the cost of acting on it is half a percent.
    """
    assert (
        await _seen_at(service, store, detector, recognizer, clip, person, pictures=10)
        is Attribution.MATCHED
    )


async def test_a_bar_at_the_top_of_the_scale_still_means_always_ask(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    preferences: FakePreferences,
    person: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The setting's own help promises it, so a strong gallery may not walk it back.

    This is the one case where the relaxation has to do nothing at all: an admin who set the bar to
    100 asked to be asked about everything, and attaching at 95 because somebody is well described
    would be the application overruling a choice it offered.
    """
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 1.0)

    assert (
        await _seen_at(service, store, detector, recognizer, clip, person, pictures=20)
        is Attribution.SUGGESTED
    )


async def test_a_rematch_judges_each_person_at_their_own_bar(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The second path that attaches on its own, asked the same question in the same order.

    Two people and one comparison each, both landing at 0.57: the one Sift knows ten pictures of
    gets the name, the one it knows two of gets a question. Written twice these two paths would be
    two answers to "may Sift decide this alone", and the one that drifted would be the one nobody
    was watching.
    """
    barely = await make_person(temp_db, "Marit Halvorsen")
    for index, (one, who) in enumerate(((clip, 0), (other_clip, 4))):
        recognizer.rule = lambda chip, who=who: person_vector(who, variant=12)
        frame = noisy_frame(400, 300, seed=70 + index)
        detector.placed = {0: [(draw_face(frame, x=60, y=40, size=180), 0.9)]}
        await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
        await service.scan(one.asset.id)
    for index in range(10):
        await store.add_reference(
            person,
            vector=person_vector(0),
            quality=1.0,
            crop=f"reference-picture-{index}".encode(),
            origin=FaceOrigin.ADDED,
            recognizer="test-recognizer",
        )
    for index in range(2):
        await store.add_reference(
            barely,
            vector=person_vector(4),
            quality=1.0,
            crop=f"other-reference-{index}".encode(),
            origin=FaceOrigin.ADDED,
            recognizer="test-recognizer",
        )

    assert await service.rematch() == 2

    well = (await store.tracks_of(clip.asset.id))[0]
    assert (well.person_id, well.attribution) == (person, Attribution.MATCHED)
    asked = (await store.tracks_of(other_clip.asset.id))[0]
    assert (asked.person_id, asked.attribution) == (barely, Attribution.SUGGESTED)


def _card(name: str | None, *kinds: Attribution | None) -> tuple[str | None, list[Sighting]]:
    """One gathered card: who it is about, and what state each of their faces is in."""
    faces = [
        Sighting(
            track_id=f"track-{at}",
            asset_id="asset-1",
            started_ms=0,
            ended_ms=0,
            picture_ms=0,
            person_id=None if name is None else name.lower(),
            person_name=name,
            confidence=None,
            attribution=kind,
        )
        for at, kind in enumerate(kinds)
    ]
    return (None if name is None else name.lower(), faces)


def test_the_people_who_need_an_answer_come_first_and_then_the_alphabet() -> None:
    """The order of the People Sift can recognize wall.

    A card needs an answer when anything on it is outstanding (a face Sift is proposing, or one
    it attributed on its own that nobody has agreed with yet), and both are questions still to be
    answered, so they are one bucket. Everything settled goes behind, and each half is alphabetical
    so a name can be found twice running.

    Not by the most recent decision, which is a fact about the PASS: a run of matches would reorder
    the whole wall overnight and a person with faces standing would sink under people with nothing
    left to answer.
    """
    fenn_waiting = _card("Fenn Marchetti", Attribution.SUGGESTED)
    bryn_matched = _card("Bryn Calloway", Attribution.MATCHED)
    esme_settled = _card("Esme Wrenfield", Attribution.CONFIRMED)
    ada_settled = _card("Ada Lovelace", Attribution.CONFIRMED)

    ordered = sorted([esme_settled, fenn_waiting, ada_settled, bryn_matched], key=_attention_first)

    assert [one[0] for one in ordered] == [
        "bryn calloway",
        "fenn marchetti",
        "ada lovelace",
        "esme wrenfield",
    ]


def test_the_card_with_no_name_sorts_last_inside_its_half() -> None:
    """Everybody this user may not be told about gathers under one nameless card. It has nothing
    to file under, and an empty string would put it at the top of the alphabet, which reads as a
    card that lost its label rather than one that never had one."""
    nameless = _card(None, Attribution.CONFIRMED)
    named = _card("Ada Lovelace", Attribution.CONFIRMED)

    assert [one[0] for one in sorted([nameless, named], key=_attention_first)] == [
        "ada lovelace",
        None,
    ]


# --- the two bulk answers, and the records that take them back -------------------------------


async def test_agreeing_with_every_proposal_writes_a_record_that_puts_them_back(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The undo for agreeing to every proposal standing for somebody.

    Naming a face as her again takes back ONE refusal, and this press settles every proposal
    standing for somebody: thousands of them on a large library. A surface that can say yes to
    thousands of faces in one press and cannot take it back would be the one decision on this board
    with no way out.

    The faces go back to WAITING rather than to being matched, which is what tells this record from
    the one beside it: a proposal is Sift asking, so taking the agreement back leaves the question
    unanswered rather than answered by Sift.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    tracks = [
        track for asset in (clip, other_clip) for track in await store.tracks_of(asset.asset.id)
    ]
    for track in tracks:
        await store.attribute(
            track.id, person_id, confidence=0.72, attribution=Attribution.SUGGESTED
        )

    run = await service.confirm_look_alikes(admin, person_id)
    assert run.changed == 2

    receipts, _total = await written.recent(limit=5, offset=0)
    agreed = next(one for one in receipts if one.title.startswith("You agreed"))
    assert agreed.title == "You agreed with 2 faces for Ada Lovelace"
    assert "waiting under Needs your input" in agreed.detail
    # The receipt comes back on the press itself, so the card that pressed can offer Undo.
    assert run.decision_id == agreed.id

    assert await IdentifiedRecords(service).reverse(admin, agreed.id, agreed.payload) is True

    for track in tracks:
        back = await store.track(track.id)
        assert back is not None
        assert back.person_id == person_id
        assert back.attribution is Attribution.SUGGESTED, (
            "a proposal agreed with goes back to being a proposal, never to being Sift's own answer"
        )
        assert back.confidence == 0.72


async def test_taking_an_agreement_back_takes_back_the_questions_it_asked_of_the_group(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Agreeing with a proposal asks the rest of its group about her too, and the undo of that
    agreement takes those questions back: the other face goes back to nobody, where it was before
    the press. Without it the agreement would come off and the group's face stay asked about as
    her."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    assert len(tracks) == 2, "the fixture is meant to produce one group of two"
    person_id = await make_person(temp_db, "Ada Lovelace")
    await store.attribute(
        tracks[0].id, person_id, confidence=0.72, attribution=Attribution.SUGGESTED
    )

    run = await service.confirm_look_alikes(admin, person_id)
    assert (run.changed, run.offered) == (1, 1)
    asked = await store.track(tracks[1].id)
    assert asked is not None and asked.person_id == person_id

    receipts, _total = await written.recent(limit=5, offset=0)
    agreed = next(one for one in receipts if one.title.startswith("You agreed"))
    assert json.loads(agreed.payload)["offered"] == [tracks[1].id]
    assert await IdentifiedRecords(service).reverse(admin, agreed.id, agreed.payload) is True

    back = await store.track(tracks[0].id)
    assert back is not None and back.person_id == person_id
    assert back.attribution is Attribution.SUGGESTED
    offered = await store.track(tracks[1].id)
    assert offered is not None and offered.person_id is None, "the group's question is taken back"


async def test_refusing_a_run_writes_a_record_that_puts_the_name_back(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """And the other half of the same press, with the half that is easy to miss.

    Putting the name back is not enough on its own: the refusal is remembered for ever by design,
    so a name restored over a refusal that is still there would be taken straight off again by the
    next pass: an undo that works until something runs.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    tracks = [
        track for asset in (clip, other_clip) for track in await store.tracks_of(asset.asset.id)
    ]
    for track in tracks:
        await store.attribute(
            track.id, person_id, confidence=0.61, attribution=Attribution.SUGGESTED
        )

    run = await service.reject_look_alikes(admin, person_id)
    assert run.changed == 2

    receipts, _total = await written.recent(limit=5, offset=0)
    refused = next(one for one in receipts if one.title.startswith("You said"))
    assert refused.title == "You said 2 faces are not Ada Lovelace"
    assert run.decision_id == refused.id
    # Each face it answered, by its file, so History draws the press and the refusals as one line.
    assert sorted(
        (one["asset_id"], one["how"]) for one in json.loads(refused.payload)[RECEIPT_FACES]
    ) == sorted((track.asset_id, FACE_SAID_NO) for track in tracks)

    assert await IdentifiedRecords(service).reverse(admin, refused.id, refused.payload) is True

    for track in tracks:
        back = await store.track(track.id)
        assert back is not None
        assert back.person_id == person_id
        assert back.attribution is Attribution.SUGGESTED
        assert back.confidence == 0.61
        assert (await store.rejections()).get(track.id) is None, (
            "a refusal left behind puts the name off again at the next pass"
        )


async def test_refusing_every_match_is_one_press_and_remembers_it(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The no beside the card's yes.

    A match is Sift putting a name on a file without being asked, which is the one thing this
    feature does while nobody is looking, so disagreeing with a run of them has to be possible
    somewhere other than one face at a time.

    It is a REFUSAL rather than an undo, and the difference is the memory: taking a re-match back
    says "do not decide this for me", while this says "this is not them" and teaches the next pass.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)

    assert (await service.reject_matches(admin, person)).changed == 2

    for one in (clip, other_clip):
        track = (await store.tracks_of(one.asset.id))[0]
        assert track.person_id is None
        assert (await store.rejections()).get(track.id) == {person}


async def test_a_scoped_yes_confirms_only_the_named_faces_and_writes_one_receipt_for_them(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """ "The one picked" out of two matches: that one is confirmed, the other is left a match.

    The receipt names only what this press did, so taking it back cannot reach the face it did not
    touch: one receipt per press, whichever scope it was.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)
    picked = (await store.tracks_of(clip.asset.id))[0].id

    confirmed, _references = await service.confirm_matches(admin, person, only=[picked])

    assert confirmed == 1
    assert (await store.tracks_of(clip.asset.id))[0].attribution is Attribution.CONFIRMED
    assert (await store.tracks_of(other_clip.asset.id))[0].attribution is Attribution.MATCHED
    receipts, _total = await written.recent(limit=5, offset=0)
    agreed = [one for one in receipts if one.title.startswith("You agreed")]
    assert len(agreed) == 1
    assert json.loads(agreed[0].payload)["track_ids"] == [picked]


async def test_a_scoped_no_refuses_only_the_named_faces_and_writes_one_receipt_for_them(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The same narrowing on the refusal: the face not named keeps its name."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)
    picked = (await store.tracks_of(clip.asset.id))[0].id

    assert (await service.reject_matches(admin, person, only=[picked])).changed == 1

    assert (await store.tracks_of(clip.asset.id))[0].person_id is None
    assert (await store.tracks_of(other_clip.asset.id))[0].person_id == person
    receipts, _total = await written.recent(limit=5, offset=0)
    refused = [one for one in receipts if one.title.startswith("You said")]
    assert len(refused) == 1
    assert json.loads(refused[0].payload)["track_ids"] == [picked]


async def test_a_scope_naming_nothing_standing_writes_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """An empty pick is not the whole tab, and an id that is not theirs cannot widen a press."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    admin = await create_user(temp_db, Role.ADMIN)
    await _matched_on_both(service, store, detector, recognizer, clip, other_clip, person)

    assert await service.confirm_matches(admin, person, only=[]) == (0, 0)
    assert (
        await service.reject_matches(admin, person, only=["01M0NOSUCHFACE00000000000"])
    ).changed == 0

    for one in (clip, other_clip):
        assert (await store.tracks_of(one.asset.id))[0].attribution is Attribution.MATCHED
    receipts, _total = await written.recent(limit=5, offset=0)
    assert not [one for one in receipts if one.title.startswith(("You agreed", "You said"))]


# --- the sentences a receipt carries --------------------------------------------------------------


def test_a_run_sure_of_its_faces_to_different_degrees_says_the_range() -> None:
    """Whole percentages joined by words: a dash between two numbers is not read as a range."""
    said = service_module._how_sure([("t1", "a1", 0.61), ("t2", "a2", 0.874), ("t3", "a1", 0.7)])
    assert said == ", between 61% and 87% sure"


def test_an_undo_says_how_many_of_its_faces_had_been_waiting_as_questions() -> None:
    """Some of the faces were questions before the run named them: the sentence counts those, and
    says Undo asks about every one of them rather than leaving the rest unnamed."""
    one = service_module._undo_asks(1, 3)
    many = service_module._undo_asks(1200, 3000)
    assert one.startswith("Until now 1 of them waited under Needs your input.")
    assert many.startswith("Until now 1,200 of them waited under Needs your input.")
    assert many.endswith("Undo takes the name off every one and asks you about them there.")


async def test_with_the_feature_off_a_press_is_refused_by_the_switch_not_the_models(
    service: FaceService, preferences: FakePreferences, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switched off, a press is refused in the switch's own words, the first thing in the way.
    Naming the models as well would be a second, wrong reason; naming nothing would leave the
    Tasks screen's Run now answering with nothing queued and nothing said."""
    preferences.set(face_settings.ENABLED_KEY, False)
    monkeypatch.setattr(weights, "installed", lambda _settings, weight: False)

    refused = await service.cannot_scan()

    assert refused is not None
    assert refused.startswith("Recognizing faces is switched off.")
    assert "downloaded" not in refused


# --- a rescan's faces, and what each was before --------------------------------------------------


async def _somebody_else_described_as(
    store: Store, temp_db: Database, vector: Vector, name: str = "Bryn Calloway"
) -> str:
    """A second person whose one picture is described by `vector`."""
    other = await make_person(temp_db, name)
    await store.add_reference(
        other,
        vector=vector,
        quality=1.0,
        crop=f"picture of {name}".encode(),
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    return other


async def _successor_of(temp_db: Database, track_id: str) -> str | None:
    row = await temp_db.fetch_one(
        "SELECT successor_id FROM face_successors WHERE track_id = ?", (track_id,)
    )
    return None if row is None else str(row["successor_id"])


async def test_a_confirmation_is_not_moved_onto_a_face_now_put_on_somebody_else(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """Moved by the person only onto a face the arithmetic put on that same person: a face found
    again as somebody else is that other person's, and her confirmation is not written over it."""
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (face,) = await store.tracks_of(clip.asset.id)
    await service.confirm(face.id, person)
    moved = person_vector(0, 4)
    other = await _somebody_else_described_as(store, temp_db, moved)

    recognizer.rule = lambda chip: moved
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (other, Attribution.MATCHED)


async def test_sifts_old_name_is_not_carried_onto_a_face_now_matched_as_somebody_else(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """A name Sift gave pairs the face found again only where the arithmetic names the same person:
    otherwise the old receipt's Undo would reach a face it never named."""
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (before,) = await store.tracks_of(clip.asset.id)
    moved = person_vector(0, 4)
    other = await _somebody_else_described_as(store, temp_db, moved)

    recognizer.rule = lambda chip: moved
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (other, Attribution.MATCHED)
    assert await _successor_of(temp_db, before.id) is None


async def test_sifts_old_name_is_not_carried_onto_a_face_somebodys_own_answer_put_back(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """The face found again is paired by description with a name a PERSON gave the file, so it is
    theirs: Sift's older guess about the same person is not paired onto it as well, and that guess's
    receipt does not reach a face somebody answered."""
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (before,) = await store.tracks_of(clip.asset.id)
    moved = person_vector(0, 4)
    await temp_db.execute(
        "INSERT INTO face_confirmations (id, asset_id, person_id, embedding, recognizer, "
        "created_at) VALUES (?, ?, ?, ?, 'test-recognizer', 0)",
        (new_id(), clip.asset.id, person, recognize.pack(moved)),
    )

    recognizer.rule = lambda chip: moved
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (person, Attribution.CONFIRMED)
    assert await _successor_of(temp_db, before.id) is None


async def test_a_confirmation_found_again_differently_goes_back_with_no_picture_to_hand_over(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """The confirmation is put back by the person even where the picture it filed has gone since:
    the answer is somebody's, and the picture was only what it taught."""
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (face,) = await store.tracks_of(clip.asset.id)
    await service.confirm(face.id, person)
    await temp_db.execute(
        "DELETE FROM face_references WHERE person_id = ? AND origin = 'confirmed'", (person,)
    )

    recognizer.rule = lambda chip: person_vector(0, 4)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (person, Attribution.CONFIRMED)
    assert await store.reference_tracks([now.id]) == set()


async def test_a_remembered_no_goes_back_only_on_a_face_described_as_the_one_it_was_said_about(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """A refusal is put back by description at the line every remembered decision uses; a face
    described too differently to be the one refused is not assumed to be it."""
    refused = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    await service.reject(refused, person)

    recognizer.rule = lambda chip: person_vector(0, 4)
    await service.scan(clip.asset.id)

    (fresh,) = await store.tracks_of(clip.asset.id)
    assert fresh.id != refused
    assert (await store.rejections()).get(fresh.id) is None


async def test_an_appearance_whose_faces_have_gone_is_compared_with_nobody(
    service: FaceService, store: Store, clip: Ingested, person: str, temp_db: Database
) -> None:
    """Nothing is left to describe it, so nothing is attached to it: not an error."""
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    track_id = new_id()
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "created_at) VALUES (?, ?, 0, 0, 1, 0.8, 0)",
        (track_id, clip.asset.id),
    )

    assert await service._attribute([track_id], await service.configuration()) == {}

    track = await store.track(track_id)
    assert track is not None and track.person_id is None


# --- a re-match over the questions standing --------------------------------------------------------


async def test_a_question_on_a_face_set_aside_is_not_judged_again(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """Set aside is somebody's answer about that face; a re-match that scored it would recognize a
    face somebody put out of the way."""
    track_id = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    await store.attribute(
        track_id, person, confidence=0.5, attribution=Attribution.SUGGESTED, asked_by=AskedBy.MATCH
    )
    pile = new_id()
    await temp_db.execute(
        "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
        "VALUES (?, 'ignored', x'00', 1, 0, 0)",
        (pile,),
    )
    await temp_db.execute("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile, track_id))

    assert await service.rematch() == 0

    track = await store.track(track_id)
    assert track is not None
    assert (track.person_id, track.attribution, track.confidence) == (
        person,
        Attribution.SUGGESTED,
        0.5,
    )


async def test_a_question_about_somebody_with_no_pictures_to_judge_it_by_stands(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """No evidence is not evidence against: the person proposed has no picture this model
    described, so the question is neither withdrawn nor moved to the person it looks like."""
    track_id = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    other = await make_person(temp_db, "Bryn Calloway")
    await store.attribute(
        track_id, other, confidence=0.5, attribution=Attribution.SUGGESTED, asked_by=AskedBy.MATCH
    )

    assert await service.rematch() == 0

    track = await store.track(track_id)
    assert track is not None
    assert (track.person_id, track.attribution, track.confidence) == (
        other,
        Attribution.SUGGESTED,
        0.5,
    )


async def test_a_face_answered_while_a_rematch_runs_keeps_the_newer_answer_and_no_receipt(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody answers between the re-match reading a face and writing it. The write finds the face
    no longer as it was read and leaves it, so the newer decision stands, and the run records
    neither a question it recognized nor a stranger it named, because neither happened."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    ada = await _somebody_else_described_as(store, temp_db, person_vector(0), "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    (asked,) = await store.tracks_of(clip.asset.id)
    (nobodys,) = await store.tracks_of(other_clip.asset.id)
    await store.attribute(
        asked.id, ada, confidence=0.5, attribution=Attribution.SUGGESTED, asked_by=AskedBy.MATCH
    )
    restate = store.restate

    async def answered_first(rulings: Any) -> Any:
        for ruling in rulings:
            await store.attribute(
                ruling.track_id, bryn, confidence=1.0, attribution=Attribution.CONFIRMED
            )
        return await restate(rulings)

    monkeypatch.setattr(store, "restate", answered_first)

    assert await service.rematch() == 0

    now = await store.tracks([asked.id, nobodys.id])
    assert {(one.person_id, one.attribution) for one in now.values()} == {
        (bryn, Attribution.CONFIRMED)
    }
    _receipts, total = await written.recent(limit=5, offset=0)
    assert total == 0


async def test_undoing_sifts_match_leaves_a_face_since_given_to_somebody_else(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    track_id = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    other = await make_person(temp_db, "Bryn Calloway")
    await store.attribute(track_id, other, confidence=1.0, attribution=Attribution.CONFIRMED)

    assert await service.unmatch([track_id, new_id()], person) == 0

    track = await store.track(track_id)
    assert track is not None
    assert (track.person_id, track.attribution) == (other, Attribution.CONFIRMED)
