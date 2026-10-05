# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes a scoped read hands back, and the conversions that build them.

Nothing here decides anything. These are the rows of the queries next door, given names and types
so a caller reads `asset.view.rating` rather than indexing a tuple, plus the four functions that
turn one database row into one of them.

They live apart from the statements for a plain reason: a view is what the rest of Sift sees, and a
statement is how it was worked out. Changing a column name touches one file; changing what callers
are handed touches this one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.access.viewer import ConcealerType, Effect, ObjectType, Role, Viewer
from sift.kernel.content import (
    Asset,
    DerivativeKind,
    asset_from_row,
)
from sift.kernel.cover_frame import CoverFrame, frame_of
from sift.kernel.db import Row
from sift.kernel.serving import art_carries, art_version


def _is_object_id(value: object) -> bool:
    """Whether a value can be bound as a single-object lookup.

    The scoped read is written `(:asset_id IS NULL OR a.id = :asset_id)`: passing NULL is how the
    grid asks for *every* row, so a single-object lookup that let NULL through would quietly return
    the first visible row instead of nothing. A route reads its path parameter from an untrusted
    request, and an absent parameter arrives as None, so it is checked before it is ever bound.

    IT CHECKS FOR A NON-EMPTY STRING, not the ALPHABET `new_id` mints from (`is_id`). The danger
    this exists for is NULL, and nothing else. A string that is not an id binds perfectly well and
    matches no row, which is the right answer to a lookup for something that is not there; refusing
    it instead would answer "no such row" for a row that IS there: a person listed on the wall,
    where the list statement asks no such question, answering 404 on their own page.

    An id in a database can come from a restored backup, a build older than this one, an older
    migration that minted lower-case ids (see `_PEOPLE_FROM_HANDLES`), or an import somebody wrote.
    Whether a row exists is a question for the WHERE clause; a read path that declines to look
    because the id is unfamiliar is deciding it already.
    """
    return isinstance(value, str) and value != ""


class AccessError(ValueError):
    """A grant that could never apply to anything."""


@dataclass(frozen=True, slots=True)
class Grant:
    id: str
    object_type: ObjectType
    object_id: str | None
    subject_user_id: str
    effect: Effect
    created_at: int


@dataclass(frozen=True, slots=True)
class GrantMark:
    """Whether anything has been said about one object, without saying to whom.

    What a grid needs to mark a tile. The names belong in the panel: a badge saying "shared" is a
    reminder to look, and a badge listing users would be an access-control screen drawn at 16
    pixels across every tile on the page.

    `shared` and `restricted` are the ANSWER (somebody can reach this, somebody is kept from it),
    however it was arrived at. `shared_here` and `restricted_here` say the decision was made on this
    object itself. Both are worth drawing and they are not the same thing: one is something you did
    and can undo where you are looking, the other is a consequence of something further up. A badge
    that only showed the first would leave every file inside a restricted folder looking untouched.
    """

    shared: bool
    restricted: bool
    shared_here: bool = False
    restricted_here: bool = False


@dataclass(frozen=True, slots=True)
class GrantSource:
    """One grant that reaches an object, and the thing it was actually made on.

    `source_id` is None for the global grant, and equal to the object asked about when the decision
    was made on that object itself, which is what lets a panel say "here" rather than naming the
    thing somebody is already looking at.
    """

    subject_user_id: str
    username: str
    effect: Effect
    source_type: ObjectType
    source_id: str | None
    source_name: str | None


@dataclass(frozen=True, slots=True)
class Reaches:
    """One user, and whether they can see the thing asked about at all.

    The reach report's row. `sees` is READ FROM THE STORED VERDICT rather than worked out (a file
    is one probe of `viewer_assets`, and everything else is whether ONE file under it comes back),
    so it is the same answer the user's own next request will get, and not a second opinion about
    it. Whatever `GrantSource` says beside it is the EXPLANATION of that answer.

    `role` is here because an admin is past every access rule, so a yes about one is not a grant and
    a report drawing it as one would be inventing a decision nobody made. `disabled` for the
    opposite case: the rows are still there for a switched-off user and they cannot make a request
    to use them, so `sees` is already false and this says which of the two reasons it is.
    """

    user_id: str
    username: str
    role: Role
    disabled: bool
    sees: bool


@dataclass(frozen=True, slots=True)
class ReachReason:
    """One reason a page of files under an entity is reachable, and how many of them it explains.

    An entity is visible to a user on a different rule from a file: it is on their wall because
    ONE file under it can be reached, and what let that file through (the folder it sits in,
    another tag it carries) was never said about the entity at all. So `Reaches` can be a true
    yes with nothing in `GrantSource` beside it, and this is what the report names then.

    `files` counts the files in the PAGE this was asked about, not every file under the entity. See
    `ReachThroughFiles.complete`, which is what says whether those are the same number.
    """

    source_type: ObjectType
    source_id: str | None
    source_name: str | None
    files: int


@dataclass(frozen=True, slots=True)
class ReachThroughFiles:
    """The reasons, and how much of the entity was looked at to find them.

    `files` is how many files the page held and `complete` whether that was all of them. Both ride
    with the answer because a count of files under a ceiling is only readable beside the number it
    is out of: "3 of her 12" is a fact and a bare "3" is one that changes meaning with the size
    of the person.
    """

    reasons: tuple[ReachReason, ...]
    files: int
    complete: bool


@dataclass(frozen=True, slots=True)
class VaultSource:
    """One thing whose vault flag is concealing the object asked about.

    The same question as `GrantSource`, asked of the other rule. Hiding is not a grant and does not
    live beside them: it takes a row off the screen of the user who did it, and no share
    overrides it. But it inherits down the same shapes (a file is concealed by a folder above it,
    by the library it is in, by somebody it is attributed to, by a set it belongs to), and "why can
    I not see this" is the same question as "why is this shared", so it is answered the same way.

    `here` is the object asked about concealing itself, which is the one case somebody can undo
    where they are standing.
    """

    source_type: ConcealerType
    source_id: str | None
    source_name: str | None
    here: bool


@dataclass(frozen=True, slots=True)
class Memberships:
    """How many of a set of files carry each person, site, collection, photo set and tag.

    Counts and not verdicts, deliberately. "All of them", "some of them" and "none" is what a
    picker draws, but it is a statement about the SET that was asked about, and the set is the
    caller's, not this layer's. A repository that answered in those three words would be deciding
    how many files the question was about, which is the one thing it does not know: a caller
    splitting a selection of two thousand into chunks would get four separate "all of them" and
    have no way to tell that a tag on every file of chunk one is on none of chunk two.

    So the raw tally comes back (entity id to how many of the asked-for files carry it), and
    whoever asked, who knows how many they asked about, turns it into words. Anything absent is
    zero; a kind nothing was found for is an empty mapping rather than a missing one.
    """

    people: Mapping[str, int]
    sites: Mapping[str, int]
    collections: Mapping[str, int]
    photo_sets: Mapping[str, int]
    tags: Mapping[str, int]
    #: The song each file carries (at most one each), as the same raw tally.
    songs: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Actionable:
    """Which of a selection a viewer may WRITE to, and which two reasons the rest fell to.

    A route asking this one id at a time and stopping at the first refusal would fail a tag going
    onto three files entirely because one of them was in the vault. Asking about the whole list in
    one place gives the route something to say instead of something to abandon: act on `allowed`,
    and report `concealed` and `refused` as the count and the reason the person actually reads.

    The two failures are kept apart because only one of them is the viewer's to fix. `concealed` is
    their own locked vault and a PIN away; `refused` is anything else (a file that is not there, a
    file somebody else owns), and must stay a single undifferentiated pile, because separating
    "not yours" from "not there" is what tells a stranger which ids exist.

    Ordering follows the caller's list rather than the query, so a message naming the first item
    names the one they would name.
    """

    allowed: tuple[str, ...]
    concealed: tuple[str, ...]
    refused: tuple[str, ...]

    @property
    def skipped(self) -> int:
        """How many items the write must leave alone."""
        return len(self.concealed) + len(self.refused)


@dataclass(frozen=True, slots=True)
class PhotoSetView:
    """A photo set, as one viewer may know it.

    Every scoped column here is scoped for the reason the collection beside it gives: `item_count`
    is what this viewer can actually see, and the cover comes back empty unless they may open it,
    because a cover is a picture of an item and publishes it at thumbnail size.

    What a collection has and this does not is an owner. What this has and a collection does not is
    where it came from: a set is DERIVED, from a gallery that was fetched or a folder that was
    read, and that is the whole reason it is not a collection with a flag on it.
    """

    id: str
    name: str
    cover_asset_id: str | None
    #: An uploaded cover. See `PersonSuggestion.cover_upload_id`.
    cover_upload_id: str | None
    #: Which moment of that file, when the cover is a chosen frame of a video. Withheld with
    #: the file. The cover's address carries it, which is what lets the browser keep the picture
    #: (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None
    vault: bool
    created_at: int
    item_count: int
    #: How big those same files are, in bytes, summed off each file's own size: scoped exactly as
    #: the count beside it is, so it is the size of what this viewer may see and nothing the vault
    #: is holding back adds to it while the vault is shut.
    size_bytes: int = 0
    #: How this set came to exist: `manual`, `download` or `folder`. It is what lets a later pass
    #: tell a set it may refresh from one somebody assembled by hand.
    origin: str = "manual"
    origin_url: str | None = None
    folder_id: str | None = None
    notes: str | None = None
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of this wall by this viewer. The same kind of thing as the heart
    #: above it: an opinion, held per user, and never a property of the row itself.
    pinned: bool = False
    #: The window of the cover's picture it is drawn as, or None for the whole of it. Read
    #: through `cover_frame.frame_of`, so it is only ever a window of the picture named above,
    #: withheld with the file. The cover's address carries it (`kernel/covers.py names_its_cover`).
    cover_frame: CoverFrame | None = None
    #: A LOCKED TILE: every file under this row that the viewer may see is in the vault, the vault
    #: is shut and placeholder mode is on, so the wall draws a padlock where the row would be and
    #: the name, the address and the notes came back empty. Only ever True on a wall; a read by id
    #: keeps the name. The rule is `_LOCKED_TILE` in `repository/entities.py`.
    locked: bool = False


@dataclass(frozen=True, slots=True)
class SongView:
    """A song, as one viewer may know it: scoped exactly as a Photo Set is (`PhotoSetView`), with
    the AcoustID recording it is where AcoustID said, in place of where a set came from."""

    id: str
    name: str
    cover_asset_id: str | None
    #: An uploaded cover. See `PersonSuggestion.cover_upload_id`.
    cover_upload_id: str | None
    #: Which moment of that file, when the cover is a chosen frame of a video. Withheld with it.
    cover_at_ms: int | None
    created_at: int
    #: How many files carrying it this viewer may see, and their size; see `PhotoSetView`.
    item_count: int
    size_bytes: int = 0
    #: The AcoustID recording this song is, or None where nothing has said which recording it is
    #: (a name read off a Site's page, or typed). Withheld with the name on a locked tile.
    recording_id: str | None = None
    notes: str | None = None
    favorite: bool = False
    rating: int | None = None
    pinned: bool = False
    cover_frame: CoverFrame | None = None
    #: A LOCKED TILE, as `PhotoSetView.locked`.
    locked: bool = False
    #: Whether THIS viewer hid it, as `PhotoSetView.vault`.
    vault: bool = False
    #: The artists it credits, in order, as (id, name). Withheld with the name on a locked tile.
    artists: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class LoopTag:
    """One tag on one Loop. The same two fields a tag chip is drawn from anywhere else."""

    id: str
    name: str


#: How far apart two lengths may be and still be the same length, in milliseconds.
#:
#: A quarter of a second. It is the drift between what a cut was asked for and what the encoder
#: wrote (a frame or two at any ordinary rate), and it is deliberately the same number as the
#: shortest Loop the loop service will accept, because a difference smaller than the shortest Loop
#: anybody can make is not a difference anybody can see.
_SAME_LENGTH_MS = 250


@dataclass(frozen=True, slots=True)
class LoopView:
    """A Loop: a stretch of one video, as one viewer may know it.

    It carries the source file's own shape because a loop is DRAWN as a still from that file at
    that moment, and the wall has to lay itself out before the picture arrives. Nothing here is
    scoped by a rule of its own: a loop reaches a viewer only through an inner join to the visible
    set, so a loop of a file they may not see is not a row at all.
    """

    id: str
    asset_id: str
    name: str | None
    start_ms: int
    end_ms: int
    created_at: int
    #: Who made it, or None for a Loop whose user has since gone. Only whoever made a Loop, or
    #: an admin, may move or remove it (see the loops router).
    created_by: str | None
    media_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    #: The opinion of the file `asset_id` names, which for a loop cut by Save as Loop is the LOOP
    #: itself, and for a Loop that is a stretch of a longer video is that video. See `whole`.
    #:
    #: Not "the video's heart and stars, not the Loop's": that holds only for a Loop that is a
    #: stretch of a longer video.
    favorite: bool = False
    rating: int | None = None
    #: And that file's pin. The wall floats it above every sort (see `_VISIBLE_LOOPS`), so for a
    #: cut clip this is the clip's own place at the top, and for a stretch of a longer video it is
    #: the source video's, shared by every Loop cut from it.
    pinned: bool = False
    #: How many times this user has opened that file. Drawn on the tile as a tally.
    views: int = 0
    #: How many times this user has pressed the tally beside it, on that same file.
    o_count: int = 0
    #: Whether the vault conceals the file this row points at, however that came about.
    #:
    #: Not the same question as `concealed` below, and this one says `hidden` the way the wire
    #: does. `concealed` asks whether this row may be
    #: drawn as a PICTURE at all; this asks whether the file is in the vault, which stays true and
    #: worth marking once the vault is open and the picture is drawn.
    hidden: bool = False
    #: Whether this user hid the FILE ITSELF, rather than something it belongs to or sits in.
    #: What decides whether the eye is filled (see `sharing-marks` for why hollow and solid mean
    #: what they mean).
    hidden_here: bool = False
    #: Whether NO copy of the file is where Sift last saw it. The tile draws a torn page.
    unreachable: bool = False
    #: The LOOP's own tags: the moment's, not the video's. Empty for a Loop nobody has tagged.
    #:
    #: Carried on the row rather than fetched per tile, for the reason every other fact here is:
    #: a wall of forty Loops would be forty requests, and a tile that cannot draw itself from its
    #: own row is a tile that flickers.
    own_tags: tuple[LoopTag, ...] = ()
    #: Whether the Loop may be drawn as a picture, or only as a locked placeholder. A frame of a
    #: concealed video IS the video, so the vault takes the picture and leaves the row.
    concealed: bool = False
    #: Whether the video's still has been built. A tile with none shimmers rather than requesting a
    #: picture that is not there and reading the 404 as one that failed.
    has_thumb: bool = False
    #: Whether the VIDEO's hover clip has been built. A Loop has no clip of its own on disk (what
    #: a Build makes belongs to the file), so a wall of Loops previews the file, and this is the
    #: fact about the file. Same reason as on an asset: without it the tile finds out by asking and
    #: reading the 404 as an answer.
    has_preview: bool = False
    #: Whether the LOOP's own still has been built: a frame at the moment it begins, rather than
    #: the video's first frame. False falls back to the video's picture rather than shimmering, so
    #: a library part-way through building them looks whole instead of looking broken.
    has_still: bool = False
    #: The name the video was imported under, so a saved copy is named after something.
    original_filename: str | None = None
    #: The token every picture address for the video carries, so a browser may keep them.
    art_version: str | None = None

    @property
    def length_ms(self) -> int:
        """How long it runs. Derived rather than stored: two columns that must agree with a third
        is a third place for them to stop agreeing."""
        return self.end_ms - self.start_ms

    @property
    def whole(self) -> bool:
        """Whether the Loop covers the whole of the file it points at.

        Which is to say: whether this row IS a clip, or is a stretch of something longer. A Loop
        saved by "Save as Loop" is the first (the file was cut to exactly the piece somebody
        chose, so the Loop and the file are the same thing), and a Loop that points into a longer
        video is the second.

        Arithmetic rather than provenance, and that is deliberate. Whether a file was PRODUCED for
        this Loop is a fact in another feature's table, and a row that happens to cover a whole
        video is the same thing to look at and to act on however it got there.

        `_SAME_LENGTH_MS` is there because the two numbers come from different places: the end is
        the length the cut was ASKED for and the duration is what ffprobe measured afterwards, and
        an encoder lands a hair either side of a request without anything being wrong. A file with
        no duration at all reads as not whole, which is the safe way round: it is the answer for a
        video Sift has never probed, and treating an unknown as a match would call every such Loop a
        clip.
        """
        if not self.duration_ms or self.duration_ms <= 0:
            return False
        return (
            self.start_ms <= _SAME_LENGTH_MS and self.end_ms >= self.duration_ms - _SAME_LENGTH_MS
        )


@dataclass(frozen=True, slots=True)
class PhotoSetPage:
    """One page of photo sets, and how many there are for whoever asked."""

    items: list[PhotoSetView]
    total: int


@dataclass(frozen=True, slots=True)
class SongPage:
    """One page of songs, and how many there are for whoever asked."""

    items: list[SongView]
    total: int


@dataclass(frozen=True, slots=True)
class LoopPage:
    """One page of loops, and how many there are for whoever asked."""

    items: list[LoopView]
    total: int


@dataclass(frozen=True, slots=True)
class FacetCount:
    """One value a dimension takes, and how many of the rows on screen carry it.

    A named shape rather than a pair, and the third field is the whole reason:
    a dimension whose value is an ID needs a readable name beside it, and a caller cannot work one
    out afterwards without asking the database a second question, which is a second statement,
    with its own permission rule to forget, answering about a set the first one already decided.

    `count` is scoped and filtered. It counts the same set the wall is showing, produced by the
    same statement, so it is never the number in the library and on a wall that already carries a
    constraint it is never the number outside that constraint. It is not called `files`, because
    the walls that count people and sites have facets too.

    `label` is None for every dimension whose value is already the readable thing: a word, a
    band, a year, a name. It is filled only where the value is an id, and then it is the name on
    the row that id belongs to.
    """

    value: str
    count: int
    label: str | None = None


@dataclass(frozen=True, slots=True)
class Enrichment:
    """One thing that wrote to a file without a person doing it, and what it is called.

    The `via` word is the `enriched:` filter's own (`stash`, `faces`, `folder`), so a mark on a
    file's own screen and the row that counted it in the Browse column are the same fact, read
    through the same predicates.

    `name` is what it is polite to say out loud, and it is filled only for a stash-box: "a
    stash-box" names nothing anybody configured, and an install asking three services wants to know
    WHICH one recognised this file. None for the other two, because Sift itself read the face and
    the folder. A stash-box whose doing is known only from a person, a tag or a filing carrying
    `source = 'stash_box'` is named by the box those rows name (`box_id`), and None where they name
    none: inventing a name for them would be a claim nothing in the library supports.

    One of these PER BOX, so a file two boxes both recognised carries two. That is why the answer
    is a list of these rather than a word with a name hung off it.

    `box` is the word Sift knows that box by (`stashdb`, `fansdb`, `pmvstash`), and it is a
    THIRD thing rather than the name in another spelling: the name is what somebody typed when the
    box was added and the slug is derived from its address, so only the slug can be keyed on. It is
    what the mark takes its colour from and what the `enriched:` filter answers to. None wherever
    the name is, and also for a box at an address Sift has never heard of.
    """

    via: str
    name: str | None = None
    box: str | None = None


@dataclass(frozen=True, slots=True)
class Folder:
    """A folder, as somebody is allowed to see it.

    It carries no file count. Ask `folder_file_count` for one, which is the only thing that counts
    files and is asked one folder at a time, on the one screen that needs a number.
    """

    id: str
    root_id: str
    parent_id: str | None
    rel_path: str
    name: str
    vault: bool
    concealed: bool


@dataclass(frozen=True, slots=True)
class FolderContents:
    """What is under a folder, counted in one pass.

    Bytes over COPIES rather than over files: a file with two copies under this folder occupies the
    bytes twice, and "size on disk" is the honest reading of that. `folders` does not count the
    folder itself.
    """

    files: int
    bytes: int
    folders: int
    #: When the most recently added file under it arrived, or None where there are none. Not a
    #: creation date (the folder rows carry none), and the difference is worth keeping in mind:
    #: this is "the folder something arrived in most recently", which is the useful reading for a
    #: media library and the only one the database can answer.
    newest_at: int | None


@dataclass(frozen=True, slots=True)
class AssetView:
    """An asset, and whether it is being shown as a locked placeholder.

    `concealed` is only ever True for a viewer whose concealment mode keeps the tile. In the
    default mode a concealed asset does not come back at all, so this flag is False and the row
    is simply absent.
    """

    asset: Asset
    concealed: bool
    #: Whether the thumbnail has been built. A tile with no thumbnail yet is still being processed
    #: and shows a shimmer; the grid uses this to avoid requesting a still that is not there and
    #: reading the 404 as a picture that failed to make.
    has_thumb: bool = False
    #: Whether the hover clip has been built. The same fact for the clip that `has_thumb` is for
    #: the still, and it exists for the same reason: the wall could only find out by asking for the
    #: clip and reading the 404 as an answer, which on a library part-way through a Build is every
    #: video on every scroll: tens of thousands of requests a night.
    #:
    #: Said by the server because the server is the only side that knows. The presence of the art
    #: token is true the moment ANY picture is built, so a file with a still and no clip would pass
    #: it and ask anyway.
    has_preview: bool = False
    #: Why there is no still, when a feature has given up on the file: the picture pass's
    #: standing verdict, in its own words. None while the still is merely not built yet.
    picture_verdict: str | None = None
    #: That verdict's code, which is what the words a person reads are chosen by. None with it.
    picture_verdict_code: str | None = None
    #: Concealed by a flag on THIS FILE, rather than by a folder, a person or a set it belongs to.
    #: The same distinction `shared_here` and `restricted_here` carry above, and the mark on a tile
    #: is drawn solid or hollow from it: solid meaning the switch is here and this is where to
    #: reach it, hollow meaning it is somewhere above and this is not.
    concealed_here: bool = False
    #: Whether NO copy of this file is where Sift last saw it.
    #:
    #: A drive that is not mounted, a folder that moved, an archive deleted after its pictures were
    #: taken in. The row survives all three, and so does the thumbnail, which lives beside the
    #: library rather than inside it, so without this the tile is indistinguishable from a file
    #: that is really there, and only opening it says otherwise.
    unreachable: bool = False
    #: Kept at the top of its wall by the viewer who asked. Read off the statement rather than from
    #: a second query, because it is what that statement ORDERS BY: a screen drawing the mark from
    #: one source and the order from another is two answers to one question, and the day they
    #: disagree the pin appears on a row that is not first.
    pinned: bool = False

    #: The token that goes on the end of every picture address for this asset, or None when there
    #: is nothing to say about them yet. It names both what the pictures are and how many times what
    #: this viewer may see has changed, which is what lets a browser keep them without asking.
    art_version: str | None = None


@dataclass(frozen=True, slots=True)
class ServedDerivative:
    """A generated picture, and what deciding its caching rule needs.

    `version` is the token the address for this picture should carry, and None when there is not
    one: a picture with no recorded digest, which is served the careful way. `concealed` is
    whether this asset is in the vault of the user asking, which is the other reason a picture is
    never given a keepable address.

    Both come from the same read that decided the viewer may have it at all, which is the point of
    this being one object rather than two calls.
    """

    path: Path
    version: str | None
    concealed: bool


@dataclass(frozen=True, slots=True)
class AssetPage:
    """A page, and how many rows there are in total.

    The total comes out of the same statement as the rows, so it counts exactly what the page is
    a page of. A count computed by a second query is a count that can disagree with the page it
    describes, and a paginator that disagrees with its own total is the first sign that filtering
    has quietly moved out of the database.
    """

    items: list[AssetView]
    total: int
    #: How big those same files are, in bytes, for this viewer: what Browse says beside its count.
    #: Out of the same read as the total, so it is the size of exactly the files the total counts.
    total_bytes: int = 0


@dataclass(frozen=True, slots=True)
class PeoplePage:
    """One page of People, and how many there are in total for whoever asked.

    The total is what a screen needs in order to draw the paging at all, and it is the SCOPED
    total: two users asking the same question are meant to get different numbers, exactly as
    they do for the count on each row.
    """

    items: list[PersonSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class TagPage:
    """One page of Tags, and the scoped total. Same shape and same reasoning as `PeoplePage`."""

    items: list[TagSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class CollectionPage:
    """One page of Collections, and the scoped total."""

    items: list[CollectionView]
    total: int


@dataclass(frozen=True, slots=True)
class SitePage:
    """One page of Sites, and the scoped total."""

    items: list[SiteSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class PersonSuggestion:
    """A person, and how many assets the viewer who asked can see of them.

    `asset_count` is scoped exactly as a tag's is: two users asking about the same person are
    meant to get different numbers, and the absolute one is never sent to anybody.

    `vault` rides along because the People screen has to draw the flag it is offering to toggle.
    A row only reaches a caller at all when the vault is already unlocked, so this never tells
    anyone something they did not already have.
    """

    id: str
    name: str
    vault: bool
    asset_count: int
    #: How big those same files are, in bytes, summed off each file's own size: scoped exactly as
    #: the count beside it is, so it is the size of what this viewer may see and nothing the vault
    #: is holding back adds to it while the vault is shut.
    size_bytes: int = 0
    #: The still they are shown as, or None, either because none was chosen or because the one
    #: that was is concealed from whoever asked. A picture of a concealed item is the item.
    cover_asset_id: str | None = None
    #: An UPLOADED cover: a picture somebody put on this row, which is not a file in the library.
    #: Never set beside `cover_asset_id` (one statement writes both, so choosing either clears the
    #: other), and unlike that one it carries no visibility question, because there is no file
    #: behind it to be allowed or refused. The screen needs it to know there is a cover to draw at
    #: all: deciding that from `cover_asset_id` alone would draw the site's own logo over an
    #: uploaded picture.
    cover_upload_id: str | None = None
    #: Which moment of that file, when the cover is a chosen frame of a video. Withheld with
    #: the file. The cover's address carries it, which is what lets the browser keep the picture
    #: (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None = None
    #: The window of the cover's picture it is drawn as, or None for the whole of it. Read
    #: through `cover_frame.frame_of`, so it is only ever a window of the picture named above,
    #: withheld with the file. The cover's address carries it (`kernel/covers.py names_its_cover`).
    cover_frame: CoverFrame | None = None
    #: A face out of that still, when the cover is a face rather than a whole frame. Never set
    #: without `cover_asset_id`, and withheld under exactly the same condition: a face is a piece
    #: of a file, so it is shown only to somebody who may open the file it came from.
    cover_track_id: str | None = None
    #: This viewer's own heart and stars. Not the person's: a rating is an opinion held by somebody,
    #: and two users on one install hold their own.
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of this wall by this viewer. The same kind of thing as the heart
    #: above it: an opinion, held per user, and never a property of the row itself.
    pinned: bool = False
    #: Whether they MAKE the edits, as against appearing in them.
    #:
    #: A property of the ROW, unlike the three above it, and the wall carries it because the card
    #: draws a mark from it. Never withheld: it says nothing about a person that their name on the
    #: same row does not, so there is nothing here for the vault to hold back.
    pmv_creator: bool = False
    #: Whether this row may never be sent outside the machine.
    #:
    #: A property of the ROW, like `pmv_creator` beside it, and carried on the WALL for the reason
    #: that one is: a mark is drawn on the card. The menu's own answer comes from the route that
    #: answers about ONE row as it opens: a mark cannot, because nobody presses anything to draw
    #: sixty of them.
    keep_local: bool = False
    #: Whether this row is marked "Don't swap": kept out of every swap with another Sift. A property
    #: of the row, carried on the wall for the reason `keep_local` is: swap mode draws a mark on
    #: every card that will not go, and a mark cannot ask a route per row.
    keep_from_swaps: bool = False
    #: The alias or username the search term matched, when it was not the name above.
    #:
    #: A person is findable by three spellings (their name, an alias somebody typed in, and any
    #: username pointed at them), and a row that came back for one of the other two has no way to
    #: explain itself. Typing a username and being offered a name nobody typed reads as the box
    #: answering a different question; saying which spelling matched turns it into an answer. None
    #: when the name matched, which is the ordinary case.
    matched_as: str | None = None
    #: A LOCKED TILE: every file under this row that the viewer may see is in the vault, the vault
    #: is shut and placeholder mode is on, so the wall draws a padlock where the row would be and
    #: the name and the cover came back empty. Only ever True on a wall; a read by id keeps the
    #: name. The rule is `_LOCKED_TILE` in `repository/entities.py`.
    locked: bool = False


@dataclass(frozen=True, slots=True)
class UsernameSuggestion:
    """One username on one site, and what the viewer who asked can see under it.

    A Username is not a second copy of a Person and the difference is the whole reason it exists. A
    Person is a human; a Username is one identity on one site: its name, the display name shown
    beside it there, an address, and the files posted under it. One human holds several; one
    username can outlive whoever was behind it.

    `person_id` is who it turned out to belong to, and None is the ordinary state of a library
    nobody has been through yet. That is the whole of the question the Organize queue asks.

    No heart, no stars and no vault flag, deliberately (see the note at the foot of the statement
    that fills this in). Concealing is done to the site or to the person, which are the two things
    somebody thinks in.
    """

    id: str
    name: str
    asset_count: int
    #: How big those same files are, in bytes, summed off each file's own size: scoped exactly as
    #: the count beside it is, so it is the size of what this viewer may see and nothing the vault
    #: is holding back adds to it while the vault is shut.
    size_bytes: int = 0
    #: The name the site shows beside the username, when it is not the username itself.
    display_name: str | None = None
    #: The username's own page on the site it is on.
    url: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    #: Who this username belongs to, once somebody has said. None until then.
    person_id: str | None = None
    person_name: str | None = None
    #: How many people already answer to this username's spelling, by their name or an alias.
    #:
    #: What says which KIND of waiting a waiting username is. **Several** is a judgement and the
    #: only one on this row: the rule that files a download attributes on exactly one match and
    #: refuses to guess past that (`attribute_to_person`), so more than one is that rule declining.
    #: **None** is not a judgement at all: it is an offer to make somebody, and it accumulates
    #: only where making people from usernames has been switched off.
    #:
    #: Zero unless the caller asked for it: it costs a subquery per row and one screen wants it.
    #: Named for what it COUNTS rather than for what a screen concludes, so a reader that did not
    #: ask cannot mistake its zero for an answer.
    name_candidates: int = 0


@dataclass(frozen=True, slots=True)
class UsernamePage:
    """One page of Usernames, and the scoped total. Same shape and reasoning as `PeoplePage`."""

    items: list[UsernameSuggestion]
    total: int


@dataclass(frozen=True, slots=True)
class SiteSuggestion:
    """One site, and what the viewer who asked can see from it.

    `asset_count` is scoped like every other count here. A site is a grantable object, so the
    difference between its real size and what somebody is shown is the size of the set they were
    kept out of, and a number is enough to publish that.
    """

    id: str
    name: str
    asset_count: int
    #: How big those same files are, in bytes, summed off each file's own size: scoped exactly as
    #: the count beside it is, so it is the size of what this viewer may see and nothing the vault
    #: is holding back adds to it while the vault is shut.
    size_bytes: int = 0
    #: (How many PEOPLE a site has media of is not on this row: it is the People cell of the site's
    #: card counts, `Repository.card_counts`, which the route copies onto the wire.)
    #:
    #: The site's own address: the first of its links by id (`sites.SITE_ADDRESS`), since the column
    #: of this name went (catalog v66). Carried so a card can link to it and its logo be matched,
    #: and never rendered as an `href` without being checked: the write path is where
    #: `javascript:` is refused.
    site_url: str | None = None
    notes: str | None = None
    #: Scoped like a person's, and None when the chosen still is one this viewer may not open.
    cover_asset_id: str | None = None
    #: An UPLOADED cover: a picture somebody put on this row, which is not a file in the library.
    #: Never set beside `cover_asset_id` (one statement writes both, so choosing either clears the
    #: other), and unlike that one it carries no visibility question, because there is no file
    #: behind it to be allowed or refused. The screen needs it to know there is a cover to draw at
    #: all: deciding that from `cover_asset_id` alone would draw the site's own logo over an
    #: uploaded picture.
    cover_upload_id: str | None = None
    #: Which moment of that file, when the cover is a chosen frame of a video. Withheld with
    #: the file. The cover's address carries it, which is what lets the browser keep the picture
    #: (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None = None
    #: The window of the cover's picture it is drawn as, or None for the whole of it. Read
    #: through `cover_frame.frame_of`, so it is only ever a window of the picture named above,
    #: withheld with the file. The cover's address carries it (`kernel/covers.py names_its_cover`).
    cover_frame: CoverFrame | None = None
    #: This viewer's own; see `PersonSuggestion`.
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of this wall by this viewer. The same kind of thing as the heart
    #: above it: an opinion, held per user, and never a property of the row itself.
    pinned: bool = False
    #: In the vault, and readable for the same reason a tag's is: only somebody who has opened it
    #: ever sees this row, so it is what lets the site offer to come back out.
    vault: bool = False
    #: The other name the term matched, when it was not the name above. See `PersonSuggestion`.
    matched_as: str | None = None
    #: Kept local: never sent outside the machine. See `PersonSuggestion`: the card draws a mark
    #: from it, and a mark cannot ask a route per row.
    keep_local: bool = False
    #: Marked "Don't swap". See `PersonSuggestion.keep_from_swaps`.
    keep_from_swaps: bool = False
    #: A LOCKED TILE: every file under this row that the viewer may see is in the vault, the vault
    #: is shut and placeholder mode is on, so the wall draws a padlock where the row would be and
    #: the name, the address and the notes came back empty. Only ever True on a wall; a read by id
    #: keeps the name. The rule is `_LOCKED_TILE` in `repository/entities.py`.
    locked: bool = False


@dataclass(frozen=True, slots=True)
class AliasMatch:
    """Everyone a typed term turns out to name.

    A tuple and not one id: two people genuinely can share a name, and `people_aliases` does not
    pretend otherwise. An empty tuple means the term named nobody, which is the case the "is this
    another name for someone?" prompt exists to catch.
    """

    term: str
    person_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TagSuggestion:
    """A tag, and how many assets the viewer who asked can see under it.

    `asset_count` is scoped, not absolute. Two users asking about the same tag are meant to get
    different numbers, and the absolute one is never sent to anybody: it is the size of the set
    somebody was not shown.
    """

    id: str
    name: str
    asset_count: int
    #: How big those same files are, in bytes, summed off each file's own size: scoped exactly as
    #: the count beside it is, so it is the size of what this viewer may see and nothing the vault
    #: is holding back adds to it while the vault is shut.
    size_bytes: int = 0
    #: The still this tag is drawn as, when the viewer may open the file it came from. None both
    #: for a tag nobody has given a cover and for one whose cover is a file this viewer cannot
    #: see, deliberately the same answer, because the second must not be distinguishable.
    cover_asset_id: str | None = None
    #: An UPLOADED cover: a picture somebody put on this row, which is not a file in the library.
    #: Never set beside `cover_asset_id` (one statement writes both, so choosing either clears the
    #: other), and unlike that one it carries no visibility question, because there is no file
    #: behind it to be allowed or refused. The screen needs it to know there is a cover to draw at
    #: all: deciding that from `cover_asset_id` alone would draw the site's own logo over an
    #: uploaded picture.
    cover_upload_id: str | None = None
    #: Which moment of that file, when the cover is a chosen frame of a video. Withheld with
    #: the file. The cover's address carries it, which is what lets the browser keep the picture
    #: (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None = None
    #: The window of the cover's picture it is drawn as, or None for the whole of it. Read
    #: through `cover_frame.frame_of`, so it is only ever a window of the picture named above,
    #: withheld with the file. The cover's address carries it (`kernel/covers.py names_its_cover`).
    cover_frame: CoverFrame | None = None
    #: In the vault. Only ever True for somebody who has opened it, since a concealed tag does not
    #: come back at all otherwise, so a screen reads it to offer the way out, never the way in.
    vault: bool = False
    #: The other name the term matched, when it was not the name above. See `PersonSuggestion`:
    #: a row that comes back for a word it does not contain has to say why, or it reads as the box
    #: answering a different question.
    matched_as: str | None = None
    #: What THIS viewer thinks of it. Two users sharing an install hold their own, which is the
    #: only reading of an opinion that makes sense, so these come off a row joined per viewer and
    #: are never a property of the tag.
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of this wall by this viewer. The same kind of thing as the heart
    #: above it: an opinion, held per user, and never a property of the row itself.
    pinned: bool = False
    #: Kept local: never sent outside the machine. See `PersonSuggestion`: the card draws a mark
    #: from it, and a mark cannot ask a route per row.
    keep_local: bool = False
    #: Marked "Don't swap". See `PersonSuggestion.keep_from_swaps`.
    keep_from_swaps: bool = False
    #: A LOCKED TILE: every file under this row that the viewer may see is in the vault, the vault
    #: is shut and placeholder mode is on, so the wall draws a padlock where the row would be and
    #: the name and the cover came back empty. Only ever True on a wall; a read by id keeps the
    #: name. The rule is `_LOCKED_TILE` in `repository/entities.py`.
    locked: bool = False
    #: The tag this one is filed under, and its name, so a picker can group a branch under its
    #: parent. Both None at the top of the tree, and the name is withheld for a parent this viewer
    #: has hidden: then the tag reads as one at the top.
    parent_id: str | None = None
    parent_name: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionView:
    """A collection, as one viewer may know it.

    Three of these columns are scoped rather than reported as they sit in the row, and each is a
    way the same fact leaks.

    `item_count` is the number of items this viewer can actually see, not the number of rows in
    the collection. A collection showing twelve items above a count of fifty has told whoever is
    reading that thirty-eight things exist, which is the one number the whole model is keeping
    back, arrived at by subtraction instead of by being shown.

    `cover_asset_id` is empty unless the viewer may see that asset. A cover is a picture of one
    item, so a collection wearing the cover of something concealed has published it at thumbnail
    size, and the count next to it can be perfectly correct while it does.

    `vault` says the collection is concealed, and a concealed collection does not come back here
    at all unless the vault is unlocked, so a caller only ever reads it as `False`, or as `True`
    on the far side of an unlock. It is carried so a screen that is allowed to see one can say so.
    """

    id: str
    name: str
    cover_asset_id: str | None
    #: An uploaded cover. See `PersonSuggestion.cover_upload_id`.
    cover_upload_id: str | None
    #: Which moment of that file, when the cover is a chosen frame of a video. Withheld with
    #: the file. The cover's address carries it, which is what lets the browser keep the picture
    #: (`kernel/covers.py names_its_cover`).
    cover_at_ms: int | None
    vault: bool
    owner_id: str | None
    created_at: int
    item_count: int
    #: How big those same files are, in bytes, summed off each file's own size: scoped exactly as
    #: the count beside it is, so it is the size of what this viewer may see and nothing the vault
    #: is holding back adds to it while the vault is shut.
    size_bytes: int = 0
    #: What THIS viewer thinks of it, off a row joined per viewer, never a property of the
    #: collection. Two users sharing an install hold their own, which is the only reading of an
    #: opinion that makes sense.
    favorite: bool = False
    rating: int | None = None
    #: Kept at the top of this wall by this viewer. The same kind of thing as the heart
    #: above it: an opinion, held per user, and never a property of the row itself.
    pinned: bool = False
    #: The window of the cover's picture it is drawn as, or None for the whole of it. Read
    #: through `cover_frame.frame_of`, so it is only ever a window of the picture named above,
    #: withheld with the file. The cover's address carries it (`kernel/covers.py names_its_cover`).
    cover_frame: CoverFrame | None = None
    #: A LOCKED TILE: every file under this row that the viewer may see is in the vault, the vault
    #: is shut and placeholder mode is on, so the wall draws a padlock where the row would be and
    #: the name came back empty. Only ever True on a wall; a read by id keeps the name. The rule is
    #: `_LOCKED_TILE` in `repository/entities.py`.
    locked: bool = False


def _frame(row: Row) -> CoverFrame | None:
    """The window of this row's cover, bound to the pointers the scoped read handed back.

    The pointers as READ, never as stored: a file this viewer may not open has had its
    `cover_asset_id` withheld by the statement, so a frame of it names a picture the row no longer
    does and is withheld with it. See `cover_frame.frame_of`.
    """
    return frame_of(
        row["cover_frame"],
        asset_id=row["cover_asset_id"],
        at_ms=row["cover_at_ms"],
        upload_id=row["cover_upload_id"],
    )


def _suggested(row: Row) -> PersonSuggestion:
    """One row of the people query as a person. Both callers of that query use this, so a listing
    and a by-id lookup cannot come to describe the same person differently."""
    return PersonSuggestion(
        id=row["id"],
        name=row["name"],
        vault=bool(row["vault"]),
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        cover_track_id=row["cover_track_id"],
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        pmv_creator=bool(row["pmv_creator"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        # The alias first when both matched: an alias is a name for the PERSON and a username is a
        # name they hold on a site, so the alias is the nearer answer to "who did you mean".
        # Both are already NULL whenever the name itself matched: the statement decides that, so
        # the rule lives in one place rather than two that can disagree.
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        matched_as=row["matched_alias"] or row["matched_username"] or None,
        locked=bool(row["locked"]),
    )


def _username_from_row(row: Row) -> UsernameSuggestion:
    """One row of the usernames query as a username. Both callers use this, so a listing and a
    by-id lookup cannot come to describe the same username differently."""
    return UsernameSuggestion(
        id=str(row["id"]),
        name=str(row["name"]),
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        display_name=row["display_name"],
        url=row["url"],
        site_id=row["site_id"],
        site_name=row["site_name"],
        person_id=row["person_id"],
        person_name=row["person_name"],
        name_candidates=int(row["name_candidates"]),
    )


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern matching this prefix literally.

    `%` and `_` are wildcards to LIKE and ordinary characters in a tag name. Unescaped, typing an
    underscore would match every tag, which looks like a broken suggester rather than like a
    pattern being interpreted.
    """
    escaped = _like_literal(prefix)
    return f"{escaped}%"


def _like_literal(term: str) -> str:
    """The wildcards taken out of a term, so LIKE reads it as the text somebody typed."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_anywhere(term: str) -> str:
    """A LIKE pattern matching this term anywhere in the value.

    What a box people TYPE INTO to filter a wall has to do. A prefix match is right for a suggester
    (it completes what is being typed) and wrong for a filter: somebody who knows a person by
    their surname types the surname, gets nothing, and concludes the person is not there.

    Unindexable, and that is fine here and only here: this filters People, which is hundreds of
    rows, not the asset table.
    """
    return f"%{_like_literal(term)}%"


def _optional_text(row: Row, name: str) -> str | None:
    """One nullable string off a row that may not carry the column at all."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return None
    return None if value is None else str(value)


def _asset_view(row: Row, viewer: Viewer) -> AssetView:
    """One row of the scoped read, as the thing every caller above hands out.

    Here rather than written out at each call site because the picture token is folded together
    from two sources: what the pictures are, which is on the row, and how many times what this
    viewer may see has changed, which is on the viewer. Two places computing that is one place
    where they eventually stop agreeing, and the symptom would be pictures that never load on
    exactly one screen.
    """
    return AssetView(
        asset_from_row(row),
        bool(row["concealed"]),
        has_thumb=bool(row["has_thumb"]),
        has_preview=art_carries(row["art_marks"], DerivativeKind.PREVIEW.value),
        picture_verdict=_optional_text(row, "picture_verdict"),
        picture_verdict_code=_optional_text(row, "picture_verdict_code"),
        unreachable=bool(row["unreachable"]),
        art_version=art_version(row["art_marks"], viewer.cache_stamp),
        concealed_here=bool(row["concealed_here"]),
        pinned=bool(row["pinned"]),
    )


def _grant_from_row(row: Row) -> Grant:
    return Grant(
        id=row["id"],
        object_type=ObjectType(str(row["object_type"])),
        object_id=row["object_id"],
        subject_user_id=row["subject_user_id"],
        effect=Effect(row["effect"]),
        created_at=row["created_at"],
    )


def _photo_set_from_row(row: Row) -> PhotoSetView:
    """One row of the photo-set query as a set. The listing and the by-id lookup both use it, so
    the two cannot come to describe the same set differently."""
    return PhotoSetView(
        id=str(row["id"]),
        name=str(row["name"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        vault=bool(row["vault"]),
        created_at=int(row["created_at"]),
        item_count=int(row["item_count"]),
        size_bytes=int(row["size_bytes"]),
        origin=str(row["origin"]),
        origin_url=row["origin_url"],
        folder_id=row["folder_id"],
        notes=row["notes"],
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        locked=bool(row["locked"]),
    )


def _song_from_row(row: Row) -> SongView:
    """One row of the songs query as a song. The listing and the by-id lookup both use it."""
    return SongView(
        id=str(row["id"]),
        name=str(row["name"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        created_at=int(row["created_at"]),
        item_count=int(row["item_count"]),
        size_bytes=int(row["size_bytes"]),
        recording_id=row["recording_id"],
        notes=row["notes"],
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        locked=bool(row["locked"]),
        vault=bool(row["vault"]),
        artists=_credits(row["artists"]),
    )


def _credits(packed: str | None) -> tuple[tuple[str, str], ...]:
    """A song's artists as the songs statement packs them (a JSON array of `[id, name]`)."""
    if not packed:
        return ()
    return tuple((str(one[0]), str(one[1])) for one in json.loads(packed))


#: What separates one tag from the next, and one field from the next, inside the packed column.
#:
#: Two control characters rather than a comma and a colon, and that is not fussiness: a tag may be
#: called `beach, party`. Any separator a person could type
#: is a separator that eventually splits a name in half, and the two below are the only characters
#: `clean_stored_text` refuses to store, so a name containing one cannot exist.
_BETWEEN_TAGS = "\x1f"
_BETWEEN_FIELDS = "\x1e"


def _loop_tags(packed: str | None) -> tuple[LoopTag, ...]:
    """The Loop's own tags, unpacked from the one column they arrive in.

    Empty for a Loop nobody has tagged, which is most of them: `group_concat` over no rows is
    NULL rather than an empty string, and both mean the same thing here.
    """
    if not packed:
        return ()
    found = []
    for entry in packed.split(_BETWEEN_TAGS):
        tag_id, _, name = entry.partition(_BETWEEN_FIELDS)
        found.append(LoopTag(id=tag_id, name=name))
    return tuple(found)


def _loop_from_row(row: Row, viewer: Viewer) -> LoopView:
    """One row of the loop query as a loop, for the reason the mapper above exists.

    The viewer is needed for the picture token and for nothing else: it is folded from what
    the pictures are, which is on the row, and how many times what this user may see has
    changed, which is on the viewer. Exactly as `_asset_view` does it, one function up.
    """
    return LoopView(
        id=str(row["id"]),
        asset_id=str(row["asset_id"]),
        name=row["name"],
        start_ms=int(row["start_ms"]),
        end_ms=int(row["end_ms"]),
        created_at=int(row["created_at"]),
        created_by=row["created_by"],
        media_type=str(row["media_type"]),
        width=row["width"],
        height=row["height"],
        duration_ms=row["duration_ms"],
        favorite=bool(row["favorite"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        pinned=bool(row["pinned"]),
        views=int(row["view_count"]),
        o_count=int(row["o_count"]),
        hidden=bool(row["vault_hidden"]),
        hidden_here=bool(row["concealed_here"]),
        unreachable=bool(row["unreachable"]),
        concealed=not bool(row["showable"]),
        has_thumb=bool(row["has_thumb"]),
        has_preview=art_carries(row["art_marks"], DerivativeKind.PREVIEW.value),
        has_still=bool(row["has_still"]),
        original_filename=row["original_filename"],
        own_tags=_loop_tags(row["own_tags"]),
        art_version=art_version(row["art_marks"], viewer.cache_stamp),
    )


def _collection_from_row(row: Row) -> CollectionView:
    return CollectionView(
        id=row["id"],
        name=row["name"],
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        vault=bool(row["vault"]),
        owner_id=row["owner_id"],
        created_at=int(row["created_at"]),
        item_count=int(row["item_count"]),
        size_bytes=int(row["size_bytes"]),
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        locked=bool(row["locked"]),
    )


def _tag_from_row(row: Row) -> TagSuggestion:
    return TagSuggestion(
        id=row["id"],
        name=row["name"],
        vault=bool(row["vault"]),
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        matched_as=row["matched_alias"] or None,
        locked=bool(row["locked"]),
        parent_id=row["parent_id"] if row["parent_name"] is not None else None,
        parent_name=row["parent_name"],
    )


def _site_from_row(row: Row) -> SiteSuggestion:
    return SiteSuggestion(
        id=row["id"],
        name=row["name"],
        asset_count=int(row["asset_count"]),
        size_bytes=int(row["size_bytes"]),
        site_url=row["site_url"],
        notes=row["notes"],
        cover_asset_id=row["cover_asset_id"],
        cover_upload_id=row["cover_upload_id"],
        cover_at_ms=row["cover_at_ms"],
        cover_frame=_frame(row),
        favorite=bool(row["favorite"]),
        pinned=bool(row["pinned"]),
        rating=None if row["rating"] is None else int(row["rating"]),
        vault=bool(row["vault"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
        matched_as=row["matched_alias"] or None,
        locked=bool(row["locked"]),
    )


def _folder_from_row(row: Row) -> Folder:
    return Folder(
        id=row["id"],
        root_id=row["root_id"],
        parent_id=row["parent_id"],
        rel_path=row["rel_path"],
        name=row["name"],
        vault=bool(row["vault"]),
        concealed=bool(row["concealed"]),
    )
