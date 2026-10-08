# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap counted again after a correction, as History says it."""

from __future__ import annotations

import pytest

from sift.kernel.access import sentences as say
from sift.kernel.access.history_ledger import _EVENT_KINDS

pytestmark = pytest.mark.unit

#: A recap on Insights, named by the days it covers as its writer names it.
RECAP = say.thing("recap", "r1", "September 2026 recap")


def test_a_recap_counted_again_says_so_word_for_word() -> None:
    line = say.feed_line("recounted", by=say.SIFT, subjects=[("recap", RECAP)]).pieces
    assert say.text_of(line) == "Sift re-counted your September 2026 recap after a correction"


def test_it_wears_a_finished_tasks_mark_and_names_a_recap_it_lost() -> None:
    assert _EVENT_KINDS["recounted"] == "ran"
    assert say.A_THING["recap"] == "a recap"
