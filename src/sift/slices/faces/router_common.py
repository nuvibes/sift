# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every faces route shares: the service, the refusals, and a face as a screen reads it."""

from __future__ import annotations

from fastapi import (
    HTTPException,
    Request,
    status,
)

from sift.kernel.log import get_logger
from sift.kernel.wiring import part_of
from sift.slices.faces.models_http import (
    GroupCard,
    RunScope,
    RunWrite,
    SightingView,
)
from sift.slices.faces.service import (
    FACES_SWITCHED_OFF,
    SERVICE,
    FaceService,
    GroupView,
    Sighting,
)

#: One logger for every faces route, under the name the routes log as.
log = get_logger("sift.slices.faces.router")


_NOT_FOUND = "not found"


def _service(request: Request) -> FaceService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _off() -> HTTPException:
    """What a route answers when the feature is switched off.

    409 rather than 404: the feature not being on is a fact about the install and says nothing
    about anybody's library, so there is nothing to conceal by pretending the address is unknown,
    and a screen that got a 404 here would report "no faces in this file", which is a different
    and wrong thing to tell somebody.
    """
    return HTTPException(
        status.HTTP_409_CONFLICT,
        FACES_SWITCHED_OFF,
    )


def _only(body: RunWrite | None) -> list[str] | None:
    """The faces a bulk answer names, or None for the whole tab.

    No body at all is the whole tab, which is what the card on the People Sift can recognize wall
    sends. `RunWrite` has already refused a page or a pick with no faces and a whole tab with some,
    so this only has to read it.
    """
    if body is None or body.scope is RunScope.ALL:
        return None
    return body.track_ids


def _scope(body: RunWrite | None) -> str:
    """Which of the three a bulk answer was, for its log line. No body is the whole tab."""
    return (body.scope if body is not None else RunScope.ALL).value


def _card(sighting: Sighting, art: str | None) -> SightingView:
    """One appearance as a screen reads it, with the token its picture is addressed by.

    The token is the same for every face in a response: it says how many times what this user
    may see has changed, and nothing about the individual crop, because a crop never changes. It is
    passed in rather than read here so that one response cannot hand out two different ones.
    """
    return SightingView(
        art=art,
        track_id=sighting.track_id,
        asset_id=sighting.asset_id,
        started_ms=sighting.started_ms,
        ended_ms=sighting.ended_ms,
        picture_ms=sighting.picture_ms,
        person_id=sighting.person_id,
        person_name=sighting.person_name,
        confidence=sighting.confidence,
        attribution=sighting.attribution,
        teachable=sighting.teachable,
        is_reference=sighting.is_reference,
        locked=sighting.locked,
        pile_id=sighting.pile_id,
        pile_status=sighting.pile_status,
        turned=sighting.turned,
    )


def _group(view: GroupView, art: str | None) -> GroupCard:
    return GroupCard(
        id=view.id,
        status=view.status,
        size=view.size,
        faces=[_card(face, art) for face in view.faces],
    )
