# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct play: HTTP range requests, straight off the disk, with no ffmpeg anywhere near it.

This is the majority path. Most files most browsers are asked to play, they can play, and the
entire cost of serving them should be reading bytes. The first test in this file counts ffmpeg
processes across a direct-play request and asserts there were none, because the day this route
starts transcoding is the day the cheap path silently stops being cheap, and nothing else would
notice.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.attention import Played
from sift.kernel.ids import new_id
from sift.slices.player import tuning
from sift.slices.player.tests.conftest import MODERN, Library, db_path, sign_in, write

pytestmark = [pytest.mark.integration]

_ROUTES = importlib.import_module("sift.slices.player.router")


#: The process listing, per platform. Both produce `pid parent name`, one process a line.
#:
#: **`ps` DOES NOT EXIST ON WINDOWS**, which is a platform Sift ships on.
#: `subprocess.run(check=False)` does not save this: a missing *executable* raises
#: `FileNotFoundError` before any exit code exists to ignore. So the failure would not be a message
#: about `ps`: it would be `[WinError 2] The system cannot find the file specified`, which reads
#: like the media fixture is missing rather than the tool.
#:
#: PowerShell's `Win32_Process` is the equivalent because it is the only listing that carries the
#: PARENT id. `tasklist` does not, and without the parent this cannot scope itself to its own
#: process tree, which is the whole reason the helper exists.
_LISTING = (
    [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | ForEach-Object "
        '{ "$($_.ProcessId) $($_.ParentProcessId) $($_.Name)" }',
    ]
    if sys.platform == "win32"
    else ["ps", "-o", "pid=,ppid=,comm=", "-e"]
)


def _own_ffmpeg_processes() -> int:
    """How many ffmpeg processes *this* test's own process tree has.

    Scoped to this process's descendants rather than to the whole machine, and that is not
    tidiness. The suite runs across every core, so a machine-wide count is really a count of
    whatever every other test happened to be doing at that instant: it would fail at random, and
    (far worse) it could pass at random while the thing it guards was broken.
    """
    try:
        listing = subprocess.run(_LISTING, capture_output=True, text=True, check=False)
    except OSError as error:  # pragma: no cover - the tool is missing entirely
        pytest.skip(f"no process listing on this platform: {error}")

    children: dict[int, list[int]] = {}
    names: dict[int, str] = {}
    for line in listing.stdout.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid, parent = int(parts[0]), int(parts[1])
        except ValueError:
            # A header row, or a name with a space in it that shifted the columns.
            continue
        name = parts[2]
        children.setdefault(parent, []).append(pid)
        names[pid] = name

    found = 0
    queue = [os.getpid()]
    while queue:
        current = queue.pop()
        if "ffmpeg" in names.get(current, ""):
            found += 1
        queue.extend(children.get(current, ()))
    return found


def test_a_playable_file_is_direct_played(client: TestClient, library: Library) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")

    response = client.post(f"/api/assets/{asset_id}/playback", json=MODERN)

    assert response.status_code == 200
    plan = response.json()
    assert plan["route"] == "direct"
    assert plan["url"].endswith("/stream")
    assert plan["streamable"] is True


@pytest.mark.parametrize("headers", [{}, {"range": "bytes=0-"}], ids=["whole", "range"])
@pytest.mark.parametrize(("kind", "heard"), [("video", True), ("image", False)])
def test_a_clip_read_is_heard_as_playing_and_a_picture_is_not(
    client: TestClient,
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    kind: str,
    heard: bool,
) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    write(db_path(client), [("UPDATE assets SET media_type = ? WHERE id = ?", (kind, asset_id))])
    played = Played(clock=lambda: 50.0)
    monkeypatch.setattr(_ROUTES, "PLAYED", played)

    assert client.get(f"/api/assets/{asset_id}/stream", headers=headers).status_code in (200, 206)
    assert (played.seconds_since() == 0.0) is heard


def test_direct_play_spawns_no_ffmpeg(client: TestClient, library: Library) -> None:
    """Direct play spawns nothing, asserted against the process table.

    Counting processes rather than mocking, because what is being claimed is about the machine and
    not about the code's intentions. A mock proves the function under test did not call the thing
    the test knew to mock; the process table proves nothing was started at all.
    """
    sign_in(client)
    asset_id = library.id_of("h264")

    before = _own_ffmpeg_processes()
    response = client.get(f"/api/assets/{asset_id}/stream")
    after = _own_ffmpeg_processes()

    assert response.status_code == 200
    assert after <= before, "direct play must never start a transcode"


def test_the_whole_file_comes_back_when_no_range_is_asked_for(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    source = next(library.media.glob("clips/h264-*.mp4"))

    response = client.get(f"/api/assets/{asset_id}/stream")

    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == source.read_bytes()


def test_a_range_request_comes_back_as_206_with_exactly_those_bytes(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    source = next(library.media.glob("clips/h264-*.mp4"))
    body = source.read_bytes()

    response = client.get(f"/api/assets/{asset_id}/stream", headers={"Range": "bytes=100-199"})

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-199/{len(body)}"
    assert response.headers["content-length"] == "100"
    assert response.content == body[100:200]


def test_a_mid_file_range_is_served_from_the_middle(client: TestClient, library: Library) -> None:
    """Seeking. A player dragging the scrubber asks for a range from the middle of the file."""
    sign_in(client)
    asset_id = library.id_of("h264")
    source = next(library.media.glob("clips/h264-*.mp4"))
    body = source.read_bytes()
    middle = len(body) // 2

    response = client.get(
        f"/api/assets/{asset_id}/stream", headers={"Range": f"bytes={middle}-{middle + 49}"}
    )

    assert response.status_code == 206
    assert response.content == body[middle : middle + 50]


def test_an_open_ended_range_runs_to_the_end_of_the_file(
    client: TestClient, library: Library
) -> None:
    """`bytes=N-` is what a player sends when it starts playing and does not know the length."""
    sign_in(client)
    asset_id = library.id_of("h264")
    source = next(library.media.glob("clips/h264-*.mp4"))
    body = source.read_bytes()
    start = len(body) - 300

    response = client.get(f"/api/assets/{asset_id}/stream", headers={"Range": f"bytes={start}-"})

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{len(body) - 1}/{len(body)}"
    assert response.content == body[start:]


def test_a_suffix_range_returns_the_last_bytes(client: TestClient, library: Library) -> None:
    """`bytes=-N` is what a player sends hunting for an index in a file that is not faststart."""
    sign_in(client)
    asset_id = library.id_of("h264")
    source = next(library.media.glob("clips/h264-*.mp4"))
    body = source.read_bytes()

    response = client.get(f"/api/assets/{asset_id}/stream", headers={"Range": "bytes=-128"})

    assert response.status_code == 206
    assert response.content == body[-128:]


def test_a_range_that_runs_past_the_end_is_clamped_rather_than_refused(
    client: TestClient, library: Library
) -> None:
    """Players routinely over-ask. The correct answer is what exists, not an error."""
    sign_in(client)
    asset_id = library.id_of("h264")
    source = next(library.media.glob("clips/h264-*.mp4"))
    size = source.stat().st_size

    response = client.get(
        f"/api/assets/{asset_id}/stream", headers={"Range": f"bytes=0-{size + 10_000}"}
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-{size - 1}/{size}"


def test_a_range_starting_past_the_end_is_refused_with_416(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    size = next(library.media.glob("clips/h264-*.mp4")).stat().st_size

    response = client.get(
        f"/api/assets/{asset_id}/stream", headers={"Range": f"bytes={size + 1}-{size + 100}"}
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{size}"


def test_a_malformed_range_gets_the_whole_file(client: TestClient, library: Library) -> None:
    """What the HTTP spec asks for: an unparseable Range is ignored, not an error.

    The whole file is always a correct answer to a bad range, and refusing would break clients
    that send something slightly odd.
    """
    sign_in(client)
    asset_id = library.id_of("h264")

    response = client.get(f"/api/assets/{asset_id}/stream", headers={"Range": "kilobytes=1-2"})

    assert response.status_code == 200


def test_streamed_bytes_are_never_cached_anywhere_but_the_asking_browser(
    client: TestClient, library: Library
) -> None:
    """`private` keeps it out of shared caches; `no-cache` forces revalidation.

    Without the second, re-locking the vault would leave concealed video readable from the local
    browser cache, on the same machine, by the next person to sit down at it, which is precisely
    who concealment is for.
    """
    sign_in(client)
    response = client.get(f"/api/assets/{library.id_of('h264')}/stream")
    assert response.headers["cache-control"] == "private, no-cache"


def test_a_file_whose_bytes_have_gone_reads_as_missing(
    client: TestClient, library: Library
) -> None:
    """The row says it is there and the disk disagrees: an unplugged drive, a deleted file."""
    sign_in(client)
    asset_id = library.id_of("h264")
    next(library.media.glob("clips/h264-*.mp4")).unlink()

    assert client.get(f"/api/assets/{asset_id}/stream").status_code == 404


def test_an_open_ended_range_is_answered_with_a_bounded_piece_of_the_file(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A player asks for "everything from here". Committing to all of it is what gets abandoned.

    The response says how much it carries and the player asks again, which it does anyway. Without
    this, one response commits the server to the rest of a video and the browser hangs up after a
    buffer's worth, leaving a read behind that nobody is waiting for.
    """
    monkeypatch.setattr(tuning, "MAX_OPEN_RANGE_BYTES", 1024)
    sign_in(client)
    asset_id = library.id_of("h264")

    response = client.get(f"/api/assets/{asset_id}/stream", headers={"Range": "bytes=0-"})

    assert response.status_code == 206
    assert int(response.headers["content-length"]) == 1024
    assert response.headers["content-range"].startswith("bytes 0-1023/")
    assert len(response.content) == 1024


def test_a_range_that_names_both_ends_is_answered_in_full(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the open-ended form is shortened. A range asked for exactly is answered exactly."""
    monkeypatch.setattr(tuning, "MAX_OPEN_RANGE_BYTES", 16)
    sign_in(client)

    response = client.get(
        f"/api/assets/{library.id_of('h264')}/stream", headers={"Range": "bytes=0-4095"}
    )

    assert int(response.headers["content-length"]) == 4096


def test_the_last_bytes_of_a_file_are_answered_in_full(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bytes=-500` names its own length. It ends in a digit, not a dash, and is not shortened."""
    monkeypatch.setattr(tuning, "MAX_OPEN_RANGE_BYTES", 16)
    sign_in(client)

    response = client.get(
        f"/api/assets/{library.id_of('h264')}/stream", headers={"Range": "bytes=-500"}
    )

    assert int(response.headers["content-length"]) == 500


# --- a repaired copy ----------------------------------------------------------------------------


def _repair(client: TestClient, asset_id: str, body: bytes) -> Path:
    """Put a repaired copy in the cache, as the remux job would have."""
    cache_dir = Path(client.app.state.settings.cache_dir)  # type: ignore[attr-defined]
    relative = f"re/pa/{asset_id}-remux.mp4"
    repaired = cache_dir / relative
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
    return repaired


def test_a_repaired_copy_is_served_instead_of_the_original(
    client: TestClient, library: Library
) -> None:
    """The whole point of building one. Without this the repair sits in the cache unread."""
    sign_in(client)
    asset_id = library.id_of("h264")
    body = b"repaired-bytes-standing-in-for-a-remuxed-file"
    _repair(client, asset_id, body)

    response = client.get(f"/api/assets/{asset_id}/stream")

    assert response.status_code == 200
    assert response.content == body


def test_a_repaired_copy_is_announced_as_mp4_whatever_the_original_was(
    client: TestClient, library: Library
) -> None:
    """A re-muxed Matroska file is an MP4. Sending the asset's own type would be a lie the browser
    acts on: it picks a demuxer from this."""
    sign_in(client)
    asset_id = library.id_of("hevc_mkv")
    _repair(client, asset_id, b"repaired")

    response = client.get(f"/api/assets/{asset_id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")


def test_a_range_request_reads_from_the_repaired_copy(client: TestClient, library: Library) -> None:
    """Seeking is the whole reason the repair exists, so the ranged path has to use it too."""
    sign_in(client)
    asset_id = library.id_of("h264")
    body = bytes(range(256)) * 4
    _repair(client, asset_id, body)

    response = client.get(f"/api/assets/{asset_id}/stream", headers={"Range": "bytes=10-19"})

    assert response.status_code == 206
    assert response.content == body[10:20]
    assert response.headers["content-range"] == f"bytes 10-19/{len(body)}"


def test_without_a_repaired_copy_the_original_is_still_served(
    client: TestClient, library: Library
) -> None:
    """Almost every file. The lookup must not change what happens to them."""
    sign_in(client)
    asset_id = library.id_of("h264")
    original = (library.media / "clips").glob("h264-*")

    response = client.get(f"/api/assets/{asset_id}/stream")

    assert response.status_code == 200
    assert response.content == next(original).read_bytes()
