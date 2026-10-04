# SPDX-License-Identifier: AGPL-3.0-or-later
"""The service on its own, for an id that names nothing, which the router resolves away but a
second caller would not."""

from __future__ import annotations

import pytest

from sift.kernel.access import Effect, ObjectType, Repository
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.collections.service import CollectionService
from sift.slices.collections.tests.conftest import NEVER_EXISTED

pytestmark = [pytest.mark.integration]


@pytest.fixture
def service(temp_db: Database, access: Repository) -> CollectionService:
    return CollectionService(temp_db, access)


async def test_deleting_a_collection_that_never_existed_says_so(
    service: CollectionService,
) -> None:
    assert await service.delete(NEVER_EXISTED, actor=Actor.sift("folder")) is None


async def test_a_delete_that_found_nothing_still_forgot_the_grants(
    service: CollectionService, temp_db: Database, access: Repository
) -> None:
    """Forget-first, and the order is the point.

    The grants are cleared before the row is looked for, so a failure between the two leaves
    grants naming a collection that still exists, recoverable, rather than a collection gone
    and its grants left pointing at whatever id turns up next.
    """
    await temp_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at, disabled) "
        "VALUES ('u1', 'guest', 'x', 'guest', 0, 0)",
        (),
    )
    await access.grant(ObjectType.COLLECTION, NEVER_EXISTED, "u1", Effect.SHARE)
    assert await access.grants_of("u1") != []

    await service.delete(NEVER_EXISTED, actor=Actor.sift("folder"))

    assert await access.grants_of("u1") == []


async def test_renaming_a_collection_that_never_existed_says_so(
    service: CollectionService,
) -> None:
    assert await service.rename(NEVER_EXISTED, "Anything", actor=Actor.sift("folder")) is None


async def test_setting_a_cover_on_a_collection_that_never_existed_says_so(
    service: CollectionService,
) -> None:
    assert await service.set_cover(NEVER_EXISTED, None, actor=Actor.sift("folder")) is None


def test_a_membership_write_is_announced_to_the_account_that_made_it() -> None:
    """On an install where nothing is shared, `bump_stamps_for_object` names nobody, so the
    writer is told about their own write separately, or the file's History would not move. A pass of
    Sift's has no user to tell."""
    from sift.kernel.audience import NOBODY, Audience
    from sift.slices.collections.service import _and_the_actor

    told = _and_the_actor(NOBODY, Actor.user("acct-1"))
    assert told.users == frozenset({"acct-1"})
    assert _and_the_actor(Audience.of_user("acct-2"), Actor.user("acct-1")).users == (
        frozenset({"acct-1", "acct-2"})
    )
    assert _and_the_actor(NOBODY, Actor.sift("folder")) == NOBODY
