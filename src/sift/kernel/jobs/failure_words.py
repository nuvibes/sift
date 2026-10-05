# SPDX-License-Identifier: AGPL-3.0-or-later
"""Why a job failed, in words a person can act on, keyed by the kind of failure.

A job's stored error is the tool's own text ("FFmpegError: ffmpeg.exe failed: [webp @ 0x...] image
data not found"), which is what somebody chasing the fault needs and nobody else can read. So a
screen that says why a run failed in passing (a hover on Activity, a task's row) says the words
here, and the Failed list keeps the stored text on the job's own row.

The kinds are the failures a person can do something about or should know the shape of: a file
that could not be read, a file that was not there, a write that was refused, a full disk, and
Sift's own interruptions. Everything else says that the tool failed and where its words are,
rather than guessing: a sentence that names the wrong cause is worse than one that names none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FailureKind:
    """One kind of failure: a stable name, what recognises it, and what a person reads."""

    #: Short and stable, for a test and a search; never shown.
    name: str
    #: Matched against the error text, case folded.
    pattern: re.Pattern[str]
    #: The whole of what a person reads, one or two sentences.
    words: str


def _kind(name: str, pattern: str, words: str) -> FailureKind:
    return FailureKind(name=name, pattern=re.compile(pattern, re.IGNORECASE), words=words)


#: In the order they are tried: the first that matches names the failure. Sift's own sentences
#: first, then a missing file before an unreadable one, because a decoder handed a path that is
#: not there reports it as a failure to read.
KINDS: tuple[FailureKind, ...] = (
    _kind(
        "restarted",
        r"sift was restarted while",
        "Sift was restarted while it was running, so it stopped partway.",
    ),
    _kind(
        "retired",
        r"no longer exists in this version",
        "This kind of work no longer exists in this version of Sift.",
    ),
    _kind(
        "stopped-answering",
        r"stopped answering partway through",
        "A folder stopped answering partway through the scan. Scan it again once it's back.",
    ),
    _kind(
        "library-unreachable",
        r"the library folder did\snot answer",
        "The library folder didn't answer, so nothing was changed. Scan it again once it's back.",
    ),
    _kind(
        "disk-full",
        r"no space left on device|not enough space on the disk|\[errno 28\]|\[winerror 112\]",
        "The disk ran out of room. Free some space, then run it again.",
    ),
    _kind(
        "missing",
        r"filenotfounderror|no such file|cannot find the (?:file|path)|noreadablecopy"
        r"|\[winerror [23]\]|\[errno 2\]",
        "A file it needed wasn't there. It may have been moved or deleted, or its drive isn't "
        "connected.",
    ),
    _kind(
        "refused-write",
        r"permissionerror|access is denied|permission denied|read-only file system"
        r"|\[winerror 5\]|\[errno 13\]",
        "Sift wasn't allowed to write a file. The folder may be read-only, or another program "
        "may be holding the file.",
    ),
    _kind(
        "never-started",
        r"it couldn't start: a file it needs",
        "A tool Sift runs couldn't start, because a file it needs was in use or missing. Run it "
        "again; if it keeps happening, another program may be holding Sift's files.",
    ),
    # A download's failures, in the words `kernel.fetch` and the model store write.
    _kind(
        "server-refused",
        r"couldn't be downloaded: \S+ answered \d+",
        "The download was refused at its source. Try again later.",
    ),
    _kind(
        "connection-refused",
        r"the connection to \S+ was refused",
        "The connection was refused. A firewall, a proxy or the network may be blocking it.",
    ),
    _kind(
        "name-not-found",
        r"couldn't be downloaded: \S+ couldn't be found",
        "The download's address couldn't be found. Check the internet connection, and whether the "
        "network blocks it.",
    ),
    _kind(
        "no-answer",
        r"couldn't be downloaded: \S+ didn't answer in time",
        "The download didn't answer in time. Check the internet connection, then run it again.",
    ),
    _kind(
        "untrusted",
        r"a secure connection to \S+ couldn't be made",
        "A secure connection couldn't be made. Check the computer's date and time, and whether "
        "security software inspects secure connections.",
    ),
    _kind(
        "proxy",
        r"the proxy didn't let the connection through",
        "The proxy didn't let the connection through. Check the system's proxy settings.",
    ),
    _kind(
        "unreached",
        r"sift couldn't connect to",
        "Sift couldn't connect for the download. Check the internet connection, and whether a "
        "firewall blocks it.",
    ),
    _kind(
        "dropped",
        r"the connection to \S+ dropped",
        "The connection dropped partway. Check the internet connection, then run it again.",
    ),
    _kind(
        "arrived-damaged",
        r"didn't arrive intact",
        "The download didn't arrive intact, so it was removed. Run it again to download it afresh.",
    ),
    _kind(
        "unreadable",
        r"could not be decoded|image data not found|invalid data found when processing input"
        r"|invalid nal unit|moov atom not found|error while decoding|invalid sbit",
        "A file couldn't be read. It may be damaged or only partly downloaded.",
    ),
)

#: What every other failure says: that the tool stopped, and where its own words are.
OTHERWISE = "The tool it ran stopped with an error of its own, which the Failed list shows."


def kind_of(error: str) -> FailureKind | None:
    """The kind this stored error is, or None for one no kind recognises."""
    return next((one for one in KINDS if one.pattern.search(error)), None)


def in_plain_words(error: str) -> str:
    """What a person reads about this stored error, in place of the error itself."""
    found = kind_of(error)
    return OTHERWISE if found is None else found.words


#: WHY A PRODUCT GAVE UP ON ONE FILE, by the code its standing verdict carries (`file_verdicts`).
#:
#: A verdict's stored reason is written by whoever gave up, and it is not always words a person
#: can read: the picture pass stores the decoder's own refusal ("this file could not be decoded
#: (... Invalid NAL unit size ...)"). The CODE is the stable half, so a file's line on a wall is
#: said from it, one sentence, short enough to stand under a tile.
#:
#: THE GATE'S REASONS SAY WHAT THE REFUSAL LIST SAYS. The codes from `ingress.Reason` are the same
#: decisions the Refused files list in Settings explains (`refused.svelte.ts`), made by the same
#: gate, so the two say them in the same words: a reader meeting two sentences for one refusal
#: would reasonably think they were two problems. `tests/gates/test_one_name_per_filter.py` holds
#: the two copies to each other.
#:
#: The rest are the codes the passes that give up write themselves: the picture pass's `no_frame`,
#: the face and meaning passes' `no_frame_decoded` and `no_copy`, the watermark read's
#: `no_picture`. `test_failure_words.py` holds every code a pass writes to a line here.
VERDICT_WORDS: dict[str, str] = {
    "empty": "An empty file.",
    "unreadable": "Sift couldn't read it.",
    "signature_not_allowed": "Its bytes aren't a picture or a video.",
    "extension_contradicts_signature": "The name and the contents disagree.",
    "does_not_end_where_it_should": "It stops early, so it may be a part-finished download.",
    "not_decodable": "It wouldn't open.",
    "animated_webp_unreadable": "The GIF wouldn't read.",
    "no_video_stream": "There's no video in it.",
    "pixels_exceeded": "The picture is too large to open safely.",
    # Not on the refusal list: a stalled share is never a refusal of the file, so the scan does not
    # remember one (see `ingress.TRANSIENT_REASONS`). Said here because a pass can still record it.
    "timed_out": "Its drive didn't answer in time.",
    "no_frame": (
        "It has no frame that can be decoded. It may be damaged or only partly downloaded."
    ),
    "no_frame_decoded": (
        "No moment of it could be decoded. It may be damaged or only partly downloaded."
    ),
    "no_copy": "No copy of this file could be read.",
    "no_picture": "It has no picture Sift can read.",
}


def why_left_out(code: str, reason: str) -> str:
    """What a person reads about one file a product gave up on, in place of the stored reason.

    The code's own sentence where there is one. A code nobody has worded yet falls back to the
    stored reason's KIND (`in_plain_words`), which never carries the tool's text, rather than to
    the reason itself, which may.
    """
    return VERDICT_WORDS.get(code) or in_plain_words(reason)
