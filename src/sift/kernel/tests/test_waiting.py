# SPDX-License-Identifier: AGPL-3.0-or-later
"""A slice declares a waiting table as names, and the kernel writes the statements from them.

The declaration is the one thing a slice hands the kernel, and every name in it is written into a
statement. So a name that is not a plain identifier is refused where it is declared, before any
statement could carry it.
"""

from __future__ import annotations

import pytest

from sift.kernel.access.waiting import Waiting, triggers


@pytest.mark.parametrize(
    ("table", "done", "filled"),
    [
        ("music_waiting; DROP TABLE assets", "audio_fingerprints", ("acodec",)),
        ("music_waiting", "audio fingerprints", ("acodec",)),
        ("music_waiting", "audio_fingerprints", ("acodec IS NULL OR 1",)),
        ("music_waiting", "audio_fingerprints", ("Acodec",)),
        ("music_waiting", "audio_fingerprints", ()),
        ("music_waiting", "audio_fingerprints", ("acodec", "acodec")),
    ],
)
def test_a_declaration_that_is_not_plain_names_is_refused(
    table: str, done: str, filled: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        Waiting(table=table, done=done, filled=filled)


def test_every_trigger_is_named_under_the_table_and_watches_what_was_declared() -> None:
    spec = Waiting(table="some_waiting", done="some_done", filled=("acodec", "probed_at"))
    made = triggers(spec)
    assert all(name.startswith("some_waiting_") for name in made)
    changed = made["some_waiting_file_changed"]
    assert "AFTER UPDATE OF acodec, probed_at ON assets" in changed
    assert "NEW.acodec IS NOT OLD.acodec OR NEW.probed_at IS NOT OLD.probed_at" in changed
    assert "{{" not in " ".join(made.values())


def test_the_rule_asks_for_a_copy_that_is_there_and_watches_every_copy() -> None:
    """A file with no present copy is work nothing can do, and counting it would leave the Music
    card far above what a run ever hands out. The rule reads the shared fragment the Build's own
    page and count read, and a copy arriving, changing status or going re-decides its file."""
    from sift.kernel.content.presence import HAS_A_PRESENT_COPY

    made = triggers(Waiting(table="some_waiting", done="some_done", filled=("acodec",)))
    assert HAS_A_PRESENT_COPY in made["some_waiting_file_changed"]
    assert "AFTER INSERT ON asset_locations" in made["some_waiting_copy_arrived"]
    assert (
        "AFTER UPDATE OF status, asset_id ON asset_locations" in made["some_waiting_copy_changed"]
    )
    assert "AFTER DELETE ON asset_locations" in made["some_waiting_copy_gone"]
