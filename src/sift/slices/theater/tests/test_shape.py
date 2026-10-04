# SPDX-License-Identifier: AGPL-3.0-or-later
"""A wall's shape: what is refused, and what a row that will not parse falls back to.

Both halves are here because they fail in opposite directions. A shape that is REFUSED never
reaches the database, so the wall that would have drawn wrong is never stored. A shape that will not
READ has already been stored: the row exists, somebody saved it, and the only useful answer is the
preset it was filed under, because a wall that refuses to open is worse than one that opens in the
shape it started as.
"""

from __future__ import annotations

import pytest

from sift.slices.theater.service import (
    CUSTOM,
    MOST_CELLS,
    Cell,
    Shape,
    Slot,
    Unusable,
    _checked,
    _read_shape,
    _write_shape,
)


def _cell() -> Cell:
    return Cell(
        source="",
        media_kind="video_gif",
        ordering="shuffle",
        end_behaviour="once",
        timer_seconds=None,
        volume=100,
    )


def _row(*widths: int) -> Shape:
    slots: list[Slot] = []
    col = 0
    for width in widths:
        slots.append(Slot(row=0, col=col, row_span=1, col_span=width))
        col += width
    return Shape(rows=1, cols=col, slots=tuple(slots))


class TestReadingOne:
    """What comes back out of the column, including everything that could be in it and should not."""

    def test_a_shape_survives_the_round_trip(self) -> None:
        shape = _row(2, 1)
        assert _read_shape(_write_shape(shape)) == shape

    def test_nothing_stored_is_no_shape(self) -> None:
        """Every wall saved before this column existed. They are drawn from their preset name."""
        assert _read_shape(None) is None
        assert _read_shape("") is None

    def test_text_that_is_not_json_is_no_shape(self) -> None:
        assert _read_shape("{not json") is None

    def test_json_that_is_not_an_object_is_no_shape(self) -> None:
        assert _read_shape("[1, 2, 3]") is None

    def test_an_object_missing_a_part_is_no_shape(self) -> None:
        assert _read_shape('{"rows": 1, "cols": 1}') is None
        assert _read_shape('{"rows": 1, "cols": 1, "slots": [{"row": 0}]}') is None

    def test_a_part_that_is_not_a_number_is_no_shape(self) -> None:
        held = '{"rows": 1, "cols": 1, "slots": [{"row": "x", "col": 0, "row_span": 1, "col_span": 1}]}'
        assert _read_shape(held) is None

    def test_slots_that_are_not_a_list_is_no_shape(self) -> None:
        assert _read_shape('{"rows": 1, "cols": 1, "slots": 7}') is None

    def test_nothing_is_written_for_no_shape(self) -> None:
        assert _write_shape(None) is None


class TestRefusingOne:
    """A wall that cannot be drawn, refused before it is stored rather than after it is loaded."""

    def test_a_grid_of_nothing(self) -> None:
        with pytest.raises(Unusable, match="at least one row"):
            _checked(CUSTOM, Shape(rows=0, cols=1, slots=()), 0, ())

    def test_more_places_than_cells(self) -> None:
        with pytest.raises(Unusable, match="2 places"):
            _checked(CUSTOM, _row(1, 1), 0, (_cell(),))

    def test_more_cells_than_a_wall_holds(self) -> None:
        wide = _row(*([1] * (MOST_CELLS + 1)))
        with pytest.raises(Unusable, match=f"1 to {MOST_CELLS}"):
            _checked(CUSTOM, wide, 0, tuple(_cell() for _ in range(MOST_CELLS + 1)))

    def test_a_cell_covering_no_squares(self) -> None:
        shape = Shape(rows=1, cols=1, slots=(Slot(row=0, col=0, row_span=0, col_span=1),))
        with pytest.raises(Unusable, match="covers no squares"):
            _checked(CUSTOM, shape, 0, (_cell(),))

    def test_a_cell_outside_the_wall(self) -> None:
        shape = Shape(rows=1, cols=1, slots=(Slot(row=-1, col=0, row_span=1, col_span=1),))
        with pytest.raises(Unusable, match="sits outside"):
            _checked(CUSTOM, shape, 0, (_cell(),))

    def test_a_cell_past_the_edge(self) -> None:
        shape = Shape(rows=1, cols=1, slots=(Slot(row=0, col=0, row_span=1, col_span=2),))
        with pytest.raises(Unusable, match="past the edge"):
            _checked(CUSTOM, shape, 0, (_cell(),))

    def test_two_cells_over_one_square(self) -> None:
        shape = Shape(
            rows=1,
            cols=2,
            slots=(
                Slot(row=0, col=0, row_span=1, col_span=2),
                Slot(row=0, col=1, row_span=1, col_span=1),
            ),
        )
        with pytest.raises(Unusable, match="share the square"):
            _checked(CUSTOM, shape, 0, (_cell(), _cell()))

    def test_a_shaped_wall_filed_under_a_name_nothing_knows(self) -> None:
        """The shape draws, and the name it is filed under still has to mean something. Otherwise
        a wall stored under a typo would read back as a preset that does not exist."""
        with pytest.raises(Unusable, match="unknown layout"):
            _checked("sideways", _row(1), 0, (_cell(),))

    def test_a_shaped_wall_may_be_filed_under_a_preset_it_matches(self) -> None:
        layout, shape, _strip, cells = _checked("side_by_side", _row(1, 1), 0, (_cell(), _cell()))
        assert layout == "side_by_side"
        assert shape == _row(1, 1)
        assert len(cells) == 2

    def test_a_wall_with_a_strip_carries_more_cells_than_the_shape_has_places(self) -> None:
        """Center stage sends six cells for a shape with one place, and that is not a mistake.

        The extra cells are the previews in the strip under the wall. Judging the shape alone
        would refuse every Center stage wall at the moment somebody tries to keep one.
        """
        _layout, shape, strip, cells = _checked(
            CUSTOM, _row(1), 5, tuple(_cell() for _ in range(6))
        )

        assert strip == 5
        assert shape is not None and len(shape.slots) == 1
        assert len(cells) == 6

    def test_a_strip_the_shape_leaves_no_room_for_is_refused(self) -> None:
        """Nine cells in all, however they are divided between the places and the strip.

        The strip has no cap of its own: a cap of five would stop a wall of three in focus at eight
        while a wall of four reached nine.
        """
        with pytest.raises(Unusable, match="room for 8 previews"):
            _checked(CUSTOM, _row(1), 9, tuple(_cell() for _ in range(10)))

    def test_a_strip_may_take_every_cell_the_places_do_not(self) -> None:
        _layout, _shape, strip, cells = _checked(
            CUSTOM, _row(1, 1, 1), 6, tuple(_cell() for _ in range(9))
        )
        assert strip == 6
        assert len(cells) == 9

    def test_a_strip_that_does_not_account_for_every_cell_is_refused(self) -> None:
        """The places and the strip together have to be exactly the cells sent.

        One short is a wall that draws an empty rectangle; one over is a cell nothing draws, holding
        a source somebody set up and will never see again.
        """
        with pytest.raises(Unusable, match="places and a strip"):
            _checked(CUSTOM, _row(1), 2, tuple(_cell() for _ in range(4)))

    def test_a_PRESET_wall_with_an_impossible_strip_is_refused(self) -> None:
        """The one refusal on the preset path.

        A wall with no shape of its own is judged by its NAME (how many cells that preset holds)
        and the strip beside it is a separate number the same row carries, which nothing else
        stops from being nonsense.

        It matters because it is the shape a row from ANOTHER version arrives in. A preset name is
        the one thing every version of Sift has understood, so an old client's wall reaches here
        with a name, a cell count and whatever it happened to put in `strip`.
        """
        with pytest.raises(Unusable, match="a wall holds 9 cells"):
            _checked("center_stage", None, MOST_CELLS + 1, tuple(_cell() for _ in range(6)))

    def test_and_a_preset_wall_with_a_strip_inside_the_wall_is_taken(self) -> None:
        """The positive control. Without it a refusal that fired for every preset would pass."""
        layout, shape, strip, cells = _checked(
            "center_stage", None, 5, tuple(_cell() for _ in range(6))
        )

        assert layout == "center_stage"
        assert shape is None, "a preset wall carries no shape of its own"
        assert strip == 5
        assert len(cells) == 6
