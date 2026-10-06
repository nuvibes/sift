# SPDX-License-Identifier: AGPL-3.0-or-later
"""The transcode path, against real ffmpeg: playlists, segments, seeking, latency and the cap.

Real media and a real encoder throughout. The thing being claimed here (that a browser which
cannot decode a file still gets smooth video, quickly, without filling the disk) is a claim about
what ffmpeg actually does on a real file, and a mocked encoder would test the plumbing while
leaving the claim itself unexamined.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.attention import Played
from sift.kernel.config import Settings
from sift.kernel.ids import new_id
from sift.kernel.jobs import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
from sift.kernel.wiring import QUEUE, part_of_app
from sift.slices.player import policy, tuning
from sift.slices.player.tests.conftest import (
    _EPOCH,
    _INSERT_ASSET,
    _INSERT_LOCATION,
    ANCIENT,
    CLIP_SECONDS,
    MODERN,
    NO_MATROSKA,
    Library,
    db_path,
    read,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]

_ROUTES = importlib.import_module("sift.slices.player.router")

#: From measurement, and not from a guess. The worst *streamable* file measured (HEVC
#: 10-bit 1080p at 60 fps on two cores) took 1.68 s, and 2.5 s adds about 50% headroom to cover
#: the gap between a throttled fast core and a genuinely modest one. Deliberately not the 4-core
#: number (0.93 s), which would fail on the hardware this is supposed to run on.
FIRST_SEGMENT_BUDGET_SECONDS = 2.5


def _plan(
    client: TestClient, asset_id: str, capabilities: dict[str, list[str]]
) -> dict[str, object]:
    response = client.post(f"/api/assets/{asset_id}/playback", json=capabilities)
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


# --- choosing the path ---------------------------------------------------------------------------


def test_a_browser_that_cannot_decode_the_codec_is_sent_to_hls(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    plan = _plan(client, library.id_of("hevc"), ANCIENT)

    assert plan["route"] == "transcode"
    # The plan rides in the address, so the URL is the playlist plus the decision.
    assert "/hls/index.m3u8" in str(plan["url"])
    assert "route=transcode" in str(plan["url"])


def _unread_row(client: TestClient, library: Library, *, mime: str) -> str:
    """A video the scan took in and nothing has read: a mime from the gate, no streams, no size."""
    asset_id = new_id()
    write(
        db_path(client),
        [
            (
                _INSERT_ASSET,
                (
                    asset_id,
                    f"digest-{asset_id}",
                    mime,
                    None,
                    None,
                    None,
                    None,
                    1024,
                    None,
                    None,
                    None,
                    "unread.bin",
                    _EPOCH,
                    None,
                    # `bit_depth` and `color_transfer`, which the statement carries. Nothing has
                    # read this file, so it knows neither.
                    None,
                    None,
                ),
            ),
            (
                _INSERT_LOCATION,
                (
                    new_id(),
                    asset_id,
                    library.root,
                    library.folder,
                    "clips/unread.bin",
                    "unread.bin",
                    _EPOCH,
                    _EPOCH,
                ),
            ),
        ],
    )
    return asset_id


def _reads_queued_for(client: TestClient, asset_id: str) -> int:
    rows = read(
        db_path(client),
        "SELECT COUNT(*) AS queued FROM jobs "
        "WHERE type = 'probe' AND state IN ('queued','running') "
        "AND json_extract(payload, '$.asset_id') = ?",
        (asset_id,),
    )
    return int(rows[0]["queued"])


def test_a_file_nobody_has_read_is_answered_as_unread_and_the_read_is_queued(
    idle_workers: None, client: TestClient, library: Library
) -> None:
    """Not a conversion of one segment that ends the playlist after two seconds, under a sentence
    blaming the browser: an unread video is a state of its own, and pressing play is what puts the
    read at the front of the queue."""
    sign_in(client)
    asset_id = _unread_row(client, library, mime="video/x-matroska")

    plan = _plan(client, asset_id, NO_MATROSKA)

    assert plan["route"] == "unread"
    assert "not read" in str(plan["reason"])
    assert plan["qualities"] == []
    assert _reads_queued_for(client, asset_id) == 1

    _plan(client, asset_id, NO_MATROSKA)
    assert _reads_queued_for(client, asset_id) == 1, "a second press does not queue a second read"


def test_a_read_that_cannot_be_queued_is_logged_and_the_answer_is_still_unread(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pressing play puts the read at the front of the queue, but the queue refusing is not the
    player's failure: the scan or the catch-up pass at start reads the file anyway, so the answer
    stays "unread" and the refusal goes to the log rather than to the screen."""
    sign_in(client)
    asset_id = _unread_row(client, library, mime="video/x-matroska")
    queue = part_of_app(cast("FastAPI", client.app), QUEUE)

    async def refusing(*_: object, **__: object) -> str:
        raise RuntimeError("the queue is closed")

    monkeypatch.setattr(queue, "enqueue", refusing)

    plan = _plan(client, asset_id, NO_MATROSKA)

    assert plan["route"] == "unread"
    assert _reads_queued_for(client, asset_id) == 0


def test_a_read_already_waiting_behind_a_scan_is_raised_rather_than_skipped(
    idle_workers: None, client: TestClient, library: Library
) -> None:
    """Standing down because a read is already coming would be a silent wait. A scan hands out a
    read per file at the ordinary priority, so a file the scan has reached has one coming, behind
    every other file in the scan. Pressing play raises that row rather than queueing a second read
    of the same file."""
    sign_in(client)
    asset_id = _unread_row(client, library, mime="video/x-matroska")
    write(
        db_path(client),
        [
            (
                "INSERT INTO jobs (id, type, state, priority, payload, created_at, updated_at) "
                "VALUES (?, 'probe', 'queued', ?, ?, 1, 1)",
                (new_id(), DEFAULT_PRIORITY, json.dumps({"asset_id": asset_id})),
            )
        ],
    )

    _plan(client, asset_id, NO_MATROSKA)

    waiting = read(
        db_path(client),
        "SELECT priority FROM jobs WHERE type = 'probe' "
        "AND json_extract(payload, '$.asset_id') = ?",
        (asset_id,),
    )
    assert [int(row["priority"]) for row in waiting] == [WAITED_ON_PRIORITY]


def test_a_file_the_read_gave_up_on_says_why_and_is_not_read_again(
    client: TestClient, library: Library
) -> None:
    """Unread and unreadable are different answers. A file that probing has given up on is not
    queued again by every press of play; the player is told why instead."""
    sign_in(client)
    asset_id = _unread_row(client, library, mime="video/x-matroska")
    write(
        db_path(client),
        [
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'probe', 'not_decodable', 'this file could not be read as video', 0, 1)",
                (asset_id,),
            )
        ],
    )

    plan = _plan(client, asset_id, NO_MATROSKA)

    assert plan["route"] == "unread"
    assert "could not read this file" in str(plan["reason"])
    assert _reads_queued_for(client, asset_id) == 0


def test_an_unread_file_in_a_box_the_browser_opens_plays_as_it_is(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    asset_id = _unread_row(client, library, mime="video/mp4")

    plan = _plan(client, asset_id, MODERN)

    assert plan["route"] == "direct"
    assert str(plan["url"]).endswith("/stream")
    assert _reads_queued_for(client, asset_id) == 0, "a file that plays as it is needs no hurry"


def _copies_queued_for(client: TestClient, asset_id: str) -> int:
    """How many whole-file repackages are outstanding for this asset.

    Read out of the queue's own table rather than through `/api/jobs`, which does not publish the
    payload, and the payload is the whole question here, since every remux job looks alike from
    the outside. Asked with `idle_workers` in force, so the pool never claims: see the fixture.
    """
    rows = read(
        db_path(client),
        "SELECT COUNT(*) AS queued FROM jobs "
        "WHERE type = 'remux' AND state IN ('queued','running') "
        "AND json_extract(payload, '$.asset_id') = ?",
        (asset_id,),
    )
    return int(rows[0]["queued"])


def test_a_container_problem_converts_now_and_asks_for_a_copy_for_next_time(
    idle_workers: None, client: TestClient, library: Library
) -> None:
    """Tier 2 takes two viewings, deliberately.

    A repackaged copy is a whole file. It is not going to appear inside the moment somebody pressed
    play, so this viewing converts and a copy is queued behind it. The
    next viewing of the same file is free and full quality, for ever, which is the trade the tier
    exists to make.

    Both halves are asserted here because either alone is a passing test over a broken feature: a
    plan with no job queued never gets faster, and a job with no plan is a video that does not play.
    """
    sign_in(client)
    asset_id = library.id_of("hevc_mkv")

    plan = _plan(client, asset_id, NO_MATROSKA)

    assert plan["route"] == "transcode", "the first viewing cannot wait for a whole-file copy"
    assert _copies_queued_for(client, asset_id) == 1, (
        "no repackaged copy was asked for, so the second viewing would convert as well"
    )


def test_asking_twice_does_not_queue_two_copies_of_the_same_file(
    idle_workers: None, client: TestClient, library: Library
) -> None:
    """A whole file's worth of disk and reading, per press of play, is the failure to avoid."""
    sign_in(client)
    asset_id = library.id_of("hevc_mkv")

    for _ in range(3):
        _plan(client, asset_id, NO_MATROSKA)

    queued = _copies_queued_for(client, asset_id)
    assert queued == 1, f"{queued} copies of one file were queued"


def test_an_hdr_file_is_mapped_to_an_ordinary_picture_on_the_shipped_ffmpeg(
    client: TestClient, library: Library
) -> None:
    """The whole chain on a real ten-bit PQ file: the plan says the colours are being mapped,
    and the first segment the shipped ffmpeg renders is 8-bit BT.709 rather than a grey copy of
    the HDR values. A tone map that only appears in the command line proves nothing; this is the
    filter graph running."""
    sign_in(client)
    plan = _plan(client, library.id_of("hdr"), ANCIENT)
    assert plan["route"] == "transcode"
    assert "HDR" in str(plan["reason"])

    body = client.get(str(plan["url"])).text
    first = next(line for line in body.splitlines() if line.startswith("/api/") and ".m4s" in line)
    init = body.split('URI="')[1].split('"')[0]
    segment = client.get(first)
    assert segment.status_code == 200, segment.text
    with tempfile.TemporaryDirectory() as scratch:
        joined = Path(scratch) / "first.mp4"
        joined.write_bytes(client.get(init).content + segment.content)
        probed = subprocess.run(
            [
                str(Settings().ffprobe_path),
                "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=pix_fmt,color_transfer",
                "-of", "default=noprint_wrappers=1", str(joined),
            ],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
    assert "pix_fmt=yuv420p" in probed.stdout
    assert "color_transfer=bt709" in probed.stdout


def test_the_plan_is_carried_all_the_way_to_the_segment(
    client: TestClient, library: Library
) -> None:
    """The decision has to survive three separate requests, and nothing holds it between them.

    `/playback` decides, the playlist lists, the segments are fetched. The server keeps no state
    across the three, so unless the decision is written into the addresses it hands out, every
    later request re-derives it from nothing and lands on the default: a full-size transcode,
    which is neither of the two cheaper answers the policy may have picked.

    This walks the whole chain rather than asserting on any one link, because each link can be
    individually correct while the chain is broken.
    """
    sign_in(client)
    plan = _plan(client, library.id_of("hevc_mkv"), NO_MATROSKA)
    route = str(plan["route"])

    playlist_url = str(plan["url"])
    assert f"route={route}" in playlist_url, "the plan must be in the URL the player is handed"

    body = client.get(playlist_url).text
    segment_urls = [line for line in body.splitlines() if line.startswith("/api/")]
    assert segment_urls, "the playlist listed no segments"
    assert all(f"route={route}" in url for url in segment_urls), (
        "every segment URL must carry the plan, or the segment is built under a different one"
    )
    assert 'URI="' in body and f"route={route}" in body.split('URI="')[1].split('"')[0]


def test_segments_declare_the_duration_they_actually_contain(
    client: TestClient, library: Library
) -> None:
    """A playlist entry is a promise: this segment is this long, beginning here.

    A stream copy cannot be cut where it is told, whatever the boundaries are, because ffmpeg bounds
    a copy by decode timestamps while a boundary is a presentation one. That is why the copied tier
    is a whole-file repackage that is direct-played, and why there is only one arm to this test:
    every segment Sift produces is an encoded one.

    The converted tier keeps the promise because it forces a keyframe at every boundary, and this
    measures the segment rather than trusting it.

    **Measured against the playlist's OWN declared duration** rather than against the nominal
    segment length, so the last segment (short by design) is held to what it actually claims.
    """
    sign_in(client)
    asset_id, route = library.id_of("hevc"), "transcode"

    body = client.get(f"/api/assets/{asset_id}/hls/index.m3u8?route={route}").text
    assert f"route={route}" in body, "the playlist did not come back on the route asked for"
    declared = [
        float(line.removeprefix("#EXTINF:").rstrip(","))
        for line in body.splitlines()
        if line.startswith("#EXTINF:")
    ]
    urls = [line for line in body.splitlines() if line.startswith("/api/")]
    init = client.get(body.split('URI="')[1].split('"')[0]).content
    assert len(declared) == len(urls), "every segment must declare a duration"

    # Every segment, not a chosen one. The fixture has a four-second keyframe interval, so the
    # even-numbered segments land on a keyframe and are correct even when the alignment is wrong:
    # checking only one of those is how this test would pass while the bug it exists for is present.
    # It is the odd ones, starting in the middle of a group of pictures, that expose it.
    for index, (url, promised) in enumerate(zip(urls[:-1], declared, strict=False)):
        response = client.get(url)
        assert response.status_code == 200

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            handle.write(init)
            handle.write(response.content)
            probe_path = Path(handle.name)

        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(probe_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            duration = float(result.stdout.strip() or 0)
        finally:
            probe_path.unlink(missing_ok=True)

        assert duration == pytest.approx(promised, abs=0.35), (
            f"{route} segment {index}: the playlist promises {promised:.3f}s and it holds "
            f"{duration:.2f}s: segments that do not match their declared duration overlap, and "
            "the timeline handed to the player is nonsense"
        )


def test_a_capable_browser_direct_plays_the_hevc_file(client: TestClient, library: Library) -> None:
    """The headline. HEVC is not automatically the expensive path: it depends who is asking."""
    sign_in(client)
    plan = _plan(client, library.id_of("hevc"), MODERN)

    assert plan["route"] == "direct"


# --- the playlist ---------------------------------------------------------------------------------


def test_the_playlist_describes_every_segment(client: TestClient, library: Library) -> None:
    sign_in(client)
    asset_id = library.id_of("hevc")

    response = client.get(f"/api/assets/{asset_id}/hls/index.m3u8")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    body = response.text

    expected = policy.segment_count(CLIP_SECONDS * 1000)
    assert body.count("#EXTINF:") == expected
    assert "#EXT-X-ENDLIST" in body
    # VOD, so the player knows the list is complete and may seek anywhere immediately.
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in body
    assert tuning.INIT_SEGMENT_NAME in body


def test_the_playlist_is_never_cached_by_anything_shared(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    response = client.get(f"/api/assets/{library.id_of('hevc')}/hls/index.m3u8")
    assert response.headers["cache-control"] == "private, no-cache"


# --- segments -------------------------------------------------------------------------------------


def test_a_segment_is_produced_and_is_real_video(client: TestClient, library: Library) -> None:
    """Not merely a 200 with bytes in it: a fragment a player can actually decode."""
    sign_in(client)
    asset_id = library.id_of("hevc")

    response = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(tuning.SEGMENT_MIME)
    assert len(response.content) > 1000

    body = response.content
    # It carries frames: a movie fragment and its data.
    assert b"moof" in body[:512]
    assert b"mdat" in body

    # And it carries no track description. `moov` belongs in the init segment, once, and a segment
    # that repeats it is the self-initialising form, which plays in some players, is refused by
    # Safari's native HLS, and adds the whole header to every segment of every file.
    assert b"moov" not in body, "the header belongs in the init segment, not in every segment"


def test_the_initialisation_segment_is_served(client: TestClient, library: Library) -> None:
    sign_in(client)
    response = client.get(f"/api/assets/{library.id_of('hevc')}/hls/{tuning.INIT_SEGMENT_NAME}")

    assert response.status_code == 200
    assert b"ftyp" in response.content[:32], "an init segment leads with the file-type box"


def test_a_segment_read_is_heard_as_playing(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    played = Played(clock=lambda: 50.0)
    monkeypatch.setattr(_ROUTES, "PLAYED", played)
    sign_in(client)
    assert played.seconds_since() is None

    client.get(f"/api/assets/{library.id_of('hevc')}/hls/{tuning.INIT_SEGMENT_NAME}")

    assert played.seconds_since() == 0.0


def test_the_first_segment_arrives_inside_the_measured_budget(
    client: TestClient, library: Library
) -> None:
    """The startup-latency promise.

    2.5 s, covering the worst streamable file measured plus headroom for real modest
    hardware. The fixture here is far smaller than that corpus, so this has enormous margin
    in practice: what it guards against is a regression that makes startup pathological, such as
    the `-ss` ordering mistake below or an accidental full-file decode.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")

    started = time.monotonic()
    response = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < FIRST_SEGMENT_BUDGET_SECONDS, (
        f"the first segment took {elapsed:.2f}s against a {FIRST_SEGMENT_BUDGET_SECONDS}s budget"
    )


def test_a_second_request_for_the_same_segment_is_served_from_the_cache(
    client: TestClient, library: Library
) -> None:
    """Which is what makes seeking back cheap.

    An already-watched segment is a file that already exists, so returning to it costs a read
    rather than a fresh run of ffmpeg.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")
    url = f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}"

    first = client.get(url)
    started = time.monotonic()
    second = client.get(url)
    cached_time = time.monotonic() - started

    assert first.content == second.content
    assert cached_time < 0.5, "a cache hit should not be re-transcoding"


# --- seeking: the silent ten-second mistake ----------------------------------------------


def test_seeking_deep_into_a_file_costs_about_what_starting_at_zero_does(
    client: TestClient, library: Library
) -> None:
    """The regression test for a one-argument mistake.

    `-ss` before `-i` seeks the input; after `-i` it decodes the whole file from the beginning and
    throws away everything before the offset. **Both produce correct video**, so no test that
    checks the output would ever notice, and the wrong order costs several times the right one
    at 5% into a 30-minute file, growing linearly from there.

    This asserts the shape of that curve rather than an absolute number: the last segment must cost
    about what the first one does. Under the wrong ordering it costs proportionally more, and on a
    long file it costs minutes.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")
    last = policy.segment_count(CLIP_SECONDS * 1000) - 1
    assert last >= 2, "the fixture must be long enough for a deep seek to mean anything"

    started = time.monotonic()
    first = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")
    at_zero = time.monotonic() - started

    started = time.monotonic()
    deep = client.get(f"/api/assets/{asset_id}/hls/{last}{tuning.SEGMENT_SUFFIX}")
    at_depth = time.monotonic() - started

    assert first.status_code == 200
    assert deep.status_code == 200
    # A generous factor: what is being caught is linear-in-offset growth, not a small difference.
    # The floor stops a fast machine's timing noise from failing it.
    assert at_depth < max(at_zero * 4, 1.5), (
        f"seeking to the end took {at_depth:.2f}s against {at_zero:.2f}s at the start: "
        "this is what `-ss` after `-i` looks like"
    )


def test_a_mid_file_segment_holds_the_video_from_that_point(
    client: TestClient, library: Library
) -> None:
    """Transcoding starts at the playhead. Different segments are genuinely different video."""
    sign_in(client)
    asset_id = library.id_of("hevc")

    first = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")
    third = client.get(f"/api/assets/{asset_id}/hls/2{tuning.SEGMENT_SUFFIX}")

    assert first.status_code == 200
    assert third.status_code == 200
    assert first.content != third.content


# --- a repaired copy ------------------------------------------------------------------------------


def _install_repair(client: TestClient, asset_id: str, body: bytes) -> None:
    """Put a repaired copy in the cache, as the remux job would have."""
    relative = f"re/pa/{asset_id}-remux.mp4"
    repaired = Path(client.app.state.settings.cache_dir) / relative  # type: ignore[attr-defined]
    repaired.parent.mkdir(parents=True, exist_ok=True)
    repaired.write_bytes(body)
    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives"
                " (id, asset_id, kind, rel_cache_path, params, size_bytes, created_at)"
                " VALUES (?, ?, 'remux', ?, '{}', ?, 1)",
                (new_id(), asset_id, relative, len(body)),
            )
        ],
    )


def test_a_segment_is_cut_from_the_repaired_copy_when_there_is_one(
    client: TestClient, library: Library
) -> None:
    """Parity with direct play. A file too badly interleaved to seek is also expensive to cut
    segments out of, and a repair that only the direct path reads leaves the transcode path paying
    for a fault that has already been fixed.

    Written so that it can only pass one way: the original in the library is replaced with bytes no
    decoder can read, and the repaired copy is the real video. Reading the original produces a 503,
    reading the repair produces a fragment. Asserting on the fragment's *content* instead would
    pass either way, because both files hold the same picture.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")
    original = library.media / "clips" / "hevc-hevc.mp4"
    playable = original.read_bytes()
    original.write_bytes(b"not a video at all")
    _install_repair(client, asset_id, playable)

    response = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")

    assert response.status_code == 200, "the segment was cut from the unreadable original"
    assert b"moof" in response.content[:512]


def test_a_repaired_matroska_file_is_planned_from_the_copy_that_will_be_sent(
    client: TestClient, library: Library
) -> None:
    """The route follows the bytes, not the row.

    A browser that decodes HEVC but cannot read Matroska normally transcodes this file (the test
    above asserts exactly that). Once a repaired copy exists the file it will be sent is an MP4 it
    can read, so the expensive path is not needed and re-encoding it would be work for a problem
    that is not in what is being served.
    """
    sign_in(client)
    asset_id = library.id_of("hevc_mkv")
    _install_repair(client, asset_id, b"the plan never opens this")

    plan = _plan(client, asset_id, NO_MATROSKA)

    # Named `remux` rather than `direct`: it IS direct play, off the disk over ranges, and the
    # distinction worth keeping is that what is being played is a copy. The stats panel says
    # "Repackaged", and somebody looking at it should not be told the original was readable.
    assert plan["route"] == "remux"
    assert str(plan["url"]).endswith("/stream")


def test_a_file_with_no_repair_is_still_cut_from_the_original(
    client: TestClient, library: Library
) -> None:
    """Almost every file. The lookup must not change what happens to them, and the mirror of the
    test above, whose fixture would let a handler that read *only* repairs pass."""
    sign_in(client)

    response = client.get(f"/api/assets/{library.id_of('hevc')}/hls/0{tuning.SEGMENT_SUFFIX}")

    assert response.status_code == 200
    assert b"moof" in response.content[:512]


def test_a_thumbnail_is_not_mistaken_for_a_repaired_copy(
    client: TestClient, library: Library
) -> None:
    """The common shape: a file with derivatives, none of which is a repair.

    Almost every asset that has been through the pipeline carries a thumbnail and a sprite, so the
    lookup walks a non-empty list and finds nothing far more often than it walks an empty one. A
    lookup that took the first derivative it saw would hand ffmpeg a PNG here, and the answer would
    be a 503 rather than a segment.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")
    relative = f"th/um/{asset_id}-thumb.png"
    thumb = Path(client.app.state.settings.cache_dir) / relative  # type: ignore[attr-defined]
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"\x89PNG\r\n\x1a\n not a video")
    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives"
                " (id, asset_id, kind, rel_cache_path, params, size_bytes, created_at)"
                " VALUES (?, ?, 'thumb', ?, '{}', ?, 1)",
                (new_id(), asset_id, relative, thumb.stat().st_size),
            )
        ],
    )

    response = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")

    assert response.status_code == 200, "the thumbnail was handed to ffmpeg as the source"
    assert b"moof" in response.content[:512]


# --- what the URL is allowed to say ------------------------------------------------------------------


@pytest.mark.parametrize(
    "segment",
    [
        "9999.m4s",  # past the end of the file
        "notanumber.m4s",  # not an index
        "0.mp4",  # not the segment suffix
        "../../etc/passwd",  # the reason any of this is validated
        "0.m4s.m4s",
        # Characters Python calls digits and refuses to convert: the superscripts. `isdigit()` is
        # True for them and `int()` raises, so a name checked by characters rather than by
        # conversion would reach the conversion and come back a 500: an unhandled error a caller
        # could ask for by typing a URL. Written as escapes because source here is ASCII.
        "\u00b2.m4s",
        "\u00b9\u00b2\u00b3.m4s",
        # Arabic-Indic 3999, which `int()` really does read. It must still be refused for being past
        # the end of the file rather than by crashing on the way to that check.
        "\u0663\u0669\u0669\u0669.m4s",
        "-1.m4s",  # a sign parses; only the init segment is allowed under zero
    ],
)
def test_a_segment_name_that_is_not_one_is_refused(
    client: TestClient, library: Library, segment: str
) -> None:
    """This value comes off the URL and becomes part of a filename, so it is checked rather than
    trusted. Anything that is not a plain index or the one known init name is a 404."""
    sign_in(client)
    response = client.get(f"/api/assets/{library.id_of('hevc')}/hls/{segment}")
    assert response.status_code == 404


def test_a_crafted_height_cannot_ask_for_an_arbitrary_scale(
    client: TestClient, library: Library
) -> None:
    """The height rides in the query string, so it is validated rather than passed to ffmpeg.

    An unbounded value here would be a request to upscale a 320x240 clip to something enormous:
    a way to make the server do a great deal of work on request.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")

    response = client.get(
        f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}?height=99999&route=transcode"
    )

    # Served, because the height was ignored rather than obeyed.
    assert response.status_code == 200


@pytest.mark.parametrize(
    "height", ["\u00b2", "\u00b9\u00b2\u00b3", "not-a-number", "", "-1", "0", "1e9"]
)
def test_a_height_that_is_not_a_number_is_ignored_rather_than_raised_on(
    client: TestClient, library: Library, height: str
) -> None:
    """The same query value, spelled in ways that must not reach the conversion.

    `isdigit()` is True for a superscript digit and `int()` raises on one, so a height checked by
    characters rather than by conversion would answer 500, which any signed-in caller could ask
    for by typing a URL. Every one of these means "no height was chosen", and the segment is
    served at its own size.
    """
    sign_in(client)
    asset_id = library.id_of("hevc")

    response = client.get(
        f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}?height={height}&route=transcode"
    )

    assert response.status_code == 200


# --- the cap, which is the promise that makes any of this safe ------------------------------------


def test_a_long_session_never_pushes_the_cache_over_its_cap(
    client: TestClient, library: Library
) -> None:
    """The six-hour-scroll promise, exercised through the real endpoint with real transcodes.

    The cap is shrunk to a few segments' worth so that eviction genuinely happens within a test's
    runtime. What is being proved is that the bound holds while segments are actually being
    produced and evicted, rather than in the cache's own unit tests where nothing is on disk.
    """
    sign_in(client)
    cache = client.app.state.segment_cache  # type: ignore[attr-defined]
    asset_id = library.id_of("hevc")
    total = policy.segment_count(CLIP_SECONDS * 1000)

    # Shrink the cap to something a handful of segments will exceed.
    first = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")
    assert first.status_code == 200
    cache._max_bytes = max(1, cache.total_bytes * 2)

    for _round in range(3):
        for index in range(total):
            response = client.get(f"/api/assets/{asset_id}/hls/{index}{tuning.SEGMENT_SUFFIX}")
            assert response.status_code == 200
            assert cache.total_bytes <= cache.max_bytes

    on_disk = sum(
        child.stat().st_size
        for child in cache.path_for("").parent.iterdir()
        if child.is_file() and not child.name.startswith(".")
    )
    assert on_disk <= cache.max_bytes


def test_a_segment_larger_than_the_whole_cap_is_served_and_not_kept(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The oversized-segment bug, through the endpoint.

    A cap smaller than a single segment. The person watching still gets their video (serving and
    caching are different decisions), and the cache does not end up permanently over its limit
    holding something it can never make room for.

    The running application pushes the stored cap onto the cache every few seconds
    (`sift.wiring.lifespan.keep_the_settings_applied`), and a transcode can outlast one beat: the
    cap set here would be put back to the setting's gigabytes mid-request and the segment kept. So
    the push is held off this one cache for the length of the test.
    """
    sign_in(client)
    cache = client.app.state.segment_cache  # type: ignore[attr-defined]
    monkeypatch.setattr(cache, "resize", lambda max_bytes: False)
    cache._max_bytes = 1
    asset_id = library.id_of("hevc")

    response = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}")

    assert response.status_code == 200
    assert len(response.content) > 1
    assert cache.total_bytes <= cache.max_bytes
    assert len(cache) == 0


# --- the master playlist, over HTTP -------------------------------------------------------------


def test_the_master_playlist_is_served_and_lists_the_variants(
    client: TestClient, library: Library
) -> None:
    """Declared BEFORE the segment route, and that is load-bearing rather than tidy. Routes match
    in the order they are written, so a master playlist declared after `hls/{segment}` would be
    looked up as a segment called "master.m3u8", refused by the strict name check there, and
    answered with a 404 that reads like a missing file."""
    sign_in(client)
    answer = client.get(f"/api/assets/{library.id_of('h264')}/hls/master.m3u8")

    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    body = answer.text
    assert body.startswith("#EXTM3U")
    assert "#EXT-X-STREAM-INF:" in body


def test_a_segment_is_served_only_at_a_height_the_ladder_offers() -> None:
    """Taking any positive number up to the source would start a distinct transcode for each
    distinct one: a signed-in caller could widen the ladder to a thousand rungs and fill the cache
    with them. A height is honoured only where it is one of this file's own rungs."""
    import importlib
    from types import SimpleNamespace

    from sift.slices.player import policy

    plan_from_query = importlib.import_module("sift.slices.player.router")._plan_from_query
    asset = SimpleNamespace(width=1920, height=1080)
    offered = policy.rungs(1920, 1080)[0].height

    def asking(height: str) -> object:
        return SimpleNamespace(query_params={"route": "transcode", "height": height})

    assert plan_from_query(asking(str(offered)), asset).scale_height == offered
    assert plan_from_query(asking("123"), asset).scale_height is None
    assert plan_from_query(asking("1079"), asset).scale_height is None
    assert plan_from_query(asking(str(offered)), asset).route is policy.Route.TRANSCODE


def test_every_byte_answering_route_answers_head_as_it_answers_get(
    client: TestClient, library: Library
) -> None:
    """A player, a download manager and a proxy all ask HEAD before GET, so every one of these
    routes answers it rather than 405. The same headers, no body."""
    sign_in(client)
    asset_id = library.id_of("h264")

    playlist = f"/api/assets/{asset_id}/hls/index.m3u8?route=transcode"
    listed = client.get(playlist).text
    segment = next(line for line in listed.splitlines() if line.startswith("/api/"))

    for path in (
        f"/api/assets/{asset_id}/stream",
        f"/api/assets/{asset_id}/hls/master.m3u8",
        playlist,
        segment,
    ):
        head = client.head(path)
        got = client.get(path)
        assert head.status_code == got.status_code == 200, path
        assert head.headers["content-type"] == got.headers["content-type"], path
        assert head.content == b"", path


def test_the_master_playlist_is_never_cached_by_anything_shared(
    client: TestClient, library: Library
) -> None:
    """It is built for one viewer's permissions, so a shared cache holding it would hand somebody
    else a list of what they may not have."""
    sign_in(client)
    answer = client.get(f"/api/assets/{library.id_of('h264')}/hls/master.m3u8")

    assert "private" in answer.headers.get("cache-control", "")


def test_a_guest_shown_nothing_cannot_fetch_the_master_playlist(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "guest", who="outsider")

    refused = client.get(f"/api/assets/{library.id_of('h264')}/hls/master.m3u8")
    absent = client.get(f"/api/assets/{new_id()}/hls/master.m3u8")

    assert refused.status_code == 404
    assert refused.json() == absent.json(), "a refusal must not read differently from a miss"


def test_a_queue_that_refuses_the_copy_does_not_break_the_playback_answer(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fire and forget, deliberately. The answer being built says how to play the file NOW, and the
    copy is for next time, so a queue that will not take it costs nothing, because the fallback
    is the conversion that was going to happen anyway. A failure to enqueue must not escape the
    handler and leave the person with no plan at all.
    """
    app = cast("FastAPI", client.app)
    queue = part_of_app(app, QUEUE)

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("the queue is not taking anything")

    monkeypatch.setattr(queue, "is_live", refuse)
    sign_in(client)

    plan = _plan(client, library.id_of("hevc_mkv"), NO_MATROSKA)

    assert plan["route"] in {"transcode", "remux"}
