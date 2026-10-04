# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the people endpoints send and accept.

Three entities, never interchangeable: a Site is a site, a Username one name on one site, a
Person a human who may hold usernames on several sites and appear in media posted by others.
"""

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

#: How many ids one assign call may carry, the tag assignment's bound: an unbounded drag is a
#: request that writes for as long as it likes.
MAX_ASSIGN = 500

#: How many other names one entity may carry: far above any real one, bounding the writes one
#: transaction may be asked for.
MAX_ALIASES = 50


def _named(what: str) -> Callable[[str], str]:
    """A validator for one named field, saying which field it is when it refuses.

    Pydantic's `min_length` counts before stripping, so a single space would land an invisible row;
    checked after stripping, by the kernel's rule shared with the search box.
    """

    def validate(value: str) -> str:
        return clean_name(value, what=what)

    return validate


class PersonView(Wire):
    id: str
    name: str
    vault: bool = False
    notes: str | None = None
    cover_asset_id: str | None = None
    #: An UPLOADED cover, never beside `cover_asset_id` (one statement writes both). It tells a
    #: screen there is a cover to draw at all, and is folded into the address so a replaced upload
    #: is fetched again.
    cover_upload_id: str | None = None
    #: Which moment of the file, for a chosen video frame; withheld with it. Folded into the
    #: cover's address so the browser keeps the picture (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None = None
    #: The window of the picture it is drawn as, or None for all of it; withheld with the file
    #: (`kernel/cover_frame.py CoverFrame`).
    cover_frame: CoverFrame | None = None
    #: When their cover is a FACE from that file (somebody who exists because a face was named is
    #: shown as it). Never without `cover_asset_id`, and withheld with it.
    cover_track_id: str | None = None
    #: The token on the face-cover address above, which a browser may keep: without it a hidden
    #: person's face would still be shown from the store. Says how often this user's visibility
    #: changed, never anything about the picture.
    art: str | None = None
    asset_count: int = 0
    #: Bytes of the files counted beside it, for this viewer (the shut vault adds nothing). None
    #: where a reply does not say, which a screen keeps rather than reads as nothing.
    size_bytes: int | None = None
    #: Whether a pass put this person on this file, rather than a person; per-asset list only.
    automatic: bool = False
    #: Which pass did: `folder`, `username` or `stash_box`, three different amounts of evidence;
    #: None when somebody did it. `automatic` is what decides whether to mark the name at all.
    source: str | None = None
    #: Which one, by name: the box whose applied match on THIS file put the name here (never "a
    #: stash-box"), the username that resolved to this person, or "its folder" (the row does not
    #: record which). Per-asset list only, beside `source`.
    source_name: str | None = None
    #: This viewer's own heart and stars: two users hold their own opinions, as about a file.
    favorite: bool = False
    rating: int | None = None
    #: This viewer's own O tally over this person's files they may see. Filled only on the person's
    #: own page: a sum per card would cost sixty sums for a number no card draws.
    o_count: int = 0
    #: Kept at the top of the wall by whoever is asking: where it sits, not an opinion.
    pinned: bool = False
    #: The card's counts beside the name, keyed by the tab each opens (`photo_sets`, `tags`,
    #: `sites`, `collections`, `people`), scoped as that tab's wall is. Wall listings only; an
    #: absent key is a cell the card does not draw.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Whether they MAKE the edits rather than appear in them. Also a key in `record`, which only
    #: one-person routes fill, so the wall's card mark reads this; both come off one column in one
    #: statement and cannot disagree.
    pmv_creator: bool = False
    #: Whether anybody has been given this, or refused it: told only to an admin, who makes grants.
    #: A person inherits from nothing, so the mark is always a decision on this row.
    shared: bool = False
    restricted: bool = False
    #: A locked tile on a wall: everything this viewer may see under it is in the shut vault with
    #: placeholders on, so the name comes back empty and the counts stay. Never on a read by id. The
    #: rule is `_LOCKED_TILE` in `kernel/access/repository/entities.py`.
    locked: bool = False
    #: Faces a pack was holding under this name, handed over: set only on the reply to creating
    #: somebody, as news about what just happened.
    faces_claimed: int = 0
    #: Whether this may never be sent outside the machine: the row's own switch. On the card,
    #: because a mark is drawn on sixty cards and cannot be a request each, and on every write
    #: reply, since a screen swaps the reply in for the row it held. Not what a menu reads for its
    #: switch, which for a file is this row OR anything it is filed under
    #: (`EnrichmentState.refused`).
    keep_local: bool = False
    #: Marked "Don't swap": kept out of every swap with another Sift. On the card and every write
    #: reply, for the reason `keep_local` is.
    keep_from_swaps: bool = False
    #: Everything on their record by field key, on one-person routes and edit replies only.
    #: Visible to a guest: a record is what somebody IS. `notes` is not in it, having its own
    #: field and route, so widening this cannot leak them.
    record: dict[str, Any] | None = None


class PeopleList(Wire):
    """One page of the People wall, and how many there are for whoever asked.

    The total comes from the statement the rows came from, so a pager cannot disagree with its page.
    """

    items: list[PersonView]
    total: int
    limit: int
    offset: int


class PersonWrite(Wire):
    """A person, as a screen sends one.

    `vault` and `notes` default to "not sent" and the routes tell the difference: the list screens
    build from carries no notes and a rename form no vault flag, so treating silence as a value
    would erase notes or unvault somebody as a side effect.
    """

    name: str = Field(max_length=MAX_NAME)
    vault: bool = False
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    #: The whole record by field key, or absent to leave it alone (a rename form knows nothing of a
    #: birthdate). An empty mapping is a real message: the form saved empty, and it clears them.
    record: dict[str, Any] | None = None
    #: Every other name they go by, replaced whole, or absent to leave them; on the name's write so
    #: a refused entry refuses the whole save.
    aliases: list[str] | None = Field(default=None, max_length=MAX_ALIASES)
    #: Every address they can be found at, replaced whole, or absent to leave them alone.
    links: list[str] | None = Field(default=None, max_length=MAX_ALIASES)

    _clean_name = field_validator("name")(_named("a person's name"))

    @field_validator("aliases")
    @classmethod
    def _each_alias(cls, value: list[str] | None) -> list[str] | None:
        """Each checked as the one-at-a-time alias route checks it; a blank entry is no entry."""
        if value is None:
            return None
        kept = [clean_name(one, what="an alias") for one in value if one.strip()]
        if any(len(one) > MAX_NAME for one in kept):
            raise ValueError(f"an alias can be at most {MAX_NAME} characters")
        return kept

    @field_validator("links")
    @classmethod
    def _each_link(cls, value: list[str] | None) -> list[str] | None:
        """Each checked as the one-at-a-time link route checks it; a blank entry is no entry."""
        if value is None:
            return None
        kept = [web_address(one) for one in value if clean_stored_text(one).strip()]
        if any(len(one) > MAX_URL for one in kept):
            raise ValueError(f"a link can be at most {MAX_URL} characters")
        return kept


class PersonNotes(Wire):
    """Free text an admin wrote about somebody, on its own.

    Its own reply from its own route: the card is read by every wall, suggester and guest, and notes
    are the most identifying thing after a name, so widening the card cannot leak them.
    """

    notes: str | None = None


class AliasView(Wire):
    id: str
    person_id: str
    alias: str


class AliasWrite(Wire):
    alias: str = Field(max_length=MAX_NAME)

    _clean_alias = field_validator("alias")(_named("an alias"))


class LinkView(Wire):
    """Somewhere a person can be found, as a screen reads it."""

    id: str
    person_id: str
    url: str
    site_id: str | None = None
    #: The site's name where Sift recognizes the address, shown instead of the URL; else null.
    site_name: str | None = None
    label: str | None = None


class LinkWrite(Wire):
    """A link being added.

    Checked for shape and scheme, then stored as written: normalising would decide that two
    spellings are one address, and sometimes lose a link. Only http and https, one rule rather than
    a list of dangerous schemes (a `javascript:` link is the oldest trick).
    """

    url: str = Field(min_length=1, max_length=MAX_URL)
    label: str | None = Field(default=None, max_length=MAX_NAME)

    @field_validator("url")
    @classmethod
    def _only_the_web(cls, value: str) -> str:
        return web_address(value)


def web_address(value: str) -> str:
    """One address a person can be found at, cleaned, or a refusal naming what is wrong with it.

    One rule for the one-at-a-time route and the record's whole list, so the two cannot drift.
    """
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
    #: How many PEOPLE this site has media of, as this viewer may see it (not usernames).
    people_count: int = 0
    #: How many files this viewer may see from the site: what a card is drawn with.
    asset_count: int = 0
    #: Bytes of the files counted beside it, for this viewer (the shut vault adds nothing). None
    #: where a reply does not say, which a screen keeps rather than reads as nothing.
    size_bytes: int | None = None
    site_url: str | None = None
    #: This viewer's own O tally over the files this Site reaches (labels included). Filled only on
    #: the Site's own page: a sum per card would cost sixty sums for a number no card draws.
    o_count: int = 0
    notes: str | None = None
    cover_asset_id: str | None = None
    #: An UPLOADED cover, never beside `cover_asset_id` (one statement writes both). It tells a
    #: screen there is a cover to draw at all, and is folded into the address so a replaced upload
    #: is fetched again.
    cover_upload_id: str | None = None
    #: Which moment of the file, for a chosen video frame; withheld with it. Folded into the
    #: cover's address so the browser keeps the picture (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None = None
    #: The window of the picture it is drawn as, or None for all of it; withheld with the file
    #: (`kernel/cover_frame.py CoverFrame`).
    cover_frame: CoverFrame | None = None
    #: The user's token for this row's pictures (`face_version` of the stamp). An uploaded cover is
    #: kept only under an address carrying it, since the stamp moves with what this user may see.
    art: str | None = None
    #: The shipped icon pack's logo for this site, or None. A TOKEN (`site_icons.token_of`), not a
    #: flag, because the address must NAME the logo for the browser to keep it: folded in after
    #: `art`, and kept a week only under exactly that address (`covers.names_the_shipped`), so a new
    #: pack or a rename onto another entry is fetched again.
    icon: str | None = None
    #: The site's record (other names, its network) by field key, on one-site routes only.
    record: dict[str, Any] | None = None
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of the wall by whoever is asking: where it sits, not an opinion.
    pinned: bool = False
    #: The card's counts beside the name, keyed by the tab each opens (`photo_sets`, `tags`,
    #: `sites`, `collections`, `people`), scoped as that tab's wall is. Wall listings only; an
    #: absent key is a cell the card does not draw.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Whether anybody has been given this, or refused it, told only to an admin, counting every
    #: network above it: a share on a network reaches its labels (`SITE_REACH`).
    shared: bool = False
    restricted: bool = False
    #: Whether that decision was made on THIS Site rather than a network above (solid or hollow
    #: mark, as a file's). See `_SITE_MARKS` in `kernel/access/repository/store.py`.
    shared_here: bool = False
    restricted_here: bool = False
    #: A locked tile on a wall: everything this viewer may see under it is in the shut vault with
    #: placeholders on, so the name comes back empty and the counts stay. Never on a read by id. The
    #: rule is `_LOCKED_TILE` in `kernel/access/repository/entities.py`.
    locked: bool = False
    #: In the vault and listed anyway (the vault is open), so a card offers to take it back out.
    hidden: bool = False
    #: Whether this may never be sent outside the machine. See `PersonView.keep_local`.
    keep_local: bool = False
    #: Marked "Don't swap". See `PersonView.keep_from_swaps`.
    keep_from_swaps: bool = False


class SiteList(Wire):
    """One page of the Sites wall, and how many there are for whoever asked.

    The total comes from the statement the rows came from, so a pager cannot disagree with its page.
    """

    items: list[SiteView]
    total: int
    limit: int
    offset: int


class VaultWrite(Wire):
    """Put a site in the vault, or take it back out.

    Answered with no body: describing what was just concealed would contradict it.
    """

    vault: bool


class UsernameView(Wire):
    """One username on one site, as the lists that show it read it.

    A username has NO PAGE: it is drawn under its person and on a site's People tab, and a press
    opens its person or Browse narrowed to it, so the count and the site's logo ride on those lists
    (`list_usernames`). One identity on one site, never a second Person; it can outlive whoever was
    behind it. `person_id` null is the ordinary state, what the Organize queue exists to answer.
    """

    id: str
    username: str
    #: How many files this viewer may see under this username.
    asset_count: int = 0
    #: Bytes of the files counted beside it, for this viewer (the shut vault adds nothing). None
    #: where a reply does not say, which a screen keeps rather than reads as nothing.
    size_bytes: int | None = None
    #: How many people already answer to this spelling: several is a judgement, none an offer. Only
    #: the waiting-usernames queue asks; zero elsewhere.
    name_candidates: int = 0
    #: The name the site shows beside the username, when it is not the username itself.
    display_name: str | None = None
    #: The username's own page on the site it is on.
    url: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    #: The Site's own permanent number for this username, on the one-username route and the lists
    #: filtered to one person or one site.
    number: str | None = None
    #: Where that number came from, as a sentence the SERVER says, as the file's History does
    #: (`_number_said`), so the two cannot disagree.
    number_said: str | None = None
    #: Where that number came from as the stored word (`metadata`, `typed`), for a screen to branch
    #: on rather than on wording. See `catalog.NUMBER_VIAS`.
    number_via: str | None = None
    #: The site's shipped logo token (as `SiteView.icon`), so a username wears its site's mark
    #: without a second read.
    site_icon: str | None = None


class UsernamePageView(Wire):
    """A page of usernames, and how many there are for whoever asked."""

    items: list[UsernameView]
    total: int
    #: Where this page begins: where the asked row (`from`) turned out to be, or the page (`near`)
    #: once the row has left the list, which only the server knows.
    offset: int


class UsernameWrite(Wire):
    """The editable half of a username: what it is called, its page, its ID and who it belongs to.

    A field left out is left alone, not blanked: the route asks which fields were sent, or a caller
    sending only a display name would detach the person.
    """

    display_name: str | None = Field(default=None, max_length=MAX_NAME)
    url: str | None = Field(default=None, max_length=MAX_URL)
    person_id: str | None = None
    #: The Site's permanent number for this username (its ID on screen), typed by an admin: digits,
    #: filling only a blank unless `replace_number` is sent, since it is what survives a rename and
    #: a wrong one is permanent and silent. 409 where the row carries another, or NAMING the other
    #: username on the same Site that holds it (`catalog.set_username_number`).
    number: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    #: Replace an existing number rather than fill a blank: its own field so the larger consequence
    #: is asked for on purpose, after the screen asks the person. Ignored without `number`.
    replace_number: bool = False

    @field_validator("display_name")
    @classmethod
    def _blank_name_is_none(cls, value: str | None) -> str | None:
        """A display name cleared in the sheet is None, so the card falls back to the username."""
        if value is None:
            return None
        return value.strip() or None

    @field_validator("url")
    @classmethod
    def _page_is_a_web_address(cls, value: str | None) -> str | None:
        """The username's page is drawn as a link and editable, so only http and https are
        accepted: `javascript:` and `data:` both run as the page. Cleared is None."""
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if not trimmed.lower().startswith(("http://", "https://")):
            raise Refused("A link has to start with http:// or https://.")
        return trimmed


class UsernameMerge(Wire):
    """Say who a username belongs to: somebody who already exists, or somebody new.

    Exactly one of the two: attaching and creating somebody new have different consequences, and a
    field meaning either would make the dangerous one the accident. `as_alias` (on by default)
    keeps the person findable by the username's spelling; off for a stage name nobody would type.
    """

    person_id: str | None = None
    new_person_name: str | None = Field(default=None, max_length=MAX_NAME)
    as_alias: bool = True


class FavoriteWrite(Wire):
    favorite: bool


class RatingWrite(Wire):
    """Stars, or None to clear them.

    Zero is refused, as for an asset: stored it would sort and filter as a real rating.
    """

    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


class EntityStateView(Wire):
    """What the server ended up holding, handed back so an optimistic control can settle."""

    favorite: bool = False
    rating: int | None = None


class EntityTagWrite(Wire):
    tag_id: str
    add: bool = True


class CoverWrite(Wire):
    """The still a person or a site is drawn as, or None to go back to having none."""

    asset_id: str | None = None
    #: Which moment of a video, in milliseconds, or None for the file's own picture; written with
    #: the file in one statement so it cannot outlive it. No ceiling: the running time is unknown
    #: here, and a moment past the end gives the last frame, as a seek past the end does in ffmpeg.
    at_ms: int | None = Field(default=None, ge=0)
    #: The upload that is ALREADY the cover, named only to reframe it; any other upload is refused
    #: (`kernel/covers.py upload_kept_by_put`). Never beside `asset_id`.
    upload_id: str | None = None
    #: The window of the picture it is drawn as, or None for the whole of it. See
    #: `kernel/cover_frame.py CoverFrame` for the bounds, which are checked here, on the way in.
    frame: CoverFrame | None = None


class SiteDetailsWrite(Wire):
    """The fields a site gained so a card could be more than a word.

    The URLs are checked here, not where drawn: more than one place renders them, and `javascript:`
    and `data:` both run as the page. The address is the first of `links` (`sites.SITE_ADDRESS`);
    a `site_url` still sent is ignored like any unknown field.
    """

    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    #: Other names the site goes by: absent leaves them, empty clears them, as a person's record.
    aliases: list[str] | None = Field(default=None, max_length=MAX_ALIASES)
    #: The network this site is part of, by name. Absent leaves it; empty or null clears it.
    parent: str | None = Field(default=None, max_length=MAX_NAME)
    #: Every address the site can be found at, replaced whole; absent leaves the list. The FIRST is
    #: the site's address (`sites.SITE_ADDRESS`), its home put first by the write.
    links: list[str] | None = Field(default=None, max_length=MAX_ALIASES)

    @field_validator("links")
    @classmethod
    def _web_urls(cls, value: list[str] | None) -> list[str] | None:
        """The same rule the single address gets, applied to each of them.

        Checked here for the reason the class gives.
        """
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
    """A Site's whole record in one write: its name, and whichever details the caller sent.

    One body, so every refusal is answered before anything is written and a save lands whole. An
    absent detail is left alone, so a rename alone renames.
    """

    name: str = Field(max_length=MAX_NAME)

    _clean_name = field_validator("name")(_named("a site's name"))


class TagOnEntity(Wire):
    """One tag, as a person or a site carries it.

    Its own model rather than the tags slice's: two fields are not worth coupling two slices.
    """

    id: str
    name: str


class PeopleAssignment(Wire):
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    person_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    add: bool = True


class SiteAssignment(Wire):
    """Which files came from which sites, and whether they are being put on or taken off.

    Both directions: the pickers show a tick, and a tick that cannot be undone lies. The per-file
    `FiledUnder` routes remain where a wrong username (WHO posted it) is corrected.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    site_ids: list[str] = Field(min_length=1, max_length=MAX_ASSIGN)
    #: Put them on, or take them off.
    add: bool = True


class FiledUnder(Wire):
    """One filing: a file, a site, and the username that filing names.

    One row per `asset_usernames` row, not per site: a file can hold the poster's row and a
    poster-unknown row on one site, and one chip for both would remove something nobody was shown.
    `username_id` names exactly what a removal removes. `site_id` can be null (a username can
    outlive its site) and is still listed, so the filing can be taken off. `username` is None for
    the stored empty string meaning "from here, poster unknown", a storage detail kept off the wire.
    """

    #: The `asset_usernames` row's other half, and the id a removal names. Never absent.
    username_id: str
    site_id: str | None = None
    #: The site's name as the Sites wall spells it. None only where the site is gone.
    site: str | None = None
    #: Who posted it, in the site's own spelling. None where the filing names nobody.
    username: str | None = None
    #: Who that username turned out to be, where anybody has said. None is the ordinary answer.
    person_id: str | None = None
    #: How the filing was decided: None by somebody, a word (`stash_box`) by a pass, so a chip can
    #: say where a site came from.
    source: str | None = None
    #: Which box, by name, whose applied match wrote it; None by somebody, or where no applied match
    #: says which (an invented name would be a claim nothing supports).
    source_name: str | None = None
    #: What the SITE's cover address carries to be kept by the browser, spelled as `SiteView` does,
    #: so each filing's chip is not re-checked on every visit (`kernel/covers.py names_its_cover`,
    #: `names_the_shipped`). All None where the site is gone.
    art: str | None = None
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    #: The window of the picture it is drawn as, or None for all of it; withheld with the file
    #: (`kernel/cover_frame.py CoverFrame`).
    cover_frame: CoverFrame | None = None
    icon: str | None = None


class AliasMatchView(Wire):
    """Who a typed term turns out to name.

    `people` is empty when it names nobody, which is what the "is this another name for someone?"
    prompt reads to decide whether to offer itself.
    """

    term: str
    people: list[PersonView]


class MergeNamed(Wire):
    """One thing a merge moves, by name: a username, another name, a link, a Site published under.

    `where` is the second word it is known by where there is one: a username's Site. `whose` is
    the one going it belonged to, by name.
    """

    name: str
    where: str | None = None
    whose: str


class MergeCounted(Wire):
    """How many of something one of the people going holds: their confirmed faces."""

    whose: str
    count: int


class MergeFilled(Wire):
    """A box blank on the one kept that the merge fills: the record's word for it, what lands in
    it, and whose it was. `value` is empty for the cover, which is a picture and not a word."""

    key: str
    label: str
    value: str
    whose: str


class MergeWeighed(Wire):
    """What merging two people would move, counted before anything moves.

    A merge cannot be taken back, so the counts are shown beside the two names BEFORE the press,
    counted rather than estimated.
    """

    from_name: str
    into_name: str
    files: int = 0
    usernames: int = 0
    aliases: int = 0
    links: int = 0
    #: Faces, on a person; zero for a site. One shape for both lets one sheet weigh either.
    faces: int = 0
    #: Labels published under a site, on a site. Always zero for a person, for the reason above.
    children: int = 0
    #: Facts the survivor lacks and the other has, which would otherwise be lost with the row.
    facts: int = 0
    #: The same moves by name, so somebody sees WHICH before a press with no undo. Each list names
    #: at most `merge.NAMED_AT_MOST` while its count stays whole, so a sheet can say how many more.
    usernames_named: list[MergeNamed] = Field(default_factory=list)
    aliases_named: list[MergeNamed] = Field(default_factory=list)
    links_named: list[MergeNamed] = Field(default_factory=list)
    #: Whose confirmed faces move, and how many of each. A person only.
    faces_from: list[MergeCounted] = Field(default_factory=list)
    #: The Sites published under the one going, by name. A site only.
    children_named: list[MergeNamed] = Field(default_factory=list)
    #: Every blank on the one kept that the merge fills, in the record's order. `facts` is how many.
    filled: list[MergeFilled] = Field(default_factory=list)


class MergeSeveral(Wire):
    """Several people folded into one, in a single act.

    `into` survives and `people` go; the survivor may be in both, since the natural thing to send
    is the whole selection.
    """

    into: str = Field(min_length=1, max_length=100)
    people: list[str] = Field(min_length=1, max_length=100)


class MergeSeveralSites(Wire):
    """Several sites folded into one, in a single act.

    Its own model so the wire says `sites`, not `people` holding site ids; the survivor may be in
    both, as above.
    """

    into: str = Field(min_length=1, max_length=100)
    sites: list[str] = Field(min_length=1, max_length=100)
