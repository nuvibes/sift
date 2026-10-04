# SPDX-License-Identifier: AGPL-3.0-or-later
"""A workbench with queues a test wrote: the shell lists, counts and routes decisions back, so
stand-ins keep faces and folders out of it. They match the kernel's protocol structurally."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.workbench import ASSET, Band, Preview, Reversal, Summary, Workbench
from sift.slices.workbench.service import WorkbenchService
from sift.slices.workbench.store import Store
from sift.testing.fixtures import create_user


@dataclass
class FakeQueue:
    """One registered queue, answering from values a test set."""

    name: str = "things"
    title: str = "Things waiting"
    decision: str = "Say yes or no to a thing."
    #: The noun phrase after the count on the card, and the same phrase about one of them. A real
    #: queue declares both; this one answers so the shape is complete.
    verb: str = "things to answer"
    verb_one: str = "thing to answer"
    icon: str = "inbox"
    count: int = 0
    #: What kind of pile this is. The board stamps it onto every summary (see `Summary.band`),
    #: so the stand-in declares it here and its survey does not.
    band: Band = Band.DECISION
    #: Which queues share a page with this one. Stamped with the band.
    group: str | None = None
    #: And what the group is called, on the one queue that leads it. Stamped the same way.
    group_title: str | None = None
    #: What the card is for, in one sentence, or None on a record. Stamped the same way.
    purpose: str | None = "Things somebody has to answer."
    preview: tuple[Preview, ...] = ()
    #: Every payload this queue was asked to picture, in order.
    pictured_from: list[str] = field(default_factory=list)
    #: Whether this queue has anything behind it at all. False stands for a feature switched off.
    present: bool = True
    #: Whether a reversal actually puts anything back.
    reverses: bool = True
    #: Whether every decision of this queue is final.
    final: bool = False
    #: What was handed to `reverse`, so a test can assert the decision's own record reached it.
    reversed_with: list[tuple[str, str]] = field(default_factory=list)
    #: Raised by `reverse`, for the case where putting it back goes wrong halfway.
    fails: Exception | None = None
    #: The counts `reverse` answers with, for a decision of many acts; None answers `reverses`.
    counts: Reversal | None = None
    #: Raised by `pictures_of` alone, a different failure from `fails`.
    picture_fault: Exception | None = None

    @property
    def reversible(self) -> bool:
        return not self.final

    async def available(self) -> bool:
        return self.present

    async def survey(self, viewer: Viewer) -> Summary:
        return Summary(
            name=self.name,
            title=self.title,
            decision=self.decision,
            verb=self.verb,
            verb_one=self.verb_one,
            icon=self.icon,
            count=self.count,
            preview=self.preview,
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """What a decision was about, read from its own payload, so the shell must hand each
        decision's payload to the queue that wrote it."""
        self.pictured_from.append(payload)
        if self.picture_fault is not None:
            raise self.picture_fault
        return tuple(Preview(kind=ASSET, id=one) for one in json.loads(payload).get("shows", ()))

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool | Reversal:
        if self.fails is not None:
            raise self.fails
        self.reversed_with.append((receipt_id, payload))
        return self.counts if self.counts is not None else self.reverses


@pytest.fixture
async def store(temp_db: Database) -> Store:
    await temp_db.initialize_schema()
    return Store(temp_db)


@pytest.fixture
def workbench() -> Workbench:
    return Workbench()


@pytest.fixture
def service(store: Store, workbench: Workbench) -> WorkbenchService:
    return WorkbenchService(store=store, workbench=workbench)


@pytest.fixture
async def admin(temp_db: Database, store: Store) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)
