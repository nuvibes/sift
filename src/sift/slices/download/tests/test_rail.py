# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Downloads row on the rail says, read from the download rows.

Read from the rows rather than the newest fifty jobs of every kind, where a download that
scrolled off that page behind a few seconds of background work would never light the dot when it
ended, and a download waiting behind a paused queue would turn the glyph as if it were fetching.
These hold the rail to the rows: an ending lights the dot until somebody looks, a retry that ends
lights it again, and nothing that cannot start turns anything.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.download.service import DownloadService, RailFacts
from sift.slices.download.sources import url_hash
from sift.slices.download.tests.test_service import registered_download

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures(registered_download.__name__)]

__all__ = ["registered_download"]


async def _queued(db: Database, download_id: str) -> None:
    url = f"https://example.com/{download_id}"
    await db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, created_at) VALUES (?, ?, ?, 'queued', 0)",
        (download_id, url, url_hash(url)),
    )


async def test_an_ending_lights_the_rail_until_it_is_seen_and_a_retry_lights_it_again(
    download_service: DownloadService, temp_db: Database
) -> None:
    for one in ("d1", "d2", "d3"):
        await _queued(temp_db, one)
    assert await download_service.rail(paused=False) == RailFacts(3, 0, 0, 0)

    await download_service.mark_running("d1")
    await download_service.mark_failed("d2", error="That address could not be found.")
    await download_service.mark_skipped("d3")
    assert await download_service.rail(paused=False) == RailFacts(1, 0, 1, 1)

    assert await download_service.mark_seen() == 3
    assert await download_service.rail(paused=False) == RailFacts(1, 0, 0, 0)
    assert await download_service.mark_seen() == 0, "nothing new is nothing to announce"

    # The one still fetching was marked too, and loses nothing by it: its ending is the news.
    await download_service.mark_failed("d1", error="The download did not finish.")
    assert await download_service.rail(paused=False) == RailFacts(0, 0, 0, 1)

    # Tried again and failed again: a second ending, so the dot comes back.
    await download_service.mark_seen()
    assert await download_service.retry("d2")
    await download_service.mark_failed("d2", error="That address could not be found.")
    assert (await download_service.rail(paused=False)).failed_unseen == 1


async def test_a_download_waiting_behind_a_paused_queue_is_not_downloading(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _queued(temp_db, "d1")
    await _queued(temp_db, "d2")
    await download_service.mark_running("d2")

    assert (await download_service.rail(paused=True)).downloading == 1, "only the one fetching"
    assert (await download_service.rail(paused=False)).downloading == 2


async def test_waiting_for_cookies_is_read_off_the_job_and_does_not_turn_the_glyph(
    download_service: DownloadService, temp_db: Database
) -> None:
    download_id = await download_service.submit_url(url="https://www.tiktok.com/@a/video/1")
    await download_service.mark_running(download_id)
    # Parked the way the kernel parks a job whose Site needs cookies nobody has unlocked.
    await temp_db.execute(
        "UPDATE jobs SET state = 'blocked' WHERE id = (SELECT job_id FROM downloads WHERE id = ?)",
        (download_id,),
    )

    assert await download_service.rail(paused=False) == RailFacts(0, 1, 0, 0)


async def test_a_row_put_away_lights_nothing(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _queued(temp_db, "d1")
    await download_service.mark_failed("d1", error="That address could not be found.")
    assert (await download_service.rail(paused=False)).failed_unseen == 1

    assert await download_service.hide("d1") is None
    assert (await download_service.rail(paused=False)).failed_unseen == 0
