# SPDX-License-Identifier: AGPL-3.0-or-later
"""Playback: direct play, remux, and on-the-fly segment transcoding.

The headline is not "transcode quickly": it is **transcode as rarely as possible**. Most files
most browsers are asked to play, they can play, and the cheapest thing this slice does is find that
out and get out of the way. See `policy` for the tiers and why the client is asked rather than
guessed at, including the middle one, which is written and currently held closed because a stream
copy cannot be cut where a fixed segment grid needs it to be.
"""

from __future__ import annotations

# The two resume preferences are keyed in the kernel, beside the rule they parameterise: the
# grid and the query language read the same two now, and neither may import this slice to learn
# how they are spelled. What they LOOK like is still declared here.
from sift.kernel.attention import PLAYED
from sift.kernel.content.user_state import RESUME_ENABLED_KEY, RESUME_MINIMUM_KEY
from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.player import schema
from sift.slices.player.cache import SEGMENT_CACHE, SegmentCache
from sift.slices.player.jobs import job_limits, register_handlers
from sift.slices.player.policy import ClientCapabilities, Plan, Route, decide
from sift.slices.player.router import (
    CACHE_MAX_GB_KEY,
    DWELL_PICTURES_KEY,
    LOOP_MODE_KEY,
    MAX_TRANSCODE_HEIGHT_KEY,
    MUTED_KEY,
    VOLUME_KEY,
    router,
)
from sift.slices.player.service import SERVICE, PlayerService

LOOP_ONE = "loop_one"
LOOP_ALL = "loop_all"
PLAY_ONCE = "once"

# A clip carries on to the next one in the grid unless somebody says otherwise, and both ways of
# stopping are one press away on the player's own bar.
#
# Stop and repeat are wrong as defaults in the same way: they stop. This is a browser for a library
# of short clips, so stopping at the end of each one means pressing something every few seconds to
# keep watching, with the queue right there unused. Same three answers, same names, same default as
# a Theater cell: a player and a wall disagreeing about what the end of a file means is worse than
# either answer on its own.
register_setting(
    key=LOOP_MODE_KEY,
    # The player runs in the browser. The server stores this and has no use for it: there is
    # no volume on a server, and nothing here would know what to do with one.
    read_by=ReadBy.CLIENT,
    scope="user",
    default=LOOP_ALL,
    section="Playback",
    label="When a video ends",
    help=(
        "Stop stays on the last frame, Repeat plays it again, and Play through moves on to "
        "the next file in the grid."
    ),
    choices=[PLAY_ONCE, LOOP_ONE, LOOP_ALL],
    choice_labels=("Stop", "Repeat", "Play through"),
)

# How loud, remembered, and remembered per person rather than per install.
#
# Volume is the one playback control somebody sets once and expects to hold forever. A browser
# forgets it on every navigation (each screen builds a new video element, and a new video element
# starts at full), so without somewhere to keep it, opening a second clip is a second jump scare.
# Stored against the user rather than in the browser so it follows somebody between their phone and
# their desk, which is where the difference is most noticeable.
#
# Whole percent, not a float. It is a slider position, it is compared for equality when deciding
# whether to save, and a float that reads back as 0.30000000000000004 is a save on every frame.
register_setting(
    key=VOLUME_KEY,
    # The player runs in the browser. The server stores this and has no use for it: there is
    # no volume on a server, and nothing here would know what to do with one.
    read_by=ReadBy.CLIENT,
    scope="user",
    default=100,
    section="Playback",
    label="Starting volume",
    help="How loud a video starts. Changing the volume while you watch saves it here.",
    minimum=0,
    maximum=100,
    unit="%",
)

# The downscale ceiling, and it is a setting rather than a constant on purpose.
#
# It applies to exactly one case: a file this browser cannot decode, on a machine that cannot
# convert it at full size fast enough to play smoothly. A 4K file the browser *can* play is never
# touched by this and arrives at full 4K. But for somebody with a 4K display, capable hardware and
# a library of 4K HEVC, a hardcoded 1080p would be a real quality loss they had no way to refuse,
# which is why this is raisable, and why Stash exposes the same control.
# Muted, remembered beside the volume and separately from it.
#
# Separate because they answer different questions. Volume is how loud, and muting is a switch
# thrown across it: somebody who mutes, closes the tab and comes back wants the sound still off
# AND their level still where they left it. Folding mute into "volume is zero" would remember the
# first and destroy the second, so unmuting would come back at whatever the default is rather than
# at the level they had chosen.
#
# Stored, because a mute held only in a variable on the player would leave every new video element
# unmuted, and somebody watching with the sound off would have to press it again on every single
# clip: the same complaint the volume setting exists to answer.
register_setting(
    key=MUTED_KEY,
    # The player runs in the browser. The server stores this and has no use for it: there is
    # no volume on a server, and nothing here would know what to do with one.
    read_by=ReadBy.CLIENT,
    scope="user",
    default=False,
    section="Playback",
    label="Start muted",
    help="Videos start with the sound off. Muting or unmuting while you watch saves it here.",
)

# Whether to remember a place at all.
#
# Its own switch rather than a minimum set impossibly high, because they are different answers.
# A large minimum still keeps a position for a long video; off keeps none, ever, for anything,
# which is what somebody sharing a screen, or simply not wanting a machine to know what they were
# partway through, is asking for. Off also stops anything being stored, not merely offered.
register_setting(
    key=RESUME_ENABLED_KEY,
    scope="user",
    default=True,
    section="Playback",
    label="Remember where you left off",
    help="Reopening a video resumes where you stopped instead of starting over.",
)

# Only videos worth going back into. A clip you watched half of is one you can simply watch again;
# a video is not, and being dropped at the beginning of one is the annoyance this answers.
#
# Per user rather than per install, like the rest of Playback: it is a preference about how
# somebody watches, and two people sharing a machine can reasonably disagree about it.
#
# Seconds, because the number people reach for here is small. Zero means every video however short,
# which is a real answer for a library of long clips, so the floor is 0 rather than a minimum
# nobody can go under.
register_setting(
    key=RESUME_MINIMUM_KEY,
    scope="user",
    default=60,
    section="Playback",
    label="Only remember videos longer than",
    help=("Shorter videos always start at the beginning, and so does one you watched to the end."),
    # "Every video" is zero, and it is the reason this is not simply the list of durations: without
    # it a clip shorter than the smallest one can never resume at all.
    choices=(0, 30, 60, 180, 300, 600, 1800),
    choice_labels=(
        "Every video",
        "30 seconds",
        "1 minute",
        "3 minutes",
        "5 minutes",
        "10 minutes",
        "30 minutes",
    ),
)

# Whether a run that is playing through stops on photographs as well as on videos.
#
# On, so a run through a folder of pictures and clips shows every one of them: a picture is held
# for two seconds (`PICTURE_SECONDS` in the player's `dwell.svelte.ts`) and the run carries on. Off,
# a run steps over photographs and plays only what has a length of its own.
#
# A GIF needs no setting: it has a length of its own, so a run plays it through once and moves on
# whether this is on or off. A photograph is the only kind with no natural end.
register_setting(
    key=DWELL_PICTURES_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=True,
    section="Playback",
    label="Include photos on a playthrough",
    help="Play through shows each photo for two seconds, then moves on. A GIF plays once.",
)

# What a screenshot does once it is taken, per person: the player's Screenshot press in the drawer,
# on a video and on a picture, reads it in the browser.
#
# Copy is the default because a screenshot is almost always taken to be pasted somewhere, and a
# file in a folder is a second trip to fetch it. Save puts it in the library, in the default
# downloads folder unless a folder is named below, so a screenshot kept is a file Sift can find
# like any other and never a stray in the machine's own Downloads. Where a browser refuses the
# clipboard (a plain http address) the picture is downloaded instead, so the press always leaves
# the person holding it.
SCREENSHOT_KEY = "playback.screenshot"
SCREENSHOT_COPY = "copy"
SCREENSHOT_SAVE = "save"

register_setting(
    key=SCREENSHOT_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=SCREENSHOT_COPY,
    section="Playback",
    label="When you take a screenshot",
    help=(
        "Copy puts it on the clipboard, ready to paste. Save adds it to your library, in the "
        "folder you choose."
    ),
    disclosure=(
        "Over a plain http address the browser has no clipboard for pictures, so there the "
        "screenshot is downloaded instead."
    ),
    choices=(SCREENSHOT_COPY, SCREENSHOT_SAVE),
    choice_labels=("Copy to the clipboard", "Save to a folder"),
)

# Which folder a saved screenshot lands in: a library folder's id, or empty for the default
# downloads folder (Settings > Downloads). Chosen from the list of folders, never typed; the pane
# draws the chooser. An id rather than a path, so a folder that is moved or renamed is still the
# folder chosen.
SCREENSHOT_FOLDER_KEY = "playback.screenshot_folder"

register_setting(
    key=SCREENSHOT_FOLDER_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default="",
    section="Playback",
    label="Save screenshots to",
    help="A folder in your library. Unless you choose one, screenshots go to your download folder.",
)

register_setting(
    key=MAX_TRANSCODE_HEIGHT_KEY,
    scope="app",
    default=1080,
    section="Playback",
    label="Largest size for converted videos",
    help=(
        "Used only when your browser can't play a video and this device can't convert it fast "
        "enough at full size."
    ),
    choices=(360, 480, 720, 1080, 1440, 2160),
    choice_labels=("360p", "480p", "720p", "1080p", "1440p", "4K"),
)

# What the converted copies may take up before the oldest are thrown away.
#
# Whole gigabytes, because that is the unit somebody thinks about a disk in, and the smallest
# useful answer is one: below that a single segment of a long film would not fit and the cache
# would evict everything it built, every time.
#
# Lowering it takes effect at once rather than at the next thing played (see `SegmentCache.resize`),
# because the moment somebody asks Sift to use less disk is the moment they are looking at the
# folder.
register_setting(
    key=CACHE_MAX_GB_KEY,
    scope="app",
    default=5,
    minimum=1,
    maximum=512,
    unit="GB",
    section="Playback",
    label="Space for converted copies",
    help=(
        "Sift converts a video only when your browser can't play the original, and keeps the "
        "converted copy so it doesn't convert it twice."
    ),
)

__all__ = [
    "CACHE_MAX_GB_KEY",
    "DWELL_PICTURES_KEY",
    "LOOP_ALL",
    "LOOP_MODE_KEY",
    "LOOP_ONE",
    "MAX_TRANSCODE_HEIGHT_KEY",
    "MUTED_KEY",
    "PLAYED",
    "PLAY_ONCE",
    "RESUME_ENABLED_KEY",
    "RESUME_MINIMUM_KEY",
    "SCREENSHOT_COPY",
    "SCREENSHOT_FOLDER_KEY",
    "SCREENSHOT_KEY",
    "SCREENSHOT_SAVE",
    "SEGMENT_CACHE",
    "SERVICE",
    "VOLUME_KEY",
    "ClientCapabilities",
    "Plan",
    "PlayerService",
    "Route",
    "SegmentCache",
    "decide",
    "job_limits",
    "register_handlers",
    "router",
    "schema",
]
