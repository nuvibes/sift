# SPDX-License-Identifier: AGPL-3.0-or-later
"""A write to a person, a tag, a collection or a Loop tells every user who could be drawing it.

A guest draws these through whatever they were given, so a write announced to the admins alone
leaves the guest's screen showing the answer from before it. Each write kind is performed here on a
real database with the change bus listening, and `data/live_writes.json` says who must hear it, so
a writer narrowed back to the admins fails by name. A user given nothing is never told.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

# For its side effect: registering the table History is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import changes
from sift.kernel.access import Effect, ObjectType, Repository, Role, by_user, users_that_may_gain
from sift.kernel.audience import Audience
from sift.kernel.changes import About, ChangeBus, Subscription
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.collections.service import CollectionService
from sift.slices.loops.service import Loop, LoopService
from sift.slices.people.service import PeopleService
from sift.slices.tags_ratings.service import TagService
from sift.testing.fixtures import Actors, World, create_user

pytestmark = [pytest.mark.gate, pytest.mark.integration]

RECORD = Path(__file__).resolve().parent / "data" / "live_writes.json"

#: Who hears a write, by the record's word: (an admin, a user given something, given nothing).
REACH = {"admins+shared": (True, True, False)}

Write = Callable[[], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class Bench:
    actors: Actors
    world: World
    people: PeopleService
    tags: TagService
    collections: CollectionService
    loops: LoopService

    @property
    def actor(self) -> Actor:
        return Actor.user(self.actors.admin.id)


class LastWord(ChangeBus):
    """Keeps only a write's last announcement: an earlier one can be read before the write lands,
    as a delete's grants are forgotten, and told to everyone, before its row goes."""

    def publish(self, audience: Audience, about: About) -> None:
        for one in self._everyone():
            one.take(as_admin=True)
        super().publish(audience, about)


@pytest.fixture
def bus() -> Iterator[ChangeBus]:
    listening = LastWord()
    changes.listens(listening)
    changes.resolves_arrivals(users_that_may_gain)
    try:
        yield listening
    finally:
        changes.listens(None)
        changes.resolves_arrivals(None)


@pytest.fixture
def bench(temp_db: Database, access: Repository, actors: Actors, world: World) -> Bench:
    return Bench(
        actors=actors,
        world=world,
        people=PeopleService(temp_db, access),
        tags=TagService(temp_db, access),
        collections=CollectionService(temp_db, access),
        loops=LoopService(temp_db),
    )


# Each returns the write to watch, after writing whatever it needs first.


async def _person_created(b: Bench) -> Write:
    return lambda: b.people.create_person(b.actors.admin, "Elina Sorrel", notes=None)


async def _person(b: Bench) -> str:
    return (await b.people.create_person(b.actors.admin, "Elina Sorrel", notes=None)).id


async def _person_renamed(b: Bench) -> Write:
    one = await _person(b)
    return lambda: b.people.update_person(
        b.actors.admin, one, "Esme Wrenfield", vault=False, notes=None
    )


async def _person_deleted(b: Bench) -> Write:
    one = await _person(b)
    return lambda: b.people.delete_person(b.actors.admin, one)


async def _alias_added(b: Bench) -> Write:
    one = await _person(b)
    return lambda: b.people.add_alias(one, "Halla Nordquist")


async def _alias_ensured(b: Bench) -> Write:
    one = await _person(b)
    return lambda: b.people.ensure_alias(one, "Halla Nordquist")


async def _alias_removed(b: Bench) -> Write:
    one = await _person(b)
    alias = await b.people.add_alias(one, "Halla Nordquist")
    return lambda: b.people.remove_alias(one, alias.id, actor=b.actor)


async def _link_added(b: Bench) -> Write:
    one = await _person(b)
    return lambda: b.people.add_link(one, "https://example.org/pier")


async def _link_removed(b: Bench) -> Write:
    one = await _person(b)
    link = await b.people.add_link(one, "https://example.org/pier")
    assert link is not None
    return lambda: b.people.remove_link(one, link.id, actor=b.actor)


async def _tag(b: Bench, name: str) -> str:
    return (await b.tags.create(name, made=by_user(b.actors.admin.id))).id


async def _tag_created(b: Bench) -> Write:
    return lambda: b.tags.create("dusk", made=by_user(b.actors.admin.id))


async def _tag_renamed(b: Bench) -> Write:
    one = await _tag(b, "pier")
    return lambda: b.tags.update(b.actors.admin, one, "harbour")


async def _tag_record_edited(b: Bench) -> Write:
    one = await _tag(b, "evening")
    return lambda: b.tags.set_record(
        one, description="Low light", category=None, aliases=[], parent=None, actor=b.actor
    )


async def _tag_record_filled(b: Bench) -> Write:
    one = await _tag(b, "morning")
    return lambda: b.tags.merge_enriched_record(
        one, description="Low light", category=None, aliases=["daybreak"]
    )


async def _tag_cover_set(b: Bench) -> Write:
    one = await _tag(b, "tide")
    return lambda: b.tags.set_cover(one, b.world.solo, actor=b.actor)


async def _tag_deleted(b: Bench) -> Write:
    one = await _tag(b, "fog")
    return lambda: b.tags.delete(one, actor=b.actor)


async def _collection(b: Bench) -> str:
    made = await b.collections.create("Shortlist", owner_id=b.actors.admin.id, actor=b.actor)
    return made.id


async def _collection_created(b: Bench) -> Write:
    return lambda: b.collections.create("Shortlist", owner_id=b.actors.admin.id, actor=b.actor)


async def _collection_renamed(b: Bench) -> Write:
    one = await _collection(b)
    return lambda: b.collections.rename(one, "Keepers", actor=b.actor)


async def _collection_tagged(b: Bench) -> Write:
    one = await _collection(b)
    return lambda: b.collections.tag(one, b.world.tag)


async def _collection_cover_set(b: Bench) -> Write:
    one = await _collection(b)
    return lambda: b.collections.set_cover(one, b.world.solo, actor=b.actor)


async def _collection_deleted(b: Bench) -> Write:
    one = await _collection(b)
    return lambda: b.collections.delete(one, actor=b.actor)


async def _loop_at(b: Bench, start_ms: int) -> Loop:
    return await b.loops.create(
        asset_id=b.world.solo,
        start_ms=start_ms,
        end_ms=start_ms + 5_000,
        name=None,
        created_by=b.actors.admin.id,
        duration_ms=None,
    )


async def _loop(b: Bench, start_ms: int = 0) -> str:
    return (await _loop_at(b, start_ms)).id


async def _loop_saved(b: Bench) -> Write:
    return lambda: _loop_at(b, 0)


async def _loop_renamed(b: Bench) -> Write:
    one = await _loop(b)
    return lambda: b.loops.rename(one, "warmup")


async def _loop_tagged(b: Bench) -> Write:
    one = await _loop(b)
    return lambda: b.loops.set_tag(one, b.world.tag, on=True)


async def _loop_superseded(b: Bench) -> Write:
    await _loop(b, 10_000)
    into = await _loop(b, 20_000)
    return lambda: b.loops.supersede(b.world.solo, 10_000, 15_000, into=into)


async def _loop_forgotten(b: Bench) -> Write:
    one = await _loop(b)
    return lambda: b.loops.forget_many([one])


WRITES: dict[str, Callable[[Bench], Awaitable[Write]]] = {
    "person created": _person_created,
    "person renamed": _person_renamed,
    "person deleted": _person_deleted,
    "person alias added": _alias_added,
    "person alias ensured": _alias_ensured,
    "person alias removed": _alias_removed,
    "person link added": _link_added,
    "person link removed": _link_removed,
    "tag created": _tag_created,
    "tag renamed": _tag_renamed,
    "tag record edited": _tag_record_edited,
    "tag record filled": _tag_record_filled,
    "tag cover set": _tag_cover_set,
    "tag deleted": _tag_deleted,
    "collection created": _collection_created,
    "collection renamed": _collection_renamed,
    "collection tagged": _collection_tagged,
    "collection cover set": _collection_cover_set,
    "collection deleted": _collection_deleted,
    "Loop saved": _loop_saved,
    "Loop renamed": _loop_renamed,
    "Loop tagged": _loop_tagged,
    "Loop superseded": _loop_superseded,
    "Loop forgotten": _loop_forgotten,
}


def _record() -> list[dict[str, str]]:
    return list(json.loads(RECORD.read_text(encoding="utf-8"))["writes"])


async def _listeners(
    bus: ChangeBus, temp_db: Database, access: Repository, b: Bench
) -> tuple[Subscription, Subscription, Subscription]:
    """An admin, a guest given one person, and a guest given nothing, each with a connection."""
    await access.grant(ObjectType.PERSON, b.world.person, b.actors.guest.id, Effect.SHARE)
    stranger = await create_user(temp_db, Role.GUEST)
    return (
        bus.subscribe(b.actors.admin.id),
        bus.subscribe(b.actors.guest.id),
        bus.subscribe(stranger.id),
    )


def _heard(about: About, *listening: Subscription) -> tuple[bool, ...]:
    admin, *others = listening
    return (
        about in admin.take(as_admin=True).about,
        *(about in one.take(as_admin=False).about for one in others),
    )


def test_the_record_and_the_writes_name_the_same_kinds() -> None:
    named = [entry["write"] for entry in _record()]

    assert len(named) == len(set(named)), "a write kind is recorded twice"
    assert set(named) == set(WRITES), "the record and the writes performed here disagree"
    assert {entry["reach"] for entry in _record()} <= set(REACH)


async def test_every_write_reaches_whoever_the_record_says(
    bus: ChangeBus, temp_db: Database, access: Repository, bench: Bench
) -> None:
    listening = await _listeners(bus, temp_db, access, bench)
    wrong: list[str] = []
    for entry in _record():
        write = await WRITES[entry["write"]](bench)
        _heard(About.LIBRARY, *listening)
        await write()
        heard = _heard(About(entry["subject"]), *listening)
        if heard != REACH[entry["reach"]]:
            wrong.append(f"{entry['write']}: (admin, given something, given nothing) {heard}")

    assert not wrong, "\nThese writes reach the wrong users:\n  " + "\n  ".join(wrong)


async def test_a_write_that_moved_nothing_tells_nobody(
    bus: ChangeBus, temp_db: Database, access: Repository, bench: Bench
) -> None:
    listening = await _listeners(bus, temp_db, access, bench)
    assert await bench.people.ensure_alias(bench.world.person, "Halla Nordquist") is not None
    _heard(About.LIBRARY, *listening)

    assert await bench.loops.rename("no-such-loop", "warmup") is None
    assert await bench.people.ensure_alias(bench.world.person, "Halla Nordquist") is None

    assert _heard(About.LIBRARY, *listening) == (False, False, False)
