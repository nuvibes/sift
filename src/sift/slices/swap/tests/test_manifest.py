# SPDX-License-Identifier: AGPL-3.0-or-later
"""The manifest: an offer as Sift's groups, each with its totals, and a state that moves as the
swap goes. Pure: an offer in, rows out."""

from __future__ import annotations

import pytest

from sift.slices.swap.manifest import (
    LOOSE,
    SONGS,
    Progress,
    files_of_group,
    grouping,
    held_as,
    keys_skipped,
    manifest_of,
)
from sift.slices.swap.models import (
    GROUP_SAMPLE,
    Offer,
    OfferedFace,
    OfferedFaces,
    OfferedFile,
    OfferedPerson,
    OfferedSite,
    OfferedSong,
    PersonRow,
)

pytestmark = [pytest.mark.unit]


def _file(key: str, **fields: object) -> OfferedFile:
    values: dict[str, object] = {
        "key": key,
        "size": 10,
        "identity": f"id-{key}",
        "kind": "image",
        "title": f"Picture {key}",
        "width": 4,
        "height": 3,
    }
    values.update(fields)
    return OfferedFile.model_validate(values)


def _offer() -> Offer:
    faces = OfferedFaces(
        recognizer="model-a",
        dimension=1,
        faces=[OfferedFace(digest="d1", quality=0.9, vector="AAAAAA==")],
    )
    return Offer(
        people=[
            OfferedPerson(name="Ava Example", faces=faces),
            OfferedPerson(name="Bryn Calloway"),
            OfferedPerson(name="Orla Tennant", faces=faces),
        ],
        sites=[OfferedSite(name="Northlight Media"), OfferedSite(name="Clipyard")],
        files=[
            _file("a1", people=[0]),
            _file("a2", people=[0, 1]),
            _file("b1", people=[1], site="Northlight Media"),
            _file("s1", site="Northlight Media", username="northlight"),
            _file("s2", site="Northlight Media", username="northlight"),
            _file("s3", site="Northlight Media", username="nl.extras"),
            _file("m1", song=OfferedSong(name="Summer song")),
            _file("l1", kind="video", duration_ms=5000, size=30),
            # A Site the offer does not list is a lie, and the file is loose.
            _file("l2", site="Nowhere"),
        ],
    )


def test_a_file_sits_under_every_person_on_it_else_under_one_other_group() -> None:
    groups = grouping(_offer())
    # Every person, even one offered for faces alone; then the Sites, Songs and loose that hold any.
    assert groups.keys == ("person:0", "person:1", "person:2", "site:0", SONGS, LOOSE)
    assert groups.members["person:0"] == (0, 1)
    assert groups.members["person:1"] == (1, 2)
    assert groups.members["person:2"] == ()
    assert groups.members["site:0"] == (3, 4, 5)
    assert groups.members[SONGS] == (6,)
    assert groups.members[LOOSE] == (7, 8)
    assert groups.of_file[1] == ("person:0", "person:1")


def test_the_guest_reads_what_it_has_and_what_it_would_take_per_group() -> None:
    offer = _offer()
    held = held_as({"a1": "same", "s1": "near", "s2": "near"})
    rows = {
        0: PersonRow(
            index=0,
            name="Ava Example",
            files=1,
            bytes=10,
            held=1,
            match_id="p1",
            match_name="Ava Example",
            matched_by="name",
        )
    }
    drawn = {
        row.key: row
        for row in manifest_of(offer, grouping(offer), Progress(), held=held, rows=rows)
    }
    ava, site, orla = drawn["person:0"], drawn["site:0"], drawn["person:2"]
    assert (ava.files, ava.have, ava.to_take, ava.held_same, ava.held_look) == (2, 1, 1, 1, 0)
    assert (ava.match_name, ava.matched_by, ava.local_id, ava.faces) == (
        "Ava Example",
        "name",
        "p1",
        1,
    )
    assert (site.name, site.files, site.have, site.to_take, site.held_look) == (
        "Northlight Media",
        3,
        2,
        1,
        2,
    )
    assert site.usernames == 2 and site.state == "offered"
    assert (orla.files, orla.state, orla.faces) == (0, "offered", 1)
    assert [one.held for one in ava.sample] == ["same", None]
    assert drawn[LOOSE].sample[0].duration_ms == 5000
    assert drawn[SONGS].name is None and drawn[SONGS].kind == "songs"


def test_a_group_wholly_held_reads_held_before_and_after_the_answer() -> None:
    offer = Offer(files=[_file("x")])
    groups = grouping(offer)
    held = held_as({"x": "same"})
    assert manifest_of(offer, groups, Progress(), held=held)[0].state == "held"
    after = manifest_of(offer, groups, Progress(answered=True), held=held)[0]
    assert (after.state, after.to_take) == ("held", 0)


def test_the_state_moves_from_offered_to_landed_on_the_receiver() -> None:
    offer = _offer()
    groups = grouping(offer)
    held = held_as({})

    def states(progress: Progress) -> dict[str, str]:
        return {row.key: row.state for row in manifest_of(offer, groups, progress, held=held)}

    # Taken: Ava's two and the loose two; Northlight skipped.
    wanted = (0, 1, 7, 8)
    assert states(Progress(answered=True, wanted=wanted))["site:0"] == "skipped"
    assert states(Progress(answered=True, wanted=wanted))["person:0"] == "waiting"
    moving = Progress(answered=True, wanted=wanted, done=(0,), crossing=(1, 7))
    drawn = {row.key: row for row in manifest_of(offer, groups, moving, held=held)}
    assert (drawn["person:0"].state, drawn["person:0"].done, drawn["person:0"].crossing) == (
        "crossing",
        1,
        1,
    )
    # A done file outside what was wanted, or past the offer, is not counted anywhere.
    landed = Progress(answered=True, wanted=wanted, done=(0, 1, 7, 3, 99), failed=(8,))
    drawn = {row.key: row for row in manifest_of(offer, groups, landed, held=held)}
    assert drawn["person:0"].state == "landed" and drawn[LOOSE].failed == 1
    assert drawn[LOOSE].state == "landed" and drawn["site:0"].done == 0


def test_the_sender_reads_offered_then_taking_skipped_and_sent() -> None:
    offer = _offer()
    groups = grouping(offer)
    before = manifest_of(offer, groups, Progress(), held=None)
    assert {row.state for row in before} == {"offered"}
    assert all(row.have is None and row.to_take == 0 for row in before)
    answered = Progress(answered=True, wanted=(3, 4, 5, 6), done=(3, 4, 5))
    drawn = {row.key: row for row in manifest_of(offer, groups, answered, held=None)}
    assert drawn["site:0"].state == "sent" and drawn["site:0"].to_take == 3
    assert drawn[SONGS].state == "taking"
    assert drawn["person:0"].state == "skipped"
    crossing = Progress(answered=True, wanted=(6,), crossing=(6,))
    assert manifest_of(offer, groups, crossing, held=None)[4].state == "crossing"


def test_a_big_group_carries_a_few_and_pages_the_rest() -> None:
    offer = Offer(files=[_file(f"file{n}") for n in range(GROUP_SAMPLE + 7)])
    groups = grouping(offer)
    (row,) = manifest_of(offer, groups, Progress(), held=held_as({}))
    assert (len(row.sample), row.more) == (GROUP_SAMPLE, 7)
    page, total = files_of_group(offer, groups, LOOSE, held=None, offset=10, limit=5)
    assert total == GROUP_SAMPLE + 7 and [one.key for one in page] == ["file10", "file11"]
    assert files_of_group(offer, groups, LOOSE, held=None, offset=-3, limit=1)[0][0].key == "file0"
    with pytest.raises(KeyError):
        files_of_group(offer, groups, "site:7", held=None, offset=0, limit=5)


def test_a_skipped_group_is_said_as_the_answer_already_says_it() -> None:
    offer = _offer()
    groups = grouping(offer)
    people, unticked = keys_skipped(offer, groups, ["person:1", "site:0", SONGS])
    assert people == {1}
    assert unticked == {"s1", "s2", "s3", "m1"}
    with pytest.raises(ValueError, match="group"):
        keys_skipped(offer, groups, ["collection:1"])
