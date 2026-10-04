# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the file is, for a caller that could actually open it.

A real drag out of the window hands the other application a path on the local disk: there is no
"stream it from over there" in the format the desktop uses. So the shell needs either a path it can
open or a name to save a copy under, and one route answers both.

The interesting half is the refusal. A path is not a secret (an admin already sees absolute paths
in the folder picker): it is simply MEANINGLESS to anybody else, and handing one to a machine that
has no such drive produces a drag of a file that is not there. That is a worse failure than saying
no, so the answer depends on where the caller is.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from sift.kernel.ids import new_id
from sift.slices.player.router import _machine_independent
from sift.slices.player.tests.conftest import Library, db_path, share, sign_in, write

pytestmark = [pytest.mark.integration]

NEVER_EXISTED = "01HX0000000000000000000099"


@pytest.fixture
def client(app: FastAPI, request: pytest.FixtureRequest) -> Iterator[TestClient]:
    """Overrides the slice's own client so the caller has an ADDRESS.

    The default one reports itself as `testclient`, which is nowhere, so every request through it
    takes the remote branch and the local one could never be reached. Loopback unless a test asks
    for somewhere else, and there is only ever one client, because entering a second one on the
    same application starts it twice.
    """
    host = getattr(request, "param", "127.0.0.1")
    with TestClient(app, client=(host, 51234)) as running:
        yield running


def test_a_caller_on_this_machine_is_told_where_the_file_is(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    answer = client.get(f"/api/assets/{library.id_of('h264')}/local-file")

    assert answer.status_code == 200
    body = answer.json()
    assert Path(body["path"]).is_file()
    assert body["filename"] == Path(body["path"]).name
    assert body["size_bytes"] == Path(body["path"]).stat().st_size


@pytest.mark.parametrize("client", ["198.51.100.7"], indirect=True)
def test_a_caller_on_another_machine_is_told_the_name_and_not_the_path(
    client: TestClient, library: Library
) -> None:
    """It is what makes the shell fetch a copy instead of opening the file in place, and the name
    is still answered because it is what that copy gets called."""
    sign_in(client)
    answer = client.get(f"/api/assets/{library.id_of('h264')}/local-file")

    assert answer.status_code == 200
    body = answer.json()
    assert body["path"] is None
    assert body["filename"].endswith(".mp4")
    assert body["size_bytes"] > 0


def test_a_proxy_in_front_makes_even_a_loopback_caller_remote(
    client: TestClient, library: Library
) -> None:
    """With a proxy in front, the connection Sift sees is the proxy's own, so the loopback
    address belongs to the proxy and proves nothing about who is really calling."""
    sign_in(client)
    answer = client.get(
        f"/api/assets/{library.id_of('h264')}/local-file",
        headers={"x-forwarded-for": "198.51.100.7"},
    )

    assert answer.status_code == 200
    assert answer.json()["path"] is None


def test_an_asset_on_a_drive_that_is_not_plugged_in_reads_as_a_miss(
    client: TestClient, library: Library
) -> None:
    """A file can sit in several places and some of them may be on a drive nobody has connected.
    There are no bytes to hand over and no path worth dragging, and it is answered the same way a
    made-up id is, because the caller cannot tell those apart and should not."""
    sign_in(client)
    unplugged = new_id()
    write(
        db_path(client),
        [
            (
                """
                INSERT INTO assets (id, identity, media_type, mime, original_filename, added_at)
                VALUES (?, ?, 'video', 'video/mp4', 'away.mp4', 0)
                """,
                (unplugged, f"digest-{unplugged}"),
            ),
            (
                """
                INSERT INTO asset_locations
                    (id, asset_id, root_id, folder_id, rel_path, filename, status,
                     first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, 'clips/away.mp4', 'away.mp4', 'missing', 0, 0)
                """,
                (new_id(), unplugged, library.root, library.folder),
            ),
        ],
    )

    gone = client.get(f"/api/assets/{unplugged}/local-file")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/local-file")

    assert gone.status_code == 404
    assert gone.json() == absent.json(), (
        "a file that has gone must not read differently from a miss"
    )


def test_a_guest_shown_nothing_cannot_ask_where_a_file_is(
    client: TestClient, library: Library
) -> None:
    """The authority is exactly the authority to watch the thing, applied inside the query rather
    than after it."""
    sign_in(client, "guest", who="outsider")

    refused = client.get(f"/api/assets/{library.id_of('h264')}/local-file")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/local-file")

    assert refused.status_code == 404
    assert refused.json() == absent.json()


def test_a_guest_shown_a_file_is_never_told_where_it_lives(
    client: TestClient, library: Library
) -> None:
    """A guest may drag what they may watch, and the drag is a copy: where the library lives, on
    this device or on the network, is not theirs to be told."""
    guest = sign_in(client, "guest", who="shown")
    share(client, library.id_of("h264"), guest)

    answer = client.get(f"/api/assets/{library.id_of('h264')}/local-file")

    assert answer.status_code == 200
    assert (answer.json()["path"], answer.json()["shared_path"]) == (None, None)
    assert answer.json()["filename"]


# --- the file on a share both machines can see --------------------------------------------------
#
# `shared_path` is what lets a Sift on a second computer hand a video straight to another
# application instead of copying it across the network first. Everything worth testing here is
# about what does NOT qualify: a path that means a different file on the machine that receives it
# would produce a confident drag of somebody else's video, which is the worst thing this can do.


def test_a_unc_path_is_offered_because_it_means_the_same_file_anywhere() -> None:
    assert _machine_independent(Path(r"\\nas\media\clips\a.mp4")) == r"\\nas\media\clips\a.mp4"


def test_a_drive_letter_is_never_offered() -> None:
    r"""`D:\Media\a.mp4` is a different file on every computer that has a D: drive."""
    assert _machine_independent(Path(r"D:\Media\a.mp4")) is None
    assert _machine_independent(Path("/srv/media/a.mp4")) is None


def test_the_two_prefixes_that_start_the_same_way_and_mean_the_opposite() -> None:
    r"""`\\?\` and `\\.\` open with the same two characters and name a LOCAL device."""
    assert _machine_independent(Path(r"\\?\C:\Media\a.mp4")) is None
    assert _machine_independent(Path(r"\\.\PhysicalDrive0")) is None


@pytest.mark.parametrize("client", ["198.51.100.7"], indirect=True)
def test_a_library_on_a_local_disk_offers_no_share_to_another_machine(
    client: TestClient, library: Library
) -> None:
    """The safety property, through the route. A local library has nothing to share, and answering
    a path anyway is how the second machine drags a file of its own by the same name."""
    sign_in(client)
    answer = client.get(f"/api/assets/{library.id_of('h264')}/local-file")

    assert answer.status_code == 200
    assert answer.json()["shared_path"] is None


# --- a file that says where it was made never leaves as it is ------------------------------------


def _located_jpeg(path: Path) -> bytes:
    """A small JPEG written over a clip's file, with an invented place in it."""
    exif = Image.Exif()
    exif[0x0110] = "Model Q"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (48.0, 51.0, 29.17)
    Image.new("RGB", (16, 12), (90, 40, 200)).save(path, format="JPEG", exif=exif)
    return path.read_bytes()


def _the_file(client: TestClient, asset_id: str) -> Path:
    return Path(client.get(f"/api/assets/{asset_id}/local-file").json()["path"])


def test_a_file_holding_a_place_is_dragged_as_the_copy_without_it_and_never_by_its_path(
    client: TestClient, library: Library
) -> None:
    """Handed the original's path, a drag into a chat window would carry where the picture was
    taken. No path is answered for it, and what the shell fetches instead has no place in it, is
    the size the answer said, and leaves the original as it was."""
    sign_in(client)
    asset_id = library.id_of("h264")
    original = _the_file(client, asset_id)
    before = _located_jpeg(original)

    answer = client.get(f"/api/assets/{asset_id}/local-file").json()
    sent = client.get(f"/api/assets/{asset_id}/outgoing")

    assert answer["holds_a_place"] is True
    assert answer["path"] is None and answer["shared_path"] is None
    assert sent.status_code == 200
    assert answer["size_bytes"] == len(sent.content)
    with Image.open(io.BytesIO(sent.content)) as picture:
        assert 0x8825 not in picture.getexif()
        assert picture.getexif()[0x0110] == "Model Q"
    assert original.read_bytes() == before


def test_a_file_with_no_place_is_answered_and_sent_as_it_is(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    original = _the_file(client, asset_id)

    answer = client.get(f"/api/assets/{asset_id}/local-file").json()
    sent = client.get(f"/api/assets/{asset_id}/outgoing")

    assert answer["holds_a_place"] is False and answer["path"] == str(original)
    assert sent.content == original.read_bytes()
    asked = client.head(f"/api/assets/{asset_id}/outgoing")
    assert asked.status_code == 200 and asked.content == b""


def test_a_file_gone_from_its_folder_is_a_miss_to_the_drag_and_to_the_copy(
    client: TestClient, library: Library
) -> None:
    """The row is still there and the file is not: neither the shell's drag nor the copy it fetches
    is answered with anything but the 404 a missing file reads as everywhere else."""
    sign_in(client)
    asset_id = library.id_of("h264")
    _the_file(client, asset_id).unlink()

    assert client.get(f"/api/assets/{asset_id}/local-file").status_code == 404
    assert client.get(f"/api/assets/{asset_id}/outgoing").status_code == 404
    assert client.head(f"/api/assets/{asset_id}/outgoing").status_code == 404


def test_a_file_whose_place_cannot_be_taken_out_is_refused_by_the_asking_and_the_taking(
    client: TestClient, library: Library
) -> None:
    """The HEAD a client asks first says 422 where the GET would, so the screen can say Sift's own
    sentence rather than leave a browser download failing with nothing to say why."""
    sign_in(client)
    asset_id = library.id_of("h264")
    original = _the_file(client, asset_id)
    data = bytearray(_located_jpeg(original))
    at = data.index(b"Exif\x00\x00") - 2
    data[at : at + 2] = b"\xff\xf0"
    original.write_bytes(bytes(data))

    assert client.head(f"/api/assets/{asset_id}/outgoing").status_code == 422
    assert client.get(f"/api/assets/{asset_id}/outgoing").status_code == 422
    assert client.get(f"/api/assets/{asset_id}/local-file").status_code == 422
