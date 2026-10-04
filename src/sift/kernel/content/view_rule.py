# SPDX-License-Identifier: AGPL-3.0-or-later
"""What counts as a view: how long a sitting with a file has to last before it is one.

In the kernel because the player (which tells the browser when a sitting will earn a view) and
Insights (which counts them) both read it and neither may import the other: one rule, never two
copies. The player re-exports these under its own `policy` names.
"""

from __future__ import annotations

# A view is a quarter of a video, capped at thirty seconds: one rule fits a six-second loop and a
# film, which no fixed number can. Sift answers one person's "have I seen this" about their own
# library, so a glance the arrow keys passed over is not a view, or the Unwatched filter would
# call a library of glances watched.
_VIEW_FRACTION = 4
_VIEW_CAP_MS = 30_000

# The floor under the fraction, and the answer for a video of unknown length: it stops a short
# clip being viewed a quarter of a second at a time. A picture is viewed by being opened instead.
_STILL_DWELL_MS = 2_000


def watch_needed(media_type: str, duration_ms: int | None) -> int:
    """How long a sitting with this file has to last before it is a view.

    `counts_as_a_view` asks whether an ENDED sitting earned one; a player asks when the moment will
    ARRIVE, to say so as it happens. The browser is handed this one file's number, computed here,
    so it never holds a second copy of the rule and cannot disagree with the server.
    """
    if media_type != "video":
        # A picture is viewed by being opened: it qualifies from its first instant.
        return 0
    if not duration_ms or duration_ms <= 0:
        return _STILL_DWELL_MS
    # The fraction is the rule; the cap stops a film asking for minutes; the floor stops a short
    # loop being viewed in an instant; the file's own length is the last word, so a threshold
    # longer than a GIF is never set that only looping could meet.
    return min(_VIEW_CAP_MS, max(_STILL_DWELL_MS, duration_ms // _VIEW_FRACTION), duration_ms)


def counts_as_a_view(media_type: str, duration_ms: int | None, watch_ms: int) -> bool:
    """Whether a sitting on this kind of file, this long, was a view.

    Decided on the server because three screens report sittings (the player, the picture viewer,
    a Theater wall) and a rule in a browser would be three rules. A picture or a GIF has no
    timeline, so opening it is looking at it; a video is judged on time watched, never position
    reached, since dragging the scrubber to the end watches nothing.
    """
    if watch_ms < 0:
        return False
    return watch_ms >= watch_needed(media_type, duration_ms)
