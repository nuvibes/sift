# SPDX-License-Identifier: AGPL-3.0-or-later
"""The edges of the download seams: a tunnel gone, a preference unreadable, an unnamed status, a
tool silent about progress. Ordinary cases here, where a plausible answer does the most damage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.tunnels import DIRECT_LABEL, EgressRouter, TunnelError
from sift.slices.download import naming
from sift.slices.download.router import _item
from sift.slices.download.service import DownloadView
from sift.slices.download.sources import failures, policy, progress
from sift.slices.download.sources.downloader import Downloader, _watching
from sift.slices.download.sources.registry import Backend
from sift.slices.download.sources.sites.catalog import site_key_of
from sift.slices.download.sources.tuning import POLICY, RunPolicy

pytestmark = pytest.mark.anyio

_URL = "https://www.youtube.com/watch?v=1"


# --- which way out a row says it went ----------------------------------------------------------


async def test_a_download_that_went_out_directly_says_so() -> None:
    async with EgressRouter({}).take(_URL) as taken:
        assert (taken.label, taken.proxy, taken.tunnel_id, taken.address) == (
            DIRECT_LABEL,
            None,
            None,
            None,
        )


async def test_a_site_pointed_at_a_tunnel_that_is_gone_refuses_rather_than_naming_anything() -> (
    None
):
    """A site pointed at a removed tunnel refuses rather than going out directly."""

    async def routed_through_a_ghost(_key: str) -> str:
        return "a-tunnel-that-was-deleted"

    router = EgressRouter({}, read_site=routed_through_a_ghost, site_of=site_key_of)
    with pytest.raises(TunnelError, match="no longer exists"):
        async with router.take(_URL):
            pytest.fail("a gone tunnel let the download out directly")


# --- naming, when the filesystem says no --------------------------------------------------------


async def test_a_rename_the_filesystem_refuses_leaves_the_download_alone(tmp_path: Path) -> None:
    """Naming is a convenience and must never be able to lose a file that arrived intact."""
    staged = tmp_path / "video.mp4"
    staged.write_bytes(b"the download")
    # A name with a separator in it cannot be created as a single file, and the folder it would need
    # does not exist, so the rename raises rather than landing somewhere unexpected.
    landed = await naming.rename(staged, "{name}", naming.Facts(original="a/b/c" * 60, when=None))
    assert landed.read_bytes() == b"the download"


# --- preferences that will not read back --------------------------------------------------------


@pytest.mark.parametrize("stored", ["not a number", None, ""])
async def test_a_size_bound_that_is_not_a_number_becomes_no_bound(stored: object) -> None:
    """No bound rather than a bound of zero. Zero would filter out every file there is."""

    async def get_app(key: str) -> object:
        return stored if key == policy.SKIP_LARGER_KEY else policy.DEFAULTS[key]

    read = await policy.read_policy(get_app)
    assert read.filters.at_most_bytes is None


@pytest.mark.parametrize("stored", ["not a number", None, ""])
async def test_a_bandwidth_cap_that_is_not_a_number_becomes_no_cap(stored: object) -> None:
    async def get_app(key: str) -> object:
        return stored if key == policy.BANDWIDTH_KEY else policy.DEFAULTS[key]

    read = await policy.read_policy(get_app)
    assert read.pacing.bytes_per_second is None


# --- a status nobody standardised ---------------------------------------------------------------


def test_a_status_number_no_standard_names_is_reported_without_inventing_a_phrase() -> None:
    """Made-up statuses do occur: a middlebox, a misconfigured server. Saying the number and
    admitting it is not standard beats attaching a phrase that means nothing."""
    found = failures.classify("https://unknown.example/x", "HTTP Error 599: ")
    assert found is not None
    assert "599" in found.sentence
    assert "not a standard" in found.sentence


# --- watching a tool report on itself -----------------------------------------------------------


def test_the_video_tool_is_watched_for_bytes() -> None:
    seen: list[progress.Progress] = []
    watch = _watching(Backend.YTDLP, seen.append)

    watch("[youtube] Extracting URL: something")  # not progress, and not reported as any
    watch("sift-progress 500 1000 1000")

    assert len(seen) == 1
    assert seen[0].done_bytes == 500
    assert seen[0].total_bytes == 1000


def test_the_gallery_tool_is_watched_for_files() -> None:
    """The gallery tool is watched for which file it is on, its one honest progress."""
    seen: list[progress.Progress] = []
    watch = _watching(Backend.GALLERYDL, seen.append)

    watch("/work/staging/one.jpg")
    watch("[twitter][error] 404")  # not a file landing
    watch("/work/staging/two.jpg")

    assert [reading.done_files for reading in seen] == [1, 2]


def test_a_paste_of_many_things_is_counted_in_items_and_keeps_the_count() -> None:
    """The item count is carried across byte reports, and is one behind the item being fetched."""
    seen: list[progress.Progress] = []
    watch = _watching(Backend.YTDLP, seen.append)

    watch("[download] Downloading item 3 of 10")
    watch("sift-progress 500 1000 1000")

    assert [(one.done_files, one.total_files) for one in seen] == [(2, 10), (2, 10)]
    assert seen[1].done_bytes == 500


# --- what a run is told when nobody wired preferences -------------------------------------------


async def test_a_downloader_with_no_preferences_wired_uses_the_chosen_defaults() -> None:
    """A build with nothing reading settings still has to pace itself. The defaults are the answer,
    not an absence of one: unpaced traffic is what the whole arrangement exists to prevent."""
    assert await Downloader()._policy() == POLICY


async def test_a_downloader_reads_its_preferences_when_one_is_wired() -> None:
    chosen = RunPolicy(verbose=True)

    async def read() -> RunPolicy:
        return chosen

    assert await Downloader(read_policy=read)._policy() is chosen


# --- the arithmetic above the list ---------------------------------------------------------------


def test_a_queue_with_one_unknown_total_reports_no_time_left_at_all() -> None:
    """One unknown total makes the whole queue report no time left."""
    from sift.slices.download.router import _summary

    live = {
        "a": progress.Progress(done_bytes=50, total_bytes=100, bytes_per_second=10, seconds_left=5),
        "b": progress.Progress(done_bytes=50, bytes_per_second=10),  # no total, so no ending
    }
    summary = _summary(running=2, queued=0, live=live, by_state={})
    assert summary.bytes_per_second == 20
    assert summary.seconds_left is None


def test_a_queue_where_everything_knows_its_size_reports_the_longest_one() -> None:
    """The longest, not the sum: they run at the same time, so the queue ends when the slowest does."""
    from sift.slices.download.router import _summary

    live = {
        "a": progress.Progress(done_bytes=1, total_bytes=2, bytes_per_second=1, seconds_left=4),
        "b": progress.Progress(done_bytes=1, total_bytes=2, bytes_per_second=1, seconds_left=9),
    }
    assert _summary(running=2, queued=3, live=live, by_state={}).seconds_left == 9


# --- how much a failure's sentence is worth -------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "tier"),
    [
        ("wall-age", 3),
        ("http-522", 2),
        ("http-404", 1),
        ("disk-full", 2),
        (None, None),
    ],
)
def test_a_failure_code_says_how_much_its_sentence_is_worth(
    code: str | None, tier: int | None
) -> None:
    """A phrase written about this site by somebody who watched it refuse is worth more than a
    status code's standard wording, and a screen has to be able to tell the two apart."""
    from sift.slices.download.jobs import _tier_of

    assert _tier_of(code) == tier


# --- what a site declares about its own size -------------------------------------------------------


def test_a_declared_size_that_is_not_a_number_is_treated_as_no_size() -> None:
    """None rather than zero, and the difference is what a screen draws: no size means no bar, and a
    zero would mean a bar already full."""
    from sift.slices.download.sources.fetcher import _declared_size

    class _Headers:
        def __init__(self, value: str | None) -> None:
            self._value = value

        def get(self, _name: str) -> str | None:
            return self._value

    class _Response:
        def __init__(self, value: str | None) -> None:
            self.headers = _Headers(value)

    assert _declared_size(_Response("nonsense")) is None  # type: ignore[arg-type]
    assert _declared_size(_Response(None)) is None  # type: ignore[arg-type]
    assert _declared_size(_Response("0")) is None  # type: ignore[arg-type]
    assert _declared_size(_Response("2048")) == 2048  # type: ignore[arg-type]


# --- the job's own seams -------------------------------------------------------------------------


async def test_a_finished_download_is_forgotten_by_what_watches_it() -> None:
    """Otherwise the record of what is running grows by one entry per download for as long as the
    process lives, and the line above the queue counts finished work as still going."""
    watching = progress.Registry()
    watching.reporter("d1")(progress.Progress(done_bytes=1))
    assert watching.of("d1") is not None
    watching.forget("d1")
    assert watching.all() == {}


async def test_a_link_from_a_site_with_no_record_gets_no_special_treatment() -> None:
    """Naming and destination are per site, and a link from somewhere Sift has no record for has
    no site, so it follows whatever everything follows rather than nothing at all."""
    from sift.slices.download.jobs import _options_for
    from sift.slices.download.site_options import SiteOptions

    asked: list[str | None] = []

    async def read(key: str | None) -> SiteOptions:
        asked.append(key)
        return SiteOptions(naming="{name}")

    assert (await _options_for(read, "https://nowhere-sift-knows.example/x")).naming == "{name}"
    assert asked == [None]

    # And with nothing wired at all (a test, a bare build), there is simply nothing special.
    assert (await _options_for(None, "https://www.youtube.com/watch?v=1")).naming is None


def test_a_tunnel_knows_the_name_somebody_gave_it() -> None:
    """It is what a download's row says it went out through, so it has to survive out of the
    process that holds the configuration."""
    from sift.kernel.tunnels import TunnelProcess, TunnelSpec

    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Iceland"))
    assert tunnel.name == "Iceland"


async def test_a_rename_onto_an_impossible_name_keeps_the_file(tmp_path: Path) -> None:
    """The last guard on naming: whatever the filesystem refuses, the download itself survives."""
    staged = tmp_path / "video.mp4"
    staged.write_bytes(b"the download")

    # A name far past every filesystem's limit once the template stops trimming it. The rename
    # raises, and the file is left exactly where it was.
    landed = await naming.rename(staged, "{name}", naming.Facts(original="x" * 400))
    assert landed.exists()


async def test_a_block_on_where_the_request_came_from_names_the_fix(tmp_path: Path) -> None:
    """A block on the request's origin names the tunnel fix, only when the download went direct."""
    from sift.slices.download.jobs import _record_failure
    from sift.slices.download.sources.errors import DownloadError

    written: list[tuple[str, str | None, int | None]] = []

    class _Ledger:
        async def mark_failed(
            self, _id: str, *, error: str, code: str | None = None, tier: int | None = None
        ) -> None:
            written.append((error, code, tier))

    wall = DownloadError(
        "The site does not serve this here.", code="wall-x", a_tunnel_would_help=True
    )

    await _record_failure(_Ledger(), "d1", wall, direct=True)  # type: ignore[arg-type]
    assert "Route this Site through a tunnel" in written[0][0]
    assert written[0] == (written[0][0], "wall-x", 3)

    await _record_failure(_Ledger(), "d2", wall, direct=False)  # type: ignore[arg-type]
    assert "Route this Site through a tunnel" not in written[1][0]


async def test_a_site_with_no_wall_matching_still_falls_through_to_the_lower_tiers() -> None:
    """A site that HAS known refusals and gave a different one. The loop has to finish rather
    than stopping at the first phrase it checked."""
    found = failures.classify(
        "https://www.youtube.com/watch?v=1", "ERROR: HTTP Error 404: Not Found"
    )
    assert found is not None
    assert found.tier < 3


async def test_a_rename_the_filesystem_will_not_do_leaves_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename the filesystem refuses leaves the downloaded file."""
    staged = tmp_path / "video.mp4"
    staged.write_bytes(b"the download")

    def refuse(self: Path, _target: Path) -> Path:
        raise OSError("the filesystem said no")

    monkeypatch.setattr(Path, "rename", refuse)
    landed = await naming.rename(staged, "{name}", naming.Facts(original="fine"))
    assert landed == staged
    assert staged.read_bytes() == b"the download"


# --- how many downloads may run ------------------------------------------------------------------


def test_paused_means_none_may_start_whatever_the_cap_says() -> None:
    """A cap of nothing is what the worker pool already understands as paused, so there is one
    mechanism rather than two answers to whether a download may start."""
    from sift.slices.download import at_once_limit

    assert at_once_limit(4, True) == 0
    assert at_once_limit(0, True) == 0


def test_no_cap_leaves_downloads_sharing_the_workers() -> None:
    """The right answer for somebody who has never opened the setting."""
    from sift.slices.download import at_once_limit

    assert at_once_limit(0, False) is None
    assert at_once_limit("not a number", False) is None
    assert at_once_limit(None, False) is None


def test_a_chosen_cap_is_honoured_when_nothing_is_paused() -> None:
    from sift.slices.download import at_once_limit

    assert at_once_limit(3, False) == 3


# --- the mark on a row ---------------------------------------------------------------------------


def test_a_site_key_comes_from_the_address_rather_than_from_what_it_was_filed_under() -> None:
    """A site key comes from the address, so the row has its mark while downloading. An unknown
    site has none."""
    from sift.slices.download.service import _site_of

    assert _site_of("https://www.youtube.com/watch?v=1") == "youtube"
    assert _site_of("https://a-site-nobody-added.example/x") is None
    assert _site_of(None) is None
    assert _site_of("") is None


def test_a_total_the_site_never_declared_is_marked_as_a_guess() -> None:
    """A total the site never declared travels marked as a guess."""
    from sift.slices.download.sources.progress import read_tool_line

    declared = read_tool_line("sift-progress 100 5000 4800")
    assert declared is not None
    assert (declared.total_bytes, declared.total_is_estimated) == (5000, False)

    guessed = read_tool_line("sift-progress 100 NA 4800")
    assert guessed is not None
    assert (guessed.total_bytes, guessed.total_is_estimated) == (4800, True)

    # Neither known: no total at all, and nothing to call a guess.
    nothing = read_tool_line("sift-progress 100 NA NA")
    assert nothing is not None
    assert (nothing.total_bytes, nothing.total_is_estimated) == (None, False)


def _held_view(status: str) -> DownloadView:
    return DownloadView(
        id="d1",
        status=status,
        dest_folder_id=None,
        site=None,
        username=None,
        asset_id=None,
        error=None,
        created_at=0,
    )


@pytest.mark.parametrize(
    ("status", "carries"),
    [("running", True), ("paused", True), ("done", False), ("failed", False), ("queued", False)],
)
def test_a_row_carries_its_live_figure_while_running_or_held(status: str, carries: bool) -> None:
    """A running or paused row carries its live figure; a settled row never does."""
    reading = progress.Progress(done_bytes=8_100_000, total_bytes=226_000_000)

    item = _item(_held_view(status), live={"d1": reading})

    assert (item.progress is not None) is carries
    if carries:
        assert item.progress is not None
        assert item.progress.done_bytes == 8_100_000
