# SPDX-License-Identifier: AGPL-3.0-or-later
"""The counts under the filter bar, and what they must never say.

A facet count is a disclosure before it is a convenience. "4+ stars (15)" over a screen that can
only show twelve has published the size of the set somebody was kept out of, and unlike a list,
a number cannot be trimmed afterwards to fix it. Filtering the rows and leaving the count alone is
precisely the shape that leaks.

So every test here asks the same question two ways: what does the facet say, and what can this
user actually open. They have to agree.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Concealment, Role, Viewer
from sift.kernel.ids import new_id
from sift.slices.auth import current_viewer
from sift.slices.browse.tests.conftest import (
    PASSWORD,
    Library,
    db_path,
    share,
    sign_in,
    write,
)
from sift.slices.stash_boxes.enrich import IMPORTED
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration


def _tag(client: TestClient, asset_id: str, name: str) -> str:
    """Put a tag on a file, making the tag only if nothing already has that name.

    Two files sharing a tag is the case most of these are about, and tag names are unique, so a
    helper that always inserted would fail on the second call rather than tagging both.
    """
    tag_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT(name) DO NOTHING",
                (tag_id, name, 0),
            ),
            (
                "INSERT INTO asset_tags (asset_id, tag_id) SELECT ?, id FROM tags WHERE name = ?",
                (asset_id, name),
            ),
        ],
    )
    return tag_id


def _tag_id(client: TestClient, name: str) -> str:
    """The id of the tag with this name, for the tests that conceal one."""
    answer = client.get("/api/tags")
    assert answer.status_code == 200, answer.text
    # A page, not a bare list. The tag route pages like the rest of the entity walls, and a
    # helper that walks the answer directly walks its KEYS instead, which is a TypeError three
    # frames down rather than a sentence about tags.
    found = [row["id"] for row in answer.json()["items"] if row["name"] == name]
    assert found, f"no tag called {name}"
    return str(found[0])


#: The named-thing columns led by "Has" and "No" rows (`any`, `none`).
_HEADED = frozenset({"tags", "people", "sites", "collections", "photo_sets", "songs"})


def _column(client: TestClient, facet: str, **params: str) -> list[tuple[str, int]]:
    answer = client.get("/api/assets/facets", params={"facet": facet, **params})
    assert answer.status_code == 200, answer.text
    return [(row["value"], row["count"]) for row in answer.json()["values"]]


def _facets(client: TestClient, facet: str, **params: str) -> dict[str, int]:
    """A column's own values, without the "Has" and "No" rows `_heads` reads."""
    rows = _column(client, facet, **params)
    return {value: n for value, n in rows if facet not in _HEADED or value not in ("any", "none")}


def _heads(client: TestClient, facet: str, **params: str) -> dict[str, int]:
    return {value: n for value, n in _column(client, facet, **params) if value in ("any", "none")}


def _reachable(client: TestClient, **params: str) -> int:
    """How many files this user can actually list. The number a facet must not exceed."""
    answer = client.get("/api/assets", params={"limit": "200", **params})
    assert answer.status_code == 200, answer.text
    return int(answer.json()["total"])


def test_a_guest_is_counted_what_a_guest_can_open(
    client: TestClient, library: Library, tmp_path: Path
) -> None:
    """The requirement, stated as plainly as it can be.

    Two files, one tag each. One is shared with the guest and one is not. An admin sees both tags
    and the guest sees one, and the count beside it is the number of files the guest can really
    reach through it, never the number in the library.
    """
    _tag(client, library.shared, "beach")
    _tag(client, library.private, "private-notes")

    sign_in(client, "admin")
    assert _facets(client, "tags") == {"beach": 1, "private-notes": 1}

    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, guest)
    sign_in(client, "guest")

    counted = _facets(client, "tags")
    assert counted == {"beach": 1}, (
        "a guest was told about a tag they cannot reach anything through"
    )
    assert sum(counted.values()) == _reachable(client)


def test_a_count_never_exceeds_what_the_account_can_list(
    client: TestClient, library: Library
) -> None:
    """The general form of the test above, and the one that would catch a count computed by a
    second query that forgot a rule. Whatever the facet says a value holds, that many files have to
    be reachable by asking for exactly that value."""
    _tag(client, library.shared, "beach")
    _tag(client, library.private, "beach")

    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, guest)
    sign_in(client, "guest")

    counted = _facets(client, "tags")

    assert counted["beach"] == _reachable(client, q="tags:beach")


def test_hiding_something_takes_it_out_of_the_counts(client: TestClient, library: Library) -> None:
    """Concealment has to reach the numbers as well as the rows. A count that still includes a
    hidden file is a way to ask how much is hidden, one facet at a time."""
    _tag(client, library.shared, "beach")
    _tag(client, library.private, "beach")

    admin = sign_in(client, "admin")
    assert _facets(client, "tags") == {"beach": 2}

    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, hidden, updated_at)"
                " VALUES (?, ?, 1, 0)"
                " ON CONFLICT(asset_id, user_id) DO UPDATE SET hidden = 1",
                (library.private, admin),
            )
        ],
    )

    assert _facets(client, "tags") == {"beach": 1}
    assert _facets(client, "tags")["beach"] == _reachable(client, q="tags:beach")


def test_a_concealed_name_is_absent_even_where_its_files_are_still_counted(
    client: TestClient, library: Library
) -> None:
    """The sharper half of concealment, and the one state where it can actually bite.

    Hiding a tag hides its files too, so in the ordinary mode the value disappears because there is
    nothing left to count, which looks like this working and proves nothing. Placeholder mode is
    the case that separates them: a concealed file STAYS in the result set as a locked tile, so its
    tag reaches the grouping and would be named. It must not be.

    A name listed beside a count says two things together: that the tag exists, and that there is
    something under it worth hiding. Absent is the only answer that says neither.
    """
    _tag(client, library.private, "private-notes")
    _tag(client, library.shared, "beach")
    user_id = sign_in(client, "admin")
    tag_id = _tag_id(client, "private-notes")

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id, role=Role.ADMIN, show_hidden=False, concealment=Concealment.PLACEHOLDER
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        write(
            db_path(client),
            [
                (
                    "INSERT INTO tag_user_state (tag_id, user_id, hidden, hidden_at, updated_at)"
                    " VALUES (?, ?, 1, 0, 0)",
                    (tag_id, user_id),
                )
            ],
        )

        counted = _facets(client, "tags")
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    # The file is still reachable as a placeholder, which is exactly why the NAME has to go.
    assert "private-notes" not in counted
    assert counted == {"beach": 1}


def test_the_counts_are_narrowed_by_the_query_they_describe(
    client: TestClient, library: Library
) -> None:
    """A facet describes what is on screen, not the library. On a screen already carrying a
    constraint, a library-wide number looks plausible in every screenshot and is wrong in all of
    them."""
    _tag(client, library.shared, "beach")
    _tag(client, library.private, "city")

    sign_in(client, "admin")

    assert _facets(client, "tags") == {"beach": 1, "city": 1}
    assert _facets(client, "tags", q="tags:beach") == {"beach": 1}


def test_has_and_no_lead_each_named_column_and_open_what_they_counted(
    client: TestClient, library: Library
) -> None:
    """ "Has tags" and "No tags" first, counted from the filter they write, never at nought."""
    _tag(client, library.shared, "beach")
    _tag(client, library.shared, "None")
    sign_in(client, "admin")

    assert _column(client, "tags")[:2] == [("any", 1), ("none", 1)]
    # A name spelled as a presence word cannot be filtered to by name, so it is not listed.
    assert _facets(client, "tags") == {"beach": 1}
    for facet in _HEADED:
        heads = _heads(client, facet)
        assert sum(heads.values()) == _reachable(client), facet
        for value, count in heads.items():
            assert _reachable(client, q=f"{facet}:{value}") == count, f"{facet}:{value}"
    assert _heads(client, "people") == {"none": 2}

    # Narrowed by another filter, the rest of the wall is counted by its own statement.
    assert _heads(client, "tags", q="tags:beach") == {"any": 1}
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.private, guest)
    sign_in(client, "guest")
    assert _heads(client, "tags") == {"none": 1}


def test_a_facet_nobody_offers_is_refused(client: TestClient, library: Library) -> None:
    """Refused rather than ignored: a caller who asked for one dimension and silently got another
    has a panel that looks wrong for no visible reason."""
    sign_in(client, "admin")

    assert client.get("/api/assets/facets", params={"facet": "shoe_size"}).status_code == 422


def test_a_file_counts_once_however_many_values_it_carries(
    client: TestClient, library: Library
) -> None:
    """The join multiplies rows (a file with three tags is three rows) and the question is how
    many FILES. Counted wrongly, every count above is inflated in the leaking direction."""
    _tag(client, library.shared, "beach")
    _tag(client, library.shared, "sunset")

    sign_in(client, "admin")

    counted = _facets(client, "tags")

    assert counted == {"beach": 1, "sunset": 1}


def test_seen_and_not_seen_are_counted_and_agree_with_the_filter(
    client: TestClient, library: Library
) -> None:
    """The "Seen" column, and the one thing that makes it worth having: it counts the files with no
    state row at all.

    Every other per-user column joins state and so can only describe files that HAVE some. "Not
    seen" is the absence of it, which is what somebody opens this column to ask, so the join is a
    LEFT one, and the number beside each row has to be the number clicking it returns, or the
    column is a count of one set and a link to another.
    """
    sign_in(client, "admin")

    # One of the two WATCHED. The other has no state row whatsoever, which is the case that has to
    # be countable too.
    #
    # Watched, not merely opened, and the number is not decoration: this column reads
    # `last_viewed_at`, which is written only when a sitting EARNS a view, and a video earns one
    # on time watched, never on being opened (see `player.policy.watch_needed`, whose ceiling is
    # 30 s). A report of `watch_ms: 0` is a sitting that watched nothing, and the file would be
    # correctly counted as unseen.
    answer = client.post(
        f"/api/assets/{library.shared}/view", json={"watch_ms": 30_000, "position_ms": 0}
    )
    assert answer.status_code in {200, 204}, answer.text

    # `done` and not `yes`: the row is "opened, and nowhere to go back to", which is narrower than
    # the `yes` the Recently viewed screen is built on. See `_VIEWED_STATES`.
    assert _facets(client, "viewed") == {"done": 1, "none": 1}

    # The value IS the filter: what each row says, and what asking for it returns.
    assert _reachable(client, viewed="done") == 1
    assert _reachable(client, viewed="none") == 1
    # The wide word agrees here because nothing is part-way through, which is the only case in
    # which it can differ.
    assert _reachable(client, viewed="yes") == 1


def test_what_you_are_partway_through_is_its_own_answer_and_the_count_is_the_filter(
    client: TestClient, library: Library
) -> None:
    """The "Continue watching" row: a long video with a place saved in it, and nothing else.

    It is the state a library is most often opened to ask about, and it is a NARROWER question than
    "seen": a file you stopped in the middle of counts here and not under `yes`, because the values
    of one column are the answers to one question and a file cannot be two of them.

    The same rule decides three things: where the player starts, whether a tile draws a bar along
    its bottom edge, and what this row counts. And it lives once, in the kernel. What this holds
    is the part that cannot be unit-tested: that the SQL form of it reaches the real column through
    the real statement, and that the number beside the row is the number clicking it returns.
    """
    admin = sign_in(client, "admin")

    # Long enough to be worth going back into (the default minimum is a minute) and stopped twenty
    # minutes in, which is neither the beginning nor the end. Written straight to the row because
    # what is under test is the reading, not the recording: the player's own tests cover that.
    write(
        db_path(client),
        [
            ("UPDATE assets SET duration_ms = 3600000 WHERE id = ?", (library.shared,)),
            (
                "INSERT INTO asset_user_state"
                " (asset_id, user_id, view_count, watched_ms, resume_ms, last_viewed_at,"
                " updated_at)"
                " VALUES (?, ?, 1, 1200000, 1200000, 1, 1)",
                (library.shared, admin),
            ),
        ],
    )

    assert _facets(client, "viewed") == {"continue": 1, "none": 1}

    # The value IS the filter, and the label is a spelling of it.
    assert _reachable(client, viewed="continue") == 1
    assert _reachable(client, q='viewed:"continue watching"') == 1
    # And it is not counted twice BY THE COLUMN: the file is part-way through, so the row beside
    # Continue watching does not hold it either. `yes` is the WIDE word, which is what the Recently
    # viewed screen is built on, and it returns this file; the COLUMN's middle row writes `done`,
    # which is the narrower thing the row actually holds.
    assert _reachable(client, viewed="yes") == 1
    assert _reachable(client, viewed="any") == 1
    assert _reachable(client, viewed="done") == 0


def test_a_finished_video_is_not_something_to_continue(
    client: TestClient, library: Library
) -> None:
    """The other edge of the same rule, through the same statement: the credits are not a place to
    be dropped back into, so a video watched to the end falls back to the ordinary "seen"."""
    admin = sign_in(client, "admin")

    write(
        db_path(client),
        [
            ("UPDATE assets SET duration_ms = 3600000 WHERE id = ?", (library.shared,)),
            (
                "INSERT INTO asset_user_state"
                " (asset_id, user_id, view_count, watched_ms, resume_ms, last_viewed_at,"
                " completed_at, updated_at)"
                " VALUES (?, ?, 1, 3600000, 3599000, 1, 1, 1)",
                (library.shared, admin),
            ),
        ],
    )

    assert _facets(client, "viewed") == {"done": 1, "none": 1}
    assert _reachable(client, viewed="continue") == 0
    assert _reachable(client, viewed="done") == 1


def test_folders_are_counted_and_the_count_is_the_filter(
    client: TestClient, library: Library
) -> None:
    """The Folders column, which is the same requirement as every other one above.

    Where a file sits is a dimension people narrow by, and one the query language understands
    should be one the panel offers, not typed and never clicked. What the row
    says has to be what asking for it returns, and the value written is the folder's NAME because
    that is the spelling `in:` reads back.
    """
    sign_in(client, "admin")

    assert _facets(client, "in") == {"clips": 2}
    assert _reachable(client, q='in:"clips"') == 2


def test_a_guest_is_counted_only_the_folders_they_can_reach(
    client: TestClient, library: Library
) -> None:
    """A count is a disclosure before it is a convenience, and a folder name is as much of one as a
    tag: it names a place on disk somebody was kept out of. Two files in one folder, one of them
    shared: the guest is told about the folder they can reach something through, and the number
    beside it is what they can really open."""
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, guest)
    sign_in(client, "guest")

    counted = _facets(client, "in")

    assert counted == {"clips": 1}
    assert sum(counted.values()) == _reachable(client)


def test_a_concealed_folder_is_absent_from_the_column(client: TestClient, library: Library) -> None:
    """Hidden means the NAME goes, not that the row shows a zero. A folder listed with nothing
    beside it names the thing being hidden and says there is something under it, which is the one
    answer concealment must not give.

    Driven in placeholder mode for the reason the tag above it is: in the ordinary mode hiding the
    folder hides its files too, so the value would disappear for want of anything to count and the
    test would pass against a rule that was never applied. In placeholder mode the files stay in the
    set as locked tiles, so the folder reaches the grouping and has to be dropped by name.
    """
    user_id = sign_in(client, "admin")

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id, role=Role.ADMIN, show_hidden=False, concealment=Concealment.PLACEHOLDER
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        write(
            db_path(client),
            [
                (
                    "INSERT INTO folder_user_state"
                    " (folder_id, user_id, hidden, hidden_at, updated_at)"
                    " VALUES (?, ?, 1, 0, 0)"
                    " ON CONFLICT(folder_id, user_id) DO UPDATE SET hidden = 1",
                    (library.folder, user_id),
                )
            ],
        )

        assert "clips" not in _facets(client, "in")
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


# --- sharing ------------------------------------------------------------------------------------
#
# The one dimension that describes the person running Sift rather than the media. Its counts are of
# decisions (what has been handed out, what has been withheld), so who may ask is part of what is
# being tested here, not just what the numbers say.

_RESTRICT_ITEM = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'item', ?, ?, 'restrict', 0)
ON CONFLICT DO NOTHING
"""


def _restrict(client: TestClient, asset_id: str, user_id: str) -> None:
    write(db_path(client), [(_RESTRICT_ITEM, (new_id(), asset_id, user_id))])


def _another_guest(client: TestClient) -> str:
    """A second user to hand something to, without becoming them.

    A grant names a real user (the table has a foreign key to it), so a test about two grants
    on one file needs two, and `sign_in` builds one user per role.
    """
    user_id, _token, _csrf = establish_session(
        db_path(client), role="guest", username="browse-guest-two", password=PASSWORD
    )
    return user_id


def test_sharing_counts_what_was_decided_on_the_file(client: TestClient, library: Library) -> None:
    guest = sign_in(client, "guest")
    admin = sign_in(client, "admin")
    share(client, library.shared, guest)
    _restrict(client, library.private, guest)

    assert _facets(client, "sharing") == {"shared": 1, "restricted": 1}
    assert admin


def test_a_file_shared_with_several_people_counts_once(
    client: TestClient, library: Library
) -> None:
    """The join multiplies rows exactly as the tag join does, and the question is how many FILES.
    Counted wrongly this inflates in the leaking direction, which is the direction that matters."""
    first = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, first)
    share(client, library.shared, _another_guest(client))

    assert _facets(client, "sharing") == {"shared": 1}


def test_a_file_both_shared_and_withheld_is_under_both(
    client: TestClient, library: Library
) -> None:
    """The two are independent, not exclusive: handed to one user and kept from another is one
    file in both columns, and it is the same answer `sharing:shared` and `sharing:restricted` give
    about it."""
    first = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, first)
    _restrict(client, library.shared, _another_guest(client))

    assert _facets(client, "sharing") == {"shared": 1, "restricted": 1}


def test_a_row_selects_exactly_the_files_it_counted(client: TestClient, library: Library) -> None:
    """Clicking a facet row writes the matching filter, so the count and the screen have to agree.
    They are two different pieces of SQL reading one table, which is where they would drift."""
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, guest)

    assert _facets(client, "sharing")["shared"] == _reachable(client, q="sharing:shared")


# --- orientation ---------------------------------------------------------------------------------


def _measured(client: TestClient, asset_id: str, width: int | None, height: int | None) -> None:
    """The seeded files arrive measured (landscape), so a test about the other answers says which
    file is which, and `None` is a file never measured, which is in no orientation at all."""
    write(
        db_path(client),
        [("UPDATE assets SET width = ?, height = ? WHERE id = ?", (width, height, asset_id))],
    )


def test_orientation_counts_the_three_ways_up_and_agrees_with_the_filter(
    client: TestClient, library: Library
) -> None:
    """Landscape, portrait, square, and a file never measured is in none of them."""
    sign_in(client, "admin")
    _measured(client, library.shared, 1920, 1080)
    _measured(client, library.private, 1080, 1920)

    assert _facets(client, "orientation") == {"landscape": 1, "portrait": 1}
    assert _reachable(client, orientation="portrait") == 1
    assert _reachable(client, orientation="tall") == 1


def test_a_square_picture_is_square_and_not_either_of_the_others(
    client: TestClient, library: Library
) -> None:
    """Its own test rather than a second measurement in the one above: the counts are memoised
    against the library's change stamp, and a row written straight into the table does not move
    it, so a re-measure after a read would be answered from the memo, correctly."""
    sign_in(client, "admin")
    _measured(client, library.shared, 1080, 1080)
    _measured(client, library.private, None, None)

    assert _facets(client, "orientation") == {"square": 1}
    assert _reachable(client, orientation="square") == 1


def test_a_file_never_measured_is_in_no_orientation(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    _measured(client, library.shared, None, None)
    _measured(client, library.private, None, None)

    assert _facets(client, "orientation") == {}
    assert _reachable(client, orientation="landscape") == 0


# --- enriched by ---------------------------------------------------------------------------------
#
# Who wrote to the file without a person doing it. Three authors, three tables, one column; a file
# two of them wrote to is under both rows, exactly as it would be under two tags.


def _a_person(client: TestClient, name: str) -> str:
    person_id = new_id()
    write(
        db_path(client),
        [("INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name))],
    )
    return person_id


def _filed_by(client: TestClient, asset_id: str, person_id: str, source: str | None) -> None:
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, ?)",
                (asset_id, person_id, source),
            )
        ],
    )


def _face_matched(client: TestClient, asset_id: str, person_id: str, attribution: str) -> None:
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality,"
                " person_id, attribution, created_at) VALUES (?, ?, 0, 1000, 1, 0.9, ?, ?, 0)",
                (new_id(), asset_id, person_id, attribution),
            )
        ],
    )


def test_enriched_counts_each_author_and_agrees_with_the_filter(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    anna = _a_person(client, "Anna")
    _filed_by(client, library.shared, anna, "folder")
    _face_matched(client, library.private, anna, "matched")

    assert _facets(client, "enriched") == {"folder": 1, "faces": 1}
    assert _reachable(client, enriched="folder") == 1
    assert _reachable(client, enriched="faces") == 1
    assert _reachable(client, enriched="stash") == 0
    assert _reachable(client, enriched="any") == 2
    assert _reachable(client, enriched="none") == 0


def test_a_person_somebody_named_by_hand_enriched_nothing(
    client: TestClient, library: Library
) -> None:
    """A name a person typed onto a file is theirs, and a face only PROPOSED is not yet anybody's."""
    sign_in(client, "admin")
    anna = _a_person(client, "Anna")
    _filed_by(client, library.shared, anna, None)
    _face_matched(client, library.private, anna, "suggested")

    # `none` IS A ROW OF THIS COLUMN, and it is these two files: the column's join is wide enough
    # that "how much of this library has nobody filled in", which is what somebody opens it to ask,
    # is a number it can draw rather than one it could only be filtered for. The row counts
    # exactly what `enriched:none` returns, as every row here does.
    assert _facets(client, "enriched") == {"none": 2}
    assert _reachable(client, enriched="none") == 2


def test_a_stash_box_that_put_a_person_on_the_file_counts_as_the_stash_box(
    client: TestClient, library: Library
) -> None:
    """`stash_box` is the word the enrichment pass writes (`enrich.IMPORTED`), and it is the
    stash-box's mark; a predicate reading any other word counts nothing.

    THE FILTER FINDS IT AND THE COLUMN HAS NO ROW FOR IT, which is the cost of having no union row
    and is written down here rather than left to be discovered. A
    `source = 'stash_box'` attribution records that a box did it and NOT which, so there is no box
    row it can go under; the only row it ever had was the union, and that row said "one of the
    three underneath me".

    It cannot arise from an ordinary enrichment (the writer attributes the person in the same
    request that applies the match, so the match row is there), and it CAN arise after the match
    rows have been dropped, which is what re-keying a box does to its cache. The attribution
    survives, the evidence of which box does not, and this file is then enriched with nothing to
    say by whom."""
    sign_in(client, "admin")
    anna = _a_person(client, "Anna")
    _filed_by(client, library.shared, anna, IMPORTED)

    # The other file, which nothing wrote to. See the `none` row's note above.
    assert _facets(client, "enriched") == {"none": 2}
    assert _reachable(client, enriched="stash") == 1


def test_each_stash_box_counts_under_its_own_word_and_the_union_is_no_longer_a_row(
    client: TestClient, library: Library
) -> None:
    """Every row of this column says WHO wrote and HOW, so each box is a row and the union is not.

    An install with several services configured can have them disagree about who is in a scene,
    and one row reading "a stash-box" sends somebody through Settings to find out which said so.
    There is no union row: its count would be the sum of its neighbours and clicking it would
    return what clicking all of them returns.

    `enriched:stash` IS STILL A FILTER and is asserted below: the predicate reads all three
    tables, so an address carrying that word narrows to every box's files. Only the row is absent.

    A box Sift has no word for is not a row at all. There is nothing to put in a filter, and a row
    nobody can click is worse than no row, and with no union row, a file whose ONLY box is
    that one has no row here at all. See the test above for the other shape of the same hole.
    """
    sign_in(client, "admin")
    anna = _a_person(client, "Anna")
    _filed_by(client, library.private, anna, IMPORTED)
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at)"
                " VALUES (?, ?, ?, ?, 0)",
                (box, name, f"https://{box}.test/graphql", slug),
            )
            for box, name, slug in (
                ("box-known", "FansDB", "fansdb"),
                ("box-nameless", "Somebody's Own", None),
            )
        ]
        + [
            (
                "INSERT INTO asset_stash_box_matches"
                " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
                " VALUES (?, ?, 'remote', '[]', 'certain', ?, 0, 0)",
                (asset, box, state),
            )
            for asset, box, state in (
                (library.shared, "box-known", "applied"),
                (library.shared, "box-nameless", "applied"),
                # A match still WAITING is a question nobody has answered: that box has written
                # nothing to this file, so it must not be counted under its row. The same line
                # `enriched_stash` draws, and the reason the number and the filter agree.
                (library.private, "box-known", "waiting"),
            )
        ],
    )

    assert _facets(client, "enriched") == {"fansdb": 1, "none": 1}
    # The row selects exactly the files it counted, which is the whole property.
    assert _reachable(client, enriched="fansdb") == 1
    assert _reachable(client, enriched="stash") == 2
    # A word no box here carries finds nothing, rather than being refused as a typo: the same
    # answer `network:` gives for an id that names no network.
    assert _reachable(client, enriched="pmvstash") == 0


def test_a_file_two_authors_wrote_to_is_under_both_and_counts_once_each(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    anna = _a_person(client, "Anna")
    _filed_by(client, library.shared, anna, "folder")
    _face_matched(client, library.shared, anna, "confirmed")
    _face_matched(client, library.shared, anna, "matched")

    # One file under both rows, and the OTHER file under `none`, which is the pair that makes
    # this column readable: a row per author, and one saying how much nothing has touched.
    assert _facets(client, "enriched") == {"folder": 1, "faces": 1, "none": 1}
    assert _reachable(client, enriched="any") == 1


def test_a_guest_is_told_there_is_no_such_dimension(client: TestClient, library: Library) -> None:
    """Not "you may not ask that", which would confirm there is something there to ask about. The
    same refusal an invented dimension gets, in the same words."""
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, guest)
    invented = client.get("/api/assets/facets", params={"facet": "shoe_size"})

    sign_in(client, "guest")
    refused = client.get("/api/assets/facets", params={"facet": "sharing"})

    assert refused.status_code == 422
    assert refused.status_code == invented.status_code


def test_the_file_itself_says_how_it_was_enriched_in_the_filters_own_words(
    client: TestClient, library: Library
) -> None:
    """The marks on a file's own screen read the SAME three predicates the column counts by, so
    the two can never disagree, and a file nothing has touched says so with an empty list.

    `via` is the filter's word and `name` is null for both of these: Sift read the face and Sift
    read the folder, and there is nothing else to name. Only a stash-box has a name, which is the
    test below.
    """
    sign_in(client, "admin")
    anna = _a_person(client, "Anna")
    _filed_by(client, library.shared, anna, "folder")
    _face_matched(client, library.shared, anna, "matched")

    shared = client.get(f"/api/assets/{library.shared}")
    private = client.get(f"/api/assets/{library.private}")

    assert shared.status_code == 200
    # `box` is the SLUG beside the name, which is what paints a mark in that box's own colours.
    # Null for both of these for the same reason `name` is: Sift did it and there is no box.
    assert shared.json()["enriched_by"] == [
        {"via": "faces", "name": None, "box": None},
        {"via": "folder", "name": None, "box": None},
    ]
    assert private.json()["enriched_by"] == []


def test_the_file_names_the_stash_box_that_recognised_it(
    client: TestClient, library: Library
) -> None:
    """WHICH box, and one entry per box on a file two of them recognised.

    This is what the shape carries the box for: "A stash-box", on a library with several services
    configured, is the exact word that sends somebody through Settings to find out which.

    The second half is the case that keeps the marks honest. With no applied match at all the file
    is still stash-enriched, because the person on it carries the pass's own word, and the mark is
    drawn WITHOUT a name rather than dropped. Otherwise a file the `enriched:stash` column still
    counts would come up bare on its own page, which is the one thing this read exists to prevent.
    """
    sign_in(client, "admin")
    for box_id, name, slug, decided in (
        ("older", "PMVStash", "pmvstash", 100),
        ("newer", "StashDB", None, 200),
    ):
        write(
            db_path(client),
            [
                (
                    "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at)"
                    " VALUES (?, ?, ?, ?, 0)",
                    (box_id, name, f"https://example.invalid/{box_id}", slug),
                ),
                (
                    "INSERT INTO asset_stash_box_matches (asset_id, box_id, remote_id, payload,"
                    " grade, state, found_at, decided_at)"
                    " VALUES (?, ?, 'remote', '[]', 'certain', 'applied', 0, ?)",
                    (library.shared, box_id, decided),
                ),
            ],
        )

    named = client.get(f"/api/assets/{library.shared}").json()["enriched_by"]

    # The box's own WORD travels with its name: the name is what the mark says and the word is
    # what it is painted in. Null for a box at an address Sift has never heard of, which is drawn
    # in the ordinary accent under its own name rather than in a colour invented for it.
    assert named == [
        {"via": "stash", "name": "StashDB", "box": None},
        {"via": "stash", "name": "PMVStash", "box": "pmvstash"},
    ]

    write(
        db_path(client),
        [("DELETE FROM asset_stash_box_matches WHERE asset_id = ?", (library.shared,))],
    )
    _filed_by(client, library.shared, _a_person(client, "Anna"), IMPORTED)

    unnamed = client.get(f"/api/assets/{library.shared}").json()["enriched_by"]

    assert unnamed == [{"via": "stash", "name": None, "box": None}]


# --- what the people on a file are like ------------------------------------------------------
#
# The file's side of a facet that belongs to a PERSON. Every one of these asks the pair of
# questions the module is written around: what does the column say, and what does clicking its row
# actually return. They read two different pieces of SQL over the same two tables, which is exactly
# where a count and a filter drift apart.


def _person_like(client: TestClient, name: str, **columns: object) -> str:
    """A person with some of their record filled in. Invented names throughout."""
    person_id = _a_person(client, name)
    for column, value in columns.items():
        write(
            db_path(client),
            [(f"UPDATE people SET {column} = ? WHERE id = ?", (value, person_id))],  # noqa: S608
        )
    return person_id


def test_a_persons_word_is_counted_on_the_file_and_the_row_is_the_filter(
    client: TestClient, library: Library
) -> None:
    """Hair colour, stored as the stash-box's own upper-case word and typed in lower case."""
    sign_in(client, "admin")
    _filed_by(client, library.shared, _person_like(client, "Marlo Venn", hair_color="BLONDE"), None)
    _filed_by(client, library.private, _person_like(client, "Ivy Sarn", hair_color="RED"), None)

    assert _facets(client, "hair") == {"BLONDE": 1, "RED": 1}
    assert _reachable(client, q="hair:blonde") == 1
    # The label's spelling and the registry's both reach the same filter.
    assert _reachable(client, hair_colour="blonde") == 1
    assert _reachable(client, hair_color="blonde") == 1


def test_a_word_does_not_find_its_neighbour(client: TestClient, library: Library) -> None:
    """The narrowing has to be exact: a file whose only person is red-haired is not a blonde file,
    and a comparison written as a LIKE or folded on one side only would say it was."""
    sign_in(client, "admin")
    _filed_by(client, library.shared, _person_like(client, "Ivy Sarn", hair_color="RED"), None)

    assert _reachable(client, q="hair:blonde") == 0
    assert _reachable(client, q="hair:red") == 1


def test_two_people_of_one_kind_on_one_file_count_it_once(
    client: TestClient, library: Library
) -> None:
    """`COUNT(DISTINCT a.id)`: a dimension join multiplies rows, and the question is how many
    FILES. Two blonde people in one file is one blonde file."""
    sign_in(client, "admin")
    _filed_by(client, library.shared, _person_like(client, "Marlo Venn", hair_color="BLONDE"), None)
    _filed_by(client, library.shared, _person_like(client, "Dell Quay", hair_color="BLONDE"), None)

    assert _facets(client, "hair") == {"BLONDE": 1}
    assert _reachable(client, q="hair:blonde") == 1


def test_every_word_a_person_carries_is_its_own_column(
    client: TestClient, library: Library
) -> None:
    """One person, six answers, six columns. Written as one test because it is one join with a
    different column in it: six near-identical tests would say nothing the first does not."""
    sign_in(client, "admin")
    person = _person_like(
        client,
        "Marlo Venn",
        gender="FEMALE",
        eye_color="BROWN",
        ethnicity="ASIAN",
        country="US",
        breast_type="NATURAL",
    )
    _filed_by(client, library.shared, person, None)

    assert _facets(client, "gender") == {"FEMALE": 1}
    assert _facets(client, "eyes") == {"BROWN": 1}
    assert _facets(client, "ethnicity") == {"ASIAN": 1}
    assert _facets(client, "nationality") == {"US": 1}
    assert _facets(client, "breasts") == {"NATURAL": 1}
    assert _reachable(client, q="nationality:us") == 1
    assert _reachable(client, q="eyes:brown") == 1


def test_a_band_row_returns_exactly_the_files_it_counted(
    client: TestClient, library: Library
) -> None:
    """Height, in ten-centimetre bands. The band expression is one definition shared by the column
    and the condition, so this is what proves the two really are reading it."""
    sign_in(client, "admin")
    _filed_by(client, library.shared, _person_like(client, "Marlo Venn", height_cm=165), None)
    _filed_by(client, library.private, _person_like(client, "Dell Quay", height_cm=178), None)

    assert _facets(client, "height") == {"160-169": 1, "170-179": 1}
    assert _reachable(client, q="height:160-169") == 1
    assert _reachable(client, height_cm="170-179") == 1
    # A bare number describes no band, so it narrows to nothing rather than widening.
    assert _reachable(client, q="height:165") == 0


def test_an_age_is_worked_out_from_a_birthdate_and_round_trips(
    client: TestClient, library: Library
) -> None:
    """`age:27` is the value the column produced, written back as the filter, and a span kept from
    an older cut of the column still finds the ages it covered. The birthdate is relative to
    today so the age cannot go stale with the calendar."""
    sign_in(client, "admin")
    born = "date('now', '-27 years')"
    person = _a_person(client, "Marlo Venn")
    write(
        db_path(client),
        [(f"UPDATE people SET birth_date = {born} WHERE id = ?", (person,))],  # noqa: S608
    )
    _filed_by(client, library.shared, person, None)

    assert _facets(client, "age") == {"27": 1}
    assert _reachable(client, q="age:27") == 1
    assert _reachable(client, q="age:28") == 0
    assert _reachable(client, q="age:25-29") == 1
    assert _reachable(client, q="age:30-34") == 0


def test_a_year_is_counted_off_the_files_own_date_and_round_trips(
    client: TestClient, library: Library
) -> None:
    """`released:2021`: the year, not the day, and the row is the filter."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            ("UPDATE assets SET release_date = ? WHERE id = ?", ("2021-06-04", library.shared)),
            ("UPDATE assets SET release_date = ? WHERE id = ?", ("2019-01-30", library.private)),
        ],
    )

    assert _facets(client, "released") == {"2021": 1, "2019": 1}
    assert _reachable(client, q="released:2021") == 1
    assert _reachable(client, release_date="2019") == 1
    # A file with no date is not a value of the dimension, and is not excluded by a negation.
    assert _reachable(client, q="-released:2021") == 1
    # There is no second year dimension over `production_date`: the shooting year is blank on
    # nearly every row, so the panel would draw one value. The COLUMN and the record field remain.


# --- the network a file was put out by -------------------------------------------------------


def _site(client: TestClient, name: str, parent_id: str | None = None) -> str:
    site_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO sites (id, name, parent_id) VALUES (?, ?, ?)",
                (site_id, name, parent_id),
            )
        ],
    )
    return site_id


def _posted_on(client: TestClient, asset_id: str, site_id: str, name: str) -> None:
    username_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 0)",
                (username_id, site_id, name),
            ),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (asset_id, username_id),
            ),
        ],
    )


def _facet_rows(client: TestClient, facet: str) -> list[dict[str, object]]:
    answer = client.get("/api/assets/facets", params={"facet": facet})
    assert answer.status_code == 200, answer.text
    return list(answer.json()["values"])


def test_a_network_counts_every_label_under_it_and_carries_its_name(
    client: TestClient, library: Library
) -> None:
    """The value is the network's ID, because two labels may be called the same thing and one row
    meaning two networks would select more files than it counted. The NAME rides beside it so the
    panel does not have to look it up through a second read."""
    sign_in(client, "admin")
    network = _site(client, "Tidecrest")
    label = _site(client, "Tidecrest Blue", network)
    _posted_on(client, library.shared, label, "one")
    _posted_on(client, library.private, network, "two")

    rows = _facet_rows(client, "network")

    assert rows == [{"value": network, "count": 2, "label": "Tidecrest"}]
    # The row IS the filter: everything under the network, the network's own files included.
    assert _reachable(client, q=f"network:{network}") == 2


def test_two_people_with_one_name_are_two_rows_each_counting_its_own_wall(
    client: TestClient, library: Library
) -> None:
    """A person's row is their id with the name beside it, so a namesake never adds to it."""
    sign_in(client, "admin")
    busy = _a_person(client, "Esme Wrenfield")
    namesake = _a_person(client, "Esme Wrenfield")
    _filed_by(client, library.shared, busy, "folder")
    _filed_by(client, library.private, busy, "folder")
    _filed_by(client, library.private, namesake, "folder")

    rows = [row for row in _facet_rows(client, "people") if row["value"] not in ("any", "none")]

    assert rows == [
        {"value": busy, "count": 2, "label": "Esme Wrenfield"},
        {"value": namesake, "count": 1, "label": "Esme Wrenfield"},
    ]
    for row in rows:
        assert _reachable(client, people=str(row["value"])) == row["count"]


def test_a_site_with_nothing_within_it_is_no_network(client: TestClient, library: Library) -> None:
    """A Site with no Site within it and none above it is not a network, and the column does not
    list it as one. A file filed only under it is under no network, as a file with no Site is."""
    sign_in(client, "admin")
    alone = _site(client, "Harrowgate")
    _posted_on(client, library.shared, alone, "one")

    assert _facets(client, "network") == {}


def test_a_scalar_facet_carries_no_label(client: TestClient, library: Library) -> None:
    """Every dimension whose value is already the readable thing says so with a null, which is what
    keeps the client from inventing a second way to read a row."""
    sign_in(client, "admin")
    _filed_by(client, library.shared, _person_like(client, "Marlo Venn", hair_color="BLONDE"), None)

    assert _facet_rows(client, "hair") == [{"value": "BLONDE", "count": 1, "label": None}]


def test_a_guest_may_narrow_by_what_the_people_in_it_are_like(
    client: TestClient, library: Library
) -> None:
    """`sharing:` is an admin's question because it describes an admin's decisions. None of these
    do (they describe the file and the people in it), so a guest gets the narrowing rather than
    the condition nothing satisfies, and the count is the count of what a guest can open."""
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    share(client, library.shared, guest)
    _filed_by(client, library.shared, _person_like(client, "Marlo Venn", hair_color="BLONDE"), None)
    _filed_by(client, library.private, _person_like(client, "Dell Quay", hair_color="BLONDE"), None)

    sign_in(client, "guest")
    assert _facets(client, "hair") == {"BLONDE": 1}
    assert _reachable(client, q="hair:blonde") == 1


# --- a band is a band, and a row is the filter behind it -----------------------------------


_CLIP_EPOCH = 1_700_000_000


def _clip(
    client: TestClient,
    library: Library,
    *,
    duration_ms: int | None = None,
    shorter_side: int = 1080,
) -> str:
    """One more video in the same folder, with the two measurements the bands are cut on.

    Rows rather than a route, for the reason the fixtures beside this give: what is being set up is
    the state, so a test of the counting is a test of the counting.
    """
    asset_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO assets (id, identity, media_type, width, height, duration_ms,"
                " size_bytes, original_filename, added_at)"
                " VALUES (?, ?, 'video', ?, ?, ?, 14, ?, ?)",
                (
                    asset_id,
                    f"digest-{asset_id}",
                    shorter_side * 2,
                    shorter_side,
                    duration_ms,
                    f"{asset_id}.mp4",
                    _CLIP_EPOCH,
                ),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                (new_id(), asset_id, library.root, library.folder, f"clips/{asset_id}", "x.mp4"),
            ),
        ],
    )
    return asset_id


def _opinion(
    client: TestClient,
    asset_id: str,
    user_id: str,
    *,
    last_viewed_at: int | None = None,
    resume_ms: int | None = None,
    completed_at: int | None = None,
) -> None:
    """What one user has done with one file, written as the row the player would leave.

    `resume_ms` WITHOUT `last_viewed_at` is not a contrived case and is the whole reason this
    helper takes them separately: a sitting too short to count as a view still writes where it got
    to and deliberately leaves the history stamp alone. See `record_watch_time` in the kernel.
    """
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, view_count, watched_ms,"
                " resume_ms, last_viewed_at, completed_at, updated_at)"
                " VALUES (?, ?, 1, 0, ?, ?, ?, 0)",
                (asset_id, user_id, resume_ms, last_viewed_at, completed_at),
            )
        ],
    )


def _row_selects_what_it_counted(client: TestClient, facet: str) -> None:
    """Every row of one column, asked the only question that matters about a facet.

    The count beside a row and the number of files clicking it returns are the same thing said
    twice, and a row where they differ is a number nobody can act on, which is worse than no
    number, because it looks like one. Asked over the HTTP surface both halves really use, so the
    band expression, the parser and the permission rule are all the real ones.
    """
    counted = dict(_column(client, facet))
    assert counted, f"the {facet} column produced no rows to check"
    for value, count in counted.items():
        assert _reachable(client, q=f"{facet}:{value}") == count, f"{facet}:{value}"


def test_every_duration_band_selects_exactly_what_it_counted(
    client: TestClient, library: Library
) -> None:
    """The edges, which is the only place this can go wrong.

    The bands are cut with `<`, so the file of exactly 60,000 ms belongs to the second band, and
    a closed `0s..60s` would claim it back, so the row would disagree with the wall it opens. Every
    seeded duration here is a boundary or one millisecond either side of one.
    """
    for duration in (
        0,
        1,
        59_999,
        60_000,
        179_999,
        180_000,
        299_999,
        300_000,
        899_999,
        900_000,
        1_799_999,
        1_800_000,
        3_599_999,
        3_600_000,
        5_000_000,
    ):
        _clip(client, library, duration_ms=duration)

    sign_in(client, "admin")
    _row_selects_what_it_counted(client, "duration")


def test_every_resolution_band_selects_exactly_what_it_counted(
    client: TestClient, library: Library
) -> None:
    """The same question of the other banded column, including the bottom band that opens downward."""
    for side in (1, 479, 480, 719, 720, 1079, 1080, 1439, 1440, 2159, 2160, 4319, 4320, 8000):
        _clip(client, library, duration_ms=1000, shorter_side=side)

    sign_in(client, "admin")
    _row_selects_what_it_counted(client, "resolution")


def test_every_view_state_selects_exactly_what_it_counted(
    client: TestClient, library: Library
) -> None:
    """Three states, and a file is in exactly one of them.

    The fifth file is the hard one: a place to go back to and no view stamped on it, the row a
    sitting too short to count leaves behind. The column puts it under Continue watching, and
    `viewed:none` must not claim it as well, nor the middle row return files the same column has
    already counted under Continue watching.
    """
    admin = sign_in(client, "admin")
    never = _clip(client, library, duration_ms=600_000)
    opened = _clip(client, library, duration_ms=600_000)
    finished = _clip(client, library, duration_ms=600_000)
    resuming = _clip(client, library, duration_ms=600_000)
    resuming_unseen = _clip(client, library, duration_ms=600_000)
    assert never

    _opinion(client, opened, admin, last_viewed_at=100)
    _opinion(client, finished, admin, last_viewed_at=100, completed_at=100)
    _opinion(client, resuming, admin, last_viewed_at=100, resume_ms=300_000)
    _opinion(client, resuming_unseen, admin, resume_ms=300_000)

    assert _facets(client, "viewed")["continue"] == 2
    _row_selects_what_it_counted(client, "viewed")


# --- asked a stash-box --------------------------------------------------------------------------
#
# WHEN a box was last asked about a file, which is a different question from who wrote to it.
#
# Not `enrichment_runs`, which has one row per box that ENRICHED something: an empty row called
# "Never enriched" over that table would contradict the "Enriched by" column on the same screen.


def _asked(client: TestClient, asset_id: str, *, at: int, found: bool) -> None:
    """A box asked about one file, and what it answered. `scan_one` writes this on every ask."""
    write(
        db_path(client),
        [
            (
                "INSERT OR IGNORE INTO stash_boxes (id, name, endpoint, created_at)"
                " VALUES ('box-1', 'Quillbox', 'https://example.invalid/graphql', 0)",
                (),
            ),
            (
                "INSERT INTO stash_box_scans (asset_id, box_id, scanned_at, found)"
                " VALUES (?, 'box-1', ?, ?)",
                (asset_id, at, 1 if found else 0),
            ),
        ],
    )


def test_the_asks_column_counts_asks_and_not_enrichments(
    client: TestClient, library: Library
) -> None:
    """A box asked and answering nothing is still an ask, and that is what the words say."""
    sign_in(client, "admin")
    _asked(client, library.shared, at=int(time.time()), found=False)

    assert _facets(client, "enrichment") == {"today": 1, "never": 1}
    _row_selects_what_it_counted(client, "enrichment")


def test_kept_local_counts_the_whole_rule_including_what_a_file_is_filed_under(
    client: TestClient, library: Library
) -> None:
    """The row is a DECISION, and the decision reaches down.

    Keeping a Site local keeps every file under it local, the way sharing is inherited, so a
    column that counted only the file's own switch would say nothing was being held back while the
    door was refusing every one of them.
    """
    sign_in(client, "admin")
    site = new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO sites (id, name) VALUES (?, 'Quillhouse')", (site,)),
            (
                "INSERT INTO usernames (id, site_id, name, created_at)"
                " VALUES ('acct-1', ?, 'quillwright', 0)",
                (site,),
            ),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, 'acct-1')",
                (library.shared,),
            ),
            ("UPDATE sites SET keep_local = 1 WHERE id = ?", (site,)),
        ],
    )

    assert _facets(client, "enrichment") == {"local": 1, "never": 1}
    _row_selects_what_it_counted(client, "enrichment")


# --- who made a file, and the facets every file has a value for -------------------------------


def _made(client: TestClient, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    write(db_path(client), statements)


def test_created_counts_each_file_under_one_maker_and_the_row_is_the_filter(
    client: TestClient, library: Library
) -> None:
    """Every file answers who made it exactly one way: a copy Sift made by Compress or by the
    editor, a swap, a download, or Sift adding it from a folder of the library. A download that
    began after the file arrived found it here already and made nothing."""
    sign_in(client, "admin")
    compressed = _clip(client, library, duration_ms=1)
    edited = _clip(client, library, duration_ms=1)
    fetched = _clip(client, library, duration_ms=1)
    swapped = _clip(client, library, duration_ms=1)
    found_again = _clip(client, library, duration_ms=1)
    event = new_id()
    _made(
        client,
        [
            (
                "INSERT INTO produced_files (id, asset_id, operation, produced_at)"
                " VALUES (?, ?, 'compress', 0)",
                (new_id(), compressed),
            ),
            (
                "INSERT INTO produced_files (id, asset_id, operation, produced_at)"
                " VALUES (?, ?, 'trim', 0)",
                (new_id(), edited),
            ),
            (
                "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at)"
                " VALUES (?, 'https://example.invalid/one', ?, 'done', ?, ?)",
                (new_id(), new_id(), fetched, _CLIP_EPOCH - 5),
            ),
            (
                "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at)"
                " VALUES (?, 'https://example.invalid/two', ?, 'done', ?, ?)",
                (new_id(), new_id(), found_again, _CLIP_EPOCH + 3600),
            ),
            (
                "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at,"
                " verb, actor_kind, actor_id) VALUES (?, 'ledger', '', '', '{}', 0, 'added',"
                " 'sift', 'swap')",
                (event,),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                " VALUES (?, 'asset', ?)",
                (event, swapped),
            ),
        ],
    )

    assert _facets(client, "created") == {
        "compress": 1,
        "edit": 1,
        "download": 1,
        "swap": 1,
        "library": 3,
    }
    _row_selects_what_it_counted(client, "created")
    # The column's label is a spelling the language takes, so the panel teaches a working word.
    assert _reachable(client, q="created_by:download") == 1


def test_favorites_loops_left_out_and_photo_sets_count_and_each_row_is_the_filter(
    client: TestClient, library: Library
) -> None:
    """Four things a file has a value for, each a column of the Files wall: whether this account
    hearted it, whether a Loop is marked in it, what Sift could not make for it, and the Photo
    Sets it is in. Each row writes the filter that returns exactly the files it counted."""
    admin = sign_in(client, "admin")
    album = new_id()
    _made(
        client,
        [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, favorite, updated_at)"
                " VALUES (?, ?, 1, 0)",
                (library.shared, admin),
            ),
            (
                "INSERT INTO loops (id, asset_id, start_ms, end_ms, created_at)"
                " VALUES (?, ?, 0, 1000, 0)",
                (new_id(), library.private),
            ),
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, at)"
                " VALUES (?, 'thumbnails', 'unreadable', 'no frame', 0)",
                (library.private,),
            ),
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, at, transient)"
                " VALUES (?, 'faces', 'busy', 'try again', 0, 1)",
                (library.shared,),
            ),
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, at)"
                " VALUES (?, 'probe', 'unreadable', 'no header', 0)",
                (library.shared,),
            ),
            (
                "INSERT INTO photo_sets (id, name, created_at) VALUES (?, 'Shoreline', 0)",
                (album,),
            ),
            (
                "INSERT INTO photo_set_items (photo_set_id, asset_id) VALUES (?, ?)",
                (album, library.shared),
            ),
        ],
    )

    assert _facets(client, "fav") == {"yes": 1, "no": 1}
    assert _facets(client, "loops") == {"any": 1, "none": 1}
    # A retry still to come is no file given up on, and the read before a Build is no product.
    assert _facets(client, "left_out") == {"thumbnails": 1}
    assert _facets(client, "photo_sets") == {"Shoreline": 1}
    for facet in ("fav", "loops", "left_out", "photo_sets"):
        _row_selects_what_it_counted(client, facet)


def test_an_o_count_is_one_row_per_number_and_each_row_is_the_filter(
    client: TestClient, library: Library
) -> None:
    """The count itself, not a band: three presses is the row `3`, and `o_count:3` returns it."""
    admin = sign_in(client, "admin")
    _made(
        client,
        [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)"
                " VALUES (?, ?, 3, 0)",
                (library.shared, admin),
            )
        ],
    )

    assert _facets(client, "o_count") == {"0": 1, "3": 1}
    _row_selects_what_it_counted(client, "o_count")
    # A range kept from an older cut of the column still returns exactly its files.
    assert _reachable(client, q="o_count:2..4") == 1
