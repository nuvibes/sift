# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap's deck as a video, its longest session's path, and Keep as Collections, over HTTP.

A small application carrying only these routers and the database, with the sign-in and the
cross-site check stood in for, as in `test_recaps_api`.
"""

from __future__ import annotations

import io
import shutil
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.config import Settings
from sift.kernel.media import Encoder
from sift.kernel.wiring import provide
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.insights import (
    keep_router,
    recaps_draw,
    session_router,
    store,
    video,
    video_router,
)
from sift.slices.insights.capture import THINGS
from sift.slices.insights.recaps_models import CardKind, KeptCard, Recipe
from sift.slices.insights.tests.conftest import World
from sift.slices.insights.tests.test_recaps import HER, made, reader, row, september
from sift.slices.insights.tests.test_recaps_year import a_year, year_recap

pytestmark = pytest.mark.integration

FILE_A, FILE_B, FILE_C = (f"01HX00000000000000000000F{n}" for n in "ABC")


@pytest.fixture
def app(world: World, access: Repository) -> FastAPI:
    app = FastAPI()
    provide(app, wiring.DATABASE, world.db)
    provide(app, wiring.ACCESS, access)
    provide(app, wiring.SETTINGS, Settings())
    for module in (video_router, session_router, keep_router):
        app.include_router(module.router, prefix="/api")
    app.dependency_overrides[csrf_protect] = lambda: None
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as running:
        yield running


def as_(app: FastAPI, viewer: Viewer) -> None:
    app.dependency_overrides[current_viewer] = lambda: viewer
    app.dependency_overrides[require_admin] = lambda: viewer


def jpeg(shade: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 112), (shade, 40, 90)).save(out, "JPEG")
    return out.getvalue()


def film(*frames: tuple[bytes, int]) -> bytes:
    return b"".join(struct.pack(">IH", len(one), held) + one for one, held in frames)


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


# --- the video ------------------------------------------------------------------------------------


async def test_the_frames_are_read_with_how_long_each_is_held() -> None:
    body = film((jpeg(10), 3), (jpeg(200), 1))
    # Split anywhere: a frame's head and its picture may arrive in different pieces.
    frames = await video.frames_of(chunks(body[:3], body[3:40], body[40:]))
    assert [one.held for one in frames] == [3, 1]
    assert len(video.piped(frames)) == 3 * len(frames[0].picture) + len(frames[1].picture)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        film((b"not a picture", 1)),
        film((jpeg(10), 0)),
        film((jpeg(10), 1))[:-4],
        film((jpeg(10), video.MOST_FRAMES + 1)),
    ],
)
async def test_what_is_not_a_film_is_refused(body: bytes) -> None:
    with pytest.raises(video.NotAFilm):
        await video.frames_of(chunks(body))


async def test_a_film_past_its_size_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(video, "MOST_BYTES", 100)
    with pytest.raises(video.NotAFilm, match="too large"):
        await video.frames_of(chunks(film((jpeg(10), 1))))


@pytest.mark.parametrize(
    ("encoder", "expected"),
    [
        (Encoder.CPU, ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast"]),
        (Encoder.NVENC, ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-preset", "p4"]),
        (Encoder.QSV, ["-vf", "format=nv12", "-c:v", "h264_qsv", "-global_quality"]),
        (Encoder.VAAPI, ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp"]),
    ],
)
def test_each_encoder_takes_the_pictures_on_its_input(
    encoder: Encoder, expected: list[str]
) -> None:
    argv = video.encode_args("ffmpeg", Path("out.mp4"), encoder, "/dev/dri/renderD128")
    joined = " ".join(argv)
    assert " ".join(expected) in joined
    assert "-f image2pipe -framerate 30 -c:v mjpeg -i -" in joined
    assert argv[-4:] == ["+faststart", "-an", "out.mp4"][-3:] or argv[-1] == "out.mp4"
    assert "-movflags +faststart" in joined
    assert ("-vaapi_device" in argv) == (encoder is Encoder.VAAPI)


def test_vaapi_without_a_render_node_is_refused() -> None:
    with pytest.raises(ValueError, match="render node"):
        video.encode_args("ffmpeg", Path("out.mp4"), Encoder.VAAPI)


async def test_a_week_or_somebody_elses_recap_is_not_filmed(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    week = await store.write_recap(world.db, world.user, "week:2026-W39", "[]")
    as_(app, reader(world, unlocked=True))
    answer = await client.post(
        f"/api/insights/recaps/{week.id}/video", files={"frames": ("film", b"")}
    )
    assert answer.status_code == 404
    month = await store.write_recap(world.db, world.user, "month:2026-09", "[]")
    other = Viewer(id="u-other", role=reader(world, unlocked=True).role)
    as_(app, other)
    answer = await client.post(
        f"/api/insights/recaps/{month.id}/video", files={"frames": ("film", b"")}
    )
    assert answer.status_code == 404
    as_(app, reader(world, unlocked=True))
    answer = await client.post(
        f"/api/insights/recaps/{month.id}/video", files={"frames": ("film", b"junk")}
    )
    assert answer.status_code == 400 and "frames" in answer.json()["detail"]


@pytest.mark.skipif(not shutil.which(Settings().ffmpeg_path), reason="no ffmpeg here")
async def test_the_deck_is_encoded_as_an_mp4(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    month = await store.write_recap(world.db, world.user, "month:2026-09", "[]")
    as_(app, reader(world, unlocked=True))
    answer = await client.post(
        f"/api/insights/recaps/{month.id}/video",
        files={"frames": ("film", film((jpeg(10), 3), (jpeg(220), 2)))},
    )
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "video/mp4"
    assert 'filename="recap-month-2026-09.mp4"' in answer.headers["content-disposition"]
    # An MP4 with its index at the front: `ftyp`, then `moov` before the pictures (`mdat`).
    assert answer.content[4:8] == b"ftyp"
    assert answer.content.index(b"moov") < answer.content.index(b"mdat")


async def test_a_refused_encode_is_said_in_words(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    month = await store.write_recap(world.db, world.user, "month:2026-09", "[]")
    app.dependency_overrides[wiring.settings] = lambda: Settings(ffmpeg_path="no-such-ffmpeg")
    as_(app, reader(world, unlocked=True))
    answer = await client.post(
        f"/api/insights/recaps/{month.id}/video", files={"frames": ("film", film((jpeg(1), 1)))}
    )
    assert (
        answer.status_code == 500 and answer.json()["detail"] == "Sift couldn't encode the video."
    )


# --- how one session went -------------------------------------------------------------------------


async def visits(world: World, session: str, pages: list[tuple[str, str, int]]) -> None:
    await world.run(
        "INSERT INTO app_sessions (id, user_id, device_id, client_kind, started_at_ms, last_at_ms)"
        " VALUES (?, ?, 'd1', 'browser', 1000, 9000000)",
        (session, world.user),
    )
    for n, (place, ref, hidden) in enumerate(pages):
        await world.run(
            "INSERT INTO page_visits (id, user_id, app_session_id, place, ref, hidden,"
            " opened_at_ms, last_at_ms, front_ms, device_id, client_kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'd1', 'browser')",
            (f"v{n:03d}", world.user, session, place, ref, hidden, 1000 + n * 60_000, 2000),
        )


async def test_the_session_is_drawn_as_its_pages(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    await september(world)
    await row(world, date(2026, 9, 4), "session_ms:session", "s1", 90 * 60_000)
    await row(world, date(2026, 9, 4), "session_pages:session", "s1", 6)
    await visits(
        world,
        "s1",
        [
            ("wall", "library", 0),
            ("wall", "library", 0),
            ("person", HER, 0),
            ("wall", "hidden", 0),
            ("settings", "playback", 0),
            ("person", HER, 1),
            ("insights", "", 0),
        ],
    )
    recap_id = await made(world)
    as_(app, reader(world, unlocked=True))
    answer = await client.get(f"/api/insights/recaps/{recap_id}/session")
    assert answer.status_code == 200, answer.text
    steps = answer.json()["steps"]
    # The page repeated is one step; Hidden and the visit written while hidden are left out.
    assert [(one["piece"]["text"], one["piece"]["href"]) for one in steps] == [
        ("Browse", "/browse"),
        ("Elina Sorrel", None),
        ("Settings", "/settings/playback"),
        ("Insights", "/insights"),
    ]
    assert [one["after_ms"] for one in steps] == [0, 120_000, 240_000, 360_000]
    assert steps[1]["piece"]["kind"] == "person" and steps[1]["piece"]["id"] == HER


async def test_a_long_session_draws_its_first_pages_and_its_last(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    await september(world)
    await row(world, date(2026, 9, 4), "session_ms:session", "s1", 90 * 60_000)
    walls = [
        "library",
        "favorites",
        "recent",
        "loops",
        "start",
        "theater",
        "people",
        "tags",
        "sites",
    ]
    await visits(world, "s1", [("wall", one, 0) for one in walls] + [("organize", "", 0)])
    as_(app, reader(world, unlocked=True))
    body = (await client.get(f"/api/insights/recaps/{await made(world)}/session")).json()
    assert [one["piece"]["text"] for one in body["steps"]] == [
        "Browse",
        "Favorites",
        "Recent",
        "Loops",
        "Start",
        "Sites",
        "Organize",
    ]
    assert body["more"] == 3


async def test_a_recap_with_no_session_has_no_path(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    await september(world)
    as_(app, reader(world, unlocked=True))
    answer = await client.get(f"/api/insights/recaps/{await made(world)}/session")
    assert answer.status_code == 404


# --- Keep as Collections --------------------------------------------------------------------------


async def files(world: World, *, hidden: str | None = None) -> None:
    for asset in (FILE_A, FILE_B, FILE_C):
        await world.run(
            "INSERT INTO assets (id, identity, media_type, duration_ms, added_at, title)"
            " VALUES (?, ?, 'video', 1000, 1, ?)",
            (asset, "identity-" + asset, asset[-1].lower() + ".mp4"),
        )
        if asset == FILE_B:
            await world.run(
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (FILE_B, HER)
            )
        await world.set_hidden(asset, asset == hidden)
    june = date(2025, 6, 5)
    await row(world, june, "sittings:file", f"video:{FILE_A}", 90)
    await row(world, june, "sittings:file", f"video:{FILE_B}", 70)
    await row(world, june, "sittings:file", f"video:{FILE_C}", 20)
    await row(world, june, "rediscovered:file", FILE_C, 300)


async def test_a_year_offers_its_collections_once(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    harbour, lantern = await a_year(world)
    await files(world)
    recap_id = await year_recap(world)
    as_(app, reader(world, unlocked=True))
    sheet = (await client.get(f"/api/insights/recaps/{recap_id}/keep")).json()["lists"]
    assert [(one["name"], one["asset_ids"], one["ticked"], one["kept"]) for one in sheet] == [
        ("Your 2025, most viewed", [FILE_A, FILE_B, FILE_C, harbour, lantern], True, None),
        ("People of 2025", [FILE_B], True, None),
        ("Rediscoveries of 2025", [FILE_C], True, None),
    ]
    await world.run(
        "INSERT INTO collections (id, name, name_sort, owner_id, created_at, created_by_kind,"
        " created_by_user_id) VALUES ('01HX0000000000000000000C01', 'People of 2025',"
        " 'people of 2025', ?, 1, 'user', ?)",
        (world.user, world.user),
    )
    again = (await client.get(f"/api/insights/recaps/{recap_id}/keep")).json()["lists"]
    assert [one["kept"] for one in again] == [None, "01HX0000000000000000000C01", None]


async def test_a_locked_reader_is_never_offered_a_hidden_file(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    await a_year(world)
    await files(world, hidden=FILE_A)
    as_(app, reader(world, unlocked=False))
    sheet = (await client.get(f"/api/insights/recaps/{await year_recap(world)}/keep")).json()
    assert sheet["lists"][0]["asset_ids"][:2] == [FILE_B, FILE_C]
    assert all(FILE_A not in one["asset_ids"] for one in sheet["lists"])


async def test_only_a_year_is_kept_as_collections(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    await september(world)
    as_(app, reader(world, unlocked=True))
    answer = await client.get(f"/api/insights/recaps/{await made(world)}/keep")
    assert answer.status_code == 404


async def test_a_film_is_encoded_on_the_graphics_card_where_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The card's encoder is asked through the accelerator, VAAPI given the render node, and the
    work folder is gone after."""
    asked: list[list[str]] = []

    async def encoded(argv: list[str], **_kwargs: object) -> None:
        asked.append(argv)
        Path(argv[-1]).write_bytes(b"film on the card")

    class Card:
        async def run(
            self, attempt: Callable[[Encoder, tuple[str, ...]], Awaitable[bytes]]
        ) -> bytes:
            return await attempt(Encoder.VAAPI, ())

    monkeypatch.setattr(video, "run", encoded)

    film = await video.encode(
        [video.Frame(jpeg(10), 2)],
        ffmpeg="ffmpeg",
        accelerator=Card(),  # type: ignore[arg-type]
        device="/dev/dri/renderD128",
    )

    assert film == b"film on the card"
    (argv,) = asked
    assert argv[argv.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
    assert not Path(argv[-1]).parent.exists()


async def test_a_keep_sheet_with_nobody_names_no_file() -> None:
    assert await keep_router._people_files(None, [], ["F1"]) == []  # type: ignore[arg-type]


async def test_a_page_of_an_unknown_place_or_a_nameless_thing_is_not_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nameless(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(name="")

    monkeypatch.setitem(THINGS, "person", nameless)

    assert session_router._place("nowhere", "x") is None
    assert await session_router._piece(None, None, "person", "P1") is None  # type: ignore[arg-type]


def test_the_session_a_recap_is_about_is_read_from_its_session_card_alone() -> None:
    def body(*cards: KeptCard) -> str:
        return recaps_draw._CARDS.dump_json(list(cards)).decode()

    def card(kind: CardKind, *sources: str) -> KeptCard:
        return KeptCard(
            id=kind, kind=kind, statement=[], recipe=Recipe(sources=dict.fromkeys(sources, 1))
        )

    assert (
        session_router.session_of(body(card("session", "sittings|", "session_ms:session|S1")))
        == "S1"
    )
    assert (
        session_router.session_of(
            body(card("session", "sittings|"), card("headline", "session_ms:session|S2"))
        )
        is None
    )
