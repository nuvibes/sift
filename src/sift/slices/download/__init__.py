# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetching media from the internet: the tools, the ledger, the site logins, the queue.

This is the downloader half of what Sift is. Paste or drop a link and the media lands in the
library, deduplicated against what is already there, attributed to the site and username it came
from, with no configuration asked for. The tools that do the fetching are run as separate processes
and never imported, which is what keeps their licence off Sift's own code and their crashes out of
its process.

The whole slice is admin-only and permanently so. Downloading reaches out from the server, holds
saved site logins, and shows a queue of what is being fetched; none of that is a guest's, and no
setting opens it to one.
"""

from __future__ import annotations

from sift.kernel.settings_registry import ReadBy, register_setting, remove_setting
from sift.slices.download import art, schema
from sift.slices.download.art import SITE_ART, ArtStore
from sift.slices.download.jobs import register_handlers
from sift.slices.download.router import router
from sift.slices.download.secrets import AdminMasterKey, SecretStore
from sift.slices.download.service import (
    DOWNLOAD,
    PAUSED_KEY,
    PEOPLE_FROM_USERNAMES_KEY,
    PHOTO_SETS_REMOVED_KEY,
    REMEMBER_KEY,
    SERVICE,
    DownloadService,
)
from sift.slices.download.site_options import SITE_OPTIONS, SiteOptionStore
from sift.slices.download.sources import Downloader, policy, progress
from sift.slices.download.sources.progress import PROGRESS
from sift.slices.download.sources.sites.catalog import site_key_of
from sift.slices.download.sources.tuning import QUALITY_BEST, QUALITY_COMPATIBLE

# Which method fetches a Site (a middleman service or a tool) is one of the three answers each
# Site is given beside its folder and its naming, asked per Site. The older instance-wide
# `downloads.service_method` value is carried over by schema v13. See `site_options` and
# `sources/registry`.

# A downloaded gallery lands as files in its folder and nothing groups it. The switch that once
# grouped one is declared removed so History lines about it still name it; its stored value is
# dropped by the settings schema.
remove_setting(
    PHOTO_SETS_REMOVED_KEY,
    label="Group downloaded galleries into Photo Sets",
    why="A gallery is files in a folder; a Photo Set is your own grouping.",
)

# Whether a download files itself under the person who posted it, inventing them when they are new.
# Instance-wide and admin-only: it changes what the library collects, not one person's view of it.
# Every Site whose catalog record says a username there is a person follows it (PMV Haven among
# them, and not only PMV Haven), so the words name downloads rather than one Site.
register_setting(
    key=PEOPLE_FROM_USERNAMES_KEY,
    scope="app",
    default=True,
    section="Downloads",
    label="Add creators you download to People",
    disclosure=(
        "Only on Sites where a username belongs to a person, such as PMV Haven, TikTok, Instagram "
        "and YouTube. A subreddit never becomes a person."
    ),
    help=(
        "A download from a creator's page, on PMV Haven or another Site, is added to the person "
        "who posted it."
    ),
)

# Whether the ledger is allowed to stop a re-paste. Instance-wide and admin-only, like everything
# else about downloading, and read live so turning it off applies to the next link rather than the
# next restart.
register_setting(
    key=REMEMBER_KEY,
    scope="app",
    default=True,
    section="Downloads",
    label="Skip links you have already downloaded",
    help=("Sift skips a link you have already downloaded. Your history is kept either way."),
)

# --- how much of the machine downloading may take ------------------------------------------------
#
# Deliberately NOT part of the processor budget the background passes share, and that is the whole
# reason these are here rather than in Performance. A download spends its life waiting on somebody
# else's server; it uses almost no processor and it does not compete with thumbnailing or
# recognition for one. Capping it by share-of-processor would slow the one thing in Sift that is not
# processor-bound and free nothing worth having.
#
# What it does compete for is the network and the disk, so those are what these two bound.

#: How many fetches run at the same time. Left at 0 there is no separate cap: downloads share the
#: worker count with everything else.
AT_ONCE_KEY = "download.at_once"

#: The free space a fetch is stopped at, in gigabytes. Not a size limit on a download: a long
#: video is large and legitimate. It is headroom for the database, which shares the disk and
#: corrupts a write when the filesystem has no room left. Five, because one 4K file can exceed half
#: a gigabyte by itself, so a smaller floor could pass at the start of a fetch and be gone before
#: it ended.
DISK_FLOOR_GB_KEY = "download.min_free_gb"

register_setting(
    key=AT_ONCE_KEY,
    scope="app",
    default=0,
    section="Downloads",
    label="Downloads at the same time",
    automatic_label="Default",
    help=("How many downloads run at the same time."),
    minimum=0,
    maximum=32,
)

register_setting(
    key=DISK_FLOOR_GB_KEY,
    scope="app",
    default=5,
    section="Downloads",
    label="Minimum free space",
    unit="GB",
    help=("A download that would take free space below this fails, so the disk never fills up."),
    minimum=1,
    maximum=1000,
)

# --- the noise a finished download makes, for whoever is looking at it -------------------------
#
# Per USER rather than instance-wide, and that is the whole point: the sound happens in one
# person's browser and belongs to that person. A downloader that chirps unasked on a machine
# somebody else is sitting at is a bug, so it is off until somebody turns it on.
#
# Read by the browser, which is where the noise is made. Nothing on the server acts on either.

#: Whether anything makes a noise at all.
SOUND_KEY = "download.sound"

#: How loud, as a percentage. A percentage because the screen draws one as a slider, which is the
#: right control for a volume and the wrong one for a number in a box.
SOUND_VOLUME_KEY = "download.sound_volume"

register_setting(
    key=SOUND_KEY,
    scope="user",
    default=False,
    section="Downloads",
    label="Play a sound when finished",
    help=("Plays a short tone when a download finishes, fails or needs you to sign in."),
    read_by=ReadBy.CLIENT,
)

register_setting(
    key=SOUND_VOLUME_KEY,
    scope="user",
    default=40,
    section="Downloads",
    label="Sound volume",
    unit="%",
    help="How loud those tones are.",
    minimum=0,
    maximum=100,
    read_by=ReadBy.CLIENT,
)

#: Whether a finished download is said in a message, and how: never, one per file, or one for a
#: batch. Per account for the reason the sound is: the message is drawn in one person's browser.
#: Off by default: a queue of forty links finishing one by one is forty messages nobody asked
#: for. Read by the browser; nothing on the server acts on it.
FINISHED_MESSAGE_KEY = "download.finished_message"

#: The three answers, as stored. `together` waits until nothing is left downloading.
FINISHED_MESSAGE_CHOICES = ("off", "each", "together")

register_setting(
    key=FINISHED_MESSAGE_KEY,
    scope="user",
    default="off",
    section="Downloads",
    label="Say when a download finishes",
    help="Shows a message on any screen when downloads finish.",
    disclosure=(
        "Each download names every file as it finishes, with Open once it's in your library. "
        "Once for many waits until nothing is left downloading, then shows one message: the "
        "file's name when only one finished, or how many did. The sound is a separate setting."
    ),
    choices=list(FINISHED_MESSAGE_CHOICES),
    choice_labels=("Off", "Each download", "Once for many"),
    read_by=ReadBy.CLIENT,
)

# --- how hard the downloaders lean on a site, and what they may fetch ---------------------------
#
# One control, both tools, and every resolver that honours the same value. The point is that an
# admin sets "network timeout", not "--socket-timeout and also --http-timeout": anything that cannot
# be expressed as one Sift setting meaning one thing everywhere is a sign it does not belong on a
# screen at all.
#
# Passed on EVERY run, even where the number matches what a tool would have chosen anyway. An unset
# default is still behaviour: it is simply behaviour nobody chose, and it moves the next time the
# dependency is updated.

#: Whether new downloads are allowed to start at all. What a Pause control on the queue sets.
#:
#: A cap of zero rather than a flag anything checks: the worker pool already understands a job type
#: it may run none of, so pausing is the same mechanism as the limit beside it and there is no
#: second answer to "may this start". Anything already running finishes: a transfer cannot be
#: suspended halfway and resumed, so stopping one means losing it, and that is Cancel's job. The key
#: itself is the service's (`PAUSED_KEY`), because the rail's read asks it too.

register_setting(
    key=PAUSED_KEY,
    scope="app",
    default=False,
    section="Downloads",
    label="Pause downloads",
    disclosure="Downloads already running finish. A transfer can't be paused halfway and resumed later.",
    help=("Nothing new starts while this is on."),
)

register_setting(
    key=policy.PACE_MS_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.PACE_MS_KEY],
    section="Downloads",
    label="Wait between requests",
    unit="ms",
    # 0 is honoured as no pause at all (`policy._count`), so the help says so rather than leaving a
    # 0 to be read as "the default".
    help=("A pause between one request and the next, on every Site. 0 sends them without a pause."),
    minimum=0,
    maximum=policy.PACE_MS_MAX,
)

register_setting(
    key=policy.RETRIES_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.RETRIES_KEY],
    section="Downloads",
    label="Retries per download",
    help=(
        "How many times Sift retries a failed download, on every Site. 0 means it doesn't retry."
    ),
    minimum=0,
    maximum=10,
)

register_setting(
    key=policy.TIMEOUT_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.TIMEOUT_KEY],
    section="Downloads",
    label="Stopped responding for",
    unit="sec",
    disclosure=(
        "Raise it on a slow connection. Lower it if stuck downloads are holding up the others. A "
        "Site that never answers costs one wait rather than one for every retry, and a download "
        "that stalls partway through is tried again."
    ),
    help=(
        "How long Sift waits on a download that has stopped sending anything before it gives up "
        "on that try. Applies to every Site."
    ),
    minimum=5,
    maximum=600,
)

register_setting(
    key=policy.BACKOFF_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.BACKOFF_KEY],
    section="Downloads",
    label="Wait after a rate limit",
    unit="sec",
    help=(
        "How long Sift waits before downloading from a Site again when it says it has too many "
        "requests. Applies to every Site."
    ),
    minimum=5,
    maximum=3600,
)

register_setting(
    key=policy.BANDWIDTH_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.BANDWIDTH_KEY],
    section="Downloads",
    label="Download speed limit",
    automatic_label="No limit",
    unit="KB/s",
    help=("Limits how fast each download can go, on every Site. Leave it empty for no limit."),
    minimum=0,
    maximum=1_000_000,
)

register_setting(
    key=policy.SKIP_SMALLER_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.SKIP_SMALLER_KEY],
    section="Downloads",
    label="Skip files smaller than",
    automatic_label="No minimum",
    unit="MB",
    help=(
        "Skips files below this size on every Site, such as a thumbnail sent instead of the real "
        "file. Leave it empty to skip nothing."
    ),
    minimum=0,
    maximum=100_000,
)

register_setting(
    key=policy.SKIP_LARGER_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.SKIP_LARGER_KEY],
    section="Downloads",
    label="Skip files larger than",
    automatic_label="No maximum",
    unit="MB",
    disclosure=(
        "Leave it empty to skip nothing. Sift goes by the size a Site states in advance, so use "
        "Minimum free space to protect your disk."
    ),
    help=("Skips files above this size, on every Site."),
    minimum=0,
    maximum=1_000_000,
)

register_setting(
    key=policy.QUALITY_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.QUALITY_KEY],
    section="Downloads",
    label="Video quality",
    # The disclosure says what each answer picks, from the sort it becomes
    # (`sources/argv.py::_SORT_BY_QUALITY`): a preference and never a filter, so a Site that does
    # not offer the preferred kind still downloads, as itself. The Sites Sift reads itself pick a
    # rung the same way (`sites.common.pick_rung`): the tallest at or under 1080p, or the tallest.
    # Nothing converts a download after it lands.
    disclosure=(
        "Best compatible picks H.264 video in an MP4 file, which plays in every browser and on "
        "every device, and on YouTube it stops at 1080p. Best available picks the highest "
        "resolution and frame rate on offer, often in the newer AV1 or VP9 formats that some "
        "older devices can't play. When a Site doesn't offer the version you prefer, the best "
        "one it does offer is taken, and some Sites offer only one. Nothing converts a download, so "
        "the file is kept exactly as the Site sent it."
    ),
    help=(
        "Which version to download when a Site offers several. Best compatible plays everywhere "
        "but stops at 1080p on YouTube; best available gets 4K."
    ),
    choices=[QUALITY_COMPATIBLE, QUALITY_BEST],
    # Short enough for the settings row's control column: the help and the disclosure say what
    # each one costs, so an answer that repeated it would be cut short with an ellipsis.
    choice_labels=(
        "Best compatible (1080p)",
        "Best available (4K)",
    ),
)

# Filed under the log, not under Downloads. It changes what the log records and nothing about what
# a download does, and it is drawn beside the log it fills: somebody reading the log for a failed
# download is the person who wants more of it. The section is also where a settings-search result
# for it lands, so filing it here keeps the result pointing at the screen that draws it.
register_setting(
    key=policy.VERBOSE_KEY,
    scope="app",
    default=policy.DEFAULTS[policy.VERBOSE_KEY],
    section="Logs",
    label="Detailed download log",
    help=("Records every step of each download in the log."),
)


def at_once_limit(at_once: object, paused: object) -> int | None:
    """How many downloads may run at the same time, or None to leave them sharing the workers.

    A decision this slice owns rather than one made where the pool is assembled: the composition
    root should be wiring, and "what does paused mean" is a fact about downloading, tested here.

    Paused wins over any cap. A type allowed none of itself is what the pool already understands as
    paused, so this is the same mechanism rather than a second answer to whether a download may
    start, and anything already running still finishes, because a transfer cannot be suspended
    halfway and picked up later.
    """
    if paused is True:
        return 0
    try:
        chosen = int(str(at_once))
    except (TypeError, ValueError):
        return None
    return chosen if chosen > 0 else None


__all__ = [
    "AT_ONCE_KEY",
    "DISK_FLOOR_GB_KEY",
    "DOWNLOAD",
    "FINISHED_MESSAGE_CHOICES",
    "FINISHED_MESSAGE_KEY",
    "PAUSED_KEY",
    "PEOPLE_FROM_USERNAMES_KEY",
    "PHOTO_SETS_REMOVED_KEY",
    "PROGRESS",
    "REMEMBER_KEY",
    "SERVICE",
    "SITE_ART",
    "SITE_OPTIONS",
    "SOUND_KEY",
    "SOUND_VOLUME_KEY",
    "AdminMasterKey",
    "ArtStore",
    "DownloadService",
    "Downloader",
    "SecretStore",
    "SiteOptionStore",
    "art",
    "at_once_limit",
    "policy",
    "progress",
    "register_handlers",
    "router",
    "schema",
    "site_key_of",
]
