# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for People, Usernames and Sites, one module per screen.

The three entities stay three; denied and missing are the same answer; nothing on disk moves. Each
screen's module keeps its own router, and this one takes their routes in a fixed order, because a
route with a literal path (`/people/facets`) has to come before the one with a parameter in the
same place (`/people/{person_id}`).
"""

from __future__ import annotations

from fastapi import APIRouter

from sift.slices.people import (
    router_files,
    router_icons,
    router_marks,
    router_merges,
    router_people,
    router_person_page,
    router_sites,
    router_usernames,
)
from sift.slices.people.router_base import (
    _person_from_suggestion,
    _service,
    _site_from_suggestion,
)
from sift.slices.people.router_people import PeopleNarrowing
from sift.slices.people.router_sites import SitesNarrowing

router = APIRouter(tags=["people"])

for _screen in (
    router_people,
    router_usernames,
    router_person_page,
    router_files,
    router_sites,
    router_marks,
    router_merges,
    router_icons,
):
    router.routes.extend(_screen.router.routes)


__all__ = [
    "PeopleNarrowing",
    "SitesNarrowing",
    "_person_from_suggestion",
    "_service",
    "_site_from_suggestion",
    "router",
]
