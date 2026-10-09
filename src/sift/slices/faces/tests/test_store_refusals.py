# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a pass refused, kept beside what it found."""

from __future__ import annotations

from dataclasses import replace

import pytest

# For its side effect: registering the table the ledger is written to, so a slice test's
# database has it. Registration happens at import and the fixtures migrate at setup.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import (
    Ingested,
)
from sift.slices.faces.models import (
    ScanStatus,
)
from sift.slices.faces.store import PassRecord, Store

pytestmark = pytest.mark.integration


def a_refusing_record(small: int | None, closer: int | None) -> PassRecord:
    """A pass that found nobody and says how many faces it refused, at each gate."""
    return PassRecord(
        status=ScanStatus.NO_FACES,
        depth="fast",
        coverage=0.5,
        frames_sampled=1,
        detector="test-detector",
        recognizer="test-recognizer",
        settings_digest="abcd1234",
        reached_ms=1000,
        refused_small=small,
        refused_closer=closer,
    )


async def test_a_pass_keeps_what_it_refused_and_a_rescan_replaces_it(
    store: Store, clip: Ingested
) -> None:
    """The counts are the reason a file with nobody in it has nobody in it, so they are written
    with the status, and a pass that replaces a file's faces replaces its reason too, or a
    rescan that found the people would go on carrying the old pass's excuse."""
    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(16, 1))
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (16, 1)

    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(2, 0))
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (2, 0)


async def test_a_resumed_pass_adds_what_it_refused_to_what_the_last_one_did(
    store: Store, clip: Ingested
) -> None:
    """A resumed pass read only the part the last one did not, so its counts are the rest of the
    same answer. Replaced, the refusals of the head of the file would vanish the moment its tail
    was read, and a tail with nobody in it would say "found none" about a file full of faces."""
    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(16, 1))

    await store.extend_pass(clip.asset.id, [], [], [], a_refusing_record(3, 2))

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (19, 3)


async def test_a_resumed_pass_over_a_scan_that_never_counted_stays_not_known(
    store: Store, clip: Ingested
) -> None:
    """Half a count is not a count. The head of the file was read before anything was counted, so
    a total made of the tail alone would be a reason given for a part as if it were the whole."""
    await store.replace_pass(clip.asset.id, [], [], a_refusing_record(None, None))

    await store.extend_pass(clip.asset.id, [], [], [], a_refusing_record(3, 2))

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (None, None)


def a_record_with_reasons(largest: int, blurred: int, turned: int, edge: int) -> PassRecord:
    """A refusing pass that also says why, reason by reason, and how big its biggest small face was."""
    return replace(
        a_refusing_record(1, blurred + turned + edge),
        refused_largest=largest,
        refused_blurred=blurred,
        refused_turned=turned,
        refused_edge=edge,
    )


async def test_a_pass_keeps_its_reasons_and_a_resume_adds_them_and_keeps_the_largest(
    store: Store, clip: Ingested
) -> None:
    """The reasons are the rest of the same answer, so a resume adds them like the counts they
    split; the size is the biggest face refused across both stretches, never the last one's."""
    await store.replace_pass(clip.asset.id, [], [], a_record_with_reasons(98, 1, 0, 0))
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_largest, scan.refused_blurred, scan.refused_turned, scan.refused_edge) == (
        98,
        1,
        0,
        0,
    )

    await store.extend_pass(clip.asset.id, [], [], [], a_record_with_reasons(60, 0, 2, 1))

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_largest, scan.refused_blurred, scan.refused_turned, scan.refused_edge) == (
        98,
        1,
        2,
        1,
    )
