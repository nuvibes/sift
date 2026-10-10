# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the end of a file without reading the file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sift.slices.logs.tail import LINE_CAP, MOST_LINES, newest_matching, tail_of


def test_the_last_few_lines_come_back_oldest_first(tmp_path: Path) -> None:
    """The order they were written in, which is the order anything quoting them is read in."""
    held = tmp_path / "sift.log"
    held.write_text("\n".join(f"line {n}" for n in range(100)) + "\n", encoding="utf-8")

    assert tail_of(held, 3) == ["line 97", "line 98", "line 99"]


def test_a_file_shorter_than_what_was_asked_for_comes_back_whole(tmp_path: Path) -> None:
    held = tmp_path / "sift.log"
    held.write_text("one\ntwo\n", encoding="utf-8")

    assert tail_of(held, 50) == ["one", "two"]


def test_it_reads_backwards_rather_than_reading_the_whole_file(tmp_path: Path) -> None:
    """The property this exists for, forced with a file bigger than one block.

    The log's ceiling is a gigabyte. A tail that read the whole thing would hold a gigabyte in
    memory to show a screenful, on the machine somebody is asking because something is already
    wrong with it. Sixty-four kilobytes is the block, so a file well past that proves the loop runs
    more than once AND that the seek arithmetic is right: a version that read one block and
    stopped would answer with the wrong lines here, not with fewer.
    """
    held = tmp_path / "sift.log"
    held.write_text("".join(f"{n:0>7}\n" for n in range(40_000)), encoding="utf-8")

    assert tail_of(held, 2) == ["0039998", "0039999"]


def test_it_reads_only_what_it_needs_from_the_end(tmp_path: Path) -> None:
    """The property the whole function exists for, and the only test here that can see it.

    Every other test asserts the ANSWER, and reading the whole file gives the same answer, slower.
    So a version that seeks and a version that does not are indistinguishable from the outside, and
    a whole-file read in place of the loop would pass everything else. What separates them
    is how much was read, so that is what this counts.

    Five megabytes, and one screenful asked for. The bound is generous on purpose: it is not a
    measurement of the block size, it is the difference between bounded and not.
    """
    held = tmp_path / "sift.log"
    held.write_bytes(b"".join(b"%07d %s\n" % (n, b"p" * 500) for n in range(10_000)))
    assert held.stat().st_size > 5_000_000, "the file has to be big enough for the answer to matter"

    read = 0
    opener = Path.open

    def counting(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = opener(self, *args, **kwargs)
        inner = handle.read

        def watched(size: int = -1) -> Any:
            nonlocal read
            got = inner(size)
            read += len(got)
            return got

        handle.read = watched
        return handle

    with patch.object(Path, "open", counting):
        found = tail_of(held, 5)

    assert len(found) == 5
    assert found[-1].startswith("0009999")
    assert read < 1_000_000, f"read {read} bytes of a 5 MB file to show five lines"


def test_the_lines_asked_for_are_found_even_when_they_span_several_reads(tmp_path: Path) -> None:
    """More lines wanted than one block holds, which is what a padded log at Detailed looks like.

    A loop that stopped at the first newline it saw would pass every other test here, because on a
    file of short lines one block already holds hundreds. Five hundred bytes a line and two hundred
    lines wanted is a hundred kilobytes, more than one read, whatever the block size.
    """
    held = tmp_path / "sift.log"
    held.write_bytes(b"".join(b"%04d %s\n" % (n, b"q" * 500) for n in range(400)))

    found = tail_of(held, 200)

    assert len(found) == 200
    assert found[0].startswith("0200")
    assert found[-1].startswith("0399")


def test_a_line_longer_than_a_block_comes_back_whole(tmp_path: Path) -> None:
    """A line bigger than one read, which is what a stack trace or a whole SQL statement is.

    That the half-read leading piece is DROPPED cannot be made to fail: the loop reads one newline
    more than it was asked for, so a fragment is always outside the window. What is worth
    asserting is the thing that does hold: a line spanning several
    blocks is reassembled rather than returned in pieces.
    """
    held = tmp_path / "sift.log"
    held.write_text("x" * 200_000 + "\nlast\n", encoding="utf-8")

    found = tail_of(held, 5)

    assert found[-1] == "last"
    assert found[0] == "x" * LINE_CAP, "the giant line is one line, cut only by the length cap"
    assert len(found) == 2


def test_a_log_that_is_not_there_is_an_empty_answer_and_not_an_error(tmp_path: Path) -> None:
    """No log file is an ordinary state (the size may be set to nothing, or the application may
    have only just started) and it is not a fault to report."""
    assert tail_of(tmp_path / "nothing.log", 10) == []
    assert tail_of(tmp_path, 10) == [], "a directory is not a log either"


def test_an_empty_file_is_an_empty_answer(tmp_path: Path) -> None:
    held = tmp_path / "sift.log"
    held.write_bytes(b"")

    assert tail_of(held, 10) == []


def test_asking_for_more_than_the_cap_gets_the_cap(tmp_path: Path) -> None:
    """The bound is here rather than only on the route, so a caller inside the application cannot
    ask for a library's worth of lines by not going through one."""
    held = tmp_path / "sift.log"
    held.write_text("".join(f"line {n}\n" for n in range(MOST_LINES + 50)), encoding="utf-8")

    assert len(tail_of(held, MOST_LINES + 50)) == MOST_LINES


def test_asking_for_nothing_reads_nothing(tmp_path: Path) -> None:
    held = tmp_path / "sift.log"
    held.write_text("one\n", encoding="utf-8")

    assert tail_of(held, 0) == []


def test_a_line_too_long_to_show_is_cut_rather_than_dropped(tmp_path: Path) -> None:
    """A very long line (a stack trace, or a whole SQL statement at Detailed) is usually the
    interesting one, so it is truncated rather than left out."""
    held = tmp_path / "sift.log"
    held.write_text("y" * (LINE_CAP + 500) + "\n", encoding="utf-8")

    (found,) = tail_of(held, 5)
    assert len(found) == LINE_CAP


def test_bytes_that_are_not_text_do_not_stop_the_read(tmp_path: Path) -> None:
    """A log holds whatever was written to it. A page that refused to draw because one line was odd
    would be useless exactly when it is wanted."""
    held = tmp_path / "sift.log"
    held.write_bytes(b"good\n\xff\xfe not text\nalso good\n")

    found = tail_of(held, 5)
    assert found[0] == "good"
    assert found[-1] == "also good"
    assert len(found) == 3


def test_a_log_file_that_is_not_there_is_an_empty_answer_rather_than_an_error(
    tmp_path: Path,
) -> None:
    """No log file is an ordinary state (a fresh install, a rotation that has just happened, a
    shell that has never been asked for one) and a screen that errors on it reads as the log
    being broken rather than as there being nothing yet."""
    assert tail_of(tmp_path / "never-written.log", 10) == []


def test_a_file_that_cannot_be_read_partway_through_is_the_same_empty_answer(
    tmp_path: Path,
) -> None:
    """The read itself can fail after the open succeeds: a file rotated out from under it, a
    share that went away. The same answer, because the alternative is a settings pane that raises
    while somebody is trying to find out what went wrong."""
    written = tmp_path / "sift.log"
    written.write_bytes(b"one\ntwo\nthree\n")

    with patch("sift.slices.logs.tail.Path.open", side_effect=OSError("gone")):
        assert tail_of(written, 2) == []

    # The known positive: the same file, read normally, is not empty.
    assert tail_of(written, 2) == ["two", "three"]


def test_a_page_for_a_log_that_is_not_there_says_so_rather_than_showing_nothing(
    tmp_path: Path,
) -> None:
    """`present` is the difference between "there is no log" and "the log is empty", and the screen
    says a different thing for each. Without it a missing file reads as a working one with nothing
    in it, which is the answer somebody would stop investigating at."""
    from sift.slices.logs.router import _read

    page = _read(tmp_path / "never-written.log", 10)

    assert page.present is False
    assert page.lines == []
    assert page.path.endswith("never-written.log")


def _record(level: str, event: str, **fields: object) -> str:
    return json.dumps(
        {"timestamp": "2026-09-25T10:00:00Z", "level": level, "event": event, **fields}
    )


def test_a_narrowed_read_goes_on_into_the_rotated_files(tmp_path: Path) -> None:
    """A rotation a second before somebody looks leaves the current file nearly empty and the error
    they came for in `.1`, which is older than every line in the current file and so comes first."""
    current = tmp_path / "sift.log"
    current.write_text(_record("info", "fresh") + "\n", encoding="utf-8")
    (tmp_path / "sift.log.1").write_text(
        _record("error", "older.failure") + "\n" + _record("info", "older.fine") + "\n",
        encoding="utf-8",
    )

    found = newest_matching(
        [current, tmp_path / "sift.log.1", tmp_path / "sift.log.2"], 10, lambda line: True
    )

    assert [json.loads(one)["event"] for one in found.lines] == [
        "older.failure",
        "older.fine",
        "fresh",
    ]
    assert found.whole is True, "every file was read to its start, and a missing .2 is a gap"


def test_a_narrowed_read_that_runs_out_of_budget_says_it_did_not_look_at_everything(
    tmp_path: Path,
) -> None:
    """A search that matches nothing must not read a gigabyte to say so, and must not say "there
    is nothing" either, when what it means is that it stopped looking."""
    current = tmp_path / "sift.log"
    current.write_bytes(b"".join(b"%07d %s\n" % (n, b"z" * 200) for n in range(20_000)))

    found = newest_matching([current], 10, lambda line: False, budget=256 * 1024)

    assert found.lines == []
    assert found.whole is False
    assert found.read_bytes <= 256 * 1024 + 64 * 1024, "one block past the budget at most"

    # The known positive: with room to read it all, the same read reaches the start.
    assert newest_matching([current], 10, lambda line: False).whole is True


def test_a_level_keeps_that_level_and_everything_louder(tmp_path: Path) -> None:
    from sift.slices.logs.router import _read

    held = tmp_path / "sift.log"
    held.write_text(
        "\n".join(
            [
                _record("debug", "a.step"),
                _record("info", "b.happened"),
                _record("warning", "c.odd"),
                _record("error", "d.failed"),
                _record("critical", "e.stopped"),
                "not one of sift's lines",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    warnings = _read(held, 50, level="warning")
    everything = _read(held, 50, level="debug")

    assert [one.event for one in warnings.lines] == ["c.odd", "d.failed", "e.stopped"]
    assert len(everything.lines) == 6, "every level, and the line nobody can rate"


def test_a_search_reads_the_event_and_its_fields_and_not_the_level(tmp_path: Path) -> None:
    from sift.slices.logs.router import _read

    held = tmp_path / "sift.log"
    held.write_text(
        "\n".join(
            [
                _record("info", "scan.started", folder="Holiday"),
                _record("info", "download.finished", site="example"),
                _record("warning", "scan.slow", folder="holiday"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    by_field = _read(held, 50, search="HOLIDAY")
    by_event = _read(held, 50, search="download")
    by_level = _read(held, 50, search="info")
    both = _read(held, 50, level="warning", search="holiday")

    assert [one.event for one in by_field.lines] == ["scan.started", "scan.slow"]
    assert [one.event for one in by_event.lines] == ["download.finished"]
    assert by_level.lines == [], "every line says its level; a search for it narrows nothing"
    assert [one.event for one in both.lines] == ["scan.slow"]


def test_a_read_filled_by_the_first_line_of_a_file_stops_before_the_rotated_ones(
    tmp_path: Path,
) -> None:
    """The first line of a file is the one carried to the end, since nothing before it can finish
    it, and when it is the last line wanted, the older files are not opened at all. The answer
    says it stopped early, because it did not look at them."""
    current = tmp_path / "sift.log"
    current.write_text("first\nsecond\nthird\n", encoding="utf-8")
    older = tmp_path / "sift.log.1"
    older.write_text("older\n", encoding="utf-8")

    found = newest_matching([current, older], 3)

    assert found.lines == ["first", "second", "third"]
    assert found.whole is False
    assert found.read_bytes == current.stat().st_size, "the rotated file was never read"


def test_a_block_without_the_search_is_passed_over_and_the_answer_is_the_same(
    tmp_path: Path,
) -> None:
    from sift.slices.logs.router import _keeps

    current = tmp_path / "sift.log"
    lines = [_record("info", "filler", n=n, pad="z" * 200) for n in range(3000)]
    lines[5] = _record("info", "found.me", folder="Needle")
    lines[2500] = _record("info", "found.me.too", folder="needle")
    current.write_text("\n".join(lines) + "\n", encoding="utf-8")
    keep = _keeps(None, "needle")
    assert keep is not None

    plain = newest_matching([current], 10, keep)
    quick = newest_matching([current], 10, keep, within=b"needle")

    assert quick == plain
    assert [json.loads(one)["event"] for one in quick.lines] == ["found.me", "found.me.too"]


def test_a_block_holding_an_escaped_character_is_always_read_line_by_line(tmp_path: Path) -> None:
    """A sharp s folds to "ss", and the line holds it escaped, so the bytes cannot rule it out."""
    from sift.slices.logs.router import _keeps

    current = tmp_path / "sift.log"
    # Not the file's first line, which is always read whole at the end.
    current.write_text(
        _record("info", "first") + "\n" + _record("info", "walked", folder="Stra\u00dfe") + "\n",
        encoding="utf-8",
    )
    keep = _keeps(None, "strasse")
    assert keep is not None

    found = newest_matching([current], 10, keep, within=b"strasse")

    assert [json.loads(one)["event"] for one in found.lines] == ["walked"]


def test_a_job_s_id_finds_its_lines_anywhere_in_the_log_with_its_summary_first(
    tmp_path: Path,
) -> None:
    """Past the search's budget too: a job ran an hour ago is still the one being asked about."""
    import importlib

    # The module, not the package's `router` attribute, which is the routes object.
    router = importlib.import_module("sift.slices.logs.router")

    job = "01M4HAJYWS66S29SPZ4P85SHKE"
    older = tmp_path / "sift.log.1"
    older.write_text(
        "\n".join(
            [
                _record("info", "job.claimed", job_id=job),
                _record("info", "content.probed", job_id=job),
                _record("info", "job.summary", job_id=job, wall_ms=12),
                _record("info", "job.summary", job_id="01M4HAJYWS66S29SPZ4P85SHKF"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    current = tmp_path / "sift.log"
    current.write_bytes(b"".join(b"%07d %s\n" % (n, b"z" * 200) for n in range(4000)))

    with patch.object(router, "SEARCH_BUDGET", 64 * 1024):
        page = router._read(current, 50, search=job.lower())
        other = router._read(current, 50, search="content.probed")

    assert [one.event for one in page.lines] == ["job.summary", "job.claimed", "content.probed"]
    assert page.whole is True
    assert other.lines == [] and other.whole is False, "any other search keeps its budget"
