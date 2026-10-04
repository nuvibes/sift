# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time code that was written to run until a release had shipped, and has been taken out.

Code that carried a library across a change (moving models into the device's store, translating the
addresses and words that moved to Sites, re-cutting black tiles, two once-only stash-box passes)
runs at no start or request any more. Each is held here, so a revert or an older branch cannot bring
one back unnoticed. History rows already stored, and a setting's older key read on import, are not
leftovers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sift.main
from sift.kernel.content import ContentStore
from sift.kernel.ml import store
from sift.slices import media_jobs, stash_boxes
from sift.slices.media_jobs import jobs as media_jobs_jobs

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(sift.main.__file__).resolve().parent


def test_the_model_store_move_is_not_run_at_a_start() -> None:
    assert store.__all__ == ["LIBRARY_MODELS"]
    assert not hasattr(store, "adopt_library_models")
    machine = (SOURCE / "wiring" / "machine.py").read_text(encoding="utf-8")
    assert "ml_store" not in machine and "adopt_library_models" not in machine


def test_no_moved_address_or_renamed_query_word_is_carried() -> None:
    for name in ("MOVED_API_PREFIXES", "MOVED_QUERY_PARAMETERS", "translate_moved_query"):
        assert not hasattr(sift.main, name), name


def test_the_black_still_catch_up_is_not_run_at_a_start() -> None:
    assert not hasattr(media_jobs, "RECUT_BLACK_STILLS")
    assert not hasattr(media_jobs_jobs, "recut_black_stills")
    assert not hasattr(ContentStore, "stills_unmeasured")
    start = (SOURCE / "wiring" / "catch_up.py").read_text(encoding="utf-8")
    assert "stills_unmeasured" not in start and "RECUT_BLACK_STILLS" not in start


def test_a_queued_lookup_reads_only_todays_word_for_a_site() -> None:
    jobs = (SOURCE / "slices" / "stash_boxes" / "jobs.py").read_text(encoding="utf-8")
    assert '"platform"' not in jobs


def test_the_two_one_time_stash_box_passes_are_not_run_at_a_start() -> None:
    for name in (
        "STASH_TAKE_BACK",
        "STASH_SITE_PICTURES",
        "taking_back_owed",
        "site_pictures_owed",
    ):
        assert not hasattr(stash_boxes, name), name
    for module in ("take_back.py", "site_pictures.py"):
        assert not (SOURCE / "slices" / "stash_boxes" / module).exists(), module
    start = (SOURCE / "wiring" / "catch_up.py").read_text(encoding="utf-8")
    assert "taking_back_owed" not in start and "site_pictures_owed" not in start


def test_no_comment_sends_a_reader_to_a_retired_stash_box_pass() -> None:
    """A comment naming a module that is gone sends the next reader looking for code that is not
    there, and reads as if the pass still ran."""
    retired = ("stash_boxes.take_back", "stash_boxes.site_pictures")
    naming = [
        f"{path.relative_to(SOURCE)}: {name}"
        for path in SOURCE.rglob("*.py")
        for name in retired
        if name in path.read_text(encoding="utf-8")
    ]
    assert naming == []
