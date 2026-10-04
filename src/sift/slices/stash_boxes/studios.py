# SPDX-License-Identifier: AGPL-3.0-or-later
"""The studios a stash-box made Sites of that may be one creator's username: asked, and answered.

The rule itself is the kernel's (`kernel.access.creator_studios`), because the catalog's own step
applies it to a library once and the writer that files a box's answers reads what it decided.
What is here is the half a person takes part in: the Sites whose signs disagree or are thin, put
as a question on the stash-box page, and the two answers. Yes moves the Site's files to her
username exactly as the repair does; No keeps it a Site and is remembered, so it is not asked
again. Each answer is one History line with an Undo (`StudioQueue.reverse`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.access.catalog import by_user
from sift.kernel.access.creator_studios import (
    Signs,
    Turned,
    Verdict,
    answered,
    box_sites,
    keep_as_site,
    put_back,
    signs_of,
    turn_into_username,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StudioQuestion:
    """A Site a stash-box made that may be one creator's username, with the signs behind asking."""

    site_id: str
    name: str
    files: int
    scenes: int
    credited: int
    others: int
    #: Where her files would go: the Site, her name there, and whether it is a store page.
    site: str
    handle: str
    store: bool


def _question(signs: Signs) -> StudioQuestion | None:
    home = signs.reading.home
    if signs.reading.verdict is not Verdict.ASK or home is None or not signs.files:
        return None
    return StudioQuestion(
        site_id=signs.site_id,
        name=signs.name,
        files=signs.files,
        scenes=signs.reading.scenes,
        credited=signs.reading.credited,
        others=signs.reading.others,
        site=home.site,
        handle=home.handle,
        store=signs.reading.store,
    )


class CreatorStudios:
    """The question about a studio, and its two answers, over the library's own database."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def questions(self) -> list[StudioQuestion]:
        """Every Site a stash-box made whose signs are worth a question nobody has answered yet.

        On one read connection, so the several tables the rule reads per Site agree with each
        other. Sites a box made are tens at most, and their kept answers a few hundred rows.
        """
        out: list[StudioQuestion] = []
        async with self._db.read() as connection:
            for signs in await box_sites(connection):
                question = _question(signs)
                if question is not None and not await answered(connection, signs.name):
                    out.append(question)
        return out

    async def turn(self, site_id: str, user_id: str) -> Turned | None:
        """Yes: this Site is her username. None where it is not a question (or not there)."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            signs = await signs_of(connection, site_id)
            if signs is None or _question(signs) is None or await answered(connection, signs.name):
                return None
            turned = await turn_into_username(
                connection, signs, made=by_user(user_id), actor=Actor.user(user_id)
            )
        # A question has a home and files the box filed there, which is all the move needs.
        if turned is not None:  # pragma: no branch
            log.info("creator_studios.turned", files=turned.files, site_gone=turned.site_gone)
        return turned

    async def keep(self, site_id: str, user_id: str) -> str | None:
        """No: this Site is a Site. The receipt's id, or None where it is not a question."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            signs = await signs_of(connection, site_id)
            if signs is None or _question(signs) is None or await answered(connection, signs.name):
                return None
            return await keep_as_site(connection, signs, actor=Actor.user(user_id))

    async def put_back(self, payload: str) -> bool:
        """Undo one answer (the repair's or a person's), from what its receipt wrote down."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return await put_back(connection, payload)
