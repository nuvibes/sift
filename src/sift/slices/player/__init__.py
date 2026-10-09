# SPDX-License-Identifier: AGPL-3.0-or-later
"""Playback: direct play, remux, and on-the-fly segment transcoding, as rarely as possible."""

from __future__ import annotations

# The resume preferences are keyed in the kernel, which the grid and query language read too.
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

# Play through by default: this browses short clips, and the queue is right there.
register_setting(
    key=LOOP_MODE_KEY,
    # The player runs in the browser; the server only stores this.
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

# Volume, per person so it follows them between devices; whole percent so equality holds.
register_setting(
    key=VOLUME_KEY,
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

# Muted, separate from the volume so unmuting returns to the chosen level.
register_setting(
    key=MUTED_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=False,
    section="Playback",
    label="Start muted",
    help="Videos start with the sound off. Muting or unmuting while you watch saves it here.",
)

# Whether to remember a place at all: off stores nothing, which a large minimum would not.
register_setting(
    key=RESUME_ENABLED_KEY,
    scope="user",
    default=True,
    section="Playback",
    label="Remember where you left off",
    help="Reopening a video resumes where you stopped instead of starting over.",
)

# Only videos worth going back into, in seconds; zero means every video.
register_setting(
    key=RESUME_MINIMUM_KEY,
    scope="user",
    default=60,
    section="Playback",
    label="Only remember videos longer than",
    help=("Shorter videos always start at the beginning, and so does one you watched to the end."),
    # Zero lets a clip shorter than the smallest choice resume at all.
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

# Whether a run playing through holds on photographs too; a GIF has its own length.
register_setting(
    key=DWELL_PICTURES_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=True,
    section="Playback",
    label="Include photos on a playthrough",
    help="Play through shows each photo for two seconds, then moves on. A GIF plays once.",
)

# What a screenshot does once taken, per person: copy by default, since it is usually pasted.
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

# A library folder's id for saved screenshots, or empty for the default downloads folder.
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

# What the converted copies may take up, in whole gigabytes; lowering it evicts immediately.
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
