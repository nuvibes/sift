# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which download tools Sift runs, their versions, and the pressed check for a newer yt-dlp.

Who may call the two routes is the authorization matrix's business (tests/gates). What is pinned
here is what they SAY: the version read out of each tool, the engine yt-dlp found told apart from
none and from not knowing, the shipped copy told apart from the machine's own, and a newer release
decided by the calendar rather than by the letters of the tag.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aiohttp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import chromaprint
from sift.kernel.access import Role, Viewer
from sift.kernel.subprocess import SubprocessError, SubprocessResult
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.download import tools

TUNING = Path(tools.__file__).resolve().parent / "sources" / "tuning.py"

#: What the vendored yt-dlp prints under `-v` with no address, cut to the lines that matter.
_HEADER = (
    "[debug] yt-dlp version stable@2026.08.19 from yt-dlp/yt-dlp [594bd50c2] (win_exe)\n"
    "[debug] exe versions: ffmpeg n7.1.5-16-g9a4bb2c579-20260816 (setts)\n"
    "[debug] JS runtimes: quickjs-ng-0.17.0\n"
    "[debug] Proxy map: {}\n"
)


# --- reading what a tool said -----------------------------------------------------------------


def test_the_engine_is_read_as_its_name_and_its_version() -> None:
    assert tools.read_engine(_HEADER) == ("QuickJS-NG", "0.17.0")


def test_the_original_quickjs_is_not_mistaken_for_the_ng_build() -> None:
    """The longer name is tried first. Read the other way round, `quickjs-ng-0.17.0` would be the
    original QuickJS at version `ng-0.17.0`, and the image's engine and the Windows one would be
    indistinguishable on the screen."""
    said = "[debug] JS runtimes: quickjs-2025-04-26\n"

    assert tools.read_engine(said) == ("QuickJS", "2025-04-26")


def test_no_engine_and_an_unknown_engine_are_different_answers() -> None:
    """ "none" is yt-dlp saying it looked and found nothing: a fact about the install, and the one
    that costs YouTube formats. A yt-dlp that never said is not the same fact and is not shown as it."""
    assert tools.read_engine("[debug] JS runtimes: none\n") == ("none", None)
    assert tools.read_engine("[debug] Proxy map: {}\n") == ("unknown", None)
    assert tools.read_engine(None) == ("unknown", None)


def test_the_ffmpeg_version_is_the_word_after_version() -> None:
    banner = "ffmpeg version n7.1.5-16-g9a4bb2c579-20260816 Copyright (c) 2000-2026 the FFmpeg developers"

    assert tools.ffmpeg_version(banner) == "n7.1.5-16-g9a4bb2c579-20260816"
    # What the fingerprinting reader keeps when ffmpeg would not answer at all.
    assert tools.ffmpeg_version("ffmpeg") is None


@pytest.mark.parametrize(
    ("latest", "running", "newer"),
    [
        ("2026.09.20", "2026.08.19", True),
        ("2026.08.19", "2026.08.19", False),
        # One release spelled two ways. A comparison of the text calls this newer.
        ("2026.8.19", "2026.08.19", False),
        # Text order says 2026.10.01 sorts before 2026.9.30. The calendar does not.
        ("2026.10.01", "2026.9.30", True),
        ("2026.07.01", "2026.08.19", False),
        (None, "2026.08.19", False),
        ("2026.09.20", None, False),
        ("nightly", "2026.08.19", False),
    ],
)
def test_newer_is_decided_by_the_calendar(
    latest: str | None, running: str | None, newer: bool
) -> None:
    assert tools.is_newer(latest, running) is newer


# --- the downloaders are the shipped copies ---------------------------------------------------


def _bound(name: str) -> ast.expr:
    tree = ast.parse(TUNING.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    raise AssertionError(f"tuning.py no longer binds {name}")


@pytest.mark.parametrize(
    ("constant", "tool"),
    [
        ("YTDLP_BINARY", "yt-dlp"),
        ("GALLERYDL_BINARY", "gallery-dl"),
        # The tunnel client's is the kernel's: `kernel/tests/test_tunnels_process.py`.
    ],
)
def test_every_downloader_is_found_through_the_vendored_tool_search(
    constant: str, tool: str
) -> None:
    """The two downloaders are the shipped copies, not bare names, so an install never runs
    whatever yt-dlp the machine has, which may be months stale. Read from the source rather than
    the value, because on a machine that has not fetched the tools the two answers are the same
    string."""
    value = _bound(constant)

    assert isinstance(value, ast.Call), f"{constant} is not resolved through vendored_tool()"
    assert isinstance(value.func, ast.Name) and value.func.id == "vendored_tool"
    assert [ast.literal_eval(arg) for arg in value.args] == [tool]


# --- the routes -------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The two routes on their own, with the tools answering from a script.

    `run` is replaced where this module looks it up, so no real tool is started; the kept answer is
    dropped before and after so one test's tools are never another's.
    """

    async def fake_run(argv: list[str], *, time_limit: float) -> SubprocessResult:
        del time_limit
        if argv[1] == "-v":
            return SubprocessResult(returncode=2, stdout=b"", stderr=_HEADER.encode())
        # By name rather than by path: the path is the vendored copy's on a machine that fetched
        # the tools and the bare name on one that did not, and both must answer here.
        said = b"1.32.13\n" if "gallery-dl" in argv[0] else b"2026.08.19\n"
        return SubprocessResult(returncode=0, stdout=said, stderr=b"")

    async def fake_ffmpeg(_settings: object) -> str:
        return "ffmpeg version n7.1.5-16-g9a4bb2c579-20260816 Copyright (c) 2000-2026"

    monkeypatch.setattr(tools, "run", fake_run)
    monkeypatch.setattr(chromaprint, "tool_version", fake_ffmpeg)
    tools.MEASURED.forget()

    app = FastAPI()
    app.include_router(tools.router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: Viewer(id="admin", role=Role.ADMIN)
    app.dependency_overrides[csrf_protect] = lambda: None
    with TestClient(app) as started:
        yield started
    tools.MEASURED.forget()


def test_every_tool_is_listed_with_the_version_it_gave(client: TestClient) -> None:
    listed = {one["key"]: one for one in client.get("/api/download-tools").json()["tools"]}

    assert list(listed) == ["ffmpeg", "yt-dlp", "gallery-dl", "js-runtime"]
    assert listed["ffmpeg"]["version"] == "n7.1.5-16-g9a4bb2c579-20260816"
    assert listed["yt-dlp"]["version"] == "2026.08.19"
    assert listed["gallery-dl"]["version"] == "1.32.13"
    assert listed["js-runtime"]["name"] == "QuickJS-NG"
    assert listed["js-runtime"]["version"] == "0.17.0"


def test_a_bare_name_is_never_called_the_shipped_copy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where nothing is vendored the search answers the bare name for every tool, and the bare name
    is the machine's own copy, whatever the two strings have in common."""
    monkeypatch.setattr(tools, "vendored_tool", lambda name: name)
    monkeypatch.setattr(tools, "YTDLP_BINARY", "yt-dlp")
    monkeypatch.setattr(tools, "GALLERYDL_BINARY", "gallery-dl")
    tools.MEASURED.forget()

    listed = {one["key"]: one for one in client.get("/api/download-tools").json()["tools"]}

    assert listed["yt-dlp"]["shipped"] is False
    assert listed["gallery-dl"]["shipped"] is False


def test_a_tool_that_will_not_start_is_not_answering_rather_than_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def missing(argv: list[str], *, time_limit: float) -> SubprocessResult:
        raise SubprocessError(f"could not run {argv[0]!r}")

    monkeypatch.setattr(tools, "run", missing)
    tools.MEASURED.forget()

    answer = client.get("/api/download-tools")

    assert answer.status_code == 200
    listed = {one["key"]: one for one in answer.json()["tools"]}
    assert listed["yt-dlp"]["version"] is None
    assert listed["gallery-dl"]["version"] is None
    assert (listed["js-runtime"]["name"], listed["js-runtime"]["version"]) == ("unknown", None)


def test_the_tools_are_asked_once_and_the_answer_kept(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked once per process, whatever opens the pane how often. The answer cannot change under a
    running Sift: the tools arrive with a release, and a release is a restart."""
    launches: list[list[str]] = []

    async def counting(argv: list[str], *, time_limit: float) -> SubprocessResult:
        del time_limit
        launches.append(argv)
        return SubprocessResult(returncode=0, stdout=b"2026.08.19\n", stderr=_HEADER.encode())

    monkeypatch.setattr(tools, "run", counting)
    tools.MEASURED.forget()

    client.get("/api/download-tools")
    client.get("/api/download-tools")

    assert len(launches) == 3


def test_the_check_reports_a_newer_release_and_changes_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def published() -> str:
        return "2026.09.20"

    monkeypatch.setattr(tools, "fetch_latest_ytdlp", published)

    answer = client.post("/api/download-tools/latest", json={"tool": "yt-dlp"}).json()

    assert answer == {
        "key": "yt-dlp",
        "running": "2026.08.19",
        "latest": "2026.09.20",
        "newer": True,
    }


def test_a_feed_that_could_not_be_read_is_not_news(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable() -> None:
        return None

    monkeypatch.setattr(tools, "fetch_latest_ytdlp", unreachable)

    answer = client.post("/api/download-tools/latest", json={"tool": "yt-dlp"}).json()

    assert answer["latest"] is None
    assert answer["newer"] is False


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield self._data


class _Feed:
    """The release feed as the guarded session would hand it over: a status and a body."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.content = _Body(body)

    async def __aenter__(self) -> _Feed:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FeedSession:
    def __init__(self, answer: _Feed | Exception) -> None:
        self._answer = answer
        self.asked: list[str] = []

    async def __aenter__(self) -> _FeedSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def get(self, url: str, **_kwargs: object) -> _Feed:
        self.asked.append(url)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _latest(monkeypatch: pytest.MonkeyPatch, answer: _Feed | Exception) -> str | None:
    session = _FeedSession(answer)
    routes: list[str | None] = []

    def opened(*, proxy: str | None) -> _FeedSession:
        routes.append(proxy)
        return session

    monkeypatch.setattr(tools, "guarded_session", opened)
    latest = asyncio.run(tools.fetch_latest_ytdlp())
    # Sift's own release feed goes out on the machine's own address, never through a tunnel.
    assert routes == [None]
    assert session.asked == [tools.YTDLP_RELEASE_FEED]
    return latest


def test_the_newest_release_is_the_feeds_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _latest(monkeypatch, _Feed(200, b'{"tag_name": " 2026.09.20 "}')) == "2026.09.20"


@pytest.mark.parametrize(
    "answer",
    [
        _Feed(503, b'{"tag_name": "2026.09.20"}'),
        _Feed(200, b'{"tag_name": "  "}'),
        _Feed(200, b'{"name": "no tag"}'),
        _Feed(200, b"<html>not json</html>"),
        aiohttp.ClientConnectionError("unreachable"),
    ],
    ids=["refused", "blank-tag", "no-tag", "not-json", "unreachable"],
)
def test_every_way_the_feed_can_fail_is_one_answer_could_not_check(
    monkeypatch: pytest.MonkeyPatch, answer: _Feed | Exception
) -> None:
    """A person's only choice after any of these is the same, so they are one answer: None."""
    assert _latest(monkeypatch, answer) is None


def test_the_check_names_the_tool_it_is_for(client: TestClient) -> None:
    """Only yt-dlp has a feed Sift reads. Anything else is refused before a connection is opened."""
    assert client.post("/api/download-tools/latest", json={"tool": "ffmpeg"}).status_code == 422
    assert client.post("/api/download-tools/latest").status_code == 422
