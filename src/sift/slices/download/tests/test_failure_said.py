# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed download's row, worded when it is shown from the code it recorded and its site.

A stored sentence keeps its words for good, however stale. Wherever a code was recorded the row
says what this build knows about that code on that site now; the stored sentence stands only where
no code was recorded.
"""

from __future__ import annotations

from dataclasses import replace

from sift.slices.download.router import _failure_said
from sift.slices.download.service import DownloadView
from sift.slices.download.sources import failures


def _row(code: str | None, url: str, *, via: str | None = None) -> DownloadView:
    return DownloadView(
        id="01D",
        status="failed",
        dest_folder_id=None,
        site=None,
        username=None,
        asset_id=None,
        error="A sentence stored when it failed. Connect the site under Connections first.",
        created_at=0,
        error_code=code,
        url=url,
        via=via,
    )


def test_a_row_with_no_code_keeps_its_stored_sentence() -> None:
    assert _failure_said(_row(None, "https://example.com/a")) is None


def test_a_row_with_no_code_whose_sentence_was_retired_says_it_as_it_is_stored_today() -> None:
    """A row stored with the retired sentence ("The download did not finish. This is often
    temporary", a double hyphen, "try it again in a little while.") has no code, so no reading, and
    its stored words would keep their dash for good. A retired codeless sentence is said as the
    same failure is stored now; any other codeless sentence still stands as it was stored."""

    def said(stored: str) -> str | None:
        return _failure_said(replace(_row(None, "https://example.com/a"), error=stored))

    assert said(
        "The download did not finish. This is often temporary -- try it again in a little while."
    ) == ("The download did not finish. Try again in a few minutes.")
    assert said(
        "Example needs you to be logged in. Add a Platform Connection for it, then try the"
        " download again."
    ) == (
        "Example wants cookies before it will show this. Add cookies for it, then try the"
        " download again."
    )
    assert said(
        "Sift cannot download from Example -- it is not a site any of its downloaders support."
    ) == ("Sift cannot download from Example. None of the downloaders Sift includes support it.")
    assert said("Nothing could be downloaded from Example.") is None


def test_a_status_is_said_from_the_code_not_the_stored_words() -> None:
    said = _failure_said(_row("http-404", "https://example.com/a"))
    assert said is not None
    assert "404 Not Found" in said
    assert "Connections first" not in said


def test_a_site_that_gives_a_code_its_own_meaning_is_read_from_its_record_now() -> None:
    url = "https://www.pornhub.com/view_video.php?viewkey=abc"
    now = failures.reading_now(url, "http-410")
    assert now is not None and now.tier == 3
    assert _failure_said(_row("http-410", url)) == now.sentence


def test_a_tunnel_is_named_only_for_a_download_that_went_out_directly() -> None:
    direct = _failure_said(_row("http-403", "https://example.com/a", via="Direct"))
    tunnelled = _failure_said(_row("http-403", "https://example.com/a", via="tunnel-a"))
    assert direct is not None and "tunnel" in direct
    assert tunnelled is not None and "tunnel" not in tunnelled


def test_a_code_this_build_has_never_heard_of_keeps_the_stored_sentence() -> None:
    assert failures.reading_now("https://example.com/a", "never-heard-of") is None
    assert _failure_said(_row("never-heard-of", "https://example.com/a")) is None
