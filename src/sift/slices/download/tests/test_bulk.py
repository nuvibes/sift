# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a whole playlist or channel: what is offered, what is refused, and what gets queued.

The listing tool is faked at the same seam every other tool run is faked at. What is being tested is
the decision: which sites may be taken whole, what a failed listing looks like, and that the result
is a list of addresses each of which becomes its own download.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from sift.kernel.public_net import TOOL_PROXY
from sift.slices.download import bulk
from sift.slices.download.sources import argv, subproc

_PLAYLIST = "https://www.youtube.com/playlist?list=PL123"


class _Listing:
    """Stands in for the listing run, and remembers what it was asked to run."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.command: list[str] = []

    async def __call__(self, command: list[str], **_kwargs: object) -> subproc.SubprocessResult:
        self.command = command
        return subproc.SubprocessResult(
            returncode=self.returncode, stdout=self.stdout, stderr="refused"
        )


def test_instagram_is_the_one_site_never_asked_for_a_whole_profile() -> None:
    """It watches for exactly this and answers by locking the account rather than by refusing the
    download, so the cost of getting it wrong is not a failed download."""
    with pytest.raises(bulk.BulkRefused) as refusal:
        bulk.bulk_site("https://www.instagram.com/someone/")
    assert "Instagram" in str(refusal.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.redgifs.com/users/someone",
        "https://www.youtube.com/@someone",
        "https://www.pornhub.com/model/someone-here/videos",
    ],
)
def test_every_other_site_may_be_taken_whole(url: str) -> None:
    """Taking a creator's whole output is the ordinary reason to point a downloader at a creator
    page. Instagram is the exception, not the rule."""
    assert bulk.bulk_site(url).bulk is True


def test_a_site_sift_does_not_know_is_refused_with_what_to_do_instead() -> None:
    with pytest.raises(bulk.BulkRefused) as refusal:
        bulk.bulk_site("https://example-host.test/someone/")
    assert "single post" in str(refusal.value)


async def test_a_playlist_answers_with_one_address_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = _Listing(stdout="https://youtu.be/one\nhttps://youtu.be/two\n\n")
    monkeypatch.setattr(subproc, "run", listing)

    found = await bulk.find_items(_PLAYLIST)

    assert found == ["https://youtu.be/one", "https://youtu.be/two"]
    # Listed, not fetched: the whole point of asking first is that it costs no media.
    assert "--flat-playlist" in listing.command
    # And paced like any other run: it is one request, to a site about to get a great many more.
    assert "--sleep-requests" in listing.command


async def test_a_listing_on_a_site_that_refuses_the_tools_own_client_asks_as_a_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listing is a request to the same site as the download, so a Site whose
    record says to connect as a browser is asked for its list that way too."""
    listing = _Listing(stdout="https://www.pornhub.com/view_video.php?viewkey=one\n")
    monkeypatch.setattr(subproc, "run", listing)

    await bulk.find_items("https://www.pornhub.com/model/someone-here/videos")

    assert listing.command[listing.command.index("--impersonate") + 1] == "chrome"


async def test_everything_found_is_reported_so_the_caller_can_say_there_was_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is applied where the count is shown, not here. Reporting a capped number as though
    it were the whole list would make the confirmation step lie about what it found."""
    listing = _Listing(
        stdout="\n".join(f"https://youtu.be/{n}" for n in range(bulk.MAX_ITEMS + 50))
    )
    monkeypatch.setattr(subproc, "run", listing)

    assert len(await bulk.find_items(_PLAYLIST)) == bulk.MAX_ITEMS + 50


async def test_a_listing_that_failed_says_so_rather_than_queueing_nothing_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subproc, "run", _Listing(returncode=1))
    with pytest.raises(bulk.BulkRefused) as refusal:
        await bulk.find_items(_PLAYLIST)
    assert "private" in str(refusal.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://coomer.st/onlyfans/user/someone",
        "https://x.com/someone",
        "https://pmvhaven.com/profile/someone",
    ],
)
async def test_a_site_that_cannot_be_counted_first_is_told_what_does_work(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """Only a video playlist can be listed ahead of time. On a gallery site, or one read by its own
    extractor, a whole album or profile is simply what an ordinary paste already fetches, so a dead
    end there would be a dead end in front of something that works. And it is said WITHOUT asking
    the site: a listing that can only fail is a request to somebody else's server for a sentence
    known in advance."""
    listing = _Listing(stdout="https://example-host.test/one\n")
    monkeypatch.setattr(subproc, "run", listing)
    with pytest.raises(bulk.BulkRefused) as refusal:
        await bulk.find_items(url)
    assert "paste" in str(refusal.value).lower()
    assert "in one go" in str(refusal.value)
    assert listing.command == []


async def test_an_empty_playlist_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subproc, "run", _Listing(stdout="\n  \n"))
    with pytest.raises(bulk.BulkRefused):
        await bulk.find_items(_PLAYLIST)


async def test_the_listing_goes_out_the_way_that_site_downloads_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A site routed through a tunnel must be ASKED through it too. A listing that went out
    directly would name the machine's own address to the one site it was routed away from."""
    listing = _Listing(stdout="https://youtu.be/one")
    monkeypatch.setattr(subproc, "run", listing)

    await bulk.find_items(_PLAYLIST, proxy="http://127.0.0.1:47100")

    proxied = urlsplit(listing.command[listing.command.index("--proxy") + 1])
    # The tool is pointed at the tool proxy's listener for THAT tunnel, never at the tunnel
    # itself, and that listener is not the direct one: the refusal holds on both ways out.
    assert proxied.port == urlsplit(TOOL_PROXY.address_for("http://127.0.0.1:47100")).port
    assert proxied.port != urlsplit(TOOL_PROXY.address_for()).port


def test_the_listing_command_never_lets_an_address_become_an_option() -> None:
    command = argv.build_enumerate_argv("-oProxyCommand=touch /tmp/pwned")
    assert command[-2] == "--"  # the terminator, so a leading dash is read as the address


async def test_a_listing_tool_that_will_not_run_still_answers_with_a_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing, or hung and killed. Everywhere else in the slice that failure lands on the queue as
    a message; here there is no download yet, so without this it is a server error and no words."""

    async def will_not_run(_command: list[str], **_kwargs: object) -> subproc.SubprocessResult:
        raise subproc.SubprocessError("yt-dlp is not installed")

    monkeypatch.setattr(subproc, "run", will_not_run)
    with pytest.raises(bulk.BulkRefused) as refusal:
        await bulk.find_items(_PLAYLIST)
    assert "could not ask" in str(refusal.value)


async def test_a_listing_the_proxy_refused_says_so_and_not_that_the_site_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.kernel.public_net import Refusal, Traffic
    from sift.slices.download.sources.errors import PRIVATE_NETWORK_REFUSED

    monkeypatch.setattr(subproc, "run", _Listing(returncode=1))
    monkeypatch.setattr(
        argv,
        "proxy_traffic",
        lambda _command: Traffic(
            connections=1,
            sent=90,
            received=0,
            refused=(Refusal("192.168.1.9:443", "private_address"),),
        ),
    )
    with pytest.raises(bulk.BulkRefused) as caught:
        await bulk.find_items("https://www.youtube.com/playlist?list=PL1")
    assert str(caught.value) == PRIVATE_NETWORK_REFUSED
