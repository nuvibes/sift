# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tag's record saved and a tag's cover chosen, read back off the record.

The record is replaced whole and the aliases are cleared first, so what a save took away leaves
nothing behind but its event; the cover column keeps no history of what it held.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

# For its side effect: registering the table the ledger is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history_events import events_of_asset, events_of_entity
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.tags_ratings.service import TagService
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(temp_db: Database, access: Repository) -> TagService:
    return TagService(temp_db, access)


async def _a_tag(temp_db: Database, name: str = "poolside") -> str:
    tag = new_id()
    await temp_db.execute("INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag, name))
    return tag


async def test_a_tags_record_saved_says_what_it_ended_up_with(
    service: TagService, temp_db: Database, actors: Actors
) -> None:
    tag = await _a_tag(temp_db)

    await service.set_record(
        tag,
        description="Shot by a pool",
        category="setting",
        aliases=["poolsides"],
        parent=None,
        actor=Actor.user(actors.admin.id),
    )

    (event,) = await events_of_entity(temp_db, actors.admin, "tag", tag)
    assert event.verb == "edited"
    assert "poolsides" in event.payload


async def test_a_tags_cover_is_on_the_tag_and_on_the_picture(
    service: TagService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    tag = await _a_tag(temp_db)

    assert await service.set_cover(tag, world.solo, actor=Actor.user(actors.admin.id))

    (on_the_tag,) = await events_of_entity(temp_db, actors.admin, "tag", tag)
    assert on_the_tag.object is not None and on_the_tag.object.id == world.solo
    assert [one.verb for one in await events_of_asset(temp_db, actors.admin, world.solo)] == [
        "edited"
    ]


async def test_a_cover_chosen_for_a_tag_that_is_gone_answers_false_and_records_nothing(
    service: TagService, temp_db: Database, world: World, actors: Actors
) -> None:
    """Deleted between the screen drawing it and the choice landing: nothing was drawn as
    anything, so there is no act to write down."""
    gone = new_id()

    assert not await service.set_cover(gone, world.solo, actor=Actor.user(actors.admin.id))

    assert await events_of_entity(temp_db, actors.admin, "tag", gone) == []
    assert await events_of_asset(temp_db, actors.admin, world.solo) == []


async def test_deleting_a_tag_that_is_not_there_answers_false_and_records_nothing(
    service: TagService, temp_db: Database, actors: Actors
) -> None:
    gone = new_id()

    assert not await service.delete(gone, actor=Actor.user(actors.admin.id))

    assert await events_of_entity(temp_db, actors.admin, "tag", gone) == []


async def test_taking_off_a_tag_that_is_not_there_writes_nothing_and_records_nothing(
    service: TagService, temp_db: Database, world: World, actors: Actors
) -> None:
    """A tag deleted while a selection was being untagged: the join row went with it, so there is
    nothing to take off and no act to write down beside the one that is."""
    tag = await _a_tag(temp_db)
    await service.assign([world.solo], [tag], add=True, actor=Actor.user(actors.admin.id))

    taken = await service.assign(
        [world.solo], [tag, new_id()], add=False, actor=Actor.user(actors.admin.id)
    )

    assert taken == 1
    verbs = {one.verb for one in await events_of_asset(temp_db, actors.admin, world.solo)}
    assert verbs == {"linked", "unlinked"}


def test_a_tag_written_by_a_pass_of_sifts_tells_nobody_extra() -> None:
    """The writer always hears of its own write; a pass of Sift's has no user to tell, so the
    audience is what the grants said and nothing more."""
    from sift.kernel.audience import NOBODY, Audience
    from sift.slices.tags_ratings.service import _and_the_actor

    assert _and_the_actor(NOBODY, Actor.user("acct-1")).users == frozenset({"acct-1"})
    assert _and_the_actor(NOBODY, Actor.sift("folder")) == NOBODY
    told = Audience.of_user("acct-2")
    assert _and_the_actor(told, Actor.sift("folder")) == told


# --- what somebody THOUGHT of a tag -------------------------------------------------------------
#
# Not the ledger: a tag hearted is one user's taste and the feed every admin reads is not where
# that belongs. It goes in `opinions`, carrying the value the upsert is about to destroy, and the
# hide is the one with most to lose, because bringing a tag back clears `hidden_at` along with the
# fact that it was ever hidden.


async def _thought(database: Database, subject_id: str) -> list[tuple[str, str, object, object]]:
    """Every opinion about one thing, oldest first. `ORDER BY id` because the id is a ULID and a
    machine's wall clock can step backwards."""
    rows = await database.fetch_all(
        "SELECT subject_kind, kind, before, after FROM opinions WHERE subject_id = ? ORDER BY id",
        (subject_id,),
    )
    return [
        (str(row["subject_kind"]), str(row["kind"]), row["before"], row["after"]) for row in rows
    ]


async def test_hiding_a_tag_and_bringing_it_back_both_leave_a_row(
    service: TagService, temp_db: Database, actors: Actors
) -> None:
    tag = await _a_tag(temp_db)

    assert await service.set_vault(actors.admin, tag, vault=True) is not None
    # A hidden tag is known only with Hidden open, so it is brought back from there.
    unlocked = replace(actors.admin, show_hidden=True)
    assert await service.set_vault(unlocked, tag, vault=False) is not None

    assert await _thought(temp_db, tag) == [
        ("tag", "hide", None, 1),
        ("tag", "hide", 1, 0),
    ]


async def test_hearting_a_tag_says_what_the_heart_was(
    service: TagService, temp_db: Database, actors: Actors
) -> None:
    tag = await _a_tag(temp_db)

    await service.set_favorite(actors.admin, tag, favorite=True)
    await service.set_favorite(actors.admin, tag, favorite=False)

    assert await _thought(temp_db, tag) == [
        ("tag", "favorite", None, 1),
        ("tag", "favorite", 1, 0),
    ]


async def test_rating_a_tag_says_what_the_stars_were(
    service: TagService, temp_db: Database, actors: Actors
) -> None:
    tag = await _a_tag(temp_db)

    await service.set_rating(actors.admin, tag, rating=5)
    await service.set_rating(actors.admin, tag, rating=2)

    assert await _thought(temp_db, tag) == [
        ("tag", "rating", None, 5),
        ("tag", "rating", 5, 2),
    ]


async def test_a_parent_made_by_another_save_in_the_same_instant_is_the_one_filed_under(
    service: TagService, temp_db: Database, actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two saves name one new parent together: the second to write finds the name taken and files
    its tag under the tag the first made, rather than failing or making a second one."""
    child = await _a_tag(temp_db)
    theirs = new_id()
    making = service.create

    async def _beaten_to_it(name: str, **made: object) -> object:
        await temp_db.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (theirs, name)
        )
        return await making(name, **made)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "create", _beaten_to_it)

    await service.set_record(
        child,
        description=None,
        category=None,
        aliases=[],
        parent="seaside",
        actor=Actor.user(actors.admin.id),
    )

    row = await temp_db.fetch_one("SELECT parent_id FROM tags WHERE id = ?", (child,))
    assert row is not None and row["parent_id"] == theirs
    assert len(await temp_db.fetch_all("SELECT id FROM tags WHERE name = 'seaside'")) == 1
