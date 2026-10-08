# SPDX-License-Identifier: AGPL-3.0-or-later
"""Theater: several videos at the same time, each cell drawing from a query of its own.

A wall of one to four cells, with or without previews, in one of the named shapes. Each cell holds a
query, fills a run from it, plays through that run and moves on, on its own clock, with its own
sound, and with a timeline of its own so a cell can be scrubbed and looped like an ordinary video.

Almost none of that is here. A cell is the search route deciding what it may draw, the playback
route deciding how this browser should play a file, and the player's own stage drawing it: all of
which existed. What this slice owns is the one thing none of them could: a wall, saved under a name,
so that setting four cells up is done once rather than every time.

Desktop only, and that is a product decision rather than a limit of the code: several audible videos
together is the point of the surface, and one of the two engines refuses it outright on a phone.
"""

from __future__ import annotations

from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.theater import schema  # noqa: F401 (imported so the tables register themselves)
from sift.slices.theater.router import router
from sift.slices.theater.service import (
    END_BEHAVIOURS,
    LAYOUT_CELLS,
    MAX_TIMER_SECONDS,
    MEDIA_KINDS,
    ORDERINGS,
    SERVICE,
    Arrangement,
    Cell,
    NameTaken,
    TheaterService,
    Unusable,
)

LAYOUT_KEY = "theater.layout"
AUTOPLAY_KEY = "theater.autoplay"
TIMER_KEY = "theater.timer_seconds"
CENTER_STAGE_KEY = "theater.center_stage"
RESUME_KEY = "theater.resume"

#: What each layout is called on screen, in the order the picker offers them, and in the picker's
#: own words (`layouts.ts`), so a layout has one name on the wall and in Settings.
_OFFERED: tuple[str, ...] = (
    "single",
    "side_by_side",
    "side_by_side_by_side",
    "stacked",
    "grid",
    "center_stage",
    "center_stage_two",
    "center_stage_three",
    "center_stage_grid",
)

_LAYOUT_LABELS: tuple[str, ...] = (
    "Grid 1x1",
    "Grid 1x2 (P)",
    "Grid 1x3",
    "Grid 1x2 (L)",
    "Grid 2x2",
    "Stage View 1x1",
    "Stage View 1x2",
    "Stage View 1x3",
    "Stage View 2x2",
)

register_setting(
    key=LAYOUT_KEY,
    # The wall is drawn in the browser and every one of these is read there. The server stores them
    # and has no use for any of them.
    read_by=ReadBy.CLIENT,
    scope="user",
    # Three across: the wall Theater is for, several videos at the same time, from the first open.
    default="side_by_side_by_side",
    section="Theater",
    label="Default layout when Theater opens",
    help="You can choose another from the wall itself.",
    # What the picker OFFERS, which is a shorter list than what the server accepts. See
    # `LAYOUT_CELLS`, which still answers to the names retired walls are filed under.
    choices=_OFFERED,
    choice_labels=_LAYOUT_LABELS,
)

register_setting(
    key=CENTER_STAGE_KEY,
    # Drawn in the browser, like every other Theater preference. The server stores the word and has
    # no use for it.
    read_by=ReadBy.CLIENT,
    scope="user",
    default="pick",
    section="Theater",
    label="When a preview comes up",
    help=(
        "Stage View shows up to four videos in focus with previews under them. "
        "Double-clicking a preview always brings it into focus."
    ),
    disclosure=(
        "Something new means the next file in that preview's queue. Resuming after a pause, a "
        "seek or a stall isn't a new file, so it doesn't bring a preview into focus."
    ),
    choices=("pick", "newest"),
    choice_labels=("When I double-click it", "As soon as it starts something new"),
)

register_setting(
    key=AUTOPLAY_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=False,
    section="Theater",
    label="Start playing when Theater opens",
    help=("Every cell starts as soon as Theater opens."),
)

register_setting(
    key=TIMER_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=0,
    section="Theater",
    label="Play the next file after",
    automatic_label="No timer",
    disclosure="Leave it empty to wait for each file to end.",
    help=("How long a cell shows one file before it plays the next, finished or not."),
    minimum=0,
    maximum=MAX_TIMER_SECONDS,
    unit="sec",
)

register_setting(
    key=RESUME_KEY,
    # The server stores the switch only; the wall is kept in the browser tab, files by id.
    read_by=ReadBy.CLIENT,
    scope="user",
    default=False,
    section="Theater",
    label="Pick up where you left off",
    help=(
        "Coming back to Theater, or reloading the page, brings back the layout and what each "
        "cell was playing. It's forgotten when the window is closed."
    ),
)

__all__ = [
    "AUTOPLAY_KEY",
    "CENTER_STAGE_KEY",
    "END_BEHAVIOURS",
    "LAYOUT_CELLS",
    "LAYOUT_KEY",
    "MAX_TIMER_SECONDS",
    "MEDIA_KINDS",
    "ORDERINGS",
    "RESUME_KEY",
    "SERVICE",
    "TIMER_KEY",
    "Arrangement",
    "Cell",
    "NameTaken",
    "TheaterService",
    "Unusable",
    "router",
]
