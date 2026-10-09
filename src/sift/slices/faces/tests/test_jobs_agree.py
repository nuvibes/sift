# SPDX-License-Identifier: AGPL-3.0-or-later
"""The task a Yes to a person's proposals runs as: it agrees, as the user who pressed, with exactly
the faces the press reached, asks for the re-match when anything changed, says how many, and does
nothing for a press nobody can act for."""

from __future__ import annotations

from typing import Any

from sift.kernel.access import Role, Viewer
from sift.slices.faces import jobs as face_jobs
from sift.slices.faces.jobs_agree import FACE_AGREE, agree
from sift.slices.faces.service_decisions import RunAnswered
from sift.slices.faces.tests.test_jobs import Context


class _Service:
    def __init__(self, *, changed: int = 2, on: bool = True, user: str | None = "u1") -> None:
        self.changed, self.on, self.user = changed, on, user
        self.asked: list[tuple[str, str, list[str]]] = []

    async def enabled(self) -> bool:
        return self.on

    async def viewer_for(self, user_id: str) -> Viewer | None:
        return Viewer(id=user_id, role=Role.ADMIN) if user_id == self.user else None

    async def confirm_look_alikes(
        self, viewer: Viewer, person_id: str, *, only: list[str]
    ) -> RunAnswered:
        self.asked.append((viewer.id, person_id, list(only)))
        return RunAnswered(changed=self.changed)


def _pressed(payload: dict[str, Any], by: str | None = "u1") -> Context:
    context = Context(payload=payload)
    context.pressed_by = by  # type: ignore[attr-defined]
    return context


async def test_the_task_agrees_with_the_faces_the_press_reached_and_asks_for_the_rematch() -> None:
    service = _Service()
    context = _pressed({"person_id": "p1", "track_ids": ["t1", "t2"]})

    await agree(context, service=service, ask=face_jobs.ask_for_rematching)  # type: ignore[arg-type]

    assert service.asked == [("u1", "p1", ["t1", "t2"])]
    assert context.queue.types == [face_jobs.FACE_REMATCH]
    assert (context.progress, context.note) == (1.0, "Agreed with 2 faces.")


async def test_a_task_that_changed_nothing_asks_for_no_rematch() -> None:
    service = _Service(changed=1)
    context = _pressed({"person_id": "p1", "track_ids": ["t1"]})
    await agree(context, service=service, ask=face_jobs.ask_for_rematching)  # type: ignore[arg-type]
    assert context.note == "Agreed with 1 face."

    idle = _Service(changed=0)
    context = _pressed({"person_id": "p1", "track_ids": ["t1"]})
    await agree(context, service=idle, ask=face_jobs.ask_for_rematching)  # type: ignore[arg-type]
    assert context.queue.types == []


async def test_nothing_is_agreed_for_nobody_or_with_faces_switched_off() -> None:
    for service, context in (
        (_Service(user="someone-else"), _pressed({"person_id": "p1", "track_ids": ["t1"]})),
        (_Service(), _pressed({"person_id": "p1"})),
        (_Service(), _pressed({"track_ids": ["t1"]}, by=None)),
        (_Service(on=False), _pressed({"person_id": "p1", "track_ids": ["t1"]})),
    ):
        await agree(context, service=service, ask=face_jobs.ask_for_rematching)  # type: ignore[arg-type]
        assert service.asked == []
        assert context.queue.types == []


def test_the_task_is_named_as_the_task_queue_says_it() -> None:
    assert FACE_AGREE == "face_agree"
