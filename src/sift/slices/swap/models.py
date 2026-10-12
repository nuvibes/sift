# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes a swap puts on the wire and on its screens."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from sift.kernel.wire import Wire

# The offer: a closed list of fields, each bounded since a peer can lie (see the offer gate test).

#: What a host can offer: an entity, one file, a saved filter, or a person's facial fingerprints.
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

MAX_CHOSEN = 500

#: Bounds the guest's memory against a peer's lie, not any real library.
MAX_OFFER_FILES = 200_000

MAX_OFFER_PEOPLE = 5_000

#: The best by quality: a match by face is only a suggestion, and a few dozen answer it.
MAX_FACES_PER_PERSON = 64

MAX_NAMES_PER_PERSON = 64

#: Long enough for anything real, short enough that a peer cannot send a novel as a name.
_TEXT = 300

MAX_SONG_NAME = _TEXT

_VECTOR = 12_000

MAX_PICTURE_TEXT = 200_000

#: Past this the rest go as numbers alone, so the offer stays one message.
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
    """What a request says was chosen, checked; raises `ValueError` with a sentence to show."""
    if not isinstance(raw, list):
        raise ValueError("Choose what to offer.")
    if len(raw) > MAX_CHOSEN:
        raise ValueError(f"A swap can offer at most {MAX_CHOSEN} things.")
    picked = tuple(dict.fromkeys(Chosen.model_validate(one) for one in raw))
    if sum(1 for one in picked if one.kind == "filter") > 1:
        raise ValueError("A swap can offer one saved filter.")
    return picked


class OfferedFace(_Strict):
    """One facial fingerprint, and its picture only when the two sides' face models differ."""

    digest: str = Field(max_length=128)
    quality: float
    vector: str = Field(max_length=_VECTOR)
    picture: str | None = Field(default=None, max_length=MAX_PICTURE_TEXT)


class OfferedFaces(_Strict):
    """A person's face descriptions, and the model that made them; two models never compare."""

    recognizer: str = Field(max_length=_TEXT)
    dimension: int = Field(ge=1, le=2048)
    faces: list[OfferedFace] = Field(default=[], max_length=MAX_FACES_PER_PERSON)
    #: Sent only to a side whose hello said it takes it (`Offer.as_sent`).
    confirmed: int | None = Field(default=None, ge=0, le=100_000_000)


class OfferedPerson(_Strict):
    """A person the host offered, as the guest needs them to match or to add them."""

    name: str = Field(min_length=1, max_length=_TEXT)
    aliases: list[str] = Field(default=[], max_length=MAX_NAMES_PER_PERSON)
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
    """A file's song: its name, its artists, and the AcoustID recording it is."""

    name: str = Field(min_length=1, max_length=MAX_SONG_NAME)
    artists: list[str] = Field(default=[], max_length=MAX_NAMES_PER_PERSON)
    #: Held to a ceiling rather than a pattern, so a recording written another way is still read.
    recording: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _bounded_artists(self) -> OfferedSong:
        if any(len(one) > _TEXT for one in self.artists):
            raise ValueError("a name in the offer is too long")
        return self


class OfferedFile(_Strict):
    """One file the host offers, as much as the guest needs to decide whether it wants it."""

    #: The host's own id for the file, opaque to the guest.
    key: str = Field(min_length=1, max_length=64)
    oshash: str | None = Field(default=None, max_length=64)
    size: int = Field(ge=0)
    identity: str = Field(min_length=1, max_length=128)
    video_phash: str | None = Field(default=None, max_length=64)
    #: Picture only: without it no picture could be recognized as already held.
    phash: str | None = Field(default=None, max_length=64)
    #: Two installs compare fingerprints only when this agrees.
    fingerprint_version: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    kind: FileKind
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    title: str = Field(min_length=1, max_length=_TEXT * 2)
    #: The leaf alone, numbered on a clash; None where the host has no name for it.
    name: str | None = Field(default=None, max_length=255)
    site: str | None = Field(default=None, max_length=_TEXT)
    username: str | None = Field(default=None, max_length=_TEXT)
    people: list[int] = Field(default=[], max_length=MAX_NAMES_PER_PERSON)
    #: Sent only to a guest whose hello said it takes songs: an older install refuses the field.
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
        """The offer as it goes on the wire, less what the other side's hello did not take."""
        exclude: dict[str, Any] = {}
        if not songs:
            exclude["files"] = {"__all__": {"song"}}
        if not counts:
            exclude["people"] = {"__all__": {"faces": {"confirmed"}}}
        return self.model_dump(mode="json", exclude=exclude or None)


class Diff(_Strict):
    """What the guest answers the offer with: the files it wants, and who each person is here."""

    wanted: list[str] = Field(default=[], max_length=MAX_OFFER_FILES)
    #: Defaulted, so an answer from an install that still sends it is accepted and ignored.
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


HeldReason = Literal["same", "near"]

MatchedBy = Literal["box", "name", "alias"]


class HeldFile(Wire):
    """One offered file the guest already has, and why: the unticked row that says so."""

    key: str
    title: str
    reason: HeldReason


class PersonRow(Wire):
    """One offered person on the guest's screen: what taking them would bring, and who they are."""

    index: int
    name: str
    files: int
    bytes: int
    held: int
    faces: int = 0
    confirmed: int | None = None
    match_id: str | None = None
    match_name: str | None = None
    matched_by: MatchedBy | None = None


#: A group of an offer, in the order Sift files things: People, Sites, Collections, Photo Sets,
#: Songs, then the files under none of them.
GroupKind = Literal["person", "site", "collection", "photo_set", "songs", "loose"]

#: Where a group stands, as this side knows it: the client draws its own ticks before Take.
GroupState = Literal[
    "offered",
    "taking",
    "held",
    "skipped",
    "waiting",
    "crossing",
    "landed",
    "sent",
    "left_out",
]

#: Why an offered file is already here: the exact file, or the same picture by its fingerprint.
HeldAs = Literal["same", "look"]

#: How many of a group's files ride on the session view; the rest are a page each on request.
GROUP_SAMPLE = 5


class ManifestFile(Wire):
    """One offered file as a group row folds open to it."""

    key: str
    title: str
    kind: FileKind
    size: int
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    held: HeldAs | None = None


class ManifestGroup(Wire):
    """One row of the manifest: a group of the offer with its totals, and where it stands.

    `files` and `bytes` are what the group offers; `have` is how many of them this library holds
    (null on the side that offered: the answer never says what the other side holds), `to_take`
    how many will be or were taken. `done` counts the files landed or sent so far."""

    key: str
    kind: GroupKind
    index: int | None = None
    name: str | None = None
    match_name: str | None = None
    matched_by: MatchedBy | None = None
    local_id: str | None = None
    usernames: int = 0
    faces: int = 0
    files: int = 0
    bytes: int = 0
    have: int | None = None
    to_take: int = 0
    held_same: int = 0
    held_look: int = 0
    state: GroupState = "offered"
    done: int = 0
    failed: int = 0
    crossing: int = 0
    left_out: Literal["local", "swap"] | None = None
    sample: list[ManifestFile] = Field(default=[])
    more: int = 0


class GroupFiles(Wire):
    """A page of one group's files."""

    files: list[ManifestFile] = Field(default=[])
    total: int = 0


class OfferScreen(Wire):
    """What the guest's offer screen draws; the totals count each file once."""

    layout: Literal["rows", "whole"]
    rows: list[PersonRow] = Field(default=[])
    everyone: list[PersonRow] = Field(default=[])
    offered_files: int = 0
    offered_bytes: int = 0
    files: int = 0
    bytes: int = 0
    shared: int = 0
    unfiled_files: int = 0
    unfiled_bytes: int = 0
    held: list[HeldFile] = Field(default=[])


# The session: the token is answered only to the host who pressed Start, and only once.


class StartSwap(Wire):
    """Start a swap: what is offered, through which tunnel, and whether stash-box ids travel."""

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
    expires: int


class JoinSwap(Wire):
    """Join a swap: the token that was pasted, and where received files go."""

    token: str = Field(min_length=1, max_length=400)
    dest_folder_id: str = Field(min_length=1, max_length=64)
    tunnel_id: str | None = Field(default=None, min_length=1, max_length=64)


class SwapJoined(Wire):
    session_id: str


class CodeAnswer(Wire):
    """They match (`true`) or They don't match (`false`), and what a two-way guest sends."""

    match: bool
    chosen: list[Chosen] = Field(default=[], max_length=MAX_CHOSEN)
    share_boxes: bool = True


#: `cut_off` is a live session a tunnel cut off, waiting for the same token to join again.
SessionState = Literal[
    "waiting", "connected", "offered", "transferring", "cut_off", "done", "ended", "failed"
]


class SwapDirection(Wire):
    """One direction of a two-way swap from this side: offered, wanted, moved, and the pace."""

    offered_files: int = 0
    wanted_files: int = 0
    files: int = 0
    bytes: int = 0
    wanted_bytes: int | None = None
    rate_bps: int | None = None
    moving: bool = False
    received_files: int | None = None
    #: The streams carrying this direction now.
    streams: int | None = None
    #: Every file it wanted has crossed: the screen says its total, not a rate.
    done: bool = False


class CrossingNow(Wire):
    """The file crossing now: the one opened last of those still on their way."""

    title: str
    size: int
    group: str


RefusedWhy = Literal["bytes", "strip", "landing"]

#: How many refused files the session view names; `refused_count` says how many there were.
MAX_REFUSED_NAMED = 50


class RefusedFile(Wire):
    """A file that came and was not let in: its bytes were not what was offered, it could not be
    stripped, or the landing failed."""

    title: str
    reason: RefusedWhy


class SwapSession(Wire):
    """One session as the swap screen draws it: its state, the code, the counts, the rate."""

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
    wanted_bytes: int | None = None
    rate_bps: int | None = None
    unwanted_files: int = 0
    received_files: int | None = None
    started_at: int
    ended_at: int | None = None
    end_reason: str | None = None
    offer: OfferScreen | None = None
    rejoin_until: int | None = None
    two_way: bool = False
    answered: bool = False
    sending: SwapDirection | None = None
    receiving: SwapDirection | None = None
    #: The groups of what this side receives (the guest's, from the offer) or, on the side that
    #: only sends, of what it offered.
    manifest: list[ManifestGroup] | None = None
    now: CrossingNow | None = None
    streams: int | None = None
    #: When the first file moved, in seconds since the epoch.
    moving_since: int | None = None
    #: When the token runs out: the host's, while there is one to show.
    token_expires: int | None = None
    peer_name: str | None = None
    #: Earlier swaps with the same device that moved a file or finished.
    swapped_before: int | None = None
    tunnel_name: str | None = None
    refused: list[RefusedFile] = Field(default=[])
    refused_count: int = 0
    #: What has landed so far and stays, whatever happens next.
    kept_files: int = 0
    kept_bytes: int = 0
    #: The guest, cut off: when it dials again on its own.
    retry_at: int | None = None


class WeighSwap(Wire):
    """What to weigh before a swap starts: the things picked, as Start would send them."""

    chosen: list[Chosen] = Field(default=[], max_length=MAX_CHOSEN)


class LeftOut(Wire):
    """A pick that wears a mark on its own row, and how many of its files the mark keeps back."""

    kind: Literal["asset", "person", "site", "tag", "folder"]
    id: str
    name: str
    mark: Literal["local", "swap"]
    files: int


class Learns(Wire):
    """What the other side will learn from the offer, counted: the people named and their other
    names, the Sites and Usernames the files are filed under, and the faces sent."""

    people: int = 0
    aliases: int = 0
    sites: int = 0
    usernames: int = 0
    faces: int = 0


class SwapWeight(Wire):
    """How many files and bytes the picks would offer, or an answer would bring, and what is out."""

    files: int = 0
    bytes: int = 0
    left_out: list[LeftOut] = Field(default=[])
    left_out_other: int = 0
    groups: list[ManifestGroup] = Field(default=[])
    learns: Learns | None = None


class ReadToken(Wire):
    """A pasted token, read before Join: nothing is dialled."""

    token: str = Field(min_length=1, max_length=400)


class TokenRead(Wire):
    """Who a token is from and until when it is good."""

    device_id: str
    device_name: str | None = None
    expires: int
    swapped_before: int | None = None


class SwapDevice(Wire):
    """This install's device id, or null before the first swap; and whether the keys are locked."""

    device_id: str | None = None
    locked: bool = False


# The screens: the answer is bounded by the offer it answers.


class TakeOffer(Wire):
    """The guest's answer to the offer screen: the people it skipped and the files it unticked."""

    skipped: list[int] = Field(default=[], max_length=MAX_OFFER_PEOPLE)
    unticked: list[str] = Field(default=[], max_length=MAX_OFFER_FILES)
    #: Whole groups skipped, by their manifest key.
    skipped_groups: list[str] = Field(default=[], max_length=2 * MAX_OFFER_PEOPLE + 2)

    @model_validator(mode="after")
    def _bounded(self) -> TakeOffer:
        if any(index < 0 for index in self.skipped):
            raise ValueError("a person's place in the offer cannot be negative")
        if any(len(key) > 64 for key in (*self.unticked, *self.skipped_groups)):
            raise ValueError("a file key the offer could not have carried")
        return self


class SwapTunnel(Wire):
    """One tunnel as the swap screens choose from it; `can_host` is null until a swap has tried."""

    id: str
    name: str
    can_host: bool | None = None
    endpoint: str | None = None
    #: The rate the last swap through it measured, in bits a second.
    rate_bps: int | None = None


class ProbeHost(Wire):
    """Host a sink behind a tunnel for `seconds`."""

    seconds: int = Field(default=20, ge=1, le=120)


class ProbeHosted(Wire):
    """Where the other device sends: its address, port and the probe's secret, hex."""

    address: str
    port: int
    secret: str
    seconds: int


class ProbeReading(Wire):
    """What a hosted sink has counted so far."""

    received: int
    connections: int
    refused: int
    seconds: float | None = None
    rate_bps: int | None = None
    done: bool


class ProbeSend(Wire):
    """Push bytes to a hosted sink through this device's tunnel."""

    address: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    secret: str = Field(min_length=64, max_length=64)
    seconds: int = Field(default=20, ge=1, le=120)
    streams: int = Field(default=1, ge=1, le=16)


class ProbeSent(Wire):
    """What a send pushed, and the rate it made in bits a second."""

    sent: int
    seconds: float
    streams: int
    failed: int
    rate_bps: int


class KeepFromSwaps(Wire):
    """Put "Do not swap" on a thing, or take it off."""

    kept_out: bool


class RefusedBy(Wire):
    """Something that keeps a file out of swaps, named as the viewer may see it."""

    kind: Literal["folder", "person", "site", "tag"]
    id: str
    name: str
    mark: Literal["local", "swap"]


class SwapRefusal(Wire):
    """Where one thing stands with swaps: its own switch, whether it is out at all, and why."""

    subject: str
    id: str
    kept_out_here: bool
    kept_out: bool
    why: str = ""
    kept_local_here: bool = False
    by: list[RefusedBy] = Field(default=[])


class HeldFaces(Wire):
    """Face descriptions swaps brought for somebody this library already had, held until added."""

    waiting: int
    added: int = 0


# The receipt: one per side, written when a swap ends, read back from History.

ReceiptWay = Literal["landed", "sent"]
FiledAs = Literal["created", "added"]


class ReceiptGroup(Wire):
    """One group of a receipt: what landed or was sent under it, and how it was filed here."""

    way: ReceiptWay
    kind: GroupKind
    id: str | None = None
    name: str | None = None
    files: int = 0
    bytes: int = 0
    filed: FiledAs | None = None
    faces: int = 0
    usernames: int = 0


class ReceiptSkipped(Wire):
    """A group that was offered and not taken."""

    kind: GroupKind
    id: str | None = None
    name: str | None = None
    files: int = 0


class ReceiptFolder(Wire):
    """Where the files landed: the folder the swap made, by id and as the library names it."""

    id: str
    path: str
    own: bool = True


class ReceiptUndo(Wire):
    """What the Undo took back."""

    at: int
    files: int = 0
    from_disk: bool = False
    people: int = 0
    sites: int = 0
    #: Files another program held open, left for a later Undo.
    in_use: int = 0
    #: Files the swap landed that the Undo leaves: they were already in the library.
    kept: int = 0


class SwapReceipt(Wire):
    """A swap, signed: what landed or was sent per group, where, how long, how fast, and its Undo."""

    session_id: str
    role: Literal["host", "guest"]
    two_way: bool = False
    device: str | None = None
    device_name: str | None = None
    started_at: int
    ended_at: int | None = None
    seconds: int | None = None
    reason: str | None = None
    files: int = 0
    bytes: int = 0
    back_files: int = 0
    back_bytes: int = 0
    #: Bytes a second over `moving`; with `back_`, the other way's over its own.
    rate: float | None = None
    back_rate: float | None = None
    #: Seconds the files moved, from the first byte to the last, less the time cut off.
    moving: int | None = None
    back_moving: int | None = None
    #: Seconds from the start of the session to its first byte.
    waited: int | None = None
    tunnel: str | None = None
    cut_offs: int = 0
    #: Seconds the session spent cut off.
    cut_seconds: int = 0
    #: Files received here whole that the end came before they were added to the library.
    not_kept: int = 0
    folder: ReceiptFolder | None = None
    groups: list[ReceiptGroup] = Field(default=[])
    skipped: list[ReceiptSkipped] = Field(default=[])
    refused: int = 0
    held: int = 0
    undo: ReceiptUndo | None = None
    can_undo: bool = False
