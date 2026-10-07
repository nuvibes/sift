# SPDX-License-Identifier: AGPL-3.0-or-later
"""The downloader seam: it picks a tool, runs it, and reads success or a plain failure from it.

The tool itself is faked: these tests are about the dispatch and the interpretation, not about
yt-dlp. A real subprocess is exercised where that matters, in the subprocess-runner tests.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.log import redact
from sift.slices.download.sources import cookie_health, subproc
from sift.slices.download.sources import downloader as downloader_mod
from sift.slices.download.sources import fetcher as fetcher_mod
from sift.slices.download.sources import tiktok as tiktok_mod
from sift.slices.download.sources.downloader import Downloader
from sift.slices.download.sources.errors import (
    CookiesNeeded,
    DownloadError,
    FetchFailed,
    LoginRequired,
    NoAnswer,
    NothingFound,
    UnsupportedURL,
)
from sift.slices.download.sources.resolved import ResolvedItem, ResolvedMedia
from sift.slices.download.sources.subproc import SubprocessResult
from sift.slices.download.sources.tuning import GALLERYDL_BINARY, YTDLP_BINARY


def _returns(
    result: SubprocessResult, capture: list[list[str]] | None = None
) -> Callable[..., Awaitable[SubprocessResult]]:
    async def fake_run(argv: list[str], **_kwargs: object) -> SubprocessResult:
        if capture is not None:
            capture.append(argv)
        return result

    return fake_run


def test_the_last_lines_of_a_tools_output_are_where_its_reason_is() -> None:
    from sift.slices.download.sources.downloader import last_lines

    said = "\n".join(f"line {n}" for n in range(1, 31)) + "\nERROR: the reason\n"

    kept = last_lines(said, 3)

    assert kept == "line 29\nline 30\nERROR: the reason"
    assert last_lines("", 20) == ""


def test_logged_tool_output_keeps_the_url_and_removes_what_is_sensitive() -> None:
    """A tool's output reaches the log through the ordinary redaction: the address stays (this is
    an admin's own log, and which URL failed is the point of it), while the account name in a
    home-directory path and any echoed cookie go."""
    long_token = "x" * 40  # a token-shaped run; the redactor removes anything 32+ chars long
    blob = (
        "ERROR: https://instagram.com/user/p/abc123 could not be fetched; "
        "wrote /home/kate/Downloads/clip.mp4; "
        f"stray token {long_token} in output"
    )
    out = str(redact(blob))
    assert "https://instagram.com/user/p/abc123" in out  # the URL is kept
    assert "kate" not in out, out  # the account name in the path is gone
    assert long_token not in out  # a token or cookie the tool echoed is gone


def test_it_handles_http_but_not_other_schemes() -> None:
    downloader = Downloader()
    assert downloader.handles("https://example.com/x")
    assert downloader.handles("http://example.com/x")
    assert not downloader.handles("ftp://example.com/x")


async def test_a_successful_fetch_returns_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"data")
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", "")))

    fetched = await Downloader().fetch("https://public.example/x", into=tmp_path)

    assert fetched.files == [tmp_path / "clip.mp4"]


async def test_a_site_that_refuses_the_tools_own_client_is_fetched_as_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pornhub's record says yt-dlp must connect the way a browser does (its own client gets 410
    from every address, a tunnel included), and the command the downloader
    runs says so. Another Site's command does not."""
    (tmp_path / "clip.mp4").write_bytes(b"data")
    seen: list[list[str]] = []
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", ""), seen))

    await Downloader().fetch("https://www.pornhub.com/view_video.php?viewkey=abc", into=tmp_path)
    await Downloader().fetch("https://vimeo.com/12345", into=tmp_path)

    pornhub, other = seen
    assert pornhub[pornhub.index("--impersonate") + 1] == "chrome"
    assert "--impersonate" not in other


async def test_a_tool_is_told_where_it_writes_so_its_temporary_files_go_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A killed one-file build leaves its unpacked runtime in the temporary directory;
    told the download's own directory, the runner points the temporary directory in there and it
    goes with the workspace. See `subproc.TOOL_TEMP`."""
    (tmp_path / "clip.mp4").write_bytes(b"data")
    told: list[object] = []

    async def fake_run(argv: list[str], **kwargs: object) -> SubprocessResult:
        told.append(kwargs.get("into"))
        return SubprocessResult(0, "", "")

    monkeypatch.setattr(subproc, "run", fake_run)

    await Downloader().fetch("https://vimeo.com/12345", into=tmp_path)

    assert told == [tmp_path]


async def test_a_video_host_uses_ytdlp_and_x_uses_gallerydl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"data")
    seen: list[list[str]] = []
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", ""), seen))

    await Downloader().fetch("https://vimeo.com/12345", into=tmp_path)  # catch-all -> yt-dlp
    await Downloader().fetch("https://x.com/a/status/1", into=tmp_path)  # X -> gallery-dl first

    assert seen[0][0] == YTDLP_BINARY
    assert seen[1][0] == GALLERYDL_BINARY


async def test_a_login_failure_becomes_login_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: HTTP Error 403: Forbidden"))
    )
    with pytest.raises(LoginRequired, match="wants cookies"):
        await Downloader().fetch(
            "https://public.example/x", into=tmp_path
        )  # catch-all -> subprocess


async def test_a_tool_that_gave_up_on_a_silent_site_is_no_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool already spent its own retries, each a whole timeout, so the job must not run it
    again. Only the last lines decide: a timeout it recovered from earlier is not the reason."""
    silent = "[download] Got error\nERROR: Unable to download webpage: The read operation timed out"
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(1, "", silent)))
    with pytest.raises(NoAnswer):
        await Downloader().fetch("https://public.example/x", into=tmp_path)

    recovered = (
        "WARNING: The read operation timed out, retrying\n" + "line\n" * 6 + "ERROR: gave up"
    )
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(1, "", recovered)))
    with pytest.raises(DownloadError) as caught:
        await Downloader().fetch("https://public.example/x", into=tmp_path)
    assert not isinstance(caught.value, NoAnswer)


async def test_a_refusal_with_no_cookies_saved_is_a_wait_and_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal cookies get past, at the seam that decides it.

    A site cookies help on, a 403, and nothing saved for it: the row is waiting, and the words say
    the download carries on by itself rather than asking somebody to press the same button again.
    `CookiesNeeded` is a `LoginRequired`, so the negative below pins the specific type: a handler
    that catches the general one first would turn the wait into a failure.
    """
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: HTTP Error 403: Forbidden"))
    )
    with pytest.raises(CookiesNeeded) as caught:
        await Downloader().fetch("https://www.youtube.com/watch?v=abc", into=tmp_path)
    assert "Add cookies" in str(caught.value)
    assert "try" not in str(caught.value).lower()


async def test_an_age_gate_with_no_cookies_saved_waits_and_says_it_is_about_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The age wall waits as the login wall does, and the waiting row says WHY: proof of age, from
    a browser that is signed in, not the general "needs cookies", which reads as a login."""
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: Sign in to confirm your age"))
    )
    with pytest.raises(CookiesNeeded) as caught:
        await Downloader().fetch("https://www.youtube.com/watch?v=abc", into=tmp_path)
    assert str(caught.value) == (
        "YouTube wants proof of age before it will show this. Add cookies from a browser you are "
        "signed in to and the download carries on by itself."
    )


async def test_a_refusal_with_cookies_saved_is_the_jar_being_turned_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same 403 on the same site, with a jar sent: there is nothing to wait for, so it is a
    failure with its own code, and it is the signal the killswitch counts, because a jar that is
    refused three times running is dead and must stop being sent."""
    cookie_health.reset()
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: HTTP Error 403: Forbidden"))
    )
    jar = tmp_path / "saved.jar"
    jar.write_text("# a jar the tool never reads here\n", encoding="utf-8")

    for _ in range(3):
        with pytest.raises(LoginRequired) as caught:
            await Downloader().fetch(
                "https://www.youtube.com/watch?v=abc", into=tmp_path, cookies_file=jar
            )
        assert not isinstance(caught.value, CookiesNeeded)
        assert caught.value.code == "cookies-refused"
        assert "turned the saved cookies away" in str(caught.value)

    assert cookie_health.status_for("youtube.com") == cookie_health.NEEDS_COOKIES
    cookie_health.reset()


async def test_a_refusal_on_a_site_cookies_cannot_help_is_the_failure_it_always_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait is not for every 403. A site whose record says cookies do not help keeps the old
    reading, because waiting for cookies nobody can produce is a row that never ends."""
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: HTTP Error 403: Forbidden"))
    )
    # RedGIFs: fetched by the tool, and its record says cookies do not help.
    with pytest.raises(LoginRequired) as caught:
        await Downloader().fetch("https://www.redgifs.com/watch/abc", into=tmp_path)
    assert not isinstance(caught.value, CookiesNeeded)


async def test_a_site_that_requires_cookies_waits_for_them_before_anything_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Instagram pointed at a tool, with nothing saved: every post is refused to a guest there
    (measured), so the download is held for cookies at once (on the same door a refusal opens),
    and no tool is started to fetch a login wall."""
    seen: list[list[str]] = []
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", ""), seen))

    async def read_downloader(_site_key: str | None) -> str | None:
        return "ytdlp"

    with pytest.raises(CookiesNeeded) as caught:
        await Downloader(read_downloader=read_downloader).fetch(
            "https://www.instagram.com/p/abc/", into=tmp_path
        )
    assert caught.value.code == downloader_mod.CODE_COOKIES_REQUIRED
    assert "Instagram" in str(caught.value) and "Add cookies" in str(caught.value)
    assert seen == []


async def test_a_site_that_requires_cookies_runs_once_they_are_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same download with a jar saved goes ahead, and the jar reaches the tool."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "clip.mp4").write_bytes(b"data")
    seen: list[list[str]] = []
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", ""), seen))
    jar = tmp_path / "saved.jar"
    jar.write_text("# a jar\n", encoding="utf-8")

    async def read_downloader(_site_key: str | None) -> str | None:
        return "ytdlp"

    await Downloader(read_downloader=read_downloader).fetch(
        "https://www.instagram.com/p/abc/", into=out, cookies_file=jar
    )
    assert "--cookies" in seen[0]


async def test_a_partial_site_is_not_held_for_cookies_it_may_not_need(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YouTube is Partial: a public video downloads without cookies, so nothing waits up front and
    the tool runs. Only a refusal makes it wait."""
    (tmp_path / "clip.mp4").write_bytes(b"data")
    seen: list[list[str]] = []
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", ""), seen))
    await Downloader().fetch("https://www.youtube.com/watch?v=abc", into=tmp_path)
    assert seen and seen[0][0] == YTDLP_BINARY


async def test_a_missing_post_becomes_nothing_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: HTTP Error 404: Not Found"))
    )
    with pytest.raises(NothingFound):
        await Downloader().fetch("https://public.example/gone", into=tmp_path)


async def test_a_run_that_says_there_was_no_media_is_nothing_found_in_the_tools_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Words no reading recognises, which still say the post held nothing: not a run that merely
    did not finish, and the tool's own line is kept as the one clue there is."""
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: No media in this post"))
    )
    with pytest.raises(NothingFound) as caught:
        await Downloader().fetch("https://public.example/post/1", into=tmp_path)
    said = str(caught.value)
    assert said.startswith("Nothing could be downloaded from ")
    assert "No media in this post" in said


async def test_a_410_is_recorded_once_with_the_site_and_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Pornhub address answered 410 Gone: the markers read nothing in it, so without the site's
    reading it would be a plain run that did not finish, retried and stored as "try again in a
    little while". A final answer is `NothingFound`, in the site's words, and on this site the words
    are a refusal of how the tool connects, which a tunnel does not answer."""
    monkeypatch.setattr(
        subproc,
        "run",
        _returns(
            SubprocessResult(
                1, "", "ERROR: [PornHub] abc: Unable to download webpage: HTTP Error 410: Gone"
            )
        ),
    )
    with pytest.raises(NothingFound) as caught:
        await Downloader().fetch(
            "https://www.pornhub.com/view_video.php?viewkey=abc", into=tmp_path
        )
    assert str(caught.value) == (
        "Pornhub answered 410 Gone: it refuses the downloader's own way of connecting, so Sift "
        "has to connect the way a browser does."
    )
    assert (caught.value.code, caught.value.tier) == ("http-410", 3)
    assert not caught.value.a_tunnel_would_help


async def test_a_tier_one_code_rides_after_the_types_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code is never the thing a row loses: where the type's sentence wins, the code follows."""
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(1, "", "ERROR: HTTP Error 400: Bad Request"))
    )
    with pytest.raises(DownloadError) as caught:
        await Downloader().fetch("https://public.example/x", into=tmp_path)
    assert str(caught.value).endswith("answered 400 Bad Request.")
    assert caught.value.tier == 1


async def test_an_unclassifiable_failure_becomes_a_plain_download_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(1, "", "something odd happened")))
    with pytest.raises(DownloadError):
        await Downloader().fetch("https://public.example/x", into=tmp_path)


async def test_an_unsupported_site_becomes_unsupported_url_not_a_retryable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yt-dlp's "Unsupported URL" is a permanent verdict (no downloader Sift ships can read the
    site), so it becomes UnsupportedURL, which the job runner records without retrying, rather than
    the generic DownloadError that would retry three times over with a "try again later" it could
    never satisfy. UnsupportedURL is a DownloadError subtype, so the negative below is what pins the win:
    the type is the specific one, and the wording does not promise a retry."""
    monkeypatch.setattr(
        subproc,
        "run",
        _returns(SubprocessResult(1, "", "ERROR: Unsupported URL: https://public.example/x")),
    )
    with pytest.raises(UnsupportedURL) as caught:
        await Downloader().fetch("https://public.example/x", into=tmp_path)
    assert "try again" not in str(caught.value).lower()
    # Its own code, so the row is not filed with every other 403 and given the sentence written
    # for one, which would invite adding cookies for a site nothing here can read with them.
    assert caught.value.code == "unsupported"


async def test_success_with_no_file_is_nothing_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", "")))
    with pytest.raises(NothingFound):
        await Downloader().fetch("https://public.example/empty", into=tmp_path)


async def test_a_partial_left_by_a_pause_is_continued_rather_than_fetched_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the seam does with bytes a paused run left in the workspace: it asks for the rest.

    The staging name is what makes it safe, and it is why this asserts the SIZE rather than merely
    that something was passed: the name is derived from the address, so the file found here is the
    same item resumed and never a different one, and the number handed over is where the fetch is
    to pick up from.
    """
    item = ResolvedItem(
        index=0,
        url="https://cdn.example/clip.mp4",
        media_type="video",
        ext=".mp4",
        referer="https://site/",
    )
    media = ResolvedMedia(
        site="Bunkr", source_url="https://site/a", source_host="site", items=[item]
    )

    async def fake_resolve(_url: str, **_kwargs: object) -> ResolvedMedia:
        return media

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)

    @contextlib.asynccontextmanager
    async def fake_session(**_kwargs: object) -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(downloader_mod, "guarded_session", fake_session)
    asked: list[object] = []

    async def fake_fetch(
        _session: object, *, url: str, dest: Path, **kwargs: object
    ) -> tuple[Path, int]:
        asked.append(kwargs.get("resume_from"))
        return dest, 10

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)
    # What a pause left behind, under the name this item stages as.
    (tmp_path / downloader_mod.stage_name(item)).write_bytes(b"aabbcc")

    await Downloader().fetch("https://site/a", into=tmp_path)
    assert asked == [6]

    # And the known negative, in the same test because it is the same call: an empty workspace is
    # every download that has not been paused, and it asks for the whole file.
    asked.clear()
    (tmp_path / downloader_mod.stage_name(item)).unlink()
    await Downloader().fetch("https://site/a", into=tmp_path)
    assert asked == [0]


async def test_a_direct_item_is_streamed_through_the_guarded_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a resolver returns a direct media address, the seam streams it over one guarded session
    (never a subprocess), names the file for the item's position, and returns what was written."""
    item = ResolvedItem(
        index=0,
        url="https://cdn.example/clip.mp4",
        media_type="video",
        ext=".mp4",
        referer="https://site/",
    )
    media = ResolvedMedia(
        site="Bunkr", source_url="https://site/a", source_host="site", items=[item]
    )

    async def fake_resolve(_url: str, **_kwargs: object) -> ResolvedMedia:
        return media

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)

    @contextlib.asynccontextmanager
    async def fake_session(**_kwargs: object) -> AsyncIterator[object]:
        yield object()  # the guarded session; the fetch is faked below, so it is only a sentinel

    monkeypatch.setattr(downloader_mod, "guarded_session", fake_session)

    seen: dict[str, object] = {}

    async def fake_fetch(
        _session: object, *, url: str, dest: Path, **kwargs: object
    ) -> tuple[Path, int]:
        seen["url"] = url
        seen["referer"] = kwargs.get("referer")
        dest.write_bytes(b"streamed")
        return dest, 8

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    fetched = await Downloader().fetch("https://site/a", into=tmp_path)

    # Named for what it is, not `0.mp4`: `index` is the item's position in its source link and is 0
    # for anything that is not a carousel, so staging by it would give every ordinary download the
    # same name.
    assert fetched.files == [tmp_path / "clip.mp4"]
    assert fetched.files[0].read_bytes() == b"streamed"
    assert seen == {"url": "https://cdn.example/clip.mp4", "referer": "https://site/"}


async def test_a_site_pointed_at_a_tool_skips_the_service_and_runs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pointed at yt-dlp, TikTok skips the service and runs the tool.

    The reader is asked for the SITE KEY the catalog resolved, never the address's host: Bunkr has
    25 addresses and a choice made against one of them has to hold for all of them."""
    (tmp_path / "clip.mp4").write_bytes(b"data")
    seen: list[list[str]] = []
    asked: list[str | None] = []
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", ""), seen))

    async def read_downloader(site_key: str | None) -> str | None:
        asked.append(site_key)
        return "ytdlp"

    await Downloader(read_downloader=read_downloader).fetch(
        "https://www.tiktok.com/@a/video/1", into=tmp_path
    )
    assert seen[0][0] == YTDLP_BINARY
    assert asked == ["tiktok"]


async def test_the_default_method_routes_tiktok_to_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no reader wired (the default), TikTok goes to the service resolver."""
    called: dict[str, str] = {}

    async def fake_service(url: str, **_kwargs: object) -> ResolvedMedia:
        called["url"] = url
        return ResolvedMedia(site="TikTok", source_url=url, source_host="tiktok.com", items=[])

    monkeypatch.setattr(tiktok_mod, "resolve_tiktok", fake_service)

    fetched = await Downloader().fetch("https://www.tiktok.com/@a/video/1", into=tmp_path)

    assert fetched.files == []  # the fake service returned no items
    assert called["url"] == "https://www.tiktok.com/@a/video/1"


async def test_x_uses_gallerydl_when_it_finds_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gallery-dl finds the images, so yt-dlp is never run."""
    seen: list[str] = []

    async def fake_run(argv: list[str], **_kw: object) -> SubprocessResult:
        seen.append(argv[0])
        (tmp_path / "img.jpg").write_bytes(b"data")
        return SubprocessResult(0, "", "")

    monkeypatch.setattr(subproc, "run", fake_run)

    fetched = await Downloader().fetch("https://x.com/quillmoss/status/1", into=tmp_path)
    assert seen == [GALLERYDL_BINARY]  # the fallback tool was not needed
    assert fetched.files == [tmp_path / "img.jpg"]


async def test_x_falls_back_to_ytdlp_when_gallerydl_finds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gallery-dl finds no images (a video post); yt-dlp then fetches the video."""
    seen: list[str] = []

    async def fake_run(argv: list[str], **_kw: object) -> SubprocessResult:
        seen.append(argv[0])
        if argv[0] == YTDLP_BINARY:
            (tmp_path / "vid.mp4").write_bytes(b"data")
        return SubprocessResult(0, "", "")

    monkeypatch.setattr(subproc, "run", fake_run)

    fetched = await Downloader().fetch("https://x.com/quillmoss/status/1", into=tmp_path)
    assert seen == [GALLERYDL_BINARY, YTDLP_BINARY]  # tried gallery-dl, then fell back
    assert fetched.files == [tmp_path / "vid.mp4"]


async def test_x_reports_nothing_found_when_both_tools_find_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", "")))  # neither writes
    with pytest.raises(NothingFound):
        await Downloader().fetch("https://x.com/quillmoss/status/1", into=tmp_path)


async def test_x_does_not_fall_back_on_a_login_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A login problem the first tool hits will not be fixed by the second, so it does not retry."""
    seen: list[str] = []

    async def fake_run(argv: list[str], **_kw: object) -> SubprocessResult:
        seen.append(argv[0])
        return SubprocessResult(1, "", "ERROR: HTTP Error 403: Forbidden")

    monkeypatch.setattr(subproc, "run", fake_run)

    with pytest.raises(LoginRequired):
        await Downloader().fetch("https://x.com/quillmoss/status/1", into=tmp_path)
    assert seen == [GALLERYDL_BINARY]  # the fallback was not tried


# --- what a directly-fetched file is called -------------------------------------------------


def _item(**over: object) -> ResolvedItem:
    """A direct item, with the fields these cases vary."""
    fields: dict[str, object] = {
        "index": 0,
        "url": "https://cdn.example/a/holiday%20clip.mp4",
        "media_type": "video",
        "ext": ".mp4",
    }
    fields.update(over)
    return ResolvedItem(**fields)  # type: ignore[arg-type]


def test_a_lone_download_is_not_called_zero() -> None:
    """The staged name is not the index.

    `index` is the item's position within its source link (0 for anything that is not a
    carousel), so staging by it would name every ordinary download `0.mp4` and leave the library's
    collision handling to number the rest: `0`, `0-1`, `0-2`.
    """
    # The address's last segment percent-decoded, its space kept (see the title case below).
    assert downloader_mod.stage_name(_item()) == "holiday clip.mp4"


def test_the_name_a_resolver_supplied_is_used() -> None:
    """Instagram and the site extractors work a real name out of the response, and it rides on the
    item; that is the only name here that means anything."""
    assert downloader_mod.stage_name(_item(filename="Sunset At The Pier.MOV")) == (
        "Sunset At The Pier.mp4"
    )


def test_a_title_keeps_its_spaces_and_brackets_for_the_name_template() -> None:
    """`{name}` fills from the staged name, so a space turned into an underscore here would reach
    the library as `creator - A_TITLE_IN_CAPITALS.mp4`. What can move a file is still refused, and
    a space at either end is not kept."""
    assert downloader_mod.stage_name(_item(filename="A TITLE IN CAPITALS (4K).mp4")) == (
        "A TITLE IN CAPITALS (4K).mp4"
    )
    assert downloader_mod.stage_name(_item(filename="  two  spaces / and a slash  .mp4")) == (
        "two spaces _ and a slash.mp4"
    )


def test_the_extension_is_the_one_the_resolver_negotiated() -> None:
    # Not the one the remote name claims. A `.jpg` on something that is really a video would
    # otherwise ride through into the library and be wrong about itself forever.
    assert downloader_mod.stage_name(_item(filename="clip.jpg", ext=".mp4")).endswith(".mp4")


def test_an_unnameable_address_gets_a_stable_digest() -> None:
    """Opaque, but the same address twice is the same name, so a resumed job re-stages the file
    it was already fetching rather than leaving a second copy beside it."""
    bare = _item(url="https://cdn.example/")
    first = downloader_mod.stage_name(bare)
    assert first == downloader_mod.stage_name(bare)
    assert first.endswith(".mp4")
    assert first != ".mp4"
    # And a different address is a different name.
    assert first != downloader_mod.stage_name(_item(url="https://cdn.example/other/"))


def test_the_index_disambiguates_a_carousel_and_nothing_else() -> None:
    """The index earns its place only where it was ever needed: several photos in one post, which
    a site may well give the same name. The lone item (every ordinary download) goes without."""
    assert downloader_mod.stage_name(_item(filename="photo.jpg", ext=".jpg")) == "photo.jpg"
    assert (
        downloader_mod.stage_name(_item(index=3, filename="photo.jpg", ext=".jpg")) == "photo-3.jpg"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "..\\..\\windows\\system32",
        ".hidden",
        "name\nwith\nnewlines",
        "a" * 400,
    ],
)
def test_a_remote_name_can_only_ever_be_a_filename(hostile: str) -> None:
    """The name comes from a remote site, so it is reduced to a permitted character set rather than
    having known-bad sequences removed. The staged file has to land in the directory the caller
    owns; a separator or a leading dot that got through would put it somewhere else, or hide it."""
    name = downloader_mod.stage_name(_item(filename=hostile))

    assert "/" not in name
    assert "\\" not in name
    assert not name.startswith(".")
    assert ".." not in name
    assert name.strip() == name
    assert len(name) < 120


async def test_an_item_already_recorded_is_not_fetched_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the per-item ledger is for. A link whose contents change is re-resolved every time, so
    without this the second paste downloads the whole set again."""
    media = ResolvedMedia(
        site="Instagram",
        source_url="https://www.instagram.com/stories/highlights/1/",
        source_host="instagram.com",
        items=[
            ResolvedItem(
                index=0, url="https://cdn/a", media_type="video", ext=".mp4", media_key="cdn/a"
            ),
            ResolvedItem(
                index=1, url="https://cdn/b", media_type="video", ext=".mp4", media_key="cdn/b"
            ),
        ],
    )

    async def fake_resolve(_url: str, **_kwargs: object) -> ResolvedMedia:
        return media

    fetched: list[str] = []

    async def fake_direct(
        _self: object, _session: object, item: ResolvedItem, into: Path, **_watching: object
    ) -> tuple[Path, int]:
        fetched.append(item.url)
        target = into / f"{item.index}.mp4"
        target.write_bytes(b"x")
        return target, 1

    monkeypatch.setattr("sift.slices.download.sources.downloader.resolve", fake_resolve)
    monkeypatch.setattr(Downloader, "_fetch_direct", fake_direct)

    async def already_have(key: str) -> bool:
        return key == "cdn/a"

    result = await Downloader().fetch(
        media.source_url, into=Path(tempfile.mkdtemp()), already_have=already_have
    )

    assert fetched == ["https://cdn/b"]
    assert list(result.item_keys.values()) == ["cdn/b"]


# --- the Downloads settings reach Sift's own fetch and the time between tool runs --------------


def _direct_media(*urls: str) -> ResolvedMedia:
    items = [
        ResolvedItem(index=n, url=url, media_type="video", ext=".mp4", referer="https://site/")
        for n, url in enumerate(urls)
    ]
    return ResolvedMedia(site="Bunkr", source_url="https://site/a", source_host="site", items=items)


def _serve_direct(monkeypatch: pytest.MonkeyPatch, media: ResolvedMedia) -> dict[str, object]:
    """Fake the resolver and the session; return what the session was opened with."""
    opened: dict[str, object] = {}

    async def fake_resolve(_url: str, **_kwargs: object) -> ResolvedMedia:
        return media

    @contextlib.asynccontextmanager
    async def fake_session(**kwargs: object) -> AsyncIterator[object]:
        opened.update(kwargs)
        yield object()

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)
    monkeypatch.setattr(downloader_mod, "guarded_session", fake_session)
    return opened


async def test_the_downloads_settings_reach_the_direct_fetch_and_its_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The policy the settings were read into is the one the direct fetch AND its session get."""
    from dataclasses import replace

    from sift.slices.download.sources.tuning import POLICY, RunPolicy

    planted = replace(POLICY, pacing=replace(POLICY.pacing, bytes_per_second=204_800))
    opened = _serve_direct(monkeypatch, _direct_media("https://cdn.example/a.mp4"))
    given: list[object] = []

    async def fake_fetch(
        _session: object, *, url: str, dest: Path, **kwargs: object
    ) -> tuple[Path, int]:
        given.append(kwargs.get("policy"))
        return dest, 1

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    async def read_policy() -> RunPolicy:
        return planted

    await Downloader(read_policy=read_policy).fetch("https://site/a", into=tmp_path)

    assert given == [planted]
    assert opened["policy"] is planted


async def test_a_file_the_size_settings_refuse_is_skipped_and_the_album_carries_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_direct(
        monkeypatch, _direct_media("https://cdn.example/huge.mp4", "https://cdn.example/ok.mp4")
    )

    async def fake_fetch(
        _session: object, *, url: str, dest: Path, **_kwargs: object
    ) -> tuple[Path, int]:
        if "huge" in url:
            raise fetcher_mod.Skipped("Sift skipped this 50 MB file.")
        dest.write_bytes(b"ok")
        return dest, 2

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    fetched = await Downloader().fetch("https://site/a", into=tmp_path)

    assert len(fetched.files) == 1


async def test_a_file_gone_from_the_site_is_left_out_and_the_album_carries_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead file in a large album is that file left out, as the tools leave it; the other files
    are still asked for and land. A file that merely did not finish still ends the run."""
    _serve_direct(
        monkeypatch,
        _direct_media(
            "https://cdn.example/a.mp4", "https://cdn.example/gone.mp4", "https://cdn.example/c.mp4"
        ),
    )
    asked: list[str] = []

    async def fake_fetch(
        _session: object, *, url: str, dest: Path, **_kwargs: object
    ) -> tuple[Path, int]:
        asked.append(url)
        if "gone" in url:
            raise NothingFound("Bunkr answered 404 Not Found.", code="http-404")
        dest.write_bytes(b"ok")
        return dest, 2

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    fetched = await Downloader().fetch("https://site/a", into=tmp_path)

    assert len(asked) == 3
    assert len(fetched.files) == 2
    # Said on the download's row, not only in the log: 2 of 3 files, 1 left out.
    assert (fetched.offered, fetched.left_out) == (3, 1)

    async def unfinished(_session: object, *, url: str, dest: Path, **_k: object) -> Any:
        raise FetchFailed("The file did not finish.")

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", unfinished)
    with pytest.raises(FetchFailed):
        await Downloader().fetch("https://site/a", into=tmp_path)


async def test_a_signature_that_ran_out_is_signed_again_from_the_files_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last files of a long album can be asked for after their signature has run out. The Site
    refuses; the file's page is read again for a fresh address, which is fetched under the same
    place and name; a refusal of a file that names no page keeps its meaning."""
    from dataclasses import replace

    stale = ResolvedItem(
        index=7,
        url="https://cdn.example/f.mp4?token=old",
        media_type="video",
        ext=".mp4",
        filename="f.mp4",
        refetch_url="https://site/f/slug",
    )
    album = ResolvedMedia(
        site="Bunkr", source_url="https://site/a", source_host="site", items=[stale]
    )
    page = ResolvedMedia(
        site="Bunkr",
        source_url="https://site/f/slug",
        source_host="site",
        items=[replace(stale, index=0, url="https://cdn.example/f.mp4?token=new", filename=None)],
    )

    async def fake_resolve(url: str, **_kwargs: object) -> ResolvedMedia:
        return page if url == "https://site/f/slug" else album

    @contextlib.asynccontextmanager
    async def fake_session(**_kwargs: object) -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)
    monkeypatch.setattr(downloader_mod, "guarded_session", fake_session)
    asked: list[tuple[str, str]] = []

    async def fake_fetch(
        _session: object, *, url: str, dest: Path, **_kwargs: object
    ) -> tuple[Path, int]:
        asked.append((url, dest.name))
        if "old" in url:
            raise NothingFound("Bunkr answered 403 Forbidden.", code="http-403")
        dest.write_bytes(b"ok")
        return dest, 2

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    fetched = await Downloader().fetch("https://site/a", into=tmp_path)

    assert asked == [
        ("https://cdn.example/f.mp4?token=old", "f-7.mp4"),
        ("https://cdn.example/f.mp4?token=new", "f-7.mp4"),
    ]
    assert len(fetched.files) == 1

    # No page to read again: the refusal stands.
    album.items[0] = replace(stale, refetch_url=None)
    asked.clear()
    with pytest.raises(NothingFound):
        await Downloader().fetch("https://site/a", into=tmp_path)
    assert len(asked) == 1


@pytest.mark.parametrize(
    ("page_items", "fresh"),
    [
        # The page offers several files: the one at the item's own place is the one signed again.
        ((3, 7), "https://cdn.example/7.mp4?token=new"),
        # Several, and none at its place: the page no longer offers it, so the refusal stands.
        ((3, 4), None),
        # The page will not be read at all: the refusal stands.
        (None, None),
    ],
)
async def test_a_page_read_again_that_no_longer_offers_the_file_leaves_the_refusal_standing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page_items: tuple[int, ...] | None,
    fresh: str | None,
) -> None:
    from dataclasses import replace

    stale = ResolvedItem(
        index=7,
        url="https://cdn.example/f.mp4?token=old",
        media_type="video",
        ext=".mp4",
        filename="f.mp4",
        refetch_url="https://site/f/slug",
    )
    album = ResolvedMedia(
        site="Bunkr", source_url="https://site/a", source_host="site", items=[stale]
    )

    async def fake_resolve(url: str, **_kwargs: object) -> ResolvedMedia:
        if url != "https://site/f/slug":
            return album
        if page_items is None:
            raise DownloadError("The page did not load.")
        return ResolvedMedia(
            site="Bunkr",
            source_url=url,
            source_host="site",
            items=[
                replace(stale, index=at, url=f"https://cdn.example/{at}.mp4?token=new")
                for at in page_items
            ],
        )

    @contextlib.asynccontextmanager
    async def fake_session(**_kwargs: object) -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)
    monkeypatch.setattr(downloader_mod, "guarded_session", fake_session)
    asked: list[str] = []

    async def fake_fetch(
        _session: object, *, url: str, dest: Path, **_kwargs: object
    ) -> tuple[Path, int]:
        asked.append(url)
        if "old" in url:
            raise NothingFound("Bunkr answered 410 Gone.", code="http-410")
        dest.write_bytes(b"ok")
        return dest, 2

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    if fresh is None:
        with pytest.raises(NothingFound, match="410"):
            await Downloader().fetch("https://site/a", into=tmp_path)
        assert asked == ["https://cdn.example/f.mp4?token=old"]
    else:
        fetched = await Downloader().fetch("https://site/a", into=tmp_path)
        assert asked == ["https://cdn.example/f.mp4?token=old", fresh]
        assert len(fetched.files) == 1


async def test_a_download_of_only_refused_files_says_why_it_has_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_direct(monkeypatch, _direct_media("https://cdn.example/huge.mp4"))

    async def fake_fetch(_session: object, **_kwargs: object) -> tuple[Path, int]:
        raise fetcher_mod.Skipped(
            "Sift skipped this 50 MB file because Skip files larger than is set to 10 MB."
        )

    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    with pytest.raises(fetcher_mod.Skipped, match="Skip files larger than is set to 10 MB"):
        await Downloader().fetch("https://site/a", into=tmp_path)


async def test_a_tool_run_told_too_many_requests_holds_the_next_run_to_that_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yt-dlp has no option for the wait after a rate limit; Sift holds the NEXT run instead."""
    from sift.slices.download.sources import ratelimit
    from sift.slices.download.sources.tuning import POLICY

    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = ratelimit.HostRateLimiter(clock=lambda: now[0], sleep=sleep)
    monkeypatch.setattr(ratelimit, "LIMITER", limiter)
    monkeypatch.setattr(
        subproc,
        "run",
        _returns(SubprocessResult(1, "", "ERROR: HTTP Error 429: Too Many Requests")),
    )

    with pytest.raises(DownloadError):
        await Downloader().fetch("https://public.example/x", into=tmp_path)
    assert slept == []  # the refused run itself was not held

    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", "")))
    (tmp_path / "clip.mp4").write_bytes(b"x")
    await Downloader().fetch("https://public.example/y", into=tmp_path)
    assert slept == [POLICY.pacing.wait_after_too_many_requests]


async def test_the_readers_are_handed_the_downloads_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Site readers' page and API reads are paced, timed out and held after a rate limit by
    the same settings as the transfer: the policy is read before the resolve
    and handed to it, so the resolve never runs on settings nobody read."""
    from dataclasses import replace

    from sift.slices.download.sources.tuning import POLICY, RunPolicy

    planted = replace(POLICY, pacing=replace(POLICY.pacing, seconds_between_requests=2.0))
    handed: list[object] = []

    async def fake_resolve(_url: str, **kwargs: object) -> ResolvedMedia:
        handed.append(kwargs.get("policy"))
        return _direct_media()

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)

    async def read_policy() -> RunPolicy:
        return planted

    # Nothing to fetch is not the point; what reached the resolve is.
    with contextlib.suppress(NothingFound):
        await Downloader(read_policy=read_policy).fetch("https://site/a", into=tmp_path)

    assert handed == [planted]


async def test_the_detailed_download_log_records_a_run_that_worked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the detailed download log on, every run is recorded (the command it ran as, and what
    the tool said), so the pace and the retries just chosen can be seen, successful runs included;
    and none while it is off."""
    from dataclasses import replace

    from structlog.testing import capture_logs

    from sift.slices.download.sources.tuning import POLICY, RunPolicy

    (tmp_path / "clip.mp4").write_bytes(b"data")
    monkeypatch.setattr(
        subproc, "run", _returns(SubprocessResult(0, "", "[debug] yt-dlp version 2026.08.19"))
    )
    pace_and_retries_off = replace(
        POLICY, pacing=replace(POLICY.pacing, seconds_between_requests=0.0, retries=0)
    )

    async def detailed() -> RunPolicy:
        return replace(pace_and_retries_off, verbose=True)

    async def plain() -> RunPolicy:
        return pace_and_retries_off

    with capture_logs() as written:
        await Downloader(read_policy=detailed).fetch("https://vimeo.com/12345", into=tmp_path)
        await Downloader(read_policy=plain).fetch("https://vimeo.com/12345", into=tmp_path)

    ran = [line for line in written if line["event"] == "download.tool_ran"]
    assert len(ran) == 1
    assert ran[0]["tool"] == "ytdlp"
    assert ran[0]["exit_code"] == 0
    assert "--sleep-requests 0.0" in ran[0]["command"]
    assert "--retries 0" in ran[0]["command"]
    assert ran[0]["command"].endswith("-- https://vimeo.com/12345")
    assert ran[0]["detail"] == "[debug] yt-dlp version 2026.08.19"


def test_the_detailed_log_keeps_both_ends_of_a_long_run_and_counts_the_middle() -> None:
    from sift.slices.download.sources.downloader import ends_of

    said = "\n".join(f"line {n}" for n in range(1, 11))

    assert (
        ends_of(said, 2, 3) == "line 1\nline 2\n... 5 lines left out ...\nline 8\nline 9\nline 10"
    )
    assert ends_of(said, 5, 5) == said
    assert ends_of("", 2, 2) == ""


def test_a_tunnels_password_in_a_logged_command_is_removed_by_the_redaction() -> None:
    """The command carries `--proxy` for a Site routed through a tunnel. Whatever credential that
    address holds goes in every mode, and the rest of the command stays readable."""
    command = "yt-dlp --retries 0 --proxy socks5://sift:hunter2@127.0.0.1:1080 -- https://e.com/a"

    out = str(redact(command, "command"))

    assert "hunter2" not in out
    assert "--retries 0" in out
    assert "127.0.0.1:1080" in out


async def test_a_connection_the_proxy_refused_is_a_permanent_refusal_not_a_login_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal reaches the tool as a 403 and reads, in its words, like a site turning a login
    away. Asked directly, the proxy says a connection into the private network was refused, and
    that is what the failure is: permanent, and never a reason to replace a cookie jar."""
    from sift.kernel.public_net import Refusal, Traffic
    from sift.slices.download.sources import argv
    from sift.slices.download.sources.errors import PrivateNetworkRefused

    monkeypatch.setattr(
        subproc,
        "run",
        _returns(
            SubprocessResult(1, "", "ERROR: HTTP Error 403: Blocked by Sift: not a public address")
        ),
    )
    monkeypatch.setattr(
        argv,
        "proxy_traffic",
        lambda _command: Traffic(
            connections=1,
            sent=120,
            received=0,
            refused=(Refusal("10.0.0.5:80", "private_address"),),
        ),
    )
    with pytest.raises(PrivateNetworkRefused, match="private network") as caught:
        await Downloader().fetch("https://public.example/x", into=tmp_path)
    assert caught.value.code == "private-network"
    assert not isinstance(caught.value, LoginRequired)


# The tools' own words for a file left out by the size options, as the vendored builds print them
# (measured against a local file: both exit 0 and write nothing).
_YTDLP_TOO_LARGE = (
    "[info] clip: Downloading 1 format(s): mp4\n\r[download] File is larger than max-filesize "
    "(300000 bytes > 102400 bytes). Aborting.\n"
)
_GALLERYDL_TOO_SMALL = (
    "[downloader.http][warning] File size smaller than allowed minimum (300000 > 1048576)\n"
)


@pytest.mark.parametrize(
    ("stdout", "stderr", "sentence"),
    [
        (
            _YTDLP_TOO_LARGE,
            "",
            "Sift skipped this 292 KB file because Skip files larger than is set to 100 KB.",
        ),
        (
            "",
            _GALLERYDL_TOO_SMALL,
            "Sift skipped this 292 KB file because Skip files smaller than is set to 1 MB.",
        ),
    ],
)
async def test_a_file_a_tool_skipped_for_its_size_says_the_skip_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str, stderr: str, sentence: str
) -> None:
    """Not "nothing could be downloaded": the same sentence Sift's own fetch says, and final, and
    the other tool is not tried, since it is held to the same size settings."""
    runs: list[list[str]] = []
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, stdout, stderr), runs))

    with pytest.raises(fetcher_mod.Skipped) as refused:
        await Downloader().fetch("https://x.com/someone/status/1", into=tmp_path)

    assert str(refused.value) == sentence
    assert len(runs) == 1
