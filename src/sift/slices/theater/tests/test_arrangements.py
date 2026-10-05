# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved walls: whose they are, and what a wall that does not add up is answered with.

Two groups of test and they are testing different kinds of promise. The first is ordinary CRUD over
HTTP. The second is the part that would be quiet if it broke: an id belonging to somebody else has
to be answered exactly as an id that was never minted, or the answer itself says whether the row
exists.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.slices.theater.tests.conftest import cell, row, sign_in, wall

pytestmark = pytest.mark.integration

ARRANGEMENTS = "/api/theater/arrangements"


def _save(client: TestClient, **overrides: object) -> dict[str, Any]:
    response = client.post(ARRANGEMENTS, json=wall(**overrides))  # type: ignore[arg-type]
    assert response.status_code == 201, response.text
    answer: dict[str, Any] = response.json()
    return answer


def test_a_new_account_has_no_walls(client: TestClient) -> None:
    sign_in(client)
    assert client.get(ARRANGEMENTS).json() == {"items": []}


def test_a_saved_wall_reads_back_exactly_as_it_was_sent(client: TestClient) -> None:
    sign_in(client)
    cells = [
        cell("people:jane", ordering="in_order", end_behaviour="loop_all", volume=40),
        cell("fav:yes", media_kind="all", timer_seconds=30, volume=0),
    ]
    saved = _save(client, name="Two of them", cells=cells)

    assert saved["name"] == "Two of them"
    assert saved["layout"] == "side_by_side"
    assert saved["cells"] == cells

    listed = client.get(ARRANGEMENTS).json()["items"]
    assert listed == [saved]


def test_a_cell_sent_without_a_shape_reads_back_as_dynamic(client: TestClient) -> None:
    """The shape of a cell arrived after walls did, and an older client sends none.

    `dynamic` is not a stand-in for a missing answer: it is the answer every wall saved before this
    already had, because a cell took the shape of whatever it was playing. So the default is the
    truth about those rows rather than a guess, and it comes back as a word the client can act on
    instead of a null it would have to interpret.
    """
    sign_in(client)
    without = cell("people:jane")
    del without["aspect"]

    saved = _save(client, name="From an older client", cells=[without, cell()])

    assert [one["aspect"] for one in saved["cells"]] == ["dynamic", "dynamic"]


def test_a_cell_keeps_the_shape_it_was_given(client: TestClient) -> None:
    """A shape is stored and handed back, and the server has no opinion about which ones exist.

    Deliberately a name this version's client does not draw. What shapes there are is a fact about
    the browser (the server never draws a cell), so judging the word here would be a second copy
    of a list that lives in the client, and adding a shape would become a release of both halves.
    The client falls back to Dynamic for anything it cannot draw; see `Cell.adopt`.
    """
    sign_in(client)
    saved = _save(client, name="An odd one", cells=[cell(aspect="anamorphic"), cell()])

    assert [one["aspect"] for one in saved["cells"]] == ["anamorphic", "dynamic"]
    assert client.get(ARRANGEMENTS).json()["items"] == [saved]


def test_cells_keep_the_order_they_were_given(client: TestClient) -> None:
    """Position is data. A wall whose cells came back shuffled would load as a different wall."""
    sign_in(client)
    cells = [cell("in:one"), cell("in:two"), cell("in:three"), cell("in:four")]
    _save(client, name="Four", layout="grid", cells=cells)

    read_back = client.get(ARRANGEMENTS).json()["items"][0]
    assert [one["source"] for one in read_back["cells"]] == [
        "in:one",
        "in:two",
        "in:three",
        "in:four",
    ]


def test_a_walls_name_is_tidied_rather_than_stored_as_typed(client: TestClient) -> None:
    sign_in(client)
    assert _save(client, name="  Front   room  ")["name"] == "Front room"


def test_a_name_this_account_already_uses_is_refused(client: TestClient) -> None:
    """The wall is several cells' worth of setting up, so a name collision is answered rather than
    quietly overwriting the one that is there."""
    sign_in(client)
    _save(client, name="Front room")

    clash = client.post(ARRANGEMENTS, json=wall(name="Front room"))
    assert clash.status_code == 409
    # Word for word, because the panel's toast shows it as it is.
    assert clash.json()["detail"] == 'You already have a Saved Layout called "Front room".'

    # And the wall that was there is still there, unchanged.
    assert len(client.get(ARRANGEMENTS).json()["items"]) == 1


def test_an_account_may_not_keep_more_walls_than_the_cap(client: TestClient) -> None:
    """A guest saves walls of their own, and a user can be taken over, so the count is bounded.

    Changing a wall they already have is deliberately not capped: somebody sitting at the limit must
    still be able to edit what is there, and an update replaces a row rather than adding one.
    """
    from sift.slices.theater.service import MAX_ARRANGEMENTS

    sign_in(client)
    for index in range(MAX_ARRANGEMENTS):
        _save(client, name=f"Wall {index}")

    full = client.post(ARRANGEMENTS, json=wall(name="One more"))
    assert full.status_code == 409
    assert full.json()["detail"] == (
        f"You already have {MAX_ARRANGEMENTS} Saved Layouts. Delete one to save another."
    )

    held = client.get(ARRANGEMENTS).json()["items"]
    assert len(held) == MAX_ARRANGEMENTS
    changed = client.patch(f"{ARRANGEMENTS}/{held[0]['id']}", json=wall(name=held[0]["name"]))
    assert changed.status_code == 200


def test_the_same_name_on_two_accounts_is_not_a_collision(client: TestClient) -> None:
    sign_in(client, who="one")
    _save(client, name="Front room")

    sign_in(client, who="two")
    assert client.post(ARRANGEMENTS, json=wall(name="Front room")).status_code == 201


def test_a_name_that_is_only_spaces_is_refused(client: TestClient) -> None:
    sign_in(client)
    assert client.post(ARRANGEMENTS, json=wall(name="   ")).status_code == 422


def test_a_wall_can_be_changed_and_keeps_its_id(client: TestClient) -> None:
    """Loading a wall, moving a cell and saving is the ordinary way one is edited, so it is one
    route rather than a rename beside a re-save."""
    sign_in(client)
    saved = _save(client, name="Front room")

    changed = client.patch(
        f"{ARRANGEMENTS}/{saved['id']}",
        json=wall(name="Back room", layout="grid", cells=[cell("fav:yes")] * 4),
    )
    assert changed.status_code == 200
    assert changed.json()["id"] == saved["id"]
    assert changed.json()["name"] == "Back room"
    assert changed.json()["layout"] == "grid"
    assert len(changed.json()["cells"]) == 4

    # The cells from the shape it used to be are gone rather than left behind it.
    assert len(client.get(ARRANGEMENTS).json()["items"][0]["cells"]) == 4


def test_a_wall_may_keep_its_own_name_when_it_is_changed(client: TestClient) -> None:
    """The collision check has to exclude the row being written, or nothing could ever be edited
    without also being renamed."""
    sign_in(client)
    saved = _save(client, name="Front room")

    same_name = client.patch(
        f"{ARRANGEMENTS}/{saved['id']}", json=wall(name="Front room", cells=[cell("fav:yes")] * 2)
    )
    assert same_name.status_code == 200
    assert same_name.json()["cells"][0]["source"] == "fav:yes"


def test_renaming_onto_another_wall_of_this_accounts_is_refused(client: TestClient) -> None:
    sign_in(client)
    _save(client, name="Front room")
    second = _save(client, name="Back room")

    clash = client.patch(f"{ARRANGEMENTS}/{second['id']}", json=wall(name="Front room"))
    assert clash.status_code == 409
    assert clash.json()["detail"] == 'You already have a Saved Layout called "Front room".'


def test_a_wall_can_be_deleted(client: TestClient) -> None:
    sign_in(client)
    saved = _save(client, name="Front room")

    assert client.delete(f"{ARRANGEMENTS}/{saved['id']}").status_code == 204
    assert client.get(ARRANGEMENTS).json() == {"items": []}


def test_deleting_a_wall_takes_its_cells_with_it(client: TestClient) -> None:
    """The cells cascade rather than being left orphaned, so a later wall cannot inherit them."""
    sign_in(client)
    saved = _save(client, name="Front room")
    client.delete(f"{ARRANGEMENTS}/{saved['id']}")

    again = _save(client, name="Front room", cells=[cell("in:one"), cell("in:two")])
    assert [one["source"] for one in again["cells"]] == ["in:one", "in:two"]


class TestAnotherAccountsWall:
    """The id of somebody else's wall is answered exactly as an id that names nothing at all.

    This is the whole of the slice's security surface. There is no route here that returns media,
    so the only thing that could leak is the existence of a row, and the way that leaks is a
    refusal that reads differently from a not-found.
    """

    @staticmethod
    def _somebody_elses(client: TestClient) -> str:
        sign_in(client, who="one")
        made = client.post(ARRANGEMENTS, json=wall(name="Theirs"))
        assert made.status_code == 201, made.text
        theirs = str(made.json()["id"])
        sign_in(client, who="two")
        return theirs

    def test_it_is_not_in_the_list(self, client: TestClient) -> None:
        self._somebody_elses(client)
        assert client.get(ARRANGEMENTS).json() == {"items": []}

    def test_it_cannot_be_changed(self, client: TestClient) -> None:
        theirs = self._somebody_elses(client)
        assert (
            client.patch(f"{ARRANGEMENTS}/{theirs}", json=wall(name="Mine now")).status_code == 404
        )

    def test_it_cannot_be_deleted(self, client: TestClient) -> None:
        """A no-op rather than a refusal, and the wall is still theirs afterwards. The statement is
        what scopes it, so there is no path here that finds somebody else's row at all."""
        theirs = self._somebody_elses(client)
        assert client.delete(f"{ARRANGEMENTS}/{theirs}").status_code == 204

        sign_in(client, who="one")
        assert [one["name"] for one in client.get(ARRANGEMENTS).json()["items"]] == ["Theirs"]

    def test_the_answer_is_the_same_for_an_id_that_names_nothing(self, client: TestClient) -> None:
        theirs = self._somebody_elses(client)
        invented = "01HX0000000000000000000091"
        assert (
            client.patch(f"{ARRANGEMENTS}/{invented}", json=wall(name="Mine")).status_code
            == client.patch(f"{ARRANGEMENTS}/{theirs}", json=wall(name="Mine")).status_code
        )
        assert (
            client.delete(f"{ARRANGEMENTS}/{invented}").status_code
            == client.delete(f"{ARRANGEMENTS}/{theirs}").status_code
        )

    def test_a_change_that_was_refused_left_their_wall_alone(self, client: TestClient) -> None:
        """The refusal has to happen before the cells are rewritten. A statement that scoped the
        arrangement and not the cells would answer 404 and still have emptied their wall."""
        theirs = self._somebody_elses(client)
        client.patch(f"{ARRANGEMENTS}/{theirs}", json=wall(name="Mine now", layout="grid"))

        sign_in(client, who="one")
        still = client.get(ARRANGEMENTS).json()["items"]
        assert len(still) == 1
        assert still[0]["name"] == "Theirs"
        assert still[0]["layout"] == "side_by_side"
        assert len(still[0]["cells"]) == 2


class TestAWallThatDoesNotAddUp:
    """Refused rather than stored. Every one of these is a wall that cannot be drawn, and storing
    it would move the failure to whoever loads it."""

    def test_an_unknown_layout(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(layout="hexagon"))
        assert answer.status_code == 422
        assert "hexagon" in answer.json()["detail"]

    def test_too_few_cells_for_the_layout(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(layout="grid", cells=[cell(), cell()]))
        assert answer.status_code == 422
        assert "4 cells" in answer.json()["detail"]

    def test_too_many_cells_for_the_layout(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(cells=[cell()] * 3))
        assert answer.status_code == 422

    def test_an_unknown_media_kind(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(cells=[cell(media_kind="audio"), cell()]))
        assert answer.status_code == 422
        assert "cell 1" in answer.json()["detail"]

    def test_an_unknown_order(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(cells=[cell(), cell(ordering="backwards")]))
        assert answer.status_code == 422
        assert "cell 2" in answer.json()["detail"]

    def test_an_unknown_end_behaviour(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(cells=[cell(end_behaviour="forever"), cell()]))
        assert answer.status_code == 422

    @pytest.mark.parametrize("seconds", [0, -5, 3601])
    def test_a_timer_out_of_range(self, client: TestClient, seconds: int) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(cells=[cell(timer_seconds=seconds), cell()]))
        assert answer.status_code == 422

    @pytest.mark.parametrize("volume", [-1, 101])
    def test_a_volume_off_the_scale(self, client: TestClient, volume: int) -> None:
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(cells=[cell(), cell(volume=volume)]))
        assert answer.status_code == 422

    def test_a_wall_that_does_not_add_up_cannot_be_saved_over_a_good_one(
        self, client: TestClient
    ) -> None:
        sign_in(client)
        saved = _save(client, name="Front room", cells=[cell("in:one"), cell("in:two")])

        client.patch(
            f"{ARRANGEMENTS}/{saved['id']}", json=wall(name="Front room", layout="hexagon")
        )

        unchanged = client.get(ARRANGEMENTS).json()["items"][0]
        assert unchanged["layout"] == "side_by_side"
        assert [one["source"] for one in unchanged["cells"]] == ["in:one", "in:two"]


class TestEveryLayout:
    """Each of the seven, with the number of cells it holds, and refusing one cell fewer.

    Written out rather than derived from the same table the service reads, deliberately. A test that
    asked the code how many cells a layout has would agree with any answer the code gave, including
    a wrong one; these are the numbers the seven shapes actually have.
    """

    @pytest.mark.parametrize(
        ("layout", "cells"),
        [
            ("side_by_side", 2),
            ("side_by_side_by_side", 3),
            ("stacked", 2),
            ("stacked_three", 3),
            ("one_above_two", 3),
            ("two_above_one", 3),
            ("grid", 4),
        ],
    )
    def test_it_holds_the_cells_it_is_named_for(
        self, client: TestClient, layout: str, cells: int
    ) -> None:
        sign_in(client)
        good = client.post(
            ARRANGEMENTS, json=wall(name=layout, layout=layout, cells=[cell()] * cells)
        )
        assert good.status_code == 201, good.text

        short = client.post(
            ARRANGEMENTS,
            json=wall(name=f"{layout} short", layout=layout, cells=[cell()] * (cells - 1)),
        )
        assert short.status_code == 422


def test_a_guest_keeps_walls_of_their_own(client: TestClient) -> None:
    """Theater is a way of watching, not an administrative surface, so this is scoped rather than
    admin-gated, and scoped is the stronger of the two."""
    sign_in(client, role="guest")
    _save(client, name="Mine")
    assert [one["name"] for one in client.get(ARRANGEMENTS).json()["items"]] == ["Mine"]


def test_signing_out_is_the_end_of_reading_them(client: TestClient) -> None:
    sign_in(client)
    _save(client, name="Front room")
    client.cookies.clear()
    assert client.get(ARRANGEMENTS).status_code == 401


class TestABuiltWall:
    """A wall that is not one of the presets: it carries its own shape, and the shape is what is
    checked.

    The whole point of storing one is that the arrangement no longer has to be a name from a short
    list, so what the server can still be sure of is that the shape DRAWS: every cell inside the
    grid, one to a square, and as many places as there are cells. Anything else is a wall that
    arrives on somebody's screen wrong rather than absent, which is the worse of the two.
    """

    def test_a_shape_reads_back_exactly_as_it_was_sent(self, client: TestClient) -> None:
        sign_in(client)
        shape = row(2, 1)
        saved = _save(client, layout="custom", shape=shape, cells=[cell(), cell()])

        assert saved["shape"] == shape
        assert saved["layout"] == "custom"
        assert client.get(ARRANGEMENTS).json()["items"][0]["shape"] == shape

    def test_a_wall_saved_without_one_reads_back_without_one(self, client: TestClient) -> None:
        """Every wall kept before walls could be built. It still opens, from its preset name."""
        sign_in(client)
        saved = _save(client, layout="stacked")

        assert saved["shape"] is None
        assert saved["layout"] == "stacked"

    def test_a_shape_decides_the_cell_count_rather_than_the_preset_name(
        self, client: TestClient
    ) -> None:
        """`side_by_side` holds two. With a shape of three, three is right: the name is only what
        the wall is filed under once it has a shape of its own."""
        sign_in(client)
        answer = client.post(
            ARRANGEMENTS,
            json=wall(layout="custom", shape=row(1, 1, 1), cells=[cell(), cell(), cell()]),
        )

        assert answer.status_code == 201, answer.text

    def test_a_shape_with_more_places_than_cells_is_refused(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(
            ARRANGEMENTS, json=wall(layout="custom", shape=row(1, 1, 1), cells=[cell(), cell()])
        )

        assert answer.status_code == 422
        assert "3 places" in answer.json()["detail"]

    def test_two_cells_over_one_square_is_refused(self, client: TestClient) -> None:
        """It would draw one picture on top of another, with no error anywhere."""
        sign_in(client)
        overlapping = {
            "rows": 1,
            "cols": 2,
            "slots": [
                {"row": 0, "col": 0, "row_span": 1, "col_span": 2},
                {"row": 0, "col": 1, "row_span": 1, "col_span": 1},
            ],
        }
        answer = client.post(
            ARRANGEMENTS, json=wall(layout="custom", shape=overlapping, cells=[cell(), cell()])
        )

        assert answer.status_code == 422
        assert "share the square" in answer.json()["detail"]

    def test_a_cell_reaching_past_the_edge_is_refused(self, client: TestClient) -> None:
        sift = {
            "rows": 1,
            "cols": 1,
            "slots": [{"row": 0, "col": 0, "row_span": 1, "col_span": 2}],
        }
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(layout="custom", shape=sift, cells=[cell()]))

        assert answer.status_code == 422
        assert "past the edge" in answer.json()["detail"]

    def test_a_built_wall_can_be_changed_and_keeps_its_shape(self, client: TestClient) -> None:
        sign_in(client)
        made = _save(client, name="Built", layout="custom", shape=row(2, 1), cells=[cell(), cell()])

        wider = row(1, 1, 1)
        answer = client.patch(
            f"{ARRANGEMENTS}/{made['id']}",
            json=wall(name="Built", layout="custom", shape=wider, cells=[cell()] * 3),
        )

        assert answer.status_code == 200, answer.text
        assert answer.json()["shape"] == wider


class TestASmartSource:
    """A cell whose source is a saved SMART search.

    Ranked by meaning rather than matched by word, which is not an order over the same files: it
    is a different set of them. A cell that took the query and dropped the mode would turn a
    search returning a couple of hundred files into one returning none, and report it as "nothing
    here matches what this cell is set to", which is true and useless.
    """

    def test_it_is_kept_and_read_back(self, client: TestClient) -> None:
        sign_in(client)
        answer = client.post(
            ARRANGEMENTS,
            json=wall(cells=[cell("gym", sort="similarity"), cell()]),
        )

        assert answer.status_code == 201
        saved = answer.json()
        assert saved["cells"][0]["sort"] == "similarity"
        assert saved["cells"][1]["sort"] is None

        back = client.get(ARRANGEMENTS).json()["items"][0]
        assert back["cells"][0]["sort"] == "similarity"

    def test_a_mode_nothing_here_offers_is_refused(self, client: TestClient) -> None:
        """Rather than stored and silently ignored on load, which is a cell that plays the wrong
        set of files for a reason nothing on screen mentions."""
        sign_in(client)
        answer = client.post(ARRANGEMENTS, json=wall(cells=[cell(sort="by_vibes"), cell()]))

        assert answer.status_code == 422
        assert "by_vibes" in answer.text
