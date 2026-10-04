# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a thing an older event named is called now: a thing with no name left to give is left out,
so its line keeps its kind rather than saying a blank."""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository
from sift.kernel.access.history_names import names_now
from sift.kernel.db import Database

pytestmark = pytest.mark.anyio


async def test_a_thing_with_no_name_to_give_is_left_out_and_a_kind_with_none_is_not_asked(
    temp_db: Database, access: Repository
) -> None:
    """A file with no title, no location left and no name it arrived with has nothing to be called;
    a kind the ledger names nothing of, and a kind asked about with no ids, are not read at all."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a-1', 'd-1', 'video', 0)"
    )
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at, original_filename)"
        " VALUES ('a-2', 'd-2', 'video', 0, 'clip.mp4')"
    )

    named = await names_now(temp_db, {"asset": ["a-1", "a-2"], "box": ["b-1"], "tag": []})

    assert named == {("asset", "a-2"): "clip.mp4"}
