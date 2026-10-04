# SPDX-License-Identifier: AGPL-3.0-or-later
"""The metric list is closed, every word in it has its statement, and every statement runs.

Three holds on the list, each for a way a list like it drifts: a word a reader may ask for that
nothing writes (it would draw a zero that looks like a fact), a statement nobody can ask for (dead
weight that still has to be kept correct), and the openings every statement repeats (the rule
for what a view is and what a sitting is), written out twice until they no longer say the same
thing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sift.slices.insights import metrics
from sift.slices.insights.metrics import ADMIN_ONLY, METRICS, MINUTES, PER_FILE, STATEMENTS, rows_of
from sift.slices.insights.tests.conftest import END, START, World

pytestmark = pytest.mark.integration

#: The list the contracts name, spelled out here so a word added to the code alone is refused.
CONTRACT = {
    "viewed_ms", "viewed_ms:kind", "sittings", "sittings:kind", "files_viewed",
    "files_viewed:kind", "viewed_ms:person", "files_viewed:person", "viewed_ms:site",
    "viewed_ms:tag", "viewed_ms:collection", "viewed_ms:photo_set", "photo_sets_viewed",
    "viewed_ms:song",
    "sittings:file", "viewed_ms:hour", "viewed_ms:weekday", "theater_ms:wall", "pickups",
    "first_opened:person", "first_opened:kind", "earliest_start", "latest_finish", "rated",
    "rated:file", "starred", "starred:file", "o", "o:file", "decided", "decided:queue", "faces_named", "files_filed",
    "files_added", "files_added:site", "files_removed", "work_ms:family", "faces_found",
    "fingerprints_made",
}  # fmt: skip


def test_the_list_is_the_contract() -> None:
    assert set(METRICS) == CONTRACT


def test_every_metric_has_one_statement_and_every_statement_a_metric() -> None:
    assert set(STATEMENTS) == set(METRICS)
    assert ADMIN_ONLY <= METRICS and MINUTES <= METRICS and PER_FILE <= METRICS


def _opening(sql: str, name: str) -> str | None:
    """The text of one named opening (`v AS (...)` or `s AS (...)`), or None when absent."""
    found = re.search(rf"(?:WITH|,)\s*{name} AS \((.*?)\n\)", sql, re.DOTALL)
    return found.group(1) if found else None


@pytest.mark.parametrize("name", ["v", "s", "k"])
def test_every_copy_of_a_shared_opening_says_the_same(name: str) -> None:
    copies = {
        metric: body
        for metric, sql in STATEMENTS.items()
        if isinstance(sql, str) and (body := _opening(sql, name)) is not None
    }
    assert copies, name
    first = next(iter(copies.values()))
    assert {metric for metric, body in copies.items() if body != first} == set()


def test_no_statement_is_built_from_pieces() -> None:
    # Literals only: nothing in the module formats or joins SQL (the rule's reason is injection).
    source = Path(metrics.__file__).read_text(encoding="utf-8")
    assert ".format(" not in source and 'f"""' not in source and ".join(" not in source


async def test_every_statement_runs_against_the_real_tables(world: World) -> None:
    params = {
        "user": world.user,
        "start": START,
        "end": END,
        "views": json.dumps([]),
        "hidden": json.dumps([]),
    }
    for metric in STATEMENTS:
        rows = await rows_of(metric, world.db.fetch_all, params)
        assert rows == [], metric


def test_no_statement_names_a_permission_carrying_table() -> None:
    """The tables the access layer scopes (files, their copies, folders, roots) are read only
    through it: a sitting carries its file's kind and length, and what arrived is the access
    layer's question. A statement here that named one would count what this User may not see."""
    source = Path(metrics.__file__).read_text(encoding="utf-8")
    assert not re.search(
        r"\b(FROM|JOIN|INTO|UPDATE)\s+(assets|asset_locations|folders|library_roots)\b", source
    )
    assert not isinstance(STATEMENTS["files_added"], str)
    assert not isinstance(STATEMENTS["files_added:site"], str)


def test_the_face_naming_acts_are_the_faces_boards_own_words() -> None:
    # Read as text: a slice's tests may not import another slice any more than the slice may.
    faces = (Path(metrics.__file__).parents[1] / "faces" / "receipts.py").read_text(
        encoding="utf-8"
    )
    statement = STATEMENTS["faces_named"]
    assert isinstance(statement, str)
    for act in metrics.FACE_NAMING_ACTS:
        assert f'= "{act}"' in faces, act
        assert f"'{act}'" in statement, act


def test_the_resplit_reads_every_per_file_metric() -> None:
    # A gone file's last state is read from these rows; one missing from the literal is a file whose
    # state is forgotten and read as hidden.
    from sift.slices.insights.store import _HIDDEN_FILES

    for metric in PER_FILE:
        assert f"'{metric}'" in _HIDDEN_FILES, metric


async def test_a_songs_time_is_its_files_and_a_hidden_files_share_is_said_apart(
    world: World,
) -> None:
    """`viewed_ms:song` is the time spent on the files carrying each song, and the part of it on
    a hidden file is kept beside it, as a Photo Set's is: a song has no hidden state of its own."""
    from sift.slices.insights import store
    from sift.slices.insights.tests.conftest import DAY, at

    shown = await world.add_file("video")
    hidden = await world.add_file("video", hidden=True)
    await world.run(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES ('s-1', 'tune', 'tune', 1)"
    )
    for asset_id in (shown, hidden):
        await world.run(
            "INSERT INTO song_files (asset_id, song_id, added_at) VALUES (?, 's-1', 1)",
            (asset_id,),
        )
    # The membership re-decides each file's verdict (the stored counts' triggers), so what this
    # User is shown of each is said again after it.
    await world.set_hidden(shown, False)
    await world.set_hidden(hidden, True)
    await world.sit(shown, at(10), 120_000)
    await world.sit(hidden, at(11), 60_000)
    counted = await store.count_day(world.db.fetch_all, world.user, DAY, ["viewed_ms:song"])
    assert [(one.key, one.whole, one.hidden) for one in counted] == [("s-1", 180_000, 60_000)]
