# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the picks leave out before Start, and why.

The fault this guards: a person picked whole with a mark on them sends nothing of theirs, and the
figure before Start is right (it is the offer's own read) with nothing on screen to say why it is
so much smaller than the picks. The answer names every pick that wears a mark and counts the rest,
through the same filters and the same vault-shut read the offer makes, so nothing in Hidden is ever
counted.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.access.catalog import refused_for_swaps_among, refusers_of_file
from sift.slices.swap import refusal
from sift.slices.swap.models import Chosen, WeighSwap
from sift.slices.swap.router import kept_from_swaps, weigh_picks
from sift.slices.swap.tests.test_offer import Library, _NoFilters, library  # noqa: F401
from sift.slices.swap.weight import LeftOutBy, Weight, left_out, weigh
from sift.testing.fixtures import hide

pytestmark = pytest.mark.unit


async def _left_out(lib: Library, chosen: list[Chosen]) -> tuple[tuple[LeftOutBy, ...], int]:
    return await left_out(lib.access, lib.db, _NoFilters(), lib.admin, chosen)


async def test_a_pick_with_nothing_marked_on_it_counts_what_a_mark_above_keeps_back(
    library: Library,  # noqa: F811
) -> None:
    ids = library.ids
    # The person's files: `p1` and `p2` go; `local` (its own switch) and `local_tag` (a tag kept
    # local) stay; `vaulted` is in Hidden and is counted nowhere.
    named, other = await _left_out(library, [Chosen(kind="person", id=ids["person"])])
    assert named == ()
    assert other == 2


async def test_what_a_mark_above_keeps_back_is_read_past_one_page(
    library: Library,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The files a mark elsewhere keeps back are read a wall's page at a time: with a page of one
    file, both of the person's are still counted, each once."""
    from sift.slices.swap import weight as weight_module

    monkeypatch.setattr(weight_module, "MAX_PAGE_SIZE", 1)
    named, other = await _left_out(library, [Chosen(kind="person", id=library.ids["person"])])
    assert (named, other) == ((), 2)


async def test_a_marked_pick_that_reaches_no_file_is_not_named(
    library: Library,  # noqa: F811
) -> None:
    """A mark on a tag nothing is filed under keeps nothing back, so there is nothing to explain."""
    await library.db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at, keep_from_swaps)"
        " VALUES ('empty-tag', 'unused', 'unused', 0, 1)"
    )
    marked = await _left_out(library, [Chosen(kind="tag", id="empty-tag")])
    assert marked == ((), 0)
    kept = await _left_out(library, [Chosen(kind="tag", id=library.ids["kept_tag"])])
    assert [(one.name, one.files) for one in kept[0]] == [("private", 1)], "the same read names one"


async def test_a_pick_kept_local_is_named_with_every_file_it_keeps_back(
    library: Library,  # noqa: F811
) -> None:
    ids = library.ids
    await library.db.execute("UPDATE people SET keep_local = 1 WHERE id = ?", (ids["person"],))
    person = Chosen(kind="person", id=ids["person"])
    named, other = await _left_out(library, [person])
    # Every file of theirs this viewer sees with the vault shut: four, and never the hidden one.
    assert named == (
        LeftOutBy(kind="person", id=ids["person"], name="Juno Pellerin", mark="local", files=4),
    )
    # The two the tag and the file's own switch keep back are the person's too: explained already.
    assert other == 0
    weight = await weigh(library.access, library.db, _NoFilters(), library.admin, [person])
    assert (weight.files, weight.left_out, weight.left_out_other) == (0, named, 0)


async def test_a_pick_marked_dont_swap_is_named_and_the_others_still_counted(
    library: Library,  # noqa: F811
) -> None:
    ids = library.ids
    await library.db.execute("UPDATE sites SET keep_from_swaps = 1 WHERE id = ?", (ids["site"],))
    chosen = [Chosen(kind="site", id=ids["site"]), Chosen(kind="person", id=ids["person"])]
    named, other = await _left_out(library, chosen)
    # The Site reaches `s1` and `p1`; its mark keeps both back.
    assert named == (
        LeftOutBy(kind="site", id=ids["site"], name="Northlight Media", mark="swap", files=2),
    )
    # The person is not marked: what keeps their `local` and `local_tag` back is on something
    # else, and `p1` is the Site's, explained already.
    assert other == 2


async def test_a_pick_in_hidden_is_named_like_any_other(library: Library) -> None:  # noqa: F811
    # Hidden decides what a screen shows, not what a swap carries: the mark on the file does.
    ids = library.ids
    await library.db.execute(
        "UPDATE assets SET keep_from_swaps = 1 WHERE id = ?", (ids["vaulted"],)
    )
    named, other = await _left_out(library, [Chosen(kind="asset", id=ids["vaulted"])])
    assert [(one.kind, one.mark, one.files) for one in named] == [("asset", "swap", 1)]
    assert other == 0
    # Nobody to read as: nothing named, nothing counted.
    gone = Viewer(id="nobody", role=Role.ADMIN)
    person = Chosen(kind="person", id=ids["person"])
    assert await left_out(library.access, library.db, _NoFilters(), gone, [person]) == ((), 0)


async def test_a_single_file_marked_is_named_by_its_name(library: Library) -> None:  # noqa: F811
    ids = library.ids
    await library.db.execute("UPDATE assets SET keep_from_swaps = 1 WHERE id = ?", (ids["p1"],))
    picks = [Chosen(kind="asset", id=ids["p1"]), Chosen(kind="asset", id=ids["p1"])]
    named, other = await _left_out(library, picks)
    assert [(one.kind, one.mark, one.files) for one in named] == [("asset", "swap", 1)]
    assert other == 0


async def test_the_route_says_the_weight_and_what_is_left_out(library: Library) -> None:  # noqa: F811
    by = LeftOutBy(kind="person", id="p", name="Juno Pellerin", mark="local", files=5679)

    class _Sessions:
        async def weigh(self, viewer: Any, chosen: Any) -> Weight:
            return Weight(files=14, bytes=2_220_000_000, left_out=(by,), left_out_other=3)

    answer = await weigh_picks(
        WeighSwap(chosen=[Chosen(kind="person", id="p")]),
        _Sessions(),  # type: ignore[arg-type]
        library.admin,
    )
    assert (answer.files, answer.bytes, answer.left_out_other) == (14, 2_220_000_000, 3)
    assert [one.model_dump() for one in answer.left_out] == [
        {"kind": "person", "id": "p", "name": "Juno Pellerin", "mark": "local", "files": 5679}
    ]


# --- the marks swap mode draws ---------------------------------------------------------------------


async def test_a_page_of_files_says_which_will_not_go_by_either_mark_from_anywhere_above(
    library: Library,  # noqa: F811
) -> None:
    ids = library.ids
    page = [ids[name] for name in ("p1", "p2", "s1", "t1", "local", "local_tag")]
    # The file's own Kept local and a tag kept local above one: both marks, both ways in.
    assert await refused_for_swaps_among(library.db, page) == {ids["local"], ids["local_tag"]}
    await library.db.execute("UPDATE sites SET keep_from_swaps = 1 WHERE id = ?", (ids["site"],))
    # "Don't swap" on a Site reaches the files filed under its usernames, and nothing else.
    assert await refused_for_swaps_among(library.db, page) == {
        ids["local"],
        ids["local_tag"],
        ids["s1"],
        ids["p1"],
    }
    assert await refused_for_swaps_among(library.db, []) == set()


async def test_a_file_says_what_above_it_keeps_it_out_and_by_which_mark(
    library: Library,  # noqa: F811
) -> None:
    ids = library.ids
    await library.db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (ids["person"],))
    await library.db.execute("UPDATE sites SET keep_local = 1 WHERE id = ?", (ids["site"],))
    above = await refusers_of_file(library.db, ids["p1"])
    assert [(one.kind, one.id, one.kept_local) for one in above] == [
        ("person", ids["person"], False),
        ("site", ids["site"], True),
    ]
    assert await refusers_of_file(library.db, ids["t1"]) == []
    assert await refusers_of_file(library.db, "") == []

    # The route names them as the reader may see them, and says the file's own switches apart.
    answer = await kept_from_swaps("asset", ids["p1"], library.access, library.db, library.admin)
    assert (answer.kept_out, answer.kept_out_here, answer.kept_local_here) == (True, False, False)
    assert [(one.kind, one.name, one.mark) for one in answer.by] == [
        ("person", "Juno Pellerin", "swap"),
        ("site", "Northlight Media", "local"),
    ]
    own = await kept_from_swaps("asset", ids["local"], library.access, library.db, library.admin)
    # Its own Kept local, said apart from the person above it, who keeps it out as well.
    assert own.kept_local_here is True
    assert [one.name for one in own.by] == ["Juno Pellerin"]
    # An entity answers for itself alone, so it names nothing above it.
    person = await kept_from_swaps(
        "person", ids["person"], library.access, library.db, library.admin
    )
    assert (person.kept_out_here, person.by) == (True, [])


async def test_what_keeps_a_file_out_is_never_named_to_a_reader_who_cannot_see_it(
    library: Library,  # noqa: F811
) -> None:
    ids = library.ids
    await library.db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (ids["person"],))
    await hide(library.db, "person", ids["person"], library.admin.id)
    named = await refusal.refused_by(library.access, library.db, library.admin, "asset", ids["p1"])
    assert named == []
