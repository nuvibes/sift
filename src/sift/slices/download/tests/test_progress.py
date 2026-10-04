# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far along a download is, and the arithmetic every seam would otherwise repeat differently.

The shape of these tests follows the shape of the thing: each seam reports what it can honestly see,
and everything derived from those readings (the rate, the time left, the fraction) happens in one
place so four seams cannot produce four different answers to "how fast is this".
"""

from __future__ import annotations

from sift.slices.download.sources import progress


class _Clock:
    """A clock a test drives. Real time makes a rate test either slow or flaky."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_total_nobody_knows_is_absent_rather_than_zero() -> None:
    """The difference is what a screen draws: no total means no bar, zero would mean a full one."""
    assert progress.Progress(done_bytes=5).fraction is None
    assert progress.Progress(done_bytes=5, total_bytes=0).fraction is None


def test_files_answer_when_bytes_cannot() -> None:
    """A gallery reports files because its tool cannot report bytes, and that still draws a bar."""
    reading = progress.Progress(done_files=212, total_files=400)
    assert reading.fraction is not None
    assert 0.5 < reading.fraction < 0.55


def test_a_fraction_never_passes_the_end() -> None:
    """A site that under-declares its length would otherwise produce a bar past its own end."""
    assert progress.Progress(done_bytes=120, total_bytes=100).fraction == 1.0


def test_the_rate_is_worked_out_from_what_moved_and_how_long_it_took() -> None:
    clock = _Clock()
    watcher = progress.Watcher(clock=clock)
    watcher.update(progress.Progress(done_bytes=0, total_bytes=1000))
    clock.now += 2.0
    latest = watcher.update(progress.Progress(done_bytes=500, total_bytes=1000))
    assert latest.bytes_per_second == 250.0
    assert latest.seconds_left == 2.0


def test_nothing_having_moved_yet_reports_no_rate_rather_than_zero() -> None:
    """Zero is a claim about speed. Not knowing yet is not the same claim, and a "0 KB/s" beside a
    download that has simply not started is what makes somebody cancel a working job."""
    clock = _Clock()
    watcher = progress.Watcher(clock=clock)
    latest = watcher.update(progress.Progress(done_bytes=0, total_bytes=1000))
    assert latest.bytes_per_second is None
    assert latest.seconds_left is None


def test_a_transfer_starting_over_is_measured_from_the_restart() -> None:
    """A retry, or the next item of an album, goes backwards. Measured from the original anchor the
    rate would be a fraction of what the transfer is really doing, for as long as it ran before."""
    clock = _Clock()
    watcher = progress.Watcher(clock=clock)
    watcher.update(progress.Progress(done_bytes=900))
    clock.now += 100.0
    watcher.update(progress.Progress(done_bytes=0))  # started again
    clock.now += 1.0
    latest = watcher.update(progress.Progress(done_bytes=500))
    assert latest.bytes_per_second == 500.0


def test_no_total_means_a_rate_but_no_time_left() -> None:
    """Half an answer is the honest one: how fast is knowable, how long is not."""
    clock = _Clock()
    watcher = progress.Watcher(clock=clock)
    watcher.update(progress.Progress(done_bytes=0))
    clock.now += 1.0
    latest = watcher.update(progress.Progress(done_bytes=100))
    assert latest.bytes_per_second == 100.0
    assert latest.seconds_left is None


def test_a_finished_transfer_has_no_time_left_rather_than_a_negative_one() -> None:
    clock = _Clock()
    watcher = progress.Watcher(clock=clock)
    watcher.update(progress.Progress(done_bytes=0, total_bytes=100))
    clock.now += 1.0
    latest = watcher.update(progress.Progress(done_bytes=100, total_bytes=100))
    assert latest.seconds_left == 0.0


# --- the registry -------------------------------------------------------------------------------


def test_what_is_not_running_has_no_progress() -> None:
    assert progress.Registry().of("never-started") is None


def test_a_reporter_feeds_the_registry_the_download_it_belongs_to() -> None:
    registry = progress.Registry()
    report = registry.reporter("d1")
    report(progress.Progress(done_bytes=42))
    latest = registry.of("d1")
    assert latest is not None
    assert latest.done_bytes == 42
    assert registry.of("d2") is None


def test_a_finished_download_is_forgotten() -> None:
    """Otherwise this grows by one entry per download for as long as the process runs, and the line
    above the queue counts finished work as still going."""
    registry = progress.Registry()
    registry.reporter("d1")(progress.Progress(done_bytes=1))
    registry.forget("d1")
    assert registry.of("d1") is None
    assert registry.all() == {}


def test_forgetting_something_that_was_never_there_is_harmless() -> None:
    """A download that failed before it moved a byte never reported, and the way out still runs."""
    progress.Registry().forget("never-started")


# --- reading what the tools say -----------------------------------------------------------------


def test_the_video_tools_line_carries_real_bytes_and_a_real_total() -> None:
    reading = progress.read_tool_line("sift-progress 1048576 10485760 10485760")
    assert reading is not None
    assert reading.done_bytes == 1048576
    assert reading.total_bytes == 10485760


def test_an_estimate_is_used_where_the_real_total_is_not_known_yet() -> None:
    """A fragmented download only learns its true size at the end. An approximate bar for four
    minutes is worth far more than no bar at all."""
    reading = progress.read_tool_line("sift-progress 500 NA 4096")
    assert reading is not None
    assert reading.total_bytes == 4096


def test_no_total_at_all_still_reports_what_has_moved() -> None:
    reading = progress.read_tool_line("sift-progress 500 NA NA")
    assert reading is not None
    assert reading.done_bytes == 500
    assert reading.total_bytes is None


def test_anything_that_is_not_a_progress_line_is_not_read_as_one() -> None:
    """Most of what a tool prints is not progress, and the marker is what makes that unambiguous
    rather than a matter of guessing at shape."""
    for line in (
        "[download] Destination: /work/video.mp4",
        "[youtube] Extracting URL",
        "sift-progress",
        "sift-progress not-a-number NA NA",
        "",
    ):
        assert progress.read_tool_line(line) is None


def test_the_gallery_tool_is_read_for_files_rather_than_bytes() -> None:
    assert progress.looks_like_a_finished_file("/work/staging/photo-01.jpg") is True
    assert progress.looks_like_a_finished_file("# /work/staging/photo-02.jpg") is True
    assert progress.looks_like_a_finished_file("[twitter][error] 404") is False
    assert progress.looks_like_a_finished_file("") is False


def test_one_file_with_no_byte_total_still_draws_a_bar_from_its_files() -> None:
    """The order matters and it is not obvious.

    A paste of many things is answered in FILES, because the bytes belong to whichever item is
    being fetched and a bar reported that way runs to the end and starts again once per item. A
    single file is the other way round (bytes move continuously and a count of one is either 0 or
    1), so bytes answer first. This is the case left over: one file, and a tool that cannot report
    bytes at all. Without the third reading it draws no bar rather than an honest half of one.
    """
    assert progress.Progress(done_files=0, total_files=1).fraction == 0.0
    assert progress.Progress(done_files=1, total_files=1).fraction == 1.0


def test_the_video_tool_says_which_item_of_how_many_it_is_on() -> None:
    """It arrives before the bytes of each item, so a row can say "3 of 10" for the whole paste
    instead of restarting a bar at zero ten times with nothing to say why."""
    assert progress.read_playlist_line("[download] Downloading item 3 of 10") == (3, 10)
    assert progress.read_playlist_line("[download] Destination: clip.mp4") is None


def test_a_held_download_keeps_its_figures_and_drops_its_rate() -> None:
    """Pause keeps what the row can truthfully say (how much is on disk) and drops what it
    cannot: a speed and a time left are about a fetch that has stopped. Summed into the strip they
    would read as "0 downloading, 7.3 MB/s, about 21s left"."""
    registry = progress.Registry()
    report = registry.reporter("d1")
    report(progress.Progress(done_bytes=0, total_bytes=1000))
    report(progress.Progress(done_bytes=500, total_bytes=1000))

    registry.hold("d1")

    held = registry.of("d1")
    assert held is not None, "held, not forgotten: the row still has a figure to draw"
    assert held.done_bytes == 500
    assert held.bytes_per_second is None
    assert held.seconds_left is None
    assert "d1" in registry.all()

    registry.forget("d1")
    assert registry.of("d1") is None


def test_holding_something_that_never_reported_is_harmless() -> None:
    """A download paused before it moved a byte has no figures to keep, and the pause still runs."""
    registry = progress.Registry()
    registry.hold("never-started")
    assert registry.of("never-started") is None
