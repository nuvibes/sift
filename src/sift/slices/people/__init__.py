# SPDX-License-Identifier: AGPL-3.0-or-later
"""People, Usernames and Sites: attribution as three things rather than one.

    Site       the site or source          Instagram, TikTok, a studio
    Username   one name on one site        a name on Instagram
    People     the human depicted          a person, across every site they appear on

They are separate because they vary independently. One person holds usernames on five sites; one
username posts several people; one site hosts thousands of usernames. A single "who is this" field
can express none of that, and the information is not recoverable once it has been flattened.

The same name on two sites is **two usernames**, and that is the constraint doing the most work
here: `UNIQUE(site_id, name)` rather than `UNIQUE(name)`. Two strangers picking the same
name is ordinary, and merging them is a mistake that looks like a feature until somebody notices
their library has attributed a stranger's media to someone they know.

A person is findable three ways (their name, an alias somebody typed, or a username linked to
them), and all three go through one resolver in the access layer, which search calls too.
Linking a username is what makes it searchable, with nothing to rebuild.

This slice owns no table. `people`, `usernames`, `sites` and the join tables belong to the
access layer because a grant can name a person or a site and the resolver has to join them;
`people_aliases` is there too, because the resolver reads it for the same reason.
"""

from __future__ import annotations

from sift.slices.people.enrich import PersonWriter, SiteWriter
from sift.slices.people.queue import UsernameQueue
from sift.slices.people.router import router
from sift.slices.people.service import (
    SERVICE,
    Alias,
    DuplicateAlias,
    PeopleService,
    Person,
    Site,
    Username,
)

__all__ = [
    "SERVICE",
    "Alias",
    "DuplicateAlias",
    "PeopleService",
    "Person",
    "PersonWriter",
    "Site",
    "SiteWriter",
    "Username",
    "UsernameQueue",
    "router",
]
