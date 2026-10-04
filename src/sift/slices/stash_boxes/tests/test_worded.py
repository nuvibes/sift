# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tagger's decisions, worded when shown: who applied which box's answer to which file.

An older stored title named neither who did it, nor the file, nor the box, though the payload
records all three; they are worded from the payload when shown.
"""

from __future__ import annotations

import json

from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.stash_boxes.queue import TaggerQueue


def _worded(payload: object):  # type: ignore[no-untyped-def]
    recorded = Recorded(
        id="01R", queue="tagger", payload=json.dumps(payload), title="", detail="", decided_at=0
    )
    return TaggerQueue.worded(TaggerQueue.__new__(TaggerQueue), recorded)


def test_one_file_names_the_file_and_its_box() -> None:
    said = _worded({"matches": [{"asset_id": "01A", "box_id": "01B"}]})
    assert said is not None
    assert said.said == (
        DOER,
        " applied what ",
        Named(kind="box", id="01B"),
        " said about ",
        Named(kind="asset", id="01A"),
    )


def test_many_files_are_counted_and_every_box_is_named_once() -> None:
    matches = [{"asset_id": f"0{n}", "box_id": box} for n, box in enumerate("BCB")]
    said = _worded({"matches": matches})
    assert said is not None
    assert said.said == (
        DOER,
        " applied what ",
        Named(kind="box", id="B"),
        " and ",
        Named(kind="box", id="C"),
        " said about ",
        "3 files",
    )
    assert _worded({"matches": []}) is None


def test_a_username_stands_without_the_new_rows_where_its_site_is_here_and_it_is_new() -> None:
    """The count above the press: a username lands without the ticks it needs when its Site is
    already here and the file is not already filed under it; one already filed, or on a Site that
    would have to be made, writes nothing on its own."""
    from sift.kernel.enrichment import Decision, Missing, Outcome
    from sift.slices.stash_boxes.match_words import _stands_without

    held = {"site": "OnlyFans", "handle": "quillmoss"}
    new = {"site": "OnlyFans", "handle": "marlaquist"}
    elsewhere = {"site": "Northlight Media", "handle": "marlaquist"}
    needs = (Missing(name="Northlight Media", kind="site"),)

    def stands(value: object) -> bool:
        decision = Decision(key="accounts", outcome=Outcome.WRITE, mine=[held], value=value)
        return _stands_without(decision, needs)

    assert stands([held, new]) is True
    assert stands([held]) is False
    assert stands([elsewhere]) is False
    assert _stands_without(Decision(key="title", outcome=Outcome.WRITE, value="x"), ()) is True
