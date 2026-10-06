# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watch state, the one-at-a-time cap, and what happens when a transcode goes wrong."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.content.user_state import HEAT_BUCKETS
from sift.kernel.hardware import HardwareReport
from sift.kernel.http import CSRF_HEADER_NAME
from sift.kernel.media import Accelerator, Encoder, choose_encoder
from sift.kernel.wiring import HARDWARE, part_of_app
from sift.slices.player import policy, tuning
from sift.slices.player.jobs import job_limits
from sift.slices.player.service import (
    TRANSCODE,
    PlayerService,
    byte_range,
    capped,
    master_playlist,
    plan_query,
    source_bitrate,
)
from sift.slices.player.tests.conftest import (
    MODERN,
    Library,
    asset,
    db_path,
    read,
    share,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]


def _state(client: TestClient, asset_id: str, user_id: str) -> tuple[int, int]:
    """Read one person's view count and watched time straight out of the table."""
    rows = read(
        db_path(client),
        "SELECT view_count, watched_ms FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (asset_id, user_id),
    )
    return (0, 0) if not rows else (rows[0]["view_count"], rows[0]["watched_ms"])


def _column(client: TestClient, asset_id: str, user_id: str, name: str) -> int | None:
    """One column of one person's state, or None where there is no row at all.

    A missing row and a NULL column mean the same thing everywhere this is used (nothing has
    happened yet), so they are deliberately not told apart.
    """
    rows = read(
        db_path(client),
        f"SELECT {name} FROM asset_user_state WHERE asset_id = ? AND user_id = ?",  # noqa: S608
        (asset_id, user_id),
    )
    return None if not rows else rows[0][name]


def _seen_at(client: TestClient, asset_id: str, user_id: str) -> int | None:
    return _column(client, asset_id, user_id, "last_viewed_at")


def _finished_at(client: TestClient, asset_id: str, user_id: str) -> int | None:
    return _column(client, asset_id, user_id, "completed_at")


# --- views ------------------------------------------------------------------------------------------


def test_watching_something_counts_a_view(client: TestClient, library: Library) -> None:
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    response = client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 4_000})

    assert response.status_code == 204
    assert _state(client, asset_id, user_id) == (1, 4_000)


def test_recording_a_view_without_the_csrf_token_is_refused(
    client: TestClient, library: Library
) -> None:
    """A view is a state change, so it carries the session's CSRF token like every other unsafe
    method: the SameSite cookie is not the only thing kept between a cross-site page and it."""
    sign_in(client)
    del client.headers[CSRF_HEADER_NAME]

    response = client.post(f"/api/assets/{library.id_of('h264')}/view", json={"watch_ms": 1_000})

    assert response.status_code == 403


def test_time_watched_accumulates_across_views(client: TestClient, library: Library) -> None:
    """Somebody who watches the same clip three times has watched it for three times as long.

    Three times, with no dedup window between them. Watching something twice in a minute is watching
    it twice and reads as two: the threshold is what separates a viewing from a glance, and a clock
    is not asked to help.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    for _ in range(3):
        client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 4_000})

    assert _state(client, asset_id, user_id) == (3, 12_000)


def test_the_plan_says_when_a_sitting_will_have_earned_its_view(
    client: TestClient, library: Library
) -> None:
    """The threshold travels with the plan, and it is the file's own.

    The player cannot say "counted" at the right moment without knowing when that moment is, and it
    must not work the number out for itself: a browser with its own copy of the rule is a second
    rule. So the server sends the answer for this one file, from the same function that judges the
    sitting when it is reported.

    A quarter of a ten-second clip. Pinned as a NUMBER rather than as "not null", because a client
    handed a zero would report a view on the first frame of every video in the library.
    """
    sign_in(client)

    assert _plan(client, library.id_of("h264"))["view_at_ms"] == 2_500


def test_a_sitting_reported_in_pieces_is_one_view(client: TestClient, library: Library) -> None:
    """A sitting can arrive in two reports and is still one viewing.

    The player says so the moment the sitting has earned its view rather than waiting to be closed,
    which is what makes a tally move when the watching happened. That means two reports for one
    sitting, and only the one that CROSSES the threshold may count. Otherwise the number moves
    when it should and then moves again for nothing.

    Both pieces carry their own time and both are kept: the split is about which report earns the
    view, never about which of them is real.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    # A quarter of a ten-second clip: the piece that crosses the line.
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 2_500})
    # The rest of the same sitting, on the way out.
    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 3_000, "already_reported_ms": 2_500},
    )

    assert _state(client, asset_id, user_id) == (1, 5_500)


def test_pieces_too_small_on_their_own_still_add_up_to_a_view(
    client: TestClient, library: Library
) -> None:
    """The threshold is asked of the SITTING, not of the piece that happens to be carrying it.

    Nothing stops a client reporting a sitting in more than two pieces, and once it does, every one
    of them can be under the bar while the sitting is plainly over it. Judging each piece alone
    would mean a clip watched in three one-second visits was never watched at all, and the fault
    would be invisible, because the time watched would be perfectly correct beside a count of zero.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    # Three quarters of the way to the 2.5s threshold, and then past it: neither piece alone.
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 1_800})
    client.post(
        f"/api/assets/{asset_id}/view", json={"watch_ms": 900, "already_reported_ms": 1_800}
    )

    assert _state(client, asset_id, user_id) == (1, 2_700)


def test_a_piece_worth_no_time_does_not_suppress_the_view(
    client: TestClient, library: Library
) -> None:
    """Zero already reported is not the same as already counted.

    A picture sends an empty first piece, because opening one IS looking at it, so for a picture,
    zero before means the view is already in. A video is the other way round: nothing watched is
    nothing earned, and the piece that follows must still be able to earn it.

    One question is asked of both (had this sitting qualified BEFORE this piece arrived?), and
    the two answers differ because the two kinds of file are judged differently. Asking it as
    "has anything been reported yet" would collapse them, and every video watched after an early
    empty report would go uncounted.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 0})
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 4_000, "already_reported_ms": 0})

    assert _state(client, asset_id, user_id) == (1, 4_000)


def test_a_sitting_too_short_to_be_a_view_still_keeps_its_time(
    client: TestClient, library: Library
) -> None:
    """The two halves of a report come apart, and this is the case that separates them.

    A second and a half of a ten-second clip is a glance (the threshold is a quarter of it), so no
    view is counted. The time is real and is kept: dropping it would leave `watched_ms` describing
    only the sittings that happened to clear a bar, which is a different number from the one it
    claims to be.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    for _ in range(3):
        client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 1_500})

    assert _state(client, asset_id, user_id) == (0, 4_500)


def test_a_glance_never_reaches_the_history(client: TestClient, library: Library) -> None:
    """And it must not, or Recently viewed fills with whatever the arrow keys passed over.

    `last_viewed_at` is what the history rail and the Viewed facet both read, so a sitting that did
    not earn a view must leave it alone, which is the whole reason the short report writes through
    a different statement rather than the same one with a flag.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 1_000})

    assert _seen_at(client, asset_id, user_id) is None


def test_reaching_the_end_marks_it_finished(client: TestClient, library: Library) -> None:
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 9_000, "position_ms": 9_600},
    )

    assert _finished_at(client, asset_id, user_id) is not None


def test_stopping_before_the_last_twentieth_is_not_finished(
    client: TestClient, library: Library
) -> None:
    """Ninety per cent of the way through a ten-second clip is nine seconds, and the bar is 95."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 9_000, "position_ms": 9_000},
    )

    assert _finished_at(client, asset_id, user_id) is None


def test_a_looping_clip_is_finished_by_the_element_saying_so(
    client: TestClient, library: Library
) -> None:
    """The case a position can never answer, and the reason `ended` is on the wire at all.

    A clip set to repeat reaches its end and is back at zero a frame later, so the position it
    reports says it was never finished, on exactly the file somebody has watched most.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 9_000, "position_ms": 120, "ended": True},
    )

    assert _finished_at(client, asset_id, user_id) is not None


def test_a_file_watched_straight_through_is_finished(client: TestClient, library: Library) -> None:
    """A file watched straight through in two reports is marked finished by the later one, which
    earns no second view."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    # The piece that crosses the line. Part-way through, so nothing about it says finished.
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 2_500, "position_ms": 2_500})
    assert _finished_at(client, asset_id, user_id) is None

    # And the piece that arrives when the file ends. It earns no second view, and it is the only
    # report that ever says the file was finished.
    client.post(
        f"/api/assets/{asset_id}/view",
        json={
            "watch_ms": 7_500,
            "already_reported_ms": 2_500,
            "position_ms": 9_900,
            "ended": True,
        },
    )

    assert _finished_at(client, asset_id, user_id) is not None, (
        "watched from beginning to end and not marked finished"
    )
    # Still ONE view: reaching the end is not a second viewing of the same sitting.
    assert _state(client, asset_id, user_id) == (1, 10_000)


def test_the_second_piece_marks_it_finished_by_position_too(
    client: TestClient, library: Library
) -> None:
    """Both ways of finishing, on the later report's branch. `ended` is the looping clip's
    answer; a position in the last twentieth is every other file's, and the branch has to know both
    or a player that never fires `ended` is a player whose files are never finished."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 2_500, "position_ms": 2_500})
    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 7_400, "already_reported_ms": 2_500, "position_ms": 9_900},
    )

    assert _finished_at(client, asset_id, user_id) is not None


def test_one_sitting_reported_in_two_pieces_is_one_row_in_plays(
    client: TestClient, library: Library
) -> None:
    """The sitting id, through the route it arrives on.

    Without it the two pieces of one sitting are two rows: the time right in total, the COUNT of
    sittings high by one for every file left open long enough to earn a view.
    """
    sign_in(client)
    asset_id = library.id_of("h264")

    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 2_500, "position_ms": 2_500, "sitting": "one-evening", "seeks": 1},
    )
    client.post(
        f"/api/assets/{asset_id}/view",
        json={
            "watch_ms": 7_500,
            "already_reported_ms": 2_500,
            "position_ms": 9_900,
            "ended": True,
            "sitting": "one-evening",
            "seeks": 2,
        },
    )

    rows = read(db_path(client), "SELECT sitting, duration_ms, seeks FROM plays", ())
    assert len(rows) == 1
    assert (rows[0]["sitting"], rows[0]["duration_ms"], rows[0]["seeks"]) == (
        "one-evening",
        10_000,
        3,
    )


def test_a_sitting_keeps_where_it_happened_and_what_the_file_was(
    client: TestClient, library: Library
) -> None:
    """The place travels on the report; the KIND is the server's own, read off the file.

    A client that claims a video is a picture is not believed, because the one fact about a sitting
    the server already holds is the file it was about.
    """
    sign_in(client)
    asset_id = library.id_of("h264")

    response = client.post(
        f"/api/assets/{asset_id}/view",
        json={
            "watch_ms": 1_000,
            "sitting": "from-a-loop",
            "screen": "panel",
            "opened_from": "loops",
            "loop": "a-saved-loop",
            "kept_filter": "a-kept-filter",
        },
    )

    assert response.status_code == 204
    (row,) = read(
        db_path(client),
        "SELECT screen, opened_from, kind, loop_id, kept_filter_id, theater_session FROM plays",
        (),
    )
    assert (row["screen"], row["opened_from"], row["kind"]) == ("panel", "loops", "video")
    assert (row["loop_id"], row["kept_filter_id"]) == ("a-saved-loop", "a-kept-filter")
    assert row["theater_session"] is None


def test_a_screen_the_list_does_not_name_is_refused(client: TestClient, library: Library) -> None:
    sign_in(client)

    response = client.post(
        f"/api/assets/{library.id_of('h264')}/view",
        json={"watch_ms": 1_000, "screen": "sideways"},
    )

    assert response.status_code == 422


def test_finishing_is_recorded_once_and_never_taken_back(
    client: TestClient, library: Library
) -> None:
    """Watching the first second again on Tuesday does not unwatch the rest of it."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 9_000, "ended": True})
    first = _finished_at(client, asset_id, user_id)

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 4_000, "position_ms": 500})

    assert first is not None
    assert _finished_at(client, asset_id, user_id) == first


def test_a_view_is_recorded_against_the_person_who_watched_and_nobody_else(
    client: TestClient, library: Library
) -> None:
    """Per-user, which is the whole reason this table has a composite key.

    Two people watching the same file must not share a count: the history rail and the
    recently-watched list are personal, and a shared counter would show one person what the other
    has been watching.
    """
    asset_id = library.id_of("h264")

    first = sign_in(client, "admin", who="first")
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 3_000})

    second = sign_in(client, "guest", who="second")
    share(client, asset_id, second)
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 7_000})

    assert _state(client, asset_id, first) == (1, 3_000)
    assert _state(client, asset_id, second) == (1, 7_000)


def test_a_negative_watch_time_is_refused(client: TestClient, library: Library) -> None:
    """It cannot happen honestly, and accepting it would let a client wind the total down."""
    sign_in(client)
    response = client.post(f"/api/assets/{library.id_of('h264')}/view", json={"watch_ms": -5_000})
    assert response.status_code == 422


def test_an_absurd_watch_time_is_refused(client: TestClient, library: Library) -> None:
    """More than a day of a single clip is a broken or hostile client, not a viewing."""
    sign_in(client)
    response = client.post(
        f"/api/assets/{library.id_of('h264')}/view", json={"watch_ms": 999_999_999}
    )
    assert response.status_code == 422


# --- where to start from -----------------------------------------------------------------------


def _lengthen(client: TestClient, asset_id: str, duration_ms: int) -> None:
    """Make a fixture clip long enough to be worth resuming. The generated clips are ten seconds,
    which is shorter than the default minimum and would make every case below read as None for the
    wrong reason."""
    write(
        db_path(client),
        [("UPDATE assets SET duration_ms = ? WHERE id = ?", (duration_ms, asset_id))],
    )


def _resume_of(client: TestClient, asset_id: str, user_id: str) -> int | None:
    rows = read(
        db_path(client),
        "SELECT resume_ms FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (asset_id, user_id),
    )
    return None if not rows else rows[0]["resume_ms"]


def _plan(client: TestClient, asset_id: str) -> dict[str, Any]:
    response = client.post(f"/api/assets/{asset_id}/playback", json=MODERN)
    assert response.status_code == 200
    return dict(response.json())


def _set_minimum(client: TestClient, seconds: int) -> None:
    response = client.put(
        "/api/settings", json={"values": {"playback.resume_minimum_seconds": seconds}}
    )
    assert response.status_code in (200, 204), response.text


def test_stopping_in_the_middle_of_a_long_video_is_remembered_and_offered_back(
    client: TestClient, library: Library
) -> None:
    """The whole feature, end to end: stop somewhere, come back, be offered that place.

    Read back through the plan rather than out of the table, because the plan is what the player
    acts on: a position stored and never returned is the same as no position at all.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    _lengthen(client, asset_id, 60 * 60 * 1000)

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 5_000, "position_ms": 900_000})

    assert _resume_of(client, asset_id, user_id) == 900_000
    assert _plan(client, asset_id)["resume_ms"] == 900_000


def test_a_video_shorter_than_the_minimum_is_not_remembered(
    client: TestClient, library: Library
) -> None:
    """The clips are ten seconds and the default minimum is a minute, so this is the ordinary case
    for a library of short clips: nothing is stored and nothing is offered."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 5_000, "position_ms": 5_000})

    assert _resume_of(client, asset_id, user_id) is None
    assert _plan(client, asset_id)["resume_ms"] is None


def test_lowering_the_minimum_makes_short_videos_remember(
    client: TestClient, library: Library
) -> None:
    """The setting is real: the same ten-second clip is remembered once the user asks for it."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    _set_minimum(client, 0)

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 3_000, "position_ms": 5_000})

    assert _resume_of(client, asset_id, user_id) == 5_000


def test_raising_the_minimum_stops_a_stored_position_being_offered(
    client: TestClient, library: Library
) -> None:
    """The read applies the rule again rather than trusting what is stored.

    Somebody who raises the minimum means short videos stop resuming NOW. Enforcing it only on the
    way in would leave them resuming until each was next watched to the end, which is not an answer
    anybody would connect to the setting they just changed.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    _set_minimum(client, 0)
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 3_000, "position_ms": 5_000})
    assert _resume_of(client, asset_id, user_id) == 5_000

    _set_minimum(client, 60)

    # Still on disk, and no longer offered.
    assert _resume_of(client, asset_id, user_id) == 5_000
    assert _plan(client, asset_id)["resume_ms"] is None


def test_watching_to_the_end_clears_the_place_it_stopped_last_time(
    client: TestClient, library: Library
) -> None:
    """A position is replaced by every report, including with nothing.

    Without this, a video stopped halfway and then watched right through would open at the halfway
    point forever: the stale value would never be written over, because a finished sitting has no
    position of its own to write.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    _lengthen(client, asset_id, 60 * 60 * 1000)

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 5_000, "position_ms": 900_000})
    assert _resume_of(client, asset_id, user_id) == 900_000

    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 2_700_000, "position_ms": 60 * 60 * 1000},
    )

    assert _resume_of(client, asset_id, user_id) is None


def test_where_somebody_stopped_is_theirs_alone(client: TestClient, library: Library) -> None:
    """Two people watching the same file each come back to their own place. A shared position would
    also tell one of them how far the other had got."""
    asset_id = library.id_of("h264")

    first = sign_in(client, "admin", who="first")
    _lengthen(client, asset_id, 60 * 60 * 1000)
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 1_000, "position_ms": 600_000})

    second = sign_in(client, "guest", who="second")
    share(client, asset_id, second)
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 1_000, "position_ms": 1_800_000})

    assert _resume_of(client, asset_id, first) == 600_000
    assert _resume_of(client, asset_id, second) == 1_800_000
    assert _plan(client, asset_id)["resume_ms"] == 1_800_000


def test_a_negative_position_is_refused(client: TestClient, library: Library) -> None:
    sign_in(client)
    response = client.post(
        f"/api/assets/{library.id_of('h264')}/view", json={"watch_ms": 0, "position_ms": -1}
    )
    assert response.status_code == 422


def test_a_report_without_a_position_says_there_is_none(
    client: TestClient, library: Library
) -> None:
    """The field is optional on the wire, and leaving it out means the beginning rather than
    "leave whatever was there": an older client must not be able to pin a stale position."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    _lengthen(client, asset_id, 60 * 60 * 1000)
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 1_000, "position_ms": 900_000})

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 1_000})

    assert _resume_of(client, asset_id, user_id) is None


# --- carrying the plan between requests --------------------------------------------------------------


def test_the_query_carries_the_route() -> None:
    """HLS is several requests and the server holds nothing between them, so the decision travels."""
    assert plan_query(policy.Plan(route=policy.Route.REMUX, reason="")) == "?route=remux"


def test_the_query_carries_the_reduced_height_too() -> None:
    """The other half of the same rule, and the one with the worse failure.

    A file the policy decided to reduce is one the machine cannot convert at full size fast enough
    to play smoothly. Losing the height between the plan and the segment does not produce a
    slightly-too-large picture: it produces the full-size conversion that was measured as unable
    to keep up, so the video stalls exactly as if the gate had never run.
    """
    plan = policy.Plan(route=policy.Route.TRANSCODE, reason="", scale_height=1080)

    query = plan_query(plan)

    assert "route=transcode" in query
    assert "height=1080" in query


def test_no_height_is_written_when_the_picture_is_kept() -> None:
    """The ordinary case. An absent height means native resolution, not a default one."""
    assert "height" not in plan_query(policy.Plan(route=policy.Route.TRANSCODE, reason=""))


# --- one transcode at a time -----------------------------------------------------------------------


def test_no_more_transcodes_run_at_once_than_this_machine_allows(
    client: TestClient, app: FastAPI, library: Library
) -> None:
    """The queue enforces this machine's transcode cap (one on a processor, three on a card), asked
    of the application rather than written here, with requests issued from several threads at
    once."""
    sign_in(client)
    asset_id = library.id_of("hevc")
    path = db_path(client)

    peak = 0
    seen_running = False
    done = threading.Event()

    def watch() -> None:
        nonlocal peak, seen_running
        while not done.is_set():
            rows = read(
                path,
                "SELECT COUNT(*) AS running FROM jobs "
                "WHERE type = 'transcode' AND state = 'running'",
            )
            running = int(rows[0]["running"])
            peak = max(peak, running)
            if running:
                seen_running = True
            time.sleep(0.005)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    # Every segment asked for at once, which is what a player reading ahead actually does.
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(
                pool.map(
                    lambda index: client.get(
                        f"/api/assets/{asset_id}/hls/{index}{tuning.SEGMENT_SUFFIX}"
                    ),
                    range(5),
                )
            )
    finally:
        done.set()
        watcher.join(timeout=5)

    cap = job_limits(choose_encoder(part_of_app(app, HARDWARE)))[TRANSCODE]

    assert all(response.status_code == 200 for response in responses)
    assert seen_running, "the watcher never observed a transcode, so it proved nothing"
    # Five were asked for at once, so a cap that binds at all has to show up as fewer than five.
    assert cap < 5, "the cap no longer binds against this test's demand; raise the demand"
    assert peak <= cap, f"{peak} transcodes ran at once; the per-type cap of {cap} is not holding"


# --- when it goes wrong ------------------------------------------------------------------------------


def test_a_segment_that_cannot_be_produced_is_not_reported_as_missing(
    client: TestClient, library: Library
) -> None:
    """503, not 404, and the distinction is a security one rather than a pedantic one.

    On these routes a 404 means "you may not have this, or it does not exist", deliberately
    indistinguishable. A transcode that failed is neither. Returning 404 would make a broken
    encoder look exactly like a permission refusal, so an operator debugging "my video will not
    play" would be looking at the access rules instead of at ffmpeg.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")

    # Take the source file away after the asset row exists: the job resolves the path, finds
    # nothing readable, and fails.
    for stale in library.media.glob("clips/hevc-*.mp4"):
        stale.unlink()

    response = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")

    assert response.status_code == 503
    assert "could not be prepared" in response.json()["detail"]


# --- the range parser, at its edges -------------------------------------------------------------------


def test_no_range_header_means_the_whole_file() -> None:
    assert byte_range(None, 100) is None


def test_a_range_with_neither_end_is_ignored() -> None:
    """`bytes=-` names nothing. The whole file is the correct answer."""
    assert byte_range("bytes=-", 100) is None


def test_a_zero_length_suffix_cannot_be_satisfied() -> None:
    """`bytes=-0` asks for the last nothing bytes, which is not a range."""
    with pytest.raises(ValueError, match="empty range"):
        byte_range("bytes=-0", 100)


def test_a_suffix_longer_than_the_file_is_the_whole_file() -> None:
    span = byte_range("bytes=-500", 100)
    assert span is not None
    assert (span.start, span.end) == (0, 99)


def test_a_reversed_range_is_ignored_and_the_whole_file_answers() -> None:
    """`bytes=80-20` is not a range. The specification says to ignore a Range header holding an
    invalid one; 416 is the word for a valid range past the end, which this is not."""
    assert byte_range("bytes=80-20", 100) is None


def test_a_range_past_the_end_is_refused() -> None:
    with pytest.raises(ValueError, match="outside the file"):
        byte_range("bytes=200-300", 100)


def test_whitespace_around_a_range_is_tolerated() -> None:
    span = byte_range("  bytes=0-9  ", 100)
    assert span is not None
    assert span.length == 10


def test_the_content_range_header_is_formatted_the_way_the_spec_wants() -> None:
    span = byte_range("bytes=10-19", 100)
    assert span is not None
    assert span.content_range == "bytes 10-19/100"


async def test_reading_a_range_stops_when_the_file_is_truncated_underneath_it(
    tmp_path: Path,
) -> None:
    """A file shrinking mid-read is a real outcome: an eviction, a full disk. The loop counts
    down what is left rather than trusting the length, so it stops rather than spinning."""
    from sift.slices.player.service import read_range

    path = tmp_path / "clip.bin"
    path.write_bytes(b"x" * 100)
    span = byte_range("bytes=0-99", 100)
    assert span is not None

    path.write_bytes(b"x" * 10)

    collected = b""
    async for chunk in read_range(path, span):
        collected += chunk

    assert collected == b"x" * 10
    await asyncio.sleep(0)


async def test_reading_stops_when_the_client_has_gone(tmp_path: Path) -> None:
    """A browser takes what it wants and hangs up, which is normal. Carrying on reading is not.

    An abandoned read holds a thread from the pool the API and every other stream share, so the
    cost of finishing it is paid by whoever asks for something next.
    """
    from sift.slices.player.service import read_range

    path = tmp_path / "clip.bin"
    path.write_bytes(b"x" * (4 * tuning.STREAM_CHUNK_BYTES))
    span = byte_range(f"bytes=0-{4 * tuning.STREAM_CHUNK_BYTES - 1}", 4 * tuning.STREAM_CHUNK_BYTES)
    assert span is not None

    asked = 0

    async def gone() -> bool:
        nonlocal asked
        asked += 1
        return asked > 2

    collected = 0
    async for chunk in read_range(path, span, gone=gone):
        collected += len(chunk)

    assert collected == 2 * tuning.STREAM_CHUNK_BYTES
    await asyncio.sleep(0)


async def test_a_read_that_is_still_wanted_runs_to_the_end(tmp_path: Path) -> None:
    """The other half of the check above: being asked must not be what stops it."""
    from sift.slices.player.service import read_range

    path = tmp_path / "clip.bin"
    path.write_bytes(b"x" * 100)
    span = byte_range("bytes=0-99", 100)
    assert span is not None

    async def gone() -> bool:
        return False

    collected = b""
    async for chunk in read_range(path, span, gone=gone):
        collected += chunk

    assert collected == b"x" * 100
    await asyncio.sleep(0)


async def test_every_chunk_read_says_a_clip_is_playing(tmp_path: Path) -> None:
    from sift.kernel.attention import Played
    from sift.slices.player.service import read_range

    path = tmp_path / "clip.bin"
    path.write_bytes(b"x" * (2 * tuning.STREAM_CHUNK_BYTES))
    whole = 2 * tuning.STREAM_CHUNK_BYTES
    span = byte_range(f"bytes=0-{whole - 1}", whole)
    assert span is not None
    now = [10.0]
    played = Played(clock=lambda: now[0])
    seen: list[float | None] = []

    async for _chunk in read_range(path, span, played=played.now):
        seen.append(played.seconds_since())
        now[0] += 4.0

    assert seen == [0.0, 0.0]
    assert played.seconds_since() == 4.0


def test_an_open_ended_range_is_shortened_to_the_limit() -> None:
    span = byte_range("bytes=100-", 10_000)
    assert span is not None
    assert capped(span, "bytes=100-", 512).length == 512
    assert capped(span, "bytes=100-", 512).start == 100


def test_a_range_shorter_than_the_limit_is_left_as_it_is() -> None:
    span = byte_range("bytes=9900-", 10_000)
    assert span is not None
    assert capped(span, "bytes=9900-", 512) is span


def test_a_range_with_both_ends_is_never_shortened() -> None:
    span = byte_range("bytes=0-9999", 10_000)
    assert span is not None
    assert capped(span, "bytes=0-9999", 512) is span


def test_a_request_with_no_range_at_all_is_never_shortened() -> None:
    span = byte_range("bytes=0-9999", 10_000)
    assert span is not None
    assert capped(span, None, 512) is span


def test_the_largest_size_stored_on_playback_is_the_ceiling_the_plan_is_decided_under(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setting read by the playback route at the moment somebody presses play.

    What the ceiling then does to a heavy file is the policy's own tests; this is the step before
    it, so a change saved on the settings screen is the ceiling the very next plan is held to.
    """
    sign_in(client)
    asset_id = library.id_of("h264")
    asked: list[int] = []
    decide = policy.decide

    def spy(*args: Any, **kwargs: Any) -> policy.Plan:
        asked.append(kwargs["max_height"])
        return decide(*args, **kwargs)

    monkeypatch.setattr(policy, "decide", spy)
    for height in (720, 1440):
        saved = client.put(
            "/api/settings", json={"values": {"playback.max_transcode_height": height}}
        )
        assert saved.status_code in (200, 204), saved.text
        _plan(client, asset_id)

    assert asked == [720, 1440]


def _set_resume(client: TestClient, on: bool) -> None:
    response = client.put("/api/settings", json={"values": {"playback.resume_enabled": on}})
    assert response.status_code in (200, 204), response.text


def test_turning_resume_off_keeps_no_position_at_all(client: TestClient, library: Library) -> None:
    """Off is not the same as a minimum nobody reaches.

    A large minimum still keeps a position for a long video. Off keeps none, ever, for anything,
    which is what somebody who does not want a machine remembering what they were partway through
    is asking for. So it has to stop the WRITE, not merely the offer.
    """
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    _lengthen(client, asset_id, 60 * 60 * 1000)
    _set_resume(client, False)

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 5_000, "position_ms": 900_000})

    assert _resume_of(client, asset_id, user_id) is None
    assert _plan(client, asset_id)["resume_ms"] is None


def test_turning_resume_off_also_stops_an_old_position_being_offered(
    client: TestClient, library: Library
) -> None:
    """Somebody who turns it off means stop doing that now, not from the next video onwards."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    _lengthen(client, asset_id, 60 * 60 * 1000)
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 5_000, "position_ms": 900_000})
    assert _resume_of(client, asset_id, user_id) == 900_000

    _set_resume(client, False)

    assert _plan(client, asset_id)["resume_ms"] is None


# --- the master playlist ------------------------------------------------------------------------


def test_the_master_playlist_lists_the_smallest_variant_first() -> None:
    """A player that has not measured anything yet takes the first one.

    Starting at the bottom means the first seconds arrive quickly on a slow connection and the
    ladder climbs from there. Starting at the top means a stall before anything is known, which is
    the shape people describe as "it never starts".
    """
    body = master_playlist(
        asset(width=3840, height=2160, duration_ms=60_000), base_url="/api/assets/x/hls"
    )
    bandwidths = [
        int(line.split("BANDWIDTH=")[1].split(",")[0])
        for line in body.splitlines()
        if line.startswith("#EXT-X-STREAM-INF:")
    ]

    assert bandwidths == sorted(bandwidths), "the variants are not in ascending order"
    assert len(set(bandwidths)) == len(bandwidths), "two variants claiming one weight is a tie"
    assert len(bandwidths) >= 2, "a ladder with one rung is not a ladder"


def test_a_portrait_ladder_declares_each_rungs_own_weight() -> None:
    """The weights are keyed by the short side, which is what a rung's name means.

    Looked up by height they would match nothing on a portrait file (852, 1280, 1920 tall are not
    keys of a table written 360 to 2160), so every portrait variant would fall to the fallback and
    be declared to cost twenty megabits, one more than the one before (20000000, 20000001,
    20000002 on a 720x1280 file that really averages two megabits), and hls.js would be told every
    choice costs the same.
    """
    body = master_playlist(
        asset(width=720, height=1280, duration_ms=60_000), base_url="/api/assets/x/hls"
    )
    bandwidths = [
        int(line.split("BANDWIDTH=")[1].split(",")[0])
        for line in body.splitlines()
        if line.startswith("#EXT-X-STREAM-INF:")
    ]

    rungs = [rung.short for rung in reversed(policy.rungs(720, 1280))]
    assert rungs == [360, 480], "a 720-wide portrait file has two rungs under it"
    assert bandwidths[:2] == [tuning.LADDER_BITRATES[360], tuning.LADDER_BITRATES[480]]
    # The rungs, not the source: the source's weight is measured from the file and this row's
    # thousand bytes put it under the floor, which the test below is about.
    assert bandwidths[1] - bandwidths[0] > 1, "one bit apart is the fallback stacking, not a ladder"


def test_every_rung_has_a_weight() -> None:
    """The two tables are one ladder written twice, and the playlist looks one up by the other."""
    assert set(tuning.LADDER_BITRATES) == set(tuning.LADDER_HEIGHTS)


def test_the_full_size_variant_never_claims_to_be_the_cheapest() -> None:
    """A measured figure can be anything; the ordering it sits in cannot.

    The source's weight is averaged from the file's own size and length, so a badly-probed row
    (or one whose size was never recorded) can make the FULL-SIZE stream look like the lightest
    thing on offer. hls.js believes what it is told, hands somebody on a slow connection the 4K
    variant, and the video never starts.
    """
    body = master_playlist(
        # A minute of video recorded as one kilobyte: nothing about this row is plausible, and a
        # library that has been through a bad import has rows exactly like it.
        asset(width=3840, height=2160, duration_ms=60_000),
        base_url="/api/assets/x/hls",
    )
    bandwidths = [
        int(line.split("BANDWIDTH=")[1].split(",")[0])
        for line in body.splitlines()
        if line.startswith("#EXT-X-STREAM-INF:")
    ]

    assert bandwidths[-1] == max(bandwidths), "the full-size variant is not the heaviest"


def test_every_variant_declares_a_size_a_codec_and_a_weight() -> None:
    """All three are what hls.js compares against the speed it is measuring.

    `BANDWIDTH` is required by the specification on every variant, and a variant with no `CODECS`
    is one a player has to fetch before it can find out it cannot play it.
    """
    body = master_playlist(
        asset(width=1920, height=1080, duration_ms=60_000), base_url="/api/assets/x/hls"
    )

    for line in body.splitlines():
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        assert "BANDWIDTH=" in line
        assert "RESOLUTION=" in line
        assert 'CODECS="avc1.' in line


def test_the_source_is_always_offered_even_when_nothing_fits_under_it() -> None:
    """A small clip has no rung below it, and "as it was made" still has to be reachable."""
    body = master_playlist(
        asset(width=640, height=360, duration_ms=60_000), base_url="/api/assets/x/hls"
    )

    variants = [line for line in body.splitlines() if line.startswith("#EXT-X-STREAM-INF:")]
    assert len(variants) == 1


def test_a_variant_url_carries_the_height_it_is_for() -> None:
    """Nothing is held between requests, so the size travels in the address or it does not travel.

    A variant URL that lost its height would be re-decided from nothing on the next request and
    land on the default: a full-size conversion, which is the one thing the person did not ask
    for.
    """
    body = master_playlist(
        asset(width=3840, height=2160, duration_ms=60_000), base_url="/api/assets/x/hls"
    )
    urls = [line for line in body.splitlines() if line.startswith("/api/")]

    assert any("height=720" in url for url in urls)
    # The source's own variant asks for no height at all, which is what means "its own size".
    assert any(url.endswith("route=transcode") for url in urls)


def test_a_file_whose_shape_nobody_knows_declares_no_resolution() -> None:
    """`RESOLUTION` is optional in the specification and a guess is worse than its absence: a
    player told the wrong size lays out around it and picks between variants on it. A row that has
    never been probed still gets a playable variant, just an unlabelled one."""
    body = master_playlist(
        asset(width=None, height=None, duration_ms=60_000), base_url="/api/assets/x/hls"
    )
    variants = [line for line in body.splitlines() if line.startswith("#EXT-X-STREAM-INF:")]

    assert len(variants) == 1
    assert "BANDWIDTH=" in variants[0]
    assert "RESOLUTION=" not in variants[0]


def test_a_file_with_no_recorded_length_is_given_the_fallback_weight() -> None:
    """The weight is averaged from the size and the length, and there is no average to take
    without both. Dividing by a zero length is the arithmetic this refuses; the figure that comes
    out instead is a stated constant rather than something invented per file."""
    assert source_bitrate(asset(duration_ms=None)) == tuning.FALLBACK_SOURCE_BITRATE
    assert source_bitrate(asset(duration_ms=0)) == tuning.FALLBACK_SOURCE_BITRATE


def test_the_weight_of_a_real_file_is_its_own_size_over_its_own_length() -> None:
    """The ordinary case, so the fallback above is visibly the exception rather than the rule."""
    row = asset(duration_ms=10_000)
    assert row.size_bytes is not None
    assert source_bitrate(row) == round(row.size_bytes * 8 * 1000 / 10_000)


# --- how many segments may be built at once -----------------------------------------------------


def test_the_processor_builds_one_segment_at_a_time() -> None:
    """The processor builds one segment at a time: ffmpeg already saturates its cores."""
    assert job_limits(Encoder.CPU) == {TRANSCODE: 1}


def test_a_card_builds_several_at_once_because_the_cores_are_not_the_limit() -> None:
    """A card builds several at once, held below four since it also draws previews."""
    limit = job_limits(Encoder.NVENC)[TRANSCODE]

    assert limit == tuning.HARDWARE_SEGMENT_JOBS
    assert 1 < limit < 4


# --- learning the encoder's real speed ----------------------------------------------------------


def _service(encoder: Encoder, tmp_path: Path) -> PlayerService:
    from sift.kernel.config import Settings
    from sift.slices.player.cache import SegmentCache

    cache = SegmentCache(tmp_path / "segments", max_bytes=1024 * 1024)
    return PlayerService(
        cache,
        cast("Any", None),
        settings=Settings(),
        accelerator=_report_offering(encoder),
        cpu_count=4,
    )


def _report_offering(encoder: Encoder) -> Accelerator:
    """An accelerator for a machine whose report offers exactly this encoder."""
    return Accelerator(
        HardwareReport(
            cpu_count=8,
            total_ram_bytes=16 << 30,
            worker_concurrency=4,
            cuda=encoder is not Encoder.CPU,
            rocm=False,
            transcode_encoders=() if encoder is Encoder.CPU else (encoder.value,),
            warnings=(),
        )
    )


def _accelerated(tmp_path: Path, **over: Any) -> PlayerService:
    """A service holding a card the report says works, and an accelerator that can change its mind."""
    from sift.kernel.config import Settings
    from sift.slices.player.cache import SegmentCache

    report = HardwareReport(
        cpu_count=8,
        total_ram_bytes=16 << 30,
        worker_concurrency=4,
        cuda=True,
        rocm=False,
        transcode_encoders=("h264_nvenc",),
        warnings=(),
    )
    return PlayerService(
        SegmentCache(tmp_path / "segments", max_bytes=1024 * 1024),
        cast("Any", None),
        settings=Settings(),
        accelerator=Accelerator(report, **over),
        cpu_count=8,
    )


def test_the_smoothness_projection_stops_believing_a_card_that_was_given_up_on(
    tmp_path: Path,
) -> None:
    """The number that decides what quality this machine can keep up with.

    A card's starting figure is many times a processor's, so left in place after a fall back it
    promises a quality nothing can render in time, and what somebody sees is a video that stalls,
    with the encoder that caused it no longer in use and nothing connecting the two.
    """
    from sift.kernel.media import GIVE_UP_AFTER, FFmpegError
    from sift.kernel.media import Encoder as KernelEncoder

    service = _accelerated(tmp_path)
    assert service.encoder_rate is not None, "a working card had no rate at all"

    async def refuse(encoder: KernelEncoder, _decode: tuple[str, ...]) -> str:
        if encoder is not KernelEncoder.CPU:
            raise FFmpegError("the driver refused")
        return "rendered"

    async def give_up() -> None:
        for _ in range(GIVE_UP_AFTER):
            await cast("Any", service._accelerator).run(refuse)

    asyncio.run(give_up())

    assert service.encoder is Encoder.CPU
    assert service.encoder_rate is None, "it still believed the card it had stopped using"


def test_a_segment_the_card_refuses_is_rendered_by_the_processor_instead(
    tmp_path: Path,
) -> None:
    """A segment the card refuses is rendered by the processor instead."""
    from sift.kernel.media import Encoder as KernelEncoder
    from sift.kernel.media import FFmpegError

    service = _accelerated(tmp_path)
    used: list[KernelEncoder] = []

    async def render_with(encoder: KernelEncoder, _decode: tuple[str, ...]) -> float:
        used.append(encoder)
        if encoder is not KernelEncoder.CPU:
            raise FFmpegError("CreateInputBuffer failed: invalid param (8)")
        return 1.0

    assert asyncio.run(cast("Any", service._accelerator).run(render_with)) == 1.0
    assert used == [KernelEncoder.NVENC, KernelEncoder.CPU], (
        "a refused segment was not re-rendered on the processor"
    )


def test_the_service_hands_its_decoder_down_to_every_segment(tmp_path: Path) -> None:
    """A field the wiring sets and the spec never carries is a feature that is on everywhere except
    in the command, and nothing about the video would look different. This is the join."""
    from sift.kernel.config import Settings
    from sift.slices.player.cache import SegmentCache

    service = PlayerService(
        SegmentCache(tmp_path / "segments", max_bytes=1024 * 1024),
        cast("Any", None),
        settings=Settings(),
        accelerator=_report_offering(Encoder.NVENC),
        cpu_count=4,
    )

    built = service.spec_for(
        tmp_path / "clip.mp4",
        tmp_path / "seg.m4s",
        index=0,
        asset=asset(width=1920, height=1080),
        plan=policy.Plan(route=policy.Route.TRANSCODE, reason=""),
    )

    assert built.decode == ("-hwaccel", "cuda")


def test_the_processor_has_no_separate_budget_to_learn(tmp_path: Path) -> None:
    """None is what tells `policy` to use its single-budget arithmetic."""
    service = _service(Encoder.CPU, tmp_path)
    service.observe(
        asset=asset(width=1920, height=1080),
        plan=policy.Plan(route=policy.Route.TRANSCODE, reason=""),
        elapsed=1.0,
        video_seconds=2.0,
    )

    assert service.encoder_rate is None


def test_a_hardware_encoder_starts_conservative_and_learns_upward(tmp_path: Path) -> None:
    """The starting figure is a third of what a current card sustains, so a fast card must move it.

    Being wrong in the optimistic direction is the mistake that produces a video pausing every few
    seconds for ever, so the estimate starts low on purpose and the real work corrects it.
    """
    service = _service(Encoder.NVENC, tmp_path)
    started = service.encoder_rate
    assert started == tuning.STARTING_ENCODER_PIXELS_PER_SECOND["h264_nvenc"]

    for _ in range(20):
        service.observe(
            asset=asset(width=1920, height=1080, fps=30.0),
            plan=policy.Plan(route=policy.Route.TRANSCODE, reason=""),
            # Two seconds of 1080p30 encoded in a tenth of a second, which is roughly a current
            # card.
            elapsed=0.4,
            video_seconds=2.0,
        )

    assert service.encoder_rate is not None
    assert service.encoder_rate > started


def test_a_segment_that_was_all_decoding_teaches_nothing(tmp_path: Path) -> None:
    """It says the encoder was not what held it up, which is not a measurement of the encoder.

    Dividing a large pixel count by whatever rounding is left over produces a wildly optimistic
    number: the direction that makes Sift promise smooth playback and then stutter.
    """
    service = _service(Encoder.NVENC, tmp_path)
    before = service.encoder_rate

    service.observe(
        asset=asset(width=3840, height=2160, fps=30.0),
        # Faster than this machine could possibly have decoded it, so nothing is left over.
        plan=policy.Plan(route=policy.Route.TRANSCODE, reason=""),
        elapsed=0.001,
        video_seconds=2.0,
    )

    assert service.encoder_rate == before


def test_an_unprobed_file_teaches_nothing_either(tmp_path: Path) -> None:
    """With no dimensions there is no pixel count, and a rate needs one."""
    service = _service(Encoder.NVENC, tmp_path)
    before = service.encoder_rate

    service.observe(
        asset=asset(width=None, height=None),
        plan=policy.Plan(route=policy.Route.TRANSCODE, reason=""),
        elapsed=1.0,
        video_seconds=2.0,
    )

    assert service.encoder_rate == before


# --- the replay curve ---------------------------------------------------------------------------


def _curve(client: TestClient, asset_id: str) -> dict[str, Any]:
    response = client.get(f"/api/assets/{asset_id}/replays")
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


def test_a_file_nobody_has_played_has_a_flat_curve(client: TestClient, library: Library) -> None:
    """All zeros rather than an error or an empty list. "No replays yet" is a real answer."""
    sign_in(client)

    curve = _curve(client, library.id_of("h264"))

    assert curve["buckets"] == HEAT_BUCKETS
    assert curve["heat"] == [0.0] * HEAT_BUCKETS
    assert curve["worth_drawing"] is False


def test_watching_one_moment_repeatedly_puts_a_peak_there(
    client: TestClient, library: Library
) -> None:
    """The whole feature, end to end: three sittings, one of them on a moment revisited."""
    sign_in(client)
    asset_id = library.id_of("h264")

    # A pass through the first half, then the same short stretch four times over.
    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 5_000, "heat": {str(at): 100 for at in range(50)}},
    )
    for _ in range(4):
        client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 3_000, "heat": {"30": 750}})

    curve = _curve(client, asset_id)

    # The peak is where it was replayed, and it is the tallest thing on the curve by construction.
    assert curve["heat"].index(max(curve["heat"])) == 30
    assert curve["heat"][30] == 1.0
    assert curve["worth_drawing"] is True
    # And the rest of the played half is a fraction of it rather than zero: a curve that erased
    # everything but its peak would say nothing about the shape of the watching.
    assert 0 < curve["heat"][10] < 1


def test_a_file_watched_straight_through_is_not_worth_drawing(
    client: TestClient, library: Library
) -> None:
    """True, and it says nothing. A flat shape on screen invites a reader to find a pattern in it."""
    sign_in(client)
    asset_id = library.id_of("h264")

    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 9_000, "heat": {str(at): 90 for at in range(HEAT_BUCKETS)}},
    )

    assert _curve(client, asset_id)["worth_drawing"] is False


def test_a_curve_is_added_to_rather_than_replaced(client: TestClient, library: Library) -> None:
    """Two sittings on one moment is twice the height of one, or the curve is only ever the last
    thing that happened."""
    sign_in(client)
    asset_id = library.id_of("h264")

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 3_000, "heat": {"10": 400}})
    client.post(
        f"/api/assets/{asset_id}/view", json={"watch_ms": 3_000, "heat": {"10": 400, "20": 400}}
    )

    rows = read(
        db_path(client),
        "SELECT bucket, watched_ms FROM asset_replay_heat WHERE asset_id = ? ORDER BY bucket",
        (asset_id,),
    )
    assert [(row["bucket"], row["watched_ms"]) for row in rows] == [(10, 800), (20, 400)]


def test_an_unreadable_slice_does_not_cost_the_whole_sitting(
    client: TestClient, library: Library
) -> None:
    """The report arrives with `keepalive` from a page that is closing, so nothing is watching for a
    reply: a refusal would be invisible and would take the view and the time with it."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")

    response = client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 4_000, "heat": {"7": 500, "not-a-slice": 500, "9999": 500}},
    )

    assert response.status_code == 204
    assert _state(client, asset_id, user_id) == (1, 4_000)
    rows = read(
        db_path(client),
        "SELECT bucket FROM asset_replay_heat WHERE asset_id = ?",
        (asset_id,),
    )
    assert [row["bucket"] for row in rows] == [7]


def test_a_replay_curve_is_per_user(client: TestClient, library: Library) -> None:
    """Like the heart and the stars. What somebody else has replayed is not a fact about the file."""
    asset_id = library.id_of("h264")

    sign_in(client, "admin", who="first")
    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 4_000, "heat": {"5": 900}})

    second = sign_in(client, "guest", who="second")
    share(client, asset_id, second)

    assert _curve(client, asset_id)["worth_drawing"] is False


def test_a_curve_cannot_be_asked_for_a_file_the_viewer_cannot_see(
    client: TestClient, library: Library
) -> None:
    """Denied and missing are the same answer here, as on every other route in this module: a 403
    would confirm the id exists, which for a concealed file is the thing being protected."""
    asset_id = library.id_of("h264")
    sign_in(client, "guest", who="outsider")

    assert client.get(f"/api/assets/{asset_id}/replays").status_code == 404
