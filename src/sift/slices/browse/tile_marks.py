# SPDX-License-Identifier: AGPL-3.0-or-later
"""What may be drawn in the corner of a tile, and when.

## Why these are seven settings and not one

Each mark answers a different question about a file (how often you have watched it, whether it is
pinned, who else can see it, whether you keep it out of sight, whether you like it, what you scored
it, how long it runs) and somebody who wants the clock and nothing else is asking a different
thing from somebody who wants everything except the clock. A single "show marks" switch cannot say
either, and a single switch for the sharing badge would leave the other six not askable at all.

## Why three answers rather than a switch

Because a tile has three behaviours: a mark always drawn, one drawn only while the pointer is over
the tile, and one not drawn. Reducing that to on-and-off would take the middle one away, and the
middle one is the interesting answer, because a mark you can summon
costs no picture until you want it.

`hover` is the honest word for the behaviour rather than the input: it is what the tile already
does, and it happens on keyboard focus too.

## What is NOT here, and why

**The warning that a file is not where Sift left it.** It is not decoration and it is not a
preference: it says the library is out of step with the disk, which is the one thing on a tile
somebody needs to be told whether they asked or not. A switch for it would be a switch for hiding a
fault, and the honest version of that switch is fixing the file.

The order below is the order the marks appear along the top of a tile, then the two at the bottom
left, then the clock. The screen draws them onto a picture of a tile in those positions, so the
order here is what somebody reads down the pane.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Always drawn.
ALWAYS = "always"
#: Drawn while the pointer is over the tile, or while something inside it has keyboard focus.
ON_HOVER = "hover"
#: Not drawn at all.
NEVER = "never"

#: The three answers, in the order a control offers them: most visible to least.
ANSWERS: tuple[str, ...] = (ALWAYS, ON_HOVER, NEVER)

#: What each answer is called on screen. The words are about the TILE rather than about the mouse,
#: because the middle one is true of keyboard focus as well and "on hover" would be a small lie to
#: anybody who never touches a pointer.
ANSWER_LABELS: tuple[str, ...] = ("Always", "Only when I point at it", "Never")


#: One name per mark. Named rather than only spelled inside the tuple below, because a migration,
#: a test and a gate each need to say "the sharing one" without re-typing the string.
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


#: Every mark a tile can carry, in the order they sit on one.
#:
#: A QUIET TILE BY DEFAULT: every mark waits for the pointer except the two that say what the file
#: IS before it is opened, the word GIF and the length of a video, which are always drawn. A grid is
#: read for its pictures, and a mark somebody wants on every tile is one answer away on this pane.
#:
#: A default is what an answer never written reads as, so an install that chose a mark keeps its
#: choice and only the marks nobody touched move.
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

#: The keys, for anything that needs the set rather than the order.
TILE_MARK_KEYS: frozenset[str] = frozenset(mark.key for mark in TILE_MARKS)

#: The sharing badge's former key, from before it was one of seven.
#:
#: Kept as a name rather than as a string in a migration, because the migration and the test that
#: proves it are two places that have to agree about the spelling.
RETIRED_SHARING_MARKS_KEY = "appearance.show_sharing_marks"
