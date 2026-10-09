# SPDX-License-Identifier: AGPL-3.0-or-later
"""What may be drawn in the corner of a tile, and when; seven marks, three answers each."""

from __future__ import annotations

from dataclasses import dataclass

ALWAYS = "always"
ON_HOVER = "hover"
NEVER = "never"

ANSWERS: tuple[str, ...] = (ALWAYS, ON_HOVER, NEVER)

#: About the tile, not the mouse: the middle answer is true of keyboard focus too.
ANSWER_LABELS: tuple[str, ...] = ("Always", "Only when I point at it", "Never")


VIEWS_KEY = "appearance.tile.views"
O_COUNT_KEY = "appearance.tile.o_count"
PINNED_KEY = "appearance.tile.pinned"
SHARING_KEY = "appearance.tile.sharing"
HIDDEN_KEY = "appearance.tile.hidden"
FAVORITE_KEY = "appearance.tile.favorite"
RATING_KEY = "appearance.tile.rating"
GIF_KEY = "appearance.tile.gif"
DURATION_KEY = "appearance.tile.duration"


@dataclass(frozen=True, slots=True)
class TileMark:
    """One mark, and what a person is choosing when they change it."""

    key: str
    default: str
    label: str
    help: str


#: Every mark a tile can carry, in order; quiet by default except GIF and the running time.
TILE_MARKS: tuple[TileMark, ...] = (
    TileMark(
        key=VIEWS_KEY,
        default=ON_HOVER,
        label="Times watched",
        help="An eye and a number, on anything you have opened at least once.",
    ),
    TileMark(
        key=O_COUNT_KEY,
        default=ON_HOVER,
        label="O counter",
        help="A drop and a number, on anything you have pressed the O counter on.",
    ),
    TileMark(
        key=PINNED_KEY,
        default=ON_HOVER,
        label="Pinned",
        help="A pin, on anything you have put at the top of a wall.",
    ),
    TileMark(
        key=SHARING_KEY,
        default=ON_HOVER,
        label="Shared or private",
        help=(
            "A badge on anything you have shared with a guest or kept private from one. It shows "
            "that a choice was made, never who it was about."
        ),
    ),
    TileMark(
        key=HIDDEN_KEY,
        default=ON_HOVER,
        label="Hidden",
        help="A crossed-out eye, on anything you keep out of the ordinary view.",
    ),
    TileMark(
        key=FAVORITE_KEY,
        default=ON_HOVER,
        label="Heart",
        help=(
            "The heart you press to like a file. When off, you can still like a file from the "
            "file itself or from its menu."
        ),
    ),
    TileMark(
        key=RATING_KEY,
        default=ON_HOVER,
        label="Rating",
        help="The rating you gave a file, beside the heart. You rate a file on the file itself.",
    ),
    TileMark(
        key=GIF_KEY,
        default=ALWAYS,
        label="The word GIF",
        help="In the corner of a looping picture, where a video shows its length.",
    ),
    TileMark(
        key=DURATION_KEY,
        default=ALWAYS,
        label="Duration",
        help="The clock in the bottom corner of a video.",
    ),
)

TILE_MARK_KEYS: frozenset[str] = frozenset(mark.key for mark in TILE_MARKS)

#: The sharing badge's former key, named so the migration and its test agree.
RETIRED_SHARING_MARKS_KEY = "appearance.show_sharing_marks"
