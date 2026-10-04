# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Shoots pass asks the meaning index once up front whether it can answer, and reads the
library only when it can."""

from __future__ import annotations

from typing import Any

import pytest

from sift.slices.shoots.service import ShootService

pytestmark = pytest.mark.unit

#: The floor these fakes use, standing in for the Photo Sets' own `MIN_PICTURES`.
_FLOOR = 10


class _Index:
    """The meaning index, answering whether it holds anything the model in use put there."""

    def __init__(self, *, can_answer: bool) -> None:
        self._can_answer = can_answer
        self.asked = 0

    async def can_answer(self) -> bool:
        self.asked += 1
        return self._can_answer


class _Library:
    """The database, counting the whole-library reads the pass makes through the sweep lane."""

    def __init__(self) -> None:
        self.swept = 0

    async def sweep_all(self, sql: str, params: Any = (), *, what: str = "sweep") -> list[Any]:
        self.swept += 1
        return []


class _Proposals:
    """The store, refusing to be written to. A declined pass writes nothing at all."""

    def __init__(self) -> None:
        self.replaced = 0
        self.dissolved = 0
        self.read_standing = 0
        self.floor: int | None = None

    async def replace_for(self, person_id: str, name: str, groups: Any) -> list[str]:
        self.replaced += 1
        return []

    async def waiting_pictures(self) -> dict[str, list[str]]:
        """What is standing, for the pass's reading of which cards are filed already. Nothing."""
        self.read_standing += 1
        return {}

    async def dissolve_under(self, least: int) -> int:
        """The floor, taken to what is already standing. Counted, so a declined pass can be held
        to writing nothing at all. See `test_a_declined_pass_writes_nothing`."""
        self.dissolved += 1
        self.floor = least
        return 0


class _Settings:
    """The preference the pass reads per run. Off, which is how it ships."""

    async def get_app(self, key: str) -> Any:
        return False


def _pass(*, can_answer: bool) -> tuple[ShootService, _Index, _Library, _Proposals]:
    index = _Index(can_answer=can_answer)
    library = _Library()
    proposals = _Proposals()
    service = ShootService(
        database=library,  # type: ignore[arg-type]
        store=proposals,  # type: ignore[arg-type]
        access=None,  # type: ignore[arg-type]
        semantic=index,  # type: ignore[arg-type]
        photo_sets=None,  # type: ignore[arg-type]
        preferences=_Settings(),  # type: ignore[arg-type]
        recorder=None,  # type: ignore[arg-type]
        least=_FLOOR,
    )
    return service, index, library, proposals


async def test_a_pass_with_nothing_to_compare_against_declines_before_reading_the_library() -> None:
    service, index, library, proposals = _pass(can_answer=False)

    found = await service.find()

    assert (found.creators, found.found, found.filed) == (0, 0, 0)
    assert index.asked == 1, "once for the pass, not once per picture"
    assert library.swept == 0, "the library was never read"
    assert proposals.replaced == 0, "and what an earlier pass proposed was left alone"
    assert proposals.dissolved == 0, "nor was anything standing dropped under the floor"
    assert proposals.read_standing == 0, "nor settled as filed"


async def test_a_pass_with_something_to_compare_against_goes_on() -> None:
    """The other direction, so the decline cannot be a pass that never runs."""
    service, index, library, _proposals = _pass(can_answer=True)

    await service.find()

    assert index.asked == 1
    assert library.swept == 1, "it read which creators have loose pictures"


async def test_a_pass_takes_the_floor_to_what_is_already_standing() -> None:
    """A pass takes the floor it was built with to proposals already standing."""
    service, _index, _library, proposals = _pass(can_answer=True)

    await service.find()

    assert proposals.dissolved == 1
    assert proposals.floor == _FLOOR


async def test_the_plan_reads_what_a_pass_would_and_writes_nothing() -> None:
    """A dry run asks `plan`, which `find` carries out: it reads the same library, and it neither
    proposes nor drops anything standing."""
    service, index, library, proposals = _pass(can_answer=True)

    planned = await service.plan()

    assert planned.can_compare and planned.shoots == 0
    assert (index.asked, library.swept) == (1, 1)
    assert (proposals.replaced, proposals.dissolved) == (0, 0)
    declined = await _pass(can_answer=False)[0].plan()
    assert not declined.can_compare


async def test_the_registered_look_runs_the_pass_and_finishes_the_job(
    clean_handlers: None,
) -> None:
    """The task's press reaches the service through the handler the slice declares."""
    from sift.kernel.jobs.worker_pool import registered_handlers
    from sift.slices.shoots.jobs import SHOOTS_LOOK, register_handlers
    from sift.slices.shoots.service import Proposed

    progress: list[float] = []

    class Finds:
        async def find(self) -> Proposed:
            return Proposed(creators=1, found=2, filed=0)

    class Context:
        async def set_progress(self, done: float) -> None:
            progress.append(done)

    register_handlers(service=Finds())  # type: ignore[arg-type]
    handler = registered_handlers()[SHOOTS_LOOK]

    await handler(Context())  # type: ignore[arg-type]

    assert progress == [1.0]
