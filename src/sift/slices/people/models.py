# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the people endpoints send and accept: a Site, a Username on one site, a Person."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import Field, field_validator

from sift.kernel.content.user_state import MAX_RATING, MIN_RATING
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.text import clean_name, clean_stored_text
from sift.kernel.wire import Refused, Wire

MAX_NAME = 120
MAX_NOTES = 4000
MAX_URL = 2000

#: Bounds the writes one drag may ask for.
MAX_ASSIGN = 500

MAX_ALIASES = 50


def _named(what: str) -> Callable[[str], str]:
    """A validator for one named field that checks after stripping, so a space is refused."""

    def validate(value: str) -> str:
        return clean_name(value, what=what)

    return validate


class PersonView(Wire):
    id: str
    name: str
    vault: bool = False
    notes: str | None = None
    cover_asset_id: str | None = None
    #: Uploaded, never beside `cover_asset_id`; in the address so a new upload is refetched.
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    #: A face from that file is the cover; never without `cover_asset_id`.
    cover_track_id: str | None = None
    #: Cache token for the face-cover address; it moves with this user's visibility.
    art: str | None = None
    asset_count: int = 0
    #: None where a reply does not say, never zero.
    size_bytes: int | None = None
    #: Per-asset list only.
    automatic: bool = False
    #: `folder`, `username` or `stash_box`; None when somebody did it.
    source: str | None = None
    #: The box, the username, or "its folder" that put the name here.
    source_name: str | None = None
    #: This viewer's own.
    favorite: bool = False
    rating: int | None = None
    #: Filled only on the person's own page.
    o_count: int = 0
    pinned: bool = False
    #: Per-tab counts on wall listings; an absent key is a cell not drawn.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Also in `record`, which only one-person routes fill.
    pmv_creator: bool = False
    #: Admin only.
    shared: bool = False
    restricted: bool = False
    #: Everything this viewer may see under it is in the shut vault (`_LOCKED_TILE`).
    locked: bool = False
    #: Set only on the reply to creating somebody.
    faces_claimed: int = 0
    #: The row's own switch; a menu reads `EnrichmentState.refused` instead.
    keep_local: bool = False
    keep_from_swaps: bool = False
    #: On one-person routes and edit replies only; `notes` is never in it.
    record: dict[str, Any] | None = None


class PeopleList(Wire):
    """One page of the People wall, with a total from the same statement."""

    items: list[PersonView]
    total: int
    limit: int
    offset: int


class PersonWrite(Wire):
    """A person as a screen sends one; unsent `vault` and `notes` are left alone."""

    name: str = Field(max_length=MAX_NAME)
    vault: bool = False
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    #: Absent leaves it alone; an empty mapping clears it.
    record: dict[str, Any] | None = None
    #: Replaced whole, or absent to leave them.
    aliases: list[str] | None = Field(default=None, max_length=MAX_ALIASES)
    links: list[str] | None = Field(default=None, max_length=MAX_ALIASES)

    _clean_name = field_validator("name")(_named("a person's name"))

    @field_validator("aliases")
    @classmethod
    def _each_alias(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        kept = [clean_name(one, what="an alias") for one in value if one.strip()]
        if any(len(one) > MAX_NAME for one in kept):
            raise ValueError(f"an alias can be at most {MAX_NAME} characters")
        return kept

    @field_validator("links")
    @classmethod
    def _each_link(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        kept = [web_address(one) for one in value if clean_stored_text(one).strip()]
        if any(len(one) > MAX_URL for one in kept):
            raise ValueError(f"a link can be at most {MAX_URL} characters")
        return kept


class PersonNotes(Wire):
    """Notes on their own route, so widening the card cannot leak them."""

    notes: str | None = None


class AliasView(Wire):
    id: str
    person_id: str
    alias: str


class AliasWrite(Wire):
    alias: str = Field(max_length=MAX_NAME)

    _clean_alias = field_validator("alias")(_named("an alias"))


class LinkView(Wire):
    id: str
    person_id: str
    url: str
    site_id: str | None = None
    #: Shown instead of the URL where Sift recognizes the site.
    site_name: str | None = None
    label: str | None = None


class LinkWrite(Wire):
    """A link, stored as written; only http and https."""

    url: str = Field(min_length=1, max_length=MAX_URL)
    label: str | None = Field(default=None, max_length=MAX_NAME)

    @field_validator("url")
    @classmethod
    def _only_the_web(cls, value: str) -> str:
        return web_address(value)


def web_address(value: str) -> str:
    """One cleaned web address, or a refusal naming what is wrong with it."""
    cleaned = clean_stored_text(value).strip()
    if not cleaned:
        raise Refused("A link needs an address.")
    lowered = cleaned.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise Refused("A link has to start with http:// or https://.")
    return cleaned


class SiteView(Wire):
    id: str
    name: str
    #: People, not usernames.
    people_count: int = 0
    asset_count: int = 0
    #: None where a reply does not say, never zero.
    size_bytes: int | None = None
    site_url: str | None = None
    #: Filled only on the Site's own page.
    o_count: int = 0
    notes: str | None = None
    cover_asset_id: str | None = None
    #: Uploaded, never beside `cover_asset_id`; in the address so a new upload is refetched.
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    art: str | None = None
    #: The shipped logo's token, so its address names the logo and can be cached.
    icon: str | None = None
    #: On one-site routes only.
    record: dict[str, Any] | None = None
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    #: Per-tab counts on wall listings; an absent key is a cell not drawn.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Admin only; counts networks above it (`SITE_REACH`).
    shared: bool = False
    restricted: bool = False
    #: Decided on this Site rather than a network above.
    shared_here: bool = False
    restricted_here: bool = False
    #: Everything this viewer may see under it is in the shut vault (`_LOCKED_TILE`).
    locked: bool = False
    #: In the open vault, so a card offers to take it out.
    hidden: bool = False
    keep_local: bool = False
    keep_from_swaps: bool = False


class SiteList(Wire):
    """One page of the Sites wall, with a total from the same statement."""

    items: list[SiteView]
    total: int
    limit: int
    offset: int


class VaultWrite(Wire):
    """Put a site in the vault or take it out; no body, which would describe what was hidden."""

    vault: bool


class UsernameView(Wire):
    """One username on one site; it has no page of its own and `person_id` is often null."""

    id: str
    username: str
    asset_count: int = 0
    #: None where a reply does not say, never zero.
    size_bytes: int | None = None
    #: People already answering to this spelling; only the waiting-usernames queue asks.
    name_candidates: int = 0
    display_name: str | None = None
    url: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    #: The Site's permanent number for this username.
    number: str | None = None
    #: Said by the server, so it matches the History (`_number_said`).
    number_said: str | None = None
    #: The stored word, for a screen to branch on (`catalog.NUMBER_VIAS`).
    number_via: str | None = None
    site_icon: str | None = None


class UsernamePageView(Wire):
    items: list[UsernameView]
    total: int
    #: Where this page begins; only the server knows where `from` landed.
    offset: int


class UsernameWrite(Wire):
    """A username's editable fields; one left out is left alone, not blanked."""

    display_name: str | None = Field(default=None, max_length=MAX_NAME)
    url: str | None = Field(default=None, max_length=MAX_URL)
    person_id: str | None = None
    #: Digits; fills only a blank unless `replace_number`, since a wrong one is permanent.
    number: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    replace_number: bool = False

    @field_validator("display_name")
    @classmethod
    def _blank_name_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("url")
    @classmethod
    def _page_is_a_web_address(cls, value: str | None) -> str | None:
        """Only http and https: `javascript:` and `data:` run as the page."""
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if not trimmed.lower().startswith(("http://", "https://")):
            raise Refused("A link has to start with http:// or https://.")
        return trimmed


class UsernameMerge(Wire):
    """Say who a username belongs to: an existing person or a new one, exactly one."""

    person_id: str | None = None
    new_person_name: str | None = Field(default=None, max_length=MAX_NAME)
    as_alias: bool = True


class FavoriteWrite(Wire):
    favorite: bool


class RatingWrite(Wire):
    """Stars, or None to clear them; zero is refused."""

    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


class EntityStateView(Wire):
    favorite: bool = False
    rating: int | None = None


class EntityTagWrite(Wire):
    tag_id: str
    add: bool = True


class CoverWrite(Wire):
    """The still a person or a site is drawn as, or None for none."""

    asset_id: str | None = None
    #: No ceiling: a moment past the end gives the last frame.
    at_ms: int | None = Field(default=None, ge=0)
    #: Only the upload already the cover, to reframe it.
    upload_id: str | None = None
    frame: CoverFrame | None = None


class SiteDetailsWrite(Wire):
    """A site's extra fields; URLs are checked here since several places draw them."""

    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    #: Absent leaves them, empty clears them.
    aliases: list[str] | None = Field(default=None, max_length=MAX_ALIASES)
    parent: str | None = Field(default=None, max_length=MAX_NAME)
    #: Replaced whole; the first is the site's address.
    links: list[str] | None = Field(default=None, max_length=MAX_ALIASES)

    @field_validator("links")
    @classmethod
    def _web_urls(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        kept: list[str] = []
        for one in value:
            trimmed = one.strip()
            if not trimmed:
                continue
            if not trimmed.lower().startswith(("http://", "https://")):
                raise Refused("A link has to start with http:// or https://.")
            kept.append(trimmed)
        return kept


class SiteRecordWrite(SiteDetailsWrite):
    """A Site's whole record in one write, so every refusal comes before anything is written."""

    name: str = Field(max_length=MAX_NAME)

    _clean_name = field_validator("name")(_named("a site's name"))


class TagOnEntity(Wire):
    """One tag as a person or a site carries it."""

    id: str
    name: str


class PeopleAssignment(Wire):
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    person_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    add: bool = True


class SiteAssignment(Wire):
    """Which files came from which sites, put on or taken off."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    site_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    add: bool = True


class FiledUnder(Wire):
    """One filing: a file, a site and its username; `username` None means poster unknown."""

    #: The id a removal names.
    username_id: str
    site_id: str | None = None
    site: str | None = None
    username: str | None = None
    person_id: str | None = None
    #: None when somebody decided it.
    source: str | None = None
    #: None unless an applied match says which.
    source_name: str | None = None
    #: The site's cover cache fields, as `SiteView`; None where the site is gone.
    art: str | None = None
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    icon: str | None = None


class AliasMatchView(Wire):
    """Who a typed term names; `people` is empty when nobody."""

    term: str
    people: list[PersonView]


class MergeNamed(Wire):
    """One thing a merge moves, by name; `where` is its Site, `whose` its owner."""

    name: str
    where: str | None = None
    whose: str


class MergeCounted(Wire):
    whose: str
    count: int


class MergeFilled(Wire):
    """A blank on the one kept that the merge fills; `value` is empty for the cover."""

    key: str
    label: str
    value: str
    whose: str


class MergeWeighed(Wire):
    """What merging two people would move, counted before anything moves."""

    from_name: str
    into_name: str
    files: int = 0
    usernames: int = 0
    aliases: int = 0
    links: int = 0
    #: Zero for a site; one shape for both.
    faces: int = 0
    children: int = 0
    facts: int = 0
    #: Each list names at most `merge.NAMED_AT_MOST`; the counts stay whole.
    usernames_named: list[MergeNamed] = Field(default_factory=list)
    aliases_named: list[MergeNamed] = Field(default_factory=list)
    links_named: list[MergeNamed] = Field(default_factory=list)
    faces_from: list[MergeCounted] = Field(default_factory=list)
    children_named: list[MergeNamed] = Field(default_factory=list)
    filled: list[MergeFilled] = Field(default_factory=list)


class MergeSeveral(Wire):
    """Several people folded into one; `into` may also be in `people`."""

    into: str = Field(min_length=1, max_length=100)
    people: list[str] = Field(min_length=1, max_length=100)


class MergeSeveralSites(Wire):
    """Several sites folded into one; `into` may also be in `sites`."""

    into: str = Field(min_length=1, max_length=100)
    sites: list[str] = Field(min_length=1, max_length=100)
