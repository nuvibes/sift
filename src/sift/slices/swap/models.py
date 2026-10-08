# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes a swap puts on the wire and on its screens.

One module, several sections, each under its own heading, the way `store.py` is laid out: the
offer, the diff and the offer screen first, then whatever the session adds under its own.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from sift.kernel.wire import Wire

# ================================================================================================
# THE OFFER, THE DIFF AND THE OFFER SCREEN
# ================================================================================================
#
# ## What the offer is allowed to say, and why the list is closed
#
# The offer is the one message in which one install tells another about its library, so what it
# may carry is decided HERE, by the fields, and nowhere else: a file's exact and perceptual hashes,
# its kind and shape, a title Sift makes up, the Site and Username it was filed under, and which
# offered people are on it; a person's names, their stash-box ids when the host allows it, and their
# facial fingerprints (the face descriptions a file of them would carry) with the host's confirmed
# count of them, pictures beside them only where the face models differ; and a file's name on the
# host's disk, the leaf alone, so a received file keeps the name the person who kept it gave it;
# and a file's song (its name, its artists, the AcoustID recording it is), only to a guest whose
# hello said it takes one (`OfferedSong`). Never a web address, a path, a rating, a note, a History
# line or a folder.
# `test_the_offer_carries_nothing_it_may_not` reads these classes' fields, so a field added here is
# a field somebody has to argue for.
#
# The name travels because a received file keeps its own name rather than the made-up title
# ("Ava Example, clip 14 of 38"). A name is what the file is called, and the leaf alone names no
# folder, no account and no path.
#
# ## Why every field is bounded
#
# The guest reads an offer a PEER wrote, and a peer can lie. Each string has a ceiling, each list a
# cap and `extra="forbid"` refuses a key nobody declared, so a hostile offer is refused as a whole
# rather than half-read, and `Offer`'s own check refuses a person index that points nowhere,
# which is the one fact `rows` and the landing would otherwise have to distrust at every use.

#: What a host can offer: an entity, one file picked on a wall in swap mode, a saved filter (a
#: question the host kept, answered on the host by the host's own search), or a person's facial
#: fingerprints alone (`facial_fingerprints`, by person id): the people the host's Sift can
#: recognize, offered with no file, which is what a swap of files alone never carries.
ChosenKind = Literal[
    "person",
    "site",
    "tag",
    "collection",
    "photo_set",
    "song",
    "asset",
    "filter",
    "facial_fingerprints",
]

#: The kinds of file a swap moves, in the words `assets.media_type` already uses.
FileKind = Literal["video", "image", "gif"]

#: How many things one offer may be made of. Generous; it exists so a request cannot be a
#: million-element list.
MAX_CHOSEN = 500

#: The most files one offer may list. It bounds the guest's memory against a peer's lie rather
#: than any real library: a hundred thousand files is a whole large library offered in one go.
MAX_OFFER_FILES = 200_000

#: The most people one offer may name.
MAX_OFFER_PEOPLE = 5_000

#: The most face descriptions one offered person carries: the best by quality. A description is a
#: suggestion for the guest to confirm (the match by face is never automatic), and a few dozen
#: good ones answer that question as well as hundreds do, at a fraction of the offer's size.
MAX_FACES_PER_PERSON = 64

#: The most aliases or stash-box ids one person carries.
MAX_NAMES_PER_PERSON = 64

#: A ceiling for a name, a title or a Site: long enough for anything real, short enough that a
#: peer cannot send a novel as a person's name.
_TEXT = 300

#: The longest song name an offer carries: the same ceiling. A longer one is cut to it.
MAX_SONG_NAME = _TEXT

#: A base64 face description: 2,048 dimensions of four bytes is the most any recognizer here has.
_VECTOR = 12_000

#: A base64 face picture: the aligned square a recognizer reads, kept as a small JPEG. Generous
#: for the few kilobytes one is; a ceiling so a peer cannot hide a file in a face.
MAX_PICTURE_TEXT = 200_000

#: The most face picture text one offer carries, all people together. Pictures travel only when the
#: two sides' models differ (see `OfferedFace.picture`); past this the rest go as numbers alone, so
#: an offer of every person a library knows stays one message the session can send.
MAX_OFFER_PICTURES = 64 * 1024 * 1024


class _Strict(Wire):
    """A shape read from a peer: nothing undeclared, nothing unbounded."""

    model_config = ConfigDict(extra="forbid")


class Chosen(_Strict):
    """One thing the host offers: an entity by id, or one saved filter by its id."""

    # Frozen, so two picks of the same thing are one: `chosen_list` folds them by value.
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ChosenKind
    id: str = Field(min_length=1, max_length=64)


def chosen_list(raw: object) -> tuple[Chosen, ...]:
    """What a request or a session row says was chosen, checked: a list of `Chosen`, at most one of
    them a filter, each named once. Raises `ValueError` with a sentence a screen can show."""
    if not isinstance(raw, list):
        raise ValueError("Choose what to offer.")
    if len(raw) > MAX_CHOSEN:
        raise ValueError(f"A swap can offer at most {MAX_CHOSEN} things.")
    picked = tuple(dict.fromkeys(Chosen.model_validate(one) for one in raw))
    if sum(1 for one in picked if one.kind == "filter") > 1:
        raise ValueError("A swap can offer one saved filter.")
    return picked


class OfferedFace(_Strict):
    """One facial fingerprint, as a file of them holds it, and its picture only when needed.

    The picture is the face square the fingerprint was read from, and it travels only when the
    guest said it uses a different face model: fingerprints from two models cannot be compared, so
    without it the guest could do nothing with this face, and with it the guest makes its own.
    """

    digest: str = Field(max_length=128)
    quality: float
    #: The description's numbers as the pack stores them (little-endian four-byte floats) in
    #: base64, so the landing can hand them to the pack import unchanged.
    vector: str = Field(max_length=_VECTOR)
    #: The face square as a JPEG, in base64, or None (the ordinary case: the models agree).
    picture: str | None = Field(default=None, max_length=MAX_PICTURE_TEXT)


class OfferedFaces(_Strict):
    """A person's face descriptions, and the model that made them.

    The recognizer travels because descriptions from two models cannot be compared at all, and the
    guest refuses them rather than comparing them: the rule the face pack import already applies.
    """

    recognizer: str = Field(max_length=_TEXT)
    dimension: int = Field(ge=1, le=2048)
    faces: list[OfferedFace] = Field(default=[], max_length=MAX_FACES_PER_PERSON)
    #: How many confirmed faces the host has of them (`faces` are the best), said on the guest's
    #: screen; sent only to a side whose hello said it takes it (`Offer.as_sent`).
    confirmed: int | None = Field(default=None, ge=0, le=100_000_000)


class OfferedPerson(_Strict):
    """A person the host offered, as the guest needs them to match or to add them."""

    name: str = Field(min_length=1, max_length=_TEXT)
    aliases: list[str] = Field(default=[], max_length=MAX_NAMES_PER_PERSON)
    #: The stash-box ids the host holds for this person, when the host allowed them to travel.
    boxes: list[str] = Field(default=[], max_length=MAX_NAMES_PER_PERSON)
    faces: OfferedFaces | None = None

    @model_validator(mode="after")
    def _bounded_names(self) -> OfferedPerson:
        if any(len(one) > _TEXT for one in (*self.aliases, *self.boxes)):
            raise ValueError("a name in the offer is too long")
        return self


class OfferedSite(_Strict):
    """A Site a file in the offer was filed under. Its name, never its address."""

    name: str = Field(min_length=1, max_length=_TEXT)


class OfferedSong(_Strict):
    """A file's song: its name, the artists the host keeps apart from the name where it keeps
    them, and the AcoustID recording it is where one said. What the receiver matches it by, in
    that order of trust: the recording, then the name."""

    name: str = Field(min_length=1, max_length=MAX_SONG_NAME)
    artists: list[str] = Field(default=[], max_length=MAX_NAMES_PER_PERSON)
    #: An AcoustID recording id: a UUID, held to a ceiling rather than a pattern so a recording
    #: written another way one day is still read.
    recording: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _bounded_artists(self) -> OfferedSong:
        if any(len(one) > _TEXT for one in self.artists):
            raise ValueError("a name in the offer is too long")
        return self


class OfferedFile(_Strict):
    """One file the host offers, as much as the guest needs to decide whether it wants it."""

    #: The host's own id for the file. Opaque to the guest: it names the file in the diff and in
    #: the transfer, and it is meaningless in the guest's library.
    key: str = Field(min_length=1, max_length=64)
    oshash: str | None = Field(default=None, max_length=64)
    size: int = Field(ge=0)
    identity: str = Field(min_length=1, max_length=128)
    #: The whole-video fingerprint. Video only.
    video_phash: str | None = Field(default=None, max_length=64)
    #: A picture's own fingerprint. Picture only: the one field beyond the design's list, because
    #: without it no picture could ever be recognized as already held and every one would be sent.
    phash: str | None = Field(default=None, max_length=64)
    #: Which generation of the fingerprints these are. Two installs compare fingerprints only when
    #: this agrees, because two generations are two different numbers for one picture.
    fingerprint_version: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    kind: FileKind
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    #: A name Sift made up ("Ava Example, clip 14 of 38"): what the offer screen lists the file as.
    title: str = Field(min_length=1, max_length=_TEXT * 2)
    #: The file's own name on the host's disk, the leaf alone: what it lands under on the guest's
    #: side, numbered on a clash. None where the host has no name for it.
    name: str | None = Field(default=None, max_length=255)
    site: str | None = Field(default=None, max_length=_TEXT)
    username: str | None = Field(default=None, max_length=_TEXT)
    #: Which offered people are on this file, as indexes into the offer's `people`, first first.
    people: list[int] = Field(default=[], max_length=MAX_NAMES_PER_PERSON)
    #: The file's song, or None. Sent only to a guest whose hello said it takes one (`songs`): an
    #: install from before songs travelled refuses an offer holding a field it does not declare.
    song: OfferedSong | None = None


class Offer(_Strict):
    """What the host offers, whole. See the section's heading for what it may carry."""

    files: list[OfferedFile] = Field(default=[], max_length=MAX_OFFER_FILES)
    people: list[OfferedPerson] = Field(default=[], max_length=MAX_OFFER_PEOPLE)
    sites: list[OfferedSite] = Field(default=[], max_length=MAX_OFFER_PEOPLE)

    @model_validator(mode="after")
    def _consistent(self) -> Offer:
        """Every person index names a person, and every key names one file."""
        count = len(self.people)
        if any(index < 0 or index >= count for one in self.files for index in one.people):
            raise ValueError("a file in the offer names a person the offer does not list")
        if len({one.key for one in self.files}) != len(self.files):
            raise ValueError("the offer lists one file twice")
        return self

    def as_sent(self, *, songs: bool, counts: bool = False) -> dict[str, Any]:
        """The offer as it goes on the wire. To a side whose hello did not say it takes songs,
        every file's `song` is left out, the key and all: an install from before songs travelled
        refuses an offer holding a field it does not declare, even an empty one. A person's
        confirmed count is left out the same way for a side whose hello did not say `counts`."""
        exclude: dict[str, Any] = {}
        if not songs:
            exclude["files"] = {"__all__": {"song"}}
        if not counts:
            exclude["people"] = {"__all__": {"faces": {"confirmed"}}}
        return self.model_dump(mode="json", exclude=exclude or None)


class Diff(_Strict):
    """What the guest answers the offer with: the files it wants, and who each person is here.

    `people` maps an offered person's index to the guest's own person, or to null where the guest
    has nobody by that name and the person arrives as somebody new. It is the guest's own reading,
    for its landing, and it never crosses: the host has no use for another library's ids, and a
    list of them would tell it who that library holds. Nor does why a file is not wanted, or which
    of the guest's files it matched.
    """

    wanted: list[str] = Field(default=[], max_length=MAX_OFFER_FILES)
    #: Read from the wire with a default, so an answer from an install that still sends it is
    #: accepted and ignored.
    people: dict[int, str | None] = Field(default={})

    def for_the_other_side(self) -> Diff:
        """The answer as it is SENT: the wanted keys and nothing else."""
        return Diff(wanted=list(self.wanted))

    @model_validator(mode="after")
    def _bounded(self) -> Diff:
        if len(self.people) > MAX_OFFER_PEOPLE:
            raise ValueError("the answer names more people than an offer may")
        if any(len(one) > 64 for one in self.wanted) or any(
            one is not None and len(one) > 64 for one in self.people.values()
        ):
            raise ValueError("the answer carries an id no install could have made")
        return self


#: Why the guest already has a file: the same bytes (an exact key agrees as well as the picture),
#: or a copy that looks the same (the fingerprint within the duplicate rule, the exact keys not).
HeldReason = Literal["same", "near"]

#: Which offered name matched one of the guest's people: a shared stash-box id, the person's
#: name, or one of their aliases.
MatchedBy = Literal["box", "name", "alias"]


class HeldFile(Wire):
    """One offered file the guest already has, and why: the unticked row that says so."""

    key: str
    title: str
    reason: HeldReason


class PersonRow(Wire):
    """One offered person on the guest's screen: what taking them would bring, and who they are here.

    `files` and `bytes` count what the guest would receive: the files under this person it does
    not already have. `held` counts the ones it does. A file under two offered people counts under
    both, which is why the screen also says how many do (`OfferScreen.shared`).
    """

    index: int
    name: str
    files: int
    bytes: int
    held: int
    #: How many facial fingerprints come with this person. A row with no files and some of these
    #: is a person offered for their facial fingerprints alone.
    faces: int = 0
    #: The host's confirmed count of them; null where the host did not say (an older Sift, nobody's).
    confirmed: int | None = None
    #: The guest's own person this one will be filed under, or null for somebody new.
    match_id: str | None = None
    match_name: str | None = None
    matched_by: MatchedBy | None = None


class OfferScreen(Wire):
    """What the guest's offer screen draws.

    `layout` is `rows` for up to ten offered people (a row each, Take or Skip) and `whole` above
    that: the totals, the five largest in `rows`, and everybody in `everyone` for the collapsed list.
    The totals count each file once, however many offered people it is under.
    """

    layout: Literal["rows", "whole"]
    rows: list[PersonRow] = Field(default=[])
    everyone: list[PersonRow] = Field(default=[])
    #: Everything offered, each file once, those already here included: what the other side sends
    #: before anything is taken or held back.
    offered_files: int = 0
    offered_bytes: int = 0
    #: What taking everything would bring: the offered files less those already here.
    files: int = 0
    bytes: int = 0
    #: How many of the files counted above are under two or more offered people.
    shared: int = 0
    #: Files the guest would receive that are under no offered person (offered through a Site, a
    #: tag, a collection, a photo set or a saved filter).
    unfiled_files: int = 0
    unfiled_bytes: int = 0
    #: What the guest already has, each with why, so the unticked rows can say so.
    held: list[HeldFile] = Field(default=[])


# ================================================================================================
# THE SESSION: starting, joining, the code, the session screen, the device
# ================================================================================================
#
# What the session routes take and answer. The token is answered to the host who pressed Start
# and to nobody else, and only while nobody has joined: it holds the session's secret, so it is
# never on a row and never in a reply once it has done its one job.


class StartSwap(Wire):
    """Start a swap: what is offered, through which tunnel, and whether stash-box ids travel.
    With `two_way` (Send and receive), the guest offers too, and what this side takes from it
    goes in `dest_folder_id`."""

    chosen: list[Chosen] = Field(min_length=1, max_length=MAX_CHOSEN)
    tunnel_id: str = Field(min_length=1, max_length=64)
    share_boxes: bool = True
    two_way: bool = False
    dest_folder_id: str | None = Field(default=None, min_length=1, max_length=64)


class SwapStarted(Wire):
    """The host's token, with the sentence the screen shows beside it, word for word."""

    session_id: str
    token: str
    sentence: str
    #: When the token stops working, in seconds since the epoch.
    expires: int


class JoinSwap(Wire):
    """Join a swap: the token that was pasted, and where received files go."""

    token: str = Field(min_length=1, max_length=400)
    dest_folder_id: str = Field(min_length=1, max_length=64)
    #: The tunnel chosen beside Join for this side's dial, remembered for the next one. Absent,
    #: the one chosen last time.
    tunnel_id: str | None = Field(default=None, min_length=1, max_length=64)


class SwapJoined(Wire):
    session_id: str


class CodeAnswer(Wire):
    """They match (`true`) or They don't match (`false`). The guest of a swap that sends and
    receives says with its They match what it sends (`chosen`) and whether stash-box ids go."""

    match: bool
    chosen: list[Chosen] = Field(default=[], max_length=MAX_CHOSEN)
    share_boxes: bool = True


#: The states a session passes through, as its row holds them.
#: A session's state as the screen draws it. `cut_off` is a live session a tunnel cut off, still in
#: its step and waiting for the same token to join again (`rejoin_until`).
SessionState = Literal[
    "waiting", "connected", "offered", "transferring", "cut_off", "done", "ended", "failed"
]


class SwapDirection(Wire):
    """One direction of a swap that sends and receives, from this side: what was offered, what
    was wanted, what has moved (sent or received), and the pace while it moves."""

    offered_files: int = 0
    wanted_files: int = 0
    files: int = 0
    bytes: int = 0
    #: What the wanted files add up to, for the estimate. Null once the session is not running.
    wanted_bytes: int | None = None
    #: This direction's own measured pace, in bits a second.
    rate_bps: int | None = None
    #: Whether its files are moving: the other side has answered the offer.
    moving: bool = False
    #: On the direction this side receives: the files received so far, each counted when its last
    #: piece is in and the whole file checks (`files` counts them once they are filed). Null on the
    #: direction this side sends, and once the session is not running.
    received_files: int | None = None


class SwapSession(Wire):
    """One session as the swap screen draws it: its state, the code, the counts, the rate.

    `sent_files` on the guest's side counts files received. `token` is the host's own, while it is
    waiting for somebody to join, and null otherwise. `code` is null until the two devices have
    said hello. `offer` is the guest's offer screen once the offer has arrived.
    """

    id: str
    short_id: str
    role: Literal["host", "guest"]
    state: SessionState
    code: str | None = None
    token: str | None = None
    sentence: str | None = None
    peer_device: str | None = None
    offered_files: int = 0
    wanted_files: int = 0
    sent_files: int = 0
    sent_bytes: int = 0
    #: What the wanted files add up to, for the estimate. Null once the session is not running.
    wanted_bytes: int | None = None
    #: The session's own measured rate over its last ten seconds, in bits a second.
    rate_bps: int | None = None
    #: Files that arrived without being asked for, refused and counted (the guest's side).
    unwanted_files: int = 0
    #: The guest's side: the files received so far, each counted when its last piece is in and the
    #: whole file checks. `sent_files` counts them once they are filed, one at a time after that,
    #: so the receiver's screen says both. Null on the host's side and once the session has ended.
    received_files: int | None = None
    started_at: int
    ended_at: int | None = None
    end_reason: str | None = None
    offer: OfferScreen | None = None
    #: While cut off: when the token runs out, and with it the wait to be joined again.
    rejoin_until: int | None = None
    #: A swap that sends and receives: each side sends and receives, and each direction is drawn
    #: from `sending` and `receiving` (the fields above keep the direction from the host to the
    #: guest). `answered` says this side has pressed Take these on the other side's offer.
    two_way: bool = False
    answered: bool = False
    sending: SwapDirection | None = None
    receiving: SwapDirection | None = None


class WeighSwap(Wire):
    """What to weigh before a swap starts: the things picked, as Start would send them."""

    chosen: list[Chosen] = Field(default=[], max_length=MAX_CHOSEN)


class LeftOut(Wire):
    """A pick that wears a mark on its own row, and how many of its files the mark keeps back:
    `local` is Kept local ("Don't enrich"), `swap` is "Don't swap"."""

    kind: Literal["asset", "person", "site", "tag", "folder"]
    id: str
    name: str
    mark: Literal["local", "swap"]
    files: int


class SwapWeight(Wire):
    """How many files and how many bytes: what the picks would offer (the sender's side, read as
    the offer reads them, Hidden open), or what an answer to an offer would bring (the receiver's
    side).

    Ahead of the press that starts it, what the picks leave out and why (`weight.left_out`): every pick that wears a
    mark, by name, and how many more files a mark on something they are filed under keeps back.
    Empty for an answer to an offer."""

    files: int = 0
    bytes: int = 0
    left_out: list[LeftOut] = Field(default=[])
    left_out_other: int = 0


class SwapDevice(Wire):
    """This install's device id, or null before the first swap made one; and whether the saved
    keys are locked (a device id cannot be made or reset until an admin signs in again)."""

    device_id: str | None = None
    locked: bool = False


# ================================================================================================
# THE SCREENS: the guest's answer to the offer, and the tunnels a swap can go through
# ================================================================================================
#
# What the swap screens send and read beyond the session's own routes. The answer is bounded by the
# offer's own caps: it names people by their place in the offer and files by the offer's keys, so
# it can never be longer than the offer it answers.


class TakeOffer(Wire):
    """The guest's answer to the offer screen: the offered people it skipped, by their index in the
    offer, and the files it unticked, by the offer's key. Everything else not already held is taken.
    """

    skipped: list[int] = Field(default=[], max_length=MAX_OFFER_PEOPLE)
    unticked: list[str] = Field(default=[], max_length=MAX_OFFER_FILES)

    @model_validator(mode="after")
    def _bounded(self) -> TakeOffer:
        if any(index < 0 for index in self.skipped):
            raise ValueError("a person's place in the offer cannot be negative")
        if any(len(key) > 64 for key in self.unticked):
            raise ValueError("a file key the offer could not have carried")
        return self


class SwapTunnel(Wire):
    """One tunnel as the swap screens choose from it: its name, its server, whether it can host.

    `can_host` is null until a swap has been started on it, which is the only way to find out: the
    provider answers the question when asked for a port, and not before."""

    id: str
    name: str
    can_host: bool | None = None
    #: The server it is connected to, as Sites and Tunnels shows it; null while it is not up.
    endpoint: str | None = None


class KeepFromSwaps(Wire):
    """Put "Do not swap" on a thing, or take it off."""

    kept_out: bool


class RefusedBy(Wire):
    """Something a file is filed under, or a folder it sits in, that keeps it out of swaps, named as the viewer may see it:
    `local` is Kept local ("Don't enrich"), `swap` is "Don't swap"."""

    kind: Literal["folder", "person", "site", "tag"]
    id: str
    name: str
    mark: Literal["local", "swap"]


class SwapRefusal(Wire):
    """Where one thing stands with swaps: its own switch, whether it is out at all, and why.

    `kept_out_here` is the switch on the thing itself; `kept_out` is the whole rule, which takes in
    "Do not enrich" and, for a file, anything it is filed under. `why` is empty when the thing's
    own switch is the reason or when it is not out at all. `kept_local_here` is its own "Don't
    enrich" switch, and `by` names what a file is filed under that keeps it out (what a press on
    a file swap mode will not send says it by), never anything this viewer may not see.
    """

    subject: str
    id: str
    kept_out_here: bool
    kept_out: bool
    why: str = ""
    kept_local_here: bool = False
    by: list[RefusedBy] = Field(default=[])


class HeldFaces(Wire):
    """The face descriptions swaps brought for somebody this library already had.

    Held, never used to name anybody, until an admin adds them from the person's page. `waiting` is
    how many adding them would give that person now; `added` is how many the press just gave.
    """

    waiting: int
    added: int = 0
