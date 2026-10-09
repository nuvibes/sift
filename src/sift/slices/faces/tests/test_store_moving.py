# SPDX-License-Identifier: AGPL-3.0-or-later
"""Moving faces between piles by hand, and what is written down about it."""

from __future__ import annotations

import pytest

# For its side effect: registering the table the ledger is written to, so a slice test's
# database has it. Registration happens at import and the fixtures migrate at setup.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import (
    Ingested,
)
from sift.slices.faces.store import Store
from sift.slices.faces.tests.test_store import NO_SUCH_PILE, record

pytestmark = pytest.mark.integration


async def test_moving_no_faces_at_all_is_not_a_move(store: Store) -> None:
    """A call naming nothing is not a call about everything: the same rule setting aside has."""
    assert await store.move_tracks([], None) is None
    assert await store.move_tracks([], NO_SUCH_PILE) is None


async def test_moving_faces_with_no_description_behind_them_makes_no_pile(store: Store) -> None:
    """A hand-made pile's middle is the average of what went into it, and there is nothing to
    average. Refused rather than written with an empty centroid, which would match everything."""
    assert await store.move_tracks(["01HX0000000000000000000098"], None) is None


async def test_moving_faces_into_a_pile_that_is_not_there_is_refused(
    store: Store, clip: Ingested
) -> None:
    """The destination is chosen from a list on screen, and a group can be named or set aside
    between the list being drawn and the press. Nothing is moved rather than a pile invented."""
    tracks = await record(store, clip.asset.id, count=1)

    assert await store.move_tracks(tracks, NO_SUCH_PILE) is None


async def test_moving_faces_into_a_new_pile_marks_it_as_one_somebody_built(
    store: Store, clip: Ingested
) -> None:
    """The split. The flag is the whole of what stops the next grouping pass throwing it away."""
    tracks = await record(store, clip.asset.id, count=2)

    made = await store.move_tracks(tracks, None)

    assert made is not None
    pile = await store.pile_of(made)
    assert pile is not None
    assert pile["by_hand"] == 1
    assert pile["size"] == 2
    assert await store.by_hand_track_ids() == set(tracks)


async def test_moving_faces_into_an_existing_pile_merges_them_into_it(
    store: Store, clip: Ingested
) -> None:
    """The merge, and the same call: a pile id is a destination, no pile id is a new one."""
    tracks = await record(store, clip.asset.id, count=3)
    grouped = await store.replace_piles([(tuple(0.0 for _ in range(8)), tracks[:1])])

    made = await store.move_tracks(tracks[1:], grouped[0])

    assert made == grouped[0]
    pile = await store.pile_of(grouped[0])
    assert pile is not None
    assert pile["by_hand"] == 1, "a pile merged into by hand must survive the next grouping pass"
    assert pile["size"] == 3


async def test_a_pile_split_out_by_hand_survives_the_next_grouping_pass(
    store: Store, clip: Ingested
) -> None:
    """The split, and the protection that makes it stick.

    Re-grouping clears the open piles and writes them again from the arithmetic. A pile somebody
    made by splitting faces out is open too, so without being marked as theirs it is deleted by the
    very next pass and the faces are scattered back to wherever the clustering puts them, silently,
    and with nothing on any screen to say a merge was undone.
    """
    tracks = await record(store, clip.asset.id, count=3, distinct=True)
    made = await store.move_tracks(tracks[:2], None)
    assert made is not None

    pile = await store.pile_of(made)
    assert pile is not None
    assert pile["by_hand"] == 1

    await store.replace_piles([(tuple(0.0 for _ in range(8)), tracks[2:])])

    assert await store.pile_of(made) is not None, "the next pass took a pile somebody had made"


async def test_a_hand_made_grouping_is_written_down_once_per_face(
    store: Store, clip: Ingested
) -> None:
    """Written as descriptions, because a rescan deletes the rows the pile sits on. One row per
    face and not one per frame: an appearance seen six times is still one decision."""
    tracks = await record(store, clip.asset.id, count=2)
    made = await store.move_tracks(tracks, None)
    assert made is not None

    written = await store.remember_grouping(made)

    assert written == 2
    assert len(await store.grouped_for(clip.asset.id)) == 2


async def test_remembering_a_grouping_that_has_gone_writes_nothing(store: Store) -> None:
    """Not an error. The pile can be emptied by a name landing on its last face between the move
    and the write, and there is then nothing to remember."""
    assert await store.remember_grouping(NO_SUCH_PILE) == 0


async def test_putting_faces_back_into_a_hand_made_pile_needs_faces(store: Store) -> None:
    """The other half of the memory: nothing found in this file that belongs to it, so nothing to
    put back. Recreating the empty pile would put a card on the wall holding nothing."""
    await store.group_again(NO_SUCH_PILE, [], b"")

    assert await store.pile_of(NO_SUCH_PILE) is None


async def test_a_hand_made_pile_is_recreated_under_its_own_identity_after_a_rescan(
    store: Store, clip: Ingested
) -> None:
    """The whole point of writing the grouping down. A rescan deletes every track the file had, so
    the pile is tidied away, and the faces have to come back TOGETHER rather than as a heap."""
    tracks = await record(store, clip.asset.id, count=2, distinct=True)
    made = await store.move_tracks(tracks, None)
    assert made is not None
    await store.remember_grouping(made)
    remembered = await store.grouped_for(clip.asset.id)
    assert remembered

    fresh = await record(store, clip.asset.id, count=2, distinct=True)
    # The rescan takes the tracks; the empty row goes in the tidy-up the pipeline runs after it.
    assert await store.drop_empty_piles() == 1
    assert await store.pile_of(made) is None

    await store.group_again(made, fresh, remembered[0][2])

    back = await store.pile_of(made)
    assert back is not None
    assert back["by_hand"] == 1
    assert back["size"] == 2


async def test_forgetting_a_grouping_takes_back_only_those_faces(
    store: Store, clip: Ingested
) -> None:
    """Moving a face out of a pile it was moved into has to clear the old memory, or the move
    works until the next scan of the file and then quietly undoes itself."""
    tracks = await record(store, clip.asset.id, count=2, distinct=True)
    made = await store.move_tracks(tracks, None)
    assert made is not None
    await store.remember_grouping(made)

    gone = await store.forget_grouping(tracks[:1])

    assert gone == 1
    assert len(await store.grouped_for(clip.asset.id)) == 1
