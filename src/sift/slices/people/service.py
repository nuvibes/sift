# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writes for the three entities, and the reads that are not permission-scoped.

The service is put together from one mixin per thing it changes (`service_base` and its
neighbours); this module is its door, and every name a caller imports from it is re-exported here.
"""

from __future__ import annotations

from sift.kernel.wiring import Part
from sift.slices.people.service_base import (
    PROMOTED,
    Alias,
    Attribution,
    DuplicateAlias,
    Filing,
    JoinReceipt,
    Link,
    Person,
    Site,
    SiteLoop,
    Username,
    UsernameFacts,
    _and_the_actor,
    _own_address_first,
)
from sift.slices.people.service_files import FilesMixin
from sift.slices.people.service_merges import MergesMixin
from sift.slices.people.service_people import PersonRecordMixin
from sift.slices.people.service_sites import SiteRecordMixin
from sift.slices.people.service_usernames import UsernamesMixin


class PeopleService(PersonRecordMixin, SiteRecordMixin, MergesMixin, FilesMixin, UsernamesMixin):
    """Everything the People, Usernames and Sites screens write."""


#: People and their aliases.
SERVICE: Part[PeopleService] = Part("people")


__all__ = [
    "PROMOTED",
    "Alias",
    "Attribution",
    "DuplicateAlias",
    "Filing",
    "JoinReceipt",
    "Link",
    "Person",
    "Site",
    "SiteLoop",
    "Username",
    "UsernameFacts",
    "_and_the_actor",
    "_own_address_first",
]
