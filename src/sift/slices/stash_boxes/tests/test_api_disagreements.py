# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box endpoints, over HTTP, against a real application: where a box and the library disagree.

Nothing here reaches a network: the adapter on the running application is replaced, so what is
asserted is which questions the routes would ask and what they hand back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.slices.stash_boxes.tests.api_support import (
    _a_file_disagreeing_about_its_title,
    _a_second_box,
    _decision,
    _file_rows,
    _history_of,
    _keep_local,
    _read,
    _settle,
    _subjects_of,
    _waiting,
    _write,
    a_person,
    a_person_link,
    add_a_box,
    db_path,
    sign_in,
)

pytestmark = pytest.mark.integration


def test_a_field_two_answers_differ_about_is_listed(client: TestClient) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    held = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    body = client.get("/api/stash-boxes/disagreements").json()

    assert [(one["key"], one["mine"], one["theirs"]) for one in body["disagreements"]] == [
        ("birth_date", "1990-01-01", "1991-02-02")
    ]


def test_one_record_is_asked_about_on_its_own(client: TestClient) -> None:
    """What a record's own page asks, and the reason it is a route of its own.

    The library's list grows with every link, and a page draws at most three rows. Both lists come
    out of one rule, so the narrow one finds the same field the wide one does.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    other = a_person(client)
    held = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    mine = client.get(f"/api/stash-boxes/disagreements/person/{person}")
    assert mine.status_code == 200, mine.text
    assert [(one["key"], one["theirs"]) for one in mine.json()["disagreements"]] == [
        ("birth_date", "1991-02-02")
    ]

    # A record nothing is linked to answers with an empty list rather than a refusal, and so does a
    # kind no box has ever heard of, so nothing here says whether a record is there.
    assert client.get(f"/api/stash-boxes/disagreements/person/{other}").json() == {
        "disagreements": []
    }
    assert client.get(f"/api/stash-boxes/disagreements/photo_set/{person}").json() == {
        "disagreements": []
    }


def test_taking_their_answer_writes_it_and_signs_for_what_it_replaced(
    client: TestClient,
) -> None:
    """The receipt records what was REPLACED, which is what makes the undo exact."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    held = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": True,
        },
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["fields"] == 1
    assert answer.json()["decision_id"]
    held = client.get(f"/api/people/{person}").json()["record"]
    assert held["birth_date"] == "1991-02-02"


def test_keeping_your_own_answer_takes_the_row_off_the_panel_and_writes_no_field(
    client: TestClient,
) -> None:
    """ "Keep yours" takes the row off the list and leaves the record untouched.

    The field is not re-written (a field written back with the value it already holds is a change
    in every log that watches for one), but the answer is recorded, or the next read would work
    the same conflict out again.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    held = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": False,
        },
    ).json()

    assert answer["fields"] == 0, "keeping your own answer must not write the field back"
    assert answer["decision_id"], "an answer nobody signed for is an answer nobody can take back"
    assert client.get("/api/stash-boxes/disagreements").json()["disagreements"] == [], (
        "the row somebody just answered is waiting again, which on screen is a dead button"
    )
    held = client.get(f"/api/people/{person}").json()["record"]
    assert held["birth_date"] == "1990-01-01"


def test_a_press_asks_about_its_own_record_and_never_surveys_the_library(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A press is looked up among its OWN record's rows, and the library survey is never paid.

    `settle` throws the survey's memo away, so a route that found the row in the survey would pay
    for the whole library on every press. The survey is made to fail here, so a route that reaches
    for it is a 500 rather than a slow press.
    """
    from sift.slices.stash_boxes.reconcile import Reconciler

    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    async def no_survey(self: Reconciler, viewer: object) -> list[object]:
        raise AssertionError("a press surveyed every linked record to find one row")

    monkeypatch.setattr(Reconciler, "disagreements", no_survey)
    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": False,
        },
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["decision_id"]
    left = client.get(f"/api/stash-boxes/disagreements/person/{person}").json()
    assert left["disagreements"] == []


def test_a_kept_answer_comes_back_as_a_question_when_either_value_moves(
    client: TestClient,
) -> None:
    """An answer is about a PAIR of values, not about a field.

    This is why the answer is stored with both sides on it. Somebody who kept 1990 over 1991 has
    said nothing at all about 1992, so editing the record puts the question back, where a row
    keyed on the field alone would have settled that field for ever on one press.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})
    client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": False,
        },
    )
    assert client.get("/api/stash-boxes/disagreements").json()["disagreements"] == []

    edited = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1992-03-03"}},
    )
    assert edited.status_code == 200, edited.text

    waiting = client.get("/api/stash-boxes/disagreements").json()["disagreements"]
    assert [(one["mine"], one["theirs"]) for one in waiting] == [("1992-03-03", "1991-02-02")]


def test_a_disagreement_nobody_is_having_cannot_be_settled(client: TestClient) -> None:
    """The row is looked up again rather than trusted from the body, and that is the check rather
    than a formality: a client naming a subject, a field and a value could otherwise write anything
    it liked into any record through a route that reads as a reconcile."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": True,
        },
    )

    assert answer.status_code == 404


def test_a_press_settles_the_row_of_the_box_it_names_and_no_other(client: TestClient) -> None:
    """Two boxes disagreeing about ONE field are two rows, and a press settles the one pressed.

    The press names the box. The second box's row is pressed here, so a route that matched the
    field alone would settle the first box's instead and this fails.
    """
    sign_in(client, "admin")
    first = add_a_box(client)
    second = _a_second_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, first, {"birth_date": "1991-02-02"})
    a_person_link(client, person, second, {"birth_date": "1992-03-03"})
    waiting = client.get(f"/api/stash-boxes/disagreements/person/{person}").json()
    assert sorted(one["box_id"] for one in waiting["disagreements"]) == sorted([first, second])

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": second,
            "key": "birth_date",
            "take_theirs": False,
        },
    )

    assert answer.status_code == 200, answer.text
    left = client.get(f"/api/stash-boxes/disagreements/person/{person}").json()["disagreements"]
    assert [(one["box_id"], one["theirs"]) for one in left] == [(first, "1991-02-02")]
    said = _decision(client, answer.json()["decision_id"])
    assert said["title"] == "Kept your birthdate for Jane: 1990-01-01, not FansDB's 1992-03-03"


def test_keeping_your_own_answer_is_one_line_on_the_record_s_history(client: TestClient) -> None:
    """One press, one line: the answer kept and the receipt of the press are not drawn as two.

    The receipt declares the answer it kept (`vocabulary.RECEIPT_KEPT`) and the thread says the
    press once, with the Undo. A route that stops declaring it puts the second line back.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": False,
        },
    )

    assert answer.status_code == 200, answer.text
    thread = client.get(f"/api/people/{person}/history").json()
    about = [one for one in thread if "kept" in one["what"].lower()]
    assert [(one["receipt"], one["undo"] is not None) for one in about] == [
        (answer.json()["decision_id"], True)
    ], [one["what"] for one in about]
    # The words the feed says about the same press (`worded.decided_said`), not the kept row's.
    assert about[0]["what"].startswith("You kept your birthdate"), about[0]["what"]


def test_taking_one_box_s_answer_is_one_line_though_it_set_another_aside(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    first = add_a_box(client)
    second = _a_second_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, first, {"birth_date": "1991-02-02"})
    a_person_link(client, person, second, {"birth_date": "1992-03-03"})

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": first,
            "key": "birth_date",
            "take_theirs": True,
        },
    )

    assert answer.status_code == 200, answer.text
    thread = client.get(f"/api/people/{person}/history").json()
    assert not [one for one in thread if one["kind"] == "kept_mine"], [o["what"] for o in thread]
    assert [one for one in thread if one["receipt"] == answer.json()["decision_id"]]


def test_a_press_naming_a_box_that_has_no_row_there_is_refused(client: TestClient) -> None:
    """A box linked to something else, or to nothing, has no row on this record to settle."""
    sign_in(client, "admin")
    box = add_a_box(client)
    other = _a_second_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": other,
            "key": "birth_date",
            "take_theirs": True,
        },
    )

    assert answer.status_code == 404
    held = client.get(f"/api/people/{person}").json()["record"]
    assert held["birth_date"] == "1990-01-01"


def test_taking_their_answer_says_the_field_both_values_and_the_box(client: TestClient) -> None:
    """The receipt says what answer was taken, not only that one was.

    The receipt names the field in the registry's word, the value taken, the value it replaced and
    the box: every one of them in hand at the moment it is written.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": True,
        },
    )

    said = _decision(client, answer.json()["decision_id"])
    assert (
        said["title"] == "Took StashDB's birthdate for Jane: 1991-02-02, where you had 1990-01-01"
    )
    assert said["detail"] == "Birthdate was 1990-01-01 and is now 1991-02-02, from StashDB."


def test_taking_one_boxs_answer_sets_every_other_boxs_different_answer_aside(
    client: TestClient,
) -> None:
    """A pressed row leaves and stays gone.

    Two boxes disagreeing with each other as well as with the library. Taking the second box's
    value ends its row and, with "yours" now its value, the first box's row turns into a new
    question (1992 against 1991) that nobody pressed. Taking one answer is the answer to the
    other, so both rows leave on one press, the
    receipt names the one set aside, and the one Undo brings both questions back as they were.
    A route that stops setting the other box aside leaves a row here and fails.
    """
    sign_in(client, "admin")
    first = add_a_box(client)
    second = _a_second_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, first, {"birth_date": "1991-02-02"})
    a_person_link(client, person, second, {"birth_date": "1992-03-03"})
    before = _waiting(client, person)
    assert len(before) == 2

    answer = _settle(client, person, second, take=True)

    assert _waiting(client, person) == []
    said = _decision(client, str(answer["decision_id"]))
    title = (
        "Took FansDB's birthdate for Jane: 1992-03-03, where you had 1990-01-01,"
        " and set aside StashDB's 1991-02-02"
    )
    assert said["title"] == title
    # The toast says what the receipt says, from the one place it is written, Jane a link.
    assert answer["said"] == title
    pieces = answer["pieces"]
    assert isinstance(pieces, list)
    assert {"text": "Jane", "kind": "person", "id": person}.items() <= pieces[1].items()
    assert said["detail"].endswith("StashDB said 1991-02-02, and that is set aside.")

    undo = client.post(f"/api/workbench/decisions/{answer['decision_id']}/undo")

    assert undo.status_code == 200, undo.text
    assert client.get(f"/api/people/{person}").json()["record"]["birth_date"] == "1990-01-01"
    assert _waiting(client, person) == before


def test_a_box_that_agreed_with_the_old_value_is_set_aside_too(client: TestClient) -> None:
    """The two-box shape: one box AGREED with the library, so it had no row at all until the
    other box's value was taken. Its row is born by the press, and leaves with it."""
    sign_in(client, "admin")
    first = add_a_box(client)
    second = _a_second_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, first, {"birth_date": "1990-01-01"})
    a_person_link(client, person, second, {"birth_date": "1992-03-03"})
    assert [one[0] for one in _waiting(client, person)] == [second]

    answer = _settle(client, person, second, take=True)

    assert _waiting(client, person) == []
    assert str(answer["said"]).endswith(", and set aside StashDB's 1990-01-01")
    client.post(f"/api/workbench/decisions/{answer['decision_id']}/undo")
    assert [one[0] for one in _waiting(client, person)] == [second]


def test_the_undo_puts_back_an_answer_given_on_another_day_rather_than_forgetting_it(
    client: TestClient,
) -> None:
    """The first box had been answered already ("keep mine", 1990 against 1991). Taking the second
    box's value sets the first aside again under the NEW pair, overwriting that row, so an Undo
    that only forgot would lose the earlier answer and bring back a question already settled."""
    sign_in(client, "admin")
    first = add_a_box(client)
    second = _a_second_box(client)
    person = a_person(client)
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-01-01"}}
    )
    a_person_link(client, person, first, {"birth_date": "1991-02-02"})
    a_person_link(client, person, second, {"birth_date": "1992-03-03"})
    _settle(client, person, first, take=False)
    assert [one[0] for one in _waiting(client, person)] == [second]

    answer = _settle(client, person, second, take=True)
    assert _waiting(client, person) == []
    client.post(f"/api/workbench/decisions/{answer['decision_id']}/undo")

    assert [one[0] for one in _waiting(client, person)] == [second]


def test_settling_a_disagreement_about_something_that_has_since_gone_writes_nothing(
    client: TestClient,
) -> None:
    """The row is resolved again before the write, so a subject removed between the read and the
    press answers "nothing was written" rather than raising."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    held = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})
    _write(db_path(client), [("DELETE FROM people WHERE id = ?", (person,))])

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": True,
        },
    )

    assert answer.status_code == 404


def test_taking_a_boxs_answer_says_which_record_it_was_written_to(client: TestClient) -> None:
    """The person, in the word the decision record uses for a person.

    Two vocabularies meet at this route (what a FIELD belongs to, and what a decision can be
    looked up BY), and they are not the same list. A field on a tag or a photo set records no
    subject at all, which is honest: neither has a history for it to appear in.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    held = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": True,
        },
    )

    assert answer.status_code == 200, answer.text
    assert _subjects_of(client, answer.json()["decision_id"]) == {("person", person)}


def test_taking_their_side_on_a_record_kept_local_is_refused_kept_local(
    client: TestClient,
) -> None:
    """A disagreement a box raised before the person was kept local: taking the box's value is a
    stored answer applied, and is refused in the door's words, not "that is not waiting"."""
    from sift.slices.stash_boxes.service import KEPT_LOCAL

    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    held = client.put(
        f"/api/people/{person}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})
    _keep_local(client, "person", person)

    assert client.get("/api/stash-boxes/disagreements").json() == {"disagreements": []}
    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": True,
        },
    )
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == KEPT_LOCAL
    taken = client.post(
        f"/api/stash-boxes/links/person/{person}/{box}/take", json={"keys": ["birth_date"]}
    )
    assert taken.status_code == 409, taken.text
    assert client.get(f"/api/people/{person}").json()["record"]["birth_date"] == "1990-01-01"


def test_a_box_disagreeing_about_a_files_title_is_shown_on_the_file(
    client: TestClient, tmp_path: Path
) -> None:
    """Not dropped silently: the plan that applied the answer works the conflict out, and a file's
    page shows it with a choice exactly as a person, a Site and a tag show theirs. The same panel,
    the same rule, the same wire shape; a file's "link" is the answer it was agreed to. Named by the FILE's own name, never by the box's title, which
    is the value under dispute."""
    asset, box = _a_file_disagreeing_about_its_title(client, tmp_path)

    (row,) = _file_rows(client, asset)

    assert row == {
        "subject": "asset",
        "local_id": asset,
        "name": "one.mp4",
        "box_id": box,
        "box_name": "StashDB",
        "key": "title",
        "mine": "What I Called It",
        "theirs": "What They Call It",
        # A title is said as it is stored: the word rule only touches a box's CONSTANT.
        "mine_said": "What I Called It",
        "theirs_said": "What They Call It",
    }
    sign_in(client, "guest")
    assert client.get(f"/api/stash-boxes/disagreements/asset/{asset}").status_code == 403


def test_keeping_your_title_on_a_file_is_not_asked_again(
    client: TestClient, tmp_path: Path
) -> None:
    """Keep yours records the answer about THESE two values, in the table a person's answer goes in,
    so the box is not asked about that field again for as long as neither side moves. It leaves
    one line in the file's History and writes nothing to the file."""
    asset, box = _a_file_disagreeing_about_its_title(client, tmp_path)

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "asset",
            "local_id": asset,
            "box_id": box,
            "key": "title",
            "take_theirs": False,
        },
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["fields"] == 0
    assert _file_rows(client, asset) == []
    assert client.get(f"/api/assets/{asset}").json()["title"] == "What I Called It"
    # Worded from the receipt's facts when the History is drawn (`reconcile._settled_said`), with
    # the file named as it is now.
    assert any(
        line.startswith("You kept your title for ")
        and line.endswith(", What I Called It, over StashDB's What They Call It")
        for line in _history_of(client, asset)
    ), _history_of(client, asset)


def test_taking_their_title_writes_it_onto_the_file_and_rewrites_no_earlier_line(
    client: TestClient, tmp_path: Path
) -> None:
    """Take theirs writes the box's value through the file's own writer. It writes NO enrichment
    run: a file's History draws the box's line off its latest run, so a run for one field would
    rewrite what the box filled in on the day it was agreed to. The receipt is the one line."""
    asset, box = _a_file_disagreeing_about_its_title(client, tmp_path)
    runs = "SELECT COUNT(*) AS n FROM enrichment_runs WHERE subject = 'asset' AND local_id = ?"
    before = _read(db_path(client), runs, (asset,))[0]["n"]

    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "asset",
            "local_id": asset,
            "box_id": box,
            "key": "title",
            "take_theirs": True,
        },
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["fields"] == 1
    assert client.get(f"/api/assets/{asset}").json()["title"] == "What They Call It"
    assert _file_rows(client, asset) == []
    assert _read(db_path(client), runs, (asset,))[0]["n"] == before
    assert any(
        line.startswith("You chose StashDB's title for ")
        and line.endswith(", What They Call It, over your What I Called It")
        for line in _history_of(client, asset)
    ), _history_of(client, asset)
