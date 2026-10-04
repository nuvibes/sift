# SPDX-License-Identifier: AGPL-3.0-or-later
"""Renaming a user, read back off the record.

A rename is an act on a user like adding one, turning one off and removing one, and records an
event as they do, so the user list can say who was renamed as well as who was removed. The old
spelling exists nowhere once the row is written, so it is in the event's payload.
"""

from __future__ import annotations

import pytest

# For its side effect: registering the table the ledger is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history_events import events_of_entity
from sift.kernel.db import Database
from sift.slices.auth.service import AuthService
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


async def test_a_rename_says_what_the_account_used_to_be_called(
    service: AuthService, auth_db: Database, access: Repository, actors: Actors
) -> None:
    made = await service.create_guest(actors.admin, "harlowquin", "A-Long-Enough-Password-1")

    await service.rename_user(made.id, "orlafennimore", by=actors.admin)

    events = await events_of_entity(auth_db, actors.admin, "login", made.id)
    renamed = [one for one in events if one.verb == "renamed"]
    assert len(renamed) == 1
    assert '"before": "harlowquin"' in renamed[0].payload
